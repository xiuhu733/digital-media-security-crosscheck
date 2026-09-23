from typing import Protocol

from .models import Document, SearchResult


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> list[SearchResult]: ...


class ContentProvider(Protocol):
    name: str

    async def fetch(self, url: str) -> Document: ...
