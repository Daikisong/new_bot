from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from pathlib import Path

import duckdb
import pytest

from news_scalping_lab.audits.coverage import audit_coverage
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.records.store import (
    BrainRecordStore,
    audit_record_store,
    record_store_report_payload,
)
from news_scalping_lab.research_import.versioned_bundle import (
    VersionedBundleImportError,
    import_versioned_bundle,
    inspect_versioned_bundle,
)
from news_scalping_lab.training import audit_training_exports, export_training
from news_scalping_lab.utils import KST, sha256_text
from news_scalping_lab.warehouse import WarehouseStore


def _payload_block(payload: str, fence: str) -> str:
    return f"```{fence}\n{payload}\n```"


def _synthetic_v11_bundle(
    *,
    schema_version: str = "nslab.research_bundle.v11",
    include_unknown: bool = True,
    validation_checked_hashes: dict[str, str] | None = None,
    issuer_available_from: str | None = None,
    issuer_sample_weight: float = 1.0,
    direct_event_sample_weights: list[float] | None = None,
) -> str:
    episode_id = "NSLAB-20300110-SYNTH"
    trade_day = date(2030, 1, 10)
    cutoff_at = datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST).isoformat()
    available_from = datetime.combine(date(2030, 1, 11), time(0, 0, 0), tzinfo=KST).isoformat()
    records = [
        {
            "record_id": "BRAIN-SYNTH-ISSUER",
            "record_type": "supervised_issuer_day_case",
            "episode_id": episode_id,
            "trade_date": trade_day.isoformat(),
            "available_from": issuer_available_from or available_from,
            "status": "tentative",
            "confidence_label": "low",
            "training_target": "issuer_day_price_response",
            "issuer_day_case_id": "20300110:000001",
            "ticker": "000001",
            "company_name": "Synthetic Issuer",
            "response_class": "positive_high10",
            "sample_weight": issuer_sample_weight,
            "training_eligible": True,
            "eligibility_reason": "synthetic verified label",
            "provenance_source_ids": ["SRC-SYNTH-1"],
        },
        {
            "record_id": "BRAIN-SYNTH-PAIR",
            "record_type": "blind_leader_preference_pair",
            "episode_id": episode_id,
            "trade_date": trade_day.isoformat(),
            "available_from": available_from,
            "training_target": "outcome_preferred_candidate",
            "blind_pair_id": "PAIR-SYNTH-1",
            "blind_preferred_candidate_id": "CAND-A",
            "blind_rejected_candidate_id": "CAND-B",
            "outcome_preferred_candidate_id": "CAND-A",
            "blind_preference_correct": True,
            "training_eligible": True,
            "eligibility_reason": "synthetic sealed pair",
            "provenance_source_ids": ["SRC-SYNTH-1"],
        },
    ]
    if direct_event_sample_weights is not None:
        for index, sample_weight in enumerate(direct_event_sample_weights, start=1):
            records.append(
                {
                    "record_id": f"BRAIN-SYNTH-DIRECT-{index}",
                    "record_type": "supervised_direct_event_case",
                    "episode_id": episode_id,
                    "trade_date": trade_day.isoformat(),
                    "available_from": available_from,
                    "training_target": "direct_event_response",
                    "case_id": f"DIRECT-SYNTH-{index}",
                    "issuer_day_case_id": "20300110:000001",
                    "event_id": f"EVT-SYNTH-{index}",
                    "ticker": "000001",
                    "company_name": "Synthetic Issuer",
                    "response_class": "positive_high10",
                    "sample_weight": sample_weight,
                    "training_eligible": True,
                    "eligibility_reason": "synthetic direct event label",
                    "provenance_source_ids": ["SRC-SYNTH-1"],
                }
            )
    if include_unknown:
        records.append(
            {
                "record_id": "BRAIN-SYNTH-UNKNOWN",
                "record_type": "future_record_type",
                "episode_id": episode_id,
                "trade_date": trade_day.isoformat(),
                "available_from": available_from,
                "training_eligible": False,
                "provenance_source_ids": ["SRC-SYNTH-1"],
            }
        )
    brain_delta = "\n".join(json.dumps(row, ensure_ascii=False) for row in records)
    source_ledger = json.dumps(
        {
            "source_id": "SRC-SYNTH-1",
            "source_type": "synthetic_fixture",
            "title": "Synthetic source",
        },
        ensure_ascii=False,
    )
    research_episode = json.dumps(
        {
            "schema_version": "nslab.research_episode.v11",
            "episode_id": episode_id,
            "trade_date": trade_day.isoformat(),
            "cutoff_at": cutoff_at,
            "available_from": available_from,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    validation_payload = {
        "schema_version": "nslab.validation_report.v3",
        "critical_error_count": 0,
        "computed_counts": {
            "brain_delta_record_count": len(records),
            "training_eligible_record_count": sum(
                1 for record in records if record.get("training_eligible") is True
            ),
        },
    }
    if validation_checked_hashes is not None:
        validation_payload["checked_artifact_hashes"] = validation_checked_hashes
    validation_report = json.dumps(
        validation_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    manifest = json.dumps(
        {
            "schema_version": "nslab.bundle_manifest.v11",
            "episode_id": episode_id,
            "trade_date": trade_day.isoformat(),
            "cutoff_at": cutoff_at,
            "bundle_status": "ACCEPT_FULL",
            "blind_valid": True,
            "validator_exit_code": 0,
            "critical_error_count": 0,
            "brain_delta_record_count": len(records),
            "training_eligible_record_count": sum(
                1 for record in records if record.get("training_eligible") is True
            ),
            "embedded_blocks": {
                "brain_delta.jsonl": {"sha256": sha256_text(brain_delta)},
                "source_ledger.jsonl": {"sha256": sha256_text(source_ledger)},
                "research_episode.json": {"sha256": sha256_text(research_episode)},
                "validation_report.json": {"sha256": sha256_text(validation_report)},
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"""---
schema_version: {schema_version}
episode_id: {episode_id}
trade_date: {trade_day.isoformat()}
cutoff_at: {cutoff_at}
bundle_status: ACCEPT_FULL
blind_valid: true
---
<!-- NSLAB:BEGIN research_episode.json -->
{_payload_block(research_episode, "json")}
<!-- NSLAB:END research_episode.json -->

<!-- NSLAB:BEGIN brain_delta.jsonl -->
{_payload_block(brain_delta, "jsonl")}
<!-- NSLAB:END brain_delta.jsonl -->

<!-- NSLAB:BEGIN source_ledger.jsonl -->
{_payload_block(source_ledger, "jsonl")}
<!-- NSLAB:END source_ledger.jsonl -->

<!-- NSLAB:BEGIN validation_report.json -->
{_payload_block(validation_report, "json")}
<!-- NSLAB:END validation_report.json -->

<!-- NSLAB:BEGIN bundle_manifest.json -->
{_payload_block(manifest, "json")}
<!-- NSLAB:END bundle_manifest.json -->
"""


def test_v11_bundle_import_preserves_brain_delta_records(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(), encoding="utf-8")

    inspection = inspect_versioned_bundle(bundle)
    result = import_versioned_bundle(bundle, root=tmp_path)

    records = BrainRecordStore(tmp_path).read_episode_records("NSLAB-20300110-SYNTH")
    unknown = next(record for record in records if record.record_id == "BRAIN-SYNTH-UNKNOWN")
    assert inspection["adapter"] == "v11"
    assert inspection["record_count"] == 3
    assert inspection["raw_record_count"] == 3
    assert inspection["normalized_record_count"] == 3
    assert inspection["dropped_record_count"] == 0
    assert inspection["quarantined_record_count"] == 0
    assert inspection["training_eligible_record_count"] == 2
    assert inspection["validation_passed"] is True
    assert inspection["record_count_matches_manifest"] is True
    assert inspection["training_eligible_count_matches_manifest"] is True
    assert inspection["hash_mismatch_count"] == 0
    assert inspection["missing_source_reference_count"] == 0
    assert inspection["inspection_status"] == "validation_passed"
    assert result.record_count == 3
    assert result.training_eligible_record_count == 2
    assert unknown.typed_payload_status == "UNKNOWN_TYPED_PAYLOAD"
    assert unknown.training_eligible is False
    assert unknown.eligibility_reason is not None
    assert "unknown record_type preserved as raw payload" in unknown.eligibility_reason
    assert unknown.payload["training_eligible"] is False
    assert (
        unknown.payload["eligibility_reason"]
        == "unknown record_type preserved as raw payload"
    )
    audit = audit_record_store(tmp_path)
    report = record_store_report_payload(tmp_path, audit)
    assert audit["stats"]["record_counts_by_typed_payload_status"] == {
        "KNOWN_TYPED_PAYLOAD": 2,
        "UNKNOWN_TYPED_PAYLOAD": 1,
    }
    assert audit["stats"]["unknown_typed_payload_count"] == 1
    assert audit["stats"]["raw_only_record_count"] == 0
    assert audit["stats"]["ineligible_record_count"] == 1
    assert report["record_counts_by_typed_payload_status"] == {
        "KNOWN_TYPED_PAYLOAD": 2,
        "UNKNOWN_TYPED_PAYLOAD": 1,
    }
    assert report["unknown_typed_payload_count"] == 1
    assert report["raw_only_record_count"] == 0
    assert report["ineligible_record_count"] == 1
    assert report["all_unknown_typed_payload_count"] == 1
    assert report["all_raw_only_record_count"] == 0
    assert report["staged_unknown_typed_payload_count"] == 0
    assert report["staged_raw_only_record_count"] == 0
    assert (tmp_path / "research" / "episodes" / "NSLAB-20300110-SYNTH" / "original_bundle.md").exists()
    assert (tmp_path / "memory" / "records" / "NSLAB-20300110-SYNTH.jsonl").exists()


def test_record_store_deep_audit_validates_import_parity(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(), encoding="utf-8")
    import_versioned_bundle(bundle, root=tmp_path)

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is True
    assert audit["manifest_count_mismatch_episode_ids"] == []
    assert audit["index_record_id_mismatch_episode_ids"] == []
    assert audit["brain_delta_count_mismatch_episode_ids"] == []
    assert audit["brain_delta_record_id_mismatch_episode_ids"] == []
    assert audit["brain_delta_training_eligible_mismatch_episode_ids"] == []
    assert audit["brain_delta_type_count_mismatch_episode_ids"] == []
    assert audit["records_with_raw_payload_hash_mismatch"] == []
    assert audit["eligible_records_with_unknown_provenance_sources"] == []


def test_record_store_deep_audit_rejects_brain_delta_record_id_gap(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(include_unknown=False), encoding="utf-8")
    import_versioned_bundle(bundle, root=tmp_path)

    record_path = tmp_path / "memory" / "records" / "NSLAB-20300110-SYNTH.jsonl"
    record_rows = _jsonl(record_path)
    record_rows[0]["record_id"] = "BRAIN-SYNTH-ISSUER-TAMPERED"
    record_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in record_rows) + "\n",
        encoding="utf-8",
    )
    record_ids = [row["record_id"] for row in record_rows]

    manifest_path = tmp_path / "memory" / "record_manifests" / "NSLAB-20300110-SYNTH.json"
    manifest = _read_json(manifest_path)
    manifest["record_ids"] = record_ids
    manifest["records_sha256"] = sha256_text(record_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    index_path = (
        tmp_path
        / "research"
        / "episodes"
        / "NSLAB-20300110-SYNTH"
        / "normalized_episode_index.json"
    )
    index = _read_json(index_path)
    index["record_ids"] = record_ids
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["manifest_record_id_mismatch_episode_ids"] == []
    assert audit["index_record_id_mismatch_episode_ids"] == []
    assert audit["manifest_hash_mismatch_episode_ids"] == []
    assert audit["records_with_raw_payload_hash_mismatch"] == []
    assert audit["brain_delta_record_id_mismatch_episode_ids"] == [
        "NSLAB-20300110-SYNTH"
    ]


def test_record_store_deep_audit_rejects_brain_delta_population_gap(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(include_unknown=False), encoding="utf-8")
    import_versioned_bundle(bundle, root=tmp_path)

    record_path = tmp_path / "memory" / "records" / "NSLAB-20300110-SYNTH.jsonl"
    record_rows = _jsonl(record_path)
    record_rows[0]["record_type"] = "memory_claim"
    record_rows[0]["training_eligible"] = False
    record_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in record_rows) + "\n",
        encoding="utf-8",
    )
    record_counts_by_type = {
        "blind_leader_preference_pair": 1,
        "memory_claim": 1,
    }

    manifest_path = tmp_path / "memory" / "record_manifests" / "NSLAB-20300110-SYNTH.json"
    manifest = _read_json(manifest_path)
    manifest["training_eligible_record_count"] = 1
    manifest["record_counts_by_type"] = record_counts_by_type
    manifest["records_sha256"] = sha256_text(record_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    index_path = (
        tmp_path
        / "research"
        / "episodes"
        / "NSLAB-20300110-SYNTH"
        / "normalized_episode_index.json"
    )
    index = _read_json(index_path)
    index["training_eligible_record_count"] = 1
    index["record_count_by_type"] = record_counts_by_type
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["manifest_training_eligible_mismatch_episode_ids"] == []
    assert audit["manifest_type_count_mismatch_episode_ids"] == []
    assert audit["index_training_eligible_mismatch_episode_ids"] == []
    assert audit["index_type_count_mismatch_episode_ids"] == []
    assert audit["records_with_raw_payload_hash_mismatch"] == []
    assert audit["brain_delta_training_eligible_mismatch_episode_ids"] == [
        "NSLAB-20300110-SYNTH"
    ]
    assert audit["brain_delta_type_count_mismatch_episode_ids"] == [
        "NSLAB-20300110-SYNTH"
    ]


def test_record_store_deep_audit_rejects_tampered_import_parity(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(include_unknown=False), encoding="utf-8")
    import_versioned_bundle(bundle, root=tmp_path)

    manifest_path = tmp_path / "memory" / "record_manifests" / "NSLAB-20300110-SYNTH.json"
    manifest = _read_json(manifest_path)
    manifest["record_count"] = 999
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    index_path = (
        tmp_path
        / "research"
        / "episodes"
        / "NSLAB-20300110-SYNTH"
        / "normalized_episode_index.json"
    )
    index = _read_json(index_path)
    index["source_ids"] = []
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    record_path = tmp_path / "memory" / "records" / "NSLAB-20300110-SYNTH.jsonl"
    record_rows = _jsonl(record_path)
    record_rows[1]["source_line"] = None
    record_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in record_rows) + "\n",
        encoding="utf-8",
    )

    raw_block_path = (
        tmp_path
        / "research"
        / "episodes"
        / "NSLAB-20300110-SYNTH"
        / "raw_blocks"
        / "brain_delta.jsonl"
    )
    raw_rows = _jsonl(raw_block_path)
    raw_rows[0]["company_name"] = "Tampered Issuer"
    raw_block_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in raw_rows),
        encoding="utf-8",
    )

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["manifest_count_mismatch_episode_ids"] == ["NSLAB-20300110-SYNTH"]
    assert audit["manifest_hash_mismatch_episode_ids"] == ["NSLAB-20300110-SYNTH"]
    assert audit["raw_block_hash_mismatch_episode_ids"] == ["NSLAB-20300110-SYNTH"]
    assert audit["records_with_raw_payload_hash_mismatch"] == ["BRAIN-SYNTH-ISSUER"]
    assert audit["records_missing_source_line"] == ["BRAIN-SYNTH-PAIR"]
    assert sorted(audit["eligible_records_with_unknown_provenance_sources"]) == [
        "BRAIN-SYNTH-ISSUER",
        "BRAIN-SYNTH-PAIR",
    ]


