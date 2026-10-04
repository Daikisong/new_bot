"""One-time semantic brain compiler and bounded daily package reader."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, TypeVar, cast
from uuid import uuid4

import duckdb
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from news_scalping_lab.brain.compiler import CATEGORY_RECORD_TYPE_ROUTES
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.offline_brain import (
    BrainPackageManifest,
    BrainPackagePointer,
    CompiledBrainGuidance,
    CurrentDayInterpretation,
    CurrentEventCapsule,
    DailyBrainContext,
    ExactWitness,
    LongPayloadChunkDigest,
    LongPayloadChunkDigestDraft,
    LongPayloadDigestBatch,
    MechanismClaimDraft,
    OfflineCompileManifest,
    SemanticCapsuleDraftBatch,
    SemanticInfluenceManifest,
    SemanticMemoryCapsule,
    SemanticReduceChildIdentityNormalization,
    SemanticReduceCitationNormalization,
    SemanticReduceClaimDraft,
    SemanticReduceDraft,
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

OFFLINE_COMPILER_VERSION = "nslab.offline_semantic_brain.compiler.v6"
LEGACY_MAP_CHECKPOINT_COMPILER_VERSION = "nslab.offline_semantic_brain.compiler.v5"
SEMANTIC_SPLITTER_VERSION = "recursive_full_population_cosine_radius.v3"
LONG_PAYLOAD_PROMPT_VERSION = "offline_long_payload_chunk_map.v2"
LEAF_PROMPT_VERSION = "offline_semantic_unit_leaf_map.v2"
REDUCE_PROMPT_VERSION = "offline_semantic_reduce.v2"
CATEGORY_REVIEW_PROMPT_VERSION = "offline_semantic_category_review.v2"
WORLD_REDUCE_PROMPT_VERSION = "offline_semantic_world_reduce.v2"
MAX_LEAF_PROMPT_BYTES = 180_000
MAX_LEAF_OUTPUT_UNITS = 16
MAX_LONG_PAYLOAD_CHUNK_BYTES = 72_000
MAX_LONG_PAYLOAD_BATCH_BYTES = 170_000
MAX_LONG_PAYLOAD_DIGEST_BUDGET_BYTES = 8_000
MAX_REDUCE_PROMPT_BYTES = 180_000
MAX_REDUCE_CHILDREN = 10
MAX_REDUCE_NODE_OUTPUT_BYTES = 12_000
MAX_REDUCE_NODE_PAYLOAD_RESERVE_BYTES = 1_024
MAX_REDUCE_LEAF_BYTES = 60_000
OFFLINE_DUCKDB_MEMORY_LIMIT = "4GB"
SPLIT_P90_DISTANCE = 0.28
SPLIT_MAX_DISTANCE = 0.55
SPLIT_DEPTH_MARGIN = 16
DAILY_MAX_CAPSULES = 24
DAILY_MAX_CLAIMS = 24
DAILY_ANN_CANDIDATES = 96
DAILY_TEMPORAL_ANN_CANDIDATES = 512
_LOGGER = logging.getLogger(__name__)

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
class OfflineBrainPlanResult:
    work_database_path: Path
    reduce_dag_plan_path: Path
    plan_receipt_path: Path
    receipt: dict[str, Any]


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
    # Full membership remains in the assignment ledger; retain only count and root here.
    member_record_count: int
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
class _PlannedReduceNode:
    """Lightweight reduce proxy used only by the zero-LLM planner."""

    node_id: str
    child_node_ids: tuple[str, ...]
    covered_capsule_ids: tuple[str, ...]
    payload_bytes: int


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
        checkpoint_dir: Path | None = None,
        compatible_checkpoint_model_configs: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.root = settings.project_root
        resolved_checkpoint_dir = None
        if checkpoint_dir is not None:
            resolved_checkpoint_dir = settings.path(checkpoint_dir)
            if not resolved_checkpoint_dir.is_dir():
                raise FileNotFoundError(
                    "explicit offline checkpoint directory is missing or not a directory"
                )
        base_llm = llm or create_llm_provider(settings)
        self.model_config = {
            "provider": str(getattr(base_llm, "provider_name", settings.llm_provider)),
            "model": str(getattr(base_llm, "model", settings.llm.model)),
            "reasoning_effort": str(getattr(base_llm, "reasoning_effort", settings.llm.reasoning_effort)),
        }
        self.llm = _trace_offline_llm(
            settings,
            base_llm,
            self.model_config,
            checkpoint_dir=resolved_checkpoint_dir,
            compatible_checkpoint_model_configs=compatible_checkpoint_model_configs,
        )
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
        self._work_connection: duckdb.DuckDBPyConnection | None = None
        self._work_progress_path: Path | None = None
        self._work_record_count = 0
        self._work_semantic_unit_count = 0
        self._work_reduce_total_count = 0
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
        work_root = self.root / "brain" / ".work" / f"{plan_id}-{uuid4().hex[:8]}"
        work_root.mkdir(parents=True, exist_ok=False)
        database_path = work_root / "semantic_plan.duckdb"
        connection = duckdb.connect(str(database_path))
        _configure_offline_duckdb(connection, temp_directory=work_root / "duckdb_tmp")
        try:
            _initialize_package_database(connection, source=source)
            unit_builds = _build_semantic_assignments(
                connection,
                source=source,
                progress_path=work_root / "progress.json",
            )
            semantic_unit_count = len(unit_builds)
            _write_offline_progress(
                work_root / "progress.json",
                phase="representative_and_distribution_planning",
                processed_record_count=source.record_count,
                total_record_count=source.record_count,
                semantic_unit_count=semantic_unit_count,
            )
            unit_rows = _load_unit_prompt_rows(connection, unit_builds=unit_builds)
            del unit_builds
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
        outlier_unit_count = 0
        for row in unit_rows:
            category = str(row["category"])
            category_unit_counts[category] += 1
            outlier_unit_count += int(bool(row["outlier_record_ids"]))
        planned_leaf_nodes = _planned_reduce_leaf_nodes(unit_rows)
        estimated_reduce_calls = _estimate_reduce_review_call_count(planned_leaf_nodes)
        estimated_leaf_node_count = sum(len(rows) for rows in planned_leaf_nodes.values())
        guaranteed_minimum_reduce_review_calls = len(category_unit_counts) + 1
        guaranteed_minimum_total_calls = (
            payload_plan.long_payload_chunk_map_call_count
            + len(leaf_batches)
            + guaranteed_minimum_reduce_review_calls
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
            "semantic_unit_count": semantic_unit_count,
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
            "estimated_reduce_leaf_node_count": estimated_leaf_node_count,
            "estimated_reduce_leaf_node_count_is_runtime_count": False,
            "estimated_reduce_review_call_count": estimated_reduce_calls,
            # Planned leaves hash semantic-unit IDs, while runtime hashes
            # model-derived capsule IDs. Their bucket counts are unrelated, so
            # this byte-packer simulation is a projection, not a bound.
            "estimated_reduce_review_call_count_is_lower_bound": False,
            "estimated_reduce_review_call_count_is_projection": True,
            "guaranteed_minimum_reduce_review_call_count": (
                guaranteed_minimum_reduce_review_calls
            ),
            "estimated_reduce_prompt_byte_packing_simulated": True,
            "estimated_reduce_prompt_byte_packing_simulation": (
                "coverage_only_leaf_proxy.v1"
            ),
            "estimated_total_logical_llm_call_count": (
                payload_plan.long_payload_chunk_map_call_count
                + len(leaf_batches)
                + estimated_reduce_calls
            ),
            "estimated_total_logical_llm_call_count_is_lower_bound": False,
            "estimated_total_logical_llm_call_count_is_projection": True,
            "guaranteed_minimum_total_logical_llm_call_count": (
                guaranteed_minimum_total_calls
            ),
            "guaranteed_minimum_total_logical_llm_call_count_is_lower_bound": True,
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
        resume_work_database: Path | None = None,
        expected_resume_work_database_sha256: str | None = None,
        expected_resume_work_database_wal_sha256: str | None = None,
        stop_after_reduce_plan: bool = False,
        require_map_plan_receipt: bool = False,
    ) -> OfflineBrainBuildResult | OfflineBrainPlanResult:
        started_at = now_kst()
        started = monotonic()
        source = resolve_source_memory_snapshot(
            source_project,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        if resume_work_database is not None and previous_package is not None:
            raise ValueError("choose either a prior package or a resume work database, not both")
        if expected_resume_work_database_sha256 is not None and resume_work_database is None:
            raise ValueError("an expected resume work database SHA requires a resume database path")
        if (
            expected_resume_work_database_wal_sha256 is not None
            and resume_work_database is None
        ):
            raise ValueError("an expected resume work database WAL SHA requires a resume database path")
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
        plan_receipt_path = work_root / "offline_map_plan_receipt.json"
        if (
            require_map_plan_receipt
            and not stop_after_reduce_plan
            and not plan_receipt_path.is_file()
        ):
            raise ValueError(
                "no sealed map-only reduce plan exists; run build-offline without "
                "--continue-after-map-plan first"
            )
        resume_database_path = resume_work_database.resolve() if resume_work_database else None
        resume_database_sha256: str | None = None
        resume_database_wal_sha256: str | None = None
        if resume_database_path is not None:
            if not resume_database_path.is_file():
                raise FileNotFoundError(f"resume work database is missing: {resume_database_path}")
            if resume_database_path == database_path.resolve():
                raise ValueError("resume work database cannot be the active target database")
            resume_database_sha256 = file_sha256(resume_database_path)
            if (
                expected_resume_work_database_sha256 is not None
                and resume_database_sha256.lower()
                != expected_resume_work_database_sha256.strip().lower()
            ):
                raise ValueError("resume work database SHA-256 does not match the attested value")
            resume_database_wal_path = Path(f"{resume_database_path}.wal")
            if resume_database_wal_path.is_file():
                resume_database_wal_sha256 = file_sha256(resume_database_wal_path)
                if expected_resume_work_database_wal_sha256 is None:
                    raise ValueError(
                        "resume work database WAL exists; its expected SHA-256 is required"
                    )
                if resume_database_wal_sha256.lower() != (
                    expected_resume_work_database_wal_sha256.strip().lower()
                ):
                    raise ValueError(
                        "resume work database WAL SHA-256 does not match the attested value"
                    )
            elif expected_resume_work_database_wal_sha256 is not None:
                raise ValueError("expected resume work database WAL is missing")

        target_database_existed = database_path.exists()
        resumed_legacy_database = False
        resumed_capsule_signatures: dict[str, tuple[str, str, str]] = {}
        if resume_database_path is not None:
            validation_path = work_root / f"resume-validation-{uuid4().hex[:8]}"
            validation_path.mkdir(parents=True, exist_ok=False)
            legacy_connection = duckdb.connect(
                str(resume_database_path),
                read_only=True,
                config={
                    "threads": 1,
                    "memory_limit": OFFLINE_DUCKDB_MEMORY_LIMIT,
                    "temp_directory": str(validation_path / "duckdb_tmp"),
                },
            )
            try:
                _configure_offline_duckdb(
                    legacy_connection,
                    temp_directory=validation_path / "duckdb_tmp",
                )
                _attach_source_memory(legacy_connection, source=source)
                _validate_reusable_assignment_database(
                    legacy_connection,
                    source=source,
                    require_complete_capsules=True,
                )
                legacy_counts = legacy_connection.execute(
                    "SELECT (SELECT count(*) FROM reduce_nodes), "
                    "(SELECT count(*) FROM mechanism_claims)"
                ).fetchone()
                if legacy_counts != (0, 0):
                    raise ValueError(
                        "legacy work database contains reducer/claim outputs that require a separate audit"
                    )
            finally:
                legacy_connection.close()
                shutil.rmtree(validation_path, ignore_errors=True)
            if resume_database_sha256 is None:
                raise ValueError("resume work database hash was not captured")
            if target_database_existed:
                resumed_legacy_database = _adopt_matching_resume_work_database(
                    resume_database_path,
                    database_path,
                    expected_sha256=resume_database_sha256,
                    expected_wal_sha256=resume_database_wal_sha256,
                )
            else:
                _copy_resume_work_database(
                    resume_database_path,
                    database_path,
                    expected_sha256=resume_database_sha256,
                    expected_wal_sha256=resume_database_wal_sha256,
                )
                resumed_legacy_database = True

        connection = duckdb.connect(
            str(database_path),
            read_only=False,
            config={
                "threads": 1,
                "memory_limit": OFFLINE_DUCKDB_MEMORY_LIMIT,
                "temp_directory": str(work_root / "duckdb_tmp"),
            },
        )
        _configure_offline_duckdb(connection, temp_directory=work_root / "duckdb_tmp")
        try:
            _drop_package_database_indexes(connection)
            if not target_database_existed and not resumed_legacy_database:
                _initialize_package_database(connection, source=source)
                unit_builds = _build_semantic_assignments(
                    connection,
                    source=source,
                    progress_path=work_root / "progress.json",
                )
            else:
                _attach_source_memory(connection, source=source)
                metadata = _read_compile_metadata(connection)
                if target_database_existed and not metadata and not resumed_legacy_database:
                    raise ValueError(
                        "existing work database has no compiler identity metadata; "
                        "refusing an implicit resume"
                    )
                if metadata:
                    expected_metadata = {
                        "compiler_version": OFFLINE_COMPILER_VERSION,
                        "compile_id": compile_id,
                        "source_memory_manifest_sha256": source.manifest_sha256,
                        "record_corpus_root": source.record_corpus_root,
                        "record_count": str(source.record_count),
                        "semantic_splitter_version": SEMANTIC_SPLITTER_VERSION,
                    }
                    if any(metadata.get(key) != value for key, value in expected_metadata.items()):
                        raise ValueError("existing work database belongs to another source or compiler identity")
                    if (
                        resume_database_sha256 is not None
                        and metadata.get("resume_source_database_sha256")
                        != resume_database_sha256
                    ):
                        raise ValueError("existing work database came from a different resume source")
                    if (
                        resume_database_path is not None
                        and metadata.get("resume_source_database_wal_sha256", "")
                        != (resume_database_wal_sha256 or "")
                    ):
                        raise ValueError(
                            "existing work database came from a different resume WAL"
                        )
                unit_builds = _validate_reusable_assignment_database(
                    connection,
                    source=source,
                    require_complete_capsules=resumed_legacy_database,
                )
                if resumed_legacy_database and metadata:
                    raise ValueError("legacy resume database unexpectedly contains v6 build metadata")
                self._previous = _PreviousPackageState(
                    self._previous.capsules_by_unit,
                    _load_reduce_nodes_from_database(connection),
                )

            if resumed_legacy_database or target_database_existed:
                resumed_capsule_signatures = _capsule_database_signatures(connection)

            semantic_unit_count = len(unit_builds)
            metadata = _read_compile_metadata(connection)
            metadata.update(
                {
                    "compiler_version": OFFLINE_COMPILER_VERSION,
                    "compile_id": compile_id,
                    "source_memory_manifest_sha256": source.manifest_sha256,
                    "record_corpus_root": source.record_corpus_root,
                    "record_count": str(source.record_count),
                    "semantic_splitter_version": SEMANTIC_SPLITTER_VERSION,
                    "embedding_identity": source.embedding_identity,
                    "semantic_unit_count": str(semantic_unit_count),
                    "resume_source_database_sha256": (
                        resume_database_sha256
                        or metadata.get("resume_source_database_sha256", "")
                    ),
                    "resume_source_database_wal_sha256": (
                        resume_database_wal_sha256
                        or metadata.get("resume_source_database_wal_sha256", "")
                    ),
                    "resume_source_database_path": (
                        resume_database_path.as_posix()
                        if resume_database_path
                        else metadata.get("resume_source_database_path", "")
                    ),
                }
            )
            _write_compile_metadata(connection, values=metadata)
            existing_plan_receipt = (
                read_json(plan_receipt_path) if plan_receipt_path.is_file() else None
            )
            if require_map_plan_receipt and not stop_after_reduce_plan:
                if existing_plan_receipt is None:
                    raise ValueError("sealed map-only plan receipt is unreadable")
                expected_receipt_identity = {
                    "compile_id": compile_id,
                    "source_memory_manifest_sha256": source.manifest_sha256,
                    "record_corpus_root": source.record_corpus_root,
                    "record_count": source.record_count,
                    "semantic_unit_count": semantic_unit_count,
                    "resume_source_database_sha256": (
                        metadata.get("resume_source_database_sha256") or None
                    ),
                    "resume_source_database_wal_sha256": (
                        metadata.get("resume_source_database_wal_sha256") or None
                    ),
                }
                if any(
                    existing_plan_receipt.get(key) != value
                    for key, value in expected_receipt_identity.items()
                ):
                    raise ValueError(
                        "sealed map-only plan belongs to another source or resume database"
                    )
            self._work_connection = connection
            self._work_progress_path = work_root / "progress.json"
            self._work_record_count = source.record_count
            self._work_semantic_unit_count = semantic_unit_count
            semantic_unit_count = len(unit_builds)
            _write_offline_progress(
                work_root / "progress.json",
                phase=(
                    "representative_and_distribution_resume"
                    if resumed_legacy_database or target_database_existed
                    else "representative_and_distribution_build"
                ),
                processed_record_count=source.record_count,
                total_record_count=source.record_count,
                semantic_unit_count=semantic_unit_count,
            )
            unit_rows = _load_unit_prompt_rows(connection, unit_builds=unit_builds)
            del unit_builds
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
            # The audit plan retains projected rows and copied long-payload chunks.
            # Its metrics and exposure ledger are captured, so release those copies
            # before leaf compilation builds the changed-row plan it actually uses.
            del package_payload_plan
            capsules = await self._compile_leaf_capsules(unit_rows)
            # Keep outcome labels out of content-addressed LLM prompts while binding
            # their full-population distribution into the resulting capsules.
            capsules = _attach_close_return_status_distributions(connection, capsules)
            current_capsule_signatures = {
                row.semantic_unit_id: (
                    row.capsule_id,
                    sha256_text(canonical_json(row.model_dump(mode="json"))),
                    row.member_record_root,
                )
                for row in capsules
            }
            resumed_capsule_exact_match_count = sum(
                resumed_capsule_signatures.get(unit_id) == signature
                for unit_id, signature in current_capsule_signatures.items()
            )
            # Reduce operates on capsules and verified child IDs, not raw news payloads.
            del unit_rows
            _write_capsules_to_database(connection, capsules)
            leaf_nodes, reduce_dag_plan = _plan_reduce_graph(capsules)
            plan_artifact = {
                "schema_version": "nslab.offline_reduce_dag_artifact.v1",
                "compile_id": compile_id,
                "source_memory_manifest_sha256": source.manifest_sha256,
                "record_corpus_root": source.record_corpus_root,
                "capsule_population_root": _model_population_root(
                    capsules, key="capsule_id"
                ),
                "plan": reduce_dag_plan,
            }
            plan_artifact_sha256 = sha256_text(canonical_json(plan_artifact))
            plan_path = work_root / "reduce_dag_plan.json"
            existing_plan = read_json(plan_path) if plan_path.is_file() else None
            metadata = _read_compile_metadata(connection)
            stored_plan_sha256 = metadata.get("reduce_dag_plan_sha256")
            stored_topology_sha256 = metadata.get("reduce_dag_topology_sha256")
            persisted_nodes = _load_reduce_nodes_from_database(connection)
            if (
                existing_plan_receipt is not None
                and existing_plan_receipt.get("reduce_dag_plan_sha256")
                != plan_artifact_sha256
                and not stop_after_reduce_plan
            ):
                raise ValueError(
                    "capsule population changed after map-only plan was sealed; "
                    "stop and reseal the plan before reducer calls"
                )
            if existing_plan is not None and (
                existing_plan.get("plan_sha256") != plan_artifact_sha256
            ) and persisted_nodes:
                raise ValueError(
                    "reduce DAG changed after persisted nodes exist; refusing unsafe resume"
                )
            if stored_plan_sha256 and stored_plan_sha256 != plan_artifact_sha256 and persisted_nodes:
                raise ValueError(
                    "work database reduce DAG identity differs from current capsules"
                )
            if (
                stored_topology_sha256
                and stored_topology_sha256 != reduce_dag_plan["topology_sha256"]
                and persisted_nodes
            ):
                raise ValueError("work database reduce topology differs from current capsules")
            planned_tasks = {
                str(row["node_id"]): tuple(str(value) for value in row["child_node_ids"])
                for row in reduce_dag_plan["tasks"]
            }
            unexpected_persisted_nodes = set(persisted_nodes) - set(planned_tasks)
            if unexpected_persisted_nodes:
                raise ValueError(
                    "work database contains reduce nodes outside the fixed DAG: "
                    f"{len(unexpected_persisted_nodes)}"
                )
            plan_artifact["plan_sha256"] = plan_artifact_sha256
            write_json(plan_path, plan_artifact)
            metadata.update(
                {
                    "reduce_dag_plan_sha256": plan_artifact_sha256,
                    "reduce_dag_topology_sha256": reduce_dag_plan["topology_sha256"],
                    "reduce_dag_model_task_count": str(
                        reduce_dag_plan["total_model_tasks"]
                    ),
                }
            )
            _write_compile_metadata(connection, values=metadata)
            self._work_reduce_total_count = int(
                reduce_dag_plan["total_model_tasks"]
            )
            self._previous = _PreviousPackageState(
                self._previous.capsules_by_unit,
                {
                    **self._previous.reduce_nodes_by_id,
                    **persisted_nodes,
                },
            )
            _write_offline_progress(
                work_root / "progress.json",
                phase="offline_reduce_planned",
                processed_record_count=source.record_count,
                total_record_count=source.record_count,
                semantic_unit_count=semantic_unit_count,
                completed_model_node_count=0,
                total_model_node_count=self._work_reduce_total_count,
            )
            if stop_after_reduce_plan:
                checkpoint_usage_rows = (
                    self.llm.checkpoint_usage_rows
                    if isinstance(self.llm, TracingLLMProvider)
                    else []
                )
                map_usage_rows, map_usage_summary = _summarize_map_checkpoint_usage(
                    checkpoint_usage_rows
                )
                map_usage_path = work_root / "map_stage_checkpoint_usage.jsonl"
                _write_jsonl(map_usage_path, map_usage_rows)
                map_plan_receipt = {
                    "schema_version": "nslab.offline_map_plan_receipt.v1",
                    "status": "MAP_AND_PLAN_READY_REDUCERS_NOT_STARTED",
                    "compile_id": compile_id,
                    "requested_model_config": {
                        **self.model_config,
                        "compiler_version": OFFLINE_COMPILER_VERSION,
                    },
                    "source_memory_manifest_sha256": source.manifest_sha256,
                    "record_corpus_root": source.record_corpus_root,
                    "record_count": source.record_count,
                    "semantic_unit_count": semantic_unit_count,
                    "semantic_capsule_count": len(capsules),
                    "capsule_population_root": plan_artifact[
                        "capsule_population_root"
                    ],
                    "resume_source_database_path": metadata.get(
                        "resume_source_database_path"
                    )
                    or None,
                    "resume_source_database_sha256": metadata.get(
                        "resume_source_database_sha256"
                    )
                    or None,
                    "resume_source_database_wal_sha256": metadata.get(
                        "resume_source_database_wal_sha256"
                    )
                    or None,
                    "resume_source_capsule_count": len(resumed_capsule_signatures),
                    "resume_source_capsule_exact_match_count": (
                        resumed_capsule_exact_match_count
                    ),
                    "resume_source_capsule_changed_or_new_count": (
                        len(capsules) - resumed_capsule_exact_match_count
                    ),
                    "reduce_dag_plan_file": plan_path.name,
                    "reduce_dag_plan_sha256": plan_artifact_sha256,
                    "reduce_dag_topology_sha256": reduce_dag_plan[
                        "topology_sha256"
                    ],
                    "reduce_leaf_count": reduce_dag_plan["reduce_leaf_count"],
                    "reduce_model_task_count": self._work_reduce_total_count,
                    "category_counts": reduce_dag_plan["category_counts"],
                    "map_checkpoint_usage_file": map_usage_path.name,
                    "map_checkpoint_usage_sha256": file_sha256(map_usage_path),
                    **map_usage_summary,
                    "created_at": now_kst().isoformat(),
                }
                write_json(plan_receipt_path, map_plan_receipt)
                _write_offline_progress(
                    work_root / "progress.json",
                    phase="offline_reduce_plan_sealed",
                    processed_record_count=source.record_count,
                    total_record_count=source.record_count,
                    semantic_unit_count=semantic_unit_count,
                    completed_model_node_count=0,
                    total_model_node_count=self._work_reduce_total_count,
                )
                return OfflineBrainPlanResult(
                    work_database_path=database_path,
                    reduce_dag_plan_path=plan_path,
                    plan_receipt_path=plan_receipt_path,
                    receipt=map_plan_receipt,
                )
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

            category_tasks = [
                asyncio.create_task(reduce_category(category))
                for category in sorted({row.category for row in capsules})
            ]
            try:
                category_results = await asyncio.gather(*category_tasks)
            except BaseException:
                for task in category_tasks:
                    task.cancel()
                await asyncio.gather(*category_tasks, return_exceptions=True)
                raise
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
            if set(world_root.covered_capsule_ids) != {
                row.capsule_id for row in capsules
            }:
                raise ValueError("world reduce tree does not cover the capsule population")
            reduce_nodes.append(world_root)
            actual_tasks = {
                row.node_id: tuple(row.child_node_ids) for row in reduce_nodes
            }
            if actual_tasks != planned_tasks:
                raise ValueError(
                    "runtime reduce graph does not match the durable pre-call DAG plan"
                )
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
                semantic_unit_count=semantic_unit_count,
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
            self._work_connection = None
            self._work_progress_path = None

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
            existing_receipt = package_dir / "build_receipt.json"
            if existing_manifest.is_file() and existing_receipt.is_file():
                result = load_offline_brain_build_result(package_dir)
                shutil.rmtree(work_root, ignore_errors=True)
                return result
            incomplete_dir = package_dir.with_name(
                f".{package_dir.name}.incomplete-{uuid4().hex[:8]}"
            )
            os.replace(package_dir, incomplete_dir)
        package_dir.parent.mkdir(parents=True, exist_ok=True)
        package_dir.mkdir()
        packaged_database = package_dir / database_path.name
        database_linked = False
        try:
            os.link(database_path, packaged_database)
        except OSError:
            pass
        else:
            database_linked = True
        if not database_linked:
            try:
                shutil.copy2(database_path, packaged_database)
                if file_sha256(packaged_database) != file_sha256(database_path):
                    packaged_database.unlink()
                    raise ValueError(
                        "copied package database does not match the resumable work database"
                    )
            except BaseException:
                if packaged_database.is_file():
                    packaged_database.unlink()
                raise

        influence = influence.model_copy(update={"brain_version": brain_version})
        _write_jsonl(package_dir / "semantic_capsules.jsonl", capsules)
        _write_jsonl(package_dir / "synthesized_mechanism_claims.jsonl", claims)
        _write_reduce_leaf_coverage(package_dir, leaf_nodes)
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
            semantic_unit_count=semantic_unit_count,
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
        checkpoint_usage_rows = (
            self.llm.checkpoint_usage_rows
            if isinstance(self.llm, TracingLLMProvider)
            else []
        )
        map_usage_rows, map_usage_summary = _summarize_map_checkpoint_usage(
            checkpoint_usage_rows
        )
        write_json(
            package_dir / "offline_resume_reuse_manifest.json",
            {
                "schema_version": "nslab.offline_resume_reuse_manifest.v1",
                "compile_id": compile_id,
                "source_memory_manifest_sha256": source.manifest_sha256,
                "record_corpus_root": source.record_corpus_root,
                "record_count": source.record_count,
                "semantic_unit_count": semantic_unit_count,
                "semantic_capsule_count": len(capsules),
                "resume_source_database_path": (
                    resume_database_path.as_posix()
                    if resume_database_path is not None
                    else None
                ),
                "resume_source_database_sha256": resume_database_sha256,
                "resume_source_database_wal_sha256": resume_database_wal_sha256,
                "resume_source_capsule_count": len(resumed_capsule_signatures),
                "resume_source_capsule_exact_match_count": (
                    resumed_capsule_exact_match_count
                ),
                "resume_source_capsule_changed_or_new_count": (
                    len(capsules) - resumed_capsule_exact_match_count
                ),
                "reduce_dag_topology_sha256": reduce_dag_plan["topology_sha256"],
                "reduce_dag_plan_sha256": plan_artifact_sha256,
                "reduce_model_task_count": self._work_reduce_total_count,
                "map_plan_receipt_sha256": (
                    file_sha256(plan_receipt_path)
                    if plan_receipt_path.is_file()
                    else None
                ),
                **map_usage_summary,
            },
        )
        write_json(package_dir / "reduce_dag_plan.json", plan_artifact)
        if plan_receipt_path.is_file():
            write_json(
                package_dir / "offline_map_plan_receipt.json",
                read_json(plan_receipt_path),
            )
            map_usage_path = work_root / "map_stage_checkpoint_usage.jsonl"
            if map_usage_path.is_file():
                shutil.copy2(
                    map_usage_path,
                    package_dir / "map_stage_checkpoint_usage.jsonl",
                )
        progress_path = work_root / "progress.json"
        if progress_path.is_file():
            write_json(
                package_dir / "offline_compile_progress.json",
                read_json(progress_path),
            )
        usage_path = package_dir / "synthesis_checkpoint_usage.jsonl"
        _write_jsonl(usage_path, checkpoint_usage_rows)
        model_usage: dict[str, dict[str, Any]] = {}
        for row in checkpoint_usage_rows:
            model_config = dict(row["model_config"])
            identity = canonical_json(model_config)
            aggregate = model_usage.setdefault(
                identity,
                {"model_config": model_config, "output_count": 0, "cache_hit_count": 0},
            )
            aggregate["output_count"] += 1
            aggregate["cache_hit_count"] += int(bool(row["cache_hit"]))
        write_json(
            package_dir / "synthesis_model_provenance.json",
            {
                "schema_version": "nslab.offline_synthesis_model_provenance.v1",
                "requested_model_config": {
                    **self.model_config,
                    "compiler_version": OFFLINE_COMPILER_VERSION,
                },
                "output_count": len(checkpoint_usage_rows),
                "checkpoint_usage_sha256": file_sha256(usage_path),
                "model_output_counts": [
                    model_usage[key]
                    for key in sorted(model_usage)
                ],
            },
        )
        package_root = _artifact_root(package_dir)
        manifest = BrainPackageManifest(
            brain_version=brain_version,
            created_at=compile_manifest.completed_at,
            build_cutoff=source.build_cutoff,
            record_count=source.record_count,
            semantic_unit_count=semantic_unit_count,
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
    ) -> list[SemanticMemoryCapsule]:
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
        del changed_payload_plan
        categories = sorted({str(row["category"]) for row in changed_rows})

        def batches() -> Iterable[tuple[str, list[dict[str, Any]]]]:
            for category in categories:
                category_rows = (
                    row for row in changed_rows if row["category"] == category
                )
                for batch in _pack_leaf_rows(category_rows):
                    yield category, batch

        batch_iterator = iter(batches())

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

        worker_count = min(
            max(1, self.settings.limits.max_concurrency),
            len(changed_rows),
        )

        async def worker() -> None:
            while True:
                try:
                    category, batch = next(batch_iterator)
                except StopIteration:
                    return
                compiled = await compile_batch(category, batch)
                for capsule in compiled:
                    capsules_by_unit[capsule.semantic_unit_id] = capsule
                self._recompiled_capsule_count += len(compiled)
                del batch, compiled

        if worker_count:
            workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
            try:
                await asyncio.gather(*workers)
            except BaseException:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise
        expected_units = {str(row["semantic_unit_id"]) for row in unit_rows}
        if set(capsules_by_unit) != expected_units:
            raise ValueError("offline semantic capsule compile omitted units")
        capsules = [capsules_by_unit[key] for key in sorted(capsules_by_unit)]
        return capsules

    async def _compile_long_payload_digests(
        self,
        payload_plan: _LongPayloadPlan,
    ) -> list[dict[str, Any]]:
        if not payload_plan.chunk_inputs:
            return payload_plan.projected_rows
        batch_iterator = iter(_pack_long_payload_chunks(payload_plan.chunk_inputs))

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

        digests_by_id: dict[str, LongPayloadChunkDigest] = {}

        async def worker() -> None:
            while True:
                try:
                    batch = next(batch_iterator)
                except StopIteration:
                    return
                self._long_payload_chunk_map_call_count += 1
                compiled = await compile_batch(batch)
                for digest in compiled:
                    if digest.chunk_id in digests_by_id:
                        raise ValueError("long payload digest stage duplicated a chunk")
                    digests_by_id[digest.chunk_id] = digest
                del batch, compiled

        worker_count = min(
            max(1, self.settings.limits.max_concurrency),
            len(payload_plan.chunk_inputs),
        )
        workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise
        if len(digests_by_id) != len(payload_plan.chunk_inputs):
            raise ValueError("long payload digest stage omitted or added chunks")

        for row in payload_plan.projected_rows:
            for representative in row["representatives"]:
                placeholders = representative.get("full_payload_chunk_digests")
                if isinstance(placeholders, list):
                    materialized: list[dict[str, Any]] = []
                    for value in placeholders:
                        chunk_id = str(value["chunk_id"])
                        digest = digests_by_id.pop(chunk_id, None)
                        if digest is None:
                            raise ValueError(
                                "long payload digest stage omitted a representative chunk"
                            )
                        materialized.append(digest.model_dump(mode="json"))
                    representative["full_payload_chunk_digests"] = materialized
        if digests_by_id:
            raise ValueError("long payload digest stage produced unused chunks")
        return payload_plan.projected_rows

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
                covered_capsule_count=len(row.capsule_ids),
                coverage_root=_leaf_coverage_root(row.node_id, row.capsule_ids),
                evidence_capsule_ids=_leaf_reduce_evidence_ids(row.capsule_ids),
                synthesis=row.synthesis,
            )
            for row in leaves
        ]
        created: list[SemanticReduceNode] = []
        level = 0
        while len(current) > 1:
            groups = list(_pack_reduce_nodes(current))
            # Successful rewrites of singleton groups do not converge to a root.
            # Detect that condition before spending any calls on this level.
            if len(groups) >= len(current):
                raise ValueError(
                    "offline reduce cannot converge: "
                    f"category={category} level={level} nodes={len(current)} "
                    f"groups={len(groups)} byte_limit={MAX_REDUCE_PROMPT_BYTES}"
                )
            next_level: list[SemanticReduceNode] = []
            for group in groups:
                if len(group) == 1:
                    next_level.append(group[0])
                    continue
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
        node_id = stable_id(
            "CATEGORY-REVIEW" if review else "REDUCE",
            category,
            level,
            child_ids,
            length=20,
        )
        prompt = _reduce_prompt(
            node_id=node_id,
            category=category,
            children=children,
            review=review,
        )
        prompt_sha256 = sha256_text(prompt)
        previous = self._previous.reduce_nodes_by_id.get(node_id)
        covered = _unique(
            capsule_id for row in children for capsule_id in row.covered_capsule_ids
        )
        covered_count = sum(row.covered_capsule_count for row in children)
        if len(covered) != covered_count:
            raise ValueError("reduce child coverage contains duplicate capsule IDs")
        coverage_root = _reduce_coverage_root(node_id, children)
        if (
            previous is not None
            and previous.prompt_sha256 == prompt_sha256
            and previous.covered_capsule_count == covered_count
            and previous.coverage_root == coverage_root
        ):
            result = previous
            self._reused_reduce_node_count += 1
        else:
            draft = await self._call_structured(
                prompt=prompt,
                response_model=SemanticReduceDraft,
                purpose=(f"offline_category_review.{category}" if review else f"offline_semantic_reduce.{node_id}"),
            )
            self._recompiled_reduce_node_count += 1
            if draft.node_id != node_id:
                raise ValueError("semantic reduce output omitted or added children")
            child_identity_normalization = _normalize_reduce_child_identity(
                draft_child_node_ids=draft.child_node_ids,
                expected_child_node_ids=child_ids,
            )
            allowed_capsule_ids = {
                capsule_id
                for child in children
                for capsule_id in child.evidence_capsule_ids
            }
            claims, citation_normalizations = _normalize_reduce_claim_citations(
                draft.claims,
                allowed_capsule_ids=allowed_capsule_ids,
            )
            evidence_capsule_ids = _unique(
                capsule_id
                for claim in claims
                for capsule_id in (
                    *claim.supporting_capsule_ids,
                    *claim.contradicting_capsule_ids,
                )
            )
            result = SemanticReduceNode(
                node_id=node_id,
                child_node_ids=child_ids,
                covered_capsule_count=covered_count,
                coverage_root=coverage_root,
                prompt_sha256=prompt_sha256,
                covered_capsule_ids=covered,
                evidence_capsule_ids=evidence_capsule_ids,
                synthesis=draft.synthesis,
                mechanisms=draft.mechanisms,
                conditions=draft.conditions,
                boundary_conditions=draft.boundary_conditions,
                failure_modes=draft.failure_modes,
                contradictions=draft.contradictions,
                claims=claims,
                citation_normalizations=citation_normalizations,
                child_identity_normalization=child_identity_normalization,
            )
        if result.node_id != node_id or result.child_node_ids != child_ids:
            raise ValueError("semantic reduce output omitted or added children")
        result = result.model_copy(
            update={
                "covered_capsule_ids": covered,
                "covered_capsule_count": covered_count,
                "coverage_root": coverage_root,
                "prompt_sha256": prompt_sha256,
            }
        )
        if self._work_connection is not None:
            _persist_reduce_node(self._work_connection, result)
        if self._work_progress_path is not None:
            _write_offline_progress(
                self._work_progress_path,
                phase="offline_reduce",
                processed_record_count=self._work_record_count,
                total_record_count=self._work_record_count,
                semantic_unit_count=self._work_semantic_unit_count,
                completed_model_node_count=(
                    self._reused_reduce_node_count + self._recompiled_reduce_node_count
                ),
                total_model_node_count=self._work_reduce_total_count,
                current_model_node_id=node_id,
            )
        return result

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
            world=True,
        )
        prompt_sha256 = sha256_text(prompt)
        previous = self._previous.reduce_nodes_by_id.get(node_id)
        covered = _unique(
            capsule_id for row in children for capsule_id in row.covered_capsule_ids
        )
        covered_count = sum(row.covered_capsule_count for row in children)
        if len(covered) != covered_count:
            raise ValueError("world reduce child coverage contains duplicate capsule IDs")
        coverage_root = _reduce_coverage_root(node_id, children)
        if (
            previous is not None
            and previous.prompt_sha256 == prompt_sha256
            and previous.covered_capsule_count == covered_count
            and previous.coverage_root == coverage_root
        ):
            result = previous
            self._reused_reduce_node_count += 1
        else:
            draft = await self._call_structured(
                prompt=prompt,
                response_model=SemanticReduceDraft,
                purpose="offline_world_model",
            )
            self._recompiled_reduce_node_count += 1
            expected_children = [row.node_id for row in children]
            if draft.node_id != node_id:
                raise ValueError("world reduce output omitted category children")
            child_identity_normalization = _normalize_reduce_child_identity(
                draft_child_node_ids=draft.child_node_ids,
                expected_child_node_ids=expected_children,
                error_message="world reduce output omitted category children",
            )
            allowed_capsule_ids = {
                capsule_id
                for child in children
                for capsule_id in child.evidence_capsule_ids
            }
            claims, citation_normalizations = _normalize_reduce_claim_citations(
                draft.claims,
                allowed_capsule_ids=allowed_capsule_ids,
                error_message="world reduce claim cited an unavailable capsule",
            )
            evidence_capsule_ids = _unique(
                capsule_id
                for claim in claims
                for capsule_id in (
                    *claim.supporting_capsule_ids,
                    *claim.contradicting_capsule_ids,
                )
            )
            result = SemanticReduceNode(
                node_id=node_id,
                child_node_ids=expected_children,
                covered_capsule_count=covered_count,
                coverage_root=coverage_root,
                prompt_sha256=prompt_sha256,
                covered_capsule_ids=covered,
                evidence_capsule_ids=evidence_capsule_ids,
                synthesis=draft.synthesis,
                mechanisms=draft.mechanisms,
                conditions=draft.conditions,
                boundary_conditions=draft.boundary_conditions,
                failure_modes=draft.failure_modes,
                contradictions=draft.contradictions,
                claims=claims,
                citation_normalizations=citation_normalizations,
                child_identity_normalization=child_identity_normalization,
            )
        expected_children = [row.node_id for row in children]
        if result.node_id != node_id or result.child_node_ids != expected_children:
            raise ValueError("world reduce output omitted category children")
        result = result.model_copy(
            update={
                "covered_capsule_ids": covered,
                "covered_capsule_count": covered_count,
                "coverage_root": coverage_root,
                "prompt_sha256": prompt_sha256,
            }
        )
        if self._work_connection is not None:
            _persist_reduce_node(self._work_connection, result)
        if self._work_progress_path is not None:
            _write_offline_progress(
                self._work_progress_path,
                phase="offline_reduce",
                processed_record_count=self._work_record_count,
                total_record_count=self._work_record_count,
                semantic_unit_count=self._work_semantic_unit_count,
                completed_model_node_count=(
                    self._reused_reduce_node_count + self._recompiled_reduce_node_count
                ),
                total_model_node_count=self._work_reduce_total_count,
                current_model_node_id=node_id,
            )
        return result

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
        allow_point_in_time_projection: bool = False,
    ) -> None:
        self.settings = settings
        self.root = settings.project_root
        self.package_dir = package_dir
        self.allow_point_in_time_projection = allow_point_in_time_projection
        self.embedding_provider = embedding_provider or create_configured_embedding_provider(
            settings,
            production=(settings.event_cluster_fallback_policy.value == "fail-closed"),
        )
        self._manifest: BrainPackageManifest | None = None

    def ensure_ready(self) -> None:
        if self._manifest is not None and self.package_dir is not None:
            return
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
        interpretation: CurrentDayInterpretation | None,
        current_event_capsules: Sequence[CurrentEventCapsule],
        cutoff_at: datetime,
        max_exact_witnesses: int,
    ) -> DailyBrainContext:
        if self._manifest is None or self.package_dir is None:
            self.ensure_ready()
        assert self._manifest is not None
        assert self.package_dir is not None
        point_in_time_projection = (
            self.allow_point_in_time_projection
            and self._manifest.build_cutoff > cutoff_at
        )
        if self._manifest.build_cutoff > cutoff_at and not point_in_time_projection:
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
            candidate_limit = (
                DAILY_TEMPORAL_ANN_CANDIDATES
                if point_in_time_projection
                else DAILY_ANN_CANDIDATES
            )
            for vector in vectors:
                rows = connection.execute(
                    """
                    SELECT capsule_id,
                           1.0 - array_cosine_distance(embedding, ?::FLOAT[384]) AS score
                    FROM semantic_capsules
                    ORDER BY array_cosine_distance(embedding, ?::FLOAT[384]), capsule_id
                    LIMIT ?
                    """,
                    [vector, vector, candidate_limit],
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
                    [vector, vector, candidate_limit],
                ).fetchall()
                for claim_id, score in claim_rows:
                    claim_scores[str(claim_id)] = max(
                        float(score), claim_scores.get(str(claim_id), -1.0)
                    )
            selected_ids = _balanced_capsule_ids(
                connection,
                capsule_scores=capsule_scores,
                limit=DAILY_MAX_CAPSULES,
                available_before=cutoff_at if point_in_time_projection else None,
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
                available_before=cutoff_at if point_in_time_projection else None,
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
                "close_return_status_distribution": row.close_return_status_distribution,
            }
            for row in selected_capsules
        ]
        return DailyBrainContext(
            brain_version=self._manifest.brain_version,
            brain_package_root=self._manifest.package_root,
            brain_build_cutoff=self._manifest.build_cutoff,
            retrieval_basis=(
                "CURRENT_NEWS" if interpretation is None else "MODEL_INTERPRETATION"
            ),
            brain_projection_mode=(
                "POINT_IN_TIME_EVIDENCE_ONLY"
                if point_in_time_projection
                else "FULL_PACKAGE"
            ),
            current_event_capsules_sha256=sha256_text(
                canonical_json(
                    [row.model_dump(mode="json") for row in current_event_capsules]
                )
            ),
            interpretation_sha256=(
                sha256_text(canonical_json(interpretation.model_dump(mode="json")))
                if interpretation is not None
                else None
            ),
            compiled_brain_guidance=(
                [] if point_in_time_projection else _load_compiled_brain_guidance(self.package_dir)
            ),
            selected_semantic_capsules=selected_capsules,
            selected_mechanism_claims=selected_claims,
            population_statistics=population_statistics,
            current_vs_history_differences=_current_history_differences(interpretation, selected_capsules),
            beneficiary_graph=(
                [
                    {"capsule_id": row.capsule_id, "implications": row.beneficiary_implications}
                    for row in selected_capsules
                    if row.beneficiary_implications
                ]
                if point_in_time_projection
                else _projection_rows(
                    self.package_dir / "beneficiary_graph" / "summary.json",
                    selected_capsule_ids=set(selected_ids),
                )
            ),
            leader_selection_memory=(
                [
                    {"capsule_id": row.capsule_id, "implications": row.leader_selection_implications}
                    for row in selected_capsules
                    if row.leader_selection_implications
                ]
                if point_in_time_projection
                else _projection_rows(
                    self.package_dir / "leader_selection_memory" / "summary.json",
                    selected_capsule_ids=set(selected_ids),
                )
            ),
            continuation_memory=(
                [
                    {"capsule_id": row.capsule_id, "implications": row.continuation_implications}
                    for row in selected_capsules
                    if row.continuation_implications
                ]
                if point_in_time_projection
                else _projection_rows(
                    self.package_dir / "continuation_memory" / "summary.json",
                    selected_capsule_ids=set(selected_ids),
                )
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


def _load_reduce_nodes_from_database(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, SemanticReduceNode]:
    rows = connection.execute(
        "SELECT node_id, payload_json FROM reduce_nodes ORDER BY node_id"
    ).fetchall()
    output: dict[str, SemanticReduceNode] = {}
    for node_id_value, payload_value in rows:
        node_id = str(node_id_value)
        node = SemanticReduceNode.model_validate_json(str(payload_value))
        if node.node_id != node_id or node_id in output:
            raise ValueError("work database contains a duplicate or mismatched reduce node")
        output[node_id] = node
    return output


def _persist_reduce_node(
    connection: duckdb.DuckDBPyConnection,
    node: SemanticReduceNode,
) -> None:
    # Coverage IDs are reconstructed from the child graph on resume; storing each
    # ancestor's full ID union would make the work database grow superlinearly.
    connection.execute(
        "INSERT OR REPLACE INTO reduce_nodes VALUES (?, ?)",
        [
            node.node_id,
            canonical_json(
                node.model_copy(update={"covered_capsule_ids": []}).model_dump(
                    mode="json"
                )
            ),
        ],
    )


def _initialize_package_database(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: SourceMemorySnapshot,
) -> None:
    _attach_source_memory(connection, source=source)
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
        CREATE TABLE offline_compile_metadata (
            meta_key VARCHAR PRIMARY KEY,
            meta_value VARCHAR NOT NULL
        );
        """
    )


