import pytest

from crosscheck.domain.models import Claim, Evidence, Relation
from crosscheck.services.llm_analyzer import analyze_evidence_prefer_llm


class EvidenceModel:
    label = "llm-primary"

    async def analyze_evidence(self, claim, evidence):
        return {"E1": {"relation": "refutes", "reason": "来源明确写明8天，主张为7天。"}}


class BrokenEvidenceModel:
    label = "llm-primary"

    async def analyze_evidence(self, claim, evidence):
        raise ValueError("模型未返回有效的证据判断")


@pytest.mark.asyncio
async def test_evidence_backup_success_clears_primary_warning(caplog):
    backup = EvidenceModel()
    backup.label = "llm-backup"
    evidence = [Evidence(id="E1", url="https://example.com", title="通知", excerpt="共8天", provider="mock")]
    updated, method, warnings = await analyze_evidence_prefer_llm(
        Claim(text="放假7天"), evidence, [BrokenEvidenceModel(), backup]
    )
    assert method == "llm-backup"
    assert updated[0].relation == Relation.REFUTES
    assert warnings == []
    assert any(getattr(record, "diagnostic_fields", {}).get("stage") == "evidence" for record in caplog.records)


@pytest.mark.asyncio
async def test_all_evidence_models_fail_keeps_warning():
    evidence = [Evidence(id="E1", url="https://example.com", title="通知", excerpt="共8天", provider="mock")]
    updated, method, warnings = await analyze_evidence_prefer_llm(
        Claim(text="放假7天"), evidence, [BrokenEvidenceModel(), BrokenEvidenceModel()]
    )
    assert method == "rules"
    assert updated == evidence
    assert "已回退规则判断" in warnings[-1]


@pytest.mark.asyncio
async def test_llm_evidence_decision_updates_relation_and_reason():
    evidence = [Evidence(id="E1", url="https://example.com", title="通知", excerpt="共8天", provider="mock")]
    updated, method, _warnings = await analyze_evidence_prefer_llm(Claim(text="放假7天"), evidence, EvidenceModel())
    assert method == "llm-primary"
    assert updated[0].relation == Relation.REFUTES
    assert "8天" in updated[0].analysis_reason
