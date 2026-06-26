from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import news_scalping_lab.cli as cli_module
from news_scalping_lab.cli import app
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.warehouse import EXPECTED_WAREHOUSE_FILES, WarehouseStore


class _AnalysisResult:
    def __init__(self, *, mode: str) -> None:
        self.mode = mode

    def model_dump(self, *, mode: str = "json") -> dict[str, str]:
        return {"mode": self.mode, "dump_mode": mode}


class _BrainResult:
    def __init__(self, *, shard_episode_count: int) -> None:
        self.shard_episode_count = shard_episode_count

    def model_dump(self, *, mode: str = "json") -> dict[str, int | str]:
        return {"shard_episode_count": self.shard_episode_count, "dump_mode": mode}


class _EvaluationResult:
    def __init__(self, *, report_path: Path) -> None:
        self.report_path = report_path
        self.episode_id = "EP-test"
        self.episode_path = report_path.with_name("episode.json")


class _TrainingExportResult:
    def __init__(self, *, path: Path) -> None:
        self.path = path
        self.manifest_path = path.with_name("manifest.json")
        self.row_count = 0


def test_analyze_cli_uses_configured_default_mode_when_mode_is_omitted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured_modes: list[str] = []

    class CapturingAnalyzer:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def analyze(self, **kwargs: Any) -> _AnalysisResult:
            selected_mode = kwargs["mode"]
            captured_modes.append(selected_mode)
            return _AnalysisResult(mode=selected_mode)

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda: Settings(project_root=tmp_path, default_mode="fast"),
    )
    monkeypatch.setattr(cli_module, "DailyAnalyzer", CapturingAnalyzer)

    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "--news",
            str(tmp_path / "news.csv"),
            "--trade-date",
            "2030-01-10",
            "--cutoff",
            "2030-01-10T08:59:59+09:00",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_modes == ["fast"]
    assert json.loads(result.output)["mode"] == "fast"


def test_analyze_cli_explicit_mode_overrides_configured_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured_modes: list[str] = []

    class CapturingAnalyzer:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def analyze(self, **kwargs: Any) -> _AnalysisResult:
            selected_mode = kwargs["mode"]
            captured_modes.append(selected_mode)
            return _AnalysisResult(mode=selected_mode)

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda: Settings(project_root=tmp_path, default_mode="exhaustive"),
    )
    monkeypatch.setattr(cli_module, "DailyAnalyzer", CapturingAnalyzer)

    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "--news",
            str(tmp_path / "news.csv"),
            "--trade-date",
            "2030-01-10",
            "--cutoff",
            "2030-01-10T08:59:59+09:00",
            "--mode",
            "brain",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_modes == ["brain"]
    assert json.loads(result.output)["mode"] == "brain"


def test_news_inspect_cli_reports_csv_validation_errors(tmp_path: Path) -> None:
    news_csv = tmp_path / "bad_news.csv"
    news_csv.write_text("date\n2030-01-10\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["news", "inspect", str(news_csv)])

    assert result.exit_code == 1
    assert "CSV missing required columns: time, title" in result.output


def test_news_import_cli_reports_csv_validation_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    news_csv = tmp_path / "bad_news.csv"
    news_csv.write_text("date\n2030-01-10\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))

    result = CliRunner().invoke(app, ["news", "import", str(news_csv)])

    assert result.exit_code == 1
    assert "CSV missing required columns: time, title" in result.output


def test_research_import_cli_reports_import_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "research.md"
    source.write_text("free-form research", encoding="utf-8")

    class FailingResearchImporter:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def import_path(self, path: Path, *, mode: str = "auto") -> object:
            raise ValueError(f"mode rejected during import: {mode}")

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "create_llm_provider", lambda settings: object())
    monkeypatch.setattr(cli_module, "ResearchImporter", FailingResearchImporter)

    result = CliRunner().invoke(
        app,
        ["research", "import", str(source), "--mode", "unsupported"],
    )

    assert result.exit_code == 1
    assert "mode rejected during import: unsupported" in result.output


def test_research_import_batch_cli_reports_source_file_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "research.md"
    source.write_text("free-form research", encoding="utf-8")

    class FailingResearchImporter:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def import_path(self, path: Path, *, mode: str = "auto") -> object:
            raise RuntimeError("semantic import failed")

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "create_llm_provider", lambda settings: object())
    monkeypatch.setattr(cli_module, "ResearchImporter", FailingResearchImporter)

    result = CliRunner().invoke(
        app,
        ["research", "import-batch", str(inbox), "--mode", "semantic"],
    )

    assert result.exit_code == 1
    assert "research import-batch failed for" in result.output
    assert "research.md" in result.output
    assert "semantic import failed" in result.output


