"""Inference adapter for the project's own trained evidence classifier."""

from __future__ import annotations

import asyncio
from pathlib import Path

from crosscheck.domain.models import Claim, Evidence, Relation
from crosscheck.ml.relation import LABELS, RelationModel, features
from crosscheck.services.evidence_analyzer import (
    _duration,
    _subject_matches,
    _year_matches,
)


class LocalRelationAnalyzer:
    label = "local-relation-model"

    def __init__(self, model_path: Path, min_confidence: float = 0.62):
        self.model_path = model_path
        self.min_confidence = min_confidence
        self._model: RelationModel | None = None

    def _load(self) -> RelationModel:
        if self._model is None:
            self._model = RelationModel.load(self.model_path)
        return self._model

    async def analyze_evidence(self, claim: Claim, evidence: list[Evidence]) -> dict[str, dict[str, str]]:
        model = await asyncio.to_thread(self._load)
        decisions: dict[str, dict[str, str]] = {}
        for item in evidence:
            if not item.excerpt.strip() or item.excerpt.startswith("正文抓取失败"):
                decisions[item.id] = {"relation": Relation.INSUFFICIENT.value, "reason": "证据正文不可用。"}
                continue
            probabilities = model.predict_proba(features(
                claim.text,
                item.excerpt,
                subject=claim.subject,
                time=claim.time,
                object_=claim.object,
                action=claim.action,
                scope=claim.scope,
                duration_days=claim.duration_days,
            ))
            predicted = max(LABELS, key=probabilities.get)
            confidence = probabilities[predicted]
            # A model/rule disagreement stays unresolved. The model may
            # classify a rule-ambiguous item when it has enough confidence.
            unsafe_promotion = item.relation == Relation.INSUFFICIENT and (
                not _subject_matches(claim, item.excerpt)
                or not _year_matches(claim, item.excerpt)
                or (claim.duration_days is not None and _duration(item.excerpt) is None)
            )
            if confidence < self.min_confidence or unsafe_promotion or (
                item.relation != Relation.INSUFFICIENT and predicted != item.relation.value
            ):
                relation = Relation.INSUFFICIENT
            else:
                relation = Relation(predicted)
            reason = (
                f"本地证据分类模型判定为 {predicted}（置信度 {confidence:.2f}）；"
                f"规则初判为 {item.relation.value}。{item.analysis_reason}"
            )
            decisions[item.id] = {"relation": relation.value, "reason": reason[:500]}
        return decisions
