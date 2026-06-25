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
    return {
        **brain,
        "passed": (
            bool(brain.get("passed"))
            and vector_index_current
            and warehouse_synced
            and warehouse_required_files_present
        ),
        "findings": findings,
        "vector_index": vector_index,
        "vector_index_current": vector_index_current,
        "warehouse_counts": warehouse_counts,
        "warehouse_research_episode_count": warehouse_research_episode_count,
        "warehouse_synced": warehouse_synced,
        "warehouse_required_files": list(EXPECTED_WAREHOUSE_FILES),
        "warehouse_missing_files": missing_warehouse_files,
        "warehouse_unreadable_files": unreadable_warehouse_files,
        "warehouse_required_files_present": warehouse_required_files_present,
    }


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
