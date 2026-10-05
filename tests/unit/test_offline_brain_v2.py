from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pytest

import news_scalping_lab.brain.offline_v2 as offline_v2
from news_scalping_lab.brain.offline_v2 import (
    BrainPackageDailyContextProvider,
    OfflineBrainBuildResult,
    OfflineBrainPlanResult,
    OfflineSemanticBrainCompiler,
    _claims_from_reduce_node,
    _copy_resume_work_database,
    _estimate_reduce_review_call_count,
    _load_reduce_nodes_from_database,
    _materialize_long_payload_chunk_digest,
    _pack_reduce_nodes,
    _persist_reduce_node,
    _plan_long_payloads,
    _plan_reduce_graph,
    _planned_reduce_leaf_nodes,
    _split_semantic_stratum,
    _utf8_chunks,
    _VectorRow,
    _write_capsules_to_database,
    _write_claims_to_database,
    resolve_source_memory_snapshot,
    select_brain_package,
)
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.offline_brain import (
    CurrentDayInterpretation,
    CurrentEventCapsule,
    LongPayloadChunkDigestDraft,
    LongPayloadDigestBatch,
    MechanismClaimDraft,
    SemanticCapsuleDraftBatch,
    SemanticMemoryCapsule,
    SemanticReduceClaimDraft,
    SemanticReduceDraft,
    SemanticReduceNode,
    SynthesizedMechanismClaim,
)
from news_scalping_lab.inference.thin_daily import _validate_brain_context_as_of
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    write_json,
)


class Embedding384:
    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        del purpose
        vectors: list[list[float]] = []
        for text in texts:
            vector = np.zeros(384, dtype=np.float32)
            vector[sum(text.encode("utf-8")) % 8] = 1.0
            vectors.append(vector.tolist())
        return vectors


class RecordingEmbedding384(Embedding384):
    def __init__(self) -> None:
        self.queries: list[list[str]] = []

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        self.queries.append(list(texts))
        return await super().embed(texts=texts, purpose=purpose)


class ReduceCoverageMismatchLLM(DeterministicMockLLMProvider):
    def __init__(self, *, omit_child: bool = False) -> None:
        super().__init__()
        self.omit_child = omit_child

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[Any],
        purpose: str,
    ) -> Any:
        if response_model is not SemanticReduceDraft:
            return await super().generate_structured(
                prompt=prompt,
                response_model=response_model,
                purpose=purpose,
            )
        payload = json.loads(prompt.split("---OFFLINE_SEMANTIC_REDUCE---\n", 1)[1])
        child_node_ids = list(payload["required_child_node_ids"])
        if self.omit_child:
            child_node_ids.pop()
        allowed_capsule_ids = list(payload["available_evidence_capsule_ids"])
        return SemanticReduceDraft(
            node_id=payload["node_id"],
            child_node_ids=child_node_ids,
            synthesis="fixture reduce",
            claims=[
                SemanticReduceClaimDraft(
                    statement="unsupported source citation",
                    mechanism="fixture",
                    supporting_capsule_ids=["CAP-not-in-child-tree"],
                    confidence="low",
                    status="unsupported",
                )
            ] if allowed_capsule_ids else [],
        )


class ReduceCitationSuffixLLM(DeterministicMockLLMProvider):
    def __init__(self, suffix: str, *, capsule_id: str | None = None) -> None:
        super().__init__()
        self.suffix = suffix
        self.capsule_id = capsule_id

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[Any],
        purpose: str,
    ) -> Any:
        if response_model is not SemanticReduceDraft:
            return await super().generate_structured(
                prompt=prompt,
                response_model=response_model,
                purpose=purpose,
            )
        payload = json.loads(prompt.split("---OFFLINE_SEMANTIC_REDUCE---\n", 1)[1])
        capsule_id = self.capsule_id or payload["available_evidence_capsule_ids"][0]
        return SemanticReduceDraft(
            node_id=payload["node_id"],
            child_node_ids=payload["required_child_node_ids"],
            synthesis="fixture reduce with one malformed citation token",
            claims=[
                SemanticReduceClaimDraft(
                    statement="evidence supports the fixture mechanism",
                    mechanism="fixture",
                    supporting_capsule_ids=[capsule_id + self.suffix],
                    confidence="low",
                    status="supported",
                )
            ],
        )


class ReduceCitationRepairLLM(DeterministicMockLLMProvider):
    def __init__(self, *, repair_valid: bool = True) -> None:
        super().__init__()
        self.repair_valid = repair_valid
        self.prompts: list[str] = []
        self.purposes: list[str] = []

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[Any],
        purpose: str,
    ) -> Any:
        if response_model is not SemanticReduceDraft:
            return await super().generate_structured(
                prompt=prompt,
                response_model=response_model,
                purpose=purpose,
            )
        self.prompts.append(prompt)
        self.purposes.append(purpose)
        payload = json.loads(prompt.split("---OFFLINE_SEMANTIC_REDUCE---\n", 1)[1])
        is_repair = "---OFFLINE_SEMANTIC_CITATION_REPAIR---" in prompt
        capsule_id = (
            payload["available_evidence_capsule_ids"][0]
            if is_repair and self.repair_valid
            else "CAP-not-in-child-tree"
        )
        return SemanticReduceDraft(
            node_id=payload["node_id"],
            child_node_ids=payload["required_child_node_ids"],
            synthesis="fixture reduce with a corrected citation",
            claims=[
                SemanticReduceClaimDraft(
                    statement="evidence supports the fixture mechanism",
                    mechanism="fixture",
                    supporting_capsule_ids=[capsule_id],
                    confidence="low",
                    status="supported",
                )
            ],
        )


class ReduceEmptyChildIDsLLM(DeterministicMockLLMProvider):
    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[Any],
        purpose: str,
    ) -> Any:
        if response_model is not SemanticReduceDraft:
            return await super().generate_structured(
                prompt=prompt,
                response_model=response_model,
                purpose=purpose,
            )
        payload = json.loads(prompt.split("---OFFLINE_SEMANTIC_REDUCE---\n", 1)[1])
        return SemanticReduceDraft(
            node_id=payload["node_id"],
            child_node_ids=[],
            synthesis="fixture reduce with omitted child identity",
        )


class CapturingReduceLLM(DeterministicMockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.last_prompt = ""

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[Any],
        purpose: str,
    ) -> Any:
        if response_model is SemanticReduceDraft:
            self.last_prompt = prompt
        return await super().generate_structured(
            prompt=prompt,
            response_model=response_model,
            purpose=purpose,
        )


class TrackingLongPayloadLLM(DeterministicMockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.long_payload_calls = 0

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[Any],
        purpose: str,
    ) -> Any:
        if response_model is LongPayloadDigestBatch:
            self.long_payload_calls += 1
        return await super().generate_structured(
            prompt=prompt,
            response_model=response_model,
            purpose=purpose,
        )


def _vector(index: int) -> list[float]:
    value = np.zeros(384, dtype=np.float32)
    value[index] = 1.0
    return value.tolist()


def _fixture_reduce_children() -> list[SemanticReduceNode]:
    return [
        SemanticReduceNode(
            node_id="LEAF-fixture-a",
            child_node_ids=[],
            covered_capsule_ids=["CAP-a", "CAP-b"],
            covered_capsule_count=2,
            coverage_root="root-a",
            evidence_capsule_ids=["CAP-a"],
            synthesis="first child",
        ),
        SemanticReduceNode(
            node_id="LEAF-fixture-b",
            child_node_ids=[],
            covered_capsule_ids=["CAP-c"],
            covered_capsule_count=1,
            coverage_root="root-b",
            evidence_capsule_ids=["CAP-c"],
            synthesis="second child",
        ),
    ]


def _reduce_plan_capsules(*, per_category: int) -> list[SemanticMemoryCapsule]:
    available_from = datetime(2025, 1, 2, 18, 0, tzinfo=KST)
    capsules: list[SemanticMemoryCapsule] = []
    for category in (*offline_v2._CATEGORY_PRECEDENCE, "world_model"):
        used_buckets: set[str] = set()
        candidate = 0
        while len(used_buckets) < per_category:
            capsule_id = f"CAP-{category}-{candidate:04d}"
            bucket = sha256_text(capsule_id)[:2]
            candidate += 1
            if bucket in used_buckets:
                continue
            used_buckets.add(bucket)
            capsules.append(
                SemanticMemoryCapsule(
                    capsule_id=capsule_id,
                    category=category,
                    semantic_unit_id=f"UNIT-{capsule_id}",
                    member_record_count=1,
                    member_independent_unit_count=1,
                    member_record_root=sha256_text(f"record-{capsule_id}"),
                    event_or_mechanism_summary=f"Summary for {capsule_id}",
                    available_from=available_from,
                    provenance_root=sha256_text(f"provenance-{capsule_id}"),
                    embedding=_vector(0),
                )
            )
    return capsules