def test_record_warehouse_and_training_use_explicit_records(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(include_unknown=False), encoding="utf-8")
    import_versioned_bundle(bundle, root=tmp_path)

    counts = WarehouseStore(tmp_path).rebuild_all()
    sft = export_training(tmp_path, kind="sft")
    preference = export_training(tmp_path, kind="preference")

    assert counts["brain_records"] == 2
    assert counts["issuer_day_cases"] == 1
    assert counts["leader_pairs"] == 1
    assert duckdb.sql(
        f"select count(*) from read_parquet('{(tmp_path / 'warehouse' / 'brain_records.parquet').as_posix()}')"
    ).fetchone() == (2,)
    store_report = _read_json(tmp_path / "diagnostics" / "brain_record_store_report.json")
    assert store_report["schema_version"] == "nslab.brain_record_store_report.v1"
    assert store_report["record_count"] == 2
    assert store_report["dropped_record_count"] == 0
    assert store_report["quarantined_record_count"] == 0
    assert store_report["audit_passed"] is True
    assert store_report["record_store_audit"]["deep"] is True
    assert store_report["record_store_audit"]["passed"] is True
    sft_rows = _jsonl(sft.path)
    preference_rows = _jsonl(preference.path)
    assert {row["record_id"] for row in sft_rows} == {"BRAIN-SYNTH-ISSUER"}
    assert {row["record_id"] for row in preference_rows} == {"BRAIN-SYNTH-PAIR"}
    assert _read_json(sft.manifest_path)["source_mode"] == "brain_records"


