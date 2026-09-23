import asyncio
import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, HttpUrl

from crosscheck.config import Settings, get_settings, save_runtime_settings
from crosscheck.diagnostics import error_fields, register_secrets, request_id
from crosscheck.diagnostics import event as log_event
from crosscheck.domain.models import VerificationReport, VerificationRequest
from crosscheck.providers.factory import (
    build_claim_analyzers,
    build_content_providers,
    build_evidence_analyzers,
    build_search_providers,
)
from crosscheck.services.errors import ProviderFailure
from crosscheck.services.orchestrator import VerificationService
from crosscheck.storage.reports import ReportRepository

from .history import get_repository, persist_report

router = APIRouter(prefix="/api/v1", tags=["verification"])
logger = logging.getLogger("uvicorn.error")


class RuntimeConfigUpdate(BaseModel):
    search_providers: list[Literal["mock", "exa", "firecrawl"]] = Field(min_length=1)
    content_provider: Literal["mock", "exa", "firecrawl"] = "mock"
    max_search_results: int = Field(default=5, ge=1, le=20)
    max_evidence_items: int = Field(default=8, ge=1, le=20)
    request_timeout_seconds: float = Field(default=60, ge=3, le=120)
    exa_api_key: str | None = None
    exa_api_url: HttpUrl = "https://api.exa.ai"
    firecrawl_api_key: str | None = None
    firecrawl_api_url: HttpUrl = "https://api.firecrawl.dev"
    llm_api_key: str | None = None
    llm_api_url: HttpUrl = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    backup_llm_api_key: str | None = None
    backup_llm_api_url: HttpUrl = "https://api.openai.com/v1"
    backup_llm_model: str = "gpt-4o-mini"
    claim_analyzer: Literal["llm", "rules"] = "llm"
    evidence_analyzer: Literal["local", "llm", "rules"] = "local"
    local_relation_model_path: str | None = None


def _mask_key(value: str | None) -> str:
    if not value:
        return ""
    return f"{value[:4]}{'•' * 8}{value[-4:]}" if len(value) > 8 else "••••••••"


def config_response(settings: Settings) -> dict:
    return {
        "search_providers": settings.provider_names(),
        "content_provider": settings.content_provider,
        "max_search_results": settings.max_search_results,
        "max_evidence_items": settings.max_evidence_items,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "exa_api_url": settings.exa_api_url,
        "exa_api_key": _mask_key(settings.exa_api_key),
        "exa_configured": bool(settings.exa_api_key),
        "firecrawl_api_url": settings.firecrawl_api_url,
        "firecrawl_api_key": _mask_key(settings.firecrawl_api_key),
        "firecrawl_configured": bool(settings.firecrawl_api_key),
        "llm_api_url": settings.llm_api_url,
        "llm_model": settings.llm_model,
        "llm_api_key": _mask_key(settings.llm_api_key),
        "llm_configured": bool(settings.llm_api_key),
        "backup_llm_api_url": settings.backup_llm_api_url,
        "backup_llm_model": settings.backup_llm_model,
        "backup_llm_api_key": _mask_key(settings.backup_llm_api_key),
        "backup_llm_configured": bool(settings.backup_llm_api_key),
        "claim_analyzer": settings.claim_analyzer,
        "evidence_analyzer": settings.evidence_analyzer,
        "local_relation_model_path": str(settings.local_relation_model_path),
    }


def get_service(settings: Settings = Depends(get_settings)) -> VerificationService:  # noqa: B008
    try:
        return VerificationService(
            build_search_providers(settings),
            build_content_providers(settings),
            settings.max_search_results,
            settings.max_evidence_items,
            claim_analyzer=build_claim_analyzers(settings),
            evidence_analyzer=build_evidence_analyzers(settings),
        )
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/verifications", response_model=VerificationReport)
async def create_verification(request: VerificationRequest, service: VerificationService = Depends(get_service), repository: ReportRepository = Depends(get_repository)):  # noqa: B008
    try:
        report = await service.verify(request)
        await persist_report(repository, report)
        return report
    except HTTPException:
        raise
    except ProviderFailure as exc:
        logger.warning("verification_failed provider=%s stage=%s type=%s", exc.provider, exc.stage, exc.error_type)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("verification_failed type=%s", type(exc).__name__)
        raise HTTPException(status_code=500, detail=f"核验处理失败（{type(exc).__name__}），请检查服务日志。") from exc