def test_resume_work_database_copy_is_hash_checked_and_non_destructive(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.duckdb"
    source.write_bytes(b"immutable source database fixture")
    expected_sha256 = file_sha256(source)
    target = tmp_path / "resume" / "semantic_capsule_index.duckdb"

    _copy_resume_work_database(
        source,
        target,
        expected_sha256=expected_sha256,
        expected_wal_sha256=None,
    )

    assert file_sha256(source) == expected_sha256
    assert file_sha256(target) == expected_sha256
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        _copy_resume_work_database(
            source,
            target,
            expected_sha256=expected_sha256,
            expected_wal_sha256=None,
        )

    bad_target = tmp_path / "resume" / "bad.duckdb"
    with pytest.raises(ValueError, match="changed while it was copied"):
        _copy_resume_work_database(
            source,
            bad_target,
            expected_sha256="0" * 64,
            expected_wal_sha256=None,
        )
    assert not bad_target.exists()
    assert not list(bad_target.parent.glob("*.copying"))


def test_resume_work_database_copy_preserves_wal_and_adopts_exact_base_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.duckdb"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import duckdb, os, sys; "
            "connection = duckdb.connect(sys.argv[1]); "
            "connection.execute('CREATE TABLE resume_rows (value INTEGER)'); "
            "connection.execute('INSERT INTO resume_rows SELECT range FROM range(1000)'); "
            "os._exit(0)",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    source_wal = Path(f"{source}.wal")
    assert source_wal.is_file()
    source_sha256 = file_sha256(source)
    source_wal_sha256 = file_sha256(source_wal)

    copied = tmp_path / "copied.duckdb"
    _copy_resume_work_database(
        source,
        copied,
        expected_sha256=source_sha256,
        expected_wal_sha256=source_wal_sha256,
    )
    copied_wal = Path(f"{copied}.wal")
    assert file_sha256(copied) == source_sha256
    assert file_sha256(copied_wal) == source_wal_sha256
    copied_reader = duckdb.connect(str(copied), read_only=True)
    assert copied_reader.execute("SELECT count(*) FROM resume_rows").fetchone() == (1000,)
    copied_reader.close()

    base_copy = tmp_path / "base-copy.duckdb"
    shutil.copy2(source, base_copy)
    assert not Path(f"{base_copy}.wal").exists()
    assert offline_v2._adopt_matching_resume_work_database(
        source,
        base_copy,
        expected_sha256=source_sha256,
        expected_wal_sha256=source_wal_sha256,
    )
    base_copy_wal = Path(f"{base_copy}.wal")
    assert file_sha256(base_copy_wal) == source_wal_sha256
    base_copy_reader = duckdb.connect(str(base_copy), read_only=True)
    assert base_copy_reader.execute("SELECT count(*) FROM resume_rows").fetchone() == (
        1000,
    )
    base_copy_reader.close()


def test_reduce_node_persistence_omits_expanded_coverage_ids() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE reduce_nodes (node_id VARCHAR PRIMARY KEY, payload_json VARCHAR NOT NULL)"
    )
    node = _fixture_reduce_children()[0]
    _persist_reduce_node(connection, node)

    persisted_json = connection.execute(
        "SELECT payload_json FROM reduce_nodes WHERE node_id = ?", [node.node_id]
    ).fetchone()[0]
    persisted = json.loads(persisted_json)
    loaded = _load_reduce_nodes_from_database(connection)[node.node_id]

    assert persisted["covered_capsule_ids"] == []
    assert persisted["covered_capsule_count"] == node.covered_capsule_count
    assert loaded.prompt_sha256 == node.prompt_sha256
    assert loaded.covered_capsule_ids == []
    connection.close()


def test_resume_capsule_and_claim_writes_are_idempotent() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE semantic_capsules (capsule_id VARCHAR PRIMARY KEY, category VARCHAR, "
        "semantic_unit_id VARCHAR, available_from VARCHAR, embedding FLOAT[384], payload_json VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE mechanism_claims (claim_id VARCHAR PRIMARY KEY, category VARCHAR, "
        "available_from VARCHAR, embedding FLOAT[384], payload_json VARCHAR)"
    )
    connection.execute(
        "CREATE TABLE mechanism_claim_capsules (claim_id VARCHAR, capsule_id VARCHAR, role VARCHAR)"
    )
    capsule = _reduce_plan_capsules(per_category=1)[0]
    _write_capsules_to_database(connection, [capsule])
    _write_capsules_to_database(
        connection,
        [capsule.model_copy(update={"event_or_mechanism_summary": "updated capsule"})],
    )
    replacement_capsule = capsule.model_copy(
        update={
            "capsule_id": f"{capsule.capsule_id}-replacement",
            "event_or_mechanism_summary": "replacement capsule",
        }
    )
    _write_capsules_to_database(connection, [replacement_capsule])
    available_from = datetime(2025, 1, 2, 18, 0, tzinfo=KST)
    claim = SynthesizedMechanismClaim(
        claim_id="CLAIM-resume",
        category=capsule.category,
        statement="original claim",
        mechanism="fixture mechanism",
        supporting_capsule_ids=[capsule.capsule_id],
        available_from=available_from,
        confidence="medium",
        status="supported",
        embedding=_vector(0),
    )
    _write_claims_to_database(connection, [claim])
    _write_claims_to_database(
        connection,
        [claim.model_copy(update={"statement": "updated claim"})],
    )

    capsule_row = connection.execute(
        "SELECT count(*), any_value(capsule_id), any_value(payload_json) "
        "FROM semantic_capsules"
    ).fetchone()
    claim_row = connection.execute(
        "SELECT count(*), any_value(payload_json) FROM mechanism_claims"
    ).fetchone()
    relationship_count = connection.execute(
        "SELECT count(*) FROM mechanism_claim_capsules"
    ).fetchone()[0]
    assert capsule_row[0] == 1
    assert capsule_row[1] == replacement_capsule.capsule_id
    assert json.loads(capsule_row[2])["event_or_mechanism_summary"] == "replacement capsule"
    assert claim_row[0] == 1
    assert json.loads(claim_row[1])["statement"] == "updated claim"
    assert relationship_count == 1
    connection.close()


@pytest.mark.asyncio
async def test_reduce_dag_plan_matches_runtime_node_and_child_topology(
    tmp_path: Path,
) -> None:
    capsules = _reduce_plan_capsules(per_category=11)
    leaves, plan = _plan_reduce_graph(capsules)
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path),
        llm=DeterministicMockLLMProvider(),
    )
    category_roots: dict[str, SemanticReduceNode] = {}
    actual_nodes: list[SemanticReduceNode] = []
    for category in (*offline_v2._CATEGORY_PRECEDENCE, "world_model"):
        category_capsules = [row for row in capsules if row.category == category]
        category_leaves = [row for row in leaves if row.category == category]
        root, nodes = await compiler._reduce_category(
            category=category,
            leaves=category_leaves,
            capsules=category_capsules,
        )
        category_roots[category] = root
        actual_nodes.extend(nodes)
    actual_nodes.append(await compiler._reduce_world(category_roots))

    expected = {
        str(row["node_id"]): tuple(row["child_node_ids"])
        for row in plan["tasks"]
    }
    actual = {row.node_id: tuple(row.child_node_ids) for row in actual_nodes}
    assert plan["category_count"] == 9
    assert plan["world_root_count"] == 1
    assert plan["total_model_tasks"] == len(actual_nodes)
    assert actual == expected


def test_reduce_dag_planner_rejects_non_convergent_packing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(offline_v2, "MAX_REDUCE_CHILDREN", 1)
    with pytest.raises(ValueError, match="cannot converge in planned DAG"):
        _plan_reduce_graph(_reduce_plan_capsules(per_category=2))


