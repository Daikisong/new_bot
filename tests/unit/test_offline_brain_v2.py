from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pytest

from news_scalping_lab.brain.offline_v2 import (
    BrainPackageDailyContextProvider,
    OfflineSemanticBrainCompiler,
    _materialize_long_payload_chunk_digest,
    _split_semantic_stratum,
    _utf8_chunks,
    _VectorRow,
    resolve_source_memory_snapshot,
    select_brain_package,
)
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.offline_brain import (
    CurrentDayInterpretation,
    CurrentEventCapsule,
    LongPayloadChunkDigestDraft,
    LongPayloadDigestBatch,
)
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.utils import KST, file_sha256, read_json, write_json


class Embedding384:
    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        del purpose
        vectors: list[list[float]] = []
        for text in texts:
            vector = np.zeros(384, dtype=np.float32)
            vector[sum(text.encode("utf-8")) % 8] = 1.0
            vectors.append(vector.tolist())
        return vectors


def _vector(index: int) -> list[float]:
    value = np.zeros(384, dtype=np.float32)
    value[index] = 1.0
    return value.tolist()


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
                "OBSERVED_POSITIVE" if positive else "OBSERVED_NEGATIVE",
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
    assert result.compile_manifest.full_population_embedding_geometry is True
    assert result.compile_manifest.semantic_splitter_version.startswith(
        "recursive_full_population_cosine_radius"
    )
    assert result.influence_manifest.primary_assignment_count == 12
    assert result.influence_manifest.population_contribution_record_count == 12
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

    connection = duckdb.connect(
        str(result.package_dir / "semantic_capsule_index.duckdb"),
        read_only=True,
    )
    try:
        connection.execute("LOAD vss")
        assert connection.execute("SELECT count(*) FROM semantic_unit_assignments").fetchone()[0] == 12
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
    assert plan["planning_llm_call_count"] == 0
    assert plan["embedding_reused"] is True
    assert plan["import_reused"] is True
    assert plan["full_population_embedding_geometry"] is True


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

    provider = BrainPackageDailyContextProvider(
        settings,
        embedding_provider=Embedding384(),
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
    with pytest.raises(ValueError, match="built after the daily inference cutoff"):
        await provider.retrieve(
            interpretation=interpretation,
            current_event_capsules=[current],
            cutoff_at=datetime(2024, 12, 31, 8, 0, tzinfo=KST),
            max_exact_witnesses=24,
        )
