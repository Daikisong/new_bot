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
from news_scalping_lab.brain.compiler import BrainCompiler, current_brain_version
from news_scalping_lab.config import ensure_project_dirs, load_settings
from news_scalping_lab.context.session_pack import export_session_pack
from news_scalping_lab.contracts.schemas import export_json_schemas
from news_scalping_lab.evaluation.evaluator import Evaluator
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.ingest.news import import_news_csv, load_news_csv
from news_scalping_lab.prices.stock_web import StockWebPriceSource
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.training import export_training
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
    written = export_json_schemas(settings.path("schemas"))
    _echo({"initialized": True, "schemas": [path.as_posix() for path in written]})


@app.command()
def doctor() -> None:
    settings = load_settings()
    store = ResearchStore(settings.project_root)
    stock_web_schema = None
    if settings.stock_web_path is not None and settings.stock_web_path.exists():
        stock_web_schema = StockWebPriceSource(settings.stock_web_path).inspect_atlas_schema()
    payload = {
        "project_root": settings.project_root.as_posix(),
        "llm_provider": settings.llm_provider,
        "web_provider": settings.web_provider,
        "price_provider": settings.price_provider,
        "stock_web_path": settings.stock_web_path.as_posix() if settings.stock_web_path else None,
        "brain_head": current_brain_version(settings.project_root),
        "accepted_episode_count": len(store.list_accepted()),
        "schema_dir_exists": settings.path("schemas").exists(),
        "stock_web_schema": stock_web_schema,
        "warehouse": WarehouseStore(settings.project_root).counts(),
    }
    _echo(payload)


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
    episode = ResearchImporter(settings.project_root).import_path(path, mode=mode)
    _echo(
        {
            "episode_id": episode.episode_id,
            "trade_date": episode.trade_date.isoformat(),
            "mode": mode,
        }
    )


@research_app.command("import-batch")
def research_import_batch(directory: Path, mode: str = "auto") -> None:
    settings = load_settings()
    importer = ResearchImporter(settings.project_root)
    imported = []
    for path in sorted(directory.iterdir()):
        if path.is_file():
            imported.append(importer.import_path(path, mode=mode).episode_id)
    _echo({"imported_episode_ids": imported})


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
    manifest = BrainCompiler(settings.project_root).update(episode_id=episode)
    _echo(manifest.model_dump(mode="json"))


@brain_app.command("audit")
def brain_audit() -> None:
    settings = load_settings()
    result = audit_brain(settings.project_root)
    _echo(result)
    if not result.get("coverage_complete", False):
        raise typer.Exit(code=1)


@brain_app.command("diff")
def brain_diff(version_a: str, version_b: str) -> None:
    _echo(
        {
            "version_a": version_a,
            "version_b": version_b,
            "status": "diff artifacts are generated during rebuild",
        }
    )


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
    mode: Annotated[str, typer.Option("--mode")] = "brain",
) -> None:
    settings = load_settings()
    output = export_session_pack(
        settings, news_csv=news, trade_date=_parse_date(trade_date), mode=mode
    )
    _echo({"session_pack": output.as_posix()})


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
    if not result.get("coverage_complete", False):
        raise typer.Exit(code=1)


@training_app.command("export-sft")
def training_export_sft() -> None:
    settings = load_settings()
    _echo({"path": export_training(settings.project_root, kind="sft").as_posix()})


@training_app.command("export-preference")
def training_export_preference() -> None:
    settings = load_settings()
    _echo({"path": export_training(settings.project_root, kind="preference").as_posix()})


@training_app.command("export-evals")
def training_export_evals() -> None:
    settings = load_settings()
    _echo({"path": export_training(settings.project_root, kind="evals").as_posix()})


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
