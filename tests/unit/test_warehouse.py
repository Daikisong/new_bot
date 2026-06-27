from __future__ import annotations

import json
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
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, canonical_json, sha256_text, write_json
from news_scalping_lab.warehouse import (
    EXPECTED_WAREHOUSE_FILES,
    RECORD_COVERAGE_COLUMNS,
    WarehouseStore,
)


def test_warehouse_writes_empty_projection_as_zero_rows(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    counts = WarehouseStore(tmp_path).rebuild_all()
    warehouse = WarehouseStore(tmp_path)
    inspected = warehouse.counts()

    assert counts["research_episodes"] == 0
    assert sorted(inspected) == sorted(EXPECTED_WAREHOUSE_FILES)
    assert inspected["research_episodes.parquet"] == 0
    assert inspected["events.parquet"] == 0
    assert inspected["mechanism_memory.parquet"] == 0
    assert inspected["company_memory.parquet"] == 0
    record_coverage_columns = [
        row[0]
        for row in duckdb.sql(
            "describe select * from "
            f"read_parquet('{(tmp_path / 'warehouse' / 'record_coverage.parquet').as_posix()}')"
        ).fetchall()
    ]
    assert record_coverage_columns == list(RECORD_COVERAGE_COLUMNS)
    assert warehouse.query_brain_records(record_type="supervised_issuer_day_case") == []


def test_record_coverage_projection_keeps_phase_and_training_target_groups(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    records = [
        _warehouse_brain_record(
            "BRAIN-COVERAGE-AUDIT",
            evidence_phase="AUDIT",
            training_target=None,
            training_eligible=False,
        ),
        _warehouse_brain_record(
            "BRAIN-COVERAGE-ELIGIBLE",
            evidence_phase="POSTMORTEM",
            training_target="issuer_day_price_response",
            training_eligible=True,
        ),
    ]

    count = WarehouseStore(tmp_path).write_record_coverage(records)

    assert count == 2
    rows = _query_parquet(
        tmp_path / "warehouse" / "record_coverage.parquet",
        "episode_id, record_type, evidence_phase, training_target, record_count, "
        "training_eligible_record_count, ineligible_record_count, audit_only_record_count",
        order_by="evidence_phase",
    )
    assert rows == [
        ("EP-coverage", "memory_claim", "AUDIT", "UNKNOWN", 1, 0, 1, 1),
        (
            "EP-coverage",
            "memory_claim",
            "POSTMORTEM",
            "issuer_day_price_response",
            1,
            1,
            0,
            0,
        ),
    ]


def test_specialized_record_tables_preserve_theme_and_beneficiary_fields(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    records = [
        _warehouse_payload_record(
            "BRAIN-THEME-RICH",
            record_type="supervised_theme_formation_case",
            training_target="theme_formation_response",
            payload={
                "record_id": "BRAIN-THEME-RICH",
                "record_type": "supervised_theme_formation_case",
                "episode_id": "EP-rich",
                "trade_date": "2030-01-10",
                "theme_id": "THEME-supply",
                "theme_name": "Supply Shock",
                "event_ids": ["EVT-1", "EVT-2"],
                "observation_ids": ["OBS-1"],
                "fact_ids": ["FACT-1"],
                "inference_ids": ["INF-1"],
                "peer_universe": ["000001", "000002"],
                "chosen_leader_ticker": "000001",
                "chosen_leader_company_name": "Leader Co",
                "rejected_candidate_tickers": ["000002"],
                "response_class": "positive_high10",
                "sample_weight": 0.75,
                "label_quality": "verified",
                "attribution_status": "theme_leader",
            },
        ),
        _warehouse_payload_record(
            "BRAIN-BENEFICIARY-RICH",
            record_type="beneficiary_discovery_case",
            training_target="beneficiary_discovery_response",
            payload={
                "record_id": "BRAIN-BENEFICIARY-RICH",
                "record_type": "beneficiary_discovery_case",
                "episode_id": "EP-rich",
                "trade_date": "2030-01-10",
                "case_id": "BEN-1",
                "event_id": "EVT-1",
                "theme_id": "THEME-supply",
                "candidate_ticker": "000003",
                "candidate_company_name": "Beneficiary Co",
                "candidate_path_type": "INFERRED_NEW",
                "beneficiary_relation": "supplier",
                "beneficiary_relation_evidence": ["FACT-2", "INF-2"],
                "blind_candidate_ids": ["CAND-3"],
                "outcome_ticker": "000003",
                "outcome_company_name": "Beneficiary Co",
                "correction_mode": "postmortem_add",
                "sample_weight": 0.25,
            },
        ),
    ]
    warehouse = WarehouseStore(tmp_path)

    assert warehouse.write_theme_formation_cases(records) == 1
    assert warehouse.write_beneficiary_cases(records) == 1

    theme_rows = _query_parquet(
        tmp_path / "warehouse" / "theme_formation_cases.parquet",
        (
            "theme_id, theme_name, event_ids, peer_universe, chosen_leader_ticker, "
            "chosen_leader_company_name, rejected_candidate_tickers, response_class, "
            "sample_weight, label_quality, attribution_status"
        ),
    )
    beneficiary_rows = _query_parquet(
        tmp_path / "warehouse" / "beneficiary_cases.parquet",
        (
            "case_id, event_id, theme_id, candidate_ticker, candidate_company_name, "
            "candidate_path_type, beneficiary_relation, beneficiary_relation_evidence, "
            "blind_candidate_ids, outcome_ticker, outcome_company_name, "
            "correction_mode, sample_weight"
        ),
    )
    assert theme_rows[0][0:2] == ("THEME-supply", "Supply Shock")
    assert json.loads(str(theme_rows[0][2])) == ["EVT-1", "EVT-2"]
    assert json.loads(str(theme_rows[0][3])) == ["000001", "000002"]
    assert theme_rows[0][4:6] == ("000001", "Leader Co")
    assert json.loads(str(theme_rows[0][6])) == ["000002"]
    assert theme_rows[0][7:] == (
        "positive_high10",
        0.75,
        "verified",
        "theme_leader",
    )
    assert beneficiary_rows[0][0:7] == (
        "BEN-1",
        "EVT-1",
        "THEME-supply",
        "000003",
        "Beneficiary Co",
        "INFERRED_NEW",
        "supplier",
    )
    assert json.loads(str(beneficiary_rows[0][7])) == ["FACT-2", "INF-2"]
    assert json.loads(str(beneficiary_rows[0][8])) == ["CAND-3"]
    assert beneficiary_rows[0][9:] == (
        "000003",
        "Beneficiary Co",
        "postmortem_add",
        0.25,
    )


def test_brain_record_query_filters_rich_record_alias_fields(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    records = [
        _warehouse_payload_record(
            "BRAIN-THEME-RICH",
            record_type="supervised_theme_formation_case",
            training_target="theme_formation_response",
            payload={
                "record_id": "BRAIN-THEME-RICH",
                "record_type": "supervised_theme_formation_case",
                "episode_id": "EP-rich",
                "trade_date": "2030-01-10",
                "theme_id": "THEME-supply",
                "peer_universe": ["000001", "000002"],
                "chosen_leader_ticker": "000001",
                "chosen_leader_company_name": "Leader Co",
                "rejected_candidate_tickers": ["000002"],
                "response_class": "positive_high10",
            },
        ),
        _warehouse_payload_record(
            "BRAIN-BENEFICIARY-RICH",
            record_type="beneficiary_discovery_case",
            training_target="beneficiary_discovery_response",
            payload={
                "record_id": "BRAIN-BENEFICIARY-RICH",
                "record_type": "beneficiary_discovery_case",
                "episode_id": "EP-rich",
                "trade_date": "2030-01-10",
                "theme_id": "THEME-supply",
                "candidate_ticker": "000003",
                "candidate_company_name": "Beneficiary Co",
                "outcome_ticker": "000004",
                "outcome_company_name": "Outcome Co",
                "candidate_path_type": "INFERRED_NEW",
            },
        ),
    ]
    warehouse = WarehouseStore(tmp_path)
    warehouse.write_brain_records(records)

    theme_rows = warehouse.query_brain_records(
        record_type="supervised_theme_formation_case",
        ticker="000002",
        company_name="Leader Co",
        theme_id="THEME-supply",
        response_class="positive_high10",
    )
    beneficiary_rows = warehouse.query_brain_records(
        record_type="beneficiary_discovery_case",
        ticker="000004",
        company_name="Outcome Co",
        path_type="INFERRED_NEW",
    )

    assert [row["record_id"] for row in theme_rows] == ["BRAIN-THEME-RICH"]
    assert theme_rows[0]["ticker"] == "000001"
    assert theme_rows[0]["payload"]["rejected_candidate_tickers"] == ["000002"]
    assert [row["record_id"] for row in beneficiary_rows] == [
        "BRAIN-BENEFICIARY-RICH"
    ]
    assert beneficiary_rows[0]["ticker"] == "000003"
    assert beneficiary_rows[0]["payload"]["outcome_ticker"] == "000004"


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


def test_warehouse_projects_daily_outcome_evaluation_metadata(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    report = {
        "schema_version": "nslab.evaluation.v1",
        "execution_protocol_version": "nslab.exhaustive_news_blind_full_market.v5",
        "trade_date": "2030-01-10",
        "created_at": "2030-01-10T16:00:00+09:00",
        "blind_prediction_id": "PRED-warehouse-outcome",
        "blind_prediction_sha256": "c" * 64,
        "outcome_coverage_status": "FULL_MARKET_COMPLETE",
        "outcomes": {
            "Warehouse Outcome Co": {
                "intraday_high_return_pct": 29.5,
                "upper_limit_touched": True,
                "upper_limit_closed": False,
            }
        },
        "performance_metrics": {
            "candidate_count": 1,
            "upper_limit_recall_at_5": 1.0,
            "precision_at_5": 1.0,
        },
        "postmortem": {
            "summary": "Warehouse projection preserves evaluation metadata.",
            "hits": ["Warehouse Outcome Co"],
            "misses": [],
            "false_positives": [],
            "failure_codes": [],
            "lessons": ["Keep evaluation metadata available in derived projections."],
        },
        "eligibility_matrix": {
            "forecast_evaluation_eligible": True,
            "direct_supervised_cases_eligible": True,
            "theme_supervised_cases_eligible": True,
            "leader_pair_training_eligible": False,
            "retrospective_memory_eligible": True,
            "brain_eligible": True,
            "reasons": {
                "leader_pair_training_eligible": "need at least two resolved blind candidates"
            },
        },
    }
    write_json(tmp_path / "reports" / "2030-01-10_postmortem.json", report)

    counts = WarehouseStore(tmp_path).rebuild_all()

    assert counts["daily_outcomes"] == 1
    rows = _query_parquet(
        tmp_path / "warehouse" / "daily_outcomes.parquet",
        (
            "schema_version, execution_protocol_version, trade_date, "
            "blind_prediction_id, blind_prediction_sha256, outcome_count, "
            "outcome_coverage_status, outcomes_json, performance_metrics_json, "
            "postmortem_json, eligibility_matrix_json"
        ),
    )
    row = rows[0]
    assert row[0:7] == (
        "nslab.evaluation.v1",
        "nslab.exhaustive_news_blind_full_market.v5",
        "2030-01-10",
        "PRED-warehouse-outcome",
        "c" * 64,
        1,
        "FULL_MARKET_COMPLETE",
    )
    assert json.loads(str(row[7]))["Warehouse Outcome Co"]["upper_limit_touched"] is True
    assert json.loads(str(row[8]))["upper_limit_recall_at_5"] == 1.0
    assert json.loads(str(row[9]))["hits"] == ["Warehouse Outcome Co"]
    assert json.loads(str(row[10]))["brain_eligible"] is True


def test_previous_trade_day_is_calendar_previous_day() -> None:
    from news_scalping_lab.warehouse import previous_trade_day

    assert previous_trade_day(date(2030, 1, 10)) == date(2030, 1, 9)


def _warehouse_brain_record(
    record_id: str,
    *,
    evidence_phase: str,
    training_target: str | None,
    training_eligible: bool,
) -> BrainRecordEnvelope:
    payload = {
        "record_id": record_id,
        "record_type": "memory_claim",
        "episode_id": "EP-coverage",
        "trade_date": "2030-01-10",
        "statement": f"{record_id} coverage statement",
    }
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="memory_claim",
        episode_id="EP-coverage",
        trade_date=date(2030, 1, 10),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
        training_target=training_target,
        evidence_phase=evidence_phase,
        training_eligible=training_eligible,
        eligibility_reason="warehouse coverage projection test",
        status="supported",
        confidence_label="medium",
        provenance_source_ids=["SRC-coverage"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_line=1,
        payload=payload,
    )


def _warehouse_payload_record(
    record_id: str,
    *,
    record_type: str,
    training_target: str,
    payload: dict[str, object],
) -> BrainRecordEnvelope:
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id="EP-rich",
        trade_date=date(2030, 1, 10),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
        training_target=training_target,
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        eligibility_reason="warehouse rich projection test",
        status="supported",
        confidence_label="medium",
        provenance_source_ids=["SRC-rich"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_line=1,
        payload=dict(payload),
    )


def _query_parquet(
    path: Path,
    columns: str,
    *,
    order_by: str | None = None,
) -> list[tuple[object, ...]]:
    query = f"select {columns} from read_parquet('{path.as_posix()}')"
    if order_by is not None:
        query = f"{query} order by {order_by}"
    return duckdb.sql(query).fetchall()
