"""Read-only, self-verifying external audit packs for production staging brains."""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import duckdb

from news_scalping_lab.audits.external_pack_standalone import verify as verify_standalone_pack
from news_scalping_lab.audits.hardcoding import audit_hardcoding
from news_scalping_lab.brain.audit import audit_brain
from news_scalping_lab.brain.compiler import (
    BRAIN_FILES,
    CATEGORY_RECORD_TYPE_ROUTES,
    LLM_FULL_PROMPT_MAX_CHARS,
    LLM_FULL_RECORD_SHARD_SIZE,
    _brain_category,
    _brain_category_prompt,
    _brain_category_review_prompt,
    _brain_record_shard_prompt,
    _compact_payload_for_llm_prompt,
    _empty_prompt_value,
    _llm_evidence_groups,
    _record_routing_features,
    _split_routing_records,
)
from news_scalping_lab.records.models import CANDIDATE_ERROR_RECORD_TYPES, BrainRecordEnvelope
from news_scalping_lab.records.routing import record_is_positive_support, record_routing_metadata
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    parse_datetime,
    read_json,
    sha256_text,
    stable_id,
    write_json,
)

AUDIT_PROFILE_SCHEMA = "nslab.external_audit_target_profile.v1"
AUDIT_CORE_SCHEMA = "nslab.external_audit_core_manifest.v1"
AUDIT_SAMPLE_SCHEMA = "nslab.external_audit_sample_manifest.v2"
ARTIFACT_LEDGER_SCHEMA = "nslab.external_audit_artifact_ledger.v1"
RECORD_LEDGER_SCHEMA = "nslab.external_audit_record_ledger.v1"
CLAIM_LEDGER_SCHEMA = "nslab.external_audit_claim_ledger.v1"
SEMANTIC_LEDGER_SCHEMA = "nslab.external_audit_semantic_ledger.v1"
PACK_FILES_NAME = "PACK_FILES.json"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
RAW_ZSTD_BLOCK_SIZE = 128 * 1024


class ExternalAuditError(ValueError):
    """Stable failure code plus a human-readable explanation."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AuditTarget:
    repo_root: Path
    stage_root: Path
    project_root: Path
    brain_manifest_path: Path
    compile_manifest_path: Path
    import_receipt_path: Path
    inventory_manifest_path: Path
    memory_manifest_path: Path
    record_artifact_manifest_path: Path

    def repo_relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.repo_root.resolve()).as_posix()


class RawZstdWriter:
    """Write a valid Zstandard frame containing raw blocks only.

    This keeps the standalone verifier dependency-free on Python 3.12 while
    still producing a standards-compliant ``.zst`` stream. The surrounding
    ZIP provides the actual compression.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("wb")
        # Descriptor 0x00 requires a window descriptor. 0x38 declares a
        # 128-KiB window, matching the largest legal block we emit.
        self._handle.write(ZSTD_MAGIC + b"\x00\x38")
        self._buffer = bytearray()
        self._closed = False

    def write(self, payload: bytes) -> None:
        if self._closed:
            raise ValueError("raw zstd writer is closed")
        self._buffer.extend(payload)
        while len(self._buffer) > RAW_ZSTD_BLOCK_SIZE:
            block = bytes(self._buffer[:RAW_ZSTD_BLOCK_SIZE])
            del self._buffer[:RAW_ZSTD_BLOCK_SIZE]
            self._write_block(block, last=False)

    def _write_block(self, block: bytes, *, last: bool) -> None:
        header = (len(block) << 3) | int(last)
        self._handle.write(header.to_bytes(3, "little"))
        self._handle.write(block)

    def close(self) -> None:
        if self._closed:
            return
        self._write_block(bytes(self._buffer), last=True)
        self._handle.close()
        self._closed = True

    def __enter__(self) -> RawZstdWriter:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _read_raw_zstd_blocks(handle: BinaryIO) -> Iterator[bytes]:
    if handle.read(4) != ZSTD_MAGIC:
        raise ValueError("zstd magic is invalid")
    if handle.read(1) != b"\x00":
        raise ValueError("unsupported zstd frame descriptor")
    if len(handle.read(1)) != 1:
        raise ValueError("zstd window descriptor is missing")
    while True:
        header = handle.read(3)
        if len(header) != 3:
            raise ValueError("zstd block header is truncated")
        value = int.from_bytes(header, "little")
        last = bool(value & 1)
        if (value >> 1) & 0x3:
            raise ValueError("only raw zstd blocks are supported")
        size = value >> 3
        block = handle.read(size)
        if len(block) != size:
            raise ValueError("zstd raw block is truncated")
        yield block
        if last:
            if handle.read(1):
                raise ValueError("zstd stream has trailing bytes")
            return


def iter_raw_zstd_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    pending = b""
    with path.open("rb") as handle:
        for block in _read_raw_zstd_blocks(handle):
            pending += block
            lines = pending.split(b"\n")
            pending = lines.pop()
            for line in lines:
                if line:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("ledger row is not an object")
                    yield value
    if pending:
        value = json.loads(pending)
        if not isinstance(value, dict):
            raise ValueError("ledger row is not an object")
        yield value


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _merkle_root(digests: list[str]) -> str:
    if not digests:
        return _sha256_bytes(b"")
    level = list(digests)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            _sha256_bytes(bytes.fromhex(level[index]) + bytes.fromhex(level[index + 1]))
            for index in range(0, len(level), 2)
        ]
    return level[0]


def _canonical_row_digest(row: dict[str, Any]) -> str:
    return sha256_text(canonical_json(row))


def load_audit_profile(repo_root: Path, brain_version: str) -> dict[str, Any]:
    path = repo_root / "runs" / "external_audit" / "profiles" / f"{brain_version}.json"
    try:
        profile = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ExternalAuditError("AUDIT_PROFILE_NOT_FOUND", str(path)) from exc
    if not isinstance(profile, dict) or profile.get("schema_version") != AUDIT_PROFILE_SCHEMA:
        raise ExternalAuditError("AUDIT_PROFILE_INVALID", str(path))
    if profile.get("brain_version") != brain_version:
        raise ExternalAuditError("AUDIT_PROFILE_BRAIN_MISMATCH", str(path))
    return profile


def find_audit_target(
    repo_root: Path,
    brain_version: str,
    *,
    profile: dict[str, Any] | None = None,
) -> AuditTarget:
    repo_root = repo_root.resolve()
    expected = profile or load_audit_profile(repo_root, brain_version)
    candidates: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    production_root = repo_root / "production" / "staging"
    for manifest_path in sorted(production_root.glob("*/project/brain/current/brain_manifest.json")):
        try:
            manifest = read_json(manifest_path)
            compile_manifest = read_json(manifest_path.parent / "llm_compile_manifest.json")
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or not isinstance(compile_manifest, dict):
            continue
        observed_source_count = compile_manifest.get("source_record_count")
        identity = (
            manifest.get("brain_version") == brain_version
            and manifest.get("build_mode") == "llm-full"
            and manifest.get("llm_provider") == expected.get("expected_provider")
            and manifest.get("llm_model") == expected.get("expected_model")
            and manifest.get("reasoning_effort") == expected.get("expected_reasoning_effort")
            and observed_source_count == expected.get("expected_source_record_count")
            and manifest.get("production_memory_snapshot_id") == expected.get("expected_memory_snapshot_id")
        )
        if identity:
            candidates.append((manifest_path, manifest, compile_manifest))
    if not candidates:
        raise ExternalAuditError("TARGET_NOT_FOUND", brain_version)
    if len(candidates) != 1:
        paths = ", ".join(path.as_posix() for path, _manifest, _compile in candidates)
        raise ExternalAuditError("AMBIGUOUS_TARGET", paths)
    brain_path, manifest, _compile_manifest = candidates[0]
    project_root = brain_path.parents[2]
    stage_root = project_root.parent
    receipt_path = stage_root / "production_batch_import_receipt.json"
    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict):
        raise ExternalAuditError("IMPORT_RECEIPT_INVALID", receipt_path.as_posix())
    inventory_ref = receipt.get("inventory_manifest")
    inventory_rel = inventory_ref.get("artifact_path") if isinstance(inventory_ref, dict) else None
    if not isinstance(inventory_rel, str):
        raise ExternalAuditError("INVENTORY_REFERENCE_MISSING", receipt_path.as_posix())
    inventory_path = (repo_root / inventory_rel).resolve()
    try:
        inventory_path.relative_to(repo_root)
    except ValueError as exc:
        raise ExternalAuditError("INVENTORY_PATH_ESCAPE", inventory_rel) from exc
    snapshot_id = manifest.get("production_memory_snapshot_id")
    if not isinstance(snapshot_id, str):
        raise ExternalAuditError("MEMORY_SNAPSHOT_REFERENCE_MISSING", brain_version)
    memory_manifest_path = project_root / "memory" / "retrieval_index" / "snapshots" / snapshot_id / "manifest.json"
    record_artifact_path = project_root / "memory" / "record_index" / "production_record_artifacts.json"
    required = (
        brain_path,
        brain_path.parent / "llm_compile_manifest.json",
        receipt_path,
        inventory_path,
        memory_manifest_path,
        record_artifact_path,
    )
    missing = [path.as_posix() for path in required if not path.is_file()]
    if missing:
        raise ExternalAuditError("TARGET_ARTIFACT_MISSING", ", ".join(missing))
    return AuditTarget(
        repo_root=repo_root,
        stage_root=stage_root,
        project_root=project_root,
        brain_manifest_path=brain_path,
        compile_manifest_path=brain_path.parent / "llm_compile_manifest.json",
        import_receipt_path=receipt_path,
        inventory_manifest_path=inventory_path,
        memory_manifest_path=memory_manifest_path,
        record_artifact_manifest_path=record_artifact_path,
    )


def _critical_target_paths(target: AuditTarget) -> list[Path]:
    paths = [
        target.brain_manifest_path,
        target.compile_manifest_path,
        target.memory_manifest_path,
        target.record_artifact_manifest_path,
        target.project_root / "memory" / "record_index" / "manifest.json",
        target.project_root / "brain" / "HEAD",
    ]
    warehouse_manifest = target.project_root / "warehouse" / "manifest.json"
    if warehouse_manifest.is_file():
        paths.append(warehouse_manifest)
    return paths


def capture_quick_target_state(target: AuditTarget) -> dict[str, Any]:
    file_count = 0
    total_bytes = 0
    metadata_hasher = hashlib.sha256()
    for path in _iter_target_files(target.project_root):
        stat = path.stat()
        relative = path.relative_to(target.project_root).as_posix()
        file_count += 1
        total_bytes += stat.st_size
        metadata_hasher.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    critical = {target.repo_relative(path): file_sha256(path) for path in _critical_target_paths(target)}
    record_manifest = read_json(target.project_root / "memory" / "record_index" / "manifest.json")
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "metadata_identity_sha256": metadata_hasher.hexdigest(),
        "critical_hashes": critical,
        "record_store_generation": (
            record_manifest.get("generation_root_sha256") if isinstance(record_manifest, dict) else None
        ),
        "record_corpus_root": (
            record_manifest.get("full_envelope_root_sha256") if isinstance(record_manifest, dict) else None
        ),
    }


def assert_target_stable(target: AuditTarget, *, seconds: float = 5.0) -> dict[str, Any]:
    processes = _related_processes(target.project_root)
    if processes:
        raise ExternalAuditError("TARGET_STILL_MUTATING", f"related processes: {processes}")
    first = capture_quick_target_state(target)
    time.sleep(seconds)
    second = capture_quick_target_state(target)
    if first != second:
        raise ExternalAuditError("TARGET_STILL_MUTATING", "target identity changed during stability window")
    return {"stability_seconds": seconds, "first": first, "second": second, "related_processes": []}


def _related_processes(project_root: Path) -> list[dict[str, Any]]:
    needle = str(project_root.resolve()).lower()
    current_pid = os.getpid()
    rows: list[dict[str, Any]] = []
    try:
        if os.name == "nt":
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
            ]
            completed = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
            payload = json.loads(completed.stdout or "[]")
            candidates = payload if isinstance(payload, list) else [payload]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                pid = item.get("ProcessId")
                line = item.get("CommandLine")
                if pid != current_pid and isinstance(line, str) and needle in line.lower():
                    rows.append({"pid": pid, "command_sha256": sha256_text(line)})
        else:
            completed = subprocess.run(
                ["ps", "-eo", "pid=,args="],
                check=True,
                capture_output=True,
                text=True,
            )
            for line in completed.stdout.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                pid_text, _, command_line = stripped.partition(" ")
                if int(pid_text) != current_pid and needle in command_line.lower():
                    rows.append({"pid": int(pid_text), "command_sha256": sha256_text(command_line)})
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    return rows


def _iter_target_files(root: Path) -> Iterator[Path]:
    root = root.resolve()
    for base, directories, files in os.walk(root):
        directories.sort()
        files.sort()
        base_path = Path(base)
        for name in files:
            path = base_path / name
            if path.is_symlink():
                raise ExternalAuditError("SYMLINK_NOT_ALLOWED", path.relative_to(root).as_posix())
            resolved = path.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ExternalAuditError("ARTIFACT_PATH_ESCAPE", path.as_posix()) from exc
            yield resolved


def _artifact_family(relative_path: str) -> str:
    return PurePosixPath(relative_path).parts[0] if PurePosixPath(relative_path).parts else "root"


def _artifact_flags(relative_path: str) -> tuple[bool, bool, bool]:
    path = PurePosixPath(relative_path)
    lower = relative_path.lower()
    sensitive = any(
        token in lower
        for token in (".env", "credential", "oauth", "auth.json", "cookie", "session.json", "private_key")
    )
    regenerable = (
        path.parts[:2] in {("brain", "llm_cache"), ("memory", "vector_index")}
        or path.parts[:1] == ("warehouse",)
        or "checkpoints/category_brain_index" in relative_path
        or "brain/snapshots" in relative_path
    )
    source_of_truth = (
        relative_path.startswith("memory/records/")
        or relative_path.startswith("memory/record_manifests/")
        or relative_path.startswith("research/episodes/")
        or relative_path
        in {
            "memory/record_index/manifest.json",
            "memory/record_index/by_record_id.json",
            "brain/current/brain_manifest.json",
            "brain/current/compiled_claims.jsonl",
            "brain/current/llm_compile_manifest.json",
        }
    )
    return source_of_truth, regenerable, sensitive


def scan_artifact_population(target: AuditTarget, ledger_path: Path) -> dict[str, Any]:
    file_count = 0
    total_bytes = 0
    digests: list[str] = []
    content_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    family_bytes: Counter[str] = Counter()
    family_digests: dict[str, list[str]] = defaultdict(list)
    largest: list[tuple[int, str, str]] = []
    sensitive_paths: list[str] = []
    with RawZstdWriter(ledger_path) as writer:
        for path in _iter_target_files(target.project_root):
            relative = path.relative_to(target.project_root).as_posix()
            stat = path.stat()
            digest = file_sha256(path)
            source_of_truth, regenerable, sensitive = _artifact_flags(relative)
            row = {
                "artifact_family": _artifact_family(relative),
                "regenerable": regenerable,
                "relative_path": relative,
                "schema_version": ARTIFACT_LEDGER_SCHEMA,
                "sensitive": sensitive,
                "sha256": digest,
                "size_bytes": stat.st_size,
                "source_of_truth": source_of_truth,
            }
            writer.write((canonical_json(row) + "\n").encode("utf-8"))
            row_digest = _canonical_row_digest(row)
            digests.append(row_digest)
            file_count += 1
            total_bytes += stat.st_size
            content_counts[digest] += 1
            family = str(row["artifact_family"])
            family_counts[family] += 1
            family_bytes[family] += stat.st_size
            family_digests[family].append(row_digest)
            if sensitive:
                sensitive_paths.append(relative)
            item = (stat.st_size, relative, digest)
            if len(largest) < 50:
                heapq.heappush(largest, item)
            elif item > largest[0]:
                heapq.heapreplace(largest, item)
    return {
        "schema_version": "nslab.external_audit_artifact_population_summary.v1",
        "artifact_file_count": file_count,
        "artifact_total_bytes": total_bytes,
        "artifact_total_gib": total_bytes / (1024**3),
        "artifact_ledger_sha256": file_sha256(ledger_path),
        "artifact_population_merkle_root": _merkle_root(digests),
        "family_counts": dict(sorted(family_counts.items())),
        "family_bytes": dict(sorted(family_bytes.items())),
        "family_roots": {family: _merkle_root(values) for family, values in sorted(family_digests.items())},
        "duplicate_content_count": sum(count - 1 for count in content_counts.values() if count > 1),
        "largest_files": [
            {"relative_path": relative, "size_bytes": size, "sha256": digest}
            for size, relative, digest in sorted(largest, reverse=True)
        ],
        "sensitive_path_count": len(sensitive_paths),
        "sensitive_paths": sensitive_paths,
    }


def artifact_index_from_ledger(ledger_path: Path) -> dict[str, tuple[int, str]]:
    index: dict[str, tuple[int, str]] = {}
    for row in iter_raw_zstd_jsonl(ledger_path):
        relative = row.get("relative_path")
        size = row.get("size_bytes")
        digest = row.get("sha256")
        if isinstance(relative, str) and isinstance(size, int) and isinstance(digest, str):
            index[relative] = (size, digest)
    return index


RecordState = tuple[str, str, str, str, bool, str, bool]


def _iter_duckdb_rows(cursor: duckdb.DuckDBPyConnection, *, batch_size: int = 2048) -> Iterator[tuple[Any, ...]]:
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        yield from rows


def _fetchone_required(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[object] | None = None,
) -> tuple[Any, ...]:
    row = connection.execute(query, parameters or []).fetchone()
    if row is None:
        raise ExternalAuditError("AUDIT_QUERY_EMPTY", sha256_text(query))
    return row


def _memory_database_path(target: AuditTarget, memory_manifest: dict[str, Any]) -> Path:
    database = memory_manifest.get("database")
    relative = database.get("artifact_path") if isinstance(database, dict) else None
    if not isinstance(relative, str):
        raise ExternalAuditError("MEMORY_DATABASE_REFERENCE_MISSING", target.memory_manifest_path.as_posix())
    path = (target.project_root / relative).resolve()
    try:
        path.relative_to(target.project_root.resolve())
    except ValueError as exc:
        raise ExternalAuditError("MEMORY_DATABASE_PATH_ESCAPE", relative) from exc
    if not path.is_file():
        raise ExternalAuditError("MEMORY_DATABASE_MISSING", relative)
    return path


