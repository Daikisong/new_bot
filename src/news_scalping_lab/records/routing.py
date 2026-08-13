"""Shared evidence-polarity and memory-lane routing for brain records."""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Any

from news_scalping_lab.contracts.memory_context import RecordRoutingMetadata
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


class RecordLabelQuality(StrEnum):
    VERIFIED = "verified"
    QUARANTINED = "quarantined"
    NO_TRADABLE_ROW = "no_tradable_row"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    NOT_APPLICABLE = "not_applicable"


class RecordRoutingDisposition(StrEnum):
    REASONING = "REASONING"
    CONTEXT = "CONTEXT"
    AUDIT = "AUDIT"
    QUARANTINED = "QUARANTINED"


POLARITY_CLASSIFIER_VERSION = "record_polarity.v2"
FALLBACK_HIGH_RETURN_POSITIVE_THRESHOLD = 5.0
FALLBACK_HIGH_RETURN_NEGATIVE_THRESHOLD = 3.0
FALLBACK_CLOSE_RETURN_NEGATIVE_THRESHOLD = 0.0
OUTCOME_CONTRADICTION_HIGH_RETURN_THRESHOLD = 10.0


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
        "negative_control_case",
        "newsless_or_unexplained_case",
    }
)
_THEME_RECORD_TYPES = frozenset({"supervised_theme_formation_case", "theme_formation_case"})
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
_REASONING_EVIDENCE_PHASES = frozenset({"BLIND", "BLIND_SAFE", "POSTMORTEM"})
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
    "result_class",
)
_LABEL_QUALITY_FIELDS = (
    "label_quality",
    "outcome_label_quality",
    "price_label_quality",
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
        return _outcome_polarity(record.payload)
    if record_type in _CONTEXT_RECORD_TYPES:
        return RecordEvidencePolarity.CONTEXT

    outcome_polarity = _outcome_polarity(record.payload)
    if record_type in _OUTCOME_LABELED_RECORD_TYPES:
        return outcome_polarity
    return RecordEvidencePolarity.UNKNOWN


def record_label_quality(record: BrainRecordEnvelope) -> RecordLabelQuality:
    """Return outcome-label quality independently from outcome direction."""

    raw_quality_entries = [
        mapping[field]
        for mapping in _payload_mappings(record.payload)
        for field in _LABEL_QUALITY_FIELDS
        if field in mapping
    ]
    values = {
        normalized
        for value in raw_quality_entries
        if isinstance(value, str) and value.strip() and (normalized := _normalize_label_quality(value)) is not None
    }
    invalid_quality_entry = any(
        not isinstance(value, str) or not value.strip() or _normalize_label_quality(value) is None
        for value in raw_quality_entries
    )
    explicit_polarities = _explicit_label_polarities(record.payload)
    explicit = _single_polarity(explicit_polarities)
    numeric = _numeric_outcome_polarity(record.payload)
    if (
        len(explicit_polarities) > 1
        or _training_eligibility_mirror_conflict(record)
        or _native_type_label_conflict(record, explicit)
        or _native_assertion_conflict(record)
        or _numeric_alias_conflict(record.payload)
        or _invalid_numeric_outcome_present(record.payload)
        or _explicit_numeric_conflict(
            record.payload,
            _quality_contract_polarity(record, explicit),
            numeric,
        )
    ):
        return RecordLabelQuality.CONFLICTING
    if invalid_quality_entry or _invalid_explicit_label_present(record.payload):
        return RecordLabelQuality.AMBIGUOUS
    if len(values) > 1:
        return RecordLabelQuality.CONFLICTING
    if record.record_type in {
        "negative_control_case",
        "newsless_or_unexplained_case",
        "counterexample",
    }:
        if values and next(iter(values)) is not RecordLabelQuality.VERIFIED:
            return next(iter(values))
        return (
            RecordLabelQuality.VERIFIED
            if _native_record_type_contract_verified(record, numeric)
            else RecordLabelQuality.MISSING
        )
    if record.record_type in CANDIDATE_ERROR_RECORD_TYPES:
        if values and next(iter(values)) is not RecordLabelQuality.VERIFIED:
            return next(iter(values))
        return (
            RecordLabelQuality.VERIFIED
            if _candidate_error_contract_verified(record, explicit, numeric)
            else RecordLabelQuality.MISSING
        )
    if values:
        quality = next(iter(values))
        if quality is not RecordLabelQuality.VERIFIED:
            return quality
        return (
            RecordLabelQuality.VERIFIED
            if _generic_record_quality_contract_verified(
                record,
                numeric,
                declared_quality=True,
            )
            else RecordLabelQuality.MISSING
        )
    explicit_labels = _all_normalized_labels(record.payload)
    if any("NO_TRADABLE_ROW" in label for label in explicit_labels):
        return RecordLabelQuality.NO_TRADABLE_ROW
    if explicit is not RecordEvidencePolarity.UNKNOWN:
        return (
            RecordLabelQuality.VERIFIED
            if _generic_record_quality_contract_verified(
                record,
                numeric,
                declared_quality=False,
            )
            else RecordLabelQuality.MISSING
        )
    if _legacy_outcome_contract_verified(record, numeric):
        return RecordLabelQuality.VERIFIED
    if record.record_type not in _OUTCOME_LABELED_RECORD_TYPES:
        return RecordLabelQuality.NOT_APPLICABLE
    return RecordLabelQuality.MISSING


def record_routing_disposition(
    record: BrainRecordEnvelope,
) -> RecordRoutingDisposition:
    """Choose runtime usage without changing polarity or eligibility."""

    quality = record_label_quality(record)
    polarity = record_evidence_polarity(record)
    if record.typed_payload_status == "UNKNOWN_TYPED_PAYLOAD" or quality in {
        RecordLabelQuality.QUARANTINED,
        RecordLabelQuality.NO_TRADABLE_ROW,
    }:
        return RecordRoutingDisposition.QUARANTINED
    if not _valid_provenance_ids(record):
        return RecordRoutingDisposition.AUDIT
    if record.evidence_phase not in _REASONING_EVIDENCE_PHASES or not record.training_eligible:
        return RecordRoutingDisposition.AUDIT
    if quality in {
        RecordLabelQuality.MISSING,
        RecordLabelQuality.AMBIGUOUS,
        RecordLabelQuality.CONFLICTING,
    }:
        return RecordRoutingDisposition.AUDIT
    if quality is RecordLabelQuality.NOT_APPLICABLE and polarity in {
        RecordEvidencePolarity.POSITIVE,
        RecordEvidencePolarity.NEGATIVE,
        RecordEvidencePolarity.UNEXPLAINED,
    }:
        return RecordRoutingDisposition.AUDIT
    if record.record_type == "blind_leader_preference_pair":
        return RecordRoutingDisposition.REASONING
    if polarity is RecordEvidencePolarity.CONTEXT:
        return RecordRoutingDisposition.CONTEXT
    if polarity is RecordEvidencePolarity.UNKNOWN:
        return RecordRoutingDisposition.AUDIT
    return RecordRoutingDisposition.REASONING


def record_routing_metadata(record: BrainRecordEnvelope) -> RecordRoutingMetadata:
    polarity = record_evidence_polarity(record)
    source, role = _polarity_source_and_role(record)
    return RecordRoutingMetadata(
        record_id=record.record_id,
        record_type=record.record_type,
        available_from=record.available_from,
        evidence_polarity=polarity.value,
        training_eligible=record.training_eligible,
        label_quality=record_label_quality(record).value,
        routing_disposition=record_routing_disposition(record).value,
        memory_lanes=sorted(record_memory_lanes(record)),
        polarity_classifier_version=POLARITY_CLASSIFIER_VERSION,
        threshold_source=source,
        threshold_role=role,
        provenance_source_ids=record.provenance_source_ids,
    )


def record_memory_lanes(record: BrainRecordEnvelope) -> frozenset[str]:
    """Return every balanced retrieval lane to which a record belongs."""

    record_type = record.record_type
    polarity = record_evidence_polarity(record)
    disposition = record_routing_disposition(record)
    lanes: set[str] = set()

    if disposition is not RecordRoutingDisposition.REASONING:
        return frozenset()
    if polarity is RecordEvidencePolarity.POSITIVE and record_is_positive_support(record):
        lanes.add(POSITIVE_ANALOGS_LANE)
    if record_type == "negative_control_case" or (
        record_type in _OUTCOME_LABELED_RECORD_TYPES and polarity is RecordEvidencePolarity.NEGATIVE
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
        record.record_type not in CANDIDATE_ERROR_RECORD_TYPES
        and record.record_type != "blind_leader_preference_pair"
        and record.training_eligible
        and record_evidence_polarity(record) is RecordEvidencePolarity.POSITIVE
        and record_label_quality(record) is RecordLabelQuality.VERIFIED
        and record_routing_disposition(record) is RecordRoutingDisposition.REASONING
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
    explicit_polarities = _explicit_label_polarities(payload)
    if len(explicit_polarities) > 1 or _numeric_alias_conflict(payload) or _invalid_numeric_outcome_present(payload):
        return RecordEvidencePolarity.UNKNOWN
    explicit = _single_polarity(explicit_polarities)
    if explicit is not RecordEvidencePolarity.UNKNOWN:
        return explicit
    return _numeric_outcome_polarity(payload)


def _explicit_label_polarity(payload: dict[str, Any]) -> RecordEvidencePolarity:
    return _single_polarity(_explicit_label_polarities(payload))


def _explicit_label_polarities(
    payload: dict[str, Any],
) -> set[RecordEvidencePolarity]:
    polarities: set[RecordEvidencePolarity] = set()
    for label in _all_normalized_labels(payload):
        polarities.update(_normalized_label_polarities(label))
    return polarities


def _normalized_label_polarities(label: str) -> set[RecordEvidencePolarity]:
    near_compounds = {
        part
        for part in _NEAR_MISS_LABEL_PARTS
        if _contains_label_marker(label, part)
    }
    negative_compounds = {
        part
        for part in _NEGATIVE_LABEL_PARTS
        if _contains_label_marker(label, part)
    }
    if (
        label.startswith(("NO_", "NOT_"))
        and any(_contains_label_marker(label, part) for part in _POSITIVE_LABEL_PARTS)
        and not negative_compounds
    ):
        return set()
    polarities: set[RecordEvidencePolarity] = set()
    if near_compounds:
        polarities.add(RecordEvidencePolarity.NEAR_MISS)
    effective_negative_compounds = negative_compounds - ({"MISS"} if near_compounds else set())
    if effective_negative_compounds:
        polarities.add(RecordEvidencePolarity.NEGATIVE)
    positive_markers = {
        part
        for part in _POSITIVE_LABEL_PARTS
        if _contains_label_marker(label, part)
    }
    if "FALSE_POSITIVE" in negative_compounds:
        positive_markers.discard("POSITIVE")
    if positive_markers:
        polarities.add(RecordEvidencePolarity.POSITIVE)
    return polarities


def _contains_label_marker(label: str, marker: str) -> bool:
    return re.search(rf"(?:^|_){re.escape(marker)}(?:_|$)", label) is not None


def _invalid_explicit_label_present(payload: dict[str, Any]) -> bool:
    entries = [mapping[field] for mapping in _payload_mappings(payload) for field in _LABEL_FIELDS if field in mapping]
    return any(
        not isinstance(value, str) or not value.strip() or not _explicit_label_polarities({"response_class": value})
        for value in entries
    )


def _all_normalized_labels(payload: dict[str, Any]) -> set[str]:
    return {
        _normalize_label(value)
        for mapping in _payload_mappings(payload)
        for field in _LABEL_FIELDS
        for value in [mapping.get(field)]
        if isinstance(value, str) and value.strip()
    }


def _single_polarity(
    polarities: set[RecordEvidencePolarity],
) -> RecordEvidencePolarity:
    if len(polarities) == 1:
        return next(iter(polarities))
    return RecordEvidencePolarity.UNKNOWN


def _numeric_outcome_polarity(payload: dict[str, Any]) -> RecordEvidencePolarity:

    upper_limit = _first_bool(payload, _UPPER_LIMIT_FIELDS)
    high_return = _first_float(payload, _HIGH_RETURN_FIELDS)
    close_return = _first_float(payload, _CLOSE_RETURN_FIELDS)
    if upper_limit is True or (high_return is not None and high_return >= FALLBACK_HIGH_RETURN_POSITIVE_THRESHOLD):
        return RecordEvidencePolarity.POSITIVE
    if high_return is not None and high_return <= FALLBACK_HIGH_RETURN_NEGATIVE_THRESHOLD:
        return RecordEvidencePolarity.NEGATIVE
    if close_return is not None and close_return <= FALLBACK_CLOSE_RETURN_NEGATIVE_THRESHOLD:
        return RecordEvidencePolarity.NEGATIVE
    if high_return is not None or (close_return is not None and close_return > 0.0):
        return RecordEvidencePolarity.NEAR_MISS
    return RecordEvidencePolarity.UNKNOWN


def _normalize_label_quality(value: str) -> RecordLabelQuality | None:
    normalized = _normalize_label(value)
    mapping = {
        "VERIFIED": RecordLabelQuality.VERIFIED,
        "QUARANTINED": RecordLabelQuality.QUARANTINED,
        "NO_TRADABLE_ROW": RecordLabelQuality.NO_TRADABLE_ROW,
        "MISSING": RecordLabelQuality.MISSING,
        "UNVERIFIED": RecordLabelQuality.MISSING,
        "AMBIGUOUS": RecordLabelQuality.AMBIGUOUS,
        "CONFLICTING": RecordLabelQuality.CONFLICTING,
        "NOT_APPLICABLE": RecordLabelQuality.NOT_APPLICABLE,
    }
    return mapping.get(normalized)


def _explicit_numeric_conflict(
    payload: dict[str, Any],
    explicit: RecordEvidencePolarity,
    numeric: RecordEvidencePolarity,
) -> bool:
    if explicit is RecordEvidencePolarity.UNKNOWN or numeric is RecordEvidencePolarity.UNKNOWN or explicit is numeric:
        return False
    high_returns = _all_finite_floats(payload, _HIGH_RETURN_FIELDS)
    upper_limit = True in _all_bools(payload, _UPPER_LIMIT_FIELDS)
    strong_positive = upper_limit or any(value >= OUTCOME_CONTRADICTION_HIGH_RETURN_THRESHOLD for value in high_returns)
    if explicit in {
        RecordEvidencePolarity.NEGATIVE,
        RecordEvidencePolarity.NEAR_MISS,
    }:
        return strong_positive
    return explicit is RecordEvidencePolarity.POSITIVE and numeric is RecordEvidencePolarity.NEGATIVE


def _quality_contract_polarity(
    record: BrainRecordEnvelope,
    explicit: RecordEvidencePolarity,
) -> RecordEvidencePolarity:
    if record.record_type in {"negative_control_case", "counterexample"}:
        return RecordEvidencePolarity.NEGATIVE
    return explicit


def _native_type_label_conflict(
    record: BrainRecordEnvelope,
    explicit: RecordEvidencePolarity,
) -> bool:
    return record.record_type in {"negative_control_case", "counterexample"} and explicit not in {
        RecordEvidencePolarity.UNKNOWN,
        RecordEvidencePolarity.NEGATIVE,
    }


def _polarity_source_and_role(
    record: BrainRecordEnvelope,
) -> tuple[str, str]:
    if record.record_type in {
        "negative_control_case",
        "counterexample",
        "newsless_or_unexplained_case",
        *_CONTEXT_RECORD_TYPES,
    }:
        return "explicit_record_type", "explicit_label"
    if _explicit_label_polarity(record.payload) is not RecordEvidencePolarity.UNKNOWN:
        return "explicit_response_class", "explicit_label"
    if _numeric_outcome_polarity(record.payload) is not RecordEvidencePolarity.UNKNOWN:
        return "numeric_outcome_fallback_v1", "retrieval_calibration_only"
    return "no_usable_outcome", "retrieval_calibration_only"


def _legacy_outcome_contract_verified(
    record: BrainRecordEnvelope,
    numeric: RecordEvidencePolarity,
) -> bool:
    return numeric is not RecordEvidencePolarity.UNKNOWN and _record_evidence_contract_verified(record)


def _native_record_type_contract_verified(
    record: BrainRecordEnvelope,
    numeric: RecordEvidencePolarity,
) -> bool:
    if not _record_evidence_contract_verified(record):
        return False
    if record.record_type == "negative_control_case":
        return numeric is not RecordEvidencePolarity.UNKNOWN or _has_nonempty_string(
            record.payload,
            (
                "negative_control_reason",
                "rejection_reason",
                "rejection_or_exclusion_reason",
            ),
        )
    if record.record_type == "counterexample":
        return _has_nonempty_string(
            record.payload,
            (
                "negative_control_reason",
                "rejection_reason",
                "rejection_or_exclusion_reason",
            ),
        ) and _has_nonempty_string(record.payload, ("screening_decision",))
    if record.record_type == "newsless_or_unexplained_case":
        return _nested_bool_contract_true(
            record.payload,
            ("no_catalyst_asserted",),
        ) and numeric is not RecordEvidencePolarity.UNKNOWN
    return False


def _candidate_error_contract_verified(
    record: BrainRecordEnvelope,
    explicit: RecordEvidencePolarity,
    numeric: RecordEvidencePolarity,
) -> bool:
    return (
        explicit is not RecordEvidencePolarity.UNKNOWN
        or numeric is not RecordEvidencePolarity.UNKNOWN
    ) and _record_evidence_contract_verified(record)


def _generic_record_quality_contract_verified(
    record: BrainRecordEnvelope,
    numeric: RecordEvidencePolarity,
    *,
    declared_quality: bool,
) -> bool:
    if record.record_type in _OUTCOME_LABELED_RECORD_TYPES:
        identity_verified = (
            _record_evidence_identity_verified(record)
            if declared_quality
            else _record_evidence_contract_verified(record)
        )
        return numeric is not RecordEvidencePolarity.UNKNOWN and identity_verified
    return _record_evidence_identity_verified(record)


def _has_nonempty_string(payload: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return any(
        isinstance(value, str) and bool(value.strip())
        for mapping in _payload_mappings(payload)
        for field in fields
        for value in [mapping.get(field)]
    )


def _native_assertion_conflict(record: BrainRecordEnvelope) -> bool:
    if record.record_type != "newsless_or_unexplained_case":
        return False
    entries = [
        mapping[field]
        for mapping in _payload_mappings(record.payload)
        for field in ("no_catalyst_asserted",)
        if field in mapping
    ]
    parsed = {_parse_bool(value) for value in entries}
    return None in parsed or len(parsed) > 1


def _training_eligibility_mirror_conflict(record: BrainRecordEnvelope) -> bool:
    entries = [
        mapping["training_eligible"]
        for mapping in _payload_mappings(record.payload)
        if "training_eligible" in mapping
    ]
    return any(not isinstance(value, bool) or value is not record.training_eligible for value in entries)


def _nested_bool_contract_true(
    payload: dict[str, Any],
    fields: tuple[str, ...],
) -> bool:
    entries = [
        mapping[field]
        for mapping in _payload_mappings(payload)
        for field in fields
        if field in mapping
    ]
    return bool(entries) and {_parse_bool(value) for value in entries} == {True}


def _record_evidence_contract_verified(record: BrainRecordEnvelope) -> bool:
    return (
        record.training_eligible
        and record.payload.get("training_eligible") is True
        and _record_evidence_identity_verified(record)
    )


def _record_evidence_identity_verified(record: BrainRecordEnvelope) -> bool:
    return (
        record.typed_payload_status == "KNOWN_TYPED_PAYLOAD"
        and record.status == "supported"
        and _valid_provenance_ids(record)
    )


def _valid_provenance_ids(record: BrainRecordEnvelope) -> bool:
    return bool(record.provenance_source_ids) and all(source_id.strip() for source_id in record.provenance_source_ids)


def _numeric_alias_conflict(payload: dict[str, Any]) -> bool:
    high_polarities = {_high_return_polarity(value) for value in _all_finite_floats(payload, _HIGH_RETURN_FIELDS)}
    close_polarities = {_close_return_polarity(value) for value in _all_finite_floats(payload, _CLOSE_RETURN_FIELDS)}
    return (
        len(high_polarities) > 1
        or len(close_polarities) > 1
        or len(_all_bools(payload, _UPPER_LIMIT_FIELDS)) > 1
        or _invalid_bool_outcome_present(payload)
    )


def _invalid_numeric_outcome_present(payload: dict[str, Any]) -> bool:
    return any(
        _finite_float(mapping[field]) is None
        for mapping in _payload_mappings(payload)
        for field in (*_HIGH_RETURN_FIELDS, *_CLOSE_RETURN_FIELDS)
        if field in mapping and mapping[field] is not None
    )


def _all_finite_floats(
    payload: dict[str, Any],
    fields: tuple[str, ...],
) -> set[float]:
    return {
        parsed
        for mapping in _payload_mappings(payload)
        for field in fields
        if field in mapping
        for parsed in [_finite_float(mapping[field])]
        if parsed is not None
    }


def _all_bools(payload: dict[str, Any], fields: tuple[str, ...]) -> set[bool]:
    return {
        parsed
        for mapping in _payload_mappings(payload)
        for field in fields
        if field in mapping
        for parsed in [_parse_bool(mapping[field])]
        if parsed is not None
    }


def _invalid_bool_outcome_present(payload: dict[str, Any]) -> bool:
    return any(
        _parse_bool(mapping[field]) is None
        for mapping in _payload_mappings(payload)
        for field in _UPPER_LIMIT_FIELDS
        if field in mapping and mapping[field] is not None
    )


def _parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "touched", "hit"}:
            return True
        if normalized in {"false", "no", "0", "not_touched", "none"}:
            return False
    return None


def _high_return_polarity(value: float) -> RecordEvidencePolarity:
    if value >= FALLBACK_HIGH_RETURN_POSITIVE_THRESHOLD:
        return RecordEvidencePolarity.POSITIVE
    if value <= FALLBACK_HIGH_RETURN_NEGATIVE_THRESHOLD:
        return RecordEvidencePolarity.NEGATIVE
    return RecordEvidencePolarity.NEAR_MISS


def _close_return_polarity(value: float) -> RecordEvidencePolarity:
    return (
        RecordEvidencePolarity.NEGATIVE
        if value <= FALLBACK_CLOSE_RETURN_NEGATIVE_THRESHOLD
        else RecordEvidencePolarity.NEAR_MISS
    )


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
        parsed = _finite_float(value)
        if parsed is not None:
            return parsed
    return None


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = (
            float(value.strip().replace("%", ""))
            if isinstance(value, str)
            else float(value)
            if isinstance(value, int | float)
            else None
        )
    except ValueError:
        return None
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _first_bool(payload: dict[str, Any], fields: tuple[str, ...]) -> bool | None:
    for field in fields:
        value = _first_value(payload, field)
        parsed = _parse_bool(value)
        if parsed is not None:
            return parsed
    return None


def _normalize_label(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
