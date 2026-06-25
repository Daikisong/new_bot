"""Context manifest episode-scope validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import is_available_as_of, parse_datetime


def inspect_manifest_episode_scope(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    status: dict[str, Any] = {
        "configured": manifest.get("schema_version") == "nslab.context_manifest.v1",
        "cutoff_at_valid": False,
        "accepted_episode_count_verified": False,
        "total_accepted_episode_count_verified": False,
        "available_episode_count_verified": False,
        "unavailable_episode_count_verified": False,
        "unavailable_episode_ids_verified": False,
        "available_unavailable_total_verified": False,
        "expected_total_accepted_episode_count": None,
        "expected_available_episode_count": None,
        "expected_unavailable_episode_count": None,
        "expected_unavailable_episode_ids": [],
        "errors": [],
    }
    if not status["configured"]:
        status["errors"].append("context_manifest_schema_version_missing_or_invalid")
        status["passed"] = False
        return status

    cutoff_raw = manifest.get("cutoff_at")
    if not isinstance(cutoff_raw, str) or not cutoff_raw:
        status["errors"].append("cutoff_at_missing_or_invalid")
        status["passed"] = False
        return status
    try:
        cutoff_at = parse_datetime(cutoff_raw)
    except ValueError:
        status["errors"].append("cutoff_at_missing_or_invalid")
        status["passed"] = False
        return status
    status["cutoff_at_valid"] = True

    accepted = ResearchStore(root).list_accepted()
    available_ids = [
        episode.episode_id
        for episode in accepted
        if is_available_as_of(episode.available_from, cutoff_at)
    ]
    unavailable_ids = [
        episode.episode_id
        for episode in accepted
        if not is_available_as_of(episode.available_from, cutoff_at)
    ]
    expected_total = len(accepted)
    expected_available = len(available_ids)
    expected_unavailable = len(unavailable_ids)
    status.update(
        {
            "expected_total_accepted_episode_count": expected_total,
            "expected_available_episode_count": expected_available,
            "expected_unavailable_episode_count": expected_unavailable,
            "expected_unavailable_episode_ids": unavailable_ids,
        }
    )

    accepted_count = _non_bool_int(manifest.get("accepted_episode_count"))
    total_count = _non_bool_int(manifest.get("total_accepted_episode_count"))
    available_count = _non_bool_int(manifest.get("available_episode_count"))
    unavailable_count = _non_bool_int(manifest.get("unavailable_episode_count"))
    manifest_unavailable_ids = _string_list_or_none(manifest.get("unavailable_episode_ids"))

    _verify_count(
        status,
        field="accepted_episode_count",
        observed=accepted_count,
        expected=expected_available,
    )
    _verify_count(
        status,
        field="total_accepted_episode_count",
        observed=total_count,
        expected=expected_total,
    )
    _verify_count(
        status,
        field="available_episode_count",
        observed=available_count,
        expected=expected_available,
    )
    _verify_count(
        status,
        field="unavailable_episode_count",
        observed=unavailable_count,
        expected=expected_unavailable,
    )
    if manifest_unavailable_ids is None:
        status["errors"].append("unavailable_episode_ids_invalid")
    else:
        verified = manifest_unavailable_ids == unavailable_ids
        status["unavailable_episode_ids_verified"] = verified
        if not verified:
            status["errors"].append("unavailable_episode_ids_mismatch")

    if (
        total_count is not None
        and available_count is not None
        and unavailable_count is not None
    ):
        verified = total_count == available_count + unavailable_count
        status["available_unavailable_total_verified"] = verified
        if not verified:
            status["errors"].append("available_unavailable_total_mismatch")

    status["passed"] = (
        status["cutoff_at_valid"]
        and status["accepted_episode_count_verified"]
        and status["total_accepted_episode_count_verified"]
        and status["available_episode_count_verified"]
        and status["unavailable_episode_count_verified"]
        and status["unavailable_episode_ids_verified"]
        and status["available_unavailable_total_verified"]
        and not status["errors"]
    )
    return status


def _verify_count(
    status: dict[str, Any],
    *,
    field: str,
    observed: int | None,
    expected: int,
) -> None:
    verified_key = f"{field}_verified"
    if observed is None or observed < 0:
        status["errors"].append(f"{field}_invalid")
        return
    verified = observed == expected
    status[verified_key] = verified
    if not verified:
        status["errors"].append(f"{field}_mismatch")


def _non_bool_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string_list_or_none(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return list(value)
