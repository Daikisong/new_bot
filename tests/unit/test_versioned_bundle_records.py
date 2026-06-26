from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

import duckdb

from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.research_import.versioned_bundle import (
    import_versioned_bundle,
    inspect_versioned_bundle,
)
from news_scalping_lab.training import export_training
from news_scalping_lab.utils import KST, sha256_text
from news_scalping_lab.warehouse import WarehouseStore


def _payload_block(payload: str, fence: str) -> str:
    return f"```{fence}\n{payload}\n```"


def _synthetic_v11_bundle(
    *,
    schema_version: str = "nslab.research_bundle.v11",
    include_unknown: bool = True,
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
            "available_from": available_from,
            "status": "tentative",
            "confidence_label": "low",
            "training_target": "issuer_day_price_response",
            "issuer_day_case_id": "20300110:000001",
            "ticker": "000001",
            "company_name": "Synthetic Issuer",
            "response_class": "positive_high10",
            "sample_weight": 1.0,
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
    validation_report = json.dumps(
        {
            "schema_version": "nslab.validation_report.v3",
            "critical_error_count": 0,
            "computed_counts": {
                "brain_delta_record_count": len(records),
                "training_eligible_record_count": 2,
            },
        },
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
            "training_eligible_record_count": 2,
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
    assert inspection["training_eligible_record_count"] == 2
    assert result.record_count == 3
    assert result.training_eligible_record_count == 2
    assert unknown.typed_payload_status == "UNKNOWN_TYPED_PAYLOAD"
    assert unknown.training_eligible is False
    assert (tmp_path / "research" / "episodes" / "NSLAB-20300110-SYNTH" / "original_bundle.md").exists()
    assert (tmp_path / "memory" / "records" / "NSLAB-20300110-SYNTH.jsonl").exists()


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
    sft_rows = _jsonl(sft.path)
    preference_rows = _jsonl(preference.path)
    assert {row["record_id"] for row in sft_rows} == {"BRAIN-SYNTH-ISSUER"}
    assert {row["record_id"] for row in preference_rows} == {"BRAIN-SYNTH-PAIR"}
    assert _read_json(sft.manifest_path)["source_mode"] == "brain_records"


def test_unknown_bundle_version_is_quarantined_without_record_loss(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "future_bundle.md"
    bundle.write_text(
        _synthetic_v11_bundle(schema_version="nslab.research_bundle.v99"),
        encoding="utf-8",
    )

    result = import_versioned_bundle(bundle, root=tmp_path)

    assert result.status == "UNSUPPORTED_BUNDLE_VERSION"
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
