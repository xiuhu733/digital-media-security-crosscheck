import httpx
import pytest
from fastapi.testclient import TestClient

from crosscheck.api.routes import get_service
from crosscheck.domain.models import VerificationRequest
from crosscheck.main import app
from crosscheck.providers.mock import MockContentProvider, MockSearchProvider
from crosscheck.services.errors import ProviderFailure
from crosscheck.services.orchestrator import VerificationService


class TimeoutSearch:
    name = 'firecrawl'

    async def search(self, query, limit):
        raise httpx.ReadTimeout('')


class TimeoutContent:
    name = 'firecrawl'

    async def fetch(self, url):
        raise httpx.ReadTimeout('')


def test_empty_timeout_has_provider_stage_and_actionable_api_message():
    app.dependency_overrides[get_service] = lambda: VerificationService(
        [TimeoutSearch()], MockContentProvider(), 1, 1
    )
    try:
        response = TestClient(app).post('/api/v1/verifications', json={'claim': '这是一条测试说法'})
        assert response.status_code == 502
        assert 'firecrawl 搜索失败' in response.json()['detail']
        assert '请求超时' in response.json()['detail']
        assert 'ReadTimeout' in response.json()['detail']
    finally:
        app.dependency_overrides.pop(get_service)


@pytest.mark.asyncio
async def test_content_failure_is_kept_in_report():
    service = VerificationService([MockSearchProvider()], TimeoutContent(), 1, 1)
    report = await service.verify(VerificationRequest(claim='这是一条测试说法'))
    assert '正文抓取失败' in report.evidence[0].excerpt


def test_http_error_does_not_leak_request_url_or_response_secret():
    request = httpx.Request('POST', 'https://example.org/?key=secret-token')
    response = httpx.Response(402, request=request, text='secret-response')
    cause = httpx.HTTPStatusError('secret-token', request=request, response=response)
    message = str(ProviderFailure('firecrawl', '搜索', cause))
    assert '额度不足' in message
    assert 'secret' not in message