def test_training_export_skip_manifest_keeps_ineligible_record_reason(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(), encoding="utf-8")
    import_versioned_bundle(bundle, root=tmp_path)

    preference = export_training(tmp_path, kind="preference")

    manifest = _read_json(preference.manifest_path)
    skipped_by_id = {
        item["record_id"]: item for item in manifest["skipped_records"]
    }
    unknown = skipped_by_id["BRAIN-SYNTH-UNKNOWN"]
    assert unknown["training_eligible"] is False
    assert "unknown record_type preserved as raw payload" in unknown["eligibility_reason"]
    assert unknown["reason"] == "unknown record_type preserved as raw payload"
    assert unknown["skip_reasons"] == [
        "unknown record_type preserved as raw payload",
        "record_type_not_selected_for_export_kind",
    ]


def test_coverage_audit_rejects_duplicate_issuer_day_projection_keys(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(include_unknown=False), encoding="utf-8")
    import_versioned_bundle(bundle, root=tmp_path)
    WarehouseStore(tmp_path).rebuild_all()

    issuer_path = tmp_path / "warehouse" / "issuer_day_cases.parquet"
    duplicate_path = tmp_path / "warehouse" / "issuer_day_cases_duplicate.parquet"
    issuer_sql_path = issuer_path.as_posix().replace("'", "''")
    duplicate_sql_path = duplicate_path.as_posix().replace("'", "''")
    duckdb.sql(
        "copy ("
        f"select * from read_parquet('{issuer_sql_path}') "
        "union all "
        f"select * from read_parquet('{issuer_sql_path}')"
        f") to '{duplicate_sql_path}' (format parquet)"
    )
    duplicate_path.replace(issuer_path)

    audit = audit_coverage(tmp_path)

    assert audit["warehouse_duplicate_identities"] == {
        "issuer_day_cases.parquet": ["20300110:000001|2030-01-10|000001"]
    }
    assert (
        "warehouse: issuer_day_cases.parquet duplicate ids: "
        "20300110:000001|2030-01-10|000001"
    ) in audit["findings"]
    assert audit["warehouse_projection_synced"] is False


