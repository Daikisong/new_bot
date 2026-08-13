"""Canonical structural checks for blind preference-pair records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def has_sealed_preference_pair(payload: Mapping[str, Any]) -> bool:
    """Return whether both blind-selected and compared alternatives are bound."""

    flattened = dict(payload)
    nested = payload.get("payload")
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            flattened.setdefault(str(key), value)
    preferred = _first_string(
        flattened,
        "blind_preferred_ticker",
        "blind_preferred_candidate_id",
        "preferred_ticker",
        "preferred_candidate_id",
        "winner_ticker",
        "winner_candidate_id",
        "blind_selected_ticker",
        "blind_selected_candidate_id",
    )
    rejected = _first_string(
        flattened,
        "blind_rejected_ticker",
        "blind_rejected_candidate_id",
        "rejected_ticker",
        "rejected_candidate_id",
        "loser_ticker",
        "loser_candidate_id",
        "comparator_ticker",
        "comparator_candidate_id",
        "comparison_ticker",
        "comparison_candidate_id",
    )
    blind_preference = _first_string(flattened, "blind_preference")
    if blind_preference is not None and blind_preference.lower() == "selected":
        preferred = preferred or _first_string(
            flattened,
            "selected_candidate_id",
            "selected_ticker",
        )
        rejected = rejected or _first_string(
            flattened,
            "comparator_candidate_id",
            "comparator_ticker",
        )
    if blind_preference is not None and blind_preference.lower() in {"left", "right"}:
        left = _first_string(flattened, "left_ticker", "left_candidate_id")
        right = _first_string(flattened, "right_ticker", "right_candidate_id")
        if blind_preference.lower() == "left":
            preferred = preferred or left
            rejected = rejected or right
        else:
            preferred = preferred or right
            rejected = rejected or left
    return preferred is not None and rejected is not None


def _first_string(payload: Mapping[str, Any], *fields: str) -> str | None:
    return next(
        (
            value
            for field in fields
            for value in [payload.get(field)]
            if isinstance(value, str) and value
        ),
        None,
    )
