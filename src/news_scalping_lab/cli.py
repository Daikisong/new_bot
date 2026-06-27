"""Command line interface."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime
from importlib import import_module
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from news_scalping_lab.audits.coverage import audit_coverage
from news_scalping_lab.audits.hardcoding import audit_hardcoding
from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.brain.audit import audit_brain
from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.brain.diff import build_brain_diff, write_brain_diff_markdown
from news_scalping_lab.config import ensure_project_dirs, load_settings, write_default_config_files
from news_scalping_lab.context.episode_scope import inspect_manifest_episode_scope
from news_scalping_lab.context.final_synthesis import (
    FINAL_SYNTHESIS_REQUIRED_INPUTS,
    final_synthesis_input_summary,
    final_synthesis_required_inputs_compatible,
)
from news_scalping_lab.context.session_pack import (
    SessionPackBudgetExceededError,
    SessionPackFutureContextError,
    export_session_pack,
)
from news_scalping_lab.contracts.schemas import export_json_schemas
from news_scalping_lab.diagnostic_reports import write_diagnostic_report
from news_scalping_lab.diagnostics import (
    build_doctor_report,
    production_readiness_report,
    real_bundle_smoke_report,
)
from news_scalping_lab.evaluation.evaluator import Evaluator
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.ingest.news import import_news_csv, load_news_csv
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.memory.company import CompanyMemoryStore
from news_scalping_lab.records.models import (
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.records.store import (
    BrainRecordStore,
    audit_record_store,
    record_store_report_payload,
)
from news_scalping_lab.reporting.bundle import export_analysis_bundle
from news_scalping_lab.reporting.sections import inspect_preopen_report_sections
from news_scalping_lab.research_import.bundle import (
    CANDIDATE_WEB_CHECK_REQUIRED_FIELDS,
    EXCLUDED_CANDIDATE_WEB_CHECK_REQUIRED_FIELDS,
    SOURCE_LEDGER_REQUIRED_FIELDS,
    SOURCE_LEDGER_USAGE_PHASES,
    WEB_TIMESTAMP_PRECISIONS,
)
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.research_import.versioned_bundle import (
    import_versioned_bundle,
    inspect_versioned_bundle,
)
from news_scalping_lab.retrieval.embedding import AsyncEmbeddingProviderAdapter
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.training import audit_training_exports, export_training
from news_scalping_lab.ui.launcher import (
    StreamlitLaunchConfig,
    StreamlitLaunchError,
    run_streamlit_ui,
)
from news_scalping_lab.utils import (
    canonical_json,
    default_news_window_start,
    file_sha256,
    parse_datetime,
    read_json,
    relative_to_root,
    sha256_text,
    write_json,
)
from news_scalping_lab.warehouse import WarehouseStore

app = typer.Typer(help="news-scalping-lab CLI")
news_app = typer.Typer(help="News CSV commands")
research_app = typer.Typer(help="Research episode commands")
brain_app = typer.Typer(help="Brain compiler commands")
context_app = typer.Typer(help="Context manifest and session pack commands")
audit_app = typer.Typer(help="Audit commands")
training_app = typer.Typer(help="Training export commands")
warehouse_app = typer.Typer(help="Warehouse projection commands")
memory_app = typer.Typer(help="Brain record memory commands")

app.add_typer(news_app, name="news")
app.add_typer(research_app, name="research")
app.add_typer(brain_app, name="brain")
app.add_typer(context_app, name="context")
app.add_typer(audit_app, name="audit")
app.add_typer(training_app, name="training")
app.add_typer(warehouse_app, name="warehouse")
app.add_typer(memory_app, name="memory")

WEB_SOURCE_REQUIRED_FIELDS = {
    "schema_version",
    "source_id",
    "query",
    "title",
    "url",
    "source_url",
    "snippet",
    "published_at",
    "retrieved_at",
    "cutoff_at",
    "time_verified",
    "available_before_cutoff",
    "content_sha256",
    "opened_text_sha256",
    "opened_text_excerpt",
}
OPENAI_LLM_PROVIDER_ALIASES = {"openai", "responses", "openai-responses"}


def _validated_brain_cli_mode(
    mode: str,
    *,
    allow_catalog: bool,
    action: str,
) -> str:
    normalized = mode.strip().lower()
    if normalized == "full":
        typer.echo(
            "deprecated full mode is catalog-only; use --mode catalog --allow-catalog",
            err=True,
        )
        raise typer.Exit(code=1)
    if normalized == "catalog" and not allow_catalog:
        typer.echo(f"catalog {action} requires --allow-catalog", err=True)
        raise typer.Exit(code=1)
    return normalized


EXCLUDED_WEB_SOURCE_REQUIRED_FIELDS = {
    "schema_version",
    "source_id",
    "query",
    "title",
    "url",
    "source_url",
    "snippet",
    "published_at",
    "retrieved_at",
    "cutoff_at",
    "exclusion_reason",
    "time_verified",
    "available_before_cutoff",
    "content_sha256",
}


def _echo(data: object) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("expected YYYY-MM-DD") from exc


def _parse_cutoff(value: str) -> datetime:
    try:
        return parse_datetime(value)
    except ValueError as exc:
        raise typer.BadParameter(
            "expected ISO timestamp, for example 2026-07-15T08:59:59+09:00"
        ) from exc


def _exit_with_error(exc: Exception) -> NoReturn:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=1) from exc


def _full_check_steps() -> tuple[tuple[str, list[str]], ...]:
    python = sys.executable
    return (
        ("ruff", [python, "-m", "ruff", "check", "."]),
        ("mypy", [python, "-m", "mypy", "src/news_scalping_lab"]),
        ("pytest", [python, "-m", "pytest"]),
        ("audit hardcoding", [python, "-m", "news_scalping_lab.cli", "audit", "hardcoding"]),
        ("audit provenance", [python, "-m", "news_scalping_lab.cli", "audit", "provenance"]),
        (
            "audit lookahead",
            [
                python,
                "-m",
                "news_scalping_lab.cli",
                "audit",
                "lookahead",
                "--trade-date",
                "2026-06-24",
            ],
        ),
        ("warehouse rebuild", [python, "-m", "news_scalping_lab.cli", "warehouse", "rebuild"]),
        ("warehouse verify", [python, "-m", "news_scalping_lab.cli", "warehouse", "verify"]),
        ("audit coverage", [python, "-m", "news_scalping_lab.cli", "audit", "coverage"]),
        ("training export-sft", [python, "-m", "news_scalping_lab.cli", "training", "export-sft"]),
        (
            "training export-preference",
            [python, "-m", "news_scalping_lab.cli", "training", "export-preference"],
        ),
        ("training export-evals", [python, "-m", "news_scalping_lab.cli", "training", "export-evals"]),
        ("training audit", [python, "-m", "news_scalping_lab.cli", "training", "audit"]),
        ("memory audit deep", [python, "-m", "news_scalping_lab.cli", "memory", "audit", "--deep"]),
        ("brain audit deep", [python, "-m", "news_scalping_lab.cli", "brain", "audit", "--deep"]),
    )


def _run_full_check_step(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


def _demo_steps(
    *,
    news: Path,
    trade_date: str,
    cutoff: str,
    mode: str,
    web_search: bool,
) -> tuple[tuple[str, list[str]], ...]:
    python = sys.executable
    analyze_command = [
        python,
        "-m",
        "news_scalping_lab.cli",
        "analyze",
        "--news",
        news.as_posix(),
        "--trade-date",
        trade_date,
        "--cutoff",
        cutoff,
        "--mode",
        mode,
    ]
    if web_search:
        analyze_command.append("--web-search")
    return (
        ("init", [python, "-m", "news_scalping_lab.cli", "init"]),
        (
            "news inspect",
            [python, "-m", "news_scalping_lab.cli", "news", "inspect", news.as_posix()],
        ),
        (
            "brain rebuild",
            [
                python,
                "-m",
                "news_scalping_lab.cli",
                "brain",
                "rebuild",
                "--mode",
                "catalog",
                "--allow-catalog",
            ],
        ),
        ("brain audit", [python, "-m", "news_scalping_lab.cli", "brain", "audit"]),
        ("warehouse rebuild", [python, "-m", "news_scalping_lab.cli", "warehouse", "rebuild"]),
        ("warehouse verify", [python, "-m", "news_scalping_lab.cli", "warehouse", "verify"]),
        ("analyze", analyze_command),
        (
            "evaluate",
            [python, "-m", "news_scalping_lab.cli", "evaluate", "--trade-date", trade_date],
        ),
        (
            "brain update",
            [
                python,
                "-m",
                "news_scalping_lab.cli",
                "brain",
                "update",
                "--episode",
                trade_date,
                "--mode",
                "catalog",
                "--allow-catalog",
            ],
        ),
        ("warehouse rebuild after update", [python, "-m", "news_scalping_lab.cli", "warehouse", "rebuild"]),
        ("warehouse verify after update", [python, "-m", "news_scalping_lab.cli", "warehouse", "verify"]),
        ("training export-sft", [python, "-m", "news_scalping_lab.cli", "training", "export-sft"]),
        (
            "training export-preference",
            [python, "-m", "news_scalping_lab.cli", "training", "export-preference"],
        ),
        ("training export-evals", [python, "-m", "news_scalping_lab.cli", "training", "export-evals"]),
        ("training audit", [python, "-m", "news_scalping_lab.cli", "training", "audit"]),
        (
            "memory audit deep after update",
            [python, "-m", "news_scalping_lab.cli", "memory", "audit", "--deep"],
        ),
        (
            "brain audit deep after update",
            [python, "-m", "news_scalping_lab.cli", "brain", "audit", "--deep"],
        ),
    )


def _run_demo_step(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


@app.command()
def init() -> None:
    settings = load_settings()
    ensure_project_dirs(settings)
    config_files = write_default_config_files(settings)
    written = export_json_schemas(settings.path("schemas"))
    _echo(
        {
            "initialized": True,
            "configs": [path.as_posix() for path in config_files],
            "schemas": [path.as_posix() for path in written],
        }
    )


@app.command()
def doctor(
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit non-zero when readiness checks report attention.",
        ),
    ] = False,
    production: Annotated[
        bool,
        typer.Option(
            "--production",
            help="Run production readiness checks for llm-full brain usage.",
        ),
    ] = False,
) -> None:
    settings = load_settings()
    report = build_doctor_report(settings, production=production)
    if production:
        report["production_readiness"] = production_readiness_report(report, settings)
    _echo(report)
    readiness = report.get("readiness")
    production_readiness = report.get("production_readiness")
    if strict and (not isinstance(readiness, dict) or readiness.get("passed") is not True):
        raise typer.Exit(code=1)
    if production and (
        not isinstance(production_readiness, dict)
        or production_readiness.get("passed") is not True
    ):
        raise typer.Exit(code=1)


@app.command("full-check")
def full_check() -> None:
    results: list[dict[str, object]] = []
    for name, command in _full_check_steps():
        typer.echo(f"running {name}: {' '.join(command)}", err=True)
        exit_code = _run_full_check_step(command)
        results.append({"name": name, "command": command, "exit_code": exit_code})
        if exit_code != 0:
            _echo({"passed": False, "failed": name, "results": results})
            raise typer.Exit(code=exit_code)
    _echo({"passed": True, "results": results})


@app.command("demo")
def demo(
    news: Annotated[Path, typer.Option("--news")] = Path("docs/csv/news_20260624.csv"),
    trade_date: Annotated[str, typer.Option("--trade-date")] = "2026-06-24",
    cutoff: Annotated[str, typer.Option("--cutoff")] = "2026-06-24T08:59:59+09:00",
    mode: Annotated[str, typer.Option("--mode")] = "exhaustive",
    web_search: Annotated[bool, typer.Option("--web-search/--no-web-search")] = True,
) -> None:
    _parse_date(trade_date)
    _parse_cutoff(cutoff)
    results: list[dict[str, object]] = []
    for name, command in _demo_steps(
        news=news,
        trade_date=trade_date,
        cutoff=cutoff,
        mode=mode,
        web_search=web_search,
    ):
        typer.echo(f"running {name}: {' '.join(command)}", err=True)
        exit_code = _run_demo_step(command)
        results.append({"name": name, "command": command, "exit_code": exit_code})
        if exit_code != 0:
            _echo({"passed": False, "failed": name, "results": results})
            raise typer.Exit(code=exit_code)
    _echo({"passed": True, "results": results})


@app.command("ui")
def ui(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65_535)] = 8501,
    headless: Annotated[bool, typer.Option("--headless/--open-browser")] = False,
) -> None:
    config = StreamlitLaunchConfig(host=host, port=port, headless=headless)
    try:
        exit_code = run_streamlit_ui(config)
    except (StreamlitLaunchError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@news_app.command("inspect")
def news_inspect(csv_path: Path) -> None:
    try:
        batch = load_news_csv(csv_path)
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(
        {
            "path": csv_path.as_posix(),
            "sha256": batch.sha256,
            "trade_date": batch.trade_date.isoformat(),
            "row_count": batch.row_count,
            "default_news_window_start_at": default_news_window_start(
                batch.trade_date
            ).isoformat(),
            "missing_collected_at": sum(
                1 for item in batch.items if item.collected_at is None
            ),
            "first_published_at": batch.items[0].published_at.isoformat() if batch.items else None,
            "last_published_at": batch.items[-1].published_at.isoformat() if batch.items else None,
        }
    )


@news_app.command("import")
def news_import(csv_path: Path) -> None:
    settings = load_settings()
    try:
        batch = import_news_csv(csv_path, settings.path("data/raw/news"))
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(
        {
            "imported": True,
            "trade_date": batch.trade_date.isoformat(),
            "rows": batch.row_count,
            "sha256": batch.sha256,
        }
    )


@research_app.command("import")
def research_import(path: Path, mode: str = "auto") -> None:
    if not path.exists():
        typer.echo(f"research import file not found: {path}", err=True)
        raise typer.Exit(code=1)
    if not path.is_file():
        typer.echo(f"research import path is not a file: {path}", err=True)
        raise typer.Exit(code=1)
    settings = load_settings()
    try:
        episode = ResearchImporter(
            settings.project_root,
            llm=create_llm_provider(settings),
            llm_max_retries=settings.llm.max_retries,
        ).import_path(path, mode=mode)
    except (OSError, RuntimeError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(
        {
            "imported": True,
            "episode_id": episode.episode_id,
            "trade_date": episode.trade_date.isoformat(),
            "mode": mode,
            "source_path": path.as_posix(),
        }
    )


@research_app.command("inspect-bundle")
def research_inspect_bundle(path: Path) -> None:
    if not path.exists() or not path.is_file():
        typer.echo(f"research bundle file not found: {path}", err=True)
        raise typer.Exit(code=1)
    try:
        inspection = inspect_versioned_bundle(path)
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    settings = load_settings()
    write_diagnostic_report(
        settings.project_root,
        "bundle_inspection_report",
        {
            "schema_version": "nslab.bundle_inspection_diagnostics.v1",
            "status": inspection.get("inspection_status"),
            "path": relative_to_root(path, settings.project_root),
            "bundle_version": inspection.get("bundle_schema_version"),
            "manifest_schema_version": inspection.get("manifest_schema_version"),
            "episode_schema_version": inspection.get("episode_schema_version"),
            "adapter": inspection.get("adapter"),
            "supported": inspection.get("supported"),
            "forward_compatible_raw_only": inspection.get(
                "forward_compatible_raw_only"
            ),
            "episode_id": inspection.get("episode_id"),
            "trade_date": inspection.get("trade_date"),
            "raw_record_count": inspection.get("raw_record_count"),
            "normalized_record_count": inspection.get("normalized_record_count"),
            "training_eligible_record_count": inspection.get(
                "training_eligible_record_count"
            ),
            "raw_record_ids": inspection.get("raw_record_ids"),
            "normalized_record_ids": inspection.get("normalized_record_ids"),
            "raw_record_without_id_count": inspection.get(
                "raw_record_without_id_count"
            ),
            "record_id_set_comparable": inspection.get("record_id_set_comparable"),
            "record_id_set_matches_raw": inspection.get("record_id_set_matches_raw"),
            "missing_normalized_record_ids": inspection.get(
                "missing_normalized_record_ids"
            ),
            "extra_normalized_record_ids": inspection.get(
                "extra_normalized_record_ids"
            ),
            "raw_record_counts_by_type": inspection.get("raw_record_counts_by_type"),
            "record_type_counts_match_raw": inspection.get(
                "record_type_counts_match_raw"
            ),
            "raw_training_eligible_record_count": inspection.get(
                "raw_training_eligible_record_count"
            ),
            "training_eligible_count_matches_raw": inspection.get(
                "training_eligible_count_matches_raw"
            ),
            "raw_payload_hashes_match": inspection.get("raw_payload_hashes_match"),
            "raw_payload_hash_mismatch_record_ids": inspection.get(
                "raw_payload_hash_mismatch_record_ids"
            ),
            "import_loss_audit_passed": inspection.get(
                "import_loss_audit_passed"
            ),
            "dropped_record_count": inspection.get("dropped_record_count"),
            "quarantined_record_count": inspection.get("quarantined_record_count"),
            "record_counts_by_type": inspection.get("record_counts_by_type"),
            "validation_passed": inspection.get("validation_passed"),
            "record_count_matches_manifest": inspection.get(
                "record_count_matches_manifest"
            ),
            "training_eligible_count_matches_manifest": inspection.get(
                "training_eligible_count_matches_manifest"
            ),
            "hash_mismatch_count": inspection.get("hash_mismatch_count"),
            "hash_expectation_conflict_count": inspection.get(
                "hash_expectation_conflict_count"
            ),
            "missing_source_reference_count": inspection.get(
                "missing_source_reference_count"
            ),
            "missing_payload_reference_count": inspection.get(
                "missing_payload_reference_count"
            ),
            "available_from_valid": inspection.get("available_from_valid"),
            "invalid_available_from_record_count": inspection.get(
                "invalid_available_from_record_count"
            ),
            "outcome_label_quality_valid": inspection.get(
                "outcome_label_quality_valid"
            ),
            "invalid_outcome_label_quality_record_count": inspection.get(
                "invalid_outcome_label_quality_record_count"
            ),
            "typed_payload_valid": inspection.get("typed_payload_valid"),
            "invalid_typed_payload_record_count": inspection.get(
                "invalid_typed_payload_record_count"
            ),
            "validation": inspection.get("validation"),
        },
    )
    _echo(inspection)


@research_app.command("smoke-bundle")
def research_smoke_bundle(
    path: Annotated[
        Path | None,
        typer.Option(
            "--path",
            help=(
                "Explicit bundle path. When omitted, NSLAB_REAL_BUNDLE_PATH and "
                "repository candidate directories are searched."
            ),
        ),
    ] = None,
    require_valid: Annotated[
        bool,
        typer.Option(
            "--require-valid",
            help="Exit non-zero unless a production-source v11 ACCEPT_FULL smoke passes.",
        ),
    ] = False,
) -> None:
    settings = load_settings()
    report = real_bundle_smoke_report(settings, explicit_path=path)
    write_diagnostic_report(settings.project_root, "bundle_smoke_report", report)
    _echo(report)
    if require_valid and report.get("passed") is not True:
        raise typer.Exit(code=1)


@research_app.command("import-bundle")
def research_import_bundle(
    path: Path,
    validate: Annotated[bool, typer.Option("--validate/--no-validate")] = True,
    accept: Annotated[bool, typer.Option("--accept/--no-accept")] = False,
) -> None:
    if not path.exists() or not path.is_file():
        typer.echo(f"research bundle file not found: {path}", err=True)
        raise typer.Exit(code=1)
    settings = load_settings()
    try:
        result = import_versioned_bundle(
            path,
            root=settings.project_root,
            validate=validate,
            accepted=accept,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(
        {
            "imported": result.status == "imported",
            "status": result.status,
            "accepted": result.accepted,
            "adapter": result.adapter_name,
            "episode_id": result.episode_id,
            "bundle_schema_version": result.bundle_schema_version,
            "record_count": result.record_count,
            "training_eligible_record_count": result.training_eligible_record_count,
            "envelope": result.envelope_path.as_posix()
            if result.envelope_path is not None
            else None,
            "records": result.record_path.as_posix()
            if result.record_path is not None
            else None,
            "manifest": result.manifest_path.as_posix()
            if result.manifest_path is not None
            else None,
            "validation": result.validation,
        }
    )


@research_app.command("migrate-legacy")
def research_migrate_legacy() -> None:
    settings = load_settings()
    store = ResearchStore(settings.project_root)
    record_store = BrainRecordStore(settings.project_root)
    accepted_episodes = store.list_accepted()
    migrated: list[str] = []
    skipped: list[str] = []
    skipped_existing_record_count = 0
    repaired_legacy_envelope_count = 0
    record_counts_by_type: Counter[str] = Counter()
    training_eligible_record_count = 0
    for episode in accepted_episodes:
        existing_records = record_store.read_episode_records(episode.episode_id)
        source_path = settings.project_root / "research" / "accepted" / f"{episode.episode_id}.json"
        if existing_records:
            skipped.append(episode.episode_id)
            skipped_existing_record_count += len(existing_records)
            for existing_record in existing_records:
                record_counts_by_type[existing_record.record_type] += 1
                if existing_record.training_eligible:
                    training_eligible_record_count += 1
            if _repair_legacy_migration_raw_hashes(
                settings.project_root,
                episode_id=episode.episode_id,
                source_path=source_path,
            ):
                repaired_legacy_envelope_count += 1
            continue
        record = _legacy_episode_record(episode)
        raw_text = source_path.read_text(encoding="utf-8")
        envelope = _legacy_record_envelope(
            episode,
            record,
            source_sha256=file_sha256(source_path),
            raw_block_sha256=sha256_text(raw_text),
        )
        index = _legacy_normalized_index(episode, record)
        try:
            record_store.store_bundle(
                source_path=source_path,
                envelope=envelope,
                index=index,
                records=[record],
                raw_blocks={"legacy_research_episode.json": raw_text},
                validation_report={
                    "schema_version": "nslab.legacy_migration_report.v1",
                    "passed": True,
                    "catalog_only": True,
                },
            )
        except (OSError, ValueError) as exc:
            _exit_with_error(exc)
        migrated.append(episode.episode_id)
        record_counts_by_type[record.record_type] += 1
        if record.training_eligible:
            training_eligible_record_count += 1
    migrated_record_count = len(migrated)
    normalized_record_count = migrated_record_count + skipped_existing_record_count
    report_payload = {
        "schema_version": "nslab.legacy_migration_report.v1",
        "passed": True,
        "findings": [],
        "status": "catalog_only_legacy_migration",
        "catalog_only": True,
        "source_episode_count": len(accepted_episodes),
        "legacy_source_record_count": len(accepted_episodes),
        "raw_record_count": len(accepted_episodes),
        "normalized_record_count": normalized_record_count,
        "migrated_record_count": migrated_record_count,
        "skipped_existing_record_count": skipped_existing_record_count,
        "repaired_legacy_envelope_count": repaired_legacy_envelope_count,
        "training_eligible_record_count": training_eligible_record_count,
        "ineligible_record_count": normalized_record_count - training_eligible_record_count,
        "dropped_record_count": 0,
        "quarantined_record_count": 0,
        "record_counts_by_type": dict(sorted(record_counts_by_type.items())),
        "migrated_episode_count": len(migrated),
        "skipped_episode_count": len(skipped),
        "migrated_episode_ids": migrated,
        "skipped_episode_ids": skipped,
    }
    write_diagnostic_report(
        settings.project_root,
        "migration_report",
        report_payload,
    )
    _echo({"migrated_episode_ids": migrated, "skipped_episode_ids": skipped})


def _repair_legacy_migration_raw_hashes(
    root: Path,
    *,
    episode_id: str,
    source_path: Path,
) -> bool:
    envelope_path = root / "research" / "episodes" / episode_id / "bundle_envelope.json"
    raw_block_path = (
        root
        / "research"
        / "episodes"
        / episode_id
        / "raw_blocks"
        / "legacy_research_episode.json"
    )
    if not envelope_path.exists() or not raw_block_path.exists() or not source_path.exists():
        return False
    try:
        envelope = read_json(envelope_path)
    except (OSError, ValueError):
        return False
    if not isinstance(envelope, dict) or envelope.get("adapter_name") != "legacy-migration":
        return False
    raw_block_hashes = envelope.get("raw_block_hashes")
    if not isinstance(raw_block_hashes, dict):
        raw_block_hashes = {}
    expected_bundle_hash = file_sha256(source_path)
    expected_block_hash = sha256_text(raw_block_path.read_text(encoding="utf-8"))
    changed = False
    if envelope.get("raw_bundle_sha256") != expected_bundle_hash:
        envelope["raw_bundle_sha256"] = expected_bundle_hash
        changed = True
    if raw_block_hashes.get("legacy_research_episode.json") != expected_block_hash:
        raw_block_hashes["legacy_research_episode.json"] = expected_block_hash
        envelope["raw_block_hashes"] = raw_block_hashes
        changed = True
    if changed:
        write_json(envelope_path, envelope)
    return changed


@research_app.command("import-batch")
def research_import_batch(
    directory: Path,
    mode: str = "auto",
    accept: Annotated[bool, typer.Option("--accept/--no-accept")] = True,
) -> None:
    settings = load_settings()
    if not directory.exists():
        typer.echo(f"research import-batch directory not found: {directory}", err=True)
        raise typer.Exit(code=1)
    if not directory.is_dir():
        typer.echo(f"research import-batch path is not a directory: {directory}", err=True)
        raise typer.Exit(code=1)
    try:
        importer = ResearchImporter(
            settings.project_root,
            llm=create_llm_provider(settings),
            llm_max_retries=settings.llm.max_retries,
        )
    except (RuntimeError, ValueError) as exc:
        _exit_with_error(exc)
    store = ResearchStore(settings.project_root)
    imported: list[str] = []
    accepted: list[str] = []
    source_files: list[str] = []
    skipped_paths: list[str] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            skipped_paths.append(path.as_posix())
            continue
        try:
            episode = importer.import_path(path, mode=mode)
        except (OSError, RuntimeError, ValueError) as exc:
            _exit_with_error(RuntimeError(f"research import-batch failed for {path}: {exc}"))
        imported.append(episode.episode_id)
        source_files.append(path.as_posix())
        if accept:
            try:
                store.accept(episode.episode_id)
            except (FileNotFoundError, OSError) as exc:
                _exit_with_error(RuntimeError(f"research accept failed for {path}: {exc}"))
            accepted.append(episode.episode_id)
    _echo(
        {
            "imported_episode_ids": imported,
            "accepted_episode_ids": accepted,
            "imported_count": len(imported),
            "accepted_count": len(accepted),
            "source_files": source_files,
            "skipped_paths": skipped_paths,
        }
    )


@research_app.command("validate")
def research_validate(episode_id: str) -> None:
    settings = load_settings()
    try:
        episode = ResearchStore(settings.project_root).get_episode(episode_id)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    _echo(
        {"valid": True, "episode_id": episode.episode_id, "schema_version": episode.schema_version}
    )


@research_app.command("accept")
def research_accept(episode_id: str) -> None:
    settings = load_settings()
    try:
        path = ResearchStore(settings.project_root).accept(episode_id)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    _echo({"accepted": episode_id, "path": relative_to_root(path, settings.project_root)})


@research_app.command("reject")
def research_reject(episode_id: str) -> None:
    settings = load_settings()
    try:
        path = ResearchStore(settings.project_root).reject(episode_id)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    _echo({"rejected": episode_id, "path": relative_to_root(path, settings.project_root)})


@brain_app.command("rebuild")
def brain_rebuild(
    mode: Annotated[str, typer.Option("--mode")] = "llm-full",
    allow_catalog: Annotated[bool, typer.Option("--allow-catalog")] = False,
) -> None:
    mode = _validated_brain_cli_mode(
        mode,
        allow_catalog=allow_catalog,
        action="rebuild",
    )
    settings = load_settings()
    try:
        manifest = BrainCompiler(
            settings.project_root,
            shard_episode_count=settings.limits.shard_episode_count,
        ).rebuild(mode=mode)
    except (FileNotFoundError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(manifest.model_dump(mode="json"))


@brain_app.command("update")
def brain_update(
    episode: Annotated[str, typer.Option("--episode")],
    mode: Annotated[str, typer.Option("--mode")] = "llm-full",
    allow_catalog: Annotated[bool, typer.Option("--allow-catalog")] = False,
) -> None:
    mode = _validated_brain_cli_mode(
        mode,
        allow_catalog=allow_catalog,
        action="update",
    )
    settings = load_settings()
    try:
        manifest = BrainCompiler(
            settings.project_root,
            shard_episode_count=settings.limits.shard_episode_count,
        ).update(episode_id=episode, mode=mode)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    _echo(manifest.model_dump(mode="json"))


@brain_app.command("audit")
def brain_audit(
    deep: Annotated[bool, typer.Option("--deep")] = False,
) -> None:
    settings = load_settings()
    result = audit_brain(settings.project_root, deep=deep)
    _echo(result)
    if not result.get("passed", False):
        raise typer.Exit(code=1)


@brain_app.command("diff")
def brain_diff(version_a: str, version_b: str) -> None:
    settings = load_settings()
    try:
        diff = build_brain_diff(settings.project_root, version_a, version_b)
        markdown_path = write_brain_diff_markdown(settings.project_root, diff)
    except (FileNotFoundError, ValueError) as exc:
        _exit_with_error(exc)
    _echo({**diff, "markdown_path": markdown_path.as_posix()})


@app.command()
def analyze(
    news: Annotated[Path, typer.Option("--news")],
    trade_date: Annotated[str, typer.Option("--trade-date")],
    cutoff: Annotated[str, typer.Option("--cutoff")],
    mode: Annotated[str | None, typer.Option("--mode")] = None,
    web_search: Annotated[bool, typer.Option("--web-search")] = False,
) -> None:
    settings = load_settings()
    analysis_mode = mode if mode is not None else settings.default_mode
    parsed_trade_date = _parse_date(trade_date)
    parsed_cutoff = _parse_cutoff(cutoff)
    try:
        analysis = asyncio.run(
            DailyAnalyzer(settings).analyze(
                news_csv=news,
                trade_date=parsed_trade_date,
                cutoff_at=parsed_cutoff,
                mode=analysis_mode,
                web_search=web_search,
            )
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(analysis.model_dump(mode="json"))


@app.command()
def evaluate(trade_date: Annotated[str, typer.Option("--trade-date")]) -> None:
    settings = load_settings()
    parsed_trade_date = _parse_date(trade_date)
    try:
        result = Evaluator(settings.project_root).evaluate(trade_date=parsed_trade_date)
        postmortem = read_json(result.report_path)
        if not isinstance(postmortem, dict):
            raise ValueError("postmortem report must be a JSON object")
    except (FileNotFoundError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(
        {
            "postmortem": result.report_path.as_posix(),
            "research_episode_id": result.episode_id,
            "research_episode_path": result.episode_path.as_posix(),
            "outcome_coverage_status": postmortem.get("outcome_coverage_status"),
            "performance_metrics": postmortem.get("performance_metrics"),
            "eligibility_matrix": postmortem.get("eligibility_matrix"),
        }
    )


@context_app.command("inspect")
def context_inspect(
    run_id: str,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit non-zero when reproducibility checks fail.",
        ),
    ] = False,
) -> None:
    settings = load_settings()
    path = settings.path("runs/manifests") / f"{run_id}.json"
    try:
        if not path.exists():
            raise FileNotFoundError(f"context manifest not found: {path}")
        manifest = read_json(path)
        if not isinstance(manifest, dict):
            raise ValueError("context manifest must be a JSON object")
        inspection = _inspect_context_manifest(settings.project_root, path, manifest)
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    _echo({**manifest, "inspection": inspection})
    if strict and not inspection["reproducibility_checks_passed"]:
        raise typer.Exit(code=1)


def _inspect_context_manifest(
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    prediction = _inspect_prediction_artifact(root, manifest)
    report = _inspect_report_artifact(root, manifest)
    news_input = _inspect_news_input(root, manifest)
    brain_files = _inspect_context_file_group(
        root,
        manifest,
        files_field="brain_files",
        hashes_field="brain_file_hashes",
    )
    shard_brain_files = _inspect_context_file_group(
        root,
        manifest,
        files_field="shard_brain_files",
        hashes_field="shard_brain_file_hashes",
    )
    supporting_artifacts = _inspect_supporting_artifacts(root, manifest)
    memory_sweep = _inspect_memory_sweep_artifacts(root, manifest)
    record_sweep = _inspect_record_sweep_artifacts(root, manifest)
    llm_traces = _inspect_llm_traces(root, manifest)
    manifest_reproducibility = _inspect_manifest_reproducibility_fields(root, manifest)
    return {
        "context_manifest": {
            "path": _display_path(root, manifest_path),
            "exists": manifest_path.exists(),
            "sha256": file_sha256(manifest_path) if manifest_path.exists() else None,
        },
        "manifest_reproducibility": manifest_reproducibility,
        "news_input": news_input,
        "context_files": {
            "brain": brain_files,
            "shard_brain": shard_brain_files,
        },
        "output_artifacts": {
            "prediction": prediction,
            "report": report,
        },
        "supporting_artifacts": supporting_artifacts,
        "memory_sweep": memory_sweep,
        "record_sweep": record_sweep,
        "llm_traces": llm_traces,
        "reproducibility_checks_passed": _prediction_artifact_status_passed(
            prediction
        )
        and _artifact_status_passed(report, required_extra_key="contains_run_id")
        and _news_input_status_passed(news_input)
        and _context_file_group_status_passed(brain_files)
        and _context_file_group_status_passed(shard_brain_files)
        and _supporting_artifacts_status_passed(supporting_artifacts)
        and _memory_sweep_status_passed(memory_sweep)
        and _record_sweep_status_passed(record_sweep)
        and _llm_trace_status_passed(llm_traces)
        and _manifest_reproducibility_status_passed(manifest_reproducibility),
    }


def _inspect_manifest_reproducibility_fields(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    episode_scope = inspect_manifest_episode_scope(root, manifest)
    price_snapshot = _inspect_price_snapshot_contract(manifest)
    status: dict[str, Any] = {
        "schema_version": manifest.get("schema_version"),
        "configured": manifest.get("schema_version") == "nslab.context_manifest.v1",
        "model_config_valid": False,
        "token_counts_valid": False,
        "truncations_valid": False,
        "web_queries_valid": False,
        "web_sources_valid": False,
        "episode_scope_valid": bool(episode_scope.get("passed")),
        "price_snapshot_valid": bool(price_snapshot.get("passed")),
        "episode_scope": episode_scope,
        "price_snapshot": price_snapshot,
        "errors": [],
    }
    if not status["configured"]:
        status["errors"].append("context_manifest_schema_version_missing_or_invalid")
        return status
    model_config = manifest.get("model_config")
    status["model_config_valid"] = isinstance(model_config, dict) and bool(model_config)
    if not status["model_config_valid"]:
        status["errors"].append("model_config_missing_or_invalid")
    token_counts = manifest.get("token_counts")
    status["token_counts_valid"] = _token_counts_valid(token_counts)
    if not status["token_counts_valid"]:
        status["errors"].append("token_counts_missing_or_invalid")
    for field in ("truncations", "web_queries", "web_sources"):
        valid = _string_list_field_valid(manifest.get(field))
        status[f"{field}_valid"] = valid
        if not valid:
            status["errors"].append(f"{field}_missing_or_invalid")
    if not status["episode_scope_valid"]:
        status["errors"].append("episode_scope_invalid")
    if not status["price_snapshot_valid"]:
        status["errors"].append("price_snapshot_invalid")
    return status


def _inspect_price_snapshot_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    raw_snapshot = manifest.get("price_snapshot")
    trade_date = _manifest_date(manifest.get("trade_date"))
    cutoff_at = _manifest_datetime(manifest.get("cutoff_at"))
    status: dict[str, Any] = {
        "configured": isinstance(raw_snapshot, dict),
        "source_name_valid": False,
        "allowed_through": None,
        "allowed_through_present": False,
        "allowed_through_valid": False,
        "allowed_through_before_trade_date": False,
        "as_of": None,
        "as_of_valid": True,
        "as_of_not_after_cutoff": True,
        "passed": False,
        "errors": [],
    }
    if not isinstance(raw_snapshot, dict):
        status["errors"].append("price_snapshot_missing_or_invalid")
        return status

    source_name = raw_snapshot.get("source_name")
    status["source_name_valid"] = isinstance(source_name, str) and bool(
        source_name.strip()
    )
    if not status["source_name_valid"]:
        status["errors"].append("price_snapshot_source_name_missing_or_invalid")

    raw_allowed = raw_snapshot.get("allowed_through")
    if isinstance(raw_allowed, str):
        status["allowed_through"] = raw_allowed
    if not isinstance(raw_allowed, str) or not raw_allowed:
        status["errors"].append("price_snapshot_allowed_through_missing_or_invalid")
    else:
        status["allowed_through_present"] = True
        try:
            allowed_through = date.fromisoformat(raw_allowed)
        except ValueError:
            status["errors"].append("price_snapshot_allowed_through_invalid_date")
        else:
            status["allowed_through_valid"] = True
            if trade_date is None:
                status["errors"].append("trade_date_missing_or_invalid")
            else:
                status["allowed_through_before_trade_date"] = (
                    allowed_through < trade_date
                )
                if not status["allowed_through_before_trade_date"]:
                    status["errors"].append(
                        "price_snapshot_allowed_through_not_before_trade_date"
                    )

    raw_as_of = raw_snapshot.get("as_of")
    if raw_as_of is not None:
        if isinstance(raw_as_of, str):
            status["as_of"] = raw_as_of
        else:
            status["as_of_valid"] = False
            status["as_of_not_after_cutoff"] = False
            status["errors"].append("price_snapshot_as_of_missing_or_invalid")
            return status
        try:
            snapshot_as_of = parse_datetime(raw_as_of)
        except ValueError:
            status["as_of_valid"] = False
            status["as_of_not_after_cutoff"] = False
            status["errors"].append("price_snapshot_as_of_invalid_datetime")
            return status
        if cutoff_at is None:
            status["as_of_not_after_cutoff"] = False
            status["errors"].append("cutoff_at_missing_or_invalid")
        else:
            status["as_of_not_after_cutoff"] = snapshot_as_of <= cutoff_at
            if not status["as_of_not_after_cutoff"]:
                status["errors"].append("price_snapshot_as_of_after_cutoff_at")

    status["passed"] = bool(
        status["configured"]
        and status["source_name_valid"]
        and status["allowed_through_valid"]
        and status["allowed_through_before_trade_date"]
        and status["as_of_valid"]
        and status["as_of_not_after_cutoff"]
        and not status["errors"]
    )
    return status


def _inspect_supporting_artifacts(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    specs = (
        ("row_disposition", "row_disposition_artifact", "row_disposition_sha256", True),
        ("event_cluster", "event_cluster_artifact", "event_cluster_sha256", True),
        (
            "open_world_first_analysis",
            "open_world_first_analysis_artifact",
            "open_world_first_analysis_sha256",
            False,
        ),
        (
            "news_novelty_review",
            "news_novelty_review_artifact",
            "news_novelty_review_sha256",
            True,
        ),
        (
            "semantic_retrieval_plan",
            "semantic_retrieval_plan_artifact",
            "semantic_retrieval_plan_sha256",
            True,
        ),
        (
            "semantic_retrieval",
            "semantic_retrieval_artifact",
            "semantic_retrieval_sha256",
            True,
        ),
        (
            "candidate_expansion",
            "candidate_expansion_artifact",
            "candidate_expansion_sha256",
            True,
        ),
        ("source_ledger", "source_ledger_artifact", "source_ledger_sha256", True),
        (
            "blind_seal_receipt",
            "blind_seal_receipt_artifact",
            "blind_seal_receipt_sha256",
            True,
        ),
        ("phase_state", "phase_state_artifact", "phase_state_sha256", True),
        ("web_source", "web_source_artifact", "web_source_sha256", False),
        (
            "excluded_web_source",
            "excluded_web_source_artifact",
            "excluded_web_source_sha256",
            False,
        ),
        (
            "candidate_web_check",
            "candidate_web_check_artifact",
            "candidate_web_check_sha256",
            False,
        ),
        (
            "candidate_verification",
            "candidate_verification_artifact",
            "candidate_verification_sha256",
            False,
        ),
        (
            "final_synthesis_context",
            "final_synthesis_context_artifact",
            "final_synthesis_context_sha256",
            True,
        ),
        (
            "excluded_candidate_web_check",
            "excluded_candidate_web_check_artifact",
            "excluded_candidate_web_check_sha256",
            False,
        ),
    )
    statuses = {
        label: _inspect_text_hashed_artifact(
            root,
            manifest,
            artifact_field=artifact_field,
            hash_field=hash_field,
            required=required,
        )
        for label, artifact_field, hash_field, required in specs
    }
    statuses["row_disposition"] = _inspect_row_disposition_artifact(root, manifest)
    statuses["event_cluster"] = _inspect_event_cluster_artifact(root, manifest)
    statuses["open_world_first_analysis"] = (
        _inspect_open_world_first_analysis_artifact(root, manifest)
    )
    statuses["news_novelty_review"] = _inspect_news_novelty_review_artifact(
        root, manifest
    )
    statuses["semantic_retrieval_plan"] = _inspect_semantic_retrieval_plan_artifact(
        root, manifest
    )
    statuses["semantic_retrieval"] = _inspect_semantic_retrieval_artifact(root, manifest)
    statuses["candidate_expansion"] = _inspect_candidate_expansion_artifact(
        root, manifest
    )
    statuses["source_ledger"] = _inspect_source_ledger_artifact(root, manifest)
    statuses["web_source"] = _inspect_web_source_artifact(root, manifest)
    statuses["excluded_web_source"] = _inspect_excluded_web_source_artifact(
        root, manifest
    )
    statuses["candidate_web_check"] = _inspect_candidate_web_check_artifact(
        root, manifest
    )
    statuses["excluded_candidate_web_check"] = (
        _inspect_excluded_candidate_web_check_artifact(root, manifest)
    )
    statuses["candidate_verification"] = _inspect_candidate_verification_artifact(
        root, manifest
    )
    statuses["final_synthesis_context"] = _inspect_final_synthesis_context_artifact(
        root, manifest
    )
    statuses["blind_seal_receipt"] = _inspect_blind_seal_receipt_artifact(
        root, manifest
    )
    statuses["phase_state"] = _inspect_phase_state_artifact(root, manifest)
    statuses["red_team"] = _inspect_red_team_artifacts(root, manifest)
    return statuses


def _inspect_text_hashed_artifact(
    root: Path,
    manifest: dict[str, Any],
    *,
    artifact_field: str,
    hash_field: str,
    required: bool,
) -> dict[str, Any]:
    status = _base_artifact_status(root, manifest.get(artifact_field))
    expected_hash = manifest.get(hash_field)
    status["artifact_field"] = artifact_field
    status["hash_field"] = hash_field
    status["required"] = required
    status["expected_sha256"] = expected_hash if isinstance(expected_hash, str) else None
    if not status["configured"]:
        if required:
            status["errors"].append(f"{artifact_field}_missing")
            status["passed"] = False
        else:
            status["passed"] = True
        return status
    artifact_path = status.pop("_artifact_path", None)
    if not isinstance(artifact_path, Path) or not status["exists"]:
        status["passed"] = False
        return status
    observed_hash = sha256_text(artifact_path.read_text(encoding="utf-8", errors="replace"))
    status["observed_sha256"] = observed_hash
    status["hash_verified"] = observed_hash == expected_hash
    if not isinstance(expected_hash, str) or not expected_hash:
        status["errors"].append(f"{hash_field}_missing")
    status["passed"] = _text_hashed_artifact_status_passed(status)
    return status


def _inspect_row_disposition_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="row_disposition_artifact",
        hash_field="row_disposition_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "row_count_verified": None,
            "summary_verified": None,
            "coverage_ratio_verified": None,
            "duplicate_row_numbers_absent": None,
            "raw_content_absent_verified": None,
            "news_window_contract_verified": None,
        }
    )
    rows = _read_artifact_jsonl_rows(
        root,
        manifest.get("row_disposition_artifact"),
        status,
        label="row_disposition",
    )
    if rows is None:
        status["passed"] = _row_disposition_status_passed(status)
        return status

    run_id = manifest.get("run_id")
    status["schema_version_verified"] = all(
        row.get("schema_version") == "nslab.row_disposition.v1" for row in rows
    )
    if not status["schema_version_verified"]:
        status["errors"].append("row_disposition_schema_version_mismatch")
    status["run_id_verified"] = not isinstance(run_id, str) or all(
        row.get("run_id") == run_id for row in rows
    )
    if not status["run_id_verified"]:
        status["errors"].append("row_disposition_run_id_mismatch")

    expected_total = _optional_int(manifest.get("news_row_count"))
    status["row_count_verified"] = expected_total is None or len(rows) == expected_total
    if not status["row_count_verified"]:
        status["errors"].append("row_disposition_count_mismatch")

    expected_summary = _row_disposition_summary_from_rows(rows)
    manifest_summary = manifest.get("row_disposition_summary")
    status["summary_verified"] = manifest_summary == expected_summary
    if not status["summary_verified"]:
        status["errors"].append("row_disposition_summary_mismatch")

    manifest_ratio = manifest.get("row_disposition_coverage_ratio")
    summary_ratio = (
        manifest_summary.get("coverage_ratio")
        if isinstance(manifest_summary, dict)
        else None
    )
    status["coverage_ratio_verified"] = (
        isinstance(manifest_ratio, int | float)
        and not isinstance(manifest_ratio, bool)
        and float(manifest_ratio) == 1.0
        and isinstance(summary_ratio, int | float)
        and not isinstance(summary_ratio, bool)
        and float(summary_ratio) == 1.0
    )
    if not status["coverage_ratio_verified"]:
        status["errors"].append("row_disposition_coverage_ratio_mismatch")

    row_numbers = [
        row.get("row_number")
        for row in rows
        if isinstance(row.get("row_number"), int)
        and not isinstance(row.get("row_number"), bool)
    ]
    status["duplicate_row_numbers_absent"] = len(row_numbers) == len(set(row_numbers))
    if not status["duplicate_row_numbers_absent"]:
        status["errors"].append("row_disposition_duplicate_row_number")

    status["raw_content_absent_verified"] = all(
        "title" not in row and "body" not in row for row in rows
    )
    if not status["raw_content_absent_verified"]:
        status["errors"].append("row_disposition_raw_content_present")

    news_window_start_at = _manifest_datetime(manifest.get("news_window_start_at"))
    cutoff_at = _manifest_datetime(manifest.get("cutoff_at"))
    status["news_window_contract_verified"] = (
        news_window_start_at is not None
        and cutoff_at is not None
        and all(
            _row_disposition_news_window_contract_matches(
                row,
                news_window_start_at,
                cutoff_at,
            )
            for row in rows
        )
    )
    if not status["news_window_contract_verified"]:
        status["errors"].append("row_disposition_news_window_contract_mismatch")

    status["passed"] = _row_disposition_status_passed(status)
    return status


def _inspect_event_cluster_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="event_cluster_artifact",
        hash_field="event_cluster_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "row_count_verified": None,
            "summary_cluster_count_verified": None,
            "summary_source_row_count_verified": None,
            "summary_exact_duplicate_count_verified": None,
            "summary_exact_duplicate_cluster_count_verified": None,
            "summary_semantic_duplicate_cluster_count_verified": None,
            "summary_cluster_method_verified": None,
            "summary_novelty_review_required_verified": None,
            "row_membership_counts_verified": None,
        }
    )
    rows = _read_artifact_jsonl_rows(
        root,
        manifest.get("event_cluster_artifact"),
        status,
        label="event_cluster",
    )
    if rows is None:
        status["passed"] = _event_cluster_status_passed(status)
        return status

    run_id = manifest.get("run_id")
    status["schema_version_verified"] = all(
        row.get("schema_version") == "nslab.news_event_cluster.v1" for row in rows
    )
    if not status["schema_version_verified"]:
        status["errors"].append("event_cluster_schema_version_mismatch")
    status["run_id_verified"] = not isinstance(run_id, str) or all(
        row.get("run_id") == run_id for row in rows
    )
    if not status["run_id_verified"]:
        status["errors"].append("event_cluster_run_id_mismatch")

    expected_count = manifest.get("event_cluster_count")
    status["row_count_verified"] = not isinstance(expected_count, int) or len(
        rows
    ) == expected_count
    if not status["row_count_verified"]:
        status["errors"].append("event_cluster_count_mismatch")

    summary = manifest.get("event_cluster_summary")
    if not isinstance(summary, dict):
        status["errors"].append("event_cluster_summary_invalid")
        status["passed"] = _event_cluster_status_passed(status)
        return status

    source_row_count = sum(_non_bool_int(row.get("row_count")) or 0 for row in rows)
    exact_duplicate_count = sum(
        _non_bool_int(row.get("exact_duplicate_count")) or 0 for row in rows
    )
    exact_duplicate_cluster_count = sum(
        1 for row in rows if (_non_bool_int(row.get("exact_duplicate_count")) or 0) > 0
    )
    status["summary_cluster_count_verified"] = (
        _non_bool_int(summary.get("cluster_count")) == len(rows)
    )
    if not status["summary_cluster_count_verified"]:
        status["errors"].append("event_cluster_summary_cluster_count_mismatch")
    status["summary_source_row_count_verified"] = (
        _non_bool_int(summary.get("source_row_count")) == source_row_count
    )
    if not status["summary_source_row_count_verified"]:
        status["errors"].append("event_cluster_summary_source_row_count_mismatch")
    status["summary_exact_duplicate_count_verified"] = (
        _non_bool_int(summary.get("exact_duplicate_count")) == exact_duplicate_count
    )
    if not status["summary_exact_duplicate_count_verified"]:
        status["errors"].append("event_cluster_summary_exact_duplicate_count_mismatch")
    status["summary_exact_duplicate_cluster_count_verified"] = (
        _non_bool_int(summary.get("exact_duplicate_cluster_count"))
        == exact_duplicate_cluster_count
    )
    if not status["summary_exact_duplicate_cluster_count_verified"]:
        status["errors"].append(
            "event_cluster_summary_exact_duplicate_cluster_count_mismatch"
        )
    status["summary_semantic_duplicate_cluster_count_verified"] = (
        _non_bool_int(summary.get("semantic_duplicate_cluster_count")) == 0
    )
    if not status["summary_semantic_duplicate_cluster_count_verified"]:
        status["errors"].append(
            "event_cluster_summary_semantic_duplicate_cluster_count_mismatch"
        )
    methods = {
        method
        for row in rows
        if isinstance(method := row.get("cluster_method"), str) and method
    }
    status["summary_cluster_method_verified"] = (
        isinstance(summary.get("cluster_method"), str)
        and bool(methods)
        and methods == {summary.get("cluster_method")}
    )
    if not status["summary_cluster_method_verified"]:
        status["errors"].append("event_cluster_summary_cluster_method_mismatch")
    status["summary_novelty_review_required_verified"] = (
        summary.get("novelty_review_required") is True
    )
    if not status["summary_novelty_review_required_verified"]:
        status["errors"].append("event_cluster_summary_novelty_review_required_mismatch")
    status["row_membership_counts_verified"] = all(
        _event_cluster_membership_counts_match(row) for row in rows
    )
    if not status["row_membership_counts_verified"]:
        status["errors"].append("event_cluster_row_membership_counts_mismatch")

    status["passed"] = _event_cluster_status_passed(status)
    return status


def _inspect_open_world_first_analysis_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="open_world_first_analysis_artifact",
        hash_field="open_world_first_analysis_sha256",
        required=False,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "prompt_hash_verified": None,
            "required_fields_present": None,
            "summary_verified": None,
        }
    )
    payload = _read_artifact_object(
        root,
        manifest.get("open_world_first_analysis_artifact"),
        status,
    )
    if payload is None:
        if not status.get("configured"):
            status["passed"] = True
        else:
            status["passed"] = _open_world_first_analysis_status_passed(status)
        return status

    status["schema_version_verified"] = (
        payload.get("schema_version") == "nslab.open_world_first_analysis.v1"
    )
    if not status["schema_version_verified"]:
        status["errors"].append("open_world_first_analysis_schema_version_mismatch")

    run_id = manifest.get("run_id")
    status["run_id_verified"] = not isinstance(run_id, str) or payload.get("run_id") == run_id
    if not status["run_id_verified"]:
        status["errors"].append("open_world_first_analysis_run_id_mismatch")

    prompt_hash = _manifest_prompt_hash(manifest, "open_world_first_analysis")
    status["prompt_hash_verified"] = (
        prompt_hash is None or payload.get("prompt_sha256") == prompt_hash
    )
    if not status["prompt_hash_verified"]:
        status["errors"].append("open_world_first_analysis_prompt_hash_mismatch")

    required_fields = [
        "event_clusters",
        "direct_company_events",
        "policy_industry_events",
        "mechanisms",
        "beneficiary_transmission_paths",
        "narrative_conversion_points",
        "direct_candidates",
        "potential_sectors",
        "beneficiary_investigation_questions",
        "uncertainties",
    ]
    status["required_fields_present"] = all(
        _string_list(payload.get(field)) for field in required_fields
    )
    if not status["required_fields_present"]:
        status["errors"].append("open_world_first_analysis_required_fields_missing")

    expected_summary = {
        "event_cluster_count": len(_string_list(payload.get("event_clusters"))),
        "direct_company_event_count": len(
            _string_list(payload.get("direct_company_events"))
        ),
        "policy_industry_event_count": len(
            _string_list(payload.get("policy_industry_events"))
        ),
        "mechanism_count": len(_string_list(payload.get("mechanisms"))),
        "transmission_path_count": len(
            _string_list(payload.get("beneficiary_transmission_paths"))
        ),
        "narrative_conversion_point_count": len(
            _string_list(payload.get("narrative_conversion_points"))
        ),
        "direct_candidate_count": len(_string_list(payload.get("direct_candidates"))),
        "potential_sector_count": len(_string_list(payload.get("potential_sectors"))),
        "investigation_question_count": len(
            _string_list(payload.get("beneficiary_investigation_questions"))
        ),
        "uncertainty_count": len(_string_list(payload.get("uncertainties"))),
    }
    status["summary_verified"] = (
        manifest.get("open_world_first_analysis_summary") == expected_summary
    )
    if not status["summary_verified"]:
        status["errors"].append("open_world_first_analysis_summary_mismatch")

    status["passed"] = _open_world_first_analysis_status_passed(status)
    return status


def _inspect_news_novelty_review_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="news_novelty_review_artifact",
        hash_field="news_novelty_review_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "prompt_hash_verified": None,
            "manifest_count_verified": None,
            "payload_cluster_count_verified": None,
            "payload_reviewed_cluster_count_verified": None,
            "summary_cluster_count_verified": None,
            "summary_reviewed_cluster_count_verified": None,
            "summary_review_mode_verified": None,
            "summary_novelty_counts_verified": None,
            "summary_time_verified_count_verified": None,
            "summary_excluded_after_cutoff_source_count_verified": None,
        }
    )
    payload = _read_artifact_object(
        root,
        manifest.get("news_novelty_review_artifact"),
        status,
    )
    if payload is None:
        status["passed"] = _news_novelty_review_status_passed(status)
        return status

    status["schema_version_verified"] = (
        payload.get("schema_version") == "nslab.news_novelty_review.v1"
    )
    if not status["schema_version_verified"]:
        status["errors"].append("news_novelty_review_schema_version_mismatch")
    run_id = manifest.get("run_id")
    status["run_id_verified"] = not isinstance(run_id, str) or payload.get("run_id") == run_id
    if not status["run_id_verified"]:
        status["errors"].append("news_novelty_review_run_id_mismatch")
    prompt_hash = _manifest_prompt_hash(manifest, "news_novelty_review")
    status["prompt_hash_verified"] = (
        not isinstance(prompt_hash, str) or payload.get("prompt_sha256") == prompt_hash
    )
    if not status["prompt_hash_verified"]:
        status["errors"].append("news_novelty_review_prompt_hash_mismatch")

    findings = payload.get("findings")
    if not isinstance(findings, list) or not all(
        isinstance(finding, dict) for finding in findings
    ):
        status["errors"].append("news_novelty_review_findings_invalid")
        status["passed"] = _news_novelty_review_status_passed(status)
        return status

    expected_count = manifest.get("news_novelty_review_count")
    status["manifest_count_verified"] = not isinstance(expected_count, int) or len(
        findings
    ) == expected_count
    if not status["manifest_count_verified"]:
        status["errors"].append("news_novelty_review_count_mismatch")
    event_cluster_count = manifest.get("event_cluster_count")
    status["payload_cluster_count_verified"] = (
        not isinstance(event_cluster_count, int)
        or payload.get("cluster_count") == event_cluster_count
    )
    if not status["payload_cluster_count_verified"]:
        status["errors"].append("news_novelty_review_cluster_count_mismatch")
    status["payload_reviewed_cluster_count_verified"] = (
        payload.get("reviewed_cluster_count") == len(findings)
    )
    if not status["payload_reviewed_cluster_count_verified"]:
        status["errors"].append("news_novelty_review_reviewed_cluster_count_mismatch")

    summary = manifest.get("news_novelty_review_summary")
    if not isinstance(summary, dict):
        status["errors"].append("news_novelty_review_summary_invalid")
        status["passed"] = _news_novelty_review_status_passed(status)
        return status

    time_verified_count = sum(1 for finding in findings if finding.get("time_verified") is True)
    excluded_ids = _string_list(payload.get("excluded_after_cutoff_source_ids"))
    novelty_counts = _news_novelty_counts(findings, summary.get("novelty_counts"))
    status["summary_cluster_count_verified"] = (
        _non_bool_int(summary.get("cluster_count"))
        == _non_bool_int(payload.get("cluster_count"))
    )
    if not status["summary_cluster_count_verified"]:
        status["errors"].append("news_novelty_review_summary_cluster_count_mismatch")
    status["summary_reviewed_cluster_count_verified"] = (
        _non_bool_int(summary.get("reviewed_cluster_count")) == len(findings)
    )
    if not status["summary_reviewed_cluster_count_verified"]:
        status["errors"].append(
            "news_novelty_review_summary_reviewed_cluster_count_mismatch"
        )
    status["summary_review_mode_verified"] = (
        isinstance(summary.get("review_mode"), str)
        and summary.get("review_mode") == payload.get("review_mode")
    )
    if not status["summary_review_mode_verified"]:
        status["errors"].append("news_novelty_review_summary_review_mode_mismatch")
    status["summary_novelty_counts_verified"] = (
        summary.get("novelty_counts") == novelty_counts
    )
    if not status["summary_novelty_counts_verified"]:
        status["errors"].append("news_novelty_review_summary_novelty_counts_mismatch")
    status["summary_time_verified_count_verified"] = (
        _non_bool_int(summary.get("time_verified_count")) == time_verified_count
    )
    if not status["summary_time_verified_count_verified"]:
        status["errors"].append(
            "news_novelty_review_summary_time_verified_count_mismatch"
        )
    status["summary_excluded_after_cutoff_source_count_verified"] = (
        _non_bool_int(summary.get("excluded_after_cutoff_source_count"))
        == len(excluded_ids)
    )
    if not status["summary_excluded_after_cutoff_source_count_verified"]:
        status["errors"].append(
            "news_novelty_review_summary_excluded_after_cutoff_source_count_mismatch"
        )

    status["passed"] = _news_novelty_review_status_passed(status)
    return status


def _inspect_semantic_retrieval_plan_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="semantic_retrieval_plan_artifact",
        hash_field="semantic_retrieval_plan_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "prompt_hash_verified": None,
            "required_categories_verified": None,
            "query_count_verified": None,
            "category_coverage_verified": None,
        }
    )
    payload = _read_artifact_object(root, manifest.get("semantic_retrieval_plan_artifact"), status)
    if payload is None:
        status["passed"] = _semantic_retrieval_plan_status_passed(status)
        return status

    status["schema_version_verified"] = (
        payload.get("schema_version") == "nslab.semantic_retrieval_plan.v1"
    )
    if not status["schema_version_verified"]:
        status["errors"].append("semantic_retrieval_plan_schema_version_mismatch")
    run_id = manifest.get("run_id")
    status["run_id_verified"] = not isinstance(run_id, str) or payload.get("run_id") == run_id
    if not status["run_id_verified"]:
        status["errors"].append("semantic_retrieval_plan_run_id_mismatch")
    prompt_hash = _manifest_prompt_hash(manifest, "semantic_retrieval_plan")
    status["prompt_hash_verified"] = (
        not isinstance(prompt_hash, str) or payload.get("prompt_sha256") == prompt_hash
    )
    if not status["prompt_hash_verified"]:
        status["errors"].append("semantic_retrieval_plan_prompt_hash_mismatch")
    expected_categories = _semantic_retrieval_required_categories(manifest)
    observed_required_categories = _string_list(payload.get("required_categories"))
    status["required_categories_verified"] = (
        bool(expected_categories) and observed_required_categories == expected_categories
    )
    if not status["required_categories_verified"]:
        status["errors"].append("semantic_retrieval_plan_required_categories_mismatch")
    queries = payload.get("queries")
    if not isinstance(queries, list):
        status["errors"].append("semantic_retrieval_plan_queries_invalid")
        status["passed"] = _semantic_retrieval_plan_status_passed(status)
        return status
    status["query_count"] = len(queries)
    expected_query_count = manifest.get("semantic_retrieval_query_count")
    status["query_count_verified"] = not isinstance(expected_query_count, int) or len(
        queries
    ) == expected_query_count
    if not status["query_count_verified"]:
        status["errors"].append("semantic_retrieval_plan_query_count_mismatch")
    query_categories = [
        query.get("category")
        for query in queries
        if isinstance(query, dict) and isinstance(query.get("category"), str)
    ]
    status["category_coverage_verified"] = (
        bool(expected_categories) and set(query_categories) == set(expected_categories)
    )
    if not status["category_coverage_verified"]:
        status["errors"].append("semantic_retrieval_plan_category_coverage_mismatch")
    status["passed"] = _semantic_retrieval_plan_status_passed(status)
    return status


def _inspect_semantic_retrieval_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="semantic_retrieval_artifact",
        hash_field="semantic_retrieval_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "query_count_verified": None,
            "category_counts_verified": None,
            "included_episode_ids_verified": None,
            "excluded_episode_ids_verified": None,
            "summary_verified": None,
            "retrieval_zero_is_valid": None,
        }
    )
    artifact_ref = manifest.get("semantic_retrieval_artifact")
    artifact_path = _resolve_project_artifact(root, artifact_ref) if isinstance(artifact_ref, str) else None
    if artifact_path is None or not artifact_path.exists():
        status["passed"] = _semantic_retrieval_status_passed(status)
        return status
    try:
        rows = [
            json.loads(line)
            for line in artifact_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        status["errors"].append("semantic_retrieval_invalid_jsonl")
        status["passed"] = _semantic_retrieval_status_passed(status)
        return status
    if not all(isinstance(row, dict) for row in rows):
        status["errors"].append("semantic_retrieval_rows_not_objects")
        status["passed"] = _semantic_retrieval_status_passed(status)
        return status

    status["row_count"] = len(rows)
    run_id = manifest.get("run_id")
    status["schema_version_verified"] = all(
        row.get("schema_version") == "nslab.semantic_retrieval_result.v1" for row in rows
    )
    if not status["schema_version_verified"]:
        status["errors"].append("semantic_retrieval_schema_version_mismatch")
    status["run_id_verified"] = not isinstance(run_id, str) or all(
        row.get("run_id") == run_id for row in rows
    )
    if not status["run_id_verified"]:
        status["errors"].append("semantic_retrieval_run_id_mismatch")
    expected_query_count = manifest.get("semantic_retrieval_query_count")
    status["query_count_verified"] = not isinstance(expected_query_count, int) or len(
        rows
    ) == expected_query_count
    if not status["query_count_verified"]:
        status["errors"].append("semantic_retrieval_query_count_mismatch")
    category_counts = Counter(
        row.get("category") for row in rows if isinstance(row.get("category"), str)
    )
    summary = manifest.get("semantic_retrieval_summary")
    expected_category_counts = (
        summary.get("category_query_counts") if isinstance(summary, dict) else None
    )
    status["category_counts_verified"] = (
        isinstance(expected_category_counts, dict)
        and dict(category_counts) == expected_category_counts
    )
    if not status["category_counts_verified"]:
        status["errors"].append("semantic_retrieval_category_counts_mismatch")
    included_ids = _unique_strings(
        episode_id
        for row in rows
        for episode_id in _string_list(row.get("included_episode_ids"))
    )
    excluded_ids = _unique_strings(
        episode_id
        for row in rows
        for episode_id in _string_list(row.get("excluded_episode_ids"))
    )
    status["included_episode_ids_verified"] = included_ids == _string_list(
        manifest.get("semantic_retrieval_episode_ids")
    )
    if not status["included_episode_ids_verified"]:
        status["errors"].append("semantic_retrieval_included_episode_ids_mismatch")
    status["excluded_episode_ids_verified"] = excluded_ids == _string_list(
        manifest.get("excluded_semantic_retrieval_episode_ids")
    )
    if not status["excluded_episode_ids_verified"]:
        status["errors"].append("semantic_retrieval_excluded_episode_ids_mismatch")
    status["retrieval_zero_is_valid"] = (
        isinstance(summary, dict) and summary.get("retrieval_zero_is_valid") is True
    )
    if not status["retrieval_zero_is_valid"]:
        status["errors"].append("semantic_retrieval_zero_policy_missing")
    status["summary_verified"] = _semantic_retrieval_summary_verified(
        summary,
        query_count=len(rows),
        included_episode_count=len(included_ids),
        excluded_episode_count=len(excluded_ids),
    )
    if not status["summary_verified"]:
        status["errors"].append("semantic_retrieval_summary_mismatch")
    status["passed"] = _semantic_retrieval_status_passed(status)
    return status


def _inspect_candidate_expansion_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="candidate_expansion_artifact",
        hash_field="candidate_expansion_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "prompt_hash_verified": None,
            "required_paths_verified": None,
            "finding_count_verified": None,
            "path_coverage_verified": None,
            "path_counts_verified": None,
            "manifest_count_verified": None,
            "continuation_d_minus_one_verified": None,
        }
    )
    payload = _read_artifact_object(root, manifest.get("candidate_expansion_artifact"), status)
    if payload is None:
        status["passed"] = _candidate_expansion_status_passed(status)
        return status

    status["schema_version_verified"] = (
        payload.get("schema_version") == "nslab.candidate_expansion.v1"
    )
    if not status["schema_version_verified"]:
        status["errors"].append("candidate_expansion_schema_version_mismatch")

    run_id = manifest.get("run_id")
    status["run_id_verified"] = not isinstance(run_id, str) or payload.get("run_id") == run_id
    if not status["run_id_verified"]:
        status["errors"].append("candidate_expansion_run_id_mismatch")

    prompt_hash = _manifest_prompt_hash(manifest, "candidate_expansion")
    status["prompt_hash_verified"] = (
        not isinstance(prompt_hash, str) or payload.get("prompt_sha256") == prompt_hash
    )
    if not status["prompt_hash_verified"]:
        status["errors"].append("candidate_expansion_prompt_hash_mismatch")

    expected_paths = _candidate_expansion_required_paths(manifest)
    observed_required_paths = _string_list(payload.get("required_paths"))
    status["required_paths_verified"] = (
        bool(expected_paths) and observed_required_paths == expected_paths
    )
    if not status["required_paths_verified"]:
        status["errors"].append("candidate_expansion_required_paths_mismatch")

    findings = payload.get("findings")
    if not isinstance(findings, list) or not all(
        isinstance(finding, dict) for finding in findings
    ):
        status["errors"].append("candidate_expansion_findings_invalid")
        status["passed"] = _candidate_expansion_status_passed(status)
        return status

    observed_paths = [
        str(finding["path"])
        for finding in findings
        if isinstance(finding.get("path"), str)
    ]
    status["finding_count"] = len(findings)
    summary = manifest.get("candidate_expansion_summary")
    expected_finding_count = (
        summary.get("finding_count") if isinstance(summary, dict) else None
    )
    status["finding_count_verified"] = not isinstance(expected_finding_count, int) or len(
        findings
    ) == expected_finding_count
    if not status["finding_count_verified"]:
        status["errors"].append("candidate_expansion_finding_count_mismatch")

    status["path_coverage_verified"] = (
        bool(expected_paths) and set(observed_paths) == set(expected_paths)
    )
    if not status["path_coverage_verified"]:
        status["errors"].append("candidate_expansion_path_coverage_mismatch")

    observed_path_counts = dict(Counter(observed_paths))
    expected_path_counts = summary.get("path_counts") if isinstance(summary, dict) else None
    status["path_counts"] = observed_path_counts
    status["path_counts_verified"] = (
        isinstance(expected_path_counts, dict)
        and observed_path_counts == expected_path_counts
    )
    if not status["path_counts_verified"]:
        status["errors"].append("candidate_expansion_path_counts_mismatch")

    expected_manifest_count = manifest.get("candidate_expansion_count")
    status["manifest_count_verified"] = not isinstance(expected_manifest_count, int) or len(
        findings
    ) == expected_manifest_count
    if not status["manifest_count_verified"]:
        status["errors"].append("candidate_expansion_manifest_count_mismatch")

    continuation_findings = [
        finding for finding in findings if finding.get("path") == "CONTINUATION"
    ]
    status["continuation_d_minus_one_verified"] = bool(continuation_findings) and all(
        finding.get("d_minus_one_market_data_only") is True
        for finding in continuation_findings
    )
    if not status["continuation_d_minus_one_verified"]:
        status["errors"].append("candidate_expansion_continuation_d_minus_one_missing")

    status["passed"] = _candidate_expansion_status_passed(status)
    return status


def _inspect_candidate_web_check_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    required = bool(
        manifest.get("candidate_web_check_artifact")
        or _optional_int(manifest.get("candidate_web_check_count"))
        or _string_list(manifest.get("candidate_web_source_ids"))
    )
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="candidate_web_check_artifact",
        hash_field="candidate_web_check_sha256",
        required=required,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "row_count_verified": None,
            "source_ids_verified": None,
            "summary_source_count_verified": None,
            "summary_excluded_source_count_verified": None,
            "summary_subject_count_verified": None,
            "summary_final_candidate_subject_count_verified": None,
            "summary_candidate_expansion_subject_count_verified": None,
            "summary_expansion_paths_verified": None,
            "verification_focus_verified": None,
            "required_fields_verified": None,
            "source_url_verified": None,
            "cutoff_verified": None,
            "opened_text_absent_verified": None,
            "raw_content_absent_verified": None,
            "timestamp_precision_verified": None,
        }
    )
    if not status.get("configured"):
        status["passed"] = _candidate_web_check_status_passed(status)
        return status
    rows = _read_artifact_jsonl_rows(
        root,
        manifest.get("candidate_web_check_artifact"),
        status,
        label="candidate_web_check",
    )
    if rows is None:
        status["passed"] = _candidate_web_check_status_passed(status)
        return status

    status["row_count"] = len(rows)
    run_id = manifest.get("run_id")
    status["schema_version_verified"] = all(
        row.get("schema_version") == "nslab.candidate_web_check.v1" for row in rows
    )
    if not status["schema_version_verified"]:
        status["errors"].append("candidate_web_check_schema_version_mismatch")
    status["run_id_verified"] = not isinstance(run_id, str) or all(
        row.get("run_id") == run_id for row in rows
    )
    if not status["run_id_verified"]:
        status["errors"].append("candidate_web_check_run_id_mismatch")

    expected_count = manifest.get("candidate_web_check_count")
    status["row_count_verified"] = not isinstance(expected_count, int) or len(
        rows
    ) == expected_count
    if not status["row_count_verified"]:
        status["errors"].append("candidate_web_check_count_mismatch")

    row_source_ids = _unique_strings(
        str(row["source_id"]) for row in rows if isinstance(row.get("source_id"), str)
    )
    status["source_ids"] = row_source_ids
    status["source_ids_verified"] = row_source_ids == _string_list(
        manifest.get("candidate_web_source_ids")
    )
    if not status["source_ids_verified"]:
        status["errors"].append("candidate_web_check_source_ids_mismatch")

    summary = manifest.get("candidate_web_check_summary")
    summary_source_count = summary.get("source_count") if isinstance(summary, dict) else None
    status["summary_source_count_verified"] = (
        isinstance(summary_source_count, int) and summary_source_count == len(rows)
    )
    if not status["summary_source_count_verified"]:
        status["errors"].append("candidate_web_check_summary_source_count_mismatch")

    excluded_rows = _read_candidate_web_excluded_rows_for_summary(root, manifest)
    subject_rows = [*rows, *excluded_rows]
    subject_keys = _candidate_web_subject_keys(subject_rows)
    final_candidate_keys = _candidate_web_subject_keys(
        row
        for row in subject_rows
        if row.get("candidate_subject_type") == "final_candidate"
    )
    expansion_subject_keys = _candidate_web_subject_keys(
        row
        for row in subject_rows
        if row.get("candidate_subject_type") == "candidate_expansion"
    )
    summary_excluded_source_count = (
        summary.get("excluded_source_count") if isinstance(summary, dict) else None
    )
    status["summary_excluded_source_count_verified"] = (
        isinstance(summary_excluded_source_count, int)
        and summary_excluded_source_count == len(excluded_rows)
    )
    if not status["summary_excluded_source_count_verified"]:
        status["errors"].append(
            "candidate_web_check_summary_excluded_source_count_mismatch"
        )

    summary_subject_count = summary.get("subject_count") if isinstance(summary, dict) else None
    status["summary_subject_count_verified"] = (
        isinstance(summary_subject_count, int)
        and summary_subject_count == len(subject_keys)
    )
    if not status["summary_subject_count_verified"]:
        status["errors"].append("candidate_web_check_summary_subject_count_mismatch")

    summary_final_candidate_count = (
        summary.get("final_candidate_subject_count")
        if isinstance(summary, dict)
        else None
    )
    status["summary_final_candidate_subject_count_verified"] = (
        isinstance(summary_final_candidate_count, int)
        and summary_final_candidate_count == len(final_candidate_keys)
    )
    if not status["summary_final_candidate_subject_count_verified"]:
        status["errors"].append(
            "candidate_web_check_summary_final_candidate_subject_count_mismatch"
        )

    summary_expansion_subject_count = (
        summary.get("candidate_expansion_subject_count")
        if isinstance(summary, dict)
        else None
    )
    status["summary_candidate_expansion_subject_count_verified"] = (
        isinstance(summary_expansion_subject_count, int)
        and summary_expansion_subject_count == len(expansion_subject_keys)
    )
    if not status["summary_candidate_expansion_subject_count_verified"]:
        status["errors"].append(
            "candidate_web_check_summary_candidate_expansion_subject_count_mismatch"
        )

    expansion_paths = _candidate_web_expansion_paths(subject_rows)
    status["summary_expansion_paths_verified"] = (
        isinstance(summary, dict)
        and _string_list(summary.get("expansion_paths")) == expansion_paths
    )
    if not status["summary_expansion_paths_verified"]:
        status["errors"].append("candidate_web_check_summary_expansion_paths_mismatch")

    expected_focus = _candidate_web_verification_focus(manifest)
    status["verification_focus_verified"] = bool(expected_focus) and all(
        _string_list(row.get("verification_focus")) == expected_focus for row in rows
    )
    if not status["verification_focus_verified"]:
        status["errors"].append("candidate_web_check_verification_focus_mismatch")

    required_fields = CANDIDATE_WEB_CHECK_REQUIRED_FIELDS | {"verification_focus"}
    status["required_fields_verified"] = all(
        required_fields <= set(row) for row in rows
    )
    if not status["required_fields_verified"]:
        status["errors"].append("candidate_web_check_required_fields_missing")

    status["source_url_verified"] = all(
        isinstance(row.get("source_url"), str)
        and row.get("source_url") == row.get("url")
        for row in rows
    )
    if not status["source_url_verified"]:
        status["errors"].append("candidate_web_check_source_url_mismatch")

    cutoff_at = _manifest_datetime(manifest.get("cutoff_at"))
    status["cutoff_verified"] = all(
        _web_source_cutoff_valid(row, cutoff_at) for row in rows
    )
    if not status["cutoff_verified"]:
        status["errors"].append("candidate_web_check_cutoff_not_verified")

    status["opened_text_absent_verified"] = all("opened_text" not in row for row in rows)
    if not status["opened_text_absent_verified"]:
        status["errors"].append("candidate_web_check_opened_text_present")

    status["raw_content_absent_verified"] = all(
        "opened_text" not in row and "body" not in row and "content" not in row
        for row in rows
    )
    if not status["raw_content_absent_verified"]:
        status["errors"].append("candidate_web_check_raw_content_present")

    status["timestamp_precision_verified"] = all(
        _web_timestamp_precision_valid(row) for row in rows
    )
    if not status["timestamp_precision_verified"]:
        status["errors"].append("candidate_web_check_timestamp_precision_invalid")

    status["passed"] = _candidate_web_check_status_passed(status)
    return status


def _inspect_excluded_candidate_web_check_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    required = bool(
        manifest.get("excluded_candidate_web_check_artifact")
        or _optional_int(manifest.get("excluded_candidate_web_check_count"))
        or _string_list(manifest.get("excluded_candidate_web_source_ids"))
    )
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="excluded_candidate_web_check_artifact",
        hash_field="excluded_candidate_web_check_sha256",
        required=required,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "row_count_verified": None,
            "source_ids_verified": None,
            "duplicate_source_ids_absent": None,
            "not_accepted_verified": None,
            "required_fields_verified": None,
            "source_url_verified": None,
            "exclusion_reason_verified": None,
            "raw_content_absent_verified": None,
            "cutoff_exclusion_verified": None,
            "timestamp_precision_verified": None,
        }
    )
    if not status.get("configured"):
        status["passed"] = _excluded_candidate_web_check_status_passed(status)
        return status
    rows = _read_artifact_jsonl_rows(
        root,
        manifest.get("excluded_candidate_web_check_artifact"),
        status,
        label="excluded_candidate_web_check",
    )
    if rows is None:
        status["passed"] = _excluded_candidate_web_check_status_passed(status)
        return status

    status["row_count"] = len(rows)
    status["schema_version_verified"] = all(
        row.get("schema_version") == "nslab.excluded_candidate_web_check.v1"
        for row in rows
    )
    if not status["schema_version_verified"]:
        status["errors"].append("excluded_candidate_web_check_schema_version_mismatch")

    run_id = manifest.get("run_id")
    status["run_id_verified"] = not isinstance(run_id, str) or all(
        row.get("run_id") == run_id for row in rows
    )
    if not status["run_id_verified"]:
        status["errors"].append("excluded_candidate_web_check_run_id_mismatch")

    expected_count = manifest.get("excluded_candidate_web_check_count")
    status["row_count_verified"] = not isinstance(expected_count, int) or len(
        rows
    ) == expected_count
    if not status["row_count_verified"]:
        status["errors"].append("excluded_candidate_web_check_count_mismatch")

    source_ids = [
        row.get("source_id") for row in rows if isinstance(row.get("source_id"), str)
    ]
    unique_source_ids = _unique_strings(str(source_id) for source_id in source_ids)
    expected_source_ids = _string_list(manifest.get("excluded_candidate_web_source_ids"))
    status["source_ids"] = unique_source_ids
    status["source_ids_verified"] = unique_source_ids == expected_source_ids
    if not status["source_ids_verified"]:
        status["errors"].append("excluded_candidate_web_check_source_ids_mismatch")
    status["duplicate_source_ids_absent"] = len(source_ids) == len(set(source_ids))
    if not status["duplicate_source_ids_absent"]:
        status["errors"].append("excluded_candidate_web_check_duplicate_source_id")

    accepted_source_ids = set(_string_list(manifest.get("candidate_web_source_ids")))
    status["not_accepted_verified"] = all(
        source_id not in accepted_source_ids for source_id in unique_source_ids
    )
    if not status["not_accepted_verified"]:
        status["errors"].append("excluded_candidate_web_check_also_accepted")

    status["required_fields_verified"] = all(
        set(row) >= EXCLUDED_CANDIDATE_WEB_CHECK_REQUIRED_FIELDS for row in rows
    )
    if not status["required_fields_verified"]:
        status["errors"].append("excluded_candidate_web_check_required_fields_missing")

    status["source_url_verified"] = all(_source_url_valid(row) for row in rows)
    if not status["source_url_verified"]:
        status["errors"].append("excluded_candidate_web_check_source_url_mismatch")

    status["exclusion_reason_verified"] = all(
        isinstance(row.get("exclusion_reason"), str) and bool(row.get("exclusion_reason"))
        for row in rows
    )
    if not status["exclusion_reason_verified"]:
        status["errors"].append(
            "excluded_candidate_web_check_exclusion_reason_missing"
        )

    status["raw_content_absent_verified"] = all(
        "body" not in row and "content" not in row and "opened_text" not in row
        for row in rows
    )
    if not status["raw_content_absent_verified"]:
        status["errors"].append("excluded_candidate_web_check_raw_content_present")

    cutoff_at = _manifest_datetime(manifest.get("cutoff_at"))
    status["cutoff_exclusion_verified"] = all(
        _excluded_web_source_cutoff_valid(row, cutoff_at) for row in rows
    )
    if not status["cutoff_exclusion_verified"]:
        status["errors"].append(
            "excluded_candidate_web_check_cutoff_exclusion_invalid"
        )

    status["timestamp_precision_verified"] = all(
        _web_timestamp_precision_valid(row) for row in rows
    )
    if not status["timestamp_precision_verified"]:
        status["errors"].append("excluded_candidate_web_check_timestamp_precision_invalid")

    status["passed"] = _excluded_candidate_web_check_status_passed(status)
    return status


def _inspect_source_ledger_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="source_ledger_artifact",
        hash_field="source_ledger_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "entry_count_verified": None,
            "required_fields_verified": None,
            "source_ids_verified": None,
            "duplicate_source_ids_absent": None,
            "summary_verified": None,
            "web_sources_covered_verified": None,
            "candidate_web_sources_covered_verified": None,
            "excluded_sources_absent_verified": None,
            "source_url_verified": None,
            "raw_content_absent_verified": None,
            "usage_phase_verified": None,
            "blind_cutoff_verified": None,
            "timestamp_precision_verified": None,
        }
    )
    if not status.get("configured"):
        status["passed"] = _source_ledger_status_passed(status)
        return status
    rows = _read_artifact_jsonl_rows(
        root,
        manifest.get("source_ledger_artifact"),
        status,
        label="source_ledger",
    )
    if rows is None:
        status["passed"] = _source_ledger_status_passed(status)
        return status

    status["row_count"] = len(rows)
    status["schema_version_verified"] = all(
        row.get("schema_version") == "nslab.source_ledger.v1" for row in rows
    )
    if not status["schema_version_verified"]:
        status["errors"].append("source_ledger_schema_version_mismatch")

    expected_count = manifest.get("source_ledger_entry_count")
    status["entry_count_verified"] = (
        isinstance(expected_count, int) and len(rows) == expected_count
    )
    if not status["entry_count_verified"]:
        status["errors"].append("source_ledger_entry_count_mismatch")

    status["required_fields_verified"] = all(
        set(row) >= SOURCE_LEDGER_REQUIRED_FIELDS for row in rows
    )
    if not status["required_fields_verified"]:
        status["errors"].append("source_ledger_required_fields_missing")

    source_ids = [
        row.get("source_id") for row in rows if isinstance(row.get("source_id"), str)
    ]
    status["source_ids_verified"] = len(source_ids) == len(rows) and all(source_ids)
    if not status["source_ids_verified"]:
        status["errors"].append("source_ledger_source_id_invalid")
    status["duplicate_source_ids_absent"] = len(source_ids) == len(set(source_ids))
    if not status["duplicate_source_ids_absent"]:
        status["errors"].append("source_ledger_duplicate_source_id")

    status["summary_verified"] = _source_ledger_summary_matches(
        manifest.get("source_ledger_summary"),
        rows,
    )
    if not status["summary_verified"]:
        status["errors"].append("source_ledger_summary_mismatch")

    ledger_web_source_ids = _source_ledger_source_ids_for_type(
        rows, "web_search_result"
    )
    status["web_sources_covered_verified"] = _same_unique_string_set(
        ledger_web_source_ids,
        manifest.get("web_sources"),
    )
    if not status["web_sources_covered_verified"]:
        status["errors"].append("source_ledger_web_sources_mismatch")

    ledger_candidate_web_source_ids = _source_ledger_source_ids_for_type(
        rows, "candidate_web_check"
    )
    status["candidate_web_sources_covered_verified"] = (
        ledger_candidate_web_source_ids
        == _string_list(manifest.get("candidate_web_source_ids"))
    )
    if not status["candidate_web_sources_covered_verified"]:
        status["errors"].append("source_ledger_candidate_web_sources_mismatch")

    excluded_source_ids = {
        *_string_list(manifest.get("excluded_web_source_ids")),
        *_string_list(manifest.get("excluded_candidate_web_source_ids")),
    }
    status["excluded_sources_absent_verified"] = not (
        set(source_ids) & excluded_source_ids
    )
    if not status["excluded_sources_absent_verified"]:
        status["errors"].append("source_ledger_excluded_source_present")

    status["source_url_verified"] = all(
        _source_url_valid(row) for row in rows
    )
    if not status["source_url_verified"]:
        status["errors"].append("source_ledger_source_url_mismatch")

    status["raw_content_absent_verified"] = all(
        "body" not in row and "content" not in row and "opened_text" not in row
        for row in rows
    )
    if not status["raw_content_absent_verified"]:
        status["errors"].append("source_ledger_raw_content_present")

    status["usage_phase_verified"] = all(
        row.get("usage_phase") in SOURCE_LEDGER_USAGE_PHASES for row in rows
    )
    if not status["usage_phase_verified"]:
        status["errors"].append("source_ledger_usage_phase_invalid")

    cutoff_at = _manifest_datetime(manifest.get("cutoff_at"))
    status["blind_cutoff_verified"] = all(
        _source_ledger_blind_cutoff_valid(row, cutoff_at) for row in rows
    )
    if not status["blind_cutoff_verified"]:
        status["errors"].append("source_ledger_blind_cutoff_invalid")

    status["timestamp_precision_verified"] = all(
        _web_timestamp_precision_valid(row) for row in rows
    )
    if not status["timestamp_precision_verified"]:
        status["errors"].append("source_ledger_timestamp_precision_invalid")

    status["passed"] = _source_ledger_status_passed(status)
    return status


def _inspect_web_source_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    required = bool(
        manifest.get("web_source_artifact") or _string_list(manifest.get("web_sources"))
    )
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="web_source_artifact",
        hash_field="web_source_sha256",
        required=required,
    )
    status.update(
        {
            "schema_version_verified": None,
            "source_ids_verified": None,
            "duplicate_source_ids_absent": None,
            "required_fields_verified": None,
            "source_url_verified": None,
            "raw_content_absent_verified": None,
            "cutoff_verified": None,
            "timestamp_precision_verified": None,
        }
    )
    if not status.get("configured"):
        status["passed"] = _web_source_status_passed(status)
        return status
    rows = _read_artifact_jsonl_rows(
        root,
        manifest.get("web_source_artifact"),
        status,
        label="web_source",
    )
    if rows is None:
        status["passed"] = _web_source_status_passed(status)
        return status

    status["row_count"] = len(rows)
    status["schema_version_verified"] = all(
        row.get("schema_version") == "nslab.web_source.v1" for row in rows
    )
    if not status["schema_version_verified"]:
        status["errors"].append("web_source_schema_version_mismatch")

    source_ids = [
        row.get("source_id") for row in rows if isinstance(row.get("source_id"), str)
    ]
    unique_source_ids = _unique_strings(str(source_id) for source_id in source_ids)
    expected_source_ids = _string_list(manifest.get("web_sources"))
    status["source_ids"] = unique_source_ids
    status["source_ids_verified"] = (
        len(expected_source_ids) == len(set(expected_source_ids))
        and set(unique_source_ids) == set(expected_source_ids)
    )
    if not status["source_ids_verified"]:
        status["errors"].append("web_source_source_ids_mismatch")
    status["duplicate_source_ids_absent"] = len(source_ids) == len(set(source_ids))
    if not status["duplicate_source_ids_absent"]:
        status["errors"].append("web_source_duplicate_source_id")

    status["required_fields_verified"] = all(
        set(row) >= WEB_SOURCE_REQUIRED_FIELDS for row in rows
    )
    if not status["required_fields_verified"]:
        status["errors"].append("web_source_required_fields_missing")

    status["source_url_verified"] = all(_source_url_valid(row) for row in rows)
    if not status["source_url_verified"]:
        status["errors"].append("web_source_source_url_mismatch")

    status["raw_content_absent_verified"] = all(
        "body" not in row and "content" not in row and "opened_text" not in row
        for row in rows
    )
    if not status["raw_content_absent_verified"]:
        status["errors"].append("web_source_raw_content_present")

    cutoff_at = _manifest_datetime(manifest.get("cutoff_at"))
    status["cutoff_verified"] = all(
        _web_source_cutoff_valid(row, cutoff_at) for row in rows
    )
    if not status["cutoff_verified"]:
        status["errors"].append("web_source_cutoff_not_verified")

    status["timestamp_precision_verified"] = all(
        _web_timestamp_precision_valid(row) for row in rows
    )
    if not status["timestamp_precision_verified"]:
        status["errors"].append("web_source_timestamp_precision_invalid")

    status["passed"] = _web_source_status_passed(status)
    return status


def _inspect_excluded_web_source_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    required = bool(
        manifest.get("excluded_web_source_artifact")
        or _string_list(manifest.get("excluded_web_source_ids"))
        or _optional_int(manifest.get("excluded_web_source_count"))
    )
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="excluded_web_source_artifact",
        hash_field="excluded_web_source_sha256",
        required=required,
    )
    status.update(
        {
            "schema_version_verified": None,
            "entry_count_verified": None,
            "source_ids_verified": None,
            "duplicate_source_ids_absent": None,
            "not_included_verified": None,
            "required_fields_verified": None,
            "source_url_verified": None,
            "exclusion_reason_verified": None,
            "raw_content_absent_verified": None,
            "cutoff_exclusion_verified": None,
            "timestamp_precision_verified": None,
        }
    )
    if not status.get("configured"):
        status["passed"] = _excluded_web_source_status_passed(status)
        return status
    rows = _read_artifact_jsonl_rows(
        root,
        manifest.get("excluded_web_source_artifact"),
        status,
        label="excluded_web_source",
    )
    if rows is None:
        status["passed"] = _excluded_web_source_status_passed(status)
        return status

    status["row_count"] = len(rows)
    status["schema_version_verified"] = all(
        row.get("schema_version") == "nslab.excluded_web_source.v1" for row in rows
    )
    if not status["schema_version_verified"]:
        status["errors"].append("excluded_web_source_schema_version_mismatch")

    expected_count = manifest.get("excluded_web_source_count")
    status["entry_count_verified"] = not isinstance(expected_count, int) or len(
        rows
    ) == expected_count
    if not status["entry_count_verified"]:
        status["errors"].append("excluded_web_source_count_mismatch")

    source_ids = [
        row.get("source_id") for row in rows if isinstance(row.get("source_id"), str)
    ]
    unique_source_ids = _unique_strings(str(source_id) for source_id in source_ids)
    expected_source_ids = _string_list(manifest.get("excluded_web_source_ids"))
    status["source_ids"] = unique_source_ids
    status["source_ids_verified"] = (
        len(expected_source_ids) == len(set(expected_source_ids))
        and set(unique_source_ids) == set(expected_source_ids)
    )
    if not status["source_ids_verified"]:
        status["errors"].append("excluded_web_source_source_ids_mismatch")
    status["duplicate_source_ids_absent"] = len(source_ids) == len(set(source_ids))
    if not status["duplicate_source_ids_absent"]:
        status["errors"].append("excluded_web_source_duplicate_source_id")

    included_source_ids = set(_string_list(manifest.get("web_sources")))
    status["not_included_verified"] = all(
        source_id not in included_source_ids for source_id in unique_source_ids
    )
    if not status["not_included_verified"]:
        status["errors"].append("excluded_web_source_also_included")

    status["required_fields_verified"] = all(
        set(row) >= EXCLUDED_WEB_SOURCE_REQUIRED_FIELDS for row in rows
    )
    if not status["required_fields_verified"]:
        status["errors"].append("excluded_web_source_required_fields_missing")

    status["source_url_verified"] = all(_source_url_valid(row) for row in rows)
    if not status["source_url_verified"]:
        status["errors"].append("excluded_web_source_source_url_mismatch")

    status["exclusion_reason_verified"] = all(
        isinstance(row.get("exclusion_reason"), str) and bool(row.get("exclusion_reason"))
        for row in rows
    )
    if not status["exclusion_reason_verified"]:
        status["errors"].append("excluded_web_source_exclusion_reason_missing")

    status["raw_content_absent_verified"] = all(
        "body" not in row and "content" not in row and "opened_text" not in row
        for row in rows
    )
    if not status["raw_content_absent_verified"]:
        status["errors"].append("excluded_web_source_raw_content_present")

    cutoff_at = _manifest_datetime(manifest.get("cutoff_at"))
    status["cutoff_exclusion_verified"] = all(
        _excluded_web_source_cutoff_valid(row, cutoff_at) for row in rows
    )
    if not status["cutoff_exclusion_verified"]:
        status["errors"].append("excluded_web_source_cutoff_exclusion_invalid")

    status["timestamp_precision_verified"] = all(
        _web_timestamp_precision_valid(row) for row in rows
    )
    if not status["timestamp_precision_verified"]:
        status["errors"].append("excluded_web_source_timestamp_precision_invalid")

    status["passed"] = _excluded_web_source_status_passed(status)
    return status


def _inspect_candidate_verification_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    required = bool(
        manifest.get("candidate_verification_artifact")
        or _optional_int(manifest.get("candidate_verification_count"))
        or manifest.get("candidate_verification_summary")
    )
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="candidate_verification_artifact",
        hash_field="candidate_verification_sha256",
        required=required,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "required_dimensions_verified": None,
            "subject_count_verified": None,
            "finding_count_verified": None,
            "dimension_coverage_verified": None,
            "status_counts_verified": None,
            "source_counts_verified": None,
            "accepted_source_ids_verified": None,
            "excluded_source_ids_verified": None,
            "subjects_without_cutoff_safe_sources_verified": None,
            "candidate_expansion_subject_count_verified": None,
            "d_minus_one_only_subject_count_verified": None,
            "d_minus_one_market_snapshots_valid": None,
            "d_minus_one_snapshot_count_verified": None,
            "d_minus_one_snapshot_unavailable_count_verified": None,
        }
    )
    if not status.get("configured"):
        status["passed"] = _candidate_verification_status_passed(status)
        return status
    payload = _read_artifact_object(
        root, manifest.get("candidate_verification_artifact"), status
    )
    if payload is None:
        status["passed"] = _candidate_verification_status_passed(status)
        return status

    status["schema_version_verified"] = (
        payload.get("schema_version") == "nslab.candidate_verification.v1"
    )
    if not status["schema_version_verified"]:
        status["errors"].append("candidate_verification_schema_version_mismatch")
    run_id = manifest.get("run_id")
    status["run_id_verified"] = not isinstance(run_id, str) or payload.get("run_id") == run_id
    if not status["run_id_verified"]:
        status["errors"].append("candidate_verification_run_id_mismatch")

    expected_dimensions = _candidate_verification_required_dimensions(manifest)
    observed_dimensions = _string_list(payload.get("required_dimensions"))
    status["required_dimensions_verified"] = (
        bool(expected_dimensions) and observed_dimensions == expected_dimensions
    )
    if not status["required_dimensions_verified"]:
        status["errors"].append("candidate_verification_required_dimensions_mismatch")

    findings = payload.get("findings")
    if not isinstance(findings, list) or not all(
        isinstance(finding, dict) for finding in findings
    ):
        status["errors"].append("candidate_verification_findings_invalid")
        status["passed"] = _candidate_verification_status_passed(status)
        return status

    summary = manifest.get("candidate_verification_summary")
    status["finding_count"] = len(findings)
    expected_manifest_count = manifest.get("candidate_verification_count")
    summary_finding_count = summary.get("finding_count") if isinstance(summary, dict) else None
    status["finding_count_verified"] = (
        (not isinstance(expected_manifest_count, int) or len(findings) == expected_manifest_count)
        and isinstance(summary_finding_count, int)
        and summary_finding_count == len(findings)
    )
    if not status["finding_count_verified"]:
        status["errors"].append("candidate_verification_finding_count_mismatch")

    summary_subject_count = summary.get("subject_count") if isinstance(summary, dict) else None
    payload_subject_count = payload.get("subject_count")
    status["subject_count_verified"] = (
        isinstance(payload_subject_count, int)
        and payload_subject_count == len(findings)
        and isinstance(summary_subject_count, int)
        and summary_subject_count == len(findings)
    )
    if not status["subject_count_verified"]:
        status["errors"].append("candidate_verification_subject_count_mismatch")

    status["dimension_coverage_verified"] = bool(expected_dimensions) and all(
        _candidate_verification_dimension_names(finding) == expected_dimensions
        for finding in findings
    )
    if not status["dimension_coverage_verified"]:
        status["errors"].append("candidate_verification_dimension_coverage_mismatch")

    observed_status_counts = _candidate_verification_status_counts(findings)
    expected_status_counts = summary.get("status_counts") if isinstance(summary, dict) else None
    status["status_counts"] = observed_status_counts
    status["status_counts_verified"] = (
        isinstance(expected_status_counts, dict)
        and observed_status_counts == expected_status_counts
    )
    if not status["status_counts_verified"]:
        status["errors"].append("candidate_verification_status_counts_mismatch")

    status["source_counts_verified"] = (
        sum(_non_bool_int(finding.get("source_count")) or 0 for finding in findings)
        == _optional_int(manifest.get("candidate_web_check_count"))
        and sum(
            _non_bool_int(finding.get("excluded_source_count")) or 0
            for finding in findings
        )
        == _optional_int(manifest.get("excluded_candidate_web_check_count"))
    )
    if not status["source_counts_verified"]:
        status["errors"].append("candidate_verification_source_counts_mismatch")

    accepted_ids = _unique_strings(
        source_id
        for finding in findings
        for source_id in _string_list(finding.get("accepted_source_ids"))
    )
    excluded_ids = _unique_strings(
        source_id
        for finding in findings
        for source_id in _string_list(finding.get("excluded_source_ids"))
    )
    status["accepted_source_ids_verified"] = accepted_ids == _string_list(
        manifest.get("candidate_web_source_ids")
    )
    if not status["accepted_source_ids_verified"]:
        status["errors"].append("candidate_verification_accepted_source_ids_mismatch")
    status["excluded_source_ids_verified"] = excluded_ids == _string_list(
        manifest.get("excluded_candidate_web_source_ids")
    )
    if not status["excluded_source_ids_verified"]:
        status["errors"].append("candidate_verification_excluded_source_ids_mismatch")

    observed_subjects_without_sources = sum(
        1 for finding in findings if not _string_list(finding.get("accepted_source_ids"))
    )
    expected_subjects_without_sources = (
        summary.get("subjects_without_cutoff_safe_sources")
        if isinstance(summary, dict)
        else None
    )
    status["subjects_without_cutoff_safe_sources_verified"] = (
        isinstance(expected_subjects_without_sources, int)
        and observed_subjects_without_sources == expected_subjects_without_sources
    )
    if not status["subjects_without_cutoff_safe_sources_verified"]:
        status["errors"].append(
            "candidate_verification_subjects_without_cutoff_safe_sources_mismatch"
        )

    observed_expansion_subject_count = sum(
        1 for finding in findings if finding.get("subject_type") == "candidate_expansion"
    )
    expected_expansion_subject_count = (
        summary.get("candidate_expansion_subject_count")
        if isinstance(summary, dict)
        else None
    )
    status["candidate_expansion_subject_count_verified"] = (
        isinstance(expected_expansion_subject_count, int)
        and observed_expansion_subject_count == expected_expansion_subject_count
    )
    if not status["candidate_expansion_subject_count_verified"]:
        status["errors"].append(
            "candidate_verification_candidate_expansion_subject_count_mismatch"
        )

    observed_d_minus_one_count = sum(
        1 for finding in findings if finding.get("d_minus_one_market_data_only") is True
    )
    expected_d_minus_one_count = (
        summary.get("d_minus_one_only_subject_count")
        if isinstance(summary, dict)
        else None
    )
    status["d_minus_one_only_subject_count_verified"] = (
        isinstance(expected_d_minus_one_count, int)
        and observed_d_minus_one_count == expected_d_minus_one_count
    )
    if not status["d_minus_one_only_subject_count_verified"]:
        status["errors"].append(
            "candidate_verification_d_minus_one_only_subject_count_mismatch"
        )

    market_snapshots = [
        finding.get("blind_safe_market_snapshot") for finding in findings
    ]
    has_market_snapshot_summary = isinstance(summary, dict) and (
        "d_minus_one_snapshot_count" in summary
        or "d_minus_one_snapshot_unavailable_count" in summary
    )
    if not has_market_snapshot_summary:
        status["d_minus_one_market_snapshots_valid"] = True
        status["d_minus_one_snapshot_count_verified"] = True
        status["d_minus_one_snapshot_unavailable_count_verified"] = True
        status["passed"] = _candidate_verification_status_passed(status)
        return status
    status["d_minus_one_market_snapshots_valid"] = all(
        isinstance(snapshot, dict)
        and isinstance(snapshot.get("status"), str)
        and snapshot.get("status") in {"snapshot", "unavailable"}
        for snapshot in market_snapshots
    )
    if not status["d_minus_one_market_snapshots_valid"]:
        status["errors"].append(
            "candidate_verification_d_minus_one_market_snapshot_invalid"
        )
    observed_snapshot_count = sum(
        1
        for snapshot in market_snapshots
        if isinstance(snapshot, dict) and snapshot.get("status") == "snapshot"
    )
    observed_unavailable_count = sum(
        1
        for snapshot in market_snapshots
        if isinstance(snapshot, dict) and snapshot.get("status") != "snapshot"
    )
    expected_snapshot_count = (
        summary.get("d_minus_one_snapshot_count") if isinstance(summary, dict) else None
    )
    expected_unavailable_count = (
        summary.get("d_minus_one_snapshot_unavailable_count")
        if isinstance(summary, dict)
        else None
    )
    status["d_minus_one_snapshot_count_verified"] = (
        expected_snapshot_count is None
        or (
            isinstance(expected_snapshot_count, int)
            and observed_snapshot_count == expected_snapshot_count
        )
    )
    if not status["d_minus_one_snapshot_count_verified"]:
        status["errors"].append("candidate_verification_d_minus_one_snapshot_count_mismatch")
    status["d_minus_one_snapshot_unavailable_count_verified"] = (
        expected_unavailable_count is None
        or (
            isinstance(expected_unavailable_count, int)
            and observed_unavailable_count == expected_unavailable_count
        )
    )
    if not status["d_minus_one_snapshot_unavailable_count_verified"]:
        status["errors"].append(
            "candidate_verification_d_minus_one_snapshot_unavailable_count_mismatch"
        )

    status["passed"] = _candidate_verification_status_passed(status)
    return status


def _inspect_final_synthesis_context_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="final_synthesis_context_artifact",
        hash_field="final_synthesis_context_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "payload_hash_verified": None,
            "required_inputs_verified": None,
            "required_input_set_verified": None,
            "payload_keys_verified": None,
            "input_summary_verified": None,
            "manifest_summary_verified": None,
            "manifest_counts_verified": None,
            "event_clusters_verified": None,
            "semantic_retrieval_plan_artifact_verified": None,
            "semantic_retrieval_artifact_verified": None,
            "semantic_retrieval_summary_verified": None,
            "semantic_retrieval_rows_verified": None,
            "semantic_retrieval_excluded_ids_verified": None,
            "semantic_retrieval_context_verified": None,
            "web_research_queries_verified": None,
            "web_research_source_ids_verified": None,
            "web_research_sources_verified": None,
            "web_research_excluded_ids_verified": None,
            "web_research_verified": None,
            "candidate_verification_context_verified": None,
            "candidate_web_checks_context_verified": None,
            "news_novelty_review_context_verified": None,
            "candidate_expansion_context_verified": None,
            "red_team_output_context_verified": None,
        }
    )
    if not (
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists")
    ):
        status["passed"] = _final_synthesis_context_status_passed(status)
        return status
    artifact_ref = manifest.get("final_synthesis_context_artifact")
    if not isinstance(artifact_ref, str):
        status["passed"] = _final_synthesis_context_status_passed(status)
        return status
    artifact_path = _resolve_project_artifact(root, artifact_ref)
    if artifact_path is None or not artifact_path.exists():
        status["passed"] = _final_synthesis_context_status_passed(status)
        return status
    try:
        payload = read_json(artifact_path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        status["errors"].append("final_synthesis_context_invalid_json")
        status["passed"] = _final_synthesis_context_status_passed(status)
        return status
    if not isinstance(payload, dict):
        status["errors"].append("final_synthesis_context_not_object")
        status["passed"] = _final_synthesis_context_status_passed(status)
        return status

    status["schema_version_verified"] = (
        payload.get("schema_version") == "nslab.final_synthesis_context.v1"
    )
    if not status["schema_version_verified"]:
        status["errors"].append("final_synthesis_context_schema_version_mismatch")

    run_id = manifest.get("run_id")
    status["run_id_verified"] = not isinstance(run_id, str) or payload.get("run_id") == run_id
    if not status["run_id_verified"]:
        status["errors"].append("final_synthesis_context_run_id_mismatch")

    context_payload = payload.get("payload")
    if not isinstance(context_payload, dict):
        status["errors"].append("final_synthesis_context_payload_invalid")
        status["passed"] = _final_synthesis_context_status_passed(status)
        return status

    status["payload_hash_verified"] = payload.get("payload_sha256") == sha256_text(
        canonical_json(context_payload)
    )
    if not status["payload_hash_verified"]:
        status["errors"].append("final_synthesis_context_payload_sha256_mismatch")

    required_inputs = context_payload.get("required_inputs")
    status["required_inputs_verified"] = (
        isinstance(required_inputs, list)
        and all(isinstance(item, str) for item in required_inputs)
        and payload.get("required_inputs") == required_inputs
    )
    if not status["required_inputs_verified"]:
        status["errors"].append("final_synthesis_context_required_inputs_mismatch")
    required_input_list = _string_list(required_inputs)
    status["required_input_set_verified"] = (
        final_synthesis_required_inputs_compatible(required_input_list)
    )
    if not status["required_input_set_verified"]:
        status["errors"].append("final_synthesis_context_required_input_set_mismatch")
    missing_payload_keys = [
        key for key in required_input_list if key not in context_payload
    ]
    status["missing_payload_keys"] = missing_payload_keys
    status["payload_keys_verified"] = not missing_payload_keys
    if not status["payload_keys_verified"]:
        status["errors"].append("final_synthesis_context_payload_keys_missing")

    expected_summary = final_synthesis_input_summary(context_payload)
    status["input_summary_verified"] = payload.get("input_summary") == expected_summary
    if not status["input_summary_verified"]:
        status["errors"].append("final_synthesis_context_input_summary_mismatch")

    manifest_summary = manifest.get("final_synthesis_context_summary")
    status["manifest_summary_verified"] = (
        manifest_summary is not None and manifest_summary == payload.get("input_summary")
    )
    if not status["manifest_summary_verified"]:
        status["errors"].append("final_synthesis_context_manifest_summary_mismatch")
    manifest_count_mismatches = _final_synthesis_manifest_count_mismatches(
        manifest, expected_summary
    )
    status["manifest_count_mismatches"] = manifest_count_mismatches
    status["manifest_counts_verified"] = not manifest_count_mismatches
    if not status["manifest_counts_verified"]:
        status["errors"].append("final_synthesis_context_manifest_count_mismatches")

    _inspect_final_synthesis_event_cluster_context(
        root,
        manifest,
        context_payload,
        status,
    )
    _inspect_final_synthesis_semantic_retrieval_context(
        root,
        manifest,
        context_payload,
        status,
    )
    _inspect_final_synthesis_web_research_context(
        root,
        manifest,
        context_payload,
        status,
    )
    _inspect_final_synthesis_candidate_context(
        root,
        manifest,
        context_payload,
        status,
    )
    _inspect_final_synthesis_review_context(
        root,
        manifest,
        context_payload,
        status,
    )

    status["passed"] = _final_synthesis_context_status_passed(status)
    return status


def _inspect_final_synthesis_event_cluster_context(
    root: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    status: dict[str, Any],
) -> None:
    event_cluster_rows = _read_final_synthesis_jsonl_context_rows(
        root,
        manifest.get("event_cluster_artifact"),
        status,
        label="event_clusters",
    )
    status["event_clusters_verified"] = (
        context_payload.get("event_clusters") == event_cluster_rows
    )
    if not status["event_clusters_verified"]:
        status["errors"].append("final_synthesis_context_event_clusters_mismatch")


def _inspect_final_synthesis_semantic_retrieval_context(
    root: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    status: dict[str, Any],
) -> None:
    context = context_payload.get("additional_semantic_retrieval")
    if not isinstance(context, dict):
        status["semantic_retrieval_context_verified"] = False
        status["errors"].append(
            "final_synthesis_context_semantic_retrieval_context_invalid"
        )
        return

    semantic_rows = _read_final_synthesis_jsonl_context_rows(
        root,
        manifest.get("semantic_retrieval_artifact"),
        status,
        label="semantic_retrieval",
    )
    checks = {
        "semantic_retrieval_plan_artifact_verified": (
            context.get("plan_artifact")
            == manifest.get("semantic_retrieval_plan_artifact")
        ),
        "semantic_retrieval_artifact_verified": (
            context.get("artifact") == manifest.get("semantic_retrieval_artifact")
        ),
        "semantic_retrieval_summary_verified": (
            context.get("summary") == manifest.get("semantic_retrieval_summary")
        ),
        "semantic_retrieval_rows_verified": context.get("rows") == semantic_rows,
        "semantic_retrieval_excluded_ids_verified": (
            context.get("excluded_episode_ids")
            == manifest.get("excluded_semantic_retrieval_episode_ids")
        ),
    }
    status.update(checks)
    error_by_field = {
        "semantic_retrieval_plan_artifact_verified": (
            "final_synthesis_context_semantic_retrieval_plan_artifact_mismatch"
        ),
        "semantic_retrieval_artifact_verified": (
            "final_synthesis_context_semantic_retrieval_artifact_mismatch"
        ),
        "semantic_retrieval_summary_verified": (
            "final_synthesis_context_semantic_retrieval_summary_mismatch"
        ),
        "semantic_retrieval_rows_verified": (
            "final_synthesis_context_semantic_retrieval_rows_mismatch"
        ),
        "semantic_retrieval_excluded_ids_verified": (
            "final_synthesis_context_semantic_retrieval_excluded_ids_mismatch"
        ),
    }
    for field, error in error_by_field.items():
        if not status[field]:
            status["errors"].append(error)
    status["semantic_retrieval_context_verified"] = all(checks.values())


def _inspect_final_synthesis_web_research_context(
    root: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    status: dict[str, Any],
) -> None:
    context = context_payload.get("web_research")
    if not isinstance(context, dict):
        status["web_research_verified"] = False
        status["errors"].append("final_synthesis_context_web_research_invalid")
        return

    status["web_research_queries_verified"] = context.get("queries") == manifest.get(
        "web_queries"
    )
    if not status["web_research_queries_verified"]:
        status["errors"].append("final_synthesis_context_web_research_queries_mismatch")

    status["web_research_source_ids_verified"] = _same_unique_string_set(
        context.get("included_sources"),
        manifest.get("web_sources"),
    )
    if not status["web_research_source_ids_verified"]:
        status["errors"].append(
            "final_synthesis_context_web_research_source_ids_mismatch"
        )

    status["web_research_excluded_ids_verified"] = _same_unique_string_set(
        context.get("excluded_after_cutoff_source_ids"),
        manifest.get("excluded_web_source_ids"),
    )
    if not status["web_research_excluded_ids_verified"]:
        status["errors"].append(
            "final_synthesis_context_web_research_excluded_ids_mismatch"
        )

    web_source_rows = _read_web_source_context_rows(root, manifest, status)
    status["web_research_sources_verified"] = context.get("sources") == web_source_rows
    if not status["web_research_sources_verified"]:
        status["errors"].append("final_synthesis_context_web_research_sources_mismatch")

    status["web_research_verified"] = bool(
        status["web_research_queries_verified"]
        and status["web_research_source_ids_verified"]
        and status["web_research_sources_verified"]
        and status["web_research_excluded_ids_verified"]
    )


def _inspect_final_synthesis_candidate_context(
    root: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    status: dict[str, Any],
) -> None:
    _check_final_synthesis_object_context(
        root,
        manifest,
        context_payload,
        status,
        artifact_field="candidate_verification_artifact",
        payload_key="candidate_verification",
        status_key="candidate_verification_context_verified",
        label="candidate_verification",
        missing_expected={},
    )

    candidate_web_rows = (
        []
        if not isinstance(manifest.get("candidate_web_check_artifact"), str)
        and not _string_list(manifest.get("candidate_web_source_ids"))
        else _read_candidate_web_check_context_rows(
            root,
            manifest.get("candidate_web_check_artifact"),
            status,
        )
    )
    status["candidate_web_checks_context_verified"] = (
        context_payload.get("candidate_web_checks") == candidate_web_rows
    )
    if not status["candidate_web_checks_context_verified"]:
        status["errors"].append("final_synthesis_context_candidate_web_checks_mismatch")


def _inspect_final_synthesis_review_context(
    root: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    status: dict[str, Any],
) -> None:
    _check_final_synthesis_object_context(
        root,
        manifest,
        context_payload,
        status,
        artifact_field="news_novelty_review_artifact",
        payload_key="news_novelty_review",
        status_key="news_novelty_review_context_verified",
        label="news_novelty_review",
    )
    _check_final_synthesis_object_context(
        root,
        manifest,
        context_payload,
        status,
        artifact_field="candidate_expansion_artifact",
        payload_key="open_world_candidate_expansion",
        status_key="candidate_expansion_context_verified",
        label="candidate_expansion",
    )

    red_team_payload = _read_final_synthesis_red_team_context_object(
        root,
        manifest,
        status,
    )
    status["red_team_output_context_verified"] = (
        context_payload.get("red_team_output") == red_team_payload
    )
    if not status["red_team_output_context_verified"]:
        status["errors"].append("final_synthesis_context_red_team_output_mismatch")


def _check_final_synthesis_object_context(
    root: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    status: dict[str, Any],
    *,
    artifact_field: str,
    payload_key: str,
    status_key: str,
    label: str,
    missing_expected: dict[str, Any] | None = None,
) -> None:
    artifact_ref = manifest.get(artifact_field)
    expected = (
        missing_expected
        if not isinstance(artifact_ref, str) and missing_expected is not None
        else _read_final_synthesis_json_context_object(
            root,
            artifact_ref,
            status,
            label=label,
        )
    )
    status[status_key] = context_payload.get(payload_key) == expected
    if not status[status_key]:
        status["errors"].append(f"final_synthesis_context_{label}_mismatch")


def _read_final_synthesis_json_context_object(
    root: Path,
    artifact_ref: object,
    status: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(artifact_ref, str) or not artifact_ref:
        return {}
    payload = _read_artifact_object(root, artifact_ref, status)
    if payload is None:
        status["errors"].append(f"final_synthesis_context_{label}_unavailable")
        return {}
    return payload


def _read_final_synthesis_red_team_context_object(
    root: Path,
    manifest: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    artifact_refs = manifest.get("red_team_artifacts")
    if not (
        isinstance(artifact_refs, list)
        and len(artifact_refs) == 1
        and isinstance(artifact_refs[0], str)
        and artifact_refs[0]
    ):
        status["errors"].append("final_synthesis_context_red_team_artifact_unavailable")
        return {}
    return _read_final_synthesis_json_context_object(
        root,
        artifact_refs[0],
        status,
        label="red_team_output",
    )


def _read_final_synthesis_jsonl_context_rows(
    root: Path,
    artifact_ref: object,
    status: dict[str, Any],
    *,
    label: str,
) -> list[dict[str, Any]]:
    rows = _read_artifact_jsonl_rows(root, artifact_ref, status, label=label)
    if rows is None:
        status["errors"].append(f"final_synthesis_context_{label}_rows_unavailable")
        return []
    return rows


def _read_candidate_web_check_context_rows(
    root: Path,
    artifact_ref: object,
    status: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(artifact_ref, str) or not artifact_ref:
        return []
    rows = _read_artifact_jsonl_rows(
        root,
        artifact_ref,
        status,
        label="candidate_web_check",
    )
    if rows is None:
        status["errors"].append(
            "final_synthesis_context_candidate_web_check_rows_unavailable"
        )
        return []
    return [_candidate_web_check_context_row(row) for row in rows]


def _candidate_web_check_context_row(row: dict[str, Any]) -> dict[str, Any]:
    context_row = {
        "candidate_rank": row.get("candidate_rank"),
        "candidate_ticker": row.get("candidate_ticker"),
        "candidate_company_name": row.get("candidate_company_name"),
        "candidate_path_type": row.get("candidate_path_type"),
        "candidate_subject_type": row.get("candidate_subject_type"),
        "candidate_expansion_path": row.get("candidate_expansion_path"),
        "candidate_expansion_hypothesis": row.get("candidate_expansion_hypothesis"),
        "candidate_investigation_questions": row.get(
            "candidate_investigation_questions"
        ),
        "verification_focus": row.get("verification_focus"),
        "source_id": row.get("source_id"),
        "query": row.get("query"),
        "title": row.get("title"),
        "url": row.get("url"),
        "snippet": row.get("snippet"),
        "published_at": row.get("published_at"),
        "time_verified": row.get("time_verified"),
        "content_sha256": row.get("content_sha256"),
        "opened_text_excerpt": row.get("opened_text_excerpt"),
    }
    if "timestamp_precision" in row:
        context_row["timestamp_precision"] = row.get("timestamp_precision")
    return context_row


def _read_web_source_context_rows(
    root: Path,
    manifest: dict[str, Any],
    status: dict[str, Any],
) -> list[dict[str, Any]]:
    web_sources = _string_list(manifest.get("web_sources"))
    artifact_ref = manifest.get("web_source_artifact")
    if not web_sources and not isinstance(artifact_ref, str):
        return []
    rows = _read_artifact_jsonl_rows(
        root,
        artifact_ref,
        status,
        label="web_source",
    )
    if rows is None:
        status["errors"].append("final_synthesis_context_web_source_rows_unavailable")
        return []
    return [_web_source_context_row(row) for row in rows]


def _web_source_context_row(row: dict[str, Any]) -> dict[str, Any]:
    context_row = {
        "source_id": row.get("source_id"),
        "query": row.get("query"),
        "title": row.get("title"),
        "url": row.get("url"),
        "snippet": row.get("snippet"),
        "published_at": row.get("published_at"),
        "time_verified": row.get("time_verified"),
        "content_sha256": row.get("content_sha256"),
        "opened_text_excerpt": row.get("opened_text_excerpt"),
    }
    if "timestamp_precision" in row:
        context_row["timestamp_precision"] = row.get("timestamp_precision")
    return context_row


def _inspect_blind_seal_receipt_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="blind_seal_receipt_artifact",
        hash_field="blind_seal_receipt_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "phase_verified": None,
            "blind_artifact_hash_verified": None,
            "prediction_path_verified": None,
            "row_disposition_hash_verified": None,
            "source_ledger_hash_verified": None,
            "no_d_outcome_verified": None,
            "validation_counts_verified": None,
        }
    )
    payload = _read_artifact_object(
        root, manifest.get("blind_seal_receipt_artifact"), status
    )
    if payload is None:
        status["passed"] = _blind_seal_receipt_status_passed(status)
        return status

    status["schema_version_verified"] = (
        payload.get("schema_version") == "nslab.blind_seal_receipt.v1"
    )
    if not status["schema_version_verified"]:
        status["errors"].append("blind_seal_receipt_schema_version_mismatch")
    run_id = manifest.get("run_id")
    status["run_id_verified"] = not isinstance(run_id, str) or payload.get("run_id") == run_id
    if not status["run_id_verified"]:
        status["errors"].append("blind_seal_receipt_run_id_mismatch")
    status["phase_verified"] = payload.get("phase") == "BLIND_SEALED"
    if not status["phase_verified"]:
        status["errors"].append("blind_seal_receipt_phase_mismatch")
    status["blind_artifact_hash_verified"] = (
        payload.get("blind_artifact_sha256") == manifest.get("blind_artifact_sha256")
    )
    if not status["blind_artifact_hash_verified"]:
        status["errors"].append("blind_seal_receipt_blind_hash_mismatch")
    status["prediction_path_verified"] = (
        payload.get("blind_prediction_path") == manifest.get("prediction_artifact")
    )
    if not status["prediction_path_verified"]:
        status["errors"].append("blind_seal_receipt_prediction_path_mismatch")
    status["row_disposition_hash_verified"] = (
        payload.get("row_disposition_sha256") == manifest.get("row_disposition_sha256")
    )
    if not status["row_disposition_hash_verified"]:
        status["errors"].append("blind_seal_receipt_row_disposition_hash_mismatch")
    status["source_ledger_hash_verified"] = (
        payload.get("source_ledger_sha256") == manifest.get("source_ledger_sha256")
    )
    if not status["source_ledger_hash_verified"]:
        status["errors"].append("blind_seal_receipt_source_ledger_hash_mismatch")
    status["no_d_outcome_verified"] = (
        payload.get("no_d_outcome_exposed") is True
        and manifest.get("no_d_outcome_exposed") is True
    )
    if not status["no_d_outcome_verified"]:
        status["errors"].append("blind_seal_receipt_no_d_outcome_mismatch")
    validation = payload.get("validation")
    expected_counts = {
        "blind_web_search_call_count": manifest.get("blind_web_search_call_count"),
        "blind_price_repository_access_count": manifest.get(
            "blind_price_repository_access_count"
        ),
        "blind_current_price_access_count": manifest.get("blind_current_price_access_count"),
        "canonical_blind_hash_verified": True,
    }
    status["validation_counts_verified"] = (
        isinstance(validation, dict)
        and all(validation.get(key) == value for key, value in expected_counts.items())
    )
    if not status["validation_counts_verified"]:
        status["errors"].append("blind_seal_receipt_validation_counts_mismatch")
    status["passed"] = _blind_seal_receipt_status_passed(status)
    return status


def _inspect_phase_state_artifact(
    root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    status = _inspect_text_hashed_artifact(
        root,
        manifest,
        artifact_field="phase_state_artifact",
        hash_field="phase_state_sha256",
        required=True,
    )
    status.update(
        {
            "schema_version_verified": None,
            "run_id_verified": None,
            "phase_verified": None,
            "completed_phase_verified": None,
            "receipt_link_verified": None,
            "trade_date_verified": None,
            "cutoff_at_verified": None,
        }
    )
    payload = _read_artifact_object(root, manifest.get("phase_state_artifact"), status)
    if payload is None:
        status["passed"] = _phase_state_status_passed(status)
        return status

    status["schema_version_verified"] = payload.get("schema_version") == "nslab.phase_state.v1"
    if not status["schema_version_verified"]:
        status["errors"].append("phase_state_schema_version_mismatch")
    run_id = manifest.get("run_id")
    status["run_id_verified"] = not isinstance(run_id, str) or payload.get("run_id") == run_id
    if not status["run_id_verified"]:
        status["errors"].append("phase_state_run_id_mismatch")
    status["phase_verified"] = payload.get("phase") == "BLIND_SEALED"
    if not status["phase_verified"]:
        status["errors"].append("phase_state_phase_mismatch")
    completed_phases = _string_list(payload.get("completed_phases"))
    expected_phases = _phase_a_names(manifest.get("blind_context_mode"))
    status["completed_phase_verified"] = bool(
        expected_phases.intersection(completed_phases)
    )
    if not status["completed_phase_verified"]:
        status["errors"].append("phase_state_completed_phase_mismatch")
    status["receipt_link_verified"] = (
        payload.get("blind_seal_receipt_sha256")
        == manifest.get("blind_seal_receipt_sha256")
    )
    if not status["receipt_link_verified"]:
        status["errors"].append("phase_state_receipt_sha_mismatch")
    status["trade_date_verified"] = payload.get("trade_date") == manifest.get("trade_date")
    if not status["trade_date_verified"]:
        status["errors"].append("phase_state_trade_date_mismatch")
    status["cutoff_at_verified"] = payload.get("cutoff_at") == manifest.get("cutoff_at")
    if not status["cutoff_at_verified"]:
        status["errors"].append("phase_state_cutoff_at_mismatch")
    status["passed"] = _phase_state_status_passed(status)
    return status


def _inspect_red_team_artifacts(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    raw_artifacts = manifest.get("red_team_artifacts")
    prompt_hashes = manifest.get("prompt_hashes")
    expected_prompt_hash = (
        prompt_hashes.get("red_team_candidate_review")
        if isinstance(prompt_hashes, dict)
        else None
    )
    status: dict[str, Any] = {
        "configured": raw_artifacts is not None,
        "artifact_count": 0,
        "missing_files": [],
        "path_escape_errors": [],
        "invalid_json": [],
        "schema_mismatches": [],
        "run_id_mismatches": [],
        "prompt_hash_mismatches": [],
        "candidate_count_mismatches": [],
        "finding_count_mismatches": [],
        "required_attack_check_mismatches": [],
        "attack_check_coverage_mismatches": [],
        "passed_to_synthesis_failures": [],
        "path_within_project": None,
        "exists_verified": None,
        "metadata_verified": None,
        "candidate_count_verified": None,
        "finding_count_verified": None,
        "required_attack_checks_verified": None,
        "attack_check_coverage_verified": None,
        "passed_to_synthesis_verified": None,
        "summary_verified": None,
        "errors": [],
    }
    if raw_artifacts is None:
        status["errors"].append("red_team_artifacts_missing")
        status["passed"] = False
        return status
    if not isinstance(raw_artifacts, list) or not all(
        isinstance(item, str) and item for item in raw_artifacts
    ):
        status["errors"].append("red_team_artifacts_invalid")
        status["passed"] = False
        return status
    artifact_refs = [str(item) for item in raw_artifacts]
    status["artifact_count"] = len(artifact_refs)
    run_id = manifest.get("run_id")
    for artifact_ref in artifact_refs:
        artifact_path = _resolve_project_artifact(root, artifact_ref)
        if artifact_path is None:
            status["path_escape_errors"].append(artifact_ref)
            continue
        if not artifact_path.exists():
            status["missing_files"].append(artifact_ref)
            continue
        try:
            payload = read_json(artifact_path)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            status["invalid_json"].append(artifact_ref)
            continue
        if not isinstance(payload, dict):
            status["invalid_json"].append(artifact_ref)
            continue
        if payload.get("schema_version") != "nslab.red_team_artifact.v1":
            status["schema_mismatches"].append(artifact_ref)
        if isinstance(run_id, str) and payload.get("run_id") != run_id:
            status["run_id_mismatches"].append(artifact_ref)
        if (
            isinstance(expected_prompt_hash, str)
            and payload.get("prompt_sha256") != expected_prompt_hash
        ):
            status["prompt_hash_mismatches"].append(artifact_ref)
        _inspect_red_team_payload_contract(
            artifact_ref=artifact_ref,
            payload=payload,
            manifest=manifest,
            status=status,
        )
    status["path_within_project"] = not status["path_escape_errors"]
    status["exists_verified"] = status["path_within_project"] and not status["missing_files"]
    status["metadata_verified"] = (
        status["exists_verified"]
        and not status["invalid_json"]
        and not status["schema_mismatches"]
        and not status["run_id_mismatches"]
        and not status["prompt_hash_mismatches"]
    )
    status["candidate_count_verified"] = not status["candidate_count_mismatches"]
    status["finding_count_verified"] = not status["finding_count_mismatches"]
    status["required_attack_checks_verified"] = not status[
        "required_attack_check_mismatches"
    ]
    status["attack_check_coverage_verified"] = not status[
        "attack_check_coverage_mismatches"
    ]
    status["passed_to_synthesis_verified"] = not status[
        "passed_to_synthesis_failures"
    ]
    status["summary_verified"] = (
        status["candidate_count_verified"]
        and status["finding_count_verified"]
        and status["required_attack_checks_verified"]
        and status["attack_check_coverage_verified"]
        and status["passed_to_synthesis_verified"]
    )
    if status["path_escape_errors"]:
        status["errors"].append("red_team_artifact_path_escapes_project_root")
    if status["missing_files"]:
        status["errors"].append("red_team_artifact_missing_files")
    if status["invalid_json"]:
        status["errors"].append("red_team_artifact_invalid_json")
    if status["schema_mismatches"]:
        status["errors"].append("red_team_artifact_schema_mismatches")
    if status["run_id_mismatches"]:
        status["errors"].append("red_team_artifact_run_id_mismatches")
    if status["prompt_hash_mismatches"]:
        status["errors"].append("red_team_artifact_prompt_hash_mismatches")
    if status["candidate_count_mismatches"]:
        status["errors"].append("red_team_artifact_candidate_count_mismatches")
    if status["finding_count_mismatches"]:
        status["errors"].append("red_team_artifact_finding_count_mismatches")
    if status["required_attack_check_mismatches"]:
        status["errors"].append("red_team_artifact_required_attack_check_mismatches")
    if status["attack_check_coverage_mismatches"]:
        status["errors"].append("red_team_artifact_attack_check_coverage_mismatches")
    if status["passed_to_synthesis_failures"]:
        status["errors"].append("red_team_artifact_not_passed_to_synthesis")
    status["passed"] = _red_team_artifact_status_passed(status)
    return status


def _inspect_red_team_payload_contract(
    *,
    artifact_ref: str,
    payload: dict[str, Any],
    manifest: dict[str, Any],
    status: dict[str, Any],
) -> None:
    summary = manifest.get("red_team_summary")
    expected_candidate_count = (
        summary.get("candidate_count") if isinstance(summary, dict) else None
    )
    observed_candidate_count = _non_bool_int(payload.get("candidate_count"))
    if (
        not isinstance(expected_candidate_count, int)
        or observed_candidate_count != expected_candidate_count
    ):
        status["candidate_count_mismatches"].append(artifact_ref)

    candidate_findings = payload.get("candidate_findings")
    if not isinstance(candidate_findings, list) or not all(
        isinstance(finding, dict) for finding in candidate_findings
    ):
        status["finding_count_mismatches"].append(artifact_ref)
        status["attack_check_coverage_mismatches"].append(artifact_ref)
        status["passed_to_synthesis_failures"].append(artifact_ref)
        return

    expected_finding_count = summary.get("finding_count") if isinstance(summary, dict) else None
    if (
        not isinstance(expected_finding_count, int)
        or len(candidate_findings) != expected_finding_count
        or observed_candidate_count != len(candidate_findings)
    ):
        status["finding_count_mismatches"].append(artifact_ref)

    expected_required_checks = (
        _string_list(summary.get("required_attack_checks"))
        if isinstance(summary, dict)
        else []
    )
    observed_required_checks = _string_list(payload.get("required_attack_checks"))
    expected_required_count = (
        summary.get("required_attack_check_count")
        if isinstance(summary, dict)
        else None
    )
    if (
        not expected_required_checks
        or observed_required_checks != expected_required_checks
        or len(observed_required_checks) != expected_required_count
    ):
        status["required_attack_check_mismatches"].append(artifact_ref)

    for index, finding in enumerate(candidate_findings, start=1):
        attack_checks = finding.get("attack_checks")
        if not isinstance(attack_checks, list) or not all(
            isinstance(check, dict) for check in attack_checks
        ):
            status["attack_check_coverage_mismatches"].append(
                {"path": artifact_ref, "finding": index}
            )
            continue
        observed_names = [
            str(check["name"])
            for check in attack_checks
            if isinstance(check.get("name"), str)
        ]
        if observed_names != observed_required_checks:
            status["attack_check_coverage_mismatches"].append(
                {"path": artifact_ref, "finding": index}
            )
        if finding.get("passed_to_synthesis") is not True or any(
            check.get("passed_to_synthesis") is not True for check in attack_checks
        ):
            status["passed_to_synthesis_failures"].append(
                {"path": artifact_ref, "finding": index}
            )

    if not (isinstance(summary, dict) and summary.get("all_findings_passed_to_synthesis") is True):
        status["passed_to_synthesis_failures"].append(artifact_ref)


def _inspect_memory_sweep_artifacts(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    raw_artifacts = manifest.get("memory_sweep_artifacts")
    raw_hashes = manifest.get("memory_sweep_artifact_hashes")
    expected_shard_count = _optional_int(manifest.get("memory_sweep_shard_count"))
    expected_cache_hits = _optional_int(manifest.get("memory_sweep_cache_hits"))
    expected_swept_count = _optional_int(manifest.get("swept_episode_count"))
    expected_swept_ids = manifest.get("swept_episode_ids")
    status: dict[str, Any] = {
        "configured": raw_artifacts is not None,
        "artifact_count": 0,
        "hash_count": 0,
        "expected_shard_count": expected_shard_count,
        "expected_cache_hits": expected_cache_hits,
        "expected_swept_episode_count": expected_swept_count,
        "observed_cache_hits": 0,
        "observed_episode_ids": [],
        "duplicate_artifacts": [],
        "missing_hashes": [],
        "extra_hashes": [],
        "path_escape_errors": [],
        "missing_files": [],
        "hash_mismatches": [],
        "invalid_json": [],
        "schema_mismatches": [],
        "metadata_mismatches": [],
        "episode_count_mismatches": [],
        "source_hash_mismatches": [],
        "shard_hash_mismatches": [],
        "path_within_project": None,
        "exists_verified": None,
        "hashes_verified": None,
        "metadata_verified": None,
        "source_hashes_verified": None,
        "shard_count_verified": None,
        "cache_hits_verified": None,
        "swept_episode_ids_verified": None,
        "errors": [],
    }
    if raw_artifacts is None:
        status["errors"].append("memory_sweep_artifacts_missing")
        status["passed"] = False
        return status
    if not isinstance(raw_artifacts, list) or not all(
        isinstance(item, str) and item for item in raw_artifacts
    ):
        status["errors"].append("memory_sweep_artifacts_invalid")
        status["passed"] = False
        return status
    if not isinstance(raw_hashes, dict):
        raw_hashes = {}
        if raw_artifacts:
            status["errors"].append("memory_sweep_artifact_hashes_missing_or_invalid")
    elif any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in raw_hashes.items()
    ):
        raw_hashes = {}
        status["errors"].append("memory_sweep_artifact_hashes_invalid")

    artifact_refs = [str(item) for item in raw_artifacts]
    hash_refs = {str(key): str(value) for key, value in raw_hashes.items()}
    status["artifact_count"] = len(artifact_refs)
    status["hash_count"] = len(hash_refs)
    status["duplicate_artifacts"] = sorted(
        {artifact_ref for artifact_ref in artifact_refs if artifact_refs.count(artifact_ref) > 1}
    )
    artifact_ref_set = set(artifact_refs)
    hash_ref_set = set(hash_refs)
    status["missing_hashes"] = sorted(artifact_ref_set - hash_ref_set)
    status["extra_hashes"] = sorted(hash_ref_set - artifact_ref_set)

    observed_episode_ids: list[str] = []
    mode = manifest.get("mode")
    trade_date = manifest.get("trade_date")
    cutoff_at = manifest.get("cutoff_at")
    brain_version = manifest.get("brain_version")
    accepted_hashes = ResearchStore(root).accepted_hashes()
    for artifact_ref in artifact_refs:
        artifact_path = _resolve_project_artifact(root, artifact_ref)
        if artifact_path is None:
            status["path_escape_errors"].append(artifact_ref)
            continue
        if not artifact_path.exists():
            status["missing_files"].append(artifact_ref)
            continue
        expected_hash = hash_refs.get(artifact_ref)
        if expected_hash is not None and file_sha256(artifact_path) != expected_hash:
            status["hash_mismatches"].append(artifact_ref)
        try:
            payload = read_json(artifact_path)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            status["invalid_json"].append(artifact_ref)
            continue
        if not isinstance(payload, dict):
            status["invalid_json"].append(artifact_ref)
            continue
        if payload.get("schema_version") != "nslab.memory_sweep_contribution.v1":
            status["schema_mismatches"].append(artifact_ref)
        mismatched_fields = [
            field
            for field, expected in (
                ("mode", mode),
                ("trade_date", trade_date),
                ("cutoff_at", cutoff_at),
                ("brain_version", brain_version),
            )
            if expected is not None and payload.get(field) != expected
        ]
        if mismatched_fields:
            status["metadata_mismatches"].append(
                {"path": artifact_ref, "fields": mismatched_fields}
            )
        episode_ids = payload.get("episode_ids")
        if not isinstance(episode_ids, list) or not all(
            isinstance(episode_id, str) for episode_id in episode_ids
        ):
            status["episode_count_mismatches"].append(artifact_ref)
            continue
        observed_episode_ids.extend(episode_ids)
        if payload.get("episode_count") != len(episode_ids):
            status["episode_count_mismatches"].append(artifact_ref)
        source_hashes = _memory_sweep_source_hashes(
            payload.get("episode_shard_source_hashes"),
            episode_ids,
        )
        if source_hashes is None:
            status["source_hash_mismatches"].append(
                {"path": artifact_ref, "reason": "invalid_or_missing_source_hashes"}
            )
        else:
            expected_shard_hash = _memory_sweep_shard_hash(source_hashes)
            if payload.get("episode_shard_sha256") != expected_shard_hash:
                status["shard_hash_mismatches"].append(artifact_ref)
            for episode_id, recorded_hash in sorted(source_hashes.items()):
                actual_hash = accepted_hashes.get(episode_id)
                if actual_hash != recorded_hash:
                    status["source_hash_mismatches"].append(
                        {
                            "path": artifact_ref,
                            "episode_id": episode_id,
                            "expected": recorded_hash,
                            "actual": actual_hash,
                        }
                    )
        if payload.get("from_cache") is True:
            status["observed_cache_hits"] += 1

    status["observed_episode_ids"] = observed_episode_ids
    status["path_within_project"] = not status["path_escape_errors"]
    status["exists_verified"] = status["path_within_project"] and not status["missing_files"]
    status["hashes_verified"] = (
        status["exists_verified"]
        and not status["duplicate_artifacts"]
        and not status["missing_hashes"]
        and not status["extra_hashes"]
        and not status["hash_mismatches"]
    )
    status["metadata_verified"] = (
        status["exists_verified"]
        and not status["invalid_json"]
        and not status["schema_mismatches"]
        and not status["metadata_mismatches"]
        and not status["episode_count_mismatches"]
        and not status["source_hash_mismatches"]
        and not status["shard_hash_mismatches"]
    )
    status["source_hashes_verified"] = (
        status["exists_verified"]
        and not status["source_hash_mismatches"]
        and not status["shard_hash_mismatches"]
    )
    status["shard_count_verified"] = expected_shard_count == status["artifact_count"]
    status["cache_hits_verified"] = expected_cache_hits == status["observed_cache_hits"]
    if isinstance(expected_swept_ids, list) and all(
        isinstance(episode_id, str) for episode_id in expected_swept_ids
    ):
        status["swept_episode_ids_verified"] = (
            Counter(observed_episode_ids) == Counter(expected_swept_ids)
            and expected_swept_count == len(expected_swept_ids)
        )
    else:
        status["errors"].append("swept_episode_ids_invalid")
        status["swept_episode_ids_verified"] = False

    if status["duplicate_artifacts"]:
        status["errors"].append("memory_sweep_artifacts_duplicates")
    if status["path_escape_errors"]:
        status["errors"].append("memory_sweep_artifact_path_escapes_project_root")
    if status["missing_files"]:
        status["errors"].append("memory_sweep_artifact_missing_files")
    if status["missing_hashes"]:
        status["errors"].append("memory_sweep_artifact_hashes_missing_hashes")
    if status["extra_hashes"]:
        status["errors"].append("memory_sweep_artifact_hashes_extra_hashes")
    if status["hash_mismatches"]:
        status["errors"].append("memory_sweep_artifact_hashes_mismatches")
    if status["invalid_json"]:
        status["errors"].append("memory_sweep_artifact_invalid_json")
    if status["schema_mismatches"]:
        status["errors"].append("memory_sweep_artifact_schema_mismatches")
    if status["metadata_mismatches"]:
        status["errors"].append("memory_sweep_artifact_metadata_mismatches")
    if status["episode_count_mismatches"]:
        status["errors"].append("memory_sweep_artifact_episode_count_mismatches")
    if status["source_hash_mismatches"]:
        status["errors"].append("memory_sweep_artifact_source_hash_mismatches")
    if status["shard_hash_mismatches"]:
        status["errors"].append("memory_sweep_artifact_shard_hash_mismatches")
    if not status["shard_count_verified"]:
        status["errors"].append("memory_sweep_shard_count_mismatch")
    if not status["cache_hits_verified"]:
        status["errors"].append("memory_sweep_cache_hits_mismatch")
    if not status["swept_episode_ids_verified"]:
        status["errors"].append("memory_sweep_swept_episode_ids_mismatch")
    status["passed"] = _memory_sweep_status_passed(status)
    return status


def _inspect_record_sweep_artifacts(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    raw_artifacts = manifest.get("record_sweep_artifacts")
    raw_hashes = manifest.get("record_sweep_artifact_hashes")
    expected_shard_count = _optional_int(manifest.get("record_sweep_shard_count"))
    expected_cache_hits = _optional_int(manifest.get("record_sweep_cache_hits"))
    expected_swept_count = _optional_int(manifest.get("swept_record_count"))
    expected_swept_ids = manifest.get("swept_record_ids")
    status: dict[str, Any] = {
        "configured": raw_artifacts is not None,
        "artifact_count": 0,
        "hash_count": 0,
        "expected_shard_count": expected_shard_count,
        "expected_cache_hits": expected_cache_hits,
        "expected_swept_record_count": expected_swept_count,
        "observed_cache_hits": 0,
        "observed_record_ids": [],
        "duplicate_artifacts": [],
        "missing_hashes": [],
        "extra_hashes": [],
        "path_escape_errors": [],
        "missing_files": [],
        "hash_mismatches": [],
        "invalid_json": [],
        "schema_mismatches": [],
        "metadata_mismatches": [],
        "record_count_mismatches": [],
        "category_field_mismatches": [],
        "source_hash_mismatches": [],
        "shard_hash_mismatches": [],
        "path_within_project": None,
        "exists_verified": None,
        "hashes_verified": None,
        "metadata_verified": None,
        "source_hashes_verified": None,
        "shard_count_verified": None,
        "cache_hits_verified": None,
        "swept_record_ids_verified": None,
        "errors": [],
    }
    if raw_artifacts is None:
        status["errors"].append("record_sweep_artifacts_missing")
        status["passed"] = False
        return status
    if not isinstance(raw_artifacts, list) or not all(
        isinstance(item, str) and item for item in raw_artifacts
    ):
        status["errors"].append("record_sweep_artifacts_invalid")
        status["passed"] = False
        return status
    if not isinstance(raw_hashes, dict):
        raw_hashes = {}
        if raw_artifacts:
            status["errors"].append("record_sweep_artifact_hashes_missing_or_invalid")
    elif any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in raw_hashes.items()
    ):
        raw_hashes = {}
        status["errors"].append("record_sweep_artifact_hashes_invalid")

    artifact_refs = [str(item) for item in raw_artifacts]
    hash_refs = {str(key): str(value) for key, value in raw_hashes.items()}
    status["artifact_count"] = len(artifact_refs)
    status["hash_count"] = len(hash_refs)
    status["duplicate_artifacts"] = sorted(
        {artifact_ref for artifact_ref in artifact_refs if artifact_refs.count(artifact_ref) > 1}
    )
    artifact_ref_set = set(artifact_refs)
    hash_ref_set = set(hash_refs)
    status["missing_hashes"] = sorted(artifact_ref_set - hash_ref_set)
    status["extra_hashes"] = sorted(hash_ref_set - artifact_ref_set)

    observed_record_ids: list[str] = []
    mode = manifest.get("mode")
    trade_date = manifest.get("trade_date")
    cutoff_at = manifest.get("cutoff_at")
    brain_version = manifest.get("brain_version")
    records_by_id = {record.record_id: record for record in BrainRecordStore(root).list_records()}
    for artifact_ref in artifact_refs:
        artifact_path = _resolve_project_artifact(root, artifact_ref)
        if artifact_path is None:
            status["path_escape_errors"].append(artifact_ref)
            continue
        if not artifact_path.exists():
            status["missing_files"].append(artifact_ref)
            continue
        expected_hash = hash_refs.get(artifact_ref)
        if expected_hash is not None and file_sha256(artifact_path) != expected_hash:
            status["hash_mismatches"].append(artifact_ref)
        try:
            payload = read_json(artifact_path)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            status["invalid_json"].append(artifact_ref)
            continue
        if not isinstance(payload, dict):
            status["invalid_json"].append(artifact_ref)
            continue
        if payload.get("schema_version") != "nslab.record_memory_sweep_contribution.v1":
            status["schema_mismatches"].append(artifact_ref)
        mismatched_fields = [
            field
            for field, expected in (
                ("mode", mode),
                ("trade_date", trade_date),
                ("cutoff_at", cutoff_at),
                ("brain_version", brain_version),
            )
            if expected is not None and payload.get(field) != expected
        ]
        if mismatched_fields:
            status["metadata_mismatches"].append(
                {"path": artifact_ref, "fields": mismatched_fields}
            )
        record_ids = payload.get("record_ids")
        if not isinstance(record_ids, list) or not all(
            isinstance(record_id, str) for record_id in record_ids
        ):
            status["record_count_mismatches"].append(artifact_ref)
            continue
        observed_record_ids.extend(record_ids)
        if payload.get("record_count") != len(record_ids):
            status["record_count_mismatches"].append(artifact_ref)
        category_mismatches = _record_sweep_category_field_mismatches(
            payload,
            record_ids,
        )
        if category_mismatches:
            status["category_field_mismatches"].append(
                {"path": artifact_ref, "fields": category_mismatches}
            )
        source_hashes = _record_sweep_source_hashes(
            payload.get("record_shard_source_hashes"),
            record_ids,
        )
        if source_hashes is None:
            status["source_hash_mismatches"].append(
                {"path": artifact_ref, "reason": "invalid_or_missing_source_hashes"}
            )
        else:
            expected_shard_hash = _record_sweep_shard_hash(source_hashes)
            if payload.get("record_shard_sha256") != expected_shard_hash:
                status["shard_hash_mismatches"].append(artifact_ref)
            for record_id, recorded_hash in sorted(source_hashes.items()):
                record = records_by_id.get(record_id)
                actual_hash = record.normalized_payload_sha256 if record is not None else None
                if actual_hash != recorded_hash:
                    status["source_hash_mismatches"].append(
                        {
                            "path": artifact_ref,
                            "record_id": record_id,
                            "expected": recorded_hash,
                            "actual": actual_hash,
                        }
                    )
        if payload.get("from_cache") is True:
            status["observed_cache_hits"] += 1

    status["observed_record_ids"] = observed_record_ids
    status["path_within_project"] = not status["path_escape_errors"]
    status["exists_verified"] = status["path_within_project"] and not status["missing_files"]
    status["hashes_verified"] = (
        status["exists_verified"]
        and not status["duplicate_artifacts"]
        and not status["missing_hashes"]
        and not status["extra_hashes"]
        and not status["hash_mismatches"]
    )
    status["metadata_verified"] = (
        status["exists_verified"]
        and not status["invalid_json"]
        and not status["schema_mismatches"]
        and not status["metadata_mismatches"]
        and not status["record_count_mismatches"]
        and not status["category_field_mismatches"]
        and not status["source_hash_mismatches"]
        and not status["shard_hash_mismatches"]
    )
    status["source_hashes_verified"] = (
        status["exists_verified"]
        and not status["source_hash_mismatches"]
        and not status["shard_hash_mismatches"]
    )
    status["shard_count_verified"] = expected_shard_count == status["artifact_count"]
    status["cache_hits_verified"] = expected_cache_hits == status["observed_cache_hits"]
    if isinstance(expected_swept_ids, list) and all(
        isinstance(record_id, str) for record_id in expected_swept_ids
    ):
        status["swept_record_ids_verified"] = (
            Counter(observed_record_ids) == Counter(expected_swept_ids)
            and expected_swept_count == len(expected_swept_ids)
        )
    else:
        status["errors"].append("swept_record_ids_invalid")
        status["swept_record_ids_verified"] = False

    if status["duplicate_artifacts"]:
        status["errors"].append("record_sweep_artifacts_duplicates")
    if status["path_escape_errors"]:
        status["errors"].append("record_sweep_artifact_path_escapes_project_root")
    if status["missing_files"]:
        status["errors"].append("record_sweep_artifact_missing_files")
    if status["missing_hashes"]:
        status["errors"].append("record_sweep_artifact_hashes_missing_hashes")
    if status["extra_hashes"]:
        status["errors"].append("record_sweep_artifact_hashes_extra_hashes")
    if status["hash_mismatches"]:
        status["errors"].append("record_sweep_artifact_hashes_mismatches")
    if status["invalid_json"]:
        status["errors"].append("record_sweep_artifact_invalid_json")
    if status["schema_mismatches"]:
        status["errors"].append("record_sweep_artifact_schema_mismatches")
    if status["metadata_mismatches"]:
        status["errors"].append("record_sweep_artifact_metadata_mismatches")
    if status["record_count_mismatches"]:
        status["errors"].append("record_sweep_artifact_record_count_mismatches")
    if status["category_field_mismatches"]:
        status["errors"].append("record_sweep_artifact_category_field_mismatches")
    if status["source_hash_mismatches"]:
        status["errors"].append("record_sweep_artifact_source_hash_mismatches")
    if status["shard_hash_mismatches"]:
        status["errors"].append("record_sweep_artifact_shard_hash_mismatches")
    if not status["shard_count_verified"]:
        status["errors"].append("record_sweep_shard_count_mismatch")
    if not status["cache_hits_verified"]:
        status["errors"].append("record_sweep_cache_hits_mismatch")
    if not status["swept_record_ids_verified"]:
        status["errors"].append("record_sweep_swept_record_ids_mismatch")
    status["passed"] = _record_sweep_status_passed(status)
    return status


def _memory_sweep_source_hashes(
    value: object,
    episode_ids: list[str],
) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        return None
    hashes = {str(key): str(item) for key, item in value.items()}
    if sorted(hashes) != sorted(episode_ids):
        return None
    return hashes


def _memory_sweep_shard_hash(source_hashes: dict[str, str]) -> str:
    return sha256_text(
        canonical_json(
            [
                {"episode_id": episode_id, "source_sha256": source_hash}
                for episode_id, source_hash in sorted(source_hashes.items())
            ]
        )
    )


def _record_sweep_source_hashes(
    value: object,
    record_ids: list[str],
) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        return None
    hashes = {str(key): str(item) for key, item in value.items()}
    if sorted(hashes) != sorted(record_ids):
        return None
    return hashes


def _record_sweep_category_field_mismatches(
    payload: dict[str, Any],
    record_ids: list[str],
) -> list[str]:
    allowed_record_ids = set(record_ids)
    mismatches: list[str] = []
    for field in (
        "positive_analogs",
        "negative_analogs",
        "negative_controls",
        "near_misses",
        "counterexamples",
        "leader_selection_pairs",
        "theme_formation_failures",
        "candidate_generation_errors",
    ):
        value = payload.get(field)
        if not isinstance(value, list):
            mismatches.append(field)
            continue
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("record_id"), str):
                mismatches.append(field)
                break
            if item["record_id"] not in allowed_record_ids:
                mismatches.append(field)
                break
    return mismatches


def _record_sweep_shard_hash(source_hashes: dict[str, str]) -> str:
    return sha256_text(
        canonical_json(
            [
                {"record_id": record_id, "source_sha256": source_hash}
                for record_id, source_hash in sorted(source_hashes.items())
            ]
        )
    )


_CONTEXT_PROMPT_TRACE_PURPOSES = {
    "open_world_first_analysis": "open_world_first_analysis",
    "news_novelty_review": "news_novelty_review",
    "semantic_retrieval_plan": "semantic_retrieval_plan",
    "candidate_expansion": "candidate_expansion",
    "blind_analysis": "daily_blind_analysis",
    "red_team_candidate_review": "red_team_candidate_review",
    "final_synthesis": "final_synthesis",
}

_CONTEXT_PROMPT_TRACE_TOKEN_KEYS = {
    "open_world_first_analysis": "open_world_first_analysis_prompt",
    "news_novelty_review": "news_novelty_review_prompt",
    "semantic_retrieval_plan": "semantic_retrieval_plan_prompt",
    "candidate_expansion": "candidate_expansion_prompt",
    "blind_analysis": "blind_analysis_prompt",
    "red_team_candidate_review": "red_team_prompt",
    "final_synthesis": "final_synthesis_prompt",
}

_TRACE_MODEL_CONFIG_KEYS = (
    "configured_provider",
    "provider_class",
    "model",
    "embedding_model",
    "max_concurrency",
    "shard_episode_count",
)


def _inspect_llm_traces(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    prompt_hashes = manifest.get("prompt_hashes")
    trace_dir = root / "runs" / "traces"
    trace_records, invalid_trace_files = _load_llm_trace_records(root, trace_dir)
    status: dict[str, Any] = {
        "configured": isinstance(prompt_hashes, dict),
        "trace_dir": _display_path(root, trace_dir),
        "trace_dir_exists": trace_dir.exists(),
        "trace_file_count": len(trace_records) + len(invalid_trace_files),
        "invalid_trace_files": invalid_trace_files,
        "expected_prompt_count": len(_CONTEXT_PROMPT_TRACE_PURPOSES),
        "matched_prompt_count": 0,
        "purposes": {},
        "errors": [],
    }
    if not isinstance(prompt_hashes, dict):
        status["errors"].append("prompt_hashes_missing_or_invalid")
        status["passed"] = False
        return status
    if not trace_dir.exists():
        status["errors"].append("llm_trace_dir_missing")

    purpose_statuses = {
        purpose: _inspect_prompt_trace(
            root,
            manifest,
            prompt_hashes,
            trace_records,
            hash_key=hash_key,
            purpose=purpose,
        )
        for hash_key, purpose in _CONTEXT_PROMPT_TRACE_PURPOSES.items()
    }
    status["purposes"] = purpose_statuses
    status["matched_prompt_count"] = sum(
        1
        for purpose_status in purpose_statuses.values()
        if purpose_status.get("matching_trace_count", 0) > 0
    )
    status["passed"] = (
        status["configured"]
        and status["trace_dir_exists"]
        and all(_prompt_trace_status_passed(item) for item in purpose_statuses.values())
        and not status["errors"]
    )
    return status


def _load_llm_trace_records(
    root: Path,
    trace_dir: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    trace_records: list[dict[str, Any]] = []
    invalid_trace_files: list[str] = []
    if not trace_dir.exists():
        return trace_records, invalid_trace_files
    for trace_path in sorted(trace_dir.glob("*.json")):
        trace_ref = _display_path(root, trace_path)
        try:
            payload = read_json(trace_path)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            invalid_trace_files.append(trace_ref)
            continue
        if not isinstance(payload, dict):
            invalid_trace_files.append(trace_ref)
            continue
        trace_records.append({"path": trace_path, "path_ref": trace_ref, "payload": payload})
    return trace_records, invalid_trace_files


def _inspect_prompt_trace(
    root: Path,
    manifest: dict[str, Any],
    prompt_hashes: dict[str, Any],
    trace_records: list[dict[str, Any]],
    *,
    hash_key: str,
    purpose: str,
) -> dict[str, Any]:
    expected_prompt_hash = prompt_hashes.get(hash_key)
    token_count_key = _CONTEXT_PROMPT_TRACE_TOKEN_KEYS.get(hash_key)
    expected_prompt_tokens = _manifest_prompt_token_count(manifest, token_count_key)
    expected_model_config = _expected_trace_model_config(manifest)
    status: dict[str, Any] = {
        "configured": isinstance(expected_prompt_hash, str) and bool(expected_prompt_hash),
        "manifest_hash_key": hash_key,
        "manifest_token_count_key": token_count_key,
        "purpose": purpose,
        "expected_prompt_sha256": expected_prompt_hash
        if isinstance(expected_prompt_hash, str)
        else None,
        "expected_prompt_tokens_estimate": expected_prompt_tokens,
        "matching_trace_count": 0,
        "matching_trace_ids": [],
        "matching_trace_paths": [],
        "trace_payloads_valid": None,
        "model_config_verified": None,
        "model_config_comparison": None,
        "model_config_mismatches": [],
        "token_counts_verified": None,
        "token_count_mismatches": [],
        "trace_validation_errors": {},
        "errors": [],
    }
    if not status["configured"]:
        status["errors"].append(f"{hash_key}_prompt_hash_missing")
        status["passed"] = False
        return status
    if expected_model_config is None:
        status["errors"].append("manifest_model_config_missing_or_invalid")
    if expected_prompt_tokens is None:
        status["errors"].append("manifest_prompt_token_count_missing_or_invalid")

    for trace_record in trace_records:
        payload = trace_record["payload"]
        if not _trace_matches_prompt(payload, purpose, str(expected_prompt_hash)):
            continue
        trace_path = trace_record["path"]
        trace_ref = str(trace_record["path_ref"])
        trace_id = payload.get("trace_id")
        status["matching_trace_count"] += 1
        status["matching_trace_paths"].append(trace_ref)
        status["matching_trace_ids"].append(trace_id if isinstance(trace_id, str) else None)
        validation_errors = _llm_trace_payload_errors(payload)
        if validation_errors:
            status["trace_validation_errors"][trace_ref] = validation_errors
        if expected_model_config:
            mismatched_keys = _trace_model_config_mismatches(
                payload,
                expected_model_config,
            )
            if mismatched_keys:
                status["model_config_mismatches"].append(
                    {
                        "path": _display_path(root, trace_path),
                        "keys": mismatched_keys,
                    }
                )
        token_count_mismatch = _trace_prompt_token_count_mismatch(
            payload,
            expected_prompt_tokens,
        )
        if token_count_mismatch:
            status["token_count_mismatches"].append(
                {
                    "path": _display_path(root, trace_path),
                    "manifest_token_count_key": token_count_key,
                    **token_count_mismatch,
                }
            )

    if status["matching_trace_count"] == 0:
        status["errors"].append("matching_trace_missing")
    status["trace_payloads_valid"] = (
        status["matching_trace_count"] > 0 and not status["trace_validation_errors"]
    )
    if expected_model_config == {}:
        status["model_config_comparison"] = "skipped_no_comparable_manifest_keys"
        status["model_config_verified"] = status["matching_trace_count"] > 0
    else:
        status["model_config_comparison"] = "verified"
        status["model_config_verified"] = (
            expected_model_config is not None
            and status["matching_trace_count"] > 0
            and not status["model_config_mismatches"]
        )
    status["token_counts_verified"] = (
        expected_prompt_tokens is not None
        and status["matching_trace_count"] > 0
        and not status["token_count_mismatches"]
    )
    if not status["trace_payloads_valid"]:
        status["errors"].append("matching_trace_payload_invalid")
    if not status["model_config_verified"]:
        status["errors"].append("matching_trace_model_config_mismatch")
    if (
        not status["token_counts_verified"]
        and "manifest_prompt_token_count_missing_or_invalid" not in status["errors"]
    ):
        status["errors"].append("matching_trace_token_count_mismatch")
    status["passed"] = _prompt_trace_status_passed(status)
    return status


def _trace_matches_prompt(
    payload: dict[str, Any],
    purpose: str,
    expected_prompt_hash: str,
) -> bool:
    trace_input = payload.get("input")
    return (
        payload.get("purpose") == purpose
        and isinstance(trace_input, dict)
        and trace_input.get("prompt_sha256") == expected_prompt_hash
    )


def _expected_trace_model_config(manifest: dict[str, Any]) -> dict[str, Any] | None:
    model_config = manifest.get("model_config")
    if not isinstance(model_config, dict) or not model_config:
        return None
    return {
        key: model_config[key] for key in _TRACE_MODEL_CONFIG_KEYS if key in model_config
    }


def _trace_model_config_mismatches(
    payload: dict[str, Any],
    expected_model_config: dict[str, Any],
) -> list[str]:
    trace_model_config = payload.get("model_config")
    if not isinstance(trace_model_config, dict):
        return ["model_config"]
    return [
        key
        for key, expected_value in expected_model_config.items()
        if trace_model_config.get(key) != expected_value
    ]


def _manifest_prompt_token_count(
    manifest: dict[str, Any],
    token_count_key: str | None,
) -> int | None:
    token_counts = manifest.get("token_counts")
    if token_count_key is None or not isinstance(token_counts, dict):
        return None
    count = token_counts.get(token_count_key)
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    return None


def _trace_prompt_token_count_mismatch(
    payload: dict[str, Any],
    expected_prompt_tokens: int | None,
) -> dict[str, Any] | None:
    trace_input = payload.get("input")
    token_usage = payload.get("token_usage")
    observed_prompt_tokens = (
        token_usage.get("prompt_tokens_estimate")
        if isinstance(token_usage, dict)
        else None
    )
    prompt_chars = trace_input.get("prompt_chars") if isinstance(trace_input, dict) else None
    prompt_chars_token_estimate = _estimate_prompt_tokens_from_chars(prompt_chars)
    reasons: list[str] = []
    if not isinstance(observed_prompt_tokens, int) or isinstance(
        observed_prompt_tokens, bool
    ):
        reasons.append("prompt_tokens_estimate_missing_or_invalid")
    else:
        if (
            expected_prompt_tokens is not None
            and observed_prompt_tokens != expected_prompt_tokens
        ):
            reasons.append("manifest_token_count_mismatch")
        if (
            prompt_chars_token_estimate is not None
            and observed_prompt_tokens != prompt_chars_token_estimate
        ):
            reasons.append("prompt_chars_token_count_mismatch")
    if prompt_chars_token_estimate is None:
        reasons.append("prompt_chars_missing_or_invalid")
    if not reasons:
        return None
    return {
        "reasons": reasons,
        "expected_prompt_tokens_estimate": expected_prompt_tokens,
        "observed_prompt_tokens_estimate": observed_prompt_tokens
        if isinstance(observed_prompt_tokens, int)
        and not isinstance(observed_prompt_tokens, bool)
        else None,
        "prompt_chars": prompt_chars
        if isinstance(prompt_chars, int) and not isinstance(prompt_chars, bool)
        else None,
        "prompt_chars_token_estimate": prompt_chars_token_estimate,
    }


def _estimate_prompt_tokens_from_chars(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return max(1, value // 4) if value else 0


def _llm_trace_payload_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version is not None and schema_version != "nslab.llm_trace.v1":
        errors.append("schema_version_invalid")
    operation = _trace_string_field(payload, "operation", errors)
    status = _trace_string_field(payload, "status", errors)
    _trace_string_field(payload, "trace_id", errors)
    _trace_string_field(payload, "purpose", errors)
    _trace_string_field(payload, "provider", errors)
    _trace_string_field(payload, "started_at", errors)
    _trace_string_field(payload, "finished_at", errors)
    if operation in {"generate_text", "generate_structured"}:
        _trace_string_field(payload, "prompt_version", errors)
    metadata = payload.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            errors.append("metadata_not_object")
        elif (
            operation in {"generate_text", "generate_structured"}
            and "prompt_version" in metadata
            and payload.get("prompt_version") != metadata.get("prompt_version")
        ):
            errors.append("metadata_prompt_version_mismatch")
    if not isinstance(payload.get("model_config"), dict) or not payload.get("model_config"):
        errors.append("model_config_missing_or_invalid")
    trace_input = payload.get("input")
    if not isinstance(trace_input, dict):
        errors.append("input_not_object")
    else:
        expected_input_hash = sha256_text(canonical_json(trace_input))
        if payload.get("input_sha256") != expected_input_hash:
            errors.append("input_sha256_mismatch")
        if operation in {"generate_text", "generate_structured"} and not isinstance(
            trace_input.get("prompt_sha256"), str
        ):
            errors.append("prompt_sha256_missing")
        if operation == "embed" and not isinstance(trace_input.get("texts_sha256"), str):
            errors.append("texts_sha256_missing")
    output = payload.get("output")
    expected_output_hash = sha256_text(canonical_json(output)) if output is not None else None
    if payload.get("output_sha256") != expected_output_hash:
        errors.append("output_sha256_mismatch")
    if status in {"ok", "checkpoint_hit"} and output is None:
        errors.append("successful_trace_missing_output")
    if operation == "embed":
        errors.extend(_embedding_trace_output_errors(output))
    if not isinstance(payload.get("tool_calls"), list):
        errors.append("tool_calls_not_list")
    retries = payload.get("retries")
    if not isinstance(retries, int) or isinstance(retries, bool):
        errors.append("retries_not_integer")
        retries = None
    errors.extend(_retry_error_history_errors(payload.get("retry_errors"), retries))
    token_usage = payload.get("token_usage")
    if not isinstance(token_usage, dict):
        errors.append("token_usage_not_object")
    else:
        if status in {"ok", "checkpoint_hit"} and not isinstance(
            token_usage.get("prompt_tokens_estimate"), int
        ):
            errors.append("prompt_token_estimate_missing")
        if (
            status in {"ok", "checkpoint_hit"}
            and operation in {"generate_text", "generate_structured"}
            and not isinstance(token_usage.get("completion_tokens_estimate"), int)
        ):
            errors.append("completion_token_estimate_missing")
    if status == "error" and not isinstance(payload.get("error"), dict):
        errors.append("error_trace_missing_error_details")
    return errors


def _retry_error_history_errors(value: object, retries: int | None) -> list[str]:
    errors: list[str] = []
    if value is None:
        if retries and retries > 0:
            errors.append("retry_errors_missing")
        return errors
    if not isinstance(value, list):
        return ["retry_errors_not_list"]
    if retries is not None and retries > 0 and len(value) != retries:
        errors.append("retry_errors_count_mismatch")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"retry_errors_{index}_not_object")
            continue
        if not isinstance(item.get("type"), str) or not item.get("type"):
            errors.append(f"retry_errors_{index}_type_missing")
        if not isinstance(item.get("message"), str):
            errors.append(f"retry_errors_{index}_message_missing")
    return errors


def _embedding_trace_output_errors(output: object) -> list[str]:
    if not isinstance(output, dict):
        return ["embed_output_summary_not_object"]
    errors: list[str] = []
    vector_count = output.get("vector_count")
    dimensions = output.get("dimensions")
    vectors_sha256 = output.get("vectors_sha256")
    if not isinstance(vector_count, int) or isinstance(vector_count, bool) or vector_count < 0:
        errors.append("embed_output_vector_count_invalid")
    if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 0:
        errors.append("embed_output_dimensions_invalid")
    if not isinstance(vectors_sha256, str) or not vectors_sha256:
        errors.append("embed_output_vectors_sha256_missing")
    return errors


def _trace_string_field(
    payload: dict[str, Any],
    field: str,
    errors: list[str],
) -> str | None:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        errors.append(f"{field}_missing")
        return None
    return value


def _inspect_context_file_group(
    root: Path,
    manifest: dict[str, Any],
    *,
    files_field: str,
    hashes_field: str,
) -> dict[str, Any]:
    raw_files = manifest.get(files_field)
    raw_hashes = manifest.get(hashes_field)
    status: dict[str, Any] = {
        "configured": raw_files is not None or raw_hashes is not None,
        "files_field": files_field,
        "hashes_field": hashes_field,
        "file_count": 0,
        "hash_count": 0,
        "missing_hashes": [],
        "extra_hashes": [],
        "duplicate_files": [],
        "missing_files": [],
        "hash_mismatches": [],
        "path_escape_errors": [],
        "path_within_project": None,
        "exists_verified": None,
        "hashes_verified": None,
        "errors": [],
    }
    if not status["configured"]:
        status["errors"].append(f"{files_field}_missing")
        return status
    if not isinstance(raw_files, list) or not all(
        isinstance(item, str) and item for item in raw_files
    ):
        status["errors"].append(f"{files_field}_invalid")
        return status
    if not isinstance(raw_hashes, dict):
        status["errors"].append(f"{hashes_field}_invalid")
        return status
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in raw_hashes.items()
    ):
        status["errors"].append(f"{hashes_field}_invalid")
        return status

    file_refs = [str(item) for item in raw_files]
    hash_refs = {str(key): str(value) for key, value in raw_hashes.items()}
    file_ref_set = set(file_refs)
    hash_ref_set = set(hash_refs)
    status["file_count"] = len(file_refs)
    status["hash_count"] = len(hash_refs)
    status["duplicate_files"] = sorted(
        {file_ref for file_ref in file_refs if file_refs.count(file_ref) > 1}
    )
    status["missing_hashes"] = sorted(file_ref_set - hash_ref_set)
    status["extra_hashes"] = sorted(hash_ref_set - file_ref_set)

    for file_ref in file_refs:
        artifact_path = _resolve_project_artifact(root, file_ref)
        if artifact_path is None:
            status["path_escape_errors"].append(file_ref)
            continue
        if not artifact_path.exists():
            status["missing_files"].append(file_ref)
            continue
        expected_hash = hash_refs.get(file_ref)
        if expected_hash is not None and file_sha256(artifact_path) != expected_hash:
            status["hash_mismatches"].append(file_ref)

    status["path_within_project"] = not status["path_escape_errors"]
    status["exists_verified"] = status["path_within_project"] and not status["missing_files"]
    status["hashes_verified"] = (
        status["exists_verified"]
        and not status["duplicate_files"]
        and not status["missing_hashes"]
        and not status["extra_hashes"]
        and not status["hash_mismatches"]
    )
    if status["duplicate_files"]:
        status["errors"].append(f"{files_field}_duplicates")
    if status["path_escape_errors"]:
        status["errors"].append(f"{files_field}_path_escapes_project_root")
    if status["missing_files"]:
        status["errors"].append(f"{files_field}_missing_files")
    if status["missing_hashes"]:
        status["errors"].append(f"{hashes_field}_missing_hashes")
    if status["extra_hashes"]:
        status["errors"].append(f"{hashes_field}_extra_hashes")
    if status["hash_mismatches"]:
        status["errors"].append(f"{hashes_field}_mismatches")
    return status


def _inspect_news_input(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    status = _base_artifact_status(root, manifest.get("news_file"))
    expected_hash = manifest.get("news_sha256")
    manifest_trade_date = _manifest_date(manifest.get("trade_date"))
    manifest_cutoff_at = _manifest_datetime(manifest.get("cutoff_at"))
    news_window_start_at = _manifest_datetime(manifest.get("news_window_start_at"))
    news_window_end_at = _manifest_datetime(manifest.get("news_window_end_at"))
    row_summary = manifest.get("row_disposition_summary")
    status["expected_sha256"] = expected_hash if isinstance(expected_hash, str) else None
    status["expected_row_count"] = _optional_int(manifest.get("news_row_count"))
    status["expected_included_row_count"] = _optional_int(
        manifest.get("included_news_row_count")
    )
    status["expected_excluded_row_count"] = _optional_int(
        manifest.get("excluded_news_row_count")
    )
    status["expected_news_window_start_at"] = (
        news_window_start_at.isoformat() if news_window_start_at else None
    )
    status["expected_news_window_end_at"] = (
        news_window_end_at.isoformat() if news_window_end_at else None
    )
    status["expected_missing_collected_at"] = (
        row_summary.get("missing_collected_at") if isinstance(row_summary, dict) else None
    )
    status["observed_row_count"] = None
    status["observed_included_row_count"] = None
    status["observed_excluded_row_count"] = None
    status["observed_missing_collected_at"] = None
    status["default_news_window_start_at"] = (
        default_news_window_start(manifest_trade_date).isoformat()
        if manifest_trade_date is not None
        else None
    )
    status["row_count_verified"] = None
    status["row_count_partition_verified"] = None
    status["included_row_count_verified"] = None
    status["excluded_row_count_verified"] = None
    status["missing_collected_at_verified"] = None
    status["news_window_start_verified"] = None
    status["news_window_end_verified"] = None
    status["news_window_counts_verified"] = None
    if not status["configured"]:
        status["errors"].append("news_file_missing")
        return status
    if news_window_start_at is None:
        status["errors"].append("news_window_start_at_missing_or_invalid")
    if news_window_end_at is None:
        status["errors"].append("news_window_end_at_missing_or_invalid")
    if manifest_trade_date is None:
        status["errors"].append("trade_date_missing_or_invalid")
    if manifest_cutoff_at is None:
        status["errors"].append("cutoff_at_missing_or_invalid")
    artifact_path = status.pop("_artifact_path", None)
    if not isinstance(artifact_path, Path) or not status["exists"]:
        return status
    observed_hash = file_sha256(artifact_path)
    status["observed_sha256"] = observed_hash
    status["hash_verified"] = observed_hash == expected_hash
    try:
        batch = load_news_csv(artifact_path, trade_date=manifest_trade_date)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        status["errors"].append(f"news_file_invalid_csv:{type(exc).__name__}")
        return status
    observed_row_count = batch.row_count
    status["observed_row_count"] = observed_row_count
    status["observed_missing_collected_at"] = sum(
        1 for item in batch.items if item.collected_at is None
    )
    status["row_count_verified"] = observed_row_count == status["expected_row_count"]
    expected_included = status["expected_included_row_count"]
    expected_excluded = status["expected_excluded_row_count"]
    if isinstance(expected_included, int) and isinstance(expected_excluded, int):
        status["row_count_partition_verified"] = (
            expected_included + expected_excluded == status["expected_row_count"]
        )
    if (
        news_window_start_at is not None
        and news_window_end_at is not None
        and manifest_trade_date is not None
        and manifest_cutoff_at is not None
    ):
        observed_included = sum(
            1 for item in batch.items if news_window_start_at <= item.published_at <= news_window_end_at
        )
        observed_excluded = observed_row_count - observed_included
        status["observed_included_row_count"] = observed_included
        status["observed_excluded_row_count"] = observed_excluded
        status["included_row_count_verified"] = observed_included == expected_included
        status["excluded_row_count_verified"] = observed_excluded == expected_excluded
        status["news_window_start_verified"] = (
            news_window_start_at == default_news_window_start(manifest_trade_date)
        )
        status["news_window_end_verified"] = news_window_end_at == manifest_cutoff_at
        status["news_window_counts_verified"] = (
            status["included_row_count_verified"] and status["excluded_row_count_verified"]
        )
    expected_missing_collected_at = status["expected_missing_collected_at"]
    if isinstance(expected_missing_collected_at, int):
        status["missing_collected_at_verified"] = (
            status["observed_missing_collected_at"] == expected_missing_collected_at
        )
    return status


def _manifest_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _manifest_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def _inspect_prediction_artifact(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    status = _base_artifact_status(root, manifest.get("prediction_artifact"))
    expected_hash = manifest.get("prediction_sha256")
    status["expected_sha256"] = expected_hash if isinstance(expected_hash, str) else None
    status["schema_version_verified"] = False
    status["sealed_at_verified"] = False
    status["blind_artifact_hash_verified"] = False
    status["manifest_blind_hash_verified"] = False
    if not status["configured"]:
        return status
    artifact_path = status.pop("_artifact_path", None)
    if not isinstance(artifact_path, Path) or not status["exists"]:
        return status
    observed_hash = file_sha256(artifact_path)
    status["observed_sha256"] = observed_hash
    status["hash_verified"] = observed_hash == expected_hash
    try:
        payload = read_json(artifact_path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        status["errors"].append("prediction_artifact_invalid_json")
        status["context_manifest_id_verified"] = False
        return status
    if not isinstance(payload, dict):
        status["errors"].append("prediction_artifact_not_object")
        status["context_manifest_id_verified"] = False
        return status
    run_id = manifest.get("run_id")
    context_manifest_id = payload.get("context_manifest_id")
    status["context_manifest_id"] = (
        context_manifest_id if isinstance(context_manifest_id, str) else None
    )
    status["context_manifest_id_verified"] = (
        isinstance(run_id, str) and context_manifest_id == run_id
    )
    status["schema_version_verified"] = (
        payload.get("schema_version") == "nslab.blind_prediction.v1"
    )
    if not status["schema_version_verified"]:
        status["errors"].append("prediction_artifact_schema_version_mismatch")
    sealed_at = payload.get("sealed_at")
    status["sealed_at_verified"] = isinstance(sealed_at, str) and bool(sealed_at)
    if not status["sealed_at_verified"]:
        status["errors"].append("prediction_artifact_sealed_at_missing")
    observed_blind_hash = payload.get("blind_artifact_sha256")
    status["blind_artifact_sha256"] = (
        observed_blind_hash if isinstance(observed_blind_hash, str) else None
    )
    prediction_for_hash = {**payload, "blind_artifact_sha256": None}
    expected_blind_hash = sha256_text(canonical_json(prediction_for_hash))
    status["expected_blind_artifact_sha256"] = expected_blind_hash
    status["blind_artifact_hash_verified"] = observed_blind_hash == expected_blind_hash
    if not status["blind_artifact_hash_verified"]:
        status["errors"].append("prediction_artifact_blind_hash_mismatch")
    manifest_blind_hash = manifest.get("blind_artifact_sha256")
    status["manifest_blind_artifact_sha256"] = (
        manifest_blind_hash if isinstance(manifest_blind_hash, str) else None
    )
    status["manifest_blind_hash_verified"] = observed_blind_hash == manifest_blind_hash
    if not status["manifest_blind_hash_verified"]:
        status["errors"].append("prediction_artifact_manifest_blind_hash_mismatch")
    return status


def _inspect_report_artifact(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    status = _base_artifact_status(root, manifest.get("report_artifact"))
    expected_hash = manifest.get("report_sha256")
    status["expected_sha256"] = expected_hash if isinstance(expected_hash, str) else None
    if not status["configured"]:
        return status
    artifact_path = status.pop("_artifact_path", None)
    if not isinstance(artifact_path, Path) or not status["exists"]:
        return status
    report_text = artifact_path.read_text(encoding="utf-8", errors="replace")
    observed_hash = sha256_text(report_text)
    status["observed_sha256"] = observed_hash
    status["hash_verified"] = observed_hash == expected_hash
    run_id = manifest.get("run_id")
    status["contains_run_id"] = isinstance(run_id, str) and run_id in report_text
    required_sections = inspect_preopen_report_sections(report_text)
    status["required_sections"] = required_sections
    if not required_sections["passed"]:
        status["errors"].append("required_report_sections_failed")
    return status


def _base_artifact_status(root: Path, artifact_ref: object) -> dict[str, Any]:
    status: dict[str, Any] = {
        "configured": False,
        "path": artifact_ref if isinstance(artifact_ref, str) else None,
        "path_within_project": None,
        "exists": False,
        "expected_sha256": None,
        "observed_sha256": None,
        "hash_verified": None,
        "errors": [],
    }
    if not isinstance(artifact_ref, str) or not artifact_ref:
        return status
    status["configured"] = True
    artifact_path = _resolve_project_artifact(root, artifact_ref)
    if artifact_path is None:
        status["path_within_project"] = False
        status["errors"].append("artifact_path_escapes_project_root")
        return status
    status["path_within_project"] = True
    status["_artifact_path"] = artifact_path
    status["exists"] = artifact_path.exists()
    if not status["exists"]:
        status["errors"].append("artifact_missing")
    return status


def _resolve_project_artifact(root: Path, artifact_ref: str) -> Path | None:
    root_resolved = root.resolve()
    candidate = Path(artifact_ref)
    resolved = (
        candidate if candidate.is_absolute() else root_resolved / candidate
    ).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _artifact_status_passed(status: dict[str, Any], *, required_extra_key: str) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists")
        and status.get("hash_verified")
        and status.get(required_extra_key)
        and not status.get("errors")
    )


def _prediction_artifact_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists")
        and status.get("hash_verified")
        and status.get("context_manifest_id_verified")
        and status.get("schema_version_verified")
        and status.get("sealed_at_verified")
        and status.get("blind_artifact_hash_verified")
        and status.get("manifest_blind_hash_verified")
        and not status.get("errors")
    )


def _news_input_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists")
        and status.get("hash_verified")
        and status.get("row_count_verified")
        and status.get("row_count_partition_verified")
        and status.get("included_row_count_verified")
        and status.get("excluded_row_count_verified")
        and status.get("missing_collected_at_verified")
        and status.get("news_window_start_verified")
        and status.get("news_window_end_verified")
        and status.get("news_window_counts_verified")
        and not status.get("errors")
    )


def _context_file_group_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists_verified")
        and status.get("hashes_verified")
        and not status.get("errors")
    )


def _supporting_artifacts_status_passed(statuses: dict[str, Any]) -> bool:
    return all(
        isinstance(status, dict) and bool(status.get("passed"))
        for status in statuses.values()
    )


def _manifest_reproducibility_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("model_config_valid")
        and status.get("token_counts_valid")
        and status.get("truncations_valid")
        and status.get("web_queries_valid")
        and status.get("web_sources_valid")
        and status.get("episode_scope_valid")
        and status.get("price_snapshot_valid")
        and not status.get("errors")
    )


def _memory_sweep_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists_verified")
        and status.get("hashes_verified")
        and status.get("metadata_verified")
        and status.get("source_hashes_verified")
        and status.get("shard_count_verified")
        and status.get("cache_hits_verified")
        and status.get("swept_episode_ids_verified")
        and not status.get("errors")
    )


def _record_sweep_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists_verified")
        and status.get("hashes_verified")
        and status.get("metadata_verified")
        and status.get("source_hashes_verified")
        and status.get("shard_count_verified")
        and status.get("cache_hits_verified")
        and status.get("swept_record_ids_verified")
        and not status.get("errors")
    )


def _llm_trace_status_passed(status: dict[str, Any]) -> bool:
    purposes = status.get("purposes")
    return bool(
        status.get("configured")
        and status.get("trace_dir_exists")
        and isinstance(purposes, dict)
        and purposes
        and all(
            isinstance(purpose_status, dict)
            and _prompt_trace_status_passed(purpose_status)
            for purpose_status in purposes.values()
        )
        and not status.get("errors")
    )


def _prompt_trace_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("matching_trace_count", 0) > 0
        and status.get("trace_payloads_valid")
        and status.get("model_config_verified")
        and status.get("token_counts_verified")
        and not status.get("errors")
    )


def _text_hashed_artifact_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists")
        and status.get("hash_verified")
        and not status.get("errors")
    )


def _row_disposition_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("row_count_verified")
        and status.get("summary_verified")
        and status.get("coverage_ratio_verified")
        and status.get("duplicate_row_numbers_absent")
        and status.get("raw_content_absent_verified")
        and status.get("news_window_contract_verified")
    )


def _event_cluster_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("row_count_verified")
        and status.get("summary_cluster_count_verified")
        and status.get("summary_source_row_count_verified")
        and status.get("summary_exact_duplicate_count_verified")
        and status.get("summary_exact_duplicate_cluster_count_verified")
        and status.get("summary_semantic_duplicate_cluster_count_verified")
        and status.get("summary_cluster_method_verified")
        and status.get("summary_novelty_review_required_verified")
        and status.get("row_membership_counts_verified")
    )


def _open_world_first_analysis_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("prompt_hash_verified")
        and status.get("required_fields_present")
        and status.get("summary_verified")
    )


def _news_novelty_review_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("prompt_hash_verified")
        and status.get("manifest_count_verified")
        and status.get("payload_cluster_count_verified")
        and status.get("payload_reviewed_cluster_count_verified")
        and status.get("summary_cluster_count_verified")
        and status.get("summary_reviewed_cluster_count_verified")
        and status.get("summary_review_mode_verified")
        and status.get("summary_novelty_counts_verified")
        and status.get("summary_time_verified_count_verified")
        and status.get("summary_excluded_after_cutoff_source_count_verified")
    )


def _semantic_retrieval_plan_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("prompt_hash_verified")
        and status.get("required_categories_verified")
        and status.get("query_count_verified")
        and status.get("category_coverage_verified")
    )


def _semantic_retrieval_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("query_count_verified")
        and status.get("category_counts_verified")
        and status.get("included_episode_ids_verified")
        and status.get("excluded_episode_ids_verified")
        and status.get("summary_verified")
        and status.get("retrieval_zero_is_valid")
    )


def _candidate_expansion_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("prompt_hash_verified")
        and status.get("required_paths_verified")
        and status.get("finding_count_verified")
        and status.get("path_coverage_verified")
        and status.get("path_counts_verified")
        and status.get("manifest_count_verified")
        and status.get("continuation_d_minus_one_verified")
    )


def _candidate_web_check_status_passed(status: dict[str, Any]) -> bool:
    if not status.get("configured"):
        return not status.get("required") and not status.get("errors")
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("row_count_verified")
        and status.get("source_ids_verified")
        and status.get("summary_source_count_verified")
        and status.get("summary_excluded_source_count_verified")
        and status.get("summary_subject_count_verified")
        and status.get("summary_final_candidate_subject_count_verified")
        and status.get("summary_candidate_expansion_subject_count_verified")
        and status.get("summary_expansion_paths_verified")
        and status.get("verification_focus_verified")
        and status.get("required_fields_verified")
        and status.get("source_url_verified")
        and status.get("cutoff_verified")
        and status.get("opened_text_absent_verified")
        and status.get("raw_content_absent_verified")
        and status.get("timestamp_precision_verified")
    )


def _excluded_candidate_web_check_status_passed(status: dict[str, Any]) -> bool:
    if not status.get("configured"):
        return not status.get("required") and not status.get("errors")
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("row_count_verified")
        and status.get("source_ids_verified")
        and status.get("duplicate_source_ids_absent")
        and status.get("not_accepted_verified")
        and status.get("required_fields_verified")
        and status.get("source_url_verified")
        and status.get("exclusion_reason_verified")
        and status.get("raw_content_absent_verified")
        and status.get("cutoff_exclusion_verified")
        and status.get("timestamp_precision_verified")
    )


def _source_ledger_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("entry_count_verified")
        and status.get("required_fields_verified")
        and status.get("source_ids_verified")
        and status.get("duplicate_source_ids_absent")
        and status.get("summary_verified")
        and status.get("web_sources_covered_verified")
        and status.get("candidate_web_sources_covered_verified")
        and status.get("excluded_sources_absent_verified")
        and status.get("source_url_verified")
        and status.get("raw_content_absent_verified")
        and status.get("usage_phase_verified")
        and status.get("blind_cutoff_verified")
        and status.get("timestamp_precision_verified")
    )


def _web_source_status_passed(status: dict[str, Any]) -> bool:
    if not status.get("configured"):
        return not status.get("required") and not status.get("errors")
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("source_ids_verified")
        and status.get("duplicate_source_ids_absent")
        and status.get("required_fields_verified")
        and status.get("source_url_verified")
        and status.get("raw_content_absent_verified")
        and status.get("cutoff_verified")
        and status.get("timestamp_precision_verified")
    )


def _excluded_web_source_status_passed(status: dict[str, Any]) -> bool:
    if not status.get("configured"):
        return not status.get("required") and not status.get("errors")
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("entry_count_verified")
        and status.get("source_ids_verified")
        and status.get("duplicate_source_ids_absent")
        and status.get("not_included_verified")
        and status.get("required_fields_verified")
        and status.get("source_url_verified")
        and status.get("exclusion_reason_verified")
        and status.get("raw_content_absent_verified")
        and status.get("cutoff_exclusion_verified")
        and status.get("timestamp_precision_verified")
    )


def _candidate_verification_status_passed(status: dict[str, Any]) -> bool:
    if not status.get("configured"):
        return not status.get("required") and not status.get("errors")
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("required_dimensions_verified")
        and status.get("subject_count_verified")
        and status.get("finding_count_verified")
        and status.get("dimension_coverage_verified")
        and status.get("status_counts_verified")
        and status.get("source_counts_verified")
        and status.get("accepted_source_ids_verified")
        and status.get("excluded_source_ids_verified")
        and status.get("subjects_without_cutoff_safe_sources_verified")
        and status.get("candidate_expansion_subject_count_verified")
        and status.get("d_minus_one_only_subject_count_verified")
        and status.get("d_minus_one_market_snapshots_valid")
        and status.get("d_minus_one_snapshot_count_verified")
        and status.get("d_minus_one_snapshot_unavailable_count_verified")
    )


def _final_synthesis_context_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("payload_hash_verified")
        and status.get("required_inputs_verified")
        and status.get("required_input_set_verified")
        and status.get("payload_keys_verified")
        and status.get("input_summary_verified")
        and status.get("manifest_summary_verified")
        and status.get("manifest_counts_verified")
        and status.get("event_clusters_verified")
        and status.get("semantic_retrieval_context_verified")
        and status.get("web_research_verified")
        and status.get("candidate_verification_context_verified")
        and status.get("candidate_web_checks_context_verified")
        and status.get("news_novelty_review_context_verified")
        and status.get("candidate_expansion_context_verified")
        and status.get("red_team_output_context_verified")
    )


def _blind_seal_receipt_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("phase_verified")
        and status.get("blind_artifact_hash_verified")
        and status.get("prediction_path_verified")
        and status.get("row_disposition_hash_verified")
        and status.get("source_ledger_hash_verified")
        and status.get("no_d_outcome_verified")
        and status.get("validation_counts_verified")
    )


def _phase_state_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        _text_hashed_artifact_status_passed(status)
        and status.get("schema_version_verified")
        and status.get("run_id_verified")
        and status.get("phase_verified")
        and status.get("completed_phase_verified")
        and status.get("receipt_link_verified")
        and status.get("trade_date_verified")
        and status.get("cutoff_at_verified")
    )


def _red_team_artifact_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists_verified")
        and status.get("metadata_verified")
        and status.get("candidate_count_verified")
        and status.get("finding_count_verified")
        and status.get("required_attack_checks_verified")
        and status.get("attack_check_coverage_verified")
        and status.get("passed_to_synthesis_verified")
        and status.get("summary_verified")
        and not status.get("errors")
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _non_bool_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _read_artifact_object(
    root: Path,
    artifact_ref: object,
    status: dict[str, Any],
) -> dict[str, Any] | None:
    artifact_path = (
        _resolve_project_artifact(root, artifact_ref) if isinstance(artifact_ref, str) else None
    )
    if artifact_path is None or not artifact_path.exists():
        return None
    try:
        payload = read_json(artifact_path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        status["errors"].append("artifact_invalid_json")
        return None
    if not isinstance(payload, dict):
        status["errors"].append("artifact_not_object")
        return None
    return payload


def _read_artifact_jsonl_rows(
    root: Path,
    artifact_ref: object,
    status: dict[str, Any],
    *,
    label: str,
) -> list[dict[str, Any]] | None:
    artifact_path = (
        _resolve_project_artifact(root, artifact_ref) if isinstance(artifact_ref, str) else None
    )
    if artifact_path is None or not artifact_path.exists():
        return None
    rows: list[dict[str, Any]] = []
    try:
        lines = artifact_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        status["errors"].append(f"{label}_unreadable")
        return None
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            status["errors"].append(f"{label}_invalid_jsonl_line:{line_number}")
            return None
        if not isinstance(row, dict):
            status["errors"].append(f"{label}_row_not_object:{line_number}")
            return None
        rows.append(row)
    return rows


def _manifest_prompt_hash(manifest: dict[str, Any], key: str) -> str | None:
    prompt_hashes = manifest.get("prompt_hashes")
    if not isinstance(prompt_hashes, dict):
        return None
    value = prompt_hashes.get(key)
    return value if isinstance(value, str) and value else None


def _semantic_retrieval_required_categories(manifest: dict[str, Any]) -> list[str]:
    summary = manifest.get("semantic_retrieval_summary")
    if not isinstance(summary, dict):
        return []
    return _string_list(summary.get("required_categories"))


def _candidate_expansion_required_paths(manifest: dict[str, Any]) -> list[str]:
    summary = manifest.get("candidate_expansion_summary")
    if not isinstance(summary, dict):
        return []
    return _string_list(summary.get("required_paths"))


def _row_disposition_summary_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_rows = len(rows)
    included = sum(
        1 for row in rows if row.get("disposition") == "INCLUDED_IN_NEWS_WINDOW"
    )
    excluded_before = sum(
        1 for row in rows if row.get("disposition") == "EXCLUDED_BEFORE_WINDOW"
    )
    excluded_after = sum(
        1 for row in rows if row.get("disposition") == "EXCLUDED_AFTER_CUTOFF"
    )
    missing_collected_at = sum(
        1 for row in rows if row.get("collected_at_present") is False
    )
    return {
        "coverage_ratio": 1.0,
        "excluded_after_cutoff": excluded_after,
        "excluded_before_window": excluded_before,
        "included_before_cutoff": included,
        "included_in_news_window": included,
        "missing_collected_at": missing_collected_at,
        "total_rows": total_rows,
    }


def _row_disposition_news_window_contract_matches(
    row: dict[str, Any],
    news_window_start_at: datetime,
    cutoff_at: datetime,
) -> bool:
    published_at = _manifest_datetime(row.get("published_at"))
    if published_at is None:
        return False
    within_window = news_window_start_at <= published_at <= cutoff_at
    if row.get("news_window_start_at") != news_window_start_at.isoformat():
        return False
    if row.get("cutoff_at") != cutoff_at.isoformat():
        return False
    if row.get("within_news_window") is not within_window:
        return False
    if within_window:
        return (
            row.get("disposition") == "INCLUDED_IN_NEWS_WINDOW"
            and row.get("eligible_for_blind_evidence") is True
        )
    if published_at > cutoff_at:
        return (
            row.get("disposition") == "EXCLUDED_AFTER_CUTOFF"
            and row.get("eligible_for_blind_evidence") is False
        )
    return (
        row.get("disposition") == "EXCLUDED_BEFORE_WINDOW"
        and row.get("eligible_for_blind_evidence") is False
    )


def _event_cluster_membership_counts_match(row: dict[str, Any]) -> bool:
    row_count = _non_bool_int(row.get("row_count"))
    if row_count is None:
        return False
    for field in ("row_numbers", "event_ids", "source_ids"):
        value = row.get(field)
        if isinstance(value, list) and len(value) != row_count:
            return False
    return True


def _news_novelty_counts(
    findings: list[dict[str, Any]],
    summary_counts: object,
) -> dict[str, int]:
    observed = Counter(
        novelty
        for finding in findings
        if isinstance(novelty := finding.get("novelty"), str) and novelty
    )
    if isinstance(summary_counts, dict):
        labels = [
            label
            for label, count in summary_counts.items()
            if isinstance(label, str)
            and isinstance(count, int)
            and not isinstance(count, bool)
        ]
        if labels:
            return {label: observed.get(label, 0) for label in labels}
    return dict(observed)


def _candidate_web_verification_focus(manifest: dict[str, Any]) -> list[str]:
    summary = manifest.get("candidate_web_check_summary")
    if not isinstance(summary, dict):
        return []
    return _string_list(summary.get("verification_focus"))


def _read_candidate_web_excluded_rows_for_summary(
    root: Path,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    artifact_ref = manifest.get("excluded_candidate_web_check_artifact")
    if not isinstance(artifact_ref, str):
        return []
    scratch_status: dict[str, Any] = {"errors": []}
    rows = _read_artifact_jsonl_rows(
        root,
        artifact_ref,
        scratch_status,
        label="excluded_candidate_web_check",
    )
    return rows or []


def _candidate_web_subject_keys(
    rows: Iterable[dict[str, Any]],
) -> set[tuple[str, int, str, str, str, str | None]]:
    return {_candidate_web_subject_key(row) for row in rows}


def _candidate_web_subject_key(
    row: dict[str, Any],
) -> tuple[str, int, str, str, str, str | None]:
    rank = row.get("candidate_rank")
    expansion_path = row.get("candidate_expansion_path")
    return (
        str(row.get("candidate_subject_type") or ""),
        rank if isinstance(rank, int) and not isinstance(rank, bool) else 0,
        str(row.get("candidate_ticker") or ""),
        str(row.get("candidate_company_name") or ""),
        str(row.get("candidate_path_type") or ""),
        str(expansion_path) if expansion_path is not None else None,
    )


def _candidate_web_expansion_paths(rows: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(row["candidate_expansion_path"])
            for row in rows
            if row.get("candidate_expansion_path") is not None
        }
    )


def _source_url_valid(row: dict[str, Any]) -> bool:
    source_url = row.get("source_url")
    if not isinstance(source_url, str) or not source_url:
        return False
    url = row.get("url")
    return not isinstance(url, str) or not url or source_url == url


def _source_ledger_blind_cutoff_valid(
    row: dict[str, Any],
    cutoff_at: datetime | None,
) -> bool:
    if row.get("usage_phase") != "BLIND":
        return True
    if row.get("available_before_cutoff") is not True:
        return False
    if row.get("time_verified") is not True:
        return False
    if cutoff_at is None:
        return True
    raw_published_at = row.get("published_at")
    if not isinstance(raw_published_at, str):
        return False
    try:
        published_at = parse_datetime(raw_published_at)
    except ValueError:
        return False
    return published_at <= cutoff_at


def _web_source_cutoff_valid(
    row: dict[str, Any],
    cutoff_at: datetime | None,
) -> bool:
    if row.get("available_before_cutoff") is not True:
        return False
    if row.get("time_verified") is not True:
        return False
    if cutoff_at is None:
        return True
    raw_cutoff_at = row.get("cutoff_at")
    if raw_cutoff_at != cutoff_at.isoformat():
        return False
    raw_published_at = row.get("published_at")
    if not isinstance(raw_published_at, str):
        return False
    try:
        published_at = parse_datetime(raw_published_at)
    except ValueError:
        return False
    return published_at <= cutoff_at


def _excluded_web_source_cutoff_valid(
    row: dict[str, Any],
    cutoff_at: datetime | None,
) -> bool:
    if row.get("available_before_cutoff") is True and row.get("time_verified") is True:
        return False
    if cutoff_at is None:
        return True
    raw_cutoff_at = row.get("cutoff_at")
    return raw_cutoff_at == cutoff_at.isoformat()


def _web_timestamp_precision_valid(row: dict[str, Any]) -> bool:
    precision = row.get("timestamp_precision")
    if precision is None:
        return True
    if not isinstance(precision, str) or precision not in WEB_TIMESTAMP_PRECISIONS:
        return False
    if precision != "date_only_end_of_day":
        return True
    raw_published_at = row.get("published_at")
    if not isinstance(raw_published_at, str):
        return False
    try:
        published_at = parse_datetime(raw_published_at)
    except ValueError:
        return False
    return (
        published_at.hour,
        published_at.minute,
        published_at.second,
        published_at.microsecond,
    ) == (23, 59, 59, 0)


def _candidate_verification_required_dimensions(manifest: dict[str, Any]) -> list[str]:
    summary = manifest.get("candidate_verification_summary")
    if isinstance(summary, dict):
        required_dimensions = _string_list(summary.get("required_dimensions"))
        if required_dimensions:
            return required_dimensions
    return _candidate_web_verification_focus(manifest)


def _candidate_verification_dimension_names(finding: dict[str, Any]) -> list[str]:
    dimensions = finding.get("verification_dimensions")
    if not isinstance(dimensions, list):
        return []
    return [
        str(dimension["name"])
        for dimension in dimensions
        if isinstance(dimension, dict) and isinstance(dimension.get("name"), str)
    ]


def _candidate_verification_status_counts(
    findings: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for finding in findings:
        dimensions = finding.get("verification_dimensions")
        if not isinstance(dimensions, list):
            continue
        for dimension in dimensions:
            if not isinstance(dimension, dict) or not isinstance(
                dimension.get("status"), str
            ):
                continue
            counts[str(dimension["status"])] += 1
    return dict(counts)


def _final_synthesis_required_inputs() -> list[str]:
    return list(FINAL_SYNTHESIS_REQUIRED_INPUTS)


def _final_synthesis_manifest_count_mismatches(
    manifest: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    expected_counts: dict[str, int] = {}
    _add_expected_count(
        expected_counts, "event_cluster_count", manifest.get("event_cluster_count")
    )
    _add_expected_count(
        expected_counts,
        "news_novelty_finding_count",
        manifest.get("news_novelty_review_count"),
    )
    _add_expected_count(
        expected_counts,
        "semantic_retrieval_row_count",
        manifest.get("semantic_retrieval_query_count"),
    )
    _add_expected_count(
        expected_counts,
        "candidate_expansion_finding_count",
        manifest.get("candidate_expansion_count"),
    )
    _add_expected_count(
        expected_counts,
        "candidate_web_check_count",
        manifest.get("candidate_web_check_count"),
    )
    _add_expected_count(
        expected_counts,
        "candidate_verification_finding_count",
        manifest.get("candidate_verification_count"),
    )
    _add_expected_count(
        expected_counts, "shard_contribution_count", manifest.get("memory_sweep_shard_count")
    )
    _add_expected_count(
        expected_counts,
        "record_shard_contribution_count",
        manifest.get("record_sweep_shard_count"),
    )
    _add_expected_count(
        expected_counts,
        "retrieved_raw_episode_count",
        len(_string_list(manifest.get("retrieved_episode_ids"))),
    )
    if "retrieved_record_ids" in manifest:
        _add_expected_count(
            expected_counts,
            "retrieved_record_count",
            len(_string_list(manifest.get("retrieved_record_ids"))),
        )
    _add_expected_count(
        expected_counts,
        "counterexample_count",
        len(_string_list(manifest.get("counterexample_episode_ids"))),
    )
    if "counterexample_record_ids" in manifest:
        _add_expected_count(
            expected_counts,
            "counterexample_record_count",
            len(_string_list(manifest.get("counterexample_record_ids"))),
        )
    _add_expected_count(
        expected_counts, "web_source_count", len(_string_list(manifest.get("web_sources")))
    )
    _add_expected_count(
        expected_counts, "global_brain_file_count", len(_string_list(manifest.get("brain_files")))
    )
    _add_expected_count(
        expected_counts,
        "shard_brain_file_count",
        len(_string_list(manifest.get("shard_brain_files"))),
    )
    red_team_summary = manifest.get("red_team_summary")
    if isinstance(red_team_summary, dict):
        _add_expected_count(
            expected_counts,
            "candidate_count",
            red_team_summary.get("candidate_count"),
        )
        _add_expected_count(
            expected_counts,
            "red_team_finding_count",
            red_team_summary.get("finding_count"),
        )

    mismatches: dict[str, dict[str, Any]] = {}
    for key, expected in expected_counts.items():
        observed = summary.get(key)
        if observed != expected:
            mismatches[key] = {"expected": expected, "observed": observed}
    return mismatches


def _add_expected_count(
    expected_counts: dict[str, int],
    key: str,
    value: object,
) -> None:
    count = _non_bool_int(value)
    if count is not None:
        expected_counts[key] = count


def _semantic_retrieval_summary_verified(
    summary: object,
    *,
    query_count: int,
    included_episode_count: int,
    excluded_episode_count: int,
) -> bool:
    if not isinstance(summary, dict):
        return False
    return (
        summary.get("query_count") == query_count
        and summary.get("included_episode_count") == included_episode_count
        and summary.get("excluded_episode_count") == excluded_episode_count
        and summary.get("retrieval_zero_is_valid") is True
        and isinstance(summary.get("category_query_counts"), dict)
        and bool(summary.get("required_categories"))
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _phase_a_names(blind_context_mode: object) -> set[str]:
    if not isinstance(blind_context_mode, str):
        return set()
    names = {f"PHASE_A_{blind_context_mode}"}
    if blind_context_mode == "NEWS_ONLY_STRICT":
        names.add("PHASE_A_NEWS_ONLY_BLIND")
    return names


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _source_ledger_source_ids_for_type(
    rows: list[dict[str, Any]],
    source_type: str,
) -> list[str]:
    return _unique_strings(
        str(row["source_id"])
        for row in rows
        if row.get("source_type") == source_type
        and isinstance(row.get("source_id"), str)
    )


def _source_ledger_summary_matches(
    summary: object,
    rows: list[dict[str, Any]],
) -> bool:
    if not isinstance(summary, dict):
        return False
    phase_counts = Counter(
        row.get("usage_phase")
        for row in rows
        if isinstance(row.get("usage_phase"), str)
    )
    return (
        summary.get("total_sources") == len(rows)
        and summary.get("blind_sources") == phase_counts.get("BLIND", 0)
        and summary.get("outcome_sources") == phase_counts.get("OUTCOME", 0)
        and summary.get("postmortem_sources") == phase_counts.get("POSTMORTEM", 0)
    )


def _same_unique_string_set(left: object, right: object) -> bool:
    if not _string_list_field_valid(left) or not _string_list_field_valid(right):
        return False
    left_values = _string_list(left)
    right_values = _string_list(right)
    left_unique = _unique_strings(left_values)
    right_unique = _unique_strings(right_values)
    return (
        len(left_unique) == len(left_values)
        and len(right_unique) == len(right_values)
        and set(left_unique) == set(right_unique)
    )


def _token_counts_valid(value: object) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    return all(
        isinstance(key, str)
        and bool(key)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
        for key, count in value.items()
    )


def _string_list_field_valid(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


@context_app.command("export-session-pack")
def context_export_session_pack(
    news: Annotated[Path, typer.Option("--news")],
    trade_date: Annotated[str, typer.Option("--trade-date")],
    cutoff: Annotated[str | None, typer.Option("--cutoff")] = None,
    mode: Annotated[str, typer.Option("--mode")] = "brain",
) -> None:
    settings = load_settings()
    parsed_trade_date = _parse_date(trade_date)
    try:
        output = export_session_pack(
            settings,
            news_csv=news,
            trade_date=parsed_trade_date,
            cutoff_at=_parse_cutoff(cutoff) if cutoff else None,
            mode=mode,
        )
    except (SessionPackBudgetExceededError, SessionPackFutureContextError) as exc:
        _echo(
            {
                "session_pack": exc.output_dir.as_posix(),
                "manifest": (exc.output_dir / "manifest.json").as_posix(),
                "errors": exc.errors,
            }
        )
        raise typer.Exit(code=1) from exc
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        _exit_with_error(exc)
    _echo({"session_pack": output.as_posix()})


@context_app.command("export-analysis-bundle")
def context_export_analysis_bundle(run_id: Annotated[str, typer.Option("--run-id")]) -> None:
    settings = load_settings()
    try:
        output = export_analysis_bundle(settings, run_id=run_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _exit_with_error(exc)
    _echo({"bundle": output.as_posix(), "run_id": run_id})


@audit_app.command("hardcoding")
def audit_hardcoding_cmd() -> None:
    settings = load_settings()
    result = audit_hardcoding(settings.project_root)
    _echo(result)
    if not result.get("passed", False):
        raise typer.Exit(code=1)


@audit_app.command("lookahead")
def audit_lookahead_cmd(
    trade_date: Annotated[str | None, typer.Option("--trade-date")] = None,
) -> None:
    settings = load_settings()
    result = audit_lookahead(
        settings.project_root, trade_date=_parse_date(trade_date) if trade_date else None
    )
    _echo(result)
    if not result.get("passed", False):
        raise typer.Exit(code=1)


@audit_app.command("provenance")
def audit_provenance_cmd() -> None:
    settings = load_settings()
    result = audit_provenance(settings.project_root)
    _echo(result)
    if not result.get("passed", False):
        raise typer.Exit(code=1)


@audit_app.command("coverage")
def audit_coverage_cmd() -> None:
    settings = load_settings()
    result = audit_coverage(settings.project_root)
    _echo(result)
    if not result.get("passed", False):
        raise typer.Exit(code=1)


@training_app.command("export-sft")
def training_export_sft() -> None:
    _export_training_kind("sft")


@training_app.command("export-preference")
def training_export_preference() -> None:
    _export_training_kind("preference")


@training_app.command("export-evals")
def training_export_evals() -> None:
    _export_training_kind("evals")


@training_app.command("audit")
def training_audit() -> None:
    settings = load_settings()
    result = audit_training_exports(settings.project_root)
    _echo(result)
    if not result.get("passed", False):
        raise typer.Exit(code=1)


def _export_training_kind(kind: str) -> None:
    settings = load_settings()
    try:
        result = export_training(settings.project_root, kind=kind)
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(
        {
            "path": result.path.as_posix(),
            "manifest": result.manifest_path.as_posix(),
            "row_count": result.row_count,
        }
    )


@warehouse_app.command("rebuild")
def warehouse_rebuild() -> None:
    settings = load_settings()
    try:
        counts = WarehouseStore(settings.project_root).rebuild_all()
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(counts)


@warehouse_app.command("inspect")
def warehouse_inspect() -> None:
    settings = load_settings()
    try:
        coverage = audit_coverage(settings.project_root)
        warehouse_counts = coverage.get("warehouse_counts")
        counts = (
            warehouse_counts
            if isinstance(warehouse_counts, dict)
            else WarehouseStore(settings.project_root).counts()
        )
        findings = coverage.get("findings")
        warehouse_findings = [
            finding
            for finding in findings
            if isinstance(finding, str) and finding.startswith("warehouse: ")
        ] if isinstance(findings, list) else []
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(
        {
            **counts,
            "status": {
                "synced": coverage.get("warehouse_synced"),
                "projection_synced": coverage.get("warehouse_projection_synced"),
                "required_files_present": coverage.get("warehouse_required_files_present"),
                "required_files": coverage.get("warehouse_required_files"),
                "missing_files": coverage.get("warehouse_missing_files"),
                "unreadable_files": coverage.get("warehouse_unreadable_files"),
                "count_mismatches": coverage.get("warehouse_count_mismatches"),
                "identity_mismatches": coverage.get("warehouse_identity_mismatches"),
                "duplicate_identities": coverage.get("warehouse_duplicate_identities"),
                "weight_mismatches": coverage.get("warehouse_weight_mismatches"),
                "findings": warehouse_findings,
            },
        }
    )


@warehouse_app.command("verify")
def warehouse_verify() -> None:
    settings = load_settings()
    try:
        coverage = audit_coverage(settings.project_root)
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    findings = coverage.get("findings", [])
    warehouse_findings = [
        finding
        for finding in findings
        if isinstance(finding, str) and finding.startswith("warehouse: ")
    ] if isinstance(findings, list) else []
    result = {
        "passed": not warehouse_findings
        and coverage.get("warehouse_required_files_present") is True
        and coverage.get("warehouse_synced") is True
        and coverage.get("warehouse_projection_synced") is True,
        "warehouse_synced": coverage.get("warehouse_synced"),
        "warehouse_projection_synced": coverage.get("warehouse_projection_synced"),
        "warehouse_required_files_present": coverage.get(
            "warehouse_required_files_present"
        ),
        "warehouse_counts": coverage.get("warehouse_counts", {}),
        "warehouse_missing_files": coverage.get("warehouse_missing_files", []),
        "warehouse_unreadable_files": coverage.get("warehouse_unreadable_files", []),
        "warehouse_count_mismatches": coverage.get(
            "warehouse_count_mismatches",
            {},
        ),
        "warehouse_identity_mismatches": coverage.get(
            "warehouse_identity_mismatches",
            {},
        ),
        "warehouse_duplicate_identities": coverage.get(
            "warehouse_duplicate_identities",
            {},
        ),
        "warehouse_weight_mismatches": coverage.get(
            "warehouse_weight_mismatches",
            {},
        ),
        "warehouse_findings": warehouse_findings,
        "required_files": coverage.get("warehouse_required_files", []),
    }
    _echo(result)
    if not result["passed"]:
        raise typer.Exit(code=1)


@warehouse_app.command("query-records")
def warehouse_query_records(
    record_type: Annotated[str | None, typer.Option("--record-type")] = None,
    training_target: Annotated[str | None, typer.Option("--training-target")] = None,
    evidence_phase: Annotated[str | None, typer.Option("--evidence-phase")] = None,
    ticker: Annotated[str | None, typer.Option("--ticker")] = None,
    company_name: Annotated[str | None, typer.Option("--company-name")] = None,
    theme_id: Annotated[str | None, typer.Option("--theme-id")] = None,
    path_type: Annotated[str | None, typer.Option("--path-type")] = None,
    response_class: Annotated[str | None, typer.Option("--response-class")] = None,
    confidence_label: Annotated[str | None, typer.Option("--confidence-label")] = None,
    trade_date_from: Annotated[str | None, typer.Option("--trade-date-from")] = None,
    trade_date_to: Annotated[str | None, typer.Option("--trade-date-to")] = None,
    available_from_as_of: Annotated[
        str | None,
        typer.Option("--available-from-as-of"),
    ] = None,
    training_eligible_only: Annotated[
        bool,
        typer.Option("--training-eligible-only"),
    ] = False,
    ineligible_only: Annotated[bool, typer.Option("--ineligible-only")] = False,
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    if training_eligible_only and ineligible_only:
        typer.echo(
            "--training-eligible-only and --ineligible-only cannot be combined",
            err=True,
        )
        raise typer.Exit(code=1)
    training_eligible = (
        True if training_eligible_only else False if ineligible_only else None
    )
    settings = load_settings()
    filters = {
        "record_type": record_type,
        "training_target": training_target,
        "evidence_phase": evidence_phase,
        "ticker": ticker,
        "company_name": company_name,
        "theme_id": theme_id,
        "path_type": path_type,
        "response_class": response_class,
        "confidence_label": confidence_label,
        "trade_date_from": trade_date_from,
        "trade_date_to": trade_date_to,
        "available_from_as_of": available_from_as_of,
        "training_eligible": training_eligible,
        "limit": limit,
    }
    try:
        rows = WarehouseStore(settings.project_root).query_brain_records(
            record_type=record_type,
            training_target=training_target,
            evidence_phase=evidence_phase,
            ticker=ticker,
            company_name=company_name,
            theme_id=theme_id,
            path_type=path_type,
            response_class=response_class,
            confidence_label=confidence_label,
            trade_date_from=trade_date_from,
            trade_date_to=trade_date_to,
            available_from_as_of=available_from_as_of,
            training_eligible=training_eligible,
            limit=limit,
        )
    except (OSError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(
        {
            "row_count": len(rows),
            "filters": {key: value for key, value in filters.items() if value is not None},
            "rows": rows,
        }
    )


@memory_app.command("inspect")
def memory_inspect(
    episode: Annotated[str, typer.Option("--episode")],
) -> None:
    settings = load_settings()
    records = BrainRecordStore(settings.project_root).read_episode_records(episode)
    _echo(
        {
            "episode_id": episode,
            "record_count": len(records),
            "training_eligible_record_count": sum(
                1 for record in records if record.training_eligible
            ),
            "ineligible_record_count": sum(
                1 for record in records if not record.training_eligible
            ),
            "record_ids": [record.record_id for record in records],
            "record_counts_by_type": dict(
                sorted(Counter(record.record_type for record in records).items())
            ),
            "record_counts_by_evidence_phase": dict(
                sorted(Counter(record.evidence_phase for record in records).items())
            ),
            "record_counts_by_training_target": dict(
                sorted(
                    Counter(
                        record.training_target or "UNKNOWN" for record in records
                    ).items()
                )
            ),
            "record_counts_by_typed_payload_status": dict(
                sorted(
                    Counter(record.typed_payload_status for record in records).items()
                )
            ),
            "unknown_typed_payload_count": sum(
                1
                for record in records
                if record.typed_payload_status == "UNKNOWN_TYPED_PAYLOAD"
            ),
        }
    )


@memory_app.command("inspect-record")
def memory_inspect_record(record_id: str) -> None:
    settings = load_settings()
    try:
        record = BrainRecordStore(settings.project_root).get_record(record_id)
    except FileNotFoundError as exc:
        _exit_with_error(exc)
    _echo(record.model_dump(mode="json"))


@memory_app.command("stats")
def memory_stats() -> None:
    settings = load_settings()
    _echo(BrainRecordStore(settings.project_root).stats())


def _require_openai_embedding_runtime() -> None:
    try:
        module = import_module("openai")
    except ImportError as exc:
        raise ValueError(
            "production vector index rebuild requires the openai SDK; install the openai extra"
        ) from exc
    if not hasattr(module, "AsyncOpenAI"):
        raise ValueError(
            "production vector index rebuild requires an openai SDK exposing AsyncOpenAI"
        )


@memory_app.command("rebuild-index")
def memory_rebuild_index(
    production: Annotated[
        bool,
        typer.Option(
            "--production",
            help="Use the configured real LLM embedding provider instead of the deterministic local index.",
        ),
    ] = False,
) -> None:
    settings = load_settings()
    mode = "deterministic"
    embedding_provider = None
    try:
        if production:
            if settings.llm_provider.strip().lower() == "mock":
                raise ValueError("production vector index rebuild requires a real LLM provider")
            if settings.llm.provider.strip().lower() == "mock":
                raise ValueError("production vector index rebuild requires a non-mock model profile")
            if not BrainRecordStore(settings.project_root).list_records():
                raise ValueError("production vector index rebuild requires normalized brain records")
            if (
                settings.llm_provider.strip().lower() in OPENAI_LLM_PROVIDER_ALIASES
                and not settings.env_value("OPENAI_API_KEY")
            ):
                raise ValueError("production vector index rebuild requires OPENAI_API_KEY")
            if settings.llm_provider.strip().lower() in OPENAI_LLM_PROVIDER_ALIASES:
                _require_openai_embedding_runtime()
            provider = create_llm_provider(settings)
            if isinstance(provider, DeterministicMockLLMProvider):
                raise ValueError("production vector index rebuild cannot use the mock LLM provider")
            embedding_model = (
                getattr(provider, "embedding_model", None)
                or settings.llm.embedding_model
                or "configured"
            )
            embedding_provider = AsyncEmbeddingProviderAdapter(
                provider,
                embedding_method=(
                    f"llm_embedding:{settings.llm_provider.strip().lower()}:{embedding_model}"
                ),
            )
            mode = "production"
        store = (
            LocalRetrievalStore(settings.project_root, embedding_provider=embedding_provider)
            if embedding_provider is not None
            else LocalRetrievalStore(settings.project_root)
        )
        manifest = store.rebuild_index()
    except (OSError, RuntimeError, ValueError) as exc:
        _exit_with_error(exc)
    _echo(
        {
            "mode": mode,
            "production": production,
            "index_path": relative_to_root(
                settings.project_root / "memory" / "vector_index",
                settings.project_root,
            ),
            "embedding_method": manifest.get("embedding_method"),
            "accepted_episode_count": manifest.get("accepted_episode_count"),
            "brain_record_count": manifest.get("brain_record_count"),
            "manifest": manifest,
        }
    )


@memory_app.command("apply-company-deltas")
def memory_apply_company_deltas(
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Only apply deltas available at this ISO datetime."),
    ] = None,
) -> None:
    settings = load_settings()
    cutoff = parse_datetime(as_of) if as_of else None
    result = CompanyMemoryStore(settings.project_root).apply_record_deltas(as_of=cutoff)
    _echo(
        {
            "processed_record_count": result.processed_record_count,
            "written_count": result.written_count,
            "written_paths": [
                path.relative_to(settings.project_root).as_posix()
                for path in result.written_paths
            ],
            "skipped_future_record_ids": result.skipped_future_record_ids,
            "skipped_invalid_record_ids": result.skipped_invalid_record_ids,
        }
    )
    if result.skipped_invalid_record_ids:
        raise typer.Exit(code=1)


@memory_app.command("audit")
def memory_audit(
    deep: Annotated[bool, typer.Option("--deep")] = False,
) -> None:
    settings = load_settings()
    result = audit_record_store(settings.project_root, deep=deep)
    report_paths = write_diagnostic_report(
        settings.project_root,
        "brain_record_store_report",
        record_store_report_payload(settings.project_root, result),
    )
    result = {**result, "diagnostic_report": report_paths}
    _echo(result)
    if not result.get("passed", False):
        raise typer.Exit(code=1)


def _legacy_episode_record(episode: Any) -> BrainRecordEnvelope:
    payload = {
        "record_type": "memory_claim",
        "record_id": f"{episode.episode_id}:legacy_catalog_record",
        "episode_id": episode.episode_id,
        "trade_date": episode.trade_date.isoformat(),
        "available_from": episode.available_from.isoformat(),
        "training_target": "legacy_catalog_only",
        "summary": episode.blind_analysis.summary,
        "open_world_mechanisms": episode.blind_analysis.open_world_mechanisms,
        "training_eligible": False,
        "eligibility_reason": "legacy v1 episode migrated as catalog_only memory",
    }
    payload_sha = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=str(payload["record_id"]),
        record_type="memory_claim",
        episode_id=episode.episode_id,
        trade_date=episode.trade_date,
        available_from=episode.available_from,
        training_target="legacy_catalog_only",
        evidence_phase="AUDIT",
        training_eligible=False,
        eligibility_reason="legacy v1 episode migrated as catalog_only memory",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=[f"{episode.episode_id}:accepted_episode"],
        raw_payload_sha256=payload_sha,
        normalized_payload_sha256=payload_sha,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="legacy_research_episode.json",
        source_line=None,
        payload=payload,
    )


def _legacy_record_envelope(
    episode: Any,
    record: BrainRecordEnvelope,
    *,
    source_sha256: str,
    raw_block_sha256: str,
) -> ResearchBundleEnvelope:
    return ResearchBundleEnvelope(
        bundle_schema_version="nslab.legacy_research_episode.v1",
        manifest_schema_version=None,
        episode_schema_version=episode.schema_version,
        episode_id=episode.episode_id,
        trade_date=episode.trade_date,
        cutoff_at=episode.cutoff_at,
        available_from=episode.available_from,
        bundle_status="LEGACY_ACCEPTED",
        blind_valid=True,
        raw_bundle_sha256=source_sha256,
        raw_block_hashes={"legacy_research_episode.json": raw_block_sha256},
        raw_block_counts={"legacy_research_episode.json": 1},
        provenance_closure_status="legacy_catalog_only",
        adapter_name="legacy-migration",
        import_status="catalog_only",
    )


def _legacy_normalized_index(
    episode: Any,
    record: BrainRecordEnvelope,
) -> NormalizedEpisodeIndex:
    return NormalizedEpisodeIndex(
        episode_id=episode.episode_id,
        trade_date=episode.trade_date,
        cutoff_at=episode.cutoff_at,
        available_from=episode.available_from,
        bundle_status="LEGACY_ACCEPTED",
        blind_valid=True,
        raw_block_names=["legacy_research_episode.json"],
        record_ids=[record.record_id],
        record_count_by_type={record.record_type: 1},
        training_eligible_record_count=0,
        source_ids=[f"{episode.episode_id}:accepted_episode"],
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