def test_coverage_audit_rejects_warehouse_weight_sum_mismatches(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "warehouse_weight_gap_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            include_unknown=False,
            issuer_sample_weight=0.5,
            direct_event_sample_weights=[0.25, 0.25],
        ),
        encoding="utf-8",
    )
    import_versioned_bundle(bundle, root=tmp_path)
    WarehouseStore(tmp_path).rebuild_all()

    audit = audit_coverage(tmp_path)

    assert audit["warehouse_weight_mismatches"] == {
        "direct_event_cases.parquet": {"20300110:000001": 0.5},
        "issuer_day_cases.parquet": {"2030-01-10|000001": 0.5},
    }
    assert (
        "warehouse: issuer_day_cases.parquet weight sum mismatch: "
        "2030-01-10|000001=0.5"
    ) in audit["findings"]
    assert (
        "warehouse: direct_event_cases.parquet weight sum mismatch: "
        "20300110:000001=0.5"
    ) in audit["findings"]
    assert audit["warehouse_projection_synced"] is False


def test_training_audit_rejects_issuer_day_weight_sum_mismatch(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "issuer_weight_gap_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            include_unknown=False,
            issuer_sample_weight=0.5,
        ),
        encoding="utf-8",
    )
    import_versioned_bundle(bundle, root=tmp_path)

    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")

    manifest = _read_json(sft.manifest_path)
    audit = audit_training_exports(tmp_path)

    assert manifest["weight_validation_status"] == "failed"
    assert manifest["weight_validation"]["issuer_day_weight_sum_mismatches"] == {
        "2030-01-10|000001": 0.5
    }
    assert audit["passed"] is False
    assert "sft: record weight validation failed" in audit["findings"]


