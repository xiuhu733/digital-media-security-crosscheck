from pathlib import Path

import pytest

from crosscheck.config import Settings
from crosscheck.domain.models import Claim, Evidence, Relation
from crosscheck.ml.relation import RelationModel, features
from crosscheck.ml.train import split_by_group, starter_samples
from crosscheck.providers.factory import build_claim_analyzers, build_evidence_analyzers
from crosscheck.services.llm_analyzer import analyze_evidence_prefer_llm


def test_local_model_trains_without_answer_as_input(tmp_path: Path):
    train, test = split_by_group(starter_samples())
    assert all("rule_relation" not in row["fields"] for row in train + test)
    model = RelationModel()
    model.train(train)
    path = tmp_path / "relation.json"
    model.save(path)
    loaded = RelationModel.load(path)
    row = test[0]
    vector = features(row["claim"], row["evidence"], **row["fields"])
    assert max(loaded.predict_proba(vector), key=loaded.predict_proba(vector).get) == row["label"]


@pytest.mark.asyncio
async def test_local_judge_keeps_conflicting_durations_unresolved(tmp_path: Path):
    model = RelationModel()
    model.train(split_by_group(starter_samples())[0])
    path = tmp_path / "relation.json"
    model.save(path)
    settings = Settings(_env_file=None, claim_analyzer="llm", evidence_analyzer="local",
                        local_relation_model_path=path, llm_api_key="test-key")
    assert build_claim_analyzers(settings)[0].label == "llm-primary"
    analyzer = build_evidence_analyzers(settings)[0]
    assert analyzer.label == "local-relation-model"
    claim = Claim(text="北京理工大学2026年国庆放假7天", subject="北京理工大学", time="2026年",
                  action="放假", duration_days=7)
    evidence = [Evidence(id="E1", url="https://example.com/notice", title="通知",
                         excerpt="北京理工大学2026年国庆放假共7天和8天。", provider="mock")]
    updated, method, warnings = await analyze_evidence_prefer_llm(claim, evidence, [analyzer])
    assert method == "local-relation-model"
    assert warnings == []
    assert updated[0].relation == Relation.INSUFFICIENT