def scan_record_population(
    target: AuditTarget,
    ledger_path: Path,
) -> tuple[dict[str, Any], dict[str, RecordState]]:
    memory_manifest = read_json(target.memory_manifest_path)
    if not isinstance(memory_manifest, dict):
        raise ExternalAuditError("MEMORY_MANIFEST_INVALID", target.memory_manifest_path.as_posix())
    database_path = _memory_database_path(target, memory_manifest)
    connection = duckdb.connect(str(database_path), read_only=True)
    memory_rows: dict[str, tuple[str, str, str, str, Any]] = {}
    try:
        cursor = connection.execute(
            "SELECT record_id, evidence_polarity, label_quality, routing_disposition, "
            "source_sha256, routing_json FROM records"
        )
        for db_row in _iter_duckdb_rows(cursor):
            db_record_id = str(db_row[0])
            if db_record_id in memory_rows:
                raise ExternalAuditError("MEMORY_DUPLICATE_RECORD_ID", db_record_id)
            try:
                routing_metadata = json.loads(str(db_row[5]))
            except json.JSONDecodeError as exc:
                raise ExternalAuditError("MEMORY_ROUTING_JSON_INVALID", db_record_id) from exc
            memory_rows[db_record_id] = (
                str(db_row[1]),
                str(db_row[2]),
                str(db_row[3]),
                str(db_row[4]),
                routing_metadata,
            )
    finally:
        connection.close()

    record_count = 0
    record_ids: set[str] = set()
    duplicate_ids: list[str] = []
    envelope_hashes: dict[str, str] = {}
    routing_metadata_by_id: dict[str, Any] = {}
    record_states: dict[str, RecordState] = {}
    episode_counts: Counter[str] = Counter()
    counters: dict[str, Counter[str]] = {
        "record_type": Counter(),
        "training_target": Counter(),
        "evidence_phase": Counter(),
        "status": Counter(),
        "confidence": Counter(),
        "typed_payload_status": Counter(),
        "routing_disposition": Counter(),
        "evidence_polarity": Counter(),
        "label_quality": Counter(),
        "year_month": Counter(),
    }
    training_eligible = Counter({"true": 0, "false": 0})
    available_min: str | None = None
    available_max: str | None = None
    future_available_count = 0
    brain_manifest = read_json(target.brain_manifest_path)
    brain_cutoff_raw = brain_manifest.get("brain_record_cutoff_at") if isinstance(brain_manifest, dict) else None
    brain_cutoff = parse_datetime(brain_cutoff_raw) if isinstance(brain_cutoff_raw, str) else None
    source_hash_mismatch_count = 0
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    chunk_rows: list[dict[str, Any]] = []
    chunk_paths: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="record-ledger-sort-", dir=ledger_path.parent) as temporary:
        temporary_root = Path(temporary)

        def flush_chunk() -> None:
            if not chunk_rows:
                return
            chunk_rows.sort(key=lambda item: str(item["record_id"]))
            chunk_path = temporary_root / f"chunk-{len(chunk_paths):05d}.jsonl"
            with chunk_path.open("w", encoding="utf-8", newline="\n") as chunk_handle:
                for item in chunk_rows:
                    chunk_handle.write(canonical_json(item) + "\n")
            chunk_paths.append(chunk_path)
            chunk_rows.clear()

        for record_file in sorted((target.project_root / "memory" / "records").glob("*.jsonl")):
            with record_file.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ExternalAuditError(
                            "RECORD_JSON_INVALID",
                            f"{record_file.name}:{line_number}",
                        ) from exc
                    if not isinstance(row, dict):
                        raise ExternalAuditError(
                            "RECORD_ROW_INVALID",
                            f"{record_file.name}:{line_number}",
                        )
                    record_id = row.get("record_id")
                    episode_id = row.get("episode_id")
                    if not isinstance(record_id, str) or not isinstance(episode_id, str):
                        raise ExternalAuditError("RECORD_ID_INVALID", f"{record_file.name}:{line_number}")
                    if record_id in record_ids:
                        duplicate_ids.append(record_id)
                    record_ids.add(record_id)
                    memory_row = memory_rows.pop(record_id, None)
                    if memory_row is None:
                        raise ExternalAuditError("MEMORY_RECORD_MISSING", record_id)
                    polarity, label_quality, disposition, memory_source_hash, routing_metadata = memory_row
                    routing_metadata_by_id[record_id] = routing_metadata
                    envelope_sha256 = sha256_text(canonical_json(row))
                    if envelope_sha256 != memory_source_hash:
                        source_hash_mismatch_count += 1
                    envelope_hashes[record_id] = envelope_sha256
                    eligible = row.get("training_eligible") is True
                    available_from = str(row.get("available_from") or "")
                    positive_support = (
                        eligible
                        and polarity == "POSITIVE"
                        and label_quality == "verified"
                        and disposition == "REASONING"
                        and str(row.get("record_type")) not in CANDIDATE_ERROR_RECORD_TYPES
                        and str(row.get("record_type")) != "blind_leader_preference_pair"
                    )
                    record_states[record_id] = (
                        episode_id,
                        disposition,
                        polarity,
                        label_quality,
                        positive_support,
                        available_from,
                        eligible,
                    )
                    raw_payload = row.get("payload")
                    payload = raw_payload if isinstance(raw_payload, dict) else {}
                    compact_payload = _compact_payload_for_llm_prompt(payload)
                    ledger_row = {
                        "available_from": available_from,
                        "confidence_label": str(row.get("confidence_label") or ""),
                        "envelope_sha256": envelope_sha256,
                        "episode_id": episode_id,
                        "evidence_phase": str(row.get("evidence_phase") or ""),
                        "evidence_polarity": polarity,
                        "label_quality": label_quality,
                        "payload_semantic_sha256": sha256_text(canonical_json(compact_payload)),
                        "positive_support_eligible": positive_support,
                        "record_id": record_id,
                        "record_type": str(row.get("record_type") or ""),
                        "routing_disposition": disposition,
                        "routing_metadata_sha256": sha256_text(canonical_json(routing_metadata)),
                        "schema_version": RECORD_LEDGER_SCHEMA,
                        "status": str(row.get("status") or ""),
                        "training_eligible": eligible,
                        "training_target": str(row.get("training_target") or "UNKNOWN"),
                    }
                    chunk_rows.append(ledger_row)
                    if len(chunk_rows) >= 50_000:
                        flush_chunk()
                    record_count += 1
                    episode_counts[episode_id] += 1
                    training_eligible["true" if eligible else "false"] += 1
                    counters["record_type"][str(row.get("record_type") or "UNKNOWN")] += 1
                    counters["training_target"][str(row.get("training_target") or "UNKNOWN")] += 1
                    counters["evidence_phase"][str(row.get("evidence_phase") or "UNKNOWN")] += 1
                    counters["status"][str(row.get("status") or "UNKNOWN")] += 1
                    counters["confidence"][str(row.get("confidence_label") or "UNKNOWN")] += 1
                    counters["typed_payload_status"][str(row.get("typed_payload_status") or "UNKNOWN")] += 1
                    counters["routing_disposition"][disposition] += 1
                    counters["evidence_polarity"][polarity] += 1
                    counters["label_quality"][label_quality] += 1
                    trade_date = str(row.get("trade_date") or "")
                    counters["year_month"][trade_date[:7] or "UNKNOWN"] += 1
                    available_min = available_from if available_min is None else min(available_min, available_from)
                    available_max = available_from if available_max is None else max(available_max, available_from)
                    if brain_cutoff is not None and parse_datetime(available_from) > brain_cutoff:
                        future_available_count += 1
        flush_chunk()
        if memory_rows:
            first_extra = min(memory_rows)
            raise ExternalAuditError("MEMORY_RECORD_POPULATION_LONG", first_extra)

        def iter_chunk(path: Path) -> Iterator[dict[str, Any]]:
            with path.open("r", encoding="utf-8") as chunk_handle:
                for chunk_line in chunk_handle:
                    value = json.loads(chunk_line)
                    if not isinstance(value, dict):
                        raise ExternalAuditError("RECORD_LEDGER_CHUNK_INVALID", path.name)
                    yield value

        population_digests: list[str] = []
        sorted_ids_hasher = hashlib.sha256()
        eligible_hasher = hashlib.sha256()
        last_merged_id = ""
        merged_rows = heapq.merge(
            *(iter_chunk(path) for path in chunk_paths),
            key=lambda item: str(item["record_id"]),
        )
        with RawZstdWriter(ledger_path) as writer:
            for ledger_row in merged_rows:
                record_id = str(ledger_row["record_id"])
                if record_id <= last_merged_id:
                    raise ExternalAuditError("RECORD_LEDGER_ORDER_INVALID", record_id)
                last_merged_id = record_id
                writer.write((canonical_json(ledger_row) + "\n").encode("utf-8"))
                population_digests.append(_canonical_row_digest(ledger_row))
                sorted_ids_hasher.update((record_id + "\n").encode("utf-8"))
                if ledger_row.get("training_eligible") is True:
                    eligible_hasher.update(
                        (record_id + "\0" + str(ledger_row["envelope_sha256"]) + "\n").encode("utf-8")
                    )

    record_corpus_sha256 = sha256_text(canonical_json(envelope_hashes))
    episode_count_root = sha256_text(canonical_json(dict(sorted(episode_counts.items()))))
    summary = {
        "schema_version": "nslab.external_audit_record_population_summary.v1",
        "record_count": record_count,
        "unique_record_id_count": len(record_ids),
        "duplicate_record_id_count": len(duplicate_ids),
        "duplicate_record_ids": duplicate_ids[:100],
        "episode_count": len(episode_counts),
        "available_from_min": available_min,
        "available_from_max": available_max,
        "training_eligible": dict(training_eligible),
        "counts": {name: dict(sorted(counter.items())) for name, counter in counters.items()},
        "unknown_payload_count": counters["typed_payload_status"].get("UNKNOWN_TYPED_PAYLOAD", 0),
        "audit_only_count": counters["routing_disposition"].get("AUDIT", 0),
        "quarantined_record_count": counters["routing_disposition"].get("QUARANTINED", 0),
        "future_available_from_count": future_available_count,
        "memory_source_hash_mismatch_count": source_hash_mismatch_count,
        "sorted_record_ids_root": sorted_ids_hasher.hexdigest(),
        "record_id_envelope_root": record_corpus_sha256,
        "routing_metadata_root": sha256_text(canonical_json(routing_metadata_by_id)),
        "episode_record_count_root": episode_count_root,
        "training_eligible_record_root": eligible_hasher.hexdigest(),
        "record_population_merkle_root": _merkle_root(population_digests),
        "record_ledger_sha256": file_sha256(ledger_path),
    }
    return summary, record_states


def scan_compiled_claims(
    target: AuditTarget,
    ledger_path: Path,
    record_states: dict[str, RecordState],
) -> tuple[dict[str, Any], set[str]]:
    claims_path = target.project_root / "brain" / "current" / "compiled_claims.jsonl"
    claim_ids: set[str] = set()
    referenced_records: set[str] = set()
    population_digests: list[str] = []
    counters: dict[str, Counter[str]] = {
        "category": Counter(),
        "status": Counter(),
        "origin": Counter(),
        "confidence": Counter(),
    }
    findings: Counter[str] = Counter()
    claim_count = 0
    known_episodes = {state[0] for state in record_states.values()}
    with RawZstdWriter(ledger_path) as writer, claims_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExternalAuditError("CLAIM_JSON_INVALID", f"line {line_number}") from exc
            if not isinstance(raw, dict):
                raise ExternalAuditError("CLAIM_ROW_INVALID", f"line {line_number}")
            claim_id = raw.get("claim_id")
            if not isinstance(claim_id, str) or not claim_id:
                raise ExternalAuditError("CLAIM_ID_INVALID", f"line {line_number}")
            if claim_id in claim_ids:
                findings["duplicate_claim_id"] += 1
            claim_ids.add(claim_id)
            supporting = _string_list(raw.get("supporting_record_ids"))
            contradicting = _string_list(raw.get("contradicting_record_ids"))
            supporting_episodes = _string_list(raw.get("supporting_episode_ids"))
            contradicting_episodes = _string_list(raw.get("contradicting_episode_ids"))
            all_refs = set(supporting) | set(contradicting)
            referenced_records.update(all_refs)
            orphan_refs = [record_id for record_id in all_refs if record_id not in record_states]
            findings["orphan_record_reference"] += len(orphan_refs)
            findings["orphan_episode_reference"] += sum(
                episode_id not in known_episodes
                for episode_id in set(supporting_episodes) | set(contradicting_episodes)
            )
            overlap = set(supporting) & set(contradicting)
            findings["support_contradict_overlap"] += len(overlap)
            positive_count = _safe_int(raw.get("positive_case_count"))
            if positive_count > 0:
                for record_id in supporting:
                    state = record_states.get(record_id)
                    if state is None:
                        continue
                    if state[1] in {"AUDIT", "QUARANTINED"}:
                        findings["audit_or_quarantined_positive_support"] += 1
                    if not state[4]:
                        findings["ineligible_positive_support"] += 1
            if raw.get("status") == "validated" and len(set(supporting_episodes)) <= 1:
                findings["validated_single_episode"] += 1
            if not str(raw.get("statement") or "").strip():
                findings["missing_statement"] += 1
            if not str(raw.get("mechanism") or "").strip():
                findings["missing_mechanism"] += 1
            for field in ("conditions", "boundary_conditions", "failure_modes"):
                if not _string_list(raw.get(field)):
                    findings[f"missing_{field}"] += 1
            support_available = [record_states[record_id][5] for record_id in supporting if record_id in record_states]
            claim_available = str(raw.get("available_from") or "")
            if support_available and parse_datetime(claim_available) < max(
                parse_datetime(value) for value in support_available
            ):
                findings["claim_available_from_before_support"] += 1
            provenance = raw.get("provenance")
            source_type = provenance.get("source_type") if isinstance(provenance, dict) else None
            if source_type == "brain_record":
                origin = "DETERMINISTIC_RECORD_CLAIM"
            elif source_type == "llm_category_synthesis":
                origin = "LLM_CATEGORY_SYNTHESIS"
            elif source_type == "llm_review_adjusted":
                origin = "LLM_REVIEW_ADJUSTED"
            elif isinstance(source_type, str) and source_type:
                origin = "OTHER"
            else:
                origin = "UNKNOWN"
            claim_sha256 = sha256_text(canonical_json(raw))
            ledger_row = {
                "available_from": claim_available,
                "category": str(raw.get("category") or "UNKNOWN"),
                "claim_id": claim_id,
                "claim_sha256": claim_sha256,
                "contradict_count": len(contradicting),
                "episode_count": len(set(supporting_episodes) | set(contradicting_episodes)),
                "origin": origin,
                "schema_version": CLAIM_LEDGER_SCHEMA,
                "status": str(raw.get("status") or "UNKNOWN"),
                "support_count": len(supporting),
            }
            writer.write((canonical_json(ledger_row) + "\n").encode("utf-8"))
            population_digests.append(_canonical_row_digest(ledger_row))
            claim_count += 1
            counters["category"][str(raw.get("category") or "UNKNOWN")] += 1
            counters["status"][str(raw.get("status") or "UNKNOWN")] += 1
            counters["origin"][origin] += 1
            counters["confidence"][str(raw.get("confidence_label") or "UNKNOWN")] += 1
    summary = {
        "schema_version": "nslab.external_audit_claim_population_summary.v1",
        "claim_count": claim_count,
        "unique_claim_id_count": len(claim_ids),
        "claim_population_merkle_root": _merkle_root(population_digests),
        "claim_ledger_sha256": file_sha256(ledger_path),
        "claim_referenced_record_count": len(referenced_records),
        "claim_unreferenced_record_count": len(record_states) - len(referenced_records),
        "counts": {name: dict(sorted(counter.items())) for name, counter in counters.items()},
        "finding_counts": dict(sorted(findings.items())),
        "hard_finding_count": sum(
            count
            for name, count in findings.items()
            if name
            not in {
                "validated_single_episode",
            }
        ),
    }
    return summary, referenced_records


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _safe_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _read_record_models(target: AuditTarget) -> Iterator[BrainRecordEnvelope]:
    for record_file in sorted((target.project_root / "memory" / "records").glob("*.jsonl")):
        with record_file.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    yield BrainRecordEnvelope.model_validate_json(line)
                except ValueError as exc:
                    raise ExternalAuditError(
                        "RECORD_MODEL_INVALID",
                        f"{record_file.name}:{line_number}",
                    ) from exc


def _iter_record_model_shards(
    target: AuditTarget,
    *,
    shard_size: int,
) -> Iterator[list[BrainRecordEnvelope]]:
    shard: list[BrainRecordEnvelope] = []
    for record in _read_record_models(target):
        shard.append(record)
        if len(shard) == shard_size:
            yield shard
            shard = []
    if shard:
        yield shard


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


