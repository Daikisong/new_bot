from __future__ import annotations

import os
from pathlib import Path

import yaml

from news_scalping_lab.config import (
    Settings,
    ensure_project_dirs,
    load_settings,
    write_default_config_files,
)
from news_scalping_lab.contracts.schemas import SCHEMA_MODELS, export_json_schemas
from news_scalping_lab.utils import read_json

EXPECTED_SCHEMA_FILES = {
    "blind_prediction.schema.json",
    "brain_manifest.schema.json",
    "candidate.schema.json",
    "company_memory.schema.json",
    "context_manifest.schema.json",
    "daily_analysis.schema.json",
    "event_ticker_edge.schema.json",
    "mechanism_memory.schema.json",
    "memory_claim.schema.json",
    "postmortem.schema.json",
    "red_team_artifact.schema.json",
    "research_episode.schema.json",
}


def test_ensure_project_dirs_creates_goal_scaffold_directories(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)

    ensure_project_dirs(settings)

    expected_dirs = [
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
        "predictions",
        "reports",
        "runs/manifests",
        "runs/traces",
        "runs/checkpoints",
        "session_packs",
        "training_exports",
        ".agents/skills/news-scalping-lab/references",
        ".agents/skills/news-scalping-lab/scripts",
    ]
    assert [relative for relative in expected_dirs if not (tmp_path / relative).is_dir()] == []


def test_write_default_config_files_bootstraps_missing_configs_without_overwrite(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    existing_default = tmp_path / "configs" / "default.yaml"
    existing_default.write_text("project_name: custom-lab\n", encoding="utf-8")

    written = write_default_config_files(settings)

    assert existing_default.read_text(encoding="utf-8") == "project_name: custom-lab\n"
    assert {path.name for path in written} == {
        "models.yaml",
        "context_budget.yaml",
        "inference.yaml",
        "evaluation.yaml",
    }
    models = yaml.safe_load((tmp_path / "configs" / "models.yaml").read_text(encoding="utf-8"))
    inference = yaml.safe_load(
        (tmp_path / "configs" / "inference.yaml").read_text(encoding="utf-8")
    )
    assert models["default"]["provider"] == "mock"
    assert models["openai"]["model"] == "gpt-5-mini"
    assert inference["default_mode"] == "exhaustive"


def test_load_settings_reads_selected_llm_model_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NSLAB_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("NSLAB_LLM_MODEL", raising=False)
    monkeypatch.delenv("NSLAB_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("NSLAB_OPENAI_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("NSLAB_LLM_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("NSLAB_LLM_MAX_OUTPUT_TOKENS", raising=False)
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "default.yaml").write_text("llm_provider: openai\n", encoding="utf-8")
    (configs / "models.yaml").write_text(
        "\n".join(
            [
                "default:",
                "  provider: mock",
                "  model: deterministic-mock",
                "openai:",
                "  provider: openai",
                "  model: gpt-configured",
                "  embedding_model: embed-configured",
                "  reasoning_effort: high",
                "  max_output_tokens: 12345",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings.llm_provider == "openai"
    assert settings.llm.provider == "openai"
    assert settings.llm.model == "gpt-configured"
    assert settings.llm.embedding_model == "embed-configured"
    assert settings.llm.reasoning_effort == "high"
    assert settings.llm.max_output_tokens == 12345


def test_tracked_json_schemas_match_contract_export(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    generated_dir = tmp_path / "schemas"

    generated_paths = export_json_schemas(generated_dir)

    generated = {path.name: read_json(path) for path in generated_paths}
    tracked = {
        path.name: read_json(path)
        for path in sorted((repo_root / "schemas").glob("*.schema.json"))
    }
    assert set(SCHEMA_MODELS) == EXPECTED_SCHEMA_FILES
    assert sorted(set(generated) - set(tracked)) == []
    assert sorted(set(tracked) - set(generated)) == []
    assert sorted(name for name in generated if generated[name] != tracked[name]) == []


def test_load_settings_reads_project_dotenv_without_overriding_environment(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("NSLAB_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("NSLAB_WEB_PROVIDER", raising=False)
    monkeypatch.delenv("NSLAB_STOCK_WEB_PATH", raising=False)
    monkeypatch.delenv("NSLAB_STOCK_WEB_CACHE", raising=False)
    monkeypatch.delenv("NSLAB_STOCK_WEB_CACHE_PATH", raising=False)
    monkeypatch.delenv("NSLAB_STOCK_WEB_REMOTE_URL", raising=False)
    monkeypatch.delenv("NSLAB_MAX_CONCURRENCY", raising=False)
    monkeypatch.delenv("NSLAB_LLM_MODEL", raising=False)
    monkeypatch.delenv("NSLAB_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("NSLAB_OPENAI_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("NSLAB_LLM_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("NSLAB_LLM_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "# local secrets and runtime settings",
                "NSLAB_LLM_PROVIDER=openai",
                "NSLAB_WEB_PROVIDER=mock",
                "NSLAB_STOCK_WEB_PATH='data/cache/stock-web'",
                "NSLAB_STOCK_WEB_CACHE=1",
                "NSLAB_STOCK_WEB_CACHE_PATH=data/cache/custom-stock-web",
                "NSLAB_STOCK_WEB_REMOTE_URL=https://example.test/stock-web.git",
                "NSLAB_MAX_CONCURRENCY=7",
                "NSLAB_OPENAI_MODEL=gpt-dotenv",
                "NSLAB_OPENAI_EMBEDDING_MODEL=embed-dotenv",
                "NSLAB_LLM_REASONING_EFFORT=medium",
                "NSLAB_LLM_MAX_OUTPUT_TOKENS=9876",
                "OPENAI_API_KEY=secret-from-dotenv",
                "MALFORMED LINE",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings.llm_provider == "openai"
    assert settings.web_provider == "mock"
    assert settings.price_provider == "stock-web"
    assert settings.stock_web_path == Path("data/cache/stock-web")
    assert settings.stock_web_cache_enabled is True
    assert settings.stock_web_cache_path == Path("data/cache/custom-stock-web")
    assert settings.stock_web_remote_url == "https://example.test/stock-web.git"
    assert settings.limits.max_concurrency == 7
    assert settings.llm.model == "gpt-dotenv"
    assert settings.llm.embedding_model == "embed-dotenv"
    assert settings.llm.reasoning_effort == "medium"
    assert settings.llm.max_output_tokens == 9876
    assert settings.path(settings.stock_web_cache_path) == (
        tmp_path / "data/cache/custom-stock-web"
    )
    assert settings.path(settings.stock_web_path) == tmp_path / "data/cache/stock-web"
    assert os.getenv("OPENAI_API_KEY") == "secret-from-dotenv"

    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "mock")
    assert load_settings(tmp_path).llm_provider == "mock"
