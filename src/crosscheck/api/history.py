import logging
import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool

from crosscheck.config import Settings, get_settings
from crosscheck.diagnostics import elapsed_ms, error_fields, event
from crosscheck.domain.models import VerificationReport
from crosscheck.storage.reports import ReportRepository

router = APIRouter(prefix="/api/v1/history", tags=["history"])
logger = logging.getLogger("uvicorn.error")


def get_repository(settings: Annotated[Settings, Depends(get_settings)]) -> ReportRepository:
    return ReportRepository(settings.database_path)


async def persist_report(repository: ReportRepository, report: VerificationReport):
    start = time.monotonic()
    try:
        await run_in_threadpool(repository.save, report)
        event("report_saved", report_id=report.request_id, elapsed_ms=elapsed_ms(start))
    except (OSError, sqlite3.Error) as exc:
        event("report_save_failed", level=logging.ERROR, report_id=report.request_id, elapsed_ms=elapsed_ms(start), **error_fields(exc))
        logger.error("report_save_failed type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="保存失败：无法保存核验记录，请检查数据库目录权限或磁盘空间。") from exc


@router.get("")
def list_history(
    repository: Annotated[ReportRepository, Depends(get_repository)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    try:
        return repository.list_reports(limit, offset)
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="核验历史暂不可读，请稍后重试。") from exc


@router.get("/{request_id}", response_model=VerificationReport)
def get_history(request_id: str, repository: Annotated[ReportRepository, Depends(get_repository)]):
    try:
        report = repository.get(request_id)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(status_code=503, detail="读取核验历史失败，请检查本地数据库。") from exc
    if report is None:
        raise HTTPException(status_code=404, detail="未找到这条核验记录。")
    return report


@router.delete("/{request_id}", status_code=204)
def delete_history(request_id: str, repository: Annotated[ReportRepository, Depends(get_repository)]):
    try:
        deleted = repository.delete(request_id)
    except (OSError, sqlite3.Error) as exc:
        logger.error("report_delete_failed type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="删除失败：无法删除核验记录，请稍后重试。") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到这条核验记录，或记录已被删除。")
    return Response(status_code=204)
