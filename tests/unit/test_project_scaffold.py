from __future__ import annotations

import yaml

from news_scalping_lab.config import Settings, ensure_project_dirs, write_default_config_files


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
