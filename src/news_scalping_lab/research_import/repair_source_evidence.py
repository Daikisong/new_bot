"""Independent CSV evidence joins used by sequential bundle repair."""

from __future__ import annotations

import csv
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.utils import KST, sha256_bytes, sha256_text

NEWS_TIMESTAMP_REPAIR_RULE = "news_csv_timestamp_sha256_row_join.v1"


def rehydrate_news_source_timestamps(
    rows: list[dict[str, Any]],
    *,
    news_csv_root: Path | None,
    cutoff_at: datetime | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fill missing news timestamps only after an exact file and row-content join."""

    repaired = deepcopy(rows)
    if news_csv_root is None:
        return repaired, _timestamp_summary(0, 0, ["news_csv_root_missing"])

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in repaired:
        if not _is_news_row(row) or _published_at(row) is not None:
            continue
        input_file = _string(row.get("input_file"))
        input_sha256 = _string(row.get("input_sha256"))
        if input_file is None or input_sha256 is None:
            continue
        grouped[(input_file, input_sha256)].append(row)

    verified_count = 0
    failures: list[str] = []
    for (input_file, input_sha256), source_rows in sorted(grouped.items()):
        csv_path = _safe_csv_path(news_csv_root, input_file)
        if csv_path is None:
            failures.append(f"{input_file}:unsafe_or_missing_csv")
            continue
        loaded = _load_verified_csv(csv_path, expected_sha256=input_sha256)
        if loaded is None:
            failures.append(f"{input_file}:input_sha256_mismatch_or_unreadable")
            continue
        csv_rows, input_hash_mode = loaded
        evidence: list[tuple[dict[str, Any], str, str]] = []
        group_failures: list[str] = []
        for source_row in source_rows:
            joined = _join_source_row(source_row, csv_rows)
            if joined is None:
                group_failures.append(_source_key(source_row))
                continue
            published_at, content_sha256 = joined
            evidence.append((source_row, published_at, content_sha256))
        if group_failures:
            failures.extend(f"{input_file}:{key}:row_join_failed" for key in group_failures)
            continue
        for source_row, published_at, content_sha256 in evidence:
            source_row["published_at"] = published_at
            source_row["time_verified"] = True
            if cutoff_at is not None:
                source_row["available_before_cutoff"] = datetime.fromisoformat(published_at) <= cutoff_at
            source_row["timestamp_repair_provenance"] = {
                "rule_id": NEWS_TIMESTAMP_REPAIR_RULE,
                "input_file": input_file,
                "input_sha256": input_sha256,
                "input_hash_mode": input_hash_mode,
                "row_index": _row_index(source_row),
                "content_sha256": content_sha256,
                "published_at": published_at,
            }
            verified_count += 1
    return repaired, _timestamp_summary(len(grouped), verified_count, failures)


def audit_rehydrated_news_source_timestamps(
    source_rows: list[dict[str, Any]],
    repaired_rows: list[dict[str, Any]],
    *,
    news_csv_root: Path | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Recompute repair attestations and return safe source-time overrides."""

    repaired_by_id = {
        source_id: row
        for row in repaired_rows
        for source_id in [_string(row.get("source_id"))]
        if source_id is not None
    }
    csv_cache: dict[tuple[str, str], tuple[list[dict[str, str]], str] | None] = {}
    failures: list[str] = []
    overrides: dict[str, str] = {}
    verified_count = 0
    changed_count = 0
    for source in source_rows:
        source_id = _string(source.get("source_id"))
        if source_id is None or not _is_news_row(source):
            continue
        repaired = repaired_by_id.get(source_id)
        if repaired is None:
            continue
        source_published = _published_at(source)
        repaired_published = _published_at(repaired)
        if source_published == repaired_published:
            continue
        changed_count += 1
        provenance = repaired.get("timestamp_repair_provenance")
        if source_published is not None or not isinstance(provenance, dict):
            failures.append(f"{source_id}:unattested_timestamp_change")
            continue
        input_file = _string(provenance.get("input_file"))
        input_sha256 = _string(provenance.get("input_sha256"))
        if (
            provenance.get("rule_id") != NEWS_TIMESTAMP_REPAIR_RULE
            or input_file != _string(source.get("input_file"))
            or input_sha256 != _string(source.get("input_sha256"))
        ):
            failures.append(f"{source_id}:attestation_identity_mismatch")
            continue
        cache_key = (input_file or "", input_sha256 or "")
        if cache_key not in csv_cache:
            csv_path = _safe_csv_path(news_csv_root, input_file) if news_csv_root is not None else None
            csv_cache[cache_key] = (
                _load_verified_csv(csv_path, expected_sha256=input_sha256)
                if csv_path is not None and input_sha256 is not None
                else None
            )
        loaded = csv_cache[cache_key]
        if loaded is None:
            failures.append(f"{source_id}:csv_evidence_unavailable")
            continue
        csv_rows, input_hash_mode = loaded
        joined = _join_source_row(source, csv_rows)
        if joined is None:
            failures.append(f"{source_id}:row_join_failed")
            continue
        expected_published, expected_content_sha256 = joined
        expected_attestation = {
            "rule_id": NEWS_TIMESTAMP_REPAIR_RULE,
            "input_file": input_file,
            "input_sha256": input_sha256,
            "input_hash_mode": input_hash_mode,
            "row_index": _row_index(source),
            "content_sha256": expected_content_sha256,
            "published_at": expected_published,
        }
        if repaired_published != expected_published or provenance != expected_attestation:
            failures.append(f"{source_id}:timestamp_or_attestation_mismatch")
            continue
        overrides[source_id] = expected_published
        verified_count += 1
    return {
        "timestamp_repair_changed_count": changed_count,
        "timestamp_repair_verified_count": verified_count,
        "timestamp_repair_failure_count": len(failures),
        "timestamp_repair_failure_samples": failures[:50],
    }, overrides


def _load_verified_csv(
    path: Path,
    *,
    expected_sha256: str,
) -> tuple[list[dict[str, str]], str] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    raw_hash = sha256_bytes(raw)
    lf_hash = sha256_bytes(raw.replace(b"\r\n", b"\n"))
    if expected_sha256 == raw_hash:
        hash_mode = "RAW_BYTES"
    elif expected_sha256 == lf_hash:
        hash_mode = "CRLF_TO_LF"
    else:
        return None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"date", "time", "title", "body"}
            if not required.issubset(set(reader.fieldnames or ())):
                return None
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error):
        return None
    return rows, hash_mode


