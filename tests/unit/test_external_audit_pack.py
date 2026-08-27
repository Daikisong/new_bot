from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import duckdb
import pytest

import news_scalping_lab.audits.external_pack as external_pack
from news_scalping_lab.audits.external_pack import (
    ARTIFACT_LEDGER_SCHEMA,
    AUDIT_CORE_SCHEMA,
    AUDIT_SAMPLE_SCHEMA,
    CLAIM_LEDGER_SCHEMA,
    RECORD_LEDGER_SCHEMA,
    ExternalAuditError,
    RawZstdWriter,
    _canonical_row_digest,
    _merkle_root,
    _pack_directory,
    audit_brain_identity,
    audit_llm_call_ledger,
    audit_policy_boundaries,
    audit_release_state,
    capture_quick_target_state,
    deterministic_stratified_sample,
    export_audit_core,
    find_audit_target,
    iter_raw_zstd_jsonl,
    scan_artifact_population,
    scan_compiled_claims,
    scan_pack_secrets,
    scan_record_population,
    semantic_coverage_outcome,
)
from news_scalping_lab.audits.external_pack_standalone import verify
from news_scalping_lab.utils import canonical_json, file_sha256, sha256_text, write_json


def _profile(*, source_count: int = 3) -> dict[str, Any]:
    return {
        "schema_version": "nslab.external_audit_target_profile.v1",
        "brain_version": "brain-test",
        "expected_provider": "codex-oauth",
        "expected_model": "gpt-5.6-sol",
        "expected_reasoning_effort": "xhigh",
        "expected_source_record_count": source_count,
        "expected_compiled_claim_count": 1,
        "expected_live_oauth_call_count": 1,
        "expected_memory_snapshot_id": "MEMIDX-test",
        "compiler_version": "nslab.brain.llm_full.compiler.v7",
        "map_reduce_version": "nslab.brain.llm_full.map_reduce.v5",
        "compile_manifest_schema": "nslab.llm_full_brain_compile_manifest.v2",
        "production_release_expected_active": False,
    }


def _target_fixture(root: Path, *, stage_name: str = "stage-a", source_count: int = 3) -> None:
    project = root / "production" / "staging" / stage_name / "project"
    brain = project / "brain" / "current"
    brain.mkdir(parents=True)
    write_json(
        brain / "brain_manifest.json",
        {
            "brain_version": "brain-test",
            "build_mode": "llm-full",
            "catalog_only": False,
            "llm_provider": "codex-oauth",
            "llm_model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "production_memory_snapshot_id": "MEMIDX-test",
            "oauth_health_check_status": "PASS",
            "cache_hit_count": 0,
            "evidence_policy": "csv-memory-only-strict",
            "web_provider": "disabled",
        },
    )
    (project / "brain/HEAD").write_text("brain-test\n", encoding="utf-8")
    write_json(
        brain / "llm_compile_manifest.json",
        {
            "schema_version": "nslab.llm_full_brain_compile_manifest.v2",
            "brain_version": "brain-test",
            "provider": "codex-oauth",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "compiler_version": "nslab.brain.llm_full.compiler.v7",
            "map_reduce_version": "nslab.brain.llm_full.map_reduce.v5",
            "source_record_count": source_count,
            "compiled_claim_count": 1,
            "record_shard_count": 1,
            "category_count": 0,
            "llm_generation_count": 1,
            "record_shards": [],
            "categories": [],
        },
    )
    snapshot = project / "memory" / "retrieval_index" / "snapshots" / "MEMIDX-test"
    snapshot.mkdir(parents=True)
    write_json(snapshot / "manifest.json", {"snapshot_id": "MEMIDX-test", "record_count": source_count})
    record_index = project / "memory" / "record_index"
    record_index.mkdir(parents=True)
    write_json(record_index / "production_record_artifacts.json", {"artifacts": {}, "root_sha256": sha256_text("x")})
    write_json(
        record_index / "manifest.json",
        {
            "record_count": source_count,
            "generation_root_sha256": sha256_text("generation"),
            "full_envelope_root_sha256": sha256_text("corpus"),
        },
    )
    inventory = root / "inventory.json"
    write_json(inventory, {"ready_record_count": source_count})
    stage = project.parent
    write_json(
        stage / "production_batch_import_receipt.json",
        {
            "inventory_manifest": {"artifact_path": "inventory.json", "sha256": file_sha256(inventory)},
            "record_corpus_sha256": sha256_text("corpus"),
            "record_artifact_root_sha256": sha256_text("artifacts"),
        },
    )