def _source_project(root: Path, *, oversized_document: bool = False) -> Path:
    snapshot_id = "MEMIDX-fixture"
    snapshot_root = root / "memory" / "retrieval_index" / "snapshots" / snapshot_id
    snapshot_root.mkdir(parents=True)
    database_path = snapshot_root / "memory.duckdb"
    connection = duckdb.connect(str(database_path))
    connection.execute(
        """
        CREATE TABLE records (
            record_id VARCHAR,
            primary_cell_id VARCHAR,
            evidence_polarity VARCHAR,
            record_type VARCHAR,
            independent_unit_id VARCHAR,
            independent_unit_type VARCHAR,
            source_sha256 VARCHAR,
            embedding FLOAT[384],
            label_quality VARCHAR,
            training_eligible BOOLEAN,
            trade_date DATE,
            available_from VARCHAR,
            document VARCHAR,
            high_return_status VARCHAR,
            close_return_status VARCHAR,
            upper_limit_status VARCHAR,
            regime_cluster VARCHAR,
            routing_disposition VARCHAR
        )
        """
    )
    rows: list[tuple[Any, ...]] = []
    for index in range(12):
        positive = index < 8
        embedding_axis = 0 if index < 4 else (1 if index < 8 else 2)
        record_type = (
            "supervised_direct_event_case" if index < 8 else "negative_control_case"
        )
        rows.append(
            (
                f"REC-{index:03d}",
                "CELL-shared" if index < 8 else "CELL-negative",
                "POSITIVE" if positive else "NEGATIVE",
                record_type,
                f"IU-{index:03d}",
                "event-issuer-day",
                f"{index:064x}",
                _vector(embedding_axis),
                "verified",
                True,
                date(2025, 1, index + 1),
                datetime(2025, 1, index + 1, 18, 0, tzinfo=KST).isoformat(),
                (
                    "x" * 220_000
                    if oversized_document and index == 0
                    else f"fixture mechanism payload {index} axis {embedding_axis}"
                ),
                "OBSERVED_POSITIVE" if positive else "OBSERVED_NEGATIVE",
                ("VALID", "MISSING", "INVALID_CONFLICT")[index % 3],
                "TOUCHED" if index == 0 else "NOT_TOUCHED",
                "fixture-regime",
                "REASONING",
            )
        )
    connection.executemany("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    connection.close()

    database_hash = file_sha256(database_path)
    manifest_path = snapshot_root / "manifest.json"
    write_json(
        manifest_path,
        {
            "snapshot_id": snapshot_id,
            "record_count": len(rows),
            "embedding_model": "fixture-real-embedding-384",
            "embedding_dimensions": 384,
            "as_of_cutoff": datetime(2025, 1, 31, 18, 0, tzinfo=KST).isoformat(),
            "database": {
                "artifact_path": f"memory/retrieval_index/snapshots/{snapshot_id}/memory.duckdb",
                "sha256": database_hash,
            },
        },
    )
    current_path = root / "memory" / "retrieval_index" / "current.json"
    write_json(
        current_path,
        {
            "snapshot_id": snapshot_id,
            "manifest_path": f"memory/retrieval_index/snapshots/{snapshot_id}/manifest.json",
            "manifest_sha256": file_sha256(manifest_path),
        },
    )
    write_json(
        root / "memory" / "record_index" / "manifest.json",
        {
            "record_count": len(rows),
            "full_envelope_root_sha256": "a" * 64,
        },
    )
    return root


def test_offline_compiler_reuses_shared_checkpoint_identity_across_worktrees(
    tmp_path: Path,
) -> None:
    checkpoint_dir = tmp_path / "origin" / "runs" / "checkpoints" / "llm"
    checkpoint_dir.mkdir(parents=True)
    payload = {"prompt_sha256": "a" * 64, "prompt_utf8_bytes": 128}
    compiler_a = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "worktree-a"),
        llm=DeterministicMockLLMProvider(),
        checkpoint_dir=checkpoint_dir,
    )
    compiler_b = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "worktree-b"),
        llm=DeterministicMockLLMProvider(),
        checkpoint_dir=checkpoint_dir,
    )

    assert isinstance(compiler_a.llm, TracingLLMProvider)
    assert isinstance(compiler_b.llm, TracingLLMProvider)
    assert compiler_a.llm.checkpoint_dir == checkpoint_dir
    assert compiler_b.llm.checkpoint_dir == checkpoint_dir
    checkpoint_id = compiler_a.llm._write_checkpoint(
        operation="generate_structured",
        purpose="offline_semantic_reduce.REDUCE-fixture",
        status="ok",
        input_payload=payload,
        output={"result": "cached"},
    )
    assert checkpoint_id == compiler_b.llm._checkpoint_id(
        operation="generate_structured",
        purpose="offline_semantic_reduce.REDUCE-fixture",
        input_payload=payload,
    )
    cached = compiler_b.llm._read_ok_checkpoint(
        operation="generate_structured",
        purpose="offline_semantic_reduce.REDUCE-fixture",
        input_payload=payload,
    )
    assert cached is not None
    assert cached["output"] == {"result": "cached"}


def test_offline_compiler_rejects_missing_explicit_checkpoint_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="explicit offline checkpoint directory"):
        OfflineSemanticBrainCompiler(
            Settings(project_root=tmp_path / "worktree"),
            llm=DeterministicMockLLMProvider(),
            checkpoint_dir=tmp_path / "missing-checkpoints",
        )


