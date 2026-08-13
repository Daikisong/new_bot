import json
from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.tools.profile_brain_records import (
    build_phase0_baseline,
    profile_brain_records,
    profile_repaired_inventory,
)
from news_scalping_lab.utils import KST, canonical_json, sha256_text


def test_profile_is_deterministic_and_preserves_unknown_record_types(
    tmp_path: Path,
) -> None:
    records = [
        _record(
            "REC-POSITIVE",
            record_type="supervised_direct_event_case",
            payload={
                "record_id": "REC-POSITIVE",
                "record_type": "supervised_direct_event_case",
                "ticker": "000001",
                "event_id": "EV-1",
                "theme_id": "TH-1",
                "D_response": {"high_return_pct": 12.0, "close_return_pct": 5.0},
            },
            eligible=True,
        ),
        _record(
            "REC-UNKNOWN",
            record_type="future_record_type",
            payload={
                "record_id": "REC-UNKNOWN",
                "record_type": "future_record_type",
                "ticker": "000001",
            },
            eligible=False,
            typed_status="UNKNOWN_TYPED_PAYLOAD",
        ),
    ]
    _write_records(tmp_path, records)

    first = profile_brain_records(tmp_path)
    second = profile_brain_records(tmp_path)

    assert first.corpus_manifest_sha256 == second.corpus_manifest_sha256
    assert first.record_count == 2
    assert first.known_typed_record_count == 1
    assert first.unknown_typed_record_count == 1
    assert first.record_counts_by_type["future_record_type"] == 1
    assert first.record_counts_by_polarity["UNKNOWN"] == 1
    assert first.outcome_field_coverage["missing_outcome"] == 1
    assert first.independent_unit_profiles["issuer-day"].keyed_record_count == 2
    assert first.independent_unit_profiles["issuer-day"].unique_unit_count == 1
    assert first.linear_retrieval.benchmarked is False


def test_missing_outcome_is_not_counted_as_negative(tmp_path: Path) -> None:
    _write_records(
        tmp_path,
        [
            _record(
                "REC-MISSING",
                record_type="supervised_issuer_day_case",
                payload={
                    "record_id": "REC-MISSING",
                    "record_type": "supervised_issuer_day_case",
                    "ticker": "000002",
                },
                eligible=True,
            )
        ],
    )

    profile = profile_brain_records(tmp_path)

    assert profile.record_counts_by_polarity == {"UNKNOWN": 1}
    assert profile.outcome_field_coverage == {"missing_outcome": 1}


def test_note_only_or_null_outcome_is_declared_but_not_usable(tmp_path: Path) -> None:
    _write_records(
        tmp_path,
        [
            _record(
                "REC-NOTE",
                record_type="supervised_issuer_day_case",
                payload={
                    "record_id": "REC-NOTE",
                    "record_type": "supervised_issuer_day_case",
                    "D_outcome": {
                        "ticker": "000002",
                        "high_return_pct": None,
                        "note": "price unavailable",
                    },
                },
                eligible=False,
            )
        ],
    )

    profile = profile_brain_records(tmp_path)

    assert profile.outcome_field_coverage == {
        "declared_but_unusable_outcome": 1,
        "declared_outcome_container": 1,
        "missing_outcome": 1,
    }
    assert profile.record_counts_by_polarity == {"UNKNOWN": 1}


