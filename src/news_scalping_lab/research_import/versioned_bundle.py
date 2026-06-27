"""Version-aware research bundle import.

This importer preserves newer bundle records without forcing them into the
legacy ``ResearchEpisode`` schema.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from news_scalping_lab.diagnostic_reports import write_diagnostic_report
from news_scalping_lab.records.models import (
    KNOWN_RECORD_PAYLOAD_MODELS,
    VALID_OUTCOME_LABEL_QUALITIES,
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.records.reference_integrity import (
    known_reference_ids_from_blocks,
    payload_reference_audit,
)
from news_scalping_lab.records.store import (
    BrainRecordStore,
    BrainRecordStoreConflictError,
    StoredBundleResult,
)
from news_scalping_lab.utils import (
    KST,
    as_kst,
    canonical_json,
    file_sha256,
    parse_datetime,
    sha256_text,
    stable_id,
)

FORBIDDEN_EVENT_TICKER_EDGE_ORIGINS = {
    "AFTER_CUTOFF_SOURCE",
    "OUTCOME_ONLY_ASSOCIATION",
}


class VersionedBundleImportError(ValueError):
    """Raised when a versioned bundle cannot be normalized safely."""


@dataclass(frozen=True)
class GenericParsedBundle:
    path: Path
    text: str
    front_matter: dict[str, str]
    blocks: dict[str, str]
    payload_blocks: dict[str, str]
    json_blocks: dict[str, Any]
    jsonl_blocks: dict[str, list[dict[str, Any]]]


@dataclass(frozen=True)
class BundleImportResult:
    status: str
    adapter_name: str
    episode_id: str
    bundle_schema_version: str
    accepted: bool
    record_count: int
    training_eligible_record_count: int
    envelope_path: Path | None
    record_path: Path | None
    manifest_path: Path | None
    validation: dict[str, Any]


@dataclass(frozen=True)
class BlockHashValidation:
    mismatches: dict[str, dict[str, Any]]
    self_referential: dict[str, dict[str, str]]
    expectation_sources: dict[str, list[dict[str, str]]]
    expectation_conflicts: dict[str, list[dict[str, str]]]


class BundleVersionAdapter(Protocol):
    name: str

    def supports(self, parsed: GenericParsedBundle) -> bool: ...

    def validate(self, parsed: GenericParsedBundle) -> dict[str, Any]: ...

    def normalize_episode_index(self, parsed: GenericParsedBundle) -> NormalizedEpisodeIndex: ...

    def normalize_brain_records(self, parsed: GenericParsedBundle) -> list[BrainRecordEnvelope]: ...

    def envelope(self, parsed: GenericParsedBundle) -> ResearchBundleEnvelope: ...


class BaseBundleAdapter:
    name = "base"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        return False

    def validation_payloads(self, parsed: GenericParsedBundle) -> list[dict[str, Any]]:
        return parsed.jsonl_blocks.get("brain_delta.jsonl", [])

    def validate(self, parsed: GenericParsedBundle) -> dict[str, Any]:
        manifest = _manifest(parsed)
        validation_report = _validation_report(parsed)
        block_hashes = _block_hashes(parsed)
        hash_validation = _hash_validation(parsed, manifest, validation_report)
        hash_mismatches = hash_validation.mismatches
        records = self.validation_payloads(parsed)
        source_ids = _source_ids(parsed)
        missing_source_refs = _missing_source_references(records, source_ids)
        payload_reference_result = payload_reference_audit(
            [
                (_record_identity(record, line_number), record)
                for line_number, record in enumerate(records, start=1)
            ],
            known_reference_ids_from_blocks(parsed.json_blocks, parsed.jsonl_blocks),
        )
        missing_payload_refs = payload_reference_result["missing_references"]
        invalid_available_from_fields = _invalid_bundle_available_from_fields(parsed)
        invalid_available_from_record_ids = _invalid_record_available_from_ids(records)
        invalid_label_quality_record_ids = _invalid_outcome_label_quality_record_ids(
            records
        )
        invalid_typed_payload_record_ids = _invalid_typed_payload_record_ids(records)
        invalid_edge_cutoff_record_ids = _invalid_event_ticker_edge_cutoff_record_ids(
            records
        )
        invalid_edge_source_ledger_record_ids = (
            _invalid_event_ticker_edge_source_ledger_cutoff_record_ids(
                records,
                parsed.jsonl_blocks.get("source_ledger.jsonl", []),
            )
        )
        invalid_company_memory_known_at_ids = (
            _invalid_company_memory_delta_known_at_record_ids(records)
        )
        backdated_company_memory_known_at_ids = (
            _backdated_company_memory_delta_known_at_record_ids(records)
        )
        invalid_issuer_day_event_weight_ids = (
            _invalid_issuer_day_event_level_weight_record_ids(records)
        )
        sample_weight_validation = _record_sample_weight_validation(records)
        expected_record_count = _int_field(
            manifest,
            "brain_delta_record_count",
            _nested_int(validation_report, "computed_counts", "brain_delta_record_count"),
        )
        expected_training_count = _int_field(
            manifest,
            "training_eligible_record_count",
            _nested_int(
                validation_report,
                "computed_counts",
                "training_eligible_record_count",
            ),
        )
        actual_training_count = sum(
            1 for record in records if record.get("training_eligible") is True
        )
        normalized_records = self.normalize_brain_records(parsed)
        import_loss_summary = _import_loss_summary(parsed, normalized_records)
        import_loss_audit_passed = _import_loss_audit_passed(import_loss_summary)
        validation = {
            "schema_version": "nslab.versioned_bundle_validation.v1",
            "adapter": self.name,
            "bundle_schema_version": bundle_schema_version(parsed),
            "manifest_schema_version": _optional_string(manifest.get("schema_version")),
            "record_count": len(records),
            "training_eligible_record_count": actual_training_count,
            "expected_record_count": expected_record_count,
            "expected_training_eligible_record_count": expected_training_count,
            "record_count_matches_manifest": (
                expected_record_count is None or expected_record_count == len(records)
            ),
            "training_eligible_count_matches_manifest": (
                expected_training_count is None
                or expected_training_count == actual_training_count
            ),
            "block_hashes": block_hashes,
            "hash_mismatches": hash_mismatches,
            "self_referential_hashes": hash_validation.self_referential,
            "hash_expectation_sources": hash_validation.expectation_sources,
            "hash_expectation_conflicts": hash_validation.expectation_conflicts,
            "source_reference_count": sum(
                len(_string_list(record.get("provenance_source_ids")))
                for record in records
            ),
            "missing_source_references": missing_source_refs,
            "provenance_closure_status": "closed" if not missing_source_refs else "missing_refs",
            "payload_reference_count": payload_reference_result["reference_count"],
            "missing_payload_references": missing_payload_refs,
            "payload_reference_closure_status": (
                "closed" if not missing_payload_refs else "missing_refs"
            ),
            "available_from_valid": (
                not invalid_available_from_fields
                and not invalid_available_from_record_ids
            ),
            "invalid_available_from_fields": invalid_available_from_fields,
            "invalid_available_from_record_ids": invalid_available_from_record_ids,
            "outcome_label_quality_valid": not invalid_label_quality_record_ids,
            "invalid_outcome_label_quality_record_ids": invalid_label_quality_record_ids,
            "typed_payload_valid": not invalid_typed_payload_record_ids,
            "invalid_typed_payload_record_ids": invalid_typed_payload_record_ids,
            "event_ticker_edge_cutoff_provenance_valid": (
                not invalid_edge_cutoff_record_ids
            ),
            "invalid_event_ticker_edge_cutoff_provenance_record_ids": (
                invalid_edge_cutoff_record_ids
            ),
            "event_ticker_edge_source_ledger_cutoff_valid": (
                not invalid_edge_source_ledger_record_ids
            ),
            "invalid_event_ticker_edge_source_ledger_cutoff_record_ids": (
                invalid_edge_source_ledger_record_ids
            ),
            "company_memory_delta_known_at_valid": (
                not invalid_company_memory_known_at_ids
            ),
            "invalid_company_memory_delta_known_at_record_ids": (
                invalid_company_memory_known_at_ids
            ),
            "company_memory_delta_known_at_not_backdated": (
                not backdated_company_memory_known_at_ids
            ),
            "backdated_company_memory_delta_known_at_record_ids": (
                backdated_company_memory_known_at_ids
            ),
            "issuer_day_event_level_weights_valid": (
                not invalid_issuer_day_event_weight_ids
            ),
            "invalid_issuer_day_event_level_weight_record_ids": (
                invalid_issuer_day_event_weight_ids
            ),
            "sample_weight_validation": sample_weight_validation,
            "sample_weight_validation_status": sample_weight_validation["status"],
            "import_loss_audit_passed": import_loss_audit_passed,
            **import_loss_summary,
            "validator_exit_code": _int_field(manifest, "validator_exit_code"),
            "critical_error_count": _int_field(
                manifest,
                "critical_error_count",
                _nested_int(validation_report, "critical_error_count"),
            ),
        }
        validation["passed"] = (
            validation["record_count_matches_manifest"] is True
            and validation["training_eligible_count_matches_manifest"] is True
            and not hash_mismatches
            and not hash_validation.expectation_conflicts
            and not missing_source_refs
            and not missing_payload_refs
            and validation["available_from_valid"] is True
            and validation["outcome_label_quality_valid"] is True
            and validation["typed_payload_valid"] is True
            and validation["event_ticker_edge_cutoff_provenance_valid"] is True
            and validation["event_ticker_edge_source_ledger_cutoff_valid"] is True
            and validation["company_memory_delta_known_at_valid"] is True
            and validation["company_memory_delta_known_at_not_backdated"] is True
            and validation["issuer_day_event_level_weights_valid"] is True
            and validation["sample_weight_validation_status"] == "passed"
            and validation["import_loss_audit_passed"] is True
        )
        return validation

    def normalize_episode_index(self, parsed: GenericParsedBundle) -> NormalizedEpisodeIndex:
        manifest = _manifest(parsed)
        episode = _episode(parsed)
        records = self.normalize_brain_records(parsed)
        source_ids = sorted(_source_ids(parsed))
        available_from = _available_from(parsed, records)
        return NormalizedEpisodeIndex(
            episode_id=_episode_id(parsed),
            trade_date=_trade_date(parsed),
            previous_trade_date=_optional_date(_field(parsed, "previous_trade_date")),
            next_trade_date=_optional_date(_field(parsed, "next_trade_date")),
            window_start=_optional_datetime(_field(parsed, "window_start")),
            cutoff_at=_optional_datetime(_field(parsed, "cutoff_at")),
            available_from=available_from,
            bundle_status=_optional_string(manifest.get("bundle_status") or _field(parsed, "bundle_status")),
            blind_valid=_optional_bool(manifest.get("blind_valid") or _field(parsed, "blind_valid")),
            research_daily_source=_optional_string(manifest.get("research_daily_source")),
            entity_quality_summary=_dict_field(episode, "entity_quality_summary"),
            fact_quality_summary=_dict_field(episode, "fact_quality_summary"),
            candidate_screening_summary=_dict_field(episode, "candidate_screening_summary"),
            entity_resolution_summary=_dict_field(episode, "entity_resolution_summary"),
            winner_census=_dict_field(episode, "winner_census"),
            raw_block_names=sorted(parsed.blocks),
            record_ids=[record.record_id for record in records],
            record_count_by_type=dict(Counter(record.record_type for record in records)),
            training_eligible_record_count=sum(
                1 for record in records if record.training_eligible
            ),
            source_ids=source_ids,
        )

    def normalize_brain_records(self, parsed: GenericParsedBundle) -> list[BrainRecordEnvelope]:
        episode_id = _episode_id(parsed)
        trade_day = _trade_date(parsed)
        default_available_from = _default_available_from(parsed)
        records: list[BrainRecordEnvelope] = []
        for line_number, payload in enumerate(
            parsed.jsonl_blocks.get("brain_delta.jsonl", []),
            start=1,
        ):
            records.append(
                _record_envelope(
                    payload=payload,
                    episode_id=episode_id,
                    trade_date=trade_day,
                    default_available_from=default_available_from,
                    source_line=line_number,
                )
            )
        return records

    def envelope(self, parsed: GenericParsedBundle) -> ResearchBundleEnvelope:
        manifest = _manifest(parsed)
        records = self.normalize_brain_records(parsed)
        validation = self.validate(parsed)
        return ResearchBundleEnvelope(
            bundle_schema_version=bundle_schema_version(parsed),
            manifest_schema_version=_optional_string(manifest.get("schema_version")),
            episode_schema_version=_optional_string(_episode(parsed).get("schema_version")),
            episode_id=_episode_id(parsed),
            trade_date=_trade_date(parsed),
            cutoff_at=_optional_datetime(_field(parsed, "cutoff_at")),
            available_from=_available_from(parsed, records),
            bundle_status=_optional_string(manifest.get("bundle_status") or _field(parsed, "bundle_status")),
            blind_valid=_optional_bool(manifest.get("blind_valid") or _field(parsed, "blind_valid")),
            raw_bundle_sha256=file_sha256(parsed.path),
            raw_block_hashes=_block_hashes(parsed),
            raw_block_counts=_block_counts(parsed),
            provenance_closure_status=str(validation["provenance_closure_status"]),
            adapter_name=self.name,
        )


class LegacyV1Adapter(BaseBundleAdapter):
    name = "legacy-v1"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        return (
            bundle_schema_version(parsed) in {"nslab.bundle_manifest.v1", "nslab.research_bundle.v1"}
            or _optional_string(_manifest(parsed).get("schema_version"))
            == "nslab.bundle_manifest.v1"
            or _optional_string(_episode(parsed).get("schema_version"))
            == "nslab.research_episode.v1"
        )


class V10Adapter(BaseBundleAdapter):
    name = "v10"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        version = bundle_schema_version(parsed)
        if _declares_research_bundle_version(version) and not version.endswith(".v10"):
            return False
        manifest_version = _optional_string(_manifest(parsed).get("schema_version"))
        episode_version = _optional_string(_episode(parsed).get("schema_version"))
        if _is_future_bundle_manifest_version(manifest_version, supported_major=10):
            return False
        versions = {
            version.split(".")[-1],
            (manifest_version or "").split(".")[-1],
            (episode_version or "").split(".")[-1],
        }
        return "v10" in versions


class V11Adapter(BaseBundleAdapter):
    name = "v11"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        version = bundle_schema_version(parsed)
        if _declares_research_bundle_version(version) and not version.endswith(".v11"):
            return False
        manifest_version = _optional_string(_manifest(parsed).get("schema_version"))
        episode_version = _optional_string(_episode(parsed).get("schema_version"))
        if _is_future_bundle_manifest_version(manifest_version, supported_major=11):
            return False
        versions = {
            version.split(".")[-1],
            (manifest_version or "").split(".")[-1],
            (episode_version or "").split(".")[-1],
        }
        return "v11" in versions

    def validate(self, parsed: GenericParsedBundle) -> dict[str, Any]:
        validation = super().validate(parsed)
        manifest = _manifest(parsed)
        validation_report = _validation_report(parsed)
        validation.update(
            {
                "bundle_status_accept_full": manifest.get("bundle_status") == "ACCEPT_FULL",
                "blind_valid": manifest.get("blind_valid") is True,
                "validator_exit_code_zero": _all_declared_int_values_zero(
                    _int_field(manifest, "validator_exit_code"),
                    _nested_int(validation_report, "validator_exit_code"),
                ),
                "critical_error_count_zero": _all_declared_int_values_zero(
                    _int_field(manifest, "critical_error_count"),
                    _nested_int(validation_report, "critical_error_count"),
                ),
            }
        )
        validation["passed"] = (
            validation["passed"] is True
            and validation["bundle_status_accept_full"] is True
            and validation["blind_valid"] is True
            and validation["validator_exit_code_zero"] is True
            and validation["critical_error_count_zero"] is True
        )
        return validation


class V23DirectIngestAdapter(BaseBundleAdapter):
    name = "v23-direct-ingest"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        manifest_version = _optional_string(_manifest(parsed).get("schema_version"))
        return (
            bundle_schema_version(parsed) == "nslab.research_bundle.v11"
            and manifest_version == "nslab.bundle_manifest.v23"
            and _v23_direct_ingest_contract_checks(parsed)["supported"] is True
        )

    def validation_payloads(self, parsed: GenericParsedBundle) -> list[dict[str, Any]]:
        return _v23_normalized_payloads(parsed)

    def validate(self, parsed: GenericParsedBundle) -> dict[str, Any]:
        validation = super().validate(parsed)
        checks = _v23_direct_ingest_contract_checks(parsed)
        manifest = _manifest(parsed)
        validation_report = _validation_report(parsed)
        direct_ingest_contract = _direct_ingest_contract(parsed) or {}
        validation.update(
            {
                "direct_ingest_contract_present": checks["contract_present"],
                "direct_ingest_contract_supported": checks["supported"],
                "direct_brain_ingest_ready": checks["direct_brain_ingest_ready"],
                "automated_import_expected_to_pass": checks[
                    "automated_import_expected_to_pass"
                ],
                "requires_human_semantic_review": checks[
                    "requires_human_semantic_review"
                ],
                "direct_ingest_fatal_blocker_count": checks["fatal_blocker_count"],
                "direct_ingest_record_count_hash_parity_ready": checks[
                    "record_count_hash_parity_ready"
                ],
                "direct_ingest_schema_contract_verified": checks[
                    "schema_contract_verified"
                ],
                "bundle_status_accept_full": manifest.get("bundle_status")
                == "ACCEPT_FULL",
                "brain_eligible": _optional_bool(manifest.get("brain_eligible")) is True,
                "validator_exit_code_zero": _all_declared_int_values_zero(
                    _int_field(manifest, "validator_exit_code"),
                    _nested_int(validation_report, "validator_exit_code"),
                    _nested_int(
                        direct_ingest_contract,
                        "hard_gate_summary",
                        "validator_exit_code",
                    ),
                ),
                "critical_error_count_zero": _all_declared_int_values_zero(
                    _int_field(manifest, "critical_error_count"),
                    _nested_int(validation_report, "critical_error_count"),
                    _nested_int(
                        direct_ingest_contract,
                        "hard_gate_summary",
                        "critical_error_count",
                    ),
                ),
            }
        )
        validation["passed"] = (
            validation["passed"] is True
            and validation["direct_ingest_contract_supported"] is True
            and validation["bundle_status_accept_full"] is True
            and validation["brain_eligible"] is True
            and validation["validator_exit_code_zero"] is True
            and validation["critical_error_count_zero"] is True
        )
        return validation

    def normalize_brain_records(self, parsed: GenericParsedBundle) -> list[BrainRecordEnvelope]:
        episode_id = _episode_id(parsed)
        trade_day = _trade_date(parsed)
        default_available_from = _default_available_from(parsed)
        raw_records = parsed.jsonl_blocks.get("brain_delta.jsonl", [])
        normalized_payloads = _v23_normalized_payloads(parsed)
        return [
            _record_envelope(
                payload=payload,
                raw_payload=raw_payload,
                episode_id=episode_id,
                trade_date=trade_day,
                default_available_from=default_available_from,
                source_line=line_number,
            )
            for line_number, (raw_payload, payload) in enumerate(
                zip(raw_records, normalized_payloads, strict=False),
                start=1,
            )
        ]


class ForwardCompatibleRawOnlyAdapter(BaseBundleAdapter):
    name = "forward-compatible-raw-only"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        return (
            _optional_string(_field(parsed, "episode_id")) is not None
            and _optional_date(_field(parsed, "trade_date")) is not None
            and bool(parsed.jsonl_blocks.get("brain_delta.jsonl"))
        )

    def validate(self, parsed: GenericParsedBundle) -> dict[str, Any]:
        validation = super().validate(parsed)
        validation.update(
            {
                "passed": False,
                "forward_compatible_raw_only": True,
                "unsupported_bundle_version": True,
                "reason": "unsupported bundle version preserved as staged raw-only records",
            }
        )
        return validation

    def normalize_brain_records(self, parsed: GenericParsedBundle) -> list[BrainRecordEnvelope]:
        episode_id = _episode_id(parsed)
        trade_day = _trade_date(parsed)
        default_available_from = _default_available_from(parsed)
        return [
            _raw_only_record_envelope(
                payload=payload,
                episode_id=episode_id,
                trade_date=trade_day,
                default_available_from=default_available_from,
                source_line=line_number,
            )
            for line_number, payload in enumerate(
                parsed.jsonl_blocks.get("brain_delta.jsonl", []),
                start=1,
            )
        ]

    def envelope(self, parsed: GenericParsedBundle) -> ResearchBundleEnvelope:
        return super().envelope(parsed).model_copy(
            update={"import_status": "forward_compatible_raw_only"}
        )


ADAPTERS: tuple[BundleVersionAdapter, ...] = (
    V23DirectIngestAdapter(),
    V11Adapter(),
    V10Adapter(),
    LegacyV1Adapter(),
)
FORWARD_COMPATIBLE_RAW_ONLY_ADAPTER = ForwardCompatibleRawOnlyAdapter()


def parse_generic_bundle(path: Path) -> GenericParsedBundle:
    text = path.read_text(encoding="utf-8", errors="replace")
    front_matter = _extract_front_matter(text)
    blocks = _extract_blocks(text)
    payload_blocks: dict[str, str] = {}
    json_blocks: dict[str, Any] = {}
    jsonl_blocks: dict[str, list[dict[str, Any]]] = {}
    for name, block in blocks.items():
        payload = _strip_optional_fence(block)
        payload_blocks[name] = payload
        if name.endswith(".json"):
            json_blocks[name] = _parse_json(name, payload)
        elif name.endswith(".jsonl"):
            jsonl_blocks[name] = _parse_jsonl(name, payload)
    return GenericParsedBundle(
        path=path,
        text=text,
        front_matter=front_matter,
        blocks=blocks,
        payload_blocks=payload_blocks,
        json_blocks=json_blocks,
        jsonl_blocks=jsonl_blocks,
    )


def inspect_versioned_bundle(path: Path) -> dict[str, Any]:
    parsed = parse_generic_bundle(path)
    adapter = select_adapter(parsed)
    raw_only_adapter = (
        FORWARD_COMPATIBLE_RAW_ONLY_ADAPTER
        if adapter is None and FORWARD_COMPATIBLE_RAW_ONLY_ADAPTER.supports(parsed)
        else None
    )
    effective_adapter = adapter or raw_only_adapter
    records = (
        effective_adapter.normalize_brain_records(parsed)
        if effective_adapter is not None
        else []
    )
    validation = effective_adapter.validate(parsed) if effective_adapter is not None else {}
    raw_record_count = len(parsed.jsonl_blocks.get("brain_delta.jsonl", []))
    normalized_record_count = len(records)
    record_count_payload = (
        _unsupported_bundle_record_payload(parsed)
        if effective_adapter is None
        else _record_count_parity_payload(
            raw_record_count=raw_record_count,
            normalized_record_count=normalized_record_count,
        )
    )
    import_loss_summary = _import_loss_summary(parsed, records)
    hash_mismatches = validation.get("hash_mismatches")
    hash_conflicts = validation.get("hash_expectation_conflicts")
    missing_source_refs = validation.get("missing_source_references")
    missing_payload_refs = validation.get("missing_payload_references")
    invalid_available_from_record_ids = validation.get("invalid_available_from_record_ids")
    invalid_label_quality_record_ids = validation.get(
        "invalid_outcome_label_quality_record_ids"
    )
    invalid_typed_payload_record_ids = validation.get("invalid_typed_payload_record_ids")
    direct_ingest_contract = _direct_ingest_contract(parsed)
    direct_ingest_hard_gates = (
        direct_ingest_contract.get("hard_gate_summary")
        if isinstance(direct_ingest_contract, dict)
        else None
    )
    direct_ingest_fatal_blockers = (
        direct_ingest_contract.get("fatal_blockers")
        if isinstance(direct_ingest_contract, dict)
        else None
    )
    final_semantic_audit_rows = parsed.jsonl_blocks.get("final_semantic_audit.jsonl")
    final_semantic_fail_count = (
        sum(
            1
            for row in final_semantic_audit_rows
            if not _semantic_audit_row_passed(row)
        )
        if final_semantic_audit_rows is not None
        else None
    )
    return {
        "path": path.as_posix(),
        "raw_bundle_sha256": file_sha256(path),
        "bundle_schema_version": bundle_schema_version(parsed),
        "manifest_schema_version": _optional_string(_manifest(parsed).get("schema_version")),
        "episode_schema_version": _optional_string(_episode(parsed).get("schema_version")),
        "adapter": effective_adapter.name if effective_adapter is not None else None,
        "supported": adapter is not None,
        "forward_compatible_raw_only": raw_only_adapter is not None,
        "episode_id": _field(parsed, "episode_id"),
        "trade_date": _field(parsed, "trade_date"),
        "block_count": len(parsed.blocks),
        "blocks": sorted(parsed.blocks),
        "record_count": len(records),
        **record_count_payload,
        "training_eligible_record_count": sum(
            1 for record in records if record.training_eligible
        ),
        **import_loss_summary,
        "quarantined_record_count": 0,
        "record_counts_by_type": dict(
            sorted(Counter(record.record_type for record in records).items())
        ),
        "validation_passed": validation.get("passed") is True,
        "import_loss_audit_passed": validation.get("import_loss_audit_passed"),
        "record_count_matches_manifest": validation.get("record_count_matches_manifest"),
        "training_eligible_count_matches_manifest": validation.get(
            "training_eligible_count_matches_manifest"
        ),
        "hash_mismatch_count": len(hash_mismatches) if isinstance(hash_mismatches, dict) else 0,
        "hash_expectation_conflict_count": (
            len(hash_conflicts) if isinstance(hash_conflicts, dict) else 0
        ),
        "missing_source_reference_count": (
            len(missing_source_refs) if isinstance(missing_source_refs, list) else 0
        ),
        "missing_payload_reference_count": (
            len(missing_payload_refs) if isinstance(missing_payload_refs, list) else 0
        ),
        "available_from_valid": validation.get("available_from_valid"),
        "invalid_available_from_record_count": (
            len(invalid_available_from_record_ids)
            if isinstance(invalid_available_from_record_ids, list)
            else 0
        ),
        "outcome_label_quality_valid": validation.get("outcome_label_quality_valid"),
        "invalid_outcome_label_quality_record_count": (
            len(invalid_label_quality_record_ids)
            if isinstance(invalid_label_quality_record_ids, list)
            else 0
        ),
        "typed_payload_valid": validation.get("typed_payload_valid"),
        "invalid_typed_payload_record_count": (
            len(invalid_typed_payload_record_ids)
            if isinstance(invalid_typed_payload_record_ids, list)
            else 0
        ),
        "direct_ingest_contract_present": isinstance(direct_ingest_contract, dict),
        "direct_ingest_contract_schema_version": (
            direct_ingest_contract.get("schema_version")
            if isinstance(direct_ingest_contract, dict)
            else None
        ),
        "direct_brain_ingest_ready": (
            direct_ingest_contract.get("direct_brain_ingest_ready")
            if isinstance(direct_ingest_contract, dict)
            else None
        ),
        "brain_eligible": (
            direct_ingest_contract.get("brain_eligible")
            if isinstance(direct_ingest_contract, dict)
            else None
        ),
        "requires_human_semantic_review": (
            direct_ingest_contract.get("requires_human_semantic_review")
            if isinstance(direct_ingest_contract, dict)
            else None
        ),
        "direct_ingest_fatal_blocker_count": (
            len(direct_ingest_fatal_blockers)
            if isinstance(direct_ingest_fatal_blockers, list)
            else None
        ),
        "direct_ingest_contract_validation_parity_verified": (
            direct_ingest_hard_gates.get(
                "direct_ingest_contract_validation_parity_verified"
            )
            if isinstance(direct_ingest_hard_gates, dict)
            else None
        ),
        "direct_ingest_contract_count_hash_parity_verified": (
            direct_ingest_hard_gates.get(
                "direct_ingest_contract_count_hash_parity_verified"
            )
            if isinstance(direct_ingest_hard_gates, dict)
            else None
        ),
        "direct_ingest_record_count_hash_parity_ready": (
            direct_ingest_hard_gates.get("record_count_hash_parity_ready")
            if isinstance(direct_ingest_hard_gates, dict)
            else None
        ),
        "direct_ingest_schema_contract_verified": (
            direct_ingest_hard_gates.get("schema_contract_verified")
            if isinstance(direct_ingest_hard_gates, dict)
            else None
        ),
        "direct_ingest_validator_exit_code": (
            _nested_int(direct_ingest_hard_gates, "validator_exit_code")
            if isinstance(direct_ingest_hard_gates, dict)
            else None
        ),
        "direct_ingest_critical_error_count": (
            _nested_int(direct_ingest_hard_gates, "critical_error_count")
            if isinstance(direct_ingest_hard_gates, dict)
            else None
        ),
        "final_semantic_audit_present": final_semantic_audit_rows is not None,
        "final_semantic_audit_count": (
            len(final_semantic_audit_rows)
            if final_semantic_audit_rows is not None
            else None
        ),
        "final_semantic_audit_fail_count": final_semantic_fail_count,
        "inspection_status": (
            "unsupported_bundle_version"
            if effective_adapter is None
            else (
                "forward_compatible_raw_only"
                if raw_only_adapter is not None
                else (
                    "validation_passed"
                    if validation.get("passed") is True
                    else "validation_failed"
                )
            )
        ),
        "validation": validation,
    }


def import_versioned_bundle(
    path: Path,
    *,
    root: Path,
    validate: bool = True,
    accepted: bool = True,
) -> BundleImportResult:
    parsed = parse_generic_bundle(path)
    adapter = select_adapter(parsed)
    source_hash = file_sha256(path)
    if adapter is None:
        raw_only_adapter = (
            FORWARD_COMPATIBLE_RAW_ONLY_ADAPTER
            if FORWARD_COMPATIBLE_RAW_ONLY_ADAPTER.supports(parsed)
            else None
        )
        if raw_only_adapter is not None:
            validation = raw_only_adapter.validate(parsed)
            records = raw_only_adapter.normalize_brain_records(parsed)
            import_loss_summary = _import_loss_summary(parsed, records)
            try:
                raw_only_stored = BrainRecordStore(root).store_bundle(
                    source_path=path,
                    envelope=raw_only_adapter.envelope(parsed),
                    index=raw_only_adapter.normalize_episode_index(parsed),
                    records=records,
                    raw_blocks=parsed.payload_blocks,
                    validation_report=validation,
                    accepted=False,
                )
            except BrainRecordStoreConflictError as exc:
                _write_store_conflict_report(
                    root=root,
                    parsed=parsed,
                    adapter_name=raw_only_adapter.name,
                    source_hash=source_hash,
                    validation=validation,
                    records=records,
                    import_loss_summary=import_loss_summary,
                    conflict=exc,
                    accepted=False,
                    acceptance_status="staged",
                )
                raise VersionedBundleImportError(str(exc)) from exc
            diagnostics_payload = {
                "status": "forward_compatible_raw_only",
                "adapter": raw_only_adapter.name,
                "bundle_version": bundle_schema_version(parsed),
                "episode_id": _episode_id(parsed),
                "accepted": False,
                "acceptance_status": "staged",
                **_record_count_parity_payload(
                    raw_record_count=len(
                        parsed.jsonl_blocks.get("brain_delta.jsonl", [])
                    ),
                    normalized_record_count=raw_only_stored.record_count,
                ),
                "training_eligible_record_count": raw_only_stored.training_eligible_record_count,
                "raw_only_record_count": raw_only_stored.record_count,
                **import_loss_summary,
                "import_loss_audit_passed": validation.get(
                    "import_loss_audit_passed",
                    _import_loss_audit_passed(import_loss_summary),
                ),
                "quarantined_record_count": 0,
                "record_counts_by_type": dict(
                    sorted(Counter(record.record_type for record in records).items())
                ),
                "validation": validation,
            }
            write_diagnostic_report(root, "bundle_import_report", diagnostics_payload)
            return BundleImportResult(
                status="forward_compatible_raw_only",
                adapter_name=raw_only_adapter.name,
                episode_id=_episode_id(parsed),
                bundle_schema_version=bundle_schema_version(parsed),
                accepted=False,
                record_count=raw_only_stored.record_count,
                training_eligible_record_count=raw_only_stored.training_eligible_record_count,
                envelope_path=raw_only_stored.envelope_path,
                record_path=raw_only_stored.record_path,
                manifest_path=raw_only_stored.manifest_path,
                validation=validation,
            )
        episode_id = _optional_string(_field(parsed, "episode_id")) or stable_id(
            "UNSUPPORTED",
            source_hash,
        )
        quarantine = BrainRecordStore(root).quarantine_conflict(
            source_path=path,
            reason="UNSUPPORTED_BUNDLE_VERSION",
            episode_id=episode_id,
            source_hash=source_hash,
            metadata={
                "bundle_schema_version": bundle_schema_version(parsed),
                "manifest_schema_version": _optional_string(_manifest(parsed).get("schema_version")),
                "quarantined_raw_record_count": len(
                    parsed.jsonl_blocks.get("brain_delta.jsonl", [])
                ),
                "quarantined_normalized_record_count": 0,
                "normalization_skipped_reason": "UNSUPPORTED_BUNDLE_VERSION",
            },
        )
        write_diagnostic_report(
            root,
            "bundle_import_report",
            {
                "status": "UNSUPPORTED_BUNDLE_VERSION",
                "episode_id": episode_id,
                "bundle_version": bundle_schema_version(parsed),
                "raw_bundle_sha256": source_hash,
                **_unsupported_bundle_record_payload(parsed),
                "missing_normalized_record_count": 0,
                "extra_normalized_record_count": 0,
                "quarantined_record_count": 1,
                "quarantine": quarantine.as_posix(),
            },
        )
        return BundleImportResult(
            status="UNSUPPORTED_BUNDLE_VERSION",
            adapter_name="unsupported",
            episode_id=episode_id,
            bundle_schema_version=bundle_schema_version(parsed),
            accepted=False,
            record_count=0,
            training_eligible_record_count=0,
            envelope_path=quarantine / "quarantine.json",
            record_path=None,
            manifest_path=None,
            validation={"passed": False, "quarantine": quarantine.as_posix()},
        )
    validation = adapter.validate(parsed)
    records = adapter.normalize_brain_records(parsed)
    if validation.get("passed") is not True and (validate or accepted):
        quarantine = _quarantine_validation_failure(
            root=root,
            path=path,
            parsed=parsed,
            adapter_name=adapter.name,
            source_hash=source_hash,
            validation=validation,
            records=records,
        )
        raise VersionedBundleImportError(
            "bundle validation failed; "
            f"quarantined at {quarantine.as_posix()}: "
            + json.dumps(validation, ensure_ascii=False, sort_keys=True)
        )
    import_loss_summary = _import_loss_summary(parsed, records)
    try:
        stored: StoredBundleResult = BrainRecordStore(root).store_bundle(
            source_path=path,
            envelope=adapter.envelope(parsed),
            index=adapter.normalize_episode_index(parsed),
            records=records,
            raw_blocks=parsed.payload_blocks,
            validation_report=validation,
            accepted=accepted,
        )
    except BrainRecordStoreConflictError as exc:
        _write_store_conflict_report(
            root=root,
            parsed=parsed,
            adapter_name=adapter.name,
            source_hash=source_hash,
            validation=validation,
            records=records,
            import_loss_summary=import_loss_summary,
            conflict=exc,
            accepted=accepted,
            acceptance_status="accepted" if accepted else "staged",
        )
        raise VersionedBundleImportError(str(exc)) from exc
    diagnostics_payload = {
        "status": "imported",
        "adapter": adapter.name,
        "bundle_version": bundle_schema_version(parsed),
        "episode_id": _episode_id(parsed),
        "raw_bundle_sha256": source_hash,
        "accepted": accepted,
        "acceptance_status": "accepted" if accepted else "staged",
        **_record_count_parity_payload(
            raw_record_count=len(parsed.jsonl_blocks.get("brain_delta.jsonl", [])),
            normalized_record_count=stored.record_count,
        ),
        "training_eligible_record_count": stored.training_eligible_record_count,
        **import_loss_summary,
        "import_loss_audit_passed": validation.get(
            "import_loss_audit_passed",
            _import_loss_audit_passed(import_loss_summary),
        ),
        "quarantined_record_count": 0,
        "record_counts_by_type": dict(
            sorted(Counter(record.record_type for record in records).items())
        ),
        "validation": validation,
    }
    write_diagnostic_report(root, "bundle_import_report", diagnostics_payload)
    return BundleImportResult(
        status="imported",
        adapter_name=adapter.name,
        episode_id=_episode_id(parsed),
        bundle_schema_version=bundle_schema_version(parsed),
        accepted=accepted,
        record_count=stored.record_count,
        training_eligible_record_count=stored.training_eligible_record_count,
        envelope_path=stored.envelope_path,
        record_path=stored.record_path,
        manifest_path=stored.manifest_path,
        validation=validation,
    )


def _write_store_conflict_report(
    *,
    root: Path,
    parsed: GenericParsedBundle,
    adapter_name: str,
    source_hash: str,
    validation: dict[str, Any],
    records: list[BrainRecordEnvelope],
    import_loss_summary: dict[str, Any],
    conflict: BrainRecordStoreConflictError,
    accepted: bool,
    acceptance_status: str,
) -> None:
    write_diagnostic_report(
        root,
        "bundle_import_report",
        {
            "status": conflict.reason,
            "adapter": adapter_name,
            "bundle_version": bundle_schema_version(parsed),
            "episode_id": conflict.episode_id,
            "raw_bundle_sha256": source_hash,
            "accepted": accepted,
            "acceptance_status": acceptance_status,
            **_record_count_parity_payload(
                raw_record_count=len(
                    parsed.jsonl_blocks.get("brain_delta.jsonl", [])
                ),
                normalized_record_count=len(records),
            ),
            "training_eligible_record_count": sum(
                1 for record in records if record.training_eligible
            ),
            **import_loss_summary,
            "import_loss_audit_passed": validation.get(
                "import_loss_audit_passed",
                _import_loss_audit_passed(import_loss_summary),
            ),
            "quarantined_record_count": 1,
            "quarantine": conflict.quarantine.as_posix(),
            "conflict_reason": conflict.reason,
            "conflict_message": str(conflict),
            "record_counts_by_type": dict(
                sorted(Counter(record.record_type for record in records).items())
            ),
            "validation": validation,
        },
    )


def _quarantine_validation_failure(
    *,
    root: Path,
    path: Path,
    parsed: GenericParsedBundle,
    adapter_name: str,
    source_hash: str,
    validation: dict[str, Any],
    records: list[BrainRecordEnvelope],
) -> Path:
    episode_id = _episode_id(parsed)
    quarantine = BrainRecordStore(root).quarantine_conflict(
        source_path=path,
        reason="BUNDLE_VALIDATION_FAILED",
        episode_id=episode_id,
        source_hash=source_hash,
        metadata={
            "adapter": adapter_name,
            "bundle_schema_version": bundle_schema_version(parsed),
            "quarantined_raw_record_count": len(
                parsed.jsonl_blocks.get("brain_delta.jsonl", [])
            ),
            "quarantined_normalized_record_count": len(records),
            "validation": validation,
        },
    )
    write_diagnostic_report(
        root,
        "bundle_import_report",
        {
            "status": "BUNDLE_VALIDATION_FAILED",
            "adapter": adapter_name,
            "bundle_version": bundle_schema_version(parsed),
            "episode_id": episode_id,
            "raw_bundle_sha256": source_hash,
            **_record_count_parity_payload(
                raw_record_count=len(
                    parsed.jsonl_blocks.get("brain_delta.jsonl", [])
                ),
                normalized_record_count=len(records),
            ),
            "training_eligible_record_count": sum(
                1 for record in records if record.training_eligible
            ),
            **_import_loss_summary(parsed, records),
            "import_loss_audit_passed": validation.get("import_loss_audit_passed"),
            "quarantined_record_count": 1,
            "quarantine": quarantine.as_posix(),
            "validation": validation,
        },
    )
    return quarantine


def _import_loss_audit_passed(summary: dict[str, Any]) -> bool:
    return (
        summary["record_id_set_matches_raw"] is True
        and summary["record_type_counts_match_raw"] is True
        and summary["training_eligible_count_matches_raw"] is True
        and summary["raw_payload_hashes_match"] is True
    )


def select_adapter(parsed: GenericParsedBundle) -> BundleVersionAdapter | None:
    for adapter in ADAPTERS:
        if adapter.supports(parsed):
            return adapter
    return None


def _import_loss_summary(
    parsed: GenericParsedBundle,
    records: list[BrainRecordEnvelope],
) -> dict[str, Any]:
    raw_records = parsed.jsonl_blocks.get("brain_delta.jsonl", [])
    raw_record_ids = [
        _record_identity(record, line_number)
        for line_number, record in enumerate(raw_records, start=1)
    ]
    normalized_record_ids = [record.record_id for record in records]
    raw_explicit_record_ids = [
        record_id for record_id in raw_record_ids if not record_id.startswith("line:")
    ]
    raw_id_set_comparable = len(raw_explicit_record_ids) == len(raw_record_ids)
    missing_normalized_record_ids = sorted(set(raw_explicit_record_ids) - set(normalized_record_ids))
    extra_normalized_record_ids = sorted(set(normalized_record_ids) - set(raw_explicit_record_ids))
    raw_counts_by_type = dict(
        sorted(
            Counter(str(record.get("record_type") or "unknown") for record in raw_records).items()
        )
    )
    normalized_counts_by_type = dict(
        sorted(Counter(record.record_type for record in records).items())
    )
    raw_training_eligible_count = sum(
        1 for record in raw_records if record.get("training_eligible") is True
    )
    raw_hashes = [
        sha256_text(json.dumps(record, ensure_ascii=False, sort_keys=True))
        for record in raw_records
    ]
    raw_payload_hash_mismatch_record_ids = [
        record.record_id
        for record, expected_hash in zip(records, raw_hashes, strict=False)
        if record.raw_payload_sha256 != expected_hash
    ]
    return {
        "raw_record_ids": raw_record_ids,
        "normalized_record_ids": normalized_record_ids,
        "raw_record_without_id_count": len(raw_record_ids) - len(raw_explicit_record_ids),
        "record_id_set_comparable": raw_id_set_comparable,
        "record_id_set_matches_raw": (
            not missing_normalized_record_ids and not extra_normalized_record_ids
            if raw_id_set_comparable
            else None
        ),
        "missing_normalized_record_ids": missing_normalized_record_ids,
        "missing_normalized_record_count": len(missing_normalized_record_ids),
        "extra_normalized_record_ids": extra_normalized_record_ids,
        "extra_normalized_record_count": len(extra_normalized_record_ids),
        "raw_record_counts_by_type": raw_counts_by_type,
        "record_type_counts_match_raw": raw_counts_by_type == normalized_counts_by_type,
        "raw_training_eligible_record_count": raw_training_eligible_count,
        "training_eligible_count_matches_raw": raw_training_eligible_count
        == sum(1 for record in records if record.training_eligible),
        "raw_payload_hashes_match": not raw_payload_hash_mismatch_record_ids
        and len(raw_hashes) == len(records),
        "raw_payload_hash_mismatch_record_ids": raw_payload_hash_mismatch_record_ids,
    }


def _record_count_parity_payload(
    *,
    raw_record_count: int,
    normalized_record_count: int,
) -> dict[str, Any]:
    return {
        "raw_record_count": raw_record_count,
        "normalized_record_count": normalized_record_count,
        "raw_normalized_record_count_matches": (
            raw_record_count == normalized_record_count
        ),
        "dropped_record_count": max(0, raw_record_count - normalized_record_count),
        "quarantined_bundle_count": 0,
        "quarantined_raw_record_count": 0,
        "normalization_skipped_reason": None,
    }


def _unsupported_bundle_record_payload(parsed: GenericParsedBundle) -> dict[str, Any]:
    raw_record_count = len(parsed.jsonl_blocks.get("brain_delta.jsonl", []))
    return {
        "raw_record_count": raw_record_count,
        "normalized_record_count": 0,
        "raw_normalized_record_count_matches": raw_record_count == 0,
        "dropped_record_count": 0,
        "quarantined_bundle_count": 1,
        "quarantined_raw_record_count": raw_record_count,
        "normalization_skipped_reason": "UNSUPPORTED_BUNDLE_VERSION",
    }


def bundle_schema_version(parsed: GenericParsedBundle) -> str:
    value = parsed.front_matter.get("schema_version")
    if value:
        return value
    manifest_version = _optional_string(_manifest(parsed).get("schema_version"))
    if manifest_version:
        return manifest_version
    episode_version = _optional_string(_episode(parsed).get("schema_version"))
    return episode_version or "UNKNOWN_BUNDLE_VERSION"


def _declares_research_bundle_version(version: str) -> bool:
    return version.startswith("nslab.research_bundle.")


def _is_future_bundle_manifest_version(
    version: str | None,
    *,
    supported_major: int,
) -> bool:
    if version is None or not version.startswith("nslab.bundle_manifest."):
        return False
    major = _schema_major(version)
    return major is not None and major > supported_major


def _schema_major(version: str) -> int | None:
    suffix = version.rsplit(".", maxsplit=1)[-1]
    if not suffix.startswith("v"):
        return None
    try:
        return int(suffix[1:])
    except ValueError:
        return None


def _record_envelope(
    *,
    payload: dict[str, Any],
    raw_payload: dict[str, Any] | None = None,
    episode_id: str,
    trade_date: date,
    default_available_from: datetime,
    source_line: int,
) -> BrainRecordEnvelope:
    record_type = str(payload.get("record_type") or "unknown")
    payload_model = KNOWN_RECORD_PAYLOAD_MODELS.get(record_type)
    typed_payload_status = (
        "KNOWN_TYPED_PAYLOAD" if payload_model is not None else "UNKNOWN_TYPED_PAYLOAD"
    )
    normalized_payload = dict(payload)
    training_eligible = bool(payload.get("training_eligible") is True)
    eligibility_reason = _optional_string(payload.get("eligibility_reason"))
    if payload_model is not None:
        try:
            payload_model.model_validate(payload)
        except ValidationError:
            typed_payload_status = "UNKNOWN_TYPED_PAYLOAD"
            training_eligible = False
            eligibility_reason = (
                (eligibility_reason + "; " if eligibility_reason else "")
                + "known record_type payload failed typed validation; preserved as raw payload"
            )
            normalized_payload["training_eligible"] = False
            normalized_payload["eligibility_reason"] = eligibility_reason
    else:
        training_eligible = False
        eligibility_reason = (
            (eligibility_reason + "; " if eligibility_reason else "")
            + "unknown record_type preserved as raw payload"
        )
        normalized_payload["training_eligible"] = False
        normalized_payload["eligibility_reason"] = eligibility_reason
    raw_payload_source = payload if raw_payload is None else raw_payload
    raw_payload_sha = sha256_text(
        json.dumps(raw_payload_source, ensure_ascii=False, sort_keys=True)
    )
    normalized_payload_sha = sha256_text(canonical_json(normalized_payload))
    record_id = _record_id(payload, episode_id, record_type, normalized_payload_sha)
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id=_optional_string(payload.get("episode_id")) or episode_id,
        trade_date=_optional_date(payload.get("trade_date")) or trade_date,
        available_from=_optional_datetime(payload.get("available_from"))
        or default_available_from,
        training_target=_optional_string(payload.get("training_target")),
        evidence_phase=_evidence_phase(record_type, payload),
        training_eligible=training_eligible,
        eligibility_reason=eligibility_reason,
        status=_optional_string(payload.get("status")) or "tentative",
        confidence_label=_optional_string(payload.get("confidence_label")) or "low",
        provenance_source_ids=_string_list(payload.get("provenance_source_ids")),
        raw_payload_sha256=raw_payload_sha,
        normalized_payload_sha256=normalized_payload_sha,
        typed_payload_status=typed_payload_status,
        source_line=source_line,
        payload=normalized_payload,
    )


def _raw_only_record_envelope(
    *,
    payload: dict[str, Any],
    episode_id: str,
    trade_date: date,
    default_available_from: datetime,
    source_line: int,
) -> BrainRecordEnvelope:
    record_type = str(payload.get("record_type") or "unknown")
    normalized_payload = dict(payload)
    normalized_payload["training_eligible"] = False
    eligibility_reason = _optional_string(payload.get("eligibility_reason"))
    eligibility_reason = (
        (eligibility_reason + "; " if eligibility_reason else "")
        + "unsupported bundle version preserved as forward-compatible raw-only record"
    )
    normalized_payload["eligibility_reason"] = eligibility_reason
    raw_payload_sha = sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    normalized_payload_sha = sha256_text(canonical_json(normalized_payload))
    return BrainRecordEnvelope(
        record_id=_record_id(payload, episode_id, record_type, normalized_payload_sha),
        record_type=record_type,
        episode_id=_optional_string(payload.get("episode_id")) or episode_id,
        trade_date=_optional_date(payload.get("trade_date")) or trade_date,
        available_from=_optional_datetime(payload.get("available_from"))
        or default_available_from,
        training_target=_optional_string(payload.get("training_target")),
        evidence_phase=_evidence_phase(record_type, payload),
        training_eligible=False,
        eligibility_reason=eligibility_reason,
        status=_optional_string(payload.get("status")) or "raw_only",
        confidence_label=_optional_string(payload.get("confidence_label")) or "low",
        provenance_source_ids=_string_list(payload.get("provenance_source_ids")),
        raw_payload_sha256=raw_payload_sha,
        normalized_payload_sha256=normalized_payload_sha,
        typed_payload_status="UNKNOWN_TYPED_PAYLOAD",
        source_line=source_line,
        payload=normalized_payload,
    )


def _record_id(
    payload: dict[str, Any],
    episode_id: str,
    record_type: str,
    normalized_payload_sha: str,
) -> str:
    for field_name in (
        "record_id",
        "brain_delta_id",
        "issuer_day_case_id",
        "case_id",
        "blind_pair_id",
        "claim_id",
        "mechanism_id",
        "counterexample_id",
        "edge_id",
        "question_id",
        "error_id",
    ):
        value = payload.get(field_name)
        if isinstance(value, str) and value:
            return value
    return stable_id("BRAIN", episode_id, record_type, normalized_payload_sha)


def _evidence_phase(record_type: str, payload: dict[str, Any]) -> str:
    value = _optional_string(payload.get("evidence_phase"))
    if value is not None:
        return value
    if record_type in {
        "memory_claim",
        "mechanism_memory",
        "counterexample",
        "research_question",
    }:
        return "AUDIT"
    if record_type == "beneficiary_discovery_case":
        return "BLIND"
    return "POSTMORTEM"


def _v23_direct_ingest_contract_checks(parsed: GenericParsedBundle) -> dict[str, Any]:
    contract = _direct_ingest_contract(parsed)
    hard_gate_summary: dict[str, Any] = {}
    fatal_blockers: list[Any] = []
    if isinstance(contract, dict):
        raw_hard_gate_summary = contract.get("hard_gate_summary")
        if isinstance(raw_hard_gate_summary, dict):
            hard_gate_summary = raw_hard_gate_summary
        raw_fatal_blockers = contract.get("fatal_blockers")
        if isinstance(raw_fatal_blockers, list):
            fatal_blockers = raw_fatal_blockers
    direct_ready = (
        isinstance(contract, dict)
        and contract.get("direct_brain_ingest_ready") is True
    )
    automated_ready = (
        isinstance(contract, dict)
        and contract.get("automated_import_expected_to_pass") is True
    )
    human_review = (
        contract.get("requires_human_semantic_review")
        if isinstance(contract, dict)
        else None
    )
    record_count_hash_ready = hard_gate_summary.get("record_count_hash_parity_ready") is True
    schema_contract_verified = hard_gate_summary.get("schema_contract_verified") is True
    supported = (
        isinstance(contract, dict)
        and direct_ready
        and automated_ready
        and human_review is False
        and not fatal_blockers
        and record_count_hash_ready
        and schema_contract_verified
    )
    return {
        "contract_present": isinstance(contract, dict),
        "supported": supported,
        "direct_brain_ingest_ready": direct_ready,
        "automated_import_expected_to_pass": automated_ready,
        "requires_human_semantic_review": human_review,
        "fatal_blocker_count": len(fatal_blockers),
        "record_count_hash_parity_ready": record_count_hash_ready,
        "schema_contract_verified": schema_contract_verified,
    }


def _semantic_audit_row_passed(row: dict[str, Any]) -> bool:
    for field_name in ("semantic_verdict", "semantic_audit_status", "status"):
        value = row.get(field_name)
        if isinstance(value, str) and value.upper() == "PASS":
            return True
    return False


def _v23_normalized_payloads(parsed: GenericParsedBundle) -> list[dict[str, Any]]:
    raw_records = parsed.jsonl_blocks.get("brain_delta.jsonl", [])
    provenance = _v23_provenance_index(parsed)
    normalized = [
        _v23_normalized_payload(record, provenance=provenance)
        for record in raw_records
    ]
    _apply_v23_fractional_sample_weights(normalized)
    return normalized


def _v23_normalized_payload(
    record: dict[str, Any],
    *,
    provenance: dict[str, dict[str, str]],
) -> dict[str, Any]:
    normalized = dict(record)
    nested = record.get("payload")
    nested_payload = dict(nested) if isinstance(nested, dict) else {}
    for key, value in nested_payload.items():
        normalized.setdefault(key, value)
    if nested_payload:
        normalized["payload"] = nested_payload
    _v23_apply_common_aliases(normalized, nested_payload)
    _v23_apply_record_type_aliases(normalized)
    _v23_apply_provenance(normalized, provenance=provenance)
    normalized.setdefault("training_target", _v23_training_target(normalized))
    normalized.setdefault("evidence_phase", _v23_evidence_phase(normalized))
    normalized.setdefault("status", "supported")
    return normalized


def _v23_apply_common_aliases(
    normalized: dict[str, Any],
    nested_payload: dict[str, Any],
) -> None:
    company_name = _first_optional_string(
        normalized.get("company_name"),
        nested_payload.get("company_name"),
        nested_payload.get("entity_name"),
        nested_payload.get("name"),
        nested_payload.get("name_on_D"),
    )
    if company_name is not None:
        normalized.setdefault("company_name", company_name)
    ticker = _first_optional_string(normalized.get("ticker"), nested_payload.get("ticker"))
    if ticker is not None:
        normalized.setdefault("ticker", ticker)
    candidate_lane = _optional_string(normalized.get("candidate_lane"))
    if candidate_lane is not None:
        normalized.setdefault("path_type", candidate_lane)
    fact_id = _optional_string(normalized.get("fact_id"))
    fact_ids = _string_list(normalized.get("fact_ids"))
    source_fact_ids = _string_list(normalized.get("source_fact_ids"))
    if fact_id is not None and fact_id not in fact_ids:
        fact_ids.append(fact_id)
    for item in source_fact_ids:
        if item not in fact_ids:
            fact_ids.append(item)
    if fact_ids:
        normalized["fact_ids"] = fact_ids
        normalized.setdefault("blind_fact_ids", fact_ids)
    if _outcome_payload(normalized):
        normalized.setdefault("D_outcome", _outcome_payload(normalized))


def _v23_apply_record_type_aliases(normalized: dict[str, Any]) -> None:
    record_type = _optional_string(normalized.get("record_type")) or "unknown"
    record_id = _optional_string(normalized.get("brain_delta_id")) or _optional_string(
        normalized.get("record_id")
    )
    ticker = _optional_string(normalized.get("ticker"))
    company_name = _optional_string(normalized.get("company_name"))
    issuer_day_group_id = _issuer_day_case_id(normalized)
    if (
        record_type in {"supervised_issuer_day_case", "supervised_direct_event_case"}
        and issuer_day_group_id is not None
    ):
        normalized.setdefault("issuer_day_weight_group_id", issuer_day_group_id)
    if record_type == "supervised_issuer_day_case":
        normalized.setdefault("issuer_day_case_id", record_id or issuer_day_group_id)
    elif record_type == "supervised_direct_event_case":
        normalized.setdefault("issuer_day_case_id", issuer_day_group_id)
        normalized.setdefault("case_id", record_id)
    elif record_type == "theme_formation_case":
        normalized.setdefault("chosen_leader_ticker", ticker)
        normalized.setdefault("chosen_leader_company_name", company_name)
    elif record_type == "blind_leader_preference_pair":
        normalized.setdefault("blind_pair_id", record_id)
        normalized.setdefault("blind_preferred_ticker", ticker)
        normalized.setdefault("blind_preferred_company_name", company_name)
        normalized.setdefault("outcome_winner_ticker", ticker)
        normalized.setdefault("outcome_winner_company_name", company_name)
        if "pair_direction_verified" in normalized:
            normalized.setdefault(
                "blind_preference_correct",
                normalized.get("pair_direction_verified") is True,
            )
    elif record_type == "candidate_generation_error_case":
        normalized.setdefault("error_id", normalized.get("audit_id") or record_id)
        normalized.setdefault("error_type", normalized.get("classification"))
        normalized.setdefault("missed_ticker", ticker)
        normalized.setdefault("missed_company_name", company_name)
        normalized.setdefault("correction_mode", normalized.get("classification"))
    elif record_type == "ranking_error_case":
        normalized.setdefault("error_id", normalized.get("audit_id") or record_id)
        normalized.setdefault("error_type", normalized.get("classification"))
        normalized.setdefault("corrected_ticker", ticker)
        normalized.setdefault("corrected_company_name", company_name)
        normalized.setdefault("correction_mode", normalized.get("classification"))
    elif record_type == "negative_control_case":
        normalized.setdefault("candidate_ticker", ticker)
        normalized.setdefault("candidate_company_name", company_name)


def _v23_apply_provenance(
    normalized: dict[str, Any],
    *,
    provenance: dict[str, dict[str, str]],
) -> None:
    source_ids = _string_list(normalized.get("provenance_source_ids"))
    for source_id in _direct_source_ids(normalized):
        _append_unique(source_ids, source_id)
    fact_to_source = provenance["fact_to_source"]
    for fact_id in _reference_values(normalized, "fact_id", "fact_ids", "source_fact_ids"):
        fact_source_id = fact_to_source.get(fact_id)
        if fact_source_id is not None:
            _append_unique(source_ids, fact_source_id)
    row_to_source = provenance["row_to_source"]
    for row_id in _reference_values(normalized, "row_id", "matched_row_ids"):
        row_source_id = row_to_source.get(row_id)
        if row_source_id is not None:
            _append_unique(source_ids, row_source_id)
    available_sources = provenance["available_source_ids"]
    if not source_ids and _has_blind_payload(normalized):
        _append_if_available(source_ids, available_sources, "SRC-BLIND-SNAPSHOT")
    if not source_ids and _outcome_payload(normalized):
        _append_if_available(source_ids, available_sources, "SRC-GOLD-REFERENCE")
    if source_ids:
        normalized["provenance_source_ids"] = source_ids


def _apply_v23_fractional_sample_weights(records: list[dict[str, Any]]) -> None:
    for record_type in ("supervised_issuer_day_case", "supervised_direct_event_case"):
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for record in records:
            if (
                record.get("record_type") != record_type
                or record.get("training_eligible") is not True
            ):
                continue
            ticker = _optional_string(record.get("ticker"))
            trade_day = _optional_string(record.get("trade_date"))
            if ticker is None or trade_day is None:
                continue
            groups.setdefault((trade_day, ticker), []).append(record)
        for (trade_day, ticker), group in groups.items():
            group_id = f"{trade_day}:{ticker}"
            explicit_total = sum(
                _numeric_weight(record.get("sample_weight"))
                for record in group
                if _numeric_weight(record.get("sample_weight")) > 0.0
            )
            missing = [
                record
                for record in group
                if _numeric_weight(record.get("sample_weight")) == 0.0
            ]
            if missing:
                fill_weight = max(0.0, 1.0 - explicit_total) / len(missing)
                for record in missing:
                    record["sample_weight"] = fill_weight
            for record in group:
                record.setdefault("issuer_day_weight_group_id", group_id)
                if record_type == "supervised_issuer_day_case":
                    record["issuer_day_sample_weight_policy"] = (
                        "fractional_issuer_day_group"
                    )
                    record.setdefault(
                        "issuer_day_case_id",
                        _optional_string(record.get("brain_delta_id"))
                        or _optional_string(record.get("record_id"))
                        or group_id,
                    )
                else:
                    record.setdefault("issuer_day_case_id", group_id)


def _v23_training_target(record: dict[str, Any]) -> str | None:
    return {
        "supervised_issuer_day_case": "issuer_day_price_response",
        "supervised_direct_event_case": "direct_event_response",
        "theme_formation_case": "theme_formation_response",
        "blind_leader_preference_pair": "outcome_preferred_candidate",
        "candidate_generation_error_case": "candidate_generation_correction",
        "ranking_error_case": "candidate_ranking_correction",
        "negative_control_case": "negative_control_calibration",
        "newsless_or_unexplained_case": "newsless_outcome_calibration",
        "context_market_state_or_fact_case": "context_market_state_or_fact",
    }.get(str(record.get("record_type") or ""))


def _v23_evidence_phase(record: dict[str, Any]) -> str:
    source_phase = _optional_string(record.get("source_phase"))
    if source_phase is not None:
        normalized = source_phase.upper()
        if "POSTSEAL" in normalized or "OUTCOME" in normalized:
            return "POSTMORTEM"
        if "BLIND" in normalized:
            return "BLIND"
        if "AUDIT" in normalized:
            return "AUDIT"
    return _evidence_phase(str(record.get("record_type") or "unknown"), record)


def _v23_provenance_index(parsed: GenericParsedBundle) -> dict[str, dict[str, str]]:
    fact_to_source: dict[str, str] = {}
    row_to_source: dict[str, str] = {}
    available_source_ids: dict[str, str] = {}
    for row in parsed.jsonl_blocks.get("fact_ledger_blind.jsonl", []):
        fact_id = _optional_string(row.get("fact_id"))
        source_id = _optional_string(row.get("source_id"))
        if fact_id is not None and source_id is not None:
            fact_to_source[fact_id] = source_id
    for row in parsed.jsonl_blocks.get("source_ledger.jsonl", []):
        source_id = _optional_string(row.get("source_id"))
        row_id = _optional_string(row.get("row_id"))
        if source_id is not None:
            available_source_ids[source_id] = source_id
        if row_id is not None and source_id is not None:
            row_to_source[row_id] = source_id
    return {
        "fact_to_source": fact_to_source,
        "row_to_source": row_to_source,
        "available_source_ids": available_source_ids,
    }


def _outcome_payload(record: dict[str, Any]) -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    for source_key, target_key in (
        ("ticker", "ticker"),
        ("company_name", "company_name"),
        ("outcome_high_return_pct", "high_return_pct"),
        ("outcome_close_return_pct", "close_return_pct"),
        ("outcome_high_return_rank", "high_return_rank"),
        ("upper_limit_touched", "upper_limit_touched"),
    ):
        if source_key in record:
            outcome[target_key] = record[source_key]
    return outcome


def _issuer_day_case_id(record: dict[str, Any]) -> str | None:
    trade_day = _optional_string(record.get("trade_date"))
    ticker = _optional_string(record.get("ticker"))
    if trade_day is None or ticker is None:
        return None
    return f"{trade_day}:{ticker}"


def _first_optional_string(*values: object) -> str | None:
    for value in values:
        candidate = _optional_string(value)
        if candidate is not None:
            return candidate
    return None


def _reference_values(record: dict[str, Any], *field_names: str) -> list[str]:
    values: list[str] = []
    for field_name in field_names:
        value = record.get(field_name)
        if isinstance(value, str) and value:
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str) and item)
    return values


def _direct_source_ids(record: dict[str, Any]) -> list[str]:
    return _reference_values(record, "source_id", "source_ids")


def _append_unique(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)


def _append_if_available(
    values: list[str],
    available_sources: dict[str, str],
    source_id: str,
) -> None:
    if source_id in available_sources:
        _append_unique(values, source_id)


def _has_blind_payload(record: dict[str, Any]) -> bool:
    return any(
        key in record
        for key in (
            "blind_score",
            "blind_rank",
            "candidate_lane",
            "matched_screening_ids",
            "screening_id",
        )
    )


def _extract_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("<!-- NSLAB:BEGIN ") and stripped.endswith(" -->"):
            name = stripped.removeprefix("<!-- NSLAB:BEGIN ").removesuffix(" -->").strip()
            if name in blocks:
                raise VersionedBundleImportError(f"duplicate BEGIN block: {name}")
            end_marker = f"<!-- NSLAB:END {name} -->"
            index += 1
            content: list[str] = []
            while index < len(lines) and lines[index].strip() != end_marker:
                content.append(lines[index])
                index += 1
            if index >= len(lines):
                raise VersionedBundleImportError(f"missing END marker for block: {name}")
            blocks[name] = "\n".join(content).strip()
        index += 1
    return blocks


def _extract_front_matter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    front_matter: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return front_matter
        if not stripped:
            continue
        key, separator, value = stripped.partition(":")
        if separator and key.strip():
            front_matter[key.strip()] = _front_matter_scalar(value)
    return front_matter


def _front_matter_scalar(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        stripped = stripped[1:-1]
    return stripped


def _strip_optional_fence(block: str) -> str:
    lines = block.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return block.strip()


def _parse_json(name: str, payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise VersionedBundleImportError(f"{name} is not valid JSON: {exc}") from exc


def _parse_jsonl(name: str, payload: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VersionedBundleImportError(
                f"{name}:{line_number} is not valid JSONL: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise VersionedBundleImportError(f"{name}:{line_number} must be a JSON object")
        rows.append(parsed)
    return rows


def _manifest(parsed: GenericParsedBundle) -> dict[str, Any]:
    value = parsed.json_blocks.get("bundle_manifest.json")
    return value if isinstance(value, dict) else {}


def _validation_report(parsed: GenericParsedBundle) -> dict[str, Any]:
    value = parsed.json_blocks.get("validation_report.json")
    return value if isinstance(value, dict) else {}


def _direct_ingest_contract(parsed: GenericParsedBundle) -> dict[str, Any] | None:
    value = parsed.json_blocks.get("direct_ingest_contract.json")
    return value if isinstance(value, dict) else None


def _episode(parsed: GenericParsedBundle) -> dict[str, Any]:
    value = parsed.json_blocks.get("research_episode.json")
    return value if isinstance(value, dict) else {}


def _field(parsed: GenericParsedBundle, key: str) -> object:
    if key in parsed.front_matter:
        return parsed.front_matter[key]
    manifest = _manifest(parsed)
    if key in manifest:
        return manifest[key]
    episode = _episode(parsed)
    return episode.get(key)


def _episode_id(parsed: GenericParsedBundle) -> str:
    value = _optional_string(_field(parsed, "episode_id"))
    if value is None:
        raise VersionedBundleImportError("bundle is missing episode_id")
    return value


def _trade_date(parsed: GenericParsedBundle) -> date:
    value = _optional_date(_field(parsed, "trade_date"))
    if value is None:
        raise VersionedBundleImportError("bundle is missing trade_date")
    return value


def _available_from(
    parsed: GenericParsedBundle,
    records: list[BrainRecordEnvelope],
) -> datetime:
    explicit = _optional_datetime(_field(parsed, "available_from"))
    if explicit is not None:
        return explicit
    if records:
        return min(record.available_from for record in records)
    return _default_available_from(parsed)


def _default_available_from(parsed: GenericParsedBundle) -> datetime:
    next_day = _optional_date(_field(parsed, "next_trade_date"))
    if next_day is not None:
        return datetime.combine(next_day, time(0, 0, 0), tzinfo=KST)
    trade_day = _trade_date(parsed)
    return datetime.combine(date.fromordinal(trade_day.toordinal() + 1), time(0, 0, 0), tzinfo=KST)


def _optional_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    if len(value) == 10:
        parsed_date = _optional_date(value)
        if parsed_date is None:
            return None
        return datetime.combine(parsed_date, time(0, 0, 0), tzinfo=KST)
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _int_field(*sources: object) -> int | None:
    index = 0
    while index < len(sources):
        source = sources[index]
        if (
            isinstance(source, dict)
            and index + 1 < len(sources)
            and isinstance(sources[index + 1], str)
        ):
            value = source.get(sources[index + 1])
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str):
                try:
                    return int(value)
                except ValueError:
                    pass
            index += 2
            continue
        if isinstance(source, int) and not isinstance(source, bool):
            return source
        if isinstance(source, str):
            try:
                return int(source)
            except ValueError:
                pass
        index += 1
    return None


def _all_declared_int_values_zero(*values: object) -> bool:
    declared_values = [
        int_value
        for value in values
        if (int_value := _int_field(value)) is not None
    ]
    return bool(declared_values) and all(value == 0 for value in declared_values)


def _nested_int(source: dict[str, Any], *keys: str) -> int | None:
    current: object = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, int) and not isinstance(current, bool) else None


def _dict_field(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _source_ids(parsed: GenericParsedBundle) -> set[str]:
    source_ids: set[str] = set()
    for row in parsed.jsonl_blocks.get("source_ledger.jsonl", []):
        source_id = row.get("source_id")
        if isinstance(source_id, str) and source_id:
            source_ids.add(source_id)
    return source_ids


def _missing_source_references(
    records: list[dict[str, Any]],
    source_ids: set[str],
) -> list[str]:
    missing: set[str] = set()
    for record in records:
        for source_id in _string_list(record.get("provenance_source_ids")):
            if source_id not in source_ids:
                missing.add(source_id)
    return sorted(missing)


def _invalid_event_ticker_edge_cutoff_record_ids(
    records: list[dict[str, Any]],
) -> list[str]:
    return [
        _record_identity(record, line_number)
        for line_number, record in enumerate(records, start=1)
        if record.get("record_type") == "event_ticker_edge"
        and record.get("training_eligible") is True
        and not _event_ticker_edge_cutoff_provenance_valid(record)
    ]


def _invalid_event_ticker_edge_source_ledger_cutoff_record_ids(
    records: list[dict[str, Any]],
    source_ledger_rows: list[dict[str, Any]],
) -> list[str]:
    rows_by_source_id = _source_ledger_rows_by_id(source_ledger_rows)
    invalid: list[str] = []
    for line_number, record in enumerate(records, start=1):
        if (
            record.get("record_type") != "event_ticker_edge"
            or record.get("training_eligible") is not True
        ):
            continue
        source_rows = [
            rows_by_source_id[source_id]
            for source_id in _string_list(record.get("provenance_source_ids"))
            if source_id in rows_by_source_id
        ]
        if any(_source_ledger_row_cutoff_valid(row) for row in source_rows):
            continue
        invalid.append(_record_identity(record, line_number))
    return invalid


def _invalid_company_memory_delta_known_at_record_ids(
    records: list[dict[str, Any]],
) -> list[str]:
    invalid: list[str] = []
    for line_number, record in enumerate(records, start=1):
        if record.get("record_type") != "company_memory_delta":
            continue
        raw_known_at = record.get("known_at")
        if raw_known_at in (None, ""):
            continue
        known_at = _explicit_datetime(raw_known_at)
        if known_at is None or known_at.tzinfo is None:
            invalid.append(_record_identity(record, line_number))
    return invalid


def _backdated_company_memory_delta_known_at_record_ids(
    records: list[dict[str, Any]],
) -> list[str]:
    backdated: list[str] = []
    for line_number, record in enumerate(records, start=1):
        if record.get("record_type") != "company_memory_delta":
            continue
        known_at = _explicit_datetime(record.get("known_at"))
        available_from = _explicit_datetime(record.get("available_from"))
        if (
            known_at is None
            or known_at.tzinfo is None
            or available_from is None
            or available_from.tzinfo is None
        ):
            continue
        if as_kst(known_at) < as_kst(available_from):
            backdated.append(_record_identity(record, line_number))
    return backdated


def _invalid_issuer_day_event_level_weight_record_ids(
    records: list[dict[str, Any]],
) -> list[str]:
    invalid: list[str] = []
    for line_number, record in enumerate(records, start=1):
        if (
            record.get("record_type") != "supervised_issuer_day_case"
            or record.get("training_eligible") is not True
            or "event_level_weights" not in record
        ):
            continue
        weights = record.get("event_level_weights")
        if not isinstance(weights, dict) or not weights:
            invalid.append(_record_identity(record, line_number))
            continue
        numeric_weights = [
            float(value)
            for value in weights.values()
            if isinstance(value, int | float) and not isinstance(value, bool)
        ]
        if (
            len(numeric_weights) != len(weights)
            or abs(sum(numeric_weights) - 1.0) > 0.000001
        ):
            invalid.append(_record_identity(record, line_number))
    return invalid


def _record_sample_weight_validation(records: list[dict[str, Any]]) -> dict[str, Any]:
    issuer_keys: set[tuple[str, str]] = set()
    duplicate_issuer_day_keys: list[str] = []
    issuer_weights: dict[str, float] = {}
    direct_weights: dict[str, float] = {}
    for record in records:
        if record.get("training_eligible") is not True:
            continue
        if record.get("record_type") == "supervised_issuer_day_case":
            key = (
                str(record.get("trade_date") or ""),
                str(record.get("ticker") or ""),
            )
            if key in issuer_keys and not _uses_fractional_issuer_day_group(record):
                duplicate_issuer_day_keys.append("|".join(key))
            issuer_keys.add(key)
            joined_key = "|".join(key)
            issuer_weights[joined_key] = issuer_weights.get(
                joined_key,
                0.0,
            ) + _numeric_weight(record.get("sample_weight", 0.0))
        if record.get("record_type") == "supervised_direct_event_case":
            issuer_day_weight_id = _issuer_day_weight_identity(record)
            direct_weights[issuer_day_weight_id] = direct_weights.get(
                issuer_day_weight_id,
                0.0,
            ) + _numeric_weight(record.get("sample_weight", 0.0))
    issuer_weight_mismatches = _weight_sum_mismatches(issuer_weights)
    direct_weight_mismatches = _weight_sum_mismatches(direct_weights)
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


def _weight_sum_mismatches(weights: dict[str, float]) -> dict[str, float]:
    return {
        key: round(total, 12)
        for key, total in sorted(weights.items())
        if abs(total - 1.0) > 0.000001
    }


def _numeric_weight(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _uses_fractional_issuer_day_group(record: dict[str, Any]) -> bool:
    return record.get("issuer_day_sample_weight_policy") == "fractional_issuer_day_group"


def _issuer_day_weight_identity(record: dict[str, Any]) -> str:
    for field_name in ("issuer_day_weight_group_id", "issuer_day_case_id"):
        value = record.get(field_name)
        if isinstance(value, str) and value:
            return value
    return f"{record.get('trade_date') or ''}:{record.get('ticker') or ''}"


def _explicit_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _source_ledger_rows_by_id(
    source_ledger_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in source_ledger_rows:
        source_id = row.get("source_id")
        if isinstance(source_id, str) and source_id:
            rows[source_id] = row
    return rows


def _event_ticker_edge_cutoff_provenance_valid(record: dict[str, Any]) -> bool:
    edge_origin = _normalized_edge_token(record.get("edge_origin"))
    source_kind = _normalized_edge_token(record.get("source_kind"))
    if (
        edge_origin in FORBIDDEN_EVENT_TICKER_EDGE_ORIGINS
        or source_kind in FORBIDDEN_EVENT_TICKER_EDGE_ORIGINS
        or "OUTCOME_ONLY" in edge_origin
        or "OUTCOME_ONLY" in source_kind
    ):
        return False
    return (
        _payload_bool_true(record, "source_time_verified", "time_verified")
        and record.get("available_before_cutoff") is True
    )


def _source_ledger_row_cutoff_valid(row: dict[str, Any]) -> bool:
    return row.get("time_verified") is True and row.get("available_before_cutoff") is True


def _payload_bool_true(payload: dict[str, Any], *field_names: str) -> bool:
    return any(payload.get(field_name) is True for field_name in field_names)


def _normalized_edge_token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().replace("-", "_").replace(" ", "_").upper()


def _record_identity(record: dict[str, Any], line_number: int) -> str:
    return (
        _optional_string(record.get("record_id"))
        or _optional_string(record.get("brain_delta_id"))
        or f"line:{line_number}"
    )


def _invalid_bundle_available_from_fields(parsed: GenericParsedBundle) -> list[str]:
    invalid: list[str] = []
    sources: tuple[tuple[str, dict[str, Any] | dict[str, str]], ...] = (
        ("front_matter", parsed.front_matter),
        ("bundle_manifest.json", _manifest(parsed)),
        ("research_episode.json", _episode(parsed)),
    )
    for source_name, source in sources:
        if "available_from" not in source:
            continue
        if _optional_datetime(source.get("available_from")) is None:
            invalid.append(f"{source_name}.available_from")
    return invalid


def _invalid_record_available_from_ids(records: list[dict[str, Any]]) -> list[str]:
    invalid: list[str] = []
    for line_number, record in enumerate(records, start=1):
        if "available_from" not in record:
            continue
        if _optional_datetime(record.get("available_from")) is not None:
            continue
        identity = _optional_string(record.get("record_id")) or f"line:{line_number}"
        invalid.append(identity)
    return invalid


def _invalid_outcome_label_quality_record_ids(
    records: list[dict[str, Any]],
) -> list[str]:
    invalid: list[str] = []
    for line_number, record in enumerate(records, start=1):
        values = _outcome_label_quality_values(record)
        if not values:
            continue
        if all(value in VALID_OUTCOME_LABEL_QUALITIES for value in values):
            continue
        identity = _optional_string(record.get("record_id")) or f"line:{line_number}"
        invalid.append(identity)
    return invalid


def _invalid_typed_payload_record_ids(records: list[dict[str, Any]]) -> list[str]:
    invalid: list[str] = []
    for line_number, record in enumerate(records, start=1):
        record_type = record.get("record_type")
        if not isinstance(record_type, str):
            continue
        payload_model = KNOWN_RECORD_PAYLOAD_MODELS.get(record_type)
        if payload_model is None:
            continue
        try:
            payload_model.model_validate(record)
        except ValidationError:
            invalid.append(_record_identity(record, line_number))
    return invalid


def _outcome_label_quality_values(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    if "label_quality" in record:
        value = record.get("label_quality")
        values.append(value if isinstance(value, str) else "")
    outcome = record.get("D_outcome")
    if isinstance(outcome, dict) and "label_quality" in outcome:
        value = outcome.get("label_quality")
        values.append(value if isinstance(value, str) else "")
    return values


def _block_hashes(parsed: GenericParsedBundle) -> dict[str, str]:
    return {
        name: sha256_text(payload)
        for name, payload in sorted(parsed.payload_blocks.items())
    }


def _block_counts(parsed: GenericParsedBundle) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, rows in parsed.jsonl_blocks.items():
        counts[name] = len(rows)
    for name, payload in parsed.json_blocks.items():
        counts[name] = len(payload) if isinstance(payload, list | dict) else 1
    return counts


def _hash_validation(
    parsed: GenericParsedBundle,
    manifest: dict[str, Any],
    validation_report: dict[str, Any],
) -> BlockHashValidation:
    expected_hashes: dict[str, str] = {}
    actual_hashes = _block_hashes(parsed)
    self_referential: dict[str, dict[str, str]] = {}
    expectation_sources: dict[str, list[dict[str, str]]] = {}
    embedded = manifest.get("embedded_blocks")
    if isinstance(embedded, dict):
        for block_name, block_meta in embedded.items():
            if not isinstance(block_name, str) or not isinstance(block_meta, dict):
                continue
            sha = block_meta.get("sha256")
            if isinstance(sha, str) and sha:
                if block_name == "bundle_manifest.json":
                    self_referential[block_name] = {
                        "expected": sha,
                        "actual": actual_hashes.get(block_name, ""),
                        "source": "bundle_manifest.embedded_blocks",
                        "reason": "hash is declared inside the same block it describes",
                    }
                    continue
                _add_hash_expectation(
                    expected_hashes,
                    expectation_sources,
                    block_name=block_name,
                    expected=sha,
                    source="bundle_manifest.embedded_blocks",
                    replace=True,
                )
    checked_hashes = validation_report.get("checked_artifact_hashes")
    if isinstance(checked_hashes, dict):
        for key, value in checked_hashes.items():
            if isinstance(key, str) and isinstance(value, str) and key in parsed.payload_blocks:
                _add_hash_expectation(
                    expected_hashes,
                    expectation_sources,
                    block_name=key,
                    expected=value,
                    source="validation_report.checked_artifact_hashes",
                )
    legacy_fields = {
        "blind_prediction.json": "prediction_sha256",
        "research_report.md": "research_report_sha256",
        "research_episode.json": "research_episode_sha256",
        "row_disposition.jsonl": "row_disposition_sha256",
        "brain_delta.jsonl": "brain_delta_sha256",
        "source_ledger.jsonl": "source_ledger_sha256",
        "phase_state.json": "phase_state_sha256",
    }
    for block_name, field_name in legacy_fields.items():
        value = manifest.get(field_name)
        if isinstance(value, str) and value:
            _add_hash_expectation(
                expected_hashes,
                expectation_sources,
                block_name=block_name,
                expected=value,
                source=f"bundle_manifest.{field_name}",
            )
    expectation_conflicts = _hash_expectation_conflicts(expectation_sources)
    mismatches = {
        block_name: {
            "expected": expected,
            "actual": actual_hashes.get(block_name, ""),
            "sources": [
                source["source"]
                for source in expectation_sources.get(block_name, [])
                if source["expected"] == expected
            ],
        }
        for block_name, expected in sorted(expected_hashes.items())
        if block_name in actual_hashes and actual_hashes[block_name] != expected
    }
    return BlockHashValidation(
        mismatches=mismatches,
        self_referential=dict(sorted(self_referential.items())),
        expectation_sources=dict(sorted(expectation_sources.items())),
        expectation_conflicts=expectation_conflicts,
    )


def _add_hash_expectation(
    expected_hashes: dict[str, str],
    expectation_sources: dict[str, list[dict[str, str]]],
    *,
    block_name: str,
    expected: str,
    source: str,
    replace: bool = False,
) -> None:
    expectation_sources.setdefault(block_name, []).append(
        {
            "expected": expected,
            "source": source,
        }
    )
    if replace or block_name not in expected_hashes:
        expected_hashes[block_name] = expected


def _hash_expectation_conflicts(
    expectation_sources: dict[str, list[dict[str, str]]],
) -> dict[str, list[dict[str, str]]]:
    conflicts: dict[str, list[dict[str, str]]] = {}
    for block_name, sources in sorted(expectation_sources.items()):
        expected_values = {source["expected"] for source in sources}
        if len(expected_values) > 1:
            conflicts[block_name] = sources
    return conflicts
