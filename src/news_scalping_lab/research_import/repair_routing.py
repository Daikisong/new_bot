"""Deterministic routing for non-standard research bundle terminal states."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from news_scalping_lab.research_import.repair_census import census_source
from news_scalping_lab.research_import.repair_models import (
    RepairTaskState,
    SourceCensus,
)


def classify_repair_source(
    path: Path,
    *,
    census: SourceCensus | None = None,
) -> tuple[RepairTaskState, str]:
    """Classify receipts and unsafe runs before the episode repair path."""

    observed_census = census or census_source(path)
    if not observed_census.strict_utf8_ok:
        return RepairTaskState.FATAL_INPUT_FAILURE, "source_is_not_strict_utf8"

    front = _front_matter(path)
    if _is_deferred_non_trading(front):
        if observed_census.explicit_record_count != 0:
            return (
                RepairTaskState.ADAPTER_REQUIRED,
                "deferred_receipt_contains_explicit_brain_records",
            )
        return RepairTaskState.DEFERRED_NON_TRADING, "verified_deferred_non_trading_receipt"

    if _is_pending_price_source(front):
        return (
            RepairTaskState.PARTIAL_PRICE_SOURCE_MISSING,
            "blind_research_preserved_pending_outcome_source",
        )

    # A discovered-looking markdown wrapper can still be a deliberately
    # quarantined run.  When its source contains no brain_delta records and
    # explicitly says that it is not brain eligible, there is nothing for a
    # mechanical repair to recover.  Route it to a preserved terminal state
    # before invoking repair/import rather than allowing empty-set parity to
    # produce a false PASS.
    if observed_census.explicit_record_count == 0 and _declares_no_brain_payload(front):
        return (
            RepairTaskState.PRESERVED_SOURCE_PAYLOAD_ABSENT,
            "source_declares_no_brain_payload",
        )

    if _declares_quarantined_run(front):
        return (
            RepairTaskState.PRESERVED_PARTIAL_NOT_CURRENT_GOLD,
            "source_declares_quarantine_status",
        )

    if observed_census.unclaimed_machine_payloads:
        return (
            RepairTaskState.ADAPTER_REQUIRED,
            "unclaimed_machine_payload_requires_adapter",
        )

    return RepairTaskState.DISCOVERED, "standard_episode_repair_candidate"


def _front_matter(path: Path) -> dict[str, Any]:
    lines: list[str] = []
    byte_budget = 1024 * 1024
    observed_bytes = 0
    with path.open("r", encoding="utf-8-sig", errors="strict") as handle:
        if handle.readline().strip() != "---":
            return {}
        for line in handle:
            observed_bytes += len(line.encode("utf-8"))
            if observed_bytes > byte_budget:
                return {}
            if line.strip() == "---":
                break
            lines.append(line)
        else:
            return {}
    source = "".join(lines)
    try:
        loaded = yaml.safe_load(source)
    except yaml.YAMLError:
        # A few legacy bundles put an unescaped JSON object inside a quoted
        # YAML scalar (for example canonical_graph_object_counts). Routing
        # only needs the scalar front-matter flags; recover those line by line
        # without rewriting or trusting the malformed value as structured data.
        loaded = _fallback_front_matter(source)
    return loaded if isinstance(loaded, dict) else {}


def _fallback_front_matter(source: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in source.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key or any(char.isspace() for char in key):
            continue
        value = raw_value.strip()
        try:
            result[key] = yaml.safe_load(value)
        except yaml.YAMLError:
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                result[key] = value[1:-1]
            else:
                result[key] = value
    return result


def _is_deferred_non_trading(front: dict[str, Any]) -> bool:
    return all(
        (
            front.get("schema_version") == "nslab.deferred_input.v1",
            front.get("artifact_type") == "deferred_non_trading_day",
            front.get("status") == "DEFERRED_NON_TRADING_DAY",
            front.get("brain_eligible") is False,
            front.get("outcome_research_performed") is False,
            front.get("merge_required") is False,
            front.get("covered_by_next_trading_day_csv") is True,
            isinstance(front.get("input_sha256"), str),
            isinstance(front.get("calendar_date"), (str, date)),
        )
    )


def _is_pending_price_source(front: dict[str, Any]) -> bool:
    bundle_status = str(front.get("bundle_status", "")).upper()
    status = str(front.get("status", "")).upper()
    outcome_status = str(front.get("outcome_status", "")).upper()
    pending_declared = bundle_status in {"PENDING_OUTCOME", "PRICE_SOURCE_MISSING"}
    outcome_missing = "MISSING" in outcome_status or "PENDING" in status
    return all(
        (
            pending_declared,
            outcome_missing,
            front.get("blind_valid") is True,
            front.get("brain_eligible") is False,
            isinstance(front.get("episode_id"), str),
            isinstance(front.get("trade_date"), (str, date)),
        )
    )


def _declares_no_brain_payload(front: dict[str, Any]) -> bool:
    bundle_status = str(front.get("bundle_status", "")).upper()
    declared_count = front.get("brain_delta_count")
    return (
        declared_count in {0, "0"}
        and (
            front.get("brain_eligible") is False
        or front.get("direct_brain_ingest_ready") is False
        or bundle_status.startswith(("QUARANTINE", "BLOCKED"))
        )
    )


def _declares_quarantined_run(front: dict[str, Any]) -> bool:
    statuses = (
        str(front.get("bundle_status", "")).upper(),
        str(front.get("status", "")).upper(),
    )
    if any(status.startswith(("QUARANTINE", "BLOCKED")) for status in statuses):
        return True
    return (
        front.get("blind_valid") is False
        and front.get("brain_eligible") is False
        and front.get("outcome_research_performed") is True
    )


__all__ = ["classify_repair_source"]