@pytest.mark.asyncio
async def test_offline_brain_build_closes_all_records_and_reduce_nodes(
    tmp_path: Path,
) -> None:
    source = _source_project(tmp_path / "source")
    settings = Settings(project_root=tmp_path / "compiler")
    result = await OfflineSemanticBrainCompiler(
        settings,
        llm=DeterministicMockLLMProvider(),
    ).build(
        source_project=source,
        output_root=tmp_path / "packages",
    )

    assert result.package_manifest.record_count == 12
    assert result.package_manifest.assignment_coverage_ratio == 1.0
    assert result.package_manifest.unassigned_record_count == 0
    assert result.package_manifest.duplicate_primary_assignment_count == 0
    assert result.package_manifest.semantic_unit_count >= 3
    assert result.package_manifest.semantic_capsule_count == result.package_manifest.semantic_unit_count
    assert result.package_manifest.synthesized_mechanism_claim_count >= 1
    assert result.package_manifest.child_omission_count == 0
    assert result.package_manifest.semantic_capsule_hnsw_index_ready is True
    assert result.package_manifest.mechanism_claim_hnsw_index_ready is True
    assert result.package_manifest.daily_ann_query_plan_verified is True
    assert result.compile_manifest.first_n_shortcut_used is False
    assert result.compile_manifest.silent_truncation_count == 0
    assert result.compile_manifest.embedding_reused is True
    assert result.compile_manifest.import_reused is True
    usage_path = result.package_dir / "synthesis_checkpoint_usage.jsonl"
    usage_rows = [
        json.loads(line)
        for line in usage_path.read_text(encoding="utf-8").splitlines()
    ]
    model_provenance = read_json(
        result.package_dir / "synthesis_model_provenance.json"
    )
    reduce_plan = read_json(result.package_dir / "reduce_dag_plan.json")
    reuse_manifest = read_json(
        result.package_dir / "offline_resume_reuse_manifest.json"
    )
    progress = read_json(result.package_dir / "offline_compile_progress.json")
    assert reduce_plan["plan"]["total_model_tasks"] == result.compile_manifest.reduce_node_count
    assert reuse_manifest["reduce_dag_plan_sha256"] == reduce_plan["plan_sha256"]
    assert reuse_manifest["map_checkpoint_request_count"] == (
        reuse_manifest["map_checkpoint_hit_count"]
        + reuse_manifest["map_fresh_output_count"]
    )
    assert progress["completed_model_node_count"] == progress["total_model_node_count"]
    assert progress["total_model_node_count"] == reduce_plan["plan"]["total_model_tasks"]
    assert len(usage_rows) == result.compile_manifest.llm_call_count
    assert model_provenance["output_count"] == len(usage_rows)
    assert model_provenance["checkpoint_usage_sha256"] == file_sha256(usage_path)
    assert sum(
        row["output_count"] for row in model_provenance["model_output_counts"]
    ) == len(usage_rows)
    assert result.compile_manifest.full_population_embedding_geometry is True
    assert result.compile_manifest.semantic_splitter_version.startswith(
        "recursive_full_population_cosine_radius"
    )
    assert result.influence_manifest.primary_assignment_count == 12
    assert result.influence_manifest.population_contribution_record_count == 12
    assert result.influence_manifest.close_return_status_accounted_record_count == 12
    assert result.influence_manifest.close_return_status_distribution_root
    assert result.influence_manifest.representative_payload_exposed_record_count <= 12
    assert (
        result.influence_manifest.representative_payload_exposed_record_count
        + result.influence_manifest.representative_payload_not_exposed_record_count
        == 12
    )
    assert result.package_manifest.representative_payload_exposure_ratio == (
        result.influence_manifest.representative_payload_exposure_ratio
    )
    assert result.package_manifest.representative_payload_read_root == (
        result.influence_manifest.representative_payload_read_root
    )
    assert result.influence_manifest.leaf_covered_semantic_unit_count == (
        result.package_manifest.semantic_unit_count
    )
    assert result.influence_manifest.final_covered_capsule_count == (
        result.package_manifest.semantic_capsule_count
    )
    capsule_rows = [
        json.loads(line)
        for line in (result.package_dir / "semantic_capsules.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    coverage_rows = [
        json.loads(line)
        for line in (result.package_dir / "semantic_reduce_leaf_coverage.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    covered_capsule_ids = [
        capsule_id for row in coverage_rows for capsule_id in row["capsule_ids"]
    ]
    assert sorted(covered_capsule_ids) == sorted(row["capsule_id"] for row in capsule_rows)
    assert len(covered_capsule_ids) == len(set(covered_capsule_ids))
    coverage_manifest = read_json(
        result.package_dir / "semantic_reduce_coverage_manifest.json"
    )
    assert coverage_manifest["capsule_count"] == len(capsule_rows)
    assert coverage_manifest["leaf_coverage_sha256"] == file_sha256(
        result.package_dir / "semantic_reduce_leaf_coverage.jsonl"
    )
    assert all(
        sum(row["close_return_status_distribution"].values())
        == row["member_record_count"]
        for row in capsule_rows
    )
    status_root_rows = sorted(
        (row["semantic_unit_id"], status, count)
        for row in capsule_rows
        for status, count in row["close_return_status_distribution"].items()
    )
    assert result.influence_manifest.close_return_status_distribution_root == (
        sha256_text(canonical_json(status_root_rows))
    )
    population_rows = [
        json.loads(line)
        for line in (
            result.package_dir / "population_cube" / "capsule_populations.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert all(row["close_return_status_distribution"] for row in population_rows)

    connection = duckdb.connect(
        str(result.package_dir / "semantic_capsule_index.duckdb"),
        read_only=True,
    )
    try:
        connection.execute("LOAD vss")
        assert connection.execute("SELECT count(*) FROM semantic_unit_assignments").fetchone()[0] == 12
        reduce_payloads = [
            json.loads(row[0])
            for row in connection.execute("SELECT payload_json FROM reduce_nodes").fetchall()
        ]
        assert reduce_payloads
        assert all(not row["covered_capsule_ids"] for row in reduce_payloads)
        assert all(row["covered_capsule_count"] >= 0 for row in reduce_payloads)
        positive_units = connection.execute(
            """
            SELECT count(*) FROM semantic_unit_centroids
            WHERE primary_cell_id = 'CELL-shared' AND evidence_polarity = 'POSITIVE'
            """
        ).fetchone()[0]
        assert positive_units == 2
        assert connection.execute(
            "SELECT max(distance) FROM semantic_unit_assignments"
        ).fetchone()[0] <= 0.34
        plan = connection.execute(
            "EXPLAIN SELECT capsule_id FROM semantic_capsules "
            "ORDER BY array_cosine_distance(embedding, ?::FLOAT[384]) LIMIT 24",
            [_vector(0)],
        ).fetchone()[1]
        assert "HNSW_INDEX_SCAN" in plan
        claim_plan = connection.execute(
            "EXPLAIN SELECT claim_id FROM mechanism_claims "
            "ORDER BY array_cosine_distance(embedding, ?::FLOAT[384]) LIMIT 24",
            [_vector(0)],
        ).fetchone()[1]
        assert "HNSW_INDEX_SCAN" in claim_plan
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_offline_build_seals_map_plan_before_reducers_and_resumes_from_it(
    tmp_path: Path,
) -> None:
    class RecordingMockLLM(DeterministicMockLLMProvider):
        def __init__(self) -> None:
            super().__init__()
            self.response_models: list[type[Any]] = []

        async def generate_structured(
            self,
            *,
            prompt: str,
            response_model: type[Any],
            purpose: str,
        ) -> Any:
            self.response_models.append(response_model)
            return await super().generate_structured(
                prompt=prompt,
                response_model=response_model,
                purpose=purpose,
            )

    source = _source_project(tmp_path / "source")
    settings = Settings(project_root=tmp_path / "compiler")
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    unsealed_llm = RecordingMockLLM()
    with pytest.raises(ValueError, match="no sealed map-only reduce plan"):
        await OfflineSemanticBrainCompiler(
            Settings(project_root=tmp_path / "unsealed"),
            llm=unsealed_llm,
        ).build(
            source_project=source,
            require_map_plan_receipt=True,
        )
    assert not unsealed_llm.response_models

    first_llm = RecordingMockLLM()
    planned = await OfflineSemanticBrainCompiler(
        settings,
        llm=first_llm,
        checkpoint_dir=checkpoint_dir,
    ).build(
        source_project=source,
        output_root=tmp_path / "packages",
        stop_after_reduce_plan=True,
        require_map_plan_receipt=True,
    )

    assert isinstance(planned, OfflineBrainPlanResult)
    assert SemanticReduceDraft not in first_llm.response_models
    plan = read_json(planned.reduce_dag_plan_path)
    receipt = read_json(planned.plan_receipt_path)
    progress = read_json(planned.work_database_path.parent / "progress.json")
    assert receipt["status"] == "MAP_AND_PLAN_READY_REDUCERS_NOT_STARTED"
    assert receipt["reduce_dag_plan_sha256"] == plan["plan_sha256"]
    assert "resume_source_database_wal_sha256" in receipt
    assert receipt["resume_source_database_wal_sha256"] is None
    assert receipt["map_checkpoint_request_count"] == (
        receipt["map_checkpoint_hit_count"] + receipt["map_fresh_output_count"]
    )
    assert progress["phase"] == "offline_reduce_plan_sealed"
    assert progress["total_model_node_count"] == receipt["reduce_model_task_count"]
    assert not (tmp_path / "packages").exists()
    connection = duckdb.connect(str(planned.work_database_path), read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM semantic_capsules").fetchone() == (
            receipt["semantic_capsule_count"],
        )
        assert connection.execute("SELECT count(*) FROM reduce_nodes").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM mechanism_claims").fetchone() == (
            0,
        )
    finally:
        connection.close()

    legacy_resume_database = tmp_path / "legacy" / "semantic_capsule_index.duckdb"
    legacy_resume_database.parent.mkdir(parents=True)
    shutil.copy2(planned.work_database_path, legacy_resume_database)
    legacy_connection = duckdb.connect(str(legacy_resume_database))
    try:
        legacy_connection.execute("DROP TABLE offline_compile_metadata")
        legacy_connection.execute("CHECKPOINT")
    finally:
        legacy_connection.close()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import duckdb, os, sys; "
            "connection = duckdb.connect(sys.argv[1]); "
            "connection.execute('CREATE TABLE resume_wal_marker AS SELECT 1 AS value'); "
            "os._exit(0)",
            str(legacy_resume_database),
        ],
        check=True,
        timeout=30,
    )
    legacy_resume_database_sha256 = file_sha256(legacy_resume_database)
    legacy_resume_database_wal_sha256 = file_sha256(
        Path(f"{legacy_resume_database}.wal")
    )

    wal_settings = Settings(project_root=tmp_path / "compiler-with-wal")
    wal_planned_llm = RecordingMockLLM()
    wal_planned = await OfflineSemanticBrainCompiler(
        wal_settings,
        llm=wal_planned_llm,
        checkpoint_dir=checkpoint_dir,
    ).build(
        source_project=source,
        output_root=tmp_path / "packages",
        resume_work_database=legacy_resume_database,
        expected_resume_work_database_sha256=legacy_resume_database_sha256,
        expected_resume_work_database_wal_sha256=legacy_resume_database_wal_sha256,
        stop_after_reduce_plan=True,
        require_map_plan_receipt=True,
    )

    assert isinstance(wal_planned, OfflineBrainPlanResult)
    wal_plan = read_json(wal_planned.reduce_dag_plan_path)
    wal_receipt = read_json(wal_planned.plan_receipt_path)
    assert wal_receipt["resume_source_database_sha256"] == (
        legacy_resume_database_sha256
    )
    assert wal_receipt["resume_source_database_wal_sha256"] == (
        legacy_resume_database_wal_sha256
    )
    assert file_sha256(legacy_resume_database) == legacy_resume_database_sha256
    assert file_sha256(Path(f"{legacy_resume_database}.wal")) == (
        legacy_resume_database_wal_sha256
    )

    tampered_receipt = {**wal_receipt, "reduce_dag_plan_sha256": "0" * 64}
    write_json(wal_planned.plan_receipt_path, tampered_receipt)
    rejected_llm = RecordingMockLLM()
    with pytest.raises(ValueError, match="capsule population changed after map-only plan"):
        await OfflineSemanticBrainCompiler(
            wal_settings,
            llm=rejected_llm,
            checkpoint_dir=checkpoint_dir,
        ).build(
            source_project=source,
            output_root=tmp_path / "packages",
            require_map_plan_receipt=True,
        )
    assert SemanticReduceDraft not in rejected_llm.response_models
    write_json(wal_planned.plan_receipt_path, wal_receipt)

    resumed_llm = RecordingMockLLM()
    completed = await OfflineSemanticBrainCompiler(
        wal_settings,
        llm=resumed_llm,
        checkpoint_dir=checkpoint_dir,
    ).build(
        source_project=source,
        output_root=tmp_path / "packages",
        require_map_plan_receipt=True,
    )

    assert isinstance(completed, OfflineBrainBuildResult)
    assert SemanticCapsuleDraftBatch not in resumed_llm.response_models
    assert SemanticReduceDraft in resumed_llm.response_models
    assert wal_plan["plan_sha256"] == plan["plan_sha256"]
    final_receipt = read_json(
        completed.package_dir / "offline_map_plan_receipt.json"
    )
    reuse_manifest = read_json(
        completed.package_dir / "offline_resume_reuse_manifest.json"
    )
    assert final_receipt["reduce_dag_plan_sha256"] == plan["plan_sha256"]
    assert reuse_manifest["map_plan_receipt_sha256"] == file_sha256(
        completed.package_dir / "offline_map_plan_receipt.json"
    )
    assert reuse_manifest["map_fresh_output_count"] == 0
    assert (completed.package_dir / "map_stage_checkpoint_usage.jsonl").is_file()


@pytest.mark.asyncio
async def test_resume_database_survives_package_failure_and_rebuilds_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_project(tmp_path / "source")
    settings = Settings(project_root=tmp_path / "compiler")
    baseline = await OfflineSemanticBrainCompiler(
        settings,
        llm=DeterministicMockLLMProvider(),
    ).build(
        source_project=source,
        output_root=tmp_path / "packages-baseline",
    )
    resume_database = tmp_path / "legacy" / "semantic_capsule_index.duckdb"
    resume_database.parent.mkdir(parents=True)
    shutil.copy2(
        baseline.package_dir / "semantic_capsule_index.duckdb",
        resume_database,
    )
    connection = duckdb.connect(str(resume_database))
    try:
        for (index_name,) in connection.execute(
            "SELECT index_name FROM duckdb_indexes()"
        ).fetchall():
            connection.execute(f"DROP INDEX {index_name}")
        connection.execute("DELETE FROM mechanism_claim_capsules")
        connection.execute("DELETE FROM mechanism_claims")
        connection.execute("DELETE FROM reduce_nodes")
        connection.execute("DROP TABLE IF EXISTS offline_compile_metadata")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    resume_database_sha256 = file_sha256(resume_database)

    original_writer = offline_v2._write_category_brain

    def fail_during_package(*_: Any, **__: Any) -> None:
        raise RuntimeError("injected package failure")

    monkeypatch.setattr(offline_v2, "_write_category_brain", fail_during_package)
    resumed_root = tmp_path / "packages-resumed"
    with pytest.raises(RuntimeError, match="injected package failure"):
        await OfflineSemanticBrainCompiler(
            settings,
            llm=DeterministicMockLLMProvider(),
        ).build(
            source_project=source,
            output_root=resumed_root,
            resume_work_database=resume_database,
            expected_resume_work_database_sha256=resume_database_sha256,
        )

    assert file_sha256(resume_database) == resume_database_sha256
    work_databases = list(
        (settings.project_root / "brain" / ".work").glob(
            "*/semantic_capsule_index.duckdb"
        )
    )
    assert len(work_databases) == 1
    work_database = work_databases[0]
    assert work_database.is_file()
    partial_package = resumed_root / baseline.package_manifest.brain_version
    assert partial_package.is_dir()
    assert not (partial_package / "build_receipt.json").exists()

    monkeypatch.setattr(offline_v2, "_write_category_brain", original_writer)
    resumed = await OfflineSemanticBrainCompiler(
        settings,
        llm=DeterministicMockLLMProvider(),
    ).build(
        source_project=source,
        output_root=resumed_root,
        resume_work_database=resume_database,
        expected_resume_work_database_sha256=resume_database_sha256,
    )

    assert resumed.package_manifest.brain_version == baseline.package_manifest.brain_version
    assert file_sha256(resume_database) == resume_database_sha256
    assert not work_database.exists()
    assert (resumed.package_dir / "build_receipt.json").is_file()
    reuse_manifest = read_json(
        resumed.package_dir / "offline_resume_reuse_manifest.json"
    )
    assert reuse_manifest["resume_source_database_sha256"] == resume_database_sha256
    assert reuse_manifest["resume_source_database_wal_sha256"] is None
    assert reuse_manifest["resume_source_capsule_exact_match_count"] == (
        resumed.package_manifest.semantic_capsule_count
    )
    assert reuse_manifest["map_fresh_output_count"] == 0


def test_full_population_outlier_beyond_old_sample_boundary_gets_own_unit() -> None:
    rows = [
        _VectorRow(
            record_id=f"REC-{index:05d}",
            independent_unit_id=f"IU-{index:05d}",
            source_sha256=f"{index:064x}",
            embedding=np.asarray(_vector(0 if index < 4096 else 1), dtype=np.float32),
        )
        for index in range(4097)
    ]

    builds, assignments = _split_semantic_stratum(
        category="single_event",
        primary_cell_id="CELL-full-population",
        evidence_polarity="POSITIVE",
        rows=rows,
    )

    assert len(builds) == 2
    assert len(assignments) == 4097
    assert assignments[-1][5] is True
    for build in builds:
        member_ids = sorted(
            str(row[0]) for row in assignments if row[1] == build.semantic_unit_id
        )
        assert build.member_record_count == len(member_ids)
        assert build.member_record_root == sha256_text(canonical_json(member_ids))


def test_utf8_long_payload_chunking_is_lossless() -> None:
    payload = "한글abc" * 30_000
    chunks = _utf8_chunks(payload, max_bytes=72_000)

    assert len(chunks) > 1
    assert "".join(chunks) == payload


def test_long_payload_digest_source_identity_is_materialized_from_ledger() -> None:
    source_row = {
        "chunk_id": "LONG-CHUNK-source",
        "semantic_unit_id": "SUNIT-source",
        "record_id": "RECORD-source",
        "chunk_index": 2,
        "chunk_count": 4,
        "document_sha256": "a" * 64,
        "chunk_sha256": "b" * 64,
    }
    draft = LongPayloadChunkDigestDraft(
        chunk_id="LONG-CHUNK-source",
        summary="Semantic content authored by the model.",
        material_facts=["fact"],
        mechanisms=["mechanism"],
    )

    materialized = _materialize_long_payload_chunk_digest(
        source_row=source_row,
        draft=draft,
    )

    assert materialized.semantic_unit_id == source_row["semantic_unit_id"]
    assert materialized.record_id == source_row["record_id"]
    assert materialized.chunk_index == source_row["chunk_index"]
    assert materialized.chunk_count == source_row["chunk_count"]
    assert materialized.document_sha256 == source_row["document_sha256"]
    assert materialized.chunk_sha256 == source_row["chunk_sha256"]
    digest_properties = LongPayloadDigestBatch.model_json_schema()["$defs"][
        "LongPayloadChunkDigestDraft"
    ]["properties"]
    assert {
        "semantic_unit_id",
        "record_id",
        "chunk_index",
        "chunk_count",
        "document_sha256",
        "chunk_sha256",
    }.isdisjoint(digest_properties)


def test_reduce_claim_availability_includes_uncited_input_capsules() -> None:
    first_available = datetime(2025, 1, 2, 18, 0, tzinfo=KST)
    later_available = datetime(2025, 1, 9, 18, 0, tzinfo=KST)

    def capsule(capsule_id: str, available_from: datetime) -> SemanticMemoryCapsule:
        return SemanticMemoryCapsule(
            capsule_id=capsule_id,
            category="single_event",
            semantic_unit_id=f"UNIT-{capsule_id}",
            member_record_count=1,
            member_independent_unit_count=1,
            member_record_root="record-root",
            event_or_mechanism_summary="fixture mechanism",
            available_from=available_from,
            provenance_root="provenance-root",
            embedding=_vector(0),
        )

    node = SemanticReduceNode(
        node_id="REDUCE-fixture",
        child_node_ids=["LEAF-fixture"],
        covered_capsule_ids=["CAP-old", "CAP-new"],
        synthesis="claim synthesized from both inputs",
        claims=[
            MechanismClaimDraft(
                statement="Old evidence supports the mechanism",
                mechanism="fixture transmission",
                supporting_capsule_ids=["CAP-old"],
                confidence="medium",
                status="supported",
            )
        ],
    )

    claims = _claims_from_reduce_node(
        node,
        category="single_event",
        capsules=[capsule("CAP-old", first_available), capsule("CAP-new", later_available)],
    )

    assert len(claims) == 1
    assert claims[0].supporting_capsule_ids == ["CAP-old"]
    assert claims[0].available_from == later_available


@pytest.mark.asyncio
async def test_reduce_rejects_claims_outside_available_source_ids(tmp_path: Path) -> None:
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=ReduceCoverageMismatchLLM(),
    )
    children = _fixture_reduce_children()

    with pytest.raises(ValueError, match="cited an unavailable capsule"):
        await compiler._reduce_node(
            category="fixture",
            level=0,
            children=children,
            review=False,
        )
    assert compiler._logical_llm_call_count == 2


