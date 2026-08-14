"""Content-addressed verification of the repaired-bundle import inventory."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.memory_context import ArtifactReference
from news_scalping_lab.contracts.production import (
    PRODUCTION_IMPORT_INVENTORY_VERSION,
    ProductionImportInventoryEntry,
    ProductionImportInventoryManifest,
    ProductionInventoryAttestation,
)
from news_scalping_lab.research_import.repair_models import RepairQualityGate
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    now_kst,
    read_json,
    relative_to_root,
    sha256_bytes,
    sha256_text,
    write_json,
)

PRODUCTION_IMPORT_MANIFEST = Path(
    "research/inbox/bundles/repaired/sequential_repair_manifest.v2.jsonl"
)
PRODUCTION_INVENTORY_DIR = Path("runs/production_import/inventories")


@dataclass(frozen=True)
class _InventoryProjection:
    entries: list[ProductionImportInventoryEntry]
    manifest_entry_count: int
    status_counts: dict[str, int]
    declared_ready_bundle_count: int
    findings: list[str]
    source_manifest_sha256: str


def build_production_import_inventory(
    root: Path,
    *,
    source_manifest_path: Path | None = None,
    deep_hash: bool = True,
) -> tuple[ProductionImportInventoryManifest, Path]:
    resolved_root = root.resolve()
    source_path = _source_manifest_path(
        resolved_root,
        source_manifest_path,
    )
    projection = _project_inventory(
        resolved_root,
        source_path,
        deep_hash=deep_hash,
    )
    entries_bytes = _entries_bytes(projection.entries)
    identity = _inventory_identity(
        source_manifest_sha256=projection.source_manifest_sha256,
        entries_sha256=sha256_bytes(entries_bytes),
        manifest_entry_count=projection.manifest_entry_count,
        status_counts=projection.status_counts,
        declared_ready_bundle_count=projection.declared_ready_bundle_count,
        findings=projection.findings,
        deep_hash=deep_hash,
    )
    inventory_id = "P9INV-" + sha256_text(canonical_json(identity))[:20].upper()
    inventory_dir = resolved_root / PRODUCTION_INVENTORY_DIR / inventory_id
    entries_path = inventory_dir / "ready_entries.jsonl"
    manifest_path = inventory_dir / "production_import_inventory.json"
    entries_path.parent.mkdir(parents=True, exist_ok=True)
    _write_immutable_bytes(entries_path, entries_bytes)
    findings = sorted(set(projection.findings))
    roots = _entry_roots(projection.entries)
    manifest = ProductionImportInventoryManifest(
        inventory_id=inventory_id,
        created_at=now_kst(),
        source_manifest=ArtifactReference(
            artifact_path=relative_to_root(source_path, resolved_root),
            sha256=projection.source_manifest_sha256,
            item_count=projection.manifest_entry_count,
        ),
        ready_entries=ArtifactReference(
            artifact_path=relative_to_root(entries_path, resolved_root),
            sha256=file_sha256(entries_path),
            item_count=len(projection.entries),
        ),
        manifest_entry_count=projection.manifest_entry_count,
        status_counts=projection.status_counts,
        declared_ready_bundle_count=projection.declared_ready_bundle_count,
        ready_bundle_count=len(projection.entries),
        ready_record_count=sum(entry.record_count for entry in projection.entries),
        ready_training_eligible_record_count=sum(
            entry.training_eligible_record_count for entry in projection.entries
        ),
        ready_semantic_excluded_record_count=sum(
            entry.semantic_excluded_record_count for entry in projection.entries
        ),
        source_root_sha256=roots["source"],
        repaired_root_sha256=roots["repaired"],
        quality_gate_root_sha256=roots["quality_gate"],
        deep_hash_verified=deep_hash,
        ready_for_import=deep_hash and not findings,
        finding_count=len(findings),
        findings=findings,
    )
    if manifest_path.exists():
        existing = ProductionImportInventoryManifest.model_validate(
            read_json(manifest_path)
        )
        comparable_fields = {"created_at", "attestation"}
        if existing.model_dump(
            mode="json",
            exclude=comparable_fields,
        ) != manifest.model_dump(mode="json", exclude=comparable_fields):
            raise ValueError("existing production import inventory conflicts with content ID")
        return existing, manifest_path
    _write_json_atomic(manifest_path, manifest.model_dump(mode="json"))
    return manifest, manifest_path


def inspect_production_import_inventory(
    root: Path,
    manifest_path: Path,
    *,
    attestation_key: str | None = None,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    errors: list[str] = []
    try:
        manifest = ProductionImportInventoryManifest.model_validate(
            read_json(manifest_path)
        )
    except (OSError, ValueError) as exc:
        return {
            "schema_version": "nslab.production_import_inventory_inspection.v1",
            "passed": False,
            "ready_for_import": False,
            "attested": False,
            "errors": [f"production_import_inventory_invalid:{exc}"],
        }
    expected_dir = (
        resolved_root / PRODUCTION_INVENTORY_DIR / manifest.inventory_id
    ).resolve()
    if manifest_path.resolve() != (
        expected_dir / "production_import_inventory.json"
    ).resolve():
        errors.append("production_import_inventory_path_mismatch")
    source_path = _resolve_artifact(
        resolved_root,
        manifest.source_manifest.artifact_path,
        errors,
        "source_manifest",
    )
    entries_path = _resolve_artifact(
        resolved_root,
        manifest.ready_entries.artifact_path,
        errors,
        "ready_entries",
    )
    if entries_path is not None and entries_path.resolve() != (
        expected_dir / "ready_entries.jsonl"
    ).resolve():
        errors.append("production_import_entries_path_mismatch")
    if (
        source_path is not None
        and file_sha256(source_path) != manifest.source_manifest.sha256
    ):
        errors.append("production_import_source_manifest_hash_mismatch")
    if (
        entries_path is not None
        and file_sha256(entries_path) != manifest.ready_entries.sha256
    ):
        errors.append("production_import_entries_hash_mismatch")
    projection_built = False
    expected_entries: list[ProductionImportInventoryEntry] = []
    if source_path is not None:
        projection = _project_inventory(
            resolved_root,
            source_path,
            deep_hash=True,
        )
        if manifest.ready_for_import:
            errors.extend(
                f"production_import_source_finding:{finding}"
                for finding in projection.findings
            )
        expected_entries = projection.entries
        expected_identity = _inventory_identity(
            source_manifest_sha256=projection.source_manifest_sha256,
            entries_sha256=sha256_bytes(_entries_bytes(expected_entries)),
            manifest_entry_count=projection.manifest_entry_count,
            status_counts=projection.status_counts,
            declared_ready_bundle_count=projection.declared_ready_bundle_count,
            findings=projection.findings,
            deep_hash=True,
        )
        expected_id = "P9INV-" + sha256_text(
            canonical_json(expected_identity)
        )[:20].upper()
        if expected_id != manifest.inventory_id:
            errors.append("production_import_inventory_id_mismatch")
        roots = _entry_roots(expected_entries)
        expected_values: dict[str, Any] = {
            "source_manifest": {
                "artifact_path": relative_to_root(source_path, resolved_root),
                "sha256": projection.source_manifest_sha256,
                "item_count": projection.manifest_entry_count,
            },
            "manifest_entry_count": projection.manifest_entry_count,
            "status_counts": projection.status_counts,
            "declared_ready_bundle_count": projection.declared_ready_bundle_count,
            "ready_bundle_count": len(expected_entries),
            "ready_record_count": sum(item.record_count for item in expected_entries),
            "ready_training_eligible_record_count": sum(
                item.training_eligible_record_count for item in expected_entries
            ),
            "ready_semantic_excluded_record_count": sum(
                item.semantic_excluded_record_count for item in expected_entries
            ),
            "source_root_sha256": roots["source"],
            "repaired_root_sha256": roots["repaired"],
            "quality_gate_root_sha256": roots["quality_gate"],
            "deep_hash_verified": True,
            "ready_for_import": not projection.findings,
            "finding_count": len(sorted(set(projection.findings))),
            "findings": sorted(set(projection.findings)),
        }
        actual = manifest.model_dump(mode="json")
        for field, expected in expected_values.items():
            actual_value = actual.get(field)
            if field == "source_manifest" and isinstance(actual_value, dict):
                actual_value = {
                    key: actual_value.get(key)
                    for key in ("artifact_path", "sha256", "item_count")
                }
            if actual_value != expected:
                errors.append(f"production_import_{field}_mismatch")
        projection_built = True
    if entries_path is not None and entries_path.read_bytes() != _entries_bytes(
        expected_entries
    ):
        errors.append("production_import_entries_projection_mismatch")
    attested = False
    if manifest.attestation is not None and attestation_key is not None:
        attested = verify_production_inventory_attestation(
            manifest,
            key_value=attestation_key,
        )
        if not attested:
            errors.append("production_import_attestation_invalid")
    elif manifest.attestation is not None:
        errors.append("production_import_attestation_key_required")
    elif attestation_key is not None:
        errors.append("production_import_attestation_missing")
    passed = not errors and projection_built
    return {
        "schema_version": "nslab.production_import_inventory_inspection.v1",
        "inventory_id": manifest.inventory_id,
        "passed": passed,
        "ready_for_import": passed and manifest.ready_for_import,
        "attested": passed and attested,
        "ready_bundle_count": manifest.ready_bundle_count,
        "ready_record_count": manifest.ready_record_count,
        "ready_training_eligible_record_count": (
            manifest.ready_training_eligible_record_count
        ),
        "errors": sorted(set(errors)),
    }


def seal_production_import_inventory(
    root: Path,
    manifest_path: Path,
    *,
    key_value: str,
) -> ProductionImportInventoryManifest:
    _validate_hmac_key(key_value)
    inspection = inspect_production_import_inventory(root, manifest_path)
    if inspection.get("passed") is not True or inspection.get(
        "ready_for_import"
    ) is not True:
        raise ValueError("only a fully verified import inventory can be sealed")
    manifest = ProductionImportInventoryManifest.model_validate(
        read_json(manifest_path)
    )
    if manifest.attestation is not None:
        raise ValueError("production import inventory is already sealed")
    commitment = sha256_text(
        canonical_json(_inventory_attestation_payload(manifest))
    )
    issued_at = now_kst()
    attestation = ProductionInventoryAttestation(
        issued_at=issued_at,
        key_id=sha256_text(key_value)[:16],
        commitment_sha256=commitment,
        signature=hmac.new(
            key_value.encode("utf-8"),
            commitment.encode("ascii"),
            hashlib.sha256,
        ).hexdigest(),
    )
    sealed = manifest.model_copy(update={"attestation": attestation})
    _write_json_atomic(manifest_path, sealed.model_dump(mode="json"))
    return sealed


def verify_production_inventory_attestation(
    manifest: ProductionImportInventoryManifest,
    *,
    key_value: str,
) -> bool:
    try:
        _validate_hmac_key(key_value)
    except ValueError:
        return False
    attestation = manifest.attestation
    if attestation is None or attestation.key_id != sha256_text(key_value)[:16]:
        return False
    if (
        as_kst(attestation.issued_at) < as_kst(manifest.created_at)
        or as_kst(attestation.issued_at) > now_kst() + timedelta(minutes=5)
    ):
        return False
    commitment = sha256_text(
        canonical_json(_inventory_attestation_payload(manifest))
    )
    expected_signature = hmac.new(
        key_value.encode("utf-8"),
        commitment.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return (
        attestation.commitment_sha256 == commitment
        and hmac.compare_digest(attestation.signature, expected_signature)
    )


def read_inventory_entries(
    root: Path,
    manifest: ProductionImportInventoryManifest,
) -> list[ProductionImportInventoryEntry]:
    path = _resolve_artifact(
        root.resolve(),
        manifest.ready_entries.artifact_path,
        [],
        "ready_entries",
    )
    if path is None or file_sha256(path) != manifest.ready_entries.sha256:
        raise ValueError("production import entries artifact is invalid")
    rows: list[ProductionImportInventoryEntry] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        try:
            rows.append(ProductionImportInventoryEntry.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(
                f"production import entry line {line_number} is invalid"
            ) from exc
    if len(rows) != manifest.ready_entries.item_count:
        raise ValueError("production import entry count mismatch")
    return rows


def _project_inventory(
    root: Path,
    source_manifest_path: Path,
    *,
    deep_hash: bool,
) -> _InventoryProjection:
    findings: list[str] = []
    status_counts: Counter[str] = Counter()
    entries: list[ProductionImportInventoryEntry] = []
    manifest_entry_count = 0
    declared_ready_bundle_count = 0
    with source_manifest_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            manifest_entry_count += 1
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                findings.append(f"source_manifest_invalid_json:{line_number}")
                continue
            if not isinstance(row, dict):
                findings.append(f"source_manifest_non_object:{line_number}")
                continue
            status = str(row.get("final_status") or "MISSING")
            status_counts[status] += 1
            is_ready = (
                row.get("ready_for_import") is True
                and status == "REPAIRED_PASS"
            )
            if not is_ready:
                continue
            declared_ready_bundle_count += 1
            entry = _ready_entry(
                root,
                row,
                line_number=line_number,
                ordinal=len(entries),
                deep_hash=deep_hash,
                findings=findings,
            )
            if entry is not None:
                entries.append(entry)
    if not deep_hash:
        findings.append("deep_hash_verification_required")
    _duplicate_findings(entries, findings)
    return _InventoryProjection(
        entries=entries,
        manifest_entry_count=manifest_entry_count,
        status_counts=dict(sorted(status_counts.items())),
        declared_ready_bundle_count=declared_ready_bundle_count,
        findings=sorted(set(findings)),
        source_manifest_sha256=file_sha256(source_manifest_path),
    )


def _ready_entry(
    root: Path,
    row: dict[str, Any],
    *,
    line_number: int,
    ordinal: int,
    deep_hash: bool,
    findings: list[str],
) -> ProductionImportInventoryEntry | None:
    required_strings = (
        "filename_date",
        "source_path",
        "source_sha256",
        "repaired_path",
        "repaired_sha256",
        "quality_gate_path",
        "engine_digest",
    )
    missing = [
        field
        for field in required_strings
        if not isinstance(row.get(field), str) or not str(row[field]).strip()
    ]
    if missing:
        findings.append(
            f"ready_entry_missing_fields:{line_number}:{','.join(sorted(missing))}"
        )
        return None
    paths: dict[str, Path] = {}
    relative_paths: dict[str, str] = {}
    for field in ("source_path", "repaired_path", "quality_gate_path"):
        candidate = Path(str(row[field])).resolve()
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            findings.append(f"ready_entry_path_outside_root:{line_number}:{field}")
            return None
        paths[field] = candidate
        relative_paths[field] = relative
        if not candidate.is_file():
            findings.append(f"ready_entry_file_missing:{line_number}:{field}")
            return None
    try:
        gate = _quality_gate(paths["quality_gate_path"])
    except (OSError, ValueError) as exc:
        findings.append(f"ready_entry_quality_gate_invalid:{line_number}:{exc}")
        return None
    declared_repaired_sha = str(row["repaired_sha256"])
    declared_source_sha = str(row["source_sha256"])
    checks = {
        "gate_passed": gate.passed,
        "gate_ready": gate.ready_for_import_pass,
        "gate_status": gate.final_status == "REPAIRED_PASS",
        "gate_no_blockers": not gate.blockers,
        "gate_repaired_sha": gate.repaired_sha256 == declared_repaired_sha,
        "gate_source_sha": gate.source_sha256 == declared_source_sha,
        "gate_engine": gate.engine_digest == row["engine_digest"],
        "manifest_deep_audit": row.get("deep_audit_passed") is True,
        "manifest_deterministic": row.get("deterministic") is True,
        "manifest_isolated_import": row.get("isolated_import_passed") is True,
        "manifest_store_unchanged": row.get("production_store_unchanged") is True,
        "manifest_not_imported": row.get("production_import_performed") is False,
    }
    for label, passed in checks.items():
        if not passed:
            findings.append(f"ready_entry_contract_failed:{line_number}:{label}")
    if deep_hash:
        if file_sha256(paths["repaired_path"]) != declared_repaired_sha:
            findings.append(f"ready_entry_repaired_hash_mismatch:{line_number}")
        if file_sha256(paths["source_path"]) != declared_source_sha:
            findings.append(f"ready_entry_source_hash_mismatch:{line_number}")
    source_size = paths["source_path"].stat().st_size
    repaired_size = paths["repaired_path"].stat().st_size
    if _int_value(row, "byte_size") != source_size:
        findings.append(f"ready_entry_source_size_mismatch:{line_number}")
    if _int_value(row, "repaired_byte_size") != repaired_size:
        findings.append(f"ready_entry_repaired_size_mismatch:{line_number}")
    try:
        record_count = _int_value(row, "record_count")
        eligible_count = _int_value(row, "training_eligible_record_count")
        excluded_count = _int_value(row, "semantic_excluded_record_count")
    except ValueError as exc:
        findings.append(f"ready_entry_count_invalid:{line_number}:{exc}")
        return None
    importer = gate.importer
    if importer.get("normalized_record_count") != record_count:
        findings.append(f"ready_entry_record_count_mismatch:{line_number}")
    if importer.get("validation_passed") is not True:
        findings.append(f"ready_entry_import_validation_failed:{line_number}")
    if importer.get("import_loss_audit_passed") is not True:
        findings.append(f"ready_entry_import_loss_failed:{line_number}")
    try:
        return ProductionImportInventoryEntry(
            ordinal=ordinal,
            source_manifest_line=line_number,
            filename_date=str(row["filename_date"]),
            source_path=relative_paths["source_path"],
            source_sha256=declared_source_sha,
            source_byte_size=source_size,
            repaired_path=relative_paths["repaired_path"],
            repaired_sha256=declared_repaired_sha,
            repaired_byte_size=repaired_size,
            quality_gate_path=relative_paths["quality_gate_path"],
            quality_gate_sha256=file_sha256(paths["quality_gate_path"]),
            engine_digest=str(row["engine_digest"]),
            record_count=record_count,
            training_eligible_record_count=eligible_count,
            semantic_excluded_record_count=excluded_count,
        )
    except ValueError as exc:
        findings.append(f"ready_entry_contract_invalid:{line_number}:{exc}")
        return None


def _quality_gate(path: Path) -> RepairQualityGate:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("quality gate must be an object")
    if payload.get("schema_version") == "nslab.repair_reviewed_verdict_cache.v1":
        payload = payload.get("quality_gate")
    if not isinstance(payload, dict):
        raise ValueError("quality gate payload is missing")
    return RepairQualityGate.model_validate(payload)


def _duplicate_findings(
    entries: list[ProductionImportInventoryEntry],
    findings: list[str],
) -> None:
    for field in (
        "source_path",
        "source_sha256",
        "repaired_path",
        "repaired_sha256",
    ):
        values = [getattr(entry, field) for entry in entries]
        duplicates = sorted(
            value for value, count in Counter(values).items() if count > 1
        )
        if duplicates:
            findings.append(
                f"ready_entry_duplicate_{field}:{','.join(duplicates[:10])}"
            )


def _entry_roots(entries: list[ProductionImportInventoryEntry]) -> dict[str, str]:
    return {
        "source": sha256_text(
            canonical_json(
                {entry.source_path: entry.source_sha256 for entry in entries}
            )
        ),
        "repaired": sha256_text(
            canonical_json(
                {entry.repaired_path: entry.repaired_sha256 for entry in entries}
            )
        ),
        "quality_gate": sha256_text(
            canonical_json(
                {
                    entry.quality_gate_path: entry.quality_gate_sha256
                    for entry in entries
                }
            )
        ),
    }


def _inventory_identity(
    *,
    source_manifest_sha256: str,
    entries_sha256: str,
    manifest_entry_count: int,
    status_counts: dict[str, int],
    declared_ready_bundle_count: int,
    findings: list[str],
    deep_hash: bool,
) -> dict[str, Any]:
    return {
        "verifier_version": PRODUCTION_IMPORT_INVENTORY_VERSION,
        "source_manifest_sha256": source_manifest_sha256,
        "entries_sha256": entries_sha256,
        "manifest_entry_count": manifest_entry_count,
        "status_counts": status_counts,
        "declared_ready_bundle_count": declared_ready_bundle_count,
        "findings": sorted(set(findings)),
        "deep_hash_verified": deep_hash,
    }


def _entries_bytes(entries: list[ProductionImportInventoryEntry]) -> bytes:
    return "".join(
        canonical_json(entry.model_dump(mode="json")) + "\n" for entry in entries
    ).encode("utf-8")


def _inventory_attestation_payload(
    manifest: ProductionImportInventoryManifest,
) -> dict[str, Any]:
    return manifest.model_dump(mode="json", exclude={"attestation"})


def _source_manifest_path(root: Path, supplied: Path | None) -> Path:
    path = (root / PRODUCTION_IMPORT_MANIFEST) if supplied is None else supplied
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("production import source manifest must be inside project root") from exc
    if not path.is_file():
        raise FileNotFoundError(f"production import source manifest not found: {path}")
    return path


def _resolve_artifact(
    root: Path,
    artifact_path: str,
    errors: list[str],
    label: str,
) -> Path | None:
    candidate = (root / artifact_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        errors.append(f"production_import_{label}_outside_root")
        return None
    if not candidate.is_file():
        errors.append(f"production_import_{label}_missing")
        return None
    return candidate


def _int_value(row: dict[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _validate_hmac_key(key_value: str) -> None:
    if len(key_value.encode("utf-8")) < 32:
        raise ValueError("production inventory HMAC key must be at least 32 bytes")


def _write_immutable_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"immutable production inventory artifact conflict: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        temporary_path.write_bytes(content)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        write_json(temporary_path, payload)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
