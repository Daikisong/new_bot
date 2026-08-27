"""Standard-library verifier copied into external NSLAB audit packs."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import IO, Any

PACK_FILES_NAME = "PACK_FILES.json"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _zip_member_identity(archive: zipfile.ZipFile, path: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(path) as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _safe_zip_names(archive: zipfile.ZipFile) -> tuple[list[str], list[str]]:
    names = [item.filename for item in archive.infolist()]
    findings: list[str] = []
    if len(names) != len(set(names)):
        findings.append("duplicate_zip_path")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            findings.append(f"unsafe_zip_path:{name}")
    return names, findings


def _verify_file_manifest(archive: zipfile.ZipFile) -> tuple[dict[str, Any], list[str]]:
    names, findings = _safe_zip_names(archive)
    if PACK_FILES_NAME not in names:
        return {}, [*findings, "pack_files_manifest_missing"]
    try:
        manifest = json.loads(archive.read(PACK_FILES_NAME))
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return {}, [*findings, "pack_files_manifest_invalid"]
    rows = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(rows, list):
        return {}, [*findings, "pack_files_rows_invalid"]
    if manifest.get("file_count") != len(rows):
        findings.append("pack_files_count_mismatch")
    declared: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            findings.append("pack_file_row_invalid")
            continue
        path = row.get("path")
        digest = row.get("sha256")
        size = row.get("size_bytes")
        if not isinstance(path, str) or path in declared:
            findings.append("pack_file_path_invalid")
            continue
        declared.add(path)
        try:
            observed_size, observed_digest = _zip_member_identity(archive, path)
        except KeyError:
            findings.append(f"pack_file_missing:{path}")
            continue
        if observed_size != size:
            findings.append(f"pack_file_size_mismatch:{path}")
        if observed_digest != digest:
            findings.append(f"pack_file_hash_mismatch:{path}")
    if set(names) != declared | {PACK_FILES_NAME}:
        findings.append("pack_file_population_mismatch")
    return manifest if isinstance(manifest, dict) else {}, findings


def _read_raw_zstd_blocks(handle: IO[bytes]) -> Iterator[bytes]:
    if handle.read(4) != ZSTD_MAGIC:
        raise ValueError("zstd_magic_invalid")
    descriptor = handle.read(1)
    if descriptor != b"\x00":
        raise ValueError("zstd_frame_descriptor_unsupported")
    if len(handle.read(1)) != 1:
        raise ValueError("zstd_window_descriptor_missing")
    while True:
        header = handle.read(3)
        if len(header) != 3:
            raise ValueError("zstd_block_header_truncated")
        value = int.from_bytes(header, "little")
        last = bool(value & 1)
        block_type = (value >> 1) & 0x3
        block_size = value >> 3
        if block_type != 0:
            raise ValueError("zstd_non_raw_block_unsupported")
        block = handle.read(block_size)
        if len(block) != block_size:
            raise ValueError("zstd_raw_block_truncated")
        yield block
        if last:
            if handle.read(1):
                raise ValueError("zstd_trailing_bytes")
            return


def _iter_zstd_jsonl(handle: IO[bytes]) -> Iterator[dict[str, Any]]:
    buffer = b""
    for block in _read_raw_zstd_blocks(handle):
        buffer += block
        lines = buffer.split(b"\n")
        buffer = lines.pop()
        for line in lines:
            if line:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("jsonl_row_not_object")
                yield row
    if buffer:
        row = json.loads(buffer)
        if not isinstance(row, dict):
            raise ValueError("jsonl_row_not_object")
        yield row


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


def _ledger_root(rows: Iterator[dict[str, Any]]) -> tuple[int, str]:
    digests: list[str] = []
    count = 0
    for row in rows:
        digests.append(_sha256_bytes(_canonical_json(row).encode("utf-8")))
        count += 1
    return count, _merkle_root(digests)


def _record_ledger_roots(rows: Iterator[dict[str, Any]]) -> dict[str, Any]:
    digests: list[str] = []
    sorted_ids = hashlib.sha256()
    envelope_hashes: dict[str, str] = {}
    count = 0
    last_record_id = ""
    for row in rows:
        record_id = row.get("record_id")
        envelope = row.get("envelope_sha256")
        if not isinstance(record_id, str) or not isinstance(envelope, str):
            raise ValueError("record_ledger_identity_invalid")
        if record_id <= last_record_id:
            raise ValueError("record_ledger_order_invalid")
        last_record_id = record_id
        sorted_ids.update((record_id + "\n").encode())
        envelope_hashes[record_id] = envelope
        digests.append(_sha256_bytes(_canonical_json(row).encode("utf-8")))
        count += 1
    return {
        "count": count,
        "population_root": _merkle_root(digests),
        "sorted_record_ids_root": sorted_ids.hexdigest(),
        "record_id_envelope_root": _sha256_bytes(_canonical_json(envelope_hashes).encode("utf-8")),
    }


def _secret_findings(archive: zipfile.ZipFile) -> list[str]:
    patterns = (
        b"-----BEGIN " + b"PRIVATE KEY-----",
        b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----",
        b"gh" + b"p_",
        b"sk-" + b"proj-",
        b"refresh_" + b"token",
        b"authorization:" + b" bearer ",
        b"c:" + b"\\users\\",
        b"/" + b"home/",
    )
    findings: list[str] = []
    for item in archive.infolist():
        if item.filename.endswith(".zst"):
            try:
                with archive.open(item) as handle:
                    for row_number, row in enumerate(_iter_zstd_jsonl(handle), start=1):
                        lowered = _canonical_json(row).encode("utf-8").lower()
                        for pattern in patterns:
                            if pattern.lower() in lowered:
                                findings.append(
                                    f"secret_pattern:{item.filename}:{row_number}:"
                                    f"{_sha256_bytes(pattern)[:12]}"
                                )
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                findings.append(f"secret_scan_ledger_invalid:{item.filename}:{exc}")
            continue
        if item.file_size > 64 * 1024 * 1024:
            continue
        lowered = archive.read(item).lower()
        for pattern in patterns:
            if pattern.lower() in lowered:
                findings.append(f"secret_pattern:{item.filename}:{_sha256_bytes(pattern)[:12]}")
    return findings


def verify(core_lite: Path, core_ledgers: Path) -> dict[str, Any]:
    findings: list[str] = []
    with zipfile.ZipFile(core_lite) as core_archive:
        _, core_findings = _verify_file_manifest(core_archive)
        findings.extend(core_findings)
        findings.extend(_secret_findings(core_archive))
        try:
            core = json.loads(core_archive.read("audit_core_manifest.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            core = {}
            findings.append("audit_core_manifest_invalid")
        if isinstance(core, dict):
            declared = core.get("core_manifest_sha256")
            body = {key: value for key, value in core.items() if key != "core_manifest_sha256"}
            if declared != _sha256_bytes(_canonical_json(body).encode("utf-8")):
                findings.append("core_manifest_canonical_hash_mismatch")
        else:
            core = {}
            findings.append("audit_core_manifest_not_object")
    with zipfile.ZipFile(core_ledgers) as ledger_archive:
        _, ledger_findings = _verify_file_manifest(ledger_archive)
        findings.extend(ledger_findings)
        findings.extend(_secret_findings(ledger_archive))
        try:
            ledger_manifest = json.loads(ledger_archive.read("ledger_pack_manifest.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
            ledger_manifest = {}
            findings.append("ledger_pack_manifest_invalid")
        for name, count_key, root_key in (
            ("ledgers/all_artifacts.jsonl.zst", "artifact_file_count", "artifact_population_merkle_root"),
            ("ledgers/records.jsonl.zst", "record_count", "record_population_merkle_root"),
            ("ledgers/compiled_claims.jsonl.zst", "claim_count", "claim_population_merkle_root"),
        ):
            try:
                with ledger_archive.open(name) as ledger_handle:
                    count, root = _ledger_root(_iter_zstd_jsonl(ledger_handle))
            except (KeyError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                findings.append(f"ledger_invalid:{name}:{exc}")
                continue
            if count != ledger_manifest.get(count_key):
                findings.append(f"ledger_count_mismatch:{name}")
            if root != ledger_manifest.get(root_key):
                findings.append(f"ledger_root_mismatch:{name}")
        try:
            with ledger_archive.open("ledgers/records.jsonl.zst") as record_handle:
                record_roots = _record_ledger_roots(_iter_zstd_jsonl(record_handle))
        except (KeyError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            record_roots = {}
            findings.append(f"record_roots_invalid:{exc}")
        core_roots = core.get("roots") if isinstance(core, dict) else None
        if isinstance(core_roots, dict):
            if record_roots.get("sorted_record_ids_root") != core_roots.get("sorted_record_ids_root"):
                findings.append("record_sorted_ids_root_mismatch")
            if record_roots.get("record_id_envelope_root") != core_roots.get("record_id_envelope_root"):
                findings.append("record_envelope_root_mismatch")
        for name, hash_key in (
            ("ledgers/all_artifacts.jsonl.zst", "artifact_ledger_sha256"),
            ("ledgers/records.jsonl.zst", "record_ledger_sha256"),
            ("ledgers/compiled_claims.jsonl.zst", "claim_ledger_sha256"),
            ("ledgers/semantic_shards.jsonl.zst", "semantic_ledger_sha256"),
        ):
            try:
                _size, observed_hash = _zip_member_identity(ledger_archive, name)
            except KeyError:
                findings.append(f"ledger_missing:{name}")
                continue
            if observed_hash != ledger_manifest.get(hash_key):
                findings.append(f"ledger_file_hash_mismatch:{name}")
        parity = core.get("roots") if isinstance(core, dict) else None
        if isinstance(parity, dict) and isinstance(ledger_manifest, dict):
            pairs = (
                ("artifact_population_root", "artifact_population_merkle_root"),
                ("record_population_root", "record_population_merkle_root"),
                ("claim_root", "claim_population_merkle_root"),
                ("brain_root", "brain_root"),
                ("memory_root", "memory_root"),
                ("warehouse_root", "warehouse_root"),
            )
            for core_key, ledger_key in pairs:
                if parity.get(core_key) != ledger_manifest.get(ledger_key):
                    findings.append(f"core_ledger_root_mismatch:{core_key}")
    return {
        "schema_version": "nslab.external_audit_pack_verification.v1",
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings,
        "core_lite_sha256": _sha256_bytes(core_lite.read_bytes()),
        "core_ledgers_sha256": _sha256_bytes(core_ledgers.read_bytes()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify NSLAB CORE-LITE and CORE-LEDGERS packs")
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--ledgers", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.pack, args.ledgers)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
