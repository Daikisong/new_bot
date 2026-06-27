from __future__ import annotations

import json
from datetime import date, datetime, time

from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.records.models import (
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.records.store import BrainRecordStore, audit_record_store
from news_scalping_lab.retrieval.embedding import DeterministicHashEmbeddingProvider
from news_scalping_lab.retrieval.store import LocalRetrievalStore, inspect_vector_index
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, canonical_json, sha256_text


def _episode(
    episode_id: str,
    *,
    summary: str,
    mechanism: str,
    available_at: datetime,
) -> ResearchEpisode:
    trade_day = date(2030, 1, 9)
    return ResearchEpisode(
        episode_id=episode_id,
        trade_date=trade_day,
        cutoff_at=datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST),
        created_at=datetime.combine(trade_day, time(16, 0, 0), tzinfo=KST),
        research_version="test-v1",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(
            summary=summary,
            open_world_mechanisms=[mechanism],
        ),
        available_from=available_at,
    )


def _store_retrieval_records(tmp_path) -> None:
    records = [
        _retrieval_record(
            "BRAIN-REC-DIRECT",
            record_type="supervised_direct_event_case",
            ticker="000001",
            theme_id="theme-direct",
            response_class="positive_high10",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-COUNTER",
            record_type="counterexample",
            ticker="000002",
            theme_id="theme-counter",
            response_class="negative_control",
            available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-GEN-ERROR",
            record_type="candidate_generation_error_case",
            ticker="000003",
            theme_id="theme-error",
            response_class="candidate_missed",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-RANK-ERROR",
            record_type="candidate_ranking_error_case",
            ticker="000004",
            theme_id="theme-error",
            response_class="leader_missed",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-ROW-ERROR",
            record_type="row_disposition_error_case",
            ticker="000005",
            theme_id="theme-error",
            response_class="row_misclassified",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-ENTITY-ERROR",
            record_type="entity_resolution_error_case",
            ticker="000006",
            theme_id="theme-error",
            response_class="entity_misresolved",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-LEADER-PAIR",
            record_type="blind_leader_preference_pair",
            ticker="",
            theme_id="",
            response_class="",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
            payload_updates={
                "company_name": None,
                "path_type": None,
                "blind_preferred_ticker": "000007",
                "blind_rejected_ticker": "000008",
                "outcome_winner_ticker": "000007",
                "candidate_path_type": "continuation",
                "D_outcome": {
                    "ticker": "000007",
                    "company_name_on_D": "Nested Winner Co",
                    "response_class": "positive_high10",
                },
            },
        ),
    ]
    raw_payload = "\n".join(record.model_dump_json() for record in records)
    raw_sha = sha256_text(raw_payload)
    source_path = tmp_path / "record_retrieval_bundle.md"
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(tmp_path).store_bundle(
        source_path=source_path,
        envelope=ResearchBundleEnvelope(
            bundle_schema_version="nslab.research_bundle.v11",
            manifest_schema_version="nslab.bundle_manifest.v11",
            episode_schema_version="nslab.research_episode.v11",
            episode_id="NSLAB-20300110-RETRIEVAL",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_bundle_sha256=raw_sha,
            raw_block_hashes={"brain_delta.jsonl": raw_sha},
            raw_block_counts={"brain_delta.jsonl": len(records)},
            provenance_closure_status="closed",
            adapter_name="unit-test",
            import_status="imported",
        ),
        index=NormalizedEpisodeIndex(
            episode_id="NSLAB-20300110-RETRIEVAL",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_block_names=["brain_delta.jsonl"],
            record_ids=[record.record_id for record in records],
            record_count_by_type={
                "supervised_direct_event_case": 1,
                "counterexample": 1,
                "candidate_generation_error_case": 1,
                "candidate_ranking_error_case": 1,
                "row_disposition_error_case": 1,
                "entity_resolution_error_case": 1,
                "blind_leader_preference_pair": 1,
            },
            training_eligible_record_count=6,
            source_ids=["SRC-RETRIEVAL"],
        ),
        records=records,
        raw_blocks={"brain_delta.jsonl": raw_payload},
        validation_report={"passed": True},
    )


