import pytest

from crosscheck.services.llm_analyzer import analyze_claim_prefer_llm


class BrokenAnalyzer:
    label = "llm-primary"

    async def analyze(self, text):
        raise RuntimeError("primary unavailable")


class BackupAnalyzer:
    label = "llm-backup"

    async def analyze(self, text):
        from crosscheck.services.claim_analyzer import analyze_claim

        return analyze_claim(text)


@pytest.mark.asyncio
async def test_primary_failure_uses_backup_model():
    claim, method, warnings = await analyze_claim_prefer_llm("今年9月份北京无人机禁飞", [BrokenAnalyzer(), BackupAnalyzer()])
    assert method == "llm-backup"
    assert claim.object == "无人机"
    assert warnings == []
