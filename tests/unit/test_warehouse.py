from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import duckdb

from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import BlindAnalysis, NewsItem, Provenance, ResearchEpisode
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST
from news_scalping_lab.warehouse import WarehouseStore


def test_warehouse_writes_empty_projection_as_zero_rows(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    counts = WarehouseStore(tmp_path).rebuild_all()
    inspected = WarehouseStore(tmp_path).counts()

    assert counts["research_episodes"] == 0
    assert inspected["research_episodes.parquet"] == 0
    assert inspected["events.parquet"] == 0


def test_warehouse_projects_observed_events_and_event_sources(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    trade_day = date(2030, 1, 10)
    observed_at = datetime.combine(trade_day, time(8, 0, 0), tzinfo=KST)
    provenance = Provenance(
        source_id="SRC-fake-news-row",
        source_type="news_csv_row",
        uri="fake_news.csv#row=1",
        content_sha256="a" * 64,
        excerpt="FakeCatalystCo announces a current catalyst.",
        observed_at=observed_at,
    )
    episode = ResearchEpisode(
        episode_id="EP-warehouse-events",
        trade_date=trade_day,
        cutoff_at=datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST),
        created_at=datetime.combine(trade_day, time(16, 0, 0), tzinfo=KST),
        research_version="warehouse-test-v1",
        price_source_snapshot={"source": "mock"},
        blind_analysis=BlindAnalysis(
            summary="Fake current event was analyzed before outcomes.",
            open_world_mechanisms=["current event -> open-world beneficiary review"],
        ),
        observed_events=[
            NewsItem(
                event_id="EVT-fake-catalyst",
                row_number=1,
                published_at=observed_at,
                title="FakeCatalystCo announces a current catalyst",
                body="A fake company event used only for warehouse projection testing.",
                source_id="SRC-fake-news-row",
                provenance=[provenance],
            )
        ],
        available_from=datetime.combine(date(2030, 1, 11), time(0, 0, 0), tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)

    counts = WarehouseStore(tmp_path).rebuild_all()

    assert counts["events"] == 1
    assert counts["event_sources"] == 1
    events = _query_parquet(
        tmp_path / "warehouse" / "events.parquet",
        "event_id, episode_id, title, source_id",
    )
    sources = _query_parquet(
        tmp_path / "warehouse" / "event_sources.parquet",
        "source_id, event_id, source_type, uri",
    )
    assert events == [
        (
            "EVT-fake-catalyst",
            "EP-warehouse-events",
            "FakeCatalystCo announces a current catalyst",
            "SRC-fake-news-row",
        )
    ]
    assert sources == [
        (
            "SRC-fake-news-row",
            "EVT-fake-catalyst",
            "news_csv_row",
            "fake_news.csv#row=1",
        )
    ]


def test_previous_trade_day_is_calendar_previous_day() -> None:
    from news_scalping_lab.warehouse import previous_trade_day

    assert previous_trade_day(date(2030, 1, 10)) == date(2030, 1, 9)


def _query_parquet(path: Path, columns: str) -> list[tuple[object, ...]]:
    return duckdb.sql(
        f"select {columns} from read_parquet('{path.as_posix()}')"
    ).fetchall()
