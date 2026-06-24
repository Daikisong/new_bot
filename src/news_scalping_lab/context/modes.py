"""Analysis mode validation."""

from __future__ import annotations

VALID_ANALYSIS_MODES = ("exhaustive", "brain", "fast")


def normalize_analysis_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in VALID_ANALYSIS_MODES:
        allowed = ", ".join(VALID_ANALYSIS_MODES)
        raise ValueError(f"analysis mode must be one of: {allowed}")
    return normalized
