import asyncio
import time

import pytest

from crosscheck.domain.models import Document, Relation, VerificationRequest
from crosscheck.providers.mock import MockSearchProvider
from crosscheck.services.orchestrator import VerificationService


class PartlyFailingContent:
    name = "firecrawl"

    async def fetch(self, url: str):
        if "official" in url:
            return Document(url=url, title="官方来源", content="正式通知：部分区域实施管理。", provider=self.name)
        raise RuntimeError("page unavailable")


@pytest.mark.asyncio
async def test_one_scrape_failure_keeps_other_evidence():
    report = await VerificationService([MockSearchProvider()], PartlyFailingContent(), 5, 8).verify(
        VerificationRequest(claim="某市全面禁止所有电动自行车上路")
    )
    assert len(report.evidence) == 2
    assert any(item.relation == Relation.INSUFFICIENT for item in report.evidence)
    assert any("正文抓取失败" in item.excerpt for item in report.evidence)


class DelayedContent:
    def __init__(self, name: str):
        self.name = name

    async def fetch(self, url: str):
        await asyncio.sleep(0.05)
        return Document(url=url, title=self.name, content=f"{self.name} 正式通知内容", provider=self.name)


@pytest.mark.asyncio
async def test_exa_and_firecrawl_content_fetch_in_parallel():
    started = time.monotonic()
    report = await VerificationService(
        [MockSearchProvider()], [DelayedContent("firecrawl"), DelayedContent("exa")], 5, 1
    ).verify(VerificationRequest(claim="某市发布一项正式通知"))
    elapsed = time.monotonic() - started
    assert elapsed < 0.12
    assert report.evidence[0].provider == "firecrawl"
