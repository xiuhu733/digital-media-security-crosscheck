from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.history import router as history_router
from .api.routes import router
from .config import get_settings
from .diagnostics import DiagnosticMiddleware, configure_logging, event

configure_logging(get_settings())
event("service_started")
app = FastAPI(title="多源信息交叉验证系统", version="0.1.0")
app.add_middleware(DiagnosticMiddleware)
app.include_router(router)
app.include_router(history_router)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok"}
