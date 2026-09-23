import pytest

from crosscheck.services.llm_analyzer import analyze_claim_prefer_llm


@pytest.mark.asyncio
async def test_no_llm_key_uses_rules_with_explicit_warning():
    claim, method, warnings = await analyze_claim_prefer_llm("今年9月份北京无人机禁飞", None)
    assert method == "rules"
    assert claim.object == "无人机"
    assert warnings
