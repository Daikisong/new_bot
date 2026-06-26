from __future__ import annotations

import os
import re
import tomllib
from importlib import import_module
from pathlib import Path

import yaml

from news_scalping_lab.config import (
    DEFAULT_CONFIG_FILES,
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
    "candidate_expansion_review.schema.json",
    "candidate_verification_review.schema.json",
    "company_memory.schema.json",
    "context_manifest.schema.json",
    "daily_analysis.schema.json",
    "event_ticker_edge.schema.json",
    "final_synthesis_context.schema.json",
    "mechanism_memory.schema.json",
    "memory_claim.schema.json",
    "news_novelty_review.schema.json",
    "open_world_first_analysis.schema.json",
    "postmortem.schema.json",
    "red_team_artifact.schema.json",
    "research_episode.schema.json",
    "semantic_retrieval_plan.schema.json",
    "semantic_research_draft.schema.json",
}


def test_repository_agent_guidance_stays_short_and_operational() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    guidance = (repo_root / "AGENTS.md").read_text(encoding="utf-8")

    required_rules = [
        "LLM-native news scalping research system",
        "Do not hardcode stocks, tickers, themes, regions, or beneficiary mappings",
        "Store research knowledge in `research/`, `memory/`, and `brain/`",
        "Exact keyword retrieval is only supporting evidence",
        "Candidate generation always starts with an open-world pass",
        "New research must be incorporated without source-code changes",
        "Blind inference must not access D-day prices or information after the cutoff",
        "Every output must include provenance and a context manifest",
        "Completion requires `ruff`, `mypy`, and `pytest` to pass",
    ]

    assert all(rule in guidance for rule in required_rules)
    for command in (
        "python -m ruff check .",
        "python -m mypy src/news_scalping_lab",
        "python -m pytest",
    ):
        assert command in guidance
    assert len([line for line in guidance.splitlines() if line.startswith("- ")]) == len(
        required_rules
    )
    assert "episode_id" not in guidance
    assert "ticker:" not in guidance.lower()
    assert re.search(r"\b\d{6}\b", guidance) is None


def test_repo_skill_documents_commands_outputs_and_recovery_without_domain_memory() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    skill = (repo_root / ".agents" / "skills" / "news-scalping-lab" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    required_fragments = [
        "research episode import",
        "brain update or full rebuild",
        "daily blind analysis",
        "postmortem evaluation",
        "lookahead leak audit",
        "Do not add stocks, tickers, themes, regions, or beneficiary mappings to source code.",
        "Research knowledge belongs in `research/`, `memory/`, and `brain/`.",
        "Exact keyword retrieval is never a candidate gate.",
        "Blind inference cannot use D-day prices or cutoff-after evidence.",
        "Exhaustive mode must include every accepted episode in the context manifest.",
        "nslab research import path/to/research.md",
        "nslab brain rebuild --mode full",
        "nslab analyze --news path/to/news.csv --trade-date YYYY-MM-DD",
        "nslab evaluate --trade-date YYYY-MM-DD",
        "nslab audit hardcoding",
        "`predictions/YYYY-MM-DD.json`",
        "`runs/manifests/<run_id>.json`",
        "python -m ruff check .",
        "python -m mypy src/news_scalping_lab",
        "python -m pytest",
        "make full-check",
        "If `brain audit` fails, run `nslab brain rebuild --mode full`.",
        "If lookahead audit fails, inspect the manifest `price_snapshot.allowed_through`",
    ]

    assert all(fragment in skill for fragment in required_fragments)
    assert "THEME_MAP" not in skill
    assert re.search(r"\b\d{6}\b", skill) is None


def test_nslab_console_entrypoint_resolves_to_cli_main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    target = pyproject["project"]["scripts"]["nslab"]
    module_name, attribute_name = target.split(":", maxsplit=1)

    module = import_module(module_name)

    assert target == "news_scalping_lab.cli:main"
    assert callable(getattr(module, attribute_name))


def test_dev_extra_includes_async_test_runner_dependency() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]
    test_files = sorted((repo_root / "tests").rglob("*.py"))
    async_marker_count = sum(
        path.read_text(encoding="utf-8").count("@pytest.mark.asyncio")
        for path in test_files
    )

    assert async_marker_count > 0
    assert any(dependency.startswith("pytest-asyncio") for dependency in dev_dependencies)