def test_training_audit_rejects_direct_event_weight_sum_mismatch(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "direct_event_weight_gap_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            include_unknown=False,
            direct_event_sample_weights=[0.25, 0.25],
        ),
        encoding="utf-8",
    )
    import_versioned_bundle(bundle, root=tmp_path)

    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")

    manifest = _read_json(sft.manifest_path)
    audit = audit_training_exports(tmp_path)

    assert manifest["weight_validation_status"] == "failed"
    assert manifest["weight_validation"]["direct_event_weight_sum_mismatches"] == {
        "20300110:000001": 0.5
    }
    assert audit["passed"] is False
    assert "sft: record weight validation failed" in audit["findings"]


def test_versioned_bundle_can_stage_records_until_accepted(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(include_unknown=False), encoding="utf-8")

    staged = import_versioned_bundle(bundle, root=tmp_path, accepted=False)
    store = BrainRecordStore(tmp_path)

    assert staged.accepted is False
    assert staged.manifest_path is not None
    assert len(store.read_episode_records("NSLAB-20300110-SYNTH")) == 2
    assert store.list_records() == []
    staged_manifest = _read_json(staged.manifest_path)
    assert staged_manifest["accepted"] is False
    assert staged_manifest["acceptance_status"] == "staged"
    assert WarehouseStore(tmp_path).rebuild_all()["brain_records"] == 0

    accepted = import_versioned_bundle(bundle, root=tmp_path, accepted=True)

    assert accepted.accepted is True
    assert accepted.manifest_path is not None
    assert {record.record_id for record in store.list_records()} == {
        "BRAIN-SYNTH-ISSUER",
        "BRAIN-SYNTH-PAIR",
    }
    accepted_manifest = _read_json(accepted.manifest_path)
    assert accepted_manifest["accepted"] is True
    assert accepted_manifest["acceptance_status"] == "accepted"
    assert WarehouseStore(tmp_path).rebuild_all()["brain_records"] == 2