def test_analyze_cli_reports_analysis_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingAnalyzer:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def analyze(self, **kwargs: Any) -> _AnalysisResult:
            raise FileNotFoundError("news file not found: missing.csv")

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "DailyAnalyzer", FailingAnalyzer)

    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "--news",
            str(tmp_path / "missing.csv"),
            "--trade-date",
            "2030-01-10",
            "--cutoff",
            "2030-01-10T08:59:59+09:00",
        ],
    )

    assert result.exit_code == 1
    assert "news file not found: missing.csv" in result.output


def test_analyze_cli_reports_invalid_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))

    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "--news",
            str(tmp_path / "news.csv"),
            "--trade-date",
            "2030-01-10",
            "--cutoff",
            "not-a-timestamp",
        ],
    )

    assert result.exit_code != 0
    assert "expected ISO timestamp" in result.output


def test_evaluate_cli_reports_missing_prediction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingEvaluator:
        def __init__(self, root: Path) -> None:
            self.root = root

        def evaluate(self, *, trade_date: object) -> _EvaluationResult:
            raise FileNotFoundError("blind prediction not found: predictions/2030-01-10.json")

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "Evaluator", FailingEvaluator)

    result = CliRunner().invoke(app, ["evaluate", "--trade-date", "2030-01-10"])

    assert result.exit_code == 1
    assert "blind prediction not found: predictions/2030-01-10.json" in result.output


def test_evaluate_cli_reports_invalid_postmortem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "reports" / "2030-01-10_postmortem.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("[]\n", encoding="utf-8")

    class InvalidPostmortemEvaluator:
        def __init__(self, root: Path) -> None:
            self.root = root

        def evaluate(self, *, trade_date: object) -> _EvaluationResult:
            return _EvaluationResult(report_path=report_path)

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "Evaluator", InvalidPostmortemEvaluator)

    result = CliRunner().invoke(app, ["evaluate", "--trade-date", "2030-01-10"])

    assert result.exit_code == 1
    assert "postmortem report must be a JSON object" in result.output


def test_context_inspect_cli_reports_missing_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))

    result = CliRunner().invoke(app, ["context", "inspect", "RUN-missing"])

    assert result.exit_code == 1
    assert "context manifest not found" in result.output


def test_context_inspect_cli_reports_invalid_manifest_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-bad.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))

    result = CliRunner().invoke(app, ["context", "inspect", "RUN-bad"])

    assert result.exit_code == 1
    assert "context manifest must be a JSON object" in result.output


def test_context_export_session_pack_cli_reports_invalid_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))

    result = CliRunner().invoke(
        app,
        [
            "context",
            "export-session-pack",
            "--news",
            str(tmp_path / "news.csv"),
            "--trade-date",
            "2030-01-10",
            "--cutoff",
            "not-a-timestamp",
        ],
    )

    assert result.exit_code != 0
    assert "expected ISO timestamp" in result.output


def test_context_export_session_pack_cli_reports_export_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_export(*args: object, **kwargs: object) -> Path:
        raise ValueError("session pack failed: missing brain")

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "export_session_pack", failing_export)

    result = CliRunner().invoke(
        app,
        [
            "context",
            "export-session-pack",
            "--news",
            str(tmp_path / "news.csv"),
            "--trade-date",
            "2030-01-10",
        ],
    )

    assert result.exit_code == 1
    assert "session pack failed: missing brain" in result.output


def test_context_export_analysis_bundle_cli_reports_export_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_export(settings: Settings, *, run_id: str) -> Path:
        raise FileNotFoundError(f"analysis bundle source not found: {run_id}")

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "export_analysis_bundle", failing_export)

    result = CliRunner().invoke(
        app,
        ["context", "export-analysis-bundle", "--run-id", "RUN-missing"],
    )

    assert result.exit_code == 1
    assert "analysis bundle source not found: RUN-missing" in result.output


def test_brain_rebuild_cli_passes_configured_shard_episode_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured_counts: list[int] = []
    settings = Settings(project_root=tmp_path)
    settings.limits.shard_episode_count = 3

    class CapturingBrainCompiler:
        def __init__(
            self,
            root: Path,
            store: object | None = None,
            *,
            shard_episode_count: int,
        ) -> None:
            self.root = root
            self.store = store
            self.shard_episode_count = shard_episode_count

        def rebuild(self, *, mode: str = "full") -> _BrainResult:
            captured_counts.append(self.shard_episode_count)
            return _BrainResult(shard_episode_count=self.shard_episode_count)

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "BrainCompiler", CapturingBrainCompiler)

    result = CliRunner().invoke(app, ["brain", "rebuild", "--mode", "full"])

    assert result.exit_code == 0, result.output
    assert captured_counts == [3]
    assert json.loads(result.output)["shard_episode_count"] == 3