def test_plans_document_tracks_goal_scope_without_domain_memory() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    plans = (repo_root / "PLANS.md").read_text(encoding="utf-8")

    required_fragments = [
        "# Implementation Plan",
        "`news-scalping-lab`",
        "LLM-native research-memory system",
        "Production code must stay generic.",
        "Research knowledge belongs in `research/`, `memory/`, and `brain/`",
        "Scaffold a Python package, CLI, configs, schemas, prompts, data directories",
        "canonical Pydantic contracts",
        "immutable research import with strict JSON and semantic mock conversion paths",
        "deterministic mock LLM, web, embedding, and price providers",
        "OpenAI and stock-web adapter seams",
        "brain rebuild, incremental update, and coverage audit",
        "100% accepted episode coverage checks",
        "exhaustive context assembly that sweeps every accepted episode",
        "never treats retrieval misses as candidate blockers",
        "`predictions/YYYY-MM-DD.json`",
        "`reports/YYYY-MM-DD_preopen.md`",
        "`runs/manifests/<run_id>.json`",
        "evaluation, hardcoding audit, lookahead audit, provenance audit",
        "session pack export, and training export",
        "unit, integration, and metamorphic tests",
        "`ruff`, `mypy`, and `pytest`",
        "No production source mapping from region, theme, policy, keyword, company, or ticker",
        "No D-day price access in blind inference.",
        "No cutoff-after evidence in blind reports.",
        "Exhaustive mode must record `swept_episode_count == accepted_episode_count`.",
        "New research must change data/brain outputs, not source code.",
        "Every output must have provenance and a context manifest.",
    ]

    assert all(fragment in plans for fragment in required_fragments)
    assert "THEME_MAP" not in plans
    assert "score += " not in plans
    assert re.search(r"\b\d{6}\b", plans) is None


def test_env_example_keeps_mock_defaults_and_no_secret_values() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env_text = (repo_root / ".env.example").read_text(encoding="utf-8")
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    env_values = dict(
        line.split("=", maxsplit=1)
        for line in env_text.splitlines()
        if line.strip() and not line.startswith("#")
    )

    required_keys = {
        "NSLAB_LLM_PROVIDER",
        "NSLAB_LLM_REASONING_EFFORT",
        "NSLAB_LLM_MAX_OUTPUT_TOKENS",
        "NSLAB_LLM_MAX_RETRIES",
        "OPENAI_API_KEY",
        "NSLAB_OPENAI_MODEL",
        "NSLAB_OPENAI_EMBEDDING_MODEL",
        "NSLAB_PRICE_PROVIDER",
        "NSLAB_STOCK_WEB_PATH",
        "NSLAB_STOCK_WEB_CACHE",
        "NSLAB_STOCK_WEB_CACHE_PATH",
        "NSLAB_STOCK_WEB_REMOTE_URL",
        "NSLAB_WEB_PROVIDER",
        "BRAVE_SEARCH_API_KEY",
        "NSLAB_BRAVE_SEARCH_COUNT",
        "NSLAB_BRAVE_SEARCH_COUNTRY",
        "NSLAB_BRAVE_SEARCH_LANG",
        "NSLAB_BRAVE_SEARCH_UI_LANG",
        "NSLAB_BRAVE_SEARCH_FRESHNESS_DAYS",
        "NSLAB_MAX_CONCURRENCY",
    }

    assert set(env_values) == required_keys
    assert env_values["NSLAB_LLM_PROVIDER"] == "mock"
    assert env_values["NSLAB_PRICE_PROVIDER"] == "mock"
    assert env_values["NSLAB_WEB_PROVIDER"] == "mock"
    assert env_values["NSLAB_STOCK_WEB_CACHE"] == "0"
    assert env_values["OPENAI_API_KEY"] == ""
    assert env_values["BRAVE_SEARCH_API_KEY"] == ""
    assert "OPENAI_API_KEY=..." not in env_text
    assert "BRAVE_SEARCH_API_KEY=..." not in env_text
    assert all("secret" not in value.lower() for value in env_values.values())
    assert ".env" in gitignore


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


