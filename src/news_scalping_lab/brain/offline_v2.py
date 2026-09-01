"""One-time semantic brain compiler and bounded daily package reader."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, TypeVar, cast

import duckdb
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from news_scalping_lab.brain.compiler import CATEGORY_RECORD_TYPE_ROUTES
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.offline_brain import (
    BrainPackageManifest,
    BrainPackagePointer,
    CurrentDayInterpretation,
    CurrentEventCapsule,
    DailyBrainContext,
    ExactWitness,
    LongPayloadChunkDigest,
    LongPayloadChunkDigestDraft,
    LongPayloadDigestBatch,
    OfflineCompileManifest,
    SemanticCapsuleDraftBatch,
    SemanticInfluenceManifest,
    SemanticMemoryCapsule,
    SemanticReduceNode,
    SynthesizedMechanismClaim,
)
from news_scalping_lab.llm.base import LLMProvider, count_provider_tokens
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.retrieval.production_embedding import (
    create_configured_embedding_provider,
)
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    now_kst,
    read_json,
    relative_to_root,
    sha256_text,
    stable_id,
    write_json,
)

OFFLINE_COMPILER_VERSION = "nslab.offline_semantic_brain.compiler.v5"
SEMANTIC_SPLITTER_VERSION = "recursive_full_population_cosine_radius.v3"
LONG_PAYLOAD_PROMPT_VERSION = "offline_long_payload_chunk_map.v2"
LEAF_PROMPT_VERSION = "offline_semantic_unit_leaf_map.v2"
REDUCE_PROMPT_VERSION = "offline_semantic_reduce.v1"
CATEGORY_REVIEW_PROMPT_VERSION = "offline_semantic_category_review.v1"
WORLD_REDUCE_PROMPT_VERSION = "offline_semantic_world_reduce.v1"
MAX_LEAF_PROMPT_BYTES = 180_000
MAX_LEAF_OUTPUT_UNITS = 16
MAX_LONG_PAYLOAD_CHUNK_BYTES = 72_000
MAX_LONG_PAYLOAD_BATCH_BYTES = 170_000
MAX_LONG_PAYLOAD_DIGEST_BUDGET_BYTES = 8_000
MAX_REDUCE_PROMPT_BYTES = 180_000
MAX_REDUCE_CHILDREN = 16
SPLIT_P90_DISTANCE = 0.28
SPLIT_MAX_DISTANCE = 0.55
SPLIT_DEPTH_MARGIN = 16
DAILY_MAX_CAPSULES = 24
DAILY_MAX_CLAIMS = 24
DAILY_ANN_CANDIDATES = 96

_CATEGORY_PRECEDENCE = (
    "single_event",
    "theme_formation",
    "beneficiary_discovery",
    "leader_selection",
    "continuation",
    "failure_modes",
    "counterexamples",
    "market_memory",
)
_ERROR_RECORD_TYPES = {
    "candidate_generation_error_case",
    "event_thesis_selection_error_case",
    "candidate_ranking_error_case",
    "ranking_error_case",
    "row_disposition_error_case",
    "entity_resolution_error_case",
}
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class SourceMemorySnapshot:
    project_root: Path
    snapshot_id: str
    manifest_path: Path
    manifest_sha256: str
    pointer_manifest_sha256: str
    pointer_manifest_hash_match: bool
    database_path: Path
    record_count: int
    record_corpus_root: str
    embedding_identity: str
    embedding_dimensions: int
    build_cutoff: datetime


@dataclass(frozen=True)
class OfflineBrainBuildResult:
    package_dir: Path
    package_manifest: BrainPackageManifest
    package_manifest_path: Path
    compile_manifest: OfflineCompileManifest
    influence_manifest: SemanticInfluenceManifest


@dataclass(frozen=True)
class _VectorRow:
    record_id: str
    independent_unit_id: str
    source_sha256: str
    embedding: npt.NDArray[np.float32]


@dataclass(frozen=True)
class _UnitBuild:
    semantic_unit_id: str
    category: str
    primary_cell_id: str
    evidence_polarity: str
    member_record_ids: tuple[str, ...]
    outlier_record_ids: tuple[str, ...]
    member_record_root: str
    provenance_root: str
    centroid: tuple[float, ...]


@dataclass(frozen=True)
class _LeafNode:
    node_id: str
    category: str
    capsule_ids: tuple[str, ...]
    synthesis: str


@dataclass(frozen=True)
class _PreviousPackageState:
    capsules_by_unit: dict[str, SemanticMemoryCapsule]
    reduce_nodes_by_id: dict[str, SemanticReduceNode]


@dataclass(frozen=True)
class _LongPayloadPlan:
    projected_rows: list[dict[str, Any]]
    chunk_inputs: list[dict[str, Any]]
    representative_record_count: int
    representative_payload_char_count: int
    oversized_unit_count: int
    chunked_representative_record_count: int
    long_payload_chunk_count: int
    long_payload_chunk_map_call_count: int


class OfflineSemanticBrainCompiler:
    """Compile all source records into immutable semantic capsules and claims."""

    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMProvider | None = None,
    ) -> None:
        self.settings = settings
        self.root = settings.project_root
        base_llm = llm or create_llm_provider(settings)
        self.model_config = {
            "provider": str(getattr(base_llm, "provider_name", settings.llm_provider)),
            "model": str(getattr(base_llm, "model", settings.llm.model)),
            "reasoning_effort": str(getattr(base_llm, "reasoning_effort", settings.llm.reasoning_effort)),
        }
        self.llm = _trace_offline_llm(settings, base_llm, self.model_config)
        self._logical_llm_call_count = 0
        self._prompt_token_count = 0
        self._reused_capsule_count = 0
        self._recompiled_capsule_count = 0
        self._reused_reduce_node_count = 0
        self._recompiled_reduce_node_count = 0
        self._representative_payload_char_count = 0
        self._representative_payload_full_read_count = 0
        self._chunked_representative_record_count = 0
        self._long_payload_chunk_count = 0
        self._long_payload_chunk_map_call_count = 0
        self._payload_exposure_rows: list[dict[str, Any]] = []
        self._previous = _PreviousPackageState({}, {})
        self._llm_semaphore = asyncio.Semaphore(max(1, settings.limits.max_concurrency))

    def plan(
        self,
        *,
        source_project: Path,
        output_path: Path | None = None,
        expected_manifest_sha256: str | None = None,
    ) -> dict[str, Any]:
        started = monotonic()
        source = resolve_source_memory_snapshot(
            source_project,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        plan_id = stable_id(
            "OFFLINE-PLAN",
            OFFLINE_COMPILER_VERSION,
            SEMANTIC_SPLITTER_VERSION,
            SPLIT_P90_DISTANCE,
            SPLIT_MAX_DISTANCE,
            source.record_corpus_root,
            source.manifest_sha256,
            length=20,
        )
        work_root = self.root / "brain" / ".work" / plan_id
        work_root.mkdir(parents=True, exist_ok=True)
        database_path = work_root / "semantic_plan.duckdb"
        if database_path.exists():
            database_path.unlink()
        connection = duckdb.connect(str(database_path))
        try:
            _initialize_package_database(connection, source=source)
            unit_builds = _build_semantic_assignments(
                connection,
                source=source,
                progress_path=work_root / "progress.json",
            )
            _write_offline_progress(
                work_root / "progress.json",
                phase="representative_and_distribution_planning",
                processed_record_count=source.record_count,
                total_record_count=source.record_count,
                semantic_unit_count=len(unit_builds),
            )
            unit_rows = _load_unit_prompt_rows(connection, unit_builds=unit_builds)
            payload_plan = _plan_long_payloads(unit_rows)
            payload_exposure_rows = _representative_payload_exposure_rows(payload_plan)
            leaf_batches = [
                batch
                for category in sorted(
                    {str(row["category"]) for row in payload_plan.projected_rows}
                )
                for batch in _pack_leaf_rows(
                    [row for row in payload_plan.projected_rows if row["category"] == category]
                )
            ]
        finally:
            connection.close()
        category_unit_counts: dict[str, int] = defaultdict(int)
        category_bucket_ids: dict[str, set[str]] = defaultdict(set)
        outlier_unit_count = 0
        for row in unit_rows:
            category = str(row["category"])
            category_unit_counts[category] += 1
            category_bucket_ids[category].add(sha256_text(str(row["semantic_unit_id"]))[:2])
            outlier_unit_count += int(bool(row["outlier_record_ids"]))
        estimated_reduce_calls = (
            sum(_reduce_call_count(len(bucket_ids)) + 1 for bucket_ids in category_bucket_ids.values()) + 1
        )
        plan = {
            "schema_version": "nslab.offline_semantic_brain_plan.v1",
            "plan_id": plan_id,
            "compiler_version": OFFLINE_COMPILER_VERSION,
            "semantic_splitter_version": SEMANTIC_SPLITTER_VERSION,
            "source_project": source.project_root.as_posix(),
            "source_memory_snapshot_id": source.snapshot_id,
            "source_memory_manifest_sha256": source.manifest_sha256,
            "source_pointer_manifest_sha256": source.pointer_manifest_sha256,
            "source_pointer_manifest_hash_match": source.pointer_manifest_hash_match,
            "source_manifest_override_attested": not source.pointer_manifest_hash_match,
            "record_corpus_root": source.record_corpus_root,
            "record_count": source.record_count,
            "embedding_identity": source.embedding_identity,
            "embedding_dimensions": source.embedding_dimensions,
            "embedding_reused": True,
            "import_reused": True,
            "full_population_embedding_geometry": True,
            "semantic_unit_count": len(unit_builds),
            "split_p90_cosine_distance": SPLIT_P90_DISTANCE,
            "split_max_cosine_distance": SPLIT_MAX_DISTANCE,
            "category_semantic_unit_counts": dict(sorted(category_unit_counts.items())),
            "dynamic_representative_count": payload_plan.representative_record_count,
            "representative_payload_exposure_ratio": (
                0.0
                if source.record_count == 0
                else payload_plan.representative_record_count / source.record_count
            ),
            "representative_payload_char_count": payload_plan.representative_payload_char_count,
            "representative_payload_full_read_count": payload_plan.representative_record_count,
            "representative_payload_truncated_count": 0,
            "representative_payload_read_root": sha256_text(
                canonical_json(payload_exposure_rows)
            ),
            "oversized_semantic_unit_count": payload_plan.oversized_unit_count,
            "chunked_representative_record_count": (
                payload_plan.chunked_representative_record_count
            ),
            "long_payload_chunk_count": payload_plan.long_payload_chunk_count,
            "long_payload_chunk_map_call_count": (
                payload_plan.long_payload_chunk_map_call_count
            ),
            "rare_outlier_unit_count": outlier_unit_count,
            "leaf_map_call_count": len(leaf_batches),
            "estimated_reduce_review_call_count": estimated_reduce_calls,
            "estimated_total_logical_llm_call_count": (
                payload_plan.long_payload_chunk_map_call_count
                + len(leaf_batches)
                + estimated_reduce_calls
            ),
            "first_n_shortcut_used": False,
            "silent_truncation_count": 0,
            "planning_llm_call_count": 0,
            "provider": self.model_config["provider"],
            "model": self.model_config["model"],
            "reasoning_effort": self.model_config["reasoning_effort"],
            "offline_max_concurrency": self.settings.limits.max_concurrency,
            "wall_clock_seconds": round(monotonic() - started, 6),
            "production_activated": False,
        }
        destination = output_path or (self.root / "diagnostics" / "offline_brain_v2_plan.json")
        write_json(destination, plan)
        shutil.rmtree(work_root, ignore_errors=True)
        return plan

    async def build(
        self,
        *,
        source_project: Path,
        output_root: Path | None = None,
        previous_package: Path | None = None,
        expected_manifest_sha256: str | None = None,
    ) -> OfflineBrainBuildResult:
        started_at = now_kst()
        started = monotonic()
        source = resolve_source_memory_snapshot(
            source_project,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        self._previous = _load_previous_package_state(previous_package)
        compile_id = stable_id(
            "OFFLINE-COMPILE",
            OFFLINE_COMPILER_VERSION,
            SEMANTIC_SPLITTER_VERSION,
            SPLIT_P90_DISTANCE,
            SPLIT_MAX_DISTANCE,
            source.record_corpus_root,
            source.manifest_sha256,
            self.model_config,
            length=20,
        )
        work_root = self.root / "brain" / ".work" / compile_id
        if output_root is None:
            output_root = self.root / "brain" / "packages"
        work_root.mkdir(parents=True, exist_ok=True)
        database_path = work_root / "semantic_capsule_index.duckdb"
        if database_path.exists():
            database_path.unlink()

        connection = duckdb.connect(str(database_path))
        try:
            _initialize_package_database(connection, source=source)
            unit_builds = _build_semantic_assignments(
                connection,
                source=source,
                progress_path=work_root / "progress.json",
            )
            _write_offline_progress(
                work_root / "progress.json",
                phase="representative_and_distribution_build",
                processed_record_count=source.record_count,
                total_record_count=source.record_count,
                semantic_unit_count=len(unit_builds),
            )
            unit_rows = _load_unit_prompt_rows(connection, unit_builds=unit_builds)
            package_payload_plan = _plan_long_payloads(unit_rows)
            self._representative_payload_char_count = (
                package_payload_plan.representative_payload_char_count
            )
            self._representative_payload_full_read_count = (
                package_payload_plan.representative_record_count
            )
            self._chunked_representative_record_count = (
                package_payload_plan.chunked_representative_record_count
            )
            self._long_payload_chunk_count = package_payload_plan.long_payload_chunk_count
            self._payload_exposure_rows = _representative_payload_exposure_rows(
                package_payload_plan
            )
            capsules, leaf_nodes = await self._compile_leaf_capsules(unit_rows)
            _write_capsules_to_database(connection, capsules)
            category_roots: dict[str, SemanticReduceNode] = {}
            reduce_nodes: list[SemanticReduceNode] = []
            claims: list[SynthesizedMechanismClaim] = []

            async def reduce_category(
                category: str,
            ) -> tuple[str, SemanticReduceNode, list[SemanticReduceNode], list[SemanticMemoryCapsule]]:
                category_leaves = [row for row in leaf_nodes if row.category == category]
                category_capsules = [row for row in capsules if row.category == category]
                root, nodes = await self._reduce_category(
                    category=category,
                    leaves=category_leaves,
                    capsules=category_capsules,
                )
                return category, root, nodes, category_capsules

            category_results = await asyncio.gather(
                *(reduce_category(category) for category in sorted({row.category for row in capsules}))
            )
            for category, root, nodes, category_capsules in category_results:
                category_roots[category] = root
                reduce_nodes.extend(nodes)
                claims.extend(
                    _claims_from_reduce_node(
                        root,
                        category=category,
                        capsules=category_capsules,
                    )
                )
            world_root = await self._reduce_world(category_roots)
            reduce_nodes.append(world_root)
            claims.extend(
                _claims_from_reduce_node(
                    world_root,
                    category="world_model",
                    capsules=capsules,
                )
            )
            claims = _dedupe_claims(claims)
            _write_claims_to_database(connection, claims)
            _write_reduce_nodes_to_database(connection, reduce_nodes)
            _finalize_package_database(connection)
            influence = _build_influence_manifest(
                connection,
                brain_version="PENDING",
                unit_builds=unit_builds,
                capsules=capsules,
                world_root=world_root,
                representative_payload_char_count=self._representative_payload_char_count,
                representative_payload_full_read_count=(
                    self._representative_payload_full_read_count
                ),
                chunked_representative_record_count=(
                    self._chunked_representative_record_count
                ),
                long_payload_chunk_count=self._long_payload_chunk_count,
                representative_payload_read_root=sha256_text(
                    canonical_json(self._payload_exposure_rows)
                ),
            )
        finally:
            connection.close()

        capsule_root = _model_population_root(capsules, key="capsule_id")
        claim_root = _model_population_root(claims, key="claim_id")
        category_root = sha256_text(
            canonical_json({key: value.model_dump(mode="json") for key, value in sorted(category_roots.items())})
        )
        brain_version = stable_id(
            "brain-v2",
            source.record_corpus_root,
            source.manifest_sha256,
            capsule_root,
            claim_root,
            category_root,
            length=16,
        )
        package_dir = output_root.resolve() / brain_version
        if package_dir.exists():
            existing_manifest = package_dir / "brain_package_manifest.json"
            if not existing_manifest.is_file():
                raise FileExistsError(f"incomplete package directory already exists: {package_dir}")
            shutil.rmtree(work_root)
            return load_offline_brain_build_result(package_dir)
        package_dir.parent.mkdir(parents=True, exist_ok=True)
        package_dir.mkdir()
        shutil.move(str(database_path), package_dir / database_path.name)

        influence = influence.model_copy(update={"brain_version": brain_version})
        _write_jsonl(package_dir / "semantic_capsules.jsonl", capsules)
        _write_jsonl(package_dir / "synthesized_mechanism_claims.jsonl", claims)
        _write_jsonl(
            package_dir / "representative_payload_exposure.jsonl",
            self._payload_exposure_rows,
        )
        _write_jsonl(package_dir / "semantic_unit_assignments.jsonl", _assignment_export_rows(package_dir))
        _write_category_brain(package_dir, category_roots=category_roots, world_root=world_root)
        _write_population_cube(package_dir, capsules=capsules)
        _write_graph_projections(package_dir, capsules=capsules)
        write_json(
            package_dir / "record_provenance_roots.json",
            {
                "schema_version": "nslab.record_provenance_roots.v1",
                "record_corpus_root": source.record_corpus_root,
                "assignment_root": influence.record_membership_root,
                "representative_root": influence.representative_record_root,
                "representative_payload_read_root": (
                    influence.representative_payload_read_root
                ),
            },
        )
        write_json(
            package_dir / "company_memory_ref.json",
            {
                "schema_version": "nslab.company_memory_ref.v1",
                "source_project": source.project_root.as_posix(),
                "available_through": source.build_cutoff.isoformat(),
            },
        )

        compile_manifest = OfflineCompileManifest(
            compile_id=compile_id,
            brain_version=brain_version,
            source_project=source.project_root.as_posix(),
            source_memory_snapshot_id=source.snapshot_id,
            source_memory_manifest_sha256=source.manifest_sha256,
            source_pointer_manifest_sha256=source.pointer_manifest_sha256,
            source_pointer_manifest_hash_match=source.pointer_manifest_hash_match,
            source_manifest_override_attested=not source.pointer_manifest_hash_match,
            record_corpus_root=source.record_corpus_root,
            record_count=source.record_count,
            embedding_identity=source.embedding_identity,
            embedding_reused=True,
            import_reused=True,
            semantic_splitter_version=SEMANTIC_SPLITTER_VERSION,
            full_population_embedding_geometry=True,
            split_p90_cosine_distance=SPLIT_P90_DISTANCE,
            split_max_cosine_distance=SPLIT_MAX_DISTANCE,
            semantic_unit_count=len(unit_builds),
            leaf_node_count=len(leaf_nodes),
            reduce_node_count=len(reduce_nodes),
            category_root_count=len(category_roots),
            child_omission_count=influence.unrepresented_reasoning_unit_count,
            first_n_shortcut_used=False,
            silent_truncation_count=0,
            representative_payload_char_count=self._representative_payload_char_count,
            representative_payload_full_read_count=(
                self._representative_payload_full_read_count
            ),
            representative_payload_truncated_count=0,
            chunked_representative_record_count=(
                self._chunked_representative_record_count
            ),
            long_payload_chunk_count=self._long_payload_chunk_count,
            long_payload_chunk_map_call_count=self._long_payload_chunk_map_call_count,
            llm_call_count=self._logical_llm_call_count,
            prompt_token_count=self._prompt_token_count,
            reused_semantic_capsule_count=self._reused_capsule_count,
            recompiled_semantic_capsule_count=self._recompiled_capsule_count,
            reused_reduce_node_count=self._reused_reduce_node_count,
            recompiled_reduce_node_count=self._recompiled_reduce_node_count,
            provider=self.model_config["provider"],
            model=self.model_config["model"],
            reasoning_effort=self.model_config["reasoning_effort"],
            max_concurrency=self.settings.limits.max_concurrency,
            started_at=started_at,
            completed_at=now_kst(),
        )
        write_json(
            package_dir / "offline_compile_manifest.json",
            compile_manifest.model_dump(mode="json"),
        )
        write_json(
            package_dir / "semantic_influence_manifest.json",
            influence.model_dump(mode="json"),
        )
        package_root = _artifact_root(package_dir)
        manifest = BrainPackageManifest(
            brain_version=brain_version,
            created_at=compile_manifest.completed_at,
            build_cutoff=source.build_cutoff,
            record_count=source.record_count,
            semantic_unit_count=len(unit_builds),
            semantic_capsule_count=len(capsules),
            synthesized_mechanism_claim_count=len(claims),
            population_contribution_record_count=influence.population_contribution_record_count,
            representative_payload_exposed_record_count=(
                influence.representative_payload_exposed_record_count
            ),
            representative_payload_not_exposed_record_count=(
                influence.representative_payload_not_exposed_record_count
            ),
            representative_payload_exposure_ratio=(
                influence.representative_payload_exposure_ratio
            ),
            representative_payload_read_root=influence.representative_payload_read_root,
            representative_payload_char_count=influence.representative_payload_char_count,
            representative_payload_full_read_count=(
                influence.representative_payload_full_read_count
            ),
            representative_payload_truncated_count=(
                influence.representative_payload_truncated_count
            ),
            chunked_representative_record_count=(
                influence.chunked_representative_record_count
            ),
            long_payload_chunk_count=influence.long_payload_chunk_count,
            record_corpus_root=source.record_corpus_root,
            memory_snapshot_root=source.manifest_sha256,
            warehouse_root=_warehouse_root(source.project_root),
            embedding_identity=source.embedding_identity,
            compiler_version=OFFLINE_COMPILER_VERSION,
            provider=self.model_config["provider"],
            model=self.model_config["model"],
            reasoning_effort=self.model_config["reasoning_effort"],
            capsule_root=capsule_root,
            mechanism_claim_root=claim_root,
            category_brain_root=category_root,
            package_root=package_root,
            assignment_coverage_ratio=1.0,
            unassigned_record_count=influence.unassigned_record_count,
            duplicate_primary_assignment_count=(influence.duplicate_primary_assignment_count),
            rare_outlier_unit_coverage_ratio=(
                1.0
                if influence.rare_outlier_unit_count == 0
                else influence.rare_outlier_represented_unit_count / influence.rare_outlier_unit_count
            ),
            unrepresented_reasoning_unit_count=(influence.unrepresented_reasoning_unit_count),
            child_omission_count=compile_manifest.child_omission_count,
            semantic_capsule_hnsw_index_ready=True,
            mechanism_claim_hnsw_index_ready=True,
            daily_ann_query_plan_verified=True,
            production_eligible=False,
        )
        write_json(
            package_dir / "brain_package_manifest.json",
            manifest.model_dump(mode="json"),
        )
        write_json(
            package_dir / "build_receipt.json",
            {
                "schema_version": "nslab.offline_brain_build_receipt.v1",
                "brain_version": brain_version,
                "previous_package": previous_package.as_posix() if previous_package else None,
                "wall_clock_seconds": round(monotonic() - started, 6),
                "package_manifest_sha256": file_sha256(package_dir / "brain_package_manifest.json"),
                "production_activated": False,
            },
        )
        shutil.rmtree(work_root, ignore_errors=True)
        return OfflineBrainBuildResult(
            package_dir=package_dir,
            package_manifest=manifest,
            package_manifest_path=package_dir / "brain_package_manifest.json",
            compile_manifest=compile_manifest,
            influence_manifest=influence,
        )

    async def _compile_leaf_capsules(
        self,
        unit_rows: list[dict[str, Any]],
    ) -> tuple[list[SemanticMemoryCapsule], list[_LeafNode]]:
        capsules_by_unit: dict[str, SemanticMemoryCapsule] = {}
        changed_rows: list[dict[str, Any]] = []
        for row in unit_rows:
            unit_id = str(row["semantic_unit_id"])
            previous = self._previous.capsules_by_unit.get(unit_id)
            if previous is not None and previous.member_record_root == row["member_record_root"]:
                capsules_by_unit[unit_id] = previous
                self._reused_capsule_count += 1
            else:
                changed_rows.append(row)
        changed_payload_plan = _plan_long_payloads(changed_rows)
        changed_rows = await self._compile_long_payload_digests(changed_payload_plan)
        work: list[tuple[str, list[dict[str, Any]]]] = []
        for category in sorted({str(row["category"]) for row in changed_rows}):
            rows = [row for row in changed_rows if row["category"] == category]
            for batch in _pack_leaf_rows(rows):
                work.append((category, batch))

        async def compile_batch(
            category: str,
            batch: list[dict[str, Any]],
        ) -> list[SemanticMemoryCapsule]:
            node_id = stable_id(
                "LEAF-MAP",
                category,
                [row["semantic_unit_id"] for row in batch],
                [row["member_record_root"] for row in batch],
                length=20,
            )
            result = await self._call_structured(
                prompt=_leaf_prompt(node_id=node_id, category=category, rows=batch),
                response_model=SemanticCapsuleDraftBatch,
                purpose=f"offline_semantic_leaf.{node_id}",
            )
            expected = [str(row["semantic_unit_id"]) for row in batch]
            if result.node_id != node_id or result.semantic_unit_ids != expected:
                raise ValueError("offline semantic leaf output identity drifted")
            by_unit = {row.semantic_unit_id: row for row in result.capsules}
            return [
                _materialize_capsule(row, draft=by_unit[str(row["semantic_unit_id"])])
                for row in batch
            ]

        queue: asyncio.Queue[tuple[str, list[dict[str, Any]]]] = asyncio.Queue()
        for item in work:
            queue.put_nowait(item)
        compiled_batches: list[list[SemanticMemoryCapsule]] = []

        async def worker() -> None:
            while True:
                try:
                    category, batch = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                compiled_batches.append(await compile_batch(category, batch))
                queue.task_done()

        await asyncio.gather(
            *(
                worker()
                for _ in range(min(max(1, self.settings.limits.max_concurrency), len(work)))
            )
        )
        for compiled in compiled_batches:
            for capsule in compiled:
                capsules_by_unit[capsule.semantic_unit_id] = capsule
                self._recompiled_capsule_count += 1
        expected_units = {str(row["semantic_unit_id"]) for row in unit_rows}
        if set(capsules_by_unit) != expected_units:
            raise ValueError("offline semantic capsule compile omitted units")
        capsules = [capsules_by_unit[key] for key in sorted(capsules_by_unit)]
        return capsules, _capsule_leaf_nodes(capsules)

    async def _compile_long_payload_digests(
        self,
        payload_plan: _LongPayloadPlan,
    ) -> list[dict[str, Any]]:
        batches = list(_pack_long_payload_chunks(payload_plan.chunk_inputs))
        self._long_payload_chunk_map_call_count += len(batches)
        if not batches:
            return payload_plan.projected_rows

        async def compile_batch(batch: list[dict[str, Any]]) -> list[LongPayloadChunkDigest]:
            chunk_ids = [str(row["chunk_id"]) for row in batch]
            node_id = stable_id(
                "LONG-PAYLOAD-MAP",
                chunk_ids,
                [row["chunk_sha256"] for row in batch],
                length=20,
            )
            result = await self._call_structured(
                prompt=_long_payload_prompt(node_id=node_id, chunks=batch),
                response_model=LongPayloadDigestBatch,
                purpose=f"offline_long_payload_map.{node_id}",
            )
            if result.node_id != node_id or result.chunk_ids != chunk_ids:
                raise ValueError("long payload digest output identity drifted")
            source_by_id = {str(row["chunk_id"]): row for row in batch}
            return [
                _materialize_long_payload_chunk_digest(
                    source_row=source_by_id[digest.chunk_id],
                    draft=digest,
                )
                for digest in result.digests
            ]

        queue: asyncio.Queue[list[dict[str, Any]]] = asyncio.Queue()
        for batch in batches:
            queue.put_nowait(batch)
        digests: list[LongPayloadChunkDigest] = []

        async def worker() -> None:
            while True:
                try:
                    batch = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                digests.extend(await compile_batch(batch))
                queue.task_done()

        await asyncio.gather(
            *(
                worker()
                for _ in range(min(max(1, self.settings.limits.max_concurrency), len(batches)))
            )
        )
        by_id = {row.chunk_id: row for row in digests}
        expected_ids = {str(row["chunk_id"]) for row in payload_plan.chunk_inputs}
        if set(by_id) != expected_ids:
            raise ValueError("long payload digest stage omitted or added chunks")
        output: list[dict[str, Any]] = []
        for row in payload_plan.projected_rows:
            representatives: list[dict[str, Any]] = []
            for representative in row["representatives"]:
                projected = dict(representative)
                placeholders = projected.get("full_payload_chunk_digests")
                if isinstance(placeholders, list):
                    projected["full_payload_chunk_digests"] = [
                        by_id[str(value["chunk_id"])].model_dump(mode="json")
                        for value in placeholders
                    ]
                representatives.append(projected)
            output.append({**row, "representatives": representatives})
        return output

    async def _reduce_category(
        self,
        *,
        category: str,
        leaves: list[_LeafNode],
        capsules: list[SemanticMemoryCapsule],
    ) -> tuple[SemanticReduceNode, list[SemanticReduceNode]]:
        current = [
            SemanticReduceNode(
                node_id=row.node_id,
                child_node_ids=[],
                covered_capsule_ids=list(row.capsule_ids),
                synthesis=row.synthesis,
            )
            for row in leaves
        ]
        created: list[SemanticReduceNode] = []
        level = 0
        while len(current) > 1:
            next_level: list[SemanticReduceNode] = []
            for group in _pack_reduce_nodes(current):
                node = await self._reduce_node(
                    category=category,
                    level=level,
                    children=group,
                    review=False,
                )
                created.append(node)
                next_level.append(node)
            current = next_level
            level += 1
        if not current:
            raise ValueError(f"category {category} has no semantic leaves")
        review = await self._reduce_node(
            category=category,
            level=level,
            children=current,
            review=True,
        )
        if set(review.covered_capsule_ids) != {row.capsule_id for row in capsules}:
            raise ValueError(f"category {category} review omitted semantic capsules")
        created.append(review)
        return review, created

    async def _reduce_node(
        self,
        *,
        category: str,
        level: int,
        children: list[SemanticReduceNode],
        review: bool,
    ) -> SemanticReduceNode:
        child_ids = [row.node_id for row in children]
        covered = _unique(capsule_id for row in children for capsule_id in row.covered_capsule_ids)
        node_id = stable_id(
            "CATEGORY-REVIEW" if review else "REDUCE",
            category,
            level,
            child_ids,
            covered,
            length=20,
        )
        prompt = _reduce_prompt(
            node_id=node_id,
            category=category,
            children=children,
            review=review,
        )
        previous = self._previous.reduce_nodes_by_id.get(node_id)
        if previous is not None:
            result = previous
            self._reused_reduce_node_count += 1
        else:
            result = await self._call_structured(
                prompt=prompt,
                response_model=SemanticReduceNode,
                purpose=(f"offline_category_review.{category}" if review else f"offline_semantic_reduce.{node_id}"),
            )
            self._recompiled_reduce_node_count += 1
        if (
            result.node_id != node_id
            or result.child_node_ids != child_ids
            or set(result.covered_capsule_ids) != set(covered)
        ):
            raise ValueError("semantic reduce output omitted or added children")
        return result.model_copy(update={"covered_capsule_ids": covered})

    async def _reduce_world(
        self,
        category_roots: dict[str, SemanticReduceNode],
    ) -> SemanticReduceNode:
        children = [category_roots[key] for key in sorted(category_roots)]
        node_id = stable_id(
            "WORLD",
            [row.node_id for row in children],
            length=20,
        )
        prompt = _reduce_prompt(
            node_id=node_id,
            category="world_model",
            children=children,
            review=True,
        )
        previous = self._previous.reduce_nodes_by_id.get(node_id)
        if previous is not None:
            result = previous
            self._reused_reduce_node_count += 1
        else:
            result = await self._call_structured(
                prompt=prompt,
                response_model=SemanticReduceNode,
                purpose="offline_world_model",
            )
            self._recompiled_reduce_node_count += 1
        expected_children = [row.node_id for row in children]
        expected_capsules = _unique(capsule_id for row in children for capsule_id in row.covered_capsule_ids)
        if (
            result.node_id != node_id
            or result.child_node_ids != expected_children
            or set(result.covered_capsule_ids) != set(expected_capsules)
        ):
            raise ValueError("world reduce output omitted category children")
        return result.model_copy(update={"covered_capsule_ids": expected_capsules})

    async def _call_structured(
        self,
        *,
        prompt: str,
        response_model: type[T],
        purpose: str,
    ) -> T:
        self._logical_llm_call_count += 1
        self._prompt_token_count += count_provider_tokens(self.llm, prompt)
        async with self._llm_semaphore:
            return await self.llm.generate_structured(
                prompt=prompt,
                response_model=response_model,
                purpose=purpose,
            )


class BrainPackageDailyContextProvider:
    """Read only precompiled capsules/claims; never scan the raw record table."""

    def __init__(
        self,
        settings: Settings,
        *,
        package_dir: Path | None = None,
        embedding_provider: Any | None = None,
    ) -> None:
        self.settings = settings
        self.root = settings.project_root
        self.package_dir = package_dir
        self.embedding_provider = embedding_provider or create_configured_embedding_provider(
            settings,
            production=(settings.event_cluster_fallback_policy.value == "fail-closed"),
        )
        self._manifest: BrainPackageManifest | None = None

    def ensure_ready(self) -> None:
        package_dir = self.package_dir or _package_dir_from_pointer(self.root)
        manifest_path = package_dir / "brain_package_manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError("selected Offline Semantic Brain V2 manifest is missing")
        manifest = BrainPackageManifest.model_validate(read_json(manifest_path))
        if _artifact_root(package_dir) != manifest.package_root:
            raise ValueError("Offline Semantic Brain V2 package root drifted")
        database = package_dir / "semantic_capsule_index.duckdb"
        if not database.is_file():
            raise FileNotFoundError("semantic capsule index is missing")
        if not (
            manifest.semantic_capsule_hnsw_index_ready
            and manifest.mechanism_claim_hnsw_index_ready
            and manifest.daily_ann_query_plan_verified
        ):
            raise ValueError("Offline Semantic Brain V2 ANN readiness is not closed")
        connection = duckdb.connect(str(database), read_only=True)
        try:
            connection.execute("LOAD vss")
            _assert_hnsw_query_plan(
                connection,
                table="semantic_capsules",
                id_column="capsule_id",
                embedding_column="embedding",
            )
            _assert_hnsw_query_plan(
                connection,
                table="mechanism_claims",
                id_column="claim_id",
                embedding_column="embedding",
            )
        finally:
            connection.close()
        self.package_dir = package_dir
        self._manifest = manifest

    async def retrieve(
        self,
        *,
        interpretation: CurrentDayInterpretation,
        current_event_capsules: Sequence[CurrentEventCapsule],
        cutoff_at: datetime,
        max_exact_witnesses: int,
    ) -> DailyBrainContext:
        if self._manifest is None or self.package_dir is None:
            self.ensure_ready()
        assert self._manifest is not None
        assert self.package_dir is not None
        if self._manifest.build_cutoff > cutoff_at:
            raise ValueError("selected BrainPackage was built after the daily inference cutoff")
        query_texts = _daily_query_texts(interpretation, current_event_capsules)
        vectors = await self.embedding_provider.embed(
            texts=query_texts,
            purpose="thin_daily_brain_retrieval",
        )
        connection = duckdb.connect(
            str(self.package_dir / "semantic_capsule_index.duckdb"),
            read_only=True,
        )
        try:
            connection.execute("LOAD vss")
            capsule_scores: dict[str, float] = {}
            claim_scores: dict[str, float] = {}
            for vector in vectors:
                rows = connection.execute(
                    """
                    SELECT capsule_id,
                           1.0 - array_cosine_distance(embedding, ?::FLOAT[384]) AS score
                    FROM semantic_capsules
                    ORDER BY array_cosine_distance(embedding, ?::FLOAT[384]), capsule_id
                    LIMIT ?
                    """,
                    [vector, vector, DAILY_ANN_CANDIDATES],
                ).fetchall()
                for capsule_id, score in rows:
                    capsule_scores[str(capsule_id)] = max(float(score), capsule_scores.get(str(capsule_id), -1.0))
                claim_rows = connection.execute(
                    """
                    SELECT claim_id,
                           1.0 - array_cosine_distance(embedding, ?::FLOAT[384]) AS score
                    FROM mechanism_claims
                    ORDER BY array_cosine_distance(embedding, ?::FLOAT[384]), claim_id
                    LIMIT ?
                    """,
                    [vector, vector, DAILY_ANN_CANDIDATES],
                ).fetchall()
                for claim_id, score in claim_rows:
                    claim_scores[str(claim_id)] = max(
                        float(score), claim_scores.get(str(claim_id), -1.0)
                    )
            selected_ids = _balanced_capsule_ids(
                connection,
                capsule_scores=capsule_scores,
                limit=DAILY_MAX_CAPSULES,
            )
            selected_capsules = [
                SemanticMemoryCapsule.model_validate_json(row[0])
                for capsule_id in selected_ids
                for row in connection.execute(
                    "SELECT payload_json FROM semantic_capsules WHERE capsule_id = ?",
                    [capsule_id],
                ).fetchall()
            ]
            selected_claims = _selected_claims(
                connection,
                selected_capsule_ids=set(selected_ids),
                claim_scores=claim_scores,
                limit=DAILY_MAX_CLAIMS,
            )
        finally:
            connection.close()
        witnesses = _unique_witnesses(selected_capsules)[:max_exact_witnesses]
        population_statistics = [
            {
                "population_root": row.member_record_root,
                "capsule_id": row.capsule_id,
                "member_record_count": row.member_record_count,
                "member_independent_unit_count": row.member_independent_unit_count,
                "record_type_distribution": row.record_type_distribution,
                "polarity_distribution": row.polarity_distribution,
                "label_quality_distribution": row.label_quality_distribution,
                "time_distribution": row.time_distribution,
                "regime_distribution": row.regime_distribution,
            }
            for row in selected_capsules
        ]
        return DailyBrainContext(
            brain_version=self._manifest.brain_version,
            brain_package_root=self._manifest.package_root,
            interpretation_sha256=sha256_text(canonical_json(interpretation.model_dump(mode="json"))),
            selected_semantic_capsules=selected_capsules,
            selected_mechanism_claims=selected_claims,
            population_statistics=population_statistics,
            current_vs_history_differences=_current_history_differences(interpretation, selected_capsules),
            beneficiary_graph=_projection_rows(
                self.package_dir / "beneficiary_graph" / "summary.json",
                selected_capsule_ids=set(selected_ids),
            ),
            leader_selection_memory=_projection_rows(
                self.package_dir / "leader_selection_memory" / "summary.json",
                selected_capsule_ids=set(selected_ids),
            ),
            continuation_memory=_projection_rows(
                self.package_dir / "continuation_memory" / "summary.json",
                selected_capsule_ids=set(selected_ids),
            ),
            unresolved_contradictions=_unique(
                condition for row in selected_capsules for condition in row.failure_conditions
            ),
            exact_witnesses=witnesses,
            retrieval_query_count=len(query_texts),
            index_query_count=2 * len(query_texts),
            online_full_corpus_scan_count=0,
            future_record_count=0,
        )


def resolve_source_memory_snapshot(
    project_root: Path,
    *,
    expected_manifest_sha256: str | None = None,
) -> SourceMemorySnapshot:
    root = project_root.resolve()
    pointer_path = root / "memory" / "retrieval_index" / "current.json"
    pointer = read_json(pointer_path)
    if not isinstance(pointer, dict):
        raise ValueError("memory index pointer must be an object")
    manifest_ref = pointer.get("manifest_path")
    if not isinstance(manifest_ref, str):
        raise ValueError("memory index pointer omitted manifest_path")
    manifest_path = (root / manifest_ref).resolve()
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("memory index manifest escapes source project") from exc
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("memory index manifest must be an object")
    pointer_manifest_sha = pointer.get("manifest_sha256")
    if not isinstance(pointer_manifest_sha, str):
        raise ValueError("memory index pointer omitted manifest_sha256")
    actual_manifest_sha = file_sha256(manifest_path)
    pointer_manifest_hash_match = pointer_manifest_sha == actual_manifest_sha
    # Legacy drift is accepted only with the actual SHA already sealed by the
    # external audit. The source pointer and snapshot remain read-only.
    if not pointer_manifest_hash_match and expected_manifest_sha256 != actual_manifest_sha:
        raise ValueError(
            "memory index manifest hash drifted; an externally attested actual SHA is required"
        )
    if expected_manifest_sha256 is not None and expected_manifest_sha256 != actual_manifest_sha:
        raise ValueError("explicit source memory manifest SHA does not match the artifact")
    database_ref = manifest.get("database")
    if not isinstance(database_ref, dict) or not isinstance(database_ref.get("artifact_path"), str):
        raise ValueError("memory index manifest omitted database artifact")
    database_path = (root / str(database_ref["artifact_path"])).resolve()
    if file_sha256(database_path) != database_ref.get("sha256"):
        raise ValueError("memory index database hash drifted")
    record_manifest = read_json(root / "memory" / "record_index" / "manifest.json")
    if not isinstance(record_manifest, dict):
        raise ValueError("record index manifest must be an object")
    return SourceMemorySnapshot(
        project_root=root,
        snapshot_id=str(manifest["snapshot_id"]),
        manifest_path=manifest_path,
        manifest_sha256=actual_manifest_sha,
        pointer_manifest_sha256=pointer_manifest_sha,
        pointer_manifest_hash_match=pointer_manifest_hash_match,
        database_path=database_path,
        record_count=int(manifest["record_count"]),
        record_corpus_root=str(record_manifest["full_envelope_root_sha256"]),
        embedding_identity=str(manifest["embedding_model"]),
        embedding_dimensions=int(manifest["embedding_dimensions"]),
        build_cutoff=datetime.fromisoformat(str(manifest["as_of_cutoff"])),
    )


def select_brain_package(
    project_root: Path,
    *,
    package_dir: Path,
    production_activated: bool = False,
) -> Path:
    root = project_root.resolve()
    package = package_dir.resolve()
    manifest_path = package / "brain_package_manifest.json"
    manifest = BrainPackageManifest.model_validate(read_json(manifest_path))
    if _artifact_root(package) != manifest.package_root:
        raise ValueError("cannot select a BrainPackage with a drifting artifact root")
    if production_activated and not manifest.production_eligible:
        raise ValueError("cannot activate a BrainPackage before production quality eligibility")
    pointer = BrainPackagePointer(
        brain_version=manifest.brain_version,
        package_path=relative_to_root(package, root),
        manifest_sha256=file_sha256(manifest_path),
        package_root=manifest.package_root,
        production_activated=production_activated,
    )
    path = root / "brain" / "current" / "brain_package_pointer.json"
    write_json(path, pointer.model_dump(mode="json"))
    return path


def load_offline_brain_build_result(package_dir: Path) -> OfflineBrainBuildResult:
    package = package_dir.resolve()
    return OfflineBrainBuildResult(
        package_dir=package,
        package_manifest=BrainPackageManifest.model_validate(read_json(package / "brain_package_manifest.json")),
        package_manifest_path=package / "brain_package_manifest.json",
        compile_manifest=OfflineCompileManifest.model_validate(read_json(package / "offline_compile_manifest.json")),
        influence_manifest=SemanticInfluenceManifest.model_validate(
            read_json(package / "semantic_influence_manifest.json")
        ),
    )


def _load_previous_package_state(
    package_dir: Path | None,
) -> _PreviousPackageState:
    if package_dir is None:
        return _PreviousPackageState({}, {})
    package = package_dir.resolve()
    manifest = BrainPackageManifest.model_validate(read_json(package / "brain_package_manifest.json"))
    if _artifact_root(package) != manifest.package_root:
        raise ValueError("previous BrainPackage root drifted")
    capsules: dict[str, SemanticMemoryCapsule] = {}
    with (package / "semantic_capsules.jsonl").open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                capsule = SemanticMemoryCapsule.model_validate_json(line)
            except ValueError as exc:
                raise ValueError(f"previous semantic capsule row {line_number} is invalid") from exc
            if capsule.semantic_unit_id in capsules:
                raise ValueError("previous BrainPackage contains duplicate semantic units")
            capsules[capsule.semantic_unit_id] = capsule
    connection = duckdb.connect(str(package / "semantic_capsule_index.duckdb"), read_only=True)
    try:
        reduce_nodes = {
            str(node_id): SemanticReduceNode.model_validate_json(str(payload))
            for node_id, payload in connection.execute("SELECT node_id, payload_json FROM reduce_nodes").fetchall()
        }
    finally:
        connection.close()
    return _PreviousPackageState(capsules, reduce_nodes)


def _initialize_package_database(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: SourceMemorySnapshot,
) -> None:
    source_path = str(source.database_path).replace("'", "''")
    connection.execute(f"ATTACH '{source_path}' AS source_memory (READ_ONLY)")
    connection.execute(
        """
        CREATE TABLE semantic_unit_assignments (
            record_id VARCHAR PRIMARY KEY,
            primary_semantic_unit_id VARCHAR NOT NULL,
            secondary_semantic_unit_ids VARCHAR NOT NULL,
            assignment_basis VARCHAR NOT NULL,
            distance DOUBLE NOT NULL,
            outlier BOOLEAN NOT NULL
        );
        CREATE TABLE semantic_unit_centroids (
            semantic_unit_id VARCHAR PRIMARY KEY,
            category VARCHAR NOT NULL,
            primary_cell_id VARCHAR NOT NULL,
            evidence_polarity VARCHAR NOT NULL,
            centroid FLOAT[384] NOT NULL,
            member_record_root VARCHAR NOT NULL,
            provenance_root VARCHAR NOT NULL
        );
        CREATE TABLE semantic_capsules (
            capsule_id VARCHAR PRIMARY KEY,
            category VARCHAR NOT NULL,
            semantic_unit_id VARCHAR NOT NULL,
            available_from VARCHAR NOT NULL,
            embedding FLOAT[384] NOT NULL,
            payload_json VARCHAR NOT NULL
        );
        CREATE TABLE mechanism_claims (
            claim_id VARCHAR PRIMARY KEY,
            category VARCHAR NOT NULL,
            available_from VARCHAR NOT NULL,
            embedding FLOAT[384] NOT NULL,
            payload_json VARCHAR NOT NULL
        );
        CREATE TABLE mechanism_claim_capsules (
            claim_id VARCHAR NOT NULL,
            capsule_id VARCHAR NOT NULL,
            role VARCHAR NOT NULL
        );
        CREATE TABLE reduce_nodes (
            node_id VARCHAR PRIMARY KEY,
            payload_json VARCHAR NOT NULL
        );
        """
    )


def _build_semantic_assignments(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: SourceMemorySnapshot,
    progress_path: Path | None = None,
) -> list[_UnitBuild]:
    category_case = _category_case_sql()
    source_connection = duckdb.connect(str(source.database_path), read_only=True)
    cursor = source_connection.execute(
        f"""
        SELECT
            {category_case} AS category,
            primary_cell_id,
            COALESCE(evidence_polarity, 'UNKNOWN') AS evidence_polarity,
            record_id,
            independent_unit_id,
            source_sha256,
            embedding
        FROM records
        ORDER BY category, primary_cell_id, evidence_polarity, hash(record_id)
        """
    )
    builds: list[_UnitBuild] = []
    current_key: tuple[str, str, str] | None = None
    current_rows: list[_VectorRow] = []
    processed_record_count = 0
    stratum_count = 0
    last_progress_at = monotonic()

    def flush() -> None:
        nonlocal current_rows, last_progress_at, processed_record_count, stratum_count
        if current_key is None or not current_rows:
            return
        category, cell_id, polarity = current_key
        try:
            group_builds, assignments = _split_semantic_stratum(
                category=category,
                primary_cell_id=cell_id,
                evidence_polarity=polarity,
                rows=current_rows,
            )
        except ValueError as exc:
            raise ValueError(
                "semantic stratum failed without truncation: "
                f"category={category}, primary_cell_id={cell_id}, "
                f"polarity={polarity}, record_count={len(current_rows)}"
            ) from exc
        connection.executemany(
            "INSERT INTO semantic_unit_assignments VALUES (?, ?, ?, ?, ?, ?)",
            assignments,
        )
        connection.executemany(
            "INSERT INTO semantic_unit_centroids VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.semantic_unit_id,
                    row.category,
                    row.primary_cell_id,
                    row.evidence_polarity,
                    list(row.centroid),
                    row.member_record_root,
                    row.provenance_root,
                )
                for row in group_builds
            ],
        )
        builds.extend(group_builds)
        processed_record_count += len(current_rows)
        stratum_count += 1
        current_time = monotonic()
        if progress_path is not None and (
            processed_record_count == source.record_count or current_time - last_progress_at >= 2.0
        ):
            _write_offline_progress(
                progress_path,
                phase="semantic_assignments",
                processed_record_count=processed_record_count,
                total_record_count=source.record_count,
                semantic_unit_count=len(builds),
                stratum_count=stratum_count,
            )
            last_progress_at = current_time
        current_rows = []

    try:
        while True:
            batch = cursor.fetchmany(4096)
            if not batch:
                break
            for raw in batch:
                key = (str(raw[0]), str(raw[1]), str(raw[2]))
                if current_key is not None and key != current_key:
                    flush()
                current_key = key
                current_rows.append(
                    _VectorRow(
                        record_id=str(raw[3]),
                        independent_unit_id=str(raw[4]),
                        source_sha256=str(raw[5]),
                        embedding=np.asarray(raw[6], dtype=np.float32),
                    )
                )
        flush()
    finally:
        source_connection.close()
    assigned_row = connection.execute("SELECT count(*) FROM semantic_unit_assignments").fetchone()
    if assigned_row is None:
        raise ValueError("semantic assignment count query returned no row")
    assigned_count = int(assigned_row[0])
    if assigned_count != source.record_count:
        raise ValueError(f"semantic primary assignment coverage mismatch: {assigned_count} != {source.record_count}")
    duplicate_row = connection.execute(
        "SELECT count(*) - count(DISTINCT record_id) FROM semantic_unit_assignments"
    ).fetchone()
    if duplicate_row is None:
        raise ValueError("semantic duplicate assignment query returned no row")
    duplicate_count = int(duplicate_row[0])
    if duplicate_count:
        raise ValueError("semantic primary assignments contain duplicates")
    return builds


def _split_semantic_stratum(
    *,
    category: str,
    primary_cell_id: str,
    evidence_polarity: str,
    rows: list[_VectorRow],
) -> tuple[list[_UnitBuild], list[tuple[Any, ...]]]:
    # Geometry uses every record. Prefix sampling can hide a rare mechanism
    # while still making the assignment ledger look structurally complete.
    matrix = _normalize_matrix(np.stack([row.embedding for row in rows]))
    stratum_centroid = _normalized_mean(matrix)
    stratum_distances = 1.0 - matrix @ stratum_centroid
    clusters = _recursive_semantic_clusters(matrix, np.arange(len(matrix)), depth=0)
    builds: list[_UnitBuild] = []
    assignment_rows: list[tuple[Any, ...]] = []
    cluster_order = sorted(
        range(len(clusters)),
        key=lambda index: (
            rows[
                int(
                    clusters[index][
                        np.argmin(
                            1.0
                            - matrix[clusters[index]]
                            @ _normalized_mean(matrix[clusters[index]])
                        )
                    ]
                )
            ].record_id
        ),
    )
    cluster_remap = {old: new for new, old in enumerate(cluster_order)}
    for old_index in cluster_order:
        member_indexes = clusters[old_index]
        if not len(member_indexes):
            continue
        member_rows = [rows[int(index)] for index in member_indexes]
        member_ids = tuple(sorted(row.record_id for row in member_rows))
        source_pairs = sorted((row.record_id, row.source_sha256) for row in member_rows)
        centroid = _normalized_mean(matrix[member_indexes])
        member_scores = matrix[member_indexes] @ centroid
        member_distances = 1.0 - member_scores
        if not _cluster_within_radius(matrix[member_indexes], member_distances):
            raise ValueError("semantic unit exceeded the full-population cosine radius")
        medoid_index = int(member_indexes[int(np.argmax(member_scores))])
        semantic_unit_id = stable_id(
            "SUNIT",
            SEMANTIC_SPLITTER_VERSION,
            category,
            primary_cell_id,
            evidence_polarity,
            rows[medoid_index].record_id,
            cluster_remap[old_index],
            length=20,
        )
        outlier_ids = tuple(
            sorted(
                rows[int(index)].record_id
                for index in member_indexes
                if stratum_distances[int(index)] > SPLIT_MAX_DISTANCE
            )
        )
        builds.append(
            _UnitBuild(
                semantic_unit_id=semantic_unit_id,
                category=category,
                primary_cell_id=primary_cell_id,
                evidence_polarity=evidence_polarity,
                member_record_ids=member_ids,
                outlier_record_ids=outlier_ids,
                member_record_root=sha256_text(canonical_json(member_ids)),
                provenance_root=sha256_text(canonical_json(source_pairs)),
                centroid=tuple(float(value) for value in centroid),
            )
        )
        for member_position, index in enumerate(member_indexes):
            record = rows[int(index)]
            assignment_rows.append(
                (
                    record.record_id,
                    semantic_unit_id,
                    "[]",
                    canonical_json(
                        [
                            SEMANTIC_SPLITTER_VERSION,
                            category,
                            primary_cell_id,
                            evidence_polarity,
                        ]
                    ),
                    float(member_distances[member_position]),
                    record.record_id in set(outlier_ids),
                )
            )
    return builds, assignment_rows


def _recursive_semantic_clusters(
    matrix: npt.NDArray[np.float32],
    indexes: npt.NDArray[np.int64],
    *,
    depth: int,
) -> list[npt.NDArray[np.int64]]:
    vectors = matrix[indexes]
    centroid = _normalized_mean(vectors)
    distances = 1.0 - vectors @ centroid
    if _cluster_within_radius(vectors, distances):
        return [indexes]
    maximum_depth = math.ceil(math.log2(max(2, len(matrix)))) + SPLIT_DEPTH_MARGIN
    if depth >= maximum_depth:
        raise ValueError(
            "semantic stratum exceeded its population-derived recursive split depth; "
            "no truncation applied"
        )
    if len(indexes) == 1:
        raise ValueError("single-record semantic unit has an invalid embedding")
    first = int(np.argmax(distances))
    second = int(np.argmin(vectors @ vectors[first]))
    left_seed = vectors[first]
    right_seed = vectors[second]
    left_mask = vectors @ left_seed >= vectors @ right_seed
    if bool(np.all(left_mask)) or bool(np.all(~left_mask)):
        if bool(np.allclose(vectors, vectors[0], rtol=0.0, atol=1e-6)):
            return [indexes]
        order = np.argsort(vectors @ (left_seed - right_seed), kind="stable")
        left_mask = np.zeros(len(indexes), dtype=bool)
        left_mask[order[len(order) // 2 :]] = True
        if bool(np.all(left_mask)) or bool(np.all(~left_mask)):
            raise ValueError("semantic stratum could not be split without truncation")
    return [
        *_recursive_semantic_clusters(matrix, indexes[left_mask], depth=depth + 1),
        *_recursive_semantic_clusters(matrix, indexes[~left_mask], depth=depth + 1),
    ]


def _cluster_within_radius(
    vectors: npt.NDArray[np.float32],
    distances: npt.NDArray[np.float32],
) -> bool:
    if bool(np.allclose(vectors, vectors[0], rtol=0.0, atol=1e-6)):
        return True
    return bool(
        float(np.quantile(distances, 0.90)) <= SPLIT_P90_DISTANCE
        and float(np.max(distances)) <= SPLIT_MAX_DISTANCE
    )


def _load_unit_prompt_rows(
    connection: duckdb.DuckDBPyConnection,
    *,
    unit_builds: list[_UnitBuild],
) -> list[dict[str, Any]]:
    by_id = {row.semantic_unit_id: row for row in unit_builds}
    representatives = _representative_rows(connection)
    reps_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in representatives:
        document = str(row[8])
        reps_by_unit[str(row[0])].append(
            {
                "record_id": str(row[1]),
                "reason": str(row[2]),
                "record_type": str(row[3]),
                "label_quality": str(row[4]),
                "training_eligible": bool(row[5]),
                "trade_date": str(row[6]),
                "available_from": str(row[7]),
                "document": document,
                "document_sha256": sha256_text(document),
                "exact_excerpt": document[:800],
                "source_sha256": str(row[9]),
            }
        )
    distributions = _unit_distributions(connection)
    rows: list[dict[str, Any]] = []
    for unit_id in sorted(by_id):
        build = by_id[unit_id]
        stats = distributions[unit_id]
        reps = reps_by_unit[unit_id]
        if not reps:
            raise ValueError(f"semantic unit has no representative: {unit_id}")
        represented_ids = {str(row["record_id"]) for row in reps}
        if build.outlier_record_ids and not represented_ids.intersection(build.outlier_record_ids):
            raise ValueError(f"semantic unit omitted its outlier representative: {unit_id}")
        rows.append(
            {
                "semantic_unit_id": unit_id,
                "category": build.category,
                "primary_cell_id": build.primary_cell_id,
                "evidence_polarity": build.evidence_polarity,
                "member_record_count": len(build.member_record_ids),
                "member_independent_unit_count": stats["independent_unit_count"],
                "member_record_root": build.member_record_root,
                "provenance_root": build.provenance_root,
                "centroid": list(build.centroid),
                "record_type_distribution": stats["record_type_distribution"],
                "polarity_distribution": stats["polarity_distribution"],
                "label_quality_distribution": stats["label_quality_distribution"],
                "time_distribution": stats["time_distribution"],
                "regime_distribution": stats["regime_distribution"],
                "available_from": stats["max_available_from"],
                "outlier_record_ids": list(build.outlier_record_ids),
                "representatives": reps,
            }
        )
    return rows


def _plan_long_payloads(unit_rows: list[dict[str, Any]]) -> _LongPayloadPlan:
    projected_rows: list[dict[str, Any]] = []
    chunk_inputs: list[dict[str, Any]] = []
    representative_count = 0
    payload_chars = 0
    oversized_units = 0
    chunked_records = 0
    for row in unit_rows:
        representatives = [dict(value) for value in row["representatives"]]
        representative_count += len(representatives)
        payload_chars += sum(len(str(value["document"])) for value in representatives)
        if len(canonical_json(row).encode("utf-8")) <= MAX_LEAF_PROMPT_BYTES:
            projected_rows.append(row)
            continue
        oversized_units += 1
        chunked_records += len(representatives)
        projected_representatives: list[dict[str, Any]] = []
        for representative in representatives:
            document = str(representative.pop("document"))
            chunks = _utf8_chunks(document, max_bytes=MAX_LONG_PAYLOAD_CHUNK_BYTES)
            document_sha = str(representative["document_sha256"])
            digest_placeholders: list[dict[str, Any]] = []
            for chunk_index, chunk_text in enumerate(chunks):
                chunk_sha = sha256_text(chunk_text)
                chunk_id = stable_id(
                    "LONG-CHUNK",
                    row["semantic_unit_id"],
                    representative["record_id"],
                    document_sha,
                    chunk_index,
                    chunk_sha,
                    length=20,
                )
                chunk_input = {
                    "chunk_id": chunk_id,
                    "semantic_unit_id": str(row["semantic_unit_id"]),
                    "record_id": str(representative["record_id"]),
                    "chunk_index": chunk_index,
                    "chunk_count": len(chunks),
                    "document_sha256": document_sha,
                    "chunk_sha256": chunk_sha,
                    "text": chunk_text,
                }
                chunk_inputs.append(chunk_input)
                digest_placeholders.append(
                    {
                        key: value
                        for key, value in chunk_input.items()
                        if key != "text"
                    }
                    | {
                        "summary": "full payload chunk digest",
                        "material_facts": [],
                        "mechanisms": [],
                        "entities": [],
                        "numeric_and_time_facts": [],
                        "caveats": [],
                    }
                )
            representative["original_document_chars"] = len(document)
            representative["full_payload_chunk_digests"] = digest_placeholders
            representative["full_payload_read"] = True
            projected_representatives.append(representative)
        projected_rows.append(
            {
                **row,
                "representatives": projected_representatives,
                "payload_projection": "FULL_CHUNK_MAP_THEN_LEAF",
            }
        )
    chunk_batches = list(_pack_long_payload_chunks(chunk_inputs))
    return _LongPayloadPlan(
        projected_rows=projected_rows,
        chunk_inputs=chunk_inputs,
        representative_record_count=representative_count,
        representative_payload_char_count=payload_chars,
        oversized_unit_count=oversized_units,
        chunked_representative_record_count=chunked_records,
        long_payload_chunk_count=len(chunk_inputs),
        long_payload_chunk_map_call_count=len(chunk_batches),
    )


def _representative_payload_exposure_rows(
    payload_plan: _LongPayloadPlan,
) -> list[dict[str, Any]]:
    chunk_node_ids: dict[str, str] = {}
    for batch in _pack_long_payload_chunks(payload_plan.chunk_inputs):
        node_id = stable_id(
            "LONG-PAYLOAD-MAP",
            [row["chunk_id"] for row in batch],
            [row["chunk_sha256"] for row in batch],
            length=20,
        )
        for chunk in batch:
            chunk_node_ids[str(chunk["chunk_id"])] = node_id
    rows: list[dict[str, Any]] = []
    for unit in payload_plan.projected_rows:
        for representative in unit["representatives"]:
            digests = representative.get("full_payload_chunk_digests")
            chunk_rows = digests if isinstance(digests, list) else []
            rows.append(
                {
                    "semantic_unit_id": str(unit["semantic_unit_id"]),
                    "record_id": str(representative["record_id"]),
                    "reason": str(representative["reason"]),
                    "document_sha256": str(representative["document_sha256"]),
                    "source_sha256": str(representative["source_sha256"]),
                    "document_char_count": int(
                        representative.get(
                            "original_document_chars",
                            len(str(representative.get("document", ""))),
                        )
                    ),
                    "exposure_mode": (
                        "FULL_CHUNK_MAP_THEN_LEAF"
                        if chunk_rows
                        else "DIRECT_FULL_PAYLOAD_IN_LEAF"
                    ),
                    "chunk_ids": [str(value["chunk_id"]) for value in chunk_rows],
                    "chunk_sha256s": [str(value["chunk_sha256"]) for value in chunk_rows],
                    "chunk_map_node_ids": sorted(
                        {
                            chunk_node_ids[str(value["chunk_id"])]
                            for value in chunk_rows
                        }
                    ),
                    "truncated": False,
                }
            )
    rows.sort(key=lambda row: (row["record_id"], row["semantic_unit_id"]))
    if len(rows) != payload_plan.representative_record_count:
        raise ValueError("representative payload exposure ledger count drifted")
    if len({str(row["record_id"]) for row in rows}) != len(rows):
        raise ValueError("representative payload exposure ledger contains duplicate records")
    return rows


def _utf8_chunks(text: str, *, max_bytes: int) -> list[str]:
    if max_bytes < 1:
        raise ValueError("long payload chunk byte limit must be positive")
    encoded = text.encode("utf-8")
    if not encoded:
        return [""]
    chunks: list[str] = []
    start = 0
    while start < len(encoded):
        end = min(start + max_bytes, len(encoded))
        while end > start:
            try:
                chunk = encoded[start:end].decode("utf-8")
            except UnicodeDecodeError:
                end -= 1
                continue
            chunks.append(chunk)
            start = end
            break
        else:
            raise ValueError("long payload could not be split on a UTF-8 boundary")
    if "".join(chunks) != text:
        raise ValueError("long payload chunking changed source text")
    return chunks


def _pack_long_payload_chunks(
    chunks: list[dict[str, Any]],
) -> Iterable[list[dict[str, Any]]]:
    current: list[dict[str, Any]] = []
    for chunk in chunks:
        candidate = [*current, chunk]
        payload = {"chunks": candidate}
        if current and len(canonical_json(payload).encode("utf-8")) > MAX_LONG_PAYLOAD_BATCH_BYTES:
            yield current
            current = [chunk]
        else:
            current = candidate
        if len(canonical_json({"chunks": current}).encode("utf-8")) > MAX_LONG_PAYLOAD_BATCH_BYTES:
            raise ValueError("single long payload chunk exceeds its explicit map budget")
    if current:
        yield current


def _representative_rows(connection: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    return connection.execute(
        """
            WITH joined AS (
                SELECT a.primary_semantic_unit_id AS unit_id, a.distance, a.outlier,
                       r.record_id, r.record_type, r.label_quality, r.training_eligible,
                       r.trade_date, r.available_from, r.document, r.source_sha256,
                       r.high_return_status, r.close_return_status, r.upper_limit_status
                FROM semantic_unit_assignments a
                JOIN source_memory.records r USING (record_id)
            ), chosen AS (
                SELECT unit_id, record_id, 'MEDOID' AS reason FROM joined
                QUALIFY row_number() OVER (PARTITION BY unit_id ORDER BY distance, record_id) = 1
                UNION
                SELECT unit_id, record_id, 'BOUNDARY' AS reason FROM joined
                QUALIFY row_number() OVER (PARTITION BY unit_id ORDER BY distance DESC, record_id) = 1
                UNION
                SELECT unit_id, record_id, 'OUTLIER' AS reason FROM joined WHERE outlier
                QUALIFY row_number() OVER (PARTITION BY unit_id ORDER BY distance DESC, record_id) = 1
                UNION
                SELECT unit_id, record_id, 'EARLIEST' AS reason FROM joined
                QUALIFY row_number() OVER (PARTITION BY unit_id ORDER BY trade_date, record_id) = 1
                UNION
                SELECT unit_id, record_id, 'LATEST' AS reason FROM joined
                QUALIFY row_number() OVER (PARTITION BY unit_id ORDER BY trade_date DESC, record_id) = 1
                UNION
                SELECT unit_id, record_id, 'RECORD_TYPE:' || record_type AS reason FROM joined
                QUALIFY row_number() OVER (PARTITION BY unit_id, record_type ORDER BY distance, record_id) = 1
                UNION
                SELECT unit_id, record_id, 'LABEL:' || label_quality AS reason FROM joined
                QUALIFY row_number() OVER (PARTITION BY unit_id, label_quality ORDER BY distance, record_id) = 1
                UNION
                SELECT unit_id, record_id, 'TRAINING:' || training_eligible::VARCHAR AS reason FROM joined
                QUALIFY row_number() OVER (PARTITION BY unit_id, training_eligible ORDER BY distance, record_id) = 1
                UNION
                SELECT unit_id, record_id, 'HIGH_STATUS:' || high_return_status AS reason FROM joined
                QUALIFY row_number() OVER (PARTITION BY unit_id, high_return_status ORDER BY distance, record_id) = 1
                UNION
                SELECT unit_id, record_id, 'UPPER_STATUS:' || upper_limit_status AS reason FROM joined
                QUALIFY row_number() OVER (PARTITION BY unit_id, upper_limit_status ORDER BY distance, record_id) = 1
            )
            SELECT c.unit_id, c.record_id, string_agg(c.reason, ',' ORDER BY c.reason),
                   any_value(j.record_type), any_value(j.label_quality),
                   any_value(j.training_eligible), any_value(j.trade_date),
                   any_value(j.available_from), any_value(j.document), any_value(j.source_sha256)
            FROM chosen c JOIN joined j USING (unit_id, record_id)
            GROUP BY c.unit_id, c.record_id
            ORDER BY c.unit_id, c.record_id
            """
    ).fetchall()


def _unit_distributions(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "record_type_distribution": {},
            "polarity_distribution": {},
            "label_quality_distribution": {},
            "time_distribution": {},
            "regime_distribution": {},
            "independent_unit_count": 0,
            "max_available_from": None,
        }
    )
    dimensions = {
        "record_type_distribution": "record_type",
        "polarity_distribution": "evidence_polarity",
        "label_quality_distribution": "label_quality",
        "time_distribution": "year(trade_date)::VARCHAR",
        "regime_distribution": "COALESCE(regime_cluster, 'UNKNOWN')",
    }
    for target, expression in dimensions.items():
        rows = connection.execute(
            f"""
            SELECT a.primary_semantic_unit_id, {expression} AS value, count(*)
            FROM semantic_unit_assignments a JOIN source_memory.records r USING (record_id)
            GROUP BY 1, 2
            """
        ).fetchall()
        for unit_id, value, count in rows:
            output[str(unit_id)][target][str(value)] = int(count)
    for unit_id, independent_count, max_available in connection.execute(
        """
        SELECT a.primary_semantic_unit_id, count(DISTINCT r.independent_unit_id),
               max(r.available_from)
        FROM semantic_unit_assignments a JOIN source_memory.records r USING (record_id)
        GROUP BY 1
        """
    ).fetchall():
        output[str(unit_id)]["independent_unit_count"] = int(independent_count)
        output[str(unit_id)]["max_available_from"] = str(max_available)
    return dict(output)


def _pack_leaf_rows(rows: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    current: list[dict[str, Any]] = []
    current_bytes = 0
    for row in rows:
        row_bytes = _leaf_row_budget_bytes(row)
        if row_bytes > MAX_LEAF_PROMPT_BYTES:
            raise ValueError("one semantic unit exceeds the leaf budget after full chunk mapping")
        if current and (
            len(current) + 1 > MAX_LEAF_OUTPUT_UNITS
            or current_bytes + row_bytes > MAX_LEAF_PROMPT_BYTES
        ):
            yield current
            current = [row]
            current_bytes = row_bytes
        else:
            current.append(row)
            current_bytes += row_bytes
    if current:
        yield current


def _leaf_row_budget_bytes(row: dict[str, Any]) -> int:
    digest_count = 0
    projected_representatives: list[dict[str, Any]] = []
    for representative in row["representatives"]:
        projected = dict(representative)
        digests = projected.get("full_payload_chunk_digests")
        if isinstance(digests, list):
            digest_count += len(digests)
            projected["full_payload_chunk_digests"] = []
        projected_representatives.append(projected)
    base = len(
        canonical_json({**row, "representatives": projected_representatives}).encode("utf-8")
    )
    budget = base + digest_count * MAX_LONG_PAYLOAD_DIGEST_BUDGET_BYTES
    actual = len(canonical_json(row).encode("utf-8"))
    if actual > budget:
        raise ValueError("long payload digest exceeded its declared leaf budget")
    return budget


def _capsule_leaf_nodes(
    capsules: list[SemanticMemoryCapsule],
) -> list[_LeafNode]:
    buckets: dict[tuple[str, str], list[SemanticMemoryCapsule]] = defaultdict(list)
    for capsule in capsules:
        bucket = sha256_text(capsule.capsule_id)[:2]
        buckets[(capsule.category, bucket)].append(capsule)
    nodes: list[_LeafNode] = []
    for (category, bucket), rows in sorted(buckets.items()):
        ordered = sorted(rows, key=lambda row: row.capsule_id)
        capsule_ids = tuple(row.capsule_id for row in ordered)
        nodes.append(
            _LeafNode(
                node_id=stable_id(
                    "LEAF-BUCKET",
                    category,
                    bucket,
                    capsule_ids,
                    length=20,
                ),
                category=category,
                capsule_ids=capsule_ids,
                synthesis=" | ".join(row.event_or_mechanism_summary for row in ordered),
            )
        )
    return nodes


def _pack_reduce_nodes(
    nodes: list[SemanticReduceNode],
) -> Iterable[list[SemanticReduceNode]]:
    current: list[SemanticReduceNode] = []
    for node in nodes:
        candidate = [*current, node]
        payload = [row.model_dump(mode="json") for row in candidate]
        if current and (
            len(candidate) > MAX_REDUCE_CHILDREN
            or len(canonical_json(payload).encode("utf-8")) > MAX_REDUCE_PROMPT_BYTES
        ):
            yield current
            current = [node]
        else:
            current = candidate
    if current:
        yield current


def _reduce_call_count(child_count: int) -> int:
    calls = 0
    current = child_count
    while current > 1:
        current = math.ceil(current / MAX_REDUCE_CHILDREN)
        calls += current
    return calls


def _long_payload_prompt(
    *,
    node_id: str,
    chunks: list[dict[str, Any]],
) -> str:
    payload = {
        "prompt_version": LONG_PAYLOAD_PROMPT_VERSION,
        "node_id": node_id,
        "required_chunk_ids": [row["chunk_id"] for row in chunks],
        "chunks": chunks,
    }
    return (
        "You are performing one-time offline semantic digestion of complete representative "
        "payload chunks. Read every supplied chunk. Preserve material facts, mechanisms, "
        "entities, numeric/time facts, and caveats without inventing missing context. Return "
        "one semantic digest for every required_chunk_id exactly once. Return chunk_id only "
        "for association; source record identity, ordering, and hashes are attached from the "
        "immutable input ledger by the compiler and must not be generated."
        "\n---OFFLINE_LONG_PAYLOAD_MAP---\n"
        + canonical_json(payload)
    )


def _materialize_long_payload_chunk_digest(
    *,
    source_row: dict[str, Any],
    draft: LongPayloadChunkDigestDraft,
) -> LongPayloadChunkDigest:
    if draft.chunk_id != str(source_row["chunk_id"]):
        raise ValueError("long payload digest chunk identity drifted")
    return LongPayloadChunkDigest(
        **draft.model_dump(mode="python"),
        semantic_unit_id=str(source_row["semantic_unit_id"]),
        record_id=str(source_row["record_id"]),
        chunk_index=int(source_row["chunk_index"]),
        chunk_count=int(source_row["chunk_count"]),
        document_sha256=str(source_row["document_sha256"]),
        chunk_sha256=str(source_row["chunk_sha256"]),
    )


def _leaf_prompt(
    *,
    node_id: str,
    category: str,
    rows: list[dict[str, Any]],
) -> str:
    payload = {
        "schema": LEAF_PROMPT_VERSION,
        "node_id": node_id,
        "category": category,
        "required_semantic_unit_ids": [row["semantic_unit_id"] for row in rows],
        "semantic_units": rows,
    }
    return (
        "Map every semantic unit into one SemanticCapsuleDraft. Use all dynamic "
        "representatives, population distributions, boundary and outlier evidence. "
        "Do not infer mechanisms from counts or hashes alone. Preserve positive, negative, "
        "near-miss, counterexample, newsless, and error distinctions. Return every required "
        "semantic_unit_id exactly once.\n---OFFLINE_SEMANTIC_LEAF---\n" + canonical_json(payload)
    )


def _reduce_prompt(
    *,
    node_id: str,
    category: str,
    children: list[SemanticReduceNode],
    review: bool,
) -> str:
    payload = {
        "schema": CATEGORY_REVIEW_PROMPT_VERSION if review else REDUCE_PROMPT_VERSION,
        "node_id": node_id,
        "category": category,
        "review": review,
        "required_child_node_ids": [row.node_id for row in children],
        "required_capsule_ids": _unique(capsule_id for row in children for capsule_id in row.covered_capsule_ids),
        "children": [row.model_dump(mode="json") for row in children],
    }
    return (
        "Reduce every child without omission. Synthesize mechanisms, applicable conditions, "
        "failure boundaries, contradictions, and concise mechanism claims. Claims must cite "
        "only provided capsule IDs. Return child_node_ids in the required order and cover "
        "every required capsule ID.\n---OFFLINE_SEMANTIC_REDUCE---\n" + canonical_json(payload)
    )


def _materialize_capsule(
    row: dict[str, Any],
    *,
    draft: Any,
) -> SemanticMemoryCapsule:
    representatives = row["representatives"]
    representative_ids = [str(value["record_id"]) for value in representatives]
    record_types = set(row["record_type_distribution"])
    polarity = str(row["evidence_polarity"]).upper()
    supporting = representative_ids if polarity == "POSITIVE" else []
    contradicting = representative_ids if polarity == "NEGATIVE" else []
    near_miss = (
        representative_ids if record_types.intersection({"negative_control_case", "timing_impossible_case"}) else []
    )
    counterexamples = representative_ids if "counterexample" in record_types else []
    unexplained = representative_ids if "newsless_or_unexplained_case" in record_types else []
    errors = representative_ids if record_types.intersection(_ERROR_RECORD_TYPES) else []
    available_from = datetime.fromisoformat(str(row["available_from"]))
    witnesses = [
        ExactWitness(
            record_id=str(value["record_id"]),
            excerpt=str(value["exact_excerpt"]),
            available_from=datetime.fromisoformat(str(value["available_from"])),
            provenance_root=str(value["source_sha256"]),
        )
        for value in representatives
    ]
    capsule_id = stable_id(
        "CAP",
        row["semantic_unit_id"],
        draft.model_dump(mode="json"),
        row["member_record_root"],
        length=20,
    )
    return SemanticMemoryCapsule(
        capsule_id=capsule_id,
        category=str(row["category"]),
        semantic_unit_id=str(row["semantic_unit_id"]),
        member_record_count=int(row["member_record_count"]),
        member_independent_unit_count=int(row["member_independent_unit_count"]),
        member_record_root=str(row["member_record_root"]),
        record_type_distribution=dict(row["record_type_distribution"]),
        polarity_distribution=dict(row["polarity_distribution"]),
        label_quality_distribution=dict(row["label_quality_distribution"]),
        time_distribution=dict(row["time_distribution"]),
        regime_distribution=dict(row["regime_distribution"]),
        event_or_mechanism_summary=draft.event_or_mechanism_summary,
        economic_transmission=draft.economic_transmission,
        market_narrative=draft.market_narrative,
        applicable_conditions=draft.applicable_conditions,
        failure_conditions=draft.failure_conditions,
        boundary_conditions=draft.boundary_conditions,
        novelty_modality_distinctions=draft.novelty_modality_distinctions,
        leader_selection_implications=draft.leader_selection_implications,
        beneficiary_implications=draft.beneficiary_implications,
        continuation_implications=draft.continuation_implications,
        supporting_record_ids=supporting,
        contradicting_record_ids=contradicting,
        near_miss_record_ids=near_miss,
        counterexample_record_ids=counterexamples,
        newsless_or_unexplained_record_ids=unexplained,
        error_record_ids=errors,
        representative_exact_witnesses=witnesses,
        available_from=available_from,
        provenance_root=str(row["provenance_root"]),
        embedding=list(row["centroid"]),
    )


def _claims_from_reduce_node(
    node: SemanticReduceNode,
    *,
    category: str,
    capsules: list[SemanticMemoryCapsule],
) -> list[SynthesizedMechanismClaim]:
    by_id = {row.capsule_id: row for row in capsules}
    output: list[SynthesizedMechanismClaim] = []
    for draft in node.claims:
        supporting_ids = [value for value in draft.supporting_capsule_ids if value in by_id]
        contradicting_ids = [value for value in draft.contradicting_capsule_ids if value in by_id]
        if not supporting_ids and node.covered_capsule_ids:
            supporting_ids = [value for value in node.covered_capsule_ids if value in by_id][:1]
        referenced = [by_id[value] for value in [*supporting_ids, *contradicting_ids]]
        if not referenced:
            continue
        embedding = _normalized_mean(np.asarray([row.embedding for row in referenced], dtype=np.float32))
        available_from = max(row.available_from for row in referenced)
        output.append(
            SynthesizedMechanismClaim(
                claim_id=stable_id(
                    "MCLAIM",
                    category,
                    node.node_id,
                    draft.model_dump(mode="json"),
                    length=20,
                ),
                category=category,
                statement=draft.statement,
                mechanism=draft.mechanism,
                conditions=draft.conditions,
                boundary_conditions=draft.boundary_conditions,
                failure_modes=draft.failure_modes,
                supporting_capsule_ids=supporting_ids,
                contradicting_capsule_ids=contradicting_ids,
                supporting_record_ids=_unique(
                    record_id for capsule_id in supporting_ids for record_id in by_id[capsule_id].supporting_record_ids
                ),
                contradicting_record_ids=_unique(
                    record_id
                    for capsule_id in contradicting_ids
                    for record_id in [
                        *by_id[capsule_id].contradicting_record_ids,
                        *by_id[capsule_id].counterexample_record_ids,
                    ]
                ),
                source_node_ids=[node.node_id],
                available_from=available_from,
                confidence=draft.confidence,
                status=draft.status,
                embedding=[float(value) for value in embedding],
            )
        )
    return output


def _dedupe_claims(
    claims: list[SynthesizedMechanismClaim],
) -> list[SynthesizedMechanismClaim]:
    by_key: dict[str, SynthesizedMechanismClaim] = {}
    for claim in claims:
        key = sha256_text(
            canonical_json(
                {
                    "category": claim.category,
                    "statement": claim.statement,
                    "mechanism": claim.mechanism,
                    "supporting_capsule_ids": claim.supporting_capsule_ids,
                    "contradicting_capsule_ids": claim.contradicting_capsule_ids,
                }
            )
        )
        by_key.setdefault(key, claim)
    return [by_key[key] for key in sorted(by_key)]


def _write_capsules_to_database(
    connection: duckdb.DuckDBPyConnection,
    capsules: list[SemanticMemoryCapsule],
) -> None:
    connection.executemany(
        "INSERT INTO semantic_capsules VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                row.capsule_id,
                row.category,
                row.semantic_unit_id,
                row.available_from.isoformat(),
                row.embedding,
                canonical_json(row.model_dump(mode="json")),
            )
            for row in capsules
        ],
    )


def _write_claims_to_database(
    connection: duckdb.DuckDBPyConnection,
    claims: list[SynthesizedMechanismClaim],
) -> None:
    if not claims:
        raise ValueError("offline brain produced no synthesized mechanism claims")
    connection.executemany(
        "INSERT INTO mechanism_claims VALUES (?, ?, ?, ?, ?)",
        [
            (
                row.claim_id,
                row.category,
                row.available_from.isoformat(),
                row.embedding,
                canonical_json(row.model_dump(mode="json")),
            )
            for row in claims
        ],
    )
    relationships = [
        (row.claim_id, capsule_id, role)
        for row in claims
        for role, capsule_ids in (
            ("SUPPORTING", row.supporting_capsule_ids),
            ("CONTRADICTING", row.contradicting_capsule_ids),
        )
        for capsule_id in capsule_ids
    ]
    if not relationships:
        raise ValueError("offline brain claims have no capsule provenance")
    connection.executemany(
        "INSERT INTO mechanism_claim_capsules VALUES (?, ?, ?)",
        relationships,
    )


def _write_reduce_nodes_to_database(
    connection: duckdb.DuckDBPyConnection,
    nodes: list[SemanticReduceNode],
) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO reduce_nodes VALUES (?, ?)",
        [(row.node_id, canonical_json(row.model_dump(mode="json"))) for row in nodes],
    )


def _finalize_package_database(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE INDEX semantic_capsule_category_idx ON semantic_capsules(category)")
    connection.execute("CREATE INDEX semantic_capsule_available_idx ON semantic_capsules(available_from)")
    connection.execute("CREATE INDEX mechanism_claim_category_idx ON mechanism_claims(category)")
    connection.execute(
        "CREATE INDEX mechanism_claim_capsule_idx ON mechanism_claim_capsules(capsule_id)"
    )
    connection.execute("INSTALL vss")
    connection.execute("LOAD vss")
    connection.execute("SET hnsw_enable_experimental_persistence = true")
    connection.execute(
        "CREATE INDEX semantic_capsules_hnsw_idx ON semantic_capsules USING HNSW (embedding) "
        "WITH (metric = 'cosine')"
    )
    connection.execute(
        "CREATE INDEX mechanism_claims_hnsw_idx ON mechanism_claims USING HNSW (embedding) "
        "WITH (metric = 'cosine')"
    )
    _assert_hnsw_query_plan(
        connection,
        table="semantic_capsules",
        id_column="capsule_id",
        embedding_column="embedding",
    )
    _assert_hnsw_query_plan(
        connection,
        table="mechanism_claims",
        id_column="claim_id",
        embedding_column="embedding",
    )
    connection.execute("CHECKPOINT")


def _assert_hnsw_query_plan(
    connection: duckdb.DuckDBPyConnection,
    *,
    table: str,
    id_column: str,
    embedding_column: str,
) -> None:
    probe = [1.0, *([0.0] * 383)]
    row = connection.execute(
        f"EXPLAIN SELECT {id_column} FROM {table} "
        f"ORDER BY array_cosine_distance({embedding_column}, ?::FLOAT[384]) LIMIT 24",
        [probe],
    ).fetchone()
    if row is None or "HNSW_INDEX_SCAN" not in str(row[1]):
        raise RuntimeError(f"{table} daily query does not use its HNSW index")


def _build_influence_manifest(
    connection: duckdb.DuckDBPyConnection,
    *,
    brain_version: str,
    unit_builds: list[_UnitBuild],
    capsules: list[SemanticMemoryCapsule],
    world_root: SemanticReduceNode,
    representative_payload_char_count: int,
    representative_payload_full_read_count: int,
    chunked_representative_record_count: int,
    long_payload_chunk_count: int,
    representative_payload_read_root: str,
) -> SemanticInfluenceManifest:
    count_row = connection.execute(
        "SELECT count(*), count(DISTINCT record_id) FROM semantic_unit_assignments"
    ).fetchone()
    if count_row is None:
        raise ValueError("semantic influence count query returned no row")
    record_count, distinct_count = count_row
    duplicate_count = int(record_count) - int(distinct_count)
    outlier_unit_ids = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT primary_semantic_unit_id FROM semantic_unit_assignments WHERE outlier"
        ).fetchall()
    }
    reasoning_units = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT a.primary_semantic_unit_id
            FROM semantic_unit_assignments a
            JOIN source_memory.records r USING (record_id)
            WHERE r.routing_disposition = 'REASONING'
            """
        ).fetchall()
    }
    capsule_units = {row.semantic_unit_id for row in capsules}
    assignment_rows = connection.execute(
        "SELECT record_id, primary_semantic_unit_id, outlier FROM semantic_unit_assignments ORDER BY record_id"
    ).fetchall()
    representative_pairs = sorted(
        (row.semantic_unit_id, witness.record_id) for row in capsules for witness in row.representative_exact_witnesses
    )
    representative_record_count = len({record_id for _, record_id in representative_pairs})
    return SemanticInfluenceManifest(
        brain_version=brain_version,
        record_count=int(record_count),
        primary_assignment_count=int(record_count),
        distinct_primary_assigned_record_count=int(distinct_count),
        unassigned_record_count=0,
        duplicate_primary_assignment_count=duplicate_count,
        semantic_unit_count=len(unit_builds),
        rare_outlier_unit_count=len(outlier_unit_ids),
        rare_outlier_represented_unit_count=len(outlier_unit_ids.intersection(capsule_units)),
        unrepresented_reasoning_unit_count=len(reasoning_units - capsule_units),
        leaf_covered_semantic_unit_count=len(capsule_units),
        reduce_covered_capsule_count=len(world_root.covered_capsule_ids),
        final_covered_capsule_count=len(world_root.covered_capsule_ids),
        population_contribution_record_count=int(record_count),
        representative_payload_exposed_record_count=representative_record_count,
        representative_payload_not_exposed_record_count=(
            int(record_count) - representative_record_count
        ),
        representative_payload_exposure_ratio=(
            0.0 if int(record_count) == 0 else representative_record_count / int(record_count)
        ),
        representative_payload_char_count=representative_payload_char_count,
        representative_payload_full_read_count=representative_payload_full_read_count,
        representative_payload_truncated_count=0,
        chunked_representative_record_count=chunked_representative_record_count,
        long_payload_chunk_count=long_payload_chunk_count,
        record_membership_root=sha256_text(canonical_json(assignment_rows)),
        representative_record_root=sha256_text(canonical_json(representative_pairs)),
        representative_payload_read_root=representative_payload_read_root,
        leaf_coverage_root=sha256_text(canonical_json(sorted(capsule_units))),
        reduce_tree_root=sha256_text(canonical_json(sorted(world_root.covered_capsule_ids))),
    )


