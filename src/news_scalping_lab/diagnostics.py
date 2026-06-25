"""Project diagnostics for the doctor command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from news_scalping_lab.brain.compiler import current_brain_version
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import (
    BlindPrediction,
    BrainManifest,
    ContextManifest,
    DailyAnalysis,
    ResearchEpisode,
)
from news_scalping_lab.prices.stock_web import StockWebPriceSource
from news_scalping_lab.retrieval.store import inspect_vector_index
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.warehouse import WarehouseStore

ENV_KEYS = [
    "NSLAB_LLM_PROVIDER",
    "NSLAB_WEB_PROVIDER",
    "NSLAB_STOCK_WEB_PATH",
    "NSLAB_STOCK_WEB_CACHE",
    "NSLAB_STOCK_WEB_CACHE_PATH",
    "NSLAB_STOCK_WEB_REMOTE_URL",
    "NSLAB_MAX_CONCURRENCY",
    "NSLAB_LLM_MODEL",
    "NSLAB_LLM_REASONING_EFFORT",
    "NSLAB_LLM_MAX_OUTPUT_TOKENS",
    "NSLAB_LLM_MAX_RETRIES",
    "NSLAB_OPENAI_MODEL",
    "NSLAB_OPENAI_EMBEDDING_MODEL",
    "OPENAI_API_KEY",
]


def build_doctor_report(settings: Settings) -> dict[str, Any]:
    store = ResearchStore(settings.project_root)
    accepted_episode_count = len(store.list_accepted())
    stock_web_path = _resolved_optional_path(settings, settings.stock_web_path)
    stock_web_cache_path = settings.path(settings.stock_web_cache_path)
    stock_web_effective_path, stock_web_effective_source = _stock_web_effective_path(
        settings,
        configured_path=stock_web_path,
        cache_path=stock_web_cache_path,
    )
    stock_web_schema = (
        StockWebPriceSource(stock_web_effective_path).inspect_atlas_schema()
        if stock_web_effective_path is not None and stock_web_effective_path.exists()
        else None
    )
    schema_dir = settings.path("schemas")
    return {
        "project_root": settings.project_root.as_posix(),
        "providers": {
            "llm": settings.llm_provider,
            "web": settings.web_provider,
            "price": settings.price_provider,
        },
        "llm_model": settings.llm.model_dump(exclude_none=True),
        "environment": _environment_status(),
        "api_connections": {
            "openai": {
                "required": settings.llm_provider == "openai",
                "configured": bool(os.getenv("OPENAI_API_KEY")),
                "status": _openai_status(settings),
            }
        },
        "stock_web": {
            "path": stock_web_path.as_posix() if stock_web_path is not None else None,
            "path_exists": bool(stock_web_path is not None and stock_web_path.exists()),
            "cache_enabled": settings.stock_web_cache_enabled,
            "cache_path": stock_web_cache_path.as_posix(),
            "cache_path_exists": stock_web_cache_path.exists(),
            "effective_path": (
                stock_web_effective_path.as_posix()
                if stock_web_effective_path is not None
                else None
            ),
            "effective_path_exists": bool(
                stock_web_effective_path is not None and stock_web_effective_path.exists()
            ),
            "effective_path_source": stock_web_effective_source,
            "remote_url": settings.stock_web_remote_url,
            "schema": stock_web_schema,
        },
        "warehouse": {
            "status": "ok",
            "counts": WarehouseStore(settings.project_root).counts(),
        },
        "brain": {
            "head": current_brain_version(settings.project_root),
            "accepted_episode_count": accepted_episode_count,
            "coverage": _brain_coverage_status(settings.project_root, accepted_episode_count),
        },
        "vector_index": inspect_vector_index(settings.project_root),
        "schemas": {
            "path": schema_dir.as_posix(),
            "exists": schema_dir.exists(),
            "file_count": _file_count(schema_dir, suffix=".json"),
            "versions": _schema_versions(),
        },
    }


def _resolved_optional_path(settings: Settings, path: Path | None) -> Path | None:
    if path is None:
        return None
    return settings.path(path)


def _stock_web_effective_path(
    settings: Settings,
    *,
    configured_path: Path | None,
    cache_path: Path,
) -> tuple[Path | None, str]:
    if configured_path is not None and configured_path.exists():
        return configured_path, "path"
    if settings.stock_web_cache_enabled and cache_path.exists():
        return cache_path, "cache"
    if configured_path is not None:
        return configured_path, "path"
    if settings.stock_web_cache_enabled:
        return cache_path, "cache"
    return None, "none"


def _environment_status() -> dict[str, dict[str, object]]:
    status: dict[str, dict[str, object]] = {}
    for key in ENV_KEYS:
        value = os.getenv(key)
        status[key] = {
            "set": value is not None,
            "value": "***" if key.endswith("KEY") and value else value,
        }
    return status


def _openai_status(settings: Settings) -> str:
    if settings.llm_provider != "openai":
        return "not_required"
    if os.getenv("OPENAI_API_KEY"):
        return "configured_not_called"
    return "missing_api_key"


def _brain_coverage_status(root: Path, accepted_episode_count: int) -> dict[str, Any]:
    coverage_path = root / "brain" / "current" / "coverage_manifest.json"
    if not coverage_path.exists():
        return {
            "manifest_exists": False,
            "coverage_complete": False,
            "covered_episode_count": 0,
            "missing_episode_ids": [],
            "status": "missing",
        }
    try:
        manifest = _read_json_object(coverage_path)
    except ValueError as exc:
        return {
            "manifest_exists": True,
            "coverage_complete": False,
            "covered_episode_count": 0,
            "missing_episode_ids": [],
            "status": "invalid",
            "error": str(exc),
        }
    covered_ids = _string_list(manifest.get("covered_episode_ids"))
    missing_ids = _string_list(manifest.get("missing_episode_ids"))
    coverage_complete = (
        manifest.get("coverage_complete") is True
        and len(covered_ids) == accepted_episode_count
        and not missing_ids
    )
    return {
        "manifest_exists": True,
        "brain_version": manifest.get("brain_version"),
        "coverage_complete": coverage_complete,
        "covered_episode_count": len(covered_ids),
        "missing_episode_ids": missing_ids,
        "status": "complete" if coverage_complete else "incomplete",
    }


def _file_count(path: Path, *, suffix: str | None = None) -> int:
    if not path.exists():
        return 0
    files = [item for item in path.rglob("*") if item.is_file()]
    if suffix is not None:
        files = [item for item in files if item.suffix == suffix]
    return len(files)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path.as_posix()}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path.as_posix()}")
    return payload


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _schema_versions() -> dict[str, str]:
    return {
        "research_episode": str(ResearchEpisode.model_fields["schema_version"].default),
        "blind_prediction": str(BlindPrediction.model_fields["schema_version"].default),
        "brain_manifest": str(BrainManifest.model_fields["schema_version"].default),
        "context_manifest": str(ContextManifest.model_fields["schema_version"].default),
        "daily_analysis": str(DailyAnalysis.model_fields["schema_version"].default),
    }
