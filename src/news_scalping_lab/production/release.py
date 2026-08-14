"""Final validation and atomic activation of isolated production releases."""

from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
from datetime import timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.brain.audit import audit_brain
from news_scalping_lab.config import load_settings
from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    MemoryCellSnapshotManifest,
)
from news_scalping_lab.contracts.models import BrainManifest
from news_scalping_lab.contracts.production import (
    PRODUCTION_RELEASE_POLICY_VERSION,
    ProductionActivationAttestation,
    ProductionBatchImportReceipt,
    ProductionCurrentPointer,
    ProductionReleaseArtifactManifest,
    ProductionReleaseConfigurationManifest,
    ProductionReleaseManifest,
    ProductionReleaseTransaction,
)
from news_scalping_lab.contracts.shadow_evaluation import ShadowEvaluationManifest
from news_scalping_lab.diagnostics import (
    build_doctor_report,
    production_readiness_report,
)
from news_scalping_lab.evaluation.shadow import ShadowReplayEvaluator
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.memory.company import CompanyMemoryStore
from news_scalping_lab.memory.index import (
    MEMORY_CURRENT_POINTER,
    MEMORY_INDEX_ROOT,
    MEMORY_MANIFEST_FILE,
    MEMORY_SNAPSHOT_DIR,
    ProductionMemoryIndex,
    inspect_current_memory_index,
)
from news_scalping_lab.memory.runtime import create_production_embedding_provider
from news_scalping_lab.policies import EvidencePolicy
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.production.importer import (
    PRODUCTION_RECORD_ARTIFACT_FILE,
    inspect_production_batch_import,
    verify_production_record_artifacts,
)
from news_scalping_lab.retrieval.production_embedding import embedding_identity
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    now_kst,
    read_json,
    relative_to_root,
    sha256_text,
    write_json,
)

PRODUCTION_RELEASES_DIR = Path("production/releases")
PRODUCTION_CURRENT_POINTER = Path("production/current.json")
PRODUCTION_ACTIVATIONS_DIR = Path("production/activations")
PRODUCTION_RELEASE_DOCTOR_FILE = Path(
    "diagnostics/phase9_production_release_readiness.json"
)
PRODUCTION_RELEASE_TRANSACTION_FILE = "production_release_transaction.json"
PRODUCTION_RELEASE_CONFIGURATION_FILE = "production_release_configuration.json"
PRODUCTION_RELEASE_ARTIFACT_FILE = "production_release_artifacts.json"
PRODUCTION_RELEASE_ARTIFACT_VERSION = "production_release_artifacts.v1"
_RELEASE_ARTIFACT_SHA_CACHE: dict[tuple[str, int, int, int, int], str] = {}
_MUTABLE_WAREHOUSE_ARTIFACTS = {
    "company_memory.parquet",
    "daily_outcomes.parquet",
    "predictions.parquet",
}


def finalize_production_release(
    root: Path,
    import_receipt_path: Path,
    shadow_evaluation_path: Path,
    *,
    promotion_key: str,
) -> tuple[ProductionReleaseManifest, Path]:
    resolved_root = root.resolve()
    _validate_hmac_key(promotion_key)
    recovery_receipt_path = _recoverable_release_receipt_path(
        resolved_root,
        import_receipt_path,
    )
    if recovery_receipt_path is not None:
        return _complete_release_transaction(
            resolved_root,
            recovery_receipt_path.parent,
            promotion_key=promotion_key,
        )
    import_inspection = inspect_production_batch_import(
        resolved_root,
        import_receipt_path,
        inventory_attestation_key=promotion_key,
    )
    if import_inspection.get("passed") is not True:
        raise ValueError("production release requires a valid batch import receipt")
    receipt = ProductionBatchImportReceipt.model_validate(
        read_json(import_receipt_path)
    )
    stage_dir = import_receipt_path.resolve().parent
    expected_stage_dir = (
        resolved_root / "production" / "staging" / receipt.import_id
    ).resolve()
    if stage_dir != expected_stage_dir:
        raise ValueError("only an isolated staging import can be finalized")
    project_root = (resolved_root / receipt.release_project_path).resolve()
    if project_root != stage_dir / "project":
        raise ValueError("batch import project path is not canonical")
    try:
        shadow_relative_path = shadow_evaluation_path.resolve().relative_to(
            project_root
        )
    except ValueError as exc:
        raise ValueError(
            "shadow evaluation must be inside the staged release project"
        ) from exc
    projection = _release_projection(
        project_root,
        shadow_evaluation_path.resolve(),
        write_doctor_report=True,
        dotenv_root=resolved_root,
    )
    projection = _projection_with_release_configuration(project_root, projection)
    findings = projection["findings"]
    if findings:
        write_json(
            stage_dir / "release_blockers.json",
            {
                "schema_version": "nslab.production_release_blockers.v1",
                "import_id": receipt.import_id,
                "finding_count": len(findings),
                "findings": findings,
            },
        )
        raise ValueError("production release blockers: " + ", ".join(findings))
    release_identity = _release_identity(
        receipt=receipt,
        projection=projection,
    )
    release_id = "P9REL-" + sha256_text(
        canonical_json(release_identity)
    )[:20].upper()
    release_dir = (resolved_root / PRODUCTION_RELEASES_DIR / release_id).resolve()
    if release_dir.exists():
        raise FileExistsError(f"production release already exists: {release_id}")
    transaction = ProductionReleaseTransaction(
        release_id=release_id,
        import_id=receipt.import_id,
        inventory_id=receipt.inventory_id,
        created_at=now_kst(),
        shadow_evaluation_relative_path=shadow_relative_path.as_posix(),
        source_import_receipt_sha256=_receipt_payload_sha256(receipt),
        release_identity_sha256=sha256_text(canonical_json(release_identity)),
    )
    transaction_path = stage_dir / PRODUCTION_RELEASE_TRANSACTION_FILE
    if transaction_path.exists():
        observed_transaction = ProductionReleaseTransaction.model_validate(
            read_json(transaction_path)
        )
        expected_without_time = transaction.model_dump(
            mode="json",
            exclude={"created_at"},
        )
        observed_without_time = observed_transaction.model_dump(
            mode="json",
            exclude={"created_at"},
        )
        if observed_without_time != expected_without_time:
            raise ValueError("production release transaction conflict")
    else:
        _write_json_atomic(transaction_path, transaction.model_dump(mode="json"))
    release_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_dir.replace(release_dir)
    return _complete_release_transaction(
        resolved_root,
        release_dir,
        promotion_key=promotion_key,
    )


def _recoverable_release_receipt_path(
    root: Path,
    requested_receipt_path: Path,
) -> Path | None:
    requested = requested_receipt_path.resolve()
    releases_root = (root / PRODUCTION_RELEASES_DIR).resolve()
    if requested.is_file():
        try:
            relative = requested.parent.relative_to(releases_root)
        except ValueError:
            return None
        if len(relative.parts) == 1:
            return requested
        return None
    expected_import_id = requested.parent.name
    if not expected_import_id.startswith("P9IMPORT-"):
        return None
    matches: list[Path] = []
    for transaction_path in sorted(
        releases_root.glob(f"*/{PRODUCTION_RELEASE_TRANSACTION_FILE}")
    ):
        try:
            transaction = ProductionReleaseTransaction.model_validate(
                read_json(transaction_path)
            )
        except (OSError, ValueError):
            continue
        if transaction.import_id == expected_import_id:
            matches.append(transaction_path.parent / requested.name)
    if len(matches) > 1:
        raise ValueError("multiple recoverable production release transactions")
    return matches[0] if matches else None


