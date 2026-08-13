from datetime import date, datetime

import pytest

from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import (
    CANDIDATE_GENERATION_ERRORS_LANE,
    NEGATIVE_CONTROLS_LANE,
    NEWSLESS_OR_UNEXPLAINED_LANE,
    POSITIVE_ANALOGS_LANE,
    THEME_FORMATION_FAILURES_LANE,
    RecordEvidencePolarity,
    record_evidence_polarity,
    record_memory_lanes,
    record_outcome_payload,
)
from news_scalping_lab.utils import KST, canonical_json, sha256_text


@pytest.mark.parametrize(
    ("record_type", "payload", "eligible", "polarity", "lanes"),
    [
        (
            "supervised_direct_event_case",
            {"D_response": {"high_return_pct": 12.5, "close_return_pct": 8.0}},
            True,
            RecordEvidencePolarity.POSITIVE,
            {POSITIVE_ANALOGS_LANE},
        ),
        (
            "supervised_direct_event_case",
            {"D_outcome": {"high_return_pct": 1.2, "close_return_pct": -0.5}},
            True,
            RecordEvidencePolarity.NEGATIVE,
            {NEGATIVE_CONTROLS_LANE},
        ),
        (
            "negative_control_case",
            {"high_return_pct": 2.0, "control_class": "WEAK"},
            True,
            RecordEvidencePolarity.NEGATIVE,
            {NEGATIVE_CONTROLS_LANE},
        ),
        (
            "newsless_or_unexplained_case",
            {"outcome_high_return_pct": 24.0, "no_catalyst_asserted": True},
            False,
            RecordEvidencePolarity.UNEXPLAINED,
            {NEWSLESS_OR_UNEXPLAINED_LANE},
        ),
        (
            "supervised_theme_formation_case",
            {"response_class": "NO_RESPONSE"},
            True,
            RecordEvidencePolarity.NEGATIVE,
            {NEGATIVE_CONTROLS_LANE, THEME_FORMATION_FAILURES_LANE},
        ),
        (
            "candidate_generation_error_case",
            {"error_type": "MISSED_WINNER"},
            True,
            RecordEvidencePolarity.NEAR_MISS,
            {"near_misses", CANDIDATE_GENERATION_ERRORS_LANE},
        ),
    ],
)
def test_record_routing_separates_quality_from_outcome_direction(
    record_type: str,
    payload: dict[str, object],
    eligible: bool,
    polarity: RecordEvidencePolarity,
    lanes: set[str],
) -> None:
    record = _record(record_type, payload=payload, training_eligible=eligible)

    assert record_evidence_polarity(record) is polarity
    assert set(record_memory_lanes(record)) == lanes


def test_ineligible_positive_outcome_is_near_miss_not_positive_support() -> None:
    record = _record(
        "supervised_issuer_day_case",
        payload={"payload": {"high_return_pct": 15.0}},
        training_eligible=False,
    )

    assert record_evidence_polarity(record) is RecordEvidencePolarity.NEAR_MISS
    assert POSITIVE_ANALOGS_LANE not in record_memory_lanes(record)
    assert "near_misses" in record_memory_lanes(record)


def test_outcome_payload_preserves_legacy_d_response_alias() -> None:
    payload = {
        "payload": {
            "D_response": {"high_return_pct": 7.5, "close_return_pct": 2.0}
        }
    }

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
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=normalized,
    )