@router.post("/verifications/stream", tags=["verification"])
async def stream_verification(request: VerificationRequest, service: VerificationService = Depends(get_service), repository: ReportRepository = Depends(get_repository)):  # noqa: B008
    async def events():
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def publish(event: dict):
            await queue.put({"type": "progress", **event})

        async def verify_and_save():
            report = await service.verify(request, publish)
            await publish({"stage": "save", "message": "正在保存核验报告…", "percent": 99})
            await persist_report(repository, report)
            await publish({"stage": "saved", "message": "报告已保存，可随时查看。", "percent": 100})
            return report

        task = asyncio.create_task(verify_and_save())
        try:
            while not task.done() or not queue.empty():
                if not queue.empty():
                    event = await queue.get()
                else:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.25)
                    except TimeoutError:
                        continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            try:
                report = task.result()
                yield f"data: {json.dumps({'type': 'result', 'report': report.model_dump(mode='json')}, ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001
                log_event("stream_verification_failed", level=logging.ERROR, **error_fields(exc))
                if isinstance(exc, HTTPException):
                    message = exc.detail
                elif isinstance(exc, ProviderFailure):
                    message = str(exc)
                else:
                    message = f"核验处理失败（{type(exc).__name__}），请根据诊断 ID 查看服务日志。"
                yield f"data: {json.dumps({'type': 'error', 'message': message, 'request_id': request_id.get()}, ensure_ascii=False)}\n\n"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/config", tags=["configuration"])
async def get_runtime_config(settings: Settings = Depends(get_settings)):  # noqa: B008
    return config_response(settings)


@router.put("/config", tags=["configuration"])
async def update_runtime_config(payload: RuntimeConfigUpdate, settings: Settings = Depends(get_settings)):  # noqa: B008
    # 空密钥表示保留已保存的密钥，便于页面只修改检索开关或 URL。
    settings.search_providers = ",".join(payload.search_providers)
    settings.content_provider = payload.content_provider
    settings.max_search_results = payload.max_search_results
    settings.max_evidence_items = payload.max_evidence_items
    settings.request_timeout_seconds = payload.request_timeout_seconds
    settings.exa_api_url = str(payload.exa_api_url)
    settings.firecrawl_api_url = str(payload.firecrawl_api_url)
    settings.llm_api_url = str(payload.llm_api_url)
    settings.llm_model = payload.llm_model
    settings.backup_llm_api_url = str(payload.backup_llm_api_url)
    settings.backup_llm_model = payload.backup_llm_model
    settings.claim_analyzer = payload.claim_analyzer
    settings.evidence_analyzer = payload.evidence_analyzer
    if payload.local_relation_model_path:
        settings.local_relation_model_path = Path(payload.local_relation_model_path)
    if payload.exa_api_key:
        settings.exa_api_key = payload.exa_api_key
    if payload.firecrawl_api_key:
        settings.firecrawl_api_key = payload.firecrawl_api_key
    if payload.llm_api_key:
        settings.llm_api_key = payload.llm_api_key
    if payload.backup_llm_api_key:
        settings.backup_llm_api_key = payload.backup_llm_api_key
    register_secrets(settings.llm_api_key, settings.backup_llm_api_key, settings.exa_api_key, settings.firecrawl_api_key)
    save_runtime_settings(settings)
    log_event("configuration_saved", request_timeout_seconds=settings.request_timeout_seconds,
          model_timeout_seconds=max(settings.request_timeout_seconds, 30.0),
          primary_configured=bool(settings.llm_api_key), backup_configured=bool(settings.backup_llm_api_key))
    return config_response(settings)
