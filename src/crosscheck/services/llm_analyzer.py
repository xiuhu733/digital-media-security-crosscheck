"""Structured claim understanding through an OpenAI-compatible endpoint."""

import json
import logging
import os
import re
import time
from uuid import uuid4

import httpx
from pydantic import ValidationError

from crosscheck.diagnostics import (
    elapsed_ms,
    endpoint_host,
    error_fields,
    event,
    fingerprint,
    redact,
    register_secrets,
)
from crosscheck.domain.models import Claim, Evidence, Relation

from .claim_analyzer import analyze_claim


def _failure_detail(exc: Exception) -> str:
    # Only expose known local messages; gateway bodies can contain sensitive data.
    if isinstance(exc, httpx.HTTPStatusError):
        reason = f"模型服务返回 HTTP {exc.response.status_code}"
    elif isinstance(exc, httpx.TimeoutException):
        reason = "模型请求超时"
    elif isinstance(exc, json.JSONDecodeError):
        reason = "模型或接口返回了格式不完整或不合法的 JSON"
    elif isinstance(exc, ValidationError):
        reason = "模型返回的字段类型不符合要求"
    elif isinstance(exc, ValueError) and str(exc) in {
        "模型未返回可解析的 JSON 对象",
        "模型未返回有效的证据判断",
        "模型未返回全部可用证据的判断",
        "模型返回的 duration_days（天数）无法转换为整数",
    }:
        reason = str(exc)
    elif isinstance(exc, ValueError):
        reason = "返回值处理失败，具体原因尚未记录"
    else:
        reason = "模型调用或响应处理失败"
    return f"{reason}（{type(exc).__name__}）"

SYSTEM_PROMPT = """你是中文事实核验系统的主张解析器。
只从用户提供的原句中提取信息，不要回答问题，不要闲聊，不要询问澄清问题。
你的整个回复必须是一个 JSON 对象，不能包含 Markdown、解释或其他文字。
字段：subject, object, action, event, time, location, scope, duration_days。
time 保留原文中的时间表达；能确定时可同时标准化为 YYYY年MM月，但不要猜测当前年份。
duration_days 必须是整数或 null。无法确定的字段必须为 null。
"""

EVIDENCE_SYSTEM_PROMPT = """你是中文事实核验系统的证据判断器。
给定一条待核验主张和若干编号证据，判断每条证据与主张的关系。
只能输出一个 JSON 对象：{"items":[{"id":"E1","relation":"supports|refutes|insufficient","reason":"简短中文理由"}]}。
只能使用输入中已有的证据编号；不要编造来源、事实或引用。
supports：证据明确支持主张的主体、时间、对象、行为或关键数量。
refutes：证据明确否定主张，或关键数量、时间、范围与主张冲突。
insufficient：证据相关但缺少关键事实，或无法确定支持/反驳。
"""


def _extract_json_object(content: str) -> dict:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    # Some gateways prepend a short sentence. Recover the first balanced JSON
    # object instead of making the whole primary-model attempt fail.
    start = cleaned.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    value = json.loads(cleaned[start : index + 1])
                    if isinstance(value, dict):
                        return value
                    break
    raise ValueError("模型未返回可解析的 JSON 对象")


