import pytest

from crosscheck.domain.models import Relation, VerificationRequest
from crosscheck.providers.mock import MockContentProvider, MockSearchProvider
from crosscheck.services.orchestrator import VerificationService


@pytest.mark.asyncio
async def test_mock_verification_is_traceable():
    service = VerificationService([MockSearchProvider()], MockContentProvider(), 5, 8)
    report = await service.verify(VerificationRequest(claim="某市全面禁止所有电动自行车上路"))
    assert report.conclusion in {Relation.REFUTES, Relation.CONFLICTS, Relation.INSUFFICIENT}
    assert report.evidence
    assert report.evidence[0].id == "E1"
    assert report.source_clusters


@pytest.mark.asyncio
async def test_claim_parser_extracts_subject_event_and_duration():
    service = VerificationService([MockSearchProvider()], MockContentProvider(), 5, 8)
    report = await service.verify(VerificationRequest(claim="北理工今年国庆放假7天"))
    assert report.claim.subject == "北京理工大学"
    assert report.claim.time.endswith("年")
    assert report.claim.event == "国庆"
    assert report.claim.duration_days == 7


def test_claim_parser_extracts_drone_flight_ban():
    from crosscheck.services.claim_analyzer import analyze_claim

    claim = analyze_claim("今年9月份北京无人机禁飞")
    assert claim.subject == "北京市"
    assert claim.location == "北京"
    assert claim.object == "无人机"
    assert claim.action == "禁飞"
    assert claim.event == "无人机禁飞"
    assert claim.time.endswith("9月")


@pytest.mark.asyncio
async def test_subject_mismatch_reason_uses_current_claim_subject():
    from crosscheck.domain.models import Document
    from crosscheck.services.evidence_analyzer import analyze_evidence

    claim = __import__("crosscheck.services.claim_analyzer", fromlist=["analyze_claim"]).analyze_claim("上海市10月起所有电动车上路")
    evidence = analyze_evidence(claim, [Document(url="https://example.com", title="电动车提示", content="北京地区电动车登记提示。", provider="mock")])[0]
    assert "上海市" in evidence.analysis_reason
    assert "北京理工大学" not in evidence.analysis_reason


@pytest.mark.asyncio
async def test_submitted_url_is_checked_even_when_search_fails():
    class FailingSearch:
        name = "failing"

        async def search(self, _query, _limit):
            raise RuntimeError("search failed")

    service = VerificationService([FailingSearch()], MockContentProvider(), 5, 8)
    report = await service.verify(VerificationRequest(claim="某市全面禁止所有电动自行车上路", urls=["https://example.com/official-notice"]))
    assert len(report.evidence) == 1
    assert str(report.evidence[0].url) == "https://example.com/official-notice"
    assert report.conclusion == Relation.REFUTES
