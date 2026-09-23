from crosscheck.diagnostics import provider_client
from crosscheck.domain.models import Document, SearchResult


class ExaSearchProvider:
    name = "exa"

    def __init__(self, api_key: str, base_url: str, timeout: float = 60.0):
        self.api_key, self.base_url, self.timeout = api_key, base_url.rstrip("/"), timeout

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        payload = {"query": query, "numResults": limit, "contents": {"text": {"maxCharacters": 1000}}}
        async with provider_client(self.name, "search", self.base_url, self.timeout, self.api_key) as client:
            response = await client.post(f"{self.base_url}/search", json=payload, headers={"x-api-key": self.api_key, "content-type": "application/json"})
            response.raise_for_status()
            data = response.json()
        return [SearchResult(provider=self.name, url=item["url"], title=item.get("title", ""), snippet=item.get("text", item.get("highlight", ""))[:2000], published_at=item.get("publishedDate"), metadata={"raw": item}) for item in data.get("results", []) if item.get("url")]

    async def fetch(self, url: str) -> Document:
        """Fetch one page through Exa Contents for parallel provider fallback."""
        payload = {"urls": [url], "text": True, "highlights": False}
        async with provider_client(self.name, "fetch", self.base_url, self.timeout, self.api_key, url) as client:
            response = await client.post(
                f"{self.base_url}/contents",
                json=payload,
                headers={"x-api-key": self.api_key, "content-type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        result = (data.get("results") or [None])[0]
        if not result or not result.get("text"):
            raise ValueError("Exa Contents 没有返回正文")
        return Document(
            url=url,
            title=result.get("title", ""),
            content=result["text"],
            provider=self.name,
            published_at=result.get("publishedDate"),
            metadata={"raw": result},
        )
