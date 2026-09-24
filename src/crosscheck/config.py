import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    log_directory: Path = Path(__file__).resolve().parents[2] / "logs"
    log_llm_response_preview: bool = False
    database_path: Path = Path(__file__).resolve().parents[2] / "data" / "crosscheck.sqlite3"
    app_env: str = "development"
    search_providers: str = "mock"
    content_provider: str = "mock"
    max_search_results: int = 5
    max_evidence_items: int = 8
    request_timeout_seconds: float = 60.0
    firecrawl_api_key: str | None = None
    firecrawl_api_url: str = "https://api.firecrawl.dev"
    exa_api_key: str | None = None
    exa_api_url: str = "https://api.exa.ai"
    llm_api_key: str | None = None
    llm_api_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    backup_llm_api_key: str | None = None
    backup_llm_api_url: str = "https://api.openai.com/v1"
    backup_llm_model: str = "gpt-4o-mini"
    claim_analyzer: Literal["llm", "rules"] = "llm"
    evidence_analyzer: Literal["hybrid", "local", "llm", "rules"] = "hybrid"
    local_relation_model_path: Path = Path(__file__).resolve().parents[2] / "models" / "evidence_relation_cfever.json.gz"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def provider_names(self) -> list[str]:
        return [item.strip().lower() for item in self.search_providers.split(",") if item.strip()]


CONFIG_FILE = Path(__file__).resolve().parents[2] / "config.local.json"


def _stored_values() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_runtime_settings(settings: Settings) -> None:
    values = {
        "search_providers": settings.search_providers,
        "content_provider": settings.content_provider,
        "max_search_results": settings.max_search_results,
        "max_evidence_items": settings.max_evidence_items,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "firecrawl_api_key": settings.firecrawl_api_key,
        "firecrawl_api_url": settings.firecrawl_api_url,
        "exa_api_key": settings.exa_api_key,
        "exa_api_url": settings.exa_api_url,
        "llm_api_key": settings.llm_api_key,
        "llm_api_url": settings.llm_api_url,
        "llm_model": settings.llm_model,
        "backup_llm_api_key": settings.backup_llm_api_key,
        "backup_llm_api_url": settings.backup_llm_api_url,
        "backup_llm_model": settings.backup_llm_model,
        "claim_analyzer": settings.claim_analyzer,
        "evidence_analyzer": settings.evidence_analyzer,
        "local_relation_model_path": str(settings.local_relation_model_path),
    }
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Keys are stored locally. Write atomically and restrict access even when
    # the user's shell umask would otherwise create a world-readable file.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG_FILE.parent, prefix=".config-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            json.dump(values, temporary, ensure_ascii=False, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, CONFIG_FILE)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for key, value in _stored_values().items():
        if key in Settings.model_fields and value is not None:
            if key in {"log_directory", "database_path", "local_relation_model_path"}:
                value = Path(value)
            setattr(settings, key, value)
    return settings
