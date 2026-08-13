from pathlib import Path

from news_scalping_lab.research_import.repair_models import RepairTaskState
from news_scalping_lab.research_import.repair_routing import classify_repair_source


def test_routes_verified_deferred_receipt_without_episode_repair(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "deferred.md",
        """---
schema_version: nslab.deferred_input.v1
artifact_type: deferred_non_trading_day
status: DEFERRED_NON_TRADING_DAY
brain_eligible: false
outcome_research_performed: false
merge_required: false
covered_by_next_trading_day_csv: true
input_sha256: abc
calendar_date: 2023-03-01
---
# Deferred
""",
    )

    state, reason = classify_repair_source(source)

    assert state is RepairTaskState.DEFERRED_NON_TRADING
    assert reason == "verified_deferred_non_trading_receipt"


def test_deferred_receipt_with_brain_record_requires_adapter(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "invalid-deferred.md",
        """---
schema_version: nslab.deferred_input.v1
artifact_type: deferred_non_trading_day
status: DEFERRED_NON_TRADING_DAY
brain_eligible: false
outcome_research_performed: false
merge_required: false
covered_by_next_trading_day_csv: true
input_sha256: abc
calendar_date: 2023-03-01
---
<!-- NSLAB:BEGIN brain_delta.jsonl -->
```jsonl
{"record_id":"BD-1","record_type":"memory_claim"}
```
<!-- NSLAB:END brain_delta.jsonl -->
""",
    )

    state, _reason = classify_repair_source(source)

    assert state is RepairTaskState.ADAPTER_REQUIRED


def test_routes_blind_complete_outcome_missing_as_partial(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "pending.md",
        """---
schema_version: nslab.research_bundle.v11
artifact_type: research_episode_bundle
episode_id: NSLAB-20260623-test
trade_date: 2026-06-23
bundle_status: PENDING_OUTCOME
status: COMPLETED_BLIND_PENDING_RESEARCH_DAILY
outcome_status: RESEARCH_DAILY_PACKAGE_MISSING
blind_valid: true
brain_eligible: false
---
# Pending outcome
""",
    )

    state, reason = classify_repair_source(source)

    assert state is RepairTaskState.PARTIAL_PRICE_SOURCE_MISSING
    assert reason == "blind_research_preserved_pending_outcome_source"


def test_preserves_declared_quarantine_without_repair(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "quarantine.md",
        """---
schema_version: nslab.research_bundle.v11
bundle_status: QUARANTINE_PHASE_CONTAMINATED_PRESEAL_OUTCOME_TOUCH
blind_valid: false
brain_eligible: false
---
# Quarantine
""",
    )

    state, reason = classify_repair_source(source)

    assert state is RepairTaskState.PRESERVED_PARTIAL_NOT_CURRENT_GOLD
    assert reason == "source_declares_quarantine_status"


def test_preserves_quarantine_even_when_brain_count_is_nonzero(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "quarantine-with-records.md",
        """---
schema_version: nslab.research_bundle.v11
bundle_status: QUARANTINE_SEMANTIC_FINAL_ENTAILMENT
brain_delta_count: 212
brain_eligible: false
direct_brain_ingest_ready: false
---
<!-- NSLAB:BEGIN brain_delta.jsonl -->
```jsonl
{"record_id":"BD-1","record_type":"memory_claim"}
```
<!-- NSLAB:END brain_delta.jsonl -->
""",
    )

    state, reason = classify_repair_source(source)

    assert state is RepairTaskState.PRESERVED_PARTIAL_NOT_CURRENT_GOLD
    assert reason == "source_declares_quarantine_status"


def test_routes_declared_empty_quarantine_without_repair(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "empty-quarantine.md",
        """---
schema_version: nslab.research_bundle.v11
bundle_status: QUARANTINE_PHASE_CONTAMINATED_PRESEAL_OUTCOME_TOUCH
brain_eligible: false
direct_brain_ingest_ready: false
brain_delta_count: 0
---
# Quarantine
""",
    )

    state, reason = classify_repair_source(source)

    assert state is RepairTaskState.PRESERVED_SOURCE_PAYLOAD_ABSENT
    assert reason == "source_declares_no_brain_payload"


def test_standard_episode_stays_discovered(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "episode.md",
        """---
schema_version: nslab.research_bundle.v11
artifact_type: research_episode_bundle
episode_id: NSLAB-20221115-test
trade_date: 2022-11-15
bundle_status: ACCEPT_FULL
brain_eligible: true
---
# Episode
""",
    )

    state, reason = classify_repair_source(source)

    assert state is RepairTaskState.DISCOVERED
    assert reason == "standard_episode_repair_candidate"


def test_malformed_json_scalar_in_yaml_front_matter_still_routes_episode(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "legacy-front-matter.md",
        "---\n"
        'schema_version: "nslab.research_bundle.v11"\n'
        'artifact_type: "research_episode_bundle"\n'
        'episode_id: "NSLAB-20250820-test"\n'
        'trade_date: "2025-08-20"\n'
        'bundle_status: "ACCEPT_FULL"\n'
        'brain_eligible: true\n'
        'canonical_graph_object_counts: "{\"news_rows\":1235}"\n'
        "---\n# Episode\n",
    )

    state, reason = classify_repair_source(source)

    assert state is RepairTaskState.DISCOVERED
    assert reason == "standard_episode_repair_candidate"


def test_unclaimed_machine_payload_requires_adapter(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "unclaimed.md",
        """---
schema_version: nslab.research_bundle.v11
episode_id: NSLAB-20300110-unclaimed
trade_date: 2030-01-10
---
```json
{"record_id":"BD-1","record_type":"memory_claim"}
""",
    )

    state, reason = classify_repair_source(source)

    assert state is RepairTaskState.ADAPTER_REQUIRED
    assert reason == "unclaimed_machine_payload_requires_adapter"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path
