from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import duckdb

from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    CompanyMemory,
    EligibilityMatrix,
    MechanismMemory,
    NewsItem,
    Provenance,
    ResearchEpisode,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, write_json
from news_scalping_lab.warehouse import WarehouseStore


def test_warehouse_writes_empty_projection_as_zero_rows(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    counts = WarehouseStore(tmp_path).rebuild_all()
    inspected = WarehouseStore(tmp_path).counts()

    assert counts["research_episodes"] == 0
    assert inspected["research_episodes.parquet"] == 0
    assert inspected["events.parquet"] == 0
    assert inspected["mechanism_memory.parquet"] == 0
    assert inspected["company_memory.parquet"] == 0


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
        execution_protocol_version="nslab.exhaustive_news_blind_full_market.v5",
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
        eligibility_matrix=EligibilityMatrix(
            forecast_evaluation_eligible=True,
            direct_supervised_cases_eligible=True,
            retrospective_memory_eligible=True,
            brain_eligible=True,
        ),
        outcome_coverage_status="PREDICTED_CANDIDATES_ONLY",
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
    research_rows = _query_parquet(
        tmp_path / "warehouse" / "research_episodes.parquet",
        "episode_id, execution_protocol_version, outcome_coverage_status, eligibility_matrix_json",
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
    assert research_rows[0][0:3] == (
        "EP-warehouse-events",
        "nslab.exhaustive_news_blind_full_market.v5",
        "PREDICTED_CANDIDATES_ONLY",
    )
    assert '"forecast_evaluation_eligible": true' in str(research_rows[0][3])


def test_warehouse_projects_mechanism_and_company_memory(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    known_at = datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST)
    provenance = Provenance(
        source_id="SRC-memory-projection",
        source_type="research_episode",
        uri="research/accepted/EP-memory.json",
        content_sha256="b" * 64,
        excerpt="Memory projection source.",
        observed_at=known_at,
    )
    mechanism = MechanismMemory(
        mechanism_id="MM-memory-projection",
        natural_language_description="Supply shock narratives can broaden through verified peers.",
        causal_chain=["event", "peer review", "leader selection"],
        observed_variations=["direct event", "theme peer"],
        successful_cases=["EP-memory"],
        boundary_conditions=["pre-cutoff evidence only"],
        leader_selection_notes=["prefer directness over name similarity"],
        provenance=[provenance],
    )
    mechanisms_dir = tmp_path / "memory" / "mechanisms" / "current"
    mechanisms_dir.mkdir(parents=True, exist_ok=True)
    (mechanisms_dir / "mechanisms.jsonl").write_text(
        mechanism.model_dump_json() + "\n",
        encoding="utf-8",
    )
    company = CompanyMemory(
        ticker="123456",
        company_name="Projection Test Co",
        aliases=["Projection Test"],
        business_descriptions=["Makes fake test components."],
        locations=["Seoul"],
        customers=["Example Customer"],
        supply_chain_roles=["component supplier"],
        prior_market_narratives=["benefits from component shortage"],
        prior_leader_occurrences=["EP-memory"],
        contradictory_relations=["customer concentration risk"],
        known_at=known_at,
        provenance=[provenance],
    )
    write_json(
        tmp_path / "memory" / "company_memory" / "CM-projection-test.json",
        company.model_dump(mode="json"),
    )

    counts = WarehouseStore(tmp_path).rebuild_all()

    assert counts["mechanism_memory"] == 1
    assert counts["company_memory"] == 1
    mechanisms = _query_parquet(
        tmp_path / "warehouse" / "mechanism_memory.parquet",
        "mechanism_id, natural_language_description, successful_cases_json, provenance_json",
    )
    companies = _query_parquet(
        tmp_path / "warehouse" / "company_memory.parquet",
        "ticker, company_name, known_at, aliases_json, prior_market_narratives_json",
    )
    assert mechanisms[0][0:3] == (
        "MM-memory-projection",
        "Supply shock narratives can broaden through verified peers.",
        '["EP-memory"]',
    )
    assert "SRC-memory-projection" in str(mechanisms[0][3])
    assert "research/accepted/EP-memory.json" in str(mechanisms[0][3])
    assert companies == [
        (
            "123456",
            "Projection Test Co",
            "2030-01-11T00:00:00+09:00",
            '["Projection Test"]',
            '["benefits from component shortage"]',
        )
    ]


def test_previous_trade_day_is_calendar_previous_day() -> None:
    from news_scalping_lab.warehouse import previous_trade_day

    assert previous_trade_day(date(2030, 1, 10)) == date(2030, 1, 9)


def _query_parquet(path: Path, columns: str) -> list[tuple[object, ...]]:
    return duckdb.sql(
        f"select {columns} from read_parquet('{path.as_posix()}')"
    ).fetchall()