class LLMClaimAnalyzer:
    name = "llm"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0, label: str = "llm", log_response_preview: bool = False):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.label = label
        self.log_response_preview = log_response_preview
        register_secrets(api_key)

    async def _request_json(self, payload: dict, stage: str) -> dict:
        attempt_id = str(uuid4())
        start = time.monotonic()
        fields = {"attempt_id": attempt_id, "stage": stage, "model_role": self.label, "model": self.model,
                  "endpoint_host": endpoint_host(self.base_url), "timeout_seconds": self.timeout}
        event("llm_request_started", **fields, input_chars=sum(len(m["content"]) for m in payload["messages"]),
              environment_proxy_present=any(os.getenv(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")))
        content = None
        finish_reason = None
        try:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                event("llm_http_response", **fields, http_status=response.status_code,
                      elapsed_ms=elapsed_ms(start), response_bytes=len(response.content),
                      upstream_request_id=response.headers.get("x-request-id"),
                      content_type=response.headers.get("content-type"))
                response.raise_for_status()
                data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            finish_reason = choice.get("finish_reason")
            usage = data.get("usage") or {}
            event("llm_response_shape", **fields, finish_reason=finish_reason,
                  content_type=type(content).__name__, content_chars=len(content) if isinstance(content, str) else None,
                  content_fingerprint=fingerprint(content) if isinstance(content, str) else None,
                  has_reasoning=bool(message.get("reasoning_content")), has_tool_calls=bool(message.get("tool_calls")),
                  refusal=bool(message.get("refusal")),
                  usage={key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(usage.get(key), int)})
            if not isinstance(content, str) or not content.strip():
                raise ValueError("模型未返回可解析的 JSON 对象")
            parsed = _extract_json_object(content)
            event("llm_json_parsed", **fields, elapsed_ms=elapsed_ms(start))
            return parsed
        except BaseException as exc:
            detail = error_fields(exc)
            detail["finish_reason"] = finish_reason
            if isinstance(content, str):
                detail["content_format"] = "empty" if not content.strip() else ("json_like" if "{" in content else "plain_text")
                if self.log_response_preview:
                    detail["response_preview"] = redact(content)[:2000]
                    detail["preview_truncated"] = len(redact(content)) > 2000
            event("llm_request_failed", level=logging.WARNING, **fields, elapsed_ms=elapsed_ms(start), **detail)
            raise

    async def analyze(self, text: str) -> Claim:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        }
        parsed = await self._request_json(payload, "claim")
        parsed["text"] = text
        if parsed.get("duration_days") is not None:
            try:
                parsed["duration_days"] = int(parsed["duration_days"])
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError("模型返回的 duration_days（天数）无法转换为整数") from exc
        claim = Claim.model_validate(parsed)
        # Keep deterministic normalization for relative dates and fill fields
        # the model may omit, while leaving the model's semantic labels intact.
        baseline = analyze_claim(text)
        updates = {}
        for field in ("subject", "object", "action", "event", "location", "scope", "duration_days"):
            if getattr(claim, field) is None and getattr(baseline, field) is not None:
                updates[field] = getattr(baseline, field)
        if claim.time and (any(word in claim.time for word in ("今年", "明年", "去年")) or not any(char.isdigit() for char in claim.time)):
            updates["time"] = baseline.time
        return claim.model_copy(update=updates)

    async def analyze_evidence(self, claim: Claim, evidence: list[Evidence]) -> dict[str, dict[str, str]]:
        evidence_text = "\n\n".join(
            f"[{item.id}] 标题：{item.title}\n正文摘录：{item.excerpt[:3500]}"
            for item in evidence
        )
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": EVIDENCE_SYSTEM_PROMPT},
                {"role": "user", "content": f"待核验主张：{claim.text}\n主张结构：{claim.model_dump_json()}\n\n证据：\n{evidence_text}"},
            ],
        }
        parsed = await self._request_json(payload, "evidence")
        allowed = {item.id for item in evidence}
        decisions: dict[str, dict[str, str]] = {}
        for item in parsed.get("items", []):
            if not isinstance(item, dict) or item.get("id") not in allowed:
                continue
            relation = item.get("relation")
            if relation not in {value.value for value in Relation if value != Relation.CONFLICTS}:
                continue
            decisions[item["id"]] = {"relation": relation, "reason": str(item.get("reason") or "模型未提供理由")[:500]}
        expected = {item.id for item in evidence if not item.excerpt.startswith("正文抓取失败") and item.excerpt.strip()}
        if expected and not expected.issubset(decisions):
            raise ValueError("模型未返回全部可用证据的判断")
        return decisions


