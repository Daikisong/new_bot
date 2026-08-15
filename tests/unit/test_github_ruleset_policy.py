from __future__ import annotations

from copy import deepcopy

from news_scalping_lab.production.github_ruleset import (
    expected_main_ruleset_payload,
    inspect_main_ruleset_payload,
)


def test_ruleset_expected_required_approval_count_is_zero() -> None:
    inspected = inspect_main_ruleset_payload(expected_main_ruleset_payload())

    assert inspected["required_approving_review_count"] == 0
    assert inspected["checks"]["required_approval_count_zero"] is True


def test_ruleset_still_requires_quality_gate() -> None:
    inspected = inspect_main_ruleset_payload(expected_main_ruleset_payload())

    assert inspected["checks"]["quality_gate_required"] is True
    assert inspected["checks"]["branch_up_to_date"] is True
    assert inspected["checks"]["conversation_resolution"] is True
    assert inspected["checks"]["default_branch_only"] is True
    assert inspected["checks"]["dismiss_stale_reviews"] is True
    assert inspected["checks"]["status_checks_enforced_on_create"] is True


def test_ruleset_still_blocks_force_push_and_deletion() -> None:
    inspected = inspect_main_ruleset_payload(expected_main_ruleset_payload())

    assert inspected["checks"]["force_push_blocked"] is True
    assert inspected["checks"]["deletion_blocked"] is True
    assert inspected["checks"]["no_bypass"] is True
    assert inspected["passed"] is True


def test_ruleset_inspection_rejects_weakened_policy() -> None:
    payload = deepcopy(expected_main_ruleset_payload())
    payload["rules"] = [
        rule for rule in payload["rules"] if rule["type"] != "non_fast_forward"
    ]

    inspected = inspect_main_ruleset_payload(payload)

    assert inspected["checks"]["force_push_blocked"] is False
    assert inspected["passed"] is False
