from datetime import date, datetime

import pytest

from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import (
    NEGATIVE_CONTROLS_LANE,
    NEWSLESS_OR_UNEXPLAINED_LANE,
    POSITIVE_ANALOGS_LANE,
    RecordEvidencePolarity,
    RecordLabelQuality,
    RecordRoutingDisposition,
    record_evidence_polarity,
    record_is_positive_support,
    record_label_quality,
    record_memory_lanes,
    record_outcome_payload,
    record_routing_disposition,
    record_routing_metadata,
)
from news_scalping_lab.utils import KST, canonical_json, sha256_text


@pytest.mark.parametrize(
    (
        "record_type",
        "payload",
        "eligible",
        "polarity",
        "quality",
        "disposition",
        "lanes",
    ),
    [
        (
            "supervised_direct_event_case",
            {
                "D_response": {
                    "high_return_pct": 12.5,
                    "close_return_pct": 8.0,
                    "label_quality": "verified",
                }
            },
            True,
            RecordEvidencePolarity.POSITIVE,
            RecordLabelQuality.VERIFIED,
            RecordRoutingDisposition.REASONING,
            {POSITIVE_ANALOGS_LANE},
        ),
        (
            "supervised_direct_event_case",
            {
                "D_response": {
                    "high_return_pct": 12.5,
                    "label_quality": "verified",
                }
            },
            False,
            RecordEvidencePolarity.POSITIVE,
            RecordLabelQuality.VERIFIED,
            RecordRoutingDisposition.AUDIT,
            set(),
        ),
        (
            "negative_control_case",
            {
                "high_return_pct": 2.0,
                "control_class": "WEAK",
                "training_eligible": True,
            },
            True,
            RecordEvidencePolarity.NEGATIVE,
            RecordLabelQuality.VERIFIED,
            RecordRoutingDisposition.REASONING,
            {NEGATIVE_CONTROLS_LANE},
        ),
        (
            "supervised_direct_event_case",
            {
                "D_outcome": {
                    "high_return_pct": 1.2,
                    "close_return_pct": -0.5,
                    "label_quality": "verified",
                }
            },
            False,
            RecordEvidencePolarity.NEGATIVE,
            RecordLabelQuality.VERIFIED,
            RecordRoutingDisposition.AUDIT,
            set(),
        ),
        (
            "candidate_generation_error_case",
            {"error_type": "MISSED_WINNER"},
            True,
            RecordEvidencePolarity.UNKNOWN,
            RecordLabelQuality.MISSING,
            RecordRoutingDisposition.AUDIT,
            set(),
        ),
        (
            "newsless_or_unexplained_case",
            {
                "outcome_high_return_pct": 24.0,
                "no_catalyst_asserted": True,
                "training_eligible": True,
            },
            True,
            RecordEvidencePolarity.UNEXPLAINED,
            RecordLabelQuality.VERIFIED,
            RecordRoutingDisposition.REASONING,
            {NEWSLESS_OR_UNEXPLAINED_LANE},
        ),
        (
            "memory_claim",
            {"statement": "market context"},
            True,
            RecordEvidencePolarity.CONTEXT,
            RecordLabelQuality.NOT_APPLICABLE,
            RecordRoutingDisposition.CONTEXT,
            set(),
        ),
        (
            "future_unknown_case",
            {"opaque": True},
            True,
            RecordEvidencePolarity.UNKNOWN,
            RecordLabelQuality.NOT_APPLICABLE,
            RecordRoutingDisposition.QUARANTINED,
            set(),
        ),
        (
            "supervised_direct_event_case",
            {},
            True,
            RecordEvidencePolarity.UNKNOWN,
            RecordLabelQuality.MISSING,
            RecordRoutingDisposition.AUDIT,
            set(),
        ),
        (
            "supervised_direct_event_case",
            {
                "response_class": "UPPER_LIMIT",
                "D_outcome": {
                    "high_return_pct": 1.0,
                    "label_quality": "verified",
                },
            },
            True,
            RecordEvidencePolarity.POSITIVE,
            RecordLabelQuality.CONFLICTING,
            RecordRoutingDisposition.AUDIT,
            set(),
        ),
        (
            "supervised_direct_event_case",
            {
                "high_return_pct": 12.0,
                "label_quality": "verified",
                "D_outcome": {"label_quality": "quarantined"},
            },
            True,
            RecordEvidencePolarity.POSITIVE,
            RecordLabelQuality.CONFLICTING,
            RecordRoutingDisposition.AUDIT,
            set(),
        ),
        (
            "supervised_direct_event_case",
            {"high_return_pct": 12.0, "label_quality": "unreviewed_magic"},
            True,
            RecordEvidencePolarity.POSITIVE,
            RecordLabelQuality.AMBIGUOUS,
            RecordRoutingDisposition.AUDIT,
            set(),
        ),
    ],
)
def test_record_routing_keeps_four_axes_independent(
    record_type: str,
    payload: dict[str, object],
    eligible: bool,
    polarity: RecordEvidencePolarity,
    quality: RecordLabelQuality,
    disposition: RecordRoutingDisposition,
    lanes: set[str],
) -> None:
    record = _record(record_type, payload=payload, training_eligible=eligible)

    assert record_evidence_polarity(record) is polarity
    assert record_label_quality(record) is quality
    assert record_routing_disposition(record) is disposition
    assert set(record_memory_lanes(record)) == lanes


