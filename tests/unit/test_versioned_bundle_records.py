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
from news_scalping_lab.utils import KST, canonical_json, sha256_text
from news_scalping_lab.warehouse import WarehouseStore


def _payload_block(payload: str, fence: str) -> str:
    return f"```{fence}\n{payload}\n```"


def _synthetic_v11_bundle(
    *,
    schema_version: str = "nslab.research_bundle.v11",
    manifest_schema_version: str = "nslab.bundle_manifest.v11",
    episode_schema_version: str = "nslab.research_episode.v11",
    include_unknown: bool = True,
    validation_checked_hashes: dict[str, str] | None = None,
    issuer_available_from: str | None = None,
    issuer_label_quality: str | None = None,
    issuer_sample_weight: float = 1.0,
    direct_event_sample_weights: list[float] | None = None,
    include_event_edge: bool = False,
    include_error_correction: bool = False,
    include_company_memory_delta: bool = False,
    source_event_ids: list[str] | None = None,
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
            **(
                {
                    "D_outcome": {"label_quality": issuer_label_quality},
                    "label_quality": issuer_label_quality,
                }
                if issuer_label_quality is not None
                else {}
            ),
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
            "blind_preferred_ticker": "000001",
            "blind_rejected_ticker": "000002",
            "outcome_winner_ticker": "000001",
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
    if include_event_edge:
        records.append(
            {
                "record_id": "BRAIN-SYNTH-EDGE",
                "record_type": "event_ticker_edge",
                "episode_id": episode_id,
                "trade_date": trade_day.isoformat(),
                "available_from": available_from,
                "training_target": "event_ticker_relation",
                "edge_id": "EDGE-SYNTH-1",
                "event_id": "EVT-SYNTH-1",
                "ticker": "000001",
                "company_name": "Synthetic Issuer",
                "relation_class": "DIRECT",
                "relation_explanation": "Synthetic direct event edge.",
                "directly_mentioned": True,
                "training_eligible": False,
                "eligibility_reason": "edge relation memory is audit-only",
                "provenance_source_ids": ["SRC-SYNTH-1"],
            }
        )
    if include_error_correction:
        records.append(
            {
                "record_id": "BRAIN-SYNTH-ERROR",
                "record_type": "candidate_generation_error_case",
                "episode_id": episode_id,
                "trade_date": trade_day.isoformat(),
                "available_from": available_from,
                "training_target": "candidate_generation_correction",
                "error_id": "ERR-SYNTH-1",
                "error_type": "missed_direct_candidate",
                "correction_mode": "add_candidate",
                "original_decision": {
                    "candidate_ids": ["CAND-B"],
                    "reason": "Blind pass underweighted direct evidence.",
                },
                "corrected_decision": {
                    "candidate_ids": ["CAND-A"],
                    "reason": "Postmortem correction promotes direct issuer.",
                },
                "corrected_candidate_ids": ["CAND-A"],
                "missed_ticker": "000001",
                "missed_company_name": "Synthetic Issuer",
                "correction_rationale": "Direct issuer evidence should seed candidate generation.",
                "training_eligible": True,
                "eligibility_reason": "synthetic explicit correction label",
                "provenance_source_ids": ["SRC-SYNTH-1"],
            }
        )
    if include_company_memory_delta:
        records.append(
            {
                "record_id": "BRAIN-SYNTH-COMPANY",
                "record_type": "company_memory_delta",
                "episode_id": episode_id,
                "trade_date": trade_day.isoformat(),
                "available_from": available_from,
                "training_target": "company_memory",
                "known_at": available_from,
                "ticker": "000001",
                "company_name": "Synthetic Issuer",
                "aliases": ["Synthetic Issuer"],
                "business_descriptions": ["Builds synthetic test components."],
                "supply_chain_roles": ["direct catalyst supplier"],
                "prior_market_narratives": ["Synthetic issuer previously led direct catalysts."],
                "contradictory_relations": ["Theme relation remains unverified."],
                "training_eligible": False,
                "eligibility_reason": "company memory delta is audit memory",
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
    inferred_source_event_ids = sorted(
        {
            event_id
            for record in records
            for event_id in (
                [record["event_id"]]
                if isinstance(record.get("event_id"), str)
                else record.get("event_ids", [])
                if isinstance(record.get("event_ids"), list)
                else []
            )
            if isinstance(event_id, str)
        }
    )
    source_ledger_payload = {
        "source_id": "SRC-SYNTH-1",
        "source_type": "synthetic_fixture",
        "title": "Synthetic source",
    }
    effective_source_event_ids = (
        inferred_source_event_ids if source_event_ids is None else source_event_ids
    )
    if effective_source_event_ids:
        source_ledger_payload["event_ids"] = effective_source_event_ids
    source_ledger = json.dumps(
        source_ledger_payload,
        ensure_ascii=False,
    )
    research_episode = json.dumps(
        {
            "schema_version": episode_schema_version,
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
            "schema_version": manifest_schema_version,
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


def test_v10_bundle_uses_version_adapter_without_legacy_schema_loss(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v10_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            schema_version="nslab.research_bundle.v10",
            manifest_schema_version="nslab.bundle_manifest.v10",
            episode_schema_version="nslab.research_episode.v10",
        ),
        encoding="utf-8",
    )

    inspection = inspect_versioned_bundle(bundle)
    result = import_versioned_bundle(bundle, root=tmp_path)
    records = BrainRecordStore(tmp_path).read_episode_records("NSLAB-20300110-SYNTH")
    envelope = _read_json(
        tmp_path
        / "research"
        / "episodes"
        / "NSLAB-20300110-SYNTH"
        / "bundle_envelope.json"
    )

    assert inspection["adapter"] == "v10"
    assert inspection["supported"] is True
    assert inspection["forward_compatible_raw_only"] is False
    assert inspection["raw_record_count"] == 3
    assert inspection["normalized_record_count"] == 3
    assert inspection["dropped_record_count"] == 0
    assert inspection["training_eligible_record_count"] == 2
    assert result.status == "imported"
    assert result.adapter_name == "v10"
    assert result.record_count == 3
    assert len(records) == 3
    assert envelope["bundle_schema_version"] == "nslab.research_bundle.v10"
    assert envelope["manifest_schema_version"] == "nslab.bundle_manifest.v10"
    assert envelope["episode_schema_version"] == "nslab.research_episode.v10"


def test_legacy_v1_bundle_adapter_preserves_records_without_schema_loss(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_legacy_v1_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            schema_version="nslab.research_bundle.v1",
            manifest_schema_version="nslab.bundle_manifest.v1",
            episode_schema_version="nslab.research_episode.v1",
        ),
        encoding="utf-8",
    )

    inspection = inspect_versioned_bundle(bundle)
    result = import_versioned_bundle(bundle, root=tmp_path)
    records = BrainRecordStore(tmp_path).read_episode_records("NSLAB-20300110-SYNTH")
    envelope = _read_json(
        tmp_path
        / "research"
        / "episodes"
        / "NSLAB-20300110-SYNTH"
        / "bundle_envelope.json"
    )

    assert inspection["adapter"] == "legacy-v1"
    assert inspection["supported"] is True
    assert inspection["raw_record_count"] == 3
    assert inspection["normalized_record_count"] == 3
    assert inspection["dropped_record_count"] == 0
    assert result.status == "imported"
    assert result.adapter_name == "legacy-v1"
    assert result.record_count == 3
    assert len(records) == 3
    assert envelope["bundle_schema_version"] == "nslab.research_bundle.v1"
    assert envelope["manifest_schema_version"] == "nslab.bundle_manifest.v1"
    assert envelope["episode_schema_version"] == "nslab.research_episode.v1"


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


def test_record_store_deep_audit_rejects_source_ledger_source_id_gap(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(include_unknown=False), encoding="utf-8")
    import_versioned_bundle(bundle, root=tmp_path)

    envelope_path = (
        tmp_path
        / "research"
        / "episodes"
        / "NSLAB-20300110-SYNTH"
        / "bundle_envelope.json"
    )
    envelope = _read_json(envelope_path)
    source_ledger_path = tmp_path / envelope["raw_block_paths"]["source_ledger.jsonl"]
    tampered_ledger = json.dumps(
        {
            "source_id": "SRC-TAMPERED",
            "source_type": "synthetic_fixture",
            "title": "Tampered source",
        },
        ensure_ascii=False,
    )
    source_ledger_path.write_text(tampered_ledger, encoding="utf-8")
    envelope["raw_block_hashes"]["source_ledger.jsonl"] = sha256_text(tampered_ledger)
    envelope_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["raw_block_hash_mismatch_episode_ids"] == []
    assert audit["source_ledger_source_id_mismatch_episode_ids"] == [
        "NSLAB-20300110-SYNTH"
    ]
    assert (
        "source_ledger source IDs do not match normalized episode index"
        in audit["findings"]
    )


def test_missing_record_event_reference_blocks_acceptance(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "missing_event_reference_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            include_unknown=False,
            include_event_edge=True,
            source_event_ids=[],
        ),
        encoding="utf-8",
    )

    inspection = inspect_versioned_bundle(bundle)
    validation = inspection["validation"]

    assert inspection["validation_passed"] is False
    assert inspection["missing_payload_reference_count"] == 1
    assert validation["payload_reference_closure_status"] == "missing_refs"
    assert validation["missing_payload_references"] == [
        {
            "reference_type": "event",
            "reference_id": "EVT-SYNTH-1",
            "record_ids": ["BRAIN-SYNTH-EDGE"],
        }
    ]
    with pytest.raises(VersionedBundleImportError, match="bundle validation failed"):
        import_versioned_bundle(bundle, root=tmp_path)

    report = _read_json(tmp_path / "diagnostics" / "bundle_import_report.json")
    assert report["status"] == "BUNDLE_VALIDATION_FAILED"
    assert report["validation"]["missing_payload_references"] == [
        {
            "reference_type": "event",
            "reference_id": "EVT-SYNTH-1",
            "record_ids": ["BRAIN-SYNTH-EDGE"],
        }
    ]


def test_record_store_deep_audit_rejects_payload_reference_gap(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(include_unknown=False, include_event_edge=True),
        encoding="utf-8",
    )
    import_versioned_bundle(bundle, root=tmp_path)

    envelope_path = (
        tmp_path
        / "research"
        / "episodes"
        / "NSLAB-20300110-SYNTH"
        / "bundle_envelope.json"
    )
    envelope = _read_json(envelope_path)
    source_ledger_path = tmp_path / envelope["raw_block_paths"]["source_ledger.jsonl"]
    tampered_ledger = json.dumps(
        {
            "source_id": "SRC-SYNTH-1",
            "source_type": "synthetic_fixture",
            "title": "Synthetic source",
        },
        ensure_ascii=False,
    )
    source_ledger_path.write_text(tampered_ledger, encoding="utf-8")
    envelope["raw_block_hashes"]["source_ledger.jsonl"] = sha256_text(tampered_ledger)
    envelope_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["raw_block_hash_mismatch_episode_ids"] == []
    assert audit["source_ledger_source_id_mismatch_episode_ids"] == []
    assert audit["records_with_unknown_payload_references"] == ["BRAIN-SYNTH-EDGE"]
    assert audit["missing_payload_references"] == [
        {
            "episode_id": "NSLAB-20300110-SYNTH",
            "reference_type": "event",
            "reference_id": "EVT-SYNTH-1",
            "record_ids": ["BRAIN-SYNTH-EDGE"],
        }
    ]
    assert (
        "record fact/inference/event references are not closed by source ledgers"
        in audit["findings"]
    )


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
    assert preference_rows[0]["input"]["blind_preferred_ticker"] == "000001"
    assert preference_rows[0]["input"]["blind_rejected_ticker"] == "000002"
    assert preference_rows[0]["output"]["outcome_winner_ticker"] == "000001"
    leader_pair_row = duckdb.sql(
        "select blind_preferred_ticker, blind_rejected_ticker, outcome_winner_ticker "
        f"from read_parquet('{(tmp_path / 'warehouse' / 'leader_pairs.parquet').as_posix()}')"
    ).fetchone()
    assert leader_pair_row == ("000001", "000002", "000001")
    future_filtered_rows = WarehouseStore(tmp_path).query_brain_records(
        record_type="supervised_issuer_day_case",
        ticker="000001",
        training_eligible=True,
        available_from_as_of="2030-01-10T23:59:59+09:00",
    )
    assert future_filtered_rows == []
    queried_rows = WarehouseStore(tmp_path).query_brain_records(
        record_type="supervised_issuer_day_case",
        ticker="000001",
        training_eligible=True,
        available_from_as_of="2030-01-11T00:00:00+09:00",
    )
    assert len(queried_rows) == 1
    assert queried_rows[0]["record_id"] == "BRAIN-SYNTH-ISSUER"
    assert queried_rows[0]["ticker"] == "000001"
    assert queried_rows[0]["payload"]["issuer_day_case_id"] == "20300110:000001"
    assert _read_json(sft.manifest_path)["source_mode"] == "brain_records"


def test_training_export_uses_explicit_error_correction_records(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(include_unknown=False, include_error_correction=True),
        encoding="utf-8",
    )
    import_versioned_bundle(bundle, root=tmp_path)

    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")

    rows = _jsonl(sft.path)
    correction_row = next(row for row in rows if row["record_id"] == "BRAIN-SYNTH-ERROR")
    assert correction_row["task"] == "record_error_correction"
    assert correction_row["training_category"] == "failure_correction_examples"
    assert correction_row["source_phase"] == "POSTMORTEM"
    assert correction_row["hindsight_safe_for_blind_sft"] is False
    assert correction_row["eligibility_basis"]["satisfied"] is True
    assert correction_row["input"]["original_decision"] == {
        "candidate_ids": ["CAND-B"],
        "reason": "Blind pass underweighted direct evidence.",
    }
    assert correction_row["output"] == {
        "corrected_candidate_ids": ["CAND-A"],
        "corrected_company_name": None,
        "corrected_decision": {
            "candidate_ids": ["CAND-A"],
            "reason": "Postmortem correction promotes direct issuer.",
        },
        "corrected_ticker": None,
        "correction_mode": "add_candidate",
        "correction_rationale": "Direct issuer evidence should seed candidate generation.",
        "eligibility_reason": "synthetic explicit correction label",
        "error_id": "ERR-SYNTH-1",
        "error_type": "missed_direct_candidate",
        "missed_company_name": "Synthetic Issuer",
        "missed_ticker": "000001",
    }
    assert "response_class" not in correction_row["output"]
    manifest = _read_json(sft.manifest_path)
    assert manifest["eligible_record_count"] == 2
    assert manifest["exported_record_count"] == 2
    assert manifest["task_counts"]["record_error_correction"] == 1
    assert manifest["category_counts"]["failure_correction_examples"] == 1
    assert audit_training_exports(tmp_path)["passed"] is True


def test_event_ticker_edge_records_project_to_warehouse_and_coverage(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(include_unknown=False, include_event_edge=True),
        encoding="utf-8",
    )
    import_versioned_bundle(bundle, root=tmp_path)

    counts = WarehouseStore(tmp_path).rebuild_all()

    assert counts["brain_records"] == 3
    assert counts["event_ticker_edges"] == 1
    assert counts["record_provenance"] == 3
    edge_row = duckdb.sql(
        "select source_kind, record_id, edge_id, episode_id, event_id, ticker, "
        "company_name, relation_class, directly_mentioned "
        f"from read_parquet('{(tmp_path / 'warehouse' / 'event_ticker_edges.parquet').as_posix()}')"
    ).fetchone()
    assert edge_row == (
        "brain_record_edge",
        "BRAIN-SYNTH-EDGE",
        "EDGE-SYNTH-1",
        "NSLAB-20300110-SYNTH",
        "EVT-SYNTH-1",
        "000001",
        "Synthetic Issuer",
        "DIRECT",
        True,
    )

    audit = audit_coverage(tmp_path)

    assert audit["warehouse_expected_source_counts"]["event_ticker_edges.parquet"] == {
        "expected": 1,
        "source_label": "accepted event ticker edges plus brain record edge records",
    }
    assert audit["warehouse_count_mismatches"] == {}
    assert audit["warehouse_identity_mismatches"] == {}


def test_warehouse_rebuild_applies_company_memory_delta_records(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            include_unknown=False,
            include_company_memory_delta=True,
        ),
        encoding="utf-8",
    )
    import_versioned_bundle(bundle, root=tmp_path)

    counts = WarehouseStore(tmp_path).rebuild_all()

    assert counts["brain_records"] == 3
    assert counts["company_memory_delta_records"] == 1
    assert counts["company_memory_delta_written"] == 1
    assert counts["company_memory"] == 1
    assert len(list((tmp_path / "memory" / "company_memory").glob("*.json"))) == 1
    company_row = duckdb.sql(
        "select ticker, company_name, known_at, business_descriptions_json, "
        "supply_chain_roles_json, prior_market_narratives_json, "
        "contradictory_relations_json, provenance_json "
        f"from read_parquet('{(tmp_path / 'warehouse' / 'company_memory.parquet').as_posix()}')"
    ).fetchone()
    assert company_row[0:7] == (
        "000001",
        "Synthetic Issuer",
        "2030-01-11T00:00:00+09:00",
        '["Builds synthetic test components."]',
        '["direct catalyst supplier"]',
        '["Synthetic issuer previously led direct catalysts."]',
        '["Theme relation remains unverified."]',
    )
    assert "company_memory_delta_record" in str(company_row[7])
    assert "memory/records/NSLAB-20300110-SYNTH.jsonl" in str(company_row[7])

    audit = audit_coverage(tmp_path)

    assert audit["warehouse_expected_source_counts"]["company_memory.parquet"] == {
        "expected": 1,
        "source_label": "source company memory files",
    }
    assert audit["warehouse_count_mismatches"] == {}
    assert audit["warehouse_identity_mismatches"] == {}


def test_coverage_audit_rejects_record_projection_identity_mismatch(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "synthetic_v11_bundle.md"
    bundle.write_text(_synthetic_v11_bundle(include_unknown=False), encoding="utf-8")
    import_versioned_bundle(bundle, root=tmp_path)
    WarehouseStore(tmp_path).rebuild_all()

    brain_records_path = tmp_path / "warehouse" / "brain_records.parquet"
    tampered_path = tmp_path / "warehouse" / "brain_records_tampered.parquet"
    brain_records_sql_path = brain_records_path.as_posix().replace("'", "''")
    tampered_sql_path = tampered_path.as_posix().replace("'", "''")
    duckdb.sql(
        "copy ("
        "select * replace ("
        "case when record_id = 'BRAIN-SYNTH-ISSUER' "
        "then 'BRAIN-TAMPERED' else record_id end as record_id"
        f") from read_parquet('{brain_records_sql_path}')"
        f") to '{tampered_sql_path}' (format parquet)"
    )
    tampered_path.replace(brain_records_path)

    audit = audit_coverage(tmp_path)

    assert audit["warehouse_count_mismatches"] == {}
    assert audit["warehouse_identity_mismatches"] == {
        "brain_records.parquet": {
            "extra": ["BRAIN-TAMPERED"],
            "missing": ["BRAIN-SYNTH-ISSUER"],
        }
    }
    assert (
        "warehouse: brain_records.parquet ids mismatch; missing normalized "
        "brain record ids: BRAIN-SYNTH-ISSUER; extra projected ids: BRAIN-TAMPERED"
    ) in audit["findings"]
    assert audit["warehouse_projection_synced"] is False


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


def test_invalid_outcome_label_quality_blocks_acceptance(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "invalid_label_quality_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            include_unknown=False,
            issuer_label_quality="model_inference_unverified",
        ),
        encoding="utf-8",
    )

    inspection = inspect_versioned_bundle(bundle)
    validation = inspection["validation"]

    assert inspection["validation_passed"] is False
    assert inspection["outcome_label_quality_valid"] is False
    assert inspection["invalid_outcome_label_quality_record_count"] == 1
    assert validation["outcome_label_quality_valid"] is False
    assert validation["invalid_outcome_label_quality_record_ids"] == [
        "BRAIN-SYNTH-ISSUER"
    ]

    with pytest.raises(VersionedBundleImportError):
        import_versioned_bundle(bundle, root=tmp_path, accepted=True)

    report = _read_json(tmp_path / "diagnostics" / "bundle_import_report.json")
    assert report["status"] == "BUNDLE_VALIDATION_FAILED"
    assert report["validation"]["invalid_outcome_label_quality_record_ids"] == [
        "BRAIN-SYNTH-ISSUER"
    ]
    assert list((tmp_path / "data" / "quarantine" / "research_bundles").glob("*/original_bundle.md"))


def test_record_store_audit_rejects_invalid_outcome_label_quality(
    tmp_path: Path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "valid_label_quality_v11_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(
            include_unknown=False,
            issuer_label_quality="verified",
        ),
        encoding="utf-8",
    )
    import_versioned_bundle(bundle, root=tmp_path)

    episode_id = "NSLAB-20300110-SYNTH"
    record_path = tmp_path / "memory" / "records" / f"{episode_id}.jsonl"
    records = [
        json.loads(line)
        for line in record_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for record in records:
        if record["record_id"] != "BRAIN-SYNTH-ISSUER":
            continue
        record["payload"]["label_quality"] = "model_inference_unverified"
        record["payload"]["D_outcome"]["label_quality"] = "model_inference_unverified"
        record["normalized_payload_sha256"] = sha256_text(
            canonical_json(record["payload"])
        )
        record["raw_payload_sha256"] = sha256_text(
            json.dumps(record["payload"], ensure_ascii=False, sort_keys=True)
        )
    record_payload = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    record_path.write_text(record_payload, encoding="utf-8")

    manifest_path = tmp_path / "memory" / "record_manifests" / f"{episode_id}.json"
    manifest = _read_json(manifest_path)
    manifest["records_sha256"] = sha256_text(record_payload)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    envelope_path = tmp_path / "research" / "episodes" / episode_id / "bundle_envelope.json"
    envelope = _read_json(envelope_path)
    brain_delta_path = tmp_path / envelope["raw_block_paths"]["brain_delta.jsonl"]
    raw_rows = [
        json.loads(line)
        for line in brain_delta_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in raw_rows:
        if row.get("record_id") == "BRAIN-SYNTH-ISSUER":
            row["label_quality"] = "model_inference_unverified"
            row["D_outcome"]["label_quality"] = "model_inference_unverified"
    raw_payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in raw_rows)
    brain_delta_path.write_text(raw_payload, encoding="utf-8")
    envelope["raw_block_hashes"]["brain_delta.jsonl"] = sha256_text(raw_payload)
    envelope_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    audit = audit_record_store(tmp_path, deep=True)

    assert audit["passed"] is False
    assert audit["payload_hash_mismatch_record_ids"] == []
    assert audit["manifest_hash_mismatch_episode_ids"] == []
    assert audit["raw_block_hash_mismatch_episode_ids"] == []
    assert audit["records_with_raw_payload_hash_mismatch"] == []
    assert audit["invalid_outcome_label_quality_record_ids"] == [
        "BRAIN-SYNTH-ISSUER"
    ]
    assert "record outcome label_quality values are invalid" in audit["findings"]


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
