"""Lookahead leak audits."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.utils import is_available_as_of, parse_datetime, read_json, sha256_text


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
        if manifest_trade_date is None:
            findings.append(f"{manifest_name}: missing trade_date")
        if manifest_cutoff_at is None:
            findings.append(f"{manifest_name}: missing cutoff_at")
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
        if (
            manifest.get("mode") == "exhaustive"
            and manifest.get("accepted_episode_count") != manifest.get("swept_episode_count")
        ):
            findings.append(f"{manifest_name}: exhaustive coverage mismatch")
        _check_row_disposition(root, manifest_name, manifest, findings)
        _check_source_ledger(root, manifest_name, manifest, findings)
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
    _check_web_source_artifact(root, manifest_name, manifest, findings)


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