async def analyze_claim_prefer_llm(text: str, analyzer: LLMClaimAnalyzer | list[LLMClaimAnalyzer] | None) -> tuple[Claim, str, list[str]]:
    analyzers = analyzer if isinstance(analyzer, list) else ([analyzer] if analyzer else [])
    if not analyzers:
        event("llm_rules_fallback", stage="claim", reason="not_configured")
        return analyze_claim(text), "rules", ["未配置主张解析器，使用规则解析。"]
    warnings = []
    uses_llm = all(getattr(candidate, "label", "").startswith("llm") for candidate in analyzers)
    prefix = "llm" if uses_llm else "claim_parser"
    for index, candidate in enumerate(analyzers):
        start = time.monotonic()
        event(f"{prefix}_attempt_started", stage="claim", model_role=candidate.label, attempt=index + 1)
        try:
            claim = await candidate.analyze(text)
            event(f"{prefix}_attempt_succeeded", stage="claim", model_role=candidate.label, elapsed_ms=elapsed_ms(start), fallback_used=index > 0)
            return claim, candidate.label, []
        except Exception as exc:  # noqa: BLE001
            event(f"{prefix}_attempt_failed", level=logging.WARNING, stage="claim", model_role=candidate.label, elapsed_ms=elapsed_ms(start), reason=_failure_detail(exc), **error_fields(exc))
            warnings.append(f"{candidate.label}解析失败：{_failure_detail(exc)}")
    event(f"{prefix}_rules_fallback", level=logging.WARNING, stage="claim", reason="all_models_failed")
    warnings.append("所有主张解析器均不可用，已回退规则解析。")
    return analyze_claim(text), "rules", warnings


async def analyze_evidence_prefer_llm(claim: Claim, evidence: list[Evidence], analyzer: LLMClaimAnalyzer | list[LLMClaimAnalyzer] | None) -> tuple[list[Evidence], str, list[str]]:
    analyzers = analyzer if isinstance(analyzer, list) else ([analyzer] if analyzer else [])
    if not evidence or all(item.excerpt.startswith("正文抓取失败") or not item.excerpt.strip() for item in evidence):
        return evidence, "rules", ["没有可用正文，无法判断证据关系。"]
    if not analyzers:
        event("llm_rules_fallback", stage="evidence", reason="not_configured")
        return evidence, "rules", ["未配置证据判定模型，使用规则证据判断。"]
    warnings = []
    uses_llm = all(getattr(candidate, "label", "").startswith("llm") for candidate in analyzers)
    prefix = "llm" if uses_llm else "evidence_model"
    for index, candidate in enumerate(analyzers):
        start = time.monotonic()
        event(f"{prefix}_attempt_started", stage="evidence", model_role=candidate.label, attempt=index + 1)
        try:
            decisions = await candidate.analyze_evidence(claim, evidence)
            updated = []
            for item in evidence:
                decision = decisions.get(item.id)
                if decision and item.excerpt.strip() and not item.excerpt.startswith("正文抓取失败"):
                    updated.append(item.model_copy(update={"relation": Relation(decision["relation"]), "analysis_reason": decision["reason"]}))
                else:
                    updated.append(item)
            event(f"{prefix}_attempt_succeeded", stage="evidence", model_role=candidate.label, elapsed_ms=elapsed_ms(start), fallback_used=index > 0, decision_count=len(decisions))
            return updated, candidate.label, []
        except Exception as exc:  # noqa: BLE001
            event(f"{prefix}_attempt_failed", level=logging.WARNING, stage="evidence", model_role=candidate.label, elapsed_ms=elapsed_ms(start), reason=_failure_detail(exc), **error_fields(exc))
            warnings.append(f"{candidate.label}证据判断失败：{_failure_detail(exc)}")
    warnings.append("所有证据判定模型均不可用，已回退规则判断。")
    event(f"{prefix}_rules_fallback", level=logging.WARNING, stage="evidence", reason="all_models_failed")
    return evidence, "rules", warnings
