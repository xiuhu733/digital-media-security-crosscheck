"""Small, from-scratch multinomial classifier for claim/evidence pairs.

The model uses sparse character n-grams and explicit comparison features. It
has no pretrained weights or third-party NLP runtime.
"""

from __future__ import annotations

import gzip
import json
import math
import re
from collections import Counter
from pathlib import Path

LABELS = ("supports", "refutes", "insufficient")
MODEL_VERSION = 1


def _grams(text: str) -> set[str]:
    clean = re.sub(r"\s+", "", text.lower())[:1200]
    return {clean[i : i + width] for width in (2, 3) for i in range(max(0, len(clean) - width + 1))}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def features(claim: str, evidence: str, *, subject: str | None = None, time: str | None = None,
             object_: str | None = None, action: str | None = None, scope: str | None = None,
             duration_days: int | None = None) -> dict[str, float]:
    """Build bounded pair features from the claim and evidence text."""
    claim_grams = _grams(claim)
    evidence_grams = _grams(evidence)
    overlap = claim_grams & evidence_grams
    result: dict[str, float] = {"bias": 1.0}
    for gram in overlap:
        result[f"shared:{gram}"] = 1.0
    for gram in list(evidence_grams)[:500]:
        result[f"evidence:{gram}"] = 1.0
    result["overlap:low" if len(overlap) < 3 else "overlap:high"] = 1.0
    result["overlap:ratio"] = len(overlap) / max(1, len(claim_grams))
    for name, value in (("subject", subject), ("time", time), ("object", object_), ("action", action)):
        if value:
            result[f"{name}:{'match' if value in evidence else 'missing'}"] = 1.0
    if scope:
        result["scope:claim"] = 1.0
    for term in ("仅", "部分", "不涉及所有", "并非全部", "不是所有", "不", "未", "禁止", "取消"):
        if term in evidence:
            result[f"evidence_term:{term}"] = 1.0
    claim_numbers = _numbers(claim)
    evidence_numbers = _numbers(evidence)
    if duration_days is not None:
        claim_numbers.add(str(duration_days))
    if claim_numbers and evidence_numbers:
        result["number:shared" if claim_numbers & evidence_numbers else "number:different"] = 1.0
    elif claim_numbers and not evidence_numbers:
        result["number:missing"] = 1.0
    year = re.search(r"\d{4}", time or "")
    if year:
        result["year:match" if year.group() in evidence else "year:missing"] = 1.0
    return result


class RelationModel:
    def __init__(self, weights: dict[str, dict[str, float]] | None = None):
        self.weights = weights or {label: {} for label in LABELS}

    def predict_proba(self, vector: dict[str, float]) -> dict[str, float]:
        scores = {label: sum(self.weights[label].get(key, 0.0) * value for key, value in vector.items())
                  for label in LABELS}
        maximum = max(scores.values())
        exps = {label: math.exp(score - maximum) for label, score in scores.items()}
        total = sum(exps.values())
        return {label: value / total for label, value in exps.items()}

    def train(self, samples: list[dict], *, epochs: int = 24, learning_rate: float = 0.12,
              regularization: float = 0.0005) -> None:
        if not samples or {row["label"] for row in samples} != set(LABELS):
            raise ValueError("训练数据必须包含支持、反驳、证据不足三类。")
        for epoch in range(epochs):
            rate = learning_rate / (1 + epoch * 0.08)
            for row in samples:
                vector = features(row["claim"], row["evidence"], **row.get("fields", {}))
                probabilities = self.predict_proba(vector)
                for label in LABELS:
                    delta = (1.0 if row["label"] == label else 0.0) - probabilities[label]
                    weights = self.weights[label]
                    for key, value in vector.items():
                        old = weights.get(key, 0.0)
                        weights[key] = old + rate * (delta * value - regularization * old)

    def save(self, path: Path, *, metadata: dict | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": MODEL_VERSION, "labels": list(LABELS), "weights": self.weights,
                   "metadata": metadata or {}}
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if path.suffix == ".gz":
            with gzip.open(path, "wb") as handle:
                handle.write(serialized)
        else:
            path.write_bytes(serialized)

    @classmethod
    def load(cls, path: Path) -> RelationModel:
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as handle:
                payload = json.loads(handle.read().decode("utf-8"))
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != MODEL_VERSION or tuple(payload.get("labels", ())) != LABELS:
            raise ValueError("证据分类模型格式不兼容。")
        weights = payload["weights"]
        if set(weights) != set(LABELS):
            raise ValueError("证据分类模型缺少类别。")
        return cls(weights)


def metrics(model: RelationModel, samples: list[dict]) -> dict:
    confusion = {label: Counter() for label in LABELS}
    for row in samples:
        vector = features(row["claim"], row["evidence"], **row.get("fields", {}))
        probabilities = model.predict_proba(vector)
        predicted = max(LABELS, key=probabilities.get)
        confusion[row["label"]][predicted] += 1
    per_class = {}
    for label in LABELS:
        tp = confusion[label][label]
        predicted_total = sum(confusion[other][label] for other in LABELS)
        actual_total = sum(confusion[label].values())
        precision = tp / predicted_total if predicted_total else 0.0
        recall = tp / actual_total if actual_total else 0.0
        per_class[label] = {"precision": round(precision, 3), "recall": round(recall, 3),
                            "f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
                            "support": actual_total}
    total = len(samples)
    correct = sum(confusion[label][label] for label in LABELS)
    return {"count": total, "accuracy": round(correct / total, 3) if total else 0.0,
            "macro_f1": round(sum(row["f1"] for row in per_class.values()) / len(LABELS), 3),
            "per_class": per_class,
            "confusion": {label: dict(confusion[label]) for label in LABELS}}