def _attach_source_memory(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: SourceMemorySnapshot,
) -> None:
    source_path = str(source.database_path).replace("'", "''")
    connection.execute(f"ATTACH '{source_path}' AS source_memory (READ_ONLY)")


def _write_compile_metadata(
    connection: duckdb.DuckDBPyConnection,
    *,
    values: Mapping[str, str],
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS offline_compile_metadata (
            meta_key VARCHAR PRIMARY KEY,
            meta_value VARCHAR NOT NULL
        )
        """
    )
    connection.execute("DELETE FROM offline_compile_metadata")
    connection.executemany(
        "INSERT INTO offline_compile_metadata VALUES (?, ?)",
        sorted((str(key), str(value)) for key, value in values.items()),
    )


def _copy_resume_work_database(
    source_path: Path,
    target_path: Path,
    *,
    expected_sha256: str,
    expected_wal_sha256: str | None,
) -> None:
    source_wal_path = Path(f"{source_path}.wal")
    target_wal_path = Path(f"{target_path}.wal")
    if target_path.exists() or target_wal_path.exists():
        raise FileExistsError(f"refusing to overwrite an existing work database: {target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_copy = target_path.with_name(f".{target_path.name}.{uuid4().hex}.copying")
    temporary_wal_copy = Path(f"{temporary_copy}.wal")
    source_wal_sha256 = (
        file_sha256(source_wal_path) if source_wal_path.is_file() else None
    )
    if source_wal_sha256 != expected_wal_sha256:
        raise ValueError("resume work database WAL SHA-256 does not match the attested value")
    wal_installed = False
    database_installed = False
    try:
        shutil.copy2(source_path, temporary_copy)
        if file_sha256(temporary_copy) != expected_sha256:
            raise ValueError("resume work database changed while it was copied")
        if source_wal_sha256 is not None:
            shutil.copy2(source_wal_path, temporary_wal_copy)
            if file_sha256(temporary_wal_copy) != source_wal_sha256:
                raise ValueError("resume work database WAL changed while it was copied")
        if file_sha256(source_path) != expected_sha256:
            raise ValueError("resume work database changed while it was copied")
        current_source_wal_sha256 = (
            file_sha256(source_wal_path) if source_wal_path.is_file() else None
        )
        if current_source_wal_sha256 != source_wal_sha256:
            raise ValueError("resume work database WAL changed while it was copied")
        if target_path.exists() or target_wal_path.exists():
            raise FileExistsError(
                f"refusing to overwrite an existing work database: {target_path}"
            )
        if source_wal_sha256 is not None:
            os.replace(temporary_wal_copy, target_wal_path)
            wal_installed = True
        os.replace(temporary_copy, target_path)
        database_installed = True
    except BaseException:
        if temporary_copy.is_file():
            temporary_copy.unlink()
        if temporary_wal_copy.is_file():
            temporary_wal_copy.unlink()
        if wal_installed and not database_installed and target_wal_path.is_file():
            target_wal_path.unlink()
        raise


def _adopt_matching_resume_work_database(
    source_path: Path,
    target_path: Path,
    *,
    expected_sha256: str,
    expected_wal_sha256: str | None,
) -> bool:
    """Restore a missing WAL only when the existing target is the exact source base copy."""
    target_wal_path = Path(f"{target_path}.wal")
    source_wal_path = Path(f"{source_path}.wal")
    if not target_path.is_file() or file_sha256(target_path) != expected_sha256:
        return False

    source_wal_sha256 = (
        file_sha256(source_wal_path) if source_wal_path.is_file() else None
    )
    if source_wal_sha256 != expected_wal_sha256:
        raise ValueError("resume work database WAL SHA-256 changed before adoption")
    if source_wal_sha256 is None:
        return not target_wal_path.exists()
    if target_wal_path.is_file():
        return file_sha256(target_wal_path) == source_wal_sha256
    if target_wal_path.exists():
        return False

    temporary_wal_copy = target_path.with_name(
        f".{target_path.name}.{uuid4().hex}.wal.copying"
    )
    try:
        shutil.copy2(source_wal_path, temporary_wal_copy)
        if file_sha256(temporary_wal_copy) != source_wal_sha256:
            raise ValueError("resume work database WAL changed while it was copied")
        if (
            file_sha256(source_path) != expected_sha256
            or file_sha256(source_wal_path) != source_wal_sha256
            or file_sha256(target_path) != expected_sha256
            or target_wal_path.exists()
        ):
            raise ValueError("resume work database changed while its WAL was restored")
        os.replace(temporary_wal_copy, target_wal_path)
    except BaseException:
        if temporary_wal_copy.is_file():
            temporary_wal_copy.unlink()
        raise
    return True


def _capsule_database_signatures(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, tuple[str, str, str]]:
    output: dict[str, tuple[str, str, str]] = {}
    cursor = connection.execute(
        "SELECT semantic_unit_id, capsule_id, payload_json FROM semantic_capsules ORDER BY semantic_unit_id"
    )
    while True:
        rows = cursor.fetchmany(1024)
        if not rows:
            break
        for semantic_unit_id, capsule_id, payload_json in rows:
            unit_id = str(semantic_unit_id)
            payload = json.loads(str(payload_json))
            if unit_id in output:
                raise ValueError(f"resume work database contains duplicate capsule unit: {unit_id}")
            output[unit_id] = (
                str(capsule_id),
                sha256_text(canonical_json(payload)),
                str(payload.get("member_record_root", "")),
            )
    return output


def _read_compile_metadata(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, str]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    if "offline_compile_metadata" not in tables:
        return {}
    return {
        str(key): str(value)
        for key, value in connection.execute(
            "SELECT meta_key, meta_value FROM offline_compile_metadata"
        ).fetchall()
    }


def _load_unit_builds_from_database(
    connection: duckdb.DuckDBPyConnection,
) -> list[_UnitBuild]:
    cursor = connection.execute(
        """
        WITH assignment_stats AS (
            SELECT primary_semantic_unit_id AS semantic_unit_id,
                   count(*) AS member_record_count,
                   list(record_id ORDER BY record_id) FILTER (WHERE outlier) AS outlier_record_ids
            FROM semantic_unit_assignments
            GROUP BY primary_semantic_unit_id
        )
        SELECT centroid.semantic_unit_id, centroid.category, centroid.primary_cell_id,
               centroid.evidence_polarity, stats.member_record_count,
               coalesce(stats.outlier_record_ids, []::VARCHAR[]),
               centroid.member_record_root, centroid.provenance_root, centroid.centroid
        FROM semantic_unit_centroids centroid
        JOIN assignment_stats stats USING (semantic_unit_id)
        ORDER BY centroid.category, centroid.semantic_unit_id
        """
    )
    builds: list[_UnitBuild] = []
    while True:
        rows = cursor.fetchmany(1024)
        if not rows:
            break
        for row in rows:
            centroid = tuple(float(value) for value in row[8])
            if len(centroid) != 384 or not all(math.isfinite(value) for value in centroid):
                raise ValueError(f"semantic centroid is invalid: {row[0]}")
            builds.append(
                _UnitBuild(
                    semantic_unit_id=str(row[0]),
                    category=str(row[1]),
                    primary_cell_id=str(row[2]),
                    evidence_polarity=str(row[3]),
                    member_record_count=int(row[4]),
                    outlier_record_ids=tuple(str(value) for value in row[5]),
                    member_record_root=str(row[6]),
                    provenance_root=str(row[7]),
                    centroid=centroid,
                )
            )
    return builds


def _validate_reusable_assignment_database(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: SourceMemorySnapshot,
    require_complete_capsules: bool,
) -> list[_UnitBuild]:
    counts = connection.execute(
        """
        SELECT
            (SELECT count(*) FROM semantic_unit_assignments),
            (SELECT count(DISTINCT record_id) FROM semantic_unit_assignments),
            (SELECT count(*) FROM source_memory.records),
            (SELECT count(*) FROM semantic_unit_centroids),
            (SELECT count(*) FROM semantic_capsules),
            (SELECT count(DISTINCT semantic_unit_id) FROM semantic_capsules)
        """
    ).fetchone()
    if counts is None:
        raise ValueError("resume work database count query returned no row")
    assignment_count, distinct_record_count, source_count, unit_count, capsule_count, capsule_units = (
        int(value) for value in counts
    )
    if source_count != source.record_count or assignment_count != source.record_count:
        raise ValueError(
            "resume work database does not contain the full source assignment population: "
            f"source={source_count}, assignments={assignment_count}, expected={source.record_count}"
        )
    if distinct_record_count != assignment_count:
        raise ValueError("resume work database contains duplicate primary assignments")
    if capsule_count > unit_count or capsule_units != capsule_count:
        raise ValueError("resume work database capsule rows are duplicated or exceed the unit set")
    if require_complete_capsules and capsule_count != unit_count:
        raise ValueError(
            "legacy resume database must contain one capsule per semantic unit: "
            f"capsules={capsule_count}, units={unit_count}"
        )
    checks = {
        "unassigned_source_records": """
            SELECT count(*) FROM source_memory.records record
            LEFT JOIN semantic_unit_assignments assignment USING (record_id)
            WHERE assignment.record_id IS NULL
        """,
        "assignment_without_source_record": """
            SELECT count(*) FROM semantic_unit_assignments assignment
            LEFT JOIN source_memory.records record USING (record_id)
            WHERE record.record_id IS NULL
        """,
        "assignment_without_centroid": """
            SELECT count(*) FROM semantic_unit_assignments assignment
            LEFT JOIN semantic_unit_centroids centroid
              ON centroid.semantic_unit_id = assignment.primary_semantic_unit_id
            WHERE centroid.semantic_unit_id IS NULL
        """,
        "source_stratum_mismatch": f"""
            SELECT count(*) FROM semantic_unit_assignments assignment
            JOIN source_memory.records record USING (record_id)
            JOIN semantic_unit_centroids centroid
              ON centroid.semantic_unit_id = assignment.primary_semantic_unit_id
            WHERE centroid.category <> ({_category_case_sql()})
               OR centroid.primary_cell_id <> record.primary_cell_id
               OR centroid.evidence_polarity <> coalesce(record.evidence_polarity, 'UNKNOWN')
        """,
        "capsule_identity_or_membership_mismatch": """
            SELECT count(*) FROM semantic_capsules capsule
            LEFT JOIN semantic_unit_centroids centroid USING (semantic_unit_id)
            WHERE centroid.semantic_unit_id IS NULL
               OR capsule.category <> centroid.category
               OR json_extract_string(capsule.payload_json, '$.capsule_id') <> capsule.capsule_id
               OR json_extract_string(capsule.payload_json, '$.semantic_unit_id') <> capsule.semantic_unit_id
               OR json_extract_string(capsule.payload_json, '$.member_record_root') <> centroid.member_record_root
               OR json_extract_string(capsule.payload_json, '$.provenance_root') <> centroid.provenance_root
        """,
        "capsule_member_count_mismatch": """
            WITH counts AS (
                SELECT primary_semantic_unit_id AS semantic_unit_id, count(*) AS member_count
                FROM semantic_unit_assignments GROUP BY 1
            )
            SELECT count(*) FROM semantic_capsules capsule
            JOIN counts USING (semantic_unit_id)
            WHERE try_cast(json_extract_string(capsule.payload_json, '$.member_record_count') AS BIGINT)
                  <> counts.member_count
        """,
    }
    for name, sql in checks.items():
        mismatch_row = connection.execute(sql).fetchone()
        if mismatch_row is None:
            raise ValueError(f"resume work database check returned no result: {name}")
        mismatch_count = int(mismatch_row[0])
        if mismatch_count:
            raise ValueError(f"resume work database failed {name}: {mismatch_count}")

    builds = _load_unit_builds_from_database(connection)
    if len(builds) != unit_count:
        raise ValueError("resume work database semantic unit/centroid counts differ")
    expected_roots = {
        row.semantic_unit_id: (
            row.member_record_count,
            row.member_record_root,
            row.provenance_root,
        )
        for row in builds
    }
    cursor = connection.execute(
        """
        SELECT assignment.primary_semantic_unit_id, assignment.record_id, record.source_sha256
        FROM semantic_unit_assignments assignment
        JOIN source_memory.records record USING (record_id)
        ORDER BY assignment.primary_semantic_unit_id, assignment.record_id
        """
    )
    current_unit: str | None = None
    member_ids: list[str] = []
    provenance_pairs: list[tuple[str, str]] = []
    verified_units = 0

    def verify_unit(unit_id: str) -> None:
        expected = expected_roots.get(unit_id)
        if expected is None:
            raise ValueError(f"resume assignment references an unknown unit: {unit_id}")
        count, member_root, provenance_root = expected
        if len(member_ids) != count:
            raise ValueError(f"resume assignment member count drifted: {unit_id}")
        if sha256_text(canonical_json(member_ids)) != member_root:
            raise ValueError(f"resume assignment member root drifted: {unit_id}")
        if sha256_text(canonical_json(provenance_pairs)) != provenance_root:
            raise ValueError(f"resume assignment provenance root drifted: {unit_id}")

    while True:
        rows = cursor.fetchmany(4096)
        if not rows:
            break
        for unit_id_value, record_id_value, source_sha_value in rows:
            unit_id = str(unit_id_value)
            record_id = str(record_id_value)
            if current_unit is not None and unit_id != current_unit:
                verify_unit(current_unit)
                verified_units += 1
                member_ids.clear()
                provenance_pairs.clear()
            current_unit = unit_id
            member_ids.append(record_id)
            provenance_pairs.append((record_id, str(source_sha_value)))
    if current_unit is not None:
        verify_unit(current_unit)
        verified_units += 1
    if verified_units != unit_count:
        raise ValueError(
            f"resume source roots cover {verified_units} units, expected {unit_count}"
        )
    return builds


def _build_semantic_assignments(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: SourceMemorySnapshot,
    progress_path: Path | None = None,
) -> list[_UnitBuild]:
    category_case = _category_case_sql()
    source_connection = duckdb.connect(str(source.database_path), read_only=True)
    _configure_offline_duckdb(
        source_connection,
        temp_directory=(progress_path.parent if progress_path is not None else source.project_root)
        / "duckdb_source_tmp",
    )
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


def _configure_offline_duckdb(
    connection: duckdb.DuckDBPyConnection,
    *,
    temp_directory: Path,
) -> None:
    """Bound DuckDB native memory and spill large planning joins to disk."""

    temp_directory.mkdir(parents=True, exist_ok=True)
    escaped_directory = str(temp_directory.resolve()).replace("'", "''")
    connection.execute(f"PRAGMA memory_limit='{OFFLINE_DUCKDB_MEMORY_LIMIT}'")
    connection.execute(f"PRAGMA temp_directory='{escaped_directory}'")


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
                member_record_count=len(member_ids),
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
    # Advanced indexing makes ``vectors`` a fresh dense array.  A large
    # population-derived stratum can recurse many levels deep, so retaining
    # every parent's temporary matrix while descending causes avoidable native
    # memory growth.  Materialize child indexes first, then release the parent
    # temporaries before entering the recursive calls; the geometry and split
    # predicate remain unchanged.
    left_indexes = indexes[left_mask]
    right_indexes = indexes[~left_mask]
    del vectors, centroid, distances, left_seed, right_seed, left_mask
    return [
        *_recursive_semantic_clusters(matrix, left_indexes, depth=depth + 1),
        *_recursive_semantic_clusters(matrix, right_indexes, depth=depth + 1),
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
                "member_record_count": build.member_record_count,
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
        representatives = row["representatives"]
        representative_count += len(representatives)
        payload_chars += sum(len(str(value["document"])) for value in representatives)
        if len(canonical_json(row).encode("utf-8")) <= MAX_LEAF_PROMPT_BYTES:
            projected_rows.append(row)
            continue
        oversized_units += 1
        chunked_records += len(representatives)
        representatives = [dict(value) for value in representatives]
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
    chunk_batch_count = sum(1 for _ in _pack_long_payload_chunks(chunk_inputs))
    return _LongPayloadPlan(
        projected_rows=projected_rows,
        chunk_inputs=chunk_inputs,
        representative_record_count=representative_count,
        representative_payload_char_count=payload_chars,
        oversized_unit_count=oversized_units,
        chunked_representative_record_count=chunked_records,
        long_payload_chunk_count=len(chunk_inputs),
        long_payload_chunk_map_call_count=chunk_batch_count,
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


def _attach_close_return_status_distributions(
    connection: duckdb.DuckDBPyConnection,
    capsules: list[SemanticMemoryCapsule],
) -> list[SemanticMemoryCapsule]:
    distributions: dict[str, dict[str, int]] = defaultdict(dict)
    cursor = connection.execute(
        """
        SELECT a.primary_semantic_unit_id,
               COALESCE(CAST(r.close_return_status AS VARCHAR), '__NULL__'),
               count(*)
        FROM semantic_unit_assignments a
        JOIN source_memory.records r USING (record_id)
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    )
    while True:
        rows = cursor.fetchmany(4096)
        if not rows:
            break
        for semantic_unit_id, status, count in rows:
            distributions[str(semantic_unit_id)][str(status)] = int(count)

    for capsule in capsules:
        status_distribution = distributions.pop(capsule.semantic_unit_id, None)
        if not status_distribution:
            raise ValueError(
                "semantic unit is missing its close-return status distribution: "
                f"{capsule.semantic_unit_id}"
            )
        if sum(status_distribution.values()) != capsule.member_record_count:
            raise ValueError(
                "close-return status population differs from capsule membership: "
                f"{capsule.semantic_unit_id}"
            )
        capsule.close_return_status_distribution = status_distribution
    if distributions:
        raise ValueError("close-return status distribution contains unknown semantic units")
    return capsules