def _assignment_export_rows(package_dir: Path) -> Iterable[dict[str, Any]]:
    connection = duckdb.connect(str(package_dir / "semantic_capsule_index.duckdb"), read_only=True)
    try:
        cursor = connection.execute(
            """
            SELECT record_id, primary_semantic_unit_id,
                   secondary_semantic_unit_ids, assignment_basis, outlier
            FROM semantic_unit_assignments
            ORDER BY record_id
            """
        )
        while True:
            rows = cursor.fetchmany(4096)
            if not rows:
                break
            for row in rows:
                yield {
                    "record_id": str(row[0]),
                    "primary_semantic_unit_id": str(row[1]),
                    "secondary_semantic_unit_ids": json.loads(str(row[2])),
                    "assignment_basis": json.loads(str(row[3])),
                    "outlier": bool(row[4]),
                }
    finally:
        connection.close()


def _write_category_brain(
    package_dir: Path,
    *,
    category_roots: dict[str, SemanticReduceNode],
    world_root: SemanticReduceNode,
) -> None:
    root = package_dir / "category_brain"
    root.mkdir()
    for category, node in sorted(category_roots.items()):
        (root / f"{category}.md").write_text(_reduce_node_markdown(category, node), encoding="utf-8", newline="\n")
    (package_dir / "world_model.md").write_text(
        _reduce_node_markdown("world_model", world_root),
        encoding="utf-8",
        newline="\n",
    )


