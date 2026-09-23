import asyncio
import json
import stat

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from crosscheck import diagnostics
from crosscheck.api.history import get_repository
from crosscheck.api.routes import get_service
from crosscheck.config import Settings, get_settings
from crosscheck.domain.models import Claim, VerificationRequest
from crosscheck.main import app
from crosscheck.providers.firecrawl import FirecrawlProvider
from crosscheck.providers.mock import MockContentProvider, MockSearchProvider
from crosscheck.services.llm_analyzer import LLMClaimAnalyzer, analyze_claim_prefer_llm
from crosscheck.services.orchestrator import VerificationService
from crosscheck.storage.reports import ReportRepository


@pytest.fixture
def log_dir(tmp_path):
    directory = tmp_path / "logs"
    diagnostics.configure_logging(Settings(log_directory=directory))
    yield directory
    diagnostics.configure_logging(get_settings())


def records(directory):
    return [json.loads(line) for path in directory.glob("*.jsonl*") for line in path.read_text().splitlines()]


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", [False, True])
async def test_model_fallback_is_traceable_and_redacted(log_dir, monkeypatch, preview):
    secret = "test-secret-credential"
    output = "普通对话，不是JSON " + secret + " token=unknown-token " + "长" * 2500

    def handle(request):
        content = output if request.url.host == "primary.test" else '{"subject":"北京"}'
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}, headers={"x-request-id": "upstream-test"})

    mock_http(monkeypatch, handle)
    primary = LLMClaimAnalyzer(secret, "https://primary.test", "test-model", label="llm-primary", log_response_preview=preview)
    backup = LLMClaimAnalyzer(secret, "https://backup.test", "test-model", label="llm-backup")
    token = diagnostics.request_id.set("trace-fallback")
    try:
        _, method, warnings = await analyze_claim_prefer_llm("私有主张不应入日志", [primary, backup])
    finally:
        diagnostics.request_id.reset(token)
    assert method == "llm-backup"
    assert warnings == []
    rows = records(log_dir)
    assert all(row["request_id"] == "trace-fallback" for row in rows)
    failed = next(row for row in rows if row["event"] == "llm_request_failed")
    assert failed["content_format"] == "plain_text"
    assert failed["finish_reason"] == "stop"
    assert failed["timeout_seconds"] == 60
    assert ("response_preview" in failed) is preview
    if preview:
        assert failed["preview_truncated"]
        assert len(failed["response_preview"]) <= 2000
    serialized = json.dumps(rows, ensure_ascii=False)
    assert secret not in serialized
    assert "unknown-token" not in serialized
    assert "私有主张" not in serialized
    assert any(row["event"] == "llm_attempt_succeeded" and row["fallback_used"] for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "http"])
async def test_timeout_and_http_failure_have_safe_details(log_dir, monkeypatch, failure):
    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive raw exception", request=request)
        return httpx.Response(503, text="sensitive upstream error body")

    mock_http(monkeypatch, handle)
    model = LLMClaimAnalyzer("test-key", "https://model.test", "test-model", timeout=42)
    _, method, warnings = await analyze_claim_prefer_llm("测试超时和错误", model)
    assert method == "rules"
    assert warnings
    rows = records(log_dir)
    failed = next(row for row in rows if row["event"] == "llm_request_failed")
    assert failed["timeout_seconds"] == 42
    assert failed["elapsed_ms"] >= 0
    assert failed["error_type"] == ("ReadTimeout" if failure == "timeout" else "HTTPStatusError")
    assert failed["http_status"] == (None if failure == "timeout" else 503)
    assert "sensitive" not in json.dumps(rows)


@pytest.mark.asyncio
async def test_concurrent_verifications_keep_distinct_request_ids(log_dir):
    service = VerificationService([MockSearchProvider()], MockContentProvider(), 2, 2)
    reports = await asyncio.gather(*(service.verify(VerificationRequest(claim=f"并发请求第 {i} 条")) for i in range(3)))
    ids = {report.request_id for report in reports}
    assert len(ids) == 3
    rows = records(log_dir)
    assert {row["request_id"] for row in rows} == ids
    for report in reports:
        scoped = [row for row in rows if row["request_id"] == report.request_id]
        assert scoped[0]["event"] == "verification_started"
        assert any(row["event"] == "verification_completed" for row in scoped)
        assert scoped[0]["claim_fingerprint"] == diagnostics.fingerprint(report.claim.text)
    assert diagnostics.request_id.get() is None


@pytest.mark.parametrize("stream", [False, True])
def test_api_report_header_and_saved_log_share_id(log_dir, tmp_path, stream):
    app.dependency_overrides[get_service] = lambda: VerificationService([MockSearchProvider()], MockContentProvider(), 2, 2)
    app.dependency_overrides[get_repository] = lambda: ReportRepository(tmp_path / "reports.db")
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/verifications" + ("/stream" if stream else ""), json={"claim": "测试日志与报告对应关系"})
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            report = events[-1]["report"]
        else:
            report = response.json()
        trace = response.headers["x-request-id"]
        assert report["request_id"] == trace
        rows = records(log_dir)
        scoped = [row for row in rows if row["request_id"] == trace]
        assert scoped[0]["event"] == "http_request_started"
        assert scoped[-1]["event"] == "http_request_finished"
        assert scoped[-1]["response_complete"]
        assert any(row["event"] == "report_saved" and row["report_id"] == trace for row in scoped)
    finally:
        app.dependency_overrides.pop(get_service)
        app.dependency_overrides.pop(get_repository)


def test_rotation_is_bounded_private_and_configuration_is_idempotent(log_dir):
    settings = Settings(log_directory=log_dir)
    diagnostics.configure_logging(settings)
    handler = diagnostics._handler
    diagnostics.configure_logging(settings)
    assert diagnostics._handler is handler
    assert diagnostics.logger.handlers.count(handler) == 1
    handler.maxBytes = 400
    for _ in range(30):
        diagnostics.event("rotation_test", detail="x" * 80)
    paths = list(log_dir.glob("*.jsonl*"))
    assert len(paths) == 4
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)
    assert all(row["event"] == "rotation_test" for row in records(log_dir))


@pytest.mark.asyncio
async def test_provider_logs_effective_timeout_and_safe_endpoint(log_dir, monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"data": {"web": []}}))
    provider = FirecrawlProvider("credential-value", "https://user:password@provider.test", timeout=20)
    assert await provider.search("private query", 1) == []
    rows = records(log_dir)
    started = next(row for row in rows if row["event"] == "provider_http_started")
    assert started["timeout_seconds"] == 65
    assert started["endpoint_host"] == "provider.test"
    assert rows[-1]["http_status"] == 200
    assert rows[-1]["attempt_id"] == started["attempt_id"]
    for sensitive in ("credential-value", "user:password", "private query"):
        assert sensitive not in json.dumps(rows)


def test_validation_details_exclude_invalid_input():
    try:
        Claim(text="private claim", duration_days="private invalid input")
    except ValidationError as exc:
        fields = diagnostics.error_fields(exc)
    assert fields["invalid_fields"] == [{"field": ["duration_days"], "type": "int_parsing"}]
    assert "private" not in json.dumps(fields)
