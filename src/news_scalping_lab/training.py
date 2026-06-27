"""Training data exports.

Exports are derived artifacts from accepted research episodes. They separate blind
reasoning from postmortem labels so hindsight never becomes a fake blind answer.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.models import (
    Candidate,
    EligibilityMatrix,
    OutcomeLabels,
    ResearchEpisode,
)
from news_scalping_lab.diagnostic_reports import write_diagnostic_report
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import canonical_json, file_sha256, now_kst, stable_id, write_json

VALID_KINDS = {"sft", "preference", "evals"}

TASK_TRAINING_CATEGORY = {
    "blind_reasoning": "blind_reasoning_examples",
    "theme_formation": "theme_formation_examples",
    "beneficiary_discovery": "beneficiary_discovery_examples",
    "leader_selection_comparison": "leader_selection_comparisons",
    "positive_vs_negative_candidate_preference": "positive_vs_negative_candidate_preferences",
    "postmortem_preference_summary": "positive_vs_negative_candidate_preferences",
    "failure_correction": "failure_correction_examples",
    "candidate_outcome_eval": "evaluation_examples",
    "failure_code_eval": "evaluation_examples",
    "record_supervised_issuer_day": "issuer_day_supervised_records",
    "record_supervised_direct_event": "direct_event_supervised_records",
    "record_supervised_theme_formation": "theme_formation_examples",
    "record_beneficiary_discovery": "beneficiary_discovery_examples",
    "record_error_correction": "failure_correction_examples",
    "record_eval": "evaluation_examples",
}

REQUIRED_TRAINING_CATEGORIES = [
    "blind_reasoning_examples",
    "theme_formation_examples",
    "beneficiary_discovery_examples",
    "leader_selection_comparisons",
    "positive_vs_negative_candidate_preferences",
    "failure_correction_examples",
]

KIND_TRAINING_CATEGORIES = {
    "sft": [
        "blind_reasoning_examples",
        "theme_formation_examples",
        "beneficiary_discovery_examples",
        "leader_selection_comparisons",
        "failure_correction_examples",
    ],
    "preference": ["positive_vs_negative_candidate_preferences"],
    "evals": ["evaluation_examples"],
}

RECORD_SFT_TRAINING_CATEGORIES = [
    "issuer_day_supervised_records",
    "direct_event_supervised_records",
]

ERROR_CORRECTION_RECORD_TYPES = {
    "candidate_generation_error_case",
    "candidate_ranking_error_case",
    "ranking_error_case",
    "row_disposition_error_case",
    "entity_resolution_error_case",
}


@dataclass(frozen=True)
class TrainingExportResult:
    path: Path
    manifest_path: Path
    row_count: int


def audit_training_exports(root: Path) -> dict[str, Any]:
    findings: list[str] = []
    manifests: dict[str, Any] = {}
    summaries: dict[str, dict[str, Any]] = {}
    record_store_hashes = {
        record.record_id: record.normalized_payload_sha256
        for record in BrainRecordStore(root).list_records()
    }
    source_record_ids: set[str] = set()
    training_eligible_record_ids: set[str] = set()
    exported_record_ids: set[str] = set()
    for kind in sorted(VALID_KINDS):
        manifest_path = root / "training_exports" / kind / "manifest.json"
        if not manifest_path.exists():
            findings.append(f"{kind}: manifest is missing")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            findings.append(f"{kind}: manifest is invalid JSON: {exc}")
            continue
        manifests[kind] = manifest
        summaries[kind] = _training_manifest_summary(manifest)
        _audit_source_hash_contract(kind, manifest, findings)
        _audit_record_store_source_contract(
            kind,
            manifest,
            record_store_hashes,
            findings,
        )
        _audit_weight_validation_contract(kind, manifest, findings)
        source_record_ids.update(_source_record_ids_from_manifest(manifest))
        training_eligible_record_ids.update(_eligible_record_ids_from_manifest(manifest))
        output_file = manifest.get("output_file")
        output_rows: list[dict[str, Any]] = []
        output_path = _artifact_path(
            root,
            output_file,
            findings=findings,
            label=f"{kind}: output_file",
        )
        if output_path is None or not output_path.exists():
            findings.append(f"{kind}: output_file is missing")
        else:
            output_rows = _read_training_rows(kind, "output_file", output_path, findings)
            _audit_training_artifact(
                kind=kind,
                label="output_file",
                path=output_path,
                rows=output_rows,
                expected_count=manifest.get("row_count"),
                expected_sha256=manifest.get("output_sha256"),
                findings=findings,
            )
            _audit_training_rows(kind, output_rows, findings)
            _audit_record_export_scope(kind, manifest, output_rows, findings)
            row_record_ids = _record_ids_from_rows(output_rows)
            exported_record_ids.update(row_record_ids)
            training_eligible_record_ids.update(row_record_ids)
        if (
            manifest.get("source_mode") == "brain_records"
            and manifest.get("weight_validation_status") == "failed"
        ):
            findings.append(f"{kind}: record weight validation failed")
        if manifest.get("source_mode") == "brain_records" and kind == "preference":
            _audit_record_preference_rows(kind, output_rows, findings)
        _audit_phase_outputs(kind, root, manifest, findings)
    unique_skipped_record_ids = sorted(source_record_ids - exported_record_ids)
    skipped_records_by_export = _skipped_records_by_export(summaries)
    report = {
        "schema_version": "nslab.training_audit.v1",
        "passed": not findings,
        "findings": findings,
        "manifests": manifests,
    }
    write_diagnostic_report(
        root,
        "training_export_report",
        {
            "schema_version": "nslab.training_export_diagnostics.v1",
            "passed": report["passed"],
            "findings": findings,
            "export_kinds": sorted(VALID_KINDS),
            "available_manifest_kinds": sorted(manifests),
            "missing_manifest_kinds": sorted(VALID_KINDS - set(manifests)),
            "brain_record_source_required": bool(record_store_hashes),
            "record_store_source_record_count": len(record_store_hashes),
            "source_episode_count": _max_int(summaries, "source_episode_count"),
            "source_record_count": _max_int(summaries, "source_record_count"),
            "eligible_record_count": _sum_int(summaries, "eligible_record_count"),
            "exported_record_count": _sum_int(summaries, "exported_record_count"),
            "row_count": _sum_int(summaries, "row_count"),
            "skipped_record_count": _sum_int(summaries, "skipped_record_count"),
            "per_export_eligible_record_count": _sum_int(
                summaries,
                "eligible_record_count",
            ),
            "per_export_exported_record_count": _sum_int(
                summaries,
                "exported_record_count",
            ),
            "per_export_skipped_record_count": _sum_int(
                summaries,
                "skipped_record_count",
            ),
            "unique_source_record_count": len(source_record_ids),
            "unique_training_eligible_record_count": len(training_eligible_record_ids),
            "unique_exported_record_count": len(exported_record_ids),
            "unique_skipped_record_count": len(unique_skipped_record_ids),
            "unique_source_record_ids": sorted(source_record_ids),
            "unique_training_eligible_record_ids": sorted(training_eligible_record_ids),
            "unique_exported_record_ids": sorted(exported_record_ids),
            "unique_skipped_record_ids": unique_skipped_record_ids,
            "skipped_records_by_export": skipped_records_by_export,
            "skipped_record_reasons_by_record_id": (
                _skipped_record_reasons_by_record_id(summaries)
            ),
            "unique_skipped_record_reasons_by_record_id": (
                _skipped_record_reasons_by_record_id(
                    summaries,
                    include_ids=set(unique_skipped_record_ids),
                )
            ),
            "skipped_record_reason_counts": _skipped_record_reason_counts(summaries),
            "blind_safe_row_count": _sum_int(summaries, "blind_safe_row_count"),
            "hindsight_row_count": _sum_int(summaries, "hindsight_row_count"),
            "source_phase_counts": _sum_counter(summaries, "source_phase_counts"),
            "source_hashes": _merge_string_maps(summaries, "source_hashes"),
            "source_record_hashes": _merge_string_maps(
                summaries,
                "source_record_hashes",
            ),
            "source_record_hash_count": len(
                _merge_string_maps(summaries, "source_record_hashes")
            ),
            "counts_by_record_type": _max_counter(summaries, "counts_by_record_type"),
            "counts_by_training_target": _max_counter(
                summaries,
                "counts_by_training_target",
            ),
            "duplicate_issuer_day_count": _max_int(
                summaries,
                "duplicate_issuer_day_count",
            ),
            "duplicate_issuer_day_keys": _merged_string_lists(
                summaries,
                "duplicate_issuer_day_keys",
            ),
            "issuer_day_weight_sum_mismatch_count": _max_int(
                summaries,
                "issuer_day_weight_sum_mismatch_count",
            ),
            "issuer_day_weight_sum_mismatches": _merge_numeric_maps(
                summaries,
                "issuer_day_weight_sum_mismatches",
            ),
            "direct_event_weight_sum_mismatch_count": _max_int(
                summaries,
                "direct_event_weight_sum_mismatch_count",
            ),
            "direct_event_weight_sum_mismatches": _merge_numeric_maps(
                summaries,
                "direct_event_weight_sum_mismatches",
            ),
            "weight_validation_statuses": {
                kind: summary.get("weight_validation_status")
                for kind, summary in sorted(summaries.items())
            },
            "exports": summaries,
        },
    )
    return report


def _audit_source_hash_contract(
    kind: str,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    if manifest.get("source_mode") != "brain_records":
        return
    hashes = manifest.get("source_record_hashes")
    if not isinstance(hashes, dict) or not hashes:
        findings.append(f"{kind}: source_record_hashes are missing")
        return
    invalid_ids = [
        str(record_id)
        for record_id, digest in hashes.items()
        if not isinstance(record_id, str)
        or not record_id
        or not isinstance(digest, str)
        or len(digest) != 64
    ]
    if invalid_ids:
        findings.append(
            f"{kind}: source_record_hashes contain invalid hash entries: "
            f"{', '.join(sorted(invalid_ids))}"
        )
    expected_count = manifest.get("source_record_count")
    if (
        isinstance(expected_count, int)
        and not isinstance(expected_count, bool)
        and expected_count != len(hashes)
    ):
        findings.append(
            f"{kind}: source_record_hashes count {len(hashes)} does not match "
            f"source_record_count {expected_count}"
        )


def _audit_record_store_source_contract(
    kind: str,
    manifest: dict[str, Any],
    record_store_hashes: dict[str, str],
    findings: list[str],
) -> None:
    if not record_store_hashes:
        return
    if manifest.get("source_mode") != "brain_records":
        findings.append(
            f"{kind}: brain record store exists but export source_mode is not brain_records"
        )
        return
    manifest_hashes = _string_map(manifest.get("source_record_hashes"))
    missing_ids = sorted(set(record_store_hashes) - set(manifest_hashes))
    extra_ids = sorted(set(manifest_hashes) - set(record_store_hashes))
    mismatched_ids = sorted(
        record_id
        for record_id, digest in manifest_hashes.items()
        if record_store_hashes.get(record_id) is not None
        and record_store_hashes[record_id] != digest
    )
    expected_count = len(record_store_hashes)
    if manifest.get("source_record_count") != expected_count:
        findings.append(
            f"{kind}: source_record_count does not match current brain record store"
        )
    if missing_ids:
        findings.append(
            f"{kind}: source_record_hashes missing current brain records: "
            f"{', '.join(missing_ids)}"
        )
    if extra_ids:
        findings.append(
            f"{kind}: source_record_hashes contain records outside current store: "
            f"{', '.join(extra_ids)}"
        )
    if mismatched_ids:
        findings.append(
            f"{kind}: source_record_hashes mismatch current brain records: "
            f"{', '.join(mismatched_ids)}"
        )


def _audit_weight_validation_contract(
    kind: str,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    if manifest.get("source_mode") != "brain_records":
        return
    validation = manifest.get("weight_validation")
    if not isinstance(validation, dict):
        findings.append(f"{kind}: weight_validation is missing")
        return
    expected_fields = _weight_validation_manifest_fields(validation)
    for field, expected_value in expected_fields.items():
        if manifest.get(field) != expected_value:
            findings.append(f"{kind}: {field} does not match weight_validation")


def _source_record_ids_from_manifest(manifest: dict[str, Any]) -> set[str]:
    hashes = manifest.get("source_record_hashes")
    if not isinstance(hashes, dict):
        return set()
    return {record_id for record_id in hashes if isinstance(record_id, str)}


def _eligible_record_ids_from_manifest(manifest: dict[str, Any]) -> set[str]:
    explicit_ids = _string_list(manifest.get("eligible_record_ids"))
    if explicit_ids or "eligible_record_ids" in manifest:
        return set(explicit_ids)
    eligible_ids: set[str] = set()
    skipped_records = manifest.get("skipped_records")
    if isinstance(skipped_records, list):
        for item in skipped_records:
            if not isinstance(item, dict) or item.get("training_eligible") is not True:
                continue
            record_id = item.get("record_id")
            if isinstance(record_id, str):
                eligible_ids.add(record_id)
    return eligible_ids


def _skipped_record_ids_from_manifest(manifest: dict[str, Any]) -> set[str]:
    explicit_ids = _string_list(manifest.get("skipped_record_ids"))
    if explicit_ids or "skipped_record_ids" in manifest:
        return set(explicit_ids)
    return _skipped_record_ids_from_entries(manifest)


def _skipped_record_ids_from_entries(manifest: dict[str, Any]) -> set[str]:
    skipped_ids: set[str] = set()
    skipped_records = manifest.get("skipped_records")
    if isinstance(skipped_records, list):
        for item in skipped_records:
            if not isinstance(item, dict):
                continue
            record_id = item.get("record_id")
            if isinstance(record_id, str) and record_id:
                skipped_ids.add(record_id)
    return skipped_ids


def _skipped_record_entries_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    skipped_records = manifest.get("skipped_records")
    if not isinstance(skipped_records, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in skipped_records:
        if not isinstance(item, dict):
            continue
        record_id = item.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            continue
        skip_reasons = _string_list(item.get("skip_reasons"))
        if not skip_reasons and isinstance(item.get("reason"), str):
            skip_reasons = [str(item["reason"])]
        entries.append(
            {
                "record_id": record_id,
                "record_type": item.get("record_type"),
                "episode_id": item.get("episode_id"),
                "training_eligible": item.get("training_eligible"),
                "eligibility_reason": item.get("eligibility_reason"),
                "reason": skip_reasons[0] if skip_reasons else item.get("reason"),
                "skip_reasons": skip_reasons,
            }
        )
    return entries


def _record_ids_from_rows(rows: list[dict[str, Any]]) -> set[str]:
    return {
        record_id
        for row in rows
        if isinstance(record_id := row.get("record_id"), str) and record_id
    }


def _source_record_ids(records: list[BrainRecordEnvelope]) -> list[str]:
    return sorted(record.record_id for record in records)


def _eligible_record_ids_for_kind(
    kind: str,
    records: list[BrainRecordEnvelope],
) -> list[str]:
    return sorted(
        record.record_id
        for record in records
        if _record_selected_for_kind(kind, record) and record.training_eligible
    )


def _skipped_record_ids(skipped_records: list[dict[str, Any]]) -> list[str]:
    return sorted(
        record_id
        for item in skipped_records
        if isinstance(record_id := item.get("record_id"), str) and record_id
    )


def export_training(root: Path, *, kind: str) -> TrainingExportResult:
    if kind not in VALID_KINDS:
        raise ValueError("kind must be sft, preference, or evals")
    target_dir = root / "training_exports" / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    store = ResearchStore(root)
    episodes = store.list_accepted()
    source_hashes = store.accepted_hashes()
    records = BrainRecordStore(root).list_records()
    source_mode = "brain_records" if records else "legacy_research_episodes"
    rows = (
        _record_rows_for_kind(kind, records)
        if records
        else _rows_for_kind(kind, episodes, source_hashes=source_hashes)
    )
    training_categories = _training_categories_for_kind(kind, source_mode=source_mode)
    source_episode_ids = (
        sorted({record.episode_id for record in records})
        if records
        else [episode.episode_id for episode in episodes]
    )
    row_episode_ids = {
        episode_id
        for row in rows
        if isinstance(episode_id := row.get("episode_id"), str) and episode_id
    }
    skipped = [] if records else _skipped_episodes(kind, episodes, row_episode_ids=row_episode_ids)
    skipped_records = _skipped_records(kind, records) if records else []
    source_record_ids = _source_record_ids(records)
    eligible_record_ids = _eligible_record_ids_for_kind(kind, records)
    exported_record_ids = sorted(_record_ids_from_rows(rows))
    skipped_record_ids = _skipped_record_ids(skipped_records)
    weight_validation = _record_weight_validation(records) if records else {}
    weight_validation_fields = _weight_validation_manifest_fields(weight_validation)
    path = target_dir / f"{kind}.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    phase_outputs = _write_phase_outputs(
        root,
        target_dir,
        kind,
        rows,
        skipped_records=skipped_records,
    )
    manifest_path = target_dir / "manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": "nslab.training_export_manifest.v1",
            "kind": kind,
            "source_mode": source_mode,
            "created_at": now_kst().isoformat(),
            "row_count": len(rows),
            "episode_count": len(source_episode_ids),
            "source_episode_count": len(source_episode_ids),
            "source_record_count": len(records),
            "source_record_ids": source_record_ids,
            "eligible_record_count": len(eligible_record_ids),
            "eligible_record_ids": eligible_record_ids,
            "exported_record_count": len(exported_record_ids),
            "exported_record_ids": exported_record_ids,
            "skipped_record_count": len(skipped_records),
            "skipped_record_ids": skipped_record_ids,
            "skipped_records": skipped_records,
            "episode_ids": source_episode_ids,
            "eligible_episode_count": len(source_episode_ids) - len(skipped),
            "skipped_episode_count": len(skipped),
            "skipped_episodes": skipped,
            "source_hashes": source_hashes,
            "source_record_hashes": {
                record.record_id: record.normalized_payload_sha256 for record in records
            },
            "output_file": _project_relative_path(root, path),
            "output_sha256": file_sha256(path),
            "phase_outputs": phase_outputs,
            "task_counts": _task_counts(rows),
            "required_training_categories": REQUIRED_TRAINING_CATEGORIES,
            "training_categories": training_categories,
            "category_counts": _category_counts(
                rows,
                training_categories=training_categories,
            ),
            "missing_training_categories": _missing_training_categories(
                rows,
                training_categories=training_categories,
            ),
            "blind_safe_row_count": sum(
                1 for row in rows if row["hindsight_safe_for_blind_sft"] is True
            ),
            "hindsight_row_count": sum(
                1 for row in rows if row["hindsight_safe_for_blind_sft"] is False
            ),
            "source_phase_counts": _source_phase_counts(rows),
            "counts_by_record_type": _record_type_counts(records),
            "counts_by_training_target": _record_training_target_counts(records),
            **weight_validation_fields,
            "weight_validation": weight_validation,
            "notes": [
                (
                    "The combined output_file is for audit and compatibility; "
                    "use phase_outputs.BLIND for blind-only SFT."
                ),
                "Blind SFT rows use only blind inputs and blind outputs.",
                (
                    "Failure-correction SFT rows are POSTMORTEM rows and are "
                    "written to phase_outputs.POSTMORTEM."
                ),
                "Preference and eval rows may include postmortem/outcome labels.",
                "Do not train postmortem labels as if they were blind answers.",
                "Rows with source_phase=POSTMORTEM must not be mixed into blind SFT.",
                (
                    "Skipped brain records are written to phase_outputs.AUDIT_ONLY "
                    "for auditability and must not be used as training rows."
                ),
            ],
        },
    )
    write_diagnostic_report(
        root,
        "training_export_report",
        {
            "kind": kind,
            "source_mode": source_mode,
            "source_episode_count": len({record.episode_id for record in records})
            if records
            else len(episodes),
            "source_record_count": len(records),
            "source_record_ids": source_record_ids,
            "eligible_record_count": len(eligible_record_ids),
            "eligible_record_ids": eligible_record_ids,
            "exported_record_count": len(exported_record_ids),
            "exported_record_ids": exported_record_ids,
            "row_count": len(rows),
            "skipped_record_count": len(skipped_records),
            "skipped_record_ids": skipped_record_ids,
            "counts_by_record_type": _record_type_counts(records),
            "counts_by_training_target": _record_training_target_counts(records),
            **weight_validation_fields,
            "weight_validation": weight_validation,
            "output_file": _project_relative_path(root, path),
        },
    )
    return TrainingExportResult(path=path, manifest_path=manifest_path, row_count=len(rows))


def _training_manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    weight_fields = _weight_validation_summary_fields(manifest)
    return {
        "kind": manifest.get("kind"),
        "source_mode": manifest.get("source_mode"),
        "source_episode_count": manifest.get("source_episode_count"),
        "source_record_count": manifest.get("source_record_count"),
        "source_record_ids": _string_list(manifest.get("source_record_ids")),
        "eligible_record_count": manifest.get("eligible_record_count"),
        "eligible_record_ids": _string_list(manifest.get("eligible_record_ids")),
        "exported_record_count": manifest.get("exported_record_count"),
        "exported_record_ids": _string_list(manifest.get("exported_record_ids")),
        "row_count": manifest.get("row_count"),
        "skipped_record_count": manifest.get("skipped_record_count"),
        "skipped_record_ids": _string_list(manifest.get("skipped_record_ids")),
        "skipped_records": _skipped_record_entries_from_manifest(manifest),
        "blind_safe_row_count": manifest.get("blind_safe_row_count"),
        "hindsight_row_count": manifest.get("hindsight_row_count"),
        "counts_by_record_type": manifest.get("counts_by_record_type", {}),
        "counts_by_training_target": manifest.get("counts_by_training_target", {}),
        "category_counts": manifest.get("category_counts", {}),
        "missing_training_categories": manifest.get("missing_training_categories", []),
        "source_phase_counts": manifest.get("source_phase_counts", {}),
        "source_hashes": _string_map(manifest.get("source_hashes")),
        "source_record_hashes": _string_map(manifest.get("source_record_hashes")),
        "source_record_hash_count": len(_string_map(manifest.get("source_record_hashes"))),
        **weight_fields,
        "output_file": manifest.get("output_file"),
    }


def _sum_int(summaries: dict[str, dict[str, Any]], key: str) -> int:
    return sum(
        value
        for summary in summaries.values()
        if isinstance(value := summary.get(key), int) and not isinstance(value, bool)
    )


def _max_int(summaries: dict[str, dict[str, Any]], key: str) -> int:
    values = [
        value
        for summary in summaries.values()
        if isinstance(value := summary.get(key), int) and not isinstance(value, bool)
    ]
    return max(values) if values else 0


def _max_counter(
    summaries: dict[str, dict[str, Any]],
    key: str,
) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for summary in summaries.values():
        value = summary.get(key)
        if not isinstance(value, dict):
            continue
        for item_key, item_value in value.items():
            if isinstance(item_key, str) and isinstance(item_value, int):
                counter[item_key] = max(counter[item_key], item_value)
    return dict(sorted(counter.items()))


def _sum_counter(
    summaries: dict[str, dict[str, Any]],
    key: str,
) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for summary in summaries.values():
        value = summary.get(key)
        if not isinstance(value, dict):
            continue
        for item_key, item_value in value.items():
            if (
                isinstance(item_key, str)
                and isinstance(item_value, int)
                and not isinstance(item_value, bool)
            ):
                counter[item_key] += item_value
    return dict(sorted(counter.items()))


def _skipped_records_by_export(
    summaries: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        kind: records
        for kind, summary in sorted(summaries.items())
        if isinstance(records := summary.get("skipped_records"), list)
    }


def _skipped_record_reasons_by_record_id(
    summaries: dict[str, dict[str, Any]],
    *,
    include_ids: set[str] | None = None,
) -> dict[str, list[str]]:
    reasons_by_record: dict[str, set[str]] = {}
    for summary in summaries.values():
        skipped_records = summary.get("skipped_records")
        if not isinstance(skipped_records, list):
            continue
        for item in skipped_records:
            if not isinstance(item, dict):
                continue
            record_id = item.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                continue
            if include_ids is not None and record_id not in include_ids:
                continue
            reasons = _string_list(item.get("skip_reasons"))
            if not reasons and isinstance(item.get("reason"), str):
                reasons = [str(item["reason"])]
            reasons_by_record.setdefault(record_id, set()).update(reasons)
    return {
        record_id: sorted(reasons)
        for record_id, reasons in sorted(reasons_by_record.items())
    }


def _skipped_record_reason_counts(
    summaries: dict[str, dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for summary in summaries.values():
        skipped_records = summary.get("skipped_records")
        if not isinstance(skipped_records, list):
            continue
        for item in skipped_records:
            if not isinstance(item, dict):
                continue
            reasons = _string_list(item.get("skip_reasons"))
            if not reasons and isinstance(item.get("reason"), str):
                reasons = [str(item["reason"])]
            for reason in reasons:
                counts[reason] += 1
    return dict(sorted(counts.items()))


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        item_key: item_value
        for item_key, item_value in sorted(value.items())
        if isinstance(item_key, str) and isinstance(item_value, str)
    }


def _merge_string_maps(
    summaries: dict[str, dict[str, Any]],
    key: str,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    for summary in summaries.values():
        value = summary.get(key)
        if isinstance(value, dict):
            merged.update(_string_map(value))
    return dict(sorted(merged.items()))


def _merge_numeric_maps(
    summaries: dict[str, dict[str, Any]],
    key: str,
) -> dict[str, float]:
    merged: dict[str, float] = {}
    for summary in summaries.values():
        value = summary.get(key)
        if isinstance(value, dict):
            merged.update(_numeric_map(value))
    return dict(sorted(merged.items()))


def _merged_string_lists(
    summaries: dict[str, dict[str, Any]],
    key: str,
) -> list[str]:
    values: set[str] = set()
    for summary in summaries.values():
        raw = summary.get(key)
        if isinstance(raw, list):
            values.update(item for item in raw if isinstance(item, str))
    return sorted(values)


def _numeric_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {
        item_key: float(item_value)
        for item_key, item_value in sorted(value.items())
        if isinstance(item_key, str)
        and isinstance(item_value, int | float)
        and not isinstance(item_value, bool)
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _weight_validation_manifest_fields(
    weight_validation: dict[str, Any],
) -> dict[str, Any]:
    duplicate_keys = _string_list(weight_validation.get("duplicate_issuer_day_keys"))
    issuer_mismatches = _numeric_map(
        weight_validation.get("issuer_day_weight_sum_mismatches")
    )
    direct_mismatches = _numeric_map(
        weight_validation.get("direct_event_weight_sum_mismatches")
    )
    duplicate_count = weight_validation.get("duplicate_issuer_day_count")
    if not isinstance(duplicate_count, int) or isinstance(duplicate_count, bool):
        duplicate_count = len(duplicate_keys)
    return {
        "weight_validation_status": weight_validation.get("status"),
        "duplicate_issuer_day_count": duplicate_count,
        "duplicate_issuer_day_keys": duplicate_keys,
        "issuer_day_weight_sum_mismatch_count": len(issuer_mismatches),
        "issuer_day_weight_sum_mismatches": issuer_mismatches,
        "direct_event_weight_sum_mismatch_count": len(direct_mismatches),
        "direct_event_weight_sum_mismatches": direct_mismatches,
    }


def _weight_validation_summary_fields(manifest: dict[str, Any]) -> dict[str, Any]:
    validation = manifest.get("weight_validation")
    fallback = _weight_validation_manifest_fields(validation if isinstance(validation, dict) else {})
    return {
        field: manifest.get(field, fallback_value)
        for field, fallback_value in fallback.items()
    }


def _project_relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _write_phase_outputs(
    root: Path,
    target_dir: Path,
    kind: str,
    rows: list[dict[str, Any]],
    *,
    skipped_records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    file_names = {
        "BLIND": f"blind_{kind}.jsonl",
        "POSTMORTEM": f"postmortem_{kind}.jsonl",
    }
    for phase, file_name in file_names.items():
        phase_rows = [row for row in rows if row.get("source_phase") == phase]
        path = target_dir / file_name
        path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in phase_rows
            ),
            encoding="utf-8",
        )
        outputs[phase] = {
            "output_file": _project_relative_path(root, path),
            "output_sha256": file_sha256(path),
            "row_count": len(phase_rows),
            "source_phase": phase,
            "hindsight_safe_for_blind_sft": phase == "BLIND",
        }
    audit_only_path = target_dir / f"audit_only_{kind}.jsonl"
    audit_only_rows = _audit_only_rows(kind, skipped_records)
    audit_only_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in audit_only_rows
        ),
        encoding="utf-8",
    )
    outputs["AUDIT_ONLY"] = {
        "output_file": _project_relative_path(root, audit_only_path),
        "output_sha256": file_sha256(audit_only_path),
        "row_count": len(audit_only_rows),
        "source_phase": "AUDIT_ONLY",
        "hindsight_safe_for_blind_sft": False,
        "audit_only": True,
    }
    return outputs


def _audit_only_rows(
    kind: str,
    skipped_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for skipped in skipped_records:
        row = dict(skipped)
        row.update(
            {
                "schema_version": "nslab.training_audit_only_record.v1",
                "kind": kind,
                "source_phase": "AUDIT_ONLY",
                "hindsight_safe_for_blind_sft": False,
                "audit_only": True,
            }
        )
        rows.append(row)
    return rows


def _artifact_path(
    root: Path,
    value: object,
    *,
    findings: list[str] | None = None,
    label: str | None = None,
) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        if findings is not None and label is not None:
            findings.append(f"{label} must be project-relative")
        return None
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        if findings is not None and label is not None:
            findings.append(f"{label} escapes project root")
        return None
    return resolved


def _read_training_rows(
    kind: str,
    label: str,
    path: Path,
    findings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            findings.append(f"{kind}: {label} line {line_number} is invalid JSON: {exc}")
            continue
        if not isinstance(payload, dict):
            findings.append(f"{kind}: {label} line {line_number} is not an object")
            continue
        rows.append(payload)
    return rows


def _audit_training_artifact(
    *,
    kind: str,
    label: str,
    path: Path,
    rows: list[dict[str, Any]],
    expected_count: object,
    expected_sha256: object,
    findings: list[str],
) -> None:
    if isinstance(expected_count, int) and not isinstance(expected_count, bool):
        if len(rows) != expected_count:
            findings.append(
                f"{kind}: {label} row_count mismatch expected {expected_count}, got {len(rows)}"
            )
    else:
        findings.append(f"{kind}: {label} row_count is missing or invalid")
    if isinstance(expected_sha256, str) and expected_sha256:
        actual_sha256 = file_sha256(path)
        if actual_sha256 != expected_sha256:
            findings.append(f"{kind}: {label} sha256 mismatch")
    else:
        findings.append(f"{kind}: {label} sha256 is missing")


def _audit_training_rows(
    kind: str,
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    for row in rows:
        example_id = _row_identifier(row)
        eligibility = row.get("eligibility_basis")
        if not isinstance(eligibility, dict) or eligibility.get("satisfied") is not True:
            findings.append(f"{kind}: exported ineligible row {example_id}")
        source_phase = row.get("source_phase")
        hindsight_safe = row.get("hindsight_safe_for_blind_sft")
        if source_phase == "BLIND" and hindsight_safe is not True:
            findings.append(f"{kind}: BLIND row is not marked blind-safe {example_id}")
        elif source_phase == "POSTMORTEM" and hindsight_safe is not False:
            findings.append(f"{kind}: POSTMORTEM row is marked blind-safe {example_id}")
        elif source_phase not in {"BLIND", "POSTMORTEM"}:
            findings.append(f"{kind}: row has invalid source_phase {example_id}")


def _audit_record_export_scope(
    kind: str,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    if manifest.get("source_mode") != "brain_records":
        return
    source_ids = _source_record_ids_from_manifest(manifest)
    skipped_ids = _skipped_record_ids_from_entries(manifest)
    row_ids: list[str] = []
    for index, row in enumerate(rows, start=1):
        record_id = row.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            findings.append(f"{kind}: brain record export row {index} record_id is missing")
            continue
        row_ids.append(record_id)
    row_id_set = set(row_ids)
    _audit_manifest_record_id_list(
        kind,
        manifest,
        "source_record_ids",
        expected_ids=source_ids,
        findings=findings,
    )
    _audit_manifest_record_id_list(
        kind,
        manifest,
        "eligible_record_ids",
        expected_ids=row_id_set,
        findings=findings,
    )
    _audit_manifest_record_id_list(
        kind,
        manifest,
        "exported_record_ids",
        expected_ids=row_id_set,
        findings=findings,
    )
    _audit_manifest_record_id_list(
        kind,
        manifest,
        "skipped_record_ids",
        expected_ids=skipped_ids,
        findings=findings,
    )
    duplicate_row_ids = sorted(
        record_id for record_id, count in Counter(row_ids).items() if count > 1
    )
    unknown_row_ids = sorted(row_id_set - source_ids)
    exported_skipped_ids = sorted(row_id_set & skipped_ids)
    uncovered_ids = sorted(source_ids - row_id_set - skipped_ids)
    skipped_unknown_ids = sorted(skipped_ids - source_ids)
    if duplicate_row_ids:
        findings.append(
            f"{kind}: duplicate exported brain record IDs: "
            f"{', '.join(duplicate_row_ids)}"
        )
    if unknown_row_ids:
        findings.append(
            f"{kind}: exported brain record IDs outside source manifest: "
            f"{', '.join(unknown_row_ids)}"
        )
    if exported_skipped_ids:
        findings.append(
            f"{kind}: exported skipped brain record IDs: "
            f"{', '.join(exported_skipped_ids)}"
        )
    if uncovered_ids:
        findings.append(
            f"{kind}: source brain records are neither exported nor skipped: "
            f"{', '.join(uncovered_ids)}"
        )
    if skipped_unknown_ids:
        findings.append(
            f"{kind}: skipped brain record IDs outside source manifest: "
            f"{', '.join(skipped_unknown_ids)}"
        )


def _audit_manifest_record_id_list(
    kind: str,
    manifest: dict[str, Any],
    field: str,
    *,
    expected_ids: set[str],
    findings: list[str],
) -> None:
    if field not in manifest:
        return
    value = manifest.get(field)
    if not isinstance(value, list) or not all(
        isinstance(record_id, str) and record_id for record_id in value
    ):
        findings.append(f"{kind}: {field} is invalid")
        return
    observed_ids = set(value)
    if observed_ids != expected_ids:
        findings.append(f"{kind}: {field} does not match manifest records")


def _audit_record_preference_rows(
    kind: str,
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    for row in rows:
        row_id = _row_identifier(row)
        if row.get("record_type") != "blind_leader_preference_pair":
            findings.append(
                f"{kind}: brain record preference row is not a sealed leader pair "
                f"{row_id}"
            )
            continue
        input_payload = row.get("input")
        output_payload = row.get("output")
        if not isinstance(input_payload, dict):
            findings.append(f"{kind}: preference row input is invalid {row_id}")
            continue
        if not isinstance(output_payload, dict):
            findings.append(f"{kind}: preference row output is invalid {row_id}")
            continue
        for field in ("blind_preferred_ticker", "blind_rejected_ticker"):
            if not _non_empty_string(input_payload.get(field)):
                findings.append(
                    f"{kind}: preference row {field} is missing {row_id}"
                )
        if not _non_empty_string(output_payload.get("outcome_winner_ticker")):
            findings.append(
                f"{kind}: preference row outcome_winner_ticker is missing {row_id}"
            )
        preference_correct = output_payload.get("blind_preference_correct")
        training_mode = output_payload.get("training_mode")
        if not isinstance(preference_correct, bool):
            findings.append(
                f"{kind}: preference row blind_preference_correct is invalid {row_id}"
            )
        if training_mode not in {"positive_preference", "correction"}:
            findings.append(f"{kind}: preference row training_mode is invalid {row_id}")
        elif isinstance(preference_correct, bool) and (
            (preference_correct and training_mode != "positive_preference")
            or (not preference_correct and training_mode != "correction")
        ):
            findings.append(
                f"{kind}: preference row training_mode does not match "
                f"blind_preference_correct {row_id}"
            )


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _audit_phase_outputs(
    kind: str,
    root: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    phase_outputs = manifest.get("phase_outputs")
    if not isinstance(phase_outputs, dict):
        findings.append(f"{kind}: phase_outputs is missing")
        return
    for phase in ("BLIND", "POSTMORTEM"):
        metadata = phase_outputs.get(phase)
        if not isinstance(metadata, dict):
            findings.append(f"{kind}: phase_outputs.{phase} is missing")
            continue
        path = _artifact_path(
            root,
            metadata.get("output_file"),
            findings=findings,
            label=f"{kind}: phase_outputs.{phase} output_file",
        )
        if path is None or not path.exists():
            findings.append(f"{kind}: phase_outputs.{phase} output_file is missing")
            continue
        rows = _read_training_rows(kind, f"phase_outputs.{phase}", path, findings)
        _audit_training_artifact(
            kind=kind,
            label=f"phase_outputs.{phase}",
            path=path,
            rows=rows,
            expected_count=metadata.get("row_count"),
            expected_sha256=metadata.get("output_sha256"),
            findings=findings,
        )
        for row in rows:
            example_id = _row_identifier(row)
            if row.get("source_phase") != phase:
                findings.append(
                    f"{kind}: phase_outputs.{phase} contains {row.get('source_phase')} "
                    f"row {example_id}"
                )
            expected_blind_safe = phase == "BLIND"
            if row.get("hindsight_safe_for_blind_sft") is not expected_blind_safe:
                findings.append(
                    f"{kind}: phase_outputs.{phase} blind-safe flag mismatch {example_id}"
                )
    metadata = phase_outputs.get("AUDIT_ONLY")
    if not isinstance(metadata, dict):
        findings.append(f"{kind}: phase_outputs.AUDIT_ONLY is missing")
        return
    path = _artifact_path(
        root,
        metadata.get("output_file"),
        findings=findings,
        label=f"{kind}: phase_outputs.AUDIT_ONLY output_file",
    )
    if path is None or not path.exists():
        findings.append(f"{kind}: phase_outputs.AUDIT_ONLY output_file is missing")
        return
    rows = _read_training_rows(kind, "phase_outputs.AUDIT_ONLY", path, findings)
    _audit_training_artifact(
        kind=kind,
        label="phase_outputs.AUDIT_ONLY",
        path=path,
        rows=rows,
        expected_count=metadata.get("row_count"),
        expected_sha256=metadata.get("output_sha256"),
        findings=findings,
    )
    if metadata.get("source_phase") != "AUDIT_ONLY":
        findings.append(f"{kind}: phase_outputs.AUDIT_ONLY source_phase mismatch")
    if metadata.get("audit_only") is not True:
        findings.append(f"{kind}: phase_outputs.AUDIT_ONLY audit_only flag mismatch")
    if metadata.get("hindsight_safe_for_blind_sft") is not False:
        findings.append(
            f"{kind}: phase_outputs.AUDIT_ONLY blind-safe flag mismatch"
        )
    expected_rows = _audit_only_rows(kind, _valid_skipped_records(manifest))
    if rows != expected_rows:
        findings.append(f"{kind}: phase_outputs.AUDIT_ONLY rows mismatch")


def _valid_skipped_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    skipped_records = manifest.get("skipped_records")
    if not isinstance(skipped_records, list):
        return []
    return [item for item in skipped_records if isinstance(item, dict)]


def _row_identifier(row: dict[str, Any]) -> str:
    for key in ("example_id", "record_id", "episode_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return "<unknown>"


def _rows_for_kind(
    kind: str,
    episodes: list[ResearchEpisode],
    *,
    source_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    if kind == "sft":
        return [
            row
            for episode in episodes
            for row in _sft_rows(episode, source_hashes=source_hashes)
        ]
    if kind == "preference":
        return [
            row
            for episode in episodes
            for row in _preference_rows(episode, source_hashes=source_hashes)
        ]
    return [
        row
        for episode in episodes
        for row in _eval_rows(episode, source_hashes=source_hashes)
    ]


def _record_rows_for_kind(
    kind: str,
    records: list[BrainRecordEnvelope],
) -> list[dict[str, Any]]:
    selected = [
        record
        for record in records
        if _record_selected_for_kind(kind, record) and record.training_eligible
    ]
    if kind == "sft":
        return [_record_sft_row(record) for record in selected]
    if kind == "preference":
        return [_record_preference_row(record) for record in selected]
    return [_record_eval_row(record) for record in selected]


def _record_selected_for_kind(kind: str, record: BrainRecordEnvelope) -> bool:
    if kind == "preference":
        return (
            record.record_type == "blind_leader_preference_pair"
            and _record_has_sealed_preference_pair(record)
        )
    if kind == "evals":
        return record.record_type in {
            "supervised_issuer_day_case",
            "supervised_direct_event_case",
            "supervised_theme_formation_case",
            "theme_formation_case",
            "beneficiary_discovery_case",
        }
    return record.record_type in {
        "supervised_issuer_day_case",
        "supervised_direct_event_case",
        "supervised_theme_formation_case",
        "theme_formation_case",
        "beneficiary_discovery_case",
        *ERROR_CORRECTION_RECORD_TYPES,
    }


def _record_has_sealed_preference_pair(record: BrainRecordEnvelope) -> bool:
    payload = record.payload
    preferred = payload.get("blind_preferred_ticker") or payload.get(
        "blind_preferred_candidate_id"
    )
    rejected = payload.get("blind_rejected_ticker") or payload.get(
        "blind_rejected_candidate_id"
    )
    return isinstance(preferred, str) and bool(preferred) and isinstance(
        rejected,
        str,
    ) and bool(rejected)


def _record_sft_row(record: BrainRecordEnvelope) -> dict[str, Any]:
    if record.record_type in ERROR_CORRECTION_RECORD_TYPES:
        return _record_error_correction_sft_row(record)
    task = _record_sft_task(record)
    return _training_record_row(
        task=task,
        record=record,
        split="sft_records",
        input_payload={
            "record_type": record.record_type,
            "training_target": record.training_target,
            "safe_D1_features": record.payload.get("safe_D1_features"),
            "blind_fact_ids": record.payload.get("blind_fact_ids", []),
            "blind_inference_ids": record.payload.get("blind_inference_ids", []),
            "event_ids": record.payload.get("event_ids", []),
            "payload": record.payload,
        },
        output_payload={
            "response_class": record.payload.get("response_class"),
            "outcome": record.payload.get("D_outcome"),
            "sample_weight": record.payload.get("sample_weight"),
            "attribution_status": record.payload.get("attribution_status"),
            "eligibility_reason": record.eligibility_reason,
        },
        hindsight_safe=False,
    )


def _record_error_correction_sft_row(record: BrainRecordEnvelope) -> dict[str, Any]:
    payload = record.payload
    return _training_record_row(
        task="record_error_correction",
        record=record,
        split="sft_records",
        input_payload={
            "record_type": record.record_type,
            "training_target": record.training_target,
            "evidence_phase": record.evidence_phase,
            "safe_D1_features": payload.get("safe_D1_features"),
            "blind_fact_ids": payload.get("blind_fact_ids", []),
            "blind_inference_ids": payload.get("blind_inference_ids", []),
            "event_ids": payload.get("event_ids", []),
            "original_decision": payload.get("original_decision")
            or _payload_subset(
                payload,
                (
                    "original_candidate_ids",
                    "original_ticker",
                    "original_company_name",
                    "observed_error",
                    "failure_context",
                ),
            ),
            "payload": payload,
        },
        output_payload={
            "error_id": payload.get("error_id"),
            "error_type": payload.get("error_type"),
            "correction_mode": payload.get("correction_mode"),
            "correction_rationale": payload.get("correction_rationale")
            or payload.get("rationale"),
            "corrected_decision": payload.get("corrected_decision"),
            "corrected_candidate_ids": payload.get("corrected_candidate_ids", []),
            "corrected_ticker": payload.get("corrected_ticker"),
            "corrected_company_name": payload.get("corrected_company_name"),
            "missed_ticker": payload.get("missed_ticker"),
            "missed_company_name": payload.get("missed_company_name"),
            "eligibility_reason": record.eligibility_reason,
        },
        hindsight_safe=False,
    )


def _record_preference_row(record: BrainRecordEnvelope) -> dict[str, Any]:
    payload = record.payload
    training_mode = (
        "positive_preference"
        if payload.get("blind_preference_correct") is True
        else "correction"
    )
    return _training_record_row(
        task="positive_vs_negative_candidate_preference",
        record=record,
        split="preference",
        input_payload={
            "blind_pair_id": payload.get("blind_pair_id"),
            "blind_preferred_candidate_id": payload.get("blind_preferred_candidate_id"),
            "blind_rejected_candidate_id": payload.get("blind_rejected_candidate_id"),
            "blind_preferred_ticker": payload.get("blind_preferred_ticker"),
            "blind_rejected_ticker": payload.get("blind_rejected_ticker"),
            "safe_D1_features": payload.get("safe_D1_features"),
        },
        output_payload={
            "outcome_preferred_candidate_id": payload.get("outcome_preferred_candidate_id"),
            "outcome_rejected_candidate_id": payload.get("outcome_rejected_candidate_id"),
            "outcome_winner_ticker": payload.get("outcome_winner_ticker"),
            "blind_preference_correct": payload.get("blind_preference_correct"),
            "training_mode": training_mode,
        },
        hindsight_safe=False,
    )


def _record_eval_row(record: BrainRecordEnvelope) -> dict[str, Any]:
    return _training_record_row(
        task="record_eval",
        record=record,
        split="evals",
        input_payload={
            "record_id": record.record_id,
            "record_type": record.record_type,
            "safe_D1_features": record.payload.get("safe_D1_features"),
        },
        output_payload={
            "response_class": record.payload.get("response_class"),
            "outcome": record.payload.get("D_outcome"),
            "label_quality": _nested_get(record.payload, "D_outcome", "label_quality"),
        },
        hindsight_safe=False,
    )


def _payload_subset(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload}


def _record_sft_task(record: BrainRecordEnvelope) -> str:
    if record.record_type == "supervised_issuer_day_case":
        return "record_supervised_issuer_day"
    if record.record_type == "supervised_direct_event_case":
        return "record_supervised_direct_event"
    if record.record_type == "supervised_theme_formation_case":
        return "record_supervised_theme_formation"
    if record.record_type == "theme_formation_case":
        return "record_supervised_theme_formation"
    if record.record_type == "beneficiary_discovery_case":
        return "record_beneficiary_discovery"
    return "record_error_correction"


def _training_record_row(
    *,
    task: str,
    record: BrainRecordEnvelope,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
    split: str,
    hindsight_safe: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "nslab.training_example.v2",
        "example_id": stable_id("TRN", split, task, record.record_id),
        "task": task,
        "training_category": _training_category_for_task(task),
        "split": split,
        "record_id": record.record_id,
        "record_type": record.record_type,
        "episode_id": record.episode_id,
        "trade_date": record.trade_date.isoformat(),
        "available_from": record.available_from.isoformat(),
        "hindsight_safe_for_blind_sft": hindsight_safe,
        "source_phase": "BLIND" if hindsight_safe else "POSTMORTEM",
        "eligibility_basis": {
            "required_fields": ["training_eligible"],
            "satisfied": record.training_eligible,
            "field_values": {"training_eligible": record.training_eligible},
            "reasons": {"training_eligible": record.eligibility_reason or ""},
        },
        "input": input_payload,
        "output": output_payload,
        "provenance": [
            {
                "source_id": source_id,
                "source_type": "brain_record_provenance",
                "uri": f"memory/records/{record.episode_id}.jsonl#{record.record_id}",
                "content_sha256": record.normalized_payload_sha256,
            }
            for source_id in record.provenance_source_ids
        ],
    }


def _sft_rows(
    episode: ResearchEpisode,
    *,
    source_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if episode.eligibility_matrix.forecast_evaluation_eligible:
        rows.extend(
            [
                _training_row(
                    task="blind_reasoning",
                    episode=episode,
                    input_payload={
                        "trade_date": episode.trade_date.isoformat(),
                        "cutoff_at": episode.cutoff_at.isoformat(),
                        "observed_events": [
                            item.model_dump(mode="json") for item in episode.observed_events
                        ],
                        "input_news_files": episode.input_news_files,
                    },
                    output_payload={
                        "summary": episode.blind_analysis.summary,
                        "open_world_mechanisms": episode.blind_analysis.open_world_mechanisms,
                        "initial_uncertainties": episode.blind_analysis.initial_uncertainties,
                        "candidates": [
                            candidate.model_dump(mode="json")
                            for candidate in episode.blind_predictions
                        ],
                    },
                    split="sft",
                    hindsight_safe=True,
                    source_hashes=source_hashes,
                ),
                _training_row(
                    task="theme_formation",
                    episode=episode,
                    input_payload={"blind_summary": episode.blind_analysis.summary},
                    output_payload={
                        "mechanisms": episode.blind_analysis.open_world_mechanisms,
                        "failure_conditions": episode.blind_analysis.initial_uncertainties,
                    },
                    split="sft",
                    hindsight_safe=True,
                    source_hashes=source_hashes,
                ),
                _training_row(
                    task="beneficiary_discovery",
                    episode=episode,
                    input_payload={
                        "blind_summary": episode.blind_analysis.summary,
                        "candidate_count": len(episode.blind_predictions),
                    },
                    output_payload={
                        "candidate_paths": [
                            {
                                "company_name": candidate.company_name,
                                "path_type": str(candidate.path_type),
                                "causal_chain": candidate.causal_chain,
                                "counterarguments": candidate.counterarguments,
                            }
                            for candidate in episode.blind_predictions
                        ],
                    },
                    split="sft",
                    hindsight_safe=True,
                    source_hashes=source_hashes,
                ),
                _training_row(
                    task="leader_selection_comparison",
                    episode=episode,
                    input_payload={
                        "blind_summary": episode.blind_analysis.summary,
                        "candidate_count": len(episode.blind_predictions),
                        "candidates": [
                            {
                                "rank": candidate.rank,
                                "company_name": candidate.company_name,
                                "ticker": candidate.ticker,
                                "path_type": str(candidate.path_type),
                                "why_now": candidate.why_now,
                                "causal_chain": candidate.causal_chain,
                                "counterarguments": candidate.counterarguments,
                                "confidence_label": str(candidate.confidence_label),
                                "evidence_quality": str(candidate.evidence_quality),
                            }
                            for candidate in episode.blind_predictions
                        ],
                    },
                    output_payload={
                        "preferred_order": [
                            {
                                "rank": candidate.rank,
                                "company_name": candidate.company_name,
                                "selection_reason": candidate.why_now,
                                "risk_checks": candidate.disconfirming_conditions,
                            }
                            for candidate in sorted(
                                episode.blind_predictions,
                                key=lambda item: item.rank,
                            )
                        ],
                        "comparison_basis": [
                            "sealed blind rank",
                            "pre-cutoff causal chain",
                            "confidence label",
                            "evidence quality",
                            "counterarguments and disconfirming conditions",
                        ],
                    },
                    split="sft",
                    hindsight_safe=True,
                    source_hashes=source_hashes,
                ),
            ]
        )
    if episode.postmortem is not None and episode.eligibility_matrix.retrospective_memory_eligible:
        rows.append(
            _training_row(
                task="failure_correction",
                episode=episode,
                input_payload={
                    "blind_candidates": [
                        candidate.model_dump(mode="json") for candidate in episode.blind_predictions
                    ],
                    "postmortem_summary": episode.postmortem.summary,
                    "misses": episode.misses,
                },
                output_payload={
                    "failure_codes": episode.postmortem.failure_codes,
                    "lessons": episode.postmortem.lessons,
                    "false_positives": episode.postmortem.false_positives,
                },
                split="sft_postmortem",
                hindsight_safe=False,
                source_hashes=source_hashes,
            )
        )
    return rows


def _preference_rows(
    episode: ResearchEpisode,
    *,
    source_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    if not episode.eligibility_matrix.leader_pair_training_eligible:
        return []
    positives: list[Candidate] = []
    negatives: list[Candidate] = []
    for candidate in episode.blind_predictions:
        outcome = _outcome_for_candidate(episode, candidate)
        if outcome is not None and outcome.upper_limit_touched:
            positives.append(candidate)
        elif outcome is not None:
            negatives.append(candidate)

    rows: list[dict[str, Any]] = []
    for positive in positives:
        for negative in negatives:
            rows.append(
                _training_row(
                    task="positive_vs_negative_candidate_preference",
                    episode=episode,
                    input_payload={
                        "blind_summary": episode.blind_analysis.summary,
                        "chosen_candidate": positive.model_dump(mode="json"),
                        "rejected_candidate": negative.model_dump(mode="json"),
                    },
                    output_payload={
                        "chosen": positive.company_name,
                        "rejected": negative.company_name,
                        "rationale": (
                            "Postmortem outcome labels favored the chosen candidate over the rejected candidate."
                        ),
                    },
                    split="preference",
                    hindsight_safe=False,
                    source_hashes=source_hashes,
                )
            )
    if not rows and episode.postmortem is not None:
        rows.append(
            _training_row(
                task="postmortem_preference_summary",
                episode=episode,
                input_payload={
                    "hits": episode.postmortem.hits,
                    "false_positives": episode.postmortem.false_positives,
                    "misses": episode.postmortem.misses,
                },
                output_payload={"lessons": episode.postmortem.lessons},
                split="preference",
                hindsight_safe=False,
                source_hashes=source_hashes,
            )
        )
    return rows


def _eval_rows(
    episode: ResearchEpisode,
    *,
    source_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if episode.eligibility_matrix.direct_supervised_cases_eligible:
        for candidate in episode.blind_predictions:
            outcome = _outcome_for_candidate(episode, candidate)
            rows.append(
                _training_row(
                    task="candidate_outcome_eval",
                    episode=episode,
                    input_payload={
                        "candidate": candidate.model_dump(mode="json"),
                        "blind_summary": episode.blind_analysis.summary,
                    },
                    output_payload={
                        "outcome": outcome.model_dump(mode="json") if outcome is not None else None,
                        "expected_labels": {
                            "upper_limit_touched": outcome.upper_limit_touched if outcome else None,
                            "upper_limit_closed": outcome.upper_limit_closed if outcome else None,
                        },
                    },
                    split="evals",
                    hindsight_safe=False,
                    source_hashes=source_hashes,
                )
            )
    if episode.postmortem is not None and episode.eligibility_matrix.retrospective_memory_eligible:
        rows.append(
            _training_row(
                task="failure_code_eval",
                episode=episode,
                input_payload={
                    "blind_summary": episode.blind_analysis.summary,
                    "postmortem_summary": episode.postmortem.summary,
                },
                output_payload={
                    "failure_codes": episode.postmortem.failure_codes,
                    "misses": episode.postmortem.misses,
                    "false_positives": episode.postmortem.false_positives,
                },
                split="evals",
                hindsight_safe=False,
                source_hashes=source_hashes,
            )
        )
    return rows


def _training_row(
    *,
    task: str,
    episode: ResearchEpisode,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
    split: str,
    hindsight_safe: bool,
    source_hashes: dict[str, str],
) -> dict[str, Any]:
    eligibility_basis = _eligibility_basis_for_task(task, episode.eligibility_matrix)
    return {
        "schema_version": "nslab.training_example.v1",
        "example_id": stable_id(
            "TRN",
            split,
            task,
            episode.episode_id,
            canonical_json(input_payload),
        ),
        "task": task,
        "training_category": _training_category_for_task(task),
        "split": split,
        "episode_id": episode.episode_id,
        "trade_date": episode.trade_date.isoformat(),
        "available_from": episode.available_from.isoformat(),
        "hindsight_safe_for_blind_sft": hindsight_safe,
        "source_phase": "BLIND" if hindsight_safe else "POSTMORTEM",
        "eligibility_basis": eligibility_basis,
        "input": input_payload,
        "output": output_payload,
        "provenance": _training_row_provenance(episode, source_hashes),
    }


def _training_row_provenance(
    episode: ResearchEpisode,
    source_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    entries = [item.model_dump(mode="json") for item in episode.provenance]
    source_hash = source_hashes.get(episode.episode_id)
    if source_hash is not None:
        entries.append(
            {
                "source_id": f"{episode.episode_id}:accepted_episode",
                "source_type": "accepted_research_episode",
                "uri": f"research/accepted/{episode.episode_id}.json",
                "content_sha256": source_hash,
            }
        )
    return entries


def _outcome_for_candidate(
    episode: ResearchEpisode,
    candidate: Candidate,
) -> OutcomeLabels | None:
    direct_key = f"{candidate.rank}:{candidate.ticker}:{candidate.company_name}"
    if direct_key in episode.outcome_labels:
        return episode.outcome_labels[direct_key]
    for key, outcome in episode.outcome_labels.items():
        if candidate.company_name in key or candidate.ticker in key:
            return outcome
    return None


def _skipped_episodes(
    kind: str,
    episodes: list[ResearchEpisode],
    *,
    row_episode_ids: set[str],
) -> list[dict[str, Any]]:
    skipped: list[dict[str, Any]] = []
    for episode in episodes:
        if episode.episode_id in row_episode_ids:
            continue
        missing = _missing_eligibility_for_kind(kind, episode.eligibility_matrix)
        if not missing:
            missing = ["training_rows"]
        if missing:
            skipped.append(
                {
                    "episode_id": episode.episode_id,
                    "missing_eligibility": missing,
                    "reasons": {
                        key: episode.eligibility_matrix.reasons.get(
                            key,
                            "no rows produced for this training export kind"
                            if key == "training_rows"
                            else "not eligible",
                        )
                        for key in missing
                    },
                }
            )
    return skipped


def _missing_eligibility_for_kind(kind: str, eligibility: EligibilityMatrix) -> list[str]:
    if kind == "sft":
        required = [
            "forecast_evaluation_eligible",
        ]
        if eligibility.retrospective_memory_eligible:
            required.append("retrospective_memory_eligible")
    elif kind == "preference":
        required = ["leader_pair_training_eligible"]
    else:
        required = ["direct_supervised_cases_eligible"]
        if eligibility.retrospective_memory_eligible:
            required.append("retrospective_memory_eligible")
    return [field for field in required if not bool(getattr(eligibility, field))]


def _eligibility_basis_for_task(
    task: str,
    eligibility: EligibilityMatrix,
) -> dict[str, Any]:
    required_fields = _required_eligibility_for_task(task)
    return {
        "required_fields": required_fields,
        "satisfied": all(bool(getattr(eligibility, field)) for field in required_fields),
        "field_values": {
            field: bool(getattr(eligibility, field)) for field in required_fields
        },
        "reasons": {
            field: eligibility.reasons[field]
            for field in required_fields
            if field in eligibility.reasons
        },
    }


def _required_eligibility_for_task(task: str) -> list[str]:
    if task in {
        "blind_reasoning",
        "theme_formation",
        "beneficiary_discovery",
        "leader_selection_comparison",
    }:
        return ["forecast_evaluation_eligible"]
    if task in {
        "positive_vs_negative_candidate_preference",
        "postmortem_preference_summary",
    }:
        return ["leader_pair_training_eligible"]
    if task == "candidate_outcome_eval":
        return ["direct_supervised_cases_eligible"]
    if task in {"failure_correction", "failure_code_eval"}:
        return ["retrospective_memory_eligible"]
    return []


def _training_category_for_task(task: str) -> str:
    return TASK_TRAINING_CATEGORY.get(task, "other")


def _task_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        task = str(row["task"])
        counts[task] = counts.get(task, 0) + 1
    return counts


def _training_categories_for_kind(kind: str, *, source_mode: str) -> list[str]:
    categories = list(KIND_TRAINING_CATEGORIES[kind])
    if kind == "sft" and source_mode == "brain_records":
        for category in RECORD_SFT_TRAINING_CATEGORIES:
            if category not in categories:
                categories.append(category)
    return categories


def _category_counts(
    rows: list[dict[str, Any]],
    *,
    training_categories: list[str],
) -> dict[str, int]:
    counts = dict.fromkeys(training_categories, 0)
    for row in rows:
        category = str(row["training_category"])
        counts[category] = counts.get(category, 0) + 1
    return counts


def _missing_training_categories(
    rows: list[dict[str, Any]],
    *,
    training_categories: list[str],
) -> list[str]:
    counts = _category_counts(rows, training_categories=training_categories)
    return [
        category
        for category in training_categories
        if counts.get(category, 0) == 0
    ]


def _source_phase_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        phase = str(row["source_phase"])
        counts[phase] = counts.get(phase, 0) + 1
    return counts


def _skipped_records(
    kind: str,
    records: list[BrainRecordEnvelope],
) -> list[dict[str, Any]]:
    skipped: list[dict[str, Any]] = []
    for record in records:
        skip_reasons: list[str] = []
        if not record.training_eligible:
            skip_reasons.append(record.eligibility_reason or "training_eligible=false")
        if not _record_selected_for_kind(kind, record):
            skip_reasons.append("record_type_not_selected_for_export_kind")
        if not skip_reasons:
            continue
        skipped.append(
            {
                "record_id": record.record_id,
                "record_type": record.record_type,
                "episode_id": record.episode_id,
                "training_eligible": record.training_eligible,
                "eligibility_reason": record.eligibility_reason,
                "reason": skip_reasons[0],
                "skip_reasons": skip_reasons,
            }
        )
    return skipped


def _record_type_counts(records: list[BrainRecordEnvelope]) -> dict[str, int]:
    return dict(sorted(Counter(record.record_type for record in records).items()))


def _record_training_target_counts(records: list[BrainRecordEnvelope]) -> dict[str, int]:
    return dict(
        sorted(Counter(record.training_target or "UNKNOWN" for record in records).items())
    )


def _record_weight_validation(records: list[BrainRecordEnvelope]) -> dict[str, Any]:
    issuer_keys: set[tuple[str, str]] = set()
    duplicate_issuer_day_keys: list[str] = []
    issuer_weights: dict[str, float] = defaultdict(float)
    direct_weights: dict[str, float] = defaultdict(float)
    for record in records:
        if not record.training_eligible:
            continue
        if record.record_type == "supervised_issuer_day_case":
            key = (
                record.trade_date.isoformat(),
                str(record.payload.get("ticker") or ""),
            )
            if key in issuer_keys and not _uses_fractional_issuer_day_group(record):
                duplicate_issuer_day_keys.append("|".join(key))
            issuer_keys.add(key)
            issuer_weights["|".join(key)] += _numeric_weight(
                record.payload.get("sample_weight", 0.0)
            )
        if record.record_type == "supervised_direct_event_case":
            issuer_day_case_id = record.payload.get("issuer_day_case_id")
            if not isinstance(issuer_day_case_id, str) or not issuer_day_case_id:
                issuer_day_case_id = f"{record.trade_date.isoformat()}:{record.payload.get('ticker') or ''}"
            direct_weights[issuer_day_case_id] += _numeric_weight(
                record.payload.get("sample_weight", 0.0)
            )
    issuer_weight_mismatches = {
        key: round(total, 12)
        for key, total in sorted(issuer_weights.items())
        if abs(total - 1.0) > 0.000001
    }
    direct_weight_mismatches = {
        key: round(total, 12)
        for key, total in sorted(direct_weights.items())
        if abs(total - 1.0) > 0.000001
    }
    return {
        "status": "passed"
        if (
            not duplicate_issuer_day_keys
            and not issuer_weight_mismatches
            and not direct_weight_mismatches
        )
        else "failed",
        "duplicate_issuer_day_count": len(duplicate_issuer_day_keys),
        "duplicate_issuer_day_keys": duplicate_issuer_day_keys,
        "issuer_day_weight_sum_mismatches": issuer_weight_mismatches,
        "direct_event_weight_sum_mismatches": direct_weight_mismatches,
    }


def _numeric_weight(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _uses_fractional_issuer_day_group(record: BrainRecordEnvelope) -> bool:
    return (
        record.payload.get("issuer_day_sample_weight_policy")
        == "fractional_issuer_day_group"
    )


def _nested_get(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
