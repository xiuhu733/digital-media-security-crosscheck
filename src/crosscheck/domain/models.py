from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


def utc_now() -> datetime:
    return datetime.now(UTC)


class Relation(str, Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    CONFLICTS = "conflicts"
    INSUFFICIENT = "insufficient"


class Claim(BaseModel):
    text: str
    subject: str | None = None
    action: str | None = None
    time: str | None = None
    location: str | None = None
    scope: str | None = None
    event: str | None = None
    duration_days: int | None = None
    object: str | None = None


class SearchResult(BaseModel):
    provider: str
    url: HttpUrl
    title: str = ""
    snippet: str = ""
    published_at: datetime | None = None
    domain: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Document(BaseModel):
    url: HttpUrl
    title: str = ""
    content: str
    provider: str
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    id: str
    url: HttpUrl
    title: str
    excerpt: str
    provider: str
    relation: Relation = Relation.INSUFFICIENT
    source_cluster_id: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    independent: bool = True
    content_sources: list[str] = Field(default_factory=list)
    fetch_failures: list[str] = Field(default_factory=list)
    analysis_reason: str = ""


class VerificationRequest(BaseModel):
    claim: str = Field(min_length=5, max_length=2000)
    urls: list[HttpUrl] = Field(default_factory=list)
    max_results: int | None = Field(default=None, ge=1, le=20)


class VerificationReport(BaseModel):
    request_id: str
    claim: Claim
    conclusion: Relation
    summary: str
    evidence: list[Evidence]
    source_clusters: dict[str, list[str]]
    searched_providers: list[str]
    created_at: datetime = Field(default_factory=utc_now)
    claim_analysis_method: str = "rules"
    evidence_analysis_method: str = "rules"
    analysis_warnings: list[str] = Field(default_factory=list)
