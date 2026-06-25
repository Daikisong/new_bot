"""Configuration loading."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


class LLMModelSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str = "mock"
    model: str = "deterministic-mock"
    embedding_model: str | None = None
    reasoning_effort: str | None = "low"
    max_output_tokens: int | None = 4096
    max_retries: int = 0


class Settings(BaseModel):
    project_root: Path = Field(default_factory=lambda: Path.cwd())
    project_name: str = "news-scalping-lab"
    llm_provider: str = "mock"
    web_provider: str = "mock"
    price_provider: str = "mock"
    stock_web_path: Path | None = None
    stock_web_remote_url: str = "https://github.com/Songdaiki/stock-web.git"
    stock_web_cache_path: Path = Path("data/cache/stock-web")
    stock_web_cache_enabled: bool = False
    default_mode: str = "exhaustive"
    timezone: str = "Asia/Seoul"
    output_dirs: OutputDirs = Field(default_factory=OutputDirs)
    limits: Limits = Field(default_factory=Limits)
    llm: LLMModelSettings = Field(default_factory=LLMModelSettings)

    def path(self, relative: str | Path) -> Path:
        path = Path(relative)
        if path.is_absolute():
            return path
        return self.project_root / path


DEFAULT_PROJECT_DIRS = [
    "configs",
    "schemas",
    "prompts/research_import",
    "prompts/brain_compile",
    "prompts/blind_analysis",
    "prompts/memory_sweep",
    "prompts/web_research",
    "prompts/candidate_generation",
    "prompts/red_team",
    "prompts/synthesis",
    "prompts/evaluation",
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
    "research/hypotheses",
    "research/counterexamples",
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
    "runs/checkpoints",
    ".agents/skills/news-scalping-lab/references",
    ".agents/skills/news-scalping-lab/scripts",
]

DEFAULT_CONFIG_FILES: dict[str, dict[str, Any]] = {
    "default.yaml": {
        "project_name": "news-scalping-lab",
        "llm_provider": "mock",
        "web_provider": "mock",
        "price_provider": "mock",
        "stock_web_path": None,
        "stock_web_remote_url": "https://github.com/Songdaiki/stock-web.git",
        "stock_web_cache_path": "data/cache/stock-web",
        "stock_web_cache_enabled": False,
        "default_mode": "exhaustive",
        "timezone": "Asia/Seoul",
        "output_dirs": {
            "predictions": "predictions",
            "reports": "reports",
            "manifests": "runs/manifests",
            "traces": "runs/traces",
            "session_packs": "session_packs",
            "training_exports": "training_exports",
        },
        "limits": {
            "max_concurrency": 4,
            "shard_episode_count": 20,
            "max_news_items_for_mock": 12,
            "session_pack_token_budget": 60_000,
        },
    },
    "models.yaml": {
        "default": {
            "provider": "mock",
            "model": "deterministic-mock",
            "reasoning_effort": "low",
            "max_output_tokens": 4096,
            "max_retries": 0,
        },
        "openai": {
            "provider": "openai",
            "model": "gpt-5-mini",
            "reasoning_effort": "medium",
            "max_output_tokens": 8192,
            "max_retries": 2,
        },
    },
    "context_budget.yaml": {
        "exhaustive": {
            "include_global_brain": True,
            "include_all_accepted_episodes": True,
            "include_retrieved_raw_episodes": True,
        },
        "brain": {
            "include_global_brain": True,
            "include_all_shard_brains": True,
            "include_retrieved_raw_episodes": True,
        },
        "fast": {
            "include_global_brain": True,
            "include_all_accepted_episodes": False,
            "include_retrieved_raw_episodes": True,
        },
    },
    "inference.yaml": {
        "default_mode": "exhaustive",
        "confidence_labels": ["very_high", "high", "medium", "low", "speculative"],
        "path_types": ["SINGLE_EVENT", "THEME_BENEFICIARY", "CONTINUATION", "HYBRID"],
    },
    "evaluation.yaml": {
        "labels": [
            "high_return_5",
            "high_return_10",
            "high_return_15",
            "high_return_20",
            "upper_limit_touched",
            "upper_limit_closed",
        ],
        "metrics": [
            "UpperLimit Recall@5",
            "UpperLimit Recall@10",
            "UpperLimit Recall@20",
            "Precision@5",
            "Precision@10",
            "Theme Recall",
            "Single-event Recall",
            "Beneficiary Recall",
            "Continuation Recall",
        ],
    },
}


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
    _load_dotenv(root / ".env")
    data = _read_yaml(root / "configs" / "default.yaml")
    if "stock_web_path" in data and data["stock_web_path"] is not None:
        data["stock_web_path"] = Path(str(data["stock_web_path"]))
    if "stock_web_cache_path" in data and data["stock_web_cache_path"] is not None:
        data["stock_web_cache_path"] = Path(str(data["stock_web_cache_path"]))

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
    stock_cache = os.getenv("NSLAB_STOCK_WEB_CACHE")
    if stock_cache and stock_cache.lower() in {"1", "true", "yes", "on"}:
        settings.stock_web_cache_enabled = True
        settings.price_provider = "stock-web"
    stock_cache_path = os.getenv("NSLAB_STOCK_WEB_CACHE_PATH")
    if stock_cache_path:
        settings.stock_web_cache_path = Path(stock_cache_path)
    stock_remote_url = os.getenv("NSLAB_STOCK_WEB_REMOTE_URL")
    if stock_remote_url:
        settings.stock_web_remote_url = stock_remote_url
    max_concurrency = os.getenv("NSLAB_MAX_CONCURRENCY")
    if max_concurrency:
        settings.limits.max_concurrency = int(max_concurrency)
    settings.llm = _load_llm_model_settings(root, settings.llm_provider)
    _apply_llm_env_overrides(settings)
    return settings


def _load_llm_model_settings(root: Path, provider: str) -> LLMModelSettings:
    profiles = _read_yaml(root / "configs" / "models.yaml")
    profile_key = _model_profile_key(provider)
    raw_profile = profiles.get(profile_key, profiles.get("default", {}))
    if raw_profile is None:
        raw_profile = {}
    if not isinstance(raw_profile, dict):
        raise ValueError(f"expected mapping model profile for {profile_key}")
    profile = LLMModelSettings(**raw_profile)
    if not profile.provider:
        profile.provider = provider
    return profile


def _model_profile_key(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized in {"openai", "responses", "openai-responses"}:
        return "openai"
    return normalized or "default"


def _apply_llm_env_overrides(settings: Settings) -> None:
    model = os.getenv("NSLAB_LLM_MODEL")
    if model:
        settings.llm.model = model
    if _model_profile_key(settings.llm_provider) == "openai":
        openai_model = os.getenv("NSLAB_OPENAI_MODEL")
        if openai_model:
            settings.llm.model = openai_model
        embedding_model = os.getenv("NSLAB_OPENAI_EMBEDDING_MODEL")
        if embedding_model:
            settings.llm.embedding_model = embedding_model
    reasoning_effort = os.getenv("NSLAB_LLM_REASONING_EFFORT")
    if reasoning_effort:
        settings.llm.reasoning_effort = reasoning_effort
    max_output_tokens = os.getenv("NSLAB_LLM_MAX_OUTPUT_TOKENS")
    if max_output_tokens:
        settings.llm.max_output_tokens = int(max_output_tokens)
    max_retries = os.getenv("NSLAB_LLM_MAX_RETRIES")
    if max_retries:
        settings.llm.max_retries = int(max_retries)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.match(key) or key in os.environ:
            continue
        os.environ[key] = _dotenv_value(raw_value)


def _dotenv_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def ensure_project_dirs(settings: Settings) -> None:
    dirs = [
        *DEFAULT_PROJECT_DIRS,
        settings.output_dirs.predictions,
        settings.output_dirs.reports,
        settings.output_dirs.manifests,
        settings.output_dirs.traces,
        settings.output_dirs.session_packs,
        settings.output_dirs.training_exports,
    ]
    for directory in dirs:
        settings.path(directory).mkdir(parents=True, exist_ok=True)


def write_default_config_files(settings: Settings) -> list[Path]:
    configs_dir = settings.path("configs")
    configs_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, payload in DEFAULT_CONFIG_FILES.items():
        path = configs_dir / filename
        if path.exists():
            continue
        path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        written.append(path)
    return written