@pytest.mark.asyncio
async def test_reduce_retries_invalid_citation_once_without_changing_claim_content(
    tmp_path: Path,
) -> None:
    provider = ReduceCitationRepairLLM()
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=provider,
    )

    result = await compiler._reduce_node(
        category="fixture",
        level=0,
        children=_fixture_reduce_children(),
        review=False,
    )

    assert result.claims[0].supporting_capsule_ids == ["CAP-a"]
    assert compiler._logical_llm_call_count == 2
    assert len(provider.prompts) == 2
    assert provider.purposes[1].endswith(
        f".citation_repair.v1.{result.node_id}"
    )
    marker = "---OFFLINE_SEMANTIC_REDUCE---\n"
    assert provider.prompts[0].split(marker, 1)[1] == provider.prompts[1].split(marker, 1)[1]
    assert "CAP-not-in-child-tree" in provider.prompts[1]


@pytest.mark.asyncio
async def test_reduce_citation_repair_still_fails_closed_after_one_retry(
    tmp_path: Path,
) -> None:
    provider = ReduceCitationRepairLLM(repair_valid=False)
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=provider,
    )

    with pytest.raises(ValueError, match="cited an unavailable capsule"):
        await compiler._reduce_node(
            category="fixture",
            level=0,
            children=_fixture_reduce_children(),
            review=False,
        )

    assert compiler._logical_llm_call_count == 2
    assert len(provider.prompts) == 2


@pytest.mark.asyncio
async def test_world_reduce_retries_invalid_citation_once(tmp_path: Path) -> None:
    provider = ReduceCitationRepairLLM()
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=provider,
    )

    result = await compiler._reduce_world(
        {"fixture": _fixture_reduce_children()[0]},
    )

    assert result.claims[0].supporting_capsule_ids == ["CAP-a"]
    assert compiler._logical_llm_call_count == 2
    assert len(provider.prompts) == 2
    assert provider.purposes[1].endswith(
        f".citation_repair.v1.{result.node_id}"
    )


