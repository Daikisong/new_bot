"""Daily population-aware memory context assembled from immutable Phase 4-6 artifacts."""

from __future__ import annotations

import json
import math
import os
import struct
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from news_scalping_lab.brain.category_index import (
    CategoryBrainIndex,
    category_guidance_claims,
    inspect_category_brain_index,
)
from news_scalping_lab.contracts.memory_context import (
    POPULATION_PURPOSE_LANES,
    AdaptiveRetrievalTrace,
    ArtifactReference,
    BeneficiaryGraphArtifact,
    CategoryBrainGuidance,
    CategoryBrainIndexManifest,
    CategoryBrainQueryPlan,
    DailyMemoryContext,
    EventClusterManifest,
    IndependentUnitType,
    MemoryCoverageManifest,
    NewsCoverageManifest,
    PopulationManifest,
    PopulationPurpose,
    RepresentativeRecord,
    RepresentativeSetManifest,
)
from news_scalping_lab.contracts.models import BrainManifest
from news_scalping_lab.contracts.runtime_retrieval import (
    RuntimeEvidenceMemo,
    RuntimeEvidencePackManifest,
    RuntimeRetrievalTrace,
)
from news_scalping_lab.memory.adaptive_retrieval import (
    ADAPTIVE_CELLS_PER_ITERATION,
    ADAPTIVE_MAX_CELL_COUNT,
    ADAPTIVE_MAX_DEPTH,
    ADAPTIVE_MAX_RECORD_COUNT,
    ADAPTIVE_MAX_TOKEN_COUNT,
    ADAPTIVE_MIN_INFORMATION_GAIN,
    ADAPTIVE_RETRIEVAL_POLICY_VERSION,
    ADAPTIVE_SMALL_EFFECTIVE_SAMPLE_SIZE,
    AdaptiveRetriever,
    _inspect_built_adaptive,
)
from news_scalping_lab.memory.beneficiary import (
    beneficiary_trigger_evidence,
    beneficiary_trigger_evidence_from_artifact,
    inspect_beneficiary_graph,
)
from news_scalping_lab.memory.diversity import (
    REPRESENTATIVE_CONTEXT_EXCERPT_CHARS,
    REPRESENTATIVE_DISTRIBUTION_SHARE_ERROR_TOLERANCE,
    REPRESENTATIVE_FACILITY_ANCHOR_COUNT,
    REPRESENTATIVE_INITIAL_MAX_RECORDS,
    REPRESENTATIVE_INITIAL_MIN_RECORDS,
    REPRESENTATIVE_MAX_CANDIDATE_POOL,
    REPRESENTATIVE_MAX_SELECTED_RECORDS,
    REPRESENTATIVE_MAX_STRATUM_RESERVE,
    REPRESENTATIVE_MAX_TOKEN_COUNT,
    REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION,
    REPRESENTATIVE_MAX_UNIT_KEY_CONCENTRATION,
    REPRESENTATIVE_QUALITY_FULL_MAX_TOKEN_COUNT,
    REPRESENTATIVE_QUALITY_FULL_SELECTION_VERSION,
    REPRESENTATIVE_SELECTION_VERSION,
    RepresentativeSelectionBudgetError,
    RepresentativeSelector,
)
from news_scalping_lab.memory.index import (
    MEMORY_INDEX_RUNTIME_QUERY_VERSION,
    MemoryCellCandidate,
    ProductionMemoryIndex,
)
from news_scalping_lab.memory.population import (
    POPULATION_CUBE_VERSION,
    POPULATION_MAX_CUBE_ROWS,
    POPULATION_MAX_SELECTED_RECORDS,
    POPULATION_PURPOSE_CLASSIFIER_VERSION,
    POPULATION_SELECTION_BUDGET_VERSION,
    PopulationRetriever,
    _purpose_record_types_sha256,
)
from news_scalping_lab.memory.runtime_v4 import (
    RUNTIME_RETRIEVAL_POLICY_VERSION,
    RUNTIME_RETRIEVAL_ROOT,
    RuntimeEvidenceBuildResult,
    RuntimeRetrievalBuildResult,
    build_runtime_retrieval_trace,
    candidates_from_daily_artifacts,
)
from news_scalping_lab.memory.statistics import (
    BLOCK_BOOTSTRAP_VERSION,
    POPULATION_STATISTICS_VERSION,
)
from news_scalping_lab.records.models import CompiledBrainClaim
from news_scalping_lab.retrieval.production_embedding import embedding_identity
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    parse_datetime,
    read_json,
    relative_to_root,
    sha256_bytes,
    sha256_text,
)

DAILY_MEMORY_CONTEXT_VERSION = "daily_population_context.v2"
DAILY_MEMORY_CONTEXT_ROOT = Path("runs/checkpoints/daily_memory_context")
DAILY_MEMORY_CONTEXT_INITIAL_FILENAME = "daily_memory_context.json"
DAILY_MEMORY_CONTEXT_RUNTIME_EVIDENCE_FILENAME = (
    "daily_memory_context.runtime_evidence.json"
)
DAILY_MEMORY_CONTEXT_FINAL_FILENAME = "daily_memory_context.final.json"
DAILY_MEMORY_COMPACT_INITIAL_FILENAME = "compact_final_context.json"
DAILY_MEMORY_COMPACT_RUNTIME_EVIDENCE_FILENAME = (
    "compact_runtime_evidence_context.json"
)
DAILY_MEMORY_COMPACT_FINAL_FILENAME = "compact_final_beneficiary_context.json"
DAILY_MEMORY_CONTEXT_MAX_BYTES = 48_000
DAILY_MEMORY_CONTEXT_INITIAL_MAX_BYTES = 30_000
DAILY_CATEGORY_GUIDANCE_MAX_COUNT = 24
DAILY_MEMORY_COMPACT_V2_SCHEMA = "nslab.daily_memory_compact_context.v2"
DAILY_MEMORY_COMPACT_V2_POLICY = "complete_roots_bounded_semantic_rows.v1"
QUALITY_FULL_MAX_CLUSTER_WORKERS = 2
DAILY_CLUSTER_CHECKPOINT_VERSION = "daily_cluster_artifacts.v2"
DAILY_CLUSTER_CHECKPOINT_SCHEMA = "nslab.daily_cluster_checkpoint.v2"
DAILY_RUNTIME_RETRIEVAL_CHECKPOINT_VERSION = "daily_runtime_retrieval.v1"
DAILY_RUNTIME_RETRIEVAL_CHECKPOINT_SCHEMA = (
    "nslab.daily_runtime_retrieval_checkpoint.v1"
)
DAILY_POPULATION_PURPOSE_UNITS: dict[
    PopulationPurpose,
    tuple[IndependentUnitType, ...],
] = {
    "catalyst_response": (
        "event-issuer-day",
        "issuer-day",
        "theme-day",
        "theme-day-ticker-day",
    ),
    "candidate_error": (
        "event-issuer-day",
        "issuer-day",
        "theme-day-ticker-day",
    ),
    "newsless": ("ticker-day",),
}


@dataclass(frozen=True)
class _DailyClusterBuild:
    cluster_id: str
    populations: list[ArtifactReference]
    representatives: list[ArtifactReference]
    adaptive_traces: list[ArtifactReference]
    population_paths: list[Path]
    representative_paths: list[Path]
    ann_ranks: dict[str, int]
    fts_ranks: dict[str, int]
    built_population_keys: list[str]
    uncovered_purposes: list[PopulationPurpose]


def _normalized_query_vector(vector: list[float]) -> list[float]:
    if not vector or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in vector
    ):
        raise ValueError("daily memory query embedding must be non-empty and finite")
    return [struct.unpack("!f", struct.pack("!f", value))[0] for value in vector]


def _daily_input_cluster_roots(
    manifest: EventClusterManifest,
    cluster_rows: list[dict[str, Any]],
) -> dict[str, str]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in cluster_rows:
        cluster_id = row.get("cluster_id")
        if not isinstance(cluster_id, str) or not cluster_id.strip():
            raise ValueError("daily memory event cluster row identity is invalid")
        if cluster_id in rows_by_id:
            raise ValueError("daily memory event cluster rows are duplicated")
        rows_by_id[cluster_id] = row
    event_policy = {
        "schema_version": manifest.schema_version,
        "clustering_version": manifest.clustering_version,
        "embedding_provider": manifest.embedding_provider,
        "embedding_model": manifest.embedding_model,
        "embedding_revision": manifest.embedding_revision,
        "embedding_artifact_sha256": manifest.embedding_artifact_sha256,
        "production_runtime_identity": manifest.production_runtime_identity,
    }
    roots: dict[str, str] = {}
    for cluster in manifest.clusters:
        if cluster.disposition != "MATERIAL_FULL_RETRIEVAL":
            continue
        cluster_row = rows_by_id.get(cluster.cluster_id)
        if cluster_row is None:
            raise ValueError(
                f"daily memory event cluster row is missing: {cluster.cluster_id}"
            )
        roots[cluster.cluster_id] = sha256_text(
            canonical_json(
                {
                    "schema_version": "nslab.daily_input_cluster_root.v1",
                    "event_policy": event_policy,
                    "cluster": cluster.model_dump(mode="json"),
                    "cluster_row_sha256": sha256_text(
                        canonical_json(cluster_row)
                    ),
                }
            )
        )
    return roots


def _daily_cluster_semantic_policy_root(
    *,
    allow_distribution_shortfall: bool,
) -> str:
    population_purpose_hashes = {
        purpose: _purpose_record_types_sha256(purpose)
        for purpose in DAILY_POPULATION_PURPOSE_UNITS
    }
    payload = {
        "schema_version": "nslab.daily_cluster_semantic_policy.v1",
        "daily_context_version": DAILY_MEMORY_CONTEXT_VERSION,
        "checkpoint_version": DAILY_CLUSTER_CHECKPOINT_VERSION,
        "memory_query_version": MEMORY_INDEX_RUNTIME_QUERY_VERSION,
        "purpose_units": {
            purpose: list(unit_types)
            for purpose, unit_types in DAILY_POPULATION_PURPOSE_UNITS.items()
        },
        "purpose_lanes": {
            purpose: list(lanes)
            for purpose, lanes in POPULATION_PURPOSE_LANES.items()
        },
        "population": {
            "statistics_version": POPULATION_STATISTICS_VERSION,
            "cube_version": POPULATION_CUBE_VERSION,
            "selection_budget_version": POPULATION_SELECTION_BUDGET_VERSION,
            "max_selected_record_count": POPULATION_MAX_SELECTED_RECORDS,
            "max_cube_row_count": POPULATION_MAX_CUBE_ROWS,
            "purpose_classifier_version": POPULATION_PURPOSE_CLASSIFIER_VERSION,
            "purpose_record_types_sha256": population_purpose_hashes,
            "bootstrap_version": BLOCK_BOOTSTRAP_VERSION,
        },
        "representative": {
            "selection_version": REPRESENTATIVE_SELECTION_VERSION,
            "quality_full_selection_version": (
                REPRESENTATIVE_QUALITY_FULL_SELECTION_VERSION
            ),
            "allow_distribution_shortfall": allow_distribution_shortfall,
            "max_selected_record_count": REPRESENTATIVE_MAX_SELECTED_RECORDS,
            "max_candidate_pool_count": REPRESENTATIVE_MAX_CANDIDATE_POOL,
            "max_token_count": REPRESENTATIVE_MAX_TOKEN_COUNT,
            "quality_full_max_token_count": (
                REPRESENTATIVE_QUALITY_FULL_MAX_TOKEN_COUNT
            ),
            "max_trade_date_concentration": (
                REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION
            ),
            "max_unit_key_concentration": (
                REPRESENTATIVE_MAX_UNIT_KEY_CONCENTRATION
            ),
            "context_excerpt_chars": REPRESENTATIVE_CONTEXT_EXCERPT_CHARS,
            "initial_min_records": REPRESENTATIVE_INITIAL_MIN_RECORDS,
            "initial_max_records": REPRESENTATIVE_INITIAL_MAX_RECORDS,
            "facility_anchor_count": REPRESENTATIVE_FACILITY_ANCHOR_COUNT,
            "max_stratum_reserve": REPRESENTATIVE_MAX_STRATUM_RESERVE,
            "distribution_share_error_tolerance": (
                REPRESENTATIVE_DISTRIBUTION_SHARE_ERROR_TOLERANCE
            ),
        },
        "adaptive": {
            "policy_version": ADAPTIVE_RETRIEVAL_POLICY_VERSION,
            "max_depth": ADAPTIVE_MAX_DEPTH,
            "max_cell_count": ADAPTIVE_MAX_CELL_COUNT,
            "max_record_count": ADAPTIVE_MAX_RECORD_COUNT,
            "max_token_count": ADAPTIVE_MAX_TOKEN_COUNT,
            "quality_full_max_token_count": (
                REPRESENTATIVE_QUALITY_FULL_MAX_TOKEN_COUNT
            ),
            "min_information_gain": ADAPTIVE_MIN_INFORMATION_GAIN,
            "cells_per_iteration": ADAPTIVE_CELLS_PER_ITERATION,
            "small_effective_sample_size": ADAPTIVE_SMALL_EFFECTIVE_SAMPLE_SIZE,
        },
    }
    return sha256_text(canonical_json(payload))


