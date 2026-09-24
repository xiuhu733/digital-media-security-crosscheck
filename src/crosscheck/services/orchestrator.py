import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from crosscheck.diagnostics import (
    elapsed_ms,
    endpoint_host,
    error_fields,
    event,
    fingerprint,
)
from crosscheck.diagnostics import request_id as trace_id
from crosscheck.domain.models import (
    Document,
    SearchResult,
    VerificationReport,
    VerificationRequest,
)
from crosscheck.domain.ports import ContentProvider, SearchProvider

from .errors import ProviderFailure
from .evidence_analyzer import analyze_evidence, summarize_relation
from .hybrid_evidence_analyzer import HybridEvidenceAnalyzer
from .llm_analyzer import (
    LLMClaimAnalyzer,
    analyze_claim_prefer_llm,
    analyze_evidence_prefer_llm,
)
from .normalizer import cluster_documents, deduplicate_results


class VerificationService:
    def __init__(self, search_providers: list[SearchProvider], content_provider: ContentProvider | list[ContentProvider], max_results: int, max_evidence_items: int, llm_analyzer: LLMClaimAnalyzer | list[LLMClaimAnalyzer] | None = None, *, claim_analyzer=None, evidence_analyzer=None):
        self.search_providers = search_providers
        self.content_providers = content_provider if isinstance(content_provider, list) else [content_provider]
        self.max_results, self.max_evidence_items = max_results, max_evidence_items
        self.llm_analyzer = llm_analyzer
        # Keep the positional llm_analyzer argument for existing callers while
        # Claim parsing and evidence relation judging are independent stages.
        self.claim_analyzer = claim_analyzer if claim_analyzer is not None else llm_analyzer
        candidates = evidence_analyzer if evidence_analyzer is not None else llm_analyzer
        candidate_list = candidates if isinstance(candidates, list) else ([candidates] if candidates else [])
        self.evidence_analyzer = candidates if isinstance(candidates, HybridEvidenceAnalyzer) or (
            candidate_list and all(hasattr(item, "analyze_evidence") for item in candidate_list)
        ) else None

    async def _search(self, provider: SearchProvider, query: str, limit: int):
        start = time.monotonic()
        fields = {"provider": provider.name, "stage": "search"}
        event("provider_started", **fields, limit=limit)
        try:
            results = await provider.search(query, limit)
            event("provider_succeeded", **fields, elapsed_ms=elapsed_ms(start), result_count=len(results))
            return results
        except Exception as exc:
            event("provider_failed", level=logging.WARNING, **fields, elapsed_ms=elapsed_ms(start), **error_fields(exc))
            raise ProviderFailure(provider.name, "搜索", exc) from exc

    async def _fetch_one(self, provider: ContentProvider, url: str):
        start = time.monotonic()
        fields = {"provider": provider.name, "stage": "fetch", "page_host": endpoint_host(url), "page_id": fingerprint(url)}
        event("provider_started", **fields)
        try:
            document = await provider.fetch(url)
            event("provider_succeeded", **fields, elapsed_ms=elapsed_ms(start), content_chars=len(document.content))
            return document
        except Exception as exc:  # noqa: BLE001
            event("provider_failed", level=logging.WARNING, **fields, elapsed_ms=elapsed_ms(start), **error_fields(exc))
            return ProviderFailure(provider.name, "正文抓取", exc)

    async def _fetch(self, url: str):
        results = await asyncio.gather(*(self._fetch_one(provider, url) for provider in self.content_providers))
        successes = [result for result in results if not isinstance(result, ProviderFailure) and result.content.strip()]
        if successes:
            # Provider order is preference order; the other successful result is
            # retained in metadata for traceability and future comparison.
            selected = successes[0]
            sources = [result.provider for result in successes]
            failures = [str(result) if isinstance(result, ProviderFailure) else f"{result.provider} 未返回正文"
                        for result in results if isinstance(result, ProviderFailure) or not result.content.strip()]
            metadata = dict(selected.metadata)
            metadata["content_sources"] = sources
            if failures:
                metadata["content_failures"] = failures
            event("content_selected", page_id=fingerprint(url), selected_provider=selected.provider, successful_providers=sources, failed_count=len(failures))
            return selected.model_copy(update={"metadata": metadata})
        return next((result for result in results if isinstance(result, ProviderFailure)),
                    ProviderFailure("content", "正文抓取", RuntimeError("所有正文供应商均未返回内容")))

    async def _progress(self, callback: Callable[[dict], Awaitable[None]] | None, stage: str, message: str, percent: int, **extra):
        event("verification_progress", stage=stage, percent=percent, **extra)
        if callback:
            await callback({"stage": stage, "message": message, "percent": percent, **extra})

    async def verify(self, request: VerificationRequest, progress: Callable[[dict], Awaitable[None]] | None = None) -> VerificationReport:
        token = trace_id.set(trace_id.get() or str(uuid4()))
        start = time.monotonic()
        stage_start = start
        current_stage = None

        async def tracked_progress(update):
            nonlocal stage_start, current_stage
            if current_stage is not None:
                event("stage_finished", stage=current_stage, elapsed_ms=elapsed_ms(stage_start))
            current_stage = update["stage"]
            stage_start = time.monotonic()
            if progress:
                await progress(update)

        event("verification_started", claim_chars=len(request.claim), claim_fingerprint=fingerprint(request.claim),
              search_providers=[p.name for p in self.search_providers], content_providers=[p.name for p in self.content_providers])
        try:
            report = await self._verify(request, tracked_progress)
            event("verification_completed", elapsed_ms=elapsed_ms(start), conclusion=report.conclusion.value,
                  claim_method=report.claim_analysis_method, evidence_method=report.evidence_analysis_method,
                  evidence_count=len(report.evidence), warning_count=len(report.analysis_warnings))
            return report
        except BaseException as exc:
            event("verification_failed", level=logging.ERROR, elapsed_ms=elapsed_ms(start), **error_fields(exc))
            raise
        finally:
            if current_stage is not None:
                event("stage_finished", stage=current_stage, elapsed_ms=elapsed_ms(stage_start))
            trace_id.reset(token)

    async def _verify(self, request: VerificationRequest, progress: Callable[[dict], Awaitable[None]] | None = None) -> VerificationReport:
        await self._progress(progress, "claim", "正在理解待核验说法…", 8)
        claim, claim_analysis_method, analysis_warnings = await analyze_claim_prefer_llm(request.claim, self.claim_analyzer)
        await self._progress(progress, "claim_done", f"已识别主张结构（{claim_analysis_method.upper()}）", 18, method=claim_analysis_method)
        limit = request.max_results or self.max_results
        await self._progress(progress, "search", f"正在并行检索 {len(self.search_providers)} 个来源…", 24, providers=[provider.name for provider in self.search_providers])
        search_results = await asyncio.gather(
            *(self._search(provider, claim.text, limit) for provider in self.search_providers),
            return_exceptions=True,
        )
        groups = [result for result in search_results if not isinstance(result, Exception)]
        failures = [result for result in search_results if isinstance(result, Exception)]
        if not groups and not request.urls:
            raise failures[0] if failures else RuntimeError("没有可用的搜索供应商")
        # Explicitly submitted URLs are part of the verification request and
        # must not disappear just because discovery failed or returned nothing.
        submitted = [SearchResult(provider="user", url=url, title="用户提供的来源") for url in request.urls]
        candidates = deduplicate_results(submitted + [item for group in groups for item in group])
        await self._progress(progress, "search_done", f"检索完成，发现 {len(candidates)} 个候选来源", 42, count=len(candidates))
        selected = candidates[: self.max_evidence_items]
        await self._progress(progress, "fetch", f"正在并行读取 {len(selected)} 个页面…", 48, count=len(selected))
        fetched = await asyncio.gather(*(self._fetch(str(item.url)) for item in selected))
        documents = []
        for candidate, result in zip(selected, fetched, strict=True):
            if isinstance(result, ProviderFailure):
                documents.append(
                    Document(
                        url=candidate.url,
                        title=candidate.title or "未命名来源",
                        content="",
                        provider=self.content_providers[0].name,
                        metadata={"fetch_error": str(result)},
                    )
                )
            else:
                documents.append(result)
        await self._progress(progress, "fetch_done", f"页面读取完成，获得 {sum(bool(document.content) for document in documents)} 份正文", 72, count=len(documents))
        clusters = cluster_documents(documents)
        await self._progress(progress, "compare", "正在逐条比较主张与证据…", 80)
        evidence = analyze_evidence(claim, documents)
        cluster_for_url = {url: cluster_id for cluster_id, urls in clusters.items() for url in urls}
        evidence = [item.model_copy(update={"source_cluster_id": cluster_for_url.get(str(item.url))}) for item in evidence]
        await self._progress(progress, "judge", "正在使用证据判定器判断关系…", 88)
        if isinstance(self.evidence_analyzer, HybridEvidenceAnalyzer):
            evidence, evidence_analysis_method, evidence_warnings = await self.evidence_analyzer.analyze(claim, evidence)
        else:
            evidence, evidence_analysis_method, evidence_warnings = await analyze_evidence_prefer_llm(claim, evidence, self.evidence_analyzer)
        analysis_warnings.extend(evidence_warnings)
        conclusion, summary = summarize_relation(evidence)
        await self._progress(progress, "complete", "核验完成，正在保存报告", 97, conclusion=conclusion.value)
        return VerificationReport(request_id=trace_id.get(), claim=claim, conclusion=conclusion, summary=summary, evidence=evidence, source_clusters=clusters, searched_providers=[provider.name for provider in self.search_providers], claim_analysis_method=claim_analysis_method, evidence_analysis_method=evidence_analysis_method, analysis_warnings=analysis_warnings)
