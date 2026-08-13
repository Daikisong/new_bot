"""Shared contracts for sequential research-bundle repair and audit."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from news_scalping_lab.contracts.models import StrictModel

REPAIR_POLICY_VERSION = "nslab.repair_policy.v1"
REPAIR_GATE_VERSION = "nslab.repair_gate.v1"
REPAIR_CENSUS_SCHEMA_VERSION = "nslab.repair_source_census.v1"
REPAIR_LINEAGE_SCHEMA_VERSION = "nslab.repair_record_lineage.v1"


class RepairTaskState(StrEnum):
    DISCOVERED = "DISCOVERED"
    REPAIRED_PASS = "REPAIRED_PASS"
    IMPORTABLE_LEGACY = "IMPORTABLE_LEGACY"
    PRESERVED_PARTIAL_NOT_CURRENT_GOLD = "PRESERVED_PARTIAL_NOT_CURRENT_GOLD"
    DEFERRED_NON_TRADING = "DEFERRED_NON_TRADING"
    PARTIAL_PRICE_SOURCE_MISSING = "PARTIAL_PRICE_SOURCE_MISSING"
    PRESERVED_SOURCE_PAYLOAD_ABSENT = "PRESERVED_SOURCE_PAYLOAD_ABSENT"
    ADAPTER_REQUIRED = "ADAPTER_REQUIRED"
    FATAL_INPUT_FAILURE = "FATAL_INPUT_FAILURE"


class ArtifactOccurrence(StrictModel):
    occurrence_id: str
    raw_name: str | None = None
    canonical_name: str | None = None
    wrapper_kind: str
    byte_start: int
    byte_end: int
    payload_sha256: str
    canonical_payload_sha256: str | None = None
    declared_format: str | None = None
    parse_status: str
    row_count: int | None = None
    top_level_shape: str | None = None
    explicit_record_ids: list[str] = Field(default_factory=list)
    overlapping_alias: bool = False
    error: str | None = None


class UnclaimedMachinePayload(StrictModel):
    occurrence_id: str
    byte_start: int
    byte_end: int
    payload_sha256: str
    detected_shape: str
    reason: str


class ArtifactRow(StrictModel):
    schema_version: str = "nslab.repair_artifact_row.v1"
    origin_key: str
    source_sha256: str
    occurrence_id: str
    canonical_name: str
    row_ordinal: int
    raw_payload_sha256: str
    raw_row_byte_start: int
    raw_row_byte_end: int
    raw_row_bytes_sha256: str
    canonical_row_sha256: str
    row: dict[str, Any]


class SourceCensus(StrictModel):
    schema_version: str = REPAIR_CENSUS_SCHEMA_VERSION
    source_path: Path
    source_sha256: str
    byte_size: int
    strict_utf8_ok: bool
    decode_error: str | None = None
    replacement_character_count: int = 0
    artifact_occurrences: list[ArtifactOccurrence] = Field(default_factory=list)
    unclaimed_machine_payloads: list[UnclaimedMachinePayload] = Field(default_factory=list)
    artifact_counts: dict[str, int] = Field(default_factory=dict)
    duplicate_names: list[str] = Field(default_factory=list)
    conflicting_duplicate_names: list[str] = Field(default_factory=list)
    explicit_record_count: int = 0
    raw_record_type_token_count: int = 0
    structure_fingerprint: str


class RecordLineageEntry(StrictModel):
    schema_version: str = REPAIR_LINEAGE_SCHEMA_VERSION
    origin_key: str
    source_sha256: str
    artifact_occurrence_id: str
    row_ordinal: int
    raw_payload_sha256: str
    raw_row_bytes_sha256: str
    original_domain_id: str | None = None
    original_record_type: str | None = None
    repaired_record_id: str | None = None
    repaired_record_type: str | None = None
    repaired_payload_sha256: str | None = None
    lineage_kind: str
    status: str
    transform_rule_ids: list[str] = Field(default_factory=list)
    changed_fields: dict[str, Any] = Field(default_factory=dict)
    derivation_inputs: list[str] = Field(default_factory=list)
    training_eligible_before: bool | None = None
    training_eligible_after: bool | None = None
    eligibility_transition_reason: str | None = None
    provenance_before: list[str] = Field(default_factory=list)
    provenance_after: list[str] = Field(default_factory=list)
    cross_record_ref_rewrites: dict[str, str] = Field(default_factory=dict)


class RepairQualityGate(StrictModel):
    schema_version: str = REPAIR_GATE_VERSION
    source_sha256: str
    repaired_sha256: str
    repaired_byte_size: int
    engine_digest: str
    repair_policy_version: str = REPAIR_POLICY_VERSION
    passed: bool
    ready_for_import_pass: bool
    importable_legacy: bool
    current_gold_pass: bool
    mechanical_gold_ready: bool
    final_status: RepairTaskState
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_census: dict[str, Any] = Field(default_factory=dict)
    lineage: dict[str, Any] = Field(default_factory=dict)
    population: dict[str, Any] = Field(default_factory=dict)
    importer: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    eligibility: dict[str, Any] = Field(default_factory=dict)
    temporal: dict[str, Any] = Field(default_factory=dict)
    semantic: dict[str, Any] = Field(default_factory=dict)
    duplicate: dict[str, Any] = Field(default_factory=dict)
    deterministic: dict[str, Any] = Field(default_factory=dict)
