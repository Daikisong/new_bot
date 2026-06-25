"""Lookahead leak audits."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import cast

from news_scalping_lab.utils import (
    file_sha256,
    is_available_as_of,
    parse_datetime,
    read_json,
    sha256_text,
)

SESSION_PACK_FILES = (
    "system_instructions.md",
    "research_brain.md",
    "memory_cases.md",
    "current_news.md",
    "company_memory.md",
    "market_context.md",
)


def audit_lookahead(root: Path, *, trade_date: date | None = None) -> dict[str, object]:
    findings: list[str] = []
    manifest_paths = [
        *sorted((root / "runs" / "manifests").glob("*.json")),
        *sorted((root / "session_packs").glob("*/manifest.json")),
    ]
    accepted_episode_available_from = _accepted_episode_available_from(root)
    for path in manifest_paths:
        manifest_name = _manifest_display_name(root, path)
        manifest = read_json(path)
        manifest_trade_date = _manifest_trade_date(manifest, fallback=trade_date)
        manifest_cutoff_at = _manifest_cutoff_at(manifest)
        manifest_as_of = _manifest_as_of(manifest)
        if manifest_trade_date is None:
            findings.append(f"{manifest_name}: missing trade_date")
        if manifest_cutoff_at is None:
            findings.append(f"{manifest_name}: missing cutoff_at")
        if _requires_as_of(manifest) and manifest_as_of is None:
            findings.append(f"{manifest_name}: missing as_of")
        if (
            manifest_as_of is not None
            and manifest_cutoff_at is not None
            and manifest_as_of > manifest_cutoff_at
        ):
            findings.append(f"{manifest_name}: as_of is after cutoff_at")
        price_snapshot = manifest.get("price_snapshot", {})
        allowed = price_snapshot.get("allowed_through")
        if (
            manifest_trade_date is not None
            and allowed is not None
            and str(allowed) >= manifest_trade_date.isoformat()
        ):
            findings.append(f"{manifest_name}: price allowed_through is not before trade date")
        _check_retrieved_episode_availability(
            manifest_name,
            manifest,
            manifest_cutoff_at,
            accepted_episode_available_from,
            findings,
        )
        _check_context_files_for_future_episode_ids(
            root,
            manifest_name,
            manifest,
            manifest_cutoff_at,
            accepted_episode_available_from,
            findings,
        )
        _check_session_pack_temporal_memory_refs(
            root,
            manifest_name,
            manifest,
            manifest_cutoff_at,
            findings,
        )
        _check_session_pack_hashes(root, path, manifest_name, manifest, findings)
        if (
            manifest.get("mode") == "exhaustive"
            and manifest.get("accepted_episode_count") != manifest.get("swept_episode_count")
        ):
            findings.append(f"{manifest_name}: exhaustive coverage mismatch")
        _check_row_disposition(root, manifest_name, manifest, findings)
        _check_source_ledger(root, manifest_name, manifest, findings)
        _check_web_source_artifact(root, manifest_name, manifest, findings)
        _check_candidate_web_check_artifact(root, manifest_name, manifest, findings)
        _check_blind_seal(root, manifest_name, manifest, findings)
        _check_news_only_blind_protocol(manifest_name, manifest, findings)
    return {
        "passed": not findings,
        "findings": findings,
        "checked_manifests": len(manifest_paths),
    }


def _manifest_display_name(root: Path, path: Path) -> str:
    if path.parent == root / "runs" / "manifests":
        return path.name
    return path.relative_to(root).as_posix()


def _manifest_trade_date(manifest: dict[object, object], *, fallback: date | None) -> date | None:
    raw = manifest.get("trade_date")
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return fallback


def _manifest_cutoff_at(manifest: dict[object, object]) -> datetime | None:
    raw = manifest.get("cutoff_at")
    if isinstance(raw, str):
        try:
            return parse_datetime(raw)
        except ValueError:
            return None
    return None


def _manifest_as_of(manifest: dict[object, object]) -> datetime | None:
    raw = manifest.get("as_of")
    if isinstance(raw, str):
        try:
            return parse_datetime(raw)
        except ValueError:
            return None
    return None


def _requires_as_of(manifest: dict[object, object]) -> bool:
    return manifest.get("schema_version") in {
        "nslab.context_manifest.v1",
        "nslab.session_pack_manifest.v1",
    }


def _accepted_episode_available_from(root: Path) -> dict[str, datetime]:
    available_from: dict[str, datetime] = {}
    for path in sorted((root / "research" / "accepted").glob("*.json")):
        try:
            episode = read_json(path)
        except Exception:
            continue
        if not isinstance(episode, dict):
            continue
        episode_id = episode.get("episode_id")
        raw_available_from = episode.get("available_from")
        if not isinstance(episode_id, str) or not isinstance(raw_available_from, str):
            continue
        try:
            available_from[episode_id] = parse_datetime(raw_available_from)
        except ValueError:
            continue
    return available_from


def _check_retrieved_episode_availability(
    manifest_name: str,
    manifest: dict[object, object],
    manifest_cutoff_at: datetime | None,
    accepted_episode_available_from: dict[str, datetime],
    findings: list[str],
) -> None:
    if manifest_cutoff_at is None:
        return
    retrieved = _string_list(manifest.get("retrieved_episode_ids"))
    excluded = set(_string_list(manifest.get("excluded_retrieved_episode_ids")))
    for episode_id in retrieved:
        available_from = accepted_episode_available_from.get(episode_id)
        if available_from is not None and not is_available_as_of(available_from, manifest_cutoff_at):
            findings.append(f"{manifest_name}: future retrieved episode not excluded: {episode_id}")
        if episode_id in excluded:
            findings.append(f"{manifest_name}: episode is both retrieved and excluded: {episode_id}")


def _check_context_files_for_future_episode_ids(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    manifest_cutoff_at: datetime | None,
    accepted_episode_available_from: dict[str, datetime],
    findings: list[str],
) -> None:
    if manifest_cutoff_at is None:
        return
    future_episode_ids = [
        episode_id
        for episode_id, available_from in accepted_episode_available_from.items()
        if not is_available_as_of(available_from, manifest_cutoff_at)
    ]
    if not future_episode_ids:
        return
    for relative_path in [
        *_string_list(manifest.get("brain_files")),
        *_string_list(manifest.get("shard_brain_files")),
        *_string_list(manifest.get("memory_sweep_artifacts")),
    ]:
        path = root / relative_path
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for episode_id in future_episode_ids:
            if episode_id in text:
                findings.append(
                    f"{manifest_name}: context file contains future episode {episode_id}: "
                    f"{relative_path}"
                )


def _check_session_pack_temporal_memory_refs(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    manifest_cutoff_at: datetime | None,
    findings: list[str],
) -> None:
    if manifest_cutoff_at is None:
        return
    for relative_ref in _string_list(manifest.get("included_company_memory_files")):
        payload = _read_json_ref(root, manifest_name, relative_ref, findings)
        if payload is None:
            continue
        if not isinstance(payload, dict):
            findings.append(f"{manifest_name}: included company memory must be object: {relative_ref}")
            continue
        _check_payload_available_as_of(
            manifest_name,
            relative_ref,
            payload,
            manifest_cutoff_at,
            label="company memory",
            timestamp_fields=("known_at",),
            findings=findings,
        )
    for relative_ref in _string_list(manifest.get("included_market_context_files")):
        payload = _read_json_ref(root, manifest_name, relative_ref, findings)
        if payload is None:
            continue
        if not isinstance(payload, dict):
            findings.append(f"{manifest_name}: included market context must be object: {relative_ref}")
            continue
        _check_payload_available_as_of(
            manifest_name,
            relative_ref,
            payload,
            manifest_cutoff_at,
            label="market_context memory",
            timestamp_fields=("available_from", "known_at"),
            findings=findings,
        )


def _read_json_ref(
    root: Path,
    manifest_name: str,
    relative_ref: str,
    findings: list[str],
) -> object | None:
    relative_path, marker = _split_artifact_ref(relative_ref)
    path = root / relative_path
    if not path.exists():
        findings.append(f"{manifest_name}: referenced memory artifact missing: {relative_ref}")
        return None
    try:
        if marker is None:
            return cast(object, json.loads(path.read_text(encoding="utf-8-sig")))
        if marker.startswith("L"):
            line_number = int(marker[1:])
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            if line_number < 1 or line_number > len(lines):
                findings.append(f"{manifest_name}: referenced memory line missing: {relative_ref}")
                return None
            return cast(object, json.loads(lines[line_number - 1]))
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            index = int(marker)
            return cast(object, payload[index])
    except (ValueError, IndexError, json.JSONDecodeError):
        findings.append(f"{manifest_name}: referenced memory artifact invalid JSON: {relative_ref}")
        return None
    findings.append(f"{manifest_name}: unsupported memory artifact ref: {relative_ref}")
    return None


def _split_artifact_ref(relative_ref: str) -> tuple[str, str | None]:
    if "#" not in relative_ref:
        return relative_ref, None
    relative_path, marker = relative_ref.rsplit("#", 1)
    return relative_path, marker


def _check_payload_available_as_of(
    manifest_name: str,
    relative_ref: str,
    payload: dict[object, object],
    manifest_cutoff_at: datetime,
    *,
    label: str,
    timestamp_fields: tuple[str, ...],
    findings: list[str],
) -> None:
    for field in timestamp_fields:
        raw_value = payload.get(field)
        if not isinstance(raw_value, str):
            continue
        try:
            timestamp = parse_datetime(raw_value)
        except ValueError:
            findings.append(f"{manifest_name}: included {label} invalid {field}: {relative_ref}")
            return
        if not is_available_as_of(timestamp, manifest_cutoff_at):
            findings.append(f"{manifest_name}: included future {label}: {relative_ref}")
        return
    fields = ", ".join(timestamp_fields)
    findings.append(f"{manifest_name}: included {label} missing temporal field {fields}: {relative_ref}")


def _check_session_pack_hashes(
    root: Path,
    manifest_path: Path,
    manifest_name: str,
    manifest: dict[object, object],
    findings: list[str],
) -> None:
    if manifest_path.parent.parent != root / "session_packs":
        return
    pack_hashes = manifest.get("pack_file_hashes")
    if pack_hashes is None and manifest.get("pack_sha256") is None:
        return
    if not isinstance(pack_hashes, dict):
        findings.append(f"{manifest_name}: pack_file_hashes is invalid")
        return
    observed_hashes: dict[str, str] = {}
    for file_name in SESSION_PACK_FILES:
        expected_hash = pack_hashes.get(file_name)
        if not isinstance(expected_hash, str):
            findings.append(f"{manifest_name}: missing pack_file_hashes: {file_name}")
            continue
        path = manifest_path.parent / file_name
        if not path.is_file():
            findings.append(f"{manifest_name}: session pack file missing: {file_name}")
            continue
        observed_hash = file_sha256(path)
        observed_hashes[file_name] = observed_hash
        if observed_hash != expected_hash:
            findings.append(f"{manifest_name}: pack_file_hashes mismatch: {file_name}")
    extra_hashes = sorted(str(key) for key in pack_hashes if key not in SESSION_PACK_FILES)
    if extra_hashes:
        findings.append(f"{manifest_name}: unlisted pack_file_hashes: {', '.join(extra_hashes)}")
    expected_pack_sha = manifest.get("pack_sha256")
    if not isinstance(expected_pack_sha, str):
        findings.append(f"{manifest_name}: missing pack_sha256")
        return
    if set(observed_hashes) != set(SESSION_PACK_FILES):
        return
    observed_pack_sha = sha256_text(
        "\n".join(observed_hashes[file_name] for file_name in SESSION_PACK_FILES)
    )
    if observed_pack_sha != expected_pack_sha:
        findings.append(f"{manifest_name}: pack_sha256 mismatch")


def _check_news_only_blind_protocol(
    manifest_name: str,
    manifest: dict[object, object],
    findings: list[str],
) -> None:
    mode = manifest.get("blind_context_mode")
    if mode == "NEWS_ONLY_STRICT":
        expected_zero_fields = [
            "blind_web_search_call_count",
            "blind_price_repository_access_count",
            "blind_current_price_access_count",
        ]
    elif mode == "CUTOFF_SAFE_WEB_BLIND":
        expected_zero_fields = [
            "blind_price_repository_access_count",
            "blind_current_price_access_count",
        ]
    else:
        return
    for field in expected_zero_fields:
        if manifest.get(field) != 0:
            findings.append(f"{manifest_name}: {field} must be 0 in {mode}")
    if manifest.get("no_d_outcome_exposed") is not True:
        findings.append(f"{manifest_name}: no_d_outcome_exposed must be true")


def _check_row_disposition(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    findings: list[str],
) -> None:
    relative_path = manifest.get("row_disposition_artifact")
    if not isinstance(relative_path, str):
        return
    path = root / relative_path
    if not path.exists():
        findings.append(f"{manifest_name}: row_disposition_artifact missing: {relative_path}")
        return
    text = path.read_text(encoding="utf-8")
    expected_sha = manifest.get("row_disposition_sha256")
    if isinstance(expected_sha, str) and sha256_text(text) != expected_sha:
        findings.append(f"{manifest_name}: row_disposition_sha256 mismatch")
    manifest_cutoff_at = _manifest_cutoff_at(manifest)
    manifest_news_window_start_at = _manifest_news_window_start_at(manifest)
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            findings.append(f"{manifest_name}: row_disposition:{line_number} invalid JSON")
            continue
        if not isinstance(row, dict):
            findings.append(f"{manifest_name}: row_disposition:{line_number} must be object")
            continue
        if "title" in row or "body" in row:
            findings.append(
                f"{manifest_name}: row_disposition:{line_number} must not duplicate title/body"
            )
        if manifest_news_window_start_at is not None and manifest_cutoff_at is not None:
            _check_row_disposition_news_window_contract(
                manifest_name,
                line_number,
                row,
                manifest_news_window_start_at,
                manifest_cutoff_at,
                findings,
            )
        rows.append(row)
    row_numbers = [row.get("row_number") for row in rows]
    if len(row_numbers) != len(set(row_numbers)):
        findings.append(f"{manifest_name}: row_disposition duplicate row_number")
    summary = manifest.get("row_disposition_summary")
    if isinstance(summary, dict):
        total_rows = summary.get("total_rows")
        if isinstance(total_rows, int) and total_rows != len(rows):
            findings.append(f"{manifest_name}: row_disposition total_rows mismatch")
    coverage_ratio = manifest.get("row_disposition_coverage_ratio")
    if isinstance(coverage_ratio, (int, float)) and float(coverage_ratio) != 1.0:
        findings.append(f"{manifest_name}: row_disposition coverage ratio must be 1.0")


def _manifest_news_window_start_at(manifest: dict[object, object]) -> datetime | None:
    raw = manifest.get("news_window_start_at")
    if isinstance(raw, str):
        try:
            return parse_datetime(raw)
        except ValueError:
            return None
    return None


def _check_row_disposition_news_window_contract(
    manifest_name: str,
    line_number: int,
    row: dict[str, object],
    manifest_news_window_start_at: datetime,
    manifest_cutoff_at: datetime,
    findings: list[str],
) -> None:
    raw_published_at = row.get("published_at")
    if not isinstance(raw_published_at, str):
        findings.append(f"{manifest_name}: row_disposition:{line_number} missing published_at")
        return
    try:
        published_at = parse_datetime(raw_published_at)
    except ValueError:
        findings.append(f"{manifest_name}: row_disposition:{line_number} invalid published_at")
        return

    raw_window_start = row.get("news_window_start_at")
    raw_cutoff_at = row.get("cutoff_at")
    if not isinstance(raw_window_start, str):
        findings.append(
            f"{manifest_name}: row_disposition:{line_number} missing news_window_start_at"
        )
    else:
        try:
            row_window_start = parse_datetime(raw_window_start)
        except ValueError:
            findings.append(
                f"{manifest_name}: row_disposition:{line_number} invalid news_window_start_at"
            )
        else:
            if row_window_start != manifest_news_window_start_at:
                findings.append(
                    f"{manifest_name}: row_disposition:{line_number} news_window_start_at mismatch"
                )
    if not isinstance(raw_cutoff_at, str):
        findings.append(f"{manifest_name}: row_disposition:{line_number} missing cutoff_at")
    else:
        try:
            row_cutoff_at = parse_datetime(raw_cutoff_at)
        except ValueError:
            findings.append(f"{manifest_name}: row_disposition:{line_number} invalid cutoff_at")
        else:
            if row_cutoff_at != manifest_cutoff_at:
                findings.append(f"{manifest_name}: row_disposition:{line_number} cutoff_at mismatch")

    collected_at_present = row.get("collected_at_present")
    if not isinstance(collected_at_present, bool):
        findings.append(
            f"{manifest_name}: row_disposition:{line_number} missing collected_at_present"
        )
    raw_collected_at = row.get("collected_at")
    if collected_at_present is True:
        if not isinstance(raw_collected_at, str):
            findings.append(f"{manifest_name}: row_disposition:{line_number} missing collected_at")
        else:
            try:
                parse_datetime(raw_collected_at)
            except ValueError:
                findings.append(
                    f"{manifest_name}: row_disposition:{line_number} invalid collected_at"
                )

    expected_within = manifest_news_window_start_at <= published_at <= manifest_cutoff_at
    if row.get("within_news_window") is not expected_within:
        findings.append(
            f"{manifest_name}: row_disposition:{line_number} within_news_window mismatch"
        )
    expected_disposition = (
        "INCLUDED_IN_NEWS_WINDOW"
        if expected_within
        else "EXCLUDED_AFTER_CUTOFF"
        if published_at > manifest_cutoff_at
        else "EXCLUDED_BEFORE_WINDOW"
    )
    if row.get("disposition") != expected_disposition:
        findings.append(f"{manifest_name}: row_disposition:{line_number} disposition mismatch")
    expected_eligible = expected_within
    if row.get("eligible_for_blind_evidence") is not expected_eligible:
        findings.append(
            f"{manifest_name}: row_disposition:{line_number} eligible_for_blind_evidence mismatch"
        )


def _check_source_ledger(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    findings: list[str],
) -> None:
    relative_path = manifest.get("source_ledger_artifact")
    if not isinstance(relative_path, str):
        return
    path = root / relative_path
    if not path.exists():
        findings.append(f"{manifest_name}: source_ledger_artifact missing: {relative_path}")
        return
    text = path.read_text(encoding="utf-8")
    expected_sha = manifest.get("source_ledger_sha256")
    if isinstance(expected_sha, str) and sha256_text(text) != expected_sha:
        findings.append(f"{manifest_name}: source_ledger_sha256 mismatch")
    rows: list[dict[str, object]] = []
    required = {
        "source_id",
        "source_type",
        "title",
        "publisher",
        "url",
        "published_at",
        "retrieved_at",
        "time_verified",
        "available_before_cutoff",
        "usage_phase",
        "input_row_ids",
        "content_sha256",
        "notes",
    }
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            findings.append(f"{manifest_name}: source_ledger:{line_number} invalid JSON")
            continue
        if not isinstance(row, dict):
            findings.append(f"{manifest_name}: source_ledger:{line_number} must be object")
            continue
        missing = sorted(required - set(row))
        if missing:
            findings.append(
                f"{manifest_name}: source_ledger:{line_number} missing fields: "
                f"{', '.join(missing)}"
            )
        if "body" in row or "content" in row:
            findings.append(
                f"{manifest_name}: source_ledger:{line_number} must not duplicate body/content"
            )
        usage_phase = row.get("usage_phase")
        if usage_phase not in {"BLIND", "OUTCOME", "POSTMORTEM"}:
            findings.append(f"{manifest_name}: source_ledger:{line_number} invalid usage_phase")
        if usage_phase == "BLIND" and row.get("available_before_cutoff") is not True:
            findings.append(
                f"{manifest_name}: source_ledger:{line_number} BLIND source after cutoff"
            )
        rows.append(row)
    source_ids = [row.get("source_id") for row in rows if isinstance(row.get("source_id"), str)]
    if len(source_ids) != len(set(source_ids)):
        findings.append(f"{manifest_name}: source_ledger duplicate source_id")
    entry_count = manifest.get("source_ledger_entry_count")
    if isinstance(entry_count, int) and entry_count != len(rows):
        findings.append(f"{manifest_name}: source_ledger entry_count mismatch")


def _check_web_source_artifact(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    findings: list[str],
) -> None:
    web_sources = set(_string_list(manifest.get("web_sources")))
    excluded = set(_string_list(manifest.get("excluded_web_source_ids")))
    if web_sources & excluded:
        findings.append(f"{manifest_name}: web source is both included and excluded")
    relative_path = manifest.get("web_source_artifact")
    if not isinstance(relative_path, str):
        if web_sources:
            findings.append(f"{manifest_name}: web_source_artifact missing")
        _check_excluded_web_source_artifact(root, manifest_name, manifest, web_sources, findings)
        return
    path = root / relative_path
    if not path.exists():
        findings.append(f"{manifest_name}: web_source_artifact missing: {relative_path}")
        return
    text = path.read_text(encoding="utf-8")
    expected_sha = manifest.get("web_source_sha256")
    if isinstance(expected_sha, str) and sha256_text(text) != expected_sha:
        findings.append(f"{manifest_name}: web_source_sha256 mismatch")
    manifest_cutoff_at = _manifest_cutoff_at(manifest)
    row_source_ids: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            findings.append(f"{manifest_name}: web_source:{line_number} invalid JSON")
            continue
        if not isinstance(row, dict):
            findings.append(f"{manifest_name}: web_source:{line_number} must be object")
            continue
        source_id = row.get("source_id")
        if isinstance(source_id, str):
            row_source_ids.add(source_id)
        if row.get("available_before_cutoff") is not True or row.get("time_verified") is not True:
            findings.append(f"{manifest_name}: web_source:{line_number} is not cutoff verified")
        raw_published_at = row.get("published_at")
        if isinstance(raw_published_at, str) and manifest_cutoff_at is not None:
            try:
                published_at = parse_datetime(raw_published_at)
            except ValueError:
                findings.append(f"{manifest_name}: web_source:{line_number} invalid published_at")
                continue
            if not is_available_as_of(published_at, manifest_cutoff_at):
                findings.append(f"{manifest_name}: web_source:{line_number} after cutoff")
    if web_sources and web_sources != row_source_ids:
        findings.append(f"{manifest_name}: web_sources do not match web_source_artifact")
    _check_excluded_web_source_artifact(root, manifest_name, manifest, web_sources, findings)


def _check_excluded_web_source_artifact(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    web_sources: set[str],
    findings: list[str],
) -> None:
    excluded = set(_string_list(manifest.get("excluded_web_source_ids")))
    relative_path = manifest.get("excluded_web_source_artifact")
    if not isinstance(relative_path, str):
        if excluded:
            findings.append(f"{manifest_name}: excluded_web_source_artifact missing")
        return
    path = root / relative_path
    if not path.exists():
        findings.append(f"{manifest_name}: excluded_web_source_artifact missing: {relative_path}")
        return
    text = path.read_text(encoding="utf-8")
    expected_sha = manifest.get("excluded_web_source_sha256")
    if isinstance(expected_sha, str) and sha256_text(text) != expected_sha:
        findings.append(f"{manifest_name}: excluded_web_source_sha256 mismatch")
    row_source_ids: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            findings.append(f"{manifest_name}: excluded_web_source:{line_number} invalid JSON")
            continue
        if not isinstance(row, dict):
            findings.append(f"{manifest_name}: excluded_web_source:{line_number} must be object")
            continue
        source_id = row.get("source_id")
        if isinstance(source_id, str):
            row_source_ids.add(source_id)
            if source_id in web_sources:
                findings.append(
                    f"{manifest_name}: excluded_web_source:{line_number} is also included"
                )
        if not isinstance(row.get("exclusion_reason"), str):
            findings.append(
                f"{manifest_name}: excluded_web_source:{line_number} missing exclusion_reason"
            )
        if row.get("available_before_cutoff") is True and row.get("time_verified") is True:
            findings.append(
                f"{manifest_name}: excluded_web_source:{line_number} is cutoff verified"
            )
    if excluded and excluded != row_source_ids:
        findings.append(
            f"{manifest_name}: excluded_web_source_ids do not match excluded artifact"
        )
    entry_count = manifest.get("excluded_web_source_count")
    if isinstance(entry_count, int) and entry_count != len(row_source_ids):
        findings.append(f"{manifest_name}: excluded_web_source_count mismatch")


def _check_candidate_web_check_artifact(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    findings: list[str],
) -> None:
    source_ids = set(_string_list(manifest.get("candidate_web_source_ids")))
    relative_path = manifest.get("candidate_web_check_artifact")
    if not isinstance(relative_path, str):
        if source_ids:
            findings.append(f"{manifest_name}: candidate_web_check_artifact missing")
        _check_excluded_candidate_web_check_artifact(root, manifest_name, manifest, findings)
        return
    path = root / relative_path
    if not path.exists():
        findings.append(
            f"{manifest_name}: candidate_web_check_artifact missing: {relative_path}"
        )
        return
    text = path.read_text(encoding="utf-8")
    expected_sha = manifest.get("candidate_web_check_sha256")
    if isinstance(expected_sha, str) and sha256_text(text) != expected_sha:
        findings.append(f"{manifest_name}: candidate_web_check_sha256 mismatch")
    manifest_cutoff_at = _manifest_cutoff_at(manifest)
    row_source_ids: set[str] = set()
    row_count = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        row_count += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            findings.append(f"{manifest_name}: candidate_web_check:{line_number} invalid JSON")
            continue
        if not isinstance(row, dict):
            findings.append(f"{manifest_name}: candidate_web_check:{line_number} must be object")
            continue
        source_id = row.get("source_id")
        if isinstance(source_id, str):
            row_source_ids.add(source_id)
        missing = sorted(
            {
                "candidate_rank",
                "candidate_company_name",
                "candidate_path_type",
                "verification_focus",
                "source_id",
            }
            - set(row)
        )
        if missing:
            findings.append(
                f"{manifest_name}: candidate_web_check:{line_number} missing fields: "
                f"{', '.join(missing)}"
            )
        if "opened_text" in row:
            findings.append(
                f"{manifest_name}: candidate_web_check:{line_number} must not duplicate opened_text"
            )
        if row.get("available_before_cutoff") is not True or row.get("time_verified") is not True:
            findings.append(
                f"{manifest_name}: candidate_web_check:{line_number} is not cutoff verified"
            )
        raw_published_at = row.get("published_at")
        if isinstance(raw_published_at, str) and manifest_cutoff_at is not None:
            try:
                published_at = parse_datetime(raw_published_at)
            except ValueError:
                findings.append(
                    f"{manifest_name}: candidate_web_check:{line_number} invalid published_at"
                )
                continue
            if not is_available_as_of(published_at, manifest_cutoff_at):
                findings.append(f"{manifest_name}: candidate_web_check:{line_number} after cutoff")
    if source_ids and source_ids != row_source_ids:
        findings.append(f"{manifest_name}: candidate_web_source_ids do not match artifact")
    expected_count = manifest.get("candidate_web_check_count")
    if isinstance(expected_count, int) and expected_count != row_count:
        findings.append(f"{manifest_name}: candidate_web_check_count mismatch")
    _check_excluded_candidate_web_check_artifact(root, manifest_name, manifest, findings)


def _check_excluded_candidate_web_check_artifact(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    findings: list[str],
) -> None:
    excluded = set(_string_list(manifest.get("excluded_candidate_web_source_ids")))
    relative_path = manifest.get("excluded_candidate_web_check_artifact")
    if not isinstance(relative_path, str):
        if excluded:
            findings.append(f"{manifest_name}: excluded_candidate_web_check_artifact missing")
        return
    path = root / relative_path
    if not path.exists():
        findings.append(
            f"{manifest_name}: excluded_candidate_web_check_artifact missing: {relative_path}"
        )
        return
    text = path.read_text(encoding="utf-8")
    expected_sha = manifest.get("excluded_candidate_web_check_sha256")
    if isinstance(expected_sha, str) and sha256_text(text) != expected_sha:
        findings.append(f"{manifest_name}: excluded_candidate_web_check_sha256 mismatch")
    row_source_ids: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            findings.append(
                f"{manifest_name}: excluded_candidate_web_check:{line_number} invalid JSON"
            )
            continue
        if not isinstance(row, dict):
            findings.append(
                f"{manifest_name}: excluded_candidate_web_check:{line_number} must be object"
            )
            continue
        source_id = row.get("source_id")
        if isinstance(source_id, str):
            row_source_ids.add(source_id)
        if not isinstance(row.get("exclusion_reason"), str):
            findings.append(
                f"{manifest_name}: excluded_candidate_web_check:{line_number} "
                "missing exclusion_reason"
            )
        if row.get("time_verified") is True or row.get("available_before_cutoff") is True:
            findings.append(
                f"{manifest_name}: excluded_candidate_web_check:{line_number} is cutoff verified"
            )
    if excluded != row_source_ids:
        findings.append(
            f"{manifest_name}: excluded_candidate_web_source_ids do not match artifact"
        )
    expected_count = manifest.get("excluded_candidate_web_check_count")
    if isinstance(expected_count, int) and expected_count != len(row_source_ids):
        findings.append(f"{manifest_name}: excluded_candidate_web_check_count mismatch")


def _check_blind_seal(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    findings: list[str],
) -> None:
    receipt = _read_manifest_json_artifact(
        root,
        manifest_name,
        manifest,
        path_field="blind_seal_receipt_artifact",
        sha_field="blind_seal_receipt_sha256",
        label="blind_seal_receipt",
        findings=findings,
    )
    phase_state = _read_manifest_json_artifact(
        root,
        manifest_name,
        manifest,
        path_field="phase_state_artifact",
        sha_field="phase_state_sha256",
        label="phase_state",
        findings=findings,
    )
    if receipt is not None:
        if receipt.get("phase") != "BLIND_SEALED":
            findings.append(f"{manifest_name}: blind_seal_receipt phase must be BLIND_SEALED")
        manifest_blind_hash = manifest.get("blind_artifact_sha256")
        if (
            isinstance(manifest_blind_hash, str)
            and receipt.get("blind_artifact_sha256") != manifest_blind_hash
        ):
            findings.append(f"{manifest_name}: blind_seal_receipt blind hash mismatch")
        if receipt.get("no_d_outcome_exposed") is not True:
            findings.append(f"{manifest_name}: blind_seal_receipt no_d_outcome_exposed must be true")
    if phase_state is not None:
        if phase_state.get("phase") != "BLIND_SEALED":
            findings.append(f"{manifest_name}: phase_state phase must be BLIND_SEALED")
        receipt_sha = manifest.get("blind_seal_receipt_sha256")
        if (
            isinstance(receipt_sha, str)
            and phase_state.get("blind_seal_receipt_sha256") != receipt_sha
        ):
            findings.append(f"{manifest_name}: phase_state receipt sha mismatch")


def _read_manifest_json_artifact(
    root: Path,
    manifest_name: str,
    manifest: dict[object, object],
    *,
    path_field: str,
    sha_field: str,
    label: str,
    findings: list[str],
) -> dict[str, object] | None:
    relative_path = manifest.get(path_field)
    if not isinstance(relative_path, str):
        return None
    path = root / relative_path
    if not path.exists():
        findings.append(f"{manifest_name}: {path_field} missing: {relative_path}")
        return None
    text = path.read_text(encoding="utf-8")
    expected_sha = manifest.get(sha_field)
    if isinstance(expected_sha, str) and sha256_text(text) != expected_sha:
        findings.append(f"{manifest_name}: {sha_field} mismatch")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        findings.append(f"{manifest_name}: {label} invalid JSON")
        return None
    if not isinstance(payload, dict):
        findings.append(f"{manifest_name}: {label} must be object")
        return None
    return payload


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
