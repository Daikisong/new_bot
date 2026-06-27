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
from news_scalping_lab.warehouse import (
    EXPECTED_WAREHOUSE_FILES,
    RECORD_COVERAGE_COLUMNS,
    WarehouseStore,
)


def audit_coverage(root: Path, *, deep: bool = False) -> dict[str, object]:
    brain = audit_brain(root, deep=deep)
    brain_audit_passed = bool(brain.get("passed"))
    brain_audit_findings = _brain_audit_findings(brain)
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
    warehouse_identity_expectations = _warehouse_identity_expectations(
        root,
        accepted_episodes,
        records=records,
    )
    warehouse_identity_mismatches = _warehouse_identity_mismatches(
        root,
        warehouse_identity_expectations,
    )
    warehouse_duplicate_identities = _warehouse_duplicate_identities(
        root,
        _warehouse_duplicate_identity_expectations(),
    )
    warehouse_weight_mismatches = _warehouse_weight_mismatches(root)
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
    warehouse_missing_columns = _warehouse_missing_columns(root)
    vector_index_current = vector_index.get("status") == "current"
    warehouse_synced = warehouse_research_episode_count == accepted_episode_count
    warehouse_projection_synced = not warehouse_count_mismatches and not (
        warehouse_identity_mismatches
        or warehouse_duplicate_identities
        or warehouse_weight_mismatches
        or warehouse_missing_columns
    )
    warehouse_required_files_present = (
        not missing_warehouse_files and not unreadable_warehouse_files
    )
    findings = [f"brain: {finding}" for finding in brain_audit_findings]
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
    for filename, missing_columns in warehouse_missing_columns.items():
        findings.append(
            "warehouse: "
            f"{filename} missing required columns: {', '.join(missing_columns)}"
        )
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
    for filename, duplicates in warehouse_duplicate_identities.items():
        duplicate_values = ", ".join(duplicates)
        findings.append(f"warehouse: {filename} duplicate ids: {duplicate_values}")
    for filename, mismatches in warehouse_weight_mismatches.items():
        mismatch_values = ", ".join(
            f"{identity}={weight_sum}"
            for identity, weight_sum in sorted(mismatches.items())
        )
        findings.append(f"warehouse: {filename} weight sum mismatch: {mismatch_values}")
    return {
        **brain,
        "passed": (
            brain_audit_passed
            and vector_index_current
            and warehouse_synced
            and warehouse_projection_synced
            and warehouse_required_files_present
        ),
        "findings": findings,
        "brain_audit_passed": brain_audit_passed,
        "brain_audit_deep": brain.get("deep"),
        "brain_audit_findings": brain_audit_findings,
        "vector_index": vector_index,
        "vector_index_current": vector_index_current,
        "warehouse_counts": warehouse_counts,
        "warehouse_expected_source_counts": warehouse_expected_source_counts,
        "warehouse_count_mismatches": warehouse_count_mismatches,
        "warehouse_identity_mismatches": warehouse_identity_mismatches,
        "warehouse_duplicate_identities": warehouse_duplicate_identities,
        "warehouse_weight_mismatches": warehouse_weight_mismatches,
        "warehouse_missing_columns": warehouse_missing_columns,
        "warehouse_research_episode_count": warehouse_research_episode_count,
        "warehouse_synced": warehouse_synced,
        "warehouse_projection_synced": warehouse_projection_synced,
        "warehouse_required_files": list(EXPECTED_WAREHOUSE_FILES),
        "warehouse_missing_files": missing_warehouse_files,
        "warehouse_unreadable_files": unreadable_warehouse_files,
        "warehouse_required_files_present": warehouse_required_files_present,
    }


def _warehouse_missing_columns(root: Path) -> dict[str, list[str]]:
    required_columns = {
        "record_coverage.parquet": list(RECORD_COVERAGE_COLUMNS),
    }
    missing: dict[str, list[str]] = {}
    for filename, expected_columns in required_columns.items():
        path = root / "warehouse" / filename
        if not path.exists():
            continue
        escaped_path = path.as_posix().replace("'", "''")
        columns = _warehouse_columns(escaped_path)
        missing_columns = [
            column for column in expected_columns if column not in set(columns)
        ]
        if missing_columns:
            missing[filename] = missing_columns
    return missing