def _complete_release_transaction(
    root: Path,
    release_dir: Path,
    *,
    promotion_key: str,
) -> tuple[ProductionReleaseManifest, Path]:
    transaction_path = release_dir / PRODUCTION_RELEASE_TRANSACTION_FILE
    transaction = ProductionReleaseTransaction.model_validate(
        read_json(transaction_path)
    )
    if release_dir.name != transaction.release_id or release_dir.parent != (
        root / PRODUCTION_RELEASES_DIR
    ).resolve():
        raise ValueError("production release transaction path mismatch")
    manifest_path = release_dir / "production_release_manifest.json"
    if manifest_path.exists():
        inspection = inspect_production_release(
            root,
            manifest_path,
            promotion_key=promotion_key,
        )
        if inspection.get("passed") is not True:
            raise ValueError(
                "existing finalized production release is invalid: "
                + ", ".join(inspection.get("errors", []))
            )
        return (
            ProductionReleaseManifest.model_validate(read_json(manifest_path)),
            manifest_path,
        )

    receipt_path = release_dir / "production_batch_import_receipt.json"
    receipt = ProductionBatchImportReceipt.model_validate(read_json(receipt_path))
    if receipt.import_id != transaction.import_id:
        raise ValueError("production release transaction import mismatch")
    if receipt.inventory_id != transaction.inventory_id:
        raise ValueError("production release transaction inventory mismatch")
    release_project_root = release_dir / "project"
    if (root / receipt.release_project_path).resolve() != release_project_root:
        if _receipt_payload_sha256(receipt) != transaction.source_import_receipt_sha256:
            raise ValueError("production release source receipt hash mismatch")
        receipt = _relocated_import_receipt(
            root,
            receipt,
            release_dir=release_dir,
        )
        _write_json_atomic(receipt_path, receipt.model_dump(mode="json"))
    import_inspection = inspect_production_batch_import(
        root,
        receipt_path,
        inventory_attestation_key=promotion_key,
    )
    if import_inspection.get("passed") is not True:
        raise ValueError("relocated production import receipt is invalid")

    shadow_evaluation_path = (
        release_project_root / transaction.shadow_evaluation_relative_path
    ).resolve()
    try:
        shadow_evaluation_path.relative_to(release_project_root.resolve())
    except ValueError as exc:
        raise ValueError("production release shadow path escapes project") from exc
    projection = _release_projection(
        release_project_root,
        shadow_evaluation_path,
        write_doctor_report=True,
        dotenv_root=root,
    )
    projection = _projection_with_release_configuration(
        release_project_root,
        projection,
    )
    findings = projection["findings"]
    if findings:
        write_json(
            release_dir / "release_blockers.json",
            {
                "schema_version": "nslab.production_release_blockers.v1",
                "import_id": receipt.import_id,
                "finding_count": len(findings),
                "findings": findings,
            },
        )
        raise ValueError(
            "production release blockers after relocation: "
            + ", ".join(findings)
        )
    release_identity = _release_identity(receipt=receipt, projection=projection)
    identity_sha256 = sha256_text(canonical_json(release_identity))
    if transaction.release_identity_sha256 != identity_sha256:
        raise ValueError("production release transaction identity mismatch")
    if transaction.release_id != "P9REL-" + identity_sha256[:20].upper():
        raise ValueError("production release transaction ID mismatch")
    artifacts = _release_artifact_paths(
        release_project_root,
        shadow_evaluation_id=str(projection["shadow_evaluation_id"]),
    )
    release_artifact_payload = _release_artifact_projection(
        release_project_root,
        shadow_evaluation_path=artifacts["shadow_evaluation_manifest"],
        use_cache=True,
    )
    if (
        projection.get("release_artifact_root_sha256")
        != release_artifact_payload.root_sha256
    ):
        raise ValueError("production release artifact projection changed")
    release_artifact_path = release_dir / PRODUCTION_RELEASE_ARTIFACT_FILE
    if release_artifact_path.exists():
        if read_json(release_artifact_path) != release_artifact_payload.model_dump(
            mode="json"
        ):
            raise ValueError("production release artifact manifest conflict")
    else:
        _write_json_atomic(
            release_artifact_path,
            release_artifact_payload.model_dump(mode="json"),
        )
    configuration_path = release_dir / PRODUCTION_RELEASE_CONFIGURATION_FILE
    configuration_payload = _release_configuration_projection(
        release_project_root
    )
    if configuration_path.exists():
        if read_json(configuration_path) != configuration_payload:
            raise ValueError("production release configuration artifact conflict")
    else:
        _write_json_atomic(configuration_path, configuration_payload)
    manifest = ProductionReleaseManifest(
        release_id=transaction.release_id,
        created_at=transaction.created_at,
        release_project_path=relative_to_root(release_project_root, root),
        release_transaction=_reference(root, transaction_path, item_count=1),
        release_configuration=_reference(
            root,
            configuration_path,
            item_count=int(configuration_payload["file_count"]),
        ),
        release_configuration_root_sha256=str(
            configuration_payload["root_sha256"]
        ),
        release_artifacts=_reference(
            root,
            release_artifact_path,
            item_count=release_artifact_payload.artifact_count,
        ),
        release_artifact_projection_version=(
            release_artifact_payload.projection_version
        ),
        release_artifact_root_sha256=release_artifact_payload.root_sha256,
        record_artifact_root_sha256=receipt.record_artifact_root_sha256,
        inventory_manifest=_reference(
            root,
            root / receipt.inventory_manifest.artifact_path,
            item_count=1,
        ),
        import_receipt=_reference(root, receipt_path, item_count=1),
        brain_manifest=_reference(
            root,
            artifacts["brain_manifest"],
            item_count=1,
        ),
        memory_snapshot_manifest=_reference(
            root,
            artifacts["memory_snapshot_manifest"],
            item_count=1,
        ),
        shadow_evaluation_manifest=_reference(
            root,
            artifacts["shadow_evaluation_manifest"],
            item_count=1,
        ),
        doctor_report=_reference(root, artifacts["doctor_report"], item_count=1),
        brain_version=str(projection["brain_version"]),
        memory_snapshot_id=str(projection["memory_snapshot_id"]),
        shadow_evaluation_id=str(projection["shadow_evaluation_id"]),
        llm_provider=str(projection["llm_provider"]),
        llm_model=str(projection["llm_model"]),
        evidence_policy=str(projection["evidence_policy"]),
        web_required=bool(projection["web_required"]),
        codex_cli_version=projection["codex_cli_version"],
        reasoning_effort=projection["reasoning_effort"],
        oauth_health_check_status=projection["oauth_health_check_status"],
        live_agent_call_count=int(projection["live_agent_call_count"]),
        embedding_provider=str(projection["embedding_provider"]),
        embedding_model=str(projection["embedding_model"]),
        embedding_revision=projection["embedding_revision"],
        embedding_artifact_sha256=projection["embedding_artifact_sha256"],
        embedding_dimensions=int(projection["embedding_dimensions"]),
        embedding_normalization=projection["embedding_normalization"],
        embedding_device=projection["embedding_device"],
        embedding_fallback_policy=str(
            projection["embedding_fallback_policy"]
        ),
        web_provider=str(projection["web_provider"]),
        price_provider=str(projection["price_provider"]),
        audit_results=dict(projection["audit_results"]),
        finding_count=0,
        findings=[],
        production_ready=True,
    )
    _write_json_atomic(manifest_path, manifest.model_dump(mode="json"))
    inspection = inspect_production_release(
        root,
        manifest_path,
        promotion_key=promotion_key,
    )
    if inspection.get("passed") is not True:
        raise ValueError(
            "finalized production release failed inspection: "
            + ", ".join(inspection.get("errors", []))
        )
    return manifest, manifest_path