def test_brain_rebuild_cli_defaults_to_llm_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_modes: list[str] = []

    class CapturingBrainCompiler:
        def __init__(
            self,
            root: Path,
            store: object | None = None,
            *,
            shard_episode_count: int,
        ) -> None:
            self.root = root
            self.store = store
            self.shard_episode_count = shard_episode_count

        def rebuild(self, *, mode: str = "full") -> _BrainResult:
            captured_modes.append(mode)
            return _BrainResult(shard_episode_count=self.shard_episode_count)

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "BrainCompiler", CapturingBrainCompiler)

    result = CliRunner().invoke(app, ["brain", "rebuild"])

    assert result.exit_code == 0, result.output
    assert captured_modes == ["llm-full"]


def test_brain_rebuild_cli_reports_invalid_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))

    result = CliRunner().invoke(app, ["brain", "rebuild", "--mode", "incremental"])

    assert result.exit_code == 1
    assert "only full rebuild is currently supported" in result.output


def test_brain_update_cli_passes_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_modes: list[str] = []

    class CapturingBrainCompiler:
        def __init__(
            self,
            root: Path,
            store: object | None = None,
            *,
            shard_episode_count: int,
        ) -> None:
            self.root = root
            self.store = store
            self.shard_episode_count = shard_episode_count

        def update(self, *, episode_id: str, mode: str = "full") -> _BrainResult:
            captured_modes.append(f"{episode_id}:{mode}")
            return _BrainResult(shard_episode_count=self.shard_episode_count)

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "BrainCompiler", CapturingBrainCompiler)

    result = CliRunner().invoke(
        app,
        ["brain", "update", "--episode", "EP-test", "--mode", "llm-full"],
    )

    assert result.exit_code == 0, result.output
    assert captured_modes == ["EP-test:llm-full"]


def test_brain_update_cli_defaults_to_llm_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_modes: list[str] = []

    class CapturingBrainCompiler:
        def __init__(
            self,
            root: Path,
            store: object | None = None,
            *,
            shard_episode_count: int,
        ) -> None:
            self.root = root
            self.store = store
            self.shard_episode_count = shard_episode_count

        def update(self, *, episode_id: str, mode: str = "full") -> _BrainResult:
            captured_modes.append(f"{episode_id}:{mode}")
            return _BrainResult(shard_episode_count=self.shard_episode_count)

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "BrainCompiler", CapturingBrainCompiler)

    result = CliRunner().invoke(app, ["brain", "update", "--episode", "EP-test"])

    assert result.exit_code == 0, result.output
    assert captured_modes == ["EP-test:llm-full"]


def test_brain_diff_cli_reports_missing_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))

    result = CliRunner().invoke(app, ["brain", "diff", "missing-a", "missing-b"])

    assert result.exit_code == 1
    assert "brain snapshot not found: missing-a" in result.output


def test_training_export_cli_reports_export_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_export(root: Path, *, kind: str) -> _TrainingExportResult:
        raise ValueError(f"training export failed: {kind}")

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "export_training", failing_export)

    result = CliRunner().invoke(app, ["training", "export-sft"])

    assert result.exit_code == 1
    assert "training export failed: sft" in result.output


def test_warehouse_rebuild_cli_reports_rebuild_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingWarehouseStore:
        def __init__(self, root: Path) -> None:
            self.root = root

        def rebuild_all(self) -> dict[str, int]:
            raise ValueError("warehouse rebuild failed: invalid source json")

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "WarehouseStore", FailingWarehouseStore)

    result = CliRunner().invoke(app, ["warehouse", "rebuild"])

    assert result.exit_code == 1
    assert "warehouse rebuild failed: invalid source json" in result.output


def test_warehouse_inspect_cli_reports_audit_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_audit(root: Path) -> dict[str, object]:
        raise ValueError("warehouse inspect failed: invalid parquet")

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "audit_coverage", failing_audit)

    result = CliRunner().invoke(app, ["warehouse", "inspect"])

    assert result.exit_code == 1
    assert "warehouse inspect failed: invalid parquet" in result.output


def test_full_check_cli_runs_configured_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = (
        ("lint", ["python", "-m", "ruff", "check", "."]),
        ("audit", ["python", "-m", "news_scalping_lab.cli", "audit", "coverage"]),
    )
    seen: list[list[str]] = []

    def fake_run(command: list[str]) -> int:
        seen.append(command)
        return 0

    monkeypatch.setattr(cli_module, "_full_check_steps", lambda: steps)
    monkeypatch.setattr(cli_module, "_run_full_check_step", fake_run)

    result = CliRunner().invoke(app, ["full-check"])

    assert result.exit_code == 0, result.output
    assert seen == [step[1] for step in steps]
    assert '"passed": true' in result.output