def _brain_audit_findings(brain: dict[str, object]) -> list[str]:
    findings: list[str] = []
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
        "episode_coverage_findings",
        "record_coverage_findings",
        "brain_diversity_findings",
        "llm_compile_findings",
        "compiled_claim_findings",
    ):
        findings.extend(_string_items(brain.get(field)))
    record_store_audit = brain.get("record_store_audit")
    if isinstance(record_store_audit, dict):
        findings.extend(_string_items(record_store_audit.get("findings")))
    return findings


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
            "expected": accepted_counts["event_ticker_edges"]
            + record_counts["event_ticker_edges"],
            "source_label": "accepted event ticker edges plus brain record edge records",
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
    *,
    records: list[Any],
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
            "expected": _event_ticker_edge_projection_ids(accepted_episodes, records),
            "source_label": "accepted event ticker edge and brain record edge ids",
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
        "brain_records.parquet": {
            "columns": ("record_id",),
            "expected": _record_ids(records),
            "source_label": "normalized brain record ids",
        },
        "issuer_day_cases.parquet": {
            "columns": ("record_id",),
            "expected": _record_ids_for_types(records, {"supervised_issuer_day_case"}),
            "source_label": "issuer-day brain record ids",
        },
        "direct_event_cases.parquet": {
            "columns": ("record_id",),
            "expected": _record_ids_for_types(records, {"supervised_direct_event_case"}),
            "source_label": "direct event brain record ids",
        },
        "theme_formation_cases.parquet": {
            "columns": ("record_id",),
            "expected": _record_ids_for_types(
                records,
                {"supervised_theme_formation_case", "theme_formation_case"},
            ),
            "source_label": "theme formation brain record ids",
        },
        "beneficiary_cases.parquet": {
            "columns": ("record_id",),
            "expected": _record_ids_for_types(records, {"beneficiary_discovery_case"}),
            "source_label": "beneficiary discovery brain record ids",
        },
        "leader_pairs.parquet": {
            "columns": ("record_id",),
            "expected": _record_ids_for_types(records, {"blind_leader_preference_pair"}),
            "source_label": "sealed leader preference pair record ids",
        },
        "error_cases.parquet": {
            "columns": ("record_id",),
            "expected": _record_ids_for_suffix(records, "_error_case"),
            "source_label": "brain error case record ids",
        },
        "memory_claims.parquet": {
            "columns": ("record_id",),
            "expected": _record_ids_for_types(
                records,
                {"memory_claim", "mechanism_memory", "counterexample"},
            ),
            "source_label": "brain memory claim record ids",
        },
        "research_questions.parquet": {
            "columns": ("record_id",),
            "expected": _record_ids_for_types(records, {"research_question"}),
            "source_label": "research question record ids",
        },
        "record_provenance.parquet": {
            "columns": ("record_id", "source_id"),
            "expected": _record_provenance_ids(records),
            "source_label": "record provenance links",
        },
        "record_coverage.parquet": {
            "columns": ("episode_id", "record_type", "evidence_phase", "training_target"),
            "expected": _record_coverage_ids(records),
            "source_label": "episode/type/phase/target record coverage groups",
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


def _warehouse_duplicate_identity_expectations() -> dict[str, dict[str, Any]]:
    return {
        "issuer_day_cases.parquet": {
            "columns": ("issuer_day_case_id", "trade_date", "ticker"),
            "source_label": "issuer-day case ids",
        },
    }


def _warehouse_duplicate_identities(
    root: Path,
    expectations: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    duplicates: dict[str, list[str]] = {}
    for filename, expectation in expectations.items():
        duplicate_values = _warehouse_duplicate_identity_values(
            root / "warehouse" / filename,
            tuple(expectation["columns"]),
        )
        if duplicate_values:
            duplicates[filename] = duplicate_values
    return duplicates


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


def _warehouse_duplicate_identity_values(path: Path, columns: tuple[str, ...]) -> list[str]:
    if not path.exists():
        return []
    escaped_path = path.as_posix().replace("'", "''")
    expression = " || '|' || ".join(
        f"coalesce(cast({column} as varchar), '')" for column in columns
    )
    try:
        rows = duckdb.sql(
            "select identity from ("
            f"select {expression} as identity, count(*) as row_count "
            f"from read_parquet('{escaped_path}') "
            "group by identity having count(*) > 1"
            ") order by identity"
        ).fetchall()
    except duckdb.Error:
        return []
    return [row[0] for row in rows if isinstance(row[0], str) and row[0]]


def _warehouse_weight_mismatches(root: Path) -> dict[str, dict[str, float | str]]:
    expectations: dict[str, tuple[tuple[str, ...], str]] = {
        "issuer_day_cases.parquet": (
            (
                "trade_date",
                "ticker",
                "training_eligible",
                "sample_weight",
            ),
            (
                "coalesce(cast(trade_date as varchar), '') || '|' || "
                "coalesce(cast(ticker as varchar), '')"
            ),
        ),
        "direct_event_cases.parquet": (
            (
                "issuer_day_case_id",
                "issuer_day_weight_group_id",
                "trade_date",
                "ticker",
                "training_eligible",
                "sample_weight",
            ),
            (
                "coalesce("
                "nullif(cast(issuer_day_weight_group_id as varchar), ''), "
                "nullif(cast(issuer_day_case_id as varchar), ''), "
                "coalesce(cast(trade_date as varchar), '') || ':' || "
                "coalesce(cast(ticker as varchar), '')"
                ")"
            ),
        ),
    }
    mismatches: dict[str, dict[str, float | str]] = {}
    for filename, (required_columns, identity_expression) in expectations.items():
        values = _warehouse_weight_sum_mismatches(
            root / "warehouse" / filename,
            required_columns=required_columns,
            identity_expression=identity_expression,
        )
        if values:
            mismatches[filename] = values
    return mismatches


def _warehouse_weight_sum_mismatches(
    path: Path,
    *,
    required_columns: tuple[str, ...],
    identity_expression: str,
) -> dict[str, float | str]:
    if not path.exists():
        return {}
    escaped_path = path.as_posix().replace("'", "''")
    columns = _warehouse_columns(escaped_path)
    if columns == ["_empty"]:
        return {}
    missing_columns = [column for column in required_columns if column not in columns]
    if missing_columns:
        return {"__missing_columns__": ", ".join(missing_columns)}
    try:
        rows = duckdb.sql(
            "select identity, round(weight_sum, 12) as weight_sum from ("
            f"select {identity_expression} as identity, "
            "sum(coalesce(try_cast(sample_weight as double), 0.0)) as weight_sum "
            f"from read_parquet('{escaped_path}') "
            "where coalesce(try_cast(training_eligible as boolean), false) "
            "group by identity"
            ") where abs(weight_sum - 1.0) > 0.000001 "
            "order by identity"
        ).fetchall()
    except duckdb.Error as exc:
        return {"__query_error__": str(exc)}
    mismatches: dict[str, float | str] = {}
    for identity, weight_sum in rows:
        if isinstance(identity, str) and identity:
            mismatches[identity] = float(weight_sum)
    return mismatches


def _warehouse_columns(escaped_path: str) -> list[str]:
    try:
        rows = duckdb.sql(
            f"describe select * from read_parquet('{escaped_path}')"
        ).fetchall()
    except duckdb.Error:
        return []
    return [row[0] for row in rows if isinstance(row[0], str)]


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


def _record_ids(records: list[Any]) -> list[str]:
    return sorted(
        record_id
        for record in records
        if isinstance((record_id := getattr(record, "record_id", None)), str)
        and record_id
    )


def _record_ids_for_types(records: list[Any], record_types: set[str]) -> list[str]:
    return sorted(
        record.record_id
        for record in records
        if getattr(record, "record_type", None) in record_types
        and isinstance(getattr(record, "record_id", None), str)
    )


def _record_ids_for_suffix(records: list[Any], suffix: str) -> list[str]:
    return sorted(
        record.record_id
        for record in records
        if isinstance(getattr(record, "record_type", None), str)
        and record.record_type.endswith(suffix)
        and isinstance(getattr(record, "record_id", None), str)
    )


def _record_provenance_ids(records: list[Any]) -> list[str]:
    values: list[str] = []
    for record in records:
        record_id = getattr(record, "record_id", None)
        if not isinstance(record_id, str) or not record_id:
            continue
        source_ids = getattr(record, "provenance_source_ids", [])
        if not isinstance(source_ids, list):
            continue
        values.extend(
            _identity(record_id, source_id)
            for source_id in source_ids
            if isinstance(source_id, str) and source_id
        )
    return sorted(values)


def _record_coverage_ids(records: list[Any]) -> list[str]:
    return sorted(
        {
            _identity(
                record.episode_id,
                record.record_type,
                record.evidence_phase,
                getattr(record, "training_target", None) or "UNKNOWN",
            )
            for record in records
            if isinstance(getattr(record, "episode_id", None), str)
            and isinstance(getattr(record, "record_type", None), str)
            and isinstance(getattr(record, "evidence_phase", None), str)
        }
    )


def _event_ticker_edge_projection_ids(
    accepted_episodes: list[ResearchEpisode],
    records: list[Any],
) -> list[str]:
    values = [
        _identity(edge.episode_id, edge.edge_id)
        for episode in accepted_episodes
        for edge in episode.event_ticker_edges
    ]
    for record in records:
        if getattr(record, "record_type", None) != "event_ticker_edge":
            continue
        episode_id = getattr(record, "episode_id", None)
        if not isinstance(episode_id, str) or not episode_id:
            continue
        edge_id = _record_payload_string(record, "edge_id")
        record_id = getattr(record, "record_id", None)
        if edge_id is None and isinstance(record_id, str) and record_id:
            edge_id = record_id
        if edge_id is not None:
            values.append(_identity(episode_id, edge_id))
    return sorted(values)


def _record_payload_string(record: Any, key: str) -> str | None:
    payload = getattr(record, "payload", None)
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


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
            str(getattr(record, "evidence_phase", "")),
            str(getattr(record, "training_target", "") or "UNKNOWN"),
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
            if getattr(record, "record_type", None)
            in {"supervised_theme_formation_case", "theme_formation_case"}
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
        "event_ticker_edges": sum(
            1
            for record in records
            if getattr(record, "record_type", None) == "event_ticker_edge"
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