def _reduce_node_markdown(category: str, node: SemanticReduceNode) -> str:
    return "\n".join(
        [
            f"# {category}",
            "",
            node.synthesis,
            "",
            "## Mechanisms",
            *[f"- {value}" for value in node.mechanisms],
            "",
            "## Conditions",
            *[f"- {value}" for value in node.conditions],
            "",
            "## Boundaries And Failures",
            *[f"- {value}" for value in [*node.boundary_conditions, *node.failure_modes]],
            "",
            f"Covered capsules: {len(node.covered_capsule_ids)}",
            "",
        ]
    )


def _write_population_cube(
    package_dir: Path,
    *,
    capsules: list[SemanticMemoryCapsule],
) -> None:
    root = package_dir / "population_cube"
    root.mkdir()
    _write_jsonl(
        root / "capsule_populations.jsonl",
        [
            {
                "capsule_id": row.capsule_id,
                "member_record_root": row.member_record_root,
                "member_record_count": row.member_record_count,
                "member_independent_unit_count": row.member_independent_unit_count,
                "record_type_distribution": row.record_type_distribution,
                "polarity_distribution": row.polarity_distribution,
                "label_quality_distribution": row.label_quality_distribution,
                "time_distribution": row.time_distribution,
                "regime_distribution": row.regime_distribution,
            }
            for row in capsules
        ],
    )