def test_reduce_citation_only_repair_rejects_claim_text_changes() -> None:
    rejected = SemanticReduceDraft(
        node_id="REDUCE-fixture",
        child_node_ids=["LEAF-fixture"],
        synthesis="unchanged synthesis",
        claims=[
            SemanticReduceClaimDraft(
                statement="unchanged claim",
                mechanism="unchanged mechanism",
                supporting_capsule_ids=["CAP-typo"],
                confidence="low",
                status="supported",
            )
        ],
    )
    claim = rejected.claims[0]
    corrected = rejected.model_copy(
        update={
            "claims": [
                claim.model_copy(
                    update={"supporting_capsule_ids": ["CAP-valid"]}
                )
            ]
        }
    )
    offline_v2._assert_reduce_citation_only_repair(rejected, corrected)

    changed_claim = corrected.claims[0].model_copy(
        update={"statement": "rewritten claim"}
    )
    changed = corrected.model_copy(update={"claims": [changed_claim]})
    with pytest.raises(ValueError, match="changed non-citation content"):
        offline_v2._assert_reduce_citation_only_repair(rejected, changed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "capsule_id", "normalizes", "expected_rule"),
    [
        (
            " \u062c",
            None,
            True,
            "allowed_capsule_id_plus_single_unicode_letter_suffix",
        ),
        (" x", None, False, None),
        (
            "\u00ad\u2014",
            None,
            True,
            "allowed_capsule_id_plus_soft_hyphen_em_dash_suffix",
        ),
        (
            "\u0639\u0646\u062f",
            None,
            True,
            "allowed_capsule_id_plus_exact_arabic_word_suffix",
        ),
        (
            "\u2019",
            None,
            True,
            "allowed_capsule_id_plus_exact_right_single_quote_suffix",
        ),
        (
            " so keep going?",
            None,
            True,
            "allowed_capsule_id_plus_exact_english_phrase_suffix",
        ),
        (
            " ... a",
            None,
            True,
            "allowed_capsule_id_plus_exact_ellipsis_a_suffix",
        ),
        (
            "\u2009",
            None,
            True,
            "allowed_capsule_id_plus_exact_thin_space_suffix",
        ),
        (
            " /\u201d",
            None,
            True,
            "allowed_capsule_id_plus_exact_slash_right_double_quote_suffix",
        ),
        ("\u00ad\u2014", "CAP-not-in-child-tree", False, None),
        ("\u0639\u0646\u062f", "CAP-not-in-child-tree", False, None),
        ("\u2019", "CAP-not-in-child-tree", False, None),
        (" so keep going?", "CAP-not-in-child-tree", False, None),
        (" so keep going!!", None, False, None),
        (" so keep going? CAP-not-in-child-tree", None, False, None),
        (" ... a", "CAP-not-in-child-tree", False, None),
        (" ... b", None, False, None),
        (" ... ab", None, False, None),
        (" ... a CAP-not-in-child-tree", None, False, None),
        ("\u2009", "CAP-not-in-child-tree", False, None),
        (" /\u201d", "CAP-not-in-child-tree", False, None),
        ("/\u201d", None, False, None),
        (" /\u201c", None, False, None),
        (" /\u201d ", None, False, None),
        ("\u200a", None, False, None),
        ("\u00ad\u2013", None, False, None),
        ("\u2018", None, False, None),
    ],
)
async def test_reduce_normalizes_only_explicit_suffixes_on_allowed_capsule_ids(
    tmp_path: Path,
    suffix: str,
    capsule_id: str | None,
    normalizes: bool,
    expected_rule: str | None,
) -> None:
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=ReduceCitationSuffixLLM(suffix, capsule_id=capsule_id),
    )
    allowed_id = "CAP-a"

    if not normalizes:
        with pytest.raises(ValueError, match="cited an unavailable capsule") as exc_info:
            await compiler._reduce_node(
                category="fixture",
                level=0,
                children=_fixture_reduce_children(),
                review=False,
            )
        message = str(exc_info.value)
        citation_base = capsule_id or allowed_id
        assert "claim_index=0" in message
        assert "field=supporting_capsule_ids" in message
        assert f"citation={ascii(citation_base + suffix)}" in message
        return

    result = await compiler._reduce_node(
        category="fixture",
        level=0,
        children=_fixture_reduce_children(),
        review=False,
    )

    assert result.claims[0].supporting_capsule_ids == [allowed_id]
    assert len(result.citation_normalizations) == 1
    normalization = result.citation_normalizations[0]
    assert normalization.original_value == allowed_id + suffix
    assert normalization.normalized_capsule_id == allowed_id
    assert normalization.rule == expected_rule
    assert compiler._logical_llm_call_count == 1


@pytest.mark.asyncio
async def test_reduce_splits_exact_pair_of_allowed_capsule_ids(tmp_path: Path) -> None:
    raw_value = "CAP-a, CAP-c"
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=ReduceCitationSuffixLLM(", CAP-c", capsule_id="CAP-a"),
    )

    result = await compiler._reduce_node(
        category="fixture",
        level=0,
        children=_fixture_reduce_children(),
        review=False,
    )

    assert result.claims[0].supporting_capsule_ids == ["CAP-a", "CAP-c"]
    assert [row.normalized_capsule_id for row in result.citation_normalizations] == [
        "CAP-a",
        "CAP-c",
    ]
    assert all(row.original_value == raw_value for row in result.citation_normalizations)
    assert all(
        row.rule == "allowed_capsule_ids_joined_by_exact_comma_space"
        for row in result.citation_normalizations
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix",
    [
        ", CAP-not-in-child-tree",
        ",  CAP-c",
        " , CAP-c",
        ", CAP-c, CAP-not-in-child-tree",
        ",CAP-c",
    ],
)
async def test_reduce_rejects_unverified_comma_joined_capsule_ids(
    tmp_path: Path,
    suffix: str,
) -> None:
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=ReduceCitationSuffixLLM(suffix, capsule_id="CAP-a"),
    )

    with pytest.raises(ValueError, match="cited an unavailable capsule"):
        await compiler._reduce_node(
            category="fixture",
            level=0,
            children=_fixture_reduce_children(),
            review=False,
        )


@pytest.mark.asyncio
async def test_reduce_still_rejects_an_omitted_child_node(tmp_path: Path) -> None:
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=ReduceCoverageMismatchLLM(omit_child=True),
    )

    with pytest.raises(ValueError, match="semantic reduce output omitted or added children"):
        await compiler._reduce_node(
            category="fixture",
            level=0,
            children=_fixture_reduce_children(),
            review=False,
        )


@pytest.mark.asyncio
async def test_reduce_restores_only_empty_child_identity_from_local_graph_and_persists_audit(
    tmp_path: Path,
) -> None:
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=ReduceEmptyChildIDsLLM(),
    )
    children = _fixture_reduce_children()
    result = await compiler._reduce_node(
        category="fixture",
        level=0,
        children=children,
        review=False,
    )
    expected_children = [child.node_id for child in children]

    assert result.child_node_ids == expected_children
    assert result.child_identity_normalization is not None
    assert result.child_identity_normalization.original_child_node_ids == []
    assert result.child_identity_normalization.restored_child_node_ids == expected_children
    assert (
        result.child_identity_normalization.rule
        == "empty_child_ids_restored_from_local_fixed_graph"
    )

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE reduce_nodes (node_id VARCHAR PRIMARY KEY, payload_json VARCHAR NOT NULL)"
        )
        _persist_reduce_node(connection, result)
        restored = _load_reduce_nodes_from_database(connection)[result.node_id]
        assert restored.child_node_ids == expected_children
        assert restored.child_identity_normalization == result.child_identity_normalization
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_world_reduce_restores_empty_child_identity_from_local_graph(
    tmp_path: Path,
) -> None:
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=ReduceEmptyChildIDsLLM(),
    )
    roots = {"fixture": _fixture_reduce_children()[0]}

    result = await compiler._reduce_world(roots)

    assert result.child_node_ids == [roots["fixture"].node_id]
    assert result.child_identity_normalization is not None
    assert result.child_identity_normalization.restored_child_node_ids == result.child_node_ids


@pytest.mark.asyncio
async def test_long_payload_batches_are_consumed_incrementally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = "long payload evidence " * 20_000
    payload_plan = _plan_long_payloads(
        [
            {
                "semantic_unit_id": "SUNIT-long-payload",
                "category": "single_event",
                "representatives": [
                    {
                        "record_id": "RECORD-long-payload",
                        "document": document,
                        "document_sha256": sha256_text(document),
                    }
                ],
            }
        ]
    )
    assert payload_plan.long_payload_chunk_map_call_count > 1

    settings = Settings(project_root=tmp_path / "compiler")
    settings.limits.max_concurrency = 1
    llm = TrackingLongPayloadLLM()
    compiler = OfflineSemanticBrainCompiler(settings, llm=llm)
    original_pack = offline_v2._pack_long_payload_chunks
    yielded_batches = 0

    def observed_batches(chunks: list[dict[str, Any]]) -> Any:
        nonlocal yielded_batches
        for batch in original_pack(chunks):
            if yielded_batches:
                assert llm.long_payload_calls >= yielded_batches
            yielded_batches += 1
            yield batch

    monkeypatch.setattr(offline_v2, "_pack_long_payload_chunks", observed_batches)
    projected_rows = await compiler._compile_long_payload_digests(payload_plan)

    assert projected_rows is payload_plan.projected_rows
    assert yielded_batches == payload_plan.long_payload_chunk_map_call_count
    assert llm.long_payload_calls == payload_plan.long_payload_chunk_map_call_count
    assert len(
        projected_rows[0]["representatives"][0]["full_payload_chunk_digests"]
    ) == payload_plan.long_payload_chunk_count


