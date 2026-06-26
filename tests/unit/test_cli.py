from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import news_scalping_lab.cli as cli_module
from news_scalping_lab.cli import app
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, canonical_json, sha256_text
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


def _cli_brain_record(record_id: str = "BRAIN-CLI") -> BrainRecordEnvelope:
    available_from = datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST)
    payload = {
        "record_id": record_id,
        "record_type": "memory_claim",
        "episode_id": "EP-cli",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "training_target": "cli_contract",
        "summary": "CLI record contract fixture.",
        "training_eligible": False,
        "provenance_source_ids": ["SRC-cli"],
    }
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="memory_claim",
        episode_id="EP-cli",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target="cli_contract",
        evidence_phase="AUDIT",
        training_eligible=False,
        eligibility_reason="cli contract fixture",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=["SRC-cli"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )


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


def test_doctor_production_cli_reports_record_store_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    result = CliRunner().invoke(app, ["doctor", "--production"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    production = payload["production_readiness"]
    record_store = production["record_store"]
    assert production["passed"] is False
    assert record_store["schema_version"] == "nslab.production_record_store.v1"
    assert record_store["deep"] is True
    assert record_store["passed"] is True
    assert record_store["record_count"] == 0
    assert "llm: mock provider cannot compile production brain" in production["findings"]


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


def test_research_inspect_bundle_cli_writes_smoke_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text("bundle", encoding="utf-8")
    inspection = {
        "inspection_status": "validation_failed",
        "path": bundle.as_posix(),
        "bundle_schema_version": "nslab.research_bundle.v11",
        "manifest_schema_version": "nslab.bundle_manifest.v11",
        "episode_schema_version": "nslab.research_episode.v11",
        "adapter": "v11",
        "supported": True,
        "forward_compatible_raw_only": False,
        "episode_id": "NSLAB-20300110-SYNTH",
        "trade_date": "2030-01-10",
        "raw_record_count": 327,
        "normalized_record_count": 327,
        "training_eligible_record_count": 325,
        "dropped_record_count": 0,
        "quarantined_record_count": 0,
        "record_counts_by_type": {"supervised_issuer_day_case": 150},
        "validation_passed": False,
        "record_count_matches_manifest": True,
        "training_eligible_count_matches_manifest": True,
        "hash_mismatch_count": 16,
        "hash_expectation_conflict_count": 0,
        "missing_source_reference_count": 0,
        "missing_payload_reference_count": 2,
        "available_from_valid": True,
        "invalid_available_from_record_count": 0,
        "outcome_label_quality_valid": False,
        "invalid_outcome_label_quality_record_count": 1,
        "validation": {"passed": False, "hash_mismatches": {"brain_delta.jsonl": {}}},
    }

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "inspect_versioned_bundle", lambda path: inspection)

    result = CliRunner().invoke(app, ["research", "inspect-bundle", str(bundle)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["inspection_status"] == "validation_failed"
    report = json.loads(
        (tmp_path / "diagnostics" / "bundle_inspection_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["schema_version"] == "nslab.bundle_inspection_diagnostics.v1"
    assert report["status"] == "validation_failed"
    assert report["bundle_version"] == "nslab.research_bundle.v11"
    assert report["raw_record_count"] == 327
    assert report["normalized_record_count"] == 327
    assert report["dropped_record_count"] == 0
    assert report["hash_mismatch_count"] == 16
    assert report["missing_payload_reference_count"] == 2
    assert report["available_from_valid"] is True
    assert report["outcome_label_quality_valid"] is False
    assert report["invalid_outcome_label_quality_record_count"] == 1
    assert report["validation"]["passed"] is False


def test_research_smoke_bundle_cli_writes_pending_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))

    result = CliRunner().invoke(app, ["research", "smoke-bundle"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "nslab.real_bundle_smoke.v1"
    assert payload["status"] == "pending"
    assert payload["passed"] is False
    report = json.loads(
        (tmp_path / "diagnostics" / "bundle_smoke_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "pending"

    required = CliRunner().invoke(app, ["research", "smoke-bundle", "--require-valid"])

    assert required.exit_code == 1


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


def test_research_migrate_legacy_writes_catalog_only_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    store = ResearchStore(tmp_path)
    trade_day = date(2030, 1, 10)
    episode = ResearchEpisode(
        episode_id="EP-legacy-cli",
        trade_date=trade_day,
        cutoff_at=datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST),
        created_at=datetime.combine(trade_day, time(16, 0, 0), tzinfo=KST),
        research_version="legacy-cli-test",
        price_source_snapshot={"source": "legacy-cli-test"},
        blind_analysis=BlindAnalysis(
            summary="Legacy accepted episode.",
            open_world_mechanisms=["legacy migration -> catalog-only record"],
        ),
        available_from=datetime.combine(date(2030, 1, 11), time(0, 0, 0), tzinfo=KST),
    )
    store.save_episode(episode)
    store.accept(episode.episode_id)
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    result = CliRunner().invoke(app, ["research", "migrate-legacy"])

    assert result.exit_code == 0, result.output
    report = json.loads(
        (tmp_path / "diagnostics" / "migration_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["schema_version"] == "nslab.legacy_migration_report.v1"
    assert report["passed"] is True
    assert report["catalog_only"] is True
    assert report["source_episode_count"] == 1
    assert report["legacy_source_record_count"] == 1
    assert report["raw_record_count"] == 1
    assert report["normalized_record_count"] == 1
    assert report["training_eligible_record_count"] == 0
    assert report["ineligible_record_count"] == 1
    assert report["dropped_record_count"] == 0
    assert report["quarantined_record_count"] == 0
    assert report["record_counts_by_type"] == {"memory_claim": 1}


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


def test_context_inspect_cli_outputs_manifest_inspection_and_strict_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-inspect.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "RUN-inspect",
                "mode": "exhaustive",
                "trade_date": "2030-01-10",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))

    result = CliRunner().invoke(app, ["context", "inspect", "RUN-inspect"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == "RUN-inspect"
    assert payload["inspection"]["context_manifest"]["exists"] is True
    assert payload["inspection"]["context_manifest"]["path"] == (
        "runs/manifests/RUN-inspect.json"
    )
    assert payload["inspection"]["reproducibility_checks_passed"] is False

    strict_result = CliRunner().invoke(
        app,
        ["context", "inspect", "RUN-inspect", "--strict"],
    )

    assert strict_result.exit_code == 1


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


def test_memory_audit_cli_writes_diagnostic_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir(parents=True)
    (diagnostics_dir / "brain_record_store_report.json").write_text(
        json.dumps({"warehouse_counts": {"brain_records": 7}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    result = CliRunner().invoke(app, ["memory", "audit", "--deep"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    report_path = tmp_path / "diagnostics" / "brain_record_store_report.json"
    markdown_path = tmp_path / "diagnostics" / "brain_record_store_report.md"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["diagnostic_report"] == {
        "json": "diagnostics/brain_record_store_report.json",
        "markdown": "diagnostics/brain_record_store_report.md",
    }
    assert report["schema_version"] == "nslab.brain_record_store_report.v1"
    assert report["audit_passed"] is True
    assert report["record_counts_by_evidence_phase"] == {}
    assert report["record_counts_by_training_target"] == {}
    assert report["record_store_audit"]["passed"] is True
    assert report["record_store_audit"]["deep"] is True
    assert report["record_count"] == 0
    assert report["warehouse_counts"] == {"brain_records": 7}
    assert markdown_path.exists()


def test_memory_inspect_record_cli_outputs_record_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    record = _cli_brain_record()
    records_path = tmp_path / "memory" / "records" / "EP-cli.jsonl"
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    result = CliRunner().invoke(app, ["memory", "inspect-record", "BRAIN-CLI"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["record_id"] == "BRAIN-CLI"
    assert payload["record_type"] == "memory_claim"
    assert payload["episode_id"] == "EP-cli"
    assert payload["payload"]["summary"] == "CLI record contract fixture."
    assert payload["normalized_payload_sha256"] == record.normalized_payload_sha256


def test_memory_inspect_record_cli_reports_missing_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    result = CliRunner().invoke(app, ["memory", "inspect-record", "BRAIN-missing"])

    assert result.exit_code == 1
    assert "record not found: BRAIN-missing" in result.output


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


def test_warehouse_verify_cli_passes_synced_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    WarehouseStore(tmp_path).rebuild_all()
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    result = CliRunner().invoke(app, ["warehouse", "verify"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert sorted(payload["required_files"]) == sorted(EXPECTED_WAREHOUSE_FILES)
    assert payload["warehouse_findings"] == []
    assert payload["warehouse_duplicate_identities"] == {}
    assert payload["warehouse_weight_mismatches"] == {}


def test_warehouse_verify_cli_exits_nonzero_on_warehouse_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mismatched_coverage(root: Path) -> dict[str, object]:
        return {
            "findings": ["warehouse: brain_records.parquet count 1 != expected 2"],
            "warehouse_required_files_present": True,
            "warehouse_projection_synced": False,
            "warehouse_counts": {"brain_records.parquet": 1},
            "warehouse_duplicate_identities": {},
            "warehouse_weight_mismatches": {},
            "warehouse_required_files": ["brain_records.parquet"],
        }

    monkeypatch.setattr(cli_module, "load_settings", lambda: Settings(project_root=tmp_path))
    monkeypatch.setattr(cli_module, "audit_coverage", mismatched_coverage)

    result = CliRunner().invoke(app, ["warehouse", "verify"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["passed"] is False
    assert payload["warehouse_findings"] == [
        "warehouse: brain_records.parquet count 1 != expected 2"
    ]


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
    steps = cli_module._full_check_steps()
    step_names = [name for name, command in steps]
    step_commands = dict(steps)

    assert "training export-sft" in step_names
    assert "training export-preference" in step_names
    assert "training export-evals" in step_names
    assert "training audit" in step_names
    assert "memory audit deep" in step_names
    assert "brain audit deep" in step_names
    assert step_names.index("training export-sft") < step_names.index("training audit")
    assert step_names.index("training export-preference") < step_names.index("training audit")
    assert step_names.index("training export-evals") < step_names.index("training audit")
    assert step_names.index("memory audit deep") < step_names.index("brain audit deep")
    assert step_commands["memory audit deep"][-2:] == ["audit", "--deep"]
    assert step_commands["brain audit deep"][-2:] == ["audit", "--deep"]


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

    assert step_names[-7:] == [
        "warehouse rebuild after update",
        "training export-sft",
        "training export-preference",
        "training export-evals",
        "training audit",
        "memory audit deep after update",
        "brain audit deep after update",
    ]
    assert step_names.index("brain update") < step_names.index("training export-sft")
    assert step_names.index("memory audit deep after update") < step_names.index(
        "brain audit deep after update"
    )


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
    assert payload["status"]["duplicate_identities"] == {}
    assert payload["status"]["weight_mismatches"] == {}


def test_warehouse_query_records_cli_filters_record_level_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    seen: list[dict[str, object]] = []

    class QueryWarehouseStore:
        def __init__(self, root: Path) -> None:
            self.root = root

        def query_brain_records(self, **filters: object) -> list[dict[str, object]]:
            seen.append(filters)
            return [
                {
                    "record_id": "BRAIN-query",
                    "record_type": filters["record_type"],
                    "ticker": filters["ticker"],
                    "training_eligible": filters["training_eligible"],
                    "payload": {"ticker": filters["ticker"]},
                }
            ]

    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "WarehouseStore", QueryWarehouseStore)

    result = CliRunner().invoke(
        app,
        [
            "warehouse",
            "query-records",
            "--record-type",
            "supervised_issuer_day_case",
            "--ticker",
            "000001",
            "--available-from-as-of",
            "2030-01-11T00:00:00+09:00",
            "--training-eligible-only",
            "--limit",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["row_count"] == 1
    assert payload["rows"][0]["record_id"] == "BRAIN-query"
    assert seen == [
        {
            "record_type": "supervised_issuer_day_case",
            "training_target": None,
            "evidence_phase": None,
            "ticker": "000001",
            "company_name": None,
            "theme_id": None,
            "path_type": None,
            "response_class": None,
            "confidence_label": None,
            "trade_date_from": None,
            "trade_date_to": None,
            "available_from_as_of": "2030-01-11T00:00:00+09:00",
            "training_eligible": True,
            "limit": 5,
        }
    ]


def test_warehouse_query_records_cli_rejects_conflicting_eligibility_flags() -> None:
    result = CliRunner().invoke(
        app,
        [
            "warehouse",
            "query-records",
            "--training-eligible-only",
            "--ineligible-only",
        ],
    )

    assert result.exit_code == 1
    assert "cannot be combined" in result.output
