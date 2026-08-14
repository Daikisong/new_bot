from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from news_scalping_lab.config import Settings, load_settings, write_default_config_files
from news_scalping_lab.contracts.memory_context import ArtifactReference
from news_scalping_lab.contracts.models import Candidate, PathType
from news_scalping_lab.contracts.production import (
    ProductionBatchImportReceipt,
    ProductionReleaseManifest,
)
from news_scalping_lab.memory.company import CompanyMemoryStore
from news_scalping_lab.production.importer import (
    inspect_production_batch_import,
    stage_production_batch_import,
)
from news_scalping_lab.production.inventory import (
    build_production_import_inventory,
    inspect_production_import_inventory,
    seal_production_import_inventory,
)
from news_scalping_lab.production.readiness import phase9_production_readiness
from news_scalping_lab.production.release import (
    activate_production_release,
    finalize_production_release,
    inspect_current_production_release,
    inspect_production_release,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.research_import.repair_models import (
    RepairQualityGate,
    RepairTaskState,
)
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    write_json,
)

_INVENTORY_KEY = "phase9-production-inventory-test-key-32-bytes"


def _inventory_fixture(root: Path) -> Path:
    source_path = root / "research" / "inbox" / "bundles" / "raw" / "20180103.md"
    repaired_path = (
        root
        / "research"
        / "inbox"
        / "bundles"
        / "repaired"
        / "2018"
        / "20180103.repaired.md"
    )
    gate_path = (
        root
        / "research"
        / "inbox"
        / "bundles"
        / ".work"
        / "source"
        / "mechanical_quality_gate.json"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    repaired_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("source bundle\n", encoding="utf-8")
    repaired_path.write_text("repaired bundle\n", encoding="utf-8")
    source_sha = file_sha256(source_path)
    repaired_sha = file_sha256(repaired_path)
    gate = RepairQualityGate(
        source_sha256=source_sha,
        repaired_sha256=repaired_sha,
        repaired_byte_size=repaired_path.stat().st_size,
        engine_digest="a" * 64,
        passed=True,
        ready_for_import_pass=True,
        importable_legacy=True,
        current_gold_pass=False,
        mechanical_gold_ready=False,
        final_status=RepairTaskState.REPAIRED_PASS,
        importer={
            "normalized_record_count": 2,
            "validation_passed": True,
            "import_loss_audit_passed": True,
        },
    )
    write_json(gate_path, gate.model_dump(mode="json"))
    manifest_path = (
        root
        / "research"
        / "inbox"
        / "bundles"
        / "repaired"
        / "sequential_repair_manifest.v2.jsonl"
    )
    row = {
        "schema_version": "nslab.repair_source_inventory.v1",
        "filename_date": "20180103",
        "source_path": str(source_path.resolve()),
        "source_sha256": source_sha,
        "byte_size": source_path.stat().st_size,
        "repaired_path": str(repaired_path.resolve()),
        "repaired_sha256": repaired_sha,
        "repaired_byte_size": repaired_path.stat().st_size,
        "quality_gate_path": str(gate_path.resolve()),
        "engine_digest": "a" * 64,
        "final_status": "REPAIRED_PASS",
        "ready_for_import": True,
        "record_count": 2,
        "training_eligible_record_count": 1,
        "semantic_excluded_record_count": 0,
        "deep_audit_passed": True,
        "deterministic": True,
        "isolated_import_passed": True,
        "production_store_unchanged": True,
        "production_import_performed": False,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _importable_inventory_fixture(root: Path) -> Path:
    (root / ".env").write_text(
        "\n".join(
            (
                "NSLAB_LLM_PROVIDER=openai",
                "NSLAB_OPENAI_MODEL=gpt-5",
                "NSLAB_OPENAI_EMBEDDING_MODEL=text-embedding-3-small",
                "NSLAB_WEB_PROVIDER=brave",
                "NSLAB_PRICE_PROVIDER=stock-web",
                "OPENAI_API_KEY=fixture-openai-key",
                "NSLAB_SHADOW_EVALUATION_HMAC_KEY=fixture-shadow-key",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    for directory_name in ("configs", "prompts", "schemas"):
        artifact = root / directory_name / "phase9-fixture.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(directory_name + "\n", encoding="utf-8")
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "research_bundles"
        / "synthetic_v11_bundle.md"
    )
    source_path = root / "research" / "inbox" / "bundles" / "raw" / "bundle.md"
    repaired_path = (
        root
        / "research"
        / "inbox"
        / "bundles"
        / "repaired"
        / "2030"
        / "bundle.repaired.md"
    )
    gate_path = (
        root
        / "research"
        / "inbox"
        / "bundles"
        / ".work"
        / "bundle"
        / "mechanical_quality_gate.json"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    repaired_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fixture, source_path)
    shutil.copy2(fixture, repaired_path)
    source_sha = file_sha256(source_path)
    repaired_sha = file_sha256(repaired_path)
    gate = RepairQualityGate(
        source_sha256=source_sha,
        repaired_sha256=repaired_sha,
        repaired_byte_size=repaired_path.stat().st_size,
        engine_digest="b" * 64,
        passed=True,
        ready_for_import_pass=True,
        importable_legacy=True,
        current_gold_pass=False,
        mechanical_gold_ready=False,
        final_status=RepairTaskState.REPAIRED_PASS,
        importer={
            "normalized_record_count": 2,
            "validation_passed": True,
            "import_loss_audit_passed": True,
        },
    )
    write_json(gate_path, gate.model_dump(mode="json"))
    manifest_path = (
        root
        / "research"
        / "inbox"
        / "bundles"
        / "repaired"
        / "sequential_repair_manifest.v2.jsonl"
    )
    row = {
        "schema_version": "nslab.repair_source_inventory.v1",
        "filename_date": "20300110",
        "source_path": str(source_path.resolve()),
        "source_sha256": source_sha,
        "byte_size": source_path.stat().st_size,
        "repaired_path": str(repaired_path.resolve()),
        "repaired_sha256": repaired_sha,
        "repaired_byte_size": repaired_path.stat().st_size,
        "quality_gate_path": str(gate_path.resolve()),
        "engine_digest": "b" * 64,
        "final_status": "REPAIRED_PASS",
        "ready_for_import": True,
        "record_count": 2,
        "training_eligible_record_count": 2,
        "semantic_excluded_record_count": 0,
        "deep_audit_passed": True,
        "deterministic": True,
        "isolated_import_passed": True,
        "production_store_unchanged": True,
        "production_import_performed": False,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return manifest_path


def _record(record_id: str, episode_id: str, *, eligible: bool) -> BrainRecordEnvelope:
    payload = {"record_type": "context_market_state_or_fact_case", "value": record_id}
    payload_hash = sha256_text(json.dumps(payload, sort_keys=True))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="context_market_state_or_fact_case",
        episode_id=episode_id,
        trade_date=date(2024, 1, 2),
        available_from=datetime(
            2024,
            1,
            3,
            0,
            0,
            tzinfo=ZoneInfo("Asia/Seoul"),
        ),
        training_eligible=eligible,
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=payload,
    )


def _write_record_store(root: Path) -> BrainRecordStore:
    store = BrainRecordStore(root)
    records = {
        "EP-A": [_record("REC-B", "EP-A", eligible=True)],
        "EP-B": [
            _record("REC-A", "EP-B", eligible=False),
            _record("REC-C", "EP-B", eligible=True),
        ],
    }
    for episode_id, rows in records.items():
        (store.records_dir / f"{episode_id}.jsonl").write_text(
            "".join(row.model_dump_json() + "\n" for row in rows),
            encoding="utf-8",
        )
        write_json(
            store.record_manifests_dir / f"{episode_id}.json",
            {
                "schema_version": "nslab.record_manifest.v1",
                "episode_id": episode_id,
                "accepted": True,
                "acceptance_status": "accepted",
            },
        )
    return store


def _prepare_staged_release_fixture(root: Path) -> tuple[Path, Path]:
    _importable_inventory_fixture(root)
    _, inventory_path = build_production_import_inventory(root)
    seal_production_import_inventory(
        root,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    receipt, receipt_path = stage_production_batch_import(
        root,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    project_root = root / receipt.release_project_path
    memory_path = (
        project_root
        / "memory"
        / "retrieval_index"
        / "snapshots"
        / "MEMIDX-fixture"
        / "manifest.json"
    )
    shadow_path = (
        project_root
        / "runs"
        / "shadow_evaluation"
        / "SHADOW-fixture"
        / "shadow_evaluation_manifest.json"
    )
    for path, payload in (
        (project_root / "brain" / "current" / "brain_manifest.json", {"brain": "fixture"}),
        (memory_path, {"memory": "fixture"}),
        (memory_path.parent / "memory.duckdb", {"database": "fixture"}),
        (
            project_root / "memory" / "company_memory" / "CM-record.json",
            {
                "ticker": "RECORD-001",
                "company_name": "Record Derived Co",
                "available_from": "2029-01-01T00:00:00+09:00",
                "known_at": "2029-01-01T00:00:00+09:00",
                "provenance": [
                    {
                        "source_id": "SRC-record-memory",
                        "source_type": "company_memory_delta_record",
                        "uri": "memory/records/fixture.jsonl",
                    }
                ],
            },
        ),
        (shadow_path, {"shadow": "fixture"}),
        (
            project_root
            / "diagnostics"
            / "phase9_production_release_readiness.json",
            {"doctor": "fixture"},
        ),
        (project_root / "warehouse" / "release_fixture.json", {"warehouse": True}),
    ):
        write_json(path, payload)
    write_json(
        project_root / "memory" / "retrieval_index" / "current.json",
        {
            "snapshot_id": "MEMIDX-fixture",
            "manifest_path": memory_path.relative_to(project_root).as_posix(),
            "manifest_sha256": file_sha256(memory_path),
        },
    )
    return receipt_path, shadow_path


def _fixture_release_projection(
    active_project_root: Path,
    active_shadow_path: Path,
    *,
    write_doctor_report: bool,
    dotenv_root: Path,
) -> dict[str, Any]:
    del write_doctor_report, dotenv_root
    from news_scalping_lab.production.release import _release_artifact_projection

    release_artifacts = _release_artifact_projection(
        active_project_root,
        shadow_evaluation_path=active_shadow_path,
        use_cache=False,
    )
    return {
        "brain_version": "brain-fixture",
        "memory_snapshot_id": "MEMIDX-fixture",
        "shadow_evaluation_id": "SHADOW-fixture",
        "llm_provider": "openai",
        "llm_model": "gpt-5",
        "embedding_model": "llm_embedding:openai:text-embedding-3-small",
        "web_provider": "brave",
        "price_provider": "stock-web",
        "audit_results": {"all": True},
        "findings": [],
        "brain_manifest_sha256": file_sha256(
            active_project_root / "brain" / "current" / "brain_manifest.json"
        ),
        "memory_manifest_sha256": file_sha256(
            active_project_root
            / "memory"
            / "retrieval_index"
            / "snapshots"
            / "MEMIDX-fixture"
            / "manifest.json"
        ),
        "shadow_manifest_sha256": file_sha256(active_shadow_path),
        "doctor_report_sha256": file_sha256(
            active_project_root
            / "diagnostics"
            / "phase9_production_release_readiness.json"
        ),
        "release_artifact_root_sha256": release_artifacts.root_sha256,
        "release_artifact_projection_version": (
            release_artifacts.projection_version
        ),
        "release_artifact_count": release_artifacts.artifact_count,
    }


def test_release_identity_binds_record_and_runtime_artifact_roots(
    tmp_path: Path,
) -> None:
    receipt_path, shadow_path = _prepare_staged_release_fixture(tmp_path)
    receipt = ProductionBatchImportReceipt.model_validate(read_json(receipt_path))
    project_root = tmp_path / receipt.release_project_path
    projection = _fixture_release_projection(
        project_root,
        shadow_path,
        write_doctor_report=True,
        dotenv_root=tmp_path,
    )
    from news_scalping_lab.production.release import (
        _projection_with_release_configuration,
        _release_identity,
    )

    projection = _projection_with_release_configuration(project_root, projection)

    baseline = _release_identity(receipt=receipt, projection=projection)
    changed_record_root = _release_identity(
        receipt=receipt.model_copy(
            update={"record_artifact_root_sha256": "f" * 64}
        ),
        projection=projection,
    )
    changed_runtime_root = _release_identity(
        receipt=receipt,
        projection={**projection, "release_artifact_root_sha256": "e" * 64},
    )
    changed_projection_version = _release_identity(
        receipt=receipt,
        projection={
            **projection,
            "release_artifact_projection_version": (
                "production_release_artifacts.v2"
            ),
        },
    )
    changed_doctor_report = _release_identity(
        receipt=receipt,
        projection={**projection, "doctor_report_sha256": "d" * 64},
    )

    assert baseline != changed_record_root
    assert baseline != changed_runtime_root
    assert baseline != changed_projection_version
    assert baseline != changed_doctor_report
    assert sha256_text(canonical_json(baseline)) != sha256_text(
        canonical_json(changed_record_root)
    )
    assert sha256_text(canonical_json(baseline)) != sha256_text(
        canonical_json(changed_runtime_root)
    )


def test_sealed_doctor_report_accepts_ready_snapshot_despite_runtime_counts() -> None:
    from news_scalping_lab.production.release import (
        _sealed_doctor_report_findings,
    )

    sealed_report = {
        "schema_version": "nslab.production_readiness.v1",
        "passed": True,
        "status": "ready",
        "finding_count": 0,
        "findings": [],
        "warehouse": {"counts": {"predictions": 0}},
    }
    current_report = {
        **sealed_report,
        "warehouse": {"counts": {"predictions": 1}},
    }

    assert sealed_report != current_report
    assert _sealed_doctor_report_findings(sealed_report) == []
    assert _sealed_doctor_report_findings(
        {**sealed_report, "passed": False, "status": "attention"}
    ) == ["doctor_report_not_ready"]


def test_production_import_inventory_is_content_addressed_and_attested(
    tmp_path: Path,
) -> None:
    _inventory_fixture(tmp_path)
    manifest, path = build_production_import_inventory(tmp_path)

    assert manifest.ready_for_import is True
    assert manifest.ready_bundle_count == 1
    assert manifest.ready_record_count == 2
    inspection = inspect_production_import_inventory(tmp_path, path)
    assert inspection["passed"] is True
    assert inspection["ready_for_import"] is True
    assert inspection["attested"] is False

    sealed = seal_production_import_inventory(
        tmp_path,
        path,
        key_value=_INVENTORY_KEY,
    )
    assert sealed.attestation is not None
    inspected = inspect_production_import_inventory(
        tmp_path,
        path,
        attestation_key=_INVENTORY_KEY,
    )
    assert inspected["passed"] is True
    assert inspected["attested"] is True
    repeated, repeated_path = build_production_import_inventory(tmp_path)
    assert repeated_path == path
    assert repeated.attestation == sealed.attestation
    wrong_key = inspect_production_import_inventory(
        tmp_path,
        path,
        attestation_key="wrong-production-inventory-key-32-bytes",
    )
    assert wrong_key["passed"] is False
    assert "production_import_attestation_invalid" in wrong_key["errors"]


def test_production_import_inventory_rejects_repaired_file_tamper(
    tmp_path: Path,
) -> None:
    _inventory_fixture(tmp_path)
    _, path = build_production_import_inventory(tmp_path)
    manifest = read_json(path)
    entries = tmp_path / manifest["ready_entries"]["artifact_path"]
    entry = json.loads(entries.read_text(encoding="utf-8").splitlines()[0])
    repaired = tmp_path / entry["repaired_path"]
    repaired.write_text("attacker rewrite\n", encoding="utf-8")

    inspected = inspect_production_import_inventory(tmp_path, path)

    assert inspected["passed"] is False
    assert any("repaired" in error for error in inspected["errors"])


def test_production_import_inventory_rejects_coherent_entries_rewrite(
    tmp_path: Path,
) -> None:
    _inventory_fixture(tmp_path)
    _, path = build_production_import_inventory(tmp_path)
    manifest = read_json(path)
    entries_path = tmp_path / manifest["ready_entries"]["artifact_path"]
    row = json.loads(entries_path.read_text(encoding="utf-8").splitlines()[0])
    row["record_count"] = 999
    entries_path.write_text(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    manifest["ready_entries"]["sha256"] = file_sha256(entries_path)
    manifest["ready_record_count"] = 999
    write_json(path, manifest)

    inspected = inspect_production_import_inventory(tmp_path, path)

    assert inspected["passed"] is False
    assert "production_import_ready_record_count_mismatch" in inspected["errors"]
    assert "production_import_entries_projection_mismatch" in inspected["errors"]


def test_production_import_inventory_blocks_outside_root_source(
    tmp_path: Path,
) -> None:
    source_manifest = _inventory_fixture(tmp_path)
    row = json.loads(source_manifest.read_text(encoding="utf-8"))
    row["source_path"] = str((tmp_path.parent / "outside.md").resolve())
    source_manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")

    manifest, path = build_production_import_inventory(tmp_path)

    assert manifest.ready_for_import is False
    assert manifest.declared_ready_bundle_count == 1
    assert manifest.ready_bundle_count == 0
    inspection = inspect_production_import_inventory(tmp_path, path)
    assert inspection["passed"] is True
    assert inspection["ready_for_import"] is False


def test_streaming_fresh_record_index_matches_legacy_projection(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "legacy"
    streaming_root = tmp_path / "streaming"
    legacy_store = _write_record_store(legacy_root)
    streaming_store = _write_record_store(streaming_root)

    legacy = legacy_store.rebuild_indexes()
    streaming = streaming_store.rebuild_indexes_streaming_fresh()

    assert streaming == legacy
    assert read_json(
        streaming_store.record_index_dir / "by_record_id.json"
    ) == read_json(legacy_store.record_index_dir / "by_record_id.json")
    assert streaming_store.inspect_streaming_index_projection() == streaming
    inspected_manifest, inspected_identity_sha256 = (
        streaming_store.inspect_streaming_index_artifacts()
    )
    assert inspected_manifest == streaming
    assert inspected_identity_sha256 == file_sha256(
        streaming_store.record_index_dir / "by_record_id.json"
    )


def test_production_batch_import_stages_without_mutating_live_store(
    tmp_path: Path,
) -> None:
    _importable_inventory_fixture(tmp_path)
    inventory, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )

    receipt, receipt_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )

    assert inventory.ready_record_count == 2
    assert receipt.imported_bundle_count == 1
    assert receipt.imported_record_count == 2
    assert not (tmp_path / "memory" / "records").exists()
    inspection = inspect_production_batch_import(
        tmp_path,
        receipt_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    assert inspection["passed"] is True
    repeated, repeated_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    assert repeated == receipt
    assert repeated_path == receipt_path


def test_production_batch_import_inspection_rejects_record_tamper(
    tmp_path: Path,
) -> None:
    _importable_inventory_fixture(tmp_path)
    _, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    receipt, receipt_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    project_root = tmp_path / receipt.release_project_path
    record_path = next((project_root / "memory" / "records").glob("*.jsonl"))
    record_path.write_text(
        record_path.read_text(encoding="utf-8").replace(
            "BRAIN-SYNTH-PAIR",
            "BRAIN-TAMPERED-PAIR",
        ),
        encoding="utf-8",
    )

    inspection = inspect_production_batch_import(
        tmp_path,
        receipt_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )

    assert inspection["passed"] is False
    assert any("record" in error for error in inspection["errors"])


def test_production_batch_import_rejects_coherent_identity_index_rewrite(
    tmp_path: Path,
) -> None:
    _importable_inventory_fixture(tmp_path)
    _, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    receipt, receipt_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    identity_path = tmp_path / receipt.record_identity_index.artifact_path
    identity_payload: dict[str, Any] = read_json(identity_path)
    identity_payload["BRAIN-SYNTH-ISSUER"]["episode_id"] = "ATTACKER-EPISODE"
    write_json(identity_path, identity_payload)
    receipt_payload: dict[str, Any] = read_json(receipt_path)
    receipt_payload["record_identity_index"]["sha256"] = file_sha256(
        identity_path
    )
    write_json(receipt_path, receipt_payload)

    inspection = inspect_production_batch_import(
        tmp_path,
        receipt_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )

    assert inspection["passed"] is False
    assert (
        "production_batch_import_record_identity_projection_mismatch"
        in inspection["errors"]
    )


def test_production_batch_import_rejects_coherent_result_rewrite(
    tmp_path: Path,
) -> None:
    _importable_inventory_fixture(tmp_path)
    _, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    receipt, receipt_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    receipt_payload: dict[str, Any] = read_json(receipt_path)
    results_path = tmp_path / receipt.bundle_results.artifact_path
    row = json.loads(results_path.read_text(encoding="utf-8"))
    row["status"] = "attacker-approved"
    row["validation_passed"] = False
    results_path.write_text(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    receipt_payload["bundle_results"]["sha256"] = file_sha256(results_path)
    write_json(receipt_path, receipt_payload)

    inspection = inspect_production_batch_import(
        tmp_path,
        receipt_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )

    assert inspection["passed"] is False
    assert "production_batch_import_result_projection_mismatch" in inspection["errors"]


def test_production_batch_import_reprojects_stored_validation_from_source(
    tmp_path: Path,
) -> None:
    _importable_inventory_fixture(tmp_path)
    _, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    receipt, receipt_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    project_root = tmp_path / receipt.release_project_path
    validation_path = next(
        (project_root / "research" / "episodes").glob("*/validation_report.json")
    )
    validation_payload: dict[str, Any] = read_json(validation_path)
    validation_payload["adapter"] = "ATTACKER-VALIDATION"
    write_json(validation_path, validation_payload)
    results_path = tmp_path / receipt.bundle_results.artifact_path
    result_row = json.loads(results_path.read_text(encoding="utf-8"))
    result_row["validation_sha256"] = sha256_text(
        canonical_json(validation_payload)
    )
    results_path.write_text(
        canonical_json(result_row) + "\n",
        encoding="utf-8",
    )
    receipt_payload: dict[str, Any] = read_json(receipt_path)
    receipt_payload["bundle_results"]["sha256"] = file_sha256(results_path)
    write_json(receipt_path, receipt_payload)

    inspection = inspect_production_batch_import(
        tmp_path,
        receipt_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )

    assert inspection["passed"] is False
    assert any("validation_projection" in error for error in inspection["errors"])


def test_production_batch_import_rejects_relocated_result_artifact(
    tmp_path: Path,
) -> None:
    _importable_inventory_fixture(tmp_path)
    _, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    receipt, receipt_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    receipt_payload: dict[str, Any] = read_json(receipt_path)
    results_path = tmp_path / receipt.bundle_results.artifact_path
    relocated_path = receipt_path.parent / "nested" / "bundle_results.jsonl"
    relocated_path.parent.mkdir()
    results_path.replace(relocated_path)
    receipt_payload["bundle_results"]["artifact_path"] = (
        relocated_path.relative_to(tmp_path).as_posix()
    )
    receipt_payload["bundle_results"]["sha256"] = file_sha256(relocated_path)
    write_json(receipt_path, receipt_payload)

    inspection = inspect_production_batch_import(
        tmp_path,
        receipt_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )

    assert inspection["passed"] is False
    assert "production_batch_import_bundle_results_path_mismatch" in inspection["errors"]


def test_failed_batch_import_never_publishes_stage_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _importable_inventory_fixture(tmp_path)
    _, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    from news_scalping_lab.production import importer as importer_module

    original_import = importer_module.import_versioned_bundle

    def fail_import(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected import failure")

    monkeypatch.setattr(importer_module, "import_versioned_bundle", fail_import)
    with pytest.raises(RuntimeError, match="injected import failure"):
        stage_production_batch_import(
            tmp_path,
            inventory_path,
            inventory_attestation_key=_INVENTORY_KEY,
        )
    assert not list(
        (tmp_path / "production" / "staging").glob(
            "P9IMPORT-*/production_batch_import_receipt.json"
        )
    )
    assert list(
        (tmp_path / "production" / ".work").glob(
            ".p9-*/failure.json"
        )
    )

    monkeypatch.setattr(importer_module, "import_versioned_bundle", original_import)
    receipt, receipt_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    assert receipt.passed is True
    assert receipt_path.is_file()


def test_completed_batch_import_is_published_in_one_directory_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _importable_inventory_fixture(tmp_path)
    _, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    from news_scalping_lab.production import importer as importer_module

    original_publish = importer_module._publish_completed_stage

    def fail_publish(_work_dir: Path, _stage_dir: Path) -> None:
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(importer_module, "_publish_completed_stage", fail_publish)
    with pytest.raises(RuntimeError, match="injected publication failure"):
        stage_production_batch_import(
            tmp_path,
            inventory_path,
            inventory_attestation_key=_INVENTORY_KEY,
        )
    assert not list((tmp_path / "production" / "staging").glob("P9IMPORT-*"))
    completed_work_receipts = list(
        (tmp_path / "production" / ".work").glob(
            ".p9-*/production_batch_import_receipt.json"
        )
    )
    assert len(completed_work_receipts) == 1

    monkeypatch.setattr(
        importer_module,
        "_publish_completed_stage",
        original_publish,
    )
    receipt, receipt_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    assert receipt.passed is True
    assert receipt_path.is_file()


def test_production_activation_is_one_signed_pointer_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_id = "P9REL-" + "A" * 20
    release_dir = tmp_path / "production" / "releases" / release_id
    project_root = release_dir / "project"
    project_root.mkdir(parents=True)
    write_default_config_files(Settings(project_root=project_root))
    artifact = release_dir / "artifact.json"
    write_json(artifact, {"ok": True})
    reference = ArtifactReference(
        artifact_path=artifact.relative_to(tmp_path).as_posix(),
        sha256=file_sha256(artifact),
        item_count=1,
    )
    manifest = ProductionReleaseManifest(
        release_id=release_id,
        created_at=datetime.now(tz=ZoneInfo("Asia/Seoul")),
        release_project_path=project_root.relative_to(tmp_path).as_posix(),
        release_transaction=reference,
        release_configuration=reference,
        release_configuration_root_sha256="a" * 64,
        release_artifacts=reference,
        release_artifact_projection_version="production_release_artifacts.v1",
        release_artifact_root_sha256="b" * 64,
        record_artifact_root_sha256="c" * 64,
        inventory_manifest=reference,
        import_receipt=reference,
        brain_manifest=reference,
        memory_snapshot_manifest=reference,
        shadow_evaluation_manifest=reference,
        doctor_report=reference,
        brain_version="brain-production",
        memory_snapshot_id="MEMIDX-production",
        shadow_evaluation_id="SHADOW-production",
        llm_provider="openai",
        llm_model="gpt-5",
        embedding_model="llm_embedding:openai:text-embedding-3-small",
        web_provider="brave",
        price_provider="stock-web",
        audit_results={"all": True},
        finding_count=0,
        findings=[],
        production_ready=True,
    )
    manifest_path = release_dir / "production_release_manifest.json"
    write_json(manifest_path, manifest.model_dump(mode="json"))
    monkeypatch.setattr(
        "news_scalping_lab.production.release.inspect_production_release",
        lambda *_args, **_kwargs: {"passed": True, "errors": []},
    )
    monkeypatch.setattr(
        "news_scalping_lab.production.release._fast_active_release_errors",
        lambda *_args, **_kwargs: [],
    )

    pointer, pointer_path = activate_production_release(
        tmp_path,
        manifest_path,
        promotion_key=_INVENTORY_KEY,
    )

    assert pointer_path == tmp_path / "production" / "current.json"
    assert pointer.release_id == release_id
    inspected = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert inspected["passed"] is True
    monkeypatch.setenv("NSLAB_PRODUCTION_PROMOTION_HMAC_KEY", _INVENTORY_KEY)
    assert load_settings(tmp_path).project_root == project_root.resolve()
    with pytest.raises(ValueError, match="already active"):
        activate_production_release(
            tmp_path,
            manifest_path,
            promotion_key=_INVENTORY_KEY,
        )

    activation_paths = list(
        (tmp_path / "production" / "activations").glob("P9ACT-*.json")
    )
    assert len(activation_paths) == 1
    activation_payload = read_json(activation_paths[0])
    activation_paths[0].unlink()
    missing_history = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert missing_history["passed"] is False
    assert "production_current_activation_history_invalid" in missing_history["errors"]
    write_json(activation_paths[0], activation_payload)

    tampered: dict[str, Any] = read_json(pointer_path)
    tampered["release_project_path"] = "production/releases/ATTACKER/project"
    write_json(pointer_path, tampered)
    rejected = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert rejected["passed"] is False
    with pytest.raises(ValueError, match="active production release is invalid"):
        load_settings(tmp_path)


def test_phase9_readiness_uses_the_active_release_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _importable_inventory_fixture(tmp_path)
    inventory, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    release_id = "P9REL-" + "B" * 20
    release_root = tmp_path / "production" / "releases" / release_id / "project"
    write_json(
        tmp_path / "memory" / "record_index" / "manifest.json",
        {"record_count": 999},
    )
    write_json(
        release_root / "memory" / "record_index" / "manifest.json",
        {"record_count": inventory.ready_record_count},
    )
    write_json(
        release_root.parent / "production_release_manifest.json",
        {"release": "fixture"},
    )
    write_json(tmp_path / "production" / "current.json", {"pointer": "fixture"})
    monkeypatch.setenv("NSLAB_PRODUCTION_PROMOTION_HMAC_KEY", _INVENTORY_KEY)
    inspection_calls: list[bool] = []

    def current_inspection(
        _root: Path,
        *,
        promotion_key: str,
        deep: bool,
    ) -> dict[str, Any]:
        assert promotion_key == _INVENTORY_KEY
        inspection_calls.append(deep)
        return {
            "passed": True,
            "release_id": release_id,
            "release_project_path": release_root.relative_to(tmp_path).as_posix(),
            "errors": [],
        }

    observed_shadow_roots: list[Path] = []

    def shadow_readiness(active_root: Path) -> dict[str, Any]:
        observed_shadow_roots.append(active_root)
        return {"ready": True}

    monkeypatch.setattr(
        "news_scalping_lab.production.readiness.inspect_current_production_release",
        current_inspection,
    )
    monkeypatch.setattr(
        "news_scalping_lab.production.readiness.shadow_replay_readiness",
        shadow_readiness,
    )

    readiness = phase9_production_readiness(tmp_path)

    assert inspection_calls == [True]
    assert observed_shadow_roots == [release_root.resolve()]
    assert readiness["current_record_count"] == inventory.ready_record_count
    assert readiness["runtime_project_root"] == (
        release_root.relative_to(tmp_path).as_posix()
    )
    assert "current record store does not match import-ready inventory" not in (
        readiness["blockers"]
    )


def test_finalize_release_moves_verified_stage_without_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _importable_inventory_fixture(tmp_path)
    _, inventory_path = build_production_import_inventory(tmp_path)
    seal_production_import_inventory(
        tmp_path,
        inventory_path,
        key_value=_INVENTORY_KEY,
    )
    receipt, receipt_path = stage_production_batch_import(
        tmp_path,
        inventory_path,
        inventory_attestation_key=_INVENTORY_KEY,
    )
    project_root = tmp_path / receipt.release_project_path
    brain_path = project_root / "brain" / "current" / "brain_manifest.json"
    memory_path = (
        project_root
        / "memory"
        / "retrieval_index"
        / "snapshots"
        / "MEMIDX-fixture"
        / "manifest.json"
    )
    shadow_path = (
        project_root
        / "runs"
        / "shadow_evaluation"
        / "SHADOW-fixture"
        / "shadow_evaluation_manifest.json"
    )
    doctor_path = (
        project_root / "diagnostics" / "phase9_production_release_readiness.json"
    )
    for path, payload in (
        (brain_path, {"brain": "fixture"}),
        (memory_path, {"memory": "fixture"}),
        (memory_path.parent / "memory.duckdb", {"database": "fixture"}),
        (
            project_root / "memory" / "company_memory" / "CM-record.json",
            {
                "ticker": "RECORD-001",
                "company_name": "Record Derived Co",
                "available_from": "2029-01-01T00:00:00+09:00",
                "known_at": "2029-01-01T00:00:00+09:00",
                "provenance": [
                    {
                        "source_id": "SRC-record-memory",
                        "source_type": "company_memory_delta_record",
                        "uri": "memory/records/fixture.jsonl",
                    }
                ],
            },
        ),
        (shadow_path, {"shadow": "fixture"}),
        (doctor_path, {"doctor": "fixture"}),
        (project_root / "warehouse" / "release_fixture.json", {"warehouse": True}),
    ):
        write_json(path, payload)
    write_json(
        project_root / "memory" / "retrieval_index" / "current.json",
        {
            "snapshot_id": "MEMIDX-fixture",
            "manifest_path": memory_path.relative_to(project_root).as_posix(),
            "manifest_sha256": file_sha256(memory_path),
        },
    )

    def projection(
        active_project_root: Path,
        active_shadow_path: Path,
        *,
        write_doctor_report: bool,
        dotenv_root: Path,
    ) -> dict[str, Any]:
        return _fixture_release_projection(
            active_project_root,
            active_shadow_path,
            write_doctor_report=write_doctor_report,
            dotenv_root=dotenv_root,
        )

    monkeypatch.setattr(
        "news_scalping_lab.production.release._release_projection",
        projection,
    )

    manifest, manifest_path = finalize_production_release(
        tmp_path,
        receipt_path,
        shadow_path,
        promotion_key=_INVENTORY_KEY,
    )

    assert manifest.production_ready is True
    assert manifest_path.parent.name == manifest.release_id
    assert not receipt_path.parent.exists()
    assert not (tmp_path / "production" / "current.json").exists()
    inspection = inspect_production_release(
        tmp_path,
        manifest_path,
        promotion_key=_INVENTORY_KEY,
    )
    assert inspection["passed"] is True

    activate_production_release(
        tmp_path,
        manifest_path,
        promotion_key=_INVENTORY_KEY,
    )
    release_project_root = tmp_path / manifest.release_project_path
    configuration_file = next((release_project_root / "prompts").glob("*"))
    original_configuration = configuration_file.read_bytes()
    configuration_file.write_text("attacker prompt rewrite\n", encoding="utf-8")
    current_after_configuration_tamper = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert current_after_configuration_tamper["passed"] is False
    assert any(
        "configuration" in error
        for error in current_after_configuration_tamper["errors"]
    )
    configuration_file.write_bytes(original_configuration)
    monkeypatch.setenv("NSLAB_OPENAI_MODEL", "gpt-5-provider-drift")
    current_after_provider_drift = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert current_after_provider_drift["passed"] is False
    assert "production_current_llm_model_mismatch" in (
        current_after_provider_drift["errors"]
    )
    monkeypatch.delenv("NSLAB_OPENAI_MODEL")
    record_file = next((release_project_root / "memory" / "records").glob("*.jsonl"))
    original_record_bytes = record_file.read_bytes()
    record_file.write_text(
        record_file.read_text(encoding="utf-8").replace(
            "BRAIN-SYNTH-ISSUER",
            "BRAIN-ATTACKER-ISSUER",
        ),
        encoding="utf-8",
    )
    current_after_record_tamper = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert current_after_record_tamper["passed"] is False
    assert "production_record_artifact_projection_mismatch" in (
        current_after_record_tamper["errors"]
    )
    record_file.write_bytes(original_record_bytes)

    validation_file = next(
        (release_project_root / "research" / "episodes").glob(
            "*/validation_report.json"
        )
    )
    original_validation_bytes = validation_file.read_bytes()
    validation_file.write_text('{"attacker":true}\n', encoding="utf-8")
    current_after_episode_tamper = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert current_after_episode_tamper["passed"] is False
    assert "production_release_artifact_projection_mismatch" in (
        current_after_episode_tamper["errors"]
    )
    validation_file.write_bytes(original_validation_bytes)

    memory_database = (
        release_project_root
        / "memory"
        / "retrieval_index"
        / "snapshots"
        / "MEMIDX-fixture"
        / "memory.duckdb"
    )
    original_database_bytes = memory_database.read_bytes()
    memory_database.write_bytes(original_database_bytes + b"tampered")
    current_after_memory_tamper = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert current_after_memory_tamper["passed"] is False
    assert "production_release_artifact_projection_mismatch" in (
        current_after_memory_tamper["errors"]
    )
    memory_database.write_bytes(original_database_bytes)

    record_company_memory = (
        release_project_root / "memory" / "company_memory" / "CM-record.json"
    )
    original_company_memory_bytes = record_company_memory.read_bytes()
    record_company_memory.write_text(
        '{"provenance":[{"source_type":"attacker"}]}\n',
        encoding="utf-8",
    )
    current_after_company_memory_tamper = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert current_after_company_memory_tamper["passed"] is False
    assert "production_release_artifact_projection_mismatch" in (
        current_after_company_memory_tamper["errors"]
    )
    record_company_memory.write_bytes(original_company_memory_bytes)

    immutable_warehouse = release_project_root / "warehouse" / "release_fixture.json"
    original_warehouse_bytes = immutable_warehouse.read_bytes()
    immutable_warehouse.write_bytes(original_warehouse_bytes + b"tampered")
    current_after_warehouse_tamper = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert current_after_warehouse_tamper["passed"] is False
    assert "production_release_artifact_projection_mismatch" in (
        current_after_warehouse_tamper["errors"]
    )
    immutable_warehouse.write_bytes(original_warehouse_bytes)

    daily_prediction_path = (
        release_project_root
        / "runs"
        / "checkpoints"
        / "output_artifacts"
        / "RUN-production-daily"
        / "blind_prediction.json"
    )
    write_json(
        daily_prediction_path,
        {"prediction_id": "PRED-production-daily"},
    )
    CompanyMemoryStore(release_project_root).upsert_from_candidates(
        [
            Candidate(
                rank=1,
                ticker="DAILY-001",
                company_name="Daily Candidate Co",
                path_type=PathType.SINGLE_EVENT,
                thesis="Cutoff-safe candidate memory.",
                why_now="The current event supports verification.",
                causal_chain=["event", "candidate"],
                counterarguments=["relation requires verification"],
            )
        ],
        prediction_path=daily_prediction_path,
        known_at=datetime(2030, 1, 10, 8, 59, tzinfo=ZoneInfo("Asia/Seoul")),
        attestation_key=_INVENTORY_KEY,
    )
    for filename in (
        "company_memory.parquet",
        "daily_outcomes.parquet",
        "predictions.parquet",
    ):
        (release_project_root / "warehouse" / filename).write_bytes(
            b"daily mutable projection"
        )
    write_json(
        release_project_root / "research" / "episodes" / "EP-daily.json",
        {"schema_version": "nslab.research_episode.v2"},
    )
    current_after_normal_daily_outputs = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert current_after_normal_daily_outputs["passed"] is True

    unsigned_company_memory = (
        release_project_root / "memory" / "company_memory" / "CM-attacker.json"
    )
    write_json(
        unsigned_company_memory,
        {
            "ticker": "EVIL-001",
            "company_name": "Unsigned Memory",
            "available_from": "2029-01-01T00:00:00+09:00",
            "known_at": "2029-01-01T00:00:00+09:00",
            "provenance": [],
        },
    )
    current_after_unsigned_memory = inspect_current_production_release(
        tmp_path,
        promotion_key=_INVENTORY_KEY,
        deep=False,
    )
    assert current_after_unsigned_memory["passed"] is False
    assert any(
        error.startswith("production_company_memory_attestation_invalid")
        for error in current_after_unsigned_memory["errors"]
    )
    unsigned_company_memory.unlink()

    relocated_doctor = (
        tmp_path / manifest.release_project_path / "diagnostics"
        / "phase9_production_release_readiness.json"
    )
    write_json(relocated_doctor, {"doctor": "tampered"})
    rejected = inspect_production_release(
        tmp_path,
        manifest_path,
        promotion_key=_INVENTORY_KEY,
    )
    assert rejected["passed"] is False


def test_finalize_release_recovers_after_interruption_following_stage_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, shadow_path = _prepare_staged_release_fixture(tmp_path)
    from news_scalping_lab.production import release as release_module

    projection_calls = 0

    def interrupted_projection(
        active_project_root: Path,
        active_shadow_path: Path,
        *,
        write_doctor_report: bool,
        dotenv_root: Path,
    ) -> dict[str, Any]:
        nonlocal projection_calls
        projection_calls += 1
        if projection_calls == 2:
            raise RuntimeError("injected post-move failure")
        return _fixture_release_projection(
            active_project_root,
            active_shadow_path,
            write_doctor_report=write_doctor_report,
            dotenv_root=dotenv_root,
        )

    monkeypatch.setattr(
        release_module,
        "_release_projection",
        interrupted_projection,
    )
    with pytest.raises(RuntimeError, match="injected post-move failure"):
        finalize_production_release(
            tmp_path,
            receipt_path,
            shadow_path,
            promotion_key=_INVENTORY_KEY,
        )
    assert not receipt_path.exists()
    release_transactions = list(
        (tmp_path / "production" / "releases").glob(
            "P9REL-*/production_release_transaction.json"
        )
    )
    assert len(release_transactions) == 1
    assert not (
        release_transactions[0].parent / "production_release_manifest.json"
    ).exists()

    monkeypatch.setattr(
        release_module,
        "_release_projection",
        _fixture_release_projection,
    )
    manifest, manifest_path = finalize_production_release(
        tmp_path,
        receipt_path,
        shadow_path,
        promotion_key=_INVENTORY_KEY,
    )

    assert manifest.production_ready is True
    assert manifest_path.is_file()
    inspection = inspect_production_release(
        tmp_path,
        manifest_path,
        promotion_key=_INVENTORY_KEY,
    )
    assert inspection["passed"] is True


def test_finalize_release_uses_outer_dotenv_without_copying_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, shadow_path = _prepare_staged_release_fixture(tmp_path)
    from news_scalping_lab.production import release as release_module

    observed_providers: list[str] = []

    def projection_with_outer_dotenv(
        active_project_root: Path,
        active_shadow_path: Path,
        *,
        write_doctor_report: bool,
        dotenv_root: Path,
    ) -> dict[str, Any]:
        settings = load_settings(
            active_project_root,
            resolve_production=False,
            dotenv_root=dotenv_root,
        )
        observed_providers.append(settings.llm_provider)
        assert settings.env_value("OPENAI_API_KEY") == "fixture-openai-key"
        assert settings.env_value("NSLAB_SHADOW_EVALUATION_HMAC_KEY") == (
            "fixture-shadow-key"
        )
        return _fixture_release_projection(
            active_project_root,
            active_shadow_path,
            write_doctor_report=write_doctor_report,
            dotenv_root=dotenv_root,
        )

    monkeypatch.setattr(
        release_module,
        "_release_projection",
        projection_with_outer_dotenv,
    )
    manifest, _ = finalize_production_release(
        tmp_path,
        receipt_path,
        shadow_path,
        promotion_key=_INVENTORY_KEY,
    )

    assert observed_providers == ["openai", "openai", "openai"]
    assert not (tmp_path / manifest.release_project_path / ".env").exists()


def test_finalize_release_recovers_from_relocated_receipt_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, shadow_path = _prepare_staged_release_fixture(tmp_path)
    from news_scalping_lab.production import release as release_module

    monkeypatch.setattr(
        release_module,
        "_release_projection",
        _fixture_release_projection,
    )
    original_write_json = release_module.write_json

    def interrupted_write(path: Path, payload: Any) -> None:
        if "production_batch_import_receipt.json" in path.name:
            raise RuntimeError("injected receipt write failure")
        original_write_json(path, payload)

    monkeypatch.setattr(release_module, "write_json", interrupted_write)
    with pytest.raises(RuntimeError, match="injected receipt write failure"):
        finalize_production_release(
            tmp_path,
            receipt_path,
            shadow_path,
            promotion_key=_INVENTORY_KEY,
        )
    assert not receipt_path.exists()
    release_receipt = next(
        (tmp_path / "production" / "releases").glob(
            "P9REL-*/production_batch_import_receipt.json"
        )
    )
    assert read_json(release_receipt)["release_project_path"].startswith(
        "production/staging/"
    )

    monkeypatch.setattr(release_module, "write_json", original_write_json)
    manifest, manifest_path = finalize_production_release(
        tmp_path,
        receipt_path,
        shadow_path,
        promotion_key=_INVENTORY_KEY,
    )
    assert manifest.production_ready is True
    assert manifest_path.is_file()


def test_release_inspection_rejects_coherent_transaction_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path, shadow_path = _prepare_staged_release_fixture(tmp_path)
    monkeypatch.setattr(
        "news_scalping_lab.production.release._release_projection",
        _fixture_release_projection,
    )
    _, manifest_path = finalize_production_release(
        tmp_path,
        receipt_path,
        shadow_path,
        promotion_key=_INVENTORY_KEY,
    )
    manifest_payload: dict[str, Any] = read_json(manifest_path)
    transaction_path = (
        tmp_path / manifest_payload["release_transaction"]["artifact_path"]
    )
    transaction_payload: dict[str, Any] = read_json(transaction_path)
    transaction_payload["release_identity_sha256"] = "a" * 64
    write_json(transaction_path, transaction_payload)
    manifest_payload["release_transaction"]["sha256"] = file_sha256(
        transaction_path
    )
    write_json(manifest_path, manifest_payload)

    inspection = inspect_production_release(
        tmp_path,
        manifest_path,
        promotion_key=_INVENTORY_KEY,
    )

    assert inspection["passed"] is False
    assert "production_release_transaction_identity_mismatch" in inspection["errors"]