def test_eligibility_does_not_change_positive_polarity() -> None:
    payload = {"D_outcome": {"high_return_pct": 15.0, "label_quality": "verified"}}
    eligible = _record(
        "supervised_issuer_day_case",
        payload=payload,
        training_eligible=True,
    )
    ineligible = _record(
        "supervised_issuer_day_case",
        payload=payload,
        training_eligible=False,
    )

    assert record_evidence_polarity(eligible) is RecordEvidencePolarity.POSITIVE
    assert record_evidence_polarity(ineligible) is RecordEvidencePolarity.POSITIVE
    assert record_is_positive_support(eligible) is True
    assert record_is_positive_support(ineligible) is False
    assert record_routing_disposition(ineligible) is RecordRoutingDisposition.AUDIT


@pytest.mark.parametrize(
    ("response_class", "expected_quality"),
    [
        ("POSITIVE_NEGATIVE", RecordLabelQuality.CONFLICTING),
        ("NOT_POSITIVE", RecordLabelQuality.AMBIGUOUS),
        ("HITLER", RecordLabelQuality.AMBIGUOUS),
        ("NO_HIT", RecordLabelQuality.AMBIGUOUS),
    ],
)
def test_unknown_or_contradictory_response_labels_fail_closed(
    response_class: str,
    expected_quality: RecordLabelQuality,
) -> None:
    record = _record(
        "supervised_direct_event_case",
        payload={"response_class": response_class},
        training_eligible=True,
    )

    assert record_label_quality(record) is expected_quality
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


def test_generic_explicit_response_without_usable_outcome_fails_closed() -> None:
    record = _record(
        "supervised_direct_event_case",
        payload={
            "training_eligible": True,
            "response_class": "POSITIVE",
        },
        training_eligible=True,
    )

    assert record_evidence_polarity(record) is RecordEvidencePolarity.POSITIVE
    assert record_label_quality(record) is RecordLabelQuality.MISSING
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


@pytest.mark.parametrize(
    "payload",
    [
        {"training_eligible": False, "high_return_pct": 12.0, "label_quality": "verified"},
        {
            "training_eligible": True,
            "high_return_pct": 12.0,
            "label_quality": "verified",
            "payload": {"training_eligible": False},
        },
        {"training_eligible": "true", "high_return_pct": 12.0, "label_quality": "verified"},
    ],
)
def test_training_eligibility_mirror_conflict_fails_closed(
    payload: dict[str, object],
) -> None:
    record = _record(
        "supervised_direct_event_case",
        payload=payload,
        training_eligible=True,
    )

    assert record_label_quality(record) is RecordLabelQuality.CONFLICTING
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


