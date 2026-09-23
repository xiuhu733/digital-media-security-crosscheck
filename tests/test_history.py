import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from crosscheck.api.history import get_repository
from crosscheck.api.routes import get_service
from crosscheck.domain.models import Claim, Relation, VerificationReport
from crosscheck.main import app
from crosscheck.providers.mock import MockContentProvider, MockSearchProvider
from crosscheck.services.orchestrator import VerificationService
from crosscheck.storage.reports import ReportRepository


@pytest.fixture
def isolated_history(tmp_path):
    database = tmp_path / "history.sqlite3"
    app.dependency_overrides[get_repository] = lambda: ReportRepository(database)
    app.dependency_overrides[get_service] = lambda: VerificationService(
        [MockSearchProvider()], MockContentProvider(), 2, 2
    )
    yield database
    app.dependency_overrides.pop(get_repository)
    app.dependency_overrides.pop(get_service)


@pytest.mark.parametrize('stream', [False, True])
def test_post_then_new_client_can_read_report(isolated_history, stream):
    with TestClient(app) as client:
        assert client.get('/api/v1/history').json()['total'] == 0
        path = '/api/v1/verifications/stream' if stream else '/api/v1/verifications'
        response = client.post(path, json={'claim': '某市发布的政策是否适用于所有车辆'})
        assert response.status_code == 200
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert events[-1]['type'] == 'result'
            assert any(event.get('stage') == 'saved' for event in events)
            report = events[-1]['report']
        else:
            report = response.json()
    # All reads create new connections; no in-memory report cache is used.
    with TestClient(app) as client:
        page = client.get('/api/v1/history').json()
        assert page['total'] == 1
        assert 'evidence' not in page['items'][0]
        assert page['items'][0]['claim_text'] == report['claim']['text']
        assert client.get('/api/v1/history/' + report['request_id']).json() == report
        assert client.get('/api/v1/history/not-found').status_code == 404
        assert client.get('/api/v1/history?limit=0').status_code == 422
    reopened = ReportRepository(isolated_history)
    assert reopened.get(report['request_id']).model_dump(mode='json') == report


def test_repository_order_pagination_and_duplicate_id(tmp_path):
    repo = ReportRepository(tmp_path / 'reports.sqlite3')
    for index in range(3):
        report = VerificationReport(
            request_id=str(index), claim=Claim(text="测试 ' OR 1=1 -- <script>"),
            conclusion=Relation.INSUFFICIENT, summary='证据不足',
            evidence=[], source_clusters={}, searched_providers=[],
            created_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=index),
        )
        repo.save(report)
        repo.save(report)
    page = repo.list_reports(1, 1)
    assert page['total'] == 3
    assert page['items'][0]['request_id'] == '1'
    assert repo.list_reports(20, 3)['items'] == []
    assert repo.get("' OR 1=1 --") is None


def test_delete_only_selected_report_and_persists(isolated_history):
    with TestClient(app) as client:
        first = client.post('/api/v1/verifications', json={'claim': '第一条用于验证删除的记录'}).json()
        second = client.post('/api/v1/verifications', json={'claim': '第二条需要保留的记录'}).json()
        path = '/api/v1/history/' + first['request_id']
        assert client.delete("/api/v1/history/' OR 1=1 --").status_code == 404
        assert client.get('/api/v1/history').json()['total'] == 2
        response = client.delete(path)
        assert response.status_code == 204
        assert response.content == b''
        assert client.get(path).status_code == 404
        assert client.delete(path).status_code == 404
    with TestClient(app) as client:
        page = client.get('/api/v1/history').json()
        assert page['total'] == 1
        assert page['items'][0]['request_id'] == second['request_id']
        assert client.get('/api/v1/history/' + second['request_id']).json() == second
        assert client.delete('/api/v1/history/' + second['request_id']).status_code == 204
        assert client.get('/api/v1/history').json()['items'] == []
    assert ReportRepository(isolated_history).get(first['request_id']) is None


def test_delete_failure_preserves_report(isolated_history, monkeypatch):
    def fail_delete(self, request_id):
        raise sqlite3.OperationalError('database is locked')

    with TestClient(app) as client:
        report = client.post('/api/v1/verifications', json={'claim': '删除失败应保留这条记录'}).json()
        monkeypatch.setattr(ReportRepository, 'delete', fail_delete)
        path = '/api/v1/history/' + report['request_id']
        response = client.delete(path)
        assert response.status_code == 503
        assert '删除失败' in response.json()['detail']
        assert client.get(path).json() == report


@pytest.mark.parametrize('stream', [False, True])
def test_storage_error_never_reports_success(isolated_history, monkeypatch, stream):
    def fail_save(self, report):
        raise sqlite3.OperationalError('disk full')

    monkeypatch.setattr(ReportRepository, 'save', fail_save)
    path = '/api/v1/verifications/stream' if stream else '/api/v1/verifications'
    with TestClient(app) as client:
        response = client.post(path, json={'claim': '测试数据库失败时的返回信息'})
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            assert events[-1]['type'] == 'error'
            assert events[-1]['request_id'] == response.headers['x-request-id']
            assert '保存失败' in events[-1]['message']
            assert not any(event['type'] == 'result' for event in events)
        else:
            assert response.status_code == 503
        assert client.get('/api/v1/history').json()['total'] == 0