def _write_graph_projections(
    package_dir: Path,
    *,
    capsules: list[SemanticMemoryCapsule],
) -> None:
    projections = {
        "beneficiary_graph": [
            {
                "capsule_id": row.capsule_id,
                "implications": row.beneficiary_implications,
            }
            for row in capsules
            if row.beneficiary_implications
        ],
        "leader_selection_memory": [
            {
                "capsule_id": row.capsule_id,
                "implications": row.leader_selection_implications,
            }
            for row in capsules
            if row.leader_selection_implications
        ],
        "continuation_memory": [
            {
                "capsule_id": row.capsule_id,
                "implications": row.continuation_implications,
            }
            for row in capsules
            if row.continuation_implications
        ],
    }
    for name, rows in projections.items():
        root = package_dir / name
        root.mkdir()
        write_json(
            root / "summary.json",
            {"schema_version": f"nslab.{name}.v1", "rows": rows},
        )


def _balanced_capsule_ids(
    connection: duckdb.DuckDBPyConnection,
    *,
    capsule_scores: dict[str, float],
    limit: int,
) -> list[str]:
    ordered = sorted(capsule_scores, key=lambda key: (-capsule_scores[key], key))
    payloads = {
        str(capsule_id): SemanticMemoryCapsule.model_validate_json(str(payload))
        for capsule_id, payload in connection.execute(
            "SELECT capsule_id, payload_json FROM semantic_capsules WHERE capsule_id IN (SELECT unnest(?))",
            [ordered],
        ).fetchall()
    }
    lanes: tuple[Callable[[SemanticMemoryCapsule], bool], ...] = (
        lambda row: bool(row.supporting_record_ids),
        lambda row: bool(row.contradicting_record_ids),
        lambda row: bool(row.near_miss_record_ids),
        lambda row: bool(row.counterexample_record_ids),
        lambda row: bool(row.newsless_or_unexplained_record_ids),
        lambda row: bool(row.error_record_ids),
        lambda row: bool(row.beneficiary_implications),
        lambda row: bool(row.leader_selection_implications),
        lambda row: bool(row.continuation_implications),
    )
    selected: list[str] = []
    for lane in lanes:
        candidate = next(
            (capsule_id for capsule_id in ordered if capsule_id not in selected and lane(payloads[capsule_id])),
            None,
        )
        if candidate is not None:
            selected.append(candidate)
    for capsule_id in ordered:
        if capsule_id not in selected:
            selected.append(capsule_id)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _selected_claims(
    connection: duckdb.DuckDBPyConnection,
    *,
    selected_capsule_ids: set[str],
    claim_scores: dict[str, float],
    limit: int,
) -> list[SynthesizedMechanismClaim]:
    linked_ids = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT claim_id FROM mechanism_claim_capsules "
            "WHERE capsule_id IN (SELECT unnest(?::VARCHAR[]))",
            [sorted(selected_capsule_ids)],
        ).fetchall()
    }
    candidate_ids = sorted(
        linked_ids | set(claim_scores),
        key=lambda claim_id: (
            claim_id not in linked_ids,
            -claim_scores.get(claim_id, -1.0),
            claim_id,
        ),
    )[:limit]
    if not candidate_ids:
        return []
    payloads = {
        str(claim_id): str(payload)
        for claim_id, payload in connection.execute(
            "SELECT claim_id, payload_json FROM mechanism_claims "
            "WHERE claim_id IN (SELECT unnest(?::VARCHAR[]))",
            [candidate_ids],
        ).fetchall()
    }
    return [
        SynthesizedMechanismClaim.model_validate_json(payloads[claim_id])
        for claim_id in candidate_ids
        if claim_id in payloads
    ]