def test_numeric_fallback_is_versioned_as_retrieval_calibration_only() -> None:
    record = _record(
        "supervised_direct_event_case",
        payload={"D_outcome": {"high_return_pct": 8.0}},
        training_eligible=True,
    )

    routing = record_routing_metadata(record)

    assert routing.evidence_polarity == "POSITIVE"
    assert routing.label_quality == "missing"
    assert routing.routing_disposition == "AUDIT"
    assert routing.polarity_classifier_version == "record_polarity.v2"
    assert routing.threshold_source == "numeric_outcome_fallback_v1"
    assert routing.threshold_role == "retrieval_calibration_only"


def test_legacy_verified_outcome_contract_preserves_reasoning_record() -> None:
    record = _record(
        "supervised_direct_event_case",
        payload={
            "training_eligible": True,
            "high_return_pct": 8.0,
            "source_fact_ids": ["FACT-1"],
        },
        training_eligible=True,
    )

    routing = record_routing_metadata(record)

    assert routing.evidence_polarity == "POSITIVE"
    assert routing.label_quality == "verified"
    assert routing.routing_disposition == "REASONING"
    assert routing.memory_lanes == ["positive_analogs"]


def test_error_classification_is_not_treated_as_price_polarity() -> None:
    record = _record(
        "candidate_generation_error_case",
        payload={
            "training_eligible": True,
            "classification": "CANDIDATE_GENERATION_MISS",
            "outcome_high_return_pct": 29.0,
        },
        training_eligible=True,
    )

    routing = record_routing_metadata(record)

    assert routing.evidence_polarity == "POSITIVE"
    assert routing.label_quality == "verified"
    assert routing.routing_disposition == "REASONING"
    assert "candidate_generation_errors" in routing.memory_lanes
    assert "positive_analogs" not in routing.memory_lanes
    assert record_is_positive_support(record) is False
    assert routing.threshold_source == "numeric_outcome_fallback_v1"
    assert routing.threshold_role == "retrieval_calibration_only"


def test_candidate_error_without_outcome_fails_closed() -> None:
    record = _record(
        "candidate_generation_error_case",
        payload={
            "training_eligible": True,
            "classification": "CANDIDATE_GENERATION_MISS",
        },
        training_eligible=True,
    )

    routing = record_routing_metadata(record)

    assert routing.evidence_polarity == "UNKNOWN"
    assert routing.label_quality == "missing"
    assert routing.routing_disposition == "AUDIT"
    assert routing.memory_lanes == []


def test_declared_near_miss_high8_is_not_overridden_by_retrieval_threshold() -> None:
    record = _record(
        "supervised_issuer_day_case",
        payload={
            "training_eligible": True,
            "outcome_label": "NEAR_MISS_HIGH8",
            "high_return_pct": 8.5,
        },
        training_eligible=True,
    )

    routing = record_routing_metadata(record)

    assert routing.evidence_polarity == "NEAR_MISS"
    assert routing.label_quality == "verified"
    assert routing.routing_disposition == "REASONING"


@pytest.mark.parametrize(
    ("record_type", "payload"),
    [
        (
            "supervised_direct_event_case",
            {
                "training_eligible": True,
                "response_class": "POSITIVE",
                "high_return_pct": 12.0,
                "label_quality": "not_applicable",
            },
        ),
        (
            "negative_control_case",
            {
                "training_eligible": True,
                "high_return_pct": 1.0,
                "label_quality": "not_applicable",
            },
        ),
    ],
)
def test_outcome_polarity_cannot_use_not_applicable_quality(
    record_type: str,
    payload: dict[str, object],
) -> None:
    record = _record(record_type, payload=payload, training_eligible=True)

    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()
    assert record_is_positive_support(record) is False


