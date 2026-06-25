"""Command line interface."""

from __future__ import annotations

import asyncio
import json
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
from news_scalping_lab.utils import file_sha256, parse_datetime, read_json, sha256_text
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
        "reproducibility_checks_passed": _artifact_status_passed(
            prediction,
            required_extra_key="context_manifest_id_verified",
        )
        and _artifact_status_passed(report, required_extra_key="contains_run_id")
        and _news_input_status_passed(news_input)
        and _context_file_group_status_passed(brain_files)
        and _context_file_group_status_passed(shard_brain_files)
        and _supporting_artifacts_status_passed(supporting_artifacts),
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
