import pytest

from crosscheck.config import Settings
from crosscheck.domain.models import Claim, Evidence, Relation, VerificationRequest
from crosscheck.providers.factory import build_evidence_analyzers
from crosscheck.providers.mock import MockContentProvider, MockSearchProvider
from crosscheck.services.hybrid_evidence_analyzer import HybridEvidenceAnalyzer
from crosscheck.services.orchestrator import VerificationService


class StubJudge:
    def __init__(self, label: str, relation: str | None):
        self.label = label
        self.relation = relation

    async def analyze_evidence(self, claim, evidence):
        if self.relation is None:
            raise RuntimeError("judge unavailable")
        return {item.id: {"relation": self.relation, "reason": f"{self.label} 的判断"} for item in evidence}


def evidence_item() -> Evidence:
    return Evidence(id="E1", url="https://example.com/notice", title="通知",
                    excerpt="北京理工大学2026年国庆放假共7天。", provider="mock")


@pytest.mark.asyncio
async def test_hybrid_agreement_keeps_relation_and_both_reasons():
    judge = HybridEvidenceAnalyzer(StubJudge("local-relation-model", "supports"), [StubJudge("llm-primary", "supports")])
    updated, method, warnings = await judge.analyze(Claim(text="北京理工大学2026年国庆放假7天"), [evidence_item()])
    assert method == "hybrid-local-llm-primary"
    assert warnings == []
    assert updated[0].relation == Relation.SUPPORTS
    assert "本地依据" in updated[0].analysis_reason
    assert "大模型依据" in updated[0].analysis_reason


@pytest.mark.asyncio
async def test_hybrid_disagreement_abstains():
    judge = HybridEvidenceAnalyzer(StubJudge("local-relation-model", "supports"), [StubJudge("llm-primary", "refutes")])
    updated, method, warnings = await judge.analyze(Claim(text="北京理工大学2026年国庆放假7天"), [evidence_item()])
    assert method == "hybrid-local-llm-primary"
    assert warnings == []
    assert updated[0].relation == Relation.INSUFFICIENT
    assert "存在分歧" in updated[0].analysis_reason


@pytest.mark.asyncio
async def test_hybrid_llm_failure_uses_local_with_warning():
    judge = HybridEvidenceAnalyzer(StubJudge("local-relation-model", "supports"), [StubJudge("llm-primary", None)])
    updated, method, warnings = await judge.analyze(Claim(text="北京理工大学2026年国庆放假7天"), [evidence_item()])
    assert method == "local-relation-model"
    assert updated[0].relation == Relation.SUPPORTS
    assert any("仅使用本地模型" in warning for warning in warnings)
    assert not any("已回退规则判断" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_hybrid_local_failure_uses_llm_with_warning():
    judge = HybridEvidenceAnalyzer(StubJudge("local-relation-model", None), [StubJudge("llm-primary", "refutes")])
    updated, method, warnings = await judge.analyze(Claim(text="北京理工大学2026年国庆放假7天"), [evidence_item()])
    assert method == "llm-primary"
    assert updated[0].relation == Relation.REFUTES
    assert any("仅使用大模型" in warning for warning in warnings)
    assert not any("已回退规则判断" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_hybrid_without_llm_configuration_reports_local_only():
    judge = HybridEvidenceAnalyzer(StubJudge("local-relation-model", "supports"), None)
    updated, method, warnings = await judge.analyze(Claim(text="北京理工大学2026年国庆放假7天"), [evidence_item()])
    assert method == "local-relation-model"
    assert updated[0].relation == Relation.SUPPORTS
    assert any("仅使用本地模型" in warning for warning in warnings)
    assert not any("使用规则证据判断" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_hybrid_is_used_by_verification_service():
    judge = HybridEvidenceAnalyzer(StubJudge("local-relation-model", "supports"), [StubJudge("llm-primary", "supports")])
    service = VerificationService([MockSearchProvider()], MockContentProvider(), 1, 1, evidence_analyzer=judge)
    report = await service.verify(VerificationRequest(claim="北京理工大学2026年国庆放假7天"))
    assert report.evidence_analysis_method == "hybrid-local-llm-primary"
    assert report.evidence[0].relation == Relation.SUPPORTS


def test_hybrid_factory_uses_both_configured_models():
    settings = Settings(_env_file=None, evidence_analyzer="hybrid", llm_api_key="test-key")
    judge = build_evidence_analyzers(settings)
    assert isinstance(judge, HybridEvidenceAnalyzer)
    assert judge.local_analyzer.label == "local-relation-model"
    assert judge.llm_analyzers[0].label == "llm-primary"