def build_daily_memory_context(
    root: Path,
    *,
    memory_index: ProductionMemoryIndex,
    run_id: str,
    trade_date: date,
    cutoff_at: datetime,
    corpus_manifest_sha256: str,
    news_coverage_manifest_path: Path,
    event_cluster_manifest_path: Path,
    event_cluster_artifact_path: Path,
    memory_coverage_manifest_path: Path,
    beneficiary_graph_path: Path,
    retrieval_cluster_ids: set[str] | None = None,
    max_cluster_workers: int = 1,
    allow_distribution_shortfall: bool = False,
) -> tuple[DailyMemoryContext, Path]:
    root = root.resolve()
    if max_cluster_workers < 1 or max_cluster_workers > QUALITY_FULL_MAX_CLUSTER_WORKERS:
        raise ValueError("daily memory cluster workers must be between 1 and 2")
    event_manifest = EventClusterManifest.model_validate(
        read_json(event_cluster_manifest_path)
    )
    if event_manifest.run_id != run_id:
        raise ValueError("daily memory event cluster manifest is invalid")
    if as_kst(event_manifest.cutoff_at) != as_kst(cutoff_at):
        raise ValueError("daily memory event cluster cutoff mismatch")
    graph = BeneficiaryGraphArtifact.model_validate(read_json(beneficiary_graph_path))
    if graph.run_id != run_id or as_kst(graph.cutoff_at) != as_kst(cutoff_at):
        raise ValueError("daily memory beneficiary graph identity mismatch")
    cluster_rows = _read_jsonl(event_cluster_artifact_path)
    all_cluster_queries = material_cluster_queries_from_sources(
        event_manifest,
        cluster_rows,
    )
    input_cluster_roots = _daily_input_cluster_roots(
        event_manifest,
        cluster_rows,
    )
    known_cluster_ids = {cluster_id for cluster_id, _query in all_cluster_queries}
    if retrieval_cluster_ids is not None:
        unknown_cluster_ids = sorted(retrieval_cluster_ids - known_cluster_ids)
        if unknown_cluster_ids:
            raise ValueError(
                "daily memory retrieval scope contains unknown material clusters: " + ", ".join(unknown_cluster_ids)
            )
        cluster_queries = [item for item in all_cluster_queries if item[0] in retrieval_cluster_ids]
    else:
        cluster_queries = all_cluster_queries
    if (
        not cluster_queries
        and retrieval_cluster_ids is None
        and event_manifest.material_cluster_count
    ):
        raise ValueError("daily memory material cluster queries are missing")
    snapshot = memory_index.resolve_snapshot(cutoff_at=cutoff_at)
    if snapshot.corpus_manifest_sha256 != corpus_manifest_sha256:
        raise ValueError("daily memory corpus differs from the resolved snapshot")
    category_manifest_path, category_index_path = _category_brain_snapshot(
        root,
        cutoff_at=cutoff_at,
        memory_snapshot_id=snapshot.snapshot_id,
        corpus_manifest_sha256=snapshot.corpus_manifest_sha256,
        source_generation_sha256=snapshot.source_generation_sha256,
    )
    category_manifest_reference = _artifact_reference(root, category_manifest_path)
    category_index_reference = _artifact_reference(root, category_index_path)
    populations: list[ArtifactReference] = []
    representatives: list[ArtifactReference] = []
    adaptive_traces: list[ArtifactReference] = []
    runtime_traces: list[ArtifactReference] = []
    population_paths_by_cluster: dict[str, list[Path]] = {}
    representative_paths_by_cluster: dict[str, list[Path]] = {}
    ann_rank_by_cluster: dict[str, dict[str, int]] = {}
    fts_rank_by_cluster: dict[str, dict[str, int]] = {}
    category_index = CategoryBrainIndex(
        root,
        category_index_path,
        embedding_provider=memory_index.embedding_provider,
    )
    try:
        query_vectors = [
            _normalized_query_vector(vector)
            for vector in memory_index.embedding_provider.embed_texts(
                [query for _cluster_id, query in cluster_queries]
            )
        ]
        if len(query_vectors) != len(cluster_queries):
            raise ValueError("daily memory query embedding coverage mismatch")
        category_query_plans = [
            category_index.query(
                cluster_id=cluster_id,
                query=query,
                query_vector=query_vector,
            )
            for (cluster_id, query), query_vector in zip(
                cluster_queries,
                query_vectors,
                strict=True,
            )
        ]
    finally:
        category_index.close()
    expanded_query_vectors = [
        _normalized_query_vector(vector)
        for vector in memory_index.embedding_provider.embed_texts(
            [plan.expanded_query for plan in category_query_plans]
        )
    ]
    if len(expanded_query_vectors) != len(category_query_plans):
        raise ValueError("daily memory expanded query embedding coverage mismatch")
    embedding_runtime_identity = {
        **embedding_identity(memory_index.embedding_provider),
        "runtime_embedding_dimensions": getattr(
            memory_index.embedding_provider,
            "dimensions",
            0,
        ),
        "snapshot_embedding_provider": snapshot.embedding_provider,
        "snapshot_embedding_model": snapshot.embedding_model,
        "snapshot_embedding_dimensions": snapshot.embedding_dimensions,
    }
    if (
        embedding_runtime_identity.get("embedding_method")
        != snapshot.embedding_model
        or embedding_runtime_identity.get("runtime_embedding_dimensions")
        != snapshot.embedding_dimensions
    ):
        raise ValueError("daily memory embedding runtime differs from its snapshot")
    for query_plan, query_vector in zip(
        category_query_plans,
        query_vectors,
        strict=True,
    ):
        if (
            query_plan.query_embedding_sha256
            != sha256_text(canonical_json(query_vector))
            or query_plan.embedding_model != snapshot.embedding_model
        ):
            raise ValueError("daily memory category query embedding identity mismatch")
    semantic_policy_root = _daily_cluster_semantic_policy_root(
        allow_distribution_shortfall=allow_distribution_shortfall,
    )
    uncovered_material_cluster_ids: list[str] = []
    built_population_keys: list[str] = []
    uncovered_population_purposes: dict[str, list[PopulationPurpose]] = {}
    material_cluster_ids = [cluster_id for cluster_id, _query in cluster_queries]
    cluster_inputs = list(
        zip(
            cluster_queries,
            category_query_plans,
            query_vectors,
            expanded_query_vectors,
            strict=True,
        )
    )

    def build_cluster(
        item: tuple[
            tuple[str, str],
            CategoryBrainQueryPlan,
            list[float],
            list[float],
        ],
    ) -> _DailyClusterBuild:
        (cluster_id, query), query_plan, query_vector, expanded_query_vector = item
        return _build_daily_cluster_artifacts(
            root,
            memory_index=memory_index,
            run_id=run_id,
            cutoff_at=cutoff_at,
            beneficiary_graph_path=beneficiary_graph_path,
            cluster_id=cluster_id,
            input_cluster_root=input_cluster_roots[cluster_id],
            query=query,
            query_plan=query_plan,
            query_vector=query_vector,
            expanded_query_vector=expanded_query_vector,
            memory_snapshot_id=snapshot.snapshot_id,
            source_generation_sha256=snapshot.source_generation_sha256,
            corpus_manifest_sha256=snapshot.corpus_manifest_sha256,
            semantic_policy_root=semantic_policy_root,
            category_brain_manifest=category_manifest_reference,
            category_brain_index_manifest=category_index_reference,
            embedding_runtime_identity=embedding_runtime_identity,
            allow_distribution_shortfall=allow_distribution_shortfall,
        )

    if max_cluster_workers == 1 or len(cluster_inputs) < 2:
        cluster_builds = [build_cluster(item) for item in cluster_inputs]
    else:
        with ThreadPoolExecutor(
            max_workers=min(max_cluster_workers, len(cluster_inputs)),
            thread_name_prefix="nslab-daily-memory",
        ) as executor:
            cluster_builds = list(executor.map(build_cluster, cluster_inputs))
    for cluster_build in cluster_builds:
        cluster_id = cluster_build.cluster_id
        populations.extend(cluster_build.populations)
        representatives.extend(cluster_build.representatives)
        adaptive_traces.extend(cluster_build.adaptive_traces)
        population_paths_by_cluster[cluster_id] = cluster_build.population_paths
        representative_paths_by_cluster[cluster_id] = cluster_build.representative_paths
        ann_rank_by_cluster[cluster_id] = cluster_build.ann_ranks
        fts_rank_by_cluster[cluster_id] = cluster_build.fts_ranks
        built_population_keys.extend(cluster_build.built_population_keys)
        uncovered_population_purposes[cluster_id] = cluster_build.uncovered_purposes
        if "catalyst_response" in cluster_build.uncovered_purposes:
            uncovered_material_cluster_ids.append(cluster_id)
    from news_scalping_lab.audits.semantic_exposure import SemanticExposureIndex

    exposure_index = SemanticExposureIndex.open_current(root)
    try:
        if exposure_index is not None:
            brain_identity = read_json(category_manifest_path)
            if not isinstance(brain_identity, dict) or exposure_index.manifest.get(
                "brain_version"
            ) != brain_identity.get("brain_version"):
                raise ValueError("semantic exposure index differs from the daily brain")
        exposure_manifest_root = sha256_text(
            canonical_json(
                exposure_index.manifest
                if exposure_index is not None
                else {"semantic_exposure_index": "ABSENT"}
            )
        )
        for cluster_id, query in cluster_queries:
            population_paths = population_paths_by_cluster.get(cluster_id, [])
            representative_paths = representative_paths_by_cluster.get(cluster_id, [])
            source_population_manifests = [
                reference
                for reference in populations
                if (root / reference.artifact_path) in population_paths
            ]
            source_representative_manifests = [
                reference
                for reference in representatives
                if (root / reference.artifact_path) in representative_paths
            ]
            runtime_identity = _daily_runtime_retrieval_identity(
                run_id=run_id,
                cluster_id=cluster_id,
                query=query,
                cutoff_at=cutoff_at,
                memory_snapshot_id=snapshot.snapshot_id,
                population_manifests=source_population_manifests,
                representative_manifests=source_representative_manifests,
                ann_ranks=ann_rank_by_cluster.get(cluster_id, {}),
                fts_ranks=fts_rank_by_cluster.get(cluster_id, {}),
                exposure_manifest_root=exposure_manifest_root,
            )
            runtime_result = _load_daily_runtime_retrieval_checkpoint(
                root,
                identity=runtime_identity,
                query=query,
            )
            if runtime_result is None:
                candidates = candidates_from_daily_artifacts(
                    root,
                    cluster_id=cluster_id,
                    population_manifest_paths=population_paths,
                    representative_manifest_paths=representative_paths,
                    exposure_resolver=exposure_index,
                    ann_rank_by_cell=ann_rank_by_cluster.get(cluster_id),
                    fts_rank_by_cell=fts_rank_by_cluster.get(cluster_id),
                    memory_index=memory_index,
                    cutoff_at=cutoff_at,
                    memory_snapshot_id=snapshot.snapshot_id,
                )
                runtime_result = build_runtime_retrieval_trace(
                    root,
                    run_id=run_id,
                    cluster_id=cluster_id,
                    query_text=query,
                    cutoff_at=cutoff_at,
                    memory_snapshot_id=snapshot.snapshot_id,
                    candidates=candidates,
                    source_population_manifests=source_population_manifests,
                    source_representative_manifests=(
                        source_representative_manifests
                    ),
                )
                _write_daily_runtime_retrieval_checkpoint(
                    root,
                    identity=runtime_identity,
                    retrieval=runtime_result,
                    adopted_existing_trace=False,
                )
                validated_runtime_result = (
                    _load_daily_runtime_retrieval_checkpoint(
                        root,
                        identity=runtime_identity,
                        query=query,
                    )
                )
                if validated_runtime_result is None:
                    raise ValueError(
                        "daily runtime retrieval checkpoint was not persisted"
                    )
                runtime_result = validated_runtime_result
            runtime_traces.append(_artifact_reference(root, runtime_result.trace_path))
    finally:
        if exposure_index is not None:
            exposure_index.close()
    representative_rows = _representative_rows(root, representatives)
    selected_record_ids = {
        str(row["record_id"]) for row in representative_rows if isinstance(row.get("record_id"), str)
    }
    category_index = CategoryBrainIndex(root, category_index_path)
    try:
        guidance_claims = category_index.guidance_claims(
            selected_record_ids=selected_record_ids,
            limit=DAILY_CATEGORY_GUIDANCE_MAX_COUNT,
        )
        proof_claim_ids = {claim_id for plan in category_query_plans for claim_id in plan.selected_claim_ids} | {
            claim.claim_id for claim in guidance_claims
        }
        proof_claims = category_index.claims_by_ids(proof_claim_ids)
        proof_by_id = category_index.claim_proofs_by_ids(proof_claim_ids)
    finally:
        category_index.close()
    output_dir = root / DAILY_MEMORY_CONTEXT_ROOT / run_id
    selected_claim_path = output_dir / "selected_category_claims.jsonl"
    _write_immutable_bytes(
        selected_claim_path,
        "".join(claim.model_dump_json() + "\n" for claim in proof_claims).encode("utf-8"),
    )
    category_guidance = _category_guidance(
        guidance_claims,
        path=selected_claim_path,
        root=root,
        selected_record_ids=selected_record_ids,
        cutoff_at=cutoff_at,
    )
    population_summaries = _population_summaries(root, populations)
    supporting, contradicting, unexplained = daily_memory_record_roles(representative_rows)
    disagreements = daily_memory_disagreements(population_summaries)
    compact_payload = compact_daily_memory_payload(
        run_id=run_id,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        memory_snapshot_id=snapshot.snapshot_id,
        material_event_cluster_ids=material_cluster_ids,
        uncovered_material_event_cluster_ids=uncovered_material_cluster_ids,
        built_population_keys=sorted(built_population_keys),
        uncovered_population_purposes=uncovered_population_purposes,
        population_summaries=population_summaries,
        representative_records=representative_rows,
        category_query_plans=category_query_plans,
        category_guidance=category_guidance,
        graph=graph,
        disagreements=disagreements,
        supporting_record_ids=supporting,
        contradicting_record_ids=contradicting,
        unexplained_record_ids=unexplained,
    )
    compact_bytes = canonical_json(compact_payload).encode("utf-8")
    if len(compact_bytes) > DAILY_MEMORY_CONTEXT_MAX_BYTES:
        raise ValueError(
            f"daily memory compact context exceeds byte budget: {len(compact_bytes)} > {DAILY_MEMORY_CONTEXT_MAX_BYTES}"
        )
    compact_path = output_dir / DAILY_MEMORY_COMPACT_INITIAL_FILENAME
    context_path = output_dir / DAILY_MEMORY_CONTEXT_INITIAL_FILENAME
    _write_immutable_bytes(compact_path, compact_bytes)
    context = DailyMemoryContext(
        run_id=run_id,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        corpus_manifest_sha256=corpus_manifest_sha256,
        news_coverage_manifest=_artifact_reference(root, news_coverage_manifest_path),
        event_cluster_manifest=_artifact_reference(root, event_cluster_manifest_path),
        event_clusters=_artifact_reference(
            root,
            event_cluster_artifact_path,
            item_count=len(cluster_rows),
        ),
        memory_coverage_manifest=_artifact_reference(root, memory_coverage_manifest_path),
        memory_snapshot_id=snapshot.snapshot_id,
        source_generation_sha256=snapshot.source_generation_sha256,
        material_event_cluster_ids=material_cluster_ids,
        runtime_retrieval_cluster_ids=[cluster_id for cluster_id, _query in cluster_queries],
        uncovered_material_event_cluster_ids=uncovered_material_cluster_ids,
        built_population_keys=sorted(built_population_keys),
        uncovered_population_purposes=uncovered_population_purposes,
        deferred_population_purposes=["leader_selection"],
        population_manifests=populations,
        representative_set_manifests=representatives,
        adaptive_retrieval_traces=adaptive_traces,
        runtime_retrieval_traces=runtime_traces,
        category_brain_manifest=_artifact_reference(root, category_manifest_path),
        category_brain_index_manifest=_artifact_reference(root, category_index_path),
        category_selected_claims=_artifact_reference(root, selected_claim_path, item_count=len(proof_claims)),
        category_selected_claim_proofs=proof_by_id,
        category_query_plans=category_query_plans,
        category_guidance=category_guidance,
        beneficiary_graph=_artifact_reference(root, beneficiary_graph_path),
        compact_final_context=_artifact_reference(root, compact_path),
        supporting_record_ids=supporting,
        contradicting_record_ids=contradicting,
        unexplained_record_ids=unexplained,
        unresolved_disagreements=disagreements,
        estimated_token_count=len(compact_bytes),
        context_complete=True,
    )
    _write_immutable_json(context_path, context.model_dump(mode="json"))
    return context, context_path


def _build_daily_cluster_artifacts(
    root: Path,
    *,
    memory_index: ProductionMemoryIndex,
    run_id: str,
    cutoff_at: datetime,
    beneficiary_graph_path: Path,
    cluster_id: str,
    input_cluster_root: str,
    query: str,
    query_plan: CategoryBrainQueryPlan,
    query_vector: list[float],
    expanded_query_vector: list[float],
    memory_snapshot_id: str,
    source_generation_sha256: str,
    corpus_manifest_sha256: str,
    semantic_policy_root: str,
    category_brain_manifest: ArtifactReference,
    category_brain_index_manifest: ArtifactReference,
    embedding_runtime_identity: dict[str, Any],
    allow_distribution_shortfall: bool,
) -> _DailyClusterBuild:
    query_embedding_sha256 = sha256_text(canonical_json(query_vector))
    checkpoint_identity = {
        "schema_version": "nslab.daily_cluster_checkpoint_identity.v2",
        "checkpoint_version": DAILY_CLUSTER_CHECKPOINT_VERSION,
        "run_id": run_id,
        "cluster_id": cluster_id,
        "input_cluster_root": input_cluster_root,
        "cutoff_at": as_kst(cutoff_at).isoformat(),
        "query_sha256": sha256_text(query),
        "query_plan_sha256": sha256_text(
            canonical_json(query_plan.model_dump(mode="json"))
        ),
        "query_embedding_sha256": query_embedding_sha256,
        "expanded_query_embedding_sha256": sha256_text(
            canonical_json(expanded_query_vector)
        ),
        "memory_snapshot_id": memory_snapshot_id,
        "source_generation_sha256": source_generation_sha256,
        "corpus_manifest_sha256": corpus_manifest_sha256,
        "beneficiary_graph_sha256": file_sha256(beneficiary_graph_path),
        "semantic_policy_root": semantic_policy_root,
        "category_brain_manifest": category_brain_manifest.model_dump(
            mode="json"
        ),
        "category_brain_index_manifest": (
            category_brain_index_manifest.model_dump(mode="json")
        ),
        "embedding_runtime_identity": embedding_runtime_identity,
        "allow_distribution_shortfall": allow_distribution_shortfall,
    }
    checkpoint_id = "DCL-" + sha256_text(canonical_json(checkpoint_identity))[
        :20
    ].upper()
    checkpoint_path = (
        root
        / DAILY_MEMORY_CONTEXT_ROOT
        / run_id
        / "clusters"
        / cluster_id
        / checkpoint_id
        / "cluster_checkpoint.json"
    )
    cached = _load_daily_cluster_checkpoint(
        root,
        checkpoint_path=checkpoint_path,
        expected_identity=checkpoint_identity,
        expected_checkpoint_id=checkpoint_id,
    )
    if cached is not None:
        return cached
    graph_trigger = beneficiary_trigger_evidence(
        root,
        beneficiary_graph_path,
        cluster_id=cluster_id,
    )
    populations: list[ArtifactReference] = []
    representatives: list[ArtifactReference] = []
    adaptive_traces: list[ArtifactReference] = []
    population_paths: list[Path] = []
    representative_paths: list[Path] = []
    ann_ranks: dict[str, int] = {}
    fts_ranks: dict[str, int] = {}
    built_population_keys: list[str] = []
    uncovered_purposes: list[PopulationPurpose] = []
    for population_purpose, unit_types in DAILY_POPULATION_PURPOSE_UNITS.items():
        lanes = POPULATION_PURPOSE_LANES[population_purpose]
        base_cells = memory_index.search_cells(
            query,
            cutoff_at=cutoff_at,
            limit=4,
            query_vector=query_vector,
            included_memory_lanes=lanes,
        )
        planned_cells = memory_index.search_cells(
            query_plan.expanded_query,
            cutoff_at=cutoff_at,
            limit=4,
            query_vector=expanded_query_vector,
            included_memory_lanes=lanes,
        )
        ann_ranks.update(
            _minimum_channel_ranks([*base_cells, *planned_cells], channel="ann")
        )
        fts_ranks.update(
            _minimum_channel_ranks([*base_cells, *planned_cells], channel="fts")
        )
        cells = _cell_candidate_union(base_cells, planned_cells, limit=8)
        if not cells:
            uncovered_purposes.append(population_purpose)
            continue
        selected_cell_ids = [item.cell_id for item in cells]
        purpose_population_count = 0
        for unit_type in unit_types:
            try:
                population_result = PopulationRetriever(
                    root,
                    memory_index=memory_index,
                ).build(
                    run_id=run_id,
                    cluster_id=cluster_id,
                    cutoff_at=cutoff_at,
                    selected_cell_ids=selected_cell_ids,
                    independent_unit_type=unit_type,
                    population_purpose=population_purpose,
                )
            except ValueError as exc:
                if str(exc).startswith("selected cells contain no records"):
                    continue
                raise
            representative_selector = RepresentativeSelector(
                root,
                memory_index=memory_index,
            )
            try:
                representative_result = representative_selector.build(
                    population_manifest_path=population_result.manifest_path,
                    query=query,
                    query_vector=query_vector,
                )
            except RepresentativeSelectionBudgetError:
                if not allow_distribution_shortfall:
                    raise
                representative_result = representative_selector.build(
                    population_manifest_path=population_result.manifest_path,
                    query=query,
                    query_vector=query_vector,
                    allow_distribution_shortfall=True,
                )
            trigger_evidence = (
                [graph_trigger]
                if population_purpose == "catalyst_response"
                and graph_trigger is not None
                else []
            )
            trace, trace_path = AdaptiveRetriever(
                root,
                memory_index=memory_index,
            ).run(
                initial_population_manifest_path=population_result.manifest_path,
                initial_representative_set_manifest_path=(
                    representative_result.manifest_path
                ),
                query=query,
                query_vector=query_vector,
                max_token_count=(
                    representative_result.manifest.max_token_count
                ),
                trigger_evidence=trigger_evidence,
            )
            if (
                representative_result.manifest.query_embedding_sha256
                != query_embedding_sha256
                or trace.query_embedding_sha256 != query_embedding_sha256
            ):
                raise ValueError(
                    "daily cluster query embedding artifact chain mismatch"
                )
            populations.append(trace.final_population_manifest)
            representatives.append(trace.final_representative_set_manifest)
            adaptive_traces.append(_artifact_reference(root, trace_path))
            population_paths.append(
                root / trace.final_population_manifest.artifact_path
            )
            representative_paths.append(
                root / trace.final_representative_set_manifest.artifact_path
            )
            built_population_keys.append(
                _population_key(cluster_id, population_purpose, unit_type)
            )
            purpose_population_count += 1
        if purpose_population_count == 0:
            uncovered_purposes.append(population_purpose)
    result = _DailyClusterBuild(
        cluster_id=cluster_id,
        populations=populations,
        representatives=representatives,
        adaptive_traces=adaptive_traces,
        population_paths=population_paths,
        representative_paths=representative_paths,
        ann_ranks=ann_ranks,
        fts_ranks=fts_ranks,
        built_population_keys=built_population_keys,
        uncovered_purposes=sorted(set(uncovered_purposes)),
    )
    _write_immutable_json(
        checkpoint_path,
        {
            "schema_version": DAILY_CLUSTER_CHECKPOINT_SCHEMA,
            "checkpoint_id": checkpoint_id,
            "identity": checkpoint_identity,
            "cluster_id": result.cluster_id,
            "population_manifests": [
                reference.model_dump(mode="json") for reference in result.populations
            ],
            "representative_set_manifests": [
                reference.model_dump(mode="json")
                for reference in result.representatives
            ],
            "adaptive_retrieval_traces": [
                reference.model_dump(mode="json")
                for reference in result.adaptive_traces
            ],
            "ann_ranks": result.ann_ranks,
            "fts_ranks": result.fts_ranks,
            "built_population_keys": result.built_population_keys,
            "uncovered_purposes": result.uncovered_purposes,
        },
    )
    validated = _load_daily_cluster_checkpoint(
        root,
        checkpoint_path=checkpoint_path,
        expected_identity=checkpoint_identity,
        expected_checkpoint_id=checkpoint_id,
    )
    if validated is None:
        raise ValueError("daily cluster checkpoint was not persisted")
    return validated


