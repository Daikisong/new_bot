from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

import news_scalping_lab.context.memory_coverage as coverage_module
from news_scalping_lab.context.memory_coverage import (
    build_memory_coverage_manifest,
    inspect_memory_coverage_manifest,
)
from news_scalping_lab.context.sweep import MemorySweeper
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.utils import KST, canonical_json, sha256_text


def test_memory_coverage_is_cutoff_safe_content_addressed_and_cacheable(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    records = [
        _record("REC-AVAILABLE", datetime(2030, 1, 10, 8, 0, tzinfo=KST)),
        _record("REC-FUTURE", datetime(2030, 1, 10, 9, 1, tzinfo=KST)),
    ]
    _store_records(tmp_path, records)

    first = build_memory_coverage_manifest(
        tmp_path,
        records=records,
        cutoff_at=cutoff,
        run_id="RUN-COVERAGE-1",
    )
    second = build_memory_coverage_manifest(
        tmp_path,
        records=records,
        cutoff_at=cutoff,
        run_id="RUN-COVERAGE-2",
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.manifest.accepted_record_count == 2
    assert first.manifest.available_record_count == 1
    assert first.manifest.future_record_count == 1
    assert first.available_record_ids == ["REC-AVAILABLE"]
    context = {
        "run_id": "RUN-COVERAGE-1",
        "cutoff_at": cutoff.isoformat(),
        "accepted_record_count": 2,
        "available_record_count": 1,
        "available_record_ids": ["REC-AVAILABLE"],
        "memory_coverage_manifest_artifact": first.manifest_path,
        "memory_coverage_manifest_sha256": first.manifest_sha256,
    }
    assert inspect_memory_coverage_manifest(tmp_path, context)["passed"] is True


def test_memory_coverage_corpus_hash_binds_full_record_envelope(tmp_path: Path) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    original = _record("REC-1", datetime(2030, 1, 10, 8, 0, tzinfo=KST))
    changed = original.model_copy(
        update={"provenance_source_ids": ["SRC-CHANGED"]}
    )

    first = build_memory_coverage_manifest(
        tmp_path,
        records=[original],
        cutoff_at=cutoff,
        run_id="RUN-ORIGINAL",
    )
    second = build_memory_coverage_manifest(
        tmp_path,
        records=[changed],
        cutoff_at=cutoff,
        run_id="RUN-CHANGED",
    )

    assert (
        first.manifest.corpus_manifest_sha256
        != second.manifest.corpus_manifest_sha256
    )


def test_memory_sweeper_production_path_emits_manifest_without_shard_bodies(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_records(
        tmp_path,
        [_record("REC-1", datetime(2030, 1, 10, 8, 0, tzinfo=KST))],
    )

    result = MemorySweeper(tmp_path, shard_episode_count=20).sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff,
        run_id="RUN-COVERAGE-ONLY",
        current_news_texts=["current news"],
        first_pass_mechanisms=["mechanism"],
        emit_legacy_contributions=False,
    )

    assert result.artifact_paths == []
    assert result.record_artifact_paths == []
    assert result.swept_record_ids == ["REC-1"]
    assert result.memory_coverage_manifest_path
    assert result.memory_coverage_manifest_sha256


def test_memory_coverage_restarts_after_interrupted_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    records = [_record("REC-1", datetime(2030, 1, 10, 8, 0, tzinfo=KST))]
    original_publish = coverage_module._publish_staged_file
    calls = 0

    def fail_after_first_publish(
        staged_path: Path,
        destination: Path,
        *,
        expected_sha256: str,
    ) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publication interruption")
        return original_publish(
            staged_path,
            destination,
            expected_sha256=expected_sha256,
        )

    monkeypatch.setattr(
        coverage_module,
        "_publish_staged_file",
        fail_after_first_publish,
    )
    with pytest.raises(OSError, match="simulated publication interruption"):
        build_memory_coverage_manifest(
            tmp_path,
            records=records,
            cutoff_at=cutoff,
            run_id="RUN-INTERRUPTED",
        )

    monkeypatch.setattr(
        coverage_module,
        "_publish_staged_file",
        original_publish,
    )
    recovered = build_memory_coverage_manifest(
        tmp_path,
        records=records,
        cutoff_at=cutoff,
        run_id="RUN-RECOVERED",
    )

    assert recovered.manifest.coverage_complete is True
    assert recovered.manifest_path.endswith(f"{recovered.manifest_sha256}.json")
    staging = tmp_path / "data" / "cache" / "memory_coverage" / ".staging"
    assert not list(staging.iterdir())


def test_memory_coverage_replaces_corrupt_content_addressed_cache(tmp_path: Path) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    records = [_record("REC-1", datetime(2030, 1, 10, 8, 0, tzinfo=KST))]
    first = build_memory_coverage_manifest(
        tmp_path,
        records=records,
        cutoff_at=cutoff,
        run_id="RUN-FIRST",
    )
    accepted_ref = first.manifest.accepted_record_hash_manifest
    assert accepted_ref is not None
    accepted_path = tmp_path / accepted_ref.artifact_path
    accepted_path.write_bytes(b"corrupt\n")

    rebuilt = build_memory_coverage_manifest(
        tmp_path,
        records=records,
        cutoff_at=cutoff,
        run_id="RUN-REBUILT",
    )

    assert rebuilt.cache_hit is False
    assert coverage_module.file_sha256(accepted_path) == accepted_ref.sha256


def test_memory_coverage_rejects_duplicate_ids_across_future_records(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    records = [
        _record("REC-DUP", datetime(2030, 1, 10, 9, 1, tzinfo=KST)),
        _record("REC-DUP", datetime(2030, 1, 10, 9, 2, tzinfo=KST)),
    ]
    _store_records(tmp_path, records)

    result = build_memory_coverage_manifest(
        tmp_path,
        records=records,
        cutoff_at=cutoff,
        run_id="RUN-FUTURE-DUPLICATE",
    )
    context = {
        "run_id": result.manifest.run_id,
        "cutoff_at": cutoff.isoformat(),
        "accepted_record_count": 2,
        "available_record_count": 0,
        "memory_coverage_manifest_artifact": result.manifest_path,
        "memory_coverage_manifest_sha256": result.manifest_sha256,
    }

    assert result.manifest.duplicate_record_count == 1
    assert result.manifest.coverage_complete is False
    assert inspect_memory_coverage_manifest(tmp_path, context)["passed"] is False


def test_memory_coverage_rejects_current_store_additions(tmp_path: Path) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    original = _record("REC-1", datetime(2030, 1, 10, 8, 0, tzinfo=KST))
    _store_records(tmp_path, [original])
    result = build_memory_coverage_manifest(
        tmp_path,
        records=[original],
        cutoff_at=cutoff,
        run_id="RUN-STALE-CORPUS",
    )
    _store_records(
        tmp_path,
        [
            original,
            _record("REC-2", datetime(2030, 1, 10, 8, 1, tzinfo=KST)),
        ],
    )
    context = {
        "run_id": result.manifest.run_id,
        "cutoff_at": cutoff.isoformat(),
        "accepted_record_count": 1,
        "available_record_count": 1,
        "memory_coverage_manifest_artifact": result.manifest_path,
        "memory_coverage_manifest_sha256": result.manifest_sha256,
    }

    inspection = inspect_memory_coverage_manifest(tmp_path, context)

    assert inspection["current_store_verified"] is False
    assert "memory_coverage_current_store_mismatch" in inspection["errors"]
    assert inspection["passed"] is False


def test_memory_coverage_manifest_is_immutable_for_same_run_id(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    original = _record("REC-1", datetime(2030, 1, 10, 8, 0, tzinfo=KST))
    changed = original.model_copy(update={"provenance_source_ids": ["SRC-CHANGED"]})

    first = build_memory_coverage_manifest(
        tmp_path,
        records=[original],
        cutoff_at=cutoff,
        run_id="RUN-SAME",
    )
    first_bytes = (tmp_path / first.manifest_path).read_bytes()
    second = build_memory_coverage_manifest(
        tmp_path,
        records=[changed],
        cutoff_at=cutoff,
        run_id="RUN-SAME",
    )

    assert first.manifest_path != second.manifest_path
    assert first.manifest_sha256 != second.manifest_sha256
    assert (tmp_path / first.manifest_path).read_bytes() == first_bytes


def _record(record_id: str, available_from: datetime) -> BrainRecordEnvelope:
    payload = {
        "record_id": record_id,
        "record_type": "supervised_direct_event_case",
        "available_from": available_from.isoformat(),
    }
    payload_sha = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="supervised_direct_event_case",
        episode_id="EP-COVERAGE",
        trade_date=date(2030, 1, 9),
        available_from=available_from,
        training_target="direct_event_response",
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        eligibility_reason="test",
        status="accepted",
        confidence_label="high",
        provenance_source_ids=["SRC-1"],
        raw_payload_sha256=payload_sha,
        normalized_payload_sha256=payload_sha,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=payload,
    )


def _store_records(root: Path, records: list[BrainRecordEnvelope]) -> None:
    path = root / "memory" / "records" / "EP-COVERAGE.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            canonical_json(record.model_dump(mode="json")) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