def _daily_query_texts(
    interpretation: CurrentDayInterpretation,
    capsules: Sequence[CurrentEventCapsule],
) -> list[str]:
    values = [
        *interpretation.retrieval_queries,
        *interpretation.policy_industry_macro_mechanisms,
        *interpretation.beneficiary_paths,
        *[row.representative_title for row in capsules],
    ]
    return _unique(value for value in values if value.strip())


def _unique_witnesses(
    capsules: list[SemanticMemoryCapsule],
) -> list[ExactWitness]:
    output: list[ExactWitness] = []
    seen: set[str] = set()
    for capsule in capsules:
        for witness in capsule.representative_exact_witnesses:
            if witness.record_id not in seen:
                seen.add(witness.record_id)
                output.append(witness)
    return output


def _current_history_differences(
    interpretation: CurrentDayInterpretation,
    capsules: list[SemanticMemoryCapsule],
) -> list[str]:
    historical_conditions = {
        value for capsule in capsules for value in [*capsule.applicable_conditions, *capsule.boundary_conditions]
    }
    return [
        *[f"current uncertainty: {value}" for value in interpretation.uncertainties],
        *[f"historical boundary: {value}" for value in sorted(historical_conditions)],
    ]


def _projection_rows(
    path: Path,
    *,
    selected_capsule_ids: set[str],
) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise ValueError(f"invalid BrainPackage projection: {path}")
    return [row for row in payload["rows"] if isinstance(row, dict) and row.get("capsule_id") in selected_capsule_ids]


