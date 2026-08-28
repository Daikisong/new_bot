from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from news_scalping_lab.context.memory_coverage import (
    build_memory_coverage_manifest_from_snapshot,
    inspect_memory_coverage_manifest,
)
from news_scalping_lab.evaluation.replay_snapshot import (
    ReplayAvailability,
    build_shadow_as_of_snapshot,
    replay_available_from,
    replay_record_is_available,
)
from news_scalping_lab.memory.index import (
    ProductionMemoryIndex,
    ReplayAvailabilityOverride,
    inspect_memory_snapshot,
    inspect_verified_evaluation_memory_index,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import (
    AsyncEmbeddingProviderAdapter,
    DeterministicHashEmbeddingProvider,
)
from news_scalping_lab.utils import KST, canonical_json, sha256_text


class _Backend:
    def __init__(self) -> None:
        self.embedded = 0

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        self.embedded += len(texts)
        return DeterministicHashEmbeddingProvider().embed_texts(texts)


def _provider(backend: _Backend) -> AsyncEmbeddingProviderAdapter:
    return AsyncEmbeddingProviderAdapter(
        backend,
        embedding_method="llm_embedding:test:replay-v1",
        production_capability_attested=True,
    )


def _record(record_id: str, *, trade_date: date, available_from: datetime) -> BrainRecordEnvelope:
    payload = {
        "record_type": "supervised_direct_event_case",
        "ticker": record_id[-6:],
        "company_name": "Generic issuer",
        "title": "supply agreement confirmed",
        "event_id": f"EVENT-{record_id}",
        "response_class": "POSITIVE",
        "high_return_pct": 12.0,
        "label_quality": "verified",
    }
    digest = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="supervised_direct_event_case",
        episode_id=f"EP-{trade_date.isoformat()}",
        trade_date=trade_date,
        available_from=available_from,
        training_target="direct_event_response",
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        status="supported",
        confidence_label="high",
        provenance_source_ids=[f"SRC-{record_id}"],
        raw_payload_sha256=digest,
        normalized_payload_sha256=digest,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=payload,
    )