def test_goal_source_package_structure_is_tracked() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package_root = repo_root / "src" / "news_scalping_lab"
    expected_packages = [
        "agents",
        "audits",
        "brain",
        "context",
        "contracts",
        "evaluation",
        "inference",
        "ingest",
        "llm",
        "memory",
        "outcomes",
        "prices",
        "reporting",
        "research_import",
        "retrieval",
        "tools",
        "ui",
        "web",
    ]

    missing = [
        relative
        for relative in expected_packages
        if not (package_root / relative / "__init__.py").is_file()
    ]

    assert missing == []


def test_makefile_exposes_quality_and_project_audit_targets() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")

    required_fragments = [
        ".PHONY: install-dev doctor test lint typecheck check audit full-check demo",
        "lint:",
        "\tpython -m ruff check .",
        "typecheck:",
        "\tpython -m mypy src/news_scalping_lab",
        "test:",
        "\tpython -m pytest",
        "audit:",
        "\tpython -m news_scalping_lab.cli audit hardcoding",
        "\tpython -m news_scalping_lab.cli audit provenance",
        "\tpython -m news_scalping_lab.cli audit lookahead --trade-date 2026-06-24",
        "\tpython -m news_scalping_lab.cli audit coverage",
        "\tpython -m news_scalping_lab.cli brain audit",
        "full-check:",
        "\tpython -m news_scalping_lab.cli full-check",
        "demo:",
        "\tpython -m news_scalping_lab.cli brain audit",
        "\tpython -m news_scalping_lab.cli warehouse rebuild",
        (
            "\tpython -m news_scalping_lab.cli analyze --news docs/csv/news_20260624.csv "
            "--trade-date 2026-06-24 --cutoff 2026-06-24T08:59:59+09:00 "
            "--mode exhaustive --web-search"
        ),
        "\tpython -m news_scalping_lab.cli evaluate --trade-date 2026-06-24",
        "\tpython -m news_scalping_lab.cli brain update --episode 2026-06-24",
    ]

    assert all(fragment in makefile for fragment in required_fragments)


def test_readme_documents_full_project_quality_gate() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    required_fragments = [
        "## Quality Gates",
        "python -m ruff check .",
        "python -m mypy src/news_scalping_lab",
        "python -m pytest",
        "python -m news_scalping_lab.cli full-check",
        "make full-check",
    ]

    assert all(fragment in readme for fragment in required_fragments)


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
    evaluation = yaml.safe_load(
        (tmp_path / "configs" / "evaluation.yaml").read_text(encoding="utf-8")
    )
    assert models["default"]["provider"] == "mock"
    assert models["default"]["max_retries"] == 0
    assert models["openai"]["model"] == "gpt-5-mini"
    assert models["openai"]["max_retries"] == 2
    default_config = yaml.safe_load(existing_default.read_text(encoding="utf-8"))
    assert default_config["project_name"] == "custom-lab"
    generated_default = DEFAULT_CONFIG_FILES["default.yaml"]
    assert generated_default["web_provider"] == "mock"
    assert generated_default["brave_search_api_key_env"] == "BRAVE_SEARCH_API_KEY"
    assert inference["default_mode"] == "exhaustive"
    assert {
        "open_gap_pct",
        "intraday_high_return_pct",
        "close_return_pct",
        "upper_limit_released",
        "one_price_upper_limit",
        "volume",
        "amount",
        "turnover_ratio",
        "market_cap_previous_close",
    }.issubset(set(evaluation["labels"]))
    assert "Average max return of top N" in evaluation["metrics"]
    assert "Gap-up hit rate" in evaluation["metrics"]
    assert "False-positive rate" in evaluation["metrics"]