def _load_daily_cluster_checkpoint(
    root: Path,
    *,
    checkpoint_path: Path,
    expected_identity: dict[str, Any],
    expected_checkpoint_id: str,
) -> _DailyClusterBuild | None:
    if not checkpoint_path.is_file():
        return None
    try:
        payload = read_json(checkpoint_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"daily cluster checkpoint is invalid: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != DAILY_CLUSTER_CHECKPOINT_SCHEMA
        or payload.get("checkpoint_id") != expected_checkpoint_id
        or payload.get("identity") != expected_identity
    ):
        raise ValueError("daily cluster checkpoint identity differs from the active request")
    allow_distribution_shortfall = expected_identity.get(
        "allow_distribution_shortfall"
    )
    if not isinstance(allow_distribution_shortfall, bool) or (
        expected_identity.get("semantic_policy_root")
        != _daily_cluster_semantic_policy_root(
            allow_distribution_shortfall=allow_distribution_shortfall,
        )
    ):
        raise ValueError("daily cluster checkpoint semantic policy is stale")
    expected_query_sha256 = str(expected_identity.get("query_sha256") or "")
    expected_query_embedding_sha256 = str(
        expected_identity.get("query_embedding_sha256") or ""
    )
    if any(
        len(value) != 64
        for value in (
            str(expected_identity.get("input_cluster_root") or ""),
            expected_query_sha256,
            expected_query_embedding_sha256,
            str(expected_identity.get("expanded_query_embedding_sha256") or ""),
            str(expected_identity.get("semantic_policy_root") or ""),
        )
    ):
        raise ValueError("daily cluster checkpoint hash identity is invalid")
    for identity_key in (
        "category_brain_manifest",
        "category_brain_index_manifest",
    ):
        try:
            identity_reference = ArtifactReference.model_validate(
                expected_identity[identity_key]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "daily cluster checkpoint category identity is invalid"
            ) from exc
        _verify_checkpoint_reference(root, identity_reference)
    embedding_runtime_identity = expected_identity.get(
        "embedding_runtime_identity"
    )
    if (
        not isinstance(embedding_runtime_identity, dict)
        or not isinstance(
            embedding_runtime_identity.get("embedding_method"),
            str,
        )
        or embedding_runtime_identity.get("embedding_method")
        != embedding_runtime_identity.get("snapshot_embedding_model")
        or embedding_runtime_identity.get("runtime_embedding_dimensions")
        != embedding_runtime_identity.get("snapshot_embedding_dimensions")
        or not isinstance(
            embedding_runtime_identity.get("snapshot_embedding_provider"),
            str,
        )
    ):
        raise ValueError("daily cluster checkpoint embedding identity is invalid")
    try:
        populations = [
            ArtifactReference.model_validate(item)
            for item in cast(list[object], payload["population_manifests"])
        ]
        representatives = [
            ArtifactReference.model_validate(item)
            for item in cast(list[object], payload["representative_set_manifests"])
        ]
        adaptive_traces = [
            ArtifactReference.model_validate(item)
            for item in cast(list[object], payload["adaptive_retrieval_traces"])
        ]
        cluster_id = str(payload["cluster_id"])
        raw_ann_ranks = cast(dict[object, object], payload["ann_ranks"])
        raw_fts_ranks = cast(dict[object, object], payload["fts_ranks"])
        if any(
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, int)
            for ranks in (raw_ann_ranks, raw_fts_ranks)
            for key, value in ranks.items()
        ):
            raise ValueError("daily cluster checkpoint channel ranks are invalid")
        ann_ranks = cast(dict[str, int], raw_ann_ranks)
        fts_ranks = cast(dict[str, int], raw_fts_ranks)
        built_population_keys = [
            str(value)
            for value in cast(list[object], payload["built_population_keys"])
        ]
        uncovered_purposes = [
            cast(PopulationPurpose, str(value))
            for value in cast(list[object], payload["uncovered_purposes"])
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"daily cluster checkpoint payload is invalid: {exc}") from exc
    if cluster_id != expected_identity["cluster_id"]:
        raise ValueError("daily cluster checkpoint cluster identity mismatch")
    if any(value < 1 for value in [*ann_ranks.values(), *fts_ranks.values()]):
        raise ValueError("daily cluster checkpoint channel ranks are invalid")
    if any(value not in DAILY_POPULATION_PURPOSE_UNITS for value in uncovered_purposes):
        raise ValueError("daily cluster checkpoint population purpose is invalid")
    expected_run_id = str(expected_identity["run_id"])
    expected_cutoff = parse_datetime(str(expected_identity["cutoff_at"]))
    expected_snapshot_id = str(expected_identity["memory_snapshot_id"])
    expected_source_generation_sha256 = str(
        expected_identity["source_generation_sha256"]
    )
    expected_corpus_manifest_sha256 = str(
        expected_identity["corpus_manifest_sha256"]
    )
    allowed_selection_versions = {REPRESENTATIVE_SELECTION_VERSION}
    allowed_adaptive_token_counts = {REPRESENTATIVE_MAX_TOKEN_COUNT}
    if allow_distribution_shortfall:
        allowed_selection_versions.add(
            REPRESENTATIVE_QUALITY_FULL_SELECTION_VERSION
        )
        allowed_adaptive_token_counts.add(
            REPRESENTATIVE_QUALITY_FULL_MAX_TOKEN_COUNT
        )
    population_cache: dict[str, PopulationManifest] = {}
    representative_cache: dict[str, RepresentativeSetManifest] = {}

    def load_population(reference: ArtifactReference) -> PopulationManifest:
        reference_key = canonical_json(reference.model_dump(mode="json"))
        cached_manifest = population_cache.get(reference_key)
        if cached_manifest is not None:
            return cached_manifest
        if reference.item_count != 1:
            raise ValueError(
                "daily cluster checkpoint population reference count is invalid"
            )
        manifest_path = _verify_checkpoint_reference(root, reference)
        manifest = PopulationManifest.model_validate(read_json(manifest_path))
        _verify_cluster_manifest_identity(
            run_id=manifest.run_id,
            cluster_id=manifest.cluster_id,
            cutoff_at=manifest.cutoff_at,
            memory_snapshot_id=manifest.memory_snapshot_id,
            source_generation_sha256=manifest.source_generation_sha256,
            corpus_manifest_sha256=manifest.corpus_manifest_sha256,
            expected_run_id=expected_run_id,
            expected_cluster_id=cluster_id,
            expected_cutoff=expected_cutoff,
            expected_snapshot_id=expected_snapshot_id,
            expected_source_generation_sha256=(
                expected_source_generation_sha256
            ),
            expected_corpus_manifest_sha256=expected_corpus_manifest_sha256,
        )
        if (
            manifest.statistics_version != POPULATION_STATISTICS_VERSION
            or manifest.cube_version != POPULATION_CUBE_VERSION
            or manifest.selection_budget_version
            != POPULATION_SELECTION_BUDGET_VERSION
            or manifest.max_selected_record_count
            != POPULATION_MAX_SELECTED_RECORDS
            or manifest.max_cube_row_count != POPULATION_MAX_CUBE_ROWS
            or manifest.purpose_classifier_version
            != POPULATION_PURPOSE_CLASSIFIER_VERSION
            or manifest.purpose_record_types_sha256
            != _purpose_record_types_sha256(manifest.population_purpose)
            or manifest.bootstrap_version != BLOCK_BOOTSTRAP_VERSION
        ):
            raise ValueError("daily cluster checkpoint population policy is stale")
        for nested in (
            manifest.member_records,
            manifest.independent_units,
            manifest.cube_rows,
        ):
            _verify_checkpoint_reference(root, nested)
        population_cache[reference_key] = manifest
        return manifest

    def load_representative(
        reference: ArtifactReference,
    ) -> RepresentativeSetManifest:
        reference_key = canonical_json(reference.model_dump(mode="json"))
        cached_manifest = representative_cache.get(reference_key)
        if cached_manifest is not None:
            return cached_manifest
        if reference.item_count != 1:
            raise ValueError(
                "daily cluster checkpoint representative reference count is invalid"
            )
        manifest_path = _verify_checkpoint_reference(root, reference)
        manifest = RepresentativeSetManifest.model_validate(
            read_json(manifest_path)
        )
        expected_representative_max_token_count = (
            REPRESENTATIVE_QUALITY_FULL_MAX_TOKEN_COUNT
            if manifest.selection_version
            == REPRESENTATIVE_QUALITY_FULL_SELECTION_VERSION
            else REPRESENTATIVE_MAX_TOKEN_COUNT
        )
        _verify_cluster_manifest_identity(
            run_id=manifest.run_id,
            cluster_id=manifest.cluster_id,
            cutoff_at=manifest.cutoff_at,
            memory_snapshot_id=manifest.memory_snapshot_id,
            source_generation_sha256=manifest.source_generation_sha256,
            corpus_manifest_sha256=manifest.corpus_manifest_sha256,
            expected_run_id=expected_run_id,
            expected_cluster_id=cluster_id,
            expected_cutoff=expected_cutoff,
            expected_snapshot_id=expected_snapshot_id,
            expected_source_generation_sha256=(
                expected_source_generation_sha256
            ),
            expected_corpus_manifest_sha256=expected_corpus_manifest_sha256,
        )
        if (
            manifest.query_sha256 != expected_query_sha256
            or manifest.query_embedding_sha256
            != expected_query_embedding_sha256
            or manifest.embedding_model
            != embedding_runtime_identity["embedding_method"]
            or manifest.selection_version not in allowed_selection_versions
            or manifest.max_selected_record_count
            != REPRESENTATIVE_MAX_SELECTED_RECORDS
            or manifest.max_candidate_pool_count
            != REPRESENTATIVE_MAX_CANDIDATE_POOL
            or manifest.max_token_count
            != expected_representative_max_token_count
            or manifest.max_trade_date_concentration
            != REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION
            or manifest.max_unit_key_concentration
            != REPRESENTATIVE_MAX_UNIT_KEY_CONCENTRATION
        ):
            raise ValueError(
                "daily cluster checkpoint representative policy or query is stale"
            )
        _verify_checkpoint_reference(root, manifest.representative_records)
        representative_cache[reference_key] = manifest
        return manifest

    def validate_pair(
        population_reference: ArtifactReference,
        representative_reference: ArtifactReference,
    ) -> tuple[PopulationManifest, RepresentativeSetManifest]:
        population_manifest = load_population(population_reference)
        representative_manifest = load_representative(
            representative_reference
        )
        if (
            representative_manifest.population_id
            != population_manifest.population_id
            or representative_manifest.population_manifest_sha256
            != population_reference.sha256
        ):
            raise ValueError(
                "daily cluster checkpoint population representative chain is invalid"
            )
        return population_manifest, representative_manifest

    final_population_keys: list[str] = []
    if len(populations) != len(representatives):
        raise ValueError("daily cluster checkpoint artifact counts conflict")
    for population_reference, representative_reference in zip(
        populations,
        representatives,
        strict=True,
    ):
        population_manifest, _representative_manifest = validate_pair(
            population_reference,
            representative_reference,
        )
        final_population_keys.append(
            _population_key(
                cluster_id,
                population_manifest.population_purpose,
                population_manifest.independent_unit_type,
            )
        )
    final_population_references = {
        canonical_json(reference.model_dump(mode="json"))
        for reference in populations
    }
    final_representative_references = {
        canonical_json(reference.model_dump(mode="json"))
        for reference in representatives
    }
    observed_final_population_references: set[str] = set()
    observed_final_representative_references: set[str] = set()
    for reference in adaptive_traces:
        if reference.item_count != 1:
            raise ValueError(
                "daily cluster checkpoint adaptive reference count is invalid"
            )
        trace_path = _verify_checkpoint_reference(root, reference)
        trace = AdaptiveRetrievalTrace.model_validate(read_json(trace_path))
        _verify_cluster_manifest_identity(
            run_id=trace.run_id,
            cluster_id=trace.cluster_id,
            cutoff_at=trace.cutoff_at,
            memory_snapshot_id=None,
            source_generation_sha256=None,
            corpus_manifest_sha256=None,
            expected_run_id=expected_run_id,
            expected_cluster_id=cluster_id,
            expected_cutoff=expected_cutoff,
            expected_snapshot_id=expected_snapshot_id,
            expected_source_generation_sha256=(
                expected_source_generation_sha256
            ),
            expected_corpus_manifest_sha256=expected_corpus_manifest_sha256,
        )
        if (
            trace.query_sha256 != expected_query_sha256
            or trace.query_embedding_sha256
            != expected_query_embedding_sha256
            or trace.policy_version != ADAPTIVE_RETRIEVAL_POLICY_VERSION
            or trace.max_depth != ADAPTIVE_MAX_DEPTH
            or trace.max_cell_count != ADAPTIVE_MAX_CELL_COUNT
            or trace.max_record_count != ADAPTIVE_MAX_RECORD_COUNT
            or trace.max_token_count not in allowed_adaptive_token_counts
            or trace.min_information_gain != ADAPTIVE_MIN_INFORMATION_GAIN
        ):
            raise ValueError(
                "daily cluster checkpoint adaptive policy or query is stale"
            )
        adaptive_inspection = _inspect_built_adaptive(
            root,
            trace_path=trace_path,
            expected_trace=trace,
        )
        if adaptive_inspection["passed"] is not True:
            raise ValueError(
                "daily cluster checkpoint adaptive artifact closure is invalid"
            )
        _initial_population, initial_representative = validate_pair(
            trace.initial_population_manifest,
            trace.initial_representative_set_manifest,
        )
        if trace.max_token_count != initial_representative.max_token_count:
            raise ValueError(
                "daily cluster checkpoint adaptive representative budget is stale"
            )
        for iteration in trace.iterations:
            validate_pair(
                iteration.population_manifest,
                iteration.representative_set_manifest,
            )
        validate_pair(
            trace.final_population_manifest,
            trace.final_representative_set_manifest,
        )
        observed_final_population_references.add(
            canonical_json(trace.final_population_manifest.model_dump(mode="json"))
        )
        observed_final_representative_references.add(
            canonical_json(
                trace.final_representative_set_manifest.model_dump(mode="json")
            )
        )
    if (
        len(populations) != len(adaptive_traces)
        or len(final_population_references) != len(populations)
        or len(final_representative_references) != len(representatives)
        or observed_final_population_references != final_population_references
        or observed_final_representative_references
        != final_representative_references
    ):
        raise ValueError("daily cluster checkpoint adaptive closure is invalid")
    if (
        len(set(built_population_keys)) != len(built_population_keys)
        or sorted(built_population_keys) != sorted(final_population_keys)
    ):
        raise ValueError("daily cluster checkpoint population keys are invalid")
    observed_purposes = {
        manifest.population_purpose for manifest in population_cache.values()
    }
    expected_uncovered_purposes = sorted(
        set(DAILY_POPULATION_PURPOSE_UNITS) - observed_purposes
    )
    if (
        uncovered_purposes != sorted(set(uncovered_purposes))
        or uncovered_purposes != expected_uncovered_purposes
    ):
        raise ValueError(
            "daily cluster checkpoint uncovered purposes are invalid"
        )
    return _DailyClusterBuild(
        cluster_id=cluster_id,
        populations=populations,
        representatives=representatives,
        adaptive_traces=adaptive_traces,
        population_paths=[root / reference.artifact_path for reference in populations],
        representative_paths=[
            root / reference.artifact_path for reference in representatives
        ],
        ann_ranks=ann_ranks,
        fts_ranks=fts_ranks,
        built_population_keys=built_population_keys,
        uncovered_purposes=uncovered_purposes,
    )


def _daily_runtime_retrieval_identity(
    *,
    run_id: str,
    cluster_id: str,
    query: str,
    cutoff_at: datetime,
    memory_snapshot_id: str,
    population_manifests: list[ArtifactReference],
    representative_manifests: list[ArtifactReference],
    ann_ranks: dict[str, int],
    fts_ranks: dict[str, int],
    exposure_manifest_root: str,
) -> dict[str, Any]:
    return {
        "schema_version": "nslab.daily_runtime_retrieval_identity.v1",
        "checkpoint_version": DAILY_RUNTIME_RETRIEVAL_CHECKPOINT_VERSION,
        "runtime_policy_version": RUNTIME_RETRIEVAL_POLICY_VERSION,
        "run_id": run_id,
        "cluster_id": cluster_id,
        "query_sha256": sha256_text(query),
        "cutoff_at": as_kst(cutoff_at).isoformat(),
        "memory_snapshot_id": memory_snapshot_id,
        "population_manifests": [
            item.model_dump(mode="json") for item in population_manifests
        ],
        "representative_manifests": [
            item.model_dump(mode="json") for item in representative_manifests
        ],
        "ann_ranks_root_sha256": sha256_text(canonical_json(ann_ranks)),
        "fts_ranks_root_sha256": sha256_text(canonical_json(fts_ranks)),
        "exposure_manifest_root_sha256": exposure_manifest_root,
    }