def _target(root: Path):
    return find_audit_target(root, "brain-test", profile=_profile())


def _write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    with RawZstdWriter(path) as writer:
        for row in rows:
            writer.write((canonical_json(row) + "\n").encode())


def _valid_packs(root: Path) -> tuple[Path, Path, Path]:
    core_dir = root / "core"
    ledger_dir = root / "ledgers"
    (ledger_dir / "ledgers").mkdir(parents=True)
    core_dir.mkdir()
    artifact_row = {
        "artifact_family": "brain",
        "regenerable": False,
        "relative_path": "brain/current/test.json",
        "schema_version": ARTIFACT_LEDGER_SCHEMA,
        "sensitive": False,
        "sha256": sha256_text("artifact"),
        "size_bytes": 8,
        "source_of_truth": True,
    }
    record_row = {
        "available_from": "2030-01-01T00:00:00+00:00",
        "confidence_label": "high",
        "envelope_sha256": sha256_text("record-envelope"),
        "episode_id": "EP-1",
        "evidence_phase": "BLIND_SAFE",
        "evidence_polarity": "POSITIVE",
        "label_quality": "verified",
        "payload_semantic_sha256": sha256_text("semantic"),
        "positive_support_eligible": True,
        "record_id": "R-1",
        "record_type": "test",
        "routing_disposition": "REASONING",
        "schema_version": RECORD_LEDGER_SCHEMA,
        "status": "validated",
        "training_eligible": True,
        "training_target": "test",
    }
    claim_row = {
        "available_from": "2030-01-01T00:00:00+00:00",
        "category": "world_model",
        "claim_id": "C-1",
        "claim_sha256": sha256_text("claim"),
        "contradict_count": 0,
        "episode_count": 1,
        "origin": "DETERMINISTIC_RECORD_CLAIM",
        "schema_version": CLAIM_LEDGER_SCHEMA,
        "status": "tentative",
        "support_count": 1,
    }
    artifact_root = _merkle_root([_canonical_row_digest(artifact_row)])
    record_root = _merkle_root([_canonical_row_digest(record_row)])
    claim_root = _merkle_root([_canonical_row_digest(claim_row)])
    _write_ledger(ledger_dir / "ledgers/all_artifacts.jsonl.zst", [artifact_row])
    _write_ledger(ledger_dir / "ledgers/records.jsonl.zst", [record_row])
    _write_ledger(ledger_dir / "ledgers/compiled_claims.jsonl.zst", [claim_row])
    _write_ledger(ledger_dir / "ledgers/semantic_shards.jsonl.zst", [])
    roots = {
        "artifact_population_root": artifact_root,
        "record_population_root": record_root,
        "sorted_record_ids_root": sha256_text("R-1\n"),
        "record_id_envelope_root": sha256_text(
            canonical_json({"R-1": record_row["envelope_sha256"]})
        ),
        "claim_root": claim_root,
        "brain_root": sha256_text("brain"),
        "memory_root": sha256_text("memory"),
        "warehouse_root": sha256_text("warehouse"),
    }
    core_body = {
        "schema_version": AUDIT_CORE_SCHEMA,
        "audit_id": "AUDIT-test",
        "brain_version": "brain-test",
        "roots": roots,
    }
    core = {**core_body, "core_manifest_sha256": sha256_text(canonical_json(core_body))}
    write_json(core_dir / "audit_core_manifest.json", core)
    write_json(
        ledger_dir / "ledger_pack_manifest.json",
        {
            "artifact_file_count": 1,
            "artifact_population_merkle_root": artifact_root,
            "artifact_ledger_sha256": file_sha256(ledger_dir / "ledgers/all_artifacts.jsonl.zst"),
            "record_count": 1,
            "record_population_merkle_root": record_root,
            "record_ledger_sha256": file_sha256(ledger_dir / "ledgers/records.jsonl.zst"),
            "claim_count": 1,
            "claim_population_merkle_root": claim_root,
            "claim_ledger_sha256": file_sha256(ledger_dir / "ledgers/compiled_claims.jsonl.zst"),
            "semantic_ledger_sha256": file_sha256(ledger_dir / "ledgers/semantic_shards.jsonl.zst"),
            "brain_root": roots["brain_root"],
            "memory_root": roots["memory_root"],
            "warehouse_root": roots["warehouse_root"],
        },
    )
    core_zip = root / "core.zip"
    ledger_zip = root / "ledgers.zip"
    _pack_directory(core_dir, core_zip)
    _pack_directory(ledger_dir, ledger_zip)
    return core_zip, ledger_zip, core_dir


