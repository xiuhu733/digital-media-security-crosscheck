from crosscheck.domain.models import Document, SearchResult


class MockSearchProvider:
    name = "mock"

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        rows = [
            SearchResult(provider=self.name, url="https://example.com/official-notice", title="示例：官方政策说明", snippet=f"与“{query}”相关的官方说明。", domain="example.com"),
            SearchResult(provider=self.name, url="https://example.org/news-report", title="示例：新闻报道", snippet="报道对政策内容进行了转述。", domain="example.org"),
        ]
        return rows[:limit]


class MockContentProvider:
    name = "mock"

    async def fetch(self, url: str) -> Document:
        if "official" in url:
            content = "官方说明：政策仅适用于指定区域和未登记车辆，不涉及所有电动自行车。"
            title = "示例：官方政策说明"
        else:
            content = "新闻报道：部分车辆将在指定区域受到管理，具体以官方文件为准。"
            title = "示例：新闻报道"
        return Document(url=url, title=title, content=content, provider=self.name)
