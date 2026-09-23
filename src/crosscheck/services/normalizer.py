import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from crosscheck.domain.models import Document, SearchResult


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    tracking_keys = {"ref", "ref_src", "source", "fbclid", "gclid"}
    query = [(k, v) for k, v in parse_qsl(parts.query) if not (k.lower().startswith("utm_") or k.lower() in tracking_keys)]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text.lower()).strip().encode()).hexdigest()


def deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    seen, unique = set(), []
    for item in results:
        key = canonicalize_url(str(item.url))
        if key not in seen:
            seen.add(key)
            unique.append(item.model_copy(update={"url": key}))
    return unique


def cluster_documents(documents: list[Document]) -> dict[str, list[str]]:
    clusters: dict[str, list[str]] = {}
    for document in documents:
        # Empty bodies are failed fetches, not copies of the same publication.
        key = content_fingerprint(document.content)[:12] if document.content.strip() else content_fingerprint(str(document.url))[:12]
        clusters.setdefault(key, []).append(str(document.url))
    return clusters
