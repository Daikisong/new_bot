"""Strict contracts for Phase 9 production import and release promotion."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    Sha256,
    StrictMemoryContextModel,
)

PRODUCTION_IMPORT_INVENTORY_VERSION = "production_import_inventory.v1"
PRODUCTION_BATCH_IMPORT_VERSION = "production_batch_import.v1"
PRODUCTION_RELEASE_POLICY_VERSION = "production_release_policy.v1"


def _canonical_relative_path(value: str) -> str:
    if value != value.strip() or not value or "\\" in value:
        raise ValueError("artifact path must be a canonical relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ":" in path.parts[0]:
        raise ValueError("artifact path must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact path cannot contain traversal segments")
    if path.as_posix() != value:
        raise ValueError("artifact path must be canonical")
    return value


class ProductionInventoryAttestation(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_inventory_attestation.v1"] = (
        "nslab.production_inventory_attestation.v1"
    )
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    issued_at: AwareDatetime
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    commitment_sha256: Sha256
    signature: Sha256


class ProductionImportInventoryEntry(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_import_inventory_entry.v1"] = (
        "nslab.production_import_inventory_entry.v1"
    )
    ordinal: int = Field(ge=0)
    source_manifest_line: int = Field(ge=1)
    filename_date: str = Field(pattern=r"^\d{8}$")
    source_path: str
    source_sha256: Sha256
    source_byte_size: int = Field(ge=0)
    repaired_path: str
    repaired_sha256: Sha256
    repaired_byte_size: int = Field(ge=0)
    quality_gate_path: str
    quality_gate_sha256: Sha256
    engine_digest: Sha256
    record_count: int = Field(ge=0)
    training_eligible_record_count: int = Field(ge=0)
    semantic_excluded_record_count: int = Field(ge=0)
    final_status: Literal["REPAIRED_PASS"] = "REPAIRED_PASS"
    ready_for_import: Literal[True] = True

    @field_validator("source_path", "repaired_path", "quality_gate_path")
    @classmethod
    def validate_artifact_path(cls, value: str) -> str:
        return _canonical_relative_path(value)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.training_eligible_record_count > self.record_count:
            raise ValueError("training eligible count exceeds record count")
        if self.semantic_excluded_record_count > self.record_count:
            raise ValueError("semantic excluded count exceeds record count")
        return self


class ProductionImportInventoryManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_import_inventory_manifest.v1"] = (
        "nslab.production_import_inventory_manifest.v1"
    )
    inventory_id: str = Field(pattern=r"^P9INV-[0-9A-F]{20}$")
    created_at: AwareDatetime
    verifier_version: Literal["production_import_inventory.v1"] = (
        "production_import_inventory.v1"
    )
    source_manifest: ArtifactReference
    ready_entries: ArtifactReference
    manifest_entry_count: int = Field(ge=0)
    status_counts: dict[str, int]
    declared_ready_bundle_count: int = Field(ge=0)
    ready_bundle_count: int = Field(ge=0)
    ready_record_count: int = Field(ge=0)
    ready_training_eligible_record_count: int = Field(ge=0)
    ready_semantic_excluded_record_count: int = Field(ge=0)
    source_root_sha256: Sha256
    repaired_root_sha256: Sha256
    quality_gate_root_sha256: Sha256
    deep_hash_verified: bool
    ready_for_import: bool
    finding_count: int = Field(ge=0)
    findings: list[str] = Field(default_factory=list)
    attestation: ProductionInventoryAttestation | None = None

    @model_validator(mode="after")
    def validate_inventory(self) -> Self:
        if any(count < 0 for count in self.status_counts.values()):
            raise ValueError("status counts cannot be negative")
        if self.manifest_entry_count != sum(self.status_counts.values()):
            raise ValueError("status counts do not cover the source manifest")
        if self.ready_bundle_count != self.ready_entries.item_count:
            raise ValueError("ready bundle count conflicts with entries artifact")
        if self.finding_count != len(self.findings):
            raise ValueError("finding count conflicts with findings")
        expected_ready = (
            self.deep_hash_verified
            and not self.findings
            and self.ready_bundle_count == self.declared_ready_bundle_count
        )
        if self.ready_for_import is not expected_ready:
            raise ValueError("ready_for_import conflicts with verification findings")
        return self


class ProductionRecordArtifact(StrictMemoryContextModel):
    sha256: Sha256
    byte_size: int = Field(ge=0)


class ProductionRecordArtifactManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_record_artifact_manifest.v1"] = (
        "nslab.production_record_artifact_manifest.v1"
    )
    record_count: int = Field(ge=0)
    artifact_count: int = Field(gt=0)
    total_byte_size: int = Field(ge=0)
    artifacts: dict[str, ProductionRecordArtifact]
    root_sha256: Sha256

    @model_validator(mode="after")
    def validate_artifacts(self) -> Self:
        if self.artifact_count != len(self.artifacts):
            raise ValueError("record artifact count mismatch")
        if self.total_byte_size != sum(
            artifact.byte_size for artifact in self.artifacts.values()
        ):
            raise ValueError("record artifact byte total mismatch")
        observed_roots: set[str] = set()
        for artifact_path in self.artifacts:
            canonical_path = _canonical_relative_path(artifact_path)
            parts = PurePosixPath(canonical_path).parts
            if len(parts) < 3 or parts[0] != "memory":
                raise ValueError("record artifact path is outside memory")
            if parts[1] not in {"records", "record_manifests", "record_index"}:
                raise ValueError("record artifact path has an unsupported root")
            observed_roots.add(parts[1])
        if observed_roots != {"records", "record_manifests", "record_index"}:
            raise ValueError("record artifact roots are incomplete")
        required_index_paths = {
            "memory/record_index/manifest.json",
            "memory/record_index/by_record_id.json",
        }
        if not required_index_paths.issubset(self.artifacts):
            raise ValueError("record index artifacts are incomplete")
        canonical = json.dumps(
            {
                path: artifact.model_dump(mode="json")
                for path, artifact in self.artifacts.items()
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_root = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.root_sha256 != expected_root:
            raise ValueError("record artifact root hash mismatch")
        return self


class ProductionReleaseArtifactManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_release_artifact_manifest.v1"] = (
        "nslab.production_release_artifact_manifest.v1"
    )
    projection_version: Literal["production_release_artifacts.v1"] = (
        "production_release_artifacts.v1"
    )
    artifact_count: int = Field(gt=0)
    total_byte_size: int = Field(ge=0)
    artifacts: dict[str, ProductionRecordArtifact]
    root_sha256: Sha256

    @model_validator(mode="after")
    def validate_artifacts(self) -> Self:
        if self.artifact_count != len(self.artifacts):
            raise ValueError("release artifact count mismatch")
        if self.total_byte_size != sum(
            artifact.byte_size for artifact in self.artifacts.values()
        ):
            raise ValueError("release artifact byte total mismatch")
        observed_roots: set[str] = set()
        for artifact_path in self.artifacts:
            canonical_path = _canonical_relative_path(artifact_path)
            parts = PurePosixPath(canonical_path).parts
            root_name = parts[0]
            if root_name not in {"brain", "memory", "research", "runs", "warehouse"}:
                raise ValueError("release artifact path has an unsupported root")
            if root_name == "runs" and (
                len(parts) < 3 or parts[1] != "shadow_evaluation"
            ):
                raise ValueError("only sealed shadow runs are release artifacts")
            observed_roots.add(root_name)
        required_roots = {"brain", "memory", "research", "runs", "warehouse"}
        if observed_roots != required_roots:
            raise ValueError("release artifact roots are incomplete")
        canonical = json.dumps(
            {
                "projection_version": self.projection_version,
                "artifacts": {
                    path: artifact.model_dump(mode="json")
                    for path, artifact in self.artifacts.items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_root = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.root_sha256 != expected_root:
            raise ValueError("release artifact root hash mismatch")
        return self


class ProductionCompanyMemoryAttestation(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_company_memory_attestation.v1"] = (
        "nslab.production_company_memory_attestation.v1"
    )
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    issued_at: AwareDatetime
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    release_id: str = Field(pattern=r"^P9REL-[0-9A-F]{20}$")
    memory_artifact_path: str
    memory_payload_sha256: Sha256
    prediction_artifact_path: str
    prediction_sha256: Sha256
    known_at: AwareDatetime
    commitment_sha256: Sha256
    signature: Sha256

    @field_validator("memory_artifact_path", "prediction_artifact_path")
    @classmethod
    def validate_artifact_path(cls, value: str) -> str:
        return _canonical_relative_path(value)


class ProductionBatchImportReceipt(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_batch_import_receipt.v1"] = (
        "nslab.production_batch_import_receipt.v1"
    )
    import_id: str = Field(pattern=r"^P9IMPORT-[0-9A-F]{20}$")
    importer_version: Literal["production_batch_import.v1"] = (
        "production_batch_import.v1"
    )
    started_at: AwareDatetime
    completed_at: AwareDatetime
    inventory_id: str = Field(pattern=r"^P9INV-[0-9A-F]{20}$")
    inventory_manifest: ArtifactReference
    release_project_path: str
    bundle_results: ArtifactReference
    record_index_manifest: ArtifactReference
    record_identity_index: ArtifactReference
    record_artifacts: ArtifactReference
    record_artifact_root_sha256: Sha256
    imported_bundle_count: int = Field(ge=0)
    imported_record_count: int = Field(ge=0)
    imported_training_eligible_record_count: int = Field(ge=0)
    quarantined_bundle_count: int = Field(ge=0)
    import_loss_count: int = Field(ge=0)
    record_store_generation_sha256: Sha256
    record_corpus_sha256: Sha256
    passed: bool
    finding_count: int = Field(ge=0)
    findings: list[str] = Field(default_factory=list)

    @field_validator("release_project_path")
    @classmethod
    def validate_release_project_path(cls, value: str) -> str:
        return _canonical_relative_path(value)

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("batch import cannot complete before it starts")
        if self.imported_bundle_count != self.bundle_results.item_count:
            raise ValueError("imported bundle count conflicts with results artifact")
        if self.finding_count != len(self.findings):
            raise ValueError("finding count conflicts with findings")
        expected_passed = (
            not self.findings
            and self.quarantined_bundle_count == 0
            and self.import_loss_count == 0
        )
        if self.passed is not expected_passed:
            raise ValueError("batch import passed flag conflicts with findings")
        return self


class ProductionReleaseTransaction(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_release_transaction.v1"] = (
        "nslab.production_release_transaction.v1"
    )
    release_id: str = Field(pattern=r"^P9REL-[0-9A-F]{20}$")
    import_id: str = Field(pattern=r"^P9IMPORT-[0-9A-F]{20}$")
    inventory_id: str = Field(pattern=r"^P9INV-[0-9A-F]{20}$")
    created_at: AwareDatetime
    shadow_evaluation_relative_path: str
    source_import_receipt_sha256: Sha256
    release_identity_sha256: Sha256

    @field_validator("shadow_evaluation_relative_path")
    @classmethod
    def validate_shadow_path(cls, value: str) -> str:
        return _canonical_relative_path(value)


class ProductionReleaseConfigurationManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_release_configuration.v1"] = (
        "nslab.production_release_configuration.v1"
    )
    file_count: int = Field(gt=0)
    file_hashes: dict[str, Sha256]
    root_sha256: Sha256

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        if self.file_count != len(self.file_hashes):
            raise ValueError("configuration file count mismatch")
        roots: set[str] = set()
        for artifact_path in self.file_hashes:
            canonical_path = _canonical_relative_path(artifact_path)
            root_name = PurePosixPath(canonical_path).parts[0]
            if root_name not in {"configs", "prompts", "schemas"}:
                raise ValueError("configuration artifact has an unsupported root")
            roots.add(root_name)
        if roots != {"configs", "prompts", "schemas"}:
            raise ValueError("configuration roots are incomplete")
        canonical = json.dumps(
            self.file_hashes,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_root = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.root_sha256 != expected_root:
            raise ValueError("configuration root hash mismatch")
        return self


class ProductionReleaseManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_release_manifest.v1"] = (
        "nslab.production_release_manifest.v1"
    )
    release_id: str = Field(pattern=r"^P9REL-[0-9A-F]{20}$")
    created_at: AwareDatetime
    policy_version: Literal["production_release_policy.v1"] = (
        "production_release_policy.v1"
    )
    release_project_path: str
    release_transaction: ArtifactReference
    release_configuration: ArtifactReference
    release_configuration_root_sha256: Sha256
    release_artifacts: ArtifactReference
    release_artifact_projection_version: Literal[
        "production_release_artifacts.v1"
    ] = "production_release_artifacts.v1"
    release_artifact_root_sha256: Sha256
    record_artifact_root_sha256: Sha256
    inventory_manifest: ArtifactReference
    import_receipt: ArtifactReference
    brain_manifest: ArtifactReference
    memory_snapshot_manifest: ArtifactReference
    shadow_evaluation_manifest: ArtifactReference
    doctor_report: ArtifactReference
    brain_version: str
    memory_snapshot_id: str
    shadow_evaluation_id: str
    llm_provider: str
    llm_model: str
    embedding_model: str
    web_provider: str
    price_provider: str
    audit_results: dict[str, bool]
    finding_count: int = Field(ge=0)
    findings: list[str] = Field(default_factory=list)
    production_ready: bool

    @field_validator("release_project_path")
    @classmethod
    def validate_project_path(cls, value: str) -> str:
        return _canonical_relative_path(value)

    @model_validator(mode="after")
    def validate_release(self) -> Self:
        if self.finding_count != len(self.findings):
            raise ValueError("finding count conflicts with findings")
        expected_ready = not self.findings and bool(self.audit_results) and all(
            self.audit_results.values()
        )
        if self.production_ready is not expected_ready:
            raise ValueError("production_ready conflicts with release audits")
        provider_values = (
            self.llm_provider,
            self.llm_model,
            self.embedding_model,
            self.web_provider,
            self.price_provider,
        )
        if self.production_ready and any(
            token in value.strip().lower()
            for value in provider_values
            for token in ("mock", "deterministic", "fixture", "test")
        ):
            raise ValueError("production release cannot use a test or mock provider")
        return self


class ProductionActivationAttestation(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_activation_attestation.v1"] = (
        "nslab.production_activation_attestation.v1"
    )
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    issued_at: AwareDatetime
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    commitment_sha256: Sha256
    signature: Sha256


class ProductionCurrentPointer(StrictMemoryContextModel):
    schema_version: Literal["nslab.production_current_pointer.v1"] = (
        "nslab.production_current_pointer.v1"
    )
    release_id: str = Field(pattern=r"^P9REL-[0-9A-F]{20}$")
    activated_at: AwareDatetime
    previous_release_id: str | None = Field(
        default=None,
        pattern=r"^P9REL-[0-9A-F]{20}$",
    )
    release_project_path: str
    release_manifest: ArtifactReference
    attestation: ProductionActivationAttestation

    @field_validator("release_project_path")
    @classmethod
    def validate_project_path(cls, value: str) -> str:
        return _canonical_relative_path(value)

    @model_validator(mode="after")
    def validate_activation_time(self) -> Self:
        if self.attestation.issued_at != self.activated_at:
            raise ValueError("activation attestation time mismatch")
        if self.previous_release_id == self.release_id:
            raise ValueError("previous release cannot equal active release")
        return self