def _daily_runtime_retrieval_checkpoint_paths(
    root: Path,
    *,
    run_id: str,
    cluster_id: str,
    identity: dict[str, Any],
) -> tuple[Path, Path, Path]:
    if any(
        not value
        or Path(value).name != value
        or value in {".", ".."}
        for value in (run_id, cluster_id)
    ):
        raise ValueError("daily runtime retrieval checkpoint segment is invalid")
    directory = (
        root / RUNTIME_RETRIEVAL_ROOT / run_id / cluster_id
    ).resolve()
    try:
        directory.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "daily runtime retrieval checkpoint escapes project root"
        ) from exc
    checkpoint_id = "DRTC-" + sha256_text(canonical_json(identity))[:20].upper()
    return (
        directory,
        directory / f"{checkpoint_id}.json",
        directory / "runtime_retrieval_checkpoint_adoption_closed.json",
    )


def _validated_daily_runtime_trace(
    root: Path,
    *,
    trace_path: Path,
    identity: dict[str, Any],
    query: str,
) -> RuntimeRetrievalBuildResult | None:
    try:
        trace_bytes = trace_path.read_bytes()
        trace = RuntimeRetrievalTrace.model_validate(
            json.loads(trace_bytes.decode("utf-8"))
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"daily runtime retrieval trace is invalid: {exc}") from exc
    if trace_bytes != canonical_json(trace.model_dump(mode="json")).encode("utf-8"):
        raise ValueError("daily runtime retrieval trace is not canonical")
    population_manifests = [
        ArtifactReference.model_validate(item)
        for item in cast(list[object], identity["population_manifests"])
    ]
    representative_manifests = [
        ArtifactReference.model_validate(item)
        for item in cast(list[object], identity["representative_manifests"])
    ]
    if (
        trace.run_id != identity["run_id"]
        or trace.cluster_id != identity["cluster_id"]
        or trace.query_text != query
        or trace.query_sha256 != identity["query_sha256"]
        or as_kst(trace.cutoff_at).isoformat() != identity["cutoff_at"]
        or trace.memory_snapshot_id != identity["memory_snapshot_id"]
        or trace.policy_version != identity["runtime_policy_version"]
        or trace.source_population_manifests != population_manifests
        or trace.source_representative_manifests != representative_manifests
        or trace.evidence_memo_artifact is not None
    ):
        return None
    record_ids = [row.record_id for row in trace.rows]
    trace_identity = {
        "run_id": trace.run_id,
        "cluster_id": trace.cluster_id,
        "query_sha256": trace.query_sha256,
        "cutoff_at": as_kst(trace.cutoff_at).isoformat(),
        "memory_snapshot_id": trace.memory_snapshot_id,
        "policy_version": trace.policy_version,
        "record_ids": record_ids,
    }
    expected_trace_id = "RTRV4-" + sha256_text(
        canonical_json(trace_identity)
    )[:20].upper()
    forbidden_stages = {"LLM_EXPOSED", "MEMO_REFERENCED", "FINAL_CITED"}
    if (
        trace.trace_id != expected_trace_id
        or trace_path.name != f"{trace.trace_id}.json"
        or record_ids != sorted(record_ids)
        or any(forbidden_stages.intersection(row.stages) for row in trace.rows)
    ):
        raise ValueError("daily runtime retrieval base trace closure is invalid")
    for reference in [*population_manifests, *representative_manifests]:
        _verify_checkpoint_reference(root, reference)
    return RuntimeRetrievalBuildResult(
        trace=trace,
        trace_path=trace_path,
        selected_record_ids=tuple(
            row.record_id for row in trace.rows if "LANE_SELECTED" in row.stages
        ),
    )


def _load_daily_runtime_retrieval_checkpoint(
    root: Path,
    *,
    identity: dict[str, Any],
    query: str,
) -> RuntimeRetrievalBuildResult | None:
    directory, checkpoint_path, adoption_marker_path = (
        _daily_runtime_retrieval_checkpoint_paths(
            root,
            run_id=str(identity["run_id"]),
            cluster_id=str(identity["cluster_id"]),
            identity=identity,
        )
    )
    if checkpoint_path.is_file():
        payload = read_json(checkpoint_path)
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version")
            != DAILY_RUNTIME_RETRIEVAL_CHECKPOINT_SCHEMA
            or payload.get("identity") != identity
        ):
            raise ValueError("daily runtime retrieval checkpoint identity is invalid")
        trace_reference = ArtifactReference.model_validate(
            payload.get("trace_artifact")
        )
        trace_path = _verify_checkpoint_reference(root, trace_reference)
        result = _validated_daily_runtime_trace(
            root,
            trace_path=trace_path,
            identity=identity,
            query=query,
        )
        if result is None:
            raise ValueError("daily runtime retrieval checkpoint trace is stale")
        return result
    if adoption_marker_path.exists() or not directory.is_dir():
        return None
    base_trace_paths = sorted(
        path
        for path in directory.glob("RTRV4-*.json")
        if ".evidence." not in path.name and ".final." not in path.name
    )
    matching = [
        result
        for path in base_trace_paths
        if (
            result := _validated_daily_runtime_trace(
                root,
                trace_path=path,
                identity=identity,
                query=query,
            )
        )
        is not None
    ]
    if len(matching) > 1:
        raise ValueError("multiple daily runtime retrieval traces match one request")
    if not matching:
        return None
    _write_daily_runtime_retrieval_checkpoint(
        root,
        identity=identity,
        retrieval=matching[0],
        adopted_existing_trace=True,
    )
    return _load_daily_runtime_retrieval_checkpoint(
        root,
        identity=identity,
        query=query,
    )


def _write_daily_runtime_retrieval_checkpoint(
    root: Path,
    *,
    identity: dict[str, Any],
    retrieval: RuntimeRetrievalBuildResult,
    adopted_existing_trace: bool,
) -> None:
    _directory, checkpoint_path, adoption_marker_path = (
        _daily_runtime_retrieval_checkpoint_paths(
            root,
            run_id=str(identity["run_id"]),
            cluster_id=str(identity["cluster_id"]),
            identity=identity,
        )
    )
    _write_immutable_json(
        checkpoint_path,
        {
            "schema_version": DAILY_RUNTIME_RETRIEVAL_CHECKPOINT_SCHEMA,
            "identity": identity,
            "trace_artifact": _artifact_reference(
                root,
                retrieval.trace_path,
            ).model_dump(mode="json"),
            "adopted_existing_trace": adopted_existing_trace,
        },
    )
    _write_immutable_json(
        adoption_marker_path,
        {
            "schema_version": (
                "nslab.daily_runtime_retrieval_adoption_marker.v1"
            ),
            "adoption_closed": True,
        },
    )


