"""Conservative fusion of the local classifier and an LLM evidence judge."""

import asyncio

from crosscheck.domain.models import Claim, Evidence, Relation

from .llm_analyzer import LLMClaimAnalyzer, analyze_evidence_prefer_llm
from .local_relation_analyzer import LocalRelationAnalyzer


class HybridEvidenceAnalyzer:
    def __init__(
        self,
        local_analyzer: LocalRelationAnalyzer,
        llm_analyzers: list[LLMClaimAnalyzer] | None,
    ):
        self.local_analyzer = local_analyzer
        self.llm_analyzers = llm_analyzers

    async def analyze(self, claim: Claim, evidence: list[Evidence]) -> tuple[list[Evidence], str, list[str]]:
        if not evidence or all(not item.excerpt.strip() or item.excerpt.startswith("正文抓取失败") for item in evidence):
            return evidence, "rules", ["没有可用正文，无法判断证据关系。"]

        (local_items, local_method, local_warnings), (llm_items, llm_method, llm_warnings) = await asyncio.gather(
            analyze_evidence_prefer_llm(claim, evidence, self.local_analyzer),
            analyze_evidence_prefer_llm(claim, evidence, self.llm_analyzers),
        )
        warnings = local_warnings + llm_warnings
        if local_method == "rules" and llm_method == "rules":
            return evidence, "rules", warnings
        if local_method == "rules":
            warnings = [warning for warning in warnings if "已回退规则判断" not in warning]
            warnings.append("本地模型不可用，本次仅使用大模型判断证据。")
            return llm_items, llm_method, warnings
        if llm_method == "rules":
            warnings = [warning for warning in warnings if "已回退规则判断" not in warning and "使用规则证据判断" not in warning]
            warnings.append("大模型不可用，本次仅使用本地模型判断证据。")
            return local_items, local_method, warnings

        combined = []
        for local_item, llm_item in zip(local_items, llm_items, strict=True):
            if not local_item.excerpt.strip() or local_item.excerpt.startswith("正文抓取失败"):
                combined.append(local_item)
                continue
            if local_item.relation == llm_item.relation:
                relation = local_item.relation
                reason = (
                    f"本地模型与大模型（{llm_method}）均判定为 {relation.value}。"
                    f"本地依据：{local_item.analysis_reason} 大模型依据：{llm_item.analysis_reason}"
                )
            else:
                relation = Relation.INSUFFICIENT
                reason = (
                    f"本地模型判定为 {local_item.relation.value}，大模型（{llm_method}）判定为 "
                    f"{llm_item.relation.value}；结果存在分歧，需人工复核。"
                    f"本地依据：{local_item.analysis_reason} 大模型依据：{llm_item.analysis_reason}"
                )
            combined.append(local_item.model_copy(update={"relation": relation, "analysis_reason": reason[:500]}))
        return combined, f"hybrid-local-{llm_method}", warnings
