"""Command line interface."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Annotated

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
from news_scalping_lab.utils import parse_datetime, read_json
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
    _echo(
        {
            "postmortem": result.report_path.as_posix(),
            "research_episode_id": result.episode_id,
            "research_episode_path": result.episode_path.as_posix(),
        }
    )


@context_app.command("inspect")
def context_inspect(run_id: str) -> None:
    settings = load_settings()
    path = settings.path("runs/manifests") / f"{run_id}.json"
    _echo(read_json(path))


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
