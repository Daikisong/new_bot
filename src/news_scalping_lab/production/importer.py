"""Isolated, bounded-memory batch import for Phase 9 release staging."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.memory_context import ArtifactReference
from news_scalping_lab.contracts.production import (
    PRODUCTION_BATCH_IMPORT_VERSION,
    ProductionBatchImportReceipt,
    ProductionImportInventoryEntry,
    ProductionImportInventoryManifest,
    ProductionRecordArtifactManifest,
)
from news_scalping_lab.production.inventory import (
    inspect_production_import_inventory,
    read_inventory_entries,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.research_import.versioned_bundle import (
    import_versioned_bundle,
    project_versioned_bundle,
)
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    now_kst,
    read_json,
    relative_to_root,
    sha256_text,
    write_json,
)

PRODUCTION_STAGING_DIR = Path("production/staging")
PRODUCTION_RECORD_ARTIFACT_FILE = Path(
    "memory/record_index/production_record_artifacts.json"
)
_RECORD_ARTIFACT_SHA_CACHE: dict[
    tuple[str, int, int, int, int],
    str,
] = {}


def stage_production_batch_import(
    root: Path,
    inventory_manifest_path: Path,
    *,
    inventory_attestation_key: str,
    phase7_transport_key: str | None = None,
) -> tuple[ProductionBatchImportReceipt, Path]:
    resolved_root = root.resolve()
    inventory_inspection = inspect_production_import_inventory(
        resolved_root,
        inventory_manifest_path,
        attestation_key=inventory_attestation_key,
    )
    if (
        inventory_inspection.get("passed") is not True
        or inventory_inspection.get("ready_for_import") is not True
        or inventory_inspection.get("attested") is not True
    ):
        raise ValueError("production import requires a verified, attested inventory")
    inventory = ProductionImportInventoryManifest.model_validate(
        read_json(inventory_manifest_path)
    )
    entries = read_inventory_entries(resolved_root, inventory)
    import_id = _production_import_id(inventory, inventory_manifest_path)
    stage_dir = (resolved_root / PRODUCTION_STAGING_DIR / import_id).resolve()
    if stage_dir.exists():
        existing_receipt_path = stage_dir / "production_batch_import_receipt.json"
        existing_inspection = inspect_production_batch_import(
            resolved_root,
            existing_receipt_path,
            inventory_attestation_key=inventory_attestation_key,
        )
        if existing_inspection.get("passed") is not True:
            raise ValueError("existing production import stage is invalid")
        return (
            ProductionBatchImportReceipt.model_validate(
                read_json(existing_receipt_path)
            ),
            existing_receipt_path,
        )
    work_parent = resolved_root / "production" / ".work"
    work_parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(
        tempfile.mkdtemp(prefix=".p9-", dir=work_parent)
    ).resolve()
    project_root = work_dir / "project"
    _copy_release_configuration(resolved_root, project_root)
    results_path = work_dir / "bundle_results.jsonl"
    failure_path = work_dir / "failure.json"
    started_at = now_kst()
    record_store = BrainRecordStore(project_root)
    existing_record_index: dict[str, dict[str, str]] = {}
    result_rows: list[dict[str, Any]] = []
    try:
        for entry in entries:
            result = import_versioned_bundle(
                resolved_root / entry.repaired_path,
                root=project_root,
                validate=True,
                accepted=True,
                external_quality_gate_path=(
                    resolved_root / entry.quality_gate_path
                ),
                phase7_transport_key=phase7_transport_key,
                record_store=record_store,
                existing_record_index=existing_record_index,
                rebuild_record_indexes=False,
            )
            row = _bundle_result_row(entry, result)
            _validate_bundle_result(entry, row)
            result_rows.append(row)
        results_path.write_bytes(_result_bytes(result_rows))
        record_index = record_store.rebuild_indexes_streaming_fresh()
        _validate_batch_totals(inventory, result_rows, record_index)
        record_artifacts = _record_artifact_projection(
            project_root,
            record_count=int(record_index["record_count"]),
            use_cache=False,
        )
        work_record_artifact_path = project_root / PRODUCTION_RECORD_ARTIFACT_FILE
        write_json(
            work_record_artifact_path,
            record_artifacts.model_dump(mode="json"),
        )
    except Exception as exc:
        write_json(
            failure_path,
            {
                "schema_version": "nslab.production_batch_import_failure.v1",
                "import_id": import_id,
                "inventory_id": inventory.inventory_id,
                "failed_after_bundle_count": len(result_rows),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise
    completed_at = now_kst()
    final_project_root = stage_dir / "project"
    final_results_path = stage_dir / "bundle_results.jsonl"
    work_record_index_manifest_path = (
        project_root / "memory" / "record_index" / "manifest.json"
    )
    work_identity_index_path = (
        project_root / "memory" / "record_index" / "by_record_id.json"
    )
    final_record_index_manifest_path = (
        final_project_root / "memory" / "record_index" / "manifest.json"
    )
    final_identity_index_path = (
        final_project_root / "memory" / "record_index" / "by_record_id.json"
    )
    final_record_artifact_path = (
        final_project_root / PRODUCTION_RECORD_ARTIFACT_FILE
    )
    receipt = ProductionBatchImportReceipt(
        import_id=import_id,
        started_at=started_at,
        completed_at=completed_at,
        inventory_id=inventory.inventory_id,
        inventory_manifest=ArtifactReference(
            artifact_path=relative_to_root(inventory_manifest_path, resolved_root),
            sha256=file_sha256(inventory_manifest_path),
            item_count=1,
        ),
        release_project_path=relative_to_root(final_project_root, resolved_root),
        bundle_results=ArtifactReference(
            artifact_path=relative_to_root(final_results_path, resolved_root),
            sha256=file_sha256(results_path),
            item_count=len(result_rows),
        ),
        record_index_manifest=ArtifactReference(
            artifact_path=relative_to_root(
                final_record_index_manifest_path,
                resolved_root,
            ),
            sha256=file_sha256(work_record_index_manifest_path),
            item_count=1,
        ),
        record_identity_index=ArtifactReference(
            artifact_path=relative_to_root(final_identity_index_path, resolved_root),
            sha256=file_sha256(work_identity_index_path),
            item_count=int(record_index["record_count"]),
        ),
        record_artifacts=ArtifactReference(
            artifact_path=relative_to_root(
                final_record_artifact_path,
                resolved_root,
            ),
            sha256=file_sha256(work_record_artifact_path),
            item_count=record_artifacts.artifact_count,
        ),
        record_artifact_root_sha256=record_artifacts.root_sha256,
        imported_bundle_count=len(result_rows),
        imported_record_count=int(record_index["record_count"]),
        imported_training_eligible_record_count=int(
            record_index["training_eligible_record_count"]
        ),
        quarantined_bundle_count=0,
        import_loss_count=0,
        record_store_generation_sha256=str(
            record_index["generation_root_sha256"]
        ),
        record_corpus_sha256=str(record_index["full_envelope_root_sha256"]),
        passed=True,
        finding_count=0,
        findings=[],
    )
    work_receipt_path = work_dir / "production_batch_import_receipt.json"
    write_json(work_receipt_path, receipt.model_dump(mode="json"))
    stage_dir.parent.mkdir(parents=True, exist_ok=True)
    _publish_completed_stage(work_dir, stage_dir)
    return receipt, stage_dir / "production_batch_import_receipt.json"


def inspect_production_batch_import(
    root: Path,
    receipt_path: Path,
    *,
    inventory_attestation_key: str,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    errors: list[str] = []
    try:
        receipt = ProductionBatchImportReceipt.model_validate(read_json(receipt_path))
    except (OSError, ValueError) as exc:
        return {
            "schema_version": "nslab.production_batch_import_inspection.v1",
            "passed": False,
            "errors": [f"production_batch_import_receipt_invalid:{exc}"],
        }
    receipt_dir = receipt_path.resolve().parent
    expected_staging_dir = (
        resolved_root / PRODUCTION_STAGING_DIR / receipt.import_id
    ).resolve()
    releases_root = (resolved_root / "production" / "releases").resolve()
    in_release = False
    try:
        relative_release = receipt_dir.relative_to(releases_root)
        in_release = len(relative_release.parts) == 1
    except ValueError:
        pass
    if receipt_dir != expected_staging_dir and not in_release:
        errors.append("production_batch_import_receipt_directory_mismatch")
    if receipt_path.resolve() != (
        receipt_dir / "production_batch_import_receipt.json"
    ).resolve():
        errors.append("production_batch_import_receipt_path_mismatch")
    expected_results_path = (receipt_dir / "bundle_results.jsonl").resolve()
    expected_project_root = (receipt_dir / "project").resolve()
    expected_record_manifest_path = (
        expected_project_root / "memory" / "record_index" / "manifest.json"
    ).resolve()
    expected_identity_index_path = (
        expected_project_root / "memory" / "record_index" / "by_record_id.json"
    ).resolve()
    expected_record_artifact_path = (
        expected_project_root / PRODUCTION_RECORD_ARTIFACT_FILE
    ).resolve()
    inventory_path = _verify_reference(
        resolved_root,
        receipt.inventory_manifest,
        errors,
        "inventory_manifest",
    )
    results_path = _verify_reference(
        resolved_root,
        receipt.bundle_results,
        errors,
        "bundle_results",
    )
    record_manifest_path = _verify_reference(
        resolved_root,
        receipt.record_index_manifest,
        errors,
        "record_index_manifest",
    )
    identity_index_path = _verify_reference(
        resolved_root,
        receipt.record_identity_index,
        errors,
        "record_identity_index",
    )
    record_artifact_path = _verify_reference(
        resolved_root,
        receipt.record_artifacts,
        errors,
        "record_artifacts",
    )
    project_root = (resolved_root / receipt.release_project_path).resolve()
    if project_root != expected_project_root:
        errors.append("production_batch_import_project_path_mismatch")
    expected_reference_paths = {
        "bundle_results": (results_path, expected_results_path),
        "record_index_manifest": (
            record_manifest_path,
            expected_record_manifest_path,
        ),
        "record_identity_index": (
            identity_index_path,
            expected_identity_index_path,
        ),
        "record_artifacts": (
            record_artifact_path,
            expected_record_artifact_path,
        ),
    }
    for label, (observed, expected_path) in expected_reference_paths.items():
        if observed is not None and observed != expected_path:
            errors.append(f"production_batch_import_{label}_path_mismatch")
    expected_item_counts = {
        "inventory_manifest": (receipt.inventory_manifest.item_count, 1),
        "record_index_manifest": (receipt.record_index_manifest.item_count, 1),
        "record_identity_index": (
            receipt.record_identity_index.item_count,
            receipt.imported_record_count,
        ),
    }
    for label, (observed_count, expected_count) in expected_item_counts.items():
        if observed_count != expected_count:
            errors.append(f"production_batch_import_{label}_item_count_mismatch")
    inventory: ProductionImportInventoryManifest | None = None
    entries: list[ProductionImportInventoryEntry] = []
    if inventory_path is not None:
        inventory_inspection = inspect_production_import_inventory(
            resolved_root,
            inventory_path,
            attestation_key=inventory_attestation_key,
        )
        if (
            inventory_inspection.get("passed") is not True
            or inventory_inspection.get("attested") is not True
        ):
            errors.append("production_batch_import_inventory_invalid")
        else:
            inventory = ProductionImportInventoryManifest.model_validate(
                read_json(inventory_path)
            )
            entries = read_inventory_entries(resolved_root, inventory)
            if inventory.inventory_id != receipt.inventory_id:
                errors.append("production_batch_import_inventory_id_mismatch")
            expected_inventory_path = (
                resolved_root
                / "runs"
                / "production_import"
                / "inventories"
                / inventory.inventory_id
                / "production_import_inventory.json"
            ).resolve()
            if inventory_path != expected_inventory_path:
                errors.append("production_batch_import_inventory_path_mismatch")
            expected_import_id = _production_import_id(inventory, inventory_path)
            if receipt.import_id != expected_import_id:
                errors.append("production_batch_import_id_mismatch")
            if receipt.started_at < inventory.created_at:
                errors.append("production_batch_import_predates_inventory")
            if (
                inventory.attestation is not None
                and receipt.started_at < inventory.attestation.issued_at
            ):
                errors.append("production_batch_import_predates_attestation")
    if receipt.completed_at > now_kst() + timedelta(minutes=5):
        errors.append("production_batch_import_completed_in_future")
    errors.extend(
        verify_production_record_artifacts(
            resolved_root,
            project_root=project_root,
            reference=receipt.record_artifacts,
            expected_root_sha256=receipt.record_artifact_root_sha256,
            record_count=receipt.imported_record_count,
            use_cache=False,
        )
    )
    result_rows = _read_result_rows(results_path, errors)
    if result_rows is not None:
        if len(result_rows) != receipt.bundle_results.item_count:
            errors.append("production_batch_import_result_count_mismatch")
        if len(entries) != len(result_rows):
            errors.append("production_batch_import_inventory_result_count_mismatch")
        elif entries and project_root.is_dir():
            expected_result_rows = _recomputed_result_rows(
                project_root,
                entries,
                errors,
            )
            if result_rows != expected_result_rows:
                errors.append("production_batch_import_result_projection_mismatch")
    stored_index = _read_dict(record_manifest_path, errors, "record_index_manifest")
    if project_root.is_dir():
        recomputed, expected_identity_sha256 = (
            BrainRecordStore(project_root).inspect_streaming_index_artifacts()
        )
        if stored_index is not None and stored_index != recomputed:
            errors.append("production_batch_import_record_index_projection_mismatch")
        if (
            identity_index_path is not None
            and file_sha256(identity_index_path) != expected_identity_sha256
        ):
            errors.append(
                "production_batch_import_record_identity_projection_mismatch"
            )
        expected_values = {
            "imported_record_count": recomputed.get("record_count"),
            "imported_training_eligible_record_count": recomputed.get(
                "training_eligible_record_count"
            ),
            "record_store_generation_sha256": recomputed.get(
                "generation_root_sha256"
            ),
            "record_corpus_sha256": recomputed.get("full_envelope_root_sha256"),
        }
        actual = receipt.model_dump(mode="json")
        for field, expected_value in expected_values.items():
            if actual.get(field) != expected_value:
                errors.append(f"production_batch_import_{field}_mismatch")
    else:
        errors.append("production_batch_import_project_missing")
    if inventory is not None:
        if receipt.imported_bundle_count != inventory.ready_bundle_count:
            errors.append("production_batch_import_bundle_total_mismatch")
        if receipt.imported_record_count != inventory.ready_record_count:
            errors.append("production_batch_import_record_total_mismatch")
        if (
            receipt.imported_training_eligible_record_count
            != inventory.ready_training_eligible_record_count
        ):
            errors.append("production_batch_import_eligible_total_mismatch")
    return {
        "schema_version": "nslab.production_batch_import_inspection.v1",
        "import_id": receipt.import_id,
        "passed": not errors and receipt.passed,
        "imported_bundle_count": receipt.imported_bundle_count,
        "imported_record_count": receipt.imported_record_count,
        "errors": sorted(set(errors)),
    }


def _copy_release_configuration(root: Path, project_root: Path) -> None:
    project_root.mkdir(parents=True)
    for name in ("configs", "prompts", "schemas"):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, project_root / name)


def _publish_completed_stage(work_dir: Path, stage_dir: Path) -> None:
    work_dir.replace(stage_dir)


def verify_production_record_artifacts(
    root: Path,
    *,
    project_root: Path,
    reference: ArtifactReference,
    expected_root_sha256: str,
    record_count: int,
    use_cache: bool,
) -> list[str]:
    errors: list[str] = []
    ledger_path = (root / reference.artifact_path).resolve()
    expected_ledger_path = (
        project_root / PRODUCTION_RECORD_ARTIFACT_FILE
    ).resolve()
    if ledger_path != expected_ledger_path:
        errors.append("production_record_artifact_path_mismatch")
    if not ledger_path.is_file():
        errors.append("production_record_artifact_manifest_missing")
        return errors
    if file_sha256(ledger_path) != reference.sha256:
        errors.append("production_record_artifact_manifest_hash_mismatch")
    try:
        observed = ProductionRecordArtifactManifest.model_validate(
            read_json(ledger_path)
        )
        expected = _record_artifact_projection(
            project_root,
            record_count=record_count,
            use_cache=use_cache,
        )
    except (OSError, ValueError) as exc:
        errors.append(f"production_record_artifact_projection_invalid:{exc}")
        return errors
    if observed != expected:
        errors.append("production_record_artifact_projection_mismatch")
    if reference.item_count != expected.artifact_count:
        errors.append("production_record_artifact_item_count_mismatch")
    if expected_root_sha256 != expected.root_sha256:
        errors.append("production_record_artifact_root_mismatch")
    return errors


def _record_artifact_projection(
    project_root: Path,
    *,
    record_count: int,
    use_cache: bool,
) -> ProductionRecordArtifactManifest:
    candidate_paths = [
        *sorted((project_root / "memory" / "records").glob("*.jsonl")),
        *sorted((project_root / "memory" / "record_manifests").glob("*.json")),
        project_root / "memory" / "record_index" / "manifest.json",
        project_root / "memory" / "record_index" / "by_record_id.json",
    ]
    artifacts: dict[str, dict[str, Any]] = {}
    for path in candidate_paths:
        if not path.is_file():
            raise ValueError(f"production record artifact is missing: {path}")
        relative_path = path.resolve().relative_to(project_root.resolve()).as_posix()
        stat_before = path.stat()
        cache_key = (
            str(path.resolve()),
            stat_before.st_size,
            stat_before.st_mtime_ns,
            stat_before.st_ctime_ns,
            stat_before.st_ino,
        )
        digest = _RECORD_ARTIFACT_SHA_CACHE.get(cache_key) if use_cache else None
        if digest is None:
            digest = file_sha256(path)
            stat_after = path.stat()
            observed_key = (
                str(path.resolve()),
                stat_after.st_size,
                stat_after.st_mtime_ns,
                stat_after.st_ctime_ns,
                stat_after.st_ino,
            )
            if observed_key != cache_key:
                raise ValueError(
                    f"production record artifact changed during verification: {path}"
                )
            _RECORD_ARTIFACT_SHA_CACHE[cache_key] = digest
        artifacts[relative_path] = {
            "sha256": digest,
            "byte_size": stat_before.st_size,
        }
    root_sha256 = sha256_text(canonical_json(artifacts))
    return ProductionRecordArtifactManifest(
        record_count=record_count,
        artifact_count=len(artifacts),
        total_byte_size=sum(
            int(artifact["byte_size"]) for artifact in artifacts.values()
        ),
        artifacts=artifacts,
        root_sha256=root_sha256,
    )


def _bundle_result_row(entry: ProductionImportInventoryEntry, result: Any) -> dict[str, Any]:
    return {
        "schema_version": "nslab.production_batch_import_bundle_result.v1",
        "ordinal": entry.ordinal,
        "source_manifest_line": entry.source_manifest_line,
        "repaired_path": entry.repaired_path,
        "repaired_sha256": entry.repaired_sha256,
        "quality_gate_sha256": entry.quality_gate_sha256,
        "status": result.status,
        "accepted": result.accepted,
        "episode_id": result.episode_id,
        "record_count": result.record_count,
        "training_eligible_record_count": result.training_eligible_record_count,
        "validation_sha256": sha256_text(canonical_json(result.validation)),
        "validation_passed": result.validation.get("passed") is True,
        "import_loss_audit_passed": result.validation.get(
            "import_loss_audit_passed"
        )
        is True,
    }


def _validate_bundle_result(
    entry: ProductionImportInventoryEntry,
    row: dict[str, Any],
) -> None:
    checks = {
        "status": row.get("status") == "imported",
        "accepted": row.get("accepted") is True,
        "record_count": row.get("record_count") == entry.record_count,
        "eligible_count": row.get("training_eligible_record_count")
        == entry.training_eligible_record_count,
        "validation": row.get("validation_passed") is True,
        "import_loss": row.get("import_loss_audit_passed") is True,
    }
    failed = sorted(label for label, passed in checks.items() if not passed)
    if failed:
        raise ValueError(
            f"production bundle import result mismatch for ordinal {entry.ordinal}: "
            + ", ".join(failed)
        )


def _validate_batch_totals(
    inventory: ProductionImportInventoryManifest,
    rows: list[dict[str, Any]],
    record_index: dict[str, Any],
) -> None:
    checks = {
        "bundle_count": len(rows) == inventory.ready_bundle_count,
        "record_count": record_index.get("record_count")
        == inventory.ready_record_count,
        "eligible_count": record_index.get("training_eligible_record_count")
        == inventory.ready_training_eligible_record_count,
    }
    failed = sorted(label for label, passed in checks.items() if not passed)
    if failed:
        raise ValueError(
            "production batch import totals mismatch: " + ", ".join(failed)
        )


def _production_import_id(
    inventory: ProductionImportInventoryManifest,
    inventory_manifest_path: Path,
) -> str:
    return "P9IMPORT-" + sha256_text(
        canonical_json(
            {
                "inventory_id": inventory.inventory_id,
                "inventory_manifest_sha256": file_sha256(
                    inventory_manifest_path
                ),
                "importer_version": PRODUCTION_BATCH_IMPORT_VERSION,
            }
        )
    )[:20].upper()


def _recomputed_result_rows(
    project_root: Path,
    entries: list[ProductionImportInventoryEntry],
    errors: list[str],
) -> list[dict[str, Any]]:
    episodes_by_source_hash: dict[str, tuple[str, Path, dict[str, Any]]] = {}
    episodes_root = project_root / "research" / "episodes"
    for envelope_path in sorted(episodes_root.glob("*/bundle_envelope.json")):
        try:
            envelope = read_json(envelope_path)
        except (OSError, ValueError):
            errors.append("production_batch_import_bundle_envelope_invalid")
            continue
        if not isinstance(envelope, dict):
            errors.append("production_batch_import_bundle_envelope_non_object")
            continue
        source_hash = envelope.get("raw_bundle_sha256")
        episode_id = envelope.get("episode_id")
        if not isinstance(source_hash, str) or not isinstance(episode_id, str):
            errors.append("production_batch_import_bundle_envelope_identity_invalid")
            continue
        if envelope_path.parent.name != episode_id:
            errors.append("production_batch_import_bundle_envelope_path_mismatch")
            continue
        if source_hash in episodes_by_source_hash:
            errors.append("production_batch_import_duplicate_source_episode")
            continue
        episodes_by_source_hash[source_hash] = (
            episode_id,
            envelope_path.parent,
            envelope,
        )

    expected_rows: list[dict[str, Any]] = []
    for entry in entries:
        observed = episodes_by_source_hash.get(entry.repaired_sha256)
        if observed is None:
            errors.append(
                f"production_batch_import_episode_missing:{entry.ordinal}"
            )
            continue
        episode_id, episode_root, stored_envelope = observed
        original_bundle_path = episode_root / "original_bundle.md"
        validation_path = episode_root / "validation_report.json"
        record_manifest_path = (
            project_root / "memory" / "record_manifests" / f"{episode_id}.json"
        )
        try:
            validation = read_json(validation_path)
            record_manifest = read_json(record_manifest_path)
            projection = project_versioned_bundle(original_bundle_path)
        except (OSError, ValueError):
            errors.append(
                f"production_batch_import_episode_artifact_invalid:{entry.ordinal}"
            )
            continue
        if (
            not original_bundle_path.is_file()
            or file_sha256(original_bundle_path) != entry.repaired_sha256
        ):
            errors.append(
                f"production_batch_import_original_bundle_mismatch:{entry.ordinal}"
            )
        if not isinstance(validation, dict) or not isinstance(record_manifest, dict):
            errors.append(
                f"production_batch_import_episode_projection_invalid:{entry.ordinal}"
            )
            continue
        expected_episode_id = projection.envelope.episode_id
        if episode_id != expected_episode_id:
            errors.append(
                f"production_batch_import_episode_identity_mismatch:{entry.ordinal}"
            )
        if validation != projection.validation:
            errors.append(
                f"production_batch_import_validation_projection_mismatch:{entry.ordinal}"
            )
        expected_records = sorted(
            projection.records,
            key=lambda item: item.record_id,
        )
        records_path = project_root / "memory" / "records" / f"{episode_id}.jsonl"
        expected_record_payload = "".join(
            record.model_dump_json() + "\n" for record in expected_records
        )
        try:
            observed_record_payload = records_path.read_text(encoding="utf-8")
            observed_records = [
                BrainRecordEnvelope.model_validate_json(line)
                for line in observed_record_payload.splitlines()
                if line.strip()
            ]
        except (OSError, ValueError):
            observed_record_payload = ""
            observed_records = []
            errors.append(
                f"production_batch_import_records_invalid:{entry.ordinal}"
            )
        if observed_records != expected_records:
            errors.append(
                f"production_batch_import_records_projection_mismatch:{entry.ordinal}"
            )
        record_counts = Counter(record.record_type for record in projection.records)
        eligible_count = sum(
            1 for record in projection.records if record.training_eligible
        )
        expected_record_manifest = {
            "schema_version": "nslab.record_manifest.v1",
            "episode_id": episode_id,
            "accepted": True,
            "acceptance_status": "accepted",
            "record_count": len(projection.records),
            "training_eligible_record_count": eligible_count,
            "record_counts_by_type": dict(sorted(record_counts.items())),
            "record_ids": [record.record_id for record in projection.records],
            "records_file": records_path.relative_to(project_root).as_posix(),
            "records_sha256": sha256_text(expected_record_payload),
        }
        if (
            record_manifest != expected_record_manifest
            or observed_record_payload != expected_record_payload
        ):
            errors.append(
                f"production_batch_import_record_manifest_mismatch:{entry.ordinal}"
            )
        expected_index = projection.index.model_copy(
            update={
                "record_ids": [record.record_id for record in projection.records],
                "record_count_by_type": dict(sorted(record_counts.items())),
                "training_eligible_record_count": eligible_count,
            }
        ).model_dump(mode="json")
        index_path = episode_root / "normalized_episode_index.json"
        try:
            stored_index = read_json(index_path)
        except (OSError, ValueError):
            stored_index = None
        if stored_index != expected_index:
            errors.append(
                f"production_batch_import_episode_index_mismatch:{entry.ordinal}"
            )
        raw_block_paths: dict[str, str] = {}
        expected_raw_filenames: set[str] = set()
        raw_blocks_valid = True
        for name, payload in sorted(projection.raw_blocks.items()):
            filename = name.replace("/", "__").replace("\\", "__")
            if filename in expected_raw_filenames:
                raw_blocks_valid = False
            expected_raw_filenames.add(filename)
            block_path = episode_root / "raw_blocks" / filename
            raw_block_paths[name] = block_path.relative_to(project_root).as_posix()
            try:
                if block_path.read_text(encoding="utf-8") != payload:
                    raw_blocks_valid = False
            except OSError:
                raw_blocks_valid = False
        observed_raw_filenames = {
            path.name
            for path in (episode_root / "raw_blocks").glob("*")
            if path.is_file()
        }
        if observed_raw_filenames != expected_raw_filenames:
            raw_blocks_valid = False
        if not raw_blocks_valid:
            errors.append(
                f"production_batch_import_raw_blocks_mismatch:{entry.ordinal}"
            )
        expected_envelope = projection.envelope.model_copy(
            update={
                "raw_block_paths": raw_block_paths,
                "normalized_episode_index_path": index_path.relative_to(
                    project_root
                ).as_posix(),
                "record_manifest_path": record_manifest_path.relative_to(
                    project_root
                ).as_posix(),
            }
        ).model_dump(mode="json")
        if stored_envelope != expected_envelope:
            errors.append(
                f"production_batch_import_envelope_projection_mismatch:{entry.ordinal}"
            )
        if (
            len(projection.records) != entry.record_count
            or eligible_count != entry.training_eligible_record_count
        ):
            errors.append(
                f"production_batch_import_inventory_projection_mismatch:{entry.ordinal}"
            )
        expected_rows.append(
            {
                "schema_version": "nslab.production_batch_import_bundle_result.v1",
                "ordinal": entry.ordinal,
                "source_manifest_line": entry.source_manifest_line,
                "repaired_path": entry.repaired_path,
                "repaired_sha256": entry.repaired_sha256,
                "quality_gate_sha256": entry.quality_gate_sha256,
                "status": "imported",
                "accepted": True,
                "episode_id": expected_episode_id,
                "record_count": len(projection.records),
                "training_eligible_record_count": eligible_count,
                "validation_sha256": sha256_text(
                    canonical_json(projection.validation)
                ),
                "validation_passed": projection.validation.get("passed") is True,
                "import_loss_audit_passed": projection.validation.get(
                    "import_loss_audit_passed"
                )
                is True,
            }
        )
    if len(episodes_by_source_hash) != len(entries):
        errors.append("production_batch_import_episode_set_mismatch")
    return expected_rows


def _result_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(canonical_json(row) + "\n" for row in rows).encode("utf-8")


def _read_result_rows(
    path: Path | None,
    errors: list[str],
) -> list[dict[str, Any]] | None:
    if path is None:
        return None
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(
                f"production_batch_import_result_invalid_json:{line_number}"
            )
            continue
        if not isinstance(row, dict):
            errors.append(
                f"production_batch_import_result_non_object:{line_number}"
            )
            continue
        rows.append(row)
    return rows


def _verify_reference(
    root: Path,
    reference: ArtifactReference,
    errors: list[str],
    label: str,
) -> Path | None:
    path = (root / reference.artifact_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        errors.append(f"production_batch_import_{label}_outside_root")
        return None
    if not path.is_file():
        errors.append(f"production_batch_import_{label}_missing")
        return None
    if file_sha256(path) != reference.sha256:
        errors.append(f"production_batch_import_{label}_hash_mismatch")
    return path


def _read_dict(
    path: Path | None,
    errors: list[str],
    label: str,
) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = read_json(path)
    except (OSError, ValueError):
        errors.append(f"production_batch_import_{label}_invalid")
        return None
    if not isinstance(payload, dict):
        errors.append(f"production_batch_import_{label}_non_object")
        return None
    return payload
