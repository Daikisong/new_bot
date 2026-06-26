from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.contracts.models import Candidate, PathType
from news_scalping_lab.memory.company import CompanyMemoryStore
from news_scalping_lab.records.models import (
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    write_json,
)


def _candidate(rank: int, company_name: str, path_type: PathType) -> Candidate:
    return Candidate(
        rank=rank,
        ticker="UNKNOWN",
        company_name=company_name,
        path_type=path_type,
        thesis=f"{company_name} can be investigated from the current event.",
        why_now="The candidate was generated before cutoff.",
        causal_chain=["current catalyst", path_type.value, "company verification"],
        counterarguments=["listing and relation must be checked"],
    )


def test_company_memory_persists_new_candidates_from_every_path_type(tmp_path) -> None:
    prediction_path = tmp_path / "predictions" / "2030-01-10.json"
    write_json(prediction_path, {"prediction_id": "PRED-company-memory"})
    known_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    candidates = [
        _candidate(1, "DirectCo", PathType.SINGLE_EVENT),
        _candidate(2, "BeneficiaryCo", PathType.THEME_BENEFICIARY),
        _candidate(3, "HybridCo", PathType.HYBRID),
        _candidate(4, "ContinuationCo", PathType.CONTINUATION),
        _candidate(5, "BENEFICIARY_DISCOVERY_REQUIRED", PathType.THEME_BENEFICIARY),
        _candidate(6, "D_MINUS_ONE_LEADER_REVIEW", PathType.CONTINUATION),
        _candidate(7, " ", PathType.SINGLE_EVENT),
    ]

    written = CompanyMemoryStore(tmp_path).upsert_from_candidates(
        candidates,
        prediction_path=prediction_path,
        known_at=known_at,
    )

    memories = [read_json(path) for path in sorted(written)]
    assert {memory["company_name"] for memory in memories} == {
        "DirectCo",
        "BeneficiaryCo",
        "HybridCo",
        "ContinuationCo",
    }
    assert all(memory["known_at"] == "2030-01-10T08:59:59+09:00" for memory in memories)
    assert all(
        memory["provenance"][0]["source_type"] == "blind_analysis_company_memory_candidate"
        for memory in memories
    )
    beneficiary_memory = next(
        memory for memory in memories if memory["company_name"] == "BeneficiaryCo"
    )
    assert "THEME_BENEFICIARY" in beneficiary_memory["supply_chain_roles"]