def _pack_leaf_rows(rows: Iterable[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
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
        groups: list[list[SemanticMemoryCapsule]] = []
        current: list[SemanticMemoryCapsule] = []
        current_bytes = 0
        for capsule in ordered:
            capsule_bytes = len(
                canonical_json(_capsule_reduce_content(capsule)).encode("utf-8")
            )
            if capsule_bytes > MAX_REDUCE_LEAF_BYTES:
                raise ValueError(
                    f"semantic capsule {capsule.capsule_id} exceeds the reduce leaf budget"
                )
            candidate_bytes = current_bytes + capsule_bytes + int(bool(current))
            if current and candidate_bytes > MAX_REDUCE_LEAF_BYTES:
                groups.append(current)
                current = [capsule]
                current_bytes = capsule_bytes
            else:
                current.append(capsule)
                current_bytes = candidate_bytes
        if current:
            groups.append(current)
        for group in groups:
            capsule_ids = tuple(row.capsule_id for row in group)
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
                    synthesis="\n".join(
                        canonical_json(_capsule_reduce_content(row)) for row in group
                    ),
                )
            )
    return nodes


def _capsule_reduce_content(capsule: SemanticMemoryCapsule) -> dict[str, Any]:
    return {
        "capsule_id": capsule.capsule_id,
        "summary": capsule.event_or_mechanism_summary,
        "economic_transmission": capsule.economic_transmission,
        "market_narrative": capsule.market_narrative,
        "applicable_conditions": capsule.applicable_conditions,
        "failure_conditions": capsule.failure_conditions,
        "boundary_conditions": capsule.boundary_conditions,
        "novelty_modality_distinctions": capsule.novelty_modality_distinctions,
        "leader_selection_implications": capsule.leader_selection_implications,
        "beneficiary_implications": capsule.beneficiary_implications,
        "continuation_implications": capsule.continuation_implications,
    }


