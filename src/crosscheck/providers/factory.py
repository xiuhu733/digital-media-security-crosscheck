from crosscheck.config import Settings
from crosscheck.services.hybrid_evidence_analyzer import HybridEvidenceAnalyzer
from crosscheck.services.llm_analyzer import LLMClaimAnalyzer
from crosscheck.services.local_relation_analyzer import LocalRelationAnalyzer

from .exa import ExaSearchProvider
from .firecrawl import FirecrawlProvider
from .mock import MockContentProvider, MockSearchProvider


def build_search_providers(settings: Settings):
    providers = []
    for name in settings.provider_names():
        if name == "mock":
            providers.append(MockSearchProvider())
        elif name == "exa" and settings.exa_api_key:
            providers.append(ExaSearchProvider(settings.exa_api_key, settings.exa_api_url, settings.request_timeout_seconds))
        elif name == "firecrawl" and settings.firecrawl_api_key:
            providers.append(FirecrawlProvider(settings.firecrawl_api_key, settings.firecrawl_api_url, settings.request_timeout_seconds))
        else:
            raise ValueError(f"未知供应商或缺少密钥: {name}")
    return providers


def build_content_provider(settings: Settings):
    if settings.content_provider == "mock":
        return MockContentProvider()
    if settings.content_provider == "firecrawl" and settings.firecrawl_api_key:
        return FirecrawlProvider(settings.firecrawl_api_key, settings.firecrawl_api_url, settings.request_timeout_seconds)
    raise ValueError(f"内容供应商未配置或不支持: {settings.content_provider}")


def build_content_providers(settings: Settings):
    """Return preferred content provider first, plus configured parallel fallback."""
    if settings.content_provider == "mock":
        return [MockContentProvider()]
    providers = []
    if settings.content_provider == "firecrawl" and settings.firecrawl_api_key:
        providers.append(FirecrawlProvider(settings.firecrawl_api_key, settings.firecrawl_api_url, settings.request_timeout_seconds))
    elif settings.content_provider == "exa" and settings.exa_api_key:
        providers.append(ExaSearchProvider(settings.exa_api_key, settings.exa_api_url, settings.request_timeout_seconds))
    else:
        raise ValueError(f"内容供应商未配置或不支持: {settings.content_provider}")
    if settings.exa_api_key and not any(item.name == "exa" for item in providers):
        providers.append(ExaSearchProvider(settings.exa_api_key, settings.exa_api_url, settings.request_timeout_seconds))
    if settings.firecrawl_api_key and not any(item.name == "firecrawl" for item in providers):
        providers.append(FirecrawlProvider(settings.firecrawl_api_key, settings.firecrawl_api_url, settings.request_timeout_seconds))
    return providers


def build_llm_analyzer(settings: Settings):
    analyzers = []
    timeout = max(settings.request_timeout_seconds, 30.0)
    if settings.llm_api_key:
        analyzers.append(LLMClaimAnalyzer(settings.llm_api_key, settings.llm_api_url, settings.llm_model, timeout, "llm-primary", settings.log_llm_response_preview))
    if settings.backup_llm_api_key:
        analyzers.append(LLMClaimAnalyzer(settings.backup_llm_api_key, settings.backup_llm_api_url, settings.backup_llm_model, timeout, "llm-backup", settings.log_llm_response_preview))
    return analyzers or None


def build_claim_analyzers(settings: Settings):
    """Build the selected claim parser while keeping a rules fallback."""
    if settings.claim_analyzer == "rules":
        return None
    return build_llm_analyzer(settings)


def build_evidence_analyzers(settings: Settings):
    """Build the selected evidence relation judge."""
    if settings.evidence_analyzer == "rules":
        return None
    if settings.evidence_analyzer == "local":
        return [LocalRelationAnalyzer(settings.local_relation_model_path)]
    if settings.evidence_analyzer == "hybrid":
        return HybridEvidenceAnalyzer(
            LocalRelationAnalyzer(settings.local_relation_model_path),
            build_llm_analyzer(settings),
        )
    return build_llm_analyzer(settings)
