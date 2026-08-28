"""Build and query the immutable v7 offline semantic-exposure sidecar."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from news_scalping_lab.brain.compiler import (
    LLM_FULL_RECORD_SHARD_SIZE,
    _compact_payload_for_llm_prompt,
    _empty_prompt_value,
    _llm_evidence_groups,
    _record_routing_features,
)
from news_scalping_lab.memory.runtime_v4 import SemanticExposureState
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import (
    record_is_positive_support,
    record_routing_metadata,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    read_json,
    relative_to_root,
    sha256_text,
    stable_id,
    write_json,
)

SEMANTIC_EXPOSURE_INDEX_VERSION = "nslab.semantic_exposure_index.v2"
SEMANTIC_EXPOSURE_ROOT = Path("runs/semantic_brain_upgrade/exposure")
SEMANTIC_EXPOSURE_DATABASE = "semantic_exposure.sqlite3"
SEMANTIC_EXPOSURE_MANIFEST = "semantic_exposure_manifest.json"
SEMANTIC_EXPOSURE_CURRENT = "current.json"


@dataclass(frozen=True)
class SemanticExposureBuildResult:
    manifest: dict[str, Any]
    manifest_path: Path
    database_path: Path


class SemanticExposureIndex:
    """Indexed lookup; no corpus scan is permitted during daily inference."""

    def __init__(self, root: Path, manifest_path: Path) -> None:
        self.root = root.resolve()
        self.manifest_path = manifest_path.resolve()
        manifest = read_json(self.manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError("semantic exposure manifest is invalid")
        self.manifest = manifest
        database_ref = manifest.get("database")
        if not isinstance(database_ref, dict):
            raise ValueError("semantic exposure database reference is missing")
        self.database_path = self.root / str(database_ref.get("artifact_path") or "")
        if not self.database_path.exists() or file_sha256(self.database_path) != database_ref.get("sha256"):
            raise ValueError("semantic exposure database hash mismatch")
        self._connection = sqlite3.connect(
            f"file:{self.database_path.as_posix()}?mode=ro",
            uri=True,
        )

    @classmethod
    def open_current(cls, root: Path) -> SemanticExposureIndex | None:
        root = root.resolve()
        pointer_path = root / SEMANTIC_EXPOSURE_ROOT / SEMANTIC_EXPOSURE_CURRENT
        if not pointer_path.exists():
            return None
        pointer = read_json(pointer_path)
        if not isinstance(pointer, dict):
            raise ValueError("semantic exposure pointer is invalid")
        manifest_path = root / str(pointer.get("manifest_path") or "")
        if not manifest_path.exists() or file_sha256(manifest_path) != pointer.get("manifest_sha256"):
            raise ValueError("semantic exposure pointer hash mismatch")
        return cls(root, manifest_path)

    def __call__(self, record_id: str) -> SemanticExposureState:
        row = self._connection.execute(
            "SELECT payload_exposed, claim_referenced, rare_payload, "
            "evidence_group_size FROM record_exposure WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"record is absent from semantic exposure index: {record_id}")
        return SemanticExposureState(
            payload_exposed=bool(row[0]),
            claim_referenced=bool(row[1]),
            rare_payload=bool(row[2]),
            evidence_group_size=str(row[3]),
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SemanticExposureIndex:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def build_semantic_exposure_index(
    root: Path,
    *,
    brain_version: str | None = None,
    records: Iterable[BrainRecordEnvelope] | None = None,
) -> SemanticExposureBuildResult:
    root = root.resolve()
    brain_manifest_path = _brain_manifest_path(root, brain_version=brain_version)
    brain_manifest = read_json(brain_manifest_path)
    if not isinstance(brain_manifest, dict):
        raise ValueError("semantic exposure requires a valid brain manifest")
    resolved_brain_version = str(brain_manifest.get("brain_version") or "")
    if not resolved_brain_version:
        raise ValueError("semantic exposure brain version is missing")
    compile_manifest_path = brain_manifest_path.parent / "llm_compile_manifest.json"
    compile_manifest = read_json(compile_manifest_path)
    if not isinstance(compile_manifest, dict):
        raise ValueError("semantic exposure compile manifest is invalid")
    if compile_manifest.get("compiler_version") != "nslab.brain.llm_full.compiler.v7":
        raise ValueError("semantic exposure v1 requires compiler v7")
    claims_path = brain_manifest_path.parent / "compiled_claims.jsonl"
    referenced = _claim_referenced_record_ids(claims_path)
    output_dir = root / SEMANTIC_EXPOSURE_ROOT / f"{resolved_brain_version}-v2"
    final_database_path = output_dir / SEMANTIC_EXPOSURE_DATABASE
    manifest_path = output_dir / SEMANTIC_EXPOSURE_MANIFEST
    if manifest_path.exists() and final_database_path.exists():
        existing = read_json(manifest_path)
        if (
            isinstance(existing, dict)
            and existing.get("brain_manifest_sha256") == file_sha256(brain_manifest_path)
            and existing.get("compile_manifest_sha256") == file_sha256(compile_manifest_path)
            and existing.get("compiled_claims_sha256") == file_sha256(claims_path)
            and isinstance(existing.get("database"), dict)
            and existing["database"].get("sha256") == file_sha256(final_database_path)
        ):
            _write_current_pointer(root, manifest_path, existing)
            return SemanticExposureBuildResult(
                manifest=existing,
                manifest_path=manifest_path,
                database_path=final_database_path,
            )
        raise ValueError("semantic exposure immutable artifact conflict")
    output_dir.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=".semantic-exposure-",
        suffix=".sqlite3",
        dir=output_dir,
    )
    os.close(handle)
    temporary_database = Path(temporary_name)
    counts: Counter[str] = Counter()
    try:
        connection = sqlite3.connect(temporary_database)
        try:
            connection.execute(
                "CREATE TABLE record_exposure ("
                "record_id TEXT PRIMARY KEY, payload_exposed INTEGER NOT NULL, "
                "claim_referenced INTEGER NOT NULL, rare_payload INTEGER NOT NULL, "
                "evidence_group_size TEXT NOT NULL, evidence_group_id TEXT NOT NULL, "
                "shard_index INTEGER NOT NULL)"
            )
            for shard_index, shard in enumerate(
                _record_shards(
                    root,
                    LLM_FULL_RECORD_SHARD_SIZE,
                    records=records,
                ),
                start=1,
            ):
                rows = _shard_exposure_rows(
                    shard,
                    shard_index=shard_index,
                    referenced=referenced,
                )
                connection.executemany(
                    "INSERT INTO record_exposure VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                counts["record_count"] += len(rows)
                counts["payload_exposed_count"] += sum(row[1] for row in rows)
                counts["claim_referenced_count"] += sum(row[2] for row in rows)
                counts["rare_payload_count"] += sum(row[3] for row in rows)
                connection.commit()
            connection.execute(
                "CREATE INDEX record_exposure_flags_idx ON record_exposure "
                "(payload_exposed, claim_referenced, rare_payload)"
            )
            connection.execute("CREATE INDEX record_exposure_group_idx ON record_exposure (evidence_group_id)")
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary_database, final_database_path)
    finally:
        if temporary_database.exists():
            temporary_database.unlink()
    expected_record_count = int(compile_manifest.get("source_record_count") or 0)
    if counts["record_count"] != expected_record_count:
        raise ValueError("semantic exposure record count differs from compiler source")
    manifest = {
        "schema_version": SEMANTIC_EXPOSURE_INDEX_VERSION,
        "brain_version": resolved_brain_version,
        "brain_manifest_sha256": file_sha256(brain_manifest_path),
        "compile_manifest_sha256": file_sha256(compile_manifest_path),
        "compiled_claims_sha256": file_sha256(claims_path),
        "compiler_version": compile_manifest.get("compiler_version"),
        "map_reduce_version": compile_manifest.get("map_reduce_version"),
        "record_count": counts["record_count"],
        "payload_exposed_count": counts["payload_exposed_count"],
        "payload_not_exposed_count": (counts["record_count"] - counts["payload_exposed_count"]),
        "claim_referenced_count": counts["claim_referenced_count"],
        "claim_unreferenced_count": (counts["record_count"] - counts["claim_referenced_count"]),
        "rare_payload_count": counts["rare_payload_count"],
        "database": {
            "artifact_path": relative_to_root(final_database_path, root),
            "sha256": file_sha256(final_database_path),
            "item_count": counts["record_count"],
        },
        "online_full_scan_allowed": False,
        "immutable": True,
    }
    write_json(manifest_path, manifest)
    _write_current_pointer(root, manifest_path, manifest)
    return SemanticExposureBuildResult(
        manifest=manifest,
        manifest_path=manifest_path,
        database_path=final_database_path,
    )


def _record_shards(
    root: Path,
    size: int,
    *,
    records: Iterable[BrainRecordEnvelope] | None = None,
) -> Iterator[list[BrainRecordEnvelope]]:
    shard: list[BrainRecordEnvelope] = []
    source = records if records is not None else BrainRecordStore(root).iter_records()
    for record in source:
        shard.append(record)
        if len(shard) == size:
            yield shard
            shard = []
    if shard:
        yield shard


def _shard_exposure_rows(
    shard: list[BrainRecordEnvelope],
    *,
    shard_index: int,
    referenced: set[str],
) -> list[tuple[str, int, int, int, str, str, int]]:
    groups, representatives = _llm_evidence_groups(shard)
    representative_ids = {record.record_id for record in representatives}
    group_sizes = {str(group.get("group_id") or ""): int(group.get("record_count") or 0) for group in groups}
    semantics_by_group: dict[str, Counter[str]] = defaultdict(Counter)
    represented_semantics: dict[str, set[str]] = defaultdict(set)
    record_rows: list[tuple[BrainRecordEnvelope, str, str]] = []
    for record in shard:
        group_id = _evidence_group_id(record)
        semantic_digest = sha256_text(canonical_json(_compact_payload_for_llm_prompt(record.payload)))
        semantics_by_group[group_id][semantic_digest] += 1
        if record.record_id in representative_ids:
            represented_semantics[group_id].add(semantic_digest)
        record_rows.append((record, group_id, semantic_digest))
    rows = []
    for record, group_id, semantic_digest in record_rows:
        routing = record_routing_metadata(record)
        rare = (
            routing.routing_disposition == "REASONING"
            and routing.label_quality == "verified"
            and semantics_by_group[group_id][semantic_digest] <= 3
            and semantic_digest not in represented_semantics[group_id]
        )
        rows.append(
            (
                record.record_id,
                int(record.record_id in representative_ids),
                int(record.record_id in referenced),
                int(rare),
                "large" if group_sizes.get(group_id, 0) >= 1000 else "small",
                group_id,
                shard_index,
            )
        )
    return rows


def _evidence_group_id(record: BrainRecordEnvelope) -> str:
    routing = record_routing_metadata(record)
    signature: dict[str, Any] = {
        "record_type": record.record_type,
        "training_target": record.training_target,
        "training_eligible": record.training_eligible,
        "eligibility_reason": record.eligibility_reason,
        "evidence_polarity": routing.evidence_polarity,
        "label_quality": routing.label_quality,
        "routing_disposition": routing.routing_disposition,
        "polarity_classifier_version": routing.polarity_classifier_version,
        "threshold_source": routing.threshold_source,
        "threshold_role": routing.threshold_role,
        "memory_lanes": routing.memory_lanes,
        "positive_support_eligible": record_is_positive_support(record),
        "evidence_phase": record.evidence_phase,
        "status": record.status,
        "confidence_label": record.confidence_label,
    }
    routing_features = _record_routing_features(record)
    if routing_features:
        signature["routing_features"] = routing_features
    exclusion = record.payload.get("training_exclusion_reason")
    if not _empty_prompt_value(exclusion):
        signature["training_exclusion_reason"] = exclusion
    return stable_id("LLMEVID", canonical_json(signature), length=20)


def _claim_referenced_record_ids(path: Path) -> set[str]:
    referenced: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = read_json_line(line)
            for field in ("supporting_record_ids", "contradicting_record_ids"):
                rows = value.get(field)
                if isinstance(rows, list):
                    referenced.update(item for item in rows if isinstance(item, str))
    return referenced


def read_json_line(line: str) -> dict[str, Any]:
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("semantic exposure JSONL row is not an object")
    return value


def _brain_manifest_path(root: Path, *, brain_version: str | None) -> Path:
    if brain_version is not None:
        path = root / "brain" / "snapshots" / brain_version / "brain_manifest.json"
    else:
        path = root / "brain" / "current" / "brain_manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"brain manifest not found: {path}")
    return path


def _write_current_pointer(
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> None:
    pointer_path = root / SEMANTIC_EXPOSURE_ROOT / SEMANTIC_EXPOSURE_CURRENT
    pointer = {
        "schema_version": "nslab.semantic_exposure_pointer.v1",
        "brain_version": manifest.get("brain_version"),
        "manifest_path": relative_to_root(manifest_path, root),
        "manifest_sha256": file_sha256(manifest_path),
    }
    write_json(pointer_path, pointer)
