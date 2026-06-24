"""Configuration loading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class OutputDirs(BaseModel):
    predictions: str = "predictions"
    reports: str = "reports"
    manifests: str = "runs/manifests"
    traces: str = "runs/traces"
    session_packs: str = "session_packs"
    training_exports: str = "training_exports"


class Limits(BaseModel):
    max_concurrency: int = 4
    shard_episode_count: int = 20
    max_news_items_for_mock: int = 12
    session_pack_token_budget: int = 60_000


class Settings(BaseModel):
    project_root: Path = Field(default_factory=lambda: Path.cwd())
    project_name: str = "news-scalping-lab"
    llm_provider: str = "mock"
    web_provider: str = "mock"
    price_provider: str = "mock"
    stock_web_path: Path | None = None
    default_mode: str = "exhaustive"
    timezone: str = "Asia/Seoul"
    output_dirs: OutputDirs = Field(default_factory=OutputDirs)
    limits: Limits = Field(default_factory=Limits)

    def path(self, relative: str | Path) -> Path:
        path = Path(relative)
        if path.is_absolute():
            return path
        return self.project_root / path


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    if content is None:
        return {}
    if not isinstance(content, dict):
        raise ValueError(f"expected mapping config in {path}")
    return content


def load_settings(project_root: Path | None = None) -> Settings:
    root = (project_root or Path.cwd()).resolve()
    data = _read_yaml(root / "configs" / "default.yaml")
    if "stock_web_path" in data and data["stock_web_path"] is not None:
        data["stock_web_path"] = Path(str(data["stock_web_path"]))

    settings = Settings(project_root=root, **data)

    llm_provider = os.getenv("NSLAB_LLM_PROVIDER")
    if llm_provider:
        settings.llm_provider = llm_provider
    web_provider = os.getenv("NSLAB_WEB_PROVIDER")
    if web_provider:
        settings.web_provider = web_provider
    stock_path = os.getenv("NSLAB_STOCK_WEB_PATH")
    if stock_path:
        settings.stock_web_path = Path(stock_path)
        settings.price_provider = "stock-web"
    max_concurrency = os.getenv("NSLAB_MAX_CONCURRENCY")
    if max_concurrency:
        settings.limits.max_concurrency = int(max_concurrency)
    return settings


def ensure_project_dirs(settings: Settings) -> None:
    dirs = [
        "data/inbox/news",
        "data/inbox/research",
        "data/raw/news",
        "data/raw/research",
        "data/normalized",
        "data/quarantine",
        "data/cache",
        "research/episodes",
        "research/accepted",
        "research/rejected",
        "research/indexes",
        "memory/episodes",
        "memory/claims",
        "memory/mechanisms",
        "memory/event_ticker_edges",
        "memory/market_memory",
        "memory/company_memory",
        "memory/shard_brains",
        "memory/vector_index",
        "brain/snapshots",
        "brain/current",
        "brain/diffs",
        "warehouse",
        settings.output_dirs.predictions,
        settings.output_dirs.reports,
        settings.output_dirs.manifests,
        settings.output_dirs.traces,
        "runs/checkpoints",
        settings.output_dirs.session_packs,
        settings.output_dirs.training_exports,
    ]
    for directory in dirs:
        settings.path(directory).mkdir(parents=True, exist_ok=True)