def _plan_reduce_graph(
    capsules: list[SemanticMemoryCapsule],
) -> tuple[list[_LeafNode], dict[str, Any]]:
    leaves = _capsule_leaf_nodes(capsules)
    categories = sorted({row.category for row in capsules})
    if not categories:
        raise ValueError("offline reducer cannot plan an empty capsule population")
    tasks: list[dict[str, Any]] = []
    category_roots: dict[str, str] = {}
    category_counts: dict[str, dict[str, int]] = {}
    for category in categories:
        category_leaves = [row for row in leaves if row.category == category]
        current = [
            SemanticReduceNode(
                node_id=row.node_id,
                child_node_ids=[],
                covered_capsule_ids=list(row.capsule_ids),
                covered_capsule_count=len(row.capsule_ids),
                coverage_root=_leaf_coverage_root(row.node_id, row.capsule_ids),
                evidence_capsule_ids=_leaf_reduce_evidence_ids(row.capsule_ids),
                synthesis=row.synthesis,
            )
            for row in category_leaves
        ]
        if not current:
            raise ValueError(f"offline reducer category is empty: {category}")
        internal_count = 0
        level = 0
        while len(current) > 1:
            groups = list(_pack_reduce_nodes(current))
            if len(groups) >= len(current):
                raise ValueError(
                    "offline reduce cannot converge in planned DAG: "
                    f"category={category} level={level} nodes={len(current)} groups={len(groups)}"
                )
            next_level: list[SemanticReduceNode] = []
            for group in groups:
                if len(group) == 1:
                    next_level.append(group[0])
                    continue
                child_ids = [row.node_id for row in group]
                node_id = stable_id("REDUCE", category, level, child_ids, length=20)
                tasks.append(
                    {
                        "node_id": node_id,
                        "kind": "category_reduce",
                        "category": category,
                        "level": level,
                        "child_node_ids": child_ids,
                    }
                )
                internal_count += 1
                next_level.append(
                    SemanticReduceNode(
                        node_id=node_id,
                        child_node_ids=child_ids,
                        covered_capsule_ids=[],
                        covered_capsule_count=sum(
                            row.covered_capsule_count for row in group
                        ),
                        coverage_root="planned",
                        evidence_capsule_ids=[],
                        synthesis="planned bounded output",
                    )
                )
            current = next_level
            level += 1
        review_children = [row.node_id for row in current]
        review_id = stable_id(
            "CATEGORY-REVIEW", category, level, review_children, length=20
        )
        tasks.append(
            {
                "node_id": review_id,
                "kind": "category_review",
                "category": category,
                "level": level,
                "child_node_ids": review_children,
            }
        )
        category_roots[category] = review_id
        category_counts[category] = {
            "capsules": sum(row.category == category for row in capsules),
            "leaf_nodes": len(category_leaves),
            "internal_reduce_nodes": internal_count,
            "review_nodes": 1,
        }
    world_children = [category_roots[key] for key in sorted(category_roots)]
    world_id = stable_id("WORLD", world_children, length=20)
    tasks.append(
        {
            "node_id": world_id,
            "kind": "world_root",
            "category": "world_model",
            "level": 0,
            "child_node_ids": world_children,
        }
    )
    node_ids = [str(row["node_id"]) for row in tasks]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("offline reducer DAG contains duplicate node identities")
    plan_body = {
        "schema_version": "nslab.offline_reduce_dag_plan.v1",
        "compiler_version": OFFLINE_COMPILER_VERSION,
        "capsule_count": len(capsules),
        "category_count": len(categories),
        "category_counts": category_counts,
        "reduce_leaf_count": len(leaves),
        "category_internal_reduce_count": sum(
            row["kind"] == "category_reduce" for row in tasks
        ),
        "category_review_count": len(categories),
        "world_root_count": 1,
        "total_model_tasks": len(tasks),
        "tasks": tasks,
    }
    plan = {
        **plan_body,
        "topology_sha256": sha256_text(canonical_json(plan_body)),
    }
    return leaves, plan


