"""Coverage audit wrapper."""

from __future__ import annotations

from pathlib import Path

from news_scalping_lab.brain.audit import audit_brain
from news_scalping_lab.retrieval.store import inspect_vector_index
from news_scalping_lab.warehouse import EXPECTED_WAREHOUSE_FILES, WarehouseStore


def audit_coverage(root: Path) -> dict[str, object]:
    brain = audit_brain(root)
    accepted_episode_count = _int_value(brain.get("accepted_episode_count"))
    vector_index = inspect_vector_index(root)
    warehouse_counts = WarehouseStore(root).counts()
    warehouse_research_episode_count = _int_value(
        warehouse_counts.get("research_episodes.parquet")
    )
    warehouse_expected_source_counts = _warehouse_expected_source_counts(root)
    warehouse_count_mismatches = _warehouse_count_mismatches(
        warehouse_counts,
        warehouse_expected_source_counts,
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
    warehouse_projection_synced = not warehouse_count_mismatches
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
    for filename, mismatch in warehouse_count_mismatches.items():
        label = warehouse_expected_source_counts[filename]["source_label"]
        findings.append(
            f"warehouse: {filename} count {mismatch['actual']} != "
            f"{label} count {mismatch['expected']}"
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


def _warehouse_expected_source_counts(root: Path) -> dict[str, dict[str, int | str]]:
    return {
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