def _semantic_dimension_digest(payload: dict[str, Any]) -> str:
    selected: dict[str, Any] = {}

    def walk(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, item in sorted(value.items()):
                path = f"{prefix}.{key}" if prefix else str(key)
                if any(token in str(key).lower() for token in ("mechanism", "value", "modality")):
                    selected[path] = item
                walk(item, path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{prefix}[{index}]")

    walk(payload)
    return sha256_text(canonical_json(selected))


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[index]


def semantic_coverage_outcome(
    *,
    total_records: int,
    payload_exposed_records: int,
    claim_referenced_records: int,
) -> dict[str, str]:
    return {
        "structural_coverage_result": (
            "STRUCTURAL_COVERAGE_COMPLETE" if total_records > 0 else "STRUCTURAL_COVERAGE_INCOMPLETE"
        ),
        "semantic_exposure_result": (
            "SEMANTIC_EXPOSURE_COMPLETE" if payload_exposed_records == total_records else "SEMANTIC_EXPOSURE_PARTIAL"
        ),
        "claim_influence_result": (
            "FINAL_CLAIM_INFLUENCE_COMPLETE"
            if claim_referenced_records == total_records
            else "FINAL_CLAIM_INFLUENCE_PARTIAL"
        ),
    }


def _load_llm_cache(target: AuditTarget) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    for path in sorted((target.project_root / "brain" / "llm_cache").glob("*.json")):
        value = read_json(path)
        if not isinstance(value, dict):
            raise ExternalAuditError("LLM_CACHE_INVALID", path.name)
        key = value.get("cache_key")
        if not isinstance(key, str) or key in cache:
            raise ExternalAuditError("LLM_CACHE_KEY_INVALID", path.name)
        cache[key] = value
    return cache


def audit_llm_call_ledger(
    target: AuditTarget,
    compile_manifest: dict[str, Any],
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expected_calls: dict[tuple[str, str], str] = {}
    for row in compile_manifest.get("record_shards", []):
        if not isinstance(row, dict):
            continue
        index = row.get("shard_index")
        prompt_sha = row.get("prompt_sha256")
        cache_key = row.get("cache_key")
        if isinstance(index, int) and isinstance(prompt_sha, str) and isinstance(cache_key, str):
            expected_calls[(f"brain_compile:shard:{index:04d}", prompt_sha)] = cache_key
    for row in compile_manifest.get("categories", []):
        if not isinstance(row, dict):
            continue
        category = row.get("category")
        if not isinstance(category, str):
            continue
        for kind in ("synthesis", "review"):
            prompt_sha = row.get(f"{kind}_prompt_sha256")
            cache_key = row.get(f"{kind}_cache_key")
            if isinstance(prompt_sha, str) and isinstance(cache_key, str):
                expected_calls[(f"brain_compile:{kind}:{category}", prompt_sha)] = cache_key
    cache_findings: list[str] = []
    for (purpose, prompt_sha), cache_key in expected_calls.items():
        row = cache.get(cache_key)
        if row is None:
            cache_findings.append(f"cache_missing:{cache_key}")
            continue
        expected_identity = {
            "purpose": purpose,
            "prompt_sha256": prompt_sha,
            "model": compile_manifest.get("model"),
            "reasoning_effort": compile_manifest.get("reasoning_effort"),
            "compiler_version": compile_manifest.get("compiler_version"),
            "map_reduce_version": compile_manifest.get("map_reduce_version"),
        }
        for key, expected in expected_identity.items():
            if row.get(key) != expected:
                cache_findings.append(f"cache_identity_mismatch:{cache_key}:{key}")

    matched: dict[tuple[str, str], dict[str, Any]] = {}
    retry_count = 0
    failure_count = 0
    duplicate_trace_count = 0
    tool_call_count = 0
    web_tool_call_count = 0
    for path in sorted((target.project_root / "runs" / "traces").glob("*.json")):
        value = read_json(path)
        if not isinstance(value, dict):
            continue
        model_config = value.get("model_config")
        if not isinstance(model_config, dict):
            continue
        if (
            model_config.get("model") != compile_manifest.get("model")
            or model_config.get("reasoning_effort") != compile_manifest.get("reasoning_effort")
            or value.get("compiler_version") != compile_manifest.get("compiler_version")
        ):
            continue
        input_value = value.get("input")
        prompt_sha = input_value.get("prompt_sha256") if isinstance(input_value, dict) else None
        raw_purpose = value.get("purpose")
        if not isinstance(raw_purpose, str) or not isinstance(prompt_sha, str):
            continue
        call_key = (raw_purpose, prompt_sha)
        if call_key not in expected_calls:
            continue
        if call_key in matched:
            duplicate_trace_count += 1
        matched[call_key] = value
        retry_count += _safe_int(value.get("retries"))
        failure_count += int(value.get("status") != "ok")
        tool_calls = value.get("tool_calls")
        if isinstance(tool_calls, list):
            tool_call_count += len(tool_calls)
            web_tool_call_count += sum(
                isinstance(call, dict) and "web" in str(call.get("name") or call.get("tool") or "").lower()
                for call in tool_calls
            )
    missing_traces = sorted(f"{purpose}:{digest}" for purpose, digest in set(expected_calls) - set(matched))
    map_calls = sum(purpose.startswith("brain_compile:shard:") for purpose, _digest in matched)
    synthesis_calls = sum(purpose.startswith("brain_compile:synthesis:") for purpose, _digest in matched)
    review_calls = sum(purpose.startswith("brain_compile:review:") for purpose, _digest in matched)
    return {
        "schema_version": "nslab.external_audit_llm_call_ledger.v1",
        "expected_generation_count": len(expected_calls),
        "map_calls": map_calls,
        "category_synthesis_calls": synthesis_calls,
        "category_review_calls": review_calls,
        "other_calls": len(matched) - map_calls - synthesis_calls - review_calls,
        "total_live_calls": len(matched),
        "cache_hit_count": 0 if len(matched) == len(expected_calls) else "NOT_IN_ARTIFACT",
        "retry_count": retry_count,
        "failure_count": failure_count,
        "tool_call_count": tool_call_count,
        "web_tool_call_count": web_tool_call_count,
        "duplicate_trace_count": duplicate_trace_count,
        "missing_trace_count": len(missing_traces),
        "missing_traces": missing_traces,
        "cache_finding_count": len(cache_findings),
        "cache_findings": cache_findings,
    }


def scan_semantic_exposure(
    target: AuditTarget,
    ledger_path: Path,
    referenced_records: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    compile_manifest = read_json(target.compile_manifest_path)
    if not isinstance(compile_manifest, dict):
        raise ExternalAuditError("COMPILE_MANIFEST_INVALID", target.compile_manifest_path.as_posix())
    shard_manifest = {
        _safe_int(row.get("shard_index")): row
        for row in compile_manifest.get("record_shards", [])
        if isinstance(row, dict)
    }
    cache = _load_llm_cache(target)
    shard_summaries: list[dict[str, Any]] = []
    exposed_record_ids: set[str] = set()
    rare_unexposed_ids: set[str] = set()
    shard_rows: list[dict[str, Any]] = []
    prompt_sizes: list[int] = []
    category_samples: dict[str, dict[str, list[BrainRecordEnvelope]]] = {
        _brain_category(file_name): {"reasoning": [], "context": []} for file_name in BRAIN_FILES
    }
    total_records = 0
    same_signature_multi_semantic_group_count = 0
    total_payload_semantic_digest_count = 0
    total_dimension_digest_count = 0
    max_evidence_group_record_count = 0
    high_cardinality_group_count = 0

    with RawZstdWriter(ledger_path) as writer:
        for shard_index, shard in enumerate(
            _iter_record_model_shards(target, shard_size=LLM_FULL_RECORD_SHARD_SIZE),
            start=1,
        ):
            total_records += len(shard)
            groups, representatives = _llm_evidence_groups(shard)
            representative_ids = {record.record_id for record in representatives}
            exposed_record_ids.update(representative_ids)
            group_counts = {str(row["group_id"]): _safe_int(row.get("record_count")) for row in groups}
            represented_groups = {
                str(row["group_id"]) for row in groups if _string_list(row.get("representative_record_ids"))
            }
            group_semantics: dict[str, Counter[str]] = defaultdict(Counter)
            group_dimensions: dict[str, set[str]] = defaultdict(set)
            represented_semantics: dict[str, set[str]] = defaultdict(set)
            group_reasoning_verified: dict[str, bool] = defaultdict(bool)
            record_semantics: list[tuple[str, str, str]] = []
            for record in shard:
                group_id = _evidence_group_id(record)
                compact_payload = _compact_payload_for_llm_prompt(record.payload)
                semantic_digest = sha256_text(canonical_json(compact_payload))
                group_semantics[group_id][semantic_digest] += 1
                group_dimensions[group_id].add(_semantic_dimension_digest(compact_payload))
                routing = record_routing_metadata(record)
                if routing.routing_disposition == "REASONING" and routing.label_quality == "verified":
                    group_reasoning_verified[group_id] = True
                    record_semantics.append((record.record_id, group_id, semantic_digest))
                if record.record_id in representative_ids:
                    represented_semantics[group_id].add(semantic_digest)
                selected_categories = {
                    "world_model",
                    *(
                        category
                        for category, allowed_types in CATEGORY_RECORD_TYPE_ROUTES.items()
                        if record.record_type in allowed_types
                    ),
                }
                for selected_category in selected_categories:
                    if selected_category not in category_samples:
                        continue
                    lane = "reasoning" if routing.routing_disposition == "REASONING" else "context"
                    if len(category_samples[selected_category][lane]) < 200:
                        category_samples[selected_category][lane].append(record)
            for record_id, group_id, digest in record_semantics:
                if group_semantics[group_id][digest] <= 3 and digest not in represented_semantics[group_id]:
                    rare_unexposed_ids.add(record_id)
            shard_semantic_digest_count = sum(len(values) for values in group_semantics.values())
            shard_dimension_digest_count = sum(len(values) for values in group_dimensions.values())
            shard_multi_semantic_groups = sum(len(values) > 1 for values in group_semantics.values())
            shard_rare_digest_count = sum(
                count <= 3 and digest not in represented_semantics[group_id]
                for group_id, digests in group_semantics.items()
                for digest, count in digests.items()
                if group_reasoning_verified[group_id]
            )
            total_payload_semantic_digest_count += shard_semantic_digest_count
            total_dimension_digest_count += shard_dimension_digest_count
            same_signature_multi_semantic_group_count += shard_multi_semantic_groups
            max_evidence_group_record_count = max(
                max_evidence_group_record_count,
                max(group_counts.values(), default=0),
            )
            high_cardinality_group_count += sum(value >= 1000 for value in group_counts.values())
            largest_groups = [
                {
                    "group_id": group_id,
                    "record_count": group_counts[group_id],
                    "payload_semantic_digest_count": len(group_semantics[group_id]),
                    "mechanism_value_modality_digest_count": len(group_dimensions[group_id]),
                    "representative_semantic_digest_count": len(represented_semantics[group_id]),
                    "reasoning_verified_present": group_reasoning_verified[group_id],
                }
                for group_id in sorted(group_counts, key=lambda item: (-group_counts[item], item))[:5]
            ]
            prompt = _brain_record_shard_prompt(
                shard_index=shard_index,
                records=shard,
                brain_version=str(compile_manifest.get("brain_version")),
                provider_name=str(compile_manifest.get("provider")),
                model=str(compile_manifest.get("model")),
                reasoning_effort=str(compile_manifest.get("reasoning_effort")),
            )
            prompt_payload = json.loads(prompt)
            prompt_chars = len(prompt)
            prompt_sizes.append(prompt_chars)
            declared = shard_manifest.get(shard_index, {})
            cache_key = declared.get("cache_key") if isinstance(declared, dict) else None
            cache_row = cache.get(str(cache_key), {})
            summary = str(cache_row.get("output") or "")
            source_reasoning, source_context = _split_routing_records(shard)
            represented_record_mass = sum(group_counts[group_id] for group_id in represented_groups)
            row = {
                "audit_context_record_count": len(source_context),
                "cache_hit": False,
                "call_success": bool(summary),
                "evidence_group_count": len(groups),
                "evidence_group_metadata_chars": len(canonical_json(prompt_payload.get("evidence_groups", []))),
                "payload_exposed_record_count": len(representatives),
                "payload_not_exposed_record_count": len(shard) - len(representatives),
                "prompt_chars": prompt_chars,
                "prompt_limit_exceeded": prompt_chars > LLM_FULL_PROMPT_MAX_CHARS,
                "prompt_sha256_matches": declared.get("prompt_sha256") == sha256_text(prompt),
                "reasoning_record_count": len(source_reasoning),
                "records_in_represented_groups": represented_record_mass,
                "records_in_unrepresented_groups": len(shard) - represented_record_mass,
                "representative_group_coverage_ratio": (len(represented_groups) / len(groups) if groups else 1.0),
                "representative_payload_chars": len(
                    canonical_json(
                        [
                            *prompt_payload.get("reasoning_records", []),
                            *prompt_payload.get("audit_context_records", []),
                        ]
                    )
                ),
                "representative_record_count": len(representatives),
                "representative_record_mass_ratio": represented_record_mass / len(shard),
                "represented_group_count": len(represented_groups),
                "represented_reasoning_group_count": sum(
                    group_id in represented_groups and group_reasoning_verified[group_id] for group_id in group_counts
                ),
                "schema_version": SEMANTIC_LEDGER_SCHEMA,
                "shard_index": shard_index,
                "source_record_count": len(shard),
                "source_record_ids_sha256": sha256_text(canonical_json(sorted(record.record_id for record in shard))),
                "summary_chars": len(summary),
                "summary_truncated": len(summary) > 12_000,
                "summary_truncated_for_category": len(summary) > 12_000,
                "payload_semantic_digest_count": shard_semantic_digest_count,
                "mechanism_value_modality_digest_count": shard_dimension_digest_count,
                "same_signature_multi_semantic_group_count": shard_multi_semantic_groups,
                "rare_reasoning_semantic_digest_not_exposed_count": shard_rare_digest_count,
                "largest_evidence_groups": largest_groups,
                "unrepresented_group_count": len(groups) - len(represented_groups),
                "unrepresented_reasoning_group_count": sum(
                    group_id not in represented_groups and group_reasoning_verified[group_id]
                    for group_id in group_counts
                ),
            }
            writer.write((canonical_json(row) + "\n").encode("utf-8"))
            shard_rows.append(row)
            shard_summaries.append(
                {
                    "shard_index": shard_index,
                    "record_count": len(shard),
                    "record_ids": [record.record_id for record in shard],
                    "record_ids_sha256": row["source_record_ids_sha256"],
                    "summary": summary,
                }
            )

    category_rows: list[dict[str, Any]] = []
    category_exposed: set[str] = set()
    category_manifest = {
        str(row.get("category")): row for row in compile_manifest.get("categories", []) if isinstance(row, dict)
    }
    for file_name in BRAIN_FILES:
        category = _brain_category(file_name)
        samples = category_samples[category]
        records = [*samples["reasoning"], *samples["context"]]
        category_exposed.update(record.record_id for record in records)
        declared = category_manifest.get(category, {})
        synthesis_prompt = _brain_category_prompt(
            category=category,
            records=records,
            shard_summaries=shard_summaries,
            brain_version=str(compile_manifest.get("brain_version")),
            provider_name=str(compile_manifest.get("provider")),
            model=str(compile_manifest.get("model")),
            reasoning_effort=str(compile_manifest.get("reasoning_effort")),
        )
        synthesis_cache = cache.get(str(declared.get("synthesis_cache_key")), {})
        synthesis = str(synthesis_cache.get("output") or "")
        review_prompt = _brain_category_review_prompt(
            category=category,
            synthesis=synthesis,
            records=records,
            shard_summaries=shard_summaries,
            brain_version=str(compile_manifest.get("brain_version")),
            provider_name=str(compile_manifest.get("provider")),
            model=str(compile_manifest.get("model")),
            reasoning_effort=str(compile_manifest.get("reasoning_effort")),
        )
        category_rows.append(
            {
                "category": category,
                "source_record_count": declared.get("source_record_count"),
                "raw_reasoning_records_directly_shown": len(samples["reasoning"]),
                "raw_context_records_directly_shown": len(samples["context"]),
                "raw_records_directly_shown": len(records),
                "raw_records_not_directly_shown": max(
                    0,
                    _safe_int(declared.get("source_record_count")) - len(records),
                ),
                "synthesis_prompt_chars": len(synthesis_prompt),
                "synthesis_prompt_sha256_matches": (
                    declared.get("synthesis_prompt_sha256") == sha256_text(synthesis_prompt)
                ),
                "review_prompt_chars": len(review_prompt),
                "review_prompt_sha256_matches": declared.get("review_prompt_sha256") == sha256_text(review_prompt),
                "shard_summary_only_record_count": max(
                    0,
                    _safe_int(declared.get("source_record_count")) - len(records),
                ),
            }
        )

    call_ledger = audit_llm_call_ledger(target, compile_manifest, cache)
    payload_exposed = len(exposed_record_ids)
    coverage_outcome = semantic_coverage_outcome(
        total_records=total_records,
        payload_exposed_records=payload_exposed,
        claim_referenced_records=len(referenced_records),
    )
    semantic_summary = {
        "schema_version": "nslab.external_audit_semantic_exposure_summary.v1",
        "byte_accounted_count": total_records,
        "group_accounted_count": total_records,
        "payload_exposed_to_map_count": payload_exposed,
        "payload_not_exposed_to_map_count": total_records - payload_exposed,
        "category_raw_exposed_unique_count": len(category_exposed),
        "claim_referenced_record_count": len(referenced_records),
        "claim_unreferenced_record_count": total_records - len(referenced_records),
        "rare_reasoning_payload_not_exposed_count": len(rare_unexposed_ids),
        "payload_semantic_digest_count": total_payload_semantic_digest_count,
        "mechanism_value_modality_digest_count": total_dimension_digest_count,
        "same_signature_multi_semantic_group_count": same_signature_multi_semantic_group_count,
        "max_evidence_group_record_count": max_evidence_group_record_count,
        "high_cardinality_group_count": high_cardinality_group_count,
        **coverage_outcome,
        "prompt_size_distribution": {
            "max": max(prompt_sizes, default=0),
            "p50": _percentile(prompt_sizes, 0.50),
            "p95": _percentile(prompt_sizes, 0.95),
            "p99": _percentile(prompt_sizes, 0.99),
            "limit": LLM_FULL_PROMPT_MAX_CHARS,
            "limit_exceeded_count": sum(value > LLM_FULL_PROMPT_MAX_CHARS for value in prompt_sizes),
        },
        "shards": shard_rows,
        "categories": category_rows,
        "semantic_ledger_sha256": file_sha256(ledger_path),
    }
    return semantic_summary, call_ledger


def audit_import_and_inventory(
    target: AuditTarget,
    *,
    artifact_index: dict[str, tuple[int, str]] | None = None,
) -> dict[str, Any]:
    receipt = read_json(target.import_receipt_path)
    inventory = read_json(target.inventory_manifest_path)
    artifact_manifest = read_json(target.record_artifact_manifest_path)
    if not all(isinstance(item, dict) for item in (receipt, inventory, artifact_manifest)):
        raise ExternalAuditError("IMPORT_ARTIFACT_INVALID", "receipt, inventory, or artifact manifest")
    receipt_dict = dict(receipt)
    inventory_dict = dict(inventory)
    artifacts_dict = dict(artifact_manifest)
    findings: list[str] = []

    def check_reference(name: str, base: Path = target.repo_root) -> dict[str, Any]:
        reference = receipt_dict.get(name)
        relative = reference.get("artifact_path") if isinstance(reference, dict) else None
        declared_hash = reference.get("sha256") if isinstance(reference, dict) else None
        if not isinstance(relative, str):
            findings.append(f"reference_missing:{name}")
            return {"status": "missing"}
        path = (base / relative).resolve()
        try:
            path.relative_to(target.repo_root)
        except ValueError:
            findings.append(f"reference_path_escape:{name}")
            return {"status": "path_escape"}
        if not path.is_file():
            findings.append(f"reference_file_missing:{name}")
            return {"status": "missing", "path": relative}
        try:
            project_relative = path.relative_to(target.project_root).as_posix()
        except ValueError:
            project_relative = None
        indexed = artifact_index.get(project_relative) if artifact_index is not None and project_relative else None
        observed = indexed[1] if indexed is not None else file_sha256(path)
        if observed != declared_hash:
            findings.append(f"reference_hash_mismatch:{name}")
        return {
            "status": "present",
            "path": relative,
            "declared_sha256": declared_hash,
            "observed_sha256": observed,
            "match": observed == declared_hash,
        }

    references = {
        name: check_reference(name)
        for name in (
            "inventory_manifest",
            "bundle_results",
            "record_artifacts",
            "record_identity_index",
            "record_index_manifest",
        )
    }
    bundle_results_path = target.stage_root / "bundle_results.jsonl"
    bundle_result_count = 0
    bundle_result_statuses: Counter[str] = Counter()
    with bundle_results_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                findings.append("bundle_result_row_invalid")
                continue
            bundle_result_count += 1
            bundle_result_statuses[str(row.get("status") or row.get("import_status") or "UNKNOWN")] += 1
    ready_entries_ref = inventory_dict.get("ready_entries")
    ready_entries_relative = ready_entries_ref.get("artifact_path") if isinstance(ready_entries_ref, dict) else None
    ready_entries_count = 0
    ready_entries_hash_match = False
    if isinstance(ready_entries_relative, str):
        ready_path = (target.repo_root / ready_entries_relative).resolve()
        if ready_path.is_file():
            with ready_path.open("r", encoding="utf-8") as handle:
                ready_entries_count = sum(1 for line in handle if line.strip())
            assert isinstance(ready_entries_ref, dict)
            ready_entries_hash_match = file_sha256(ready_path) == ready_entries_ref.get("sha256")
        else:
            findings.append("ready_entries_missing")
    else:
        findings.append("ready_entries_reference_missing")

    declared_artifacts = artifacts_dict.get("artifacts")
    artifact_hash_mismatches: list[str] = []
    if isinstance(declared_artifacts, dict):
        for relative, metadata in declared_artifacts.items():
            if not isinstance(relative, str) or not isinstance(metadata, dict):
                artifact_hash_mismatches.append(str(relative))
                continue
            path = (target.project_root / relative).resolve()
            try:
                path.relative_to(target.project_root)
            except ValueError:
                artifact_hash_mismatches.append(relative)
                continue
            observed = artifact_index.get(relative) if artifact_index is not None else None
            if observed is None and path.is_file():
                observed = (path.stat().st_size, file_sha256(path))
            if observed is None or observed != (metadata.get("byte_size"), metadata.get("sha256")):
                artifact_hash_mismatches.append(relative)
    else:
        findings.append("record_artifact_rows_invalid")
        declared_artifacts = {}
    declared_artifact_count = len(declared_artifacts)
    declared_artifact_bytes = sum(
        int(metadata.get("byte_size", 0)) for metadata in declared_artifacts.values() if isinstance(metadata, dict)
    )
    if declared_artifact_count != artifacts_dict.get("artifact_count"):
        findings.append("record_artifact_count_mismatch")
    if declared_artifact_bytes != artifacts_dict.get("total_byte_size"):
        findings.append("record_artifact_byte_total_mismatch")
    recomputed_artifact_root = sha256_text(canonical_json(declared_artifacts))
    if recomputed_artifact_root != artifacts_dict.get("root_sha256"):
        findings.append("record_artifact_root_mismatch")
    if bundle_result_count != receipt_dict.get("imported_bundle_count"):
        findings.append("bundle_result_count_mismatch")
    if ready_entries_count != inventory_dict.get("ready_bundle_count"):
        findings.append("inventory_ready_entry_count_mismatch")
    findings.extend(f"record_artifact_mismatch:{path}" for path in artifact_hash_mismatches[:100])
    attestation = inventory_dict.get("attestation")
    return {
        "schema_version": "nslab.external_audit_import_inventory.v1",
        "inventory_id": inventory_dict.get("inventory_id"),
        "inventory_ready_for_import": inventory_dict.get("ready_for_import"),
        "inventory_ready_bundle_count": inventory_dict.get("ready_bundle_count"),
        "inventory_ready_record_count": inventory_dict.get("ready_record_count"),
        "inventory_training_eligible_count": inventory_dict.get("ready_training_eligible_record_count"),
        "inventory_semantic_excluded_count": inventory_dict.get("ready_semantic_excluded_record_count"),
        "inventory_ready_entries_count": ready_entries_count,
        "inventory_ready_entries_hash_match": ready_entries_hash_match,
        "inventory_attestation_present": isinstance(attestation, dict),
        "inventory_attestation_key_id": attestation.get("key_id") if isinstance(attestation, dict) else None,
        "import_id": receipt_dict.get("import_id"),
        "imported_bundle_count": receipt_dict.get("imported_bundle_count"),
        "imported_record_count": receipt_dict.get("imported_record_count"),
        "imported_training_eligible_record_count": receipt_dict.get("imported_training_eligible_record_count"),
        "import_loss_count": receipt_dict.get("import_loss_count"),
        "quarantined_bundle_count": receipt_dict.get("quarantined_bundle_count"),
        "bundle_result_count": bundle_result_count,
        "bundle_result_statuses": dict(sorted(bundle_result_statuses.items())),
        "record_corpus_sha256": receipt_dict.get("record_corpus_sha256"),
        "record_artifact_root_sha256": receipt_dict.get("record_artifact_root_sha256"),
        "recomputed_record_artifact_root_sha256": recomputed_artifact_root,
        "record_store_generation_sha256": receipt_dict.get("record_store_generation_sha256"),
        "record_artifact_hash_mismatch_count": len(artifact_hash_mismatches),
        "record_artifact_count": declared_artifact_count,
        "record_artifact_total_bytes": declared_artifact_bytes,
        "record_artifact_manifest_record_count": artifacts_dict.get("record_count"),
        "production_inventory_attestation_status": (
            "PRESENT_KEY_NOT_EXPOSED" if isinstance(attestation, dict) else "NOT_IN_ARTIFACT"
        ),
        "import_inspection_report": "RECOMPUTED_BY_EXTERNAL_AUDIT",
        "import_loss_report": "RECOMPUTED_BY_EXTERNAL_AUDIT",
        "references": references,
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings,
    }


def audit_memory_snapshot(
    target: AuditTarget,
    *,
    deterministic_seed: str,
    artifact_index: dict[str, tuple[int, str]] | None = None,
) -> dict[str, Any]:
    manifest = read_json(target.memory_manifest_path)
    brain_manifest = read_json(target.brain_manifest_path)
    if not isinstance(manifest, dict):
        raise ExternalAuditError("MEMORY_MANIFEST_INVALID", target.memory_manifest_path.as_posix())
    if not isinstance(brain_manifest, dict):
        raise ExternalAuditError("BRAIN_MANIFEST_INVALID", target.brain_manifest_path.as_posix())
    findings: list[str] = []
    artifact_results: dict[str, Any] = {}
    for name in (
        "source_record_hashes",
        "excluded_future_record_hashes",
        "routing_metadata",
        "embedding_hashes",
        "cell_entries",
        "memberships",
        "database",
    ):
        reference_value = manifest.get(name)
        reference = reference_value if isinstance(reference_value, dict) else None
        relative = reference.get("artifact_path") if reference is not None else None
        if not isinstance(relative, str):
            findings.append(f"memory_artifact_reference_missing:{name}")
            continue
        path = (target.project_root / relative).resolve()
        try:
            path.relative_to(target.project_root)
        except ValueError:
            findings.append(f"memory_artifact_path_escape:{name}")
            continue
        if not path.is_file():
            findings.append(f"memory_artifact_missing:{name}")
            continue
        indexed = artifact_index.get(relative) if artifact_index is not None else None
        observed_hash = indexed[1] if indexed is not None else file_sha256(path)
        assert reference is not None
        hash_match = observed_hash == reference.get("sha256")
        if not hash_match:
            findings.append(f"memory_artifact_hash_mismatch:{name}")
        artifact_results[name] = {
            "path": relative,
            "item_count": reference.get("item_count"),
            "declared_sha256": reference.get("sha256"),
            "observed_sha256": observed_hash,
            "hash_match": hash_match,
        }
    database_path = _memory_database_path(target, manifest)
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        counts = {
            table: int(_fetchone_required(connection, f"SELECT COUNT(*) FROM {table}")[0])
            for table in (
                "records",
                "reasoning_records",
                "memberships",
                "secondary_memberships",
                "cells",
                "reasoning_cells",
                "provenance_edges",
                "reasoning_cell_facets",
            )
        }
        empty_cell_count = int(
            _fetchone_required(connection, "SELECT COUNT(*) FROM cells WHERE primary_member_count = 0")[0]
        )
        orphan_memberships = int(
            _fetchone_required(
                connection,
                "SELECT COUNT(*) FROM memberships m LEFT JOIN records r USING(record_id) WHERE r.record_id IS NULL",
            )[0]
        )
        duplicate_primary = int(
            _fetchone_required(
                connection,
                "SELECT COUNT(*) FROM (SELECT record_id FROM memberships GROUP BY record_id HAVING COUNT(*) > 1)",
            )[0]
        )
        dense_vector_count = int(
            _fetchone_required(connection, "SELECT COUNT(*) FROM records WHERE embedding IS NOT NULL")[0]
        )
        fts_document_count = counts["reasoning_records"]
        hnsw_item_count = counts["reasoning_cells"] + counts["reasoning_cell_facets"]
        selected_record_id = str(
            _fetchone_required(
                connection,
                "SELECT record_id FROM records ORDER BY sha256(? || record_id), record_id LIMIT 1",
                [deterministic_seed],
            )[0]
        )
        lookup = _fetchone_required(
            connection,
            "SELECT primary_cell_id, document FROM records WHERE record_id = ?",
            [selected_record_id],
        )
        membership_lookup = _fetchone_required(
            connection,
            "SELECT COUNT(*) FROM memberships WHERE record_id = ?",
            [selected_record_id],
        )[0]
        cell_id = str(lookup[0])
        nearest = connection.execute(
            "SELECT cell_id FROM reasoning_cells ORDER BY array_cosine_distance("
            "centroid, (SELECT centroid FROM cells WHERE cell_id = ?)) LIMIT 1",
            [cell_id],
        ).fetchone()
        document = str(lookup[1])
        query_token = next((token for token in document.split() if token.isalnum()), selected_record_id)
        try:
            fts_count = int(
                _fetchone_required(
                    connection,
                    "SELECT COUNT(*) FROM (SELECT "
                    "fts_main_reasoning_records.match_bm25(record_id, ?) AS score "
                    "FROM reasoning_records) WHERE score IS NOT NULL",
                    [query_token],
                )[0]
            )
            fts_status = "PASS"
        except duckdb.Error:
            fts_count = 0
            fts_status = "FAIL"
        population_aggregate = connection.execute(
            "SELECT routing_disposition, COUNT(*) FROM records GROUP BY routing_disposition ORDER BY 1"
        ).fetchall()
    finally:
        connection.close()
    smoke = {
        "deterministic_seed_sha256": sha256_text(deterministic_seed),
        "selected_record_id_sha256": sha256_text(selected_record_id),
        "record_id_lookup": "PASS" if lookup else "FAIL",
        "cell_membership_lookup": "PASS" if membership_lookup == 1 else "FAIL",
        "fts_query": fts_status,
        "fts_result_count": fts_count,
        "fts_query_sha256": sha256_text(query_token),
        "hnsw_nearest_cell_query": "PASS" if nearest else "FAIL",
        "population_aggregate": (
            "PASS" if sum(int(row[1]) for row in population_aggregate) == counts["records"] else "FAIL"
        ),
        "representative_lookup": "NOT_IN_ARTIFACT",
    }
    parity = {
        "record_count": counts["records"] == manifest.get("record_count"),
        "primary_membership_count": counts["memberships"] == manifest.get("primary_membership_count"),
        "secondary_membership_count": counts["secondary_memberships"] == manifest.get("secondary_membership_count"),
        "cell_count": counts["cells"] == manifest.get("cell_count"),
        "brain_snapshot_reference": brain_manifest.get("production_memory_snapshot_id") == manifest.get("snapshot_id"),
        "brain_source_generation_reference": isinstance(manifest.get("source_generation_sha256"), str)
        and brain_manifest.get("production_memory_source_generation_sha256")
        == manifest.get("source_generation_sha256"),
    }
    findings.extend(f"memory_count_mismatch:{name}" for name, passed in parity.items() if not passed)
    return {
        "schema_version": "nslab.external_audit_memory_snapshot.v1",
        "snapshot_id": manifest.get("snapshot_id"),
        "source_record_count": manifest.get("record_count"),
        "source_record_hash_count": artifact_results.get("source_record_hashes", {}).get("item_count"),
        "routing_root": manifest.get("routing_metadata_sha256"),
        "source_generation_sha256": manifest.get("source_generation_sha256"),
        "embedding_provider": manifest.get("embedding_provider"),
        "embedding_model": manifest.get("embedding_model"),
        "embedding_dimensions": manifest.get("embedding_dimensions"),
        "real_embedding": manifest.get("real_embedding"),
        "snapshot_reuse_identity": {
            "brain_referenced_snapshot_id": brain_manifest.get("production_memory_snapshot_id"),
            "brain_referenced_source_generation": brain_manifest.get("production_memory_source_generation_sha256"),
            "snapshot_manifest_sha256": file_sha256(target.memory_manifest_path),
            "prebuild_snapshot_receipt": "NOT_IN_ARTIFACT",
            "timestamp_rebuild_claim": "NOT_IN_ARTIFACT",
        },
        "counts": counts,
        "empty_cell_count": empty_cell_count,
        "orphan_membership_count": orphan_memberships,
        "duplicate_primary_membership_count": duplicate_primary,
        "fts_document_count": fts_document_count,
        "dense_vector_count": dense_vector_count,
        "hnsw_item_count": hnsw_item_count,
        "population_cube_count": counts["reasoning_cell_facets"],
        "representative_registry_count": "NOT_IN_ARTIFACT",
        "artifact_results": artifact_results,
        "count_parity": parity,
        "smoke": smoke,
        "passed": not findings
        and all(
            smoke[key] == "PASS"
            for key in (
                "record_id_lookup",
                "cell_membership_lookup",
                "fts_query",
                "hnsw_nearest_cell_query",
                "population_aggregate",
            )
        ),
        "finding_count": len(findings),
        "findings": findings,
    }


def audit_warehouse(target: AuditTarget, record_summary: dict[str, Any]) -> dict[str, Any]:
    warehouse = target.project_root / "warehouse"
    expected = (
        "brain_records",
        "issuer_day_cases",
        "direct_event_cases",
        "theme_formation_cases",
        "beneficiary_cases",
        "leader_pairs",
        "error_cases",
        "memory_claims",
        "research_questions",
        "record_provenance",
        "record_coverage",
        "company_memory",
    )
    connection = duckdb.connect()
    findings: list[str] = []
    counts: dict[str, int | str] = {}
    try:
        for name in expected:
            path = warehouse / f"{name}.parquet"
            if not path.is_file():
                counts[name] = "NOT_IN_ARTIFACT"
                findings.append(f"warehouse_artifact_missing:{name}")
                continue
            counts[name] = int(_fetchone_required(connection, "SELECT COUNT(*) FROM read_parquet(?)", [str(path)])[0])
        brain_path = str(warehouse / "brain_records.parquet")
        brain_count, unique_count, eligible_count = _fetchone_required(
            connection,
            "SELECT COUNT(*), COUNT(DISTINCT record_id), "
            "SUM(CASE WHEN training_eligible THEN 1 ELSE 0 END) FROM read_parquet(?)",
            [brain_path],
        )
        type_rows = connection.execute(
            "SELECT record_type, COUNT(*) FROM read_parquet(?) GROUP BY record_type ORDER BY record_type",
            [brain_path],
        ).fetchall()
        expected_type_counts = record_summary.get("counts", {}).get("record_type", {})
        type_counts = {str(name): int(count) for name, count in type_rows}
        if type_counts != expected_type_counts:
            findings.append("warehouse_record_type_projection_mismatch")
        if int(brain_count) != record_summary.get("record_count"):
            findings.append("warehouse_brain_record_count_mismatch")
        if int(unique_count) != int(brain_count):
            findings.append("warehouse_duplicate_record_id")
        if int(eligible_count or 0) != record_summary.get("training_eligible", {}).get("true"):
            findings.append("warehouse_training_eligibility_mismatch")
        issuer_path = str(warehouse / "issuer_day_cases.parquet")
        issuer_duplicate_count = int(
            _fetchone_required(
                connection,
                "SELECT COUNT(*) FROM (SELECT episode_id, issuer_day_case_id FROM read_parquet(?) "
                "WHERE issuer_day_case_id IS NOT NULL GROUP BY episode_id, issuer_day_case_id "
                "HAVING COUNT(*) > 1)",
                [issuer_path],
            )[0]
        )
        issuer_weight_violation_count = int(
            _fetchone_required(
                connection,
                "SELECT COUNT(*) FROM (SELECT issuer_day_weight_group_id, SUM(sample_weight) total "
                "FROM read_parquet(?) WHERE issuer_day_weight_group_id IS NOT NULL AND training_eligible "
                "GROUP BY issuer_day_weight_group_id HAVING abs(total - 1.0) > 0.0000001)",
                [issuer_path],
            )[0]
        )
        event_weight_violation_count = int(
            _fetchone_required(
                connection,
                "SELECT COUNT(*) FROM (SELECT issuer_day_weight_group_id, SUM(sample_weight) total "
                "FROM read_parquet(?) WHERE issuer_day_weight_group_id IS NOT NULL AND training_eligible "
                "GROUP BY issuer_day_weight_group_id HAVING abs(total - 1.0) > 0.0000001)",
                [str(warehouse / "direct_event_cases.parquet")],
            )[0]
        )
        invalid_company_memory_time_count = int(
            _fetchone_required(
                connection,
                "SELECT COUNT(*) FROM read_parquet(?) WHERE known_at < available_from",
                [str(warehouse / "company_memory.parquet")],
            )[0]
        )
        delayed_company_memory_count = int(
            _fetchone_required(
                connection,
                "SELECT COUNT(*) FROM read_parquet(?) WHERE known_at > available_from",
                [str(warehouse / "company_memory.parquet")],
            )[0]
        )
        invalid_leader_pair_projection_count = int(
            _fetchone_required(
                connection,
                "SELECT COUNT(*) FROM read_parquet(?) WHERE record_type <> 'blind_leader_preference_pair'",
                [str(warehouse / "leader_pairs.parquet")],
            )[0]
        )
    finally:
        connection.close()
    for label, value in (
        ("issuer_day_duplicate", issuer_duplicate_count),
        ("issuer_day_weight", issuer_weight_violation_count),
        ("event_weight", event_weight_violation_count),
        ("company_memory_known_at", invalid_company_memory_time_count),
        ("leader_pair_projection", invalid_leader_pair_projection_count),
    ):
        if value:
            findings.append(f"warehouse_{label}_violation")
    return {
        "schema_version": "nslab.external_audit_warehouse.v1",
        "manifest_status": "NOT_IN_ARTIFACT",
        "counts": counts,
        "brain_record_count": int(brain_count),
        "unique_record_id_count": int(unique_count),
        "training_eligible_count": int(eligible_count or 0),
        "record_type_counts": type_counts,
        "issuer_day_duplicate_count": issuer_duplicate_count,
        "issuer_day_weight_violation_count": issuer_weight_violation_count,
        "event_weight_violation_count": event_weight_violation_count,
        "company_memory_known_at_before_available_from_count": invalid_company_memory_time_count,
        "company_memory_delayed_known_at_count": delayed_company_memory_count,
        "invalid_leader_pair_projection_count": invalid_leader_pair_projection_count,
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings,
    }


def _git_output(repo_root: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and completed.returncode:
        raise ExternalAuditError("GIT_IDENTITY_FAILED", sha256_text(" ".join(arguments)))
    return completed.stdout.rstrip()


def _pointer_state(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "production" / "current.json"
    if not path.is_file():
        return {"status": "ABSENT", "sha256": None, "size_bytes": 0}
    return {"status": "PRESENT", "sha256": file_sha256(path), "size_bytes": path.stat().st_size}


def build_target_lock(
    target: AuditTarget,
    profile: dict[str, Any],
    artifact_summary: dict[str, Any],
) -> dict[str, Any]:
    receipt = read_json(target.import_receipt_path)
    record_manifest = read_json(target.project_root / "memory" / "record_index" / "manifest.json")
    warehouse_manifest = target.project_root / "warehouse" / "manifest.json"
    family_roots = artifact_summary.get("family_roots", {})
    lock = {
        "schema_version": "nslab.external_audit_target_lock.v1",
        "brain_version": profile.get("brain_version"),
        "exact_staged_project_root": target.repo_relative(target.project_root),
        "stage_root": target.repo_relative(target.stage_root),
        "brain_manifest": {
            "path": target.repo_relative(target.brain_manifest_path),
            "sha256": file_sha256(target.brain_manifest_path),
        },
        "llm_compile_manifest": {
            "path": target.repo_relative(target.compile_manifest_path),
            "sha256": file_sha256(target.compile_manifest_path),
        },
        "import_receipt": {
            "path": target.repo_relative(target.import_receipt_path),
            "sha256": file_sha256(target.import_receipt_path),
        },
        "inventory_manifest": {
            "path": target.repo_relative(target.inventory_manifest_path),
            "sha256": file_sha256(target.inventory_manifest_path),
        },
        "memory_snapshot_manifest": {
            "path": target.repo_relative(target.memory_manifest_path),
            "sha256": file_sha256(target.memory_manifest_path),
        },
        "warehouse_manifest": (
            {
                "path": target.repo_relative(warehouse_manifest),
                "sha256": file_sha256(warehouse_manifest),
            }
            if warehouse_manifest.is_file()
            else "NOT_IN_ARTIFACT"
        ),
        "record_artifact_manifest": {
            "path": target.repo_relative(target.record_artifact_manifest_path),
            "sha256": file_sha256(target.record_artifact_manifest_path),
        },
        "record_corpus_sha256": receipt.get("record_corpus_sha256") if isinstance(receipt, dict) else None,
        "record_artifact_root": (receipt.get("record_artifact_root_sha256") if isinstance(receipt, dict) else None),
        "record_store_generation": (
            record_manifest.get("generation_root_sha256") if isinstance(record_manifest, dict) else None
        ),
        "brain_root": family_roots.get("brain"),
        "memory_root": family_roots.get("memory"),
        "warehouse_root": family_roots.get("warehouse"),
        "artifact_population_root": artifact_summary.get("artifact_population_merkle_root"),
        "staging_build_commit": profile.get("staging_build_commit"),
        "symlink_policy": "REJECT_ALL",
        "path_policy": "CANONICAL_REPO_RELATIVE_POSIX_ONLY",
    }
    lock["target_lock_sha256"] = sha256_text(canonical_json(lock))
    return lock


def _target_lock_markdown(lock: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# External audit target lock",
            "",
            f"- Brain: `{lock.get('brain_version')}`",
            f"- Exact repo-relative project root: `{lock.get('exact_staged_project_root')}`",
            f"- Staging build commit: `{lock.get('staging_build_commit')}`",
            f"- Artifact population root: `{lock.get('artifact_population_root')}`",
            f"- Record corpus root: `{lock.get('record_corpus_sha256')}`",
            f"- Record artifact root: `{lock.get('record_artifact_root')}`",
            f"- Brain root: `{lock.get('brain_root')}`",
            f"- Memory root: `{lock.get('memory_root')}`",
            f"- Warehouse root: `{lock.get('warehouse_root')}`",
            "",
            "All paths are exact repository-relative POSIX paths. Absolute user-home paths are intentionally excluded.",
        )
    )


def audit_code_identity(target: AuditTarget, profile: dict[str, Any]) -> dict[str, Any]:
    build_commit = str(profile.get("staging_build_commit") or "")
    commit_type = _git_output(target.repo_root, "cat-file", "-t", build_commit, check=False)
    observed_tree = _git_output(target.repo_root, "rev-parse", f"{build_commit}^{{tree}}", check=False)
    parents = _git_output(target.repo_root, "show", "-s", "--format=%P", build_commit, check=False).split()
    signature = subprocess.run(
        ["git", "-C", str(target.repo_root), "verify-commit", build_commit],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    signature_output = (signature.stdout + signature.stderr).lower()
    signature_present = "signature made" in signature_output or signature.returncode == 0
    if signature.returncode == 0:
        signature_status = "VALID_LOCAL_TRUST"
    elif "no public key" in signature_output and signature_present:
        signature_status = "SIGNATURE_PRESENT_LOCAL_KEY_UNAVAILABLE"
    else:
        signature_status = "UNVERIFIED"
    github_verification: dict[str, Any] | str = "NOT_AVAILABLE"
    repository = profile.get("repository")
    if isinstance(repository, str) and shutil.which("gh"):
        completed = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/commits/{build_commit}",
                "--jq",
                ".commit.verification | {verified, reason}",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode == 0:
            try:
                value = json.loads(completed.stdout)
                if isinstance(value, dict):
                    github_verification = value
            except json.JSONDecodeError:
                pass
    current_head = _git_output(target.repo_root, "rev-parse", "HEAD")
    drift_paths = _git_output(
        target.repo_root,
        "diff",
        "--name-only",
        f"{build_commit}..{current_head}",
        "--",
        "src",
        "configs",
        "prompts",
        "schemas",
    ).splitlines()
    working_tree_paths = [
        line[3:]
        for line in _git_output(target.repo_root, "status", "--short", "--untracked-files=all").splitlines()
        if len(line) > 3
    ]
    return {
        "schema_version": "nslab.external_audit_code_identity.v1",
        "repository": repository,
        "staging_build_commit": build_commit,
        "staging_build_commit_exists": commit_type == "commit",
        "staging_build_tree_expected": profile.get("expected_tree"),
        "staging_build_tree_observed": observed_tree,
        "staging_build_tree_match": observed_tree == profile.get("expected_tree"),
        "staging_build_merge_parent_count": len(parents),
        "merged_pr": profile.get("merged_pr"),
        "local_signature_status": signature_status,
        "github_signature_verification": github_verification,
        "audit_tool_commit": current_head,
        "audit_tool_is_descendant": subprocess.run(
            ["git", "-C", str(target.repo_root), "merge-base", "--is-ancestor", build_commit, current_head],
            check=False,
        ).returncode
        == 0,
        "tracked_source_config_prompt_schema_drift": drift_paths,
        "working_tree_paths": working_tree_paths,
        "staging_commit_artifact_evidence": "NOT_IN_ARTIFACT",
        "staging_commit_assessment": (
            "PROFILE_ASSERTED_AND_TREE_CORROBORATED_NOT_ARTIFACT_EMBEDDED"
            if observed_tree == profile.get("expected_tree")
            else "UNVERIFIED"
        ),
    }


def audit_brain_identity(
    target: AuditTarget,
    profile: dict[str, Any],
    claim_summary: dict[str, Any],
    call_ledger: dict[str, Any],
) -> dict[str, Any]:
    brain = read_json(target.brain_manifest_path)
    compile_manifest = read_json(target.compile_manifest_path)
    if not isinstance(brain, dict) or not isinstance(compile_manifest, dict):
        raise ExternalAuditError("BRAIN_IDENTITY_INVALID", "brain or compile manifest")
    observed = {
        "brain_version": brain.get("brain_version"),
        "build_mode": brain.get("build_mode"),
        "catalog_only": brain.get("catalog_only"),
        "provider": compile_manifest.get("provider"),
        "model": compile_manifest.get("model"),
        "reasoning_effort": compile_manifest.get("reasoning_effort"),
        "oauth_health": brain.get("oauth_health_check_status"),
        "compiler_version": compile_manifest.get("compiler_version"),
        "map_reduce_version": compile_manifest.get("map_reduce_version"),
        "compile_manifest_schema": compile_manifest.get("schema_version"),
        "source_record_count": compile_manifest.get("source_record_count"),
        "compiled_claim_count": claim_summary.get("claim_count"),
        "physical_record_shard_count": compile_manifest.get("record_shard_count"),
        "category_count": compile_manifest.get("category_count"),
        "llm_generation_count": compile_manifest.get("llm_generation_count"),
        "live_call_count": call_ledger.get("total_live_calls"),
        "cache_hit_count": brain.get("cache_hit_count"),
        "evidence_policy": brain.get("evidence_policy"),
        "web_provider": brain.get("web_provider"),
    }
    checks = {
        "brain_version": observed["brain_version"] == profile.get("brain_version"),
        "build_mode": observed["build_mode"] == "llm-full",
        "catalog_only": observed["catalog_only"] is False,
        "provider": observed["provider"] == profile.get("expected_provider"),
        "model": observed["model"] == profile.get("expected_model"),
        "reasoning_effort": observed["reasoning_effort"] == profile.get("expected_reasoning_effort"),
        "oauth_health": observed["oauth_health"] == "PASS",
        "compiler_version": observed["compiler_version"] == profile.get("compiler_version"),
        "map_reduce_version": observed["map_reduce_version"] == profile.get("map_reduce_version"),
        "compile_manifest_schema": observed["compile_manifest_schema"] == profile.get("compile_manifest_schema"),
        "source_record_count": observed["source_record_count"] == profile.get("expected_source_record_count"),
        "compiled_claim_count": observed["compiled_claim_count"] == profile.get("expected_compiled_claim_count"),
        "live_call_count": observed["live_call_count"] == profile.get("expected_live_oauth_call_count"),
        "generation_trace_closure": call_ledger.get("missing_trace_count") == 0,
        "generation_failure_zero": call_ledger.get("failure_count") == 0,
    }
    return {
        "schema_version": "nslab.external_audit_brain_identity.v1",
        "observed": observed,
        "checks": checks,
        "passed": all(checks.values()),
        "finding_count": sum(not passed for passed in checks.values()),
        "findings": [name for name, passed in checks.items() if not passed],
        "compiled_claim_origin_caveat": (
            "Compiled claims are classified by provenance; deterministic record claims are not described as "
            "individually LLM-authored."
        ),
    }


def audit_brain_categories(target: AuditTarget, profile: dict[str, Any]) -> dict[str, Any]:
    compile_manifest = read_json(target.compile_manifest_path)
    if not isinstance(compile_manifest, dict):
        raise ExternalAuditError("COMPILE_MANIFEST_INVALID", target.compile_manifest_path.as_posix())
    category_manifest = {
        str(row.get("file_name")): row for row in compile_manifest.get("categories", []) if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    content_hashes: Counter[str] = Counter()
    body_hashes: Counter[str] = Counter()
    findings: list[str] = []
    fixed_ticker_count = 0
    fixed_rule_count = 0
    for file_name in BRAIN_FILES:
        path = target.project_root / "brain" / "current" / file_name
        if not path.is_file():
            findings.append(f"category_missing:{file_name}")
            continue
        text = path.read_text(encoding="utf-8")
        digest = file_sha256(path)
        body = text.partition("## Category Synthesis")[2]
        body_digest = sha256_text(body)
        content_hashes[digest] += 1
        body_hashes[body_digest] += 1
        declared = category_manifest.get(file_name, {})
        source_record_ids = declared.get("source_record_ids")
        source_record_ids_sha256 = (
            sha256_text(canonical_json(sorted(_string_list(source_record_ids))))
            if isinstance(source_record_ids, list)
            else "NOT_IN_ARTIFACT"
        )
        source_match = f"Source record count: {declared.get('source_record_count')}" in text
        identity_match = all(
            marker in text
            for marker in (
                f"Provider: `{profile.get('expected_provider')}`",
                f"Model: `{profile.get('expected_model')}`",
                f"Reasoning effort: `{profile.get('expected_reasoning_effort')}`",
                f"Brain version: `{profile.get('brain_version')}`",
            )
        )
        fixed_ticker_count += len(re.findall(r"(?<![A-Za-z0-9_-])\d{6}(?![A-Za-z0-9_-])", text))
        for line in text.splitlines():
            if not re.search(
                r"(?:ticker|company|theme|region|policy).{0,30}"
                r"(?:whitelist|allowlist|lookup table|score table)",
                line,
                flags=re.IGNORECASE,
            ):
                continue
            if re.search(r"\b(?:no|not|never|avoid|reject|unsupported|without)\b", line, flags=re.IGNORECASE):
                continue
            fixed_rule_count += 1
        if not source_match:
            findings.append(f"category_source_count_mismatch:{file_name}")
        if not identity_match:
            findings.append(f"category_identity_mismatch:{file_name}")
        rows.append(
            {
                "file_name": file_name,
                "category": declared.get("category"),
                "size_bytes": path.stat().st_size,
                "sha256": digest,
                "body_sha256": body_digest,
                "source_record_count": declared.get("source_record_count"),
                "source_record_ids_sha256": source_record_ids_sha256,
                "identity_match": identity_match,
                "source_count_header_match": source_match,
            }
        )
    identical_pairs = sum(count * (count - 1) // 2 for count in content_hashes.values() if count > 1)
    identical_body_pairs = sum(count * (count - 1) // 2 for count in body_hashes.values() if count > 1)
    if identical_pairs:
        findings.append("byte_identical_category_pair")
    if identical_body_pairs:
        findings.append("title_only_different_category_pair")
    if fixed_rule_count:
        findings.append("fixed_lookup_or_score_rule_in_category_output")
    return {
        "schema_version": "nslab.external_audit_brain_categories.v1",
        "category_count": len(rows),
        "categories": rows,
        "byte_identical_pair_count": identical_pairs,
        "title_only_different_body_pair_count": identical_body_pairs,
        "six_digit_numeric_literal_count": fixed_ticker_count,
        "fixed_lookup_or_score_rule_count": fixed_rule_count,
        "reference_closure_source": "audits/compiled_claims_summary.json",
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings,
    }


def _project_brain_audit(result: dict[str, object]) -> dict[str, Any]:
    finding_keys = (
        "artifact_read_findings",
        "episode_coverage_findings",
        "determinism_findings",
        "record_coverage_findings",
        "brain_diversity_findings",
        "llm_compile_findings",
        "compiled_claim_findings",
    )
    projected_findings = {key: value for key in finding_keys if isinstance((value := result.get(key)), list) and value}
    return {
        "schema_version": "nslab.external_audit_brain_read_only_audit.v1",
        "deep": result.get("deep"),
        "passed": result.get("passed"),
        "brain_version": result.get("brain_version"),
        "brain_build_mode": result.get("brain_build_mode"),
        "catalog_only": result.get("catalog_only"),
        "coverage_complete": result.get("coverage_complete"),
        "accepted_episode_count": result.get("accepted_episode_count"),
        "brain_covered_episode_count": result.get("brain_covered_episode_count"),
        "record_coverage_complete": result.get("record_coverage_complete"),
        "deterministic_rebuild_verified": result.get("deterministic_rebuild_verified"),
        "llm_compile_manifest_present": result.get("llm_compile_manifest_present"),
        "compiled_claim_file_present": result.get("compiled_claim_file_present"),
        "finding_groups": projected_findings,
        "finding_count": sum(len(value) for value in projected_findings.values()),
        "write_report": False,
    }


def audit_existing_brain_reports(target: AuditTarget) -> dict[str, Any]:
    diagnostics = target.project_root / "diagnostics"
    rows: list[dict[str, Any]] = []
    for file_name in (
        "brain_compile_report.json",
        "brain_record_store_report.json",
        "record_coverage_report.json",
    ):
        path = diagnostics / file_name
        rows.append(
            {
                "file_name": file_name,
                "status": "PRESENT" if path.is_file() else "NOT_IN_ARTIFACT",
                "size_bytes": path.stat().st_size if path.is_file() else 0,
                "sha256": file_sha256(path) if path.is_file() else None,
            }
        )
    return {
        "schema_version": "nslab.external_audit_existing_brain_reports.v1",
        "artifacts": rows,
        "reported_before_audit": "REPORTED_BUT_DISTINCT_AUDIT_ARTIFACT_NOT_IDENTIFIED",
        "reported_after_audit": "REPORTED_BUT_DISTINCT_AUDIT_ARTIFACT_NOT_IDENTIFIED",
        "same_brain_root_before_after": "NOT_IN_ARTIFACT",
        "old_artifact_deletion_receipt": "NOT_IN_ARTIFACT",
    }


def audit_old_model_absence(target: AuditTarget) -> dict[str, Any]:
    active_old_identity_files: list[str] = []
    for path in (
        target.brain_manifest_path,
        target.compile_manifest_path,
        *sorted((target.project_root / "brain" / "llm_cache").glob("*.json")),
        *sorted((target.project_root / "brain" / "snapshots").glob("*/brain_manifest.json")),
    ):
        if not path.is_file():
            continue
        value = read_json(path)
        if not isinstance(value, dict):
            continue
        model = value.get("model") or value.get("llm_model")
        effort = value.get("reasoning_effort")
        if model == "gpt-5.4" or (model == "gpt-5.4" and effort == "high"):
            active_old_identity_files.append(path.relative_to(target.project_root).as_posix())
    historical_trace_count = 0
    for path in sorted((target.project_root / "runs" / "traces").glob("*.json")):
        value = read_json(path)
        model_config = value.get("model_config") if isinstance(value, dict) else None
        if isinstance(model_config, dict) and model_config.get("model") == "gpt-5.4":
            historical_trace_count += 1
    return {
        "schema_version": "nslab.external_audit_old_model_absence.v1",
        "active_brain_cache_snapshot_old_identity_count": len(active_old_identity_files),
        "active_brain_cache_snapshot_old_identity_files": active_old_identity_files,
        "historical_old_model_trace_count": historical_trace_count,
        "historical_trace_interpretation": "PRESERVED_AUDIT_HISTORY_NOT_ACTIVE_BRAIN_OR_CACHE",
        "deletion_receipt": "NOT_IN_ARTIFACT",
        "exact_deleted_bytes_verification": "NOT_IN_ARTIFACT",
        "passed": not active_old_identity_files,
    }


def audit_policy_boundaries(
    target: AuditTarget,
    record_summary: dict[str, Any],
    claim_summary: dict[str, Any],
    call_ledger: dict[str, Any],
    warehouse_summary: dict[str, Any],
) -> dict[str, Any]:
    brain = read_json(target.brain_manifest_path)
    hardcoding = audit_hardcoding(target.repo_root)
    if not isinstance(brain, dict):
        raise ExternalAuditError("BRAIN_MANIFEST_INVALID", target.brain_manifest_path.as_posix())
    evidence_policy = str(brain.get("evidence_policy") or "")
    web_provider = str(brain.get("web_provider") or "")
    hardcoding_findings = hardcoding.get("findings")
    metrics = {
        "future_record_exposure_count": record_summary.get("future_available_from_count"),
        "claim_temporal_leak_count": claim_summary.get("finding_counts", {}).get(
            "claim_available_from_before_support",
            0,
        ),
        "orphan_record_reference_count": claim_summary.get("finding_counts", {}).get(
            "orphan_record_reference",
            0,
        ),
        "orphan_episode_reference_count": claim_summary.get("finding_counts", {}).get(
            "orphan_episode_reference",
            0,
        ),
        "company_memory_invalid_time_count": warehouse_summary.get(
            "company_memory_known_at_before_available_from_count"
        ),
        "evidence_policy": evidence_policy,
        "web_provider": web_provider,
        "llm_tool_call_count": call_ledger.get("tool_call_count"),
        "llm_web_tool_call_count": call_ledger.get("web_tool_call_count"),
        "fixed_ticker_or_mapping_finding_count": (
            len(hardcoding_findings) if isinstance(hardcoding_findings, list) else 0
        ),
        "phase8_equal_zero_web_surface": "NOT_IN_ARTIFACT",
    }
    checks = {
        "future_record_exposure_zero": metrics["future_record_exposure_count"] == 0,
        "claim_temporal_leak_zero": metrics["claim_temporal_leak_count"] == 0,
        "claim_record_reference_closure": metrics["orphan_record_reference_count"] == 0,
        "claim_episode_reference_closure": metrics["orphan_episode_reference_count"] == 0,
        "company_memory_time_valid": metrics["company_memory_invalid_time_count"] == 0,
        "csv_memory_only_strict": evidence_policy.lower().replace("_", "-") == "csv-memory-only-strict",
        "web_provider_disabled": web_provider == "disabled",
        "llm_web_tool_calls_zero": metrics["llm_web_tool_call_count"] == 0,
        "hardcoding_audit": hardcoding.get("passed") is True,
    }
    return {
        "schema_version": "nslab.external_audit_policy_boundaries.v1",
        "metrics": metrics,
        "checks": checks,
        "passed": all(checks.values()),
        "finding_count": sum(not value for value in checks.values()),
        "findings": [name for name, value in checks.items() if not value],
        "hardcoding_audit": hardcoding,
        "blind_outcome_phase_separation": record_summary.get("counts", {}).get("evidence_phase", {}),
    }


def audit_release_state(repo_root: Path, profile: dict[str, Any]) -> dict[str, Any]:
    pointer = _pointer_state(repo_root)
    active = pointer["status"] == "PRESENT"
    expected_active = profile.get("production_release_expected_active")
    return {
        "schema_version": "nslab.external_audit_release_state.v1",
        "pointer": pointer,
        "active_production_release": active,
        "expected_active": expected_active,
        "matches_expected": active is expected_active,
        "finalize_release_evidence": "NOT_IN_ARTIFACT",
        "activation_evidence": "NOT_IN_ARTIFACT",
        "status": "STAGING_AUDIT_ONLY",
        "production_activation": "NOT_PRODUCTION_ACTIVATED" if not active else "ACTIVE_RELEASE_PRESENT",
    }


def environment_identity() -> dict[str, Any]:
    return {
        "schema_version": "nslab.external_audit_environment_identity.v1",
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "duckdb_version": duckdb.__version__,
        "filesystem_encoding": sys.getfilesystemencoding(),
        "timezone": datetime.now().astimezone().tzname(),
        "absolute_paths_redacted": True,
    }


def _metric(
    name: str,
    reported: object,
    observed: object,
    source: str,
    *,
    tolerance: float | None = None,
) -> dict[str, Any]:
    finding: str | None
    match: bool | str
    if reported == "NOT_REPORTED":
        match = "NOT_REPORTED"
        finding = None
    elif observed == "NOT_IN_ARTIFACT":
        match = "NOT_IN_ARTIFACT"
        finding = "NOT_IN_ARTIFACT"
    elif tolerance is not None and isinstance(reported, (int, float)) and isinstance(observed, (int, float)):
        match = abs(float(reported) - float(observed)) <= tolerance
        finding = None if match else "REPORTED_OBSERVED_MISMATCH"
    else:
        match = reported == observed
        finding = None if match else "REPORTED_OBSERVED_MISMATCH"
    return {
        "metric": name,
        "reported_value": reported,
        "observed_value": observed,
        "source_artifact": source,
        "match": match,
        "finding": finding,
    }


def build_reported_vs_observed(
    profile: dict[str, Any],
    brain_identity: dict[str, Any],
    artifact_summary: dict[str, Any],
    call_ledger: dict[str, Any],
    release_state: dict[str, Any],
) -> dict[str, Any]:
    observed = brain_identity.get("observed", {})
    metrics = [
        _metric("brain version", profile.get("brain_version"), observed.get("brain_version"), "brain identity"),
        _metric("model", profile.get("expected_model"), observed.get("model"), "compile manifest"),
        _metric(
            "reasoning effort",
            profile.get("expected_reasoning_effort"),
            observed.get("reasoning_effort"),
            "compile manifest",
        ),
        _metric(
            "source records",
            profile.get("expected_source_record_count"),
            observed.get("source_record_count"),
            "record population and compile manifest",
        ),
        _metric(
            "compiled claims",
            profile.get("expected_compiled_claim_count"),
            observed.get("compiled_claim_count"),
            "compiled claim ledger",
        ),
        _metric(
            "live OAuth calls",
            profile.get("expected_live_oauth_call_count"),
            call_ledger.get("total_live_calls"),
            "LLM traces",
        ),
        _metric("cache hits", 0, call_ledger.get("cache_hit_count"), "brain manifest and traces"),
        _metric(
            "memory snapshot ID",
            profile.get("expected_memory_snapshot_id"),
            observed.get("memory_snapshot_id"),
            "brain and memory manifests",
        ),
        _metric(
            "staging file count",
            "NOT_REPORTED",
            artifact_summary.get("artifact_file_count"),
            "artifact ledger",
        ),
        _metric(
            "staging byte size",
            "NOT_REPORTED",
            artifact_summary.get("artifact_total_bytes"),
            "artifact ledger",
        ),
        _metric(
            "staging GiB",
            profile.get("expected_stage_size_gib_reported"),
            artifact_summary.get("artifact_total_gib"),
            "artifact ledger",
            tolerance=0.001,
        ),
        _metric(
            "brain audit before",
            "PASS",
            "NOT_IN_ARTIFACT",
            "distinct before report not identified",
        ),
        _metric(
            "brain audit after",
            "PASS",
            "NOT_IN_ARTIFACT",
            "distinct after report not identified",
        ),
        _metric(
            "pytest count",
            profile.get("pytest_count_reported"),
            "NOT_IN_ARTIFACT",
            "operator report only",
        ),
        _metric(
            "old artifact deleted GiB",
            profile.get("old_artifact_deleted_gib_reported"),
            "NOT_IN_ARTIFACT",
            "deletion receipt absent",
        ),
        _metric(
            "active release state",
            profile.get("production_release_expected_active"),
            release_state.get("active_production_release"),
            "production/current.json",
        ),
    ]
    return {
        "schema_version": "nslab.external_audit_reported_vs_observed.v1",
        "metrics": metrics,
        "mismatch_count": sum(item["match"] is False for item in metrics),
        "not_in_artifact_count": sum(item["match"] == "NOT_IN_ARTIFACT" for item in metrics),
    }


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _pack_directory(source: Path, destination: Path) -> dict[str, Any]:
    files = [
        path
        for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix())
        if path.is_file() and path.name != PACK_FILES_NAME
    ]
    rows = [
        {
            "path": path.relative_to(source).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in files
    ]
    pack_files = {
        "schema_version": "nslab.external_audit_pack_files.v1",
        "file_count": len(rows),
        "files": rows,
    }
    write_json(source / PACK_FILES_NAME, pack_files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for path in [*files, source / PACK_FILES_NAME]:
            archive.write(path, path.relative_to(source).as_posix())
    return {
        "path": destination,
        "size_bytes": destination.stat().st_size,
        "sha256": file_sha256(destination),
        "file_count": len(rows) + 1,
    }


def _secret_patterns() -> tuple[bytes, ...]:
    return (
        b"-----BEGIN " + b"PRIVATE KEY-----",
        b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----",
        b"gh" + b"p_",
        b"sk-" + b"proj-",
        b"refresh_" + b"token",
        b"authorization:" + b" bearer ",
        b"c:" + b"\\users\\",
        b"/" + b"home/",
    )


def scan_pack_secrets(root: Path) -> dict[str, Any]:
    findings: list[str] = []
    forbidden_names = {".env", "auth.json", "cookies.json"}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if path.name.lower() in forbidden_names:
            findings.append(f"forbidden_file_name:{relative}")
        if path.suffix == ".zst":
            rows = iter_raw_zstd_jsonl(path)
            for row_number, row in enumerate(rows, start=1):
                lowered = canonical_json(row).encode("utf-8").lower()
                for pattern in _secret_patterns():
                    if pattern.lower() in lowered:
                        findings.append(f"secret_pattern:{relative}:{row_number}:{sha256_text(pattern.hex())[:12]}")
            continue
        if path.stat().st_size > 64 * 1024 * 1024:
            findings.append(f"unexpected_large_nonledger_file:{relative}")
            continue
        lowered = path.read_bytes().lower()
        for pattern in _secret_patterns():
            if pattern.lower() in lowered:
                findings.append(f"secret_pattern:{relative}:{sha256_text(pattern.hex())[:12]}")
    return {
        "schema_version": "nslab.external_audit_secret_scan.v1",
        "passed": not findings,
        "secret_finding_count": len(findings),
        "findings": findings,
        "raw_news_text_included": False,
        "absolute_user_paths_included": False,
    }


def _write_core_documents(
    core_root: Path,
    *,
    audit_id: str,
    target_lock: dict[str, Any],
    code_identity: dict[str, Any],
    environment: dict[str, Any],
    staging_identity: dict[str, Any],
    reported: dict[str, Any],
    artifact_summary: dict[str, Any],
    import_summary: dict[str, Any],
    record_summary: dict[str, Any],
    brain_identity: dict[str, Any],
    call_ledger: dict[str, Any],
    claim_summary: dict[str, Any],
    semantic_summary: dict[str, Any],
    category_summary: dict[str, Any],
    memory_summary: dict[str, Any],
    warehouse_summary: dict[str, Any],
    policy_summary: dict[str, Any],
    existing_audits: dict[str, Any],
    old_model_audit: dict[str, Any],
    current_brain_audit: dict[str, Any],
    release_state: dict[str, Any],
    read_only: dict[str, Any],
    profile: dict[str, Any],
    target: AuditTarget,
) -> None:
    json_documents = {
        "target_lock.json": target_lock,
        "code_identity.json": code_identity,
        "environment_identity.json": environment,
        "staging_identity.json": staging_identity,
        "reported_vs_observed.json": reported,
        "inventory/inventory_audit.json": import_summary,
        "import/import_audit.json": import_summary,
        "brain/brain_identity.json": brain_identity,
        "brain/llm_call_audit.json": call_ledger,
        "brain/category_artifact_audit.json": category_summary,
        "brain/compiled_claims_summary.json": claim_summary,
        "brain/semantic_influence_summary.json": semantic_summary,
        "memory/memory_snapshot_audit.json": memory_summary,
        "warehouse/warehouse_audit.json": warehouse_summary,
        "audits/lookahead_provenance_hardcoding.json": policy_summary,
        "audits/existing_brain_audits.json": existing_audits,
        "audits/old_model_absence.json": old_model_audit,
        "audits/current_read_only_brain_audit.json": current_brain_audit,
        "audits/production_release_state.json": release_state,
        "audits/read_only_parity.json": read_only,
        "summaries/artifact_population_summary.json": artifact_summary,
        "summaries/record_population_summary.json": record_summary,
        "summaries/claim_population_summary.json": claim_summary,
    }
    for relative, payload in json_documents.items():
        write_json(core_root / relative, payload)
    _copy_file(target.inventory_manifest_path, core_root / "inventory/production_import_inventory.json")
    _copy_file(target.import_receipt_path, core_root / "import/production_batch_import_receipt.json")
    _copy_file(
        target.record_artifact_manifest_path,
        core_root / "import/production_record_artifacts.json",
    )
    _copy_file(
        target.project_root / "memory" / "record_index" / "manifest.json",
        core_root / "import/record_index_manifest.json",
    )
    _copy_file(target.memory_manifest_path, core_root / "memory/memory_snapshot_manifest.json")
    verifier_source = Path(__file__).with_name("external_pack_standalone.py")
    _copy_file(verifier_source, core_root / "verifier/verify_core.py")
    _write_text(
        core_root / "verifier/verify_deep.py",
        """from __future__ import annotations

import argparse
import json
from pathlib import Path

from news_scalping_lab.audits.external_pack import verify_audit_pack

parser = argparse.ArgumentParser(description="Deep-verify an NSLAB external audit pack")
parser.add_argument("--pack", required=True, type=Path)
parser.add_argument("--ledgers", required=True, type=Path)
parser.add_argument("--repo-root", required=True, type=Path)
args = parser.parse_args()
result = verify_audit_pack(args.pack, args.ledgers, repo_root=args.repo_root, deep=True)
print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
raise SystemExit(0 if result["passed"] else 1)
""",
    )
    _write_text(core_root / "external_audit/work/audit_target_lock.md", _target_lock_markdown(target_lock))
    write_json(core_root / "external_audit/work/audit_target_lock.json", target_lock)
    limitations = [
        "STRUCTURALLY_ACCOUNTED does not mean every payload was shown to the map LLM.",
        "PAYLOAD_EXPOSED_TO_LLM does not prove final semantic influence.",
        "FINAL_CLAIM_REFERENCED does not prove sentence-level semantic validity.",
        "SEMANTICALLY_VALIDATED_BY_SAMPLE remains pending until an external seed is supplied.",
    ]
    finding_lines = [
        f"- Import/inventory findings: `{import_summary.get('finding_count')}`",
        f"- Brain identity findings: `{brain_identity.get('finding_count')}`",
        f"- Claim hard findings: `{claim_summary.get('hard_finding_count')}`",
        f"- Category findings: `{category_summary.get('finding_count')}`",
        f"- Memory findings: `{memory_summary.get('finding_count')}`",
        f"- Warehouse findings: `{warehouse_summary.get('finding_count')}`",
        f"- Boundary audit findings: `{policy_summary.get('finding_count')}`",
        f"- Current read-only brain audit findings: `{current_brain_audit.get('finding_count')}`",
        f"- Active old-model findings: `{0 if old_model_audit.get('passed') else 1}`",
    ]
    overview = "\n".join(
        (
            f"# NSLAB external audit overview: {audit_id}",
            "",
            f"Target brain: `{profile.get('brain_version')}`",
            f"Provider/model/effort: `{profile.get('expected_provider')}` / "
            f"`{profile.get('expected_model')}` / `{profile.get('expected_reasoning_effort')}`",
            f"Records: `{record_summary.get('record_count')}`",
            f"Compiled claims: `{claim_summary.get('claim_count')}`",
            f"Live OAuth calls: `{call_ledger.get('total_live_calls')}`",
            f"Artifact population root: `{artifact_summary.get('artifact_population_merkle_root')}`",
            "",
            "## Coverage labels",
            "",
            f"- `{semantic_summary.get('structural_coverage_result')}`",
            f"- `{semantic_summary.get('semantic_exposure_result')}`",
            f"- `{semantic_summary.get('claim_influence_result')}`",
            "- `SEMANTICALLY_VALIDATED_BY_SAMPLE`: AWAITING_EXTERNAL_AUDITOR_SEED",
            "",
            "## Interpretation limits",
            "",
            *(f"- {item}" for item in limitations),
            "",
            "## Findings",
            "",
            *finding_lines,
            "",
            "This is a read-only staging audit. It does not finalize or activate a production release.",
        )
    )
    _write_text(core_root / "audit_overview.md", overview)
    _write_text(
        core_root / "README_AUDIT.md",
        "\n".join(
            (
                "# Verify this audit pack",
                "",
                "Run the standard-library verifier with the two ZIP files:",
                "",
                "```bash",
                "python verifier/verify_core.py --pack <CORE-LITE.zip> --ledgers <CORE-LEDGERS.zip>",
                "```",
                "",
                "For a full local rescan in the installed project environment:",
                "",
                "```bash",
                "python verifier/verify_deep.py --pack <CORE-LITE.zip> --ledgers <CORE-LEDGERS.zip> "
                "--repo-root <REPOSITORY>",
                "```",
                "",
                "The pack excludes full news text, repaired bundles, credentials, and absolute user paths.",
            )
        ),
    )


def _core_manifest(
    *,
    audit_id: str,
    created_at: str,
    profile: dict[str, Any],
    code_identity: dict[str, Any],
    target_lock: dict[str, Any],
    artifact_summary: dict[str, Any],
    record_summary: dict[str, Any],
    claim_summary: dict[str, Any],
    semantic_summary: dict[str, Any],
    call_ledger: dict[str, Any],
    memory_summary: dict[str, Any],
    warehouse_summary: dict[str, Any],
    read_only: dict[str, Any],
    secret_scan: dict[str, Any],
    audit_findings: dict[str, Any],
) -> dict[str, Any]:
    family_roots = artifact_summary.get("family_roots", {})
    body = {
        "schema_version": AUDIT_CORE_SCHEMA,
        "audit_id": audit_id,
        "created_at": created_at,
        "brain_version": profile.get("brain_version"),
        "staging_build_commit": profile.get("staging_build_commit"),
        "audit_tool_commit": code_identity.get("audit_tool_commit"),
        "target_lock_sha256": target_lock.get("target_lock_sha256"),
        "provider": profile.get("expected_provider"),
        "model": profile.get("expected_model"),
        "reasoning_effort": profile.get("expected_reasoning_effort"),
        "counts": {
            "artifact_file_count": artifact_summary.get("artifact_file_count"),
            "artifact_total_bytes": artifact_summary.get("artifact_total_bytes"),
            "record_count": record_summary.get("record_count"),
            "training_eligible_count": record_summary.get("training_eligible", {}).get("true"),
            "claim_count": claim_summary.get("claim_count"),
            "live_call_count": call_ledger.get("total_live_calls"),
            "cache_hit_count": call_ledger.get("cache_hit_count"),
            "retry_count": call_ledger.get("retry_count"),
            "failure_count": call_ledger.get("failure_count"),
        },
        "roots": {
            "artifact_population_root": artifact_summary.get("artifact_population_merkle_root"),
            "record_population_root": record_summary.get("record_population_merkle_root"),
            "sorted_record_ids_root": record_summary.get("sorted_record_ids_root"),
            "record_id_envelope_root": record_summary.get("record_id_envelope_root"),
            "routing_metadata_root": record_summary.get("routing_metadata_root"),
            "claim_root": claim_summary.get("claim_population_merkle_root"),
            "brain_root": family_roots.get("brain"),
            "memory_root": family_roots.get("memory"),
            "warehouse_root": family_roots.get("warehouse"),
            "record_corpus_sha256": target_lock.get("record_corpus_sha256"),
            "record_artifact_root": target_lock.get("record_artifact_root"),
        },
        "memory_snapshot_id": memory_summary.get("snapshot_id"),
        "coverage": {
            "byte_accounted_count": semantic_summary.get("byte_accounted_count"),
            "group_accounted_count": semantic_summary.get("group_accounted_count"),
            "payload_exposed_to_map_count": semantic_summary.get("payload_exposed_to_map_count"),
            "payload_not_exposed_to_map_count": semantic_summary.get("payload_not_exposed_to_map_count"),
            "claim_referenced_record_count": semantic_summary.get("claim_referenced_record_count"),
            "claim_unreferenced_record_count": semantic_summary.get("claim_unreferenced_record_count"),
            "rare_reasoning_payload_not_exposed_count": semantic_summary.get(
                "rare_reasoning_payload_not_exposed_count"
            ),
            "structural_result": semantic_summary.get("structural_coverage_result"),
            "payload_result": semantic_summary.get("semantic_exposure_result"),
            "claim_reference_result": semantic_summary.get("claim_influence_result"),
        },
        "warehouse_closure_passed": warehouse_summary.get("passed"),
        "read_only_parity": read_only.get("passed"),
        "secret_finding_count": secret_scan.get("secret_finding_count"),
        "audit_findings": audit_findings,
        "sample_pack_status": "AWAITING_EXTERNAL_AUDITOR_SEED",
        "semantic_validation_status": "AWAITING_EXTERNAL_AUDITOR_SEED",
    }
    return {**body, "core_manifest_sha256": sha256_text(canonical_json(body))}


def _ledger_manifest(
    core: dict[str, Any],
    artifact_summary: dict[str, Any],
    record_summary: dict[str, Any],
    claim_summary: dict[str, Any],
    semantic_summary: dict[str, Any],
) -> dict[str, Any]:
    roots = core["roots"]
    return {
        "schema_version": "nslab.external_audit_ledger_pack_manifest.v1",
        "audit_id": core["audit_id"],
        "brain_version": core["brain_version"],
        "core_manifest_sha256": core["core_manifest_sha256"],
        "artifact_file_count": artifact_summary["artifact_file_count"],
        "artifact_population_merkle_root": artifact_summary["artifact_population_merkle_root"],
        "artifact_ledger_sha256": artifact_summary["artifact_ledger_sha256"],
        "record_count": record_summary["record_count"],
        "record_population_merkle_root": record_summary["record_population_merkle_root"],
        "record_ledger_sha256": record_summary["record_ledger_sha256"],
        "claim_count": claim_summary["claim_count"],
        "claim_population_merkle_root": claim_summary["claim_population_merkle_root"],
        "claim_ledger_sha256": claim_summary["claim_ledger_sha256"],
        "semantic_shard_count": len(semantic_summary.get("shards", [])),
        "semantic_ledger_sha256": semantic_summary.get("semantic_ledger_sha256"),
        "brain_root": roots["brain_root"],
        "memory_root": roots["memory_root"],
        "warehouse_root": roots["warehouse_root"],
    }


def _read_core_manifest_from_zip(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        value = json.loads(archive.read("audit_core_manifest.json"))
    if not isinstance(value, dict):
        raise ExternalAuditError("CORE_MANIFEST_INVALID", path.name)
    return value


def verify_audit_pack(
    pack: Path,
    ledgers: Path,
    *,
    repo_root: Path | None = None,
    deep: bool = False,
    stability_seconds: float = 5.0,
) -> dict[str, Any]:
    standalone = verify_standalone_pack(pack, ledgers)
    if not deep or not standalone.get("passed"):
        return {**standalone, "deep": False}
    if repo_root is None:
        raise ExternalAuditError("DEEP_VERIFY_REPO_REQUIRED", "--repo-root")
    core = _read_core_manifest_from_zip(pack)
    brain_version = core.get("brain_version")
    if not isinstance(brain_version, str):
        raise ExternalAuditError("CORE_BRAIN_VERSION_INVALID", pack.name)
    target = find_audit_target(repo_root, brain_version)
    assert_target_stable(target, seconds=stability_seconds)
    before = capture_quick_target_state(target)
    pointer_before = _pointer_state(target.repo_root)
    findings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="nslab-audit-deep-") as temporary:
        temp = Path(temporary)
        artifact = scan_artifact_population(target, temp / "artifacts.jsonl.zst")
        records, states = scan_record_population(target, temp / "records.jsonl.zst")
        claims, _ = scan_compiled_claims(target, temp / "claims.jsonl.zst", states)
    roots = core.get("roots", {})
    comparisons = {
        "artifact_population_root": artifact.get("artifact_population_merkle_root")
        == roots.get("artifact_population_root"),
        "record_population_root": records.get("record_population_merkle_root") == roots.get("record_population_root"),
        "sorted_record_ids_root": records.get("sorted_record_ids_root") == roots.get("sorted_record_ids_root"),
        "record_id_envelope_root": records.get("record_id_envelope_root") == roots.get("record_id_envelope_root"),
        "routing_metadata_root": records.get("routing_metadata_root") == roots.get("routing_metadata_root"),
        "claim_root": claims.get("claim_population_merkle_root") == roots.get("claim_root"),
        "brain_root": artifact.get("family_roots", {}).get("brain") == roots.get("brain_root"),
        "memory_root": artifact.get("family_roots", {}).get("memory") == roots.get("memory_root"),
        "warehouse_root": artifact.get("family_roots", {}).get("warehouse") == roots.get("warehouse_root"),
    }
    findings.extend(f"deep_root_mismatch:{name}" for name, passed in comparisons.items() if not passed)
    after = capture_quick_target_state(target)
    pointer_after = _pointer_state(target.repo_root)
    if before != after:
        findings.append("READ_ONLY_VIOLATION:target_quick_state")
    if pointer_before != pointer_after:
        findings.append("READ_ONLY_VIOLATION:production_pointer")
    return {
        **standalone,
        "schema_version": "nslab.external_audit_deep_verification.v1",
        "passed": standalone.get("passed") is True and not findings,
        "finding_count": int(standalone.get("finding_count", 0)) + len(findings),
        "findings": [*standalone.get("findings", []), *findings],
        "deep": True,
        "root_comparisons": comparisons,
        "read_only_parity": before == after and pointer_before == pointer_after,
    }


def export_audit_core(
    repo_root: Path,
    brain_version: str,
    output_dir: Path,
    *,
    stability_seconds: float = 5.0,
    run_deep_verifier: bool = True,
    write_commitment: bool = True,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    profile = load_audit_profile(repo_root, brain_version)
    target = find_audit_target(repo_root, brain_version, profile=profile)
    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to(target.project_root.resolve())
    except ValueError:
        pass
    else:
        raise ExternalAuditError("OUTPUT_INSIDE_TARGET", output_dir.name)
    created = datetime.now(UTC)
    audit_id = f"AUDIT-{created.strftime('%Y%m%dT%H%M%SZ')}-{sha256_text(brain_version)[:10].upper()}"
    audit_root = output_dir / audit_id
    if audit_root.exists():
        raise ExternalAuditError("AUDIT_OUTPUT_EXISTS", audit_id)
    ledger_root = audit_root / "core_ledgers"
    core_root = audit_root / "core_lite"
    work_root = audit_root / "work"
    audit_root.mkdir(parents=True)
    (ledger_root / "ledgers").mkdir(parents=True)
    core_root.mkdir()
    work_root.mkdir()

    stability = assert_target_stable(target, seconds=stability_seconds)
    before = capture_quick_target_state(target)
    pointer_before = _pointer_state(repo_root)
    artifact_ledger = ledger_root / "ledgers" / "all_artifacts.jsonl.zst"
    record_ledger = ledger_root / "ledgers" / "records.jsonl.zst"
    claim_ledger = ledger_root / "ledgers" / "compiled_claims.jsonl.zst"
    semantic_ledger = ledger_root / "ledgers" / "semantic_shards.jsonl.zst"

    artifact_summary = scan_artifact_population(target, artifact_ledger)
    artifact_index = artifact_index_from_ledger(artifact_ledger)
    target_lock = build_target_lock(target, profile, artifact_summary)
    write_json(work_root / "external_audit/work/audit_target_lock.json", target_lock)
    _write_text(
        work_root / "external_audit/work/audit_target_lock.md",
        _target_lock_markdown(target_lock),
    )
    record_summary, record_states = scan_record_population(target, record_ledger)
    claim_summary, referenced_records = scan_compiled_claims(target, claim_ledger, record_states)
    semantic_summary, call_ledger = scan_semantic_exposure(target, semantic_ledger, referenced_records)
    import_summary = audit_import_and_inventory(target, artifact_index=artifact_index)
    memory_seed = sha256_text(
        canonical_json(
            {
                "audit_id": audit_id,
                "target_lock_sha256": target_lock["target_lock_sha256"],
                "artifact_population_root": artifact_summary["artifact_population_merkle_root"],
            }
        )
    )
    memory_summary = audit_memory_snapshot(
        target,
        deterministic_seed=memory_seed,
        artifact_index=artifact_index,
    )
    warehouse_summary = audit_warehouse(target, record_summary)

    compile_manifest = read_json(target.compile_manifest_path)
    record_index_manifest = read_json(target.project_root / "memory" / "record_index" / "manifest.json")
    parity_sources = {
        "import_receipt": import_summary.get("imported_record_count"),
        "inventory": import_summary.get("inventory_ready_record_count"),
        "record_store_scan": record_summary.get("record_count"),
        "record_index": (
            record_index_manifest.get("record_count") if isinstance(record_index_manifest, dict) else None
        ),
        "warehouse_brain_records": warehouse_summary.get("brain_record_count"),
        "memory_source_records": memory_summary.get("source_record_count"),
        "memory_source_hashes": memory_summary.get("source_record_hash_count"),
        "brain_compile": compile_manifest.get("source_record_count") if isinstance(compile_manifest, dict) else None,
    }
    record_summary["population_parity"] = parity_sources
    record_summary["population_parity_passed"] = len(set(parity_sources.values())) == 1
    receipt_corpus = import_summary.get("record_corpus_sha256")
    record_summary["record_corpus_receipt_match"] = record_summary.get("record_id_envelope_root") == receipt_corpus

    brain_identity = audit_brain_identity(target, profile, claim_summary, call_ledger)
    brain_identity["observed"]["memory_snapshot_id"] = memory_summary.get("snapshot_id")
    category_summary = audit_brain_categories(target, profile)
    policy_summary = audit_policy_boundaries(
        target,
        record_summary,
        claim_summary,
        call_ledger,
        warehouse_summary,
    )
    existing_audits = audit_existing_brain_reports(target)
    old_model_audit = audit_old_model_absence(target)
    current_brain_audit = _project_brain_audit(audit_brain(target.project_root, deep=True, write_report=False))
    release_state = audit_release_state(repo_root, profile)
    code_identity = audit_code_identity(target, profile)
    environment = environment_identity()

    after_artifact_ledger = work_root / "after_artifacts.jsonl.zst"
    after_artifact_summary = scan_artifact_population(target, after_artifact_ledger)
    after_artifact_ledger.unlink()
    after = capture_quick_target_state(target)
    pointer_after = _pointer_state(repo_root)
    artifact_parity_fields = (
        "artifact_file_count",
        "artifact_total_bytes",
        "artifact_population_merkle_root",
        "family_roots",
    )
    artifact_parity = all(
        artifact_summary.get(key) == after_artifact_summary.get(key) for key in artifact_parity_fields
    )
    read_only = {
        "schema_version": "nslab.external_audit_read_only_parity.v1",
        "stability": stability,
        "before": before,
        "after": after,
        "artifact_population_before": {key: artifact_summary.get(key) for key in artifact_parity_fields},
        "artifact_population_after": {key: after_artifact_summary.get(key) for key in artifact_parity_fields},
        "production_pointer_before": pointer_before,
        "production_pointer_after": pointer_after,
        "quick_state_match": before == after,
        "artifact_population_match": artifact_parity,
        "production_pointer_match": pointer_before == pointer_after,
        "passed": before == after and artifact_parity and pointer_before == pointer_after,
    }
    if not read_only["passed"]:
        raise ExternalAuditError("READ_ONLY_VIOLATION", canonical_json(read_only)[-512:])

    staging_identity = {
        "schema_version": "nslab.external_audit_staging_identity.v1",
        "target_lock_sha256": target_lock["target_lock_sha256"],
        "brain_version": brain_version,
        "build_mode": brain_identity["observed"].get("build_mode"),
        "provider": brain_identity["observed"].get("provider"),
        "model": brain_identity["observed"].get("model"),
        "reasoning_effort": brain_identity["observed"].get("reasoning_effort"),
        "record_count": record_summary.get("record_count"),
        "claim_count": claim_summary.get("claim_count"),
        "memory_snapshot_id": memory_summary.get("snapshot_id"),
        "production_status": release_state.get("status"),
        "production_activation": release_state.get("production_activation"),
    }
    reported = build_reported_vs_observed(
        profile,
        brain_identity,
        artifact_summary,
        call_ledger,
        release_state,
    )
    _write_core_documents(
        core_root,
        audit_id=audit_id,
        target_lock=target_lock,
        code_identity=code_identity,
        environment=environment,
        staging_identity=staging_identity,
        reported=reported,
        artifact_summary=artifact_summary,
        import_summary=import_summary,
        record_summary=record_summary,
        brain_identity=brain_identity,
        call_ledger=call_ledger,
        claim_summary=claim_summary,
        semantic_summary=semantic_summary,
        category_summary=category_summary,
        memory_summary=memory_summary,
        warehouse_summary=warehouse_summary,
        policy_summary=policy_summary,
        existing_audits=existing_audits,
        old_model_audit=old_model_audit,
        current_brain_audit=current_brain_audit,
        release_state=release_state,
        read_only=read_only,
        profile=profile,
        target=target,
    )
    preliminary_secret_scan = scan_pack_secrets(core_root)
    if not preliminary_secret_scan["passed"]:
        raise ExternalAuditError("PACK_SECRET_VIOLATION", canonical_json(preliminary_secret_scan))
    write_json(core_root / "audits/secret_scan.json", preliminary_secret_scan)
    audit_findings = {
        "import_inventory": import_summary.get("findings", []),
        "brain_identity": brain_identity.get("findings", []),
        "compiled_claims": claim_summary.get("finding_counts", {}),
        "brain_categories": category_summary.get("findings", []),
        "memory": memory_summary.get("findings", []),
        "warehouse": warehouse_summary.get("findings", []),
        "policy_boundaries": policy_summary.get("findings", []),
        "current_brain_audit": current_brain_audit.get("finding_groups", {}),
        "old_model_active_identity": old_model_audit.get("active_brain_cache_snapshot_old_identity_files", []),
        "semantic_limitations": [
            semantic_summary.get("semantic_exposure_result"),
            semantic_summary.get("claim_influence_result"),
        ],
    }
    core = _core_manifest(
        audit_id=audit_id,
        created_at=created.isoformat(),
        profile=profile,
        code_identity=code_identity,
        target_lock=target_lock,
        artifact_summary=artifact_summary,
        record_summary=record_summary,
        claim_summary=claim_summary,
        semantic_summary=semantic_summary,
        call_ledger=call_ledger,
        memory_summary=memory_summary,
        warehouse_summary=warehouse_summary,
        read_only=read_only,
        secret_scan=preliminary_secret_scan,
        audit_findings=audit_findings,
    )
    write_json(core_root / "audit_core_manifest.json", core)
    write_json(work_root / "audit_core_manifest.json", core)
    final_secret_scan = scan_pack_secrets(core_root)
    if not final_secret_scan["passed"]:
        raise ExternalAuditError("PACK_SECRET_VIOLATION", canonical_json(final_secret_scan))

    ledger_manifest = _ledger_manifest(
        core,
        artifact_summary,
        record_summary,
        claim_summary,
        semantic_summary,
    )
    write_json(ledger_root / "ledger_pack_manifest.json", ledger_manifest)
    core_zip = audit_root / f"nslab_{brain_version}_audit_core_lite_{audit_id}.zip"
    ledger_zip = audit_root / f"nslab_{brain_version}_audit_core_ledgers_{audit_id}.zip"
    core_pack = _pack_directory(core_root, core_zip)
    ledger_pack = _pack_directory(ledger_root, ledger_zip)
    standalone = verify_audit_pack(core_zip, ledger_zip)
    if not standalone.get("passed"):
        raise ExternalAuditError("STANDALONE_VERIFIER_FAILED", canonical_json(standalone))
    deep_result = (
        verify_audit_pack(
            core_zip,
            ledger_zip,
            repo_root=repo_root,
            deep=True,
            stability_seconds=stability_seconds,
        )
        if run_deep_verifier
        else {"passed": "NOT_RUN", "deep": False}
    )
    if run_deep_verifier and not deep_result.get("passed"):
        raise ExternalAuditError("DEEP_VERIFIER_FAILED", canonical_json(deep_result))
    write_json(audit_root / "verification/standalone_verification.json", standalone)
    write_json(audit_root / "verification/deep_verification.json", deep_result)

    commitment = {
        "schema_version": "nslab.external_audit_public_commitment.v1",
        "audit_id": audit_id,
        "brain_version": brain_version,
        "staging_build_commit": profile.get("staging_build_commit"),
        "audit_tool_commit": code_identity.get("audit_tool_commit"),
        "core_lite_sha256": core_pack["sha256"],
        "core_ledgers_sha256": ledger_pack["sha256"],
        "core_manifest_sha256": core["core_manifest_sha256"],
        "artifact_population_root": artifact_summary["artifact_population_merkle_root"],
        "record_artifact_root": target_lock["record_artifact_root"],
        "record_corpus_sha256": target_lock["record_corpus_sha256"],
        "brain_root": target_lock["brain_root"],
        "claim_root": claim_summary["claim_population_merkle_root"],
        "memory_snapshot_id": memory_summary["snapshot_id"],
        "memory_root": target_lock["memory_root"],
        "warehouse_root": target_lock["warehouse_root"],
        "record_count": record_summary["record_count"],
        "claim_count": claim_summary["claim_count"],
        "created_at": created.isoformat(),
        "standalone_verifier_passed": standalone.get("passed"),
        "deep_verifier_passed": deep_result.get("passed"),
        "read_only_parity_passed": read_only["passed"],
        "secret_finding_count": final_secret_scan["secret_finding_count"],
        "sample_pack_status": "AWAITING_EXTERNAL_AUDITOR_SEED",
    }
    commitment_path = repo_root / "runs" / "external_audit" / "anchors" / f"{audit_id}.json"
    if write_commitment:
        write_json(commitment_path, commitment)
    write_json(audit_root / "public_commitment.json", commitment)
    return {
        "schema_version": "nslab.external_audit_export_result.v1",
        "audit_id": audit_id,
        "audit_root": str(audit_root),
        "target_root": target.repo_relative(target.project_root),
        "core_lite": {**core_pack, "path": str(core_zip)},
        "core_ledgers": {**ledger_pack, "path": str(ledger_zip)},
        "core_manifest": str(work_root / "audit_core_manifest.json"),
        "commitment_path": (commitment_path.relative_to(repo_root).as_posix() if write_commitment else "NOT_WRITTEN"),
        "commitment": commitment,
        "standalone_verification": standalone,
        "deep_verification": deep_result,
        "read_only_parity": read_only,
        "secret_scan": final_secret_scan,
        "coverage": core["coverage"],
        "sample_pack_status": "AWAITING_EXTERNAL_AUDITOR_SEED",
        "production_activation": release_state["production_activation"],
    }


def deterministic_stratified_sample(
    rows: list[dict[str, Any]],
    *,
    seed: str,
    count: int,
    id_field: str,
    stratum_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    if count <= 0 or not rows:
        return []
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        row_id = row.get(id_field)
        if not isinstance(row_id, str) or row_id in seen:
            continue
        seen.add(row_id)
        stratum = canonical_json([row.get(field, "UNKNOWN") for field in stratum_fields])
        buckets[stratum].append(row)
    ordered_strata = sorted(buckets, key=lambda item: sha256_text(seed + "\0stratum\0" + item))
    for items in buckets.values():
        items.sort(key=lambda row: (sha256_text(seed + "\0item\0" + str(row[id_field])), str(row[id_field])))
    selected: list[dict[str, Any]] = []
    offsets = dict.fromkeys(buckets, 0)
    while len(selected) < min(count, len(seen)):
        progress = False
        for stratum in ordered_strata:
            offset = offsets[stratum]
            items = buckets[stratum]
            if offset >= len(items):
                continue
            selected.append(items[offset])
            offsets[stratum] += 1
            progress = True
            if len(selected) == min(count, len(seen)):
                break
        if not progress:
            break
    return selected


def _redact_sample_value(value: Any, *, key: str = "") -> Any:
    normalized_key = key.lower()
    allowed_sensitive_metadata = {
        "commitment_sha256",
        "key_id",
        "sha256",
        "signature",
    }
    if normalized_key not in allowed_sensitive_metadata and any(
        token in normalized_key
        for token in ("api_key", "authorization", "cookie", "credential", "refresh_token", "secret")
    ):
        return "<REDACTED>"
    if isinstance(value, dict):
        return {str(item_key): _redact_sample_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sample_value(item, key=key) for item in value]
    if isinstance(value, str):
        redacted = re.sub(r"(?i)[A-Z]:\\Users\\[^\s\"']+", "<ABSOLUTE_PATH_REDACTED>", value)
        redacted = re.sub(r"/home/[^\s\"']+", "<ABSOLUTE_PATH_REDACTED>", redacted)
        for pattern in _secret_patterns():
            text_pattern = pattern.decode("utf-8", errors="ignore")
            if text_pattern and text_pattern.lower() in redacted.lower():
                return "<REDACTED>"
        return redacted
    return value


def _claim_reference_ids(target: AuditTarget) -> set[str]:
    referenced: set[str] = set()
    path = target.project_root / "brain" / "current" / "compiled_claims.jsonl"
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            referenced.update(_string_list(row.get("supporting_record_ids")))
            referenced.update(_string_list(row.get("contradicting_record_ids")))
    return referenced


def _sample_record_candidates(
    target: AuditTarget,
    referenced: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    candidates: list[dict[str, Any]] = []
    rare_record_ids: set[str] = set()
    for shard in _iter_record_model_shards(target, shard_size=LLM_FULL_RECORD_SHARD_SIZE):
        groups, representatives = _llm_evidence_groups(shard)
        exposed = {record.record_id for record in representatives}
        group_sizes = {str(row.get("group_id")): _safe_int(row.get("record_count")) for row in groups}
        group_semantics: dict[str, Counter[str]] = defaultdict(Counter)
        represented_semantics: dict[str, set[str]] = defaultdict(set)
        metadata: list[tuple[BrainRecordEnvelope, str, str]] = []
        for record in shard:
            group_id = _evidence_group_id(record)
            semantic = sha256_text(canonical_json(_compact_payload_for_llm_prompt(record.payload)))
            group_semantics[group_id][semantic] += 1
            if record.record_id in exposed:
                represented_semantics[group_id].add(semantic)
            metadata.append((record, group_id, semantic))
        for record, group_id, semantic in metadata:
            routing = record_routing_metadata(record)
            rare = (
                routing.routing_disposition == "REASONING"
                and routing.label_quality == "verified"
                and group_semantics[group_id][semantic] <= 3
                and semantic not in represented_semantics[group_id]
            )
            if rare:
                rare_record_ids.add(record.record_id)
            candidates.append(
                {
                    "record_id": record.record_id,
                    "year": record.trade_date.year,
                    "record_type": record.record_type,
                    "training_eligible": record.training_eligible,
                    "routing_disposition": routing.routing_disposition,
                    "evidence_polarity": routing.evidence_polarity,
                    "label_quality": routing.label_quality,
                    "confidence": record.confidence_label or "UNKNOWN",
                    "evidence_phase": record.evidence_phase,
                    "status": record.status or "UNKNOWN",
                    "payload_exposed": record.record_id in exposed,
                    "claim_referenced": record.record_id in referenced,
                    "rare_payload": rare,
                    "evidence_group_size": "large" if group_sizes.get(group_id, 0) >= 1000 else "small",
                }
            )
    return candidates, rare_record_ids


def _selected_raw_records(target: AuditTarget, selected_ids: set[str]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for record in _read_record_models(target):
        if record.record_id in selected_ids:
            selected.append(_redact_sample_value(record.model_dump(mode="json")))
    selected.sort(key=lambda row: str(row.get("record_id")))
    return selected


def _sample_claims(target: AuditTarget, *, seed: str, count: int) -> list[dict[str, Any]]:
    path = target.project_root / "brain" / "current" / "compiled_claims.jsonl"
    rows: list[dict[str, Any]] = []
    raw_by_id: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict) or not isinstance(raw.get("claim_id"), str):
                continue
            claim_id = str(raw["claim_id"])
            provenance = raw.get("provenance")
            source_type = provenance.get("source_type") if isinstance(provenance, dict) else None
            origin = (
                "DETERMINISTIC_RECORD_CLAIM"
                if source_type == "brain_record"
                else "LLM_CATEGORY_SYNTHESIS"
                if source_type == "llm_category_synthesis"
                else "LLM_REVIEW_ADJUSTED"
                if source_type == "llm_review_adjusted"
                else "OTHER"
                if isinstance(source_type, str) and source_type
                else "UNKNOWN"
            )
            rows.append(
                {
                    "claim_id": claim_id,
                    "category": raw.get("category", "UNKNOWN"),
                    "status": raw.get("status", "UNKNOWN"),
                    "origin": origin,
                }
            )
            raw_by_id[claim_id] = raw
    chosen = deterministic_stratified_sample(
        rows,
        seed=seed,
        count=count,
        id_field="claim_id",
        stratum_fields=("category", "status", "origin"),
    )
    return [_redact_sample_value(raw_by_id[str(row["claim_id"])]) for row in chosen]


def _sample_json_files(
    paths: list[Path],
    *,
    seed: str,
    count: int,
    id_key: str,
    date_key: str | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    raw_by_id: dict[str, dict[str, Any]] = {}
    for path in paths:
        value = read_json(path)
        if not isinstance(value, dict):
            continue
        item_id = value.get(id_key)
        if not isinstance(item_id, str):
            item_id = path.stem
        date_value = str(value.get(date_key) or "UNKNOWN") if date_key else "UNKNOWN"
        candidates.append({"sample_id": item_id, "year": date_value[:4]})
        raw_by_id[item_id] = value
    chosen = deterministic_stratified_sample(
        candidates,
        seed=seed,
        count=count,
        id_field="sample_id",
        stratum_fields=("year",),
    )
    return [_redact_sample_value(raw_by_id[str(row["sample_id"])]) for row in chosen]


def _sample_jsonl(
    path: Path,
    *,
    seed: str,
    count: int,
    id_key: str,
    stratum_key: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    raw_by_id: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                continue
            item_id = str(value.get(id_key) or f"row-{index}")
            candidates.append({"sample_id": item_id, "stratum": value.get(stratum_key, "UNKNOWN")})
            raw_by_id[item_id] = value
    chosen = deterministic_stratified_sample(
        candidates,
        seed=seed,
        count=count,
        id_field="sample_id",
        stratum_fields=("stratum",),
    )
    return [_redact_sample_value(raw_by_id[str(row["sample_id"])]) for row in chosen]


_SAMPLE_RECORD_STRATUM_FIELDS = (
    "year",
    "record_type",
    "training_eligible",
    "routing_disposition",
    "evidence_polarity",
    "label_quality",
    "confidence",
    "evidence_phase",
    "status",
    "payload_exposed",
    "claim_referenced",
    "rare_payload",
    "evidence_group_size",
)


def _episode_sample_paths(project_root: Path) -> list[Path]:
    """Return legacy episodes and current nested bundle envelopes deterministically."""
    episode_root = project_root / "research" / "episodes"
    candidates = [
        *episode_root.glob("*.json"),
        *episode_root.glob("*/bundle_envelope.json"),
    ]
    return sorted(candidates, key=lambda path: path.relative_to(episode_root).as_posix())


def _retrieval_trace_sample_paths(project_root: Path) -> list[Path]:
    """Return completed daily retrieval traces, excluding LLM build traces."""
    candidates: list[Path] = []
    for path in project_root.rglob("adaptive_retrieval_trace.json"):
        raw = read_json(path)
        if isinstance(raw, dict) and str(raw.get("schema_version", "")).startswith("nslab.adaptive_retrieval_trace."):
            candidates.append(path)
    for path in project_root.rglob("*.final.json"):
        raw = read_json(path)
        if isinstance(raw, dict) and raw.get("schema_version") == "nslab.runtime_retrieval_trace.v1":
            candidates.append(path)
    return sorted(candidates, key=lambda path: path.relative_to(project_root).as_posix())


def _sample_selection_metadata(
    candidates: list[dict[str, Any]],
    primary: list[dict[str, Any]],
    rare: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(row["record_id"]): row for row in candidates}
    primary_ids = {str(row["record_id"]) for row in primary}
    rare_ids = {str(row["record_id"]) for row in rare}
    selected: list[dict[str, Any]] = []
    for record_id in sorted(primary_ids | rare_ids):
        metadata = dict(by_id[record_id])
        metadata["selection_roles"] = [
            role
            for role, included in (
                ("PRIMARY_STRATIFIED", record_id in primary_ids),
                ("RARE_REASONING", record_id in rare_ids),
            )
            if included
        ]
        selected.append(metadata)
    return selected


def _sample_strata_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    def label(value: Any) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        if value is None or value == "":
            return "unknown"
        return str(value).casefold()

    return {
        field: dict(sorted(Counter(label(row.get(field)) for row in rows).items()))
        for field in _SAMPLE_RECORD_STRATUM_FIELDS
    }


def export_audit_sample(
    repo_root: Path,
    core_manifest_path: Path,
    external_seed: str,
    output_dir: Path,
) -> dict[str, Any]:
    core = read_json(core_manifest_path)
    if not isinstance(core, dict) or core.get("schema_version") != AUDIT_CORE_SCHEMA:
        raise ExternalAuditError("CORE_MANIFEST_INVALID", core_manifest_path.name)
    declared_hash = core.get("core_manifest_sha256")
    body = {key: value for key, value in core.items() if key != "core_manifest_sha256"}
    if declared_hash != sha256_text(canonical_json(body)):
        raise ExternalAuditError("CORE_MANIFEST_HASH_MISMATCH", core_manifest_path.name)
    brain_version = core.get("brain_version")
    if not isinstance(brain_version, str):
        raise ExternalAuditError("CORE_BRAIN_VERSION_INVALID", core_manifest_path.name)
    target = find_audit_target(repo_root, brain_version)
    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to(target.project_root.resolve())
    except ValueError:
        pass
    else:
        raise ExternalAuditError("OUTPUT_INSIDE_TARGET", output_dir.name)
    roots = core.get("roots", {})
    selection_key = sha256_text(
        external_seed
        + str(declared_hash)
        + str(core.get("staging_build_commit"))
        + str(roots.get("record_artifact_root"))
        + str(roots.get("brain_root"))
    )
    seed_sha8 = sha256_text(external_seed)[:8]
    sample_id = f"SAMPLE-{seed_sha8.upper()}-{selection_key[:12].upper()}"
    sample_root = output_dir / sample_id
    if sample_root.exists():
        raise ExternalAuditError("SAMPLE_OUTPUT_EXISTS", sample_id)
    sample_root.mkdir(parents=True)
    before = capture_quick_target_state(target)
    pointer_before = _pointer_state(target.repo_root)
    referenced = _claim_reference_ids(target)
    candidates, rare_ids = _sample_record_candidates(target, referenced)
    chosen = deterministic_stratified_sample(
        candidates,
        seed=selection_key,
        count=768,
        id_field="record_id",
        stratum_fields=_SAMPLE_RECORD_STRATUM_FIELDS,
    )
    rare_rows = [{"record_id": record_id, "rare": True} for record_id in rare_ids]
    chosen_rare = deterministic_stratified_sample(
        rare_rows,
        seed=selection_key + "rare",
        count=128,
        id_field="record_id",
        stratum_fields=("rare",),
    )
    selected_ids = {str(row["record_id"]) for row in [*chosen, *chosen_rare]}
    selection_metadata = _sample_selection_metadata(candidates, chosen, chosen_rare)
    records = _selected_raw_records(target, selected_ids)
    claims = _sample_claims(target, seed=selection_key + "claims", count=192)
    episode_paths = _episode_sample_paths(target.project_root)
    episodes = _sample_json_files(
        episode_paths,
        seed=selection_key + "episodes",
        count=32,
        id_key="episode_id",
        date_key="trade_date",
    )
    memory_manifest = read_json(target.memory_manifest_path)
    if not isinstance(memory_manifest, dict):
        raise ExternalAuditError("MEMORY_MANIFEST_INVALID", target.memory_manifest_path.name)
    cells_ref = memory_manifest.get("cell_entries")
    cells_relative = cells_ref.get("artifact_path") if isinstance(cells_ref, dict) else None
    if not isinstance(cells_relative, str):
        raise ExternalAuditError("MEMORY_CELL_ARTIFACT_MISSING", brain_version)
    cells = _sample_jsonl(
        target.project_root / cells_relative,
        seed=selection_key + "cells",
        count=96,
        id_key="cell_id",
        stratum_key="reasoning_member_count",
    )
    retrieval_trace_paths = _retrieval_trace_sample_paths(target.project_root)
    traces = _sample_json_files(
        retrieval_trace_paths,
        seed=selection_key + "traces",
        count=96,
        id_key="trace_id",
    )
    company_memories = _sample_json_files(
        sorted((target.project_root / "memory" / "company_memory").glob("*.json")),
        seed=selection_key + "company",
        count=96,
        id_key="ticker",
    )
    sample_payloads = {
        "records.json": records,
        "record_selection_metadata.json": selection_metadata,
        "compiled_claims.json": claims,
        "episodes.json": episodes,
        "memory_cells.json": cells,
        "retrieval_traces.json": traces,
        "company_memories.json": company_memories,
    }
    for name, payload in sample_payloads.items():
        write_json(sample_root / "samples" / name, payload)
    primary_record_ids = sorted(str(row["record_id"]) for row in chosen)
    rare_record_ids = sorted(str(row["record_id"]) for row in chosen_rare)
    selection_role_counts = Counter(role for row in selection_metadata for role in row["selection_roles"])
    sample_tool_commit = _git_output(repo_root, "rev-parse", "HEAD")
    sample_manifest = {
        "schema_version": AUDIT_SAMPLE_SCHEMA,
        "sample_id": sample_id,
        "brain_version": brain_version,
        "core_manifest_sha256": declared_hash,
        "core_audit_tool_commit": core.get("audit_tool_commit"),
        "sample_tool_commit": sample_tool_commit,
        "external_seed_sha256": sha256_text(external_seed),
        "selection_key_sha256": selection_key,
        "selection_algorithm": "sha256_stratified_round_robin.v1",
        "record_count": len(records),
        "primary_record_count": len(chosen),
        "rare_record_count": len(chosen_rare),
        "episode_count": len(episodes),
        "episode_source_status": "PRESENT" if episode_paths else "NOT_IN_ARTIFACT",
        "claim_count": len(claims),
        "memory_cell_count": len(cells),
        "retrieval_trace_count": len(traces),
        "retrieval_trace_status": ("PRESENT" if retrieval_trace_paths else "NOT_IN_ARTIFACT"),
        "company_memory_count": len(company_memories),
        "selected_record_ids": sorted(selected_ids),
        "selected_primary_record_ids": primary_record_ids,
        "selected_rare_record_ids": rare_record_ids,
        "selected_record_metadata_root": sha256_text(canonical_json(selection_metadata)),
        "record_strata_counts": _sample_strata_counts(selection_metadata),
        "selection_role_counts": dict(sorted(selection_role_counts.items())),
        "redaction_policy": "credentials_and_absolute_user_paths_only.v1",
    }
    write_json(sample_root / "sample_manifest.json", sample_manifest)
    secret_scan = scan_pack_secrets(sample_root)
    if not secret_scan["passed"]:
        raise ExternalAuditError("SAMPLE_SECRET_VIOLATION", canonical_json(secret_scan))
    write_json(sample_root / "secret_scan.json", secret_scan)
    after = capture_quick_target_state(target)
    pointer_after = _pointer_state(target.repo_root)
    if before != after or pointer_before != pointer_after:
        raise ExternalAuditError("READ_ONLY_VIOLATION", sample_id)
    destination = output_dir / f"nslab_{brain_version}_audit_sample_{seed_sha8}.zip"
    pack = _pack_directory(sample_root, destination)
    return {
        "schema_version": "nslab.external_audit_sample_export_result.v1",
        "sample_id": sample_id,
        "path": str(destination),
        "size_bytes": pack["size_bytes"],
        "sha256": pack["sha256"],
        "selection_key_sha256": selection_key,
        "sample_tool_commit": sample_tool_commit,
        "counts": {
            "records": len(records),
            "episodes": len(episodes),
            "claims": len(claims),
            "memory_cells": len(cells),
            "retrieval_traces": len(traces),
            "company_memories": len(company_memories),
        },
        "episode_source_status": "PRESENT" if episode_paths else "NOT_IN_ARTIFACT",
        "retrieval_trace_status": ("PRESENT" if retrieval_trace_paths else "NOT_IN_ARTIFACT"),
        "read_only_parity": True,
        "secret_finding_count": 0,
    }