def _summarize_map_checkpoint_usage(
    checkpoint_usage_rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    map_rows = [
        row
        for row in checkpoint_usage_rows
        if str(row["purpose"]).startswith(
            ("offline_semantic_leaf.", "offline_long_payload_map.")
        )
    ]
    by_model: dict[str, dict[str, Any]] = {}
    for row in map_rows:
        key = canonical_json(row["model_config"])
        aggregate = by_model.setdefault(
            key,
            {"model_config": dict(row["model_config"]), "total": 0, "cache_hits": 0},
        )
        aggregate["total"] += 1
        aggregate["cache_hits"] += int(bool(row["cache_hit"]))
    return map_rows, {
        "map_checkpoint_request_count": len(map_rows),
        "map_checkpoint_hit_count": sum(
            int(bool(row["cache_hit"])) for row in map_rows
        ),
        "map_fresh_output_count": sum(
            int(not bool(row["cache_hit"])) for row in map_rows
        ),
        "map_output_counts_by_model": [by_model[key] for key in sorted(by_model)],
    }


def _planned_reduce_leaf_nodes(
    unit_rows: Sequence[dict[str, Any]],
) -> dict[str, list[_PlannedReduceNode]]:
    """Build deterministic, coverage-only leaf proxies for the zero-LLM plan.

    Runtime leaf buckets use the final capsule IDs, which include model-authored
    capsule prose and therefore do not exist during planning. Semantic-unit IDs
    provide deterministic proxy buckets without making a model call, but their
    bucket population is not a bound on model-derived capsule-ID buckets. The
    proxies retain every planned capsule ID and omit generated prose.
    """

    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in unit_rows:
        category = str(row["category"])
        semantic_unit_id = str(row["semantic_unit_id"])
        bucket = sha256_text(semantic_unit_id)[:2]
        buckets[(category, bucket)].append(semantic_unit_id)

    output: dict[str, list[_PlannedReduceNode]] = defaultdict(list)
    for (category, bucket), semantic_unit_ids in sorted(buckets.items()):
        covered = sorted(set(semantic_unit_ids))
        node_id = stable_id(
            "PLANNED-LEAF-BUCKET",
            category,
            bucket,
            covered,
            length=20,
        )
        output[category].append(
            _PlannedReduceNode(
                node_id=node_id,
                child_node_ids=(),
                covered_capsule_ids=tuple(covered),
                payload_bytes=_planned_reduce_payload_bytes(
                    node_id=node_id,
                    child_node_ids=(),
                    covered_capsule_ids=covered,
                ),
            )
        )
    return dict(sorted(output.items()))


def _estimate_reduce_review_call_count(
    leaves_by_category: Mapping[
        str, Sequence[SemanticReduceNode | _PlannedReduceNode]
    ],
) -> int:
    """Count reduce/review/root calls using the runtime byte-aware packer.

    The next-level proxies preserve child identity and covered capsule IDs but
    leave generated prose empty. This makes the projection deterministic while
    exercising the exact `_pack_reduce_nodes` byte and child limits for proxy
    inputs; it may under- or overestimate runtime. One category review is added
    per non-empty category, followed by the single world root call.
    """

    calls = 0
    for category, leaves in sorted(leaves_by_category.items()):
        current = [
            row
            if isinstance(row, _PlannedReduceNode)
            else _planned_reduce_node_from_model(row)
            for row in leaves
        ]
        level = 0
        while len(current) > 1:
            groups = list(_pack_planned_reduce_nodes(current))
            calls += len(groups)
            next_level = [
                _planned_reduce_node(
                    category=category,
                    level=level,
                    children=group,
                )
                for group in groups
            ]
            # A coverage-only proxy can stay over the byte budget forever when
            # it has multiple oversized children. Real model prose may shrink
            # at the next level, so stop this proxy simulation rather than
            # claiming an infinite tree or burning memory.
            if len(next_level) >= len(current):
                break
            current = next_level
            level += 1
        if current:
            calls += 1
    # `_reduce_world` always emits one root request for the category roots.
    return calls + 1


def _planned_reduce_node(
    *,
    category: str,
    level: int,
    children: Sequence[_PlannedReduceNode],
) -> _PlannedReduceNode:
    child_node_ids = tuple(row.node_id for row in children)
    covered = tuple(
        _unique(
            capsule_id
            for row in children
            for capsule_id in row.covered_capsule_ids
        )
    )
    node_id = stable_id(
        "PLANNED-REDUCE",
        category,
        level,
        child_node_ids,
        length=20,
    )
    return _PlannedReduceNode(
        node_id=node_id,
        child_node_ids=child_node_ids,
        covered_capsule_ids=covered,
        payload_bytes=(
            MAX_REDUCE_NODE_OUTPUT_BYTES + MAX_REDUCE_NODE_PAYLOAD_RESERVE_BYTES
        ),
    )


def _planned_reduce_node_from_model(node: SemanticReduceNode) -> _PlannedReduceNode:
    return _PlannedReduceNode(
        node_id=node.node_id,
        child_node_ids=tuple(node.child_node_ids),
        covered_capsule_ids=tuple(node.covered_capsule_ids),
        payload_bytes=(
            MAX_REDUCE_NODE_OUTPUT_BYTES + MAX_REDUCE_NODE_PAYLOAD_RESERVE_BYTES
            if node.child_node_ids
            else len(canonical_json(_reduce_child_payload(node)).encode("utf-8"))
        ),
    )


def _planned_reduce_payload_bytes(
    *,
    node_id: str,
    child_node_ids: Sequence[str],
    covered_capsule_ids: Sequence[str],
) -> int:
    """Serialize the compact reduce child payload used by the runtime packer."""

    return len(
        canonical_json(
            {
                "node_id": node_id,
                "child_node_ids": list(child_node_ids),
                "covered_capsule_count": len(covered_capsule_ids),
                "coverage_root": sha256_text(canonical_json(sorted(covered_capsule_ids))),
                "evidence_capsule_ids": _leaf_reduce_evidence_ids(covered_capsule_ids),
                "synthesis": "",
                "mechanisms": [],
                "conditions": [],
                "boundary_conditions": [],
                "failure_modes": [],
                "contradictions": [],
                "claims": [],
            }
        ).encode("utf-8")
    )


def _pack_planned_reduce_nodes(
    nodes: Sequence[_PlannedReduceNode],
) -> Iterable[list[_PlannedReduceNode]]:
    """Byte-aware packer equivalent using cached proxy payload sizes."""

    current: list[_PlannedReduceNode] = []
    current_bytes = 2  # `[]` in canonical JSON
    for node in nodes:
        candidate_bytes = current_bytes + node.payload_bytes + (1 if current else 0)
        if current and (
            len(current) + 1 > MAX_REDUCE_CHILDREN
            or candidate_bytes > MAX_REDUCE_PROMPT_BYTES - 8_192
        ):
            yield current
            current = [node]
            current_bytes = 2 + node.payload_bytes
        else:
            current.append(node)
            current_bytes = candidate_bytes
    if current:
        yield current


def _pack_reduce_nodes(
    nodes: list[SemanticReduceNode],
) -> Iterable[list[SemanticReduceNode]]:
    current: list[SemanticReduceNode] = []
    current_bytes = 2  # `[]` in canonical JSON
    for node in nodes:
        node_bytes = (
            MAX_REDUCE_NODE_OUTPUT_BYTES + MAX_REDUCE_NODE_PAYLOAD_RESERVE_BYTES
            if node.child_node_ids
            else len(canonical_json(_reduce_child_payload(node)).encode("utf-8"))
        )
        candidate_bytes = current_bytes + node_bytes + (1 if current else 0)
        if current and (
            len(current) + 1 > MAX_REDUCE_CHILDREN
            or candidate_bytes > MAX_REDUCE_PROMPT_BYTES - 8_192
        ):
            yield current
            current = [node]
            current_bytes = 2 + node_bytes
        else:
            current.append(node)
            current_bytes = candidate_bytes
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
    world: bool = False,
) -> str:
    allowed_capsule_ids = _unique(
        capsule_id for row in children for capsule_id in row.evidence_capsule_ids
    )
    payload = {
        "schema": (
            WORLD_REDUCE_PROMPT_VERSION
            if world
            else CATEGORY_REVIEW_PROMPT_VERSION
            if review
            else REDUCE_PROMPT_VERSION
        ),
        "node_id": node_id,
        "category": category,
        "review": review,
        "required_child_node_ids": [row.node_id for row in children],
        "available_evidence_capsule_ids": allowed_capsule_ids,
        "children": [_reduce_child_payload(row) for row in children],
    }
    prompt = (
        "Synthesize every supplied child summary without dropping positive, negative, "
        "near-miss, failure, or boundary evidence. The local compiler owns the complete "
        "capsule membership ledger; do not reproduce capsule ID lists or infer unseen "
        "mechanisms from coverage counts or hashes. Claims may cite only IDs in "
        "available_evidence_capsule_ids. Return the supplied node_id and every required "
        "child_node_id in order. Keep the response concise and within its schema limits."
        "\n---OFFLINE_SEMANTIC_REDUCE---\n" + canonical_json(payload)
    )
    prompt_bytes = len(prompt.encode("utf-8"))
    if prompt_bytes > MAX_REDUCE_PROMPT_BYTES:
        raise ValueError(
            f"offline reduce prompt exceeds byte contract: {prompt_bytes} "
            f"> {MAX_REDUCE_PROMPT_BYTES}"
        )
    return prompt


