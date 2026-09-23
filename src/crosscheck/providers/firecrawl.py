from crosscheck.diagnostics import provider_client
from crosscheck.domain.models import Document, SearchResult


class FirecrawlProvider:
    name = "firecrawl"

    def __init__(self, api_key: str, base_url: str, timeout: float = 60.0):
        self.api_key, self.base_url, self.timeout = api_key, base_url.rstrip("/"), timeout

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        # Search is discovery only. Requesting scrapeOptions here makes Firecrawl
        # fetch and render every result, which is much slower and is unnecessary
        # because the orchestrator scrapes selected URLs in the next step.
        payload = {
            "query": query,
            "limit": limit,
            "sources": ["web"],
            "highlights": True,
            "timeout": max(60_000, int(self.timeout * 1000)),
        }
        async with provider_client(self.name, "search", self.base_url, max(self.timeout, 65.0), self.api_key) as client:
            response = await client.post(f"{self.base_url}/v2/search", json=payload, headers=self._headers())
            response.raise_for_status()
            data = response.json()
        # v2 groups web results under data.web. Keep the v1 shape as a fallback
        # for self-hosted installations that still return data/results.
        body = data.get("data", data)
        rows = body.get("web", body.get("results", [])) if isinstance(body, dict) else []
        return [
            SearchResult(
                provider=self.name,
                url=item["url"],
                title=item.get("title", ""),
                snippet=item.get("description", item.get("snippet", item.get("markdown", "")))[:2000],
                published_at=item.get("publishedDate", item.get("date")),
                metadata={"raw": item},
            )
            for item in rows
            if item.get("url")
        ]

    async def fetch(self, url: str) -> Document:
        async with provider_client(self.name, "fetch", self.base_url, max(self.timeout, 65.0), self.api_key, url) as client:
            response = await client.post(
                f"{self.base_url}/v2/scrape",
                json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
                headers=self._headers(),
            )
            response.raise_for_status()
            response_data = response.json()
            data = response_data.get("data", response_data)
        metadata = data.get("metadata", {})
        return Document(url=url, title=metadata.get("title", ""), content=data.get("markdown", data.get("content", "")), provider=self.name, published_at=metadata.get("publishedTime"), metadata={"raw": data})