def test_corpus_hash_changes_when_provenance_changes(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    record = _record(
        "REC-1",
        record_type="memory_claim",
        payload={"record_id": "REC-1", "record_type": "memory_claim"},
        eligible=True,
    )
    _write_records(first_root, [record])
    _write_records(
        second_root,
        [record.model_copy(update={"provenance_source_ids": ["SRC-CHANGED"]})],
    )

    first = profile_brain_records(first_root)
    second = profile_brain_records(second_root)

    assert first.corpus_manifest_sha256 != second.corpus_manifest_sha256


def test_profile_counts_match_record_population(tmp_path: Path) -> None:
    records = [
        _record(
            f"REC-{index}",
            record_type="negative_control_case",
            payload={
                "record_id": f"REC-{index}",
                "record_type": "negative_control_case",
                "ticker": f"{index:06d}",
                "high_return_pct": 1.0,
            },
            eligible=index % 2 == 0,
        )
        for index in range(5)
    ]
    _write_records(tmp_path, records)

    profile = profile_brain_records(tmp_path, sweep_shard_size=2)

    assert sum(profile.record_counts_by_type.values()) == profile.record_count == 5
    assert sum(profile.record_counts_by_polarity.values()) == 5
    assert sum(profile.record_counts_by_label_quality.values()) == 5
    assert sum(profile.record_counts_by_routing_disposition.values()) == 5
    assert sum(profile.routing_four_axis_crosstab.values()) == 5
    assert profile.record_counts_by_polarity == {"NEGATIVE": 5}
    assert profile.record_counts_by_routing_disposition == {
        "AUDIT": 2,
        "REASONING": 3,
    }
    assert sum(sum(counts.values()) for counts in profile.eligibility_polarity_crosstab.values()) == 5
    assert profile.sweep_burden.estimated_record_shard_count == 3


def test_repaired_inventory_profile_uses_manifest_without_loading_bundles(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "sequential_repair_manifest.v2.jsonl"
    rows = [
        {
            "filename_date": "20240102",
            "final_status": "REPAIRED_PASS",
            "ready_for_import": True,
            "record_count": 300,
            "training_eligible_record_count": 200,
            "byte_size": 1000,
            "repaired_byte_size": 1100,
            "engine_digest": "engine-a",
        },
        {
            "filename_date": "20240103",
            "final_status": "DEFERRED_NON_TRADING",
            "ready_for_import": False,
            "byte_size": 100,
            "engine_digest": "engine-a",
        },
    ]
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    profile = profile_repaired_inventory(manifest)

    assert profile.entry_count == 2
    assert profile.ready_for_import_count == 1
    assert profile.declared_record_count == 300
    assert profile.declared_training_eligible_record_count == 200
    assert profile.ready_declared_record_count == 300
    assert profile.ready_declared_training_eligible_record_count == 200
    assert profile.non_ready_declared_record_count == 0
    assert profile.status_counts == {
        "DEFERRED_NON_TRADING": 1,
        "REPAIRED_PASS": 1,
    }
    assert profile.record_count_coverage_count == 1
    assert profile.ready_record_count_coverage_count == 1


def test_phase0_baseline_combines_store_and_repair_inventory(tmp_path: Path) -> None:
    _write_records(
        tmp_path,
        [
            _record(
                "REC-1",
                record_type="negative_control_case",
                payload={
                    "record_id": "REC-1",
                    "record_type": "negative_control_case",
                },
                eligible=True,
            )
        ],
    )
    manifest = tmp_path / "repair.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "filename_date": "20300110",
                "final_status": "REPAIRED_PASS",
                "ready_for_import": True,
                "record_count": 1,
                "training_eligible_record_count": 1,
                "byte_size": 100,
                "repaired_byte_size": 120,
                "engine_digest": "engine",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    baseline = build_phase0_baseline(tmp_path, repair_manifest=manifest)

    assert baseline.schema_version == "nslab.brain_memory_phase0_baseline.v1"
    assert baseline.corpus.record_count == 1
    assert baseline.repaired_inventory is not None
    assert baseline.repaired_inventory.ready_for_import_count == 1


def _write_records(root: Path, records: list[BrainRecordEnvelope]) -> None:
    records_dir = root / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / "EP-1.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in reversed(records)),
        encoding="utf-8",
    )


def _record(
    record_id: str,
    *,
    record_type: str,
    payload: dict[str, object],
    eligible: bool,
    typed_status: str = "KNOWN_TYPED_PAYLOAD",
) -> BrainRecordEnvelope:
    payload = {**payload, "training_eligible": eligible}
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id="EP-1",
        trade_date=date(2030, 1, 10),
        available_from=datetime(2030, 1, 11, tzinfo=KST),
        training_target="fixture",
        evidence_phase="POSTMORTEM",
        training_eligible=eligible,
        eligibility_reason="fixture",
        status="supported",
        confidence_label="medium",
        provenance_source_ids=["SRC-1"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status=typed_status,
        payload=payload,
    )