def test_invalid_bundle_can_only_be_staged_not_accepted(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "bad_hash_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle_with_bad_brain_delta_hash(),
        encoding="utf-8",
    )

    staged = import_versioned_bundle(
        bundle,
        root=tmp_path,
        accepted=False,
        validate=False,
    )
    assert staged.accepted is False
    assert staged.validation["passed"] is False
    assert staged.validation["hash_mismatches"]["brain_delta.jsonl"]["sources"] == [
        "bundle_manifest.embedded_blocks"
    ]
    assert len(BrainRecordStore(tmp_path).read_episode_records("NSLAB-20300110-SYNTH")) == 2
    assert BrainRecordStore(tmp_path).list_records() == []

    with pytest.raises(VersionedBundleImportError):
        import_versioned_bundle(
            bundle,
            root=tmp_path,
            accepted=True,
            validate=False,
        )

    report = _read_json(tmp_path / "diagnostics" / "bundle_import_report.json")
    assert report["status"] == "BUNDLE_VALIDATION_FAILED"
    assert report["raw_record_count"] == 2
    assert report["normalized_record_count"] == 2
    assert report["dropped_record_count"] == 0
    assert report["quarantined_record_count"] == 1
    assert list((tmp_path / "data" / "quarantine" / "research_bundles").glob("*/original_bundle.md"))