def test_load_settings_reads_selected_llm_model_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NSLAB_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("NSLAB_LLM_MODEL", raising=False)
    monkeypatch.delenv("NSLAB_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("NSLAB_OPENAI_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("NSLAB_LLM_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("NSLAB_LLM_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("NSLAB_LLM_MAX_RETRIES", raising=False)
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
                "  max_retries: 3",
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
    assert settings.llm.max_retries == 3


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
    monkeypatch.delenv("NSLAB_PRICE_PROVIDER", raising=False)
    monkeypatch.delenv("NSLAB_BRAVE_SEARCH_COUNT", raising=False)
    monkeypatch.delenv("NSLAB_BRAVE_SEARCH_COUNTRY", raising=False)
    monkeypatch.delenv("NSLAB_BRAVE_SEARCH_LANG", raising=False)
    monkeypatch.delenv("NSLAB_BRAVE_SEARCH_UI_LANG", raising=False)
    monkeypatch.delenv("NSLAB_BRAVE_SEARCH_FRESHNESS_DAYS", raising=False)
    monkeypatch.delenv("NSLAB_BRAVE_SEARCH_API_KEY_ENV", raising=False)
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
    monkeypatch.delenv("NSLAB_LLM_MAX_RETRIES", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "# local secrets and runtime settings",
                "NSLAB_LLM_PROVIDER=openai",
                "NSLAB_WEB_PROVIDER=brave",
                "NSLAB_PRICE_PROVIDER=stock-web",
                "NSLAB_BRAVE_SEARCH_COUNT=15",
                "NSLAB_BRAVE_SEARCH_COUNTRY=KR",
                "NSLAB_BRAVE_SEARCH_LANG=ko",
                "NSLAB_BRAVE_SEARCH_UI_LANG=ko-KR",
                "NSLAB_BRAVE_SEARCH_FRESHNESS_DAYS=3",
                "NSLAB_STOCK_WEB_PATH='data/cache/stock-web'",
                "NSLAB_STOCK_WEB_CACHE=1",
                "NSLAB_STOCK_WEB_CACHE_PATH=data/cache/custom-stock-web",
                "NSLAB_STOCK_WEB_REMOTE_URL=https://example.test/stock-web.git",
                "NSLAB_MAX_CONCURRENCY=7",
                "NSLAB_OPENAI_MODEL=gpt-dotenv",
                "NSLAB_OPENAI_EMBEDDING_MODEL=embed-dotenv",
                "NSLAB_LLM_REASONING_EFFORT=medium",
                "NSLAB_LLM_MAX_OUTPUT_TOKENS=9876",
                "NSLAB_LLM_MAX_RETRIES=4",
                "OPENAI_API_KEY=secret-from-dotenv",
                "MALFORMED LINE",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings.llm_provider == "openai"
    assert settings.web_provider == "brave"
    assert settings.brave_search_count == 15
    assert settings.brave_search_country == "KR"
    assert settings.brave_search_lang == "ko"
    assert settings.brave_search_ui_lang == "ko-KR"
    assert settings.brave_search_freshness_days == 3
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
    assert settings.llm.max_retries == 4
    assert settings.path(settings.stock_web_cache_path) == (
        tmp_path / "data/cache/custom-stock-web"
    )
    assert settings.path(settings.stock_web_path) == tmp_path / "data/cache/stock-web"
    assert settings.env_value("OPENAI_API_KEY") == "secret-from-dotenv"
    assert os.getenv("OPENAI_API_KEY") is None
    assert os.getenv("NSLAB_PRICE_PROVIDER") is None

    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "mock")
    assert load_settings(tmp_path).llm_provider == "mock"