def _verify_checkpoint_reference(root: Path, reference: ArtifactReference) -> Path:
    path = (root / reference.artifact_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("daily cluster checkpoint artifact escapes project root") from exc
    if not path.is_file() or file_sha256(path) != reference.sha256:
        raise ValueError("daily cluster checkpoint artifact closure is invalid")
    return path


def _verify_cluster_manifest_identity(
    *,
    run_id: str,
    cluster_id: str,
    cutoff_at: datetime,
    memory_snapshot_id: str | None,
    source_generation_sha256: str | None,
    corpus_manifest_sha256: str | None,
    expected_run_id: str,
    expected_cluster_id: str,
    expected_cutoff: datetime,
    expected_snapshot_id: str,
    expected_source_generation_sha256: str,
    expected_corpus_manifest_sha256: str,
) -> None:
    if (
        run_id != expected_run_id
        or cluster_id != expected_cluster_id
        or as_kst(cutoff_at) != as_kst(expected_cutoff)
        or (
            memory_snapshot_id is not None
            and memory_snapshot_id != expected_snapshot_id
        )
        or (
            source_generation_sha256 is not None
            and source_generation_sha256
            != expected_source_generation_sha256
        )
        or (
            corpus_manifest_sha256 is not None
            and corpus_manifest_sha256 != expected_corpus_manifest_sha256
        )
    ):
        raise ValueError("daily cluster checkpoint manifest identity mismatch")


def inspect_daily_memory_context(
    root: Path,
    path: Path,
    *,
    memory_index: ProductionMemoryIndex | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    resolved = path.resolve()
    errors: list[str] = []
    try:
        context = DailyMemoryContext.model_validate(read_json(resolved))
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"daily_memory_context_invalid:{exc}"]}
    index_path = root / context.category_brain_index_manifest.artifact_path
    if memory_index is None:
        errors.append("daily_memory_context_memory_index_required")
    expected_path = (
        root
        / DAILY_MEMORY_CONTEXT_ROOT
        / context.run_id
        / _daily_memory_context_stage_filename(context)
    ).resolve()
    if resolved != expected_path:
        errors.append("daily_memory_context_path_mismatch")
    references = [
        context.news_coverage_manifest,
        context.event_cluster_manifest,
        context.event_clusters,
        context.memory_coverage_manifest,
        context.category_brain_manifest,
        context.category_brain_index_manifest,
        context.category_selected_claims,
        *context.population_manifests,
        *context.representative_set_manifests,
        *context.adaptive_retrieval_traces,
        *context.runtime_retrieval_traces,
        *context.runtime_evidence_traces,
        *context.runtime_evidence_memos,
        *(
            [context.runtime_evidence_pack_manifest]
            if context.runtime_evidence_pack_manifest is not None
            else []
        ),
        context.beneficiary_graph,
        *([context.final_beneficiary_graph] if context.final_beneficiary_graph is not None else []),
        context.compact_final_context,
    ]
    for reference in references:
        artifact_path = (root / reference.artifact_path).resolve()
        try:
            artifact_path.relative_to(root)
        except ValueError:
            errors.append("daily_memory_context_artifact_path_escape")
            continue
        if not artifact_path.exists():
            errors.append("daily_memory_context_artifact_missing")
        elif file_sha256(artifact_path) != reference.sha256:
            errors.append("daily_memory_context_artifact_hash_mismatch")
    runtime_trace_clusters: list[str] = []
    runtime_traces: list[RuntimeRetrievalTrace] = []
    for reference in context.runtime_retrieval_traces:
        try:
            runtime_trace = RuntimeRetrievalTrace.model_validate(read_json(root / reference.artifact_path))
        except (OSError, ValueError) as exc:
            errors.append(f"daily_memory_runtime_trace_invalid:{exc}")
            continue
        if (
            runtime_trace.run_id != context.run_id
            or as_kst(runtime_trace.cutoff_at) != as_kst(context.cutoff_at)
            or runtime_trace.memory_snapshot_id != context.memory_snapshot_id
        ):
            errors.append("daily_memory_runtime_trace_identity_mismatch")
        runtime_trace_clusters.append(runtime_trace.cluster_id)
        runtime_traces.append(runtime_trace)
    if runtime_trace_clusters != context.runtime_retrieval_cluster_ids:
        errors.append("daily_memory_runtime_trace_cluster_coverage_mismatch")
    if context.runtime_evidence_pack_manifest is not None:
        try:
            pack_path = (
                root
                / context.runtime_evidence_pack_manifest.artifact_path
            ).resolve()
            pack_path.relative_to(root)
            pack_bytes = pack_path.read_bytes()
            if sha256_bytes(pack_bytes) != (
                context.runtime_evidence_pack_manifest.sha256
            ):
                raise ValueError("runtime evidence pack manifest hash mismatch")
            pack_manifest = RuntimeEvidencePackManifest.model_validate(
                json.loads(pack_bytes)
            )
            selected_occurrence_count = sum(
                sum("LANE_SELECTED" in row.stages for row in trace.rows)
                for trace in runtime_traces
            )
            selected_record_ids = {
                row.record_id
                for trace in runtime_traces
                for row in trace.rows
                if "LANE_SELECTED" in row.stages
            }
            if (
                pack_manifest.run_id != context.run_id
                or as_kst(pack_manifest.cutoff_at) != as_kst(context.cutoff_at)
                or pack_manifest.memory_snapshot_id
                != context.memory_snapshot_id
                or pack_manifest.cluster_ids
                != sorted(context.runtime_retrieval_cluster_ids)
                or pack_manifest.assignment_count
                != selected_occurrence_count
                or pack_manifest.unique_record_count
                != len(selected_record_ids)
                or context.runtime_evidence_assignment_count
                != pack_manifest.assignment_count
                or context.runtime_evidence_unique_record_count
                != pack_manifest.unique_record_count
                or context.runtime_evidence_packed_call_count
                != len(pack_manifest.packs)
                or context.runtime_evidence_avoided_payload_occurrence_count
                != pack_manifest.avoided_payload_occurrence_count
            ):
                errors.append("daily_memory_runtime_evidence_pack_mismatch")
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            errors.append(f"daily_memory_runtime_evidence_pack_invalid:{exc}")
    graph_inspection = inspect_beneficiary_graph(
        root,
        root / context.beneficiary_graph.artifact_path,
    )
    if graph_inspection.get("passed") is not True:
        errors.extend(f"daily_memory_{error}" for error in graph_inspection.get("errors", []) if isinstance(error, str))
    cluster_query_by_id: dict[str, str] = {}
    news: NewsCoverageManifest | None = None
    event: EventClusterManifest | None = None
    coverage: MemoryCoverageManifest | None = None
    try:
        news = NewsCoverageManifest.model_validate(read_json(root / context.news_coverage_manifest.artifact_path))
        event = EventClusterManifest.model_validate(read_json(root / context.event_cluster_manifest.artifact_path))
        coverage = MemoryCoverageManifest.model_validate(
            read_json(root / context.memory_coverage_manifest.artifact_path)
        )
    except (OSError, ValueError) as exc:
        errors.append(f"daily_memory_context_source_manifest_invalid:{exc}")
    else:
        for identity_name, identity_observed, identity_expected in (
            ("news_run", news.run_id, context.run_id),
            ("news_trade_date", news.trade_date, context.trade_date),
            ("news_cutoff", as_kst(news.cutoff_at), as_kst(context.cutoff_at)),
            ("event_run", event.run_id, context.run_id),
            ("event_trade_date", event.trade_date, context.trade_date),
            ("event_cutoff", as_kst(event.cutoff_at), as_kst(context.cutoff_at)),
            ("coverage_run", coverage.run_id, context.run_id),
            ("coverage_cutoff", as_kst(coverage.cutoff_at), as_kst(context.cutoff_at)),
            (
                "coverage_corpus",
                coverage.corpus_manifest_sha256,
                context.corpus_manifest_sha256,
            ),
        ):
            if identity_observed != identity_expected:
                errors.append(f"daily_memory_context_{identity_name}_mismatch")
        if not coverage.coverage_complete:
            errors.append("daily_memory_context_memory_coverage_incomplete")
        material_ids = [item.cluster_id for item in event.clusters if item.disposition == "MATERIAL_FULL_RETRIEVAL"]
        if material_ids != context.material_event_cluster_ids:
            errors.append("daily_memory_context_material_clusters_mismatch")
        try:
            cluster_rows = _read_jsonl(root / context.event_clusters.artifact_path)
            cluster_queries = _material_cluster_queries(
                root / context.event_cluster_manifest.artifact_path,
                root / context.event_clusters.artifact_path,
            )
        except (OSError, ValueError) as exc:
            errors.append(f"daily_memory_event_cluster_rows_invalid:{exc}")
        else:
            cluster_query_by_id = dict(cluster_queries)
            if len(cluster_rows) != context.event_clusters.item_count or {
                str(row.get("cluster_id")) for row in cluster_rows
            } != {item.cluster_id for item in event.clusters}:
                errors.append("daily_memory_event_cluster_row_coverage_mismatch")
            if [cluster_id for cluster_id, _query in cluster_queries] != material_ids:
                errors.append("daily_memory_event_cluster_rows_mismatch")
    populations: dict[str, PopulationManifest] = {}
    population_keys: Counter[tuple[str, str, str]] = Counter()
    population_by_reference: dict[str, PopulationManifest] = {}
    population_references: set[str] = set()
    for reference in context.population_manifests:
        try:
            population = PopulationManifest.model_validate(read_json(root / reference.artifact_path))
        except (OSError, ValueError) as exc:
            errors.append(f"daily_memory_population_invalid:{exc}")
            continue
        if population.population_id in populations:
            errors.append("daily_memory_population_duplicate")
        populations[population.population_id] = population
        reference_key = canonical_json(reference.model_dump(mode="json"))
        population_references.add(reference_key)
        population_by_reference[reference_key] = population
        errors.extend(_daily_artifact_identity_errors(context, population, "population"))
        population_keys.update(
            [
                (
                    population.cluster_id,
                    population.population_purpose,
                    population.independent_unit_type,
                )
            ]
        )
        if memory_index is not None:
            population_inspection = PopulationRetriever(
                root,
                memory_index=memory_index,
            ).inspect(root / reference.artifact_path)
            if population_inspection.get("passed") is not True:
                errors.extend(
                    f"daily_memory_population_{error}"
                    for error in population_inspection.get("errors", [])
                    if isinstance(error, str)
                )
    representatives: dict[str, RepresentativeSetManifest] = {}
    representative_keys: Counter[tuple[str, str, str]] = Counter()
    representative_references: set[str] = set()
    for reference in context.representative_set_manifests:
        try:
            representative = RepresentativeSetManifest.model_validate(read_json(root / reference.artifact_path))
        except (OSError, ValueError) as exc:
            errors.append(f"daily_memory_representative_invalid:{exc}")
            continue
        if representative.representative_set_id in representatives:
            errors.append("daily_memory_representative_duplicate")
        representatives[representative.representative_set_id] = representative
        representative_references.add(canonical_json(reference.model_dump(mode="json")))
        errors.extend(_daily_artifact_identity_errors(context, representative, "representative"))
        linked_population = populations.get(representative.population_id)
        if linked_population is None or representative.population_id != linked_population.population_id:
            errors.append("daily_memory_representative_population_mismatch")
        elif linked_population is not None:
            representative_keys.update(
                [
                    (
                        linked_population.cluster_id,
                        linked_population.population_purpose,
                        linked_population.independent_unit_type,
                    )
                ]
            )
        if memory_index is not None:
            representative_inspection = RepresentativeSelector(
                root,
                memory_index=memory_index,
            ).inspect(root / reference.artifact_path)
            if representative_inspection.get("passed") is not True:
                errors.extend(
                    f"daily_memory_representative_{error}"
                    for error in representative_inspection.get("errors", [])
                    if isinstance(error, str)
                )
    traces: dict[str, AdaptiveRetrievalTrace] = {}
    trace_keys: Counter[tuple[str, str, str]] = Counter()
    for reference in context.adaptive_retrieval_traces:
        try:
            trace = AdaptiveRetrievalTrace.model_validate(read_json(root / reference.artifact_path))
        except (OSError, ValueError) as exc:
            errors.append(f"daily_memory_adaptive_trace_invalid:{exc}")
            continue
        if trace.trace_id in traces:
            errors.append("daily_memory_adaptive_trace_duplicate")
        traces[trace.trace_id] = trace
        if trace.run_id != context.run_id or as_kst(trace.cutoff_at) != as_kst(context.cutoff_at):
            errors.append("daily_memory_adaptive_trace_identity_mismatch")
        if canonical_json(trace.final_population_manifest.model_dump(mode="json")) not in population_references:
            errors.append("daily_memory_adaptive_final_population_mismatch")
        if (
            canonical_json(trace.final_representative_set_manifest.model_dump(mode="json"))
            not in representative_references
        ):
            errors.append("daily_memory_adaptive_final_representative_mismatch")
        final_population = population_by_reference.get(
            canonical_json(trace.final_population_manifest.model_dump(mode="json"))
        )
        if final_population is None:
            final_population_path = root / trace.final_population_manifest.artifact_path
            try:
                final_population = PopulationManifest.model_validate(read_json(final_population_path))
            except (OSError, ValueError):
                errors.append("daily_memory_adaptive_final_population_invalid")
        if final_population is not None:
            trace_keys.update(
                [
                    (
                        final_population.cluster_id,
                        final_population.population_purpose,
                        final_population.independent_unit_type,
                    )
                ]
            )
            try:
                expected_trigger = (
                    beneficiary_trigger_evidence(
                        root,
                        root / context.beneficiary_graph.artifact_path,
                        cluster_id=trace.cluster_id,
                    )
                    if final_population.population_purpose == "catalyst_response"
                    else None
                )
            except ValueError:
                errors.append("daily_memory_adaptive_trigger_source_invalid")
            else:
                expected_trigger_rows = [expected_trigger] if expected_trigger is not None else []
                if trace.trigger_evidence != expected_trigger_rows:
                    errors.append("daily_memory_adaptive_trigger_evidence_mismatch")
        if memory_index is not None:
            adaptive_inspection = AdaptiveRetriever(
                root,
                memory_index=memory_index,
            ).inspect(root / reference.artifact_path)
            if adaptive_inspection.get("passed") is not True:
                errors.extend(
                    f"daily_memory_adaptive_{error}"
                    for error in adaptive_inspection.get("errors", [])
                    if isinstance(error, str)
                )
    if population_keys != representative_keys or population_keys != trace_keys:
        errors.append("daily_memory_population_chain_multiplicity_mismatch")
    if any(count != 1 for count in population_keys.values()):
        errors.append("daily_memory_population_key_duplicate")
    observed_population_keys = sorted(
        _population_key(cluster_id, purpose, unit_type) for cluster_id, purpose, unit_type in population_keys
    )
    if observed_population_keys != context.built_population_keys:
        errors.append("daily_memory_built_population_keys_mismatch")
    attempted_purposes = set(DAILY_POPULATION_PURPOSE_UNITS)
    expected_uncovered_purposes: dict[str, list[PopulationPurpose]] = {}
    for cluster_id in context.material_event_cluster_ids:
        observed_purposes = {
            purpose for observed_cluster, purpose, _unit_type in population_keys if observed_cluster == cluster_id
        }
        expected_uncovered_purposes[cluster_id] = sorted(attempted_purposes - observed_purposes)
    if expected_uncovered_purposes != context.uncovered_population_purposes:
        errors.append("daily_memory_uncovered_population_purposes_mismatch")
    expected_uncovered_clusters = sorted(
        cluster_id for cluster_id, purposes in expected_uncovered_purposes.items() if "catalyst_response" in purposes
    )
    if expected_uncovered_clusters != context.uncovered_material_event_cluster_ids:
        errors.append("daily_memory_uncovered_material_clusters_mismatch")
    try:
        chain_populations = [
            PopulationManifest.model_validate(read_json(root / reference.artifact_path))
            for reference in context.population_manifests
        ]
        chain_representative_sources = []
        for reference in context.representative_set_manifests:
            representative_manifest = RepresentativeSetManifest.model_validate(
                read_json(root / reference.artifact_path)
            )
            chain_representative_sources.append(
                (
                    representative_manifest,
                    [
                        RepresentativeRecord.model_validate(row)
                        for row in _read_jsonl(root / representative_manifest.representative_records.artifact_path)
                    ],
                )
            )
        chain_traces = [
            AdaptiveRetrievalTrace.model_validate(read_json(root / reference.artifact_path))
            for reference in context.adaptive_retrieval_traces
        ]
        chain_graph = BeneficiaryGraphArtifact.model_validate(read_json(root / context.beneficiary_graph.artifact_path))
    except (OSError, ValueError) as exc:
        errors.append(f"daily_memory_artifact_chain_invalid:{exc}")
    else:
        errors.extend(
            daily_memory_artifact_chain_errors(
                context,
                populations=chain_populations,
                representative_sources=chain_representative_sources,
                traces=chain_traces,
                graph=chain_graph,
            )
        )
    try:
        brain = BrainManifest.model_validate(read_json(root / context.category_brain_manifest.artifact_path))
        proof_claims = [
            CompiledBrainClaim.model_validate(row)
            for row in _read_jsonl(root / context.category_selected_claims.artifact_path)
        ]
    except (OSError, ValueError) as exc:
        errors.append(f"daily_memory_category_brain_invalid:{exc}")
        proof_claims = []
    else:
        expected_brain_root = root / "brain" / "snapshots" / brain.brain_version
        if (root / context.category_brain_manifest.artifact_path).resolve() != (
            expected_brain_root / "brain_manifest.json"
        ).resolve():
            errors.append("daily_memory_category_brain_path_mismatch")
        if brain.build_mode != "llm-full" or not brain.production_eligible or not brain.coverage_complete:
            errors.append("daily_memory_category_brain_not_production")
        if (
            brain.production_memory_snapshot_id != context.memory_snapshot_id
            or brain.production_memory_corpus_sha256 != context.corpus_manifest_sha256
            or brain.production_memory_source_generation_sha256 != context.source_generation_sha256
        ):
            errors.append("daily_memory_category_brain_memory_snapshot_mismatch")
        index_inspection = inspect_category_brain_index(root, index_path)
        if index_inspection.get("passed") is not True:
            errors.extend(
                f"daily_memory_{error}" for error in index_inspection.get("errors", []) if isinstance(error, str)
            )
        index_manifest_payload = index_inspection.get("manifest")
        index_claim_ids = {str(value) for value in index_inspection.get("claim_ids", [])}
        if (
            not isinstance(index_manifest_payload, dict)
            or brain.category_brain_index_manifest_artifact != context.category_brain_index_manifest.artifact_path
            or brain.category_brain_index_manifest_sha256 != context.category_brain_index_manifest.sha256
            or brain.compiled_claim_count != len(index_claim_ids)
            or set(brain.compiled_claim_ids) != index_claim_ids
            or brain.compiled_claims_sha256 != (index_manifest_payload.get("claims_artifact") or {}).get("sha256")
            or brain.brain_record_cutoff_at is None
            or parse_datetime(str(index_manifest_payload.get("brain_record_cutoff_at")))
            != as_kst(brain.brain_record_cutoff_at)
            or as_kst(brain.brain_record_cutoff_at) > as_kst(context.cutoff_at)
            or index_manifest_payload.get("brain_version") != brain.brain_version
        ):
            errors.append("daily_memory_category_brain_index_mismatch")
        if isinstance(index_manifest_payload, dict):
            try:
                category_index_manifest = CategoryBrainIndexManifest.model_validate(index_manifest_payload)
            except ValueError as exc:
                errors.append(f"daily_memory_category_brain_index_invalid:{exc}")
            else:
                if news is not None and event is not None and coverage is not None:
                    errors.extend(
                        daily_memory_source_chain_errors(
                            context,
                            news=news,
                            event=event,
                            coverage=coverage,
                            brain=brain,
                            category_index=category_index_manifest,
                        )
                    )
        proof_by_id = {claim.claim_id: claim for claim in proof_claims}
        if (
            len(proof_claims) != context.category_selected_claims.item_count
            or len(proof_by_id) != len(proof_claims)
            or not set(proof_by_id).issubset(index_claim_ids)
        ):
            errors.append("daily_memory_category_selected_claims_mismatch")
        if {item.cluster_id for item in context.category_query_plans} != set(context.material_event_cluster_ids):
            errors.append("daily_memory_category_query_plan_coverage_mismatch")
        inspection_category_index = CategoryBrainIndex(
            root,
            index_path,
            embedding_provider=(memory_index.embedding_provider if memory_index is not None else None),
        )
        try:
            indexed_claims = {
                claim.claim_id: claim for claim in inspection_category_index.claims_by_ids(set(proof_by_id))
            }
            indexed_proofs = inspection_category_index.claim_proofs_by_ids(set(proof_by_id))
        except (OSError, ValueError) as exc:
            errors.append(f"daily_memory_category_selected_claim_proof_invalid:{exc}")
            indexed_claims = {}
            indexed_proofs = {}
        if indexed_claims != proof_by_id or indexed_proofs != context.category_selected_claim_proofs:
            errors.append("daily_memory_category_selected_claim_payload_mismatch")
        for plan in context.category_query_plans:
            if (
                plan.original_query != cluster_query_by_id.get(plan.cluster_id)
                or plan.source_artifact_path != context.category_brain_index_manifest.artifact_path
                or plan.source_artifact_sha256 != context.category_brain_index_manifest.sha256
                or not set(plan.selected_claim_ids).issubset(proof_by_id)
            ):
                errors.append("daily_memory_category_query_plan_source_mismatch")
                continue
            if memory_index is not None:
                expected_plan = inspection_category_index.query(
                    cluster_id=plan.cluster_id,
                    query=plan.original_query,
                )
                if plan != expected_plan:
                    errors.append("daily_memory_category_query_plan_recomputed_mismatch")
        inspection_category_index.close()
    try:
        representative_rows = _representative_rows(
            root,
            context.representative_set_manifests,
        )
        population_summaries = _population_summaries(
            root,
            context.population_manifests,
        )
        graph = BeneficiaryGraphArtifact.model_validate(read_json(root / context.beneficiary_graph.artifact_path))
        compact_graph = (
            BeneficiaryGraphArtifact.model_validate(read_json(root / context.final_beneficiary_graph.artifact_path))
            if context.final_beneficiary_graph is not None
            else graph
        )
    except (OSError, ValueError) as exc:
        errors.append(f"daily_memory_compact_source_invalid:{exc}")
    else:
        selected_record_ids = {
            str(row["record_id"]) for row in representative_rows if isinstance(row.get("record_id"), str)
        }
        try:
            expected_guidance = _category_guidance(
                category_guidance_claims(
                    root,
                    index_path,
                    selected_record_ids=selected_record_ids,
                    limit=DAILY_CATEGORY_GUIDANCE_MAX_COUNT,
                ),
                path=root / context.category_selected_claims.artifact_path,
                root=root,
                selected_record_ids=selected_record_ids,
                cutoff_at=context.cutoff_at,
            )
        except (OSError, ValueError) as exc:
            errors.append(f"daily_memory_category_guidance_recompute_failed:{exc}")
            expected_guidance = []
        if expected_guidance != context.category_guidance:
            errors.append("daily_memory_category_guidance_mismatch")
        supporting, contradicting, unexplained = daily_memory_record_roles(representative_rows)
        disagreements = daily_memory_disagreements(population_summaries)
        if context.unresolved_disagreements != disagreements:
            errors.append("daily_memory_disagreements_mismatch")
        expected_compact = compact_daily_memory_payload(
            run_id=context.run_id,
            trade_date=context.trade_date,
            cutoff_at=context.cutoff_at,
            memory_snapshot_id=context.memory_snapshot_id,
            material_event_cluster_ids=context.material_event_cluster_ids,
            uncovered_material_event_cluster_ids=(context.uncovered_material_event_cluster_ids),
            built_population_keys=context.built_population_keys,
            uncovered_population_purposes=context.uncovered_population_purposes,
            population_summaries=population_summaries,
            representative_records=representative_rows,
            category_query_plans=context.category_query_plans,
            category_guidance=expected_guidance,
            graph=graph,
            disagreements=disagreements,
            supporting_record_ids=supporting,
            contradicting_record_ids=contradicting,
            unexplained_record_ids=unexplained,
        )
        if context.runtime_evidence_traces:
            try:
                runtime_traces = [
                    RuntimeRetrievalTrace.model_validate(read_json(root / reference.artifact_path))
                    for reference in context.runtime_evidence_traces
                ]
                runtime_memos = [
                    RuntimeEvidenceMemo.model_validate(row)
                    for reference in context.runtime_evidence_memos
                    for row in _read_jsonl(root / reference.artifact_path)
                ]
                supporting, contradicting, unexplained = runtime_evidence_record_roles(
                    supporting_record_ids=supporting,
                    contradicting_record_ids=contradicting,
                    unexplained_record_ids=unexplained,
                    traces=runtime_traces,
                )
                expected_compact = runtime_evidence_compact_payload(
                    expected_compact,
                    traces=runtime_traces,
                    memos=runtime_memos,
                    supporting_record_ids=supporting,
                    contradicting_record_ids=contradicting,
                    unexplained_record_ids=unexplained,
                )
            except (OSError, ValueError) as exc:
                errors.append(f"daily_memory_runtime_evidence_invalid:{exc}")
        if context.final_beneficiary_graph is not None:
            expected_compact = final_beneficiary_compact_payload(
                expected_compact,
                graph=compact_graph,
            )
        for role_name, role_observed, role_expected in (
            ("supporting", context.supporting_record_ids, supporting),
            ("contradicting", context.contradicting_record_ids, contradicting),
            ("unexplained", context.unexplained_record_ids, unexplained),
        ):
            if role_observed != role_expected:
                errors.append(f"daily_memory_{role_name}_mismatch")
        compact_path = root / context.compact_final_context.artifact_path
        expected_bytes = canonical_json(expected_compact).encode("utf-8")
        if not compact_path.exists() or compact_path.read_bytes() != expected_bytes:
            errors.append("daily_memory_compact_context_recomputed_mismatch")
        if context.estimated_token_count != len(expected_bytes):
            errors.append("daily_memory_context_token_count_mismatch")
    if not context.context_complete:
        errors.append("daily_memory_context_incomplete")
    return {
        "passed": not errors,
        "errors": sorted(set(errors)),
        "context": context.model_dump(mode="json"),
    }


def attach_runtime_evidence_to_daily_context(
    root: Path,
    *,
    context_path: Path,
    evidence_results: list[RuntimeEvidenceBuildResult],
    pack_manifest: RuntimeEvidencePackManifest | None = None,
    pack_manifest_path: Path | None = None,
) -> tuple[DailyMemoryContext, Path]:
    """Attach LLM evidence memos before final synthesis binds the context hash."""

    root = root.resolve()
    resolved_context_path = context_path.resolve()
    context = DailyMemoryContext.model_validate(read_json(resolved_context_path))
    output_dir = root / DAILY_MEMORY_CONTEXT_ROOT / context.run_id
    runtime_context_path = (
        output_dir / DAILY_MEMORY_CONTEXT_RUNTIME_EVIDENCE_FILENAME
    )
    result_by_cluster = {result.trace.cluster_id: result for result in evidence_results}
    if set(result_by_cluster) != set(context.runtime_retrieval_cluster_ids):
        raise ValueError("runtime evidence does not cover every memory-enabled cluster")
    if len(result_by_cluster) != len(evidence_results):
        raise ValueError("runtime evidence clusters must be unique")
    source_trace_by_cluster = {
        RuntimeRetrievalTrace.model_validate(read_json(root / reference.artifact_path)).cluster_id: reference
        for reference in context.runtime_retrieval_traces
    }
    if set(source_trace_by_cluster) != set(result_by_cluster):
        raise ValueError("runtime evidence source trace coverage mismatch")
    evidence_trace_refs = [
        _artifact_reference(root, result_by_cluster[cluster_id].trace_path)
        for cluster_id in context.runtime_retrieval_cluster_ids
    ]
    memo_refs = [
        _artifact_reference(
            root,
            result_by_cluster[cluster_id].memo_path,
            item_count=len(result_by_cluster[cluster_id].memos),
        )
        for cluster_id in context.runtime_retrieval_cluster_ids
    ]
    if (pack_manifest is None) != (pack_manifest_path is None):
        raise ValueError("runtime evidence pack manifest and path must be paired")
    pack_reference: ArtifactReference | None = None
    if pack_manifest is not None and pack_manifest_path is not None:
        selected_occurrence_count = sum(
            sum("LANE_SELECTED" in row.stages for row in trace.rows)
            for trace in (
                result_by_cluster[cluster_id].trace
                for cluster_id in context.runtime_retrieval_cluster_ids
            )
        )
        selected_record_ids = {
            row.record_id
            for result in result_by_cluster.values()
            for row in result.trace.rows
            if "LANE_SELECTED" in row.stages
        }
        if (
            pack_manifest.run_id != context.run_id
            or pack_manifest.cutoff_at != context.cutoff_at
            or pack_manifest.memory_snapshot_id != context.memory_snapshot_id
            or pack_manifest.cluster_ids
            != sorted(context.runtime_retrieval_cluster_ids)
            or pack_manifest.assignment_count != selected_occurrence_count
            or pack_manifest.unique_record_count != len(selected_record_ids)
        ):
            raise ValueError("runtime evidence pack manifest closure mismatch")
        parsed_pack = RuntimeEvidencePackManifest.model_validate(
            read_json(pack_manifest_path)
        )
        if parsed_pack != pack_manifest:
            raise ValueError("runtime evidence pack manifest bytes drifted")
        pack_reference = _artifact_reference(root, pack_manifest_path)
    if context.runtime_evidence_traces:
        if (
            resolved_context_path == runtime_context_path.resolve()
            and context.runtime_evidence_traces == evidence_trace_refs
            and context.runtime_evidence_memos == memo_refs
            and context.runtime_evidence_pack_manifest == pack_reference
        ):
            return context, resolved_context_path
        raise ValueError("runtime evidence is already attached with different inputs")
    traces = [result_by_cluster[cluster_id].trace for cluster_id in context.runtime_retrieval_cluster_ids]
    memos = [
        memo for cluster_id in context.runtime_retrieval_cluster_ids for memo in result_by_cluster[cluster_id].memos
    ]
    source_compact_path = root / context.compact_final_context.artifact_path
    compact = read_json(source_compact_path)
    if not isinstance(compact, dict):
        raise ValueError("daily memory compact context is invalid")
    supporting, contradicting, unexplained = runtime_evidence_record_roles(
        supporting_record_ids=context.supporting_record_ids,
        contradicting_record_ids=context.contradicting_record_ids,
        unexplained_record_ids=context.unexplained_record_ids,
        traces=traces,
    )
    compact = runtime_evidence_compact_payload(
        compact,
        traces=traces,
        memos=memos,
        supporting_record_ids=supporting,
        contradicting_record_ids=contradicting,
        unexplained_record_ids=unexplained,
    )
    compact_bytes = canonical_json(compact).encode("utf-8")
    if len(compact_bytes) > DAILY_MEMORY_CONTEXT_MAX_BYTES:
        raise ValueError(
            "runtime evidence compact context exceeds byte budget: "
            f"{len(compact_bytes)} > {DAILY_MEMORY_CONTEXT_MAX_BYTES}"
        )
    compact_path = output_dir / DAILY_MEMORY_COMPACT_RUNTIME_EVIDENCE_FILENAME
    _write_immutable_bytes(compact_path, compact_bytes)
    updated = context.model_copy(
        update={
            "runtime_evidence_traces": evidence_trace_refs,
            "runtime_evidence_memos": memo_refs,
            "runtime_evidence_pack_manifest": pack_reference,
            "runtime_evidence_assignment_count": (
                pack_manifest.assignment_count if pack_manifest is not None else 0
            ),
            "runtime_evidence_unique_record_count": (
                pack_manifest.unique_record_count if pack_manifest is not None else 0
            ),
            "runtime_evidence_packed_call_count": (
                len(pack_manifest.packs) if pack_manifest is not None else 0
            ),
            "runtime_evidence_avoided_payload_occurrence_count": (
                pack_manifest.avoided_payload_occurrence_count
                if pack_manifest is not None
                else 0
            ),
            "compact_final_context": _artifact_reference(root, compact_path),
            "supporting_record_ids": supporting,
            "contradicting_record_ids": contradicting,
            "unexplained_record_ids": unexplained,
            "estimated_token_count": len(compact_bytes),
        }
    )
    _write_immutable_json(
        runtime_context_path,
        updated.model_dump(mode="json"),
    )
    return updated, runtime_context_path