def test_full_check_cli_stops_on_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = (
        ("lint", ["python", "-m", "ruff", "check", "."]),
        ("pytest", ["python", "-m", "pytest"]),
        ("audit", ["python", "-m", "news_scalping_lab.cli", "audit", "coverage"]),
    )
    exit_codes = {"lint": 0, "pytest": 5, "audit": 0}
    seen: list[list[str]] = []

    def fake_run(command: list[str]) -> int:
        seen.append(command)
        step_name = next(name for name, step_command in steps if step_command == command)
        return exit_codes[step_name]

    monkeypatch.setattr(cli_module, "_full_check_steps", lambda: steps)
    monkeypatch.setattr(cli_module, "_run_full_check_step", fake_run)

    result = CliRunner().invoke(app, ["full-check"])

    assert result.exit_code == 5
    assert seen == [steps[0][1], steps[1][1]]
    assert '"passed": false' in result.output
    assert '"failed": "pytest"' in result.output


def test_full_check_steps_export_training_before_training_audit() -> None:
    step_names = [name for name, command in cli_module._full_check_steps()]

    assert "training export-sft" in step_names
    assert "training export-preference" in step_names
    assert "training export-evals" in step_names
    assert "training audit" in step_names
    assert step_names.index("training export-sft") < step_names.index("training audit")
    assert step_names.index("training export-preference") < step_names.index("training audit")
    assert step_names.index("training export-evals") < step_names.index("training audit")


def test_demo_cli_runs_configured_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = (
        ("init", ["python", "-m", "news_scalping_lab.cli", "init"]),
        ("analyze", ["python", "-m", "news_scalping_lab.cli", "analyze"]),
    )
    seen: list[list[str]] = []

    def fake_steps(**kwargs: object) -> tuple[tuple[str, list[str]], ...]:
        assert kwargs["trade_date"] == "2030-01-10"
        assert kwargs["web_search"] is False
        return steps

    def fake_run(command: list[str]) -> int:
        seen.append(command)
        return 0

    monkeypatch.setattr(cli_module, "_demo_steps", fake_steps)
    monkeypatch.setattr(cli_module, "_run_demo_step", fake_run)

    result = CliRunner().invoke(
        app,
        [
            "demo",
            "--news",
            str(tmp_path / "news.csv"),
            "--trade-date",
            "2030-01-10",
            "--cutoff",
            "2030-01-10T08:59:59+09:00",
            "--no-web-search",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == [step[1] for step in steps]
    assert '"passed": true' in result.output


def test_demo_steps_refresh_derived_artifacts_after_brain_update() -> None:
    steps = cli_module._demo_steps(
        news=Path("docs/csv/news_20260624.csv"),
        trade_date="2026-06-24",
        cutoff="2026-06-24T08:59:59+09:00",
        mode="exhaustive",
        web_search=True,
    )
    step_names = [name for name, command in steps]

    assert step_names[-6:] == [
        "warehouse rebuild after update",
        "training export-sft",
        "training export-preference",
        "training export-evals",
        "training audit",
        "brain audit after update",
    ]
    assert step_names.index("brain update") < step_names.index("training export-sft")


def test_demo_cli_stops_on_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = (
        ("init", ["python", "-m", "news_scalping_lab.cli", "init"]),
        ("analyze", ["python", "-m", "news_scalping_lab.cli", "analyze"]),
        ("evaluate", ["python", "-m", "news_scalping_lab.cli", "evaluate"]),
    )
    seen: list[list[str]] = []

    def fake_run(command: list[str]) -> int:
        seen.append(command)
        return 7 if command == steps[1][1] else 0

    monkeypatch.setattr(cli_module, "_demo_steps", lambda **kwargs: steps)
    monkeypatch.setattr(cli_module, "_run_demo_step", fake_run)

    result = CliRunner().invoke(app, ["demo"])

    assert result.exit_code == 7
    assert seen == [steps[0][1], steps[1][1]]
    assert '"passed": false' in result.output
    assert '"failed": "analyze"' in result.output


def test_warehouse_inspect_cli_includes_counts_and_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    WarehouseStore(tmp_path).rebuild_all()
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    result = CliRunner().invoke(app, ["warehouse", "inspect"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["research_episodes.parquet"] == 0
    assert payload["events.parquet"] == 0
    assert sorted(payload["status"]["required_files"]) == sorted(EXPECTED_WAREHOUSE_FILES)
    assert payload["status"]["required_files_present"] is True
    assert payload["status"]["missing_files"] == []
    assert payload["status"]["unreadable_files"] == []
    assert payload["status"]["count_mismatches"] == {}
    assert payload["status"]["identity_mismatches"] == {}
