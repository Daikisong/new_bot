"""Shared evidence-polarity and memory-lane routing for brain records."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from news_scalping_lab.records.models import (
    CANDIDATE_ERROR_RECORD_TYPES,
    BrainRecordEnvelope,
)


class RecordEvidencePolarity(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEAR_MISS = "NEAR_MISS"
    UNEXPLAINED = "UNEXPLAINED"
    CONTEXT = "CONTEXT"
    UNKNOWN = "UNKNOWN"


POSITIVE_ANALOGS_LANE = "positive_analogs"
NEGATIVE_CONTROLS_LANE = "negative_controls"
NEAR_MISSES_LANE = "near_misses"
COUNTEREXAMPLES_LANE = "counterexamples"
LEADER_SELECTION_PAIRS_LANE = "leader_selection_pairs"
THEME_FORMATION_FAILURES_LANE = "theme_formation_failures"
CANDIDATE_GENERATION_ERRORS_LANE = "candidate_generation_errors"
NEWSLESS_OR_UNEXPLAINED_LANE = "newsless_or_unexplained"

MEMORY_RETRIEVAL_LANES = (
    POSITIVE_ANALOGS_LANE,
    NEGATIVE_CONTROLS_LANE,
    NEAR_MISSES_LANE,
    COUNTEREXAMPLES_LANE,
    LEADER_SELECTION_PAIRS_LANE,
    THEME_FORMATION_FAILURES_LANE,
    CANDIDATE_GENERATION_ERRORS_LANE,
    NEWSLESS_OR_UNEXPLAINED_LANE,
)

_OUTCOME_LABELED_RECORD_TYPES = frozenset(
    {
        "supervised_issuer_day_case",
        "supervised_direct_event_case",
        "supervised_theme_formation_case",
        "theme_formation_case",
        "beneficiary_discovery_case",
    }
)
_THEME_RECORD_TYPES = frozenset(
    {"supervised_theme_formation_case", "theme_formation_case"}
)
_CONTEXT_RECORD_TYPES = frozenset(
    {
        "context_market_state_or_fact_case",
        "market_state_context_case",
        "memory_claim",
        "mechanism_memory",
        "company_memory_delta",
        "event_ticker_edge",
        "retrospective_theme_member_edge",
        "retrospective_theme_discovery",
        "research_question",
    }
)
_OUTCOME_CONTAINERS = (
    "payload",
    "D_outcome",
    "D_response",
    "outcome",
    "issuer_day_outcome",
    "fields",
)
_LABEL_FIELDS = (
    "response_class",
    "outcome_response_class",
    "outcome_label",
    "classification",
    "result_class",
    "result",
)
_HIGH_RETURN_FIELDS = (
    "outcome_high_return_pct",
    "high_return_pct",
    "intraday_high_return_pct",
    "D_high_return_pct",
)
_CLOSE_RETURN_FIELDS = (
    "outcome_close_return_pct",
    "close_return_pct",
    "D_close_return_pct",
)
_UPPER_LIMIT_FIELDS = (
    "upper_limit_touched",
    "is_upper_limit",
    "upper_limit",
)
_NEAR_MISS_LABEL_PARTS = (
    "NEAR_MISS",
    "MIXED_RESPONSE",
    "PARTIAL_RESPONSE",
    "UNCLEAR_RESPONSE",
)
_NEGATIVE_LABEL_PARTS = (
    "NO_RESPONSE",
    "NON_RESPONSE",
    "NO_TRADABLE_ROW",
    "FALSE_POSITIVE",
    "NON_SCORING",
    "NEGATIVE",
    "FAILED",
    "FAILURE",
    "FLAT",
    "WEAK",
    "MISS",
    "NOT_FINAL",
)
_POSITIVE_LABEL_PARTS = (
    "POSITIVE",
    "UPPER_LIMIT",
    "HIGH10",
    "HIGH_10",
    "STRONG_RESPONSE",
    "WORKED",
    "WINNER",
    "HIT",
)


def record_evidence_polarity(record: BrainRecordEnvelope) -> RecordEvidencePolarity:
    """Return outcome direction without treating eligibility as bullishness."""

    record_type = record.record_type
    if record_type == "newsless_or_unexplained_case":
        return RecordEvidencePolarity.UNEXPLAINED
    if record_type in {"negative_control_case", "counterexample"}:
        return RecordEvidencePolarity.NEGATIVE
    if record_type in CANDIDATE_ERROR_RECORD_TYPES:
        return RecordEvidencePolarity.NEAR_MISS
    if record_type in _CONTEXT_RECORD_TYPES:
        return RecordEvidencePolarity.CONTEXT

    outcome_polarity = _outcome_polarity(record.payload)
    if record_type in _OUTCOME_LABELED_RECORD_TYPES:
        if not record.training_eligible and outcome_polarity is RecordEvidencePolarity.POSITIVE:
            return RecordEvidencePolarity.NEAR_MISS
        return outcome_polarity
    return RecordEvidencePolarity.UNKNOWN


def record_memory_lanes(record: BrainRecordEnvelope) -> frozenset[str]:
    """Return every balanced retrieval lane to which a record belongs."""

    record_type = record.record_type
    polarity = record_evidence_polarity(record)
    lanes: set[str] = set()

    if polarity is RecordEvidencePolarity.POSITIVE and record.training_eligible:
        lanes.add(POSITIVE_ANALOGS_LANE)
    if record_type == "negative_control_case" or (
        record_type in _OUTCOME_LABELED_RECORD_TYPES
        and polarity is RecordEvidencePolarity.NEGATIVE
    ):
        lanes.add(NEGATIVE_CONTROLS_LANE)
    if polarity is RecordEvidencePolarity.NEAR_MISS:
        lanes.add(NEAR_MISSES_LANE)
    if record_type == "counterexample":
        lanes.add(COUNTEREXAMPLES_LANE)
    if record_type == "blind_leader_preference_pair":
        lanes.add(LEADER_SELECTION_PAIRS_LANE)
    if record_type in _THEME_RECORD_TYPES and polarity in {
        RecordEvidencePolarity.NEGATIVE,
        RecordEvidencePolarity.NEAR_MISS,
    }:
        lanes.add(THEME_FORMATION_FAILURES_LANE)
    if record_type in CANDIDATE_ERROR_RECORD_TYPES:
        lanes.add(CANDIDATE_GENERATION_ERRORS_LANE)
    if record_type == "newsless_or_unexplained_case":
        lanes.add(NEWSLESS_OR_UNEXPLAINED_LANE)
    return frozenset(lanes)


def record_is_positive_support(record: BrainRecordEnvelope) -> bool:
    return (
        record.training_eligible
        and record_evidence_polarity(record) is RecordEvidencePolarity.POSITIVE
    )


def record_outcome_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the most specific outcome payload without dropping legacy aliases."""

    for mapping in _payload_mappings(payload):
        for key in ("D_outcome", "D_response", "outcome", "issuer_day_outcome"):
            value = mapping.get(key)
            if isinstance(value, dict) and value:
                return value
    summary: dict[str, Any] = {}
    for key in (*_HIGH_RETURN_FIELDS, *_CLOSE_RETURN_FIELDS, *_UPPER_LIMIT_FIELDS):
        value = _first_value(payload, key)
        if value is not None:
            summary[key] = value
    return summary