def inspect_production_release(
    root: Path,
    manifest_path: Path,
    *,
    promotion_key: str,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    errors: list[str] = []
    try:
        manifest = ProductionReleaseManifest.model_validate(read_json(manifest_path))
    except (OSError, ValueError) as exc:
        return {
            "schema_version": "nslab.production_release_inspection.v1",
            "passed": False,
            "errors": [f"production_release_manifest_invalid:{exc}"],
        }
    release_dir = (
        resolved_root / PRODUCTION_RELEASES_DIR / manifest.release_id
    ).resolve()
    if manifest_path.resolve() != release_dir / "production_release_manifest.json":
        errors.append("production_release_manifest_path_mismatch")
    project_root = (resolved_root / manifest.release_project_path).resolve()
    if project_root != release_dir / "project":
        errors.append("production_release_project_path_mismatch")
    references = {
        "release_transaction": manifest.release_transaction,
        "release_configuration": manifest.release_configuration,
        "release_artifacts": manifest.release_artifacts,
        "inventory_manifest": manifest.inventory_manifest,
        "import_receipt": manifest.import_receipt,
        "brain_manifest": manifest.brain_manifest,
        "memory_snapshot_manifest": manifest.memory_snapshot_manifest,
        "shadow_evaluation_manifest": manifest.shadow_evaluation_manifest,
        "doctor_report": manifest.doctor_report,
    }
    paths = {
        label: _verify_reference(resolved_root, reference, errors, label)
        for label, reference in references.items()
    }
    receipt_path = paths["import_receipt"]
    if receipt_path is not None:
        import_inspection = inspect_production_batch_import(
            resolved_root,
            receipt_path,
            inventory_attestation_key=promotion_key,
        )
        if import_inspection.get("passed") is not True:
            errors.append("production_release_import_invalid")
        receipt = ProductionBatchImportReceipt.model_validate(read_json(receipt_path))
    else:
        receipt = None
    transaction_path = paths["release_transaction"]
    transaction: ProductionReleaseTransaction | None = None
    if transaction_path is not None:
        try:
            transaction = ProductionReleaseTransaction.model_validate(
                read_json(transaction_path)
            )
        except (OSError, ValueError):
            errors.append("production_release_transaction_invalid")
        else:
            if transaction_path != release_dir / PRODUCTION_RELEASE_TRANSACTION_FILE:
                errors.append("production_release_transaction_path_mismatch")
            if transaction.release_id != manifest.release_id:
                errors.append("production_release_transaction_release_id_mismatch")
            if transaction.created_at != manifest.created_at:
                errors.append("production_release_transaction_created_at_mismatch")
            if receipt is not None:
                if transaction.import_id != receipt.import_id:
                    errors.append("production_release_transaction_import_id_mismatch")
                if transaction.inventory_id != receipt.inventory_id:
                    errors.append("production_release_transaction_inventory_id_mismatch")
                source_receipt = _stage_form_import_receipt(
                    resolved_root,
                    receipt,
                )
                if (
                    transaction.source_import_receipt_sha256
                    != _receipt_payload_sha256(source_receipt)
                ):
                    errors.append("production_release_transaction_receipt_mismatch")
    configuration_path = paths["release_configuration"]
    if configuration_path is not None:
        if configuration_path != release_dir / PRODUCTION_RELEASE_CONFIGURATION_FILE:
            errors.append("production_release_configuration_path_mismatch")
        try:
            configuration_payload = (
                ProductionReleaseConfigurationManifest.model_validate(
                    read_json(configuration_path)
                ).model_dump(mode="json")
            )
            expected_configuration = _release_configuration_projection(project_root)
        except (OSError, ValueError):
            errors.append("production_release_configuration_invalid")
        else:
            if configuration_payload != expected_configuration:
                errors.append("production_release_configuration_projection_mismatch")
            if (
                manifest.release_configuration_root_sha256
                != expected_configuration["root_sha256"]
            ):
                errors.append("production_release_configuration_root_mismatch")
            if (
                manifest.release_configuration.item_count
                != expected_configuration["file_count"]
            ):
                errors.append("production_release_configuration_count_mismatch")
    errors.extend(
        _release_artifact_errors(
            resolved_root,
            release_dir=release_dir,
            project_root=project_root,
            manifest=manifest,
            manifest_path=paths.get("release_artifacts"),
            shadow_evaluation_path=paths.get("shadow_evaluation_manifest"),
            use_cache=False,
        )
    )
    shadow_path = paths["shadow_evaluation_manifest"]
    projection: dict[str, Any] | None = None
    if project_root.is_dir() and shadow_path is not None:
        projection = _release_projection(
            project_root,
            shadow_path,
            write_doctor_report=False,
            dotenv_root=resolved_root,
        )
        projection = _projection_with_release_configuration(
            project_root,
            projection,
        )
        errors.extend(
            f"production_release_projection:{finding}"
            for finding in projection["findings"]
        )
        expected_fields = {
            "brain_version": projection["brain_version"],
            "memory_snapshot_id": projection["memory_snapshot_id"],
            "shadow_evaluation_id": projection["shadow_evaluation_id"],
            "llm_provider": projection["llm_provider"],
            "llm_model": projection["llm_model"],
            "evidence_policy": projection["evidence_policy"],
            "web_required": projection["web_required"],
            "codex_cli_version": projection["codex_cli_version"],
            "reasoning_effort": projection["reasoning_effort"],
            "oauth_health_check_status": projection[
                "oauth_health_check_status"
            ],
            "live_agent_call_count": projection["live_agent_call_count"],
            "embedding_provider": projection["embedding_provider"],
            "embedding_model": projection["embedding_model"],
            "embedding_revision": projection["embedding_revision"],
            "embedding_artifact_sha256": projection[
                "embedding_artifact_sha256"
            ],
            "embedding_dimensions": projection["embedding_dimensions"],
            "embedding_normalization": projection[
                "embedding_normalization"
            ],
            "embedding_device": projection["embedding_device"],
            "embedding_fallback_policy": projection[
                "embedding_fallback_policy"
            ],
            "web_provider": projection["web_provider"],
            "price_provider": projection["price_provider"],
            "release_artifact_root_sha256": projection[
                "release_artifact_root_sha256"
            ],
            "release_artifact_projection_version": projection[
                "release_artifact_projection_version"
            ],
            "audit_results": projection["audit_results"],
        }
        actual = manifest.model_dump(mode="json")
        for field, expected in expected_fields.items():
            if actual.get(field) != expected:
                errors.append(f"production_release_{field}_mismatch")
    if receipt is not None and projection is not None:
        release_identity = _release_identity(receipt=receipt, projection=projection)
        identity_sha256 = sha256_text(canonical_json(release_identity))
        expected_id = "P9REL-" + identity_sha256[:20].upper()
        if manifest.release_id != expected_id:
            errors.append("production_release_id_mismatch")
        if transaction is not None:
            if transaction.release_identity_sha256 != identity_sha256:
                errors.append("production_release_transaction_identity_mismatch")
            shadow_path = paths["shadow_evaluation_manifest"]
            if shadow_path is not None:
                try:
                    shadow_relative = shadow_path.relative_to(project_root).as_posix()
                except ValueError:
                    errors.append("production_release_shadow_path_escape")
                else:
                    if (
                        transaction.shadow_evaluation_relative_path
                        != shadow_relative
                    ):
                        errors.append("production_release_transaction_shadow_mismatch")
    if transaction is not None:
        if as_kst(transaction.created_at) > now_kst() + timedelta(minutes=5):
            errors.append("production_release_transaction_created_in_future")
        if receipt is not None and transaction.created_at < receipt.completed_at:
            errors.append("production_release_transaction_predates_import")
    expected_paths = _release_artifact_paths(
        project_root,
        shadow_evaluation_id=manifest.shadow_evaluation_id,
    )
    for label in (
        "brain_manifest",
        "memory_snapshot_manifest",
        "shadow_evaluation_manifest",
        "doctor_report",
    ):
        path = paths[label]
        if path is not None and path != expected_paths[label]:
            errors.append(f"production_release_{label}_path_mismatch")
    if receipt_path is not None and receipt_path != (
        release_dir / "production_batch_import_receipt.json"
    ):
        errors.append("production_release_import_receipt_path_mismatch")
    if receipt is not None and manifest.inventory_manifest != receipt.inventory_manifest:
        errors.append("production_release_inventory_reference_mismatch")
    if (
        receipt is not None
        and manifest.record_artifact_root_sha256
        != receipt.record_artifact_root_sha256
    ):
        errors.append("production_release_record_artifact_root_mismatch")
    for label, reference in references.items():
        if label in {"release_configuration", "release_artifacts"}:
            continue
        if reference.item_count != 1:
            errors.append(f"production_release_{label}_item_count_mismatch")
    return {
        "schema_version": "nslab.production_release_inspection.v1",
        "release_id": manifest.release_id,
        "passed": not errors and manifest.production_ready,
        "production_ready": not errors and manifest.production_ready,
        "errors": sorted(set(errors)),
    }


def activate_production_release(
    root: Path,
    manifest_path: Path,
    *,
    promotion_key: str,
) -> tuple[ProductionCurrentPointer, Path]:
    resolved_root = root.resolve()
    _validate_hmac_key(promotion_key)
    inspection = inspect_production_release(
        resolved_root,
        manifest_path,
        promotion_key=promotion_key,
    )
    if inspection.get("passed") is not True:
        raise ValueError("only a fully inspected production release can be activated")
    manifest = ProductionReleaseManifest.model_validate(read_json(manifest_path))
    previous_release_id: str | None = None
    current_path = resolved_root / PRODUCTION_CURRENT_POINTER
    if current_path.exists():
        previous = inspect_current_production_release(
            resolved_root,
            promotion_key=promotion_key,
            deep=False,
        )
        if previous.get("passed") is not True:
            raise ValueError("existing production pointer is invalid")
        previous_release_id = str(previous["release_id"])
        if previous_release_id == manifest.release_id:
            raise ValueError("production release is already active")
    activated_at = now_kst()
    if activated_at < manifest.created_at:
        raise ValueError("production release cannot be activated before creation")
    unsigned_payload = {
        "schema_version": "nslab.production_current_pointer.v1",
        "release_id": manifest.release_id,
        "activated_at": activated_at.isoformat(),
        "previous_release_id": previous_release_id,
        "release_project_path": manifest.release_project_path,
        "release_manifest": _reference(
            resolved_root,
            manifest_path,
            item_count=1,
        ).model_dump(mode="json"),
    }
    commitment = sha256_text(canonical_json(unsigned_payload))
    attestation = ProductionActivationAttestation(
        issued_at=activated_at,
        key_id=sha256_text(promotion_key)[:16],
        commitment_sha256=commitment,
        signature=hmac.new(
            promotion_key.encode("utf-8"),
            commitment.encode("ascii"),
            hashlib.sha256,
        ).hexdigest(),
    )
    pointer = ProductionCurrentPointer.model_validate(
        {**unsigned_payload, "attestation": attestation.model_dump(mode="json")}
    )
    current_path.parent.mkdir(parents=True, exist_ok=True)
    activation_path = _activation_history_path(resolved_root, pointer)
    if activation_path.exists():
        observed = ProductionCurrentPointer.model_validate(read_json(activation_path))
        if observed != pointer:
            raise ValueError("production activation history conflict")
    else:
        _write_json_atomic(activation_path, pointer.model_dump(mode="json"))
    _write_json_atomic(current_path, pointer.model_dump(mode="json"))
    return pointer, current_path


def rollback_production_release(
    root: Path,
    target_release_id: str,
    *,
    promotion_key: str,
) -> tuple[ProductionCurrentPointer, Path]:
    resolved_root = root.resolve()
    current = inspect_current_production_release(
        resolved_root,
        promotion_key=promotion_key,
        deep=False,
    )
    if current.get("passed") is not True:
        raise ValueError("current production release is invalid")
    if current.get("release_id") == target_release_id:
        raise ValueError("rollback target is already active")
    manifest_path = (
        resolved_root
        / PRODUCTION_RELEASES_DIR
        / target_release_id
        / "production_release_manifest.json"
    )
    return activate_production_release(
        resolved_root,
        manifest_path,
        promotion_key=promotion_key,
    )


def inspect_current_production_release(
    root: Path,
    *,
    promotion_key: str,
    deep: bool = True,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    pointer_path = resolved_root / PRODUCTION_CURRENT_POINTER
    errors: list[str] = []
    try:
        pointer = ProductionCurrentPointer.model_validate(read_json(pointer_path))
    except (OSError, ValueError) as exc:
        return {
            "schema_version": "nslab.production_current_inspection.v1",
            "passed": False,
            "errors": [f"production_current_pointer_invalid:{exc}"],
        }
    manifest_path = _verify_reference(
        resolved_root,
        pointer.release_manifest,
        errors,
        "current_release_manifest",
    )
    expected_release_dir = (
        resolved_root / PRODUCTION_RELEASES_DIR / pointer.release_id
    ).resolve()
    project_root = (resolved_root / pointer.release_project_path).resolve()
    if project_root != expected_release_dir / "project":
        errors.append("production_current_project_path_mismatch")
    if manifest_path != expected_release_dir / "production_release_manifest.json":
        errors.append("production_current_manifest_path_mismatch")
    if not _verify_pointer_attestation(pointer, promotion_key):
        errors.append("production_current_attestation_invalid")
    if pointer.release_manifest.item_count != 1:
        errors.append("production_current_manifest_item_count_mismatch")
    history_path = _activation_history_path(resolved_root, pointer)
    try:
        history_pointer = ProductionCurrentPointer.model_validate(
            read_json(history_path)
        )
    except (OSError, ValueError):
        errors.append("production_current_activation_history_invalid")
    else:
        if history_pointer != pointer:
            errors.append("production_current_activation_history_mismatch")
    if manifest_path is not None:
        try:
            manifest = ProductionReleaseManifest.model_validate(
                read_json(manifest_path)
            )
        except (OSError, ValueError):
            errors.append("production_current_release_manifest_invalid")
        else:
            if manifest.release_id != pointer.release_id:
                errors.append("production_current_release_id_mismatch")
            if manifest.release_project_path != pointer.release_project_path:
                errors.append("production_current_release_project_mismatch")
            if not manifest.production_ready:
                errors.append("production_current_release_not_ready")
            if pointer.activated_at < manifest.created_at:
                errors.append("production_current_activation_predates_release")
            errors.extend(
                _fast_active_release_errors(
                    resolved_root,
                    manifest,
                    expected_release_dir,
                    project_root,
                    promotion_key=promotion_key,
                )
            )
    if pointer.previous_release_id is not None:
        previous_manifest_path = (
            resolved_root
            / PRODUCTION_RELEASES_DIR
            / pointer.previous_release_id
            / "production_release_manifest.json"
        )
        if not previous_manifest_path.is_file():
            errors.append("production_current_previous_release_missing")
    if deep and manifest_path is not None:
        release_inspection = inspect_production_release(
            resolved_root,
            manifest_path,
            promotion_key=promotion_key,
        )
        if release_inspection.get("passed") is not True:
            errors.append("production_current_release_deep_inspection_failed")
    return {
        "schema_version": "nslab.production_current_inspection.v1",
        "release_id": pointer.release_id,
        "release_project_path": pointer.release_project_path,
        "passed": not errors,
        "errors": sorted(set(errors)),
    }


def resolve_active_production_root(
    root: Path,
    *,
    promotion_key: str,
) -> Path:
    inspection = inspect_current_production_release(
        root,
        promotion_key=promotion_key,
        deep=False,
    )
    if inspection.get("passed") is not True:
        raise ValueError(
            "active production release is invalid: "
            + ", ".join(inspection.get("errors", []))
        )
    return (root.resolve() / str(inspection["release_project_path"])).resolve()


def _fast_active_release_errors(
    root: Path,
    manifest: ProductionReleaseManifest,
    release_dir: Path,
    project_root: Path,
    *,
    promotion_key: str,
) -> list[str]:
    errors: list[str] = []
    settings = load_settings(
        project_root,
        resolve_production=False,
        dotenv_root=root,
    )
    expected_provider_fields = {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm.model,
        "evidence_policy": settings.evidence_policy.value,
        "web_required": False,
        "web_provider": settings.web_provider,
        "price_provider": settings.price_provider,
        "embedding_fallback_policy": (
            settings.event_cluster_fallback_policy.value
        ),
    }
    manifest_payload = manifest.model_dump(mode="json")
    for field, expected_value in expected_provider_fields.items():
        if manifest_payload.get(field) != expected_value:
            errors.append(f"production_current_{field}_mismatch")
    selected_embedding = settings.embedding_provider.strip().lower()
    expected_embedding_provider = (
        "local-production" if selected_embedding == "auto" else selected_embedding
    )
    if manifest.embedding_provider != expected_embedding_provider:
        errors.append("production_current_embedding_provider_mismatch")
    if (
        expected_embedding_provider == "local-production"
        and (
            manifest.embedding_revision != settings.local_embedding_revision
            or settings.local_embedding_model not in manifest.embedding_model
            or settings.local_embedding_revision not in manifest.embedding_model
        )
    ):
        errors.append("production_current_embedding_model_mismatch")
    references = {
        "release_transaction": manifest.release_transaction,
        "release_configuration": manifest.release_configuration,
        "release_artifacts": manifest.release_artifacts,
        "inventory_manifest": manifest.inventory_manifest,
        "import_receipt": manifest.import_receipt,
        "brain_manifest": manifest.brain_manifest,
        "memory_snapshot_manifest": manifest.memory_snapshot_manifest,
        "shadow_evaluation_manifest": manifest.shadow_evaluation_manifest,
        "doctor_report": manifest.doctor_report,
    }
    paths = {
        label: _verify_reference(root, reference, errors, f"current_{label}")
        for label, reference in references.items()
    }
    canonical_paths = {
        "release_transaction": release_dir / PRODUCTION_RELEASE_TRANSACTION_FILE,
        "release_configuration": release_dir
        / PRODUCTION_RELEASE_CONFIGURATION_FILE,
        "release_artifacts": release_dir / PRODUCTION_RELEASE_ARTIFACT_FILE,
        "import_receipt": release_dir / "production_batch_import_receipt.json",
    }
    try:
        canonical_paths.update(
            _release_artifact_paths(
                project_root,
                shadow_evaluation_id=manifest.shadow_evaluation_id,
            )
        )
    except (OSError, ValueError):
        errors.append("production_current_release_artifact_paths_invalid")
    for label, expected_path in canonical_paths.items():
        observed_path = paths.get(label)
        if observed_path is not None and observed_path != expected_path:
            errors.append(f"production_current_{label}_path_mismatch")
    configuration_path = paths.get("release_configuration")
    if configuration_path is not None:
        try:
            observed_configuration = (
                ProductionReleaseConfigurationManifest.model_validate(
                    read_json(configuration_path)
                ).model_dump(mode="json")
            )
            expected_configuration = _release_configuration_projection(project_root)
        except (OSError, ValueError):
            errors.append("production_current_release_configuration_invalid")
        else:
            if observed_configuration != expected_configuration:
                errors.append(
                    "production_current_release_configuration_projection_mismatch"
                )
            if (
                manifest.release_configuration_root_sha256
                != expected_configuration["root_sha256"]
            ):
                errors.append("production_current_release_configuration_root_mismatch")
            if (
                manifest.release_configuration.item_count
                != expected_configuration["file_count"]
            ):
                errors.append("production_current_release_configuration_count_mismatch")
    errors.extend(
        _release_artifact_errors(
            root,
            release_dir=release_dir,
            project_root=project_root,
            manifest=manifest,
            manifest_path=paths.get("release_artifacts"),
            shadow_evaluation_path=paths.get("shadow_evaluation_manifest"),
            use_cache=True,
        )
    )
    receipt_path = paths.get("import_receipt")
    if receipt_path is not None:
        try:
            receipt = ProductionBatchImportReceipt.model_validate(
                read_json(receipt_path)
            )
        except (OSError, ValueError):
            errors.append("production_current_release_receipt_invalid")
        else:
            if receipt.inventory_manifest != manifest.inventory_manifest:
                errors.append("production_current_release_inventory_mismatch")
            if (
                receipt.record_artifact_root_sha256
                != manifest.record_artifact_root_sha256
            ):
                errors.append("production_current_record_artifact_root_mismatch")
            errors.extend(
                verify_production_record_artifacts(
                    root,
                    project_root=project_root,
                    reference=receipt.record_artifacts,
                    expected_root_sha256=receipt.record_artifact_root_sha256,
                    record_count=receipt.imported_record_count,
                    use_cache=True,
                )
            )
    errors.extend(
        CompanyMemoryStore(project_root, create=False).production_integrity_errors(
            attestation_key=promotion_key
        )
    )
    return errors


def _release_projection(
    project_root: Path,
    shadow_evaluation_path: Path,
    *,
    write_doctor_report: bool,
    dotenv_root: Path,
) -> dict[str, Any]:
    findings: list[str] = []
    settings = load_settings(
        project_root,
        resolve_production=False,
        dotenv_root=dotenv_root,
    )
    if settings.evidence_policy is not EvidencePolicy.CSV_MEMORY_ONLY_STRICT:
        findings.append("evidence_policy_not_csv_memory_only_strict")
    if settings.web_provider.strip().lower() != "disabled":
        findings.append("strict_evidence_web_provider_not_disabled")
    if settings.event_cluster_fallback_policy.value != "fail-closed":
        findings.append("production_embedding_not_fail_closed")
    company_memory_errors = CompanyMemoryStore(
        project_root,
        create=False,
    ).production_integrity_errors(
        attestation_key=settings.env_value(
            "NSLAB_PRODUCTION_PROMOTION_HMAC_KEY"
        )
    )
    if company_memory_errors:
        findings.append("company_memory_integrity_failed")
    brain_path = project_root / "brain" / "current" / "brain_manifest.json"
    try:
        brain = BrainManifest.model_validate(read_json(brain_path))
    except (OSError, ValueError) as exc:
        raise ValueError(f"production brain manifest is invalid: {exc}") from exc
    if (
        brain.evidence_policy != settings.evidence_policy.value
        or brain.web_provider != settings.web_provider.strip().lower()
        or brain.web_required is not False
    ):
        findings.append("brain_evidence_policy_identity_mismatch")
    if settings.llm_provider.strip().lower() in {"codex-oauth", "codex_oauth"} and (
        brain.llm_provider != settings.llm_provider
        or brain.live_agent_call_count < 1
        or brain.oauth_health_check_status != "PASS"
    ):
        findings.append("brain_codex_oauth_identity_not_ready")
    brain_inspection = audit_brain(project_root, deep=True)
    if brain_inspection.get("passed") is not True:
        findings.append("brain_deep_audit_failed")
    memory_inspection = inspect_current_memory_index(project_root)
    memory_payload = memory_inspection.get("manifest")
    if not isinstance(memory_payload, dict):
        raise ValueError("production memory snapshot manifest is missing")
    memory = MemoryCellSnapshotManifest.model_validate(memory_payload)
    if (
        memory_inspection.get("passed") is not True
        or memory.production_ready is not True
    ):
        findings.append("memory_deep_audit_failed")
    llm_provider = create_llm_provider(settings)
    embedding_provider = create_production_embedding_provider(
        settings,
        require_records=True,
        provider_factory=create_llm_provider,
        module_loader=import_module,
    )
    embedding_runtime = embedding_identity(embedding_provider)
    expected_brain_embedding = {
        "embedding_provider": embedding_runtime["embedding_provider"],
        "embedding_model": embedding_runtime["embedding_model"],
        "embedding_revision": embedding_runtime["embedding_revision"],
        "embedding_artifact_sha256": embedding_runtime[
            "embedding_artifact_sha256"
        ],
        "embedding_dimensions": memory.embedding_dimensions,
        "embedding_normalization": embedding_runtime["normalization"],
        "embedding_device": embedding_runtime["device"],
    }
    brain_payload = brain.model_dump(mode="json")
    for field, expected_value in expected_brain_embedding.items():
        if brain_payload.get(field) != expected_value:
            findings.append(f"brain_{field}_identity_mismatch")
    memory_index = ProductionMemoryIndex(
        project_root,
        embedding_provider=embedding_provider,
        production=True,
    )
    price_source = create_price_source(settings)
    shadow_evaluator = ShadowReplayEvaluator(
        project_root,
        pre_registration_key=settings.env_value(
            "NSLAB_SHADOW_EVALUATION_HMAC_KEY"
        ),
        memory_index=memory_index,
        price_source=price_source,
        llm_provider=llm_provider,
        runner_attestation_key=settings.env_value(
            "NSLAB_SHADOW_RUNNER_HMAC_KEY"
        ),
        truth_attestation_key=settings.env_value(
            "NSLAB_SHADOW_TRUTH_HMAC_KEY"
        ),
    )
    shadow = ShadowEvaluationManifest.model_validate(
        read_json(shadow_evaluation_path)
    )
    shadow_inspection = shadow_evaluator.inspect(shadow_evaluation_path)
    if (
        shadow_inspection.get("passed") is not True
        or shadow.production_ready is not True
    ):
        findings.append("shadow_evaluation_not_production_ready")
    doctor = build_doctor_report(settings, production=True)
    readiness = production_readiness_report(doctor, settings)
    if readiness.get("passed") is not True:
        findings.append("doctor_production_failed")
    doctor_path = project_root / PRODUCTION_RELEASE_DOCTOR_FILE
    if write_doctor_report:
        write_json(doctor_path, readiness)
    else:
        try:
            observed_doctor = read_json(doctor_path)
        except (OSError, ValueError):
            findings.append("doctor_report_missing_or_invalid")
        else:
            findings.extend(_sealed_doctor_report_findings(observed_doctor))
    provenance = audit_provenance(project_root, memory_index=memory_index)
    if provenance.get("passed") is not True:
        findings.append("provenance_audit_failed")
    release_artifacts = _release_artifact_projection(
        project_root,
        shadow_evaluation_path=shadow_evaluation_path,
        use_cache=True,
    )
    audit_results = {
        "brain_deep": brain_inspection.get("passed") is True,
        "memory_deep": memory_inspection.get("passed") is True,
        "shadow_evaluation": shadow_inspection.get("passed") is True
        and shadow.production_ready is True,
        "doctor_production": readiness.get("passed") is True,
        "provenance": provenance.get("passed") is True,
    }
    return {
        "brain_version": brain.brain_version,
        "memory_snapshot_id": memory.snapshot_id,
        "shadow_evaluation_id": shadow.evaluation_id,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm.model,
        "evidence_policy": settings.evidence_policy.value,
        "web_required": False,
        "codex_cli_version": brain.codex_cli_version,
        "reasoning_effort": brain.reasoning_effort,
        "oauth_health_check_status": brain.oauth_health_check_status,
        "live_agent_call_count": brain.live_agent_call_count,
        "embedding_provider": embedding_runtime["embedding_provider"],
        "embedding_model": memory.embedding_model,
        "embedding_revision": embedding_runtime["embedding_revision"],
        "embedding_artifact_sha256": embedding_runtime[
            "embedding_artifact_sha256"
        ],
        "embedding_dimensions": memory.embedding_dimensions,
        "embedding_normalization": embedding_runtime["normalization"],
        "embedding_device": embedding_runtime["device"],
        "embedding_fallback_policy": (
            settings.event_cluster_fallback_policy.value
        ),
        "web_provider": settings.web_provider,
        "price_provider": settings.price_provider,
        "audit_results": audit_results,
        "findings": sorted(set(findings)),
        "brain_manifest_sha256": file_sha256(brain_path),
        "memory_manifest_sha256": file_sha256(
            _memory_manifest_path(project_root, memory.snapshot_id)
        ),
        "shadow_manifest_sha256": file_sha256(shadow_evaluation_path),
        "doctor_report_sha256": (
            file_sha256(doctor_path) if doctor_path.is_file() else "0" * 64
        ),
        "release_artifact_root_sha256": release_artifacts.root_sha256,
        "release_artifact_projection_version": (
            release_artifacts.projection_version
        ),
        "release_artifact_count": release_artifacts.artifact_count,
    }


def _release_identity(
    *,
    receipt: ProductionBatchImportReceipt,
    projection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "policy_version": PRODUCTION_RELEASE_POLICY_VERSION,
        "import_id": receipt.import_id,
        "inventory_id": receipt.inventory_id,
        "record_store_generation_sha256": receipt.record_store_generation_sha256,
        "record_corpus_sha256": receipt.record_corpus_sha256,
        "record_artifact_root_sha256": receipt.record_artifact_root_sha256,
        "brain_version": projection["brain_version"],
        "brain_manifest_sha256": projection["brain_manifest_sha256"],
        "memory_snapshot_id": projection["memory_snapshot_id"],
        "memory_manifest_sha256": projection["memory_manifest_sha256"],
        "shadow_evaluation_id": projection["shadow_evaluation_id"],
        "shadow_manifest_sha256": projection["shadow_manifest_sha256"],
        "doctor_report_sha256": projection["doctor_report_sha256"],
        "llm_provider": projection["llm_provider"],
        "llm_model": projection["llm_model"],
        "evidence_policy": projection["evidence_policy"],
        "web_required": projection["web_required"],
        "codex_cli_version": projection["codex_cli_version"],
        "reasoning_effort": projection["reasoning_effort"],
        "oauth_health_check_status": projection[
            "oauth_health_check_status"
        ],
        "live_agent_call_count": projection["live_agent_call_count"],
        "embedding_provider": projection["embedding_provider"],
        "embedding_model": projection["embedding_model"],
        "embedding_revision": projection["embedding_revision"],
        "embedding_artifact_sha256": projection[
            "embedding_artifact_sha256"
        ],
        "embedding_dimensions": projection["embedding_dimensions"],
        "embedding_normalization": projection["embedding_normalization"],
        "embedding_device": projection["embedding_device"],
        "embedding_fallback_policy": projection[
            "embedding_fallback_policy"
        ],
        "web_provider": projection["web_provider"],
        "price_provider": projection["price_provider"],
        "release_artifact_root_sha256": projection[
            "release_artifact_root_sha256"
        ],
        "release_artifact_projection_version": projection[
            "release_artifact_projection_version"
        ],
        "release_configuration_root_sha256": projection[
            "release_configuration_root_sha256"
        ],
    }


def _sealed_doctor_report_findings(report: object) -> list[str]:
    if not isinstance(report, dict):
        return ["doctor_report_invalid"]
    findings: list[str] = []
    if report.get("schema_version") != "nslab.production_readiness.v1":
        findings.append("doctor_report_schema_mismatch")
    if report.get("passed") is not True or report.get("status") != "ready":
        findings.append("doctor_report_not_ready")
    if report.get("finding_count") != 0 or report.get("findings") != []:
        findings.append("doctor_report_contains_findings")
    return findings


def _projection_with_release_configuration(
    project_root: Path,
    projection: dict[str, Any],
) -> dict[str, Any]:
    configuration = _release_configuration_projection(project_root)
    return {
        **projection,
        "release_configuration_root_sha256": configuration["root_sha256"],
        "release_configuration_file_count": configuration["file_count"],
    }


def _release_configuration_projection(project_root: Path) -> dict[str, Any]:
    resolved_project_root = project_root.resolve()
    file_hashes: dict[str, str] = {}
    for directory_name in ("configs", "prompts", "schemas"):
        directory = resolved_project_root / directory_name
        if not directory.is_dir():
            raise ValueError(
                f"production release configuration directory is missing: {directory_name}"
            )
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            resolved_path = path.resolve()
            try:
                relative_path = resolved_path.relative_to(
                    resolved_project_root
                ).as_posix()
            except ValueError as exc:
                raise ValueError(
                    "production release configuration file escapes project root"
                ) from exc
            if path.is_symlink() or resolved_path != path.absolute():
                raise ValueError(
                    "production release configuration cannot contain symlinks"
                )
            file_hashes[relative_path] = file_sha256(resolved_path)
    root_sha256 = sha256_text(canonical_json(file_hashes))
    return ProductionReleaseConfigurationManifest(
        file_count=len(file_hashes),
        file_hashes=file_hashes,
        root_sha256=root_sha256,
    ).model_dump(mode="json")


def _release_artifact_projection(
    project_root: Path,
    *,
    shadow_evaluation_path: Path,
    use_cache: bool,
) -> ProductionReleaseArtifactManifest:
    resolved_project_root = project_root.resolve()
    resolved_shadow_path = shadow_evaluation_path.resolve()
    expected_shadow_root = (
        resolved_project_root
        / "runs"
        / "shadow_evaluation"
        / resolved_shadow_path.parent.name
    ).resolve()
    if (
        resolved_shadow_path.parent != expected_shadow_root
        or resolved_shadow_path.name != "shadow_evaluation_manifest.json"
    ):
        raise ValueError("production shadow evaluation path is not canonical")

    candidate_paths: set[Path] = set()
    for root_name in ("brain", "warehouse"):
        candidate_paths.update(
            path
            for path in (resolved_project_root / root_name).rglob("*")
            if path.is_file()
            and not (
                root_name == "warehouse"
                and path.name in _MUTABLE_WAREHOUSE_ARTIFACTS
            )
        )
    memory_root = resolved_project_root / "memory"
    for path in memory_root.rglob("*"):
        if not path.is_file():
            continue
        relative_memory = path.relative_to(memory_root)
        if relative_memory.parts[0] in {
            "record_index",
            "record_manifests",
            "records",
        }:
            continue
        if (
            relative_memory.parts[0] == "company_memory"
            and not _record_derived_company_memory(path)
        ):
            continue
        if relative_memory.as_posix() == PRODUCTION_RECORD_ARTIFACT_FILE.as_posix():
            continue
        candidate_paths.add(path)
    research_root = resolved_project_root / "research"
    for path in research_root.rglob("*"):
        if not path.is_file():
            continue
        relative_research = path.relative_to(research_root)
        if "raw_blocks" in relative_research.parts:
            continue
        if relative_research.name == "original_bundle.md":
            continue
        if (
            len(relative_research.parts) == 2
            and relative_research.parts[0] == "episodes"
            and relative_research.suffix == ".json"
        ):
            continue
        candidate_paths.add(path)
    candidate_paths.update(
        path for path in expected_shadow_root.rglob("*") if path.is_file()
    )

    artifacts: dict[str, dict[str, Any]] = {}
    for path in sorted(candidate_paths):
        resolved_path = path.resolve()
        try:
            relative_path = resolved_path.relative_to(
                resolved_project_root
            ).as_posix()
        except ValueError as exc:
            raise ValueError("production release artifact escapes project root") from exc
        if path.is_symlink() or resolved_path != path.absolute():
            raise ValueError(
                f"production release artifact cannot be a symlink: {relative_path}"
            )
        stat_before = resolved_path.stat()
        cache_key = (
            relative_path,
            stat_before.st_size,
            stat_before.st_mtime_ns,
            stat_before.st_ctime_ns,
            stat_before.st_ino,
        )
        digest = _RELEASE_ARTIFACT_SHA_CACHE.get(cache_key) if use_cache else None
        if digest is None:
            digest = file_sha256(resolved_path)
            stat_after = resolved_path.stat()
            observed_key = (
                relative_path,
                stat_after.st_size,
                stat_after.st_mtime_ns,
                stat_after.st_ctime_ns,
                stat_after.st_ino,
            )
            if observed_key != cache_key:
                raise ValueError(
                    "production release artifact changed during verification: "
                    f"{relative_path}"
                )
            _RELEASE_ARTIFACT_SHA_CACHE[cache_key] = digest
        artifacts[relative_path] = {
            "sha256": digest,
            "byte_size": stat_before.st_size,
        }
    return ProductionReleaseArtifactManifest(
        projection_version=PRODUCTION_RELEASE_ARTIFACT_VERSION,
        artifact_count=len(artifacts),
        total_byte_size=sum(
            int(artifact["byte_size"]) for artifact in artifacts.values()
        ),
        artifacts=artifacts,
        root_sha256=sha256_text(
            canonical_json(
                {
                    "projection_version": PRODUCTION_RELEASE_ARTIFACT_VERSION,
                    "artifacts": artifacts,
                }
            )
        ),
    )


def _record_derived_company_memory(path: Path) -> bool:
    try:
        payload = read_json(path)
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    provenance = payload.get("provenance")
    return isinstance(provenance, list) and any(
        isinstance(item, dict)
        and item.get("source_type") == "company_memory_delta_record"
        for item in provenance
    )


def _release_artifact_errors(
    root: Path,
    *,
    release_dir: Path,
    project_root: Path,
    manifest: ProductionReleaseManifest,
    manifest_path: Path | None,
    shadow_evaluation_path: Path | None,
    use_cache: bool,
) -> list[str]:
    errors: list[str] = []
    expected_manifest_path = (release_dir / PRODUCTION_RELEASE_ARTIFACT_FILE).resolve()
    if manifest_path != expected_manifest_path:
        errors.append("production_release_artifact_manifest_path_mismatch")
    if manifest_path is None or shadow_evaluation_path is None:
        return errors
    try:
        observed = ProductionReleaseArtifactManifest.model_validate(
            read_json(manifest_path)
        )
        expected = _release_artifact_projection(
            project_root,
            shadow_evaluation_path=shadow_evaluation_path,
            use_cache=use_cache,
        )
    except (OSError, ValueError) as exc:
        errors.append(f"production_release_artifact_projection_invalid:{exc}")
        return errors
    if observed != expected:
        errors.append("production_release_artifact_projection_mismatch")
    if manifest.release_artifacts.item_count != expected.artifact_count:
        errors.append("production_release_artifact_item_count_mismatch")
    if manifest.release_artifact_root_sha256 != expected.root_sha256:
        errors.append("production_release_artifact_root_mismatch")
    if manifest.release_artifact_projection_version != expected.projection_version:
        errors.append("production_release_artifact_projection_version_mismatch")
    try:
        manifest_path.relative_to(root.resolve())
    except ValueError:
        errors.append("production_release_artifact_manifest_outside_root")
    return errors


def _relocated_import_receipt(
    root: Path,
    receipt: ProductionBatchImportReceipt,
    *,
    release_dir: Path,
) -> ProductionBatchImportReceipt:
    project_root = release_dir / "project"
    return receipt.model_copy(
        update={
            "release_project_path": relative_to_root(project_root, root),
            "bundle_results": _reference(
                root,
                release_dir / "bundle_results.jsonl",
                item_count=receipt.bundle_results.item_count,
            ),
            "record_index_manifest": _reference(
                root,
                project_root / "memory" / "record_index" / "manifest.json",
                item_count=1,
            ),
            "record_identity_index": _reference(
                root,
                project_root / "memory" / "record_index" / "by_record_id.json",
                item_count=receipt.imported_record_count,
            ),
            "record_artifacts": _reference(
                root,
                project_root / PRODUCTION_RECORD_ARTIFACT_FILE,
                item_count=receipt.record_artifacts.item_count,
            ),
        }
    )


def _stage_form_import_receipt(
    root: Path,
    receipt: ProductionBatchImportReceipt,
) -> ProductionBatchImportReceipt:
    stage_dir = root / "production" / "staging" / receipt.import_id
    project_root = stage_dir / "project"
    return receipt.model_copy(
        update={
            "release_project_path": relative_to_root(project_root, root),
            "bundle_results": receipt.bundle_results.model_copy(
                update={
                    "artifact_path": relative_to_root(
                        stage_dir / "bundle_results.jsonl",
                        root,
                    )
                }
            ),
            "record_index_manifest": receipt.record_index_manifest.model_copy(
                update={
                    "artifact_path": relative_to_root(
                        project_root / "memory" / "record_index" / "manifest.json",
                        root,
                    )
                }
            ),
            "record_identity_index": receipt.record_identity_index.model_copy(
                update={
                    "artifact_path": relative_to_root(
                        project_root
                        / "memory"
                        / "record_index"
                        / "by_record_id.json",
                        root,
                    )
                }
            ),
            "record_artifacts": receipt.record_artifacts.model_copy(
                update={
                    "artifact_path": relative_to_root(
                        project_root / PRODUCTION_RECORD_ARTIFACT_FILE,
                        root,
                    )
                }
            ),
        }
    )


def _receipt_payload_sha256(receipt: ProductionBatchImportReceipt) -> str:
    return sha256_text(canonical_json(receipt.model_dump(mode="json")))


def _release_artifact_paths(
    project_root: Path,
    *,
    shadow_evaluation_id: str,
) -> dict[str, Path]:
    memory_pointer = read_json(project_root / MEMORY_INDEX_ROOT / MEMORY_CURRENT_POINTER)
    if not isinstance(memory_pointer, dict):
        raise ValueError("production memory current pointer is invalid")
    snapshot_id = memory_pointer.get("snapshot_id")
    manifest_relative_path = memory_pointer.get("manifest_path")
    manifest_sha256 = memory_pointer.get("manifest_sha256")
    if not all(
        isinstance(value, str) and value
        for value in (snapshot_id, manifest_relative_path, manifest_sha256)
    ):
        raise ValueError("production memory current pointer is incomplete")
    memory_manifest_path = _memory_manifest_path(project_root, str(snapshot_id))
    expected_relative_path = relative_to_root(memory_manifest_path, project_root)
    if manifest_relative_path != expected_relative_path:
        raise ValueError("production memory current pointer path is not canonical")
    if (
        not memory_manifest_path.is_file()
        or file_sha256(memory_manifest_path) != manifest_sha256
    ):
        raise ValueError("production memory current pointer hash mismatch")
    return {
        "brain_manifest": project_root / "brain" / "current" / "brain_manifest.json",
        "memory_snapshot_manifest": memory_manifest_path,
        "shadow_evaluation_manifest": project_root
        / "runs"
        / "shadow_evaluation"
        / shadow_evaluation_id
        / "shadow_evaluation_manifest.json",
        "doctor_report": project_root / PRODUCTION_RELEASE_DOCTOR_FILE,
    }


def _memory_manifest_path(project_root: Path, snapshot_id: str) -> Path:
    return (
        project_root
        / MEMORY_INDEX_ROOT
        / MEMORY_SNAPSHOT_DIR
        / snapshot_id
        / MEMORY_MANIFEST_FILE
    )


def _reference(root: Path, path: Path, *, item_count: int) -> ArtifactReference:
    return ArtifactReference(
        artifact_path=relative_to_root(path, root),
        sha256=file_sha256(path),
        item_count=item_count,
    )


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
        errors.append(f"production_release_{label}_outside_root")
        return None
    if not path.is_file():
        errors.append(f"production_release_{label}_missing")
        return None
    if file_sha256(path) != reference.sha256:
        errors.append(f"production_release_{label}_hash_mismatch")
    return path


def _verify_pointer_attestation(
    pointer: ProductionCurrentPointer,
    key_value: str,
) -> bool:
    try:
        _validate_hmac_key(key_value)
    except ValueError:
        return False
    attestation = pointer.attestation
    if attestation.key_id != sha256_text(key_value)[:16]:
        return False
    if as_kst(pointer.activated_at) > now_kst() + timedelta(minutes=5):
        return False
    commitment = sha256_text(
        canonical_json(
            pointer.model_dump(mode="json", exclude={"attestation"})
        )
    )
    signature = hmac.new(
        key_value.encode("utf-8"),
        commitment.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return (
        attestation.commitment_sha256 == commitment
        and hmac.compare_digest(attestation.signature, signature)
    )


def _activation_history_path(
    root: Path,
    pointer: ProductionCurrentPointer,
) -> Path:
    activation_id = "P9ACT-" + sha256_text(
        canonical_json(pointer.model_dump(mode="json"))
    )[:20].upper()
    return root / PRODUCTION_ACTIVATIONS_DIR / f"{activation_id}.json"


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


def _validate_hmac_key(key_value: str) -> None:
    if len(key_value.encode("utf-8")) < 32:
        raise ValueError("production promotion HMAC key must be at least 32 bytes")
