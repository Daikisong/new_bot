"""Independent CSV evidence joins used by sequential bundle repair."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.utils import KST, canonical_json, sha256_bytes, sha256_text

NEWS_TIMESTAMP_REPAIR_RULE = "news_csv_timestamp_sha256_row_join.v2"


def rehydrate_news_source_timestamps(
    rows: list[dict[str, Any]],
    *,
    news_csv_root: Path | None,
    cutoff_at: datetime | None,
    declared_input_file: str | None = None,
    declared_input_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verify news timestamps only after an exact file and row-content join."""

    repaired = deepcopy(rows)
    if news_csv_root is None:
        return repaired, _timestamp_summary(0, 0, ["news_csv_root_missing"])

    declared_csv_names = _declared_csv_names_by_sha(repaired)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in repaired:
        if not _is_news_row(row) or (
            _published_at(row) is not None and row.get("time_verified") is True
        ):
            continue
        input_sha256 = _input_sha256(row) or declared_input_sha256
        input_file = _input_file(row) or declared_input_file
        if input_file is None and input_sha256 is not None:
            input_file = declared_csv_names.get(input_sha256)
        if input_file is None or input_sha256 is None:
            continue
        grouped[(input_file, input_sha256)].append(row)

    verified_count = 0
    failures: list[str] = []
    for (input_file, input_sha256), source_rows in sorted(grouped.items()):
        loaded = _resolve_verified_csv(
            news_csv_root,
            input_file=input_file,
            expected_sha256=input_sha256,
        )
        if loaded is None:
            failures.append(
                f"{input_file}:input_sha256_not_found_or_csv_unreadable"
            )
            continue
        csv_rows, input_hash_mode, evidence_file, resolution = loaded
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
                "evidence_file": evidence_file,
                "evidence_resolution": resolution,
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
    cutoff_at: datetime | None = None,
    declared_input_file: str | None = None,
    declared_input_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Recompute repair attestations and return safe source-time overrides."""

    repaired_by_id = {
        source_id: row
        for row in repaired_rows
        for source_id in [
            _string(row.get("source_id") or row.get("source_row_id") or row.get("row_id"))
        ]
        if source_id is not None
    }
    declared_csv_names = _declared_csv_names_by_sha(source_rows)
    csv_cache: dict[
        tuple[str, str],
        tuple[list[dict[str, str]], str, str, str] | None,
    ] = {}
    failures: list[str] = []
    overrides: dict[str, str] = {}
    verified_count = 0
    changed_count = 0
    for source in source_rows:
        # Older ledgers call the same stable source identity source_row_id or
        # row_id.  Repair may materialize source_id, so audit both sides by the
        # shared legacy identity instead of requiring the modern field upfront.
        source_id = _string(
            source.get("source_id") or source.get("source_row_id") or source.get("row_id")
        )
        if source_id is None or not _is_news_row(source):
            continue
        repaired = repaired_by_id.get(source_id)
        if repaired is None:
            continue
        source_published = _published_at(source)
        repaired_published = _published_at(repaired)
        time_verification_added = (
            source.get("time_verified") is not True
            and repaired.get("time_verified") is True
        )
        if source_published == repaired_published and not time_verification_added:
            continue
        changed_count += 1
        provenance = repaired.get("timestamp_repair_provenance")
        if not isinstance(provenance, dict):
            failures.append(f"{source_id}:unattested_timestamp_change")
            continue
        input_sha256 = _string(provenance.get("input_sha256"))
        input_file = _string(provenance.get("input_file"))
        source_input_sha256 = _input_sha256(source) or declared_input_sha256
        expected_input_file = _input_file(source) or declared_input_file
        if expected_input_file is None and source_input_sha256 is not None:
            expected_input_file = declared_csv_names.get(source_input_sha256)
        if (
            provenance.get("rule_id") != NEWS_TIMESTAMP_REPAIR_RULE
            or input_file != expected_input_file
            or input_sha256 != source_input_sha256
        ):
            failures.append(f"{source_id}:attestation_identity_mismatch")
            continue
        cache_key = (input_file or "", input_sha256 or "")
        if cache_key not in csv_cache:
            csv_cache[cache_key] = (
                _resolve_verified_csv(
                    news_csv_root,
                    input_file=input_file,
                    expected_sha256=input_sha256,
                )
                if news_csv_root is not None
                and input_file is not None
                and input_sha256 is not None
                else None
            )
        loaded = csv_cache[cache_key]
        if loaded is None:
            failures.append(f"{source_id}:csv_evidence_unavailable")
            continue
        csv_rows, input_hash_mode, evidence_file, resolution = loaded
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
            "evidence_file": evidence_file,
            "evidence_resolution": resolution,
            "row_index": _row_index(source),
            "content_sha256": expected_content_sha256,
            "published_at": expected_published,
        }
        if (
            repaired_published != expected_published
            or (
                source_published is not None
                and source_published != expected_published
            )
            or repaired.get("time_verified") is not True
            or provenance != expected_attestation
        ):
            failures.append(f"{source_id}:timestamp_or_attestation_mismatch")
            continue
        if cutoff_at is not None:
            expected_before_cutoff = (
                datetime.fromisoformat(expected_published) <= cutoff_at
            )
            if (
                repaired.get("available_before_cutoff")
                is not expected_before_cutoff
            ):
                failures.append(f"{source_id}:cutoff_attestation_mismatch")
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


def _resolve_verified_csv(
    root: Path,
    *,
    input_file: str,
    expected_sha256: str,
) -> tuple[list[dict[str, str]], str, str, str] | None:
    """Resolve the declared CSV by safe basename or by its content identity."""

    if Path(input_file).name != input_file:
        return None
    resolved_root = root.resolve()
    exact = _safe_csv_path(resolved_root, input_file)
    if exact is not None:
        loaded = _load_verified_csv(exact, expected_sha256=expected_sha256)
        if loaded is not None:
            rows, hash_mode = loaded
            return (
                rows,
                hash_mode,
                exact.relative_to(resolved_root).as_posix(),
                "DECLARED_FILENAME",
            )

    matches: list[tuple[Path, list[dict[str, str]], str]] = []
    for candidate in sorted(resolved_root.rglob("*.csv")):
        try:
            resolved_candidate = candidate.resolve()
            resolved_candidate.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        loaded = _load_verified_csv(
            resolved_candidate,
            expected_sha256=expected_sha256,
        )
        if loaded is None:
            continue
        rows, hash_mode = loaded
        matches.append((resolved_candidate, rows, hash_mode))
    if not matches:
        return None
    canonical_digests = {
        sha256_text(canonical_json(rows)) for _, rows, _ in matches
    }
    if len(canonical_digests) != 1:
        return None
    selected, rows, hash_mode = matches[0]
    return (
        rows,
        hash_mode,
        selected.relative_to(resolved_root).as_posix(),
        "CONTENT_SHA256",
    )


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
    for source_fields, csv_field in (
        (("page",), "page"),
        (("source_row_number", "page_row", "provider_row", "input_row"), "row"),
    ):
        source_value = next(
            (
                value
                for field in source_fields
                for value in [_identity_text(source_row.get(field))]
                if value is not None
            ),
            None,
        )
        csv_value = _identity_text(csv_row.get(csv_field))
        if source_value is not None and csv_value is not None and source_value != csv_value:
            return None
    content_sha256 = sha256_text(
        f"{_normalized_text(csv_row.get('title'))}\n{_normalized_text(csv_row.get('body'))}"
    )
    # Some complete-population ledgers hash stripped title/body with a NUL
    # separator. The separator is unambiguous and every other row identity
    # anchor is still checked independently before this digest is accepted.
    legacy_nul_content_sha256 = sha256_text(
        f"{str(csv_row.get('title') or '').strip()}\0"
        f"{str(csv_row.get('body') or '').strip()}"
    )
    raw_row_sha256 = sha256_text(canonical_json(csv_row))
    # Recovered v2 ledgers preserve the original CSV header order and join
    # each raw cell with ASCII Unit Separator before hashing.
    legacy_unit_separator_row_sha256 = sha256_text(
        "\x1f".join(str(value or "") for value in csv_row.values())
    )
    declared_content_sha256 = _string(source_row.get("content_sha256"))
    declared_raw_row_sha256 = _string(
        source_row.get("raw_row_sha256") or source_row.get("row_sha256")
    )
    if (
        declared_content_sha256 is None
        and declared_raw_row_sha256 is None
        and (
            "body" not in source_row
            or _normalized_text(source_row.get("body"))
            != _normalized_text(csv_row.get("body"))
        )
    ):
        return None
    if (
        declared_content_sha256 is not None
        and declared_content_sha256
        not in {content_sha256, legacy_nul_content_sha256}
    ):
        return None
    if (
        declared_raw_row_sha256 is not None
        and declared_raw_row_sha256
        not in {raw_row_sha256, legacy_unit_separator_row_sha256}
    ):
        return None
    raw_date = _string(csv_row.get("date"))
    raw_time = _string(csv_row.get("time"))
    if raw_date is None or raw_time is None:
        return None
    try:
        published = datetime.fromisoformat(f"{raw_date}T{raw_time}").replace(tzinfo=KST)
    except ValueError:
        return None
    existing_published = _published_at(source_row)
    if existing_published is not None:
        try:
            parsed_existing = datetime.fromisoformat(existing_published)
        except ValueError:
            return None
        if parsed_existing.tzinfo is None or parsed_existing != published:
            return None
    return published.isoformat(), declared_content_sha256 or content_sha256


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
    if str(row.get("source_type") or "").lower() in {"news_csv_row", "news_row"}:
        return True
    input_file = _input_file(row)
    return bool(
        input_file is not None
        and Path(input_file).name == input_file
        and input_file.lower().endswith(".csv")
        and _input_sha256(row) is not None
        and _row_index(row) is not None
        and "title" in row
        and "body" in row
        and _published_at(row) is not None
    )


def _published_at(row: dict[str, Any]) -> str | None:
    return _string(row.get("published_at_kst") or row.get("published_at"))


def _row_index(row: dict[str, Any]) -> int | None:
    # csv_ordinal is the one-based position in the complete CSV. Do not use
    # source_row_number here: legacy bundles use it for the row within a page.
    for field in (
        "row_index",
        "csv_row_index",
        "csv_position",
        "csv_ordinal",
        "csv_row_number",
        "source_row_index",
        "input_row_number",
    ):
        value = row.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    row_id_match = re.fullmatch(r"NEWS-0*(\d+)", str(row.get("row_id") or ""))
    source_id_match = re.fullmatch(
        r"SRC-NEWS-0*(\d+)",
        str(row.get("source_id") or ""),
    )
    if (
        row_id_match is not None
        and source_id_match is not None
        and row_id_match.group(1) == source_id_match.group(1)
    ):
        return int(row_id_match.group(1))
    for field in ("source_row",):
        value = row.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _declared_csv_names_by_sha(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Return unambiguous CSV basenames declared by bundle-level sources."""

    candidates: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if str(row.get("source_type") or "").lower() != "news_csv":
            continue
        sha256 = _string(row.get("sha256") or row.get("input_sha256"))
        path = _string(row.get("path") or row.get("input_file"))
        if sha256 is None or path is None:
            continue
        basename = Path(path).name
        if basename.lower().endswith(".csv"):
            candidates[sha256].add(basename)
    return {
        sha256: next(iter(names))
        for sha256, names in candidates.items()
        if len(names) == 1
    }


def _source_key(row: dict[str, Any]) -> str:
    return _string(row.get("source_id") or row.get("row_id")) or "UNKNOWN_SOURCE"


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _input_file(row: dict[str, Any]) -> str | None:
    return _string(row.get("input_file") or row.get("source_file"))


def _input_sha256(row: dict[str, Any]) -> str | None:
    return _string(row.get("input_sha256") or row.get("source_sha256"))


def _identity_text(value: Any) -> str | None:
    """Normalize exact row identity anchors without accepting lossy values."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return _string(value)