def test_self_referential_manifest_hash_is_reported_without_blocking_import(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "manifest_self_hash_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle_with_manifest_self_hash(),
        encoding="utf-8",
    )

    inspection = inspect_versioned_bundle(bundle)
    validation = inspection["validation"]

    assert validation["passed"] is True
    assert validation["hash_mismatches"] == {}
    assert validation["self_referential_hashes"]["bundle_manifest.json"] == {
        "expected": "1" * 64,
        "actual": validation["block_hashes"]["bundle_manifest.json"],
        "source": "bundle_manifest.embedded_blocks",
        "reason": "hash is declared inside the same block it describes",
    }

    imported = import_versioned_bundle(bundle, root=tmp_path)

    assert imported.record_count == 2
    assert imported.training_eligible_record_count == 2
    assert imported.validation["passed"] is True
    assert imported.validation["self_referential_hashes"]["bundle_manifest.json"][
        "expected"
    ] == "1" * 64


def test_conflicting_hash_expectation_sources_block_acceptance(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "conflicting_hash_sources_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            include_unknown=False,
            validation_checked_hashes={"brain_delta.jsonl": "2" * 64},
        ),
        encoding="utf-8",
    )

    inspection = inspect_versioned_bundle(bundle)
    validation = inspection["validation"]

    assert validation["passed"] is False
    assert validation["hash_mismatches"] == {}
    assert validation["hash_expectation_conflicts"]["brain_delta.jsonl"] == [
        {
            "expected": validation["block_hashes"]["brain_delta.jsonl"],
            "source": "bundle_manifest.embedded_blocks",
        },
        {
            "expected": "2" * 64,
            "source": "validation_report.checked_artifact_hashes",
        },
    ]

    with pytest.raises(VersionedBundleImportError):
        import_versioned_bundle(bundle, root=tmp_path, accepted=True)


def test_invalid_record_available_from_blocks_acceptance(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "invalid_available_from_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            include_unknown=False,
            issuer_available_from="not-a-timestamp",
        ),
        encoding="utf-8",
    )

    inspection = inspect_versioned_bundle(bundle)
    validation = inspection["validation"]

    assert inspection["validation_passed"] is False
    assert inspection["available_from_valid"] is False
    assert inspection["invalid_available_from_record_count"] == 1
    assert validation["available_from_valid"] is False
    assert validation["invalid_available_from_record_ids"] == ["BRAIN-SYNTH-ISSUER"]

    with pytest.raises(VersionedBundleImportError):
        import_versioned_bundle(bundle, root=tmp_path, accepted=True)

    report = _read_json(tmp_path / "diagnostics" / "bundle_import_report.json")
    assert report["status"] == "BUNDLE_VALIDATION_FAILED"
    assert report["validation"]["invalid_available_from_record_ids"] == [
        "BRAIN-SYNTH-ISSUER"
    ]
    assert list((tmp_path / "data" / "quarantine" / "research_bundles").glob("*/original_bundle.md"))