@pytest.mark.parametrize("invalid_quality", ["", None, 123, False, "unknown"])
def test_present_but_invalid_label_quality_fails_closed(
    invalid_quality: object,
) -> None:
    record = _record(
        "supervised_direct_event_case",
        payload={
            "training_eligible": True,
            "high_return_pct": 8.0,
            "label_quality": invalid_quality,
        },
        training_eligible=True,
    )

    assert record_label_quality(record) is RecordLabelQuality.AMBIGUOUS
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


def test_duplicate_equivalent_quality_aliases_are_not_a_conflict() -> None:
    record = _record(
        "supervised_direct_event_case",
        payload={
            "training_eligible": True,
            "response_class": "POSITIVE",
            "label_quality": "verified",
            "D_outcome": {
                "label_quality": "VERIFIED",
                "high_return_pct": 12.0,
            },
        },
        training_eligible=True,
    )

    assert record_label_quality(record) is RecordLabelQuality.VERIFIED
    assert record_routing_disposition(record) is RecordRoutingDisposition.REASONING


@pytest.mark.parametrize("record_type", ["negative_control_case", "counterexample"])
def test_negative_record_type_with_strong_positive_outcome_fails_closed(
    record_type: str,
) -> None:
    record = _record(
        record_type,
        payload={"training_eligible": True, "high_return_pct": 12.0},
        training_eligible=True,
    )

    assert record_label_quality(record) is RecordLabelQuality.CONFLICTING
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


@pytest.mark.parametrize(
    ("record_type", "response_class"),
    [
        ("negative_control_case", "POSITIVE"),
        ("counterexample", "POSITIVE"),
        ("counterexample", "NEAR_MISS"),
    ],
)
def test_native_negative_type_rejects_nonnegative_explicit_label(
    record_type: str,
    response_class: str,
) -> None:
    record = _record(
        record_type,
        payload={
            "training_eligible": True,
            "response_class": response_class,
            "negative_control_reason": "explicit negative evidence",
            "screening_decision": "REJECT",
        },
        training_eligible=True,
    )

    assert record_label_quality(record) is RecordLabelQuality.CONFLICTING
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


@pytest.mark.parametrize("record_type", ["negative_control_case", "counterexample"])
def test_record_type_name_alone_does_not_verify_negative_evidence(
    record_type: str,
) -> None:
    record = _record(
        record_type,
        payload={"training_eligible": True},
        training_eligible=True,
    )

    assert record_label_quality(record) is RecordLabelQuality.MISSING
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT


def test_counterexample_native_contract_preserves_reasoning_without_price() -> None:
    record = _record(
        "counterexample",
        payload={
            "training_eligible": True,
            "negative_control_reason": "local predicate owner is absent",
            "screening_decision": "AUDIT_ONLY",
        },
        training_eligible=True,
    )

    assert record_label_quality(record) is RecordLabelQuality.VERIFIED
    assert record_routing_disposition(record) is RecordRoutingDisposition.REASONING
    assert record_memory_lanes(record) == frozenset({"counterexamples"})


@pytest.mark.parametrize(
    ("record_type", "payload"),
    [
        (
            "counterexample",
            {"training_eligible": True, "response_class": "NEGATIVE"},
        ),
        (
            "counterexample",
            {
                "training_eligible": True,
                "label_quality": "verified",
                "response_class": "NEGATIVE",
            },
        ),
        (
            "newsless_or_unexplained_case",
            {"training_eligible": True, "response_class": "POSITIVE"},
        ),
        (
            "newsless_or_unexplained_case",
            {
                "training_eligible": True,
                "label_quality": "verified",
                "high_return_pct": 18.0,
            },
        ),
        (
            "negative_control_case",
            {"training_eligible": True, "response_class": "NEGATIVE"},
        ),
    ],
)
def test_native_type_contract_cannot_be_bypassed_by_declared_labels(
    record_type: str,
    payload: dict[str, object],
) -> None:
    record = _record(record_type, payload=payload, training_eligible=True)

    assert record_label_quality(record) is RecordLabelQuality.MISSING
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT


