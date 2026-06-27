"""Brain coverage audit."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from news_scalping_lab.brain.compiler import (
    BRAIN_FILES,
    CATEGORY_RECORD_TYPE_ROUTES,
    LLM_FULL_COMPILER_VERSION,
    _brain_category,
    _records_for_category,
    current_brain_file_hashes,
    current_brain_version,
    expected_brain_version,
)
from news_scalping_lab.contracts.models import MechanismMemory, MemoryClaim, ResearchEpisode
from news_scalping_lab.diagnostic_reports import write_diagnostic_report
from news_scalping_lab.records.models import BrainRecordEnvelope, CompiledBrainClaim
from news_scalping_lab.records.store import BrainRecordStore, audit_record_store
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    file_sha256,
    is_available_as_of,
    parse_datetime,
    read_json,
    sha256_text,
)

LLM_FULL_COMPILE_MANIFEST_SCHEMA_VERSION = "nslab.llm_full_brain_compile_manifest.v1"


def audit_brain(root: Path, *, deep: bool = False) -> dict[str, object]:
    store = ResearchStore(root)
    accepted = store.list_accepted()
    coverage_path = root / "brain" / "current" / "coverage_manifest.json"
    coverage_manifest = read_json(coverage_path) if coverage_path.exists() else {}
    brain_manifest_path = root / "brain" / "current" / "brain_manifest.json"
    brain_manifest = read_json(brain_manifest_path) if brain_manifest_path.exists() else {}
    covered = set(_string_list(coverage_manifest.get("covered_episode_ids", [])))
    accepted_ids = {episode.episode_id for episode in accepted}
    source_hashes = store.accepted_hashes()
    missing = sorted(accepted_ids - covered)
    extra = sorted(covered - accepted_ids)
    claim_audit = _audit_claims(root, accepted)
    mechanism_audit = _audit_mechanisms(root, accepted)
    determinism_audit = _audit_deterministic_brain_state(
        root=root,
        current_manifest=brain_manifest,
        accepted=accepted,
        source_hashes=source_hashes,
    )
    record_audit = _audit_record_coverage(root)
    record_store_audit = audit_record_store(root, deep=deep)
    diversity_audit = _audit_brain_diversity(root)
    llm_compile_audit = _audit_llm_compile_manifest(
        root,
        current_manifest=brain_manifest,
    )
    compiled_claim_audit = _audit_compiled_claims(root, accepted_ids)
    hard_findings = [
        *claim_audit["invalid_claim_lines"],
        *claim_audit["claims_without_support"],
        *claim_audit["claims_with_unknown_support"],
        *claim_audit["claim_temporal_leaks"],
        *claim_audit["claims_without_provenance"],
        *claim_audit["validated_single_support_claims"],
        *mechanism_audit["invalid_mechanism_lines"],
        *mechanism_audit["mechanisms_without_cases"],
        *mechanism_audit["mechanisms_with_unknown_success_cases"],
        *mechanism_audit["mechanisms_without_provenance"],
        *determinism_audit["determinism_findings"],
        *record_audit["record_coverage_findings"],
        *record_store_audit["findings"],
        *diversity_audit["brain_diversity_findings"],
        *llm_compile_audit["llm_compile_findings"],
        *compiled_claim_audit["compiled_claim_findings"],
    ]
    coverage_complete = not missing and not extra and len(covered) == len(accepted)
    result = {
        "deep": deep,
        "accepted_episode_count": len(accepted),
        "brain_covered_episode_count": len(covered),
        "missing_episode_ids": missing,
        "extra_episode_ids": extra,
        **claim_audit,
        **mechanism_audit,
        **determinism_audit,
        **record_audit,
        **diversity_audit,
        **llm_compile_audit,
        **compiled_claim_audit,
        "record_store_audit": record_store_audit,
        "coverage_complete": coverage_complete,
        "passed": coverage_complete and not hard_findings,
        "brain_version": current_brain_version(root),
        "brain_build_mode": _brain_build_mode(brain_manifest or coverage_manifest),
        "catalog_only": _brain_catalog_only(brain_manifest or coverage_manifest),
        "updated_episode_id": (brain_manifest or coverage_manifest).get("updated_episode_id"),
        "last_full_rebuild": (brain_manifest or coverage_manifest).get("last_full_rebuild_at")
        or (brain_manifest or coverage_manifest).get("created_at"),
    }
    _write_latest_brain_audit_summary(root, result, deep=deep)
    _write_latest_record_coverage_audit_summary(root, result)
    return result


def _write_latest_brain_audit_summary(
    root: Path,
    result: dict[str, object],
    *,
    deep: bool,
) -> None:
    report_path = root / "diagnostics" / "brain_compile_report.json"
    report: dict[str, Any] = {}
    if report_path.exists():
        payload = read_json(report_path)
        if isinstance(payload, dict):
            report = payload
    report.setdefault("schema_version", "nslab.brain_compile_diagnostics.v1")
    category_source_record_types = result.get("brain_category_source_record_types")
    if isinstance(category_source_record_types, dict):
        report["category_source_record_type_counts"] = category_source_record_types
        report["category_source_record_counts"] = (
            _category_source_record_counts_from_type_distribution(
                category_source_record_types
            )
        )
    report["latest_brain_audit"] = {
        "deep": deep,
        "passed": result.get("passed"),
        "brain_version": result.get("brain_version"),
        "brain_build_mode": result.get("brain_build_mode"),
        "catalog_only": result.get("catalog_only"),
        "coverage_complete": result.get("coverage_complete"),
        "record_coverage_complete": result.get("record_coverage_complete"),
        "deterministic_rebuild_verified": result.get("deterministic_rebuild_verified"),
        "llm_compile_manifest_present": result.get("llm_compile_manifest_present"),
        "llm_compile_manifest_schema_version": result.get(
            "llm_compile_manifest_schema_version"
        ),
        "llm_compile_expected_manifest_schema_version": result.get(
            "llm_compile_expected_manifest_schema_version"
        ),
        "llm_compile_category_schema_mismatches": result.get(
            "llm_compile_category_schema_mismatches"
        ),
        "compiled_claim_file_present": result.get("compiled_claim_file_present"),
        "brain_category_file_count": result.get("brain_category_file_count"),
        "brain_category_missing_files": result.get("brain_category_missing_files"),
        "brain_category_source_record_types": result.get(
            "brain_category_source_record_types"
        ),
        "brain_category_source_population_mismatches": result.get(
            "brain_category_source_population_mismatches"
        ),
        "brain_empty_category_complete_files": result.get(
            "brain_empty_category_complete_files"
        ),
        "brain_category_files_identical": result.get("brain_category_files_identical"),
        "brain_category_bodies_identical": result.get("brain_category_bodies_identical"),
        "finding_count": len(_brain_audit_findings(result)),
        "findings": _brain_audit_findings(result),
    }
    write_diagnostic_report(root, "brain_compile_report", report)


def _category_source_record_counts_from_type_distribution(
    category_source_record_types: dict[object, object],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for category, type_counts in category_source_record_types.items():
        if not isinstance(category, str) or not isinstance(type_counts, dict):
            continue
        counts[category] = sum(
            count
            for count in type_counts.values()
            if isinstance(count, int) and not isinstance(count, bool)
        )
    return dict(sorted(counts.items()))


def _write_latest_record_coverage_audit_summary(
    root: Path,
    result: dict[str, object],
) -> None:
    report_path = root / "diagnostics" / "record_coverage_report.json"
    report: dict[str, Any] = {}
    if report_path.exists():
        payload = read_json(report_path)
        if isinstance(payload, dict):
            report = payload
    report.setdefault("schema_version", "nslab.record_coverage_manifest.v1")
    for key in (
        "accepted_record_count",
        "available_record_count",
        "available_record_count_as_of",
        "training_eligible_available_record_count",
        "training_eligible_record_count_as_of",
        "compiled_record_count",
        "swept_record_count",
        "swept_record_ids",
        "unswept_record_ids",
        "unknown_swept_record_ids",
        "duplicate_swept_record_ids",
        "record_counts_by_type",
        "record_counts_by_evidence_phase",
        "record_counts_by_training_target",
        "ineligible_record_count",
        "audit_only_record_count",
    ):
        if key in result:
            report[key] = result.get(key)
    findings = result.get("record_coverage_findings")
    record_coverage_complete = result.get("record_coverage_complete")
    report["latest_record_coverage_audit"] = {
        "passed": record_coverage_complete is True,
        "record_coverage_complete": record_coverage_complete,
        "accepted_record_count": result.get("accepted_record_count"),
        "available_record_count": result.get("available_record_count"),
        "available_record_count_as_of": result.get("available_record_count_as_of"),
        "training_eligible_available_record_count": result.get(
            "training_eligible_available_record_count"
        ),
        "training_eligible_record_count_as_of": result.get(
            "training_eligible_record_count_as_of"
        ),
        "compiled_record_count": result.get("compiled_record_count"),
        "swept_record_count": result.get("swept_record_count"),
        "swept_record_ids": result.get("swept_record_ids"),
        "unswept_record_ids": result.get("unswept_record_ids"),
        "unknown_swept_record_ids": result.get("unknown_swept_record_ids"),
        "duplicate_swept_record_ids": result.get("duplicate_swept_record_ids"),
        "record_counts_by_type": result.get("record_counts_by_type"),
        "record_counts_by_evidence_phase": result.get(
            "record_counts_by_evidence_phase"
        ),
        "record_counts_by_training_target": result.get(
            "record_counts_by_training_target"
        ),
        "ineligible_record_count": result.get("ineligible_record_count"),
        "audit_only_record_count": result.get("audit_only_record_count"),
        "finding_count": len(findings) if isinstance(findings, list) else 0,
        "findings": findings if isinstance(findings, list) else [],
    }
    write_diagnostic_report(root, "record_coverage_report", report)


def _brain_audit_findings(result: dict[str, object]) -> list[str]:
    finding_keys = (
        "missing_episode_ids",
        "extra_episode_ids",
        "invalid_claim_lines",
        "claims_without_support",
        "claims_with_unknown_support",
        "claim_temporal_leaks",
        "claims_without_provenance",
        "validated_single_support_claims",
        "invalid_mechanism_lines",
        "mechanisms_without_cases",
        "mechanisms_with_unknown_success_cases",
        "mechanisms_without_provenance",
        "determinism_findings",
        "record_coverage_findings",
        "brain_diversity_findings",
        "llm_compile_findings",
        "compiled_claim_findings",
    )
    findings: list[str] = []
    for key in finding_keys:
        value = result.get(key)
        if isinstance(value, list):
            findings.extend(f"{key}: {item}" for item in value)
    record_store_audit = result.get("record_store_audit")
    if isinstance(record_store_audit, dict):
        record_findings = record_store_audit.get("findings")
        if isinstance(record_findings, list):
            findings.extend(f"record_store: {item}" for item in record_findings)
    return findings


def _audit_brain_diversity(root: Path) -> dict[str, Any]:
    current_dir = root / "brain" / "current"
    findings: list[str] = []
    file_hashes: dict[str, str] = {}
    body_hashes: dict[str, str] = {}
    missing_files: list[str] = []
    for file_name in BRAIN_FILES:
        path = current_dir / file_name
        if not path.exists():
            missing_files.append(file_name)
            continue
        text = path.read_text(encoding="utf-8")
        file_hashes[file_name] = file_sha256(path)
        body_hashes[file_name] = _normalized_brain_body_hash(text)
    for file_name in missing_files:
        findings.append(f"brain category file missing: {file_name}")
    identical_files = _duplicate_hash_groups(file_hashes)
    for group in identical_files:
        findings.append("brain category files are byte-identical: " + ", ".join(group))
    identical_bodies = _duplicate_hash_groups(body_hashes)
    for group in identical_bodies:
        findings.append(
            "brain category files differ only by title or metadata: " + ", ".join(group)
        )
    llm_manifest = _read_llm_compile_manifest(root)
    category_type_distribution = _category_record_type_distribution(root, llm_manifest)
    source_population_mismatches = _category_source_population_mismatches(
        root,
        llm_manifest,
    )
    for category in source_population_mismatches:
        findings.append(f"brain category source population mismatch: {category}")
    empty_category_complete_files = _empty_category_complete_files(
        current_dir,
        llm_manifest,
    )
    for file_name in empty_category_complete_files:
        findings.append(f"brain category with no source records declares complete: {file_name}")
    return {
        "brain_diversity_findings": findings,
        "brain_category_file_count": len(file_hashes),
        "brain_category_missing_files": missing_files,
        "brain_category_source_record_types": category_type_distribution,
        "brain_category_source_population_mismatches": source_population_mismatches,
        "brain_empty_category_complete_files": empty_category_complete_files,
        "brain_category_files_identical": identical_files,
        "brain_category_bodies_identical": identical_bodies,
    }


def _read_llm_compile_manifest(root: Path) -> dict[str, Any]:
    path = root / "brain" / "current" / "llm_compile_manifest.json"
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _category_record_type_distribution(
    root: Path,
    llm_manifest: dict[str, Any],
) -> dict[str, dict[str, int]]:
    records = BrainRecordStore(root).list_records()
    categories = llm_manifest.get("categories")
    if not isinstance(categories, list):
        return _fallback_category_record_type_distribution(records)
    records_by_id = {record.record_id: record for record in records}
    distribution: dict[str, dict[str, int]] = {}
    for category in categories:
        if not isinstance(category, dict):
            continue
        category_name = category.get("category")
        record_ids = category.get("source_record_ids")
        if not isinstance(category_name, str) or not isinstance(record_ids, list):
            continue
        counts: dict[str, int] = {}
        for record_id in record_ids:
            if not isinstance(record_id, str):
                continue
            record = records_by_id.get(record_id)
            record_type = record.record_type if record is not None else "UNKNOWN_RECORD"
            counts[record_type] = counts.get(record_type, 0) + 1
        distribution[category_name] = dict(sorted(counts.items()))
    return distribution


def _fallback_category_record_type_distribution(
    records: list[BrainRecordEnvelope],
) -> dict[str, dict[str, int]]:
    distribution: dict[str, dict[str, int]] = {}
    for file_name in BRAIN_FILES:
        category = _brain_category(file_name)
        if category == "world_model":
            category_records = records
        else:
            allowed = CATEGORY_RECORD_TYPE_ROUTES.get(category, set())
            category_records = [
                record for record in records if record.record_type in allowed
            ]
        distribution[category] = _record_type_counts(category_records)
    return dict(sorted(distribution.items()))


def _record_type_counts(records: list[BrainRecordEnvelope]) -> dict[str, int]:
    return dict(sorted(Counter(record.record_type for record in records).items()))


def _category_source_population_mismatches(
    root: Path,
    llm_manifest: dict[str, Any],
) -> list[str]:
    categories = _dict_list(llm_manifest.get("categories"))
    if not categories:
        return []
    records = BrainRecordStore(root).list_records()
    expected_by_category = {
        _brain_category(file_name): [
            record.record_id
            for record in _records_for_category(records, _brain_category(file_name))
        ]
        for file_name in BRAIN_FILES
    }
    observed_by_category: dict[str, list[str]] = {}
    for category in categories:
        category_name = _string_value(category.get("category"))
        if category_name is None:
            continue
        observed_by_category[category_name] = _string_list(
            category.get("source_record_ids")
        )
    mismatches: list[str] = []
    for category_name, expected_ids in sorted(expected_by_category.items()):
        observed_ids = observed_by_category.get(category_name)
        if observed_ids is None or sorted(observed_ids) != sorted(expected_ids):
            mismatches.append(category_name)
    return mismatches


def _empty_category_complete_files(
    current_dir: Path,
    llm_manifest: dict[str, Any],
) -> list[str]:
    categories = _dict_list(llm_manifest.get("categories"))
    if not categories:
        return []
    empty_files = {
        _string_value(category.get("file_name"))
        for category in categories
        if category.get("source_record_count") == 0
        or not _string_list(category.get("source_record_ids"))
    }
    flagged: list[str] = []
    for file_name in sorted(item for item in empty_files if item is not None):
        path = current_dir / file_name
        if not path.exists():
            continue
        if _declares_category_complete(path.read_text(encoding="utf-8")):
            flagged.append(file_name)
    return flagged


def _declares_category_complete(text: str) -> bool:
    normalized = text.lower()
    complete_markers = (
        "complete",
        "completed",
        "fully covered",
        "coverage complete",
        "완료",
        "완성",
    )
    return any(marker in normalized for marker in complete_markers)


def _audit_llm_compile_manifest(
    root: Path,
    *,
    current_manifest: dict[str, Any],
) -> dict[str, Any]:
    manifest = _read_llm_compile_manifest(root)
    findings: list[str] = []
    unknown_record_ids: list[str] = []
    unknown_compiled_claim_ids: list[str] = []
    category_schema_mismatches: list[str] = []
    category_count_mismatches: list[str] = []
    shard_count_mismatches: list[str] = []
    compiled_claim_count_mismatches: list[str] = []
    shard_record_ids: set[str] = set()
    record_ids = {record.record_id for record in BrainRecordStore(root).list_records()}
    if not manifest:
        if _brain_build_mode(current_manifest) == "llm-full":
            findings.append("llm-full compile manifest is missing")
        return {
            "llm_compile_manifest_present": False,
            "llm_compile_manifest_schema_version": None,
            "llm_compile_expected_manifest_schema_version": (
                LLM_FULL_COMPILE_MANIFEST_SCHEMA_VERSION
            ),
            "llm_compile_compiler_version": None,
            "llm_compile_expected_compiler_version": LLM_FULL_COMPILER_VERSION,
            "llm_compile_findings": findings,
            "llm_compile_unknown_record_ids": unknown_record_ids,
            "llm_compile_unknown_compiled_claim_ids": unknown_compiled_claim_ids,
            "llm_compile_category_schema_mismatches": category_schema_mismatches,
            "llm_compile_category_count_mismatches": category_count_mismatches,
            "llm_compile_shard_count_mismatches": shard_count_mismatches,
            "llm_compile_compiled_claim_count_mismatches": (
                compiled_claim_count_mismatches
            ),
        }
    schema_version = _string_value(manifest.get("schema_version"))
    if schema_version != LLM_FULL_COMPILE_MANIFEST_SCHEMA_VERSION:
        observed_schema = schema_version or "missing"
        findings.append(
            "llm compile manifest schema_version is "
            f"{observed_schema}, not {LLM_FULL_COMPILE_MANIFEST_SCHEMA_VERSION}"
        )
    compiler_version = _string_value(manifest.get("compiler_version"))
    if compiler_version != LLM_FULL_COMPILER_VERSION:
        observed_version = compiler_version or "missing"
        findings.append(
            "llm compile manifest compiler_version is "
            f"{observed_version}, not {LLM_FULL_COMPILER_VERSION}"
        )
    compiled_claim_file_present, compiled_claim_ids = _compiled_claim_ids(root)
    source_count = _int_value(manifest.get("source_record_count"))
    if source_count is None or source_count != len(record_ids):
        findings.append("llm compile manifest source_record_count does not match record store")
    compiled_claim_count = _int_value(manifest.get("compiled_claim_count"))
    if not compiled_claim_file_present:
        findings.append("llm compile manifest exists without compiled claims file")
    if compiled_claim_count is None or compiled_claim_count != len(compiled_claim_ids):
        compiled_claim_count_mismatches.append("manifest")
    for index, shard in enumerate(_dict_list(manifest.get("record_shards")), start=1):
        ids = _string_list(shard.get("record_ids"))
        shard_record_ids.update(ids)
        if shard.get("record_count") != len(ids):
            shard_count_mismatches.append(f"record_shards[{index}]")
        unknown_record_ids.extend(record_id for record_id in ids if record_id not in record_ids)
    if shard_count_mismatches:
        findings.append("llm compile manifest shard record counts do not match record IDs")
    if shard_record_ids != record_ids:
        findings.append("llm compile manifest shard record IDs do not match record store")
    categories = _dict_list(manifest.get("categories"))
    category_schema_mismatches = _llm_compile_category_schema_mismatches(
        manifest,
        categories,
    )
    for category in categories:
        category_name = _string_value(category.get("category")) or "UNKNOWN_CATEGORY"
        ids = _string_list(category.get("source_record_ids"))
        if category.get("source_record_count") != len(ids):
            category_count_mismatches.append(category_name)
        unknown_record_ids.extend(record_id for record_id in ids if record_id not in record_ids)
        claim_ids = _string_list(category.get("compiled_claim_ids"))
        if category.get("compiled_claim_count") != len(claim_ids):
            compiled_claim_count_mismatches.append(category_name)
        unknown_compiled_claim_ids.extend(
            claim_id for claim_id in claim_ids if claim_id not in compiled_claim_ids
        )
    if category_schema_mismatches:
        findings.append("llm compile manifest categories do not match canonical brain files")
    if category_count_mismatches:
        findings.append("llm compile manifest category source counts do not match record IDs")
    if compiled_claim_count_mismatches:
        findings.append("llm compile manifest compiled claim counts do not match claim IDs")
    unknown_record_ids = sorted(set(unknown_record_ids))
    if unknown_record_ids:
        findings.append("llm compile manifest references unknown record IDs")
    unknown_compiled_claim_ids = sorted(set(unknown_compiled_claim_ids))
    if unknown_compiled_claim_ids:
        findings.append("llm compile manifest references unknown compiled claim IDs")
    return {
        "llm_compile_manifest_present": True,
        "llm_compile_manifest_schema_version": schema_version,
        "llm_compile_expected_manifest_schema_version": (
            LLM_FULL_COMPILE_MANIFEST_SCHEMA_VERSION
        ),
        "llm_compile_compiler_version": compiler_version,
        "llm_compile_expected_compiler_version": LLM_FULL_COMPILER_VERSION,
        "llm_compile_findings": findings,
        "llm_compile_unknown_record_ids": unknown_record_ids,
        "llm_compile_unknown_compiled_claim_ids": unknown_compiled_claim_ids,
        "llm_compile_category_schema_mismatches": sorted(category_schema_mismatches),
        "llm_compile_category_count_mismatches": sorted(category_count_mismatches),
        "llm_compile_shard_count_mismatches": sorted(shard_count_mismatches),
        "llm_compile_compiled_claim_count_mismatches": sorted(
            compiled_claim_count_mismatches
        ),
    }


def _llm_compile_category_schema_mismatches(
    manifest: dict[str, Any],
    categories: list[dict[str, Any]],
) -> list[str]:
    expected_by_category = {
        _brain_category(file_name): file_name for file_name in BRAIN_FILES
    }
    mismatches: list[str] = []
    manifest_category_count = _int_value(manifest.get("category_count"))
    if manifest_category_count != len(BRAIN_FILES):
        observed = (
            "missing"
            if manifest_category_count is None
            else str(manifest_category_count)
        )
        mismatches.append(f"category_count: expected {len(BRAIN_FILES)}, got {observed}")
    if len(categories) != len(BRAIN_FILES):
        mismatches.append(f"categories: expected {len(BRAIN_FILES)}, got {len(categories)}")
    observed_counts: Counter[str] = Counter()
    for index, category in enumerate(categories, start=1):
        category_name = _string_value(category.get("category"))
        file_name = _string_value(category.get("file_name"))
        if category_name is None:
            mismatches.append(f"categories[{index}]: missing category")
            continue
        observed_counts[category_name] += 1
        expected_file_name = expected_by_category.get(category_name)
        if expected_file_name is None:
            mismatches.append(f"categories[{index}]: unexpected category {category_name}")
            continue
        if file_name != expected_file_name:
            observed_file = file_name or "missing"
            mismatches.append(
                f"{category_name}: expected file {expected_file_name}, got {observed_file}"
            )
    for category_name, count in sorted(observed_counts.items()):
        if count > 1:
            mismatches.append(f"{category_name}: duplicate category entry count {count}")
    missing_categories = sorted(set(expected_by_category) - set(observed_counts))
    for category_name in missing_categories:
        mismatches.append(f"{category_name}: missing category entry")
    return mismatches


def _compiled_claim_ids(root: Path) -> tuple[bool, set[str]]:
    path = root / "brain" / "current" / "compiled_claims.jsonl"
    if not path.exists():
        return False, set()
    claim_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            claim = CompiledBrainClaim.model_validate(raw)
        except (json.JSONDecodeError, ValidationError):
            continue
        claim_ids.add(claim.claim_id)
    return True, claim_ids


def _audit_compiled_claims(root: Path, accepted_ids: set[str]) -> dict[str, Any]:
    path = root / "brain" / "current" / "compiled_claims.jsonl"
    invalid_lines: list[str] = []
    without_supporting_records: list[str] = []
    unknown_supporting_records: list[str] = []
    unknown_contradicting_records: list[str] = []
    unknown_supporting_episodes: list[str] = []
    unknown_contradicting_episodes: list[str] = []
    compiled_claim_temporal_leaks: list[str] = []
    validated_without_contradictions: list[str] = []
    validated_single_episode: list[str] = []
    findings: list[str] = []
    if not path.exists():
        return {
            "compiled_claim_file_present": False,
            "compiled_claim_findings": findings,
            "invalid_compiled_claim_lines": invalid_lines,
            "compiled_claims_without_supporting_records": without_supporting_records,
            "compiled_claims_with_unknown_supporting_records": unknown_supporting_records,
            "compiled_claims_with_unknown_contradicting_records": unknown_contradicting_records,
            "compiled_claims_with_unknown_supporting_episodes": unknown_supporting_episodes,
            "compiled_claims_with_unknown_contradicting_episodes": unknown_contradicting_episodes,
            "compiled_claim_temporal_leaks": compiled_claim_temporal_leaks,
            "validated_compiled_claims_without_contradictions": (
                validated_without_contradictions
            ),
            "validated_compiled_claims_with_single_episode": validated_single_episode,
        }
    records = BrainRecordStore(root).list_records()
    records_by_id = {record.record_id: record for record in records}
    record_ids = set(records_by_id)
    known_episode_ids = set(accepted_ids)
    known_episode_ids.update(record.episode_id for record in records)
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            claim = CompiledBrainClaim.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            invalid_lines.append(f"compiled_claims.jsonl:{line_number}: {exc}")
            continue
        if not claim.supporting_record_ids:
            without_supporting_records.append(claim.claim_id)
        unknown_supporting = sorted(set(claim.supporting_record_ids) - record_ids)
        if unknown_supporting:
            unknown_supporting_records.append(
                f"{claim.claim_id}: {', '.join(unknown_supporting)}"
            )
        unknown_contradicting = sorted(set(claim.contradicting_record_ids) - record_ids)
        if unknown_contradicting:
            unknown_contradicting_records.append(
                f"{claim.claim_id}: {', '.join(unknown_contradicting)}"
            )
        unknown_support_episodes = sorted(
            set(claim.supporting_episode_ids) - known_episode_ids
        )
        if unknown_support_episodes:
            unknown_supporting_episodes.append(
                f"{claim.claim_id}: {', '.join(unknown_support_episodes)}"
            )
        unknown_contradiction_episodes = sorted(
            set(claim.contradicting_episode_ids) - known_episode_ids
        )
        if unknown_contradiction_episodes:
            unknown_contradicting_episodes.append(
                f"{claim.claim_id}: {', '.join(unknown_contradiction_episodes)}"
            )
        for record_id in claim.supporting_record_ids:
            record = records_by_id.get(record_id)
            if record is not None and not is_available_as_of(
                record.available_from,
                claim.available_from,
            ):
                compiled_claim_temporal_leaks.append(
                    f"{claim.claim_id}: available_from precedes supporting record {record_id}"
                )
        for record_id in claim.contradicting_record_ids:
            record = records_by_id.get(record_id)
            if record is not None and not is_available_as_of(
                record.available_from,
                claim.available_from,
            ):
                compiled_claim_temporal_leaks.append(
                    f"{claim.claim_id}: available_from precedes contradicting record {record_id}"
                )
        if claim.status == "validated":
            if not claim.contradicting_record_ids and not claim.contradicting_episode_ids:
                validated_without_contradictions.append(claim.claim_id)
            if len(set(claim.supporting_episode_ids)) <= 1:
                validated_single_episode.append(claim.claim_id)
    if invalid_lines:
        findings.append("compiled claim lines are invalid")
    if without_supporting_records:
        findings.append("compiled claims are missing supporting_record_ids")
    if unknown_supporting_records:
        findings.append("compiled claims reference unknown supporting record IDs")
    if unknown_contradicting_records:
        findings.append("compiled claims reference unknown contradicting record IDs")
    if unknown_supporting_episodes:
        findings.append("compiled claims reference unknown supporting episode IDs")
    if unknown_contradicting_episodes:
        findings.append("compiled claims reference unknown contradicting episode IDs")
    if compiled_claim_temporal_leaks:
        findings.append("compiled claims expose future record evidence")
    if validated_without_contradictions:
        findings.append("validated compiled claims are missing contradiction evidence")
    if validated_single_episode:
        findings.append("validated compiled claims rely on one or zero supporting episodes")
    return {
        "compiled_claim_file_present": True,
        "compiled_claim_findings": findings,
        "invalid_compiled_claim_lines": invalid_lines,
        "compiled_claims_without_supporting_records": without_supporting_records,
        "compiled_claims_with_unknown_supporting_records": unknown_supporting_records,
        "compiled_claims_with_unknown_contradicting_records": unknown_contradicting_records,
        "compiled_claims_with_unknown_supporting_episodes": unknown_supporting_episodes,
        "compiled_claims_with_unknown_contradicting_episodes": unknown_contradicting_episodes,
        "compiled_claim_temporal_leaks": compiled_claim_temporal_leaks,
        "validated_compiled_claims_without_contradictions": (
            validated_without_contradictions
        ),
        "validated_compiled_claims_with_single_episode": validated_single_episode,
    }


def _duplicate_hash_groups(hashes: dict[str, str]) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for file_name, digest in hashes.items():
        groups.setdefault(digest, []).append(file_name)
    return [sorted(names) for names in groups.values() if len(names) > 1]


def _normalized_brain_body_hash(text: str) -> str:
    ignored_prefixes = (
        "# ",
        "Brain version:",
        "Build mode:",
        "Provider:",
        "Model:",
        "Category:",
        "Source record count:",
        "Accepted episodes covered:",
    )
    body = "\n".join(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith(ignored_prefixes)
    )
    return sha256_text(body)


def _audit_deterministic_brain_state(
    *,
    root: Path,
    current_manifest: dict[str, Any],
    accepted: list[ResearchEpisode],
    source_hashes: dict[str, str],
) -> dict[str, Any]:
    findings: list[str] = []
    head_version = current_brain_version(root)
    manifest_version = _string_value(current_manifest.get("brain_version"))
    manifest_source_hashes = _string_dict(current_manifest.get("source_hashes"))
    source_hashes_verified = manifest_source_hashes == source_hashes
    if not source_hashes_verified:
        findings.append("brain source_hashes do not match accepted episode files")
    shard_episode_count = _current_shard_episode_count(root)
    if shard_episode_count is None:
        findings.append("brain shard_episode_count missing or invalid")
    expected_version = (
        expected_brain_version(
            compiler_mode=_string_value(current_manifest.get("build_mode")) or "catalog",
            covered_episode_ids=[episode.episode_id for episode in accepted],
            source_hashes=source_hashes,
            brain_record_hashes={
                record.record_id: record.normalized_payload_sha256
                for record in BrainRecordStore(root).list_records()
            },
            shard_episode_count=shard_episode_count,
        )
        if shard_episode_count is not None
        else None
    )
    version_matches_expected = (
        manifest_version is not None
        and expected_version is not None
        and manifest_version == expected_version
    )
    if not version_matches_expected:
        findings.append("brain_version does not match deterministic accepted source state")
    head_matches_manifest = head_version is not None and head_version == manifest_version
    if not head_matches_manifest:
        findings.append("brain HEAD does not match current brain_manifest")
    snapshot_matches_current = _snapshot_matches_current(
        root=root,
        brain_version=manifest_version,
    )
    if not snapshot_matches_current:
        findings.append("brain immutable snapshot does not match current brain files")
    return {
        "determinism_findings": findings,
        "deterministic_rebuild_verified": not findings,
        "expected_brain_version": expected_version,
        "manifest_brain_version": manifest_version,
        "head_matches_manifest": head_matches_manifest,
        "source_hashes_verified": source_hashes_verified,
        "shard_episode_count": shard_episode_count,
        "version_matches_expected": version_matches_expected,
        "snapshot_matches_current": snapshot_matches_current,
    }


def _audit_record_coverage(root: Path) -> dict[str, Any]:
    records = BrainRecordStore(root).list_records()
    record_ids = {record.record_id for record in records}
    training_eligible_count = sum(1 for record in records if record.training_eligible)
    record_counts_by_type = dict(
        sorted(Counter(record.record_type for record in records).items())
    )
    record_counts_by_phase = dict(
        sorted(Counter(record.evidence_phase for record in records).items())
    )
    record_counts_by_target = dict(
        sorted(Counter(record.training_target or "UNKNOWN" for record in records).items())
    )
    ineligible_count = sum(1 for record in records if not record.training_eligible)
    audit_only_count = sum(1 for record in records if record.evidence_phase == "AUDIT")
    if not records:
        return {
            "accepted_record_count": 0,
            "available_record_count": 0,
            "available_record_count_as_of": 0,
            "training_eligible_available_record_count": 0,
            "training_eligible_record_count_as_of": 0,
            "compiled_record_count": 0,
            "swept_record_count": 0,
            "swept_record_ids": [],
            "unswept_record_ids": [],
            "unknown_swept_record_ids": [],
            "duplicate_swept_record_ids": [],
            "record_counts_by_type": {},
            "record_counts_by_evidence_phase": {},
            "record_counts_by_training_target": {},
            "ineligible_record_count": 0,
            "audit_only_record_count": 0,
            "record_coverage_complete": True,
            "record_coverage_findings": [],
        }
    manifest_path = root / "brain" / "current" / "record_coverage_manifest.json"
    if not manifest_path.exists():
        return {
            "accepted_record_count": len(records),
            "available_record_count": len(records),
            "available_record_count_as_of": len(records),
            "training_eligible_available_record_count": training_eligible_count,
            "training_eligible_record_count_as_of": training_eligible_count,
            "compiled_record_count": 0,
            "swept_record_count": 0,
            "swept_record_ids": [],
            "unswept_record_ids": [record.record_id for record in records],
            "unknown_swept_record_ids": [],
            "duplicate_swept_record_ids": [],
            "record_counts_by_type": record_counts_by_type,
            "record_counts_by_evidence_phase": record_counts_by_phase,
            "record_counts_by_training_target": record_counts_by_target,
            "ineligible_record_count": ineligible_count,
            "audit_only_record_count": audit_only_count,
            "record_coverage_complete": False,
            "record_coverage_findings": ["record coverage manifest is missing"],
        }
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        return {
            "accepted_record_count": len(records),
            "available_record_count": len(records),
            "available_record_count_as_of": len(records),
            "training_eligible_available_record_count": training_eligible_count,
            "training_eligible_record_count_as_of": training_eligible_count,
            "compiled_record_count": 0,
            "swept_record_count": 0,
            "swept_record_ids": [],
            "unswept_record_ids": [record.record_id for record in records],
            "unknown_swept_record_ids": [],
            "duplicate_swept_record_ids": [],
            "record_counts_by_type": record_counts_by_type,
            "record_counts_by_evidence_phase": record_counts_by_phase,
            "record_counts_by_training_target": record_counts_by_target,
            "ineligible_record_count": ineligible_count,
            "audit_only_record_count": audit_only_count,
            "record_coverage_complete": False,
            "record_coverage_findings": [
                "record coverage manifest must be a JSON object"
            ],
        }
    findings: list[str] = []
    coverage_as_of = _record_coverage_as_of(manifest, findings)
    available_records_as_of = (
        [
            record
            for record in records
            if is_available_as_of(record.available_from, coverage_as_of)
        ]
        if coverage_as_of is not None
        else records
    )
    available_count_as_of = len(available_records_as_of)
    training_eligible_count_as_of = sum(
        1 for record in available_records_as_of if record.training_eligible
    )
    raw_swept_record_ids = manifest.get("swept_record_ids")
    raw_unswept_record_ids = manifest.get("unswept_record_ids")
    swept_id_list = _string_list(raw_swept_record_ids)
    swept_ids = set(swept_id_list)
    unswept = sorted(record_ids - swept_ids)
    unexpected = sorted(swept_ids - record_ids)
    duplicate_swept = sorted(
        record_id for record_id, count in Counter(swept_id_list).items() if count > 1
    )
    if manifest.get("schema_version") != "nslab.record_coverage_manifest.v1":
        findings.append("record coverage manifest schema_version is invalid")
    if not _string_list_field_valid(raw_swept_record_ids):
        findings.append("record coverage manifest swept_record_ids is invalid")
    if not _string_list_field_valid(raw_unswept_record_ids):
        findings.append("record coverage manifest unswept_record_ids is invalid")
    if unswept:
        findings.append("record coverage manifest has unswept records")
    if unexpected:
        findings.append("record coverage manifest includes unknown swept records")
    if duplicate_swept:
        findings.append("record coverage manifest has duplicate swept records")
    if _string_list(raw_unswept_record_ids) != unswept:
        findings.append("record coverage manifest unswept ids do not match record store")
    if manifest.get("accepted_record_count") != len(records):
        findings.append("record coverage manifest count does not match record store")
    if manifest.get("available_record_count") != len(records):
        findings.append(
            "record coverage manifest available count does not match record store"
        )
    if (
        manifest.get("available_record_count_as_of") is not None
        and manifest.get("available_record_count_as_of") != available_count_as_of
    ):
        findings.append(
            "record coverage manifest as-of available count does not match record store"
        )
    if manifest.get("training_eligible_available_record_count") != training_eligible_count:
        findings.append(
            "record coverage manifest training eligible count does not match record store"
        )
    if (
        manifest.get("training_eligible_record_count_as_of") is not None
        and manifest.get("training_eligible_record_count_as_of")
        != training_eligible_count_as_of
    ):
        findings.append(
            "record coverage manifest as-of training eligible count does not match record store"
        )
    if manifest.get("compiled_record_count") != len(records):
        findings.append(
            "record coverage manifest compiled count does not match record store"
        )
    if manifest.get("swept_record_count") != len(swept_id_list):
        findings.append("record coverage manifest swept count does not match swept IDs")
    if _int_dict(manifest.get("record_counts_by_type")) != record_counts_by_type:
        findings.append("record coverage manifest type counts do not match record store")
    if _int_dict(manifest.get("record_counts_by_evidence_phase")) != record_counts_by_phase:
        findings.append("record coverage manifest phase counts do not match record store")
    if (
        _int_dict(manifest.get("record_counts_by_training_target"))
        != record_counts_by_target
    ):
        findings.append(
            "record coverage manifest training target counts do not match record store"
        )
    if manifest.get("ineligible_record_count") != ineligible_count:
        findings.append(
            "record coverage manifest ineligible count does not match record store"
        )
    if manifest.get("audit_only_record_count") != audit_only_count:
        findings.append(
            "record coverage manifest audit-only count does not match record store"
        )
    has_audit_findings = bool(findings)
    manifest_complete = manifest.get("coverage_complete")
    if manifest_complete is not True:
        findings.append("record coverage manifest is not marked complete")
    elif has_audit_findings:
        findings.append("record coverage manifest is marked complete despite audit findings")
    return {
        "accepted_record_count": len(records),
        "available_record_count": len(records),
        "available_record_count_as_of": available_count_as_of,
        "training_eligible_available_record_count": training_eligible_count,
        "training_eligible_record_count_as_of": training_eligible_count_as_of,
        "compiled_record_count": len(records),
        "swept_record_count": len(swept_ids & record_ids),
        "swept_record_ids": sorted(swept_ids & record_ids),
        "unswept_record_ids": unswept,
        "unknown_swept_record_ids": unexpected,
        "duplicate_swept_record_ids": duplicate_swept,
        "record_counts_by_type": record_counts_by_type,
        "record_counts_by_evidence_phase": record_counts_by_phase,
        "record_counts_by_training_target": record_counts_by_target,
        "ineligible_record_count": ineligible_count,
        "audit_only_record_count": audit_only_count,
        "record_coverage_complete": not findings,
        "record_coverage_findings": findings,
    }


def _record_coverage_as_of(
    manifest: dict[str, Any],
    findings: list[str],
) -> datetime | None:
    raw = manifest.get("record_coverage_as_of")
    if not isinstance(raw, str) or not raw:
        findings.append("record coverage manifest record_coverage_as_of missing or invalid")
        return None
    try:
        return parse_datetime(raw)
    except ValueError:
        findings.append("record coverage manifest record_coverage_as_of missing or invalid")
        return None


def _audit_mechanisms(root: Path, accepted: list[ResearchEpisode]) -> dict[str, list[str]]:
    accepted_ids = {episode.episode_id for episode in accepted}
    mechanisms_path = root / "memory" / "mechanisms" / "current" / "mechanisms.jsonl"
    invalid_mechanism_lines: list[str] = []
    mechanisms_without_cases: list[str] = []
    mechanisms_with_unknown_success_cases: list[str] = []
    mechanisms_without_provenance: list[str] = []
    if not mechanisms_path.exists():
        return {
            "invalid_mechanism_lines": invalid_mechanism_lines,
            "mechanisms_without_cases": mechanisms_without_cases,
            "mechanisms_with_unknown_success_cases": mechanisms_with_unknown_success_cases,
            "mechanisms_without_provenance": mechanisms_without_provenance,
        }
    for line_number, line in enumerate(
        mechanisms_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            mechanism = MechanismMemory.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            invalid_mechanism_lines.append(f"mechanisms.jsonl:{line_number}: {exc}")
            continue
        if not mechanism.successful_cases and not mechanism.failed_cases:
            mechanisms_without_cases.append(mechanism.mechanism_id)
        unknown_success_cases = [
            episode_id
            for episode_id in mechanism.successful_cases
            if episode_id not in accepted_ids
        ]
        if unknown_success_cases:
            mechanisms_with_unknown_success_cases.append(
                f"{mechanism.mechanism_id}: {', '.join(sorted(unknown_success_cases))}"
            )
        if not mechanism.provenance:
            mechanisms_without_provenance.append(mechanism.mechanism_id)
    return {
        "invalid_mechanism_lines": invalid_mechanism_lines,
        "mechanisms_without_cases": mechanisms_without_cases,
        "mechanisms_with_unknown_success_cases": mechanisms_with_unknown_success_cases,
        "mechanisms_without_provenance": mechanisms_without_provenance,
    }


def _audit_claims(root: Path, accepted: list[ResearchEpisode]) -> dict[str, list[str]]:
    accepted_by_id = {episode.episode_id: episode for episode in accepted}
    claims_path = root / "brain" / "current" / "claims.jsonl"
    invalid_claim_lines: list[str] = []
    claims_without_support: list[str] = []
    claims_with_unknown_support: list[str] = []
    claims_without_provenance: list[str] = []
    claim_temporal_leaks: list[str] = []
    single_support_claims_without_contradictions: list[str] = []
    validated_single_support_claims: list[str] = []
    if not claims_path.exists():
        return {
            "invalid_claim_lines": invalid_claim_lines,
            "claims_without_support": claims_without_support,
            "claims_with_unknown_support": claims_with_unknown_support,
            "claims_without_provenance": claims_without_provenance,
            "claim_temporal_leaks": claim_temporal_leaks,
            "validated_single_support_claims": validated_single_support_claims,
            "single_support_claims_without_contradictions": (
                single_support_claims_without_contradictions
            ),
        }
    for line_number, line in enumerate(claims_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            claim = MemoryClaim.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            invalid_claim_lines.append(f"claims.jsonl:{line_number}: {exc}")
            continue
        if not claim.support_episode_ids:
            claims_without_support.append(claim.claim_id)
        unknown_support = [
            episode_id
            for episode_id in claim.support_episode_ids
            if episode_id not in accepted_by_id
        ]
        if unknown_support:
            claims_with_unknown_support.append(
                f"{claim.claim_id}: {', '.join(sorted(unknown_support))}"
            )
        if not claim.provenance:
            claims_without_provenance.append(claim.claim_id)
        for episode_id in claim.support_episode_ids:
            episode = accepted_by_id.get(episode_id)
            if episode is None:
                continue
            if not is_available_as_of(episode.available_from, claim.available_from):
                claim_temporal_leaks.append(
                    f"{claim.claim_id}: available_from precedes support {episode_id}"
                )
        if (
            len(claim.support_episode_ids) == 1
            and claim.support_episode_ids[0] in accepted_by_id
            and not claim.contradiction_episode_ids
            and not claim.near_miss_episode_ids
        ):
            single_support_claims_without_contradictions.append(claim.claim_id)
            if claim.status == "validated":
                validated_single_support_claims.append(claim.claim_id)
    return {
        "invalid_claim_lines": invalid_claim_lines,
        "claims_without_support": claims_without_support,
        "claims_with_unknown_support": claims_with_unknown_support,
        "claims_without_provenance": claims_without_provenance,
        "claim_temporal_leaks": claim_temporal_leaks,
        "validated_single_support_claims": validated_single_support_claims,
        "single_support_claims_without_contradictions": (
            single_support_claims_without_contradictions
        ),
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_list_field_valid(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_value(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, int) and not isinstance(item, bool)
    }


def _brain_build_mode(manifest: dict[str, Any]) -> str:
    value = manifest.get("build_mode")
    if isinstance(value, str) and value:
        return value
    if manifest.get("created_at") is not None:
        return "full"
    return "unknown"


def _brain_catalog_only(manifest: dict[str, Any]) -> bool | None:
    value = manifest.get("catalog_only")
    if isinstance(value, bool):
        return value
    build_mode = _brain_build_mode(manifest)
    if build_mode in {"full", "catalog", "incremental"}:
        return True
    if build_mode in {"llm-full", "asof_context"}:
        return False
    return None


def _current_shard_episode_count(root: Path) -> int | None:
    manifest_path = root / "memory" / "shard_brains" / "current" / "manifest.json"
    if not manifest_path.exists():
        return None
    payload = read_json(manifest_path)
    value = payload.get("shard_episode_count") if isinstance(payload, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _snapshot_matches_current(*, root: Path, brain_version: str | None) -> bool:
    if brain_version is None:
        return False
    snapshot_dir = root / "brain" / "snapshots" / brain_version
    if not snapshot_dir.exists():
        return False
    return current_brain_file_hashes(root) == {
        f"brain/current/{path.name}": file_sha256(path)
        for path in sorted(snapshot_dir.glob("*"))
        if path.is_file()
    }
