"""Lookahead leak audits."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.utils import is_available_as_of, parse_datetime, read_json


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


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