@pytest.mark.asyncio
async def test_oversized_representative_payload_is_fully_chunk_mapped(
    tmp_path: Path,
) -> None:
    source = _source_project(tmp_path / "source", oversized_document=True)
    settings = Settings(project_root=tmp_path / "compiler")
    plan = OfflineSemanticBrainCompiler(
        settings,
        llm=DeterministicMockLLMProvider(),
    ).plan(source_project=source)
    assert plan["long_payload_chunk_count"] > 1
    assert plan["long_payload_chunk_map_call_count"] >= 1
    assert plan["representative_payload_truncated_count"] == 0
    assert plan["estimated_total_logical_llm_call_count"] == (
        plan["long_payload_chunk_map_call_count"]
        + plan["leaf_map_call_count"]
        + plan["estimated_reduce_review_call_count"]
    )
    result = await OfflineSemanticBrainCompiler(
        settings,
        llm=DeterministicMockLLMProvider(),
    ).build(source_project=source, output_root=tmp_path / "packages")

    assert result.compile_manifest.long_payload_chunk_count > 1
    assert result.compile_manifest.long_payload_chunk_map_call_count >= 1
    assert result.compile_manifest.chunked_representative_record_count >= 1
    assert result.compile_manifest.representative_payload_truncated_count == 0
    assert result.package_manifest.representative_payload_full_read_count == (
        result.package_manifest.representative_payload_exposed_record_count
    )
    exposure_rows = [
        json.loads(line)
        for line in (result.package_dir / "representative_payload_exposure.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    chunked = [row for row in exposure_rows if row["exposure_mode"] == "FULL_CHUNK_MAP_THEN_LEAF"]
    assert chunked
    assert all(
        row["chunk_ids"]
        and row["chunk_map_node_ids"]
        and row["truncated"] is False
        for row in chunked
    )


def test_offline_plan_uses_embeddings_but_zero_llm_calls(tmp_path: Path) -> None:
    source = _source_project(tmp_path / "source")
    plan = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler"),
        llm=DeterministicMockLLMProvider(),
    ).plan(source_project=source)

    assert plan["record_count"] == 12
    assert plan["semantic_unit_count"] >= 3
    assert plan["dynamic_representative_count"] >= plan["semantic_unit_count"]
    assert plan["leaf_map_call_count"] >= 1
    assert plan["estimated_total_logical_llm_call_count"] > plan["leaf_map_call_count"]
    assert plan["estimated_reduce_leaf_node_count"] >= 1
    assert plan["estimated_reduce_leaf_node_count_is_runtime_count"] is False
    assert plan["estimated_reduce_review_call_count_is_lower_bound"] is False
    assert plan["estimated_reduce_review_call_count_is_projection"] is True
    mandatory_reduce_calls = len(plan["category_semantic_unit_counts"]) + 1
    assert plan["guaranteed_minimum_reduce_review_call_count"] == mandatory_reduce_calls
    assert plan["estimated_reduce_prompt_byte_packing_simulated"] is True
    assert plan["estimated_reduce_review_call_count"] >= mandatory_reduce_calls
    assert plan["estimated_total_logical_llm_call_count_is_lower_bound"] is False
    assert plan["estimated_total_logical_llm_call_count_is_projection"] is True
    minimum_total = (
        plan["long_payload_chunk_map_call_count"]
        + plan["leaf_map_call_count"]
        + mandatory_reduce_calls
    )
    assert plan["guaranteed_minimum_total_logical_llm_call_count"] == minimum_total
    assert plan["guaranteed_minimum_total_logical_llm_call_count_is_lower_bound"] is True
    assert plan["estimated_total_logical_llm_call_count"] >= minimum_total
    assert plan["planning_llm_call_count"] == 0
    assert plan["embedding_reused"] is True
    assert plan["import_reused"] is True
    assert plan["full_population_embedding_geometry"] is True


def test_planner_reduce_projection_uses_runtime_byte_packer() -> None:
    children = [
        SemanticReduceNode(
            node_id=f"LEAF-large-{index}",
            child_node_ids=[],
            covered_capsule_ids=[f"CAP-{index}"],
            covered_capsule_count=1,
            coverage_root=f"root-{index}",
            evidence_capsule_ids=[f"CAP-{index}"],
            synthesis="x" * 100_000,
        )
        for index in range(2)
    ]

    assert [len(group) for group in _pack_reduce_nodes(children)] == [1, 1]
    # The proxy cannot know whether model prose will shrink at the next level;
    # it stops at the first non-progressing lower-bound level.
    assert _estimate_reduce_review_call_count({"fixture": children}) == 4


def test_internal_reduce_payload_reserve_covers_local_coverage_fields() -> None:
    evidence_ids = [f"CAP-{index:020d}" for index in range(24)]
    draft = SemanticReduceDraft(
        node_id="N" * 64,
        child_node_ids=[f"CHILD-{index}-" + "C" * 56 for index in range(10)],
        synthesis="s" * 2_000,
        mechanisms=["m" * 64] * 6,
        conditions=["c" * 64] * 6,
        boundary_conditions=["b" * 64] * 6,
        failure_modes=["f" * 64] * 6,
        contradictions=["x" * 64] * 6,
        claims=[
            SemanticReduceClaimDraft(
                statement="s" * 96,
                mechanism="m" * 96,
                supporting_capsule_ids=evidence_ids[index * 6 : index * 6 + 3],
                contradicting_capsule_ids=evidence_ids[index * 6 + 3 : index * 6 + 6],
                confidence="c" * 32,
                status="s" * 32,
            )
            for index in range(4)
        ],
    )
    node = SemanticReduceNode(
        node_id=draft.node_id,
        child_node_ids=draft.child_node_ids,
        covered_capsule_count=823_279,
        coverage_root="r" * 64,
        prompt_sha256="p" * 64,
        evidence_capsule_ids=evidence_ids,
        synthesis=draft.synthesis,
        mechanisms=draft.mechanisms,
        conditions=draft.conditions,
        boundary_conditions=draft.boundary_conditions,
        failure_modes=draft.failure_modes,
        contradictions=draft.contradictions,
        claims=[MechanismClaimDraft(**claim.model_dump(mode="python")) for claim in draft.claims],
    )
    child_payload_bytes = len(
        canonical_json(offline_v2._reduce_child_payload(node)).encode("utf-8")
    )
    draft_bytes = len(draft.model_dump_json().encode("utf-8"))

    assert draft_bytes <= offline_v2.MAX_REDUCE_NODE_OUTPUT_BYTES
    assert child_payload_bytes - draft_bytes <= (
        offline_v2.MAX_REDUCE_NODE_PAYLOAD_RESERVE_BYTES
    )
    assert child_payload_bytes <= (
        offline_v2.MAX_REDUCE_NODE_OUTPUT_BYTES
        + offline_v2.MAX_REDUCE_NODE_PAYLOAD_RESERVE_BYTES
    )
    children = [node.model_copy(update={"node_id": f"NODE-{index}"}) for index in range(10)]
    prompt = offline_v2._reduce_prompt(
        node_id="PROMPT",
        category="fixture",
        children=children,
        review=False,
    )
    assert len(prompt.encode("utf-8")) <= offline_v2.MAX_REDUCE_PROMPT_BYTES


def test_world_reduce_uses_its_own_prompt_version() -> None:
    prompt = offline_v2._reduce_prompt(
        node_id="WORLD",
        category="world_model",
        children=_fixture_reduce_children(),
        review=True,
        world=True,
    )
    payload = json.loads(prompt.split("---OFFLINE_SEMANTIC_REDUCE---\n", 1)[1])

    assert payload["schema"] == offline_v2.WORLD_REDUCE_PROMPT_VERSION


@pytest.mark.asyncio
@pytest.mark.parametrize("capsule_count", [11039, 10965, 17039, 52644])
async def test_full_scale_reduce_keeps_complete_coverage_out_of_llm_payload(
    tmp_path: Path, capsule_count: int,
) -> None:
    provider = CapturingReduceLLM()
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path), llm=provider,
    )
    ids = [f"CAP-{index:020d}" for index in range(capsule_count)]
    midpoint = len(ids) // 2
    children = [
        SemanticReduceNode(
            node_id=f"LEAF-{index}",
            child_node_ids=[],
            covered_capsule_ids=part,
            covered_capsule_count=len(part),
            coverage_root=offline_v2._leaf_coverage_root(f"LEAF-{index}", part),
            evidence_capsule_ids=offline_v2._leaf_reduce_evidence_ids(part),
            synthesis="fixture semantic digest",
        )
        for index, part in enumerate((ids[:midpoint], ids[midpoint:]))
    ]
    result = await compiler._reduce_node(
        category="fixture", level=0, children=children, review=False,
    )
    assert compiler._logical_llm_call_count == 1
    assert result.covered_capsule_count == capsule_count
    assert result.covered_capsule_ids == ids
    payload = json.loads(provider.last_prompt.split("---OFFLINE_SEMANTIC_REDUCE---\n", 1)[1])
    assert all("covered_capsule_ids" not in row for row in payload["children"])
    assert len(payload["available_evidence_capsule_ids"]) <= 8
    assert len(provider.last_prompt.encode("utf-8")) < 10_000


@pytest.mark.asyncio
async def test_singleton_remainder_is_carried_without_a_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler = OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path), llm=DeterministicMockLLMProvider(),
    )
    monkeypatch.setattr(offline_v2, "MAX_REDUCE_CHILDREN", 2)
    leaves = [
        offline_v2._LeafNode(
            node_id=f"LEAF-{index}", category="fixture", capsule_ids=(), synthesis=f"condition {index}",
        )
        for index in range(3)
    ]
    _, nodes = await compiler._reduce_category(category="fixture", leaves=leaves, capsules=[])
    assert compiler._logical_llm_call_count == 3  # Two merges and one category review.
    assert len(nodes[0].child_node_ids) == 2
    assert nodes[1].child_node_ids == [nodes[0].node_id, "LEAF-2"]
    assert len(nodes[-1].child_node_ids) == 1


