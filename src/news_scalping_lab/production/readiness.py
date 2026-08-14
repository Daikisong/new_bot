"""Read-only Phase 9 readiness summary for the current project root."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from news_scalping_lab.config import load_settings
from news_scalping_lab.contracts.production import ProductionImportInventoryManifest
from news_scalping_lab.evaluation.shadow import shadow_replay_readiness
from news_scalping_lab.llm.codex_oauth_provider import codex_login_status
from news_scalping_lab.policies import EvidencePolicy, web_required_for_policy
from news_scalping_lab.production.bootstrap import PROVIDER_IDENTITY_RECEIPT
from news_scalping_lab.production.inventory import (
    PRODUCTION_INVENTORY_DIR,
    verify_production_inventory_attestation,
)
from news_scalping_lab.production.release import inspect_current_production_release
from news_scalping_lab.utils import file_sha256, read_json, relative_to_root


def phase9_production_readiness(root: Path) -> dict[str, Any]:
    resolved_root = root.resolve()
    base_settings = load_settings(resolved_root, resolve_production=False)
    key_value = base_settings.env_value("NSLAB_PRODUCTION_PROMOTION_HMAC_KEY")
    inventory, inventory_path = _latest_inventory(resolved_root)
    blockers: list[str] = []
    inventory_current = False
    inventory_attested = False
    if inventory is None or inventory_path is None:
        blockers.append("production import inventory is missing")
    else:
        source_path = resolved_root / inventory.source_manifest.artifact_path
        entries_path = resolved_root / inventory.ready_entries.artifact_path
        inventory_current = (
            source_path.is_file()
            and entries_path.is_file()
            and file_sha256(source_path) == inventory.source_manifest.sha256
            and file_sha256(entries_path) == inventory.ready_entries.sha256
        )
        if not inventory.ready_for_import:
            blockers.append("production import inventory is not ready")
        if not inventory_current:
            blockers.append("production import inventory source generation is stale")
        if key_value is None:
            blockers.append("production promotion HMAC key is missing")
        else:
            inventory_attested = verify_production_inventory_attestation(
                inventory,
                key_value=key_value,
            )
            if not inventory_attested:
                blockers.append("production import inventory is not attested")
    staged_receipts = sorted(
        (resolved_root / "production" / "staging").glob(
            "*/production_batch_import_receipt.json"
        )
    )
    release_manifests = sorted(
        (resolved_root / "production" / "releases").glob(
            "*/production_release_manifest.json"
        )
    )
    if not staged_receipts and not release_manifests:
        blockers.append("production batch import has not been staged")
    current_pointer = resolved_root / "production" / "current.json"
    if current_pointer.is_file() and key_value is not None:
        current = inspect_current_production_release(
            resolved_root,
            promotion_key=key_value,
            deep=True,
        )
    else:
        current = {
            "passed": False,
            "release_id": None,
            "release_project_path": None,
            "errors": ["production current pointer is missing"],
        }
    runtime_root = resolved_root
    current_project_path = current.get("release_project_path")
    if current.get("passed") is True and isinstance(current_project_path, str):
        candidate_runtime_root = (resolved_root / current_project_path).resolve()
        try:
            candidate_runtime_root.relative_to(resolved_root)
        except ValueError:
            blockers.append("production release project escapes the project root")
        else:
            runtime_root = candidate_runtime_root
    settings = load_settings(
        runtime_root,
        resolve_production=False,
        dotenv_root=resolved_root,
    )
    evidence_policy = EvidencePolicy.parse(settings.evidence_policy)
    web_required = web_required_for_policy(evidence_policy)
    provider_receipt = _read_dict(runtime_root / PROVIDER_IDENTITY_RECEIPT)
    embedding_ready = (
        settings.embedding_provider.strip().lower()
        not in {"", "mock", "deterministic", "deterministic-hash"}
        and provider_receipt.get("embedding_identity") is not None
    )
    web_ready = (
        settings.web_provider.strip().lower() == "disabled"
        if evidence_policy is EvidencePolicy.CSV_MEMORY_ONLY_STRICT
        else settings.web_provider.strip().lower() not in {"", "mock", "disabled"}
    )
    record_index = _read_dict(
        runtime_root / "memory" / "record_index" / "manifest.json"
    )
    current_record_count = _integer(record_index.get("record_count"))
    expected_record_count = inventory.ready_record_count if inventory is not None else None
    if (
        expected_record_count is not None
        and current_record_count != expected_record_count
    ):
        blockers.append("current record store does not match import-ready inventory")
    provider_configured = {
        "llm": settings.llm_provider.strip().lower() not in {"", "mock"},
        "llm_model": settings.llm.model.strip().lower()
        not in {"", "deterministic-mock"},
        "embedding": embedding_ready,
        "web": web_ready,
        "price": settings.price_provider.strip().lower() not in {"", "mock"},
    }
    for label, configured in provider_configured.items():
        if not configured:
            blockers.append(f"production {label} provider is not configured")
    if evidence_policy is not EvidencePolicy.CSV_MEMORY_ONLY_STRICT:
        blockers.append("production evidence policy is not CSV_MEMORY_ONLY_STRICT")
    if settings.event_cluster_fallback_policy.value != "fail-closed":
        blockers.append("production embedding fallback policy is not fail-closed")
    oauth_health: dict[str, Any]
    if settings.llm_provider.strip().lower() in {"codex-oauth", "codex_oauth"}:
        try:
            oauth_health = codex_login_status(settings.codex_command)
        except OSError as exc:
            oauth_health = {
                "logged_in": False,
                "status": "CLI_UNAVAILABLE",
                "error": type(exc).__name__,
            }
        if oauth_health.get("logged_in") is not True:
            blockers.append("Codex OAuth health check is not ready")
    else:
        oauth_health = {"logged_in": None, "status": "not_selected"}
    shadow = shadow_replay_readiness(runtime_root)
    if shadow.get("ready") is not True:
        blockers.append("Phase 8 production shadow gate is not ready")
    if current.get("passed") is not True:
        blockers.append("production release is not active")
    blockers = sorted(set(blockers))
    return {
        "schema_version": "nslab.phase9_production_readiness.v1",
        "ready": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "inventory_id": inventory.inventory_id if inventory is not None else None,
        "inventory_manifest_path": (
            relative_to_root(inventory_path, resolved_root)
            if inventory_path is not None
            else None
        ),
        "inventory_ready": (
            inventory.ready_for_import if inventory is not None else False
        ),
        "inventory_current": inventory_current,
        "inventory_attested": inventory_attested,
        "ready_bundle_count": (
            inventory.ready_bundle_count if inventory is not None else 0
        ),
        "ready_record_count": expected_record_count or 0,
        "ready_training_eligible_record_count": (
            inventory.ready_training_eligible_record_count
            if inventory is not None
            else 0
        ),
        "current_record_count": current_record_count,
        "staged_import_receipt_count": len(staged_receipts),
        "release_manifest_count": len(release_manifests),
        "active_release_id": current.get("release_id"),
        "runtime_project_root": relative_to_root(runtime_root, resolved_root),
        "provider_configured": provider_configured,
        "evidence_policy": evidence_policy.value,
        "web_required": web_required,
        "web_provider": settings.web_provider.strip().lower(),
        "web_policy_status": (
            "READY_DISABLED_BY_DESIGN"
            if evidence_policy is EvidencePolicy.CSV_MEMORY_ONLY_STRICT
            and web_ready
            else "NOT_READY"
        ),
        "embedding_fallback_policy": settings.event_cluster_fallback_policy.value,
        "codex_oauth_health": oauth_health,
        "shadow_readiness": shadow,
    }


def _latest_inventory(
    root: Path,
) -> tuple[ProductionImportInventoryManifest | None, Path | None]:
    candidates: list[tuple[ProductionImportInventoryManifest, Path]] = []
    for path in sorted(
        (root / PRODUCTION_INVENTORY_DIR).glob(
            "*/production_import_inventory.json"
        )
    ):
        try:
            manifest = ProductionImportInventoryManifest.model_validate(
                read_json(path)
            )
        except (OSError, ValueError):
            continue
        candidates.append((manifest, path))
    if not candidates:
        return None, None
    return max(
        candidates,
        key=lambda item: (item[0].created_at, item[0].inventory_id),
    )


def _read_dict(path: Path) -> dict[str, Any]:
    try:
        payload = read_json(path)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
