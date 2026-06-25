"""Command line interface."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer

from news_scalping_lab.audits.coverage import audit_coverage
from news_scalping_lab.audits.hardcoding import audit_hardcoding
from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.brain.audit import audit_brain
from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.brain.diff import build_brain_diff, write_brain_diff_markdown
from news_scalping_lab.config import ensure_project_dirs, load_settings, write_default_config_files
from news_scalping_lab.context.session_pack import (
    SessionPackBudgetExceededError,
    SessionPackFutureContextError,
    export_session_pack,
)
from news_scalping_lab.contracts.schemas import export_json_schemas
from news_scalping_lab.diagnostics import build_doctor_report
from news_scalping_lab.evaluation.evaluator import Evaluator
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.ingest.news import import_news_csv, load_news_csv
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.reporting.bundle import export_analysis_bundle
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.training import export_training
from news_scalping_lab.ui.launcher import (
    StreamlitLaunchConfig,
    StreamlitLaunchError,
    run_streamlit_ui,
)
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    parse_datetime,
    read_json,
    sha256_text,
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

app.add_typer(news_app, name="news")
app.add_typer(research_app, name="research")
app.add_typer(brain_app, name="brain")
app.add_typer(context_app, name="context")
app.add_typer(audit_app, name="audit")
app.add_typer(training_app, name="training")
app.add_typer(warehouse_app, name="warehouse")


def _echo(data: object) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("expected YYYY-MM-DD") from exc


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
def doctor() -> None:
    settings = load_settings()
    _echo(build_doctor_report(settings))


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
    batch = load_news_csv(csv_path)
    _echo(
        {
            "path": csv_path.as_posix(),
            "sha256": batch.sha256,
            "trade_date": batch.trade_date.isoformat(),
            "row_count": batch.row_count,
            "first_published_at": batch.items[0].published_at.isoformat() if batch.items else None,
            "last_published_at": batch.items[-1].published_at.isoformat() if batch.items else None,
        }
    )


@news_app.command("import")
def news_import(csv_path: Path) -> None:
    settings = load_settings()
    batch = import_news_csv(csv_path, settings.path("data/raw/news"))
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
    settings = load_settings()
    episode = ResearchImporter(
        settings.project_root,
        llm=create_llm_provider(settings),
    ).import_path(path, mode=mode)
    _echo(
        {
            "episode_id": episode.episode_id,
            "trade_date": episode.trade_date.isoformat(),
            "mode": mode,
        }
    )


@research_app.command("import-batch")
def research_import_batch(
    directory: Path,
    mode: str = "auto",
    accept: Annotated[bool, typer.Option("--accept/--no-accept")] = True,
) -> None:
    settings = load_settings()
    importer = ResearchImporter(
        settings.project_root,
        llm=create_llm_provider(settings),
    )
    store = ResearchStore(settings.project_root)
    imported: list[str] = []
    accepted: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.is_file():
            episode = importer.import_path(path, mode=mode)
            imported.append(episode.episode_id)
            if accept:
                store.accept(episode.episode_id)
                accepted.append(episode.episode_id)
    _echo({"imported_episode_ids": imported, "accepted_episode_ids": accepted})


@research_app.command("validate")
def research_validate(episode_id: str) -> None:
    settings = load_settings()
    episode = ResearchStore(settings.project_root).get_episode(episode_id)
    _echo(
        {"valid": True, "episode_id": episode.episode_id, "schema_version": episode.schema_version}
    )


@research_app.command("accept")
def research_accept(episode_id: str) -> None:
    settings = load_settings()
    path = ResearchStore(settings.project_root).accept(episode_id)
    _echo({"accepted": episode_id, "path": path.as_posix()})


@research_app.command("reject")
def research_reject(episode_id: str) -> None:
    settings = load_settings()
    path = ResearchStore(settings.project_root).reject(episode_id)
    _echo({"rejected": episode_id, "path": path.as_posix()})


@brain_app.command("rebuild")
def brain_rebuild(mode: str = "full") -> None:
    settings = load_settings()
    manifest = BrainCompiler(settings.project_root).rebuild(mode=mode)
    _echo(manifest.model_dump(mode="json"))


@brain_app.command("update")
def brain_update(episode: Annotated[str, typer.Option("--episode")]) -> None:
    settings = load_settings()
    try:
        manifest = BrainCompiler(settings.project_root).update(episode_id=episode)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    _echo(manifest.model_dump(mode="json"))


@brain_app.command("audit")
def brain_audit() -> None:
    settings = load_settings()
    result = audit_brain(settings.project_root)
    _echo(result)
    if not result.get("passed", False):
        raise typer.Exit(code=1)


@brain_app.command("diff")
def brain_diff(version_a: str, version_b: str) -> None:
    settings = load_settings()
    diff = build_brain_diff(settings.project_root, version_a, version_b)
    markdown_path = write_brain_diff_markdown(settings.project_root, diff)
    _echo({**diff, "markdown_path": markdown_path.as_posix()})


@app.command()
def analyze(
    news: Annotated[Path, typer.Option("--news")],
    trade_date: Annotated[str, typer.Option("--trade-date")],
    cutoff: Annotated[str, typer.Option("--cutoff")],
    mode: Annotated[str, typer.Option("--mode")] = "exhaustive",
    web_search: Annotated[bool, typer.Option("--web-search")] = False,
) -> None:
    settings = load_settings()
    parsed_trade_date = _parse_date(trade_date)
    analysis = asyncio.run(
        DailyAnalyzer(settings).analyze(
            news_csv=news,
            trade_date=parsed_trade_date,
            cutoff_at=parse_datetime(cutoff),
            mode=mode,
            web_search=web_search,
        )
    )
    _echo(analysis.model_dump(mode="json"))


@app.command()
def evaluate(trade_date: Annotated[str, typer.Option("--trade-date")]) -> None:
    settings = load_settings()
    result = Evaluator(settings.project_root).evaluate(trade_date=_parse_date(trade_date))
    postmortem = read_json(result.report_path)
    if not isinstance(postmortem, dict):
        raise typer.BadParameter("postmortem report must be a JSON object")
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
def context_inspect(run_id: str) -> None:
    settings = load_settings()
    path = settings.path("runs/manifests") / f"{run_id}.json"
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise typer.BadParameter("context manifest must be a JSON object")
    _echo(
        {
            **manifest,
            "inspection": _inspect_context_manifest(settings.project_root, path, manifest),
        }
    )


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
    llm_traces = _inspect_llm_traces(root, manifest)
    return {
        "context_manifest": {
            "path": _display_path(root, manifest_path),
            "exists": manifest_path.exists(),
            "sha256": file_sha256(manifest_path) if manifest_path.exists() else None,
        },
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
        "llm_traces": llm_traces,
        "reproducibility_checks_passed": _artifact_status_passed(
            prediction,
            required_extra_key="context_manifest_id_verified",
        )
        and _artifact_status_passed(report, required_extra_key="contains_run_id")
        and _news_input_status_passed(news_input)
        and _context_file_group_status_passed(brain_files)
        and _context_file_group_status_passed(shard_brain_files)
        and _supporting_artifacts_status_passed(supporting_artifacts)
        and _memory_sweep_status_passed(memory_sweep)
        and _llm_trace_status_passed(llm_traces),
    }


def _inspect_supporting_artifacts(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    specs = (
        ("row_disposition", "row_disposition_artifact", "row_disposition_sha256", True),
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
        "path_within_project": None,
        "exists_verified": None,
        "metadata_verified": None,
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
    status["path_within_project"] = not status["path_escape_errors"]
    status["exists_verified"] = status["path_within_project"] and not status["missing_files"]
    status["metadata_verified"] = (
        status["exists_verified"]
        and not status["invalid_json"]
        and not status["schema_mismatches"]
        and not status["run_id_mismatches"]
        and not status["prompt_hash_mismatches"]
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
    status["passed"] = _red_team_artifact_status_passed(status)
    return status


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
        "path_within_project": None,
        "exists_verified": None,
        "hashes_verified": None,
        "metadata_verified": None,
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
    if not status["shard_count_verified"]:
        status["errors"].append("memory_sweep_shard_count_mismatch")
    if not status["cache_hits_verified"]:
        status["errors"].append("memory_sweep_cache_hits_mismatch")
    if not status["swept_episode_ids_verified"]:
        status["errors"].append("memory_sweep_swept_episode_ids_mismatch")
    status["passed"] = _memory_sweep_status_passed(status)
    return status


_CONTEXT_PROMPT_TRACE_PURPOSES = {
    "blind_analysis": "daily_blind_analysis",
    "red_team_candidate_review": "red_team_candidate_review",
    "final_synthesis": "final_synthesis",
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
    expected_model_config = _expected_trace_model_config(manifest)
    status: dict[str, Any] = {
        "configured": isinstance(expected_prompt_hash, str) and bool(expected_prompt_hash),
        "manifest_hash_key": hash_key,
        "purpose": purpose,
        "expected_prompt_sha256": expected_prompt_hash
        if isinstance(expected_prompt_hash, str)
        else None,
        "matching_trace_count": 0,
        "matching_trace_ids": [],
        "matching_trace_paths": [],
        "trace_payloads_valid": None,
        "model_config_verified": None,
        "model_config_comparison": None,
        "model_config_mismatches": [],
        "trace_validation_errors": {},
        "errors": [],
    }
    if not status["configured"]:
        status["errors"].append(f"{hash_key}_prompt_hash_missing")
        status["passed"] = False
        return status
    if expected_model_config is None:
        status["errors"].append("manifest_model_config_missing_or_invalid")

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
    if not status["trace_payloads_valid"]:
        status["errors"].append("matching_trace_payload_invalid")
    if not status["model_config_verified"]:
        status["errors"].append("matching_trace_model_config_mismatch")
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
    if not isinstance(payload.get("tool_calls"), list):
        errors.append("tool_calls_not_list")
    if not isinstance(payload.get("retries"), int):
        errors.append("retries_not_integer")
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
    status["expected_sha256"] = expected_hash if isinstance(expected_hash, str) else None
    status["expected_row_count"] = _optional_int(manifest.get("news_row_count"))
    status["expected_included_row_count"] = _optional_int(
        manifest.get("included_news_row_count")
    )
    status["expected_excluded_row_count"] = _optional_int(
        manifest.get("excluded_news_row_count")
    )
    status["observed_row_count"] = None
    status["row_count_verified"] = None
    status["row_count_partition_verified"] = None
    if not status["configured"]:
        status["errors"].append("news_file_missing")
        return status
    artifact_path = status.pop("_artifact_path", None)
    if not isinstance(artifact_path, Path) or not status["exists"]:
        return status
    observed_hash = file_sha256(artifact_path)
    status["observed_sha256"] = observed_hash
    status["hash_verified"] = observed_hash == expected_hash
    try:
        batch = load_news_csv(artifact_path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        status["errors"].append(f"news_file_invalid_csv:{type(exc).__name__}")
        return status
    observed_row_count = batch.row_count
    status["observed_row_count"] = observed_row_count
    status["row_count_verified"] = observed_row_count == status["expected_row_count"]
    expected_included = status["expected_included_row_count"]
    expected_excluded = status["expected_excluded_row_count"]
    if isinstance(expected_included, int) and isinstance(expected_excluded, int):
        status["row_count_partition_verified"] = (
            expected_included + expected_excluded == status["expected_row_count"]
        )
    return status


def _inspect_prediction_artifact(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    status = _base_artifact_status(root, manifest.get("prediction_artifact"))
    expected_hash = manifest.get("prediction_sha256")
    status["expected_sha256"] = expected_hash if isinstance(expected_hash, str) else None
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


def _news_input_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists")
        and status.get("hash_verified")
        and status.get("row_count_verified")
        and status.get("row_count_partition_verified")
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


def _memory_sweep_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists_verified")
        and status.get("hashes_verified")
        and status.get("metadata_verified")
        and status.get("shard_count_verified")
        and status.get("cache_hits_verified")
        and status.get("swept_episode_ids_verified")
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


def _red_team_artifact_status_passed(status: dict[str, Any]) -> bool:
    return bool(
        status.get("configured")
        and status.get("path_within_project")
        and status.get("exists_verified")
        and status.get("metadata_verified")
        and not status.get("errors")
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


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
            cutoff_at=parse_datetime(cutoff) if cutoff else None,
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
    _echo({"session_pack": output.as_posix()})


@context_app.command("export-analysis-bundle")
def context_export_analysis_bundle(run_id: Annotated[str, typer.Option("--run-id")]) -> None:
    settings = load_settings()
    try:
        output = export_analysis_bundle(settings, run_id=run_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
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
    settings = load_settings()
    result = export_training(settings.project_root, kind="sft")
    _echo(
        {
            "path": result.path.as_posix(),
            "manifest": result.manifest_path.as_posix(),
            "row_count": result.row_count,
        }
    )


@training_app.command("export-preference")
def training_export_preference() -> None:
    settings = load_settings()
    result = export_training(settings.project_root, kind="preference")
    _echo(
        {
            "path": result.path.as_posix(),
            "manifest": result.manifest_path.as_posix(),
            "row_count": result.row_count,
        }
    )


@training_app.command("export-evals")
def training_export_evals() -> None:
    settings = load_settings()
    result = export_training(settings.project_root, kind="evals")
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
    _echo(WarehouseStore(settings.project_root).rebuild_all())


@warehouse_app.command("inspect")
def warehouse_inspect() -> None:
    settings = load_settings()
    _echo(WarehouseStore(settings.project_root).counts())


if __name__ == "__main__":
    app()