def test_planned_leaf_proxy_preserves_each_semantic_unit_once() -> None:
    rows = [
        {"category": "fixture", "semantic_unit_id": f"UNIT-{index}"}
        for index in range(37)
    ]
    leaves = _planned_reduce_leaf_nodes(rows)
    covered = [
        capsule_id
        for category_rows in leaves.values()
        for node in category_rows
        for capsule_id in node.covered_capsule_ids
    ]

    assert sorted(covered) == sorted(row["semantic_unit_id"] for row in rows)
    assert len(covered) == len(set(covered))
    first = next(iter(leaves["fixture"]))
    model = SemanticReduceNode(
        node_id=first.node_id,
        child_node_ids=[],
        covered_capsule_ids=list(first.covered_capsule_ids),
        covered_capsule_count=len(first.covered_capsule_ids),
        coverage_root=sha256_text(canonical_json(sorted(first.covered_capsule_ids))),
        evidence_capsule_ids=offline_v2._leaf_reduce_evidence_ids(
            first.covered_capsule_ids
        ),
        synthesis="",
    )
    assert first.payload_bytes == len(
        canonical_json(offline_v2._reduce_child_payload(model)).encode("utf-8")
    )


def test_source_manifest_pointer_drift_requires_explicit_attested_sha(
    tmp_path: Path,
) -> None:
    source = _source_project(tmp_path / "source")
    pointer_path = source / "memory" / "retrieval_index" / "current.json"
    pointer = read_json(pointer_path)
    pointer["manifest_sha256"] = "f" * 64
    write_json(pointer_path, pointer)
    manifest_path = (
        source
        / "memory"
        / "retrieval_index"
        / "snapshots"
        / "MEMIDX-fixture"
        / "manifest.json"
    )
    actual_sha = file_sha256(manifest_path)

    with pytest.raises(ValueError, match="externally attested actual SHA"):
        resolve_source_memory_snapshot(source)

    snapshot = resolve_source_memory_snapshot(
        source,
        expected_manifest_sha256=actual_sha,
    )
    assert snapshot.manifest_sha256 == actual_sha
    assert snapshot.pointer_manifest_hash_match is False


@pytest.mark.asyncio
async def test_incremental_update_matches_clean_full_rebuild(tmp_path: Path) -> None:
    source = _source_project(tmp_path / "source")
    first = await OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler-a"),
        llm=DeterministicMockLLMProvider(),
    ).build(source_project=source, output_root=tmp_path / "packages-a")
    incremental = await OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler-b"),
        llm=DeterministicMockLLMProvider(),
    ).build(
        source_project=source,
        output_root=tmp_path / "packages-b",
        previous_package=first.package_dir,
    )
    clean = await OfflineSemanticBrainCompiler(
        Settings(project_root=tmp_path / "compiler-c"),
        llm=DeterministicMockLLMProvider(),
    ).build(source_project=source, output_root=tmp_path / "packages-c")

    assert incremental.package_manifest.brain_version == clean.package_manifest.brain_version
    assert incremental.package_manifest.capsule_root == clean.package_manifest.capsule_root
    assert incremental.package_manifest.mechanism_claim_root == (
        clean.package_manifest.mechanism_claim_root
    )
    assert incremental.influence_manifest.record_membership_root == (
        clean.influence_manifest.record_membership_root
    )
    assert incremental.influence_manifest.reduce_tree_root == (
        clean.influence_manifest.reduce_tree_root
    )
    assert incremental.influence_manifest.close_return_status_distribution_root == (
        clean.influence_manifest.close_return_status_distribution_root
    )
    assert incremental.compile_manifest.llm_call_count == 0
    assert incremental.compile_manifest.reused_semantic_capsule_count == (
        incremental.package_manifest.semantic_capsule_count
    )
    assert incremental.compile_manifest.recompiled_semantic_capsule_count == 0
    assert incremental.compile_manifest.reused_reduce_node_count >= 1


@pytest.mark.asyncio
async def test_daily_reader_uses_only_precompiled_package(tmp_path: Path) -> None:
    source = _source_project(tmp_path / "source")
    settings = Settings(project_root=tmp_path / "compiler")
    result = await OfflineSemanticBrainCompiler(
        settings,
        llm=DeterministicMockLLMProvider(),
    ).build(source_project=source, output_root=settings.project_root / "brain" / "packages")
    pointer = select_brain_package(
        settings.project_root,
        package_dir=result.package_dir,
        production_activated=False,
    )
    assert read_json(pointer)["production_activated"] is False
    with pytest.raises(ValueError, match="production quality eligibility"):
        select_brain_package(
            settings.project_root,
            package_dir=result.package_dir,
            production_activated=True,
        )

    embedding = RecordingEmbedding384()
    provider = BrainPackageDailyContextProvider(
        settings,
        embedding_provider=embedding,
    )
    interpretation = CurrentDayInterpretation(
        analyzed_cluster_ids=["EVCL-fixture"],
        event_map=["fixture event"],
        policy_industry_macro_mechanisms=["fixture mechanism"],
        beneficiary_paths=["fixture beneficiary path"],
        uncertainties=["fixture uncertainty"],
        retrieval_queries=["fixture mechanism payload"],
    )
    current = CurrentEventCapsule(
        cluster_id="EVCL-fixture",
        source_row_ids=[1],
        event_ids=["EVT-fixture"],
        source_ids=["SRC-fixture"],
        representative_title="fixture event",
        published_times=[datetime(2026, 1, 2, 7, 0, tzinfo=KST)],
    )
    initial_context = await provider.retrieve(
        interpretation=None,
        current_event_capsules=[current],
        cutoff_at=datetime(2026, 1, 2, 8, 0, tzinfo=KST),
        max_exact_witnesses=24,
    )
    assert initial_context.retrieval_basis == "CURRENT_NEWS"
    assert initial_context.interpretation_sha256 is None
    assert initial_context.selected_semantic_capsules
    assert all(
        sum(row["close_return_status_distribution"].values())
        == row["member_record_count"]
        for row in initial_context.population_statistics
    )
    assert initial_context.brain_build_cutoff == result.package_manifest.build_cutoff
    assert initial_context.compiled_brain_guidance[0].content == (
        result.package_dir / "world_model.md"
    ).read_text(encoding="utf-8")
    assert len(initial_context.compiled_brain_guidance) == 1 + len(list(
        (result.package_dir / "category_brain").glob("*.md")
    ))
    historical_provider = BrainPackageDailyContextProvider(
        settings,
        package_dir=result.package_dir,
        embedding_provider=Embedding384(),
        allow_point_in_time_projection=True,
    )
    historical_cutoff = datetime(2025, 1, 5, 8, 0, tzinfo=KST)
    historical_context = await historical_provider.retrieve(
        interpretation=None,
        current_event_capsules=[current],
        cutoff_at=historical_cutoff,
        max_exact_witnesses=24,
    )
    assert historical_context.brain_projection_mode == "POINT_IN_TIME_EVIDENCE_ONLY"
    assert historical_context.compiled_brain_guidance == []
    assert all(
        capsule.available_from <= historical_cutoff
        for capsule in historical_context.selected_semantic_capsules
    )
    assert all(
        claim.available_from <= historical_cutoff
        for claim in historical_context.selected_mechanism_claims
    )
    _validate_brain_context_as_of(historical_context, cutoff_at=historical_cutoff)
    context = await provider.retrieve(
        interpretation=interpretation,
        current_event_capsules=[current],
        cutoff_at=datetime(2026, 1, 2, 8, 0, tzinfo=KST),
        max_exact_witnesses=24,
    )

    assert context.brain_version == result.package_manifest.brain_version
    assert context.selected_semantic_capsules
    assert context.selected_mechanism_claims
    assert len(context.exact_witnesses) <= 24
    assert context.online_full_corpus_scan_count == 0
    assert context.future_record_count == 0

    news_context = await provider.retrieve(
        interpretation=None,
        current_event_capsules=[current],
        cutoff_at=datetime(2026, 1, 2, 8, 0, tzinfo=KST),
        max_exact_witnesses=24,
    )
    assert embedding.queries[-1] == ["fixture event"]
    assert news_context.retrieval_basis == "CURRENT_NEWS"
    assert news_context.interpretation_sha256 is None
    assert news_context.brain_build_cutoff == result.package_manifest.build_cutoff
    assert news_context.current_event_capsules_sha256 == sha256_text(
        canonical_json([current.model_dump(mode="json")])
    )
    guidance = {row.artifact: row for row in news_context.compiled_brain_guidance}
    assert "world_model.md" in guidance
    assert any(path.startswith("category_brain/") for path in guidance)
    assert all(row.content.strip() for row in guidance.values())
    assert all(row.sha256 == sha256_text(row.content) for row in guidance.values())

    with pytest.raises(ValueError, match="built after the daily inference cutoff"):
        await provider.retrieve(
            interpretation=None,
            current_event_capsules=[current],
            cutoff_at=datetime(2024, 12, 31, 8, 0, tzinfo=KST),
            max_exact_witnesses=24,
        )