def test_newsless_native_contract_requires_assertion_and_outcome() -> None:
    missing_outcome = _record(
        "newsless_or_unexplained_case",
        payload={"training_eligible": True, "no_catalyst_asserted": True},
        training_eligible=True,
    )
    complete = _record(
        "newsless_or_unexplained_case",
        payload={
            "training_eligible": True,
            "no_catalyst_asserted": True,
            "outcome_high_return_pct": 18.0,
        },
        training_eligible=True,
    )

    assert record_label_quality(missing_outcome) is RecordLabelQuality.MISSING
    assert record_label_quality(complete) is RecordLabelQuality.VERIFIED
    assert record_routing_disposition(complete) is RecordRoutingDisposition.REASONING


def test_newsless_native_assertion_supports_nested_alias_and_rejects_conflict() -> None:
    nested = _record(
        "newsless_or_unexplained_case",
        payload={
            "training_eligible": True,
            "payload": {
                "no_catalyst_asserted": True,
                "outcome_high_return_pct": 18.0,
            },
        },
        training_eligible=True,
    )
    conflicting = _record(
        "newsless_or_unexplained_case",
        payload={
            "training_eligible": True,
            "no_catalyst_asserted": True,
            "payload": {
                "no_catalyst_asserted": False,
                "outcome_high_return_pct": 18.0,
            },
        },
        training_eligible=True,
    )

    assert record_label_quality(nested) is RecordLabelQuality.VERIFIED
    assert record_routing_disposition(nested) is RecordRoutingDisposition.REASONING
    assert record_label_quality(conflicting) is RecordLabelQuality.CONFLICTING
    assert record_routing_disposition(conflicting) is RecordRoutingDisposition.AUDIT


@pytest.mark.parametrize("invalid_label", ["MAGIC_RESPONSE", "", None, 123, False])
def test_present_but_invalid_response_label_fails_closed(
    invalid_label: object,
) -> None:
    record = _record(
        "supervised_direct_event_case",
        payload={
            "training_eligible": True,
            "response_class": invalid_label,
            "high_return_pct": 8.0,
        },
        training_eligible=True,
    )

    assert record_label_quality(record) is RecordLabelQuality.AMBIGUOUS
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


@pytest.mark.parametrize("provenance", [[""], ["   "], ["SRC-1", ""]])
def test_blank_provenance_ids_fail_closed(provenance: list[str]) -> None:
    record = _record(
        "supervised_direct_event_case",
        payload={
            "training_eligible": True,
            "response_class": "POSITIVE",
            "label_quality": "verified",
        },
        training_eligible=True,
    ).model_copy(update={"provenance_source_ids": provenance})

    assert record_label_quality(record) is RecordLabelQuality.MISSING
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT


@pytest.mark.parametrize(
    "record_type",
    ["candidate_generation_error_case", "blind_leader_preference_pair", "memory_claim"],
)
def test_blank_provenance_blocks_every_runtime_disposition(record_type: str) -> None:
    record = _record(
        record_type,
        payload={"training_eligible": True},
        training_eligible=True,
    ).model_copy(update={"provenance_source_ids": ["   "]})

    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