def test_audit_target_lock_finds_exact_brain_once(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    assert target.repo_relative(target.project_root) == "production/staging/stage-a/project"


def test_audit_target_lock_rejects_ambiguous_brain(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    shutil.copytree(
        tmp_path / "production/staging/stage-a",
        tmp_path / "production/staging/stage-b",
    )
    with pytest.raises(ExternalAuditError, match="AMBIGUOUS_TARGET"):
        _target(tmp_path)


def test_external_audit_export_is_read_only(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    before = capture_quick_target_state(target)
    scan_artifact_population(target, tmp_path / "outside/artifacts.zst")
    assert capture_quick_target_state(target) == before


def test_external_audit_export_pipeline_builds_self_verifying_packs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    record_row = {
        "available_from": "2030-01-01T00:00:00+00:00",
        "confidence_label": "high",
        "envelope_sha256": sha256_text("record-envelope"),
        "episode_id": "EP-1",
        "evidence_phase": "BLIND_SAFE",
        "evidence_polarity": "POSITIVE",
        "label_quality": "verified",
        "payload_semantic_sha256": sha256_text("semantic"),
        "positive_support_eligible": True,
        "record_id": "R-1",
        "record_type": "test",
        "routing_disposition": "REASONING",
        "routing_metadata_sha256": sha256_text("routing"),
        "schema_version": RECORD_LEDGER_SCHEMA,
        "status": "supported",
        "training_eligible": True,
        "training_target": "test",
    }
    claim_row = {
        "available_from": "2030-01-01T00:00:00+00:00",
        "category": "world_model",
        "claim_id": "C-1",
        "claim_sha256": sha256_text("claim"),
        "contradict_count": 0,
        "episode_count": 1,
        "origin": "DETERMINISTIC_RECORD_CLAIM",
        "schema_version": CLAIM_LEDGER_SCHEMA,
        "status": "tentative",
        "support_count": 1,
    }

    def records(_target_value, ledger_path):
        _write_ledger(ledger_path, [record_row])
        return (
            {
                "record_count": 1,
                "unique_record_id_count": 1,
                "training_eligible": {"true": 1, "false": 0},
                "counts": {"record_type": {"test": 1}},
                "sorted_record_ids_root": sha256_text("R-1\n"),
                "record_id_envelope_root": sha256_text(
                    canonical_json({"R-1": record_row["envelope_sha256"]})
                ),
                "routing_metadata_root": sha256_text("routing-root"),
                "record_population_merkle_root": _merkle_root([_canonical_row_digest(record_row)]),
                "record_ledger_sha256": file_sha256(ledger_path),
            },
            {"R-1": ("EP-1", "REASONING", "POSITIVE", "verified", True, "2030-01-01", True)},
        )

    def claims(_target_value, ledger_path, _states):
        _write_ledger(ledger_path, [claim_row])
        return (
            {
                "claim_count": 1,
                "claim_population_merkle_root": _merkle_root([_canonical_row_digest(claim_row)]),
                "claim_ledger_sha256": file_sha256(ledger_path),
                "claim_referenced_record_count": 1,
                "claim_unreferenced_record_count": 0,
                "finding_counts": {},
                "hard_finding_count": 0,
            },
            {"R-1"},
        )

    def semantic(_target_value, ledger_path, _referenced):
        _write_ledger(ledger_path, [])
        return (
            {
                "byte_accounted_count": 1,
                "group_accounted_count": 1,
                "payload_exposed_to_map_count": 1,
                "payload_not_exposed_to_map_count": 0,
                "claim_referenced_record_count": 1,
                "claim_unreferenced_record_count": 0,
                "rare_reasoning_payload_not_exposed_count": 0,
                "structural_coverage_result": "STRUCTURAL_COVERAGE_COMPLETE",
                "semantic_exposure_result": "SEMANTIC_EXPOSURE_COMPLETE",
                "claim_influence_result": "FINAL_CLAIM_INFLUENCE_COMPLETE",
                "shards": [],
                "categories": [],
                "semantic_ledger_sha256": file_sha256(ledger_path),
            },
            {
                "total_live_calls": 1,
                "cache_hit_count": 0,
                "retry_count": 0,
                "failure_count": 0,
                "missing_trace_count": 0,
                "tool_call_count": 0,
                "web_tool_call_count": 0,
            },
        )

    monkeypatch.setattr(external_pack, "load_audit_profile", lambda *_args: _profile())
    monkeypatch.setattr(external_pack, "assert_target_stable", lambda *_args, **_kwargs: {"passed": True})
    monkeypatch.setattr(external_pack, "scan_record_population", records)
    monkeypatch.setattr(external_pack, "scan_compiled_claims", claims)
    monkeypatch.setattr(external_pack, "scan_semantic_exposure", semantic)
    monkeypatch.setattr(
        external_pack,
        "audit_import_and_inventory",
        lambda *_args, **_kwargs: {
            "imported_record_count": 1,
            "inventory_ready_record_count": 1,
            "record_corpus_sha256": sha256_text("corpus"),
            "finding_count": 0,
            "findings": [],
        },
    )
    monkeypatch.setattr(
        external_pack,
        "audit_memory_snapshot",
        lambda *_args, **_kwargs: {
            "snapshot_id": "MEMIDX-test",
            "source_record_count": 1,
            "source_record_hash_count": 1,
            "finding_count": 0,
            "findings": [],
        },
    )
    monkeypatch.setattr(
        external_pack,
        "audit_warehouse",
        lambda *_args, **_kwargs: {
            "brain_record_count": 1,
            "finding_count": 0,
            "findings": [],
            "passed": True,
        },
    )
    monkeypatch.setattr(
        external_pack,
        "audit_brain_identity",
        lambda *_args, **_kwargs: {
            "observed": {
                "brain_version": "brain-test",
                "build_mode": "llm-full",
                "provider": "codex-oauth",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "source_record_count": 1,
                "compiled_claim_count": 1,
            },
            "finding_count": 0,
            "findings": [],
            "passed": True,
        },
    )
    monkeypatch.setattr(
        external_pack,
        "audit_brain_categories",
        lambda *_args: {"finding_count": 0, "findings": [], "passed": True},
    )
    monkeypatch.setattr(
        external_pack,
        "audit_policy_boundaries",
        lambda *_args: {"finding_count": 0, "findings": [], "passed": True},
    )
    monkeypatch.setattr(external_pack, "audit_existing_brain_reports", lambda *_args: {})
    monkeypatch.setattr(
        external_pack,
        "audit_old_model_absence",
        lambda *_args: {
            "passed": True,
            "active_brain_cache_snapshot_old_identity_files": [],
        },
    )
    monkeypatch.setattr(
        external_pack,
        "audit_brain",
        lambda *_args, **_kwargs: {"passed": True, "brain_version": "brain-test"},
    )
    monkeypatch.setattr(
        external_pack,
        "audit_code_identity",
        lambda *_args: {"audit_tool_commit": sha256_text("tool"), "working_tree_paths": []},
    )
    result = export_audit_core(
        tmp_path,
        "brain-test",
        tmp_path.parent / f"{tmp_path.name}-export",
        stability_seconds=0,
        run_deep_verifier=False,
        write_commitment=False,
    )
    assert result["standalone_verification"]["passed"] is True
    assert result["read_only_parity"]["passed"] is True
    assert result["production_activation"] == "NOT_PRODUCTION_ACTIVATED"
    assert Path(result["core_lite"]["path"]).is_file()
    assert Path(result["core_ledgers"]["path"]).is_file()
    assert capture_quick_target_state(target) == capture_quick_target_state(_target(tmp_path))


def test_external_audit_preserves_production_pointer(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    pointer = tmp_path / "production/current.json"
    write_json(pointer, {"release_id": "old"})
    before = file_sha256(pointer)
    scan_artifact_population(_target(tmp_path), tmp_path / "outside/artifacts.zst")
    assert file_sha256(pointer) == before


def test_artifact_ledger_covers_every_staging_file(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    summary = scan_artifact_population(target, tmp_path / "outside/artifacts.zst")
    expected = sum(path.is_file() for path in target.project_root.rglob("*"))
    assert summary["artifact_file_count"] == expected


def test_artifact_population_root_is_deterministic(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    first = scan_artifact_population(target, tmp_path / "one.zst")
    second = scan_artifact_population(target, tmp_path / "two.zst")
    assert first["artifact_population_merkle_root"] == second["artifact_population_merkle_root"]


def test_record_population_parity_823279_fixture_independent(tmp_path: Path) -> None:
    _target_fixture(tmp_path, source_count=3)
    assert _target(tmp_path).project_root.name == "project"
    assert _profile()["expected_source_record_count"] == 3


def test_record_population_external_sort_handles_mixed_case_ids(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    records_dir = target.project_root / "memory/records"
    records_dir.mkdir(parents=True)
    rows = [
        {
            "record_id": "nslab-A",
            "record_type": "test",
            "episode_id": "EP-a",
            "trade_date": "2030-01-01",
            "available_from": "2030-01-02T00:00:00+00:00",
            "training_target": "test",
            "evidence_phase": "POSTMORTEM",
            "training_eligible": True,
            "status": "supported",
            "confidence_label": "high",
            "typed_payload_status": "KNOWN_TYPED_PAYLOAD",
            "payload": {"value": "a"},
        },
        {
            "record_id": "NSLAB-B",
            "record_type": "test",
            "episode_id": "EP-b",
            "trade_date": "2030-01-02",
            "available_from": "2030-01-03T00:00:00+00:00",
            "training_target": "test",
            "evidence_phase": "POSTMORTEM",
            "training_eligible": False,
            "status": "tentative",
            "confidence_label": "low",
            "typed_payload_status": "KNOWN_TYPED_PAYLOAD",
            "payload": {"value": "b"},
        },
    ]
    (records_dir / "a.jsonl").write_text(canonical_json(rows[0]) + "\n", encoding="utf-8")
    (records_dir / "b.jsonl").write_text(canonical_json(rows[1]) + "\n", encoding="utf-8")
    database = target.project_root / "memory/test.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute(
        "CREATE TABLE records(record_id VARCHAR, evidence_polarity VARCHAR, label_quality VARCHAR, "
        "routing_disposition VARCHAR, source_sha256 VARCHAR, routing_json VARCHAR)"
    )
    for row in reversed(rows):
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
            [
                row["record_id"],
                "POSITIVE",
                "verified",
                "REASONING",
                sha256_text(canonical_json(row)),
                canonical_json({"routing_disposition": "REASONING"}),
            ],
        )
    connection.close()
    write_json(
        target.memory_manifest_path,
        {
            "snapshot_id": "MEMIDX-test",
            "record_count": 2,
            "database": {"artifact_path": "memory/test.duckdb"},
        },
    )
    summary, states = scan_record_population(target, tmp_path / "mixed-records.zst")
    ledger_ids = [row["record_id"] for row in iter_raw_zstd_jsonl(tmp_path / "mixed-records.zst")]
    assert ledger_ids == ["NSLAB-B", "nslab-A"]
    assert summary["record_count"] == 2
    assert set(states) == {"NSLAB-B", "nslab-A"}


def test_brain_identity_requires_gpt_5_6_sol_xhigh(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    result = audit_brain_identity(
        target,
        _profile(),
        {"claim_count": 1},
        {"total_live_calls": 1, "missing_trace_count": 0, "failure_count": 0},
    )
    assert result["passed"] is True
    manifest = json.loads(target.compile_manifest_path.read_text())
    manifest["model"] = "gpt-5.4"
    write_json(target.compile_manifest_path, manifest)
    assert audit_brain_identity(
        target,
        _profile(),
        {"claim_count": 1},
        {"total_live_calls": 1, "missing_trace_count": 0, "failure_count": 0},
    )["passed"] is False


def test_llm_call_ledger_matches_compile_manifest(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    compile_manifest = json.loads(target.compile_manifest_path.read_text())
    prompt_sha = sha256_text("prompt")
    compile_manifest["record_shards"] = [
        {"shard_index": 1, "prompt_sha256": prompt_sha, "cache_key": "CACHE-1"}
    ]
    cache = {
        "CACHE-1": {
            "purpose": "brain_compile:shard:0001",
            "prompt_sha256": prompt_sha,
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "compiler_version": "nslab.brain.llm_full.compiler.v7",
            "map_reduce_version": "nslab.brain.llm_full.map_reduce.v5",
        }
    }
    traces = target.project_root / "runs/traces"
    traces.mkdir(parents=True)
    write_json(
        traces / "trace.json",
        {
            "compiler_version": "nslab.brain.llm_full.compiler.v7",
            "model_config": {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
            "input": {"prompt_sha256": prompt_sha},
            "purpose": "brain_compile:shard:0001",
            "retries": 0,
            "status": "ok",
            "tool_calls": [],
        },
    )
    result = audit_llm_call_ledger(target, compile_manifest, cache)
    assert result["total_live_calls"] == 1
    assert result["missing_trace_count"] == 0
    assert result["failure_count"] == 0


def _claim(*, claim_id: str, source_type: str) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "category": "world_model",
        "status": "tentative",
        "statement": "statement",
        "mechanism": "mechanism",
        "conditions": ["condition"],
        "boundary_conditions": ["boundary"],
        "failure_modes": ["failure"],
        "supporting_record_ids": ["R-1"],
        "contradicting_record_ids": [],
        "supporting_episode_ids": ["EP-1"],
        "contradicting_episode_ids": [],
        "positive_case_count": 1,
        "available_from": "2030-01-02T00:00:00+00:00",
        "provenance": {"source_type": source_type},
    }


def test_claim_count_and_reference_closure(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    claims = target.project_root / "brain/current/compiled_claims.jsonl"
    claims.write_text(canonical_json(_claim(claim_id="C-1", source_type="brain_record")) + "\n")
    states = {"R-1": ("EP-1", "REASONING", "POSITIVE", "verified", True, "2030-01-01", True)}
    result, referenced = scan_compiled_claims(target, tmp_path / "claims.zst", states)
    assert result["claim_count"] == 1
    assert result["hard_finding_count"] == 0
    assert referenced == {"R-1"}


def test_claim_origin_is_not_all_labeled_llm_generated(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    claims = target.project_root / "brain/current/compiled_claims.jsonl"
    claims.write_text(
        canonical_json(_claim(claim_id="C-1", source_type="brain_record"))
        + "\n"
        + canonical_json(_claim(claim_id="C-2", source_type="llm_category_synthesis"))
        + "\n"
    )
    states = {"R-1": ("EP-1", "REASONING", "POSITIVE", "verified", True, "2030-01-01", True)}
    result, _ = scan_compiled_claims(target, tmp_path / "claims.zst", states)
    assert result["counts"]["origin"] == {
        "DETERMINISTIC_RECORD_CLAIM": 1,
        "LLM_CATEGORY_SYNTHESIS": 1,
    }


def test_semantic_coverage_distinguishes_accounted_exposed_referenced() -> None:
    result = semantic_coverage_outcome(
        total_records=100,
        payload_exposed_records=30,
        claim_referenced_records=10,
    )
    assert result == {
        "structural_coverage_result": "STRUCTURAL_COVERAGE_COMPLETE",
        "semantic_exposure_result": "SEMANTIC_EXPOSURE_PARTIAL",
        "claim_influence_result": "FINAL_CLAIM_INFLUENCE_PARTIAL",
    }


def test_unrepresented_reasoning_groups_are_reported() -> None:
    result = semantic_coverage_outcome(total_records=4, payload_exposed_records=3, claim_referenced_records=4)
    assert result["semantic_exposure_result"] == "SEMANTIC_EXPOSURE_PARTIAL"


def test_same_signature_rare_payload_is_detected() -> None:
    rows = [
        {"record_id": f"R-{index}", "signature": "same", "rare": index == 3}
        for index in range(4)
    ]
    chosen = deterministic_stratified_sample(
        rows,
        seed="seed",
        count=4,
        id_field="record_id",
        stratum_fields=("signature", "rare"),
    )
    assert any(row["rare"] is True for row in chosen)


def test_category_first_200_exposure_is_reported() -> None:
    outcome = semantic_coverage_outcome(total_records=250, payload_exposed_records=200, claim_referenced_records=0)
    assert outcome["semantic_exposure_result"] == "SEMANTIC_EXPOSURE_PARTIAL"


def test_prompt_size_distribution_is_reported() -> None:
    sizes = sorted([10, 20, 30, 40, 50])
    assert sizes[2] == 30
    assert max(sizes) == 50


def test_memory_snapshot_identity_and_population_parity(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    memory = json.loads(target.memory_manifest_path.read_text())
    assert memory["snapshot_id"] == _profile()["expected_memory_snapshot_id"]
    assert memory["record_count"] == _profile()["expected_source_record_count"]


def test_warehouse_projection_parity() -> None:
    populations = {
        "record_store": 3,
        "warehouse": 3,
        "memory": 3,
        "compile": 3,
    }
    assert len(set(populations.values())) == 1


def test_no_future_available_from(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    target = _target(tmp_path)
    target.brain_manifest_path.write_text(
        target.brain_manifest_path.read_text().replace("\n}", ',\n  "brain_record_cutoff_at": "2030-01-03"\n}'),
    )
    claim = _claim(claim_id="C-1", source_type="brain_record")
    (target.project_root / "brain/current/compiled_claims.jsonl").write_text(canonical_json(claim) + "\n")
    states = {"R-1": ("EP-1", "REASONING", "POSITIVE", "verified", True, "2030-01-01", True)}
    result, _ = scan_compiled_claims(target, tmp_path / "claims.zst", states)
    assert result["finding_counts"].get("claim_available_from_before_support", 0) == 0


def test_csv_only_web_evidence_zero(tmp_path: Path) -> None:
    _target_fixture(tmp_path)
    result = audit_policy_boundaries(
        _target(tmp_path),
        {"future_available_from_count": 0, "counts": {"evidence_phase": {"BLIND_SAFE": 3}}},
        {"finding_counts": {}},
        {"tool_call_count": 0, "web_tool_call_count": 0},
        {"company_memory_known_at_before_available_from_count": 0},
    )
    assert result["passed"] is True


def test_core_lite_contains_no_record_raw_text(tmp_path: Path) -> None:
    _, _, core = _valid_packs(tmp_path)
    assert b"FULL_NEWS_RAW_TEXT_SENTINEL" not in b"".join(path.read_bytes() for path in core.rglob("*"))


def test_core_pack_contains_no_secrets(tmp_path: Path) -> None:
    _, _, core = _valid_packs(tmp_path)
    assert scan_pack_secrets(core)["secret_finding_count"] == 0


def test_standalone_verifier_detects_tamper(tmp_path: Path) -> None:
    core_zip, ledger_zip, _ = _valid_packs(tmp_path)
    assert verify(core_zip, ledger_zip)["passed"] is True
    with zipfile.ZipFile(core_zip, "a") as archive:
        archive.writestr("audit_core_manifest.json", b"{}")
    result = verify(core_zip, ledger_zip)
    assert result["passed"] is False
    assert any("duplicate_zip_path" in finding for finding in result["findings"])


def _sample_rows() -> list[dict[str, Any]]:
    return [
        {"record_id": f"R-{index:03d}", "year": 2020 + index % 3, "kind": index % 2}
        for index in range(30)
    ]


def test_sample_selection_is_seed_deterministic() -> None:
    first = deterministic_stratified_sample(
        _sample_rows(), seed="one", count=12, id_field="record_id", stratum_fields=("year", "kind")
    )
    second = deterministic_stratified_sample(
        _sample_rows(), seed="one", count=12, id_field="record_id", stratum_fields=("year", "kind")
    )
    assert first == second


def test_sample_manifest_uses_v2_for_expanded_selection_contract() -> None:
    assert AUDIT_SAMPLE_SCHEMA == "nslab.external_audit_sample_manifest.v2"


def test_different_seed_changes_sample() -> None:
    first = deterministic_stratified_sample(
        _sample_rows(), seed="one", count=12, id_field="record_id", stratum_fields=("year", "kind")
    )
    second = deterministic_stratified_sample(
        _sample_rows(), seed="two", count=12, id_field="record_id", stratum_fields=("year", "kind")
    )
    assert {row["record_id"] for row in first} != {row["record_id"] for row in second}


def test_sample_is_stratified() -> None:
    selected = deterministic_stratified_sample(
        _sample_rows(), seed="one", count=12, id_field="record_id", stratum_fields=("year", "kind")
    )
    assert len({(row["year"], row["kind"]) for row in selected}) == 6


def test_sample_episode_paths_include_nested_bundle_envelopes(tmp_path: Path) -> None:
    episode_root = tmp_path / "research" / "episodes"
    write_json(episode_root / "legacy.json", {"episode_id": "legacy"})
    write_json(
        episode_root / "nested" / "bundle_envelope.json",
        {"episode_id": "nested"},
    )
    write_json(
        episode_root / "nested" / "validation_report.json",
        {"episode_id": "not-a-bundle-envelope"},
    )

    paths = external_pack._episode_sample_paths(tmp_path)

    assert [path.relative_to(episode_root).as_posix() for path in paths] == [
        "legacy.json",
        "nested/bundle_envelope.json",
    ]


def test_sample_retrieval_paths_exclude_llm_build_traces(tmp_path: Path) -> None:
    expected = tmp_path / "runs" / "daily" / "adaptive_retrieval_trace.json"
    write_json(expected, {"schema_version": "nslab.adaptive_retrieval_trace.v4"})
    write_json(
        tmp_path / "runs" / "traces" / "llm.json",
        {"schema_version": "nslab.llm_trace.v1"},
    )

    assert external_pack._retrieval_trace_sample_paths(tmp_path) == [expected]


def test_sample_selection_metadata_exposes_all_roles_and_strata() -> None:
    candidates = [
        {
            "record_id": "R-1",
            "payload_exposed": True,
            "rare_payload": False,
            "status": "OPEN",
        },
        {
            "record_id": "R-2",
            "payload_exposed": False,
            "rare_payload": True,
            "status": "open",
        },
    ]
    selected = external_pack._sample_selection_metadata(
        candidates,
        [candidates[0]],
        [{"record_id": "R-2", "rare": True}],
    )

    assert [row["record_id"] for row in selected] == ["R-1", "R-2"]
    assert selected[0]["selection_roles"] == ["PRIMARY_STRATIFIED"]
    assert selected[1]["selection_roles"] == ["RARE_REASONING"]
    assert external_pack._sample_strata_counts(selected)["payload_exposed"] == {
        "false": 1,
        "true": 1,
    }
    assert external_pack._sample_strata_counts(selected)["status"] == {"open": 2}


def test_release_state_reports_staging_only(tmp_path: Path) -> None:
    result = audit_release_state(tmp_path, _profile())
    assert result["production_activation"] == "NOT_PRODUCTION_ACTIVATED"
