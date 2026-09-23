"""Structured, bounded diagnostics without request bodies or credentials."""

import hashlib
import json
import logging
import os
import re
import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

request_id: ContextVar[str | None] = ContextVar("diagnostic_request_id", default=None)
logger = logging.getLogger("crosscheck.diagnostics")
_lock = threading.Lock()
_handler: RotatingFileHandler | None = None
_secrets: set[str] = set()


def register_secrets(*values: str | None) -> None:
    with _lock:
        _secrets.update(value for value in values if value)


def redact(value: str) -> str:
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)bearer\s+[\w.+/=-]+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)([\"']?(?:api[_-]?key|token|password|secret|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}&]+", r"\1[REDACTED]", value)
    return value


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def elapsed_ms(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


def endpoint_host(url: str) -> str:
    try:
        return urlsplit(url).hostname or "unknown"
    except ValueError:
        return "invalid"


def error_fields(exc: BaseException) -> dict:
    response = getattr(exc, "response", None)
    # Stack locations are useful without persisting exception text or locals.
    frames = []
    tb = exc.__traceback__
    while tb:
        frames.append(f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}:{tb.tb_frame.f_code.co_name}")
        tb = tb.tb_next
    fields = {"error_type": type(exc).__name__, "http_status": getattr(response, "status_code", None), "stack": frames[-8:]}
    if isinstance(exc, json.JSONDecodeError):
        fields.update(json_line=exc.lineno, json_column=exc.colno, json_position=exc.pos)
    if isinstance(exc, ValidationError):
        fields["invalid_fields"] = [{"field": list(item["loc"]), "type": item["type"]}
                                    for item in exc.errors(include_input=False, include_context=False, include_url=False)]
    return fields


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "diagnostic_fields", {})
        data = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "pid": record.process,
            "request_id": getattr(record, "diagnostic_request_id", None),
            "event": record.getMessage(),
            **fields,
        }
        # Redact before JSON serialization so quotes/newlines in keys are handled.
        def clean(value):
            if isinstance(value, str):
                return redact(value)[:4000]
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [clean(item) for item in value]
            return value
        return json.dumps(clean(data), ensure_ascii=False)


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8")


def configure_logging(settings) -> None:
    global _handler
    register_secrets(settings.llm_api_key, settings.backup_llm_api_key, settings.exa_api_key, settings.firecrawl_api_key)
    directory = settings.log_directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Separate workers avoid competing rotation of one file.
    target = directory / f"crosscheck-{os.getpid()}.jsonl"
    with _lock:
        if _handler and Path(_handler.baseFilename) == target:
            return
        if _handler:
            logger.removeHandler(_handler)
            _handler.close()
        handler = PrivateRotatingHandler(target, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        _handler = handler


def event(name: str, *, level: int = logging.INFO, **fields) -> None:
    logger.log(level, name, extra={"diagnostic_request_id": request_id.get(), "diagnostic_fields": fields})


def provider_client(provider: str, stage: str, base_url: str, timeout: float, api_key: str, page_url: str | None = None) -> httpx.AsyncClient:
    register_secrets(api_key)
    start = time.monotonic()
    fields = {"provider": provider, "stage": stage, "attempt_id": str(uuid4()),
              "endpoint_host": endpoint_host(base_url), "timeout_seconds": timeout}
    if page_url:
        fields["page_id"] = fingerprint(page_url)
    event("provider_http_started", **fields)

    async def received(response):
        event("provider_http_response", **fields, http_status=response.status_code,
              headers_elapsed_ms=elapsed_ms(start), upstream_request_id=response.headers.get("x-request-id"))

    return httpx.AsyncClient(timeout=timeout, event_hooks={"response": [received]})


class DiagnosticMiddleware:
    """ASGI scope includes the complete SSE response and inherited child tasks."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = request_id.set(str(uuid4()))
        start = time.monotonic()
        status = None
        finished = False

        async def tracked_send(message):
            nonlocal status, finished
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", request_id.get().encode())]}
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True

        # No query strings or arbitrary paths (which may contain credentials).
        path = scope.get("path", "")
        route = path if path in {"/api/v1/config", "/api/v1/verifications", "/api/v1/verifications/stream", "/api/v1/history"} else "other"
        event("http_request_started", method=scope["method"], route=route)
        try:
            await self.app(scope, receive, tracked_send)
        except BaseException as exc:
            event("http_request_failed", level=logging.ERROR, **error_fields(exc))
            raise
        finally:
            event("http_request_finished", route=route, http_status=status, elapsed_ms=elapsed_ms(start), response_complete=finished)
            request_id.reset(token)
