"""Coverage audit wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from news_scalping_lab.brain.audit import audit_brain
from news_scalping_lab.contracts.models import ResearchEpisode
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.store import inspect_vector_index
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import read_json
from news_scalping_lab.warehouse import EXPECTED_WAREHOUSE_FILES, WarehouseStore


def audit_coverage(root: Path) -> dict[str, object]:
    brain = audit_brain(root)
    accepted_episode_count = _int_value(brain.get("accepted_episode_count"))
    accepted_episodes = ResearchStore(root).list_accepted()
    records = BrainRecordStore(root).list_records()
    vector_index = inspect_vector_index(root)
    warehouse_counts = WarehouseStore(root).counts()
    warehouse_research_episode_count = _int_value(
        warehouse_counts.get("research_episodes.parquet")
    )
    warehouse_expected_source_counts = _warehouse_expected_source_counts(
        root,
        accepted_episodes,
        records=records,
    )
    warehouse_count_mismatches = _warehouse_count_mismatches(
        warehouse_counts,
        warehouse_expected_source_counts,
    )
    warehouse_identity_expectations = _warehouse_identity_expectations(root, accepted_episodes)
    warehouse_identity_mismatches = _warehouse_identity_mismatches(
        root,
        warehouse_identity_expectations,
    )
    missing_warehouse_files = [
        filename
        for filename in EXPECTED_WAREHOUSE_FILES
        if not (root / "warehouse" / filename).exists()
    ]
    unreadable_warehouse_files = [
        filename
        for filename in EXPECTED_WAREHOUSE_FILES
        if isinstance(warehouse_counts.get(filename), str)
    ]
    vector_index_current = vector_index.get("status") == "current"
    warehouse_synced = warehouse_research_episode_count == accepted_episode_count
    warehouse_projection_synced = not warehouse_count_mismatches and not (
        warehouse_identity_mismatches
    )
    warehouse_required_files_present = (
        not missing_warehouse_files and not unreadable_warehouse_files
    )
    findings = [
        f"brain: {finding}"
        for field in (
            "missing_episode_ids",
            "extra_episode_ids",
            "claims_without_support",
            "claims_with_unknown_support",
            "claims_without_provenance",
            "claim_temporal_leaks",
            "mechanisms_without_cases",
            "mechanisms_with_unknown_success_cases",
            "mechanisms_without_provenance",
            "invalid_claim_lines",
            "invalid_mechanism_lines",
            "determinism_findings",
        )
        for finding in _string_items(brain.get(field))
    ]
    if not vector_index_current:
        findings.append(f"vector_index: status is {vector_index.get('status')}")
    if not warehouse_synced:
        findings.append(
            "warehouse: research_episodes.parquet count "
            f"{warehouse_research_episode_count} != accepted_episode_count "
            f"{accepted_episode_count}"
        )
    for filename in missing_warehouse_files:
        findings.append(f"warehouse: missing required parquet file: {filename}")
    for filename in unreadable_warehouse_files:
        findings.append(f"warehouse: unreadable required parquet file: {filename}")
    for filename, count_mismatch in warehouse_count_mismatches.items():
        label = warehouse_expected_source_counts[filename]["source_label"]
        findings.append(
            f"warehouse: {filename} count {count_mismatch['actual']} != "
            f"{label} count {count_mismatch['expected']}"
        )
    for filename, identity_mismatch in warehouse_identity_mismatches.items():
        expectation = warehouse_identity_expectations[filename]
        missing = ", ".join(identity_mismatch["missing"]) or "none"
        extra = ", ".join(identity_mismatch["extra"]) or "none"
        findings.append(
            f"warehouse: {filename} ids mismatch; missing "
            f"{expectation['source_label']}: {missing}; extra projected ids: {extra}"
        )
    return {
        **brain,
        "passed": (
            bool(brain.get("passed"))
            and vector_index_current
            and warehouse_synced
            and warehouse_projection_synced
            and warehouse_required_files_present
        ),
        "findings": findings,
        "vector_index": vector_index,
        "vector_index_current": vector_index_current,
        "warehouse_counts": warehouse_counts,
        "warehouse_expected_source_counts": warehouse_expected_source_counts,
        "warehouse_count_mismatches": warehouse_count_mismatches,
        "warehouse_identity_mismatches": warehouse_identity_mismatches,
        "warehouse_research_episode_count": warehouse_research_episode_count,
        "warehouse_synced": warehouse_synced,
        "warehouse_projection_synced": warehouse_projection_synced,
        "warehouse_required_files": list(EXPECTED_WAREHOUSE_FILES),
        "warehouse_missing_files": missing_warehouse_files,
        "warehouse_unreadable_files": unreadable_warehouse_files,
        "warehouse_required_files_present": warehouse_required_files_present,
    }


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0


def _warehouse_expected_source_counts(
    root: Path,
    accepted_episodes: list[ResearchEpisode],
    *,
    records: list[Any],
) -> dict[str, dict[str, int | str]]:
    accepted_counts = _accepted_episode_projection_counts(accepted_episodes)
    record_counts = _record_projection_counts(records)
    return {
        "events.parquet": {
            "expected": accepted_counts["events"],
            "source_label": "accepted observed events",
        },
        "event_sources.parquet": {
            "expected": accepted_counts["event_sources"],
            "source_label": "accepted event sources",
        },
        "event_ticker_edges.parquet": {
            "expected": accepted_counts["event_ticker_edges"],
            "source_label": "accepted event ticker edges",
        },
        "market_memory.parquet": {
            "expected": accepted_counts["market_memory"],
            "source_label": "accepted market memory claims",
        },
        "predictions.parquet": {
            "expected": len(list((root / "predictions").glob("*.json"))),
            "source_label": "source predictions",
        },
        "daily_outcomes.parquet": {
            "expected": len(list((root / "reports").glob("*_postmortem.json"))),
            "source_label": "source postmortem reports",
        },
        "company_memory.parquet": {
            "expected": len(list((root / "memory" / "company_memory").glob("*.json"))),
            "source_label": "source company memory files",
        },
        "mechanism_memory.parquet": {
            "expected": _nonempty_jsonl_line_count(
                root / "memory" / "mechanisms" / "current" / "mechanisms.jsonl"
            ),
            "source_label": "source mechanism memory records",
        },
        "brain_records.parquet": {
            "expected": record_counts["brain_records"],
            "source_label": "normalized brain records",
        },
        "issuer_day_cases.parquet": {
            "expected": record_counts["issuer_day_cases"],
            "source_label": "issuer-day brain records",
        },
        "direct_event_cases.parquet": {
            "expected": record_counts["direct_event_cases"],
            "source_label": "direct event brain records",
        },
        "theme_formation_cases.parquet": {
            "expected": record_counts["theme_formation_cases"],
            "source_label": "theme formation brain records",
        },
        "beneficiary_cases.parquet": {
            "expected": record_counts["beneficiary_cases"],
            "source_label": "beneficiary discovery brain records",
        },
        "leader_pairs.parquet": {
            "expected": record_counts["leader_pairs"],
            "source_label": "sealed leader preference pair records",
        },
        "error_cases.parquet": {
            "expected": record_counts["error_cases"],
            "source_label": "brain error case records",
        },
        "memory_claims.parquet": {
            "expected": record_counts["memory_claims"],
            "source_label": "brain memory claim records",
        },
        "research_questions.parquet": {
            "expected": record_counts["research_questions"],
            "source_label": "research question records",
        },
        "record_provenance.parquet": {
            "expected": record_counts["record_provenance"],
            "source_label": "record provenance links",
        },
        "record_coverage.parquet": {
            "expected": record_counts["record_coverage"],
            "source_label": "episode/type record coverage groups",
        },
    }


def _warehouse_identity_expectations(
    root: Path,
    accepted_episodes: list[ResearchEpisode],
) -> dict[str, dict[str, Any]]:
    return {
        "research_episodes.parquet": {
            "columns": ("episode_id",),
            "expected": sorted(episode.episode_id for episode in accepted_episodes),
            "source_label": "accepted episode ids",
        },
        "events.parquet": {
            "columns": ("episode_id", "event_id"),
            "expected": sorted(
                _identity(episode.episode_id, event.event_id)
                for episode in accepted_episodes
                for event in episode.observed_events
            ),
            "source_label": "accepted event ids",
        },
        "event_sources.parquet": {
            "columns": ("episode_id", "event_id", "source_id", "uri"),
            "expected": sorted(
                _identity(episode.episode_id, event.event_id, source.source_id, source.uri)
                for episode in accepted_episodes
                for event in episode.observed_events
                for source in event.provenance
            ),
            "source_label": "accepted event source ids",
        },
        "event_ticker_edges.parquet": {
            "columns": ("episode_id", "edge_id"),
            "expected": sorted(
                _identity(edge.episode_id, edge.edge_id)
                for episode in accepted_episodes
                for edge in episode.event_ticker_edges
            ),
            "source_label": "accepted event ticker edge ids",
        },
        "market_memory.parquet": {
            "columns": ("claim_id",),
            "expected": sorted(
                claim.claim_id
                for episode in accepted_episodes
                for claim in [*episode.lessons, *episode.counterexamples]
            ),
            "source_label": "accepted market memory claim ids",
        },
        "predictions.parquet": {
            "columns": ("prediction_id",),
            "expected": _source_prediction_ids(root),
            "source_label": "source predictions",
        },
        "daily_outcomes.parquet": {
            "columns": ("trade_date", "blind_prediction_id"),
            "expected": _source_daily_outcome_ids(root),
            "source_label": "source postmortem report ids",
        },
        "company_memory.parquet": {
            "columns": ("ticker", "company_name"),
            "expected": _source_company_memory_ids(root),
            "source_label": "source company memory ids",
        },
        "mechanism_memory.parquet": {
            "columns": ("mechanism_id",),
            "expected": _source_mechanism_memory_ids(root),
            "source_label": "source mechanism memory ids",
        },
    }


def _warehouse_identity_mismatches(
    root: Path,
    expectations: dict[str, dict[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    mismatches: dict[str, dict[str, list[str]]] = {}
    for filename, expectation in expectations.items():
        expected = set(_string_items(expectation.get("expected")))
        if not expected:
            continue
        actual = set(
            _warehouse_identity_values(
                root / "warehouse" / filename,
                tuple(expectation["columns"]),
            )
        )
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            mismatches[filename] = {"missing": missing, "extra": extra}
    return mismatches


def _warehouse_identity_values(path: Path, columns: tuple[str, ...]) -> list[str]:
    if not path.exists():
        return []
    escaped_path = path.as_posix().replace("'", "''")
    expression = " || '|' || ".join(
        f"coalesce(cast({column} as varchar), '')" for column in columns
    )
    try:
        rows = duckdb.sql(
            f"select {expression} as identity from read_parquet('{escaped_path}')"
        ).fetchall()
    except duckdb.Error:
        return []
    return [row[0] for row in rows if isinstance(row[0], str) and row[0]]


def _source_prediction_ids(root: Path) -> list[str]:
    prediction_ids: list[str] = []
    for path in sorted((root / "predictions").glob("*.json")):
        data = read_json(path)
        prediction_id = data.get("prediction_id")
        if isinstance(prediction_id, str) and prediction_id:
            prediction_ids.append(prediction_id)
    return sorted(prediction_ids)


def _source_daily_outcome_ids(root: Path) -> list[str]:
    outcome_ids: list[str] = []
    for path in sorted((root / "reports").glob("*_postmortem.json")):
        data = read_json(path)
        trade_date = data.get("trade_date")
        prediction_id = data.get("blind_prediction_id")
        if isinstance(trade_date, str) and isinstance(prediction_id, str):
            outcome_ids.append(_identity(trade_date, prediction_id))
    return sorted(outcome_ids)


def _source_company_memory_ids(root: Path) -> list[str]:
    company_ids: list[str] = []
    for path in sorted((root / "memory" / "company_memory").glob("*.json")):
        data = read_json(path)
        ticker = data.get("ticker")
        company_name = data.get("company_name")
        if isinstance(ticker, str) and isinstance(company_name, str):
            company_ids.append(_identity(ticker, company_name))
    return sorted(company_ids)


def _source_mechanism_memory_ids(root: Path) -> list[str]:
    path = root / "memory" / "mechanisms" / "current" / "mechanisms.jsonl"
    if not path.exists():
        return []
    mechanism_ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        mechanism_id = data.get("mechanism_id") if isinstance(data, dict) else None
        if isinstance(mechanism_id, str) and mechanism_id:
            mechanism_ids.append(mechanism_id)
    return sorted(mechanism_ids)


def _identity(*parts: object) -> str:
    return "|".join(str(part) for part in parts)


def _accepted_episode_projection_counts(episodes: list[ResearchEpisode]) -> dict[str, int]:
    event_sources: set[tuple[str, str, str, str]] = set()
    for episode in episodes:
        for event in episode.observed_events:
            for source in event.provenance:
                event_sources.add(
                    (episode.episode_id, event.event_id, source.source_id, source.uri)
                )
    return {
        "events": sum(len(episode.observed_events) for episode in episodes),
        "event_sources": len(event_sources),
        "event_ticker_edges": sum(len(episode.event_ticker_edges) for episode in episodes),
        "market_memory": sum(
            len(episode.lessons) + len(episode.counterexamples)
            for episode in episodes
        ),
    }


def _record_projection_counts(records: list[Any]) -> dict[str, int]:
    grouped = {
        (
            str(getattr(record, "episode_id", "")),
            str(getattr(record, "record_type", "")),
        )
        for record in records
    }
    return {
        "brain_records": len(records),
        "issuer_day_cases": sum(
            1
            for record in records
            if getattr(record, "record_type", None) == "supervised_issuer_day_case"
        ),
        "direct_event_cases": sum(
            1
            for record in records
            if getattr(record, "record_type", None) == "supervised_direct_event_case"
        ),
        "theme_formation_cases": sum(
            1
            for record in records
            if getattr(record, "record_type", None) == "supervised_theme_formation_case"
        ),
        "beneficiary_cases": sum(
            1
            for record in records
            if getattr(record, "record_type", None) == "beneficiary_discovery_case"
        ),
        "leader_pairs": sum(
            1
            for record in records
            if getattr(record, "record_type", None) == "blind_leader_preference_pair"
        ),
        "error_cases": sum(
            1
            for record in records
            if str(getattr(record, "record_type", "")).endswith("_error_case")
        ),
        "memory_claims": sum(
            1
            for record in records
            if getattr(record, "record_type", None)
            in {"memory_claim", "mechanism_memory", "counterexample"}
        ),
        "research_questions": sum(
            1
            for record in records
            if getattr(record, "record_type", None) == "research_question"
        ),
        "record_provenance": sum(
            len(getattr(record, "provenance_source_ids", [])) for record in records
        ),
        "record_coverage": len(grouped),
    }


def _nonempty_jsonl_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _warehouse_count_mismatches(
    warehouse_counts: dict[str, int | str],
    expected_counts: dict[str, dict[str, int | str]],
) -> dict[str, dict[str, int]]:
    mismatches: dict[str, dict[str, int]] = {}
    for filename, expected_payload in expected_counts.items():
        expected = expected_payload["expected"]
        if not isinstance(expected, int):
            continue
        actual = _int_value(warehouse_counts.get(filename))
        if actual != expected:
            mismatches[filename] = {"actual": actual, "expected": expected}
    return mismatches


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
