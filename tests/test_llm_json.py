import pytest

from crosscheck.services.llm_analyzer import (
    _extract_json_object,
    _failure_detail,
    analyze_claim_prefer_llm,
)


def test_extracts_json_after_markdown_or_explanation():
    assert _extract_json_object('```json\n{"subject":"北京"}\n```') == {"subject": "北京"}
    assert _extract_json_object('好的，结果如下：{"subject":"北京"}') == {"subject": "北京"}


class NonJsonAnalyzer:
    label = "llm-primary"

    async def analyze(self, text):
        raise ValueError("模型未返回可解析的 JSON 对象")


@pytest.mark.asyncio
async def test_non_json_primary_is_reported_for_fallback():
    claim, method, warnings = await analyze_claim_prefer_llm("北理工今年国庆放假7天", [NonJsonAnalyzer()])
    assert method == "rules"
    assert "ValueError" in warnings[0]
    assert "模型未返回可解析的 JSON 对象" in warnings[0]
    assert claim.subject == "北京理工大学"


def test_failure_detail_does_not_expose_arbitrary_exception_text():
    detail = _failure_detail(ValueError("private gateway body and secret key"))
    assert "private" not in detail
    assert "ValueError" in detail


def test_duration_failure_has_specific_message():
    detail = _failure_detail(ValueError("模型返回的 duration_days（天数）无法转换为整数"))
    assert "天数" in detail
    assert "整数" in detail