def test_unknown_bundle_version_with_common_records_is_staged_raw_only(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "future_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(schema_version="nslab.research_bundle.v99"),
        encoding="utf-8",
    )

    result = import_versioned_bundle(bundle, root=tmp_path)

    assert result.status == "forward_compatible_raw_only"
    assert result.adapter_name == "forward-compatible-raw-only"
    assert result.accepted is False
    assert result.envelope_path is not None
    assert result.envelope_path.exists()
    assert result.record_path is not None
    assert result.record_path.exists()
    assert result.record_count == 3
    assert result.training_eligible_record_count == 0
    assert result.validation["forward_compatible_raw_only"] is True
    records = BrainRecordStore(tmp_path).read_episode_records("NSLAB-20300110-SYNTH")
    assert len(records) == 3
    assert all(record.training_eligible is False for record in records)
    assert all(record.typed_payload_status == "UNKNOWN_TYPED_PAYLOAD" for record in records)
    assert BrainRecordStore(tmp_path).list_records() == []
    manifest = _read_json(tmp_path / "memory" / "record_manifests" / "NSLAB-20300110-SYNTH.json")
    assert manifest["accepted"] is False
    assert manifest["acceptance_status"] == "staged"
    envelope = _read_json(result.envelope_path)
    assert envelope["import_status"] == "forward_compatible_raw_only"
    report = _read_json(tmp_path / "diagnostics" / "bundle_import_report.json")
    assert report["status"] == "forward_compatible_raw_only"
    assert report["raw_only_record_count"] == 3
    assert report["dropped_record_count"] == 0
    assert report["quarantined_record_count"] == 0
    audit = audit_record_store(tmp_path)
    store_report = record_store_report_payload(tmp_path, audit)
    assert audit["record_count"] == 0
    assert audit["all_record_count"] == 3
    assert audit["staged_record_count"] == 3
    assert audit["stats"]["unknown_typed_payload_count"] == 0
    assert audit["all_stats"]["unknown_typed_payload_count"] == 3
    assert audit["staged_stats"]["raw_only_record_count"] == 3
    assert store_report["unknown_typed_payload_count"] == 0
    assert store_report["raw_only_record_count"] == 0
    assert store_report["all_unknown_typed_payload_count"] == 3
    assert store_report["all_raw_only_record_count"] == 3
    assert store_report["staged_unknown_typed_payload_count"] == 3
    assert store_report["staged_raw_only_record_count"] == 3


def test_opaque_unknown_bundle_version_is_quarantined(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "opaque_future_bundle.md"
    bundle.write_text(_opaque_unsupported_bundle(), encoding="utf-8")

    inspection = inspect_versioned_bundle(bundle)
    result = import_versioned_bundle(bundle, root=tmp_path)

    assert inspection["supported"] is False
    assert inspection["forward_compatible_raw_only"] is False
    assert result.status == "UNSUPPORTED_BUNDLE_VERSION"
    assert result.record_count == 0
    assert result.training_eligible_record_count == 0
    assert result.envelope_path is not None
    assert result.envelope_path.exists()
    assert list((tmp_path / "data" / "quarantine" / "research_bundles").glob("*/original_bundle.md"))


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _synthetic_v11_bundle_with_bad_brain_delta_hash() -> str:
    return re.sub(
        r'("brain_delta\.jsonl": \{"sha256": ")[0-9a-f]{64}(")',
        lambda match: match.group(1) + ("0" * 64) + match.group(2),
        _synthetic_v11_bundle(include_unknown=False),
        count=1,
    )


def _synthetic_v11_bundle_with_manifest_self_hash() -> str:
    return _synthetic_v11_bundle(include_unknown=False).replace(
        '"embedded_blocks": {',
        '"embedded_blocks": {"bundle_manifest.json": {"sha256": "'
        + ("1" * 64)
        + '"}, ',
        1,
    )


def _opaque_unsupported_bundle() -> str:
    return """---
schema_version: nslab.research_bundle.v99
episode_id: FUTURE-OPAQUE
trade_date: 2030-01-10
---
<!-- NSLAB:BEGIN future_payload.json -->
```json
{"schema_version":"nslab.future_payload.v1","opaque":true}
```
<!-- NSLAB:END future_payload.json -->
"""