def _join_source_row(
    source_row: dict[str, Any],
    csv_rows: list[dict[str, str]],
) -> tuple[str, str] | None:
    row_index = _row_index(source_row)
    if row_index is None or row_index < 1 or row_index > len(csv_rows):
        return None
    csv_row = csv_rows[row_index - 1]
    if _normalized_text(source_row.get("title")) != _normalized_text(csv_row.get("title")):
        return None
    for source_field, csv_field in (("page", "page"), ("source_row_number", "row")):
        source_value = _string(source_row.get(source_field))
        csv_value = _string(csv_row.get(csv_field))
        if source_value is not None and csv_value is not None and source_value != csv_value:
            return None
    content_sha256 = sha256_text(
        f"{_normalized_text(csv_row.get('title'))}\n{_normalized_text(csv_row.get('body'))}"
    )
    if content_sha256 != _string(source_row.get("content_sha256")):
        return None
    raw_date = _string(csv_row.get("date"))
    raw_time = _string(csv_row.get("time"))
    if raw_date is None or raw_time is None:
        return None
    try:
        published = datetime.fromisoformat(f"{raw_date}T{raw_time}").replace(tzinfo=KST)
    except ValueError:
        return None
    return published.isoformat(), content_sha256


def _safe_csv_path(root: Path | None, input_file: str | None) -> Path | None:
    if root is None or input_file is None or Path(input_file).name != input_file:
        return None
    resolved_root = root.resolve()
    candidate = (resolved_root / input_file).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _timestamp_summary(group_count: int, verified_count: int, failures: list[str]) -> dict[str, Any]:
    return {
        "csv_group_count": group_count,
        "timestamp_repair_verified_count": verified_count,
        "timestamp_repair_failure_count": len(failures),
        "timestamp_repair_failure_samples": failures[:50],
    }


def _is_news_row(row: dict[str, Any]) -> bool:
    return str(row.get("source_type") or "").lower() in {"news_csv_row", "news_row"}


def _published_at(row: dict[str, Any]) -> str | None:
    return _string(row.get("published_at_kst") or row.get("published_at"))


def _row_index(row: dict[str, Any]) -> int | None:
    value = row.get("row_index")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _source_key(row: dict[str, Any]) -> str:
    return _string(row.get("source_id") or row.get("row_id")) or "UNKNOWN_SOURCE"


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