def _source_index(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProductionMemoryIndex, _Backend, list[BrainRecordEnvelope]]:
    records = [
        _record(
            "REC-BUILD",
            trade_date=date(2030, 1, 2),
            available_from=datetime(2030, 1, 10, tzinfo=KST),
        ),
        _record(
            "REC-HOLDOUT",
            trade_date=date(2030, 1, 9),
            available_from=datetime(2030, 1, 10, tzinfo=KST),
        ),
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    BrainRecordStore(root).rebuild_indexes()
    backend = _Backend()
    index = ProductionMemoryIndex(
        root,
        embedding_provider=_provider(backend),
        production=True,
    )
    index.build(as_of=datetime(2030, 1, 10, 12, tzinfo=KST))
    return index, backend, records


def _replay_projection(
    records: list[BrainRecordEnvelope],
) -> dict[str, ReplayAvailabilityOverride]:
    return {
        record.episode_id: ReplayAvailabilityOverride(
            episode_id=record.episode_id,
            source_trade_date=record.trade_date,
            replay_available_from=datetime.combine(
                record.trade_date + timedelta(days=1),
                time(0, 0),
                tzinfo=KST,
            ),
            derivation="UNIT_TEST_EXPLICIT",
        )
        for record in records
    }


def test_historical_replay_excludes_future_trade_dates() -> None:
    availability = ReplayAvailability(
        record_id="REC-1",
        source_trade_date=date(2030, 1, 9),
        replay_available_from=replay_available_from(
            source_trade_date=date(2030, 1, 9),
            next_actual_trading_day=date(2030, 1, 10),
        ),
    )

    assert not replay_record_is_available(
        availability,
        replay_trade_date=date(2030, 1, 9),
    )
    assert replay_record_is_available(
        availability,
        replay_trade_date=date(2030, 1, 10),
    )


def test_holdout_records_not_in_build_brain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _backend, records = _source_index(tmp_path, monkeypatch)
    source = index.resolve_snapshot(cutoff_at=datetime(2030, 1, 10, 12, tzinfo=KST))

    result = build_shadow_as_of_snapshot(
        tmp_path,
        memory_index=index,
        build_cutoff=datetime(2030, 1, 5, tzinfo=KST),
        source_snapshot_id=source.snapshot_id,
        holdout_record_ids={"REC-HOLDOUT"},
        replay_availability_by_episode=_replay_projection(records),
    )

    assert result.receipt["holdout_overlap_count"] == 0
    assert result.memory_snapshot.record_count == 1
    assert result.memory_snapshot.excluded_future_record_count == 1
    assert result.memory_snapshot.availability_mode == "replay_available_from"
    assert result.memory_snapshot.evaluation_only is True
    inspection = inspect_memory_snapshot(
        tmp_path,
        result.memory_snapshot.snapshot_id,
    )
    assert inspection["status"] == "current_as_of", inspection["errors"]
    index.activate_verified_evaluation_snapshot(
        result.memory_snapshot,
        receipt_path=result.receipt_path,
    )
    resolved, effective = index.effective_available_from_for_records(
        ["REC-BUILD"],
        cutoff_at=datetime(2030, 1, 5, tzinfo=KST),
    )
    assert resolved.snapshot_id == result.memory_snapshot.snapshot_id
    assert effective == {
        "REC-BUILD": datetime(2030, 1, 3, tzinfo=KST),
    }
    assert inspect_verified_evaluation_memory_index(tmp_path)["passed"] is True
    assert (
        index.resolve_snapshot(cutoff_at=datetime(2030, 1, 20, tzinfo=KST)).snapshot_id
        == result.memory_snapshot.snapshot_id
    )
    monkeypatch.setattr(
        BrainRecordStore,
        "list_records",
        lambda self: (_ for _ in ()).throw(AssertionError("evaluation coverage must not scan the source corpus")),
    )
    coverage = build_memory_coverage_manifest_from_snapshot(
        tmp_path,
        snapshot=result.memory_snapshot,
        cutoff_at=datetime(2030, 1, 5, tzinfo=KST),
        run_id="REPLAY-COVERAGE-TEST",
    )
    assert coverage.manifest.accepted_record_count == 1
    assert coverage.manifest.corpus_manifest_sha256 == result.memory_snapshot.corpus_manifest_sha256
    context = {
        "run_id": "REPLAY-COVERAGE-TEST",
        "cutoff_at": datetime(2030, 1, 5, tzinfo=KST).isoformat(),
        "memory_coverage_manifest_artifact": coverage.manifest_path,
        "memory_coverage_manifest_sha256": coverage.manifest_sha256,
        "accepted_record_count": 1,
        "available_record_count": 1,
        "available_record_ids": ["REC-BUILD"],
    }
    assert inspect_memory_coverage_manifest(tmp_path, context)["passed"] is True


def test_full_corpus_centroids_not_used_in_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, backend, records = _source_index(tmp_path, monkeypatch)
    source = index.resolve_snapshot(cutoff_at=datetime(2030, 1, 10, 12, tzinfo=KST))
    embedded_before = backend.embedded

    result = build_shadow_as_of_snapshot(
        tmp_path,
        memory_index=index,
        build_cutoff=datetime(2030, 1, 5, tzinfo=KST),
        source_snapshot_id=source.snapshot_id,
        holdout_record_ids={"REC-HOLDOUT"},
        replay_availability_by_episode=_replay_projection(records),
    )

    assert result.receipt["full_corpus_centroids_used"] is False
    assert result.receipt["centroid_population_record_count"] == 1
    assert result.receipt["source_snapshot_record_count"] == 2
    assert result.receipt["generated_embedding_count"] == 0
    assert backend.embedded == embedded_before


def test_replay_projection_allows_an_episode_with_an_empty_record_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _backend, records = _source_index(tmp_path, monkeypatch)
    source = index.resolve_snapshot(cutoff_at=datetime(2030, 1, 10, 12, tzinfo=KST))
    empty_episode_id = "EP-EMPTY"
    records_dir = tmp_path / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    projection = _replay_projection(records)
    projection[empty_episode_id] = ReplayAvailabilityOverride(
        episode_id=empty_episode_id,
        source_trade_date=date(2030, 1, 2),
        replay_available_from=datetime(2030, 1, 3, tzinfo=KST),
        derivation="UNIT_TEST_EMPTY_RECORD_LEDGER",
    )
    for episode_id in projection:
        (records_dir / f"{episode_id}.jsonl").touch()

    result = build_shadow_as_of_snapshot(
        tmp_path,
        memory_index=index,
        build_cutoff=datetime(2030, 1, 5, tzinfo=KST),
        source_snapshot_id=source.snapshot_id,
        holdout_record_ids={"REC-HOLDOUT"},
        replay_availability_by_episode=projection,
    )

    assert result.memory_snapshot.record_count == 1
    assert result.receipt["availability_projection_episode_count"] == 3
