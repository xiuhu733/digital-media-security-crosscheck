from crosscheck.domain.models import Document, SearchResult
from crosscheck.services.normalizer import (
    canonicalize_url,
    cluster_documents,
    deduplicate_results,
)


def test_canonicalize_removes_tracking_parameters():
    assert canonicalize_url("HTTPS://Example.COM/a/?utm_source=x&b=1#part") == "https://example.com/a?b=1"


def test_deduplicate_results_by_url():
    rows = [SearchResult(provider="a", url="https://example.com/a?utm_source=x"), SearchResult(provider="b", url="https://example.com/a")]
    assert len(deduplicate_results(rows)) == 1


def test_canonicalize_preserves_nontracking_reference_ids():
    assert canonicalize_url("https://example.com/article?reference=42&source_id=7&ref=feed") == "https://example.com/article?reference=42&source_id=7"


def test_failed_pages_are_not_clustered_as_identical_sources():
    documents = [
        Document(url="https://example.com/a", content="", provider="mock"),
        Document(url="https://example.com/b", content="", provider="mock"),
    ]
    assert len(cluster_documents(documents)) == 2