def _package_dir_from_pointer(project_root: Path) -> Path:
    pointer_path = project_root / "brain" / "current" / "brain_package_pointer.json"
    pointer = BrainPackagePointer.model_validate(read_json(pointer_path))
    package = (project_root / pointer.package_path).resolve()
    try:
        package.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError("BrainPackage pointer escapes project root") from exc
    manifest_path = package / "brain_package_manifest.json"
    if file_sha256(manifest_path) != pointer.manifest_sha256:
        raise ValueError("BrainPackage pointer manifest hash drifted")
    return package


def _category_case_sql() -> str:
    record_type_categories: dict[str, str] = {}
    for category in _CATEGORY_PRECEDENCE:
        for record_type in CATEGORY_RECORD_TYPE_ROUTES[category]:
            record_type_categories.setdefault(record_type, category)
    clauses = [
        f"WHEN record_type = '{record_type}' THEN '{category}'"
        for record_type, category in sorted(record_type_categories.items())
    ]
    return "CASE " + " ".join(clauses) + " ELSE 'world_model' END"


def _normalize_matrix(
    matrix: npt.NDArray[np.float32],
) -> npt.NDArray[np.float32]:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return cast(npt.NDArray[np.float32], matrix / norms)


def _normalized_mean(
    matrix: npt.NDArray[np.float32],
) -> npt.NDArray[np.float32]:
    value = np.mean(matrix, axis=0, dtype=np.float64).astype(np.float32)
    norm = float(np.linalg.norm(value))
    if norm == 0.0:
        return cast(npt.NDArray[np.float32], value)
    normalized = value / norm
    return cast(npt.NDArray[np.float32], normalized)