def _reduce_child_payload(node: SemanticReduceNode) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "child_node_ids": node.child_node_ids,
        "covered_capsule_count": node.covered_capsule_count,
        "coverage_root": node.coverage_root,
        "evidence_capsule_ids": node.evidence_capsule_ids,
        "synthesis": node.synthesis,
        "mechanisms": node.mechanisms,
        "conditions": node.conditions,
        "boundary_conditions": node.boundary_conditions,
        "failure_modes": node.failure_modes,
        "contradictions": node.contradictions,
        "claims": [claim.model_dump(mode="json") for claim in node.claims],
    }


def _leaf_reduce_evidence_ids(capsule_ids: Sequence[str]) -> list[str]:
    ordered = sorted(set(capsule_ids))
    if len(ordered) <= 4:
        return ordered
    indices = (0, len(ordered) // 3, (2 * len(ordered)) // 3, len(ordered) - 1)
    return _unique(ordered[index] for index in indices)


def _normalize_reduce_claim_citations(
    claims: Sequence[SemanticReduceClaimDraft],
    *,
    allowed_capsule_ids: set[str],
    error_message: str = "semantic reduce claim cited an unavailable capsule",
) -> tuple[list[MechanismClaimDraft], list[SemanticReduceCitationNormalization]]:
    normalized_claims: list[MechanismClaimDraft] = []
    normalizations: list[SemanticReduceCitationNormalization] = []
    citation_fields = ("supporting_capsule_ids", "contradicting_capsule_ids")
    for claim_index, claim in enumerate(claims):
        payload = claim.model_dump(mode="python")
        for field_name in citation_fields:
            normalized_ids: list[str] = []
            for original_value in payload[field_name]:
                if original_value in allowed_capsule_ids:
                    normalized_ids.append(original_value)
                    continue
                # Accept only the observed soft-hyphen/em-dash artifact after an allowed ID.
                exact_formatting_suffix = "\u00ad\u2014"
                if original_value.endswith(exact_formatting_suffix):
                    prefix = original_value[: -len(exact_formatting_suffix)]
                    if prefix in allowed_capsule_ids:
                        normalized_ids.append(prefix)
                        normalizations.append(
                            SemanticReduceCitationNormalization(
                                claim_index=claim_index,
                                field=field_name,
                                original_value=original_value,
                                normalized_capsule_id=prefix,
                                rule="allowed_capsule_id_plus_soft_hyphen_em_dash_suffix",
                            )
                        )
                        continue
                # Repair only the exact multilingual suffix confirmed in this checkpoint.
                exact_arabic_suffix = "\u0639\u0646\u062f"
                if original_value.endswith(exact_arabic_suffix):
                    prefix = original_value[: -len(exact_arabic_suffix)]
                    if prefix in allowed_capsule_ids:
                        normalized_ids.append(prefix)
                        normalizations.append(
                            SemanticReduceCitationNormalization(
                                claim_index=claim_index,
                                field=field_name,
                                original_value=original_value,
                                normalized_capsule_id=prefix,
                                rule="allowed_capsule_id_plus_exact_arabic_word_suffix",
                            )
                        )
                        continue
                prefix, separator, suffix = original_value.partition(" ")
                if not (
                    separator
                    and prefix in allowed_capsule_ids
                    and len(suffix) == 1
                    and not suffix.isascii()
                    and suffix.isalpha()
                ):
                    raise ValueError(error_message)
                normalized_ids.append(prefix)
                normalizations.append(
                    SemanticReduceCitationNormalization(
                        claim_index=claim_index,
                        field=field_name,
                        original_value=original_value,
                        normalized_capsule_id=prefix,
                    )
                )
            payload[field_name] = normalized_ids
        normalized_claims.append(MechanismClaimDraft(**payload))
    return normalized_claims, normalizations


def _normalize_reduce_child_identity(
    *,
    draft_child_node_ids: Sequence[str],
    expected_child_node_ids: Sequence[str],
    error_message: str = "semantic reduce output omitted or added children",
) -> SemanticReduceChildIdentityNormalization | None:
    expected = list(expected_child_node_ids)
    observed = list(draft_child_node_ids)
    if observed == expected:
        return None
    if not observed and expected:
        return SemanticReduceChildIdentityNormalization(
            original_child_node_ids=observed,
            restored_child_node_ids=expected,
        )
    raise ValueError(error_message)


def _leaf_coverage_root(node_id: str, capsule_ids: Sequence[str]) -> str:
    return sha256_text(
        canonical_json({"node_id": node_id, "capsule_ids": sorted(set(capsule_ids))})
    )


def _reduce_coverage_root(
    node_id: str,
    children: Sequence[SemanticReduceNode],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "node_id": node_id,
                "children": [
                    {
                        "node_id": child.node_id,
                        "covered_capsule_count": child.covered_capsule_count,
                        "coverage_root": child.coverage_root,
                    }
                    for child in children
                ],
            }
        )
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
    covered = [
        by_id[capsule_id]
        for capsule_id in node.covered_capsule_ids
        if capsule_id in by_id
    ]
    if len(covered) != len(node.covered_capsule_ids):
        raise ValueError("reduce claim source tree contains unknown capsule coverage")
    if not covered:
        return []
    # A reduce claim can be influenced by every input, not only the IDs it cites.
    node_available_from = max(row.available_from for row in covered)
    output: list[SynthesizedMechanismClaim] = []
    for draft in node.claims:
        supporting_ids = [value for value in draft.supporting_capsule_ids if value in by_id]
        contradicting_ids = [value for value in draft.contradicting_capsule_ids if value in by_id]
        referenced = [by_id[value] for value in [*supporting_ids, *contradicting_ids]]
        if not referenced:
            continue
        embedding = _normalized_mean(np.asarray([row.embedding for row in referenced], dtype=np.float32))
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
                available_from=node_available_from,
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
    if not capsules:
        return
    semantic_unit_ids = [row.semantic_unit_id for row in capsules]
    if len(set(semantic_unit_ids)) != len(semantic_unit_ids):
        raise ValueError("offline capsule output contains duplicate semantic unit IDs")
    rows = [
        (
            row.capsule_id,
            row.category,
            row.semantic_unit_id,
            row.available_from.isoformat(),
            row.embedding,
            canonical_json(row.model_dump(mode="json")),
        )
        for row in capsules
    ]
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            "DELETE FROM semantic_capsules "
            "WHERE semantic_unit_id IN (SELECT UNNEST(?))",
            [semantic_unit_ids],
        )
        connection.executemany(
            "INSERT INTO semantic_capsules VALUES (?, ?, ?, ?, ?, ?)", rows
        )
        persisted_counts = connection.execute(
            """
            SELECT count(*), count(DISTINCT semantic_unit_id)
            FROM semantic_capsules
            WHERE semantic_unit_id IN (SELECT UNNEST(?))
            """,
            [semantic_unit_ids],
        ).fetchone()
        if persisted_counts is None:
            raise ValueError("offline capsule persistence count query returned no row")
        written_count, written_units = persisted_counts
        if int(written_count) != len(capsules) or int(written_units) != len(capsules):
            raise ValueError(
                "offline capsule persistence did not produce one row per semantic unit"
            )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _write_claims_to_database(
    connection: duckdb.DuckDBPyConnection,
    claims: list[SynthesizedMechanismClaim],
) -> None:
    if not claims:
        raise ValueError("offline brain produced no synthesized mechanism claims")
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
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("DELETE FROM mechanism_claim_capsules")
        connection.execute("DELETE FROM mechanism_claims")
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
        connection.executemany(
            "INSERT INTO mechanism_claim_capsules VALUES (?, ?, ?)",
            relationships,
        )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _write_reduce_nodes_to_database(
    connection: duckdb.DuckDBPyConnection,
    nodes: list[SemanticReduceNode],
) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO reduce_nodes VALUES (?, ?)",
        [
            (
                row.node_id,
                canonical_json(
                    row.model_copy(update={"covered_capsule_ids": []}).model_dump(
                        mode="json"
                    )
                ),
            )
            for row in nodes
        ],
    )