def test_company_memory_candidate_updates_do_not_backfill_future_relations(tmp_path) -> None:
    first_prediction_path = tmp_path / "predictions" / "2030-01-10.json"
    second_prediction_path = tmp_path / "predictions" / "2030-01-11.json"
    write_json(first_prediction_path, {"prediction_id": "PRED-company-memory-early"})
    write_json(second_prediction_path, {"prediction_id": "PRED-company-memory-late"})
    first = _candidate(1, "TemporalCo", PathType.SINGLE_EVENT)
    second = _candidate(1, "TemporalCo", PathType.SINGLE_EVENT).model_copy(
        update={
            "why_now": "A later relation was found after the earlier cutoff.",
            "counterarguments": ["later relation must not appear in earlier memory"],
        }
    )

    store = CompanyMemoryStore(tmp_path)
    first_paths = store.upsert_from_candidates(
        [first],
        prediction_path=first_prediction_path,
        known_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    second_paths = store.upsert_from_candidates(
        [second],
        prediction_path=second_prediction_path,
        known_at=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
    )

    assert first_paths != second_paths
    memories = [read_json(path) for path in sorted(first_paths + second_paths)]
    assert {memory["known_at"] for memory in memories} == {
        "2030-01-10T08:59:59+09:00",
        "2030-01-11T08:59:59+09:00",
    }
    earlier = next(
        memory
        for memory in memories
        if memory["known_at"] == "2030-01-10T08:59:59+09:00"
    )
    assert "later relation must not appear in earlier memory" not in earlier[
        "contradictory_relations"
    ]


def test_company_memory_delta_records_apply_as_temporal_memory(tmp_path) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    available = _company_delta_record(
        "BRAIN-COMPANY-AVAILABLE",
        available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
        known_at="2030-01-10T08:30:00+09:00",
        ticker="DELTA",
        company_name="Delta Memory Co",
        business_descriptions=["Verified pre-cutoff business line"],
        supply_chain_roles=["direct event supplier"],
        contradictory_relations=["customer relation was disputed"],
    )
    future = _company_delta_record(
        "BRAIN-COMPANY-FUTURE",
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
        known_at="2030-01-10T09:30:00+09:00",
        ticker="FUTURE",
        company_name="Future Memory Co",
    )
    _store_company_delta_records(tmp_path, [available, future])

    result = CompanyMemoryStore(tmp_path).apply_record_deltas(as_of=cutoff)

    assert result.processed_record_count == 2
    assert result.written_count == 1
    assert result.skipped_future_record_ids == ["BRAIN-COMPANY-FUTURE"]
    memory = read_json(result.written_paths[0])
    assert memory["ticker"] == "DELTA"
    assert memory["company_name"] == "Delta Memory Co"
    assert memory["known_at"] == "2030-01-10T08:30:00+09:00"
    assert memory["business_descriptions"] == ["Verified pre-cutoff business line"]
    assert memory["supply_chain_roles"] == ["direct event supplier"]
    assert memory["contradictory_relations"] == ["customer relation was disputed"]
    assert memory["provenance"][0]["source_type"] == "company_memory_delta_record"
    assert memory["provenance"][0]["uri"] == "memory/records/EP-company-deltas.jsonl"
    assert memory["provenance"][0]["content_sha256"] == file_sha256(
        tmp_path / "memory" / "records" / "EP-company-deltas.jsonl"
    )


def test_company_memory_delta_cannot_backdate_before_record_available_from(tmp_path) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    record = _company_delta_record(
        "BRAIN-COMPANY-BACKDATED",
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
        known_at="2030-01-10T08:00:00+09:00",
        ticker="BACK",
        company_name="Backdated Memory Co",
    )
    _store_company_delta_records(tmp_path, [record])

    result = CompanyMemoryStore(tmp_path).apply_record_deltas(as_of=cutoff)

    assert result.written_count == 0
    assert result.skipped_future_record_ids == ["BRAIN-COMPANY-BACKDATED"]
    assert list((tmp_path / "memory" / "company_memory").glob("*.json")) == []


def _company_delta_record(
    record_id: str,
    *,
    available_from: datetime,
    known_at: str,
    ticker: str,
    company_name: str,
    business_descriptions: list[str] | None = None,
    supply_chain_roles: list[str] | None = None,
    contradictory_relations: list[str] | None = None,
) -> BrainRecordEnvelope:
    payload = {
        "record_id": record_id,
        "record_type": "company_memory_delta",
        "episode_id": "EP-company-deltas",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "known_at": known_at,
        "ticker": ticker,
        "company_name": company_name,
        "business_descriptions": business_descriptions or [],
        "supply_chain_roles": supply_chain_roles or [],
        "contradictory_relations": contradictory_relations or [],
        "summary": f"{company_name} memory delta",
    }
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="company_memory_delta",
        episode_id="EP-company-deltas",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target="audit_only",
        evidence_phase="POSTMORTEM",
        training_eligible=False,
        eligibility_reason="company memory delta is audit memory, not training source",
        status="supported",
        confidence_label="medium",
        provenance_source_ids=["SRC-company-delta"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )


def _store_company_delta_records(
    root: Path,
    records: list[BrainRecordEnvelope],
) -> None:
    raw_payload = "\n".join(record.model_dump_json() for record in records)
    raw_sha = sha256_text(raw_payload)
    source_path = root / "company_delta_bundle.md"
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(root).store_bundle(
        source_path=source_path,
        envelope=ResearchBundleEnvelope(
            bundle_schema_version="nslab.research_bundle.v11",
            manifest_schema_version="nslab.bundle_manifest.v11",
            episode_schema_version="nslab.research_episode.v11",
            episode_id="EP-company-deltas",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
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
            episode_id="EP-company-deltas",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_block_names=["brain_delta.jsonl"],
            record_ids=[record.record_id for record in records],
            record_count_by_type={"company_memory_delta": len(records)},
            training_eligible_record_count=0,
            source_ids=["SRC-company-delta"],
        ),
        records=records,
        raw_blocks={"brain_delta.jsonl": raw_payload},
        validation_report={"passed": True},
    )