def bind_final_beneficiary_graph_to_daily_context(
    root: Path,
    *,
    context_path: Path,
    beneficiary_graph_path: Path,
) -> tuple[DailyMemoryContext, Path]:
    """Bind the post-candidate graph without rewriting retrieval-time trace inputs."""

    root = root.resolve()
    resolved_context_path = context_path.resolve()
    context = DailyMemoryContext.model_validate(read_json(resolved_context_path))
    output_dir = root / DAILY_MEMORY_CONTEXT_ROOT / context.run_id
    final_context_path = output_dir / DAILY_MEMORY_CONTEXT_FINAL_FILENAME
    graph = BeneficiaryGraphArtifact.model_validate(read_json(beneficiary_graph_path))
    graph_reference = _artifact_reference(root, beneficiary_graph_path)
    if context.final_beneficiary_graph is not None:
        if (
            resolved_context_path == final_context_path.resolve()
            and context.final_beneficiary_graph == graph_reference
        ):
            return context, resolved_context_path
        raise ValueError("final beneficiary graph is already bound with different inputs")
    if graph.run_id != context.run_id or as_kst(graph.cutoff_at) != as_kst(context.cutoff_at):
        raise ValueError("final beneficiary graph differs from the daily context")
    source_compact_path = root / context.compact_final_context.artifact_path
    compact = read_json(source_compact_path)
    if not isinstance(compact, dict):
        raise ValueError("daily memory compact context is invalid")
    compact = final_beneficiary_compact_payload(compact, graph=graph)
    compact_bytes = canonical_json(compact).encode("utf-8")
    if len(compact_bytes) > DAILY_MEMORY_CONTEXT_MAX_BYTES:
        raise ValueError("final daily memory compact context exceeds byte budget")
    compact_path = output_dir / DAILY_MEMORY_COMPACT_FINAL_FILENAME
    _write_immutable_bytes(compact_path, compact_bytes)
    updated = context.model_copy(
        update={
            "final_beneficiary_graph": graph_reference,
            "compact_final_context": _artifact_reference(root, compact_path),
            "estimated_token_count": len(compact_bytes),
        }
    )
    _write_immutable_json(
        final_context_path,
        updated.model_dump(mode="json"),
    )
    return updated, final_context_path


def final_beneficiary_compact_payload(
    compact: dict[str, Any],
    *,
    graph: BeneficiaryGraphArtifact,
) -> dict[str, Any]:
    payload = json.loads(canonical_json(compact))
    is_v2 = payload.get("schema_version") == DAILY_MEMORY_COMPACT_V2_SCHEMA
    graph_rows = _compact_beneficiary_graph_paths(graph)
    if is_v2:
        graph_rows = sorted(graph_rows, key=canonical_json)
    unresolved_candidate_ids = (
        sorted(graph.unresolved_candidate_ids)
        if is_v2
        else graph.unresolved_candidate_ids
    )
    payload["beneficiary_graph"] = {
        "path_count": graph.path_count,
        "paths": [],
        "unresolved_candidate_ids": [] if is_v2 else unresolved_candidate_ids,
    }
    omitted = payload.get("omitted_counts")
    if not isinstance(omitted, dict):
        raise ValueError("daily memory compact omission counts are invalid")
    omitted["beneficiary_graph_paths"] = len(graph_rows)
    graph_payload = payload["beneficiary_graph"]
    if not isinstance(graph_payload, dict):
        raise ValueError("daily compact beneficiary graph payload is invalid")
    if is_v2:
        commitments = payload.get("coverage_commitments")
        if not isinstance(commitments, dict):
            raise ValueError("daily compact v2 graph commitments are invalid")
        commitments["beneficiary_graph_paths"] = _compact_coverage_commitment(
            graph_rows
        )
        commitments["beneficiary_graph_unresolved_candidate_ids"] = (
            _compact_coverage_commitment(unresolved_candidate_ids)
        )
        omitted["beneficiary_graph_unresolved_candidate_ids"] = len(
            unresolved_candidate_ids
        )
    _extend_compact_round_robin(
        payload,
        field="paths",
        rows=graph_rows,
        cluster_field="event_cluster_ids",
        container=graph_payload,
    )
    omitted["beneficiary_graph_paths"] = len(graph_rows) - len(
        graph_payload["paths"]
    )
    return cast(dict[str, Any], payload)


def runtime_evidence_record_roles(
    *,
    supporting_record_ids: list[str],
    contradicting_record_ids: list[str],
    unexplained_record_ids: list[str],
    traces: list[RuntimeRetrievalTrace],
) -> tuple[list[str], list[str], list[str]]:
    """Merge runtime-selected records into disjoint final provenance roles."""

    supporting = set(supporting_record_ids)
    contradicting = set(contradicting_record_ids)
    unexplained = set(unexplained_record_ids)
    positive_lanes = {
        "POSITIVE_ANALOG",
        "THEME_FORMATION_SUCCESS",
        "CONTINUATION_SUCCESS",
    }
    negative_lanes = {
        "NEGATIVE_CONTROL",
        "NEAR_MISS",
        "COUNTEREXAMPLE",
        "CANDIDATE_GENERATION_ERROR",
        "CANDIDATE_RANKING_ERROR",
        "THEME_FORMATION_FAILURE",
        "CONTINUATION_FAILURE",
    }
    for trace in traces:
        for row in trace.rows:
            if "LANE_SELECTED" not in row.stages:
                continue
            record_id = row.record_id
            if record_id in supporting | contradicting | unexplained:
                continue
            if row.lane in positive_lanes:
                supporting.add(record_id)
            elif row.lane in negative_lanes:
                contradicting.add(record_id)
            else:
                unexplained.add(record_id)
    return sorted(supporting), sorted(contradicting), sorted(unexplained)


def runtime_evidence_compact_payload(
    compact: dict[str, Any],
    *,
    traces: list[RuntimeRetrievalTrace],
    memos: list[RuntimeEvidenceMemo],
    supporting_record_ids: list[str] | None = None,
    contradicting_record_ids: list[str] | None = None,
    unexplained_record_ids: list[str] | None = None,
) -> dict[str, Any]:
    payload = json.loads(canonical_json(compact))
    if payload.get("schema_version") == DAILY_MEMORY_COMPACT_V2_SCHEMA:
        return _runtime_evidence_compact_payload_v2(
            payload,
            traces=traces,
            memos=memos,
            supporting_record_ids=supporting_record_ids,
            contradicting_record_ids=contradicting_record_ids,
            unexplained_record_ids=unexplained_record_ids,
        )
    _replace_compact_record_roles(
        payload,
        supporting_record_ids=supporting_record_ids,
        contradicting_record_ids=contradicting_record_ids,
        unexplained_record_ids=unexplained_record_ids,
    )
    payload["runtime_retrieval_version"] = "adaptive_population_drilldown.v4"
    payload["runtime_evidence_map_reduce_version"] = "runtime_evidence_map_reduce.v1"
    payload["runtime_retrieval"] = [
        {
            "trace_id": trace.trace_id,
            "cluster_id": trace.cluster_id,
            "memory_snapshot_id": trace.memory_snapshot_id,
            "selected_record_count": sum("LANE_SELECTED" in row.stages for row in trace.rows),
            "lane_selected_counts": trace.lane_selected_counts,
            "offline_unexposed_recovered_count": (trace.offline_unexposed_recovered_count),
            "rare_mechanism_recovered_count": trace.rare_mechanism_recovered_count,
            "online_full_scan_count": trace.online_full_scan_count,
        }
        for trace in traces
    ]
    payload["runtime_evidence_memos"] = [
        {
            "memo_id": memo.memo_id,
            "cluster_id": memo.cluster_id,
            "lane": memo.lane,
            "source_record_ids": memo.source_record_ids,
            "source_record_hash_root": memo.source_record_hash_root,
            "current_vs_history_similarities": [
                _excerpt_text(value, limit=180) for value in memo.current_vs_history_similarities[:2]
            ],
            "current_vs_history_differences": [
                _excerpt_text(value, limit=180) for value in memo.current_vs_history_differences[:2]
            ],
            "supporting_conditions": [_excerpt_text(value, limit=160) for value in memo.supporting_conditions[:2]],
            "failure_conditions": [_excerpt_text(value, limit=160) for value in memo.failure_conditions[:2]],
            "unresolved_conflicts": [_excerpt_text(value, limit=160) for value in memo.unresolved_conflicts[:2]],
        }
        for memo in memos
    ]
    omitted = payload.get("omitted_counts")
    if isinstance(omitted, dict):
        omitted["runtime_evidence_memos"] = 0
    if len(canonical_json(payload).encode("utf-8")) > DAILY_MEMORY_CONTEXT_MAX_BYTES:
        representatives = payload.get("representative_records")
        if isinstance(representatives, list):
            if isinstance(omitted, dict):
                omitted["representative_records"] = int(omitted.get("representative_records") or 0) + len(
                    representatives
                )
            payload["representative_records"] = []
    if len(canonical_json(payload).encode("utf-8")) > DAILY_MEMORY_CONTEXT_MAX_BYTES:
        guidance = payload.get("category_brain_guidance")
        if isinstance(guidance, list):
            if isinstance(omitted, dict):
                omitted["category_brain_guidance"] = int(omitted.get("category_brain_guidance") or 0) + len(guidance)
            payload["category_brain_guidance"] = []
    if len(canonical_json(payload).encode("utf-8")) > DAILY_MEMORY_CONTEXT_MAX_BYTES:
        payload["runtime_evidence_memos"] = [
            {
                "memo_id": memo.memo_id,
                "cluster_id": memo.cluster_id,
                "lane": memo.lane,
                "source_record_ids": memo.source_record_ids,
                "source_record_hash_root": memo.source_record_hash_root,
            }
            for memo in memos
        ]
        if isinstance(omitted, dict):
            omitted["runtime_evidence_memo_text"] = len(memos)
    return cast(dict[str, Any], payload)


def _runtime_evidence_compact_payload_v2(
    payload: dict[str, Any],
    *,
    traces: list[RuntimeRetrievalTrace],
    memos: list[RuntimeEvidenceMemo],
    supporting_record_ids: list[str] | None,
    contradicting_record_ids: list[str] | None,
    unexplained_record_ids: list[str] | None,
) -> dict[str, Any]:
    trace_rows = sorted(
        [
            {
                "trace_id": trace.trace_id,
                "cluster_id": trace.cluster_id,
                "memory_snapshot_id": trace.memory_snapshot_id,
                "selected_record_count": sum(
                    "LANE_SELECTED" in row.stages for row in trace.rows
                ),
                "lane_selected_counts": trace.lane_selected_counts,
                "offline_unexposed_recovered_count": (
                    trace.offline_unexposed_recovered_count
                ),
                "rare_mechanism_recovered_count": (
                    trace.rare_mechanism_recovered_count
                ),
                "online_full_scan_count": trace.online_full_scan_count,
            }
            for trace in traces
        ],
        key=canonical_json,
    )
    memo_rows = sorted(
        [
            {
                "memo_id": memo.memo_id,
                "cluster_id": memo.cluster_id,
                "lane": memo.lane,
                "source_record_ids": memo.source_record_ids[:8],
                "source_record_id_count": len(memo.source_record_ids),
                "source_record_id_omitted_count": max(
                    0, len(memo.source_record_ids) - 8
                ),
                "source_record_hash_root": memo.source_record_hash_root,
                "current_vs_history_similarities": [
                    _excerpt_text(value, limit=180)
                    for value in memo.current_vs_history_similarities[:2]
                ],
                "current_vs_history_differences": [
                    _excerpt_text(value, limit=180)
                    for value in memo.current_vs_history_differences[:2]
                ],
                "supporting_conditions": [
                    _excerpt_text(value, limit=160)
                    for value in memo.supporting_conditions[:2]
                ],
                "failure_conditions": [
                    _excerpt_text(value, limit=160)
                    for value in memo.failure_conditions[:2]
                ],
                "unresolved_conflicts": [
                    _excerpt_text(value, limit=160)
                    for value in memo.unresolved_conflicts[:2]
                ],
            }
            for memo in memos
        ],
        key=canonical_json,
    )
    trace_commitment_rows = sorted(
        [
            {
                "trace_id": trace.trace_id,
                "sha256": sha256_text(
                    canonical_json(trace.model_dump(mode="json"))
                ),
            }
            for trace in traces
        ],
        key=canonical_json,
    )
    memo_commitment_rows = sorted(
        [
            {
                "memo_id": memo.memo_id,
                "sha256": sha256_text(
                    canonical_json(memo.model_dump(mode="json"))
                ),
            }
            for memo in memos
        ],
        key=canonical_json,
    )
    commitments = payload.get("coverage_commitments")
    omitted = payload.get("omitted_counts")
    if not isinstance(commitments, dict) or not isinstance(omitted, dict):
        raise ValueError("daily compact v2 coverage accounting is invalid")
    commitments["runtime_retrieval"] = _compact_coverage_commitment(
        trace_commitment_rows
    )
    commitments["runtime_evidence_memos"] = _compact_coverage_commitment(
        memo_commitment_rows
    )
    payload["runtime_retrieval_version"] = "adaptive_population_drilldown.v4"
    payload["runtime_evidence_map_reduce_version"] = (
        "runtime_evidence_map_reduce.v1"
    )
    payload["runtime_retrieval"] = []
    payload["runtime_evidence_memos"] = []
    omitted["runtime_retrieval"] = len(trace_rows)
    omitted["runtime_evidence_memos"] = len(memo_rows)
    _replace_compact_record_roles(
        payload,
        supporting_record_ids=supporting_record_ids,
        contradicting_record_ids=contradicting_record_ids,
        unexplained_record_ids=unexplained_record_ids,
    )
    if len(canonical_json(payload).encode("utf-8")) > DAILY_MEMORY_CONTEXT_MAX_BYTES:
        raise ValueError("runtime evidence commitments exceed compact byte budget")
    _extend_compact_surfaces(
        payload,
        surfaces=[
            (
                cast(list[Any], payload["runtime_retrieval"]),
                _compact_round_robin_order(trace_rows, "cluster_id"),
            ),
            (
                cast(list[Any], payload["runtime_evidence_memos"]),
                _compact_round_robin_order(memo_rows, "cluster_id"),
            ),
        ],
        max_bytes=DAILY_MEMORY_CONTEXT_MAX_BYTES,
    )
    omitted["runtime_retrieval"] = len(trace_rows) - len(
        payload["runtime_retrieval"]
    )
    omitted["runtime_evidence_memos"] = len(memo_rows) - len(
        payload["runtime_evidence_memos"]
    )
    return payload


def _replace_compact_record_roles(
    payload: dict[str, Any],
    *,
    supporting_record_ids: list[str] | None,
    contradicting_record_ids: list[str] | None,
    unexplained_record_ids: list[str] | None,
) -> None:
    role_values = {
        "supporting_record_ids": supporting_record_ids,
        "contradicting_record_ids": contradicting_record_ids,
        "unexplained_record_ids": unexplained_record_ids,
    }
    if all(value is None for value in role_values.values()):
        return
    if any(value is None for value in role_values.values()):
        raise ValueError("daily compact record roles must be updated together")
    normalized = {
        name: sorted(cast(list[str], value))
        for name, value in role_values.items()
    }
    if payload.get("schema_version") != DAILY_MEMORY_COMPACT_V2_SCHEMA:
        payload.update(normalized)
        return
    commitments = payload.get("coverage_commitments")
    omitted = payload.get("omitted_counts")
    if not isinstance(commitments, dict) or not isinstance(omitted, dict):
        raise ValueError("daily compact v2 record role accounting is invalid")
    for name, values in normalized.items():
        payload[name] = []
        commitments[name] = _compact_coverage_commitment(values)
        omitted[name] = len(values)