def _write_reduce_leaf_coverage(
    package_dir: Path,
    leaves: Sequence[_LeafNode],
) -> None:
    rows = [
        {
            "node_id": row.node_id,
            "category": row.category,
            "capsule_ids": list(row.capsule_ids),
            "covered_capsule_count": len(row.capsule_ids),
            "coverage_root": _leaf_coverage_root(row.node_id, row.capsule_ids),
        }
        for row in sorted(leaves, key=lambda item: item.node_id)
    ]
    path = package_dir / "semantic_reduce_leaf_coverage.jsonl"
    _write_jsonl(path, rows)
    write_json(
        package_dir / "semantic_reduce_coverage_manifest.json",
        {
            "schema_version": "nslab.semantic_reduce_coverage_manifest.v1",
            "leaf_node_count": len(rows),
            "capsule_count": sum(row["covered_capsule_count"] for row in rows),
            "leaf_coverage_sha256": file_sha256(path),
        },
    )


def _drop_package_database_indexes(connection: duckdb.DuckDBPyConnection) -> None:
    expected_indexes = {
        "semantic_capsule_category_idx",
        "semantic_capsule_available_idx",
        "mechanism_claim_category_idx",
        "mechanism_claim_capsule_idx",
        "semantic_capsules_hnsw_idx",
        "mechanism_claims_hnsw_idx",
    }
    existing_indexes = {
        str(row[0])
        for row in connection.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
    }
    if {"semantic_capsules_hnsw_idx", "mechanism_claims_hnsw_idx"} & existing_indexes:
        try:
            connection.execute("LOAD vss")
        except duckdb.Error:
            connection.execute("INSTALL vss")
            connection.execute("LOAD vss")
    for index_name in sorted(expected_indexes & existing_indexes):
        connection.execute(f"DROP INDEX {index_name}")