def record_response_class(payload: dict[str, Any]) -> str | None:
    for field in _LABEL_FIELDS:
        value = _first_value(payload, field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _outcome_polarity(payload: dict[str, Any]) -> RecordEvidencePolarity:
    labels = [
        _normalize_label(value)
        for field in _LABEL_FIELDS
        for value in [_first_value(payload, field)]
        if isinstance(value, str) and value.strip()
    ]
    if any(any(part in label for part in _NEAR_MISS_LABEL_PARTS) for label in labels):
        return RecordEvidencePolarity.NEAR_MISS
    if any(any(part in label for part in _NEGATIVE_LABEL_PARTS) for label in labels):
        return RecordEvidencePolarity.NEGATIVE
    if any(any(part in label for part in _POSITIVE_LABEL_PARTS) for label in labels):
        return RecordEvidencePolarity.POSITIVE

    upper_limit = _first_bool(payload, _UPPER_LIMIT_FIELDS)
    high_return = _first_float(payload, _HIGH_RETURN_FIELDS)
    close_return = _first_float(payload, _CLOSE_RETURN_FIELDS)
    if upper_limit is True or (high_return is not None and high_return >= 5.0):
        return RecordEvidencePolarity.POSITIVE
    if high_return is not None and high_return <= 3.0:
        return RecordEvidencePolarity.NEGATIVE
    if close_return is not None and close_return <= 0.0:
        return RecordEvidencePolarity.NEGATIVE
    if high_return is not None or (close_return is not None and close_return > 0.0):
        return RecordEvidencePolarity.NEAR_MISS
    return RecordEvidencePolarity.UNKNOWN


def _payload_mappings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    queue = [payload]
    seen: set[int] = set()
    while queue:
        mapping = queue.pop(0)
        identity = id(mapping)
        if identity in seen:
            continue
        seen.add(identity)
        mappings.append(mapping)
        for key in _OUTCOME_CONTAINERS:
            nested = mapping.get(key)
            if isinstance(nested, dict):
                queue.append(nested)
    return mappings


def _first_value(payload: dict[str, Any], field: str) -> object:
    for mapping in _payload_mappings(payload):
        if field in mapping and mapping[field] is not None:
            return mapping[field]
    return None


def _first_float(payload: dict[str, Any], fields: tuple[str, ...]) -> float | None:
    for field in fields:
        value = _first_value(payload, field)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip().replace("%", ""))
            except ValueError:
                continue
    return None


def _first_bool(payload: dict[str, Any], fields: tuple[str, ...]) -> bool | None:
    for field in fields:
        value = _first_value(payload, field)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "yes", "1", "touched", "hit"}:
                return True
            if normalized in {"false", "no", "0", "not_touched", "none"}:
                return False
    return None


def _normalize_label(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
