"""Immutable DuckDB FTS/HNSW snapshots for production memory retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import tempfile
import threading
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal, cast

import duckdb
import numpy as np
import numpy.typing as npt

from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    MemoryCellEntry,
    MemoryCellMembership,
    MemoryCellSnapshotManifest,
)
from news_scalping_lab.memory.cells import (
    MEMORY_CELL_CLUSTERING_VERSION,
    MEMORY_CELL_MEMBERSHIP_RULE,
    MEMORY_CELL_MEMBERSHIP_RULE_VERSION,
    MEMORY_CELL_NORMALIZER_VERSION,
    MEMORY_CELL_SCHEMA_VERSION,
    MEMORY_CELL_SIGNATURE_BITS,
    MEMORY_VECTOR_QUANTIZATION_SCALE,
    MISSING_STRUCTURAL_CONTEXT,
    MemoryCellBuild,
    RecordMemoryDocumentResolver,
    _cell_id,
    _cosine_similarity,
    _secondary_cells,
    build_memory_cells,
    build_record_memory_documents,
    independent_unit_type,
    normalized_quantized_sum,
    record_independent_unit_id,
    vector_signatures_and_margins,
)
from news_scalping_lab.memory.statistics import (
    POPULATION_STATISTICS_VERSION,
    project_population_record,
)
from news_scalping_lab.records.hashing import (
    brain_record_envelope_hashes,
    brain_record_envelope_sha256,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import (
    POLARITY_CLASSIFIER_VERSION,
    record_routing_metadata,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import (
    AsyncEmbeddingProviderAdapter,
    DeterministicHashEmbeddingProvider,
    LocalEmbeddingProvider,
)
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    now_kst,
    parse_datetime,
    read_json,
    relative_to_root,
    sha256_text,
    write_json,
)

MEMORY_INDEX_SCHEMA_VERSION = "nslab.production_memory_index.v3"
MEMORY_INDEX_ROOT = Path("memory/retrieval_index")
MEMORY_SNAPSHOT_DIR = "snapshots"
MEMORY_CURRENT_POINTER = "current.json"
MEMORY_AS_OF_REGISTRY = "as_of_registry.json"
MEMORY_DATABASE_FILE = "memory.duckdb"
MEMORY_CELL_FILE = "cells.jsonl"
MEMORY_MEMBERSHIP_FILE = "memberships.jsonl"
MEMORY_SOURCE_HASH_FILE = "source_record_hashes.jsonl"
MEMORY_FUTURE_HASH_FILE = "excluded_future_record_hashes.jsonl"
MEMORY_ROUTING_FILE = "routing_metadata.jsonl"
MEMORY_EMBEDDING_HASH_FILE = "embedding_hashes.jsonl"
MEMORY_AVAILABILITY_PROJECTION_FILE = "availability_projection.jsonl"
MEMORY_MANIFEST_FILE = "manifest.json"
REPLAY_AVAILABILITY_PROJECTION_VERSION = "nslab.replay_availability_projection.v1"
MEMORY_INDEX_EMBEDDING_BATCH_SIZE = 128
MEMORY_INDEX_QUERY_CANDIDATE_MULTIPLIER = 4
MEMORY_INDEX_STREAMING_AUDIT_THRESHOLD = 10_000
_ORIGINAL_LIST_RECORDS = BrainRecordStore.list_records
_PROCESS_VERIFIED_DATABASE_FILES: dict[str, tuple[int, int, int]] = {}


@dataclass(frozen=True)
class MemoryCellCandidate:
    cell_id: str
    score: float
    ann_score: float | None
    fts_score: float | None
    primary_member_count: int
    independent_unit_count: int


@dataclass(frozen=True)
class ReplayAvailabilityOverride:
    """Evaluation-only availability derived without changing source records."""

    episode_id: str
    source_trade_date: date
    replay_available_from: datetime
    derivation: str

    def __post_init__(self) -> None:
        if (
            not self.episode_id.strip()
            or not self.derivation.strip()
            or as_kst(self.replay_available_from).date() <= self.source_trade_date
        ):
            raise ValueError("replay availability override is invalid")


@dataclass(frozen=True)
class MemoryCellMember:
    record_id: str
    primary_cell_id: str
    matched_as: str
    independent_unit_id: str
    available_from: datetime
    routing_disposition: str
    evidence_polarity: str
    label_quality: str
    memory_lanes: tuple[str, ...]


@dataclass(frozen=True)
class PopulationCellMember:
    record_id: str
    independent_unit_id: str
    independent_unit_type: str
    primary_cell_id: str
    matched_cell_ids: tuple[str, ...]
    trade_date: date
    record_type: str
    training_eligible: bool
    routing_disposition: str
    evidence_polarity: str
    label_quality: str
    memory_lanes: tuple[str, ...]
    path_type: str
    regime_cluster: str
    high_return_pct: float | None
    close_return_pct: float | None
    upper_limit_touched: bool | None
    outcome_observed: bool
    sample_weight: float
    high_return_status: str
    close_return_status: str
    upper_limit_status: str
    sample_weight_status: str

    def __post_init__(self) -> None:
        valid_statuses = {"VALID", "MISSING", "INVALID_CONFLICT"}
        for value, status, name in (
            (self.high_return_pct, self.high_return_status, "high_return"),
            (self.close_return_pct, self.close_return_status, "close_return"),
            (self.upper_limit_touched, self.upper_limit_status, "upper_limit"),
        ):
            if status not in valid_statuses:
                raise ValueError(f"unsupported {name} population status")
            if (value is not None) is not (status == "VALID"):
                raise ValueError(f"{name} value conflicts with population status")
        if self.sample_weight_status not in {
            "VALID",
            "DEFAULT",
            "INVALID_CONFLICT",
        }:
            raise ValueError("unsupported sample weight population status")
        if (self.sample_weight > 0.0) is not (self.sample_weight_status in {"VALID", "DEFAULT"}):
            raise ValueError("sample weight value conflicts with population status")
        observed = any(
            status == "VALID"
            for status in (
                self.high_return_status,
                self.close_return_status,
                self.upper_limit_status,
            )
        )
        if self.outcome_observed is not observed:
            raise ValueError("outcome_observed conflicts with metric statuses")


@dataclass(frozen=True)
class RepresentativeSourceRecord:
    record_id: str
    embedding: tuple[float, ...]
    document: str
    source_sha256: str
    provenance_source_ids: tuple[str, ...]


@dataclass(frozen=True)
class _StreamingBuildState:
    record_count: int
    future_record_count: int
    next_available_from: datetime | None
    retained_record_count: int
    dimensions: int
    max_available_from: datetime
    disposition_counts: Counter[str]
    cell_count: int
    secondary_membership_count: int
    independent_unit_count: int
    unsupported_reasoning_record_count: int
    unsupported_reasoning_record_ids_sha256: str
    readiness: dict[str, bool]


@dataclass(frozen=True)
class _FinalizedDatabaseState:
    cell_count: int
    secondary_membership_count: int
    independent_unit_count: int
    unsupported_reasoning_record_count: int
    unsupported_reasoning_record_ids_sha256: str
    readiness: dict[str, bool]


class ProductionMemoryIndex:
    """Build and query versioned SQL indexes without Python corpus scans."""

    def __init__(
        self,
        root: Path,
        *,
        embedding_provider: LocalEmbeddingProvider,
        production: bool = True,
        embedding_batch_size: int = MEMORY_INDEX_EMBEDDING_BATCH_SIZE,
    ) -> None:
        if embedding_batch_size < 1:
            raise ValueError("embedding_batch_size must be positive")
        self.root = root.resolve()
        self.embedding_provider = embedding_provider
        self.production = production
        self.embedding_batch_size = embedding_batch_size
        self.index_root = self.root / MEMORY_INDEX_ROOT
        self.snapshots_root = self.index_root / MEMORY_SNAPSHOT_DIR
        self.current_pointer_path = self.index_root / MEMORY_CURRENT_POINTER
        self.as_of_registry_path = self.index_root / MEMORY_AS_OF_REGISTRY
        self._verified_database_files = _PROCESS_VERIFIED_DATABASE_FILES
        self._runtime_connection_state = threading.local()
        if production and not _is_production_embedding_provider(embedding_provider):
            raise ValueError("production memory index requires an attested real embedding provider")

    def build(
        self,
        *,
        as_of: datetime | None = None,
        promote_current: bool | None = None,
        stage_only: bool = False,
        cutoff_mode: Literal["live", "explicit"] | None = None,
        reuse_embeddings_from_snapshot_id: str | None = None,
        replay_availability_by_episode: Mapping[str, ReplayAvailabilityOverride] | None = None,
    ) -> MemoryCellSnapshotManifest:
        if stage_only and promote_current:
            raise ValueError("a staged memory snapshot cannot be promoted during build")
        cutoff = as_kst(as_of or now_kst())
        source_generation_sha256 = self._record_store_generation_sha256()
        resolved_cutoff_mode = cutoff_mode or ("explicit" if as_of is not None else "live")
        if resolved_cutoff_mode not in {"live", "explicit"}:
            raise ValueError("cutoff_mode must be live or explicit")
        if reuse_embeddings_from_snapshot_id is not None and (
            not stage_only or as_of is None or resolved_cutoff_mode != "explicit"
        ):
            raise ValueError("future snapshot embedding reuse is allowed only for a staged explicit as-of build")
        if replay_availability_by_episode is not None and (
            not stage_only
            or as_of is None
            or resolved_cutoff_mode != "explicit"
            or reuse_embeddings_from_snapshot_id is None
        ):
            raise ValueError("replay availability is allowed only for a staged explicit reuse build")
        availability_projection_bytes = (
            _availability_projection_bytes(replay_availability_by_episode)
            if replay_availability_by_episode is not None
            else None
        )
        availability_projection_sha256 = (
            hashlib.sha256(availability_projection_bytes).hexdigest()
            if availability_projection_bytes is not None
            else None
        )
        cutoff_identity = "live_partition" if resolved_cutoff_mode == "live" else f"explicit:{cutoff.isoformat()}"
        embedding_method = self.embedding_provider.embedding_method
        parent = (
            self._embedding_reuse_parent(
                reuse_embeddings_from_snapshot_id,
                embedding_method=embedding_method,
                source_generation_sha256=source_generation_sha256,
            )
            if reuse_embeddings_from_snapshot_id is not None
            else self._latest_compatible_snapshot(
                max_available_from=cutoff,
                embedding_method=embedding_method,
            )
        )
        self.snapshots_root.mkdir(parents=True, exist_ok=True)
        reusable = self._reusable_snapshot(
            cutoff=cutoff,
            cutoff_identity=cutoff_identity,
            source_generation_sha256=source_generation_sha256,
            embedding_method=embedding_method,
            availability_projection_sha256=availability_projection_sha256,
        )
        if reusable is not None:
            if self._record_store_generation_sha256() != source_generation_sha256:
                raise ValueError("record store changed while memory snapshot was verified")
            if not stage_only:
                if self._should_activate(
                    as_of=as_of,
                    promote_current=promote_current,
                ):
                    self.activate(reusable, requested_cutoff=cutoff)
                else:
                    self._register_snapshot(reusable, requested_cutoff=cutoff)
            return reusable
        build_dir = Path(tempfile.mkdtemp(prefix=".memory-index-", dir=self.snapshots_root))
        try:
            state = self._build_streaming_database(
                build_dir,
                cutoff=cutoff,
                parent=parent,
                replay_availability_by_episode=replay_availability_by_episode,
                availability_projection_bytes=availability_projection_bytes,
            )
            database_path = build_dir / MEMORY_DATABASE_FILE
            source_hash_path = build_dir / MEMORY_SOURCE_HASH_FILE
            future_hash_path = build_dir / MEMORY_FUTURE_HASH_FILE
            routing_path = build_dir / MEMORY_ROUTING_FILE
            embedding_hash_path = build_dir / MEMORY_EMBEDDING_HASH_FILE
            cell_path = build_dir / MEMORY_CELL_FILE
            membership_path = build_dir / MEMORY_MEMBERSHIP_FILE
            availability_projection_path = (
                build_dir / MEMORY_AVAILABILITY_PROJECTION_FILE if availability_projection_bytes is not None else None
            )
            corpus_manifest_sha256 = file_sha256(source_hash_path)
            routing_metadata_sha256 = _routing_root_from_database(database_path)
            snapshot_identity: dict[str, object] = {
                "schema_version": MEMORY_INDEX_SCHEMA_VERSION,
                "corpus_manifest_sha256": corpus_manifest_sha256,
                "source_generation_sha256": source_generation_sha256,
                "cutoff_identity": cutoff_identity,
                "max_available_from": state.max_available_from.isoformat(),
                "next_available_from": (
                    state.next_available_from.isoformat() if state.next_available_from is not None else None
                ),
                "embedding_method": embedding_method,
                "embedding_dimensions": state.dimensions,
                "clustering_version": MEMORY_CELL_CLUSTERING_VERSION,
                "normalizer_version": MEMORY_CELL_NORMALIZER_VERSION,
                "cell_schema_version": MEMORY_CELL_SCHEMA_VERSION,
                "polarity_classifier_version": POLARITY_CLASSIFIER_VERSION,
                "population_projection_version": POPULATION_STATISTICS_VERSION,
                "unsupported_reasoning_record_count": (state.unsupported_reasoning_record_count),
                "unsupported_reasoning_record_ids_sha256": (state.unsupported_reasoning_record_ids_sha256),
                "routing_metadata_sha256": routing_metadata_sha256,
                "future_hashes_sha256": file_sha256(future_hash_path),
                "cells_sha256": file_sha256(cell_path),
                "memberships_sha256": file_sha256(membership_path),
                "embedding_hashes_sha256": file_sha256(embedding_hash_path),
            }
            if availability_projection_path is not None:
                snapshot_identity.update(
                    {
                        "availability_mode": "replay_available_from",
                        "availability_projection_version": (REPLAY_AVAILABILITY_PROJECTION_VERSION),
                        "availability_projection_sha256": file_sha256(availability_projection_path),
                        "evaluation_only": True,
                    }
                )
            snapshot_id = _snapshot_id(snapshot_identity)
            final_dir = self.snapshots_root / snapshot_id
            final_relative = relative_to_root(final_dir, self.root)
            real_embedding = _is_production_embedding_provider(self.embedding_provider)
            production_ready = (
                real_embedding
                and state.record_count > 0
                and state.unsupported_reasoning_record_count == 0
                and all(state.readiness.values())
            )
            manifest = MemoryCellSnapshotManifest(
                snapshot_id=snapshot_id,
                corpus_manifest_sha256=corpus_manifest_sha256,
                source_generation_sha256=source_generation_sha256,
                as_of_cutoff=cutoff,
                cutoff_identity=cutoff_identity,
                max_available_from=state.max_available_from,
                embedding_provider=embedding_method.split(":", 1)[0],
                embedding_model=embedding_method,
                real_embedding=real_embedding,
                embedding_dimensions=state.dimensions,
                clustering_version=MEMORY_CELL_CLUSTERING_VERSION,
                normalizer_version=MEMORY_CELL_NORMALIZER_VERSION,
                cell_schema_version=MEMORY_CELL_SCHEMA_VERSION,
                polarity_classifier_version=POLARITY_CLASSIFIER_VERSION,
                population_projection_version=POPULATION_STATISTICS_VERSION,
                routing_metadata_sha256=routing_metadata_sha256,
                availability_mode=(
                    "replay_available_from" if availability_projection_path is not None else "source_available_from"
                ),
                availability_projection_version=(
                    REPLAY_AVAILABILITY_PROJECTION_VERSION if availability_projection_path is not None else None
                ),
                availability_projection=(
                    ArtifactReference(
                        artifact_path=(f"{final_relative}/{MEMORY_AVAILABILITY_PROJECTION_FILE}"),
                        sha256=file_sha256(availability_projection_path),
                        item_count=len(replay_availability_by_episode or {}),
                    )
                    if availability_projection_path is not None
                    else None
                ),
                evaluation_only=availability_projection_path is not None,
                record_count=state.record_count,
                excluded_future_record_count=state.future_record_count,
                next_available_from=state.next_available_from,
                reasoning_record_count=state.disposition_counts["REASONING"],
                context_record_count=state.disposition_counts["CONTEXT"],
                audit_record_count=state.disposition_counts["AUDIT"],
                quarantined_record_count=state.disposition_counts["QUARANTINED"],
                cell_count=state.cell_count,
                primary_membership_count=state.record_count,
                secondary_membership_count=state.secondary_membership_count,
                independent_unit_count=state.independent_unit_count,
                unsupported_reasoning_record_count=(state.unsupported_reasoning_record_count),
                unsupported_reasoning_record_ids_sha256=(state.unsupported_reasoning_record_ids_sha256),
                parent_snapshot_id=parent.snapshot_id if parent is not None else None,
                retained_record_count=state.retained_record_count,
                added_record_count=state.record_count - state.retained_record_count,
                source_record_hashes=ArtifactReference(
                    artifact_path=f"{final_relative}/{MEMORY_SOURCE_HASH_FILE}",
                    sha256=file_sha256(source_hash_path),
                    item_count=state.record_count,
                ),
                excluded_future_record_hashes=ArtifactReference(
                    artifact_path=f"{final_relative}/{MEMORY_FUTURE_HASH_FILE}",
                    sha256=file_sha256(future_hash_path),
                    item_count=state.future_record_count,
                ),
                routing_metadata=ArtifactReference(
                    artifact_path=f"{final_relative}/{MEMORY_ROUTING_FILE}",
                    sha256=file_sha256(routing_path),
                    item_count=state.record_count,
                ),
                embedding_hashes=ArtifactReference(
                    artifact_path=f"{final_relative}/{MEMORY_EMBEDDING_HASH_FILE}",
                    sha256=file_sha256(embedding_hash_path),
                    item_count=state.record_count,
                ),
                cell_entries=ArtifactReference(
                    artifact_path=f"{final_relative}/{MEMORY_CELL_FILE}",
                    sha256=file_sha256(cell_path),
                    item_count=state.cell_count,
                ),
                memberships=ArtifactReference(
                    artifact_path=f"{final_relative}/{MEMORY_MEMBERSHIP_FILE}",
                    sha256=file_sha256(membership_path),
                    item_count=state.record_count,
                ),
                database=ArtifactReference(
                    artifact_path=f"{final_relative}/{MEMORY_DATABASE_FILE}",
                    sha256=file_sha256(database_path),
                    item_count=state.record_count,
                ),
                metadata_index_ready=state.readiness["metadata_index_ready"],
                fts_index_ready=state.readiness["fts_index_ready"],
                hnsw_index_ready=state.readiness["hnsw_index_ready"],
                provenance_graph_ready=state.readiness["provenance_graph_ready"],
                production_ready=production_ready,
            )
            write_json(build_dir / MEMORY_MANIFEST_FILE, manifest.model_dump(mode="json"))
            if self._record_store_generation_sha256() != source_generation_sha256:
                raise ValueError("record store changed while memory snapshot was built")
            existing = self._existing_snapshot(final_dir)
            if existing is not None:
                shutil.rmtree(build_dir)
                manifest = existing
            else:
                try:
                    os.rename(build_dir, final_dir)
                except FileExistsError:
                    existing = self._existing_snapshot(final_dir)
                    if existing is None:
                        raise ValueError("memory snapshot publication conflict") from None
                    shutil.rmtree(build_dir)
                    manifest = existing
            if not stage_only:
                if self._should_activate(
                    as_of=as_of,
                    promote_current=promote_current,
                ):
                    self.activate(manifest, requested_cutoff=cutoff)
                else:
                    self._register_snapshot(manifest, requested_cutoff=cutoff)
            return manifest
        except Exception:
            if build_dir.exists():
                shutil.rmtree(build_dir)
            raise

    def _build_streaming_database(
        self,
        build_dir: Path,
        *,
        cutoff: datetime,
        parent: MemoryCellSnapshotManifest | None,
        replay_availability_by_episode: Mapping[str, ReplayAvailabilityOverride] | None,
        availability_projection_bytes: bytes | None,
    ) -> _StreamingBuildState:
        batch_size = self.embedding_batch_size
        resolver = RecordMemoryDocumentResolver(self.root)
        if replay_availability_by_episode is not None:
            _validate_replay_projection_file_scope(
                self.root,
                projection_episode_ids=set(replay_availability_by_episode),
            )
        parent_hashes = self._snapshot_source_hashes(parent)
        parent_connection = (
            _connect_index(self.root / parent.database.artifact_path, read_only=True) if parent is not None else None
        )
        connection: duckdb.DuckDBPyConnection | None = None
        dimensions = 0
        record_count = 0
        future_count = 0
        next_available_from: datetime | None = None
        retained_count = 0
        max_available_from: datetime | None = None
        disposition_counts: Counter[str] = Counter()
        cell_sums: dict[str, npt.NDArray[np.int64]] = {}
        reasoning_sums: dict[str, npt.NDArray[np.int64]] = {}
        cell_counts: Counter[str] = Counter()
        reasoning_counts: Counter[str] = Counter()
        database_path = build_dir / MEMORY_DATABASE_FILE
        source_file = (build_dir / MEMORY_SOURCE_HASH_FILE).open("wb")
        future_file = (build_dir / MEMORY_FUTURE_HASH_FILE).open("wb")
        routing_file = (build_dir / MEMORY_ROUTING_FILE).open("wb")
        embedding_file = (build_dir / MEMORY_EMBEDDING_HASH_FILE).open("wb")
        if availability_projection_bytes is not None:
            (build_dir / MEMORY_AVAILABILITY_PROJECTION_FILE).write_bytes(availability_projection_bytes)
        batch: list[BrainRecordEnvelope] = []
        batch_available_from: dict[str, datetime] = {}
        observed_episode_ids: set[str] = set()
        try:
            for record in _iter_source_records(self.root):
                effective_available_from = _effective_record_available_from(
                    record,
                    replay_availability_by_episode,
                )
                observed_episode_ids.add(record.episode_id)
                source_hash = brain_record_envelope_sha256(record)
                if effective_available_from > cutoff:
                    _write_jsonl_row(
                        future_file,
                        {"record_id": record.record_id, "sha256": source_hash},
                    )
                    future_count += 1
                    next_available_from = min(
                        next_available_from or effective_available_from,
                        effective_available_from,
                    )
                    continue
                batch.append(record)
                batch_available_from[record.record_id] = effective_available_from
                if len(batch) < batch_size:
                    continue
                connection, dimensions, retained = self._insert_streaming_batch(
                    database_path,
                    connection=connection,
                    dimensions=dimensions,
                    records=batch,
                    resolver=resolver,
                    parent=parent,
                    parent_hashes=parent_hashes,
                    parent_connection=parent_connection,
                    source_file=source_file,
                    routing_file=routing_file,
                    embedding_file=embedding_file,
                    cell_sums=cell_sums,
                    reasoning_sums=reasoning_sums,
                    cell_counts=cell_counts,
                    reasoning_counts=reasoning_counts,
                    disposition_counts=disposition_counts,
                    effective_available_from_by_record=batch_available_from,
                )
                retained_count += retained
                record_count += len(batch)
                batch_max = max(batch_available_from.values())
                max_available_from = max(max_available_from or batch_max, batch_max)
                batch = []
                batch_available_from = {}
            if batch:
                connection, dimensions, retained = self._insert_streaming_batch(
                    database_path,
                    connection=connection,
                    dimensions=dimensions,
                    records=batch,
                    resolver=resolver,
                    parent=parent,
                    parent_hashes=parent_hashes,
                    parent_connection=parent_connection,
                    source_file=source_file,
                    routing_file=routing_file,
                    embedding_file=embedding_file,
                    cell_sums=cell_sums,
                    reasoning_sums=reasoning_sums,
                    cell_counts=cell_counts,
                    reasoning_counts=reasoning_counts,
                    disposition_counts=disposition_counts,
                    effective_available_from_by_record=batch_available_from,
                )
                retained_count += retained
                record_count += len(batch)
                batch_max = max(batch_available_from.values())
                max_available_from = max(max_available_from or batch_max, batch_max)
            if replay_availability_by_episode is not None and observed_episode_ids != set(
                replay_availability_by_episode
            ):
                missing = sorted(observed_episode_ids - set(replay_availability_by_episode))
                unexpected = sorted(set(replay_availability_by_episode) - observed_episode_ids)
                unexpected_with_records = [
                    episode_id
                    for episode_id in unexpected
                    if not _episode_record_file_is_empty(self.root, episode_id)
                ]
                if missing or unexpected_with_records:
                    raise ValueError(
                        "replay availability episode coverage mismatch: "
                        f"missing={missing[:10]} "
                        f"unexpected={unexpected_with_records[:10]}"
                    )
            if connection is None or max_available_from is None:
                raise ValueError("production memory index requires cutoff-safe brain records")
            state = _finalize_streaming_database(
                connection,
                build_dir=build_dir,
                dimensions=dimensions,
                cell_sums=cell_sums,
                reasoning_sums=reasoning_sums,
                cell_counts=cell_counts,
                reasoning_counts=reasoning_counts,
                require_extensions=self.production,
            )
            connection.close()
            connection = None
            return _StreamingBuildState(
                record_count=record_count,
                future_record_count=future_count,
                next_available_from=next_available_from,
                retained_record_count=retained_count,
                dimensions=dimensions,
                max_available_from=max_available_from,
                disposition_counts=disposition_counts,
                cell_count=state.cell_count,
                secondary_membership_count=state.secondary_membership_count,
                independent_unit_count=state.independent_unit_count,
                unsupported_reasoning_record_count=(state.unsupported_reasoning_record_count),
                unsupported_reasoning_record_ids_sha256=(state.unsupported_reasoning_record_ids_sha256),
                readiness=state.readiness,
            )
        finally:
            source_file.close()
            future_file.close()
            routing_file.close()
            embedding_file.close()
            if parent_connection is not None:
                parent_connection.close()
            if connection is not None:
                connection.close()

    def _insert_streaming_batch(
        self,
        database_path: Path,
        *,
        connection: duckdb.DuckDBPyConnection | None,
        dimensions: int,
        records: list[BrainRecordEnvelope],
        resolver: RecordMemoryDocumentResolver,
        parent: MemoryCellSnapshotManifest | None,
        parent_hashes: dict[str, str],
        parent_connection: duckdb.DuckDBPyConnection | None,
        source_file: BinaryIO,
        routing_file: BinaryIO,
        embedding_file: BinaryIO,
        cell_sums: dict[str, npt.NDArray[np.int64]],
        reasoning_sums: dict[str, npt.NDArray[np.int64]],
        cell_counts: Counter[str],
        reasoning_counts: Counter[str],
        disposition_counts: Counter[str],
        effective_available_from_by_record: Mapping[str, datetime],
    ) -> tuple[duckdb.DuckDBPyConnection, int, int]:
        source_hashes = {record.record_id: brain_record_envelope_sha256(record) for record in records}
        documents = {record.record_id: resolver.document(record) for record in records}
        routing_by_id = {record.record_id: record_routing_metadata(record) for record in records}
        unresolved = [
            record.record_id
            for record in records
            if routing_by_id[record.record_id].routing_disposition == "REASONING"
            and MISSING_STRUCTURAL_CONTEXT in documents[record.record_id]
        ]
        if unresolved:
            raise ValueError("reasoning records require structural evidence documents: " + ", ".join(unresolved[:10]))
        reusable_ids = [
            record.record_id
            for record in records
            if parent_hashes.get(record.record_id) == source_hashes[record.record_id]
        ]
        parent_vectors: dict[str, list[float]] = {}
        if reusable_ids and parent_connection is not None:
            parent_vectors = {
                str(row[0]): [float(value) for value in row[1]]
                for row in parent_connection.execute(
                    "SELECT record_id, embedding FROM records WHERE record_id IN (SELECT UNNEST(?::VARCHAR[]))",
                    [reusable_ids],
                ).fetchall()
            }
            parent_documents = {
                str(row[0]): str(row[1])
                for row in parent_connection.execute(
                    "SELECT record_id, document FROM records WHERE record_id IN (SELECT UNNEST(?::VARCHAR[]))",
                    [reusable_ids],
                ).fetchall()
            }
            parent_vectors = {
                record_id: vector
                for record_id, vector in parent_vectors.items()
                if parent_documents.get(record_id) == documents[record_id]
            }
        missing = [record for record in records if record.record_id not in parent_vectors]
        generated = self._embed_batches([documents[record.record_id] for record in missing])
        generated_by_id = {record.record_id: vector for record, vector in zip(missing, generated, strict=True)}
        vectors = [
            _float32_vector(parent_vectors.get(record.record_id) or generated_by_id[record.record_id])
            for record in records
        ]
        batch_dimensions = _embedding_dimensions(vectors, self.embedding_provider)
        if dimensions and batch_dimensions != dimensions:
            raise ValueError("embedding dimensions changed during index build")
        dimensions = dimensions or batch_dimensions
        if parent is not None and parent.embedding_dimensions != dimensions:
            raise ValueError("parent embedding dimensions do not match current provider")
        if connection is None:
            connection = _create_streaming_database(database_path, dimensions=dimensions)
        signatures = vector_signatures_and_margins(vectors)
        record_rows = []
        provenance_rows: list[tuple[str, str]] = []
        for record, vector, (signature, margins) in zip(
            records,
            vectors,
            signatures,
            strict=True,
        ):
            routing = routing_by_id[record.record_id]
            cell_id = _cell_id(signature)
            independent_unit_id = record_independent_unit_id(record)
            unit_type = independent_unit_type(independent_unit_id) or "unsupported"
            population = project_population_record(record)
            source_hash = source_hashes[record.record_id]
            routing_json = canonical_json(routing.model_dump(mode="json"))
            embedding_hash = sha256_text(canonical_json(vector))
            record_rows.append(
                (
                    record.record_id,
                    record.episode_id,
                    record.record_type,
                    record.training_target,
                    record.trade_date,
                    effective_available_from_by_record[record.record_id].isoformat(),
                    record.training_eligible,
                    record.evidence_phase,
                    routing.evidence_polarity,
                    routing.label_quality,
                    routing.routing_disposition,
                    canonical_json(routing.memory_lanes),
                    documents[record.record_id],
                    cell_id,
                    vector,
                    signature,
                    margins,
                    independent_unit_id,
                    unit_type,
                    population.path_type,
                    population.regime_cluster,
                    population.high_return_pct,
                    population.close_return_pct,
                    population.upper_limit_touched,
                    population.outcome_observed,
                    population.sample_weight,
                    population.high_return_status,
                    population.close_return_status,
                    population.upper_limit_status,
                    population.sample_weight_status,
                    source_hash,
                    routing_json,
                    embedding_hash,
                )
            )
            provenance_rows.extend(
                (record.record_id, source_id) for source_id in sorted(set(record.provenance_source_ids))
            )
            _write_jsonl_row(
                source_file,
                {"record_id": record.record_id, "sha256": source_hash},
            )
            _write_jsonl_row(
                routing_file,
                {"record_id": record.record_id, "routing": routing.model_dump(mode="json")},
            )
            _write_jsonl_row(
                embedding_file,
                {"record_id": record.record_id, "sha256": embedding_hash},
            )
            quantized = np.rint(np.asarray(vector, dtype=np.float32) * MEMORY_VECTOR_QUANTIZATION_SCALE).astype(
                np.int64
            )
            cell_sums.setdefault(signature, np.zeros(dimensions, dtype=np.int64))
            cell_sums[signature] += quantized
            cell_counts[signature] += 1
            disposition_counts[routing.routing_disposition] += 1
            if routing.routing_disposition == "REASONING":
                reasoning_sums.setdefault(
                    signature,
                    np.zeros(dimensions, dtype=np.int64),
                )
                reasoning_sums[signature] += quantized
                reasoning_counts[signature] += 1
        connection.executemany(
            "INSERT INTO records VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            record_rows,
        )
        if provenance_rows:
            connection.executemany(
                "INSERT INTO provenance_edges VALUES (?, ?)",
                provenance_rows,
            )
        return connection, dimensions, len(parent_vectors)

    def search_cells(
        self,
        query: str,
        *,
        cutoff_at: datetime,
        limit: int = 12,
        query_vector: list[float] | None = None,
        included_memory_lanes: tuple[str, ...] | None = None,
        included_regime_clusters: tuple[str, ...] | None = None,
    ) -> list[MemoryCellCandidate]:
        if not query.strip() or limit < 1:
            return []
        manifest = self.resolve_snapshot(cutoff_at=cutoff_at)
        if self.production and not manifest.production_ready:
            raise ValueError("selected memory snapshot is not production ready")
        vector = query_vector if query_vector is not None else self._embed_batches([query])[0]
        if len(vector) != manifest.embedding_dimensions or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
            for value in vector
        ):
            raise ValueError("query embedding dimension does not match memory snapshot")
        database_path = self.root / manifest.database.artifact_path
        self._verify_runtime_database(manifest)
        connection = self._runtime_connection(database_path)
        try:
            filtered_metadata: list[tuple[Any, ...]] | None = None
            candidate_limit = max(limit, limit * MEMORY_INDEX_QUERY_CANDIDATE_MULTIPLIER)
            vector_type = _vector_type(manifest.embedding_dimensions)
            ann_limit = candidate_limit
            broad_ann_rows = connection.execute(
                f"""
                SELECT cell_id,
                       1.0 - array_cosine_distance(centroid, ?::{vector_type}) AS score,
                       primary_member_count,
                       independent_unit_count
                FROM reasoning_cells
                ORDER BY array_cosine_distance(centroid, ?::{vector_type})
                LIMIT ?
                """,
                [vector, vector, ann_limit],
            ).fetchall()
            if included_memory_lanes is None and included_regime_clusters is None:
                ann_rows = broad_ann_rows
                fts_rows = connection.execute(
                    """
                    SELECT primary_cell_id, MAX(score) AS score
                    FROM (
                        SELECT primary_cell_id,
                               fts_main_reasoning_records.match_bm25(record_id, ?) AS score
                        FROM reasoning_records
                    ) matched
                    WHERE score IS NOT NULL
                    GROUP BY primary_cell_id
                    ORDER BY score DESC
                    LIMIT ?
                    """,
                    [query, candidate_limit],
                ).fetchall()
            else:
                filter_sql, filter_parameters = _cell_search_filter(
                    included_memory_lanes=included_memory_lanes,
                    included_regime_clusters=included_regime_clusters,
                )
                facets = _cell_facets(
                    included_memory_lanes=included_memory_lanes,
                    included_regime_clusters=included_regime_clusters,
                )
                facet_scores: dict[str, float] = {}
                for facet_kind, facet_value in facets:
                    facet_rows = connection.execute(
                        f"""
                        SELECT cell_id,
                               1.0 - array_cosine_distance(
                                   centroid, ?::{vector_type}
                               ) AS score
                        FROM reasoning_cell_facets
                        WHERE facet_kind = ? AND facet_value = ?
                        ORDER BY array_cosine_distance(
                            centroid, ?::{vector_type}
                        )
                        LIMIT ?
                        """,
                        [
                            vector,
                            facet_kind,
                            facet_value,
                            vector,
                            ann_limit,
                        ],
                    ).fetchall()
                    for cell_id, score in facet_rows:
                        key = str(cell_id)
                        facet_scores[key] = max(
                            facet_scores.get(key, 0.0),
                            float(score),
                        )
                facet_ann_rows = sorted(
                    facet_scores.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:ann_limit]
                fts_rows = connection.execute(
                    f"""
                    SELECT r.primary_cell_id,
                           MAX(fts_main_reasoning_records.match_bm25(
                               r.record_id, ?
                           )) AS score
                    FROM reasoning_records r
                    WHERE {filter_sql.removeprefix(" AND ")}
                    GROUP BY r.primary_cell_id
                    HAVING score IS NOT NULL
                    ORDER BY score DESC, r.primary_cell_id
                    LIMIT ?
                    """,
                    [query, *filter_parameters, candidate_limit],
                ).fetchall()
                candidate_cell_ids = sorted({str(row[0]) for row in facet_ann_rows} | {str(row[0]) for row in fts_rows})
                if candidate_cell_ids:
                    filtered_metadata = connection.execute(
                        f"""
                        SELECT primary_cell_id,
                               COUNT(*) AS primary_member_count,
                               COUNT(DISTINCT independent_unit_id)
                                   AS independent_unit_count
                        FROM records
                        WHERE routing_disposition = 'REASONING'
                          AND primary_cell_id IN (
                              SELECT UNNEST(?::VARCHAR[])
                          ) {filter_sql}
                        GROUP BY primary_cell_id
                        """,
                        [candidate_cell_ids, *filter_parameters],
                    ).fetchall()
                    metadata_by_cell = {str(row[0]): (int(row[1]), int(row[2])) for row in filtered_metadata}
                    ann_rows = [
                        (
                            str(row[0]),
                            float(row[1]),
                            metadata_by_cell[str(row[0])][0],
                            metadata_by_cell[str(row[0])][1],
                        )
                        for row in facet_ann_rows
                        if str(row[0]) in metadata_by_cell
                    ]
                    ann_rows.sort(key=lambda row: (-float(row[1]), str(row[0])))
                    ann_rows = ann_rows[:candidate_limit]
                    fts_rows = [row for row in fts_rows if str(row[0]) in metadata_by_cell]
                else:
                    ann_rows = []
                    fts_rows = []
                    filtered_metadata = []
            return _merge_cell_candidates(
                connection,
                ann_rows=ann_rows,
                fts_rows=fts_rows,
                limit=limit,
                metadata_rows=(filtered_metadata),
            )
        finally:
            self._retain_runtime_connection(connection)

    def members_for_cells(
        self,
        cell_ids: list[str],
        *,
        cutoff_at: datetime,
        routing_dispositions: tuple[str, ...] = ("REASONING",),
    ) -> list[MemoryCellMember]:
        if not cell_ids:
            return []
        manifest = self.resolve_snapshot(cutoff_at=cutoff_at)
        database_path = self.root / manifest.database.artifact_path
        self._verify_runtime_database(manifest)
        connection = self._runtime_connection(database_path)
        try:
            rows = connection.execute(
                """
                SELECT DISTINCT r.record_id,
                       m.primary_cell_id,
                       CASE WHEN m.primary_cell_id IN (SELECT UNNEST(?::VARCHAR[]))
                            THEN 'PRIMARY' ELSE 'SECONDARY' END AS matched_as,
                       m.independent_unit_id,
                       r.available_from,
                       r.routing_disposition,
                       r.evidence_polarity,
                       r.label_quality,
                       r.memory_lanes
                FROM records r
                JOIN memberships m USING (record_id)
                LEFT JOIN secondary_memberships s USING (record_id)
                WHERE (m.primary_cell_id IN (SELECT UNNEST(?::VARCHAR[]))
                       OR s.cell_id IN (SELECT UNNEST(?::VARCHAR[])))
                  AND r.available_from <= ?
                  AND r.routing_disposition IN (SELECT UNNEST(?::VARCHAR[]))
                ORDER BY r.record_id
                """,
                [
                    cell_ids,
                    cell_ids,
                    cell_ids,
                    as_kst(cutoff_at).isoformat(),
                    list(routing_dispositions),
                ],
            ).fetchall()
            return [
                MemoryCellMember(
                    record_id=str(row[0]),
                    primary_cell_id=str(row[1]),
                    matched_as=str(row[2]),
                    independent_unit_id=str(row[3]),
                    available_from=parse_datetime(str(row[4])),
                    routing_disposition=str(row[5]),
                    evidence_polarity=str(row[6]),
                    label_quality=str(row[7]),
                    memory_lanes=tuple(json.loads(str(row[8]))),
                )
                for row in rows
            ]
        finally:
            self._retain_runtime_connection(connection)

    def population_members_for_cells(
        self,
        cell_ids: list[str],
        *,
        cutoff_at: datetime,
        independent_unit_type: str,
        routing_dispositions: tuple[str, ...] = ("REASONING",),
        included_memory_lanes: tuple[str, ...] | None = None,
        included_record_types: tuple[str, ...] | None = None,
        excluded_record_types: tuple[str, ...] = (),
        max_records: int | None = None,
        force_database_verification: bool = False,
    ) -> tuple[MemoryCellSnapshotManifest, list[PopulationCellMember]]:
        """Return every cutoff-safe member of selected cells for a unit kind."""

        selected_cells = sorted(set(cell_ids))
        if not selected_cells:
            raise ValueError("population retrieval requires selected cells")
        if not routing_dispositions:
            raise ValueError("population retrieval requires routing dispositions")
        if max_records is not None and max_records < 1:
            raise ValueError("population record budget must be positive")
        if included_memory_lanes is not None and not included_memory_lanes:
            raise ValueError("population memory lane filter cannot be empty")
        purpose_filter_sql = ""
        purpose_parameters: list[object] = []
        if included_memory_lanes is not None:
            purpose_filter_sql += " AND list_has_any(from_json(r.memory_lanes, '[\"VARCHAR\"]'), ?::VARCHAR[])"
            purpose_parameters.append(sorted(set(included_memory_lanes)))
        if included_record_types is not None:
            if not included_record_types:
                raise ValueError("population record type filter cannot be empty")
            purpose_filter_sql += " AND r.record_type IN (SELECT UNNEST(?::VARCHAR[]))"
            purpose_parameters.append(sorted(set(included_record_types)))
        if excluded_record_types:
            purpose_filter_sql += " AND r.record_type NOT IN (SELECT UNNEST(?::VARCHAR[]))"
            purpose_parameters.append(sorted(set(excluded_record_types)))
        manifest = self.resolve_snapshot(cutoff_at=cutoff_at)
        database_path = self.root / manifest.database.artifact_path
        self._verify_runtime_database(
            manifest,
            force=force_database_verification,
        )
        connection = self._runtime_connection(database_path)
        try:
            known_cell_ids = {
                str(row[0])
                for row in connection.execute(
                    "SELECT cell_id FROM cells WHERE cell_id IN (SELECT UNNEST(?::VARCHAR[]))",
                    [selected_cells],
                ).fetchall()
            }
            missing_cell_ids = sorted(set(selected_cells) - known_cell_ids)
            if missing_cell_ids:
                raise ValueError(
                    "selected population cells are absent from the snapshot: " + ", ".join(missing_cell_ids)
                )
            future_trade_date_count = _fetch_count(
                connection,
                """
                WITH matched AS (
                    SELECT record_id FROM memberships
                    WHERE primary_cell_id IN (SELECT UNNEST(?::VARCHAR[]))
                    UNION
                    SELECT record_id FROM secondary_memberships
                    WHERE cell_id IN (SELECT UNNEST(?::VARCHAR[]))
                )
                SELECT COUNT(*)
                FROM records r JOIN matched USING (record_id)
                WHERE r.available_from <= ?
                  AND r.routing_disposition IN (SELECT UNNEST(?::VARCHAR[]))
                  AND r.independent_unit_type = ?
                  AND r.trade_date > ?
                """
                + purpose_filter_sql,
                [
                    selected_cells,
                    selected_cells,
                    as_kst(cutoff_at).isoformat(),
                    sorted(set(routing_dispositions)),
                    independent_unit_type,
                    as_kst(cutoff_at).date(),
                    *purpose_parameters,
                ],
            )
            if future_trade_date_count:
                raise ValueError("selected population contains a trade date after the cutoff")
            if max_records is not None:
                selected_record_count = _fetch_count(
                    connection,
                    """
                    WITH matched AS (
                        SELECT record_id FROM memberships
                        WHERE primary_cell_id IN (SELECT UNNEST(?::VARCHAR[]))
                        UNION
                        SELECT record_id FROM secondary_memberships
                        WHERE cell_id IN (SELECT UNNEST(?::VARCHAR[]))
                    )
                    SELECT COUNT(*)
                    FROM records r JOIN matched USING (record_id)
                    WHERE r.available_from <= ?
                      AND r.routing_disposition IN (SELECT UNNEST(?::VARCHAR[]))
                      AND r.independent_unit_type = ?
                    """
                    + purpose_filter_sql,
                    [
                        selected_cells,
                        selected_cells,
                        as_kst(cutoff_at).isoformat(),
                        sorted(set(routing_dispositions)),
                        independent_unit_type,
                        *purpose_parameters,
                    ],
                )
                if selected_record_count > max_records:
                    raise ValueError(
                        "selected population exceeds the operational record budget: "
                        f"{selected_record_count} > {max_records}"
                    )
            rows = connection.execute(
                f"""
                WITH matched AS (
                    SELECT record_id, primary_cell_id AS cell_id
                    FROM memberships
                    WHERE primary_cell_id IN (SELECT UNNEST(?::VARCHAR[]))
                    UNION
                    SELECT record_id, cell_id
                    FROM secondary_memberships
                    WHERE cell_id IN (SELECT UNNEST(?::VARCHAR[]))
                )
                SELECT r.record_id,
                       r.independent_unit_id,
                       r.independent_unit_type,
                       r.primary_cell_id,
                       matched.cell_id,
                       r.trade_date,
                       r.record_type,
                       r.training_eligible,
                       r.routing_disposition,
                       r.evidence_polarity,
                       r.label_quality,
                       r.memory_lanes,
                       r.path_type,
                       r.regime_cluster,
                       r.high_return_pct,
                       r.close_return_pct,
                       r.upper_limit_touched,
                       r.outcome_observed,
                       r.sample_weight,
                       r.high_return_status,
                       r.close_return_status,
                       r.upper_limit_status,
                       r.sample_weight_status
                FROM records r
                JOIN matched USING (record_id)
                WHERE r.available_from <= ?
                  AND r.trade_date <= ?
                  AND r.routing_disposition IN (SELECT UNNEST(?::VARCHAR[]))
                  AND r.independent_unit_type = ?
                  {purpose_filter_sql}
                ORDER BY r.record_id, matched.cell_id
                """,
                [
                    selected_cells,
                    selected_cells,
                    as_kst(cutoff_at).isoformat(),
                    as_kst(cutoff_at).date(),
                    sorted(set(routing_dispositions)),
                    independent_unit_type,
                    *purpose_parameters,
                ],
            ).fetchall()
        finally:
            self._retain_runtime_connection(connection)
        grouped: dict[str, tuple[tuple[object, ...], set[str]]] = {}
        for row in rows:
            record_id = str(row[0])
            if record_id not in grouped:
                grouped[record_id] = (row, set())
            grouped[record_id][1].add(str(row[4]))
        return manifest, [
            PopulationCellMember(
                record_id=str(row[0]),
                independent_unit_id=str(row[1]),
                independent_unit_type=str(row[2]),
                primary_cell_id=str(row[3]),
                matched_cell_ids=tuple(sorted(matched_cells)),
                trade_date=cast(date, row[5]),
                record_type=str(row[6]),
                training_eligible=bool(row[7]),
                routing_disposition=str(row[8]),
                evidence_polarity=str(row[9]),
                label_quality=str(row[10]),
                memory_lanes=tuple(json.loads(str(row[11]))),
                path_type=str(row[12]),
                regime_cluster=str(row[13]),
                high_return_pct=(float(cast(float, row[14])) if row[14] is not None else None),
                close_return_pct=(float(cast(float, row[15])) if row[15] is not None else None),
                upper_limit_touched=(bool(row[16]) if row[16] is not None else None),
                outcome_observed=bool(row[17]),
                sample_weight=float(cast(float, row[18])),
                high_return_status=str(row[19]),
                close_return_status=str(row[20]),
                upper_limit_status=str(row[21]),
                sample_weight_status=str(row[22]),
            )
            for row, matched_cells in (grouped[key] for key in sorted(grouped))
        ]

    def effective_available_from_for_records(
        self,
        record_ids: list[str],
        *,
        cutoff_at: datetime,
    ) -> tuple[MemoryCellSnapshotManifest, dict[str, datetime]]:
        """Read cutoff-effective availability from the active immutable snapshot."""

        selected_ids = sorted(set(record_ids))
        if not selected_ids:
            raise ValueError("effective availability lookup requires record IDs")
        if len(selected_ids) > 2_048:
            raise ValueError("effective availability lookup exceeds its record budget")
        manifest = self.resolve_snapshot(cutoff_at=cutoff_at)
        self._verify_runtime_database(manifest)
        connection = self._runtime_connection(self.root / manifest.database.artifact_path)
        try:
            rows = connection.execute(
                """
                SELECT record_id, available_from
                FROM records
                WHERE record_id IN (SELECT UNNEST(?::VARCHAR[]))
                  AND available_from <= ?
                ORDER BY record_id
                """,
                [selected_ids, as_kst(cutoff_at).isoformat()],
            ).fetchall()
        finally:
            self._retain_runtime_connection(connection)
        observed = {str(record_id): parse_datetime(str(available_from)) for record_id, available_from in rows}
        if set(observed) != set(selected_ids):
            raise ValueError("effective availability lookup is not closed over selected records")
        return manifest, observed

    def representative_source_records(
        self,
        record_ids: list[str],
        *,
        cutoff_at: datetime,
        force_database_verification: bool = False,
    ) -> tuple[MemoryCellSnapshotManifest, list[RepresentativeSourceRecord]]:
        """Load a bounded representative pool from the active snapshot DB."""

        selected_ids = sorted(set(record_ids))
        if not selected_ids:
            raise ValueError("representative source retrieval requires record IDs")
        if len(selected_ids) > 2_048:
            raise ValueError("representative source retrieval exceeds its record budget")
        manifest = self.resolve_snapshot(cutoff_at=cutoff_at)
        self._verify_runtime_database(
            manifest,
            force=force_database_verification,
        )
        connection = self._runtime_connection(self.root / manifest.database.artifact_path)
        try:
            rows = connection.execute(
                """
                SELECT r.record_id,
                       r.embedding,
                       r.document,
                       r.source_sha256,
                       list(p.source_id ORDER BY p.source_id) AS provenance_source_ids
                FROM records r
                LEFT JOIN provenance_edges p USING (record_id)
                WHERE r.record_id IN (SELECT UNNEST(?::VARCHAR[]))
                  AND r.available_from <= ?
                GROUP BY r.record_id, r.embedding, r.document, r.source_sha256
                ORDER BY r.record_id
                """,
                [selected_ids, as_kst(cutoff_at).isoformat()],
            ).fetchall()
        finally:
            self._retain_runtime_connection(connection)
        observed_ids = {str(row[0]) for row in rows}
        if observed_ids != set(selected_ids):
            raise ValueError("representative source records are absent from the snapshot")
        return manifest, [
            RepresentativeSourceRecord(
                record_id=str(row[0]),
                embedding=tuple(float(value) for value in row[1]),
                document=str(row[2]),
                source_sha256=str(row[3]),
                provenance_source_ids=tuple(str(value) for value in row[4]),
            )
            for row in rows
        ]

    def _verify_runtime_database(
        self,
        manifest: MemoryCellSnapshotManifest,
        *,
        force: bool = False,
    ) -> None:
        path = self.root / manifest.database.artifact_path
        try:
            stat = path.stat()
        except OSError as exc:
            raise ValueError("memory snapshot database is unavailable") from exc
        observed_state = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        verification_key = f"{manifest.snapshot_id}:{path.resolve()}"
        if not force and self._verified_database_files.get(verification_key) == observed_state:
            return
        if file_sha256(path) != manifest.database.sha256:
            raise ValueError("memory snapshot database hash mismatch")
        self._verified_database_files[verification_key] = observed_state

    def _runtime_connection(self, database_path: Path) -> duckdb.DuckDBPyConnection:
        """Reuse one immutable index connection per worker thread and snapshot."""

        key = str(database_path.resolve())
        connections = getattr(self._runtime_connection_state, "connections", None)
        if connections is None:
            connections = {}
            self._runtime_connection_state.connections = connections
        connection = connections.get(key)
        if connection is None:
            connection = _connect_index(database_path, read_only=True)
            connections[key] = connection
        return cast(duckdb.DuckDBPyConnection, connection)

    @staticmethod
    def _retain_runtime_connection(
        _connection: duckdb.DuckDBPyConnection,
    ) -> None:
        """The thread-local cache owns immutable runtime connections."""

    def resolve_snapshot(self, *, cutoff_at: datetime) -> MemoryCellSnapshotManifest:
        cutoff = as_kst(cutoff_at)
        if self.production and not self._registry_is_bound_to_current_pointer():
            raise ValueError("memory snapshot registry is not bound to the active pointer")
        registry = self._read_registry()
        candidates = []
        manifests_by_id = {manifest.snapshot_id: manifest for manifest in self.list_snapshots()}
        active_evaluation_snapshot_id: str | None = None
        if self.current_pointer_path.is_file():
            pointer = read_json(self.current_pointer_path)
            active_id = str(pointer.get("snapshot_id") or "") if isinstance(pointer, dict) else ""
            active_manifest = manifests_by_id.get(active_id)
            if active_manifest is not None and active_manifest.evaluation_only:
                active_evaluation_snapshot_id = active_id
        for entry in registry.get("snapshots", []):
            if not isinstance(entry, dict):
                continue
            try:
                requested_cutoff = as_kst(parse_datetime(str(entry["requested_cutoff"])))
                snapshot_id = str(entry["snapshot_id"])
                embedding_model = str(entry["embedding_model"])
            except (KeyError, TypeError, ValueError):
                continue
            manifest = manifests_by_id.get(snapshot_id)
            if (
                requested_cutoff <= cutoff
                and (
                    manifest is None
                    or manifest.evaluation_only
                    or manifest.next_available_from is None
                    or cutoff < as_kst(manifest.next_available_from)
                )
                and embedding_model == self.embedding_provider.embedding_method
                and manifest is not None
                and (active_evaluation_snapshot_id is None or snapshot_id == active_evaluation_snapshot_id)
                and _registry_entry_matches_manifest(entry, manifest, self.root)
                and _manifest_versions_current(manifest)
                and manifest.snapshot_id == _snapshot_id(_snapshot_identity_from_manifest(manifest))
                and (not self.production or manifest.production_ready)
            ):
                candidates.append((requested_cutoff, manifest))
        if not candidates:
            raise FileNotFoundError("no compatible memory snapshot exists at or before cutoff")
        selected = max(candidates, key=lambda item: item[0])[1]
        if self.production and not self._record_store_generation_allows(
            selected,
            cutoff=cutoff,
        ):
            raise ValueError("memory snapshot is stale relative to the record store")
        return selected

    def list_snapshots(self) -> list[MemoryCellSnapshotManifest]:
        if not self.snapshots_root.exists():
            return []
        manifests: list[MemoryCellSnapshotManifest] = []
        for path in sorted(self.snapshots_root.glob(f"*/{MEMORY_MANIFEST_FILE}")):
            try:
                manifests.append(MemoryCellSnapshotManifest.model_validate(read_json(path)))
            except (OSError, ValueError):
                continue
        return manifests

    def activate(
        self,
        manifest: MemoryCellSnapshotManifest,
        *,
        requested_cutoff: datetime | None = None,
    ) -> None:
        if manifest.evaluation_only:
            raise ValueError("evaluation replay snapshots cannot enter the production activation path")
        effective_requested_cutoff = as_kst(requested_cutoff or manifest.as_of_cutoff)
        if effective_requested_cutoff != as_kst(manifest.as_of_cutoff):
            raise ValueError("requested cutoff does not match memory snapshot cutoff")
        inspection = inspect_memory_snapshot(self.root, manifest.snapshot_id)
        if inspection.get("status") != "current_as_of":
            raise ValueError(
                "cannot activate an invalid or stale memory snapshot: " + canonical_json(inspection.get("errors", []))
            )
        registry_bytes = self.as_of_registry_path.read_bytes() if self.as_of_registry_path.exists() else None
        pointer_bytes = self.current_pointer_path.read_bytes() if self.current_pointer_path.exists() else None
        try:
            self._register_snapshot(
                manifest,
                requested_cutoff=effective_requested_cutoff,
                bind_current_pointer=False,
            )
            self._write_current_pointer(manifest)
        except Exception:
            _restore_optional_file(self.as_of_registry_path, registry_bytes)
            _restore_optional_file(self.current_pointer_path, pointer_bytes)
            raise

    def activate_verified_evaluation_snapshot(
        self,
        manifest: MemoryCellSnapshotManifest,
        *,
        receipt_path: Path,
    ) -> None:
        """Activate an isolated replay snapshot from its build receipt.

        This deliberately avoids a second full database projection audit. It is
        restricted to immutable, explicit-cutoff shadow receipts and is never used
        by the production release path.
        """

        resolved_receipt = receipt_path.resolve()
        try:
            resolved_receipt.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("evaluation snapshot receipt escapes the project") from exc
        receipt = read_json(resolved_receipt)
        if not isinstance(receipt, dict):
            raise ValueError("evaluation snapshot receipt is invalid")
        required = {
            "schema_version": "nslab.shadow_replay_as_of_snapshot.v1",
            "snapshot_id": manifest.snapshot_id,
            "record_count": manifest.record_count,
            "retained_embedding_count": manifest.record_count,
            "generated_embedding_count": 0,
            "centroid_population_record_count": manifest.record_count,
            "full_corpus_centroids_used": False,
            "holdout_overlap_count": 0,
            "calibration_overlap_count": 0,
            "immutable": True,
            "production_available_from_mutated": False,
            "availability_mode": "replay_available_from",
            "availability_projection_version": (REPLAY_AVAILABILITY_PROJECTION_VERSION),
        }
        if any(receipt.get(key) != value for key, value in required.items()):
            raise ValueError("evaluation snapshot receipt contract is not satisfied")
        if (
            manifest.cutoff_identity != f"explicit:{as_kst(manifest.as_of_cutoff).isoformat()}"
            or not manifest.evaluation_only
            or manifest.availability_mode != "replay_available_from"
            or manifest.availability_projection is None
            or receipt.get("availability_projection_sha256") != manifest.availability_projection.sha256
            or receipt.get("availability_projection_episode_count") != manifest.availability_projection.item_count
            or file_sha256(self.root / manifest.availability_projection.artifact_path)
            != manifest.availability_projection.sha256
            or receipt.get("build_cutoff") != as_kst(manifest.as_of_cutoff).isoformat()
            or receipt.get("source_record_hashes_sha256")
            != file_sha256(self.root / manifest.source_record_hashes.artifact_path)
            or manifest.source_generation_sha256 != self._record_store_generation_sha256(rebuild_missing=False)
            or not manifest.real_embedding
            or manifest.unsupported_reasoning_record_count != 0
            or not all(
                (
                    manifest.metadata_index_ready,
                    manifest.fts_index_ready,
                    manifest.hnsw_index_ready,
                    manifest.provenance_graph_ready,
                )
            )
        ):
            raise ValueError("evaluation snapshot receipt does not close the snapshot")
        registry_bytes = self.as_of_registry_path.read_bytes() if self.as_of_registry_path.exists() else None
        pointer_bytes = self.current_pointer_path.read_bytes() if self.current_pointer_path.exists() else None
        try:
            self._register_snapshot(
                manifest,
                requested_cutoff=manifest.as_of_cutoff,
                bind_current_pointer=False,
            )
            self._write_current_pointer(manifest)
            self._bind_evaluation_receipt_to_pointer(
                manifest,
                receipt_path=resolved_receipt,
            )
        except Exception:
            _restore_optional_file(self.as_of_registry_path, registry_bytes)
            _restore_optional_file(self.current_pointer_path, pointer_bytes)
            raise

    def _embed_batches(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self.embedding_batch_size):
            vectors.extend(self.embedding_provider.embed_texts(texts[offset : offset + self.embedding_batch_size]))
        if len(vectors) != len(texts):
            raise ValueError("embedding provider returned the wrong vector count")
        return vectors

    def _vectors_with_parent(
        self,
        records: list[BrainRecordEnvelope],
        *,
        texts: list[str],
        source_hashes: dict[str, str],
        parent: MemoryCellSnapshotManifest | None,
    ) -> tuple[list[list[float]], int]:
        parent_vectors: dict[str, list[float]] = {}
        if parent is not None:
            parent_hashes = self._snapshot_source_hashes(parent)
            reusable_ids = [
                record.record_id
                for record in records
                if parent_hashes.get(record.record_id) == source_hashes[record.record_id]
            ]
            if reusable_ids:
                database_path = self.root / parent.database.artifact_path
                connection = _connect_index(database_path, read_only=True)
                try:
                    parent_vectors = {
                        str(row[0]): [float(value) for value in row[1]]
                        for row in connection.execute(
                            """
                            SELECT record_id, embedding
                            FROM records
                            WHERE record_id IN (SELECT UNNEST(?::VARCHAR[]))
                            """,
                            [reusable_ids],
                        ).fetchall()
                    }
                finally:
                    connection.close()
        missing_indices = [index for index, record in enumerate(records) if record.record_id not in parent_vectors]
        new_vectors = self._embed_batches([texts[index] for index in missing_indices])
        generated_by_index = dict(zip(missing_indices, new_vectors, strict=True))
        vectors = [
            parent_vectors.get(record.record_id) or generated_by_index[index] for index, record in enumerate(records)
        ]
        return vectors, len(parent_vectors)

    def _existing_snapshot(self, final_dir: Path) -> MemoryCellSnapshotManifest | None:
        manifest_path = final_dir / MEMORY_MANIFEST_FILE
        if not manifest_path.exists():
            return None
        try:
            manifest = MemoryCellSnapshotManifest.model_validate(read_json(manifest_path))
        except (OSError, ValueError) as exc:
            raise ValueError("existing memory snapshot is invalid") from exc
        inspection = inspect_memory_snapshot(self.root, manifest.snapshot_id)
        if inspection.get("status") != "current_as_of":
            raise ValueError("existing memory snapshot conflicts with current source records")
        return manifest

    def _reusable_snapshot(
        self,
        *,
        cutoff: datetime,
        cutoff_identity: str,
        source_generation_sha256: str,
        embedding_method: str,
        availability_projection_sha256: str | None,
    ) -> MemoryCellSnapshotManifest | None:
        candidates = [
            manifest
            for manifest in self.list_snapshots()
            if as_kst(manifest.as_of_cutoff) == cutoff
            and manifest.cutoff_identity == cutoff_identity
            and manifest.source_generation_sha256 == source_generation_sha256
            and manifest.embedding_model == embedding_method
            and (manifest.availability_projection.sha256 if manifest.availability_projection is not None else None)
            == availability_projection_sha256
            and _manifest_versions_current(manifest)
            and (not self.production or manifest.production_ready)
        ]
        for manifest in sorted(candidates, key=lambda item: item.snapshot_id):
            inspection = inspect_memory_snapshot(self.root, manifest.snapshot_id)
            if inspection.get("status") != "current_as_of":
                raise ValueError(
                    "matching reusable memory snapshot is invalid: " + canonical_json(inspection.get("errors", []))
                )
            return manifest
        return None

    def _latest_compatible_snapshot(
        self,
        *,
        max_available_from: datetime,
        embedding_method: str,
    ) -> MemoryCellSnapshotManifest | None:
        manifests_by_id = {manifest.snapshot_id: manifest for manifest in self.list_snapshots()}
        candidates: list[tuple[datetime, MemoryCellSnapshotManifest]] = []
        for entry in self._read_registry().get("snapshots", []):
            if not isinstance(entry, dict):
                continue
            try:
                requested_cutoff = as_kst(parse_datetime(str(entry["requested_cutoff"])))
                manifest = manifests_by_id[str(entry["snapshot_id"])]
            except (KeyError, TypeError, ValueError):
                continue
            if (
                requested_cutoff > as_kst(max_available_from)
                or manifest.embedding_model != embedding_method
                or manifest.evaluation_only
                or not _manifest_versions_current(manifest)
                or not _registry_entry_matches_manifest(entry, manifest, self.root)
            ):
                continue
            candidates.append((requested_cutoff, manifest))

        for _requested_cutoff, manifest in sorted(
            candidates,
            key=lambda item: (item[0], item[1].snapshot_id),
            reverse=True,
        ):
            inspection = inspect_memory_snapshot(self.root, manifest.snapshot_id)
            errors = inspection.get("errors")
            allowed_parent_staleness = {
                "future_record_hashes_stale",
                "source_generation_hash_stale",
                "next_available_from_stale",
            }
            if inspection.get("status") == "current_as_of" or (
                isinstance(errors, list) and errors and set(errors) <= allowed_parent_staleness
            ):
                return manifest
        return None

    def _embedding_reuse_parent(
        self,
        snapshot_id: str,
        *,
        embedding_method: str,
        source_generation_sha256: str,
    ) -> MemoryCellSnapshotManifest:
        parent = next(
            (manifest for manifest in self.list_snapshots() if manifest.snapshot_id == snapshot_id),
            None,
        )
        if parent is None:
            raise ValueError("requested embedding reuse snapshot does not exist")
        if (
            parent.embedding_model != embedding_method
            or parent.source_generation_sha256 != source_generation_sha256
            or parent.evaluation_only
            or not _manifest_versions_current(parent)
        ):
            raise ValueError("requested embedding reuse snapshot is incompatible")
        inspection = inspect_memory_snapshot(self.root, parent.snapshot_id)
        if inspection.get("status") != "current_as_of":
            raise ValueError(
                "requested embedding reuse snapshot is invalid: " + canonical_json(inspection.get("errors", []))
            )
        return parent

    def _snapshot_source_hashes(
        self,
        manifest: MemoryCellSnapshotManifest | None,
    ) -> dict[str, str]:
        if manifest is None:
            return {}
        path = self.root / manifest.source_record_hashes.artifact_path
        try:
            return {str(row["record_id"]): str(row["sha256"]) for row in _read_jsonl(path)}
        except (OSError, KeyError, TypeError, ValueError):
            return {}

    def _write_current_pointer(self, manifest: MemoryCellSnapshotManifest) -> None:
        self.index_root.mkdir(parents=True, exist_ok=True)
        manifest_path = self.snapshots_root / manifest.snapshot_id / MEMORY_MANIFEST_FILE
        payload = {
            "schema_version": "nslab.memory_index_pointer.v1",
            "snapshot_id": manifest.snapshot_id,
            "manifest_path": relative_to_root(manifest_path, self.root),
            "manifest_sha256": sha256_text(manifest_path.read_text(encoding="utf-8")),
            "as_of_registry_sha256": (
                sha256_text(self.as_of_registry_path.read_text(encoding="utf-8"))
                if self.as_of_registry_path.exists()
                else sha256_text("")
            ),
        }
        temporary = self.current_pointer_path.with_suffix(".json.tmp")
        write_json(temporary, payload)
        os.replace(temporary, self.current_pointer_path)

    def _bind_evaluation_receipt_to_pointer(
        self,
        manifest: MemoryCellSnapshotManifest,
        *,
        receipt_path: Path,
    ) -> None:
        pointer = read_json(self.current_pointer_path)
        if not isinstance(pointer, dict) or pointer.get("snapshot_id") != manifest.snapshot_id:
            raise ValueError("evaluation pointer binding is unavailable")
        pointer.update(
            {
                "evaluation_only": True,
                "evaluation_receipt_path": relative_to_root(
                    receipt_path,
                    self.root,
                ),
                "evaluation_receipt_sha256": file_sha256(receipt_path),
                "evaluation_database_sha256": manifest.database.sha256,
            }
        )
        temporary = self.current_pointer_path.with_suffix(".json.tmp")
        write_json(temporary, pointer)
        os.replace(temporary, self.current_pointer_path)

    def _register_snapshot(
        self,
        manifest: MemoryCellSnapshotManifest,
        *,
        requested_cutoff: datetime,
        bind_current_pointer: bool = True,
    ) -> None:
        requested_cutoff = as_kst(requested_cutoff)
        if requested_cutoff != as_kst(manifest.as_of_cutoff):
            raise ValueError("requested cutoff does not match memory snapshot cutoff")
        registry = self._read_registry()
        entries = [
            entry
            for entry in registry.get("snapshots", [])
            if isinstance(entry, dict)
            and not (
                entry.get("requested_cutoff") == requested_cutoff.isoformat()
                and entry.get("embedding_model") == manifest.embedding_model
            )
        ]
        entries.append(
            {
                "requested_cutoff": requested_cutoff.isoformat(),
                "embedding_model": manifest.embedding_model,
                "snapshot_id": manifest.snapshot_id,
                "manifest_sha256": sha256_text(
                    (self.snapshots_root / manifest.snapshot_id / MEMORY_MANIFEST_FILE).read_text(encoding="utf-8")
                ),
                "corpus_manifest_sha256": manifest.corpus_manifest_sha256,
                "source_generation_sha256": manifest.source_generation_sha256,
                "record_count": manifest.record_count,
                "next_available_from": (
                    manifest.next_available_from.isoformat() if manifest.next_available_from is not None else None
                ),
                "as_of_cutoff": manifest.as_of_cutoff.isoformat(),
                "cutoff_identity": manifest.cutoff_identity,
                "max_available_from": manifest.max_available_from.isoformat(),
                "clustering_version": manifest.clustering_version,
                "normalizer_version": manifest.normalizer_version,
                "cell_schema_version": manifest.cell_schema_version,
                "polarity_classifier_version": manifest.polarity_classifier_version,
                "population_projection_version": manifest.population_projection_version,
                "unsupported_reasoning_record_count": (manifest.unsupported_reasoning_record_count),
                "unsupported_reasoning_record_ids_sha256": (manifest.unsupported_reasoning_record_ids_sha256),
            }
        )
        entries.sort(key=lambda item: (str(item["embedding_model"]), str(item["requested_cutoff"])))
        payload = {
            "schema_version": "nslab.memory_as_of_registry.v1",
            "snapshots": entries,
        }
        self.index_root.mkdir(parents=True, exist_ok=True)
        temporary = self.as_of_registry_path.with_suffix(".json.tmp")
        write_json(temporary, payload)
        os.replace(temporary, self.as_of_registry_path)
        if bind_current_pointer and self.current_pointer_path.exists():
            try:
                pointer = read_json(self.current_pointer_path)
                active = next(item for item in self.list_snapshots() if item.snapshot_id == pointer.get("snapshot_id"))
            except (OSError, StopIteration, TypeError, ValueError):
                return
            self._write_current_pointer(active)

    def _registry_is_bound_to_current_pointer(self) -> bool:
        if not self.current_pointer_path.exists() or not self.as_of_registry_path.exists():
            return False
        try:
            pointer = read_json(self.current_pointer_path)
            expected = str(pointer["as_of_registry_sha256"])
        except (OSError, KeyError, TypeError, ValueError):
            return False
        return expected == sha256_text(self.as_of_registry_path.read_text(encoding="utf-8"))

    def _record_store_generation_allows(
        self,
        manifest: MemoryCellSnapshotManifest,
        *,
        cutoff: datetime,
    ) -> bool:
        try:
            generation = self._record_store_generation(rebuild_missing=False)
        except ValueError:
            return False
        current_root = generation.get("generation_root_sha256")
        if current_root == manifest.source_generation_sha256:
            return True
        history = generation.get("generation_history")
        if not isinstance(history, dict):
            return False
        changed_min = history.get(manifest.source_generation_sha256)
        if not isinstance(changed_min, str):
            return False
        try:
            return as_kst(parse_datetime(changed_min)) > as_kst(cutoff)
        except ValueError:
            return False

    def _record_store_generation_sha256(self, *, rebuild_missing: bool = True) -> str:
        generation = self._record_store_generation(rebuild_missing=rebuild_missing)
        return str(generation["generation_root_sha256"])

    def _record_store_generation(
        self,
        *,
        rebuild_missing: bool,
    ) -> dict[str, Any]:
        path = self.root / "memory" / "record_index" / "manifest.json"
        try:
            generation = read_json(path)
        except (OSError, ValueError):
            generation = None
        if not (
            isinstance(generation, dict)
            and generation.get("schema_version") == "nslab.record_index_manifest.v2"
            and generation.get("record_hash_kind") == "canonical_full_envelope_sha256"
            and isinstance(generation.get("generation_root_sha256"), str)
        ):
            if not rebuild_missing:
                raise ValueError("record store generation manifest is unavailable")
            generation = BrainRecordStore(self.root).rebuild_indexes()
        return dict(generation)

    def _should_activate(
        self,
        *,
        as_of: datetime | None,
        promote_current: bool | None,
    ) -> bool:
        if promote_current is not None:
            return promote_current
        return as_of is None or not self.current_pointer_path.exists()

    def _read_registry(self) -> dict[str, Any]:
        if not self.as_of_registry_path.exists():
            return {"schema_version": "nslab.memory_as_of_registry.v1", "snapshots": []}
        try:
            payload = read_json(self.as_of_registry_path)
        except (OSError, ValueError):
            return {"schema_version": "nslab.memory_as_of_registry.v1", "snapshots": []}
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "nslab.memory_as_of_registry.v1"
            or not isinstance(payload.get("snapshots"), list)
        ):
            return {"schema_version": "nslab.memory_as_of_registry.v1", "snapshots": []}
        return payload


def inspect_memory_snapshot(root: Path, snapshot_id: str) -> dict[str, object]:
    root = root.resolve()
    snapshot_dir = root / MEMORY_INDEX_ROOT / MEMORY_SNAPSHOT_DIR / snapshot_id
    manifest_path = snapshot_dir / MEMORY_MANIFEST_FILE
    base: dict[str, object] = {
        "snapshot_id": snapshot_id,
        "path": relative_to_root(snapshot_dir, root),
        "status": "missing",
        "passed": False,
        "errors": [],
    }
    if not manifest_path.exists():
        return base
    try:
        raw_manifest = read_json(manifest_path)
    except (OSError, ValueError) as exc:
        return {**base, "status": "invalid", "errors": [f"manifest_invalid:{exc}"]}
    if isinstance(raw_manifest, dict) and raw_manifest.get("schema_version") in {
        "nslab.memory_cell_snapshot_manifest.v1",
        "nslab.memory_cell_snapshot_manifest.v2",
    }:
        return {
            **base,
            "status": "stale",
            "errors": ["snapshot_schema_legacy"],
            "manifest": raw_manifest,
            "production_ready": False,
            "legacy_read_compatible": True,
        }
    try:
        manifest = MemoryCellSnapshotManifest.model_validate(raw_manifest)
    except ValueError as exc:
        return {**base, "status": "invalid", "errors": [f"manifest_invalid:{exc}"]}
    errors: list[str] = []
    if manifest.snapshot_id != snapshot_id:
        errors.append("snapshot_id_mismatch")
    if not _manifest_versions_current(manifest):
        errors.append("snapshot_versions_stale")
    if manifest.snapshot_id != _snapshot_id(_snapshot_identity_from_manifest(manifest)):
        errors.append("snapshot_identity_mismatch")
    artifacts = (
        manifest.source_record_hashes,
        manifest.excluded_future_record_hashes,
        manifest.routing_metadata,
        manifest.embedding_hashes,
        manifest.cell_entries,
        manifest.memberships,
        manifest.database,
        *((manifest.availability_projection,) if manifest.availability_projection is not None else ()),
    )
    for artifact in artifacts:
        path = (root / artifact.artifact_path).resolve()
        try:
            path.relative_to(snapshot_dir.resolve())
        except ValueError:
            errors.append(f"artifact_path_escapes_snapshot:{artifact.artifact_path}")
            continue
        if not path.exists():
            errors.append(f"artifact_missing:{artifact.artifact_path}")
            continue
        observed = file_sha256(path)
        if observed != artifact.sha256:
            errors.append(f"artifact_hash_mismatch:{artifact.artifact_path}")
    if not _source_generation_allows_manifest(root, manifest):
        errors.append("source_generation_hash_stale")
    if manifest.record_count + manifest.excluded_future_record_count > MEMORY_INDEX_STREAMING_AUDIT_THRESHOLD:
        errors.extend(_streaming_snapshot_integrity_errors(root, manifest))
        status = (
            "current_as_of"
            if not errors
            else "stale"
            if any(error.endswith("_stale") for error in errors)
            else "invalid"
        )
        return {
            **base,
            "status": status,
            "passed": not errors,
            "errors": errors,
            "manifest": manifest.model_dump(mode="json"),
            "production_ready": manifest.production_ready,
            "record_count": manifest.record_count,
            "cell_count": manifest.cell_count,
            "streaming_audit": True,
        }
    try:
        replay_availability = load_snapshot_replay_availability(root, manifest)
    except (OSError, ValueError) as exc:
        replay_availability = None
        errors.append(f"availability_projection_invalid:{exc}")
    source_path = root / manifest.source_record_hashes.artifact_path
    embedding_hash_path = root / manifest.embedding_hashes.artifact_path
    try:
        declared_hashes = {str(row["record_id"]): str(row["sha256"]) for row in _read_jsonl(source_path)}
    except (OSError, KeyError, TypeError, ValueError):
        declared_hashes = {}
        errors.append("source_record_hashes_invalid")
    try:
        declared_embedding_hashes = {
            str(row["record_id"]): str(row["sha256"]) for row in _read_jsonl(embedding_hash_path)
        }
    except (OSError, KeyError, TypeError, ValueError):
        declared_embedding_hashes = {}
        errors.append("embedding_hashes_invalid")
    all_source_records = BrainRecordStore(root).list_records()
    current_records = [
        record
        for record in all_source_records
        if _effective_record_available_from(record, replay_availability) <= as_kst(manifest.as_of_cutoff)
    ]
    future_records = [
        record
        for record in all_source_records
        if _effective_record_available_from(record, replay_availability) > as_kst(manifest.as_of_cutoff)
    ]
    current_hashes = brain_record_envelope_hashes(current_records)
    if current_hashes != declared_hashes:
        errors.append("source_record_hashes_stale")
    future_path = root / manifest.excluded_future_record_hashes.artifact_path
    try:
        declared_future_hashes = {str(row["record_id"]): str(row["sha256"]) for row in _read_jsonl(future_path)}
    except (OSError, KeyError, TypeError, ValueError):
        declared_future_hashes = {}
        errors.append("future_record_hashes_invalid")
    observed_future_hashes = brain_record_envelope_hashes(future_records)
    observed_next_available_from = min(
        (_effective_record_available_from(record, replay_availability) for record in future_records),
        default=None,
    )
    if observed_next_available_from != (
        as_kst(manifest.next_available_from) if manifest.next_available_from is not None else None
    ):
        errors.append("next_available_from_stale")
    if declared_future_hashes != observed_future_hashes:
        errors.append("future_record_hashes_stale")
    if (
        set(declared_hashes) & set(declared_future_hashes)
        or len(declared_future_hashes) != manifest.excluded_future_record_count
    ):
        errors.append("source_future_partition_mismatch")
    routing_path = root / manifest.routing_metadata.artifact_path
    try:
        declared_routing = {str(row["record_id"]): row["routing"] for row in _read_jsonl(routing_path)}
    except (OSError, KeyError, TypeError, ValueError):
        declared_routing = {}
        errors.append("routing_metadata_invalid")
    current_routing = {
        record.record_id: record_routing_metadata(record).model_dump(mode="json") for record in current_records
    }
    if declared_routing != current_routing:
        errors.append("routing_metadata_hash_stale")
    if sha256_text(source_path.read_text(encoding="utf-8")) != manifest.corpus_manifest_sha256:
        errors.append("corpus_manifest_hash_mismatch")
    membership_path = root / manifest.memberships.artifact_path
    cell_path = root / manifest.cell_entries.artifact_path
    try:
        membership_rows = [MemoryCellMembership.model_validate(row) for row in _read_jsonl(membership_path)]
        cell_rows = [MemoryCellEntry.model_validate(row) for row in _read_jsonl(cell_path)]
    except (OSError, ValueError):
        membership_rows = []
        cell_rows = []
        errors.append("cell_sidecar_invalid")
    if len(membership_rows) != manifest.memberships.item_count:
        errors.append("membership_sidecar_count_mismatch")
    if len(cell_rows) != manifest.cell_entries.item_count:
        errors.append("cell_sidecar_count_mismatch")

    database_path = root / manifest.database.artifact_path
    if database_path.exists() and not any(error.startswith("artifact_") for error in errors):
        try:
            connection = _connect_index(database_path, read_only=True)
            try:
                record_count = _fetch_count(connection, "SELECT COUNT(*) FROM records")
                primary_count = _fetch_count(connection, "SELECT COUNT(*) FROM memberships")
                distinct_primary = _fetch_count(
                    connection,
                    "SELECT COUNT(DISTINCT record_id) FROM memberships",
                )
                future_count = _fetch_count(
                    connection,
                    "SELECT COUNT(*) FROM records WHERE available_from > ?",
                    [as_kst(manifest.max_available_from).isoformat()],
                )
                if record_count != manifest.record_count:
                    errors.append("database_record_count_mismatch")
                if primary_count != record_count or distinct_primary != record_count:
                    errors.append("database_primary_membership_mismatch")
                if future_count:
                    errors.append("database_future_membership_detected")
                projected_current_records = [
                    _record_with_effective_available_from(
                        record,
                        replay_availability,
                    )
                    for record in current_records
                ]
                errors.extend(
                    _database_integrity_errors(
                        connection,
                        root=root,
                        manifest=manifest,
                        current_records=projected_current_records,
                        declared_hashes=declared_hashes,
                        declared_embedding_hashes=declared_embedding_hashes,
                        memberships=membership_rows,
                        cells=cell_rows,
                    )
                )
            finally:
                connection.close()
        except (duckdb.Error, OSError, ValueError) as exc:
            errors.append(f"database_invalid:{exc}")
    status = "current_as_of" if not errors else "stale" if "source_record_hashes_stale" in errors else "invalid"
    return {
        **base,
        "status": status,
        "passed": not errors,
        "errors": errors,
        "manifest": manifest.model_dump(mode="json"),
        "production_ready": manifest.production_ready,
        "record_count": manifest.record_count,
        "cell_count": manifest.cell_count,
    }


def inspect_current_memory_index(root: Path) -> dict[str, object]:
    pointer_path = root.resolve() / MEMORY_INDEX_ROOT / MEMORY_CURRENT_POINTER
    if not pointer_path.exists():
        return {"status": "missing", "passed": False, "pointer_exists": False}
    try:
        pointer = read_json(pointer_path)
        snapshot_id = str(pointer["snapshot_id"])
        manifest_relative_path = str(pointer["manifest_path"])
        manifest_path = root.resolve() / manifest_relative_path
        manifest_sha256 = str(pointer["manifest_sha256"])
        registry_sha256 = str(pointer["as_of_registry_sha256"])
    except (OSError, KeyError, TypeError, ValueError):
        return {"status": "invalid", "passed": False, "pointer_exists": True}
    expected_manifest_path = (
        root.resolve() / MEMORY_INDEX_ROOT / MEMORY_SNAPSHOT_DIR / snapshot_id / MEMORY_MANIFEST_FILE
    ).resolve()
    expected_relative_path = relative_to_root(expected_manifest_path, root.resolve())
    if manifest_relative_path != expected_relative_path or manifest_path.resolve() != expected_manifest_path:
        return {
            "status": "invalid",
            "passed": False,
            "pointer_exists": True,
            "pointer_path_verified": False,
        }
    inspection = inspect_memory_snapshot(root, snapshot_id)
    if not manifest_path.exists() or sha256_text(manifest_path.read_text(encoding="utf-8")) != manifest_sha256:
        return {**inspection, "status": "invalid", "passed": False, "pointer_hash_verified": False}
    registry_path = root.resolve() / MEMORY_INDEX_ROOT / MEMORY_AS_OF_REGISTRY
    observed_registry_sha256 = (
        sha256_text(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else sha256_text("")
    )
    if observed_registry_sha256 != registry_sha256:
        return {
            **inspection,
            "status": "invalid",
            "passed": False,
            "pointer_hash_verified": True,
            "registry_hash_verified": False,
        }
    manifest_payload = inspection.get("manifest")
    if isinstance(manifest_payload, dict):
        try:
            manifest = MemoryCellSnapshotManifest.model_validate(manifest_payload)
            partition_verified = _current_source_partition_verified(
                root.resolve(),
                manifest,
            )
        except (OSError, KeyError, TypeError, ValueError):
            partition_verified = False
        if not partition_verified:
            return {
                **inspection,
                "status": "stale",
                "passed": False,
                "pointer_hash_verified": True,
                "registry_hash_verified": True,
                "source_partition_verified": False,
            }
    return {
        **inspection,
        "pointer_exists": True,
        "pointer_path_verified": True,
        "pointer_hash_verified": True,
        "registry_hash_verified": True,
        "source_partition_verified": True,
    }


def active_memory_snapshot_manifest(
    root: Path,
) -> MemoryCellSnapshotManifest | None:
    """Resolve the hash-bound active manifest without running a full DB audit."""

    root = root.resolve()
    pointer_path = root / MEMORY_INDEX_ROOT / MEMORY_CURRENT_POINTER
    if not pointer_path.is_file():
        return None
    pointer = read_json(pointer_path)
    if not isinstance(pointer, dict):
        raise ValueError("active memory pointer is invalid")
    snapshot_id = str(pointer.get("snapshot_id") or "")
    manifest_path = (root / MEMORY_INDEX_ROOT / MEMORY_SNAPSHOT_DIR / snapshot_id / MEMORY_MANIFEST_FILE).resolve()
    expected_relative = relative_to_root(manifest_path, root)
    if (
        not snapshot_id
        or pointer.get("manifest_path") != expected_relative
        or not manifest_path.is_file()
        or pointer.get("manifest_sha256") != sha256_text(manifest_path.read_text(encoding="utf-8"))
    ):
        raise ValueError("active memory pointer does not bind its manifest")
    registry_path = root / MEMORY_INDEX_ROOT / MEMORY_AS_OF_REGISTRY
    registry_sha256 = (
        sha256_text(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else sha256_text("")
    )
    if pointer.get("as_of_registry_sha256") != registry_sha256:
        raise ValueError("active memory pointer does not bind its registry")
    manifest = MemoryCellSnapshotManifest.model_validate(read_json(manifest_path))
    if manifest.snapshot_id != snapshot_id:
        raise ValueError("active memory snapshot identity mismatch")
    return manifest


def inspect_verified_evaluation_memory_index(root: Path) -> dict[str, object]:
    """Verify the receipt-bound evaluation pointer without a corpus projection."""

    root = root.resolve()
    manifest = active_memory_snapshot_manifest(root)
    if manifest is None or not manifest.evaluation_only:
        return {
            "status": "not_evaluation",
            "passed": False,
            "production_ready": False,
        }
    pointer_path = root / MEMORY_INDEX_ROOT / MEMORY_CURRENT_POINTER
    pointer = read_json(pointer_path)
    if not isinstance(pointer, dict):
        raise ValueError("evaluation memory pointer is invalid")
    receipt_value = pointer.get("evaluation_receipt_path")
    if not isinstance(receipt_value, str) or not receipt_value:
        raise ValueError("evaluation memory pointer lacks its receipt")
    receipt_path = (root / receipt_value).resolve()
    try:
        receipt_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("evaluation memory receipt escapes the project") from exc
    receipt = read_json(receipt_path)
    if (
        not isinstance(receipt, dict)
        or pointer.get("evaluation_only") is not True
        or pointer.get("evaluation_receipt_sha256") != file_sha256(receipt_path)
        or pointer.get("evaluation_database_sha256") != manifest.database.sha256
        or receipt.get("schema_version") != "nslab.shadow_replay_as_of_snapshot.v1"
        or receipt.get("snapshot_id") != manifest.snapshot_id
        or receipt.get("record_count") != manifest.record_count
        or receipt.get("retained_embedding_count") != manifest.record_count
        or receipt.get("generated_embedding_count") != 0
        or receipt.get("centroid_population_record_count") != manifest.record_count
        or receipt.get("full_corpus_centroids_used") is not False
        or receipt.get("holdout_overlap_count") != 0
        or receipt.get("calibration_overlap_count") != 0
        or receipt.get("immutable") is not True
        or receipt.get("production_available_from_mutated") is not False
        or receipt.get("source_record_hashes_sha256") != manifest.source_record_hashes.sha256
        or receipt.get("availability_projection_sha256")
        != (manifest.availability_projection.sha256 if manifest.availability_projection is not None else None)
        or not (root / manifest.database.artifact_path).is_file()
        or not manifest.production_ready
    ):
        raise ValueError("evaluation memory receipt binding is invalid")
    return {
        "status": "current_as_of",
        "passed": True,
        "production_ready": True,
        "evaluation_only": True,
        "receipt_verified": True,
        "snapshot_id": manifest.snapshot_id,
        "record_count": manifest.record_count,
        "cell_count": manifest.cell_count,
        "manifest": manifest.model_dump(mode="json"),
    }


def _current_source_partition_verified(
    root: Path,
    manifest: MemoryCellSnapshotManifest,
) -> bool:
    connection = duckdb.connect(
        str(root / manifest.database.artifact_path),
        read_only=True,
    )
    try:
        connection.execute("CREATE TEMP TABLE expected_all_source (record_id VARCHAR PRIMARY KEY, sha256 VARCHAR)")
        rows: list[tuple[str, str]] = []
        for record in _iter_source_records(root):
            rows.append((record.record_id, brain_record_envelope_sha256(record)))
            if len(rows) >= 2048:
                connection.executemany(
                    "INSERT INTO expected_all_source VALUES (?, ?)",
                    rows,
                )
                rows.clear()
        if rows:
            connection.executemany(
                "INSERT INTO expected_all_source VALUES (?, ?)",
                rows,
            )
        _load_hash_sidecar(
            connection,
            table_name="current_safe_hashes",
            path=root / manifest.source_record_hashes.artifact_path,
        )
        _load_hash_sidecar(
            connection,
            table_name="current_future_hashes",
            path=root / manifest.excluded_future_record_hashes.artifact_path,
        )
        return not _sql_symmetric_difference_count(
            connection,
            "SELECT record_id, sha256 FROM expected_all_source",
            "SELECT record_id, sha256 FROM current_safe_hashes "
            "UNION ALL SELECT record_id, sha256 FROM current_future_hashes",
        )
    except (duckdb.Error, OSError, ValueError):
        return False
    finally:
        connection.close()


def _streaming_snapshot_integrity_errors(
    root: Path,
    manifest: MemoryCellSnapshotManifest,
) -> list[str]:
    errors: list[str] = []
    try:
        replay_availability = load_snapshot_replay_availability(root, manifest)
    except (OSError, ValueError) as exc:
        return [f"availability_projection_invalid:{exc}"]
    database_path = root / manifest.database.artifact_path
    connection = _connect_index(database_path, read_only=True)
    try:
        connection.execute(
            """
            CREATE TEMP TABLE expected_records (
                record_id VARCHAR PRIMARY KEY,
                episode_id VARCHAR,
                record_type VARCHAR,
                training_target VARCHAR,
                trade_date DATE,
                available_from VARCHAR,
                training_eligible BOOLEAN,
                evidence_phase VARCHAR,
                evidence_polarity VARCHAR,
                label_quality VARCHAR,
                routing_disposition VARCHAR,
                memory_lanes VARCHAR,
                document VARCHAR,
                source_sha256 VARCHAR,
                routing_json VARCHAR,
                independent_unit_id VARCHAR,
                independent_unit_type VARCHAR,
                path_type VARCHAR,
                regime_cluster VARCHAR,
                high_return_pct DOUBLE,
                close_return_pct DOUBLE,
                upper_limit_touched BOOLEAN,
                outcome_observed BOOLEAN,
                sample_weight DOUBLE,
                high_return_status VARCHAR,
                close_return_status VARCHAR,
                upper_limit_status VARCHAR,
                sample_weight_status VARCHAR
            )
            """
        )
        connection.execute("CREATE TEMP TABLE expected_provenance (record_id VARCHAR, source_id VARCHAR)")
        connection.execute("CREATE TEMP TABLE expected_future (record_id VARCHAR PRIMARY KEY, source_sha256 VARCHAR)")
        resolver = RecordMemoryDocumentResolver(root)
        record_rows: list[tuple[object, ...]] = []
        provenance_rows: list[tuple[str, str]] = []
        future_rows: list[tuple[str, str]] = []
        observed_next_available_from: datetime | None = None
        for record in _iter_source_records(root):
            source_hash = brain_record_envelope_sha256(record)
            effective_available_from = _effective_record_available_from(
                record,
                replay_availability,
            )
            if effective_available_from > as_kst(manifest.as_of_cutoff):
                future_rows.append((record.record_id, source_hash))
                observed_next_available_from = min(
                    observed_next_available_from or effective_available_from,
                    effective_available_from,
                )
            else:
                routing = record_routing_metadata(record)
                independent_unit_id = record_independent_unit_id(record)
                population = project_population_record(record)
                record_rows.append(
                    (
                        record.record_id,
                        record.episode_id,
                        record.record_type,
                        record.training_target,
                        record.trade_date,
                        effective_available_from.isoformat(),
                        record.training_eligible,
                        record.evidence_phase,
                        routing.evidence_polarity,
                        routing.label_quality,
                        routing.routing_disposition,
                        canonical_json(routing.memory_lanes),
                        resolver.document(record),
                        source_hash,
                        canonical_json(routing.model_dump(mode="json")),
                        independent_unit_id,
                        independent_unit_type(independent_unit_id) or "unsupported",
                        population.path_type,
                        population.regime_cluster,
                        population.high_return_pct,
                        population.close_return_pct,
                        population.upper_limit_touched,
                        population.outcome_observed,
                        population.sample_weight,
                        population.high_return_status,
                        population.close_return_status,
                        population.upper_limit_status,
                        population.sample_weight_status,
                    )
                )
                provenance_rows.extend(
                    (record.record_id, source_id) for source_id in sorted(set(record.provenance_source_ids))
                )
            if len(record_rows) >= 512:
                connection.executemany(
                    "INSERT INTO expected_records VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    record_rows,
                )
                record_rows.clear()
            if len(provenance_rows) >= 2048:
                connection.executemany(
                    "INSERT INTO expected_provenance VALUES (?, ?)",
                    provenance_rows,
                )
                provenance_rows.clear()
            if len(future_rows) >= 512:
                connection.executemany(
                    "INSERT INTO expected_future VALUES (?, ?)",
                    future_rows,
                )
                future_rows.clear()
        if record_rows:
            connection.executemany(
                "INSERT INTO expected_records VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                record_rows,
            )
        if provenance_rows:
            connection.executemany(
                "INSERT INTO expected_provenance VALUES (?, ?)",
                provenance_rows,
            )
        if future_rows:
            connection.executemany(
                "INSERT INTO expected_future VALUES (?, ?)",
                future_rows,
            )
        if observed_next_available_from != (
            as_kst(manifest.next_available_from) if manifest.next_available_from is not None else None
        ):
            errors.append("next_available_from_stale")
        _load_hash_sidecar(
            connection,
            table_name="declared_source_hashes",
            path=root / manifest.source_record_hashes.artifact_path,
        )
        _load_hash_sidecar(
            connection,
            table_name="declared_future_hashes",
            path=root / manifest.excluded_future_record_hashes.artifact_path,
        )
        _load_hash_sidecar(
            connection,
            table_name="declared_embedding_hashes",
            path=root / manifest.embedding_hashes.artifact_path,
        )
        _load_membership_sidecar(
            connection,
            path=root / manifest.memberships.artifact_path,
        )
        _load_cell_sidecar(
            connection,
            path=root / manifest.cell_entries.artifact_path,
        )
        if _sql_symmetric_difference_count(
            connection,
            "SELECT record_id, source_sha256 FROM expected_records",
            "SELECT record_id, sha256 FROM declared_source_hashes",
        ):
            errors.append("source_record_hashes_stale")
        if _sql_symmetric_difference_count(
            connection,
            "SELECT record_id, source_sha256 FROM expected_future",
            "SELECT record_id, sha256 FROM declared_future_hashes",
        ):
            errors.append("future_record_hashes_stale")
        comparable_columns = (
            "record_id, episode_id, record_type, training_target, trade_date, "
            "available_from, training_eligible, evidence_phase, evidence_polarity, "
            "label_quality, routing_disposition, memory_lanes, document, source_sha256, "
            "routing_json, independent_unit_id, independent_unit_type, path_type, "
            "regime_cluster, high_return_pct, close_return_pct, upper_limit_touched, "
            "outcome_observed, sample_weight, high_return_status, close_return_status, "
            "upper_limit_status, sample_weight_status"
        )
        if _sql_symmetric_difference_count(
            connection,
            f"SELECT {comparable_columns} FROM expected_records",
            f"SELECT {comparable_columns} FROM records",
        ):
            errors.append("database_record_projection_mismatch")
        if _sql_symmetric_difference_count(
            connection,
            "SELECT record_id, source_id FROM expected_provenance",
            "SELECT record_id, source_id FROM provenance_edges",
        ):
            errors.append("database_provenance_edges_mismatch")
        if _sql_symmetric_difference_count(
            connection,
            "SELECT record_id, embedding_sha256 FROM records",
            "SELECT record_id, sha256 FROM declared_embedding_hashes",
        ):
            errors.append("database_embedding_hashes_mismatch")
        expected_routing_root = _routing_root_from_query(
            connection,
            "SELECT record_id, routing_json FROM expected_records ORDER BY record_id",
        )
        if expected_routing_root != manifest.routing_metadata_sha256:
            errors.append("routing_metadata_hash_stale")
        errors.extend(_streaming_cell_integrity_errors(connection))
        errors.extend(_database_index_readiness_errors(connection, manifest))
    except (duckdb.Error, OSError, ValueError) as exc:
        errors.append(f"streaming_database_invalid:{exc}")
    finally:
        connection.close()
    return errors


def _load_hash_sidecar(
    connection: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    path: Path,
) -> None:
    connection.execute(f"CREATE TEMP TABLE {table_name} (record_id VARCHAR PRIMARY KEY, sha256 VARCHAR)")
    rows: list[tuple[str, str]] = []
    for row in _read_jsonl(path):
        rows.append((str(row["record_id"]), str(row["sha256"])))
        if len(rows) >= 2048:
            connection.executemany(
                f"INSERT INTO {table_name} VALUES (?, ?)",
                rows,
            )
            rows.clear()
    if rows:
        connection.executemany(f"INSERT INTO {table_name} VALUES (?, ?)", rows)


def _load_membership_sidecar(
    connection: duckdb.DuckDBPyConnection,
    *,
    path: Path,
) -> None:
    connection.execute(
        """
        CREATE TEMP TABLE declared_memberships (
            record_id VARCHAR PRIMARY KEY,
            primary_cell_id VARCHAR,
            secondary_cell_ids VARCHAR,
            independent_unit_id VARCHAR,
            membership_score DOUBLE,
            membership_rule VARCHAR,
            membership_rule_version VARCHAR,
            available_from VARCHAR,
            routing_disposition VARCHAR
        )
        """
    )
    rows: list[tuple[object, ...]] = []
    for raw in _read_jsonl(path):
        item = MemoryCellMembership.model_validate(raw)
        rows.append(
            (
                item.record_id,
                item.primary_cell_id,
                canonical_json(item.secondary_cell_ids),
                item.independent_unit_id,
                item.membership_score,
                item.membership_rule,
                item.membership_rule_version,
                item.available_from.isoformat(),
                item.routing_disposition,
            )
        )
        if len(rows) >= 1024:
            connection.executemany(
                "INSERT INTO declared_memberships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            rows.clear()
    if rows:
        connection.executemany(
            "INSERT INTO declared_memberships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _load_cell_sidecar(
    connection: duckdb.DuckDBPyConnection,
    *,
    path: Path,
) -> None:
    connection.execute(
        """
        CREATE TEMP TABLE declared_cells (
            cell_id VARCHAR PRIMARY KEY,
            signature VARCHAR,
            primary_member_count INTEGER,
            reasoning_member_count INTEGER,
            secondary_member_count INTEGER,
            independent_unit_count INTEGER,
            centroid_sha256 VARCHAR,
            reasoning_centroid_sha256 VARCHAR
        )
        """
    )
    rows = []
    for raw in _read_jsonl(path):
        item = MemoryCellEntry.model_validate(raw)
        rows.append(
            (
                item.cell_id,
                item.signature,
                item.primary_member_count,
                item.reasoning_member_count,
                item.secondary_member_count,
                item.independent_unit_count,
                item.centroid_sha256,
                item.reasoning_centroid_sha256,
            )
        )
    if rows:
        connection.executemany(
            "INSERT INTO declared_cells VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _sql_symmetric_difference_count(
    connection: duckdb.DuckDBPyConnection,
    left_query: str,
    right_query: str,
) -> int:
    return _fetch_count(
        connection,
        "SELECT CAST(SUM(item_count) AS BIGINT) FROM ("
        f"SELECT COUNT(*) AS item_count FROM (({left_query}) EXCEPT ({right_query})) "
        f"UNION ALL SELECT COUNT(*) AS item_count FROM (({right_query}) EXCEPT ({left_query}))"
        ") differences",
    )


def _streaming_cell_integrity_errors(
    connection: duckdb.DuckDBPyConnection,
) -> list[str]:
    errors: list[str] = []
    cell_sums: dict[str, npt.NDArray[np.int64]] = {}
    reasoning_sums: dict[str, npt.NDArray[np.int64]] = {}
    cell_counts: Counter[str] = Counter()
    reasoning_counts: Counter[str] = Counter()
    last_record_id = ""
    dimensions = 0
    while rows := connection.execute(
        """
        SELECT record_id, embedding, signature, primary_cell_id, routing_disposition,
               margins, independent_unit_id, available_from
        FROM records WHERE record_id > ? ORDER BY record_id LIMIT 512
        """,
        [last_record_id],
    ).fetchall():
        vectors = [[float(value) for value in row[1]] for row in rows]
        signatures = vector_signatures_and_margins(vectors)
        dimensions = len(vectors[0])
        for row, vector, (signature, _margins) in zip(
            rows,
            vectors,
            signatures,
            strict=True,
        ):
            record_id = str(row[0])
            routing_disposition = str(row[4])
            if signature != str(row[2]) or _cell_id(signature) != str(row[3]):
                errors.append("database_record_signature_mismatch")
                return errors
            quantized = np.rint(np.asarray(vector, dtype=np.float32) * MEMORY_VECTOR_QUANTIZATION_SCALE).astype(
                np.int64
            )
            cell_sums.setdefault(signature, np.zeros(dimensions, dtype=np.int64))
            cell_sums[signature] += quantized
            cell_counts[signature] += 1
            if routing_disposition == "REASONING":
                reasoning_sums.setdefault(
                    signature,
                    np.zeros(dimensions, dtype=np.int64),
                )
                reasoning_sums[signature] += quantized
                reasoning_counts[signature] += 1
            last_record_id = record_id
    centroids = {
        signature: normalized_quantized_sum(values, cell_counts[signature]) for signature, values in cell_sums.items()
    }
    reasoning_centroids = {
        signature: normalized_quantized_sum(
            values,
            reasoning_counts[signature],
        )
        for signature, values in reasoning_sums.items()
    }
    cell_ids_by_signature = {signature: _cell_id(signature) for signature in cell_sums}
    connection.execute(
        """
        CREATE TEMP TABLE expected_memberships (
            record_id VARCHAR PRIMARY KEY,
            primary_cell_id VARCHAR,
            secondary_cell_ids VARCHAR,
            independent_unit_id VARCHAR,
            membership_score DOUBLE,
            membership_rule VARCHAR,
            membership_rule_version VARCHAR,
            available_from VARCHAR,
            routing_disposition VARCHAR
        )
        """
    )
    connection.execute("CREATE TEMP TABLE expected_secondary_memberships (record_id VARCHAR, cell_id VARCHAR)")
    last_record_id = ""
    expected_membership_rows: list[tuple[object, ...]] = []
    expected_secondary_rows: list[tuple[str, str]] = []
    while rows := connection.execute(
        """
        SELECT record_id, embedding, signature, margins, independent_unit_id,
               available_from, routing_disposition
        FROM records WHERE record_id > ? ORDER BY record_id LIMIT 512
        """,
        [last_record_id],
    ).fetchall():
        for row in rows:
            record_id = str(row[0])
            vector = [float(value) for value in row[1]]
            signature = str(row[2])
            margins = [float(value) for value in row[3]]
            primary_cell_id = cell_ids_by_signature[signature]
            secondary_cell_ids = _secondary_cells(
                signature,
                margins=margins,
                cell_ids_by_signature=cell_ids_by_signature,
            )
            expected_membership_rows.append(
                (
                    record_id,
                    primary_cell_id,
                    canonical_json(secondary_cell_ids),
                    str(row[4]),
                    max(
                        0.0,
                        min(
                            1.0,
                            _cosine_similarity(vector, centroids[signature]),
                        ),
                    ),
                    MEMORY_CELL_MEMBERSHIP_RULE,
                    MEMORY_CELL_MEMBERSHIP_RULE_VERSION,
                    str(row[5]),
                    str(row[6]),
                )
            )
            expected_secondary_rows.extend((record_id, cell_id) for cell_id in secondary_cell_ids)
            last_record_id = record_id
        connection.executemany(
            "INSERT INTO expected_memberships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            expected_membership_rows,
        )
        expected_membership_rows.clear()
        if expected_secondary_rows:
            connection.executemany(
                "INSERT INTO expected_secondary_memberships VALUES (?, ?)",
                expected_secondary_rows,
            )
            expected_secondary_rows.clear()
    membership_columns = (
        "record_id, primary_cell_id, independent_unit_id, membership_score, membership_rule, membership_rule_version"
    )
    if _sql_symmetric_difference_count(
        connection,
        f"SELECT {membership_columns} FROM expected_memberships",
        f"SELECT {membership_columns} FROM memberships",
    ):
        errors.append("database_memberships_recomputed_mismatch")
    declared_membership_columns = (
        "record_id, primary_cell_id, secondary_cell_ids, independent_unit_id, "
        "membership_score, membership_rule, membership_rule_version, "
        "available_from, routing_disposition"
    )
    if _sql_symmetric_difference_count(
        connection,
        f"SELECT {declared_membership_columns} FROM expected_memberships",
        f"SELECT {declared_membership_columns} FROM declared_memberships",
    ):
        errors.append("membership_sidecar_recomputed_mismatch")
    if _sql_symmetric_difference_count(
        connection,
        "SELECT record_id, cell_id FROM expected_secondary_memberships",
        "SELECT record_id, cell_id FROM secondary_memberships",
    ):
        errors.append("database_secondary_memberships_recomputed_mismatch")
    if _sql_symmetric_difference_count(
        connection,
        "SELECT record_id, independent_unit_id FROM expected_records",
        "SELECT record_id, independent_unit_id FROM records",
    ):
        errors.append("database_independent_unit_mismatch")
    secondary_counts = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT cell_id, COUNT(*) FROM expected_secondary_memberships GROUP BY cell_id"
        ).fetchall()
    }
    independent_counts = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT primary_cell_id, COUNT(DISTINCT independent_unit_id) "
            "FROM expected_memberships GROUP BY primary_cell_id"
        ).fetchall()
    }
    observed_cells = {
        str(row[0]): (
            str(row[1]),
            int(row[2]),
            int(row[3]),
            int(row[4]),
            int(row[5]),
            sha256_text(canonical_json([float(value) for value in row[6]])),
        )
        for row in connection.execute(
            "SELECT cell_id, signature, primary_member_count, "
            "reasoning_member_count, secondary_member_count, "
            "independent_unit_count, centroid FROM cells"
        ).fetchall()
    }
    expected_cells = {
        _cell_id(signature): (
            signature,
            cell_counts[signature],
            reasoning_counts[signature],
            secondary_counts.get(_cell_id(signature), 0),
            independent_counts[_cell_id(signature)],
            sha256_text(canonical_json(centroids[signature])),
        )
        for signature in cell_sums
    }
    if observed_cells != expected_cells:
        errors.append("database_cells_recomputed_mismatch")
    observed_reasoning = {
        str(row[0]): (int(row[1]), [float(value) for value in row[2]])
        for row in connection.execute("SELECT cell_id, primary_member_count, centroid FROM reasoning_cells").fetchall()
    }
    expected_reasoning = {
        _cell_id(signature): (
            reasoning_counts[signature],
            reasoning_centroids[signature],
        )
        for signature in reasoning_sums
    }
    if observed_reasoning != expected_reasoning:
        errors.append("database_reasoning_cells_mismatch")
    declared_cell_columns = (
        "cell_id, signature, primary_member_count, reasoning_member_count, "
        "secondary_member_count, independent_unit_count, centroid_sha256"
    )
    connection.execute(
        """
        CREATE TEMP TABLE expected_cells (
            cell_id VARCHAR PRIMARY KEY,
            signature VARCHAR,
            primary_member_count INTEGER,
            reasoning_member_count INTEGER,
            secondary_member_count INTEGER,
            independent_unit_count INTEGER,
            centroid_sha256 VARCHAR,
            reasoning_centroid_sha256 VARCHAR
        )
        """
    )
    connection.executemany(
        "INSERT INTO expected_cells VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                cell_id,
                values[0],
                values[1],
                values[2],
                values[3],
                values[4],
                values[5],
                (
                    sha256_text(canonical_json(reasoning_centroids[values[0]]))
                    if values[0] in reasoning_centroids
                    else None
                ),
            )
            for cell_id, values in expected_cells.items()
        ],
    )
    if _sql_symmetric_difference_count(
        connection,
        f"SELECT {declared_cell_columns}, reasoning_centroid_sha256 FROM expected_cells",
        f"SELECT {declared_cell_columns}, reasoning_centroid_sha256 FROM declared_cells",
    ):
        errors.append("cell_sidecar_recomputed_mismatch")
    return errors


def _database_index_readiness_errors(
    connection: duckdb.DuckDBPyConnection,
    manifest: MemoryCellSnapshotManifest,
) -> list[str]:
    errors: list[str] = []
    unsupported_count, unsupported_hash = _unsupported_reasoning_identity(connection)
    if (
        unsupported_count != manifest.unsupported_reasoning_record_count
        or unsupported_hash != manifest.unsupported_reasoning_record_ids_sha256
    ):
        errors.append("database_unsupported_reasoning_units_mismatch")
    index_names = {str(row[0]) for row in connection.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
    required = {
        "records_available_idx",
        "records_disposition_idx",
        "records_type_idx",
        "records_primary_cell_idx",
        "reasoning_facets_value_idx",
        "reasoning_facets_cell_idx",
        "secondary_cell_idx",
        "provenance_source_idx",
    }
    if manifest.metadata_index_ready and not required <= index_names:
        errors.append("database_metadata_indexes_missing")
    if (
        manifest.hnsw_index_ready
        and not {
            "reasoning_cells_hnsw_idx",
            "reasoning_cell_facets_hnsw_idx",
        }
        <= index_names
    ):
        errors.append("database_hnsw_indexes_missing")
    schema_names = {
        str(row[0]) for row in connection.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
    }
    if manifest.fts_index_ready and "fts_main_reasoning_records" not in schema_names:
        errors.append("database_fts_index_missing")
    try:
        reasoning_projection_mismatch = _sql_symmetric_difference_count(
            connection,
            "SELECT record_id, primary_cell_id, memory_lanes, regime_cluster, document FROM reasoning_records",
            "SELECT record_id, primary_cell_id, memory_lanes, regime_cluster, "
            "document FROM records "
            "WHERE routing_disposition = 'REASONING'",
        )
    except duckdb.Error:
        reasoning_projection_mismatch = 1
    if reasoning_projection_mismatch:
        errors.append("database_reasoning_fts_projection_mismatch")
    errors.extend(_reasoning_cell_facet_errors(connection))
    return errors


def _reasoning_cell_facet_errors(
    connection: duckdb.DuckDBPyConnection,
) -> list[str]:
    expected = """
        SELECT 'lane' AS facet_kind,
               lane.lane_value AS facet_value,
               primary_cell_id AS cell_id,
               COUNT(*) AS primary_member_count,
               COUNT(DISTINCT independent_unit_id) AS independent_unit_count
        FROM records
        CROSS JOIN UNNEST(
            from_json(memory_lanes, '["VARCHAR"]')
        ) AS lane(lane_value)
        WHERE routing_disposition = 'REASONING'
        GROUP BY lane.lane_value, primary_cell_id
        UNION ALL
        SELECT 'regime' AS facet_kind,
               upper(trim(regime_cluster)) AS facet_value,
               primary_cell_id AS cell_id,
               COUNT(*) AS primary_member_count,
               COUNT(DISTINCT independent_unit_id) AS independent_unit_count
        FROM records
        WHERE routing_disposition = 'REASONING'
          AND trim(regime_cluster) != ''
        GROUP BY upper(trim(regime_cluster)), primary_cell_id
        UNION ALL
        SELECT 'lane_regime' AS facet_kind,
               lane.lane_value || '|' || upper(trim(regime_cluster))
                   AS facet_value,
               primary_cell_id AS cell_id,
               COUNT(*) AS primary_member_count,
               COUNT(DISTINCT independent_unit_id) AS independent_unit_count
        FROM records
        CROSS JOIN UNNEST(
            from_json(memory_lanes, '["VARCHAR"]')
        ) AS lane(lane_value)
        WHERE routing_disposition = 'REASONING'
          AND trim(regime_cluster) != ''
        GROUP BY lane.lane_value, upper(trim(regime_cluster)), primary_cell_id
    """
    observed = (
        "SELECT facet_kind, facet_value, cell_id, primary_member_count, "
        "independent_unit_count FROM reasoning_cell_facets"
    )
    errors = []
    try:
        if _sql_symmetric_difference_count(connection, expected, observed):
            errors.append("database_reasoning_cell_facets_mismatch")
        centroid_mismatch = _fetch_count(
            connection,
            "SELECT COUNT(*) FROM reasoning_cell_facets facets "
            "JOIN reasoning_cells cells USING (cell_id) "
            "WHERE facets.centroid != cells.centroid",
        )
        if centroid_mismatch:
            errors.append("database_reasoning_cell_facet_centroid_mismatch")
    except duckdb.Error:
        errors.append("database_reasoning_cell_facets_missing")
    return errors


def _unsupported_reasoning_identity(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[int, str]:
    record_ids = [
        str(row[0])
        for row in connection.execute(
            "SELECT record_id FROM records "
            "WHERE routing_disposition = 'REASONING' "
            "AND independent_unit_type = 'unsupported' ORDER BY record_id"
        ).fetchall()
    ]
    return len(record_ids), sha256_text(canonical_json(record_ids))


def _database_integrity_errors(
    connection: duckdb.DuckDBPyConnection,
    *,
    root: Path,
    manifest: MemoryCellSnapshotManifest,
    current_records: list[BrainRecordEnvelope],
    declared_hashes: dict[str, str],
    declared_embedding_hashes: dict[str, str],
    memberships: list[MemoryCellMembership],
    cells: list[MemoryCellEntry],
) -> list[str]:
    errors: list[str] = []
    database_record_ids = {str(row[0]) for row in connection.execute("SELECT record_id FROM records").fetchall()}
    if database_record_ids != set(declared_hashes):
        errors.append("database_record_ids_mismatch")
    database_vectors = {
        str(row[0]): [float(value) for value in row[1]]
        for row in connection.execute("SELECT record_id, embedding FROM records").fetchall()
    }
    observed_embedding_hashes = {
        record_id: sha256_text(canonical_json(vector)) for record_id, vector in database_vectors.items()
    }
    if observed_embedding_hashes != declared_embedding_hashes:
        errors.append("database_embedding_hashes_mismatch")
    source_by_id = {record.record_id: record for record in current_records}
    source_documents = build_record_memory_documents(
        root,
        current_records,
    )
    database_record_projection = {
        str(row[0]): tuple(row[1:26])
        for row in connection.execute(
            """
            SELECT record_id, episode_id, record_type, training_target,
                   trade_date, available_from, training_eligible, evidence_phase,
                   evidence_polarity, label_quality, routing_disposition,
                   memory_lanes, document, primary_cell_id,
                   independent_unit_type, path_type, regime_cluster,
                   high_return_pct, close_return_pct, upper_limit_touched,
                   outcome_observed, sample_weight, high_return_status,
                   close_return_status, upper_limit_status, sample_weight_status
            FROM records
            """
        ).fetchall()
    }
    expected_record_projection: dict[str, tuple[object, ...]] = {}
    membership_primary = {item.record_id: item.primary_cell_id for item in memberships}
    for record_id, record in source_by_id.items():
        routing = record_routing_metadata(record)
        population = project_population_record(record)
        unit_id = record_independent_unit_id(record)
        expected_record_projection[record_id] = (
            record.episode_id,
            record.record_type,
            record.training_target,
            record.trade_date,
            as_kst(record.available_from).isoformat(),
            record.training_eligible,
            record.evidence_phase,
            routing.evidence_polarity,
            routing.label_quality,
            routing.routing_disposition,
            canonical_json(routing.memory_lanes),
            source_documents[record_id],
            membership_primary.get(record_id),
            independent_unit_type(unit_id) or "unsupported",
            population.path_type,
            population.regime_cluster,
            population.high_return_pct,
            population.close_return_pct,
            population.upper_limit_touched,
            population.outcome_observed,
            population.sample_weight,
            population.high_return_status,
            population.close_return_status,
            population.upper_limit_status,
            population.sample_weight_status,
        )
    if database_record_projection != expected_record_projection:
        errors.append("database_record_projection_mismatch")
    database_memberships = {
        str(row[0]): (
            str(row[1]),
            str(row[2]),
            float(row[3]),
            str(row[4]),
            str(row[5]),
        )
        for row in connection.execute(
            """
            SELECT record_id, primary_cell_id, independent_unit_id,
                   membership_score, membership_rule, membership_rule_version
            FROM memberships
            """
        ).fetchall()
    }
    expected_memberships = {
        item.record_id: (
            item.primary_cell_id,
            item.independent_unit_id,
            item.membership_score,
            item.membership_rule,
            item.membership_rule_version,
        )
        for item in memberships
    }
    if database_memberships != expected_memberships:
        errors.append("database_membership_sidecar_mismatch")
    database_secondary = {
        (str(row[0]), str(row[1]))
        for row in connection.execute("SELECT record_id, cell_id FROM secondary_memberships").fetchall()
    }
    expected_secondary = {(item.record_id, cell_id) for item in memberships for cell_id in item.secondary_cell_ids}
    if database_secondary != expected_secondary:
        errors.append("database_secondary_membership_mismatch")
    database_cells = {
        str(row[0]): (
            str(row[1]),
            int(row[2]),
            int(row[3]),
            int(row[4]),
            int(row[5]),
            sha256_text(canonical_json([float(value) for value in row[6]])),
        )
        for row in connection.execute(
            """
            SELECT cell_id, signature, primary_member_count,
                   reasoning_member_count, secondary_member_count,
                   independent_unit_count, centroid
            FROM cells
            """
        ).fetchall()
    }
    expected_cells = {
        item.cell_id: (
            item.signature,
            item.primary_member_count,
            item.reasoning_member_count,
            item.secondary_member_count,
            item.independent_unit_count,
            item.centroid_sha256,
        )
        for item in cells
    }
    if database_cells != expected_cells:
        errors.append("database_cell_sidecar_mismatch")
    if set(database_vectors) == {record.record_id for record in current_records}:
        ordered_records = sorted(current_records, key=lambda item: item.record_id)
        recomputed = build_memory_cells(
            ordered_records,
            [database_vectors[record.record_id] for record in ordered_records],
            max_available_from=manifest.max_available_from,
            documents=source_documents,
        )
        recomputed_memberships = {item.record_id: item.model_dump(mode="json") for item in recomputed.memberships}
        declared_memberships = {item.record_id: item.model_dump(mode="json") for item in memberships}
        if recomputed_memberships != declared_memberships:
            errors.append("source_recomputed_membership_mismatch")
        recomputed_cells = {item.cell_id: item.model_dump(mode="json") for item in recomputed.cells}
        declared_cells = {item.cell_id: item.model_dump(mode="json") for item in cells}
        if recomputed_cells != declared_cells:
            errors.append("source_recomputed_cells_mismatch")
        reasoning_cell_projection = {
            str(row[0]): (
                int(row[1]),
                int(row[2]),
                sha256_text(canonical_json([float(value) for value in row[3]])),
            )
            for row in connection.execute(
                """
                SELECT cell_id, primary_member_count, independent_unit_count, centroid
                FROM reasoning_cells
                """
            ).fetchall()
        }
        reasoning_units: dict[str, set[str]] = {}
        for item in recomputed.memberships:
            if item.routing_disposition == "REASONING":
                reasoning_units.setdefault(item.primary_cell_id, set()).add(item.independent_unit_id)
        expected_reasoning_projection = {
            cell.cell_id: (
                cell.reasoning_member_count,
                len(reasoning_units[cell.cell_id]),
                cell.reasoning_centroid_sha256,
            )
            for cell in recomputed.cells
            if cell.reasoning_member_count > 0
        }
        if reasoning_cell_projection != expected_reasoning_projection:
            errors.append("database_reasoning_cells_mismatch")
    database_provenance = {
        (str(row[0]), str(row[1]))
        for row in connection.execute("SELECT record_id, source_id FROM provenance_edges").fetchall()
    }
    expected_provenance = {
        (record.record_id, source_id) for record in current_records for source_id in set(record.provenance_source_ids)
    }
    if database_provenance != expected_provenance:
        errors.append("database_provenance_edges_mismatch")
    database_dispositions = Counter(
        str(row[0]) for row in connection.execute("SELECT routing_disposition FROM records").fetchall()
    )
    manifest_dispositions = Counter(
        {
            "REASONING": manifest.reasoning_record_count,
            "CONTEXT": manifest.context_record_count,
            "AUDIT": manifest.audit_record_count,
            "QUARANTINED": manifest.quarantined_record_count,
        }
    )
    if +database_dispositions != +manifest_dispositions:
        errors.append("database_routing_disposition_count_mismatch")
    index_names = {str(row[0]) for row in connection.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
    metadata_indexes = {
        "records_available_idx",
        "records_disposition_idx",
        "records_type_idx",
        "records_primary_cell_idx",
        "records_unit_type_idx",
        "reasoning_facets_value_idx",
        "reasoning_facets_cell_idx",
        "secondary_cell_idx",
        "provenance_source_idx",
    }
    if manifest.metadata_index_ready and not metadata_indexes <= index_names:
        errors.append("database_metadata_indexes_missing")
    if (
        manifest.hnsw_index_ready
        and not {
            "reasoning_cells_hnsw_idx",
            "reasoning_cell_facets_hnsw_idx",
        }
        <= index_names
    ):
        errors.append("database_hnsw_indexes_missing")
    schema_names = {
        str(row[0]) for row in connection.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
    }
    if manifest.fts_index_ready and "fts_main_reasoning_records" not in schema_names:
        errors.append("database_fts_index_missing")
    errors.extend(_reasoning_cell_facet_errors(connection))
    unsupported_count, unsupported_hash = _unsupported_reasoning_identity(connection)
    if (
        unsupported_count != manifest.unsupported_reasoning_record_count
        or unsupported_hash != manifest.unsupported_reasoning_record_ids_sha256
    ):
        errors.append("database_unsupported_reasoning_units_mismatch")
    return errors


def _create_streaming_database(
    path: Path,
    *,
    dimensions: int,
) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(str(path))
    vector_type = _vector_type(dimensions)
    connection.execute(
        f"""
        CREATE TABLE records (
            record_id VARCHAR PRIMARY KEY,
            episode_id VARCHAR NOT NULL,
            record_type VARCHAR NOT NULL,
            training_target VARCHAR,
            trade_date DATE NOT NULL,
            available_from VARCHAR NOT NULL,
            training_eligible BOOLEAN NOT NULL,
            evidence_phase VARCHAR NOT NULL,
            evidence_polarity VARCHAR NOT NULL,
            label_quality VARCHAR NOT NULL,
            routing_disposition VARCHAR NOT NULL,
            memory_lanes VARCHAR NOT NULL,
            document VARCHAR NOT NULL,
            primary_cell_id VARCHAR NOT NULL,
            embedding {vector_type} NOT NULL,
            signature VARCHAR NOT NULL,
            margins FLOAT[{MEMORY_CELL_SIGNATURE_BITS}] NOT NULL,
            independent_unit_id VARCHAR NOT NULL,
            independent_unit_type VARCHAR NOT NULL,
            path_type VARCHAR NOT NULL,
            regime_cluster VARCHAR NOT NULL,
            high_return_pct DOUBLE,
            close_return_pct DOUBLE,
            upper_limit_touched BOOLEAN,
            outcome_observed BOOLEAN NOT NULL,
            sample_weight DOUBLE NOT NULL,
            high_return_status VARCHAR NOT NULL,
            close_return_status VARCHAR NOT NULL,
            upper_limit_status VARCHAR NOT NULL,
            sample_weight_status VARCHAR NOT NULL,
            source_sha256 VARCHAR NOT NULL,
            routing_json VARCHAR NOT NULL,
            embedding_sha256 VARCHAR NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE cells (
            cell_id VARCHAR PRIMARY KEY,
            signature VARCHAR NOT NULL,
            primary_member_count INTEGER NOT NULL,
            reasoning_member_count INTEGER NOT NULL,
            secondary_member_count INTEGER NOT NULL,
            independent_unit_count INTEGER NOT NULL,
            centroid {vector_type} NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE reasoning_cells (
            cell_id VARCHAR PRIMARY KEY,
            primary_member_count INTEGER NOT NULL,
            independent_unit_count INTEGER NOT NULL,
            centroid {vector_type} NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE memberships (
            record_id VARCHAR PRIMARY KEY,
            primary_cell_id VARCHAR NOT NULL,
            independent_unit_id VARCHAR NOT NULL,
            membership_score DOUBLE NOT NULL,
            membership_rule VARCHAR NOT NULL,
            membership_rule_version VARCHAR NOT NULL
        )
        """
    )
    connection.execute("CREATE TABLE secondary_memberships (record_id VARCHAR NOT NULL, cell_id VARCHAR NOT NULL)")
    connection.execute("CREATE TABLE provenance_edges (record_id VARCHAR NOT NULL, source_id VARCHAR NOT NULL)")
    return connection


def _finalize_streaming_database(
    connection: duckdb.DuckDBPyConnection,
    *,
    build_dir: Path,
    dimensions: int,
    cell_sums: dict[str, npt.NDArray[np.int64]],
    reasoning_sums: dict[str, npt.NDArray[np.int64]],
    cell_counts: Counter[str],
    reasoning_counts: Counter[str],
    require_extensions: bool,
) -> _FinalizedDatabaseState:
    signatures = sorted(cell_sums)
    cell_ids_by_signature = {signature: _cell_id(signature) for signature in signatures}
    centroids = {
        signature: normalized_quantized_sum(cell_sums[signature], cell_counts[signature]) for signature in signatures
    }
    reasoning_centroids = {
        signature: normalized_quantized_sum(
            reasoning_sums[signature],
            reasoning_counts[signature],
        )
        for signature in sorted(reasoning_sums)
    }
    membership_path = build_dir / MEMORY_MEMBERSHIP_FILE
    membership_file = membership_path.open("wb")
    secondary_count = 0
    membership_rows: list[tuple[object, ...]] = []
    secondary_rows: list[tuple[str, str]] = []
    try:
        last_record_id = ""
        while rows := connection.execute(
            """
            SELECT record_id, signature, margins, embedding, independent_unit_id,
                   available_from, routing_disposition
            FROM records
            WHERE record_id > ?
            ORDER BY record_id
            LIMIT 512
            """,
            [last_record_id],
        ).fetchall():
            for row in rows:
                record_id = str(row[0])
                signature = str(row[1])
                margins = [float(value) for value in row[2]]
                vector = [float(value) for value in row[3]]
                independent_unit_id = str(row[4])
                available_from = parse_datetime(str(row[5]))
                routing_disposition = str(row[6])
                primary_cell_id = cell_ids_by_signature[signature]
                secondary_cell_ids = _secondary_cells(
                    signature,
                    margins=margins,
                    cell_ids_by_signature=cell_ids_by_signature,
                )
                membership_score = max(
                    0.0,
                    min(
                        1.0,
                        _cosine_similarity(vector, centroids[signature]),
                    ),
                )
                membership = MemoryCellMembership(
                    record_id=record_id,
                    primary_cell_id=primary_cell_id,
                    secondary_cell_ids=secondary_cell_ids,
                    independent_unit_id=independent_unit_id,
                    membership_score=membership_score,
                    membership_rule=MEMORY_CELL_MEMBERSHIP_RULE,
                    membership_rule_version=MEMORY_CELL_MEMBERSHIP_RULE_VERSION,
                    available_from=available_from,
                    routing_disposition=routing_disposition,
                )
                _write_jsonl_row(
                    membership_file,
                    membership.model_dump(mode="json"),
                )
                membership_rows.append(
                    (
                        record_id,
                        primary_cell_id,
                        independent_unit_id,
                        membership_score,
                        MEMORY_CELL_MEMBERSHIP_RULE,
                        MEMORY_CELL_MEMBERSHIP_RULE_VERSION,
                    )
                )
                secondary_rows.extend((record_id, cell_id) for cell_id in secondary_cell_ids)
                secondary_count += len(secondary_cell_ids)
                last_record_id = record_id
            connection.executemany(
                "INSERT INTO memberships VALUES (?, ?, ?, ?, ?, ?)",
                membership_rows,
            )
            membership_rows.clear()
            if secondary_rows:
                connection.executemany(
                    "INSERT INTO secondary_memberships VALUES (?, ?)",
                    secondary_rows,
                )
                secondary_rows.clear()
    finally:
        membership_file.close()
    secondary_by_cell = {
        str(row[0]): int(row[1])
        for row in connection.execute("SELECT cell_id, COUNT(*) FROM secondary_memberships GROUP BY cell_id").fetchall()
    }
    independent_by_cell = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT primary_cell_id, COUNT(DISTINCT independent_unit_id) FROM memberships GROUP BY primary_cell_id"
        ).fetchall()
    }
    reasoning_units = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT primary_cell_id, COUNT(DISTINCT independent_unit_id) "
            "FROM records WHERE routing_disposition = 'REASONING' "
            "GROUP BY primary_cell_id"
        ).fetchall()
    }
    cell_entries = []
    cell_rows = []
    reasoning_rows = []
    for signature in signatures:
        cell_id = cell_ids_by_signature[signature]
        centroid = centroids[signature]
        reasoning_count = reasoning_counts[signature]
        reasoning_centroid = reasoning_centroids.get(signature)
        entry = MemoryCellEntry(
            cell_id=cell_id,
            signature=signature,
            primary_member_count=cell_counts[signature],
            reasoning_member_count=reasoning_count,
            secondary_member_count=secondary_by_cell.get(cell_id, 0),
            independent_unit_count=independent_by_cell[cell_id],
            centroid_sha256=sha256_text(canonical_json(centroid)),
            reasoning_centroid_sha256=(
                sha256_text(canonical_json(reasoning_centroid)) if reasoning_centroid is not None else None
            ),
        )
        cell_entries.append(entry)
        cell_rows.append(
            (
                cell_id,
                signature,
                entry.primary_member_count,
                reasoning_count,
                entry.secondary_member_count,
                entry.independent_unit_count,
                centroid,
            )
        )
        if reasoning_centroid is not None:
            reasoning_rows.append(
                (
                    cell_id,
                    reasoning_count,
                    reasoning_units[cell_id],
                    reasoning_centroid,
                )
            )
    connection.executemany(
        "INSERT INTO cells VALUES (?, ?, ?, ?, ?, ?, ?)",
        cell_rows,
    )
    if reasoning_rows:
        connection.executemany(
            "INSERT INTO reasoning_cells VALUES (?, ?, ?, ?)",
            reasoning_rows,
        )
    with (build_dir / MEMORY_CELL_FILE).open("wb") as cell_file:
        for entry in cell_entries:
            _write_jsonl_row(cell_file, entry.model_dump(mode="json"))
    readiness = _finalize_database_indexes(
        connection,
        require_extensions=require_extensions,
    )
    connection.execute("CHECKPOINT")
    independent_unit_count = _fetch_count(
        connection,
        "SELECT COUNT(DISTINCT independent_unit_id) FROM memberships",
    )
    (
        unsupported_reasoning_record_count,
        unsupported_reasoning_record_ids_sha256,
    ) = _unsupported_reasoning_identity(connection)
    return _FinalizedDatabaseState(
        cell_count=len(cell_entries),
        secondary_membership_count=secondary_count,
        independent_unit_count=independent_unit_count,
        unsupported_reasoning_record_count=unsupported_reasoning_record_count,
        unsupported_reasoning_record_ids_sha256=(unsupported_reasoning_record_ids_sha256),
        readiness=readiness,
    )


def _finalize_database_indexes(
    connection: duckdb.DuckDBPyConnection,
    *,
    require_extensions: bool,
) -> dict[str, bool]:
    readiness = {
        "metadata_index_ready": False,
        "fts_index_ready": False,
        "hnsw_index_ready": False,
        "provenance_graph_ready": False,
    }
    _create_reasoning_retrieval_tables(connection)
    for statement in (
        "CREATE INDEX records_available_idx ON records(available_from)",
        "CREATE INDEX records_disposition_idx ON records(routing_disposition)",
        "CREATE INDEX records_type_idx ON records(record_type)",
        "CREATE INDEX records_primary_cell_idx ON records(primary_cell_id)",
        "CREATE INDEX records_unit_type_idx ON records(independent_unit_type)",
        "CREATE INDEX reasoning_facets_value_idx ON reasoning_cell_facets(facet_kind, facet_value)",
        "CREATE INDEX reasoning_facets_cell_idx ON reasoning_cell_facets(cell_id)",
        "CREATE INDEX secondary_cell_idx ON secondary_memberships(cell_id)",
        "CREATE INDEX provenance_source_idx ON provenance_edges(source_id)",
    ):
        connection.execute(statement)
    readiness["metadata_index_ready"] = True
    readiness["provenance_graph_ready"] = True
    try:
        connection.execute("INSTALL fts")
        connection.execute("LOAD fts")
        connection.execute(
            "PRAGMA create_fts_index('reasoning_records', 'record_id', 'document', "
            "stemmer='none', stopwords='none', ignore='', overwrite=1)"
        )
        readiness["fts_index_ready"] = True
    except duckdb.Error:
        if require_extensions:
            raise
    try:
        connection.execute("INSTALL vss")
        connection.execute("LOAD vss")
        connection.execute("SET hnsw_enable_experimental_persistence = true")
        connection.execute(
            "CREATE INDEX reasoning_cells_hnsw_idx ON reasoning_cells USING HNSW (centroid) WITH (metric = 'cosine')"
        )
        connection.execute(
            "CREATE INDEX reasoning_cell_facets_hnsw_idx "
            "ON reasoning_cell_facets USING HNSW (centroid) "
            "WITH (metric = 'cosine')"
        )
        readiness["hnsw_index_ready"] = True
    except duckdb.Error:
        if require_extensions:
            raise
    return readiness


def _create_reasoning_retrieval_tables(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    connection.execute(
        "CREATE TABLE reasoning_records AS "
        "SELECT record_id, primary_cell_id, memory_lanes, regime_cluster, document "
        "FROM records WHERE routing_disposition = 'REASONING'"
    )
    connection.execute(
        """
        CREATE TABLE reasoning_cell_facets AS
        SELECT 'lane' AS facet_kind,
               lane_value AS facet_value,
               grouped.primary_cell_id AS cell_id,
               grouped.primary_member_count,
               grouped.independent_unit_count,
               cells.centroid
        FROM (
            SELECT primary_cell_id,
                   lane.lane_value,
                   COUNT(*) AS primary_member_count,
                   COUNT(DISTINCT independent_unit_id) AS independent_unit_count
            FROM records
            CROSS JOIN UNNEST(
                from_json(memory_lanes, '["VARCHAR"]')
            ) AS lane(lane_value)
            WHERE routing_disposition = 'REASONING'
            GROUP BY primary_cell_id, lane.lane_value
        ) grouped
        JOIN reasoning_cells cells ON cells.cell_id = grouped.primary_cell_id
        UNION ALL
        SELECT 'regime' AS facet_kind,
               regime_value AS facet_value,
               grouped.primary_cell_id AS cell_id,
               grouped.primary_member_count,
               grouped.independent_unit_count,
               cells.centroid
        FROM (
            SELECT primary_cell_id,
                   upper(trim(regime_cluster)) AS regime_value,
                   COUNT(*) AS primary_member_count,
                   COUNT(DISTINCT independent_unit_id) AS independent_unit_count
            FROM records
            WHERE routing_disposition = 'REASONING'
              AND trim(regime_cluster) != ''
            GROUP BY primary_cell_id, upper(trim(regime_cluster))
        ) grouped
        JOIN reasoning_cells cells ON cells.cell_id = grouped.primary_cell_id
        UNION ALL
        SELECT 'lane_regime' AS facet_kind,
               lane_value || '|' || regime_value AS facet_value,
               grouped.primary_cell_id AS cell_id,
               grouped.primary_member_count,
               grouped.independent_unit_count,
               cells.centroid
        FROM (
            SELECT primary_cell_id,
                   lane.lane_value,
                   upper(trim(regime_cluster)) AS regime_value,
                   COUNT(*) AS primary_member_count,
                   COUNT(DISTINCT independent_unit_id) AS independent_unit_count
            FROM records
            CROSS JOIN UNNEST(
                from_json(memory_lanes, '["VARCHAR"]')
            ) AS lane(lane_value)
            WHERE routing_disposition = 'REASONING'
              AND trim(regime_cluster) != ''
            GROUP BY primary_cell_id, lane.lane_value,
                     upper(trim(regime_cluster))
        ) grouped
        JOIN reasoning_cells cells ON cells.cell_id = grouped.primary_cell_id
        """
    )


def _write_jsonl_row(handle: BinaryIO, row: dict[str, Any]) -> None:
    handle.write((canonical_json(row) + "\n").encode("utf-8"))


def _routing_root_from_database(path: Path) -> str:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        return _routing_root_from_query(
            connection,
            "SELECT record_id, routing_json FROM records ORDER BY record_id",
        )
    finally:
        connection.close()


def _routing_root_from_query(
    connection: duckdb.DuckDBPyConnection,
    query: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"{")
    cursor = connection.execute(query)
    first = True
    while rows := cursor.fetchmany(1024):
        for record_id, routing_json in rows:
            if not first:
                digest.update(b",")
            first = False
            digest.update(canonical_json(str(record_id)).encode("utf-8"))
            digest.update(b":")
            digest.update(str(routing_json).encode("utf-8"))
    digest.update(b"}")
    return digest.hexdigest()


def _iter_source_records(root: Path) -> Iterator[BrainRecordEnvelope]:
    """Keep streaming in production while preserving list_records test seams."""

    store = BrainRecordStore(root)
    if BrainRecordStore.list_records is not _ORIGINAL_LIST_RECORDS:
        yield from store.list_records()
    else:
        yield from store.iter_records()


def _episode_record_file_is_empty(root: Path, episode_id: str) -> bool:
    """Return true only for a declared episode whose record ledger is 0 bytes."""

    path = root / "memory" / "records" / f"{episode_id}.jsonl"
    return path.is_file() and path.stat().st_size == 0


def _validate_replay_projection_file_scope(
    root: Path,
    *,
    projection_episode_ids: set[str],
) -> None:
    """Reject obvious replay coverage mismatches before an expensive index build."""

    record_paths = list((root / "memory" / "records").glob("*.jsonl"))
    if not record_paths:
        return
    file_episode_ids = {path.stem for path in record_paths}
    nonempty_episode_ids = {path.stem for path in record_paths if path.stat().st_size > 0}
    missing = sorted(nonempty_episode_ids - projection_episode_ids)
    unexpected = sorted(projection_episode_ids - file_episode_ids)
    if missing or unexpected:
        raise ValueError(
            "replay availability file coverage mismatch: "
            f"missing={missing[:10]} unexpected={unexpected[:10]}"
        )


def _build_database(
    path: Path,
    *,
    records: list[BrainRecordEnvelope],
    vectors: list[list[float]],
    cells: MemoryCellBuild,
    dimensions: int,
    require_extensions: bool,
) -> dict[str, bool]:
    connection = duckdb.connect(str(path))
    vector_type = _vector_type(dimensions)
    readiness = {
        "metadata_index_ready": False,
        "fts_index_ready": False,
        "hnsw_index_ready": False,
        "provenance_graph_ready": False,
    }
    try:
        connection.execute(
            f"""
            CREATE TABLE records (
                record_id VARCHAR PRIMARY KEY,
                episode_id VARCHAR NOT NULL,
                record_type VARCHAR NOT NULL,
                training_target VARCHAR,
                trade_date DATE NOT NULL,
                available_from VARCHAR NOT NULL,
                training_eligible BOOLEAN NOT NULL,
                evidence_phase VARCHAR NOT NULL,
                evidence_polarity VARCHAR NOT NULL,
                label_quality VARCHAR NOT NULL,
                routing_disposition VARCHAR NOT NULL,
                memory_lanes VARCHAR NOT NULL,
                document VARCHAR NOT NULL,
                primary_cell_id VARCHAR NOT NULL,
                independent_unit_type VARCHAR NOT NULL,
                path_type VARCHAR NOT NULL,
                regime_cluster VARCHAR NOT NULL,
                high_return_pct DOUBLE,
                close_return_pct DOUBLE,
                upper_limit_touched BOOLEAN,
                outcome_observed BOOLEAN NOT NULL,
                sample_weight DOUBLE NOT NULL,
                high_return_status VARCHAR NOT NULL,
                close_return_status VARCHAR NOT NULL,
                upper_limit_status VARCHAR NOT NULL,
                sample_weight_status VARCHAR NOT NULL,
                embedding {vector_type} NOT NULL
            )
            """
        )
        membership_by_record = {item.record_id: item for item in cells.memberships}
        record_rows = []
        for record, vector in zip(records, vectors, strict=True):
            routing = record_routing_metadata(record)
            membership = membership_by_record[record.record_id]
            population = project_population_record(record)
            record_rows.append(
                (
                    record.record_id,
                    record.episode_id,
                    record.record_type,
                    record.training_target,
                    record.trade_date,
                    as_kst(record.available_from).isoformat(),
                    record.training_eligible,
                    record.evidence_phase,
                    routing.evidence_polarity,
                    routing.label_quality,
                    routing.routing_disposition,
                    canonical_json(routing.memory_lanes),
                    cells.documents[record.record_id],
                    membership.primary_cell_id,
                    independent_unit_type(membership.independent_unit_id) or "unsupported",
                    population.path_type,
                    population.regime_cluster,
                    population.high_return_pct,
                    population.close_return_pct,
                    population.upper_limit_touched,
                    population.outcome_observed,
                    population.sample_weight,
                    population.high_return_status,
                    population.close_return_status,
                    population.upper_limit_status,
                    population.sample_weight_status,
                    vector,
                )
            )
        if record_rows:
            connection.executemany(
                "INSERT INTO records VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?)",
                record_rows,
            )
        connection.execute(
            f"""
            CREATE TABLE cells (
                cell_id VARCHAR PRIMARY KEY,
                signature VARCHAR NOT NULL,
                primary_member_count INTEGER NOT NULL,
                reasoning_member_count INTEGER NOT NULL,
                secondary_member_count INTEGER NOT NULL,
                independent_unit_count INTEGER NOT NULL,
                centroid {vector_type} NOT NULL
            )
            """
        )
        cell_rows = [
            (
                cell.cell_id,
                cell.signature,
                cell.primary_member_count,
                cell.reasoning_member_count,
                cell.secondary_member_count,
                cell.independent_unit_count,
                cells.centroids[cell.cell_id],
            )
            for cell in cells.cells
        ]
        if cell_rows:
            connection.executemany(
                "INSERT INTO cells VALUES (?, ?, ?, ?, ?, ?, ?)",
                cell_rows,
            )
        connection.execute(
            f"""
            CREATE TABLE reasoning_cells (
                cell_id VARCHAR PRIMARY KEY,
                primary_member_count INTEGER NOT NULL,
                independent_unit_count INTEGER NOT NULL,
                centroid {vector_type} NOT NULL
            )
            """
        )
        reasoning_units: dict[str, set[str]] = {}
        for item in cells.memberships:
            if item.routing_disposition == "REASONING":
                reasoning_units.setdefault(item.primary_cell_id, set()).add(item.independent_unit_id)
        reasoning_cell_rows = [
            (
                cell.cell_id,
                cell.reasoning_member_count,
                len(reasoning_units[cell.cell_id]),
                cells.reasoning_centroids[cell.cell_id],
            )
            for cell in cells.cells
            if cell.reasoning_member_count > 0
        ]
        if reasoning_cell_rows:
            connection.executemany(
                "INSERT INTO reasoning_cells VALUES (?, ?, ?, ?)",
                reasoning_cell_rows,
            )
        connection.execute(
            """
            CREATE TABLE memberships (
                record_id VARCHAR PRIMARY KEY,
                primary_cell_id VARCHAR NOT NULL,
                independent_unit_id VARCHAR NOT NULL,
                membership_score DOUBLE NOT NULL,
                membership_rule VARCHAR NOT NULL,
                membership_rule_version VARCHAR NOT NULL
            )
            """
        )
        membership_rows = [
            (
                item.record_id,
                item.primary_cell_id,
                item.independent_unit_id,
                item.membership_score,
                item.membership_rule,
                item.membership_rule_version,
            )
            for item in cells.memberships
        ]
        if membership_rows:
            connection.executemany(
                "INSERT INTO memberships VALUES (?, ?, ?, ?, ?, ?)",
                membership_rows,
            )
        connection.execute("CREATE TABLE secondary_memberships (record_id VARCHAR NOT NULL, cell_id VARCHAR NOT NULL)")
        secondary_rows = [
            (item.record_id, cell_id) for item in cells.memberships for cell_id in item.secondary_cell_ids
        ]
        if secondary_rows:
            connection.executemany(
                "INSERT INTO secondary_memberships VALUES (?, ?)",
                secondary_rows,
            )
        connection.execute("CREATE TABLE provenance_edges (record_id VARCHAR NOT NULL, source_id VARCHAR NOT NULL)")
        provenance_rows = [
            (record.record_id, source_id)
            for record in records
            for source_id in sorted(set(record.provenance_source_ids))
        ]
        if provenance_rows:
            connection.executemany(
                "INSERT INTO provenance_edges VALUES (?, ?)",
                provenance_rows,
            )
        _create_reasoning_retrieval_tables(connection)
        for statement in (
            "CREATE INDEX records_available_idx ON records(available_from)",
            "CREATE INDEX records_disposition_idx ON records(routing_disposition)",
            "CREATE INDEX records_type_idx ON records(record_type)",
            "CREATE INDEX records_primary_cell_idx ON records(primary_cell_id)",
            "CREATE INDEX records_unit_type_idx ON records(independent_unit_type)",
            "CREATE INDEX reasoning_facets_value_idx ON reasoning_cell_facets(facet_kind, facet_value)",
            "CREATE INDEX reasoning_facets_cell_idx ON reasoning_cell_facets(cell_id)",
            "CREATE INDEX secondary_cell_idx ON secondary_memberships(cell_id)",
            "CREATE INDEX provenance_source_idx ON provenance_edges(source_id)",
        ):
            connection.execute(statement)
        readiness["metadata_index_ready"] = True
        readiness["provenance_graph_ready"] = True

        try:
            connection.execute("INSTALL fts")
            connection.execute("LOAD fts")
            connection.execute(
                "PRAGMA create_fts_index('reasoning_records', 'record_id', 'document', "
                "stemmer='none', stopwords='none', ignore='', overwrite=1)"
            )
            readiness["fts_index_ready"] = True
        except duckdb.Error:
            if require_extensions:
                raise

        if records:
            try:
                connection.execute("INSTALL vss")
                connection.execute("LOAD vss")
                connection.execute("SET hnsw_enable_experimental_persistence = true")
                connection.execute(
                    "CREATE INDEX reasoning_cells_hnsw_idx ON reasoning_cells USING HNSW (centroid) "
                    "WITH (metric = 'cosine')"
                )
                connection.execute(
                    "CREATE INDEX reasoning_cell_facets_hnsw_idx "
                    "ON reasoning_cell_facets USING HNSW (centroid) "
                    "WITH (metric = 'cosine')"
                )
                readiness["hnsw_index_ready"] = True
            except duckdb.Error:
                if require_extensions:
                    raise
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return readiness


def _connect_index(path: Path, *, read_only: bool) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(str(path), read_only=read_only)
    try:
        connection.execute("LOAD fts")
        connection.execute("LOAD vss")
    except duckdb.Error:
        connection.close()
        raise
    return connection


def _merge_cell_candidates(
    connection: duckdb.DuckDBPyConnection,
    *,
    ann_rows: list[tuple[Any, ...]],
    fts_rows: list[tuple[Any, ...]],
    limit: int,
    metadata_rows: list[tuple[Any, ...]] | None = None,
) -> list[MemoryCellCandidate]:
    scores: dict[str, dict[str, float | None]] = {}
    metadata: dict[str, tuple[int, int]] = {}
    if metadata_rows is not None:
        metadata.update({str(row[0]): (int(row[1]), int(row[2])) for row in metadata_rows})
    for cell_id, score, primary_count, unit_count in ann_rows:
        key = str(cell_id)
        scores.setdefault(key, {"ann": None, "fts": None})["ann"] = float(score)
        metadata[key] = (int(primary_count), int(unit_count))
    maximum_fts = max((float(row[1]) for row in fts_rows), default=0.0)
    for cell_id, score in fts_rows:
        key = str(cell_id)
        normalized = float(score) / maximum_fts if maximum_fts > 0.0 else 0.0
        scores.setdefault(key, {"ann": None, "fts": None})["fts"] = normalized
    missing_metadata = sorted(set(scores) - set(metadata))
    if missing_metadata:
        rows = connection.execute(
            """
            SELECT cell_id, primary_member_count, independent_unit_count
            FROM reasoning_cells
            WHERE cell_id IN (SELECT UNNEST(?::VARCHAR[]))
            """,
            [missing_metadata],
        ).fetchall()
        metadata.update({str(row[0]): (int(row[1]), int(row[2])) for row in rows})
    candidates = []
    for cell_id, channel_scores in scores.items():
        ann_score = channel_scores["ann"]
        fts_score = channel_scores["fts"]
        combined = 0.7 * (ann_score or 0.0) + 0.3 * (fts_score or 0.0)
        primary_count, unit_count = metadata[cell_id]
        candidates.append(
            MemoryCellCandidate(
                cell_id=cell_id,
                score=combined,
                ann_score=ann_score,
                fts_score=fts_score,
                primary_member_count=primary_count,
                independent_unit_count=unit_count,
            )
        )
    candidates.sort(key=lambda item: (-item.score, item.cell_id))
    return candidates[:limit]


def _cell_search_filter(
    *,
    included_memory_lanes: tuple[str, ...] | None,
    included_regime_clusters: tuple[str, ...] | None,
) -> tuple[str, list[object]]:
    clauses = []
    parameters: list[object] = []
    if included_memory_lanes is not None:
        lanes = sorted({value.strip() for value in included_memory_lanes if value.strip()})
        if not lanes:
            raise ValueError("cell search memory lane filter cannot be empty")
        clauses.append("list_has_any(from_json(memory_lanes, '[\"VARCHAR\"]'), ?::VARCHAR[])")
        parameters.append(lanes)
    if included_regime_clusters is not None:
        regimes = sorted({value.strip().upper() for value in included_regime_clusters if value.strip()})
        if not regimes:
            raise ValueError("cell search regime filter cannot be empty")
        clauses.append("upper(regime_cluster) IN (SELECT UNNEST(?::VARCHAR[]))")
        parameters.append(regimes)
    return (
        " AND " + " AND ".join(clauses) if clauses else "",
        parameters,
    )


def _cell_facets(
    *,
    included_memory_lanes: tuple[str, ...] | None,
    included_regime_clusters: tuple[str, ...] | None,
) -> list[tuple[str, str]]:
    lanes: list[str] = []
    regimes: list[str] = []
    if included_memory_lanes is not None:
        lanes = sorted({value.strip() for value in included_memory_lanes if value.strip()})
        if not lanes:
            raise ValueError("cell search memory lane filter cannot be empty")
    if included_regime_clusters is not None:
        regimes = sorted({value.strip().upper() for value in included_regime_clusters if value.strip()})
        if not regimes:
            raise ValueError("cell search regime filter cannot be empty")
    if lanes and regimes:
        return [("lane_regime", f"{lane}|{regime}") for lane in lanes for regime in regimes]
    if lanes:
        return [("lane", lane) for lane in lanes]
    if regimes:
        return [("regime", regime) for regime in regimes]
    if not lanes and not regimes:
        raise ValueError("cell facet filter requires at least one dimension")
    raise AssertionError("unreachable cell facet state")


def _fetch_count(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[object] | None = None,
) -> int:
    row = connection.execute(query, parameters or []).fetchone()
    if row is None or not isinstance(row[0], int):
        raise ValueError("count query returned no integer row")
    return row[0]


def _embedding_dimensions(
    vectors: list[list[float]],
    provider: LocalEmbeddingProvider,
) -> int:
    if not vectors:
        dimensions = getattr(provider, "dimensions", 0)
        if not isinstance(dimensions, int) or dimensions < 1:
            raise ValueError("empty index requires a declared embedding dimension")
        return dimensions
    dimensions = len(vectors[0])
    if dimensions < 1 or any(len(vector) != dimensions for vector in vectors):
        raise ValueError("embedding dimensions must be non-zero and consistent")
    if any(not math.isfinite(value) for vector in vectors for value in vector):
        raise ValueError("embeddings must contain only finite values")
    return dimensions


def _availability_projection_bytes(
    rows: Mapping[str, ReplayAvailabilityOverride],
) -> bytes:
    if not rows:
        raise ValueError("replay availability projection cannot be empty")
    payload = bytearray()
    for episode_id, item in sorted(rows.items()):
        if episode_id != item.episode_id:
            raise ValueError("replay availability projection key mismatch")
        payload.extend(
            (
                canonical_json(
                    {
                        "schema_version": REPLAY_AVAILABILITY_PROJECTION_VERSION,
                        "episode_id": item.episode_id,
                        "source_trade_date": item.source_trade_date.isoformat(),
                        "replay_available_from": as_kst(item.replay_available_from).isoformat(),
                        "derivation": item.derivation,
                    }
                )
                + "\n"
            ).encode("utf-8")
        )
    return bytes(payload)


def load_snapshot_replay_availability(
    root: Path,
    manifest: MemoryCellSnapshotManifest,
) -> dict[str, ReplayAvailabilityOverride] | None:
    """Load the sealed episode projection for an evaluation-only snapshot."""

    if manifest.availability_mode == "source_available_from":
        if manifest.availability_projection is not None or manifest.evaluation_only:
            raise ValueError("source availability snapshot has replay metadata")
        return None
    reference = manifest.availability_projection
    if (
        not manifest.evaluation_only
        or manifest.availability_projection_version != REPLAY_AVAILABILITY_PROJECTION_VERSION
        or reference is None
    ):
        raise ValueError("replay availability manifest metadata is incomplete")
    path = (root.resolve() / reference.artifact_path).resolve()
    snapshot_dir = (root.resolve() / MEMORY_INDEX_ROOT / MEMORY_SNAPSHOT_DIR / manifest.snapshot_id).resolve()
    try:
        path.relative_to(snapshot_dir)
    except ValueError as exc:
        raise ValueError("replay availability projection escapes the snapshot") from exc
    if not path.is_file() or file_sha256(path) != reference.sha256:
        raise ValueError("replay availability projection hash mismatch")
    result: dict[str, ReplayAvailabilityOverride] = {}
    for raw in _read_jsonl(path):
        if raw.get("schema_version") != REPLAY_AVAILABILITY_PROJECTION_VERSION:
            raise ValueError("replay availability projection schema mismatch")
        episode_id = str(raw.get("episode_id") or "")
        if not episode_id or episode_id in result:
            raise ValueError("replay availability episode IDs must be unique")
        source_trade_date = date.fromisoformat(str(raw["source_trade_date"]))
        result[episode_id] = ReplayAvailabilityOverride(
            episode_id=episode_id,
            source_trade_date=source_trade_date,
            replay_available_from=parse_datetime(str(raw["replay_available_from"])),
            derivation=str(raw.get("derivation") or ""),
        )
    if len(result) != reference.item_count:
        raise ValueError("replay availability projection count mismatch")
    return result


def _effective_record_available_from(
    record: BrainRecordEnvelope,
    replay_availability_by_episode: Mapping[str, ReplayAvailabilityOverride] | None,
) -> datetime:
    if replay_availability_by_episode is None:
        return as_kst(record.available_from)
    override = replay_availability_by_episode.get(record.episode_id)
    if override is None:
        raise ValueError(f"record episode lacks replay availability: {record.episode_id}")
    if override.source_trade_date != record.trade_date:
        raise ValueError(f"record trade date conflicts with replay availability: {record.record_id}")
    return as_kst(override.replay_available_from)


def _record_with_effective_available_from(
    record: BrainRecordEnvelope,
    replay_availability_by_episode: Mapping[str, ReplayAvailabilityOverride] | None,
) -> BrainRecordEnvelope:
    effective = _effective_record_available_from(
        record,
        replay_availability_by_episode,
    )
    if effective == as_kst(record.available_from):
        return record
    return record.model_copy(update={"available_from": effective})


def _snapshot_id(identity: dict[str, object]) -> str:
    return f"MEMIDX-{sha256_text(canonical_json(identity))[:20]}"


def _snapshot_identity_from_manifest(
    manifest: MemoryCellSnapshotManifest,
) -> dict[str, object]:
    identity: dict[str, object] = {
        "schema_version": MEMORY_INDEX_SCHEMA_VERSION,
        "corpus_manifest_sha256": manifest.corpus_manifest_sha256,
        "source_generation_sha256": manifest.source_generation_sha256,
        "cutoff_identity": manifest.cutoff_identity,
        "max_available_from": manifest.max_available_from.isoformat(),
        "next_available_from": (
            manifest.next_available_from.isoformat() if manifest.next_available_from is not None else None
        ),
        "embedding_method": manifest.embedding_model,
        "embedding_dimensions": manifest.embedding_dimensions,
        "clustering_version": manifest.clustering_version,
        "normalizer_version": manifest.normalizer_version,
        "cell_schema_version": manifest.cell_schema_version,
        "polarity_classifier_version": manifest.polarity_classifier_version,
        "population_projection_version": manifest.population_projection_version,
        "unsupported_reasoning_record_count": (manifest.unsupported_reasoning_record_count),
        "unsupported_reasoning_record_ids_sha256": (manifest.unsupported_reasoning_record_ids_sha256),
        "routing_metadata_sha256": manifest.routing_metadata_sha256,
        "future_hashes_sha256": manifest.excluded_future_record_hashes.sha256,
        "cells_sha256": manifest.cell_entries.sha256,
        "memberships_sha256": manifest.memberships.sha256,
        "embedding_hashes_sha256": manifest.embedding_hashes.sha256,
    }
    if manifest.availability_projection is not None:
        identity.update(
            {
                "availability_mode": manifest.availability_mode,
                "availability_projection_version": (manifest.availability_projection_version),
                "availability_projection_sha256": (manifest.availability_projection.sha256),
                "evaluation_only": manifest.evaluation_only,
            }
        )
    return identity


def _manifest_versions_current(manifest: MemoryCellSnapshotManifest) -> bool:
    return (
        manifest.schema_version == "nslab.memory_cell_snapshot_manifest.v3"
        and manifest.clustering_version == MEMORY_CELL_CLUSTERING_VERSION
        and manifest.normalizer_version == MEMORY_CELL_NORMALIZER_VERSION
        and manifest.cell_schema_version == MEMORY_CELL_SCHEMA_VERSION
        and manifest.polarity_classifier_version == POLARITY_CLASSIFIER_VERSION
        and manifest.population_projection_version == POPULATION_STATISTICS_VERSION
        and manifest.record_hash_kind == "canonical_full_envelope_sha256"
    )


def _source_generation_allows_manifest(
    root: Path,
    manifest: MemoryCellSnapshotManifest,
) -> bool:
    try:
        generation = read_json(root / "memory" / "record_index" / "manifest.json")
    except (OSError, ValueError):
        return False
    if not isinstance(generation, dict):
        return False
    current_root = generation.get("generation_root_sha256")
    if current_root == manifest.source_generation_sha256:
        return True
    history = generation.get("generation_history")
    if not isinstance(history, dict):
        return False
    changed_min = history.get(manifest.source_generation_sha256)
    if not isinstance(changed_min, str):
        return False
    try:
        return as_kst(parse_datetime(changed_min)) > as_kst(manifest.as_of_cutoff)
    except ValueError:
        return False


def _registry_entry_matches_manifest(
    entry: dict[str, Any],
    manifest: MemoryCellSnapshotManifest,
    root: Path,
) -> bool:
    manifest_path = (
        root.resolve() / MEMORY_INDEX_ROOT / MEMORY_SNAPSHOT_DIR / manifest.snapshot_id / MEMORY_MANIFEST_FILE
    )
    if not manifest_path.exists():
        return False
    return (
        entry.get("manifest_sha256") == sha256_text(manifest_path.read_text(encoding="utf-8"))
        and entry.get("corpus_manifest_sha256") == manifest.corpus_manifest_sha256
        and entry.get("source_generation_sha256") == manifest.source_generation_sha256
        and entry.get("record_count") == manifest.record_count
        and entry.get("next_available_from")
        == (manifest.next_available_from.isoformat() if manifest.next_available_from is not None else None)
        and entry.get("max_available_from") == manifest.max_available_from.isoformat()
        and entry.get("as_of_cutoff") == manifest.as_of_cutoff.isoformat()
        and entry.get("cutoff_identity") == manifest.cutoff_identity
        and entry.get("requested_cutoff") == manifest.as_of_cutoff.isoformat()
        and entry.get("clustering_version") == manifest.clustering_version
        and entry.get("normalizer_version") == manifest.normalizer_version
        and entry.get("cell_schema_version") == manifest.cell_schema_version
        and entry.get("polarity_classifier_version") == manifest.polarity_classifier_version
        and entry.get("population_projection_version") == manifest.population_projection_version
        and entry.get("unsupported_reasoning_record_count") == manifest.unsupported_reasoning_record_count
        and entry.get("unsupported_reasoning_record_ids_sha256") == manifest.unsupported_reasoning_record_ids_sha256
    )


def _vector_type(dimensions: int) -> str:
    if dimensions < 1:
        raise ValueError("embedding dimensions must be positive")
    return f"FLOAT[{dimensions}]"


def _float32_vector(vector: list[float]) -> list[float]:
    return [struct.unpack("!f", struct.pack("!f", value))[0] for value in vector]


def _restore_optional_file(path: Path, payload: bytes | None) -> None:
    if payload is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.restore")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _latest_available_from(records: list[BrainRecordEnvelope]) -> datetime | None:
    return max((record.available_from for record in records), default=None)


def _is_deterministic_embedding(provider: LocalEmbeddingProvider) -> bool:
    return isinstance(provider, DeterministicHashEmbeddingProvider) or provider.embedding_method.startswith(
        "deterministic_"
    )


def _is_production_embedding_provider(provider: LocalEmbeddingProvider) -> bool:
    if not isinstance(provider, AsyncEmbeddingProviderAdapter):
        return False
    if not provider.production_capability_attested:
        return False
    current: object = provider.provider
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "DeterministicMockLLMProvider":
            return False
        nested = getattr(current, "provider", None)
        if nested is None:
            break
        current = nested
    return not _is_deterministic_embedding(provider)


def _jsonl(rows: Any) -> str:
    return "".join(canonical_json(row) + "\n" for row in rows)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError("JSONL row must be an object")
        rows.append(parsed)
    return rows