def _finalize_package_database(connection: duckdb.DuckDBPyConnection) -> None:
    _drop_package_database_indexes(connection)
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
    semantic_unit_count: int,
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
    status_distribution_rows = sorted(
        (row.semantic_unit_id, status, count)
        for row in capsules
        for status, count in row.close_return_status_distribution.items()
    )
    close_return_status_accounted_record_count = sum(
        count for _, _, count in status_distribution_rows
    )
    return SemanticInfluenceManifest(
        brain_version=brain_version,
        record_count=int(record_count),
        primary_assignment_count=int(record_count),
        distinct_primary_assigned_record_count=int(distinct_count),
        unassigned_record_count=0,
        duplicate_primary_assignment_count=duplicate_count,
        semantic_unit_count=semantic_unit_count,
        rare_outlier_unit_count=len(outlier_unit_ids),
        rare_outlier_represented_unit_count=len(outlier_unit_ids.intersection(capsule_units)),
        unrepresented_reasoning_unit_count=len(reasoning_units - capsule_units),
        leaf_covered_semantic_unit_count=len(capsule_units),
        reduce_covered_capsule_count=world_root.covered_capsule_count,
        final_covered_capsule_count=world_root.covered_capsule_count,
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
        reduce_tree_root=world_root.coverage_root,
        close_return_status_accounted_record_count=(
            close_return_status_accounted_record_count
        ),
        close_return_status_distribution_root=sha256_text(
            canonical_json(status_distribution_rows)
        ),
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
                "close_return_status_distribution": row.close_return_status_distribution,
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
    available_before: datetime | None = None,
) -> list[str]:
    ordered = sorted(capsule_scores, key=lambda key: (-capsule_scores[key], key))
    payloads = {
        str(capsule_id): SemanticMemoryCapsule.model_validate_json(str(payload))
        for capsule_id, payload in connection.execute(
            "SELECT capsule_id, payload_json FROM semantic_capsules WHERE capsule_id IN (SELECT unnest(?))",
            [ordered],
        ).fetchall()
    }
    if available_before is not None:
        ordered = [
            capsule_id
            for capsule_id in ordered
            if capsule_id in payloads
            and payloads[capsule_id].available_from <= available_before
        ]
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
    available_before: datetime | None = None,
) -> list[SynthesizedMechanismClaim]:
    linked_ids = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT claim_id FROM mechanism_claim_capsules "
            "WHERE capsule_id IN (SELECT unnest(?::VARCHAR[])) "
            "ORDER BY claim_id LIMIT ?",
            [sorted(selected_capsule_ids), max(limit * 16, limit)],
        ).fetchall()
    }
    candidate_ids = sorted(
        linked_ids,
        key=lambda claim_id: (
            -claim_scores.get(claim_id, -1.0),
            claim_id,
        ),
    )
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
    claims = [
        SynthesizedMechanismClaim.model_validate_json(payloads[claim_id])
        for claim_id in candidate_ids
        if claim_id in payloads
    ]
    if available_before is not None:
        claims = [claim for claim in claims if claim.available_from <= available_before]
    return claims[:limit]


def _daily_query_texts(
    interpretation: CurrentDayInterpretation | None,
    capsules: Sequence[CurrentEventCapsule],
) -> list[str]:
    values = [
        "\n".join(
            [
                row.representative_title,
                *row.predicate_exact_sentences,
                *row.issuer_company_literals,
                *row.counterparty_literals,
                *row.numeric_unit_literals,
                *row.modality_literals,
            ]
        )
        for row in capsules
    ]
    if interpretation is not None:
        values.extend(
            [
                *interpretation.retrieval_queries,
                *interpretation.policy_industry_macro_mechanisms,
                *interpretation.beneficiary_paths,
            ]
        )
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
    interpretation: CurrentDayInterpretation | None,
    capsules: list[SemanticMemoryCapsule],
) -> list[str]:
    historical_conditions = {
        value for capsule in capsules for value in [*capsule.applicable_conditions, *capsule.boundary_conditions]
    }
    return [
        *[
            f"current uncertainty: {value}"
            for value in (
                interpretation.uncertainties if interpretation is not None else []
            )
        ],
        *[f"historical boundary: {value}" for value in sorted(historical_conditions)],
    ]


def _load_compiled_brain_guidance(package_dir: Path) -> list[CompiledBrainGuidance]:
    categories = sorted((package_dir / "category_brain").glob("*.md"))
    if not categories:
        raise ValueError("selected BrainPackage has no compiled category guidance")
    output: list[CompiledBrainGuidance] = []
    for path in [package_dir / "world_model.md", *categories]:
        content = path.read_bytes().decode("utf-8")
        if not content.strip():
            raise ValueError(f"compiled brain guidance is empty: {path.name}")
        output.append(
            CompiledBrainGuidance(
                artifact=path.relative_to(package_dir).as_posix(),
                sha256=sha256_text(content),
                content=content,
            )
        )
    return output


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
        return np.asarray(value, dtype=np.float32)
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
    completed_model_node_count: int | None = None,
    total_model_node_count: int | None = None,
    current_model_node_id: str | None = None,
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
            "completed_model_node_count": completed_model_node_count,
            "total_model_node_count": total_model_node_count,
            "current_model_node_id": current_model_node_id,
            "updated_at": now_kst().isoformat(),
        },
    )


def _trace_offline_llm(
    settings: Settings,
    provider: LLMProvider,
    model_config: dict[str, Any],
    *,
    checkpoint_dir: Path | None = None,
    compatible_checkpoint_model_configs: Sequence[dict[str, Any]] | None = None,
) -> LLMProvider:
    current_model_config = {
        **model_config,
        "compiler_version": OFFLINE_COMPILER_VERSION,
    }
    compatible_configs: list[dict[str, Any]] = []
    for candidate in compatible_checkpoint_model_configs or []:
        config = {**candidate, "compiler_version": OFFLINE_COMPILER_VERSION}
        compatible_configs.append(config)
    for candidate in [current_model_config, *compatible_configs]:
        if candidate["compiler_version"] == OFFLINE_COMPILER_VERSION:
            compatible_configs.append(
                {
                    **candidate,
                    "compiler_version": LEGACY_MAP_CHECKPOINT_COMPILER_VERSION,
                }
            )
    if isinstance(provider, TracingLLMProvider):
        if checkpoint_dir is not None and provider.checkpoint_dir.resolve() != checkpoint_dir.resolve():
            raise ValueError("wrapped LLM provider uses a different checkpoint directory")
        provider.configure_checkpoint_identity(
            model_config=current_model_config,
            default_metadata={"compiler_version": OFFLINE_COMPILER_VERSION},
            compatible_checkpoint_model_configs=compatible_configs,
        )
        return provider
    return TracingLLMProvider(
        provider,
        trace_dir=settings.path(settings.output_dirs.traces),
        checkpoint_dir=checkpoint_dir,
        model_config=current_model_config,
        compatible_checkpoint_model_configs=compatible_configs,
        default_metadata={"compiler_version": OFFLINE_COMPILER_VERSION},
        max_retries=settings.llm.max_retries,
    )
