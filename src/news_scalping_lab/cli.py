"""Command line interface."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime
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
from news_scalping_lab.context.final_synthesis import final_synthesis_input_summary
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
from news_scalping_lab.reporting.sections import inspect_preopen_report_sections
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
    default_news_window_start,
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
        llm_max_retries=settings.llm.max_retries,
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
        llm_max_retries=settings.llm.max_retries,
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
    manifest_reproducibility = _inspect_manifest_reproducibility_fields(manifest)
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
        and _llm_trace_status_passed(llm_traces)
        and _manifest_reproducibility_status_passed(manifest_reproducibility),
    }


def _inspect_manifest_reproducibility_fields(manifest: dict[str, Any]) -> dict[str, Any]:
    status: dict[str, Any] = {
        "schema_version": manifest.get("schema_version"),
        "configured": manifest.get("schema_version") == "nslab.context_manifest.v1",
        "model_config_valid": False,
        "token_counts_valid": False,
        "truncations_valid": False,
        "web_queries_valid": False,
        "web_sources_valid": False,
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
    return status


def _inspect_supporting_artifacts(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    specs = (
        ("row_disposition", "row_disposition_artifact", "row_disposition_sha256", True),
        ("event_cluster", "event_cluster_artifact", "event_cluster_sha256", True),
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
    statuses["semantic_retrieval_plan"] = _inspect_semantic_retrieval_plan_artifact(
        root, manifest
    )
    statuses["semantic_retrieval"] = _inspect_semantic_retrieval_artifact(root, manifest)
    statuses["candidate_expansion"] = _inspect_candidate_expansion_artifact(
        root, manifest
    )
    statuses["candidate_web_check"] = _inspect_candidate_web_check_artifact(
        root, manifest
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
            "verification_focus_verified": None,
            "required_fields_verified": None,
            "source_url_verified": None,
            "cutoff_verified": None,
            "opened_text_absent_verified": None,
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

    expected_focus = _candidate_web_verification_focus(manifest)
    status["verification_focus_verified"] = bool(expected_focus) and all(
        _string_list(row.get("verification_focus")) == expected_focus for row in rows
    )
    if not status["verification_focus_verified"]:
        status["errors"].append("candidate_web_check_verification_focus_mismatch")

    required_fields = {
        "candidate_rank",
        "candidate_company_name",
        "candidate_path_type",
        "verification_focus",
        "source_id",
        "source_url",
        "url",
    }
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

    status["cutoff_verified"] = all(
        row.get("available_before_cutoff") is True
        and row.get("time_verified") is True
        for row in rows
    )
    if not status["cutoff_verified"]:
        status["errors"].append("candidate_web_check_cutoff_not_verified")

    status["opened_text_absent_verified"] = all("opened_text" not in row for row in rows)
    if not status["opened_text_absent_verified"]:
        status["errors"].append("candidate_web_check_opened_text_present")

    status["passed"] = _candidate_web_check_status_passed(status)
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
            "candidate_expansion_subject_count_verified": None,
            "d_minus_one_only_subject_count_verified": None,
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
        required_input_list == _final_synthesis_required_inputs()
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

    status["passed"] = _final_synthesis_context_status_passed(status)
    return status


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
    expected_phase = f"PHASE_A_{manifest.get('blind_context_mode')}"
    status["completed_phase_verified"] = expected_phase in completed_phases
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
    "news_novelty_review": "news_novelty_review",
    "semantic_retrieval_plan": "semantic_retrieval_plan",
    "candidate_expansion": "candidate_expansion",
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
        and not status.get("errors")
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
        and status.get("verification_focus_verified")
        and status.get("required_fields_verified")
        and status.get("source_url_verified")
        and status.get("cutoff_verified")
        and status.get("opened_text_absent_verified")
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
        and status.get("candidate_expansion_subject_count_verified")
        and status.get("d_minus_one_only_subject_count_verified")
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


def _candidate_web_verification_focus(manifest: dict[str, Any]) -> list[str]:
    summary = manifest.get("candidate_web_check_summary")
    if not isinstance(summary, dict):
        return []
    return _string_list(summary.get("verification_focus"))


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
    return [
        "current_news",
        "open_world_first_analysis",
        "news_novelty_review",
        "additional_semantic_retrieval",
        "open_world_candidate_expansion",
        "web_research",
        "global_brain",
        "all_shard_brains",
        "all_shard_contributions",
        "retrieved_raw_episodes",
        "positive_cases",
        "negative_cases",
        "counterexamples",
        "candidate_research",
        "candidate_web_checks",
        "candidate_verification",
        "red_team_output",
        "d_minus_one_market_data",
        "company_memory",
        "market_memory",
    ]


def _final_synthesis_manifest_count_mismatches(
    manifest: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    expected_counts: dict[str, int] = {}
    _add_expected_count(
        expected_counts, "current_news_count", manifest.get("included_news_row_count")
    )
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
        "retrieved_raw_episode_count",
        len(_string_list(manifest.get("retrieved_episode_ids"))),
    )
    _add_expected_count(
        expected_counts,
        "counterexample_count",
        len(_string_list(manifest.get("counterexample_episode_ids"))),
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


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


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
