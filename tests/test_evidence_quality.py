import pytest

from crosscheck.domain.models import Claim, Document, Evidence, Relation
from crosscheck.providers.mock import MockContentProvider, MockSearchProvider
from crosscheck.services.claim_analyzer import analyze_claim
from crosscheck.services.evidence_analyzer import analyze_evidence
from crosscheck.services.llm_analyzer import (
    LLMClaimAnalyzer,
    analyze_evidence_prefer_llm,
)
from crosscheck.services.orchestrator import VerificationService


def test_late_evidence_is_sent_to_model_and_shown_in_report():
    claim = Claim(text="北理工今年国庆放假7天", subject="北京理工大学", action="放假", event="国庆", duration_days=7)
    body = "无关导航。" * 250 + "北京理工大学2026年国庆放假共8天。"
    item = analyze_evidence(claim, [Document(url="https://example.com", title="学校通知", content=body, provider="mock")])[0]
    assert item.relation == Relation.REFUTES
    assert "共8天" in item.excerpt
    assert len(item.excerpt) <= 1800


def test_unrelated_debunking_word_and_missing_duration_do_not_refute_or_support():
    claim = Claim(text="北理工国庆放假7天", subject="北京理工大学", action="放假", event="国庆", duration_days=7)
    doc = Document(url="https://example.com", title="通知", content="北京理工大学国庆放假通知。另有一条无关传闻正在辟谣。", provider="mock")
    item = analyze_evidence(claim, [doc])[0]
    assert item.relation == Relation.INSUFFICIENT


def test_empty_body_cannot_be_supported_by_matching_title():
    claim = Claim(text="某市无人机禁飞", object="无人机", action="禁飞")
    item = analyze_evidence(claim, [Document(url="https://example.com", title="某市无人机禁飞", content="", provider="mock")])[0]
    assert item.relation == Relation.INSUFFICIENT


def test_title_keywords_alone_cannot_support_unrelated_body():
    claim = Claim(text="北京市无人机禁飞", subject="北京市", object="无人机", action="禁飞")
    doc = Document(url="https://example.com/draft", title="北京市无人机禁飞通知", content="这份页面仅说明公开征求意见的时间。", provider="mock")
    assert analyze_evidence(claim, [doc])[0].relation == Relation.INSUFFICIENT


def test_scope_rebuttal_requires_matching_subject_and_year():
    claim = Claim(text="上海市2026年所有电动自行车上路", subject="上海市", time="2026年", scope="全面", object="电动自行车")
    rebuttal = "上海市2026年政策仅涉及未登记车辆，不涉及所有电动自行车。"
    matching = Document(url="https://example.com/match", title="政策说明", content=rebuttal, provider="mock")
    other_city = Document(url="https://example.com/other", title="北京政策说明", content="北京市2026年政策不涉及所有电动自行车。", provider="mock")
    old_year = Document(url="https://example.com/old", title="上海政策说明", content="上海市2019年政策不涉及所有电动自行车。", provider="mock")
    results = analyze_evidence(claim, [matching, other_city, old_year])
    assert [item.relation for item in results] == [Relation.REFUTES, Relation.INSUFFICIENT, Relation.INSUFFICIENT]


def test_scope_rebuttal_cannot_use_only_matching_title():
    claim = Claim(text="上海市2026年所有电动自行车禁行", subject="上海市", time="2026年", scope="全面", object="电动自行车")
    doc = Document(url="https://example.com/confused", title="上海市2026年电动自行车政策汇总", content="北京市2026年政策不涉及所有电动自行车。", provider="mock")
    assert analyze_evidence(claim, [doc])[0].relation == Relation.INSUFFICIENT


def test_conflicting_durations_in_one_page_are_not_arbitrarily_selected():
    claim = Claim(text="北理工今年国庆放假7天", subject="北京理工大学", action="放假", duration_days=7)
    doc = Document(url="https://example.com/dates", title="北京理工大学国庆通知", content="北京理工大学国庆放假共7天。另一个安排写明放假共8天。", provider="mock")
    assert analyze_evidence(claim, [doc])[0].relation == Relation.INSUFFICIENT


def test_relative_year_is_resolved_in_local_timezone():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    local_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    assert analyze_claim("去年北京理工大学国庆放假8天").time == f"{local_year - 1}年"


@pytest.mark.asyncio
async def test_llm_relative_month_is_normalized_with_year():
    analyzer = LLMClaimAnalyzer("key", "https://example.com", "model")

    async def response(_payload, _stage):
        return {"subject": "北京市", "object": "无人机", "action": "禁飞", "event": "无人机禁飞", "time": "今年9月份", "location": "北京", "scope": None, "duration_days": None}

    analyzer._request_json = response
    claim = await analyzer.analyze("今年9月份北京无人机禁飞")
    assert claim.time == analyze_claim("今年9月份北京无人机禁飞").time


class EmptyPreferredContent:
    name = "empty"

    async def fetch(self, url):
        return Document(url=url, title="空页面", content="", provider=self.name)


@pytest.mark.asyncio
async def test_empty_preferred_content_uses_nonempty_fallback():
    service = VerificationService([MockSearchProvider()], [EmptyPreferredContent(), MockContentProvider()], 1, 1)
    document = await service._fetch("https://example.com/official-notice")
    assert document.provider == "mock"
    assert document.content.strip()
    assert "empty 未返回正文" in document.metadata["content_failures"]


@pytest.mark.asyncio
async def test_partial_model_decision_retries_backup_instead_of_mixing_with_rules():
    first = LLMClaimAnalyzer("key", "https://example.com", "model", label="llm-primary")
    second = LLMClaimAnalyzer("key", "https://example.com", "model", label="llm-backup")

    async def partial(_payload, _stage):
        return {"items": [{"id": "E1", "relation": "supports", "reason": "第一条"}]}

    async def complete(_payload, _stage):
        return {"items": [
            {"id": "E1", "relation": "supports", "reason": "第一条"},
            {"id": "E2", "relation": "refutes", "reason": "第二条"},
        ]}

    first._request_json = partial
    second._request_json = complete
    evidence = [
        Evidence(id="E1", url="https://example.com/1", title="一", excerpt="证据一", provider="mock"),
        Evidence(id="E2", url="https://example.com/2", title="二", excerpt="证据二", provider="mock"),
    ]
    updated, method, warnings = await analyze_evidence_prefer_llm(Claim(text="测试说法"), evidence, [first, second])
    assert method == "llm-backup"
    assert warnings == []
    assert [item.relation for item in updated] == [Relation.SUPPORTS, Relation.REFUTES]