@pytest.mark.parametrize("phase", ["UNKNOWN", "AUDIT ", "POSTMORTEM typo", ""])
def test_unknown_or_malformed_evidence_phase_fails_closed(phase: str) -> None:
    record = _record(
        "supervised_direct_event_case",
        payload={
            "training_eligible": True,
            "response_class": "POSITIVE",
            "label_quality": "verified",
        },
        training_eligible=True,
    ).model_copy(update={"evidence_phase": phase})

    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "response_class": "POSITIVE",
            "D_outcome": {
                "response_class": "NEGATIVE",
                "high_return_pct": 10.0,
            },
        },
        {
            "training_eligible": True,
            "high_return_pct": 10.0,
            "D_outcome": {"high_return_pct": -5.0},
        },
        {
            "training_eligible": True,
            "response_class": "NEAR_MISS",
            "high_return_pct": 20.0,
        },
        {
            "training_eligible": True,
            "response_class": "NEGATIVE",
            "high_return_pct": 12.0,
        },
        {"training_eligible": True, "high_return_pct": "NaN"},
        {"training_eligible": True, "high_return_pct": "Infinity"},
        {"training_eligible": True, "high_return_pct": "-Infinity"},
        {
            "training_eligible": True,
            "upper_limit_touched": "true",
            "D_outcome": {"is_upper_limit": "false"},
        },
        {"training_eligible": True, "upper_limit_touched": "maybe"},
    ],
)
def test_conflicting_or_non_finite_outcomes_fail_closed(
    payload: dict[str, object],
) -> None:
    record = _record(
        "supervised_direct_event_case",
        payload=payload,
        training_eligible=True,
    )

    assert record_label_quality(record) is RecordLabelQuality.CONFLICTING
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()
    assert record_is_positive_support(record) is False


@pytest.mark.parametrize(
    ("record_type", "payload"),
    [
        (
            "supervised_direct_event_case",
            {
                "training_eligible": True,
                "response_class": "POSITIVE",
                "label_quality": "verified",
            },
        ),
        (
            "negative_control_case",
            {"training_eligible": True, "control_class": "WEAK"},
        ),
    ],
)
def test_explicit_labels_without_provenance_do_not_enter_reasoning(
    record_type: str,
    payload: dict[str, object],
) -> None:
    record = _record(
        record_type,
        payload=payload,
        training_eligible=True,
    ).model_copy(update={"provenance_source_ids": []})

    assert record_label_quality(record) is RecordLabelQuality.MISSING
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT
    assert record_memory_lanes(record) == frozenset()


@pytest.mark.parametrize(
    ("training_mirror", "expected_quality"),
    [
        (None, RecordLabelQuality.MISSING),
        (False, RecordLabelQuality.CONFLICTING),
    ],
)
def test_legacy_explicit_label_requires_training_eligibility_mirror(
    training_mirror: bool | None,
    expected_quality: RecordLabelQuality,
) -> None:
    payload: dict[str, object] = {
        "response_class": "POSITIVE",
        "high_return_pct": 10.0,
    }
    if training_mirror is not None:
        payload["training_eligible"] = training_mirror
    record = _record(
        "supervised_direct_event_case",
        payload=payload,
        training_eligible=True,
    )

    assert record_evidence_polarity(record) is RecordEvidencePolarity.POSITIVE
    assert record_label_quality(record) is expected_quality
    assert record_routing_disposition(record) is RecordRoutingDisposition.AUDIT


def test_outcome_payload_preserves_legacy_d_response_alias() -> None:
    payload = {"payload": {"D_response": {"high_return_pct": 7.5, "close_return_pct": 2.0}}}

    assert record_outcome_payload(payload) == {
        "high_return_pct": 7.5,
        "close_return_pct": 2.0,
    }


def _record(
    record_type: str,
    *,
    payload: dict[str, object],
    training_eligible: bool,
) -> BrainRecordEnvelope:
    normalized = {
        "record_id": f"REC-{record_type}",
        "record_type": record_type,
        **payload,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    typed_status = "UNKNOWN_TYPED_PAYLOAD" if record_type == "future_unknown_case" else "KNOWN_TYPED_PAYLOAD"
    return BrainRecordEnvelope(
        record_id=f"REC-{record_type}",
        record_type=record_type,
        episode_id="NSLAB-20300110-ROUTING",
        trade_date=date(2030, 1, 10),
        available_from=datetime(2030, 1, 11, tzinfo=KST),
        training_target="unit_test",
        evidence_phase="POSTMORTEM",
        training_eligible=training_eligible,
        eligibility_reason="unit test",
        status="supported",
        confidence_label="medium",
        provenance_source_ids=["SRC-1"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status=typed_status,
        payload=normalized,
    )