def _retrieval_record(
    record_id: str,
    *,
    record_type: str,
    ticker: str,
    theme_id: str,
    response_class: str,
    available_from: datetime,
    payload_updates: dict[str, object] | None = None,
) -> BrainRecordEnvelope:
    payload = {
        "record_id": record_id,
        "record_type": record_type,
        "episode_id": "NSLAB-20300110-RETRIEVAL",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "training_target": "direct_event_response",
        "evidence_phase": "BLIND_SAFE",
        "ticker": ticker,
        "company_name": f"{ticker} Test Co",
        "theme_id": theme_id,
        "path_type": "single_event",
        "response_class": response_class,
    }
    if payload_updates:
        payload.update(payload_updates)
    raw_payload_hash = sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    normalized_payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id="NSLAB-20300110-RETRIEVAL",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target="direct_event_response",
        evidence_phase="BLIND_SAFE",
        training_eligible=record_type != "counterexample",
        eligibility_reason="unit test retrieval record",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=["SRC-RETRIEVAL"],
        raw_payload_sha256=raw_payload_hash,
        normalized_payload_sha256=normalized_payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )


def test_catalog_only_record_store_audit_checks_raw_block_hash(tmp_path) -> None:
    available_from = datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST)
    payload = {
        "record_id": "BRAIN-CATALOG-ONLY",
        "record_type": "memory_claim",
        "episode_id": "EP-catalog-only",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "training_target": "legacy_catalog_only",
        "evidence_phase": "AUDIT",
        "summary": "Catalog-only legacy memory.",
    }
    payload_sha = sha256_text(canonical_json(payload))
    record = BrainRecordEnvelope(
        record_id="BRAIN-CATALOG-ONLY",
        record_type="memory_claim",
        episode_id="EP-catalog-only",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target="legacy_catalog_only",
        evidence_phase="AUDIT",
        training_eligible=False,
        eligibility_reason="catalog-only unit test",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=["EP-catalog-only:accepted_episode"],
        raw_payload_sha256=payload_sha,
        normalized_payload_sha256=payload_sha,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="legacy_research_episode.json",
        source_line=None,
        payload=payload,
    )
    raw_payload = canonical_json(payload)
    raw_sha = sha256_text(raw_payload)
    source_path = tmp_path / "legacy_episode.json"
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(tmp_path).store_bundle(
        source_path=source_path,
        envelope=ResearchBundleEnvelope(
            bundle_schema_version="nslab.legacy_research_episode.v1",
            manifest_schema_version=None,
            episode_schema_version="nslab.research_episode.v1",
            episode_id="EP-catalog-only",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=available_from,
            bundle_status="LEGACY_ACCEPTED",
            blind_valid=True,
            raw_bundle_sha256=raw_sha,
            raw_block_hashes={"legacy_research_episode.json": raw_sha},
            raw_block_counts={"legacy_research_episode.json": 1},
            provenance_closure_status="legacy_catalog_only",
            adapter_name="legacy-migration",
            import_status="catalog_only",
        ),
        index=NormalizedEpisodeIndex(
            episode_id="EP-catalog-only",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=available_from,
            bundle_status="LEGACY_ACCEPTED",
            blind_valid=True,
            raw_block_names=["legacy_research_episode.json"],
            record_ids=[record.record_id],
            record_count_by_type={"memory_claim": 1},
            training_eligible_record_count=0,
            source_ids=["EP-catalog-only:accepted_episode"],
        ),
        records=[record],
        raw_blocks={"legacy_research_episode.json": raw_payload},
        validation_report={"passed": True, "catalog_only": True},
    )
    raw_block_path = (
        tmp_path
        / "research"
        / "episodes"
        / "EP-catalog-only"
        / "raw_blocks"
        / "legacy_research_episode.json"
    )
    raw_block_path.write_text('{"tampered": true}', encoding="utf-8")

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["raw_block_hash_mismatch_episode_ids"] == ["EP-catalog-only"]