def _unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _model_population_root(models: list[Any], *, key: str) -> str:
    rows = sorted(
        (
            str(getattr(model, key)),
            sha256_text(canonical_json(model.model_dump(mode="json"))),
        )
        for model in models
    )
    return sha256_text(canonical_json(rows))


def _artifact_root(package_dir: Path) -> str:
    excluded = {
        "brain_package_manifest.json",
        "build_receipt.json",
    }
    rows = [
        (path.relative_to(package_dir).as_posix(), file_sha256(path), path.stat().st_size)
        for path in sorted(package_dir.rglob("*"))
        if path.is_file() and path.name not in excluded
    ]
    return sha256_text(canonical_json(rows))


def _warehouse_root(source_project: Path) -> str:
    warehouse = source_project / "warehouse"
    if not warehouse.is_dir():
        return sha256_text("WAREHOUSE_UNAVAILABLE")
    rows = [(path.name, file_sha256(path), path.stat().st_size) for path in sorted(warehouse.glob("*.parquet"))]
    return sha256_text(canonical_json(rows))


def _write_jsonl(path: Path, rows: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            payload = row.model_dump(mode="json") if hasattr(row, "model_dump") else row
            handle.write(canonical_json(payload) + "\n")


def _write_offline_progress(
    path: Path,
    *,
    phase: str,
    processed_record_count: int,
    total_record_count: int,
    semantic_unit_count: int,
    stratum_count: int | None = None,
) -> None:
    write_json(
        path,
        {
            "schema_version": "nslab.offline_brain_progress.v1",
            "phase": phase,
            "processed_record_count": processed_record_count,
            "total_record_count": total_record_count,
            "record_progress_ratio": (
                1.0 if total_record_count == 0 else processed_record_count / total_record_count
            ),
            "semantic_unit_count": semantic_unit_count,
            "stratum_count": stratum_count,
            "updated_at": now_kst().isoformat(),
        },
    )


def _trace_offline_llm(
    settings: Settings,
    provider: LLMProvider,
    model_config: dict[str, Any],
) -> LLMProvider:
    if isinstance(provider, TracingLLMProvider):
        return provider
    return TracingLLMProvider(
        provider,
        trace_dir=settings.path(settings.output_dirs.traces),
        model_config={**model_config, "compiler_version": OFFLINE_COMPILER_VERSION},
        default_metadata={"compiler_version": OFFLINE_COMPILER_VERSION},
        max_retries=settings.llm.max_retries,
    )
