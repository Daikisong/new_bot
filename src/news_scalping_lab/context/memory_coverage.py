"""Immutable record coverage manifests for daily reasoning runs."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import ValidationError

from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    MemoryCellSnapshotManifest,
    MemoryCoverageManifest,
)
from news_scalping_lab.memory.index import active_memory_snapshot_manifest
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    parse_datetime,
    read_json,
)


@dataclass(frozen=True)
class MemoryCoverageBuildResult:
    manifest: MemoryCoverageManifest
    manifest_path: str
    manifest_sha256: str
    cache_hit: bool
    available_record_ids: list[str]
    training_eligible_available_record_ids: list[str]


@dataclass(frozen=True)
class _CoverageRecordRow:
    record_id: str
    episode_id: str
    record_type: str
    evidence_phase: str
    available_from: datetime
    training_eligible: bool
    source_sha256: str


_SNAPSHOT_COVERAGE_CACHE: dict[
    tuple[str, str, str, str],
    MemoryCoverageBuildResult,
] = {}


def build_memory_coverage_manifest(
    root: Path,
    *,
    records: Iterable[BrainRecordEnvelope | _CoverageRecordRow],
    cutoff_at: datetime,
    run_id: str,
) -> MemoryCoverageBuildResult:
    """Build compact, content-addressed coverage evidence without record payloads."""

    cache_root = root / "data" / "cache" / "memory_coverage"
    staging_root = cache_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    build_id = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
    accepted_staging = staging_root / f"{build_id}.accepted"
    available_staging = staging_root / f"{build_id}.available"
    ids_staging = staging_root / f"{build_id}.ids"
    accepted_digest = hashlib.sha256()
    available_digest = hashlib.sha256()
    ids_digest = hashlib.sha256()
    accepted_record_count = 0
    accepted_record_ids: set[str] = set()
    duplicate_record_count = 0
    available_record_ids: list[str] = []
    training_eligible_available_record_ids: list[str] = []
    try:
        with (
            accepted_staging.open("xb") as accepted_handle,
            available_staging.open("xb") as available_handle,
            ids_staging.open("xb") as ids_handle,
        ):
            for record in records:
                accepted_record_count += 1
                if record.record_id in accepted_record_ids:
                    duplicate_record_count += 1
                else:
                    accepted_record_ids.add(record.record_id)
                record_line = _record_hash_line(record)
                accepted_digest.update(record_line)
                accepted_handle.write(record_line)
                if record.available_from > cutoff_at:
                    continue
                available_digest.update(record_line)
                available_handle.write(record_line)
                id_line = _record_id_line(record.record_id)
                ids_digest.update(id_line)
                ids_handle.write(id_line)
                available_record_ids.append(record.record_id)
                if record.training_eligible:
                    training_eligible_available_record_ids.append(record.record_id)
            for handle in (accepted_handle, available_handle, ids_handle):
                handle.flush()
                os.fsync(handle.fileno())

        accepted_sha = accepted_digest.hexdigest()
        available_sha = available_digest.hexdigest()
        available_ids_sha = ids_digest.hexdigest()
        accepted_path = cache_root / "accepted" / f"{accepted_sha}.jsonl"
        available_path = cache_root / "available" / f"{available_sha}.jsonl"
        available_ids_path = cache_root / "ids" / f"{available_ids_sha}.jsonl"
        accepted_hit = _publish_staged_file(
            accepted_staging,
            accepted_path,
            expected_sha256=accepted_sha,
        )
        available_hit = _publish_staged_file(
            available_staging,
            available_path,
            expected_sha256=available_sha,
        )
        available_ids_hit = _publish_staged_file(
            ids_staging,
            available_ids_path,
            expected_sha256=available_ids_sha,
        )
    finally:
        accepted_staging.unlink(missing_ok=True)
        available_staging.unlink(missing_ok=True)
        ids_staging.unlink(missing_ok=True)

    manifest = MemoryCoverageManifest(
        run_id=run_id,
        cutoff_at=cutoff_at,
        corpus_manifest_sha256=accepted_sha,
        accepted_record_count=accepted_record_count,
        available_record_count=len(available_record_ids),
        future_record_count=accepted_record_count - len(available_record_ids),
        missing_record_count=0,
        unexpected_record_count=0,
        duplicate_record_count=duplicate_record_count,
        available_record_ids=ArtifactReference(
            artifact_path=available_ids_path.relative_to(root).as_posix(),
            sha256=available_ids_sha,
            item_count=len(available_record_ids),
        ),
        record_hash_manifest=ArtifactReference(
            artifact_path=available_path.relative_to(root).as_posix(),
            sha256=available_sha,
            item_count=len(available_record_ids),
        ),
        accepted_record_hash_manifest=ArtifactReference(
            artifact_path=accepted_path.relative_to(root).as_posix(),
            sha256=accepted_sha,
            item_count=accepted_record_count,
        ),
        coverage_complete=duplicate_record_count == 0,
    )
    manifest_bytes = (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path = cache_root / "manifests" / f"{manifest_sha256}.json"
    manifest_staging = staging_root / f"{build_id}.manifest"
    _atomic_write_bytes(
        manifest_staging,
        manifest_bytes,
        expected_sha256=manifest_sha256,
    )
    _publish_staged_file(
        manifest_staging,
        manifest_path,
        expected_sha256=manifest_sha256,
    )
    return MemoryCoverageBuildResult(
        manifest=manifest,
        manifest_path=manifest_path.relative_to(root).as_posix(),
        manifest_sha256=manifest_sha256,
        cache_hit=accepted_hit and available_hit and available_ids_hit,
        available_record_ids=available_record_ids,
        training_eligible_available_record_ids=(
            training_eligible_available_record_ids
        ),
    )


def build_memory_coverage_manifest_from_snapshot(
    root: Path,
    *,
    snapshot: MemoryCellSnapshotManifest,
    cutoff_at: datetime,
    run_id: str,
) -> MemoryCoverageBuildResult:
    """Build coverage from a sealed evaluation population without corpus scans."""

    if not snapshot.evaluation_only:
        raise ValueError("snapshot coverage fast path is evaluation-only")
    partition_identity = (
        "ALL_SNAPSHOT_RECORDS"
        if as_kst(snapshot.max_available_from) <= as_kst(cutoff_at)
        else as_kst(cutoff_at).isoformat()
    )
    cache_key = (
        str(root.resolve()),
        snapshot.snapshot_id,
        snapshot.database.sha256,
        partition_identity,
    )
    cached = _SNAPSHOT_COVERAGE_CACHE.get(cache_key)
    if cached is not None:
        manifest = cached.manifest.model_copy(
            update={"run_id": run_id, "cutoff_at": cutoff_at}
        )
        manifest_path, manifest_sha256 = _write_coverage_manifest(
            root,
            manifest,
        )
        return MemoryCoverageBuildResult(
            manifest=manifest,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            cache_hit=True,
            available_record_ids=cached.available_record_ids,
            training_eligible_available_record_ids=(
                cached.training_eligible_available_record_ids
            ),
        )
    database_path = root.resolve() / snapshot.database.artifact_path
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        cursor = connection.execute(
            """
            SELECT record_id, episode_id, record_type, evidence_phase,
                   available_from, training_eligible, source_sha256
            FROM records
            ORDER BY record_id
            """
        )

        def rows() -> Iterator[_CoverageRecordRow]:
            observed_count = 0
            while batch := cursor.fetchmany(4096):
                for row in batch:
                    observed_count += 1
                    yield _CoverageRecordRow(
                        record_id=str(row[0]),
                        episode_id=str(row[1]),
                        record_type=str(row[2]),
                        evidence_phase=str(row[3]),
                        available_from=parse_datetime(str(row[4])),
                        training_eligible=bool(row[5]),
                        source_sha256=str(row[6]),
                    )
            if observed_count != snapshot.record_count:
                raise ValueError("evaluation snapshot coverage count mismatch")

        generic_result = build_memory_coverage_manifest(
            root,
            records=rows(),
            cutoff_at=cutoff_at,
            run_id=run_id,
        )
        manifest = generic_result.manifest.model_copy(
            update={
                "corpus_manifest_sha256": snapshot.corpus_manifest_sha256
            }
        )
        manifest_path, manifest_sha256 = _write_coverage_manifest(
            root,
            manifest,
        )
        result = MemoryCoverageBuildResult(
            manifest=manifest,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            cache_hit=generic_result.cache_hit,
            available_record_ids=generic_result.available_record_ids,
            training_eligible_available_record_ids=(
                generic_result.training_eligible_available_record_ids
            ),
        )
        _SNAPSHOT_COVERAGE_CACHE[cache_key] = result
        return result
    finally:
        connection.close()


def _write_coverage_manifest(
    root: Path,
    manifest: MemoryCoverageManifest,
) -> tuple[str, str]:
    manifest_bytes = (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path = (
        root
        / "data"
        / "cache"
        / "memory_coverage"
        / "manifests"
        / f"{manifest_sha256}.json"
    )
    _atomic_write_bytes(
        manifest_path,
        manifest_bytes,
        expected_sha256=manifest_sha256,
    )
    return manifest_path.relative_to(root).as_posix(), manifest_sha256


def inspect_memory_coverage_manifest(
    root: Path,
    context_manifest: Mapping[str, Any],
    *,
    verify_current_store: bool = True,
) -> dict[str, Any]:
    """Validate a coverage manifest and its content-addressed evidence."""

    status: dict[str, Any] = {
        "configured": False,
        "manifest_hash_verified": False,
        "contract_verified": False,
        "references_verified": False,
        "record_sets_verified": False,
        "cutoff_verified": False,
        "context_verified": False,
        "current_store_verified": None,
        "errors": [],
    }
    artifact_ref = context_manifest.get("memory_coverage_manifest_artifact")
    expected_hash = context_manifest.get("memory_coverage_manifest_sha256")
    if not isinstance(artifact_ref, str) or not artifact_ref:
        status["errors"].append("memory_coverage_manifest_missing")
        status["passed"] = False
        return status
    status["configured"] = True
    artifact_path = _resolve_project_path(root, artifact_ref)
    if artifact_path is None:
        status["errors"].append("memory_coverage_manifest_path_escapes_project")
        status["passed"] = False
        return status
    if not artifact_path.exists():
        status["errors"].append("memory_coverage_manifest_file_missing")
        status["passed"] = False
        return status
    actual_hash = file_sha256(artifact_path)
    status["manifest_hash_verified"] = (
        isinstance(expected_hash, str) and actual_hash == expected_hash
    )
    if not status["manifest_hash_verified"]:
        status["errors"].append("memory_coverage_manifest_hash_mismatch")
    try:
        payload = read_json(artifact_path)
        manifest = MemoryCoverageManifest.model_validate(payload)
    except (
        OSError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        status["errors"].append("memory_coverage_manifest_contract_invalid")
        status["passed"] = False
        return status
    status["contract_verified"] = True

    active_snapshot = active_memory_snapshot_manifest(root)
    if active_snapshot is not None and active_snapshot.evaluation_only:
        database_path = root.resolve() / active_snapshot.database.artifact_path
        database_stat = database_path.stat()
        partition_identity = (
            "ALL_SNAPSHOT_RECORDS"
            if as_kst(active_snapshot.max_available_from)
            <= as_kst(manifest.cutoff_at)
            else as_kst(manifest.cutoff_at).isoformat()
        )
        cache_key = (
            str(root.resolve()),
            active_snapshot.snapshot_id,
            active_snapshot.database.sha256,
            partition_identity,
        )
        cached = _SNAPSHOT_COVERAGE_CACHE.get(cache_key)
        if cached is not None and (
            database_stat.st_size > 0
            and manifest.accepted_record_hash_manifest
            == cached.manifest.accepted_record_hash_manifest
            and manifest.record_hash_manifest
            == cached.manifest.record_hash_manifest
            and manifest.available_record_ids
            == cached.manifest.available_record_ids
            and manifest.accepted_record_count == active_snapshot.record_count
            and manifest.available_record_count
            == len(cached.available_record_ids)
            and manifest.future_record_count
            == active_snapshot.record_count - len(cached.available_record_ids)
            and manifest.corpus_manifest_sha256
            == active_snapshot.corpus_manifest_sha256
            and as_kst(active_snapshot.max_available_from)
            <= as_kst(manifest.cutoff_at)
        ):
            context_ids = context_manifest.get("available_record_ids")
            context_verified = (
                manifest.run_id == context_manifest.get("run_id")
                and manifest.cutoff_at.isoformat()
                == str(context_manifest.get("cutoff_at"))
                and manifest.accepted_record_count
                == context_manifest.get("accepted_record_count")
                and manifest.available_record_count
                == context_manifest.get("available_record_count")
                and (
                    context_ids is None
                    or context_ids == cached.available_record_ids
                )
            )
            status.update(
                {
                    "references_verified": True,
                    "record_sets_verified": True,
                    "cutoff_verified": True,
                    "context_verified": context_verified,
                    "current_store_verified": True,
                    "evaluation_snapshot_cache_verified": True,
                    "passed": context_verified,
                }
            )
            if not context_verified:
                status["errors"].append(
                    "memory_coverage_context_manifest_mismatch"
                )
            return status

    accepted_ref = manifest.accepted_record_hash_manifest
    if accepted_ref is None:
        status["errors"].append("accepted_record_hash_manifest_missing")
        status["passed"] = False
        return status
    available_ids_rows = _load_jsonl_reference(root, manifest.available_record_ids)
    available_hash_rows = _load_jsonl_reference(root, manifest.record_hash_manifest)
    accepted_hash_rows = _load_jsonl_reference(root, accepted_ref)
    if any(rows is None for rows in (available_ids_rows, available_hash_rows, accepted_hash_rows)):
        status["errors"].append("memory_coverage_referenced_artifact_invalid")
        status["passed"] = False
        return status
    assert available_ids_rows is not None
    assert available_hash_rows is not None
    assert accepted_hash_rows is not None
    status["references_verified"] = True

    available_ids = _record_ids_from_id_rows(available_ids_rows)
    available_hash_records = _validated_record_hash_rows(available_hash_rows)
    accepted_hash_records = _validated_record_hash_rows(accepted_hash_rows)
    if available_ids is None or available_hash_records is None or accepted_hash_records is None:
        status["errors"].append("memory_coverage_row_contract_invalid")
        status["passed"] = False
        return status
    expected_available_ids = [row["record_id"] for row in available_hash_records]
    available_counter = Counter(
        (row["record_id"], row["record_sha256"]) for row in available_hash_records
    )
    accepted_counter = Counter(
        (row["record_id"], row["record_sha256"]) for row in accepted_hash_records
    )
    observed_duplicate_count = sum(
        count - 1
        for count in Counter(
            row["record_id"] for row in accepted_hash_records
        ).values()
        if count > 1
    )
    corpus_identity_verified = (
        manifest.corpus_manifest_sha256
        == active_snapshot.corpus_manifest_sha256
        if active_snapshot is not None and active_snapshot.evaluation_only
        else file_sha256(_resolve_required_reference(root, accepted_ref))
        == manifest.corpus_manifest_sha256
    )
    status["record_sets_verified"] = (
        available_ids == expected_available_ids
        and not (available_counter - accepted_counter)
        and len(available_ids) == manifest.available_record_count
        and len(accepted_hash_records) == manifest.accepted_record_count
        and observed_duplicate_count == manifest.duplicate_record_count
        and corpus_identity_verified
        and manifest.coverage_complete
    )
    if not status["record_sets_verified"]:
        status["errors"].append("memory_coverage_record_sets_mismatch")

    available_cutoffs = [parse_datetime(row["available_from"]) for row in available_hash_records]
    accepted_cutoffs = [parse_datetime(row["available_from"]) for row in accepted_hash_records]
    observed_available_count = sum(value <= manifest.cutoff_at for value in accepted_cutoffs)
    observed_future_count = len(accepted_cutoffs) - observed_available_count
    status["cutoff_verified"] = (
        all(value <= manifest.cutoff_at for value in available_cutoffs)
        and observed_available_count == manifest.available_record_count
        and observed_future_count == manifest.future_record_count
    )
    if not status["cutoff_verified"]:
        status["errors"].append("memory_coverage_cutoff_mismatch")

    context_available_ids = context_manifest.get("available_record_ids")
    context_id_match = (
        context_available_ids is None
        or (
            isinstance(context_available_ids, list)
            and all(isinstance(value, str) for value in context_available_ids)
            and context_available_ids == available_ids
        )
    )
    status["context_verified"] = (
        manifest.run_id == context_manifest.get("run_id")
        and manifest.cutoff_at.isoformat() == str(context_manifest.get("cutoff_at"))
        and manifest.accepted_record_count == context_manifest.get("accepted_record_count")
        and manifest.available_record_count == context_manifest.get("available_record_count")
        and context_id_match
    )
    if not status["context_verified"]:
        status["errors"].append("memory_coverage_context_manifest_mismatch")

    if verify_current_store:
        active_snapshot = active_memory_snapshot_manifest(root)
        if active_snapshot is not None and active_snapshot.evaluation_only:
            current_counter = _snapshot_source_hash_counter(
                root,
                active_snapshot,
            )
        else:
            current_counter = Counter(
                (record.record_id, _record_sha256(record))
                for record in BrainRecordStore(root).list_records()
            )
        status["current_store_verified"] = accepted_counter == current_counter
        if not status["current_store_verified"]:
            status["errors"].append("memory_coverage_current_store_mismatch")

    status["passed"] = all(
        status[field]
        for field in (
            "manifest_hash_verified",
            "contract_verified",
            "references_verified",
            "record_sets_verified",
            "cutoff_verified",
            "context_verified",
        )
    ) and status["current_store_verified"] is not False
    return status


def _record_hash_lines(
    records: Iterable[BrainRecordEnvelope | _CoverageRecordRow],
) -> Iterator[bytes]:
    for record in records:
        yield _record_hash_line(record)


def _record_hash_line(record: BrainRecordEnvelope | _CoverageRecordRow) -> bytes:
    row = {
        "available_from": record.available_from.isoformat(),
        "episode_id": record.episode_id,
        "evidence_phase": record.evidence_phase,
        "record_id": record.record_id,
        "record_sha256": (
            record.source_sha256
            if isinstance(record, _CoverageRecordRow)
            else _record_sha256(record)
        ),
        "record_type": record.record_type,
        "training_eligible": record.training_eligible,
    }
    return (canonical_json(row) + "\n").encode("utf-8")


def _record_id_lines(record_ids: Iterable[str]) -> Iterator[bytes]:
    for record_id in record_ids:
        yield _record_id_line(record_id)


def _record_id_line(record_id: str) -> bytes:
    return (canonical_json({"record_id": record_id}) + "\n").encode("utf-8")


def _record_sha256(record: BrainRecordEnvelope) -> str:
    return hashlib.sha256(
        canonical_json(record.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


def _snapshot_source_hash_counter(
    root: Path,
    snapshot: MemoryCellSnapshotManifest,
) -> Counter[tuple[str, str]]:
    path = root.resolve() / snapshot.source_record_hashes.artifact_path
    if not path.is_file() or file_sha256(path) != snapshot.source_record_hashes.sha256:
        raise ValueError("evaluation snapshot source hash ledger is invalid")
    result: Counter[tuple[str, str]] = Counter()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            result[(str(row["record_id"]), str(row["sha256"]))] += 1
    if sum(result.values()) != snapshot.source_record_hashes.item_count:
        raise ValueError("evaluation snapshot source hash count mismatch")
    return result


def _lines_sha256(lines: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line)
    return digest.hexdigest()


def _atomic_write_lines(
    path: Path,
    lines: Iterable[bytes],
    *,
    expected_sha256: str,
) -> bool:
    if path.exists() and file_sha256(path) == expected_sha256:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.parent / f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    digest = hashlib.sha256()
    try:
        with partial.open("xb") as handle:
            for line in lines:
                digest.update(line)
                handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        if digest.hexdigest() != expected_sha256:
            raise OSError("partial coverage artifact hash mismatch")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    if file_sha256(path) != expected_sha256:
        raise OSError("committed coverage artifact hash mismatch")
    return False


def _publish_staged_file(
    staged_path: Path,
    destination: Path,
    *,
    expected_sha256: str,
) -> bool:
    if file_sha256(staged_path) != expected_sha256:
        raise OSError("staged coverage artifact hash mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and file_sha256(destination) == expected_sha256:
        return True
    os.replace(staged_path, destination)
    if file_sha256(destination) != expected_sha256:
        raise OSError("committed coverage artifact hash mismatch")
    return False


def _atomic_write_bytes(path: Path, payload: bytes, *, expected_sha256: str) -> None:
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("atomic payload hash does not match expected SHA-256")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.parent / f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        with partial.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if file_sha256(partial) != expected_sha256:
            raise OSError("partial coverage artifact hash mismatch")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    if file_sha256(path) != expected_sha256:
        raise OSError("committed coverage artifact hash mismatch")


def _resolve_project_path(root: Path, relative_path: str) -> Path | None:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _resolve_required_reference(root: Path, reference: ArtifactReference) -> Path:
    path = _resolve_project_path(root, reference.artifact_path)
    if path is None:
        raise ValueError("coverage reference escapes project root")
    return path


def _load_jsonl_reference(
    root: Path,
    reference: ArtifactReference,
) -> list[dict[str, Any]] | None:
    path = _resolve_project_path(root, reference.artifact_path)
    if path is None or not path.exists() or file_sha256(path) != reference.sha256:
        return None
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                return None
            rows.append(row)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if len(rows) != reference.item_count:
        return None
    return rows


def _record_ids_from_id_rows(rows: list[dict[str, Any]]) -> list[str] | None:
    record_ids: list[str] = []
    for row in rows:
        if set(row) != {"record_id"} or not isinstance(row.get("record_id"), str):
            return None
        record_ids.append(str(row["record_id"]))
    return record_ids


def _validated_record_hash_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    required = {
        "available_from",
        "episode_id",
        "evidence_phase",
        "record_id",
        "record_sha256",
        "record_type",
        "training_eligible",
    }
    for row in rows:
        if set(row) != required:
            return None
        if not all(
            isinstance(row.get(field), str)
            for field in (
                "available_from",
                "episode_id",
                "evidence_phase",
                "record_id",
                "record_sha256",
                "record_type",
            )
        ):
            return None
        if len(str(row["record_sha256"])) != 64:
            return None
        if not isinstance(row.get("training_eligible"), bool):
            return None
        try:
            parse_datetime(str(row["available_from"]))
        except ValueError:
            return None
    return rows