def _store_single_edge_record(
    tmp_path,
    *,
    path_type: str,
    edge_origin: str = "CSV_INPUT",
    source_time_verified: bool = True,
    available_before_cutoff: bool = True,
    ledger_time_verified: bool = True,
    ledger_available_before_cutoff: bool = True,
) -> None:
    available_from = datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST)
    payload = {
        "record_id": "BRAIN-EDGE",
        "record_type": "event_ticker_edge",
        "episode_id": "NSLAB-20300110-EDGE",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "training_target": "edge_memory",
        "evidence_phase": "BLIND_SAFE",
        "edge_id": "EDGE-1",
        "event_id": "EVT-1",
        "ticker": "000001",
        "company_name": "Edge Test Co",
        "relation_class": "DIRECT",
        "path_type": path_type,
        "edge_origin": edge_origin,
        "source_time_verified": source_time_verified,
        "available_before_cutoff": available_before_cutoff,
        "training_eligible": True,
        "eligibility_reason": "unit test edge record",
    }
    raw_payload_hash = sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    normalized_payload_hash = sha256_text(canonical_json(payload))
    record = BrainRecordEnvelope(
        record_id="BRAIN-EDGE",
        record_type="event_ticker_edge",
        episode_id="NSLAB-20300110-EDGE",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target="edge_memory",
        evidence_phase="BLIND_SAFE",
        training_eligible=True,
        eligibility_reason="unit test edge record",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=["SRC-EDGE"],
        raw_payload_sha256=raw_payload_hash,
        normalized_payload_sha256=normalized_payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )
    raw_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    source_ledger_payload = json.dumps(
        {
            "source_id": "SRC-EDGE",
            "event_ids": ["EVT-1"],
            "time_verified": ledger_time_verified,
            "available_before_cutoff": ledger_available_before_cutoff,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    raw_sha = sha256_text(raw_payload)
    source_ledger_sha = sha256_text(source_ledger_payload)
    source_path = tmp_path / "edge_bundle.md"
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(tmp_path).store_bundle(
        source_path=source_path,
        envelope=ResearchBundleEnvelope(
            bundle_schema_version="nslab.research_bundle.v11",
            manifest_schema_version="nslab.bundle_manifest.v11",
            episode_schema_version="nslab.research_episode.v11",
            episode_id="NSLAB-20300110-EDGE",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=available_from,
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_bundle_sha256=raw_sha,
            raw_block_hashes={
                "brain_delta.jsonl": raw_sha,
                "source_ledger.jsonl": source_ledger_sha,
            },
            raw_block_counts={"brain_delta.jsonl": 1, "source_ledger.jsonl": 1},
            provenance_closure_status="closed",
            adapter_name="unit-test",
            import_status="imported",
        ),
        index=NormalizedEpisodeIndex(
            episode_id="NSLAB-20300110-EDGE",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=available_from,
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_block_names=["brain_delta.jsonl", "source_ledger.jsonl"],
            record_ids=["BRAIN-EDGE"],
            record_count_by_type={"event_ticker_edge": 1},
            training_eligible_record_count=1,
            source_ids=["SRC-EDGE"],
        ),
        records=[record],
        raw_blocks={
            "brain_delta.jsonl": raw_payload,
            "source_ledger.jsonl": source_ledger_payload,
        },
        validation_report={"passed": True},
    )


def _store_single_company_memory_delta_record(
    tmp_path,
    *,
    available_from: datetime,
    known_at: str,
) -> None:
    payload = {
        "record_id": "BRAIN-COMPANY",
        "record_type": "company_memory_delta",
        "episode_id": "NSLAB-20300110-COMPANY",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "training_target": "company_memory",
        "evidence_phase": "BLIND_SAFE",
        "ticker": "000001",
        "company_name": "Company Memory Co",
        "known_at": known_at,
        "business_descriptions": ["Verified business line"],
        "training_eligible": False,
        "eligibility_reason": "company memory delta is audit memory",
    }
    raw_payload_hash = sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    normalized_payload_hash = sha256_text(canonical_json(payload))
    record = BrainRecordEnvelope(
        record_id="BRAIN-COMPANY",
        record_type="company_memory_delta",
        episode_id="NSLAB-20300110-COMPANY",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target="company_memory",
        evidence_phase="BLIND_SAFE",
        training_eligible=False,
        eligibility_reason="company memory delta is audit memory",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=[],
        raw_payload_sha256=raw_payload_hash,
        normalized_payload_sha256=normalized_payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )
    raw_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    raw_sha = sha256_text(raw_payload)
    source_path = tmp_path / "company_memory_delta_bundle.md"
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(tmp_path).store_bundle(
        source_path=source_path,
        envelope=ResearchBundleEnvelope(
            bundle_schema_version="nslab.research_bundle.v11",
            manifest_schema_version="nslab.bundle_manifest.v11",
            episode_schema_version="nslab.research_episode.v11",
            episode_id="NSLAB-20300110-COMPANY",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=available_from,
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_bundle_sha256=raw_sha,
            raw_block_hashes={"brain_delta.jsonl": raw_sha},
            raw_block_counts={"brain_delta.jsonl": 1},
            provenance_closure_status="closed",
            adapter_name="unit-test",
            import_status="imported",
        ),
        index=NormalizedEpisodeIndex(
            episode_id="NSLAB-20300110-COMPANY",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=available_from,
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_block_names=["brain_delta.jsonl"],
            record_ids=["BRAIN-COMPANY"],
            record_count_by_type={"company_memory_delta": 1},
            training_eligible_record_count=0,
            source_ids=[],
        ),
        records=[record],
        raw_blocks={"brain_delta.jsonl": raw_payload},
        validation_report={"passed": True},
    )


def _store_single_issuer_day_record(
    tmp_path,
    *,
    event_level_weights: dict[str, object],
) -> None:
    available_from = datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST)
    payload = {
        "record_id": "BRAIN-ISSUER",
        "record_type": "supervised_issuer_day_case",
        "episode_id": "NSLAB-20300110-ISSUER",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "training_target": "issuer_day_price_response",
        "evidence_phase": "POSTMORTEM",
        "issuer_day_case_id": "20300110:000001",
        "ticker": "000001",
        "company_name": "Issuer Weight Co",
        "event_ids": sorted(event_level_weights),
        "sample_weight": 1.0,
        "event_level_weights": event_level_weights,
        "D_outcome": {"label_quality": "verified"},
        "training_eligible": True,
        "eligibility_reason": "unit test issuer-day record",
    }
    raw_payload_hash = sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    normalized_payload_hash = sha256_text(canonical_json(payload))
    record = BrainRecordEnvelope(
        record_id="BRAIN-ISSUER",
        record_type="supervised_issuer_day_case",
        episode_id="NSLAB-20300110-ISSUER",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target="issuer_day_price_response",
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        eligibility_reason="unit test issuer-day record",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=["SRC-ISSUER"],
        raw_payload_sha256=raw_payload_hash,
        normalized_payload_sha256=normalized_payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )
    raw_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    source_ledger_payload = json.dumps(
        {"source_id": "SRC-ISSUER", "event_ids": sorted(event_level_weights)},
        ensure_ascii=False,
        sort_keys=True,
    )
    raw_sha = sha256_text(raw_payload)
    source_ledger_sha = sha256_text(source_ledger_payload)
    source_path = tmp_path / "issuer_day_bundle.md"
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(tmp_path).store_bundle(
        source_path=source_path,
        envelope=ResearchBundleEnvelope(
            bundle_schema_version="nslab.research_bundle.v11",
            manifest_schema_version="nslab.bundle_manifest.v11",
            episode_schema_version="nslab.research_episode.v11",
            episode_id="NSLAB-20300110-ISSUER",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=available_from,
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_bundle_sha256=raw_sha,
            raw_block_hashes={
                "brain_delta.jsonl": raw_sha,
                "source_ledger.jsonl": source_ledger_sha,
            },
            raw_block_counts={"brain_delta.jsonl": 1, "source_ledger.jsonl": 1},
            provenance_closure_status="closed",
            adapter_name="unit-test",
            import_status="imported",
        ),
        index=NormalizedEpisodeIndex(
            episode_id="NSLAB-20300110-ISSUER",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=available_from,
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_block_names=["brain_delta.jsonl", "source_ledger.jsonl"],
            record_ids=["BRAIN-ISSUER"],
            record_count_by_type={"supervised_issuer_day_case": 1},
            training_eligible_record_count=1,
            source_ids=["SRC-ISSUER"],
        ),
        records=[record],
        raw_blocks={
            "brain_delta.jsonl": raw_payload,
            "source_ledger.jsonl": source_ledger_payload,
        },
        validation_report={"passed": True},
    )


def test_record_store_audit_rejects_invalid_event_ticker_edge_path_type(tmp_path) -> None:
    _store_single_edge_record(tmp_path, path_type="OUTCOME_ONLY")

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["invalid_event_ticker_edge_path_type_record_ids"] == ["BRAIN-EDGE"]
    assert "event_ticker_edge path_type values are invalid" in audit["findings"]


def test_record_store_audit_accepts_documented_event_ticker_edge_path_types(tmp_path) -> None:
    _store_single_edge_record(tmp_path, path_type="market_memory")

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is True
    assert audit["invalid_event_ticker_edge_path_type_record_ids"] == []
    assert audit["event_ticker_edge_cutoff_provenance_violation_record_ids"] == []


def test_record_store_audit_rejects_outcome_only_training_eligible_edge(
    tmp_path,
) -> None:
    _store_single_edge_record(
        tmp_path,
        path_type="MARKET_MEMORY",
        edge_origin="OUTCOME_ONLY_ASSOCIATION",
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["event_ticker_edge_cutoff_provenance_violation_record_ids"] == [
        "BRAIN-EDGE"
    ]
    assert (
        "training-eligible event_ticker_edge records require cutoff provenance"
        in audit["findings"]
    )


def test_record_store_audit_rejects_after_cutoff_training_eligible_edge(
    tmp_path,
) -> None:
    _store_single_edge_record(
        tmp_path,
        path_type="FUNDAMENTAL",
        edge_origin="AFTER_CUTOFF_SOURCE",
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["event_ticker_edge_cutoff_provenance_violation_record_ids"] == [
        "BRAIN-EDGE"
    ]


def test_record_store_audit_rejects_unverified_training_eligible_edge(
    tmp_path,
) -> None:
    _store_single_edge_record(
        tmp_path,
        path_type="DIRECT",
        source_time_verified=False,
        available_before_cutoff=True,
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["event_ticker_edge_cutoff_provenance_violation_record_ids"] == [
        "BRAIN-EDGE"
    ]


def test_record_store_audit_rejects_edge_without_cutoff_safe_source_ledger(
    tmp_path,
) -> None:
    _store_single_edge_record(
        tmp_path,
        path_type="DIRECT",
        source_time_verified=True,
        available_before_cutoff=True,
        ledger_time_verified=True,
        ledger_available_before_cutoff=False,
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["event_ticker_edge_cutoff_provenance_violation_record_ids"] == []
    assert audit["event_ticker_edge_source_ledger_cutoff_violation_record_ids"] == [
        "BRAIN-EDGE"
    ]
    assert (
        "training-eligible event_ticker_edge provenance sources must be cutoff-safe"
        in audit["findings"]
    )


def test_record_store_audit_rejects_backdated_company_memory_delta_known_at(
    tmp_path,
) -> None:
    _store_single_company_memory_delta_record(
        tmp_path,
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
        known_at="2030-01-10T08:00:00+09:00",
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["backdated_company_memory_delta_known_at_record_ids"] == [
        "BRAIN-COMPANY"
    ]
    assert (
        "company_memory_delta known_at values precede record available_from"
        in audit["findings"]
    )


def test_record_store_audit_rejects_naive_company_memory_delta_known_at(
    tmp_path,
) -> None:
    _store_single_company_memory_delta_record(
        tmp_path,
        available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
        known_at="2030-01-10T08:30:00",
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["invalid_company_memory_delta_known_at_record_ids"] == [
        "BRAIN-COMPANY"
    ]
    assert "company_memory_delta known_at values are invalid" in audit["findings"]


def test_record_store_audit_accepts_temporal_company_memory_delta_known_at(
    tmp_path,
) -> None:
    _store_single_company_memory_delta_record(
        tmp_path,
        available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
        known_at="2030-01-10T08:30:00+09:00",
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is True
    assert audit["invalid_company_memory_delta_known_at_record_ids"] == []
    assert audit["backdated_company_memory_delta_known_at_record_ids"] == []


def test_record_store_audit_rejects_issuer_day_event_level_weight_mismatch(
    tmp_path,
) -> None:
    _store_single_issuer_day_record(
        tmp_path,
        event_level_weights={"EVT-1": 0.25, "EVT-2": 0.5},
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["issuer_day_event_level_weight_mismatch_record_ids"] == [
        "BRAIN-ISSUER"
    ]
    assert "issuer-day event_level_weights must sum to 1" in audit["findings"]


def test_record_store_audit_accepts_balanced_issuer_day_event_level_weights(
    tmp_path,
) -> None:
    _store_single_issuer_day_record(
        tmp_path,
        event_level_weights={"EVT-1": 0.25, "EVT-2": 0.75},
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is True
    assert audit["issuer_day_event_level_weight_mismatch_record_ids"] == []


def test_local_memory_store_adds_and_lists_accepted_episode(tmp_path) -> None:
    memory = LocalRetrievalStore(tmp_path)
    episode = _episode(
        "EP-memory",
        summary="Accepted memory summary.",
        mechanism="current event -> open-world path",
        available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
    )

    memory.add_episode(episode)

    assert [item.episode_id for item in memory.list_all_episodes()] == ["EP-memory"]
    assert (tmp_path / "research" / "accepted" / "EP-memory.json").exists()
    assert (tmp_path / "memory" / "vector_index" / "manifest.json").exists()
    assert (tmp_path / "memory" / "vector_index" / "episodes.jsonl").exists()
    index = memory.inspect_index()
    assert index["status"] == "current"
    assert index["record_count"] == 1


def test_local_memory_store_filters_available_as_of_cutoff(tmp_path) -> None:
    memory = LocalRetrievalStore(tmp_path)
    available = _episode(
        "EP-available",
        summary="Available before cutoff.",
        mechanism="available mechanism",
        available_at=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
    )
    future = _episode(
        "EP-future",
        summary="Unavailable after cutoff.",
        mechanism="future mechanism",
        available_at=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
    )
    memory.add_episode(available)
    memory.add_episode(future)

    as_of = memory.get_available_as_of(datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST))

    assert [episode.episode_id for episode in as_of] == ["EP-available"]


def test_semantic_search_keeps_research_available_without_exact_keyword_gate(tmp_path) -> None:
    memory = LocalRetrievalStore(tmp_path)
    memory.add_episode(
        _episode(
            "EP-abstract",
            summary="Past case about indirect supply-chain beneficiary discovery.",
            mechanism="new catalyst -> infer adjacent infrastructure demand",
            available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
    )

    assert memory.search_semantic("unseen wording with no shared tokens", limit=5) == [
        "EP-abstract"
    ]
    assert LocalRetrievalStore(tmp_path, force_empty=True).search_semantic(
        "unseen wording with no shared tokens", limit=5
    ) == []


def test_record_retrieval_supports_structural_filters(tmp_path) -> None:
    _store_retrieval_records(tmp_path)
    memory = LocalRetrievalStore(tmp_path)
    memory.rebuild_index()

    assert memory.search_records(
        "unseen wording",
        record_type="supervised_direct_event_case",
        training_target="direct_event_response",
        trade_date_from="2030-01-10",
        trade_date_to="2030-01-10",
        ticker="000001",
        company_name="000001 Test Co",
        theme_id="theme-direct",
        path_type="single_event",
        response_class="positive_high10",
        evidence_phase="BLIND_SAFE",
        confidence_label="low",
        training_eligible=True,
        available_from=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    ) == ["BRAIN-REC-DIRECT"]
    assert memory.search_records(
        "unseen wording",
        record_type="counterexample",
        training_eligible=False,
        available_from=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    ) == []
    assert memory.search_records(
        "unseen wording",
        record_type="counterexample",
        training_eligible=False,
        available_from=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
    ) == ["BRAIN-REC-COUNTER"]
    assert memory.search_records(
        "unseen wording",
        record_type="supervised_direct_event_case",
        trade_date_from="2030-01-11",
        trade_date_to="2030-01-11",
        training_eligible=True,
        available_from=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
    ) == []
    assert set(
        memory.search_records(
            "unseen wording",
            record_type=(
                "candidate_generation_error_case",
                "candidate_ranking_error_case",
                "row_disposition_error_case",
                "entity_resolution_error_case",
            ),
            theme_id="theme-error",
            available_from=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            limit=10,
        )
    ) == {
        "BRAIN-REC-GEN-ERROR",
        "BRAIN-REC-RANK-ERROR",
        "BRAIN-REC-ROW-ERROR",
        "BRAIN-REC-ENTITY-ERROR",
    }


def test_record_retrieval_filters_alias_and_nested_payload_fields(tmp_path) -> None:
    _store_retrieval_records(tmp_path)
    memory = LocalRetrievalStore(tmp_path)
    memory.rebuild_index()

    assert memory.search_records(
        "unseen wording",
        record_type="blind_leader_preference_pair",
        ticker="000008",
        path_type="continuation",
        available_from=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    ) == ["BRAIN-REC-LEADER-PAIR"]
    assert memory.search_records(
        "unseen wording",
        record_type="blind_leader_preference_pair",
        ticker="000007",
        company_name="Nested Winner Co",
        response_class="positive_high10",
        available_from=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    ) == ["BRAIN-REC-LEADER-PAIR"]


def test_vector_index_marks_stale_when_accepted_episode_changes_without_rebuild(tmp_path) -> None:
    memory = LocalRetrievalStore(tmp_path)
    memory.add_episode(
        _episode(
            "EP-indexed",
            summary="Indexed memory summary.",
            mechanism="indexed mechanism",
            available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
    )
    store = ResearchStore(tmp_path)
    second = _episode(
        "EP-new",
        summary="New accepted memory summary.",
        mechanism="new mechanism",
        available_at=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    store.save_episode(second)
    store.accept(second.episode_id)

    stale = inspect_vector_index(tmp_path)
    rebuilt = memory.rebuild_index()

    assert stale["status"] == "stale"
    assert rebuilt["record_count"] == 2
    assert inspect_vector_index(tmp_path)["status"] == "current"


def test_local_retrieval_store_uses_injected_embedding_provider(tmp_path) -> None:
    class RecordingEmbeddingProvider(DeterministicHashEmbeddingProvider):
        embedding_method = "recording_hashing_v1"

        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(list(texts))
            return super().embed_texts(texts)

    provider = RecordingEmbeddingProvider()
    memory = LocalRetrievalStore(tmp_path, embedding_provider=provider)
    memory.add_episode(
        _episode(
            "EP-provider",
            summary="Provider-backed memory summary.",
            mechanism="provider mechanism",
            available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
    )

    result = memory.search_semantic("provider query", limit=5)
    index = memory.inspect_index()

    assert result == ["EP-provider"]
    assert index["embedding_method"] == "recording_hashing_v1"
    assert provider.calls[0][0].startswith("EP-provider")
    assert provider.calls[-1] == ["provider query"]


def test_vector_index_supports_provider_specific_dimensions(tmp_path) -> None:
    class TwoDimensionalEmbeddingProvider:
        embedding_method = "two_dimensional_provider_v1"
        dimensions = 2

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(text)), 1.0] for text in texts]

        async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
            return self.embed_texts(texts)

    memory = LocalRetrievalStore(
        tmp_path,
        embedding_provider=TwoDimensionalEmbeddingProvider(),
    )
    memory.add_episode(
        _episode(
            "EP-two-dim",
            summary="Two dimensional provider summary.",
            mechanism="provider-specific embedding dimensions",
            available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
    )

    index = memory.inspect_index()

    assert index["status"] == "current"
    assert index["embedding_method"] == "two_dimensional_provider_v1"
    assert index["dimensions"] == 2
    assert memory.search_semantic("two dimensional query", limit=1) == ["EP-two-dim"]
