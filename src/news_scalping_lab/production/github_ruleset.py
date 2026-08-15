"""Expected single-operator GitHub ruleset and server-response inspection."""

from __future__ import annotations

from typing import Any

GITHUB_RULESET_NAME = "main-quality-gate"
GITHUB_REQUIRED_APPROVAL_COUNT = 0
GITHUB_REQUIRED_STATUS_CHECK = "quality-gate"


def expected_main_ruleset_payload() -> dict[str, Any]:
    """Return the exact server policy expected for the protected main branch."""

    return {
        "name": GITHUB_RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {
            "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": (
                        GITHUB_REQUIRED_APPROVAL_COUNT
                    ),
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": True,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [
                        {"context": GITHUB_REQUIRED_STATUS_CHECK}
                    ],
                },
            },
        ],
    }


def inspect_main_ruleset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a GitHub API ruleset response into secret-free policy checks."""

    raw_rules = payload.get("rules")
    rules = (
        {
            str(rule.get("type")): rule
            for rule in raw_rules
            if isinstance(rule, dict) and isinstance(rule.get("type"), str)
        }
        if isinstance(raw_rules, list)
        else {}
    )
    pull_parameters = _parameters(rules.get("pull_request"))
    status_parameters = _parameters(rules.get("required_status_checks"))
    status_checks = status_parameters.get("required_status_checks")
    contexts = {
        str(item.get("context"))
        for item in status_checks
        if isinstance(item, dict) and isinstance(item.get("context"), str)
    } if isinstance(status_checks, list) else set()
    bypass = payload.get("bypass_actors")
    conditions = payload.get("conditions")
    ref_name = (
        conditions.get("ref_name") if isinstance(conditions, dict) else None
    )
    checks = {
        "name": payload.get("name") == GITHUB_RULESET_NAME,
        "active": payload.get("enforcement") == "active",
        "target_branch": payload.get("target") == "branch",
        "default_branch_only": ref_name
        == {"include": ["~DEFAULT_BRANCH"], "exclude": []},
        "required_approval_count_zero": pull_parameters.get(
            "required_approving_review_count"
        )
        == GITHUB_REQUIRED_APPROVAL_COUNT,
        "conversation_resolution": pull_parameters.get(
            "required_review_thread_resolution"
        )
        is True,
        "dismiss_stale_reviews": pull_parameters.get(
            "dismiss_stale_reviews_on_push"
        )
        is True,
        "no_code_owner_requirement": pull_parameters.get(
            "require_code_owner_review"
        )
        is False,
        "no_last_push_approval": pull_parameters.get(
            "require_last_push_approval"
        )
        is False,
        "quality_gate_required": GITHUB_REQUIRED_STATUS_CHECK in contexts,
        "branch_up_to_date": status_parameters.get(
            "strict_required_status_checks_policy"
        )
        is True,
        "status_checks_enforced_on_create": status_parameters.get(
            "do_not_enforce_on_create"
        )
        is False,
        "deletion_blocked": "deletion" in rules,
        "force_push_blocked": "non_fast_forward" in rules,
        "no_bypass": bypass in (None, []),
    }
    return {
        "schema_version": "nslab.github_ruleset_status.v1",
        "ruleset_id": payload.get("id"),
        "name": payload.get("name"),
        "enforcement": payload.get("enforcement"),
        "required_approving_review_count": pull_parameters.get(
            "required_approving_review_count"
        ),
        "required_status_checks": sorted(contexts),
        "checks": checks,
        "passed": all(checks.values()),
    }


def _parameters(rule: object) -> dict[str, Any]:
    if not isinstance(rule, dict):
        return {}
    parameters = rule.get("parameters")
    return parameters if isinstance(parameters, dict) else {}