def _daily_artifact_identity_errors(
    context: DailyMemoryContext,
    artifact: PopulationManifest | RepresentativeSetManifest,
    label: str,
) -> list[str]:
    errors = []
    for name, observed, expected in (
        ("run", artifact.run_id, context.run_id),
        ("cutoff", as_kst(artifact.cutoff_at), as_kst(context.cutoff_at)),
        ("snapshot", artifact.memory_snapshot_id, context.memory_snapshot_id),
        (
            "source_generation",
            artifact.source_generation_sha256,
            context.source_generation_sha256,
        ),
        ("corpus", artifact.corpus_manifest_sha256, context.corpus_manifest_sha256),
    ):
        if observed != expected:
            errors.append(f"daily_memory_{label}_{name}_mismatch")
    return errors


def daily_memory_artifact_chain_errors(
    context: DailyMemoryContext,
    *,
    populations: list[PopulationManifest],
    representative_sources: list[tuple[RepresentativeSetManifest, list[RepresentativeRecord]]],
    traces: list[AdaptiveRetrievalTrace],
    graph: BeneficiaryGraphArtifact,
) -> list[str]:
    errors: list[str] = []
    if (
        len(populations) != len(context.population_manifests)
        or len(representative_sources) != len(context.representative_set_manifests)
        or len(traces) != len(context.adaptive_retrieval_traces)
    ):
        return ["daily_memory_population_chain_source_count_mismatch"]
    population_by_id: dict[str, PopulationManifest] = {}
    population_by_reference: dict[str, PopulationManifest] = {}
    population_keys: Counter[tuple[str, str, str]] = Counter()
    for reference, population in zip(
        context.population_manifests,
        populations,
        strict=True,
    ):
        if population.population_id in population_by_id:
            errors.append("daily_memory_population_duplicate")
        population_by_id[population.population_id] = population
        population_by_reference[canonical_json(reference.model_dump(mode="json"))] = population
        errors.extend(_daily_artifact_identity_errors(context, population, "population"))
        population_keys.update(
            [
                (
                    population.cluster_id,
                    population.population_purpose,
                    population.independent_unit_type,
                )
            ]
        )

    representative_references = {
        canonical_json(reference.model_dump(mode="json")) for reference in context.representative_set_manifests
    }
    representative_keys: Counter[tuple[str, str, str]] = Counter()
    representative_ids: set[str] = set()
    for representative, records in representative_sources:
        if representative.representative_set_id in representative_ids:
            errors.append("daily_memory_representative_duplicate")
        representative_ids.add(representative.representative_set_id)
        errors.extend(_daily_artifact_identity_errors(context, representative, "representative"))
        linked_population = population_by_id.get(representative.population_id)
        if linked_population is None:
            errors.append("daily_memory_representative_population_mismatch")
            continue
        linked_reference = next(
            (
                reference
                for reference, population in zip(
                    context.population_manifests,
                    populations,
                    strict=True,
                )
                if population.population_id == representative.population_id
            ),
            None,
        )
        if (
            linked_reference is None
            or representative.population_manifest_sha256 != linked_reference.sha256
            or representative.cluster_id != linked_population.cluster_id
            or representative.population_record_count != linked_population.raw_record_count
            or representative.population_unit_count != linked_population.independent_unit_count
            or representative.selected_record_ids != [record.record_id for record in records]
            or representative.selected_independent_unit_ids != [record.independent_unit_id for record in records]
            or representative.representative_records.item_count != len(records)
        ):
            errors.append("daily_memory_representative_records_mismatch")
        representative_keys.update(
            [
                (
                    linked_population.cluster_id,
                    linked_population.population_purpose,
                    linked_population.independent_unit_type,
                )
            ]
        )

    trace_keys: Counter[tuple[str, str, str]] = Counter()
    trace_ids: set[str] = set()
    population_reference_keys = set(population_by_reference)
    for trace in traces:
        if trace.trace_id in trace_ids:
            errors.append("daily_memory_adaptive_trace_duplicate")
        trace_ids.add(trace.trace_id)
        if trace.run_id != context.run_id or as_kst(trace.cutoff_at) != as_kst(context.cutoff_at):
            errors.append("daily_memory_adaptive_trace_identity_mismatch")
        final_population_key = canonical_json(trace.final_population_manifest.model_dump(mode="json"))
        final_representative_key = canonical_json(trace.final_representative_set_manifest.model_dump(mode="json"))
        if final_population_key not in population_reference_keys:
            errors.append("daily_memory_adaptive_final_population_mismatch")
            continue
        if final_representative_key not in representative_references:
            errors.append("daily_memory_adaptive_final_representative_mismatch")
        final_population = population_by_reference[final_population_key]
        if trace.cluster_id != final_population.cluster_id:
            errors.append("daily_memory_adaptive_trace_cluster_mismatch")
        trace_keys.update(
            [
                (
                    final_population.cluster_id,
                    final_population.population_purpose,
                    final_population.independent_unit_type,
                )
            ]
        )
        expected_trigger = (
            beneficiary_trigger_evidence_from_artifact(
                graph,
                source_artifact=context.beneficiary_graph,
                cluster_id=trace.cluster_id,
            )
            if final_population.population_purpose == "catalyst_response"
            else None
        )
        if trace.trigger_evidence != ([expected_trigger] if expected_trigger is not None else []):
            errors.append("daily_memory_adaptive_trigger_evidence_mismatch")

    if population_keys != representative_keys or population_keys != trace_keys:
        errors.append("daily_memory_population_chain_multiplicity_mismatch")
    if any(count != 1 for count in population_keys.values()):
        errors.append("daily_memory_population_key_duplicate")
    observed_population_keys = sorted(
        _population_key(cluster_id, purpose, unit_type) for cluster_id, purpose, unit_type in population_keys
    )
    if observed_population_keys != context.built_population_keys:
        errors.append("daily_memory_built_population_keys_mismatch")
    attempted_purposes = set(DAILY_POPULATION_PURPOSE_UNITS)
    expected_uncovered_purposes: dict[str, list[PopulationPurpose]] = {}
    for cluster_id in context.material_event_cluster_ids:
        observed_purposes = {
            purpose for observed_cluster, purpose, _unit_type in population_keys if observed_cluster == cluster_id
        }
        expected_uncovered_purposes[cluster_id] = sorted(attempted_purposes - observed_purposes)
    if expected_uncovered_purposes != context.uncovered_population_purposes:
        errors.append("daily_memory_uncovered_population_purposes_mismatch")
    expected_uncovered_clusters = sorted(
        cluster_id for cluster_id, purposes in expected_uncovered_purposes.items() if "catalyst_response" in purposes
    )
    if expected_uncovered_clusters != context.uncovered_material_event_cluster_ids:
        errors.append("daily_memory_uncovered_material_clusters_mismatch")
    return sorted(set(errors))


def daily_memory_source_chain_errors(
    context: DailyMemoryContext,
    *,
    news: NewsCoverageManifest,
    event: EventClusterManifest,
    coverage: MemoryCoverageManifest,
    brain: BrainManifest,
    category_index: CategoryBrainIndexManifest,
) -> list[str]:
    errors: list[str] = []
    for identity_name, observed, expected in (
        ("news_run", news.run_id, context.run_id),
        ("news_trade_date", news.trade_date, context.trade_date),
        ("news_cutoff", as_kst(news.cutoff_at), as_kst(context.cutoff_at)),
        ("event_run", event.run_id, context.run_id),
        ("event_trade_date", event.trade_date, context.trade_date),
        ("event_cutoff", as_kst(event.cutoff_at), as_kst(context.cutoff_at)),
        ("coverage_run", coverage.run_id, context.run_id),
        ("coverage_cutoff", as_kst(coverage.cutoff_at), as_kst(context.cutoff_at)),
        (
            "coverage_corpus",
            coverage.corpus_manifest_sha256,
            context.corpus_manifest_sha256,
        ),
    ):
        if observed != expected:
            errors.append(f"daily_memory_context_{identity_name}_mismatch")
    if not coverage.coverage_complete:
        errors.append("daily_memory_context_memory_coverage_incomplete")
    if news.covered_row_count != news.input_row_count or news.missing_row_count != 0:
        errors.append("daily_memory_context_news_coverage_incomplete")
    if (
        event.input_row_count != news.input_row_count
        or event.unassigned_row_count != 0
        or event.duplicate_assignment_count != 0
    ):
        errors.append("daily_memory_context_event_coverage_incomplete")
    material_ids = [item.cluster_id for item in event.clusters if item.disposition == "MATERIAL_FULL_RETRIEVAL"]
    if not set(context.material_event_cluster_ids).issubset(material_ids):
        errors.append("daily_memory_context_material_clusters_mismatch")
    if brain.build_mode != "llm-full" or not brain.production_eligible or not brain.coverage_complete:
        errors.append("daily_memory_category_brain_not_production")
    if (
        brain.production_memory_snapshot_id != context.memory_snapshot_id
        or brain.production_memory_corpus_sha256 != context.corpus_manifest_sha256
        or brain.production_memory_source_generation_sha256 != context.source_generation_sha256
    ):
        errors.append("daily_memory_category_brain_memory_snapshot_mismatch")
    brain_cutoff = brain.brain_record_cutoff_at
    memory_cutoff = brain.production_memory_as_of_cutoff
    if (
        brain_cutoff is None
        or as_kst(brain_cutoff) > as_kst(context.cutoff_at)
        or memory_cutoff is None
        or as_kst(memory_cutoff) != as_kst(brain_cutoff)
    ):
        errors.append("daily_memory_category_brain_cutoff_mismatch")
    if (
        brain.category_brain_index_manifest_artifact != context.category_brain_index_manifest.artifact_path
        or brain.category_brain_index_manifest_sha256 != context.category_brain_index_manifest.sha256
        or category_index.brain_version != brain.brain_version
        or brain_cutoff is None
        or as_kst(category_index.brain_record_cutoff_at) != as_kst(brain_cutoff)
        or as_kst(category_index.brain_record_cutoff_at) > as_kst(context.cutoff_at)
    ):
        errors.append("daily_memory_category_brain_index_mismatch")
    return sorted(set(errors))


def compact_daily_memory_payload(
    *,
    run_id: str,
    trade_date: date,
    cutoff_at: datetime,
    memory_snapshot_id: str,
    material_event_cluster_ids: list[str],
    uncovered_material_event_cluster_ids: list[str],
    built_population_keys: list[str],
    uncovered_population_purposes: dict[str, list[PopulationPurpose]],
    population_summaries: list[dict[str, Any]],
    representative_records: list[dict[str, Any]],
    category_query_plans: list[CategoryBrainQueryPlan],
    category_guidance: list[CategoryBrainGuidance],
    graph: BeneficiaryGraphArtifact,
    disagreements: list[str],
    supporting_record_ids: list[str],
    contradicting_record_ids: list[str],
    unexplained_record_ids: list[str],
) -> dict[str, Any]:
    population_by_id = {str(row["population_id"]): row for row in population_summaries}
    compact_populations = [
        {
            "population_id": row["population_id"],
            "cluster_id": row["cluster_id"],
            "population_purpose": row["population_purpose"],
            "independent_unit_type": row["independent_unit_type"],
            "raw_record_count": row["raw_record_count"],
            "independent_unit_count": row["independent_unit_count"],
            "effective_sample_size": row["effective_sample_size"],
            "polarity_counts": row["polarity_counts"],
            "regime_counts": row["regime_counts"],
            "observed_rates": [
                {
                    "metric": rate["metric"],
                    "observed_population_rate": rate["observed_population_rate"],
                    "lower_bound": rate["lower_bound"],
                    "upper_bound": rate["upper_bound"],
                    "denominator": rate["denominator"],
                }
                for rate in row["observed_rates"]
            ],
        }
        for row in population_summaries
    ]
    compact_representatives = []
    for row in representative_records:
        population = population_by_id.get(str(row.get("population_id")), {})
        compact_representatives.append(
            {
                "cluster_id": row.get("cluster_id"),
                "population_purpose": population.get("population_purpose"),
                "independent_unit_type": population.get("independent_unit_type"),
                "record_id": row.get("record_id"),
                "independent_unit_id": row.get("independent_unit_id"),
                "trade_date": row.get("trade_date"),
                "strata": row.get("strata"),
                "context_excerpt": _excerpt_text(str(row.get("context_excerpt") or ""), limit=640),
                "provenance_source_ids": row.get("provenance_source_ids"),
            }
        )
    compact_plans = [
        {
            "cluster_id": item.cluster_id,
            "selected_claim_ids": item.selected_claim_ids,
            "expanded_query": _excerpt_text(item.expanded_query, limit=480),
        }
        for item in category_query_plans
    ]
    compact_guidance = [
        {
            "claim_id": item.claim_id,
            "category": item.category,
            "statement": _excerpt_text(item.statement, limit=360),
            "mechanism": _excerpt_text(item.mechanism, limit=240),
            "supporting_record_ids": item.supporting_record_ids,
            "contradicting_record_ids": item.contradicting_record_ids,
        }
        for item in category_guidance
    ]
    compact_graph_paths = _compact_beneficiary_graph_paths(graph)
    legacy_payload: dict[str, Any] = {
        "schema_version": "nslab.daily_memory_compact_context.v1",
        "run_id": run_id,
        "trade_date": trade_date.isoformat(),
        "cutoff_at": as_kst(cutoff_at).isoformat(),
        "context_version": DAILY_MEMORY_CONTEXT_VERSION,
        "memory_snapshot_id": memory_snapshot_id,
        "material_event_cluster_ids": material_event_cluster_ids,
        "uncovered_material_event_cluster_ids": (uncovered_material_event_cluster_ids),
        "built_population_keys": built_population_keys,
        "uncovered_population_purposes": uncovered_population_purposes,
        "population_summaries": compact_populations,
        "representative_records": [],
        "category_brain_query_plans": compact_plans,
        "category_brain_guidance": [],
        "beneficiary_graph": {
            "path_count": graph.path_count,
            "paths": [],
            "unresolved_candidate_ids": graph.unresolved_candidate_ids,
        },
        "omitted_counts": {
            "representative_records": len(compact_representatives),
            "category_brain_guidance": len(compact_guidance),
            "beneficiary_graph_paths": len(compact_graph_paths),
        },
        "unresolved_disagreements": disagreements,
        "supporting_record_ids": supporting_record_ids,
        "contradicting_record_ids": contradicting_record_ids,
        "unexplained_record_ids": unexplained_record_ids,
    }
    if len(canonical_json(legacy_payload).encode("utf-8")) <= DAILY_MEMORY_CONTEXT_MAX_BYTES:
        _extend_compact_round_robin(
            legacy_payload,
            field="representative_records",
            rows=compact_representatives,
            cluster_field="cluster_id",
        )
        graph_payload = legacy_payload["beneficiary_graph"]
        if not isinstance(graph_payload, dict):
            raise ValueError("daily compact beneficiary graph payload is invalid")
        _extend_compact_round_robin(
            legacy_payload,
            field="paths",
            rows=compact_graph_paths,
            cluster_field="event_cluster_ids",
            container=graph_payload,
        )
        _extend_compact_rows(
            legacy_payload,
            field="category_brain_guidance",
            rows=compact_guidance,
        )
        omitted = legacy_payload["omitted_counts"]
        if not isinstance(omitted, dict):
            raise ValueError("daily compact omission payload is invalid")
        omitted["representative_records"] = len(compact_representatives) - len(
            legacy_payload["representative_records"]
        )
        omitted["category_brain_guidance"] = len(compact_guidance) - len(
            legacy_payload["category_brain_guidance"]
        )
        omitted["beneficiary_graph_paths"] = len(compact_graph_paths) - len(
            graph_payload["paths"]
        )
        represented_clusters = {
            str(row.get("cluster_id"))
            for row in legacy_payload["representative_records"]
            if isinstance(row, dict)
        }
        required_representative_clusters = {
            str(row.get("cluster_id")) for row in compact_representatives
        }
        if required_representative_clusters.issubset(represented_clusters):
            return legacy_payload

    # Large days cannot repeat complete identity ledgers inside a 48 KB prompt
    # projection. The immutable daily context remains authoritative; this compact
    # view commits every complete surface and carries only bounded semantic rows.
    return _coverage_committed_compact_payload(
        run_id=run_id,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        memory_snapshot_id=memory_snapshot_id,
        material_event_cluster_ids=material_event_cluster_ids,
        uncovered_material_event_cluster_ids=uncovered_material_event_cluster_ids,
        built_population_keys=built_population_keys,
        uncovered_population_purposes=uncovered_population_purposes,
        compact_populations=compact_populations,
        compact_representatives=compact_representatives,
        compact_plans=compact_plans,
        compact_guidance=compact_guidance,
        compact_graph_paths=compact_graph_paths,
        graph=graph,
        disagreements=disagreements,
        supporting_record_ids=supporting_record_ids,
        contradicting_record_ids=contradicting_record_ids,
        unexplained_record_ids=unexplained_record_ids,
    )


def _coverage_committed_compact_payload(
    *,
    run_id: str,
    trade_date: date,
    cutoff_at: datetime,
    memory_snapshot_id: str,
    material_event_cluster_ids: list[str],
    uncovered_material_event_cluster_ids: list[str],
    built_population_keys: list[str],
    uncovered_population_purposes: dict[str, list[PopulationPurpose]],
    compact_populations: list[dict[str, Any]],
    compact_representatives: list[dict[str, Any]],
    compact_plans: list[dict[str, Any]],
    compact_guidance: list[dict[str, Any]],
    compact_graph_paths: list[dict[str, Any]],
    graph: BeneficiaryGraphArtifact,
    disagreements: list[str],
    supporting_record_ids: list[str],
    contradicting_record_ids: list[str],
    unexplained_record_ids: list[str],
) -> dict[str, Any]:
    material_ids = sorted(material_event_cluster_ids)
    uncovered_material_ids = sorted(uncovered_material_event_cluster_ids)
    population_keys = sorted(built_population_keys)
    uncovered_purposes = {
        cluster_id: sorted(purposes)
        for cluster_id, purposes in sorted(uncovered_population_purposes.items())
    }
    populations = sorted(compact_populations, key=canonical_json)
    representatives = sorted(compact_representatives, key=canonical_json)
    plans = sorted(compact_plans, key=canonical_json)
    guidance = sorted(compact_guidance, key=canonical_json)
    graph_paths = sorted(compact_graph_paths, key=canonical_json)
    unresolved_candidate_ids = sorted(graph.unresolved_candidate_ids)
    disagreement_rows = sorted(disagreements)
    role_rows = {
        "supporting_record_ids": sorted(supporting_record_ids),
        "contradicting_record_ids": sorted(contradicting_record_ids),
        "unexplained_record_ids": sorted(unexplained_record_ids),
    }
    committed_values: dict[str, Any] = {
        "material_event_cluster_ids": material_ids,
        "uncovered_material_event_cluster_ids": uncovered_material_ids,
        "built_population_keys": population_keys,
        "uncovered_population_purposes": uncovered_purposes,
        "population_summaries": populations,
        "representative_records": representatives,
        "category_brain_query_plans": plans,
        "category_brain_guidance": guidance,
        "beneficiary_graph_paths": graph_paths,
        "beneficiary_graph_unresolved_candidate_ids": unresolved_candidate_ids,
        "unresolved_disagreements": disagreement_rows,
        **role_rows,
    }
    payload: dict[str, Any] = {
        "schema_version": DAILY_MEMORY_COMPACT_V2_SCHEMA,
        "run_id": run_id,
        "trade_date": trade_date.isoformat(),
        "cutoff_at": as_kst(cutoff_at).isoformat(),
        "context_version": DAILY_MEMORY_CONTEXT_VERSION,
        "memory_snapshot_id": memory_snapshot_id,
        "coverage_projection_policy": DAILY_MEMORY_COMPACT_V2_POLICY,
        "coverage_commitments": {
            name: _compact_coverage_commitment(value)
            for name, value in committed_values.items()
        },
        "population_summaries": [],
        "representative_records": [],
        "category_brain_query_plans": [],
        "category_brain_guidance": [],
        "beneficiary_graph": {
            "path_count": graph.path_count,
            "paths": [],
            "unresolved_candidate_ids": [],
        },
        "unresolved_disagreements": [],
        "supporting_record_ids": [],
        "contradicting_record_ids": [],
        "unexplained_record_ids": [],
        "omitted_counts": {
            name: _compact_item_count(value)
            for name, value in committed_values.items()
        },
    }
    graph_payload = cast(dict[str, Any], payload["beneficiary_graph"])
    _extend_compact_surfaces(
        payload,
        surfaces=[
            (
                cast(list[Any], payload["population_summaries"]),
                _compact_round_robin_order(populations, "cluster_id"),
            ),
            (
                cast(list[Any], payload["representative_records"]),
                _compact_round_robin_order(representatives, "cluster_id"),
            ),
            (
                cast(list[Any], payload["category_brain_query_plans"]),
                _compact_round_robin_order(plans, "cluster_id"),
            ),
            (
                cast(list[Any], payload["category_brain_guidance"]),
                deque(guidance),
            ),
            (
                cast(list[Any], graph_payload["paths"]),
                _compact_round_robin_order(graph_paths, "event_cluster_ids"),
            ),
            (
                cast(list[Any], payload["unresolved_disagreements"]),
                deque(disagreement_rows),
            ),
        ],
        max_bytes=DAILY_MEMORY_CONTEXT_INITIAL_MAX_BYTES,
    )
    omitted = cast(dict[str, int], payload["omitted_counts"])
    omitted["population_summaries"] = len(populations) - len(
        payload["population_summaries"]
    )
    omitted["representative_records"] = len(representatives) - len(
        payload["representative_records"]
    )
    omitted["category_brain_query_plans"] = len(plans) - len(
        payload["category_brain_query_plans"]
    )
    omitted["category_brain_guidance"] = len(guidance) - len(
        payload["category_brain_guidance"]
    )
    omitted["beneficiary_graph_paths"] = len(graph_paths) - len(
        graph_payload["paths"]
    )
    omitted["unresolved_disagreements"] = len(disagreement_rows) - len(
        payload["unresolved_disagreements"]
    )
    if len(canonical_json(payload).encode("utf-8")) > DAILY_MEMORY_CONTEXT_INITIAL_MAX_BYTES:
        raise ValueError("daily compact commitment projection exceeds its reserved byte budget")
    return payload


def _compact_item_count(value: Any) -> int:
    return len(value) if isinstance(value, (dict, list)) else 1


def _compact_coverage_commitment(value: Any) -> dict[str, Any]:
    return {
        "item_count": _compact_item_count(value),
        "root_sha256": sha256_text(canonical_json(value)),
    }


def _compact_beneficiary_graph_paths(
    graph: BeneficiaryGraphArtifact,
) -> list[dict[str, Any]]:
    return [
        {
            "path_id": item.path_id,
            "event_cluster_ids": item.event_cluster_ids,
            "mechanism_steps": item.mechanism_steps,
            "business_roles": item.business_roles,
            "ticker": item.ticker,
            "company_name": item.company_name,
            "source_ids": item.source_ids,
            "candidate_rank": item.candidate_rank,
            "candidate_path_type": item.candidate_path_type,
        }
        for item in graph.paths
    ]


def _compact_round_robin_order(
    rows: list[dict[str, Any]],
    cluster_field: str,
) -> deque[Any]:
    groups: dict[str, deque[dict[str, Any]]] = {}
    for row in rows:
        raw_cluster = row.get(cluster_field)
        cluster_ids = raw_cluster if isinstance(raw_cluster, list) else [raw_cluster]
        cluster_id = str(cluster_ids[0]) if cluster_ids else ""
        groups.setdefault(cluster_id, deque()).append(row)
    for cluster_id, group in groups.items():
        groups[cluster_id] = deque(sorted(group, key=canonical_json))
    ordered: deque[Any] = deque()
    while groups:
        for cluster_id in sorted(groups):
            group = groups[cluster_id]
            if group:
                ordered.append(group.popleft())
            if not group:
                groups.pop(cluster_id)
    return ordered


def _extend_compact_surfaces(
    root_payload: dict[str, Any],
    *,
    surfaces: list[tuple[list[Any], deque[Any]]],
    max_bytes: int,
) -> None:
    active = list(surfaces)
    while active:
        progressed = False
        next_active: list[tuple[list[Any], deque[Any]]] = []
        for target, pending in active:
            while pending:
                row = pending.popleft()
                if _compact_append_fits(
                    root_payload,
                    target,
                    row,
                    max_bytes=max_bytes,
                ):
                    progressed = True
                    break
            if pending:
                next_active.append((target, pending))
        if not progressed:
            return
        active = next_active


def _extend_compact_round_robin(
    root_payload: dict[str, Any],
    *,
    field: str,
    rows: list[dict[str, Any]],
    cluster_field: str,
    container: dict[str, Any] | None = None,
) -> None:
    target = root_payload if container is None else container
    target[field] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        raw_cluster = row.get(cluster_field)
        cluster_ids = raw_cluster if isinstance(raw_cluster, list) else [raw_cluster]
        cluster_id = str(cluster_ids[0]) if cluster_ids else ""
        groups.setdefault(cluster_id, []).append(row)
    pending = {
        cluster_id: sorted(
            values,
            key=lambda item: canonical_json(item),
        )
        for cluster_id, values in sorted(groups.items())
    }
    while pending:
        progressed = False
        for cluster_id in list(pending):
            group = pending[cluster_id]
            if not group:
                pending.pop(cluster_id)
                continue
            row = group.pop(0)
            if _compact_append_fits(root_payload, target[field], row):
                progressed = True
            elif not target[field]:
                return
        if not progressed:
            return


def _extend_compact_rows(
    root_payload: dict[str, Any],
    *,
    field: str,
    rows: list[dict[str, Any]],
) -> None:
    target = root_payload[field]
    for row in sorted(rows, key=canonical_json):
        if not _compact_append_fits(root_payload, target, row):
            return


def _compact_append_fits(
    root_payload: dict[str, Any],
    target: list[Any],
    row: Any,
    *,
    max_bytes: int = DAILY_MEMORY_CONTEXT_MAX_BYTES,
) -> bool:
    target.append(row)
    if len(canonical_json(root_payload).encode("utf-8")) <= max_bytes:
        return True
    target.pop()
    return False


def _excerpt_text(value: str, *, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 3].rstrip() + "..."


def _material_cluster_queries(
    event_cluster_manifest_path: Path,
    event_cluster_artifact_path: Path,
) -> list[tuple[str, str]]:
    manifest = EventClusterManifest.model_validate(read_json(event_cluster_manifest_path))
    return material_cluster_queries_from_sources(
        manifest,
        _read_jsonl(event_cluster_artifact_path),
    )


def material_cluster_queries_from_sources(
    manifest: EventClusterManifest,
    event_cluster_rows: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    rows_by_id = {str(row.get("cluster_id")): row for row in event_cluster_rows}
    result = []
    for cluster in manifest.clusters:
        if cluster.disposition != "MATERIAL_FULL_RETRIEVAL":
            continue
        cluster_id = cluster.cluster_id
        row = rows_by_id.get(cluster_id, {})
        query = "\n".join(
            value.strip()
            for value in (
                str(row.get("representative_title_excerpt") or ""),
                str(row.get("representative_body_excerpt") or ""),
            )
            if value.strip()
        )
        if not query:
            raise ValueError(f"daily memory cluster query is empty: {cluster_id}")
        result.append((cluster_id, query))
    return result


def _representative_rows(
    root: Path,
    references: list[ArtifactReference],
) -> list[dict[str, Any]]:
    sources: list[tuple[RepresentativeSetManifest, list[RepresentativeRecord]]] = []
    for reference in references:
        manifest_path = root / reference.artifact_path
        manifest = RepresentativeSetManifest.model_validate(read_json(manifest_path))
        artifact_path = root / manifest.representative_records.artifact_path
        sources.append(
            (
                manifest,
                [RepresentativeRecord.model_validate(row) for row in _read_jsonl(artifact_path)],
            )
        )
    return representative_rows_from_sources(sources)


def representative_rows_from_sources(
    sources: list[tuple[RepresentativeSetManifest, list[RepresentativeRecord]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_units: set[tuple[str, str]] = set()
    for manifest, representatives in sources:
        for representative in representatives:
            key = (manifest.representative_set_id, representative.independent_unit_id)
            if key in seen_units:
                raise ValueError("daily memory representative unit is duplicated")
            seen_units.add(key)
            rows.append(
                {
                    "representative_set_id": manifest.representative_set_id,
                    "population_id": manifest.population_id,
                    "cluster_id": manifest.cluster_id,
                    **representative.model_dump(mode="json"),
                }
            )
    return rows


def _population_summaries(
    root: Path,
    references: list[ArtifactReference],
) -> list[dict[str, Any]]:
    return population_summary_rows(
        [PopulationManifest.model_validate(read_json(root / reference.artifact_path)) for reference in references]
    )


def population_summary_rows(
    manifests: list[PopulationManifest],
) -> list[dict[str, Any]]:
    return [
        {
            "population_id": manifest.population_id,
            "cluster_id": manifest.cluster_id,
            "population_purpose": manifest.population_purpose,
            "independent_unit_type": manifest.independent_unit_type,
            "selected_cell_ids": manifest.selected_cell_ids,
            "raw_record_count": manifest.raw_record_count,
            "independent_unit_count": manifest.independent_unit_count,
            "effective_sample_size": manifest.effective_sample_size,
            "polarity_counts": manifest.polarity_counts,
            "regime_counts": manifest.regime_counts,
            "outcome_summary": manifest.outcome_summary.model_dump(mode="json"),
            "observed_rates": [item.model_dump(mode="json") for item in manifest.observed_rates],
        }
        for manifest in manifests
    ]


def _cell_candidate_union(
    base_cells: list[MemoryCellCandidate],
    planned_cells: list[MemoryCellCandidate],
    *,
    limit: int,
) -> list[MemoryCellCandidate]:
    result: list[MemoryCellCandidate] = []
    seen: set[str] = set()
    for cell in (*base_cells, *planned_cells):
        cell_id = getattr(cell, "cell_id", None)
        if not isinstance(cell_id, str) or not cell_id or cell_id in seen:
            continue
        seen.add(cell_id)
        result.append(cell)
        if len(result) >= limit:
            break
    return result


def _minimum_channel_ranks(
    cells: list[MemoryCellCandidate],
    *,
    channel: str,
) -> dict[str, int]:
    if channel not in {"ann", "fts"}:
        raise ValueError("unsupported memory cell retrieval channel")
    scored = [
        (cell.cell_id, score)
        for cell in cells
        for score in [cell.ann_score if channel == "ann" else cell.fts_score]
        if score is not None
    ]
    ranked = sorted(
        scored,
        key=lambda item: (-float(item[1]), item[0]),
    )
    result: dict[str, int] = {}
    for rank, (cell_id, _score) in enumerate(ranked, start=1):
        result[cell_id] = min(rank, result.get(cell_id, rank))
    return result


def _population_key(
    cluster_id: str,
    purpose: str,
    unit_type: str,
) -> str:
    return f"{cluster_id}|{purpose}|{unit_type}"


def _category_guidance(
    claims: list[CompiledBrainClaim],
    *,
    path: Path,
    root: Path,
    selected_record_ids: set[str],
    cutoff_at: datetime,
) -> list[CategoryBrainGuidance]:
    return category_guidance_from_claims(
        claims,
        source_artifact=ArtifactReference(
            artifact_path=relative_to_root(path, root),
            sha256=file_sha256(path),
            item_count=len(claims),
        ),
        selected_record_ids=selected_record_ids,
        cutoff_at=cutoff_at,
    )


def category_guidance_from_claims(
    claims: list[CompiledBrainClaim],
    *,
    source_artifact: ArtifactReference,
    selected_record_ids: set[str],
    cutoff_at: datetime,
) -> list[CategoryBrainGuidance]:
    guidance = []
    for claim in claims:
        if as_kst(claim.available_from) > as_kst(cutoff_at):
            continue
        supporting = sorted(set(claim.supporting_record_ids) & selected_record_ids)
        contradicting = sorted(set(claim.contradicting_record_ids) & selected_record_ids)
        if not supporting and not contradicting:
            continue
        guidance.append(
            CategoryBrainGuidance(
                claim_id=claim.claim_id,
                category=claim.category,
                statement=claim.statement,
                mechanism=claim.mechanism,
                status=claim.status,
                confidence_label=claim.confidence_label,
                supporting_record_ids=supporting,
                contradicting_record_ids=contradicting,
                source_artifact_path=source_artifact.artifact_path,
                source_artifact_sha256=source_artifact.sha256,
            )
        )
    guidance.sort(key=lambda item: (item.category, item.claim_id))
    return guidance[:DAILY_CATEGORY_GUIDANCE_MAX_COUNT]


def daily_memory_record_roles(
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    supporting = []
    contradicting = []
    unexplained = []
    for row in rows:
        record_id = str(row.get("record_id") or "")
        strata = {str(value) for value in row.get("strata") or []}
        if "polarity:POSITIVE" in strata:
            supporting.append(record_id)
        elif strata.intersection({"polarity:NEGATIVE", "polarity:NEAR_MISS"}):
            contradicting.append(record_id)
        else:
            unexplained.append(record_id)
    return (
        sorted(set(supporting)),
        sorted(set(contradicting)),
        sorted(set(unexplained)),
    )


def daily_memory_disagreements(rows: list[dict[str, Any]]) -> list[str]:
    disagreements = []
    for row in rows:
        polarities = {
            key
            for key, count in (row.get("polarity_counts") or {}).items()
            if count and key in {"POSITIVE", "NEGATIVE", "NEAR_MISS"}
        }
        if len(polarities) > 1:
            disagreements.append(f"{row['cluster_id']}:polarity_conflict")
        regimes = {key for key, count in (row.get("regime_counts") or {}).items() if count and key not in {"UNKNOWN"}}
        if "CONFLICTING" in regimes or len(regimes - {"CONFLICTING"}) > 1:
            disagreements.append(f"{row['cluster_id']}:regime_disagreement")
    return sorted(set(disagreements))


def _artifact_reference(
    root: Path,
    path: Path,
    *,
    item_count: int = 1,
) -> ArtifactReference:
    return ArtifactReference(
        artifact_path=relative_to_root(path.resolve(), root),
        sha256=file_sha256(path),
        item_count=item_count,
    )


def _category_brain_snapshot(
    root: Path,
    *,
    cutoff_at: datetime,
    memory_snapshot_id: str,
    corpus_manifest_sha256: str,
    source_generation_sha256: str,
) -> tuple[Path, Path]:
    matches: list[tuple[BrainManifest, Path, Path]] = []
    for manifest_path in sorted((root / "brain" / "snapshots").glob("*/brain_manifest.json")):
        try:
            brain = BrainManifest.model_validate(read_json(manifest_path))
        except (OSError, ValueError):
            continue
        index_ref = brain.category_brain_index_manifest_artifact
        index_sha256 = brain.category_brain_index_manifest_sha256
        index_path = root / index_ref if isinstance(index_ref, str) else None
        if (
            brain.build_mode == "llm-full"
            and brain.production_eligible
            and brain.coverage_complete
            and brain.production_memory_snapshot_id == memory_snapshot_id
            and brain.production_memory_corpus_sha256 == corpus_manifest_sha256
            and brain.production_memory_source_generation_sha256 == source_generation_sha256
            and brain.brain_record_cutoff_at is not None
            and as_kst(brain.brain_record_cutoff_at) <= as_kst(cutoff_at)
            and index_path is not None
            and isinstance(index_sha256, str)
            and index_path.exists()
            and file_sha256(index_path) == index_sha256
            and inspect_category_brain_index(root, index_path, deep=False).get("passed") is True
        ):
            try:
                index_manifest = CategoryBrainIndexManifest.model_validate(read_json(index_path))
            except (OSError, ValueError):
                continue
            if (
                brain.brain_record_cutoff_at is not None
                and index_manifest.brain_version == brain.brain_version
                and as_kst(index_manifest.brain_record_cutoff_at) == as_kst(brain.brain_record_cutoff_at)
            ):
                matches.append((brain, manifest_path, index_path))
    if not matches:
        raise ValueError("daily memory has no immutable llm-full brain for the resolved memory snapshot")
    matches.sort(key=lambda item: (item[0].created_at, item[0].brain_version))
    _brain, manifest_path, index_path = matches[-1]
    return manifest_path, index_path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"JSONL row must be an object: {path}")
            rows.append(payload)
    return rows


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("immutable daily memory artifact conflict")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (canonical_json(payload) + "\n").encode("utf-8")
    _write_immutable_bytes(path, encoded)


def _daily_memory_context_stage_filename(context: DailyMemoryContext) -> str:
    if context.final_beneficiary_graph is not None:
        return DAILY_MEMORY_CONTEXT_FINAL_FILENAME
    if context.runtime_evidence_traces:
        return DAILY_MEMORY_CONTEXT_RUNTIME_EVIDENCE_FILENAME
    return DAILY_MEMORY_CONTEXT_INITIAL_FILENAME
