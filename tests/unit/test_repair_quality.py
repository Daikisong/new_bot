import json
from pathlib import Path

import pytest

from news_scalping_lab.research_import import repair_quality as repair_quality_module
from news_scalping_lab.research_import.repair_census import artifact_rows, census_source
from news_scalping_lab.research_import.repair_models import (
    RepairTaskState,
)
from news_scalping_lab.research_import.repair_quality import (
    _artifact_lineage_audit,
    _artifact_occurrence_lineage_audit,
    _build_lineage,
    _case_population_record_ids,
    _case_population_source_gap,
    _case_relation_values,
    _combined_population,
    _declared_join_values_match,
    _derived_case_population_audit,
    _illegal_transform_paths,
    _is_aggregate_news_source,
    _leader_policy_thresholds,
    _nontraining_case_underfill_only,
    _outcome_nested_value,
    _parse_datetime_or_none,
    _population_audit,
    _provenance_and_eligibility_audit,
    _record_has_verified_context_provenance,
    _relation_scalar_equal,
    _resolved_source_semantic_failures,
    _semantic_audit,
    _semantic_exclusion_audit,
    _semantic_verdict_value,
    _source_semantic_row_passes,
    _temporal_audit,
    evaluate_bundle_quality,
    semantic_exclusion_relation_ids,
)
from news_scalping_lab.utils import canonical_json, sha256_text


def test_relation_scalar_equal_preserves_nan_metrics() -> None:
    assert _relation_scalar_equal(float("nan"), float("nan")) is True
    assert _relation_scalar_equal("NaN", "NaN") is True


def test_illegal_transform_allows_unchanged_nan_metric() -> None:
    assert (
        _illegal_transform_paths(
            {"safe_D1_context_used": {"close_return_pct": float("nan")}},
            {"safe_D1_context_used": {"close_return_pct": float("nan")}},
        )
        == []
    )


def test_illegal_transform_allows_source_anchored_nested_row_alias() -> None:
    before = {
        "record_id": "BD-1",
        "record_type": "candidate_generation_error_case",
        "provenance_source_ids": ["SRC-001333"],
        "payload": {"provenance_source_ids": ["ROW-001333"]},
    }
    after = {
        **before,
        "record_id": "EP-20250219__BD-1",
        "brain_delta_id": "EP-20250219__BD-1",
        "payload": {"provenance_source_ids": ["SRC-001333"]},
    }

    assert _illegal_transform_paths(before, after) == []


def test_illegal_transform_allows_mistyped_event_to_screening_alias() -> None:
    before = {
        "record_id": "BD-1",
        "record_type": "supervised_issuer_day_case",
        "event_ids": ["CAND-1"],
    }
    after = {
        **before,
        "record_id": "EP-20251210__BD-1",
        "brain_delta_id": "EP-20251210__BD-1",
        "event_ids": [],
        "screening_ids": ["CAND-1"],
        "related_domain_ids": ["CAND-1"],
        "legacy_mistyped_event_reference_values": ["CAND-1"],
    }

    assert _illegal_transform_paths(before, after) == []


def test_illegal_transform_allows_overlapping_mistyped_event_fields() -> None:
    before = {
        "record_id": "BD-1",
        "record_type": "supervised_issuer_day_case",
        "event_ids": ["OBS-1", "OBS-2"],
        "related_event_ids": ["IDAY-1", "OBS-1", "OBS-2"],
    }
    after = {
        "record_id": "EP-20260430__BD-1",
        "brain_delta_id": "EP-20260430__BD-1",
        "record_type": "supervised_issuer_day_case",
        "screening_ids": ["OBS-1", "OBS-2"],
        "related_domain_ids": ["IDAY-1", "OBS-1", "OBS-2"],
        "legacy_mistyped_event_reference_values": ["IDAY-1", "OBS-1", "OBS-2"],
    }

    assert _illegal_transform_paths(before, after) == []


def test_illegal_transform_allows_nested_mistyped_event_to_screening_alias() -> None:
    before = {
        "record_id": "BD-1",
        "record_type": "supervised_issuer_day_case",
        "payload": {"event_ids": ["SCR-1"]},
    }
    after = {
        "record_id": "EP-20260528__BD-1",
        "brain_delta_id": "EP-20260528__BD-1",
        "record_type": "supervised_issuer_day_case",
        "payload": {"event_ids": [], "screening_ids": ["SCR-1"]},
        "related_domain_ids": ["SCR-1"],
        "legacy_mistyped_event_reference_values": ["SCR-1"],
    }

    assert _illegal_transform_paths(before, after) == []


def test_illegal_transform_rejects_unanchored_nested_row_alias() -> None:
    before = {
        "record_id": "BD-1",
        "record_type": "candidate_generation_error_case",
        "provenance_source_ids": ["SRC-001333"],
        "payload": {"provenance_source_ids": ["ROW-001333"]},
    }
    after = {
        **before,
        "record_id": "EP-20250219__BD-1",
        "brain_delta_id": "EP-20250219__BD-1",
        "payload": {"provenance_source_ids": ["SRC-009999"]},
    }

    assert _illegal_transform_paths(before, after) == [
        "payload.provenance_source_ids"
    ]


def test_illegal_transform_allows_source_anchored_missed_leader_aliases() -> None:
    before = {
        "record_id": "BD-MISS-1",
        "record_type": "MISSED_OUTCOME_LEADER",
        "training_eligible": True,
        "label": "MISSED_WITH_NONRANKABLE_EVIDENCE",
        "error_mode": "MISSED_WITH_NONRANKABLE_EVIDENCE",
        "ticker": "000001",
        "company": "Source Company",
    }
    after = {
        **before,
        "record_id": "EP-20240923__BD-MISS-1",
        "record_type": "candidate_generation_error_case",
        "legacy_record_type": "MISSED_OUTCOME_LEADER",
        "training_target": "candidate_generation_correction",
        "sample_weight": 1.0,
        "error_id": "EP-20240923__BD-MISS-1",
        "error_type": "MISSED_WITH_NONRANKABLE_EVIDENCE",
        "missed_ticker": "000001",
        "missed_company_name": "Source Company",
        "correction_mode": "MISSED_WITH_NONRANKABLE_EVIDENCE",
    }

    assert _illegal_transform_paths(before, after) == []


def test_lineage_rejects_same_id_payload_replacement(tmp_path: Path) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "memory_claim",
                    "payload": {"fact": "ORIGINAL"},
                }
            ]
        },
    )

    _, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [
            {
                "record_id": "BD-1",
                "record_type": "memory_claim",
                "payload": {"fact": "COMPLETELY_REPLACED"},
            }
        ],
    )

    assert audit["unaccounted_original_record_count"] == 0
    assert audit["illegal_transform_count"] == 1


def test_leader_outcome_class_does_not_lower_global_threshold() -> None:
    rows = [
        type(
            "ArtifactRowStub",
            (),
            {
                "row": {
                    "outcome_class": "LIQUIDITY_LEADER_HIGH5",
                    "included_by_rule": ["high_return_pct_ge_10", "amount_rank_top50"],
                }
            },
        )()
    ]

    (
        amount_top_n,
        turnover_top_n,
        high_return_threshold,
        high_return_rank_top_n,
    ) = _leader_policy_thresholds(rows)

    assert amount_top_n == 50
    assert turnover_top_n is None
    assert high_return_threshold == 10.0
    assert high_return_rank_top_n is None


def test_leader_policy_reads_policy_bands_alias() -> None:
    rows = [
        type(
            "ArtifactRowStub",
            (object,),
            {"row": {"policy_bands": ["TURNOVER_TOP20"]}},
        )()
    ]

    _, turnover_top_n, _, _ = _leader_policy_thresholds(rows)

    assert turnover_top_n == 20


def test_leader_policy_reads_postmortem_group_n_aliases() -> None:
    rows = [
        type(
            "ArtifactRowStub",
            (object,),
            {
                "row": {
                    "leader_policy": {
                        "minimum_high_return_pct": 10,
                        "amount_rank_top_group_n": 100,
                        "turnover_rank_top_group_n": 100,
                    }
                }
            },
        )()
    ]

    amount_top_n, turnover_top_n, high_return_threshold, _ = _leader_policy_thresholds(
        [], policy_rows=rows
    )

    assert amount_top_n == 100
    assert turnover_top_n == 100
    assert high_return_threshold == 10.0


def test_leader_policy_reads_high_return_floor_pct_alias() -> None:
    rows = [
        type(
            "ArtifactRowStub",
            (object,),
            {
                "row": {
                    "outcome_leader_census_policy": {
                        "high_return_floor_pct": 5.0,
                        "amount_top_n": 30,
                        "turnover_top_n": 30,
                    }
                }
            },
        )()
    ]

    amount_top_n, turnover_top_n, high_return_threshold, _ = _leader_policy_thresholds(
        [], policy_rows=rows
    )

    assert amount_top_n == 30
    assert turnover_top_n == 30
    assert high_return_threshold == 5.0


def test_leader_policy_reads_legacy_census_policy_and_cohort_tags() -> None:
    rows = [
        type(
            "ArtifactRowStub",
            (object,),
            {
                "row": {
                    "census_policy": {
                        "amount_rank_top_group_max_rank": 30,
                        "turnover_rank_top_group_max_rank": 30,
                        "high_return_thresholds": [10, 15, 20],
                    },
                    "cohort_tags": ["AMOUNT_TOP30", "TURNOVER_TOP30"],
                }
            },
        )()
    ]

    amount_top_n, turnover_top_n, high_return_threshold, _ = _leader_policy_thresholds(
        rows
    )

    assert amount_top_n == 30
    assert turnover_top_n == 30
    assert high_return_threshold == 10.0


def test_outcome_nested_value_reads_data_container() -> None:
    assert _outcome_nested_value(
        {"data": {"amount_rank": 7}}, "amount_rank"
    ) == 7


def test_news_csv_raw_bytes_descriptor_is_not_a_news_row() -> None:
    assert _is_aggregate_news_source(
        {"source_type": "NEWS_CSV_RAW_BYTES", "source_id": "SRC-CORE-NEWS-FILE"}
    )


def test_population_accepts_direct_event_case_join_without_case_id_copy() -> None:
    def row(name: str, payload: dict[str, object], ordinal: int) -> object:
        return type(
            "ArtifactRowStub",
            (),
            {
                "canonical_name": name,
                "occurrence_id": f"OCC-{ordinal}",
                "raw_payload_sha256": f"HASH-{ordinal}",
                "row": payload,
            },
        )()

    result = _population_audit(
        [
            row(
                "direct_event_cases.jsonl",
                {
                    "direct_event_case_id": "DEC-SCR-000001",
                    "screening_id": "SCR-000001",
                    "candidate_id": "CAND-000001",
                    "ticker": "111111",
                    "sealed_fact_ids": ["FACT-000001"],
                    "trade_date": "2019-02-22",
                },
                1,
            ),
            row(
                "brain_delta.jsonl",
                {
                    "record_id": "BD-000001",
                    "record_type": "supervised_direct_event_case",
                    "screening_id": "SCR-000001",
                    "ticker": "111111",
                    "source_fact_ids": ["FACT-000001"],
                    "trade_date": "2019-02-22",
                },
                2,
            ),
        ]
    )

    assert result["rules"]["case_to_brain:DIRECT_EVENT"]["missing_keys"] == []


def test_population_accepts_explicit_rejected_direct_event_alias() -> None:
    def row(name: str, payload: dict[str, object], ordinal: int) -> object:
        return type(
            "ArtifactRowStub",
            (),
            {
                "canonical_name": name,
                "occurrence_id": f"OCC-{ordinal}",
                "raw_payload_sha256": f"HASH-{ordinal}",
                "row": payload,
            },
        )()

    result = _population_audit(
        [
            row(
                "direct_event_cases.jsonl",
                {
                    "direct_event_case_id": "DEC-1",
                    "candidate_id": "CAND-1",
                    "screening_id": "SCR-1",
                    "ticker": "111111",
                    "sealed_fact_ids": ["FACT-1"],
                    "outcome_id": "OUT-1",
                    "trade_date": "2020-10-16",
                    "screening_decision": "EXCLUDE",
                    "semantic_verdict": "FAIL",
                    "training_eligible": False,
                },
                1,
            ),
            row(
                "brain_delta.jsonl",
                {
                    "record_id": "BD-1",
                    "record_type": "negative_control_case",
                    "candidate_id": "CAND-1",
                    "screening_id": "SCR-1",
                    "ticker": "111111",
                    "source_fact_ids": ["FACT-1"],
                    "outcome_id": "OUT-1",
                    "trade_date": "2020-10-16",
                },
                2,
            ),
        ]
    )

    assert result["rules"]["case_to_brain:DIRECT_EVENT"]["missing_keys"] == []


def test_population_accepts_ranking_rec_rre_alias_in_payload() -> None:
    def row(name: str, payload: dict[str, object], ordinal: int) -> object:
        return type(
            "ArtifactRowStub",
            (),
            {
                "canonical_name": name,
                "occurrence_id": f"OCC-{ordinal}",
                "raw_payload_sha256": f"HASH-{ordinal}",
                "row": payload,
            },
        )()

    result = _population_audit(
        [
            row("ranking_error_cases.jsonl", {"ranking_error_case_id": "REC-00001"}, 1),
            row(
                "brain_delta.jsonl",
                {
                    "record_id": "BD-000001",
                    "record_type": "candidate_ranking_error_case",
                    "payload": {"reverse_ranking_error_case_id": "RRE-00001"},
                },
                2,
            ),
        ]
    )

    assert result["rules"]["case_to_brain:RANKING"]["missing_keys"] == []


def test_event_ticker_edge_cutoff_source_filter_is_legal() -> None:
    before = {
        "record_type": "event_ticker_edge",
        "source_phase": "POSTMORTEM",
        "provenance_source_ids": ["SRC-NEWS-1", "SRC-OUTCOME-1"],
    }
    after = {
        **before,
        "provenance_source_ids": ["SRC-NEWS-1"],
        "source_ids": ["SRC-NEWS-1"],
        "provenance_source_filter": {
            "rule_id": "event_ticker_edge_cutoff_safe_sources.v1",
            "removed_source_ids": ["SRC-OUTCOME-1"],
            "retained_source_ids": ["SRC-NEWS-1"],
        },
    }

    assert _illegal_transform_paths(before, after) == []


def test_selected_blind_screening_alias_preserves_non_scr_prefix() -> None:
    before = {
        "record_type": "candidate_ranking_error_case",
        "selected_blind_event_ids": ["OBS-001068"],
    }
    after = {
        **before,
        "selected_blind_event_ids": [],
        "selected_blind_screening_ids": ["OBS-001068"],
        "legacy_mistyped_event_reference_values": ["OBS-001068"],
        "related_domain_ids": ["OBS-001068"],
    }

    assert _illegal_transform_paths(before, after) == []


def test_numeric_identifier_string_normalization_is_legal() -> None:
    before = {"record_type": "context_market_state_or_fact_case", "row_id": 762}
    after = {"record_type": "context_market_state_or_fact_case", "row_id": "762"}

    assert _illegal_transform_paths(before, after) == []


def test_semantic_exclusion_can_fill_null_eligibility_reason() -> None:
    before = {
        "record_type": "supervised_issuer_day_case",
        "eligibility_reason": None,
        "training_eligible": True,
        "sample_weight": 1.0,
    }
    after = {
        **before,
        "eligibility_reason": "semantic_contract_failed",
        "training_exclusion_reason": "semantic_contract_failed",
        "semantic_exclusion_relation_ids": ["FACT-1"],
        "training_eligible": False,
        "sample_weight": 0.0,
    }

    assert _illegal_transform_paths(before, after) == []


def test_case_population_accepts_explicit_derivation_source_case_id() -> None:
    record = {
        "record_type": "negative_control_case",
        "repair_population_derivations": [
            {"source_case_id": "NEG-000016"},
        ],
    }

    assert "NEG-000016" in _case_population_record_ids(
        record,
        "NEGATIVE",
        ("negative_control_id", "negative_control_case_id"),
    )


def test_ticker_mirror_from_single_related_ticker_is_legal() -> None:
    before = {
        "record_type": "newsless_or_unexplained_case",
        "ticker": None,
        "related_tickers": ["000270"],
    }
    after = {**before, "ticker": "000270"}

    assert _illegal_transform_paths(before, after) == []


def test_event_ticker_edge_relation_class_alias_is_legal() -> None:
    before = {
        "record_type": "event_ticker_edge",
        "payload": {"relation_class": "NAMED_ACQUISITION_TARGET"},
    }
    after = {
        **before,
        "path_type": "INFERRED_NEW",
        "relation_class": "INFERRED_NEW",
    }

    assert _illegal_transform_paths(before, after) == []


def test_event_ticker_edge_cutoff_source_filter_is_legal_for_retrospective_discovery() -> None:
    before = {
        "record_type": "event_ticker_edge",
        "source_phase": "RETROSPECTIVE_DISCOVERY",
        "provenance_source_ids": ["SRC-NEWS-1", "SRC-OUTCOME-1"],
    }
    after = {
        **before,
        "provenance_source_ids": ["SRC-NEWS-1"],
        "source_ids": ["SRC-NEWS-1"],
        "provenance_source_filter": {
            "rule_id": "event_ticker_edge_cutoff_safe_sources.v1",
            "removed_source_ids": ["SRC-OUTCOME-1"],
            "retained_source_ids": ["SRC-NEWS-1"],
        },
    }

    assert _illegal_transform_paths(before, after) == []


def test_artifact_lineage_joins_source_id_and_source_row_id_aliases(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source-source-row-id.md",
        {
            "source_ledger.jsonl": [
                {"source_row_id": "SRC-1", "source_type": "NEWS_CSV_ROW"}
            ]
        },
    )
    repaired = _write_bundle(
        tmp_path / "repaired-source-row-id.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_row_id": "SRC-1",
                    "source_id": "SRC-1",
                    "source_type": "NEWS_CSV_ROW",
                }
            ]
        },
    )

    audit = _artifact_lineage_audit(
        artifact_rows(source),
        artifact_rows(repaired),
    )

    assert audit["artifact_orphan_repaired_row_count"] == 0
    assert audit["artifact_missing_source_row_count"] == 0
    assert audit["artifact_illegal_transform_count"] == 0


def test_trade_date_materialization_must_match_outcome_snapshot() -> None:
    source = {
        "record_type": "supervised_direct_event_case",
        "record_id": "BD-1",
        "outcome": {"snapshot_date": "2018-09-03"},
    }
    repaired = {**source, "trade_date": "2018-09-03"}
    assert _illegal_transform_paths(source, repaired) == []
    assert _illegal_transform_paths(
        source,
        {**source, "trade_date": "2018-09-04"},
    ) == ["trade_date"]
    pair_source = {
        "record_type": "blind_leader_preference_pair",
        "record_id": "BD-2",
        "outcome_labels": {
            "preferred": {"snapshot_date": "2018-09-03"},
            "rejected": {"snapshot_date": "2018-09-03"},
        },
    }
    assert _illegal_transform_paths(
        pair_source,
        {**pair_source, "trade_date": "2018-09-03"},
    ) == []
    metadata_source = {
        "record_type": "context_market_state_or_fact_case",
        "record_id": "BD-3",
        "episode_id": "NSLAB-20180903-example",
    }
    assert _illegal_transform_paths(
        metadata_source,
        {**metadata_source, "trade_date": "2018-09-03"},
    ) == []


def test_legacy_episode_date_and_postmortem_availability_mirrors_are_allowed() -> None:
    source_without_dates = {
        "record_id": "BD-1",
        "record_type": "supervised_issuer_day_case",
        "source_phase": "POSTSEAL_SUPERVISED",
    }
    repaired_with_dates = {
        **source_without_dates,
        "record_id": "NSLAB-20210406-abcd__BD-1",
        "episode_id": "NSLAB-20210406-abcd",
        "trade_date": "2021-04-06",
        "available_from": "2021-04-07T00:00:00+09:00",
    }
    assert _illegal_transform_paths(source_without_dates, repaired_with_dates) == []
    assert _illegal_transform_paths(
        source_without_dates,
        {**repaired_with_dates, "trade_date": "2021-04-07"},
    ) == ["trade_date"]

    postmortem_placeholder = {
        "record_id": "BD-2",
        "record_type": "supervised_issuer_day_case",
        "source_phase": "POSTMORTEM",
        "trade_date": "2021-04-05",
        "available_from": "POSTMORTEM",
    }
    postmortem_canonical = {
        **postmortem_placeholder,
        "available_from": "2021-04-06T00:00:00+09:00",
    }
    assert _illegal_transform_paths(postmortem_placeholder, postmortem_canonical) == []
    assert _illegal_transform_paths(
        postmortem_placeholder,
        {**postmortem_canonical, "available_from": "2021-04-05T00:00:00+09:00"},
    ) == ["available_from"]

    phase_label_placeholder = {
        **postmortem_placeholder,
        "available_from": "POSTSEAL_OUTCOME",
    }
    assert _illegal_transform_paths(
        phase_label_placeholder,
        {**phase_label_placeholder, "available_from": "2021-04-06T00:00:00+09:00"},
    ) == []


def test_legacy_population_mismatch_is_quarantined_only_when_source_matches_repair() -> None:
    source = {
        "current_contract_blocks_present": False,
        "population_underfill_count": 8,
        "population_extra_count": 3,
        "duplicate_logical_key_count": 0,
        "liquidity_policy_underspecified_count": 1,
        "rules": {
            "legacy_relation": {
                "mode": "EXPECTED_SUBSET",
                "actual_count": 10,
                "expected_count": 18,
                "missing_keys": ["A"] * 8,
                "extra_keys": [],
            }
        },
    }
    repaired = json.loads(json.dumps(source))
    assert _combined_population(source, repaired)["legacy_contract_population_quarantine"] is True

    changed = json.loads(json.dumps(source))
    changed["population_underfill_count"] = 7
    assert _combined_population(source, changed)["legacy_contract_population_quarantine"] is False


def test_legacy_population_case_alias_normalization_is_quarantined() -> None:
    source = {
        "current_contract_blocks_present": False,
        "population_underfill_count": 9,
        "population_extra_count": 207,
        "duplicate_logical_key_count": 0,
        "liquidity_policy_underspecified_count": 0,
        "declared_population_manifest_complete": False,
        "rules": {
            "case_to_brain:BENEFICIARY": {
                "mode": "EXPECTED_SUBSET",
                "actual_count": 0,
                "expected_count": 9,
                "missing_keys": ["BEN-1"] * 9,
                "extra_keys": [],
            },
            "material_queue_to_disposition": {
                "mode": "EXPECTED_SUBSET",
                "actual_count": 558,
                "expected_count": 558,
                "missing_keys": [],
                "extra_keys": ["NEWS-EXTRA"] * 207,
            },
        },
    }
    repaired = json.loads(json.dumps(source))
    repaired["population_underfill_count"] = 0
    repaired["rules"]["case_to_brain:BENEFICIARY"].update(
        actual_count=9,
        missing_keys=[],
    )

    assert _combined_population(source, repaired)["legacy_contract_population_quarantine"] is True

    changed_relation = json.loads(json.dumps(repaired))
    changed_relation["rules"]["material_queue_to_disposition"]["actual_count"] = 559
    assert _combined_population(source, changed_relation)["legacy_contract_population_quarantine"] is False


def test_issuer_case_relation_reads_combined_fact_alias() -> None:
    case = {"combined_fact_ids": ["FACT-1", "FACT-2"]}
    assert _case_relation_values(case, "source_fact_ids", "combined_fact_ids") == {
        "FACT-1",
        "FACT-2",
    }


def test_join_evidence_allows_observed_secondary_aliases() -> None:
    assert _declared_join_values_match(
        {"source_screening_id": ["SCR-1"]},
        {"source_screening_id": ["SCR-1"], "fact_id": ["FACT-1"]},
    )
    assert not _declared_join_values_match(
        {"source_screening_id": ["SCR-2"]},
        {"source_screening_id": ["SCR-1"], "fact_id": ["FACT-1"]},
    )
    assert _declared_join_values_match(
        {"theme_id": ["THEME-A"]},
        {"theme_id": ["THEME-A", "THCASE-1"]},
    )


def test_lineage_allows_record_id_materialized_from_brain_delta_id(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source-brain-id.md",
        {
            "brain_delta.jsonl": [
                {
                    "brain_delta_id": "BD-1",
                    "record_type": "supervised_issuer_day_case",
                }
            ]
        },
    )
    repaired = {
        "record_id": "EP-1__BD-1",
        "brain_delta_id": "EP-1__BD-1",
        "record_type": "supervised_issuer_day_case",
    }

    _, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired],
    )

    assert audit["illegal_transform_count"] == 0


def test_lineage_joins_legacy_brain_record_id_alias(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source-brain-record-id.md",
        {
            "brain_delta.jsonl": [
                {
                    "brain_record_id": "BD-LEGACY-1",
                    "record_type": "supervised_issuer_day_case",
                }
            ]
        },
    )
    repaired = {
        "record_id": "EP-1__BD-CANONICAL-1",
        "brain_delta_id": "EP-1__BD-CANONICAL-1",
        "brain_record_id": "BD-LEGACY-1",
        "record_type": "supervised_issuer_day_case",
    }

    _, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired],
    )

    assert audit["unaccounted_original_record_count"] == 0
    assert audit["orphan_repaired_record_count"] == 0


def test_population_closure_uses_brain_delta_id_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "closure-alias-test.md",
        {
            "brain_delta.jsonl": [{"brain_delta_id": "BD-1"}],
            "record_provenance_closure_audit.jsonl": [
                {"brain_delta_id": "BD-1"}
            ],
        },
    )
    audit = _population_audit(
        artifact_rows(bundle),
        present_artifact_names={
            "brain_delta.jsonl",
            "record_provenance_closure_audit.jsonl",
        },
    )

    assert audit["rules"]["brain_to_provenance_closure"]["missing_keys"] == []
    assert audit["rules"]["brain_to_provenance_closure"]["extra_keys"] == []


def test_population_closure_uses_legacy_brain_record_id_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "closure-brain-record-alias-test.md",
        {
            "brain_delta.jsonl": [{"brain_record_id": "BD-LEGACY-1"}],
            "record_provenance_closure_audit.jsonl": [
                {"brain_record_id": "BD-LEGACY-1"}
            ],
        },
    )
    audit = _population_audit(
        artifact_rows(bundle),
        present_artifact_names={
            "brain_delta.jsonl",
            "record_provenance_closure_audit.jsonl",
        },
    )

    assert audit["rules"]["brain_to_provenance_closure"]["missing_keys"] == []
    assert audit["rules"]["brain_to_provenance_closure"]["extra_keys"] == []


def test_semantic_verdict_accepts_final_boolean_aliases() -> None:
    assert _semantic_verdict_value({"final_semantic_pass": True}) == "PASS"
    assert _semantic_verdict_value({"final_semantic_pass": False}) == "FAIL"
    assert _semantic_verdict_value({"semantic_decision": "PASS"}) == "PASS"
    assert _semantic_verdict_value({"semantic_decision": "PASS_TO_RANKING"}) == "PASS"


def test_final_evidence_uses_matching_semantic_audit_verdict(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "final-evidence-verdict.md",
        {
            "final_evidence_witness.jsonl": [
                {
                    "candidate_id": "CAND-1",
                    "final_evidence_witness_id": "FEW-1",
                    "primary_quote": "Issuer announced a plan",
                    "primary_fact_id": "FACT-1",
                    "local_predicate_owner": "Issuer",
                }
            ],
            "final_semantic_audit.jsonl": [
                {"candidate_id": "CAND-1", "semantic_decision": "PASS"}
            ],
            "fact_ledger_blind.jsonl": [
                {
                    "fact_id": "FACT-1",
                    "exact_quote": "Issuer announced a plan",
                    "source_id": "SRC-1",
                }
            ],
            "source_ledger.jsonl": [
                {"source_id": "SRC-1", "title": "Issuer announced a plan"}
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0


def test_datetime_parser_accepts_explicit_kst_source_ledger_alias() -> None:
    parsed = _parse_datetime_or_none("2018-05-02 08:58:54 KST")

    assert parsed is not None
    assert parsed.isoformat() == "2018-05-02T08:58:54+09:00"


def test_lineage_allows_quarantined_unknown_fact_inference_tokens() -> None:
    before = {
        "source_fact_ids": ["FACT-1", "PMFACT-OUTCOME-1"],
        "source_inference_ids": ["INF-1", "PMINF-AUDIT-1"],
    }
    after = {
        "source_fact_ids": ["FACT-1"],
        "source_inference_ids": ["INF-1"],
        "legacy_unresolved_fact_tokens": ["PMFACT-OUTCOME-1"],
        "legacy_unresolved_inference_tokens": ["PMINF-AUDIT-1"],
        "unresolved_reference_reason": "typed_reference_not_present_in_bundle_ledger",
    }

    assert _illegal_transform_paths(before, after) == []


def test_lineage_rejects_same_id_arbitrary_semantic_field_addition(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "memory_claim",
                    "payload": {"fact": "ORIGINAL"},
                }
            ]
        },
    )
    repaired = {
        "record_id": "BD-1",
        "record_type": "memory_claim",
        "payload": {"fact": "ORIGINAL"},
        "label": "WINNER",
    }

    lineage, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired],
    )

    assert audit["illegal_transform_count"] == 1
    assert lineage[0].status == "ILLEGAL_TRANSFORM"
    assert lineage[0].changed_fields["illegal_transform_paths"] == ["label"]


def test_closure_audit_allows_recomputed_rewrite(tmp_path: Path) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-1",
                    "resolved_provenance_source_ids": ["SRC-OLD"],
                    "closure_status": "CLOSED",
                }
            ]
        },
    )
    repaired = _write_bundle(
        tmp_path / "repaired.md",
        {
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "EP__BD-1",
                    "resolved_provenance_source_ids": ["SRC-REAL"],
                    "closure_status": "CLOSED_NOT_TRAINING",
                }
            ]
        },
    )

    row_audit = _artifact_lineage_audit(
        artifact_rows(source),
        artifact_rows(repaired),
    )
    occurrence_audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(repaired),
    )

    assert row_audit["artifact_missing_source_row_count"] == 0
    assert row_audit["artifact_orphan_repaired_row_count"] == 0
    assert row_audit["artifact_illegal_transform_count"] == 0
    assert occurrence_audit["artifact_occurrence_changed_count"] == 0


def test_case_artifact_occurrence_allows_audited_record_id_namespacing(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "case-source.md",
        {"newsless_or_unexplained_cases.jsonl": [{"newsless_case_id": "NEWSLESS-1", "brain_record_id": "BD-1"}]},
    )
    repaired = _write_bundle(
        tmp_path / "case-repaired.md",
        {"newsless_or_unexplained_cases.jsonl": [{"newsless_case_id": "NEWSLESS-1", "brain_record_id": "EP__BD-1"}]},
    )

    row_audit = _artifact_lineage_audit(
        artifact_rows(source),
        artifact_rows(repaired),
    )
    occurrence_audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(repaired),
    )

    assert row_audit["artifact_illegal_transform_count"] == 0
    assert occurrence_audit["artifact_occurrence_changed_count"] == 0


def test_postmortem_population_allows_brain_delta_id_namespacing(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "population-source.md",
        {
            "postmortem_supervised_population.jsonl": [
                {
                    "brain_delta_id": "BD-1",
                    "record_type": "supervised_issuer_day_case",
                    "training_eligible": True,
                }
            ]
        },
    )
    repaired = _write_bundle(
        tmp_path / "population-repaired.md",
        {
            "postmortem_supervised_population.jsonl": [
                {
                    "brain_delta_id": "EP__BD-1",
                    "record_type": "supervised_issuer_day_case",
                    "training_eligible": True,
                }
            ]
        },
    )

    audit = _artifact_occurrence_lineage_audit(
        census_source(source), census_source(repaired)
    )

    assert audit["artifact_occurrence_changed_count"] == 0


def test_context_case_artifact_occurrence_allows_scalar_record_id_namespacing(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "context-source.md",
        {
            "context_market_state_or_fact_cases.jsonl": [
                {
                    "record_id": "BD-1",
                    "context_case_id": "CTX-1",
                    "provenance_source_ids": ["SRC-1"],
                    "value": "unchanged",
                }
            ]
        },
    )
    repaired = _write_bundle(
        tmp_path / "context-repaired.md",
        {
            "context_market_state_or_fact_cases.jsonl": [
                {
                    "record_id": "EP__BD-1",
                    "context_case_id": "CTX-1",
                    "provenance_source_ids": ["SRC-1"],
                    "value": "unchanged",
                }
            ]
        },
    )

    audit = _artifact_occurrence_lineage_audit(
        census_source(source), census_source(repaired)
    )

    assert audit["artifact_occurrence_changed_count"] == 0


def test_phase_audit_occurrence_allows_nested_counterexample_id_namespacing(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "phase-source.md",
        {
            "phase_audit_report.json": [
                {
                    "episode_id": "EP-1",
                    "semantic_postmortem_correction": {
                        "counterexample_record_id": "BD-1",
                        "classification": "SEMANTIC_FALSE_POSITIVE",
                    },
                }
            ],
            "access_log.jsonl": [
                {
                    "seq": 1,
                    "counterexample_record_id": "BD-1",
                    "outcome_byte_access": False,
                }
            ],
        },
    )
    repaired = _write_bundle(
        tmp_path / "phase-repaired.md",
        {
            "phase_audit_report.json": [
                {
                    "episode_id": "EP-1",
                    "semantic_postmortem_correction": {
                        "counterexample_record_id": "EP-1__BD-1",
                        "classification": "SEMANTIC_FALSE_POSITIVE",
                    },
                }
            ],
            "access_log.jsonl": [
                {
                    "seq": 1,
                    "counterexample_record_id": "EP-1__BD-1",
                    "outcome_byte_access": False,
                }
            ],
        },
    )

    audit = _artifact_occurrence_lineage_audit(
        census_source(source), census_source(repaired)
    )

    assert audit["artifact_occurrence_changed_count"] == 0


def test_reverse_record_coverage_allows_namespaced_record_ids(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "reverse-source.md",
        {
            "outcome_reverse_record_coverage.jsonl": [
                {"audit_id": "OUTNEWS-1", "brain_delta_record_ids": ["BD-1"]}
            ]
        },
    )
    repaired = _write_bundle(
        tmp_path / "reverse-repaired.md",
        {
            "outcome_reverse_record_coverage.jsonl": [
                {"audit_id": "OUTNEWS-1", "brain_delta_record_ids": ["EP__BD-1"]}
            ]
        },
    )

    occurrence_audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(repaired),
    )

    assert occurrence_audit["artifact_occurrence_changed_count"] == 0


def test_lineage_allows_namespacing_type_alias_and_training_downgrade(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "issuer_day_case",
                    "training_eligible": True,
                    "sample_weight": 1.0,
                    "payload": {"record_id": "BD-1"},
                }
            ]
        },
    )
    repaired = {
        "record_id": "EP__BD-1",
        "record_type": "supervised_issuer_day_case",
        "legacy_record_type": "issuer_day_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "training_exclusion_reason": "unsupported_legacy_training_type",
        "eligibility_reason": "unsupported_legacy_training_type",
        "payload": {"record_id": "EP__BD-1"},
    }

    _, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired],
    )

    assert audit["illegal_transform_count"] == 0
    assert audit["unaccounted_original_record_count"] == 0
    assert audit["orphan_repaired_record_count"] == 0


def test_lineage_allows_semantic_contract_training_exclusion(tmp_path: Path) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "supervised_direct_event_case",
                    "training_eligible": True,
                    "sample_weight": 1.0,
                    "eligibility_reason": "source research eligible",
                    "candidate_id": "CAND-1",
                }
            ]
        },
    )
    repaired = {
        "record_id": "BD-1",
        "record_type": "supervised_direct_event_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "training_exclusion_reason": "semantic_contract_failed",
        "eligibility_reason": "source research eligible; semantic_contract_failed",
        "semantic_exclusion_relation_ids": ["CAND-1"],
        "candidate_id": "CAND-1",
    }

    _, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired],
    )

    assert audit["illegal_transform_count"] == 0
    assert audit["unaccounted_original_record_count"] == 0


def test_lineage_allows_exact_payload_mirrors_with_namespaced_brain_id(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "memory_claim",
                    "payload": {
                        "record_id": "BD-1",
                        "episode_id": "EP-SOURCE",
                        "lesson": "source-retained lesson",
                        "upper_limit_touched": False,
                    },
                }
            ]
        },
    )
    repaired = {
        "record_id": "EP-CANONICAL__BD-1",
        "record_type": "memory_claim",
        "episode_id": "EP-SOURCE",
        "lesson": "source-retained lesson",
        "upper_limit_touched": False,
        "payload": {
            "record_id": "EP-CANONICAL__BD-1",
            "episode_id": "EP-SOURCE",
            "lesson": "source-retained lesson",
            "upper_limit_touched": False,
        },
    }

    _, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired],
    )

    assert audit["illegal_transform_count"] == 0


def test_lineage_allows_episode_normalization_and_mistyped_event_split(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "episode_id": "LEGACY-EPISODE",
                    "record_type": "negative_control_case",
                    "related_event_ids": ["CASE-1"],
                }
            ]
        },
    )
    repaired = {
        "record_id": "CANONICAL-EPISODE__BD-1",
        "brain_delta_id": "CANONICAL-EPISODE__BD-1",
        "episode_id": "CANONICAL-EPISODE",
        "legacy_source_episode_id": "LEGACY-EPISODE",
        "record_type": "negative_control_case",
        "related_domain_ids": ["CASE-1"],
        "legacy_mistyped_event_reference_values": ["CASE-1"],
    }

    _, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired],
    )

    assert audit["illegal_transform_count"] == 0
    assert audit["unaccounted_original_record_count"] == 0


def test_lineage_allows_only_bundle_bound_added_episode_id(tmp_path: Path) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {"brain_delta.jsonl": [{"record_id": "BD-1", "record_type": "memory_claim"}]},
    )
    good = {
        "record_id": "EP-1__BD-1",
        "record_type": "memory_claim",
        "episode_id": "EP-1",
    }
    bad = {**good, "episode_id": "UNRELATED-EPISODE"}

    _, good_audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [good],
    )
    _, bad_audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [bad],
    )

    assert good_audit["illegal_transform_count"] == 0
    assert bad_audit["illegal_transform_count"] == 1


def test_lineage_rejects_unknown_record_type_alias(tmp_path: Path) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "unreviewed_theme_alias",
                    "payload": {"record_id": "BD-1"},
                }
            ]
        },
    )
    repaired = {
        "record_id": "EP__BD-1",
        "record_type": "theme_formation_case",
        "legacy_record_type": "unreviewed_theme_alias",
        "payload": {"record_id": "EP__BD-1"},
    }

    _, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired],
    )

    assert audit["unaccounted_original_record_count"] == 0
    assert audit["illegal_transform_count"] == 1


def test_lineage_accepts_derived_record_only_with_case_origin(tmp_path: Path) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {"issuer_day_cases.jsonl": [{"issuer_day_case_id": "IDAY-1", "ticker": "000001"}]},
    )
    repaired = {
        "record_id": "BD-DERIVED-1",
        "record_type": "supervised_issuer_day_case",
        "issuer_day_case_id": "IDAY-1",
        "payload": {"issuer_day_case_id": "IDAY-1"},
    }

    lineage, audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired],
    )

    assert audit["derived_record_count"] == 1
    assert audit["orphan_repaired_record_count"] == 0
    assert lineage[0].lineage_kind == "DERIVED"
    assert lineage[0].derivation_inputs


def test_candidate_negative_witness_is_allowed_but_final_witness_is_strict(
    tmp_path: Path,
) -> None:
    evidence = {
        "source_ledger.jsonl": [
            {
                "source_id": "SRC-1",
                "source_type": "NEWS",
                "title": "exact quoted evidence",
            }
        ],
        "fact_ledger_blind.jsonl": [
            {
                "fact_id": "FACT-1",
                "source_id": "SRC-1",
                "exact_quote": "exact quoted evidence",
            }
        ],
        "candidate_screening.jsonl": [
            {
                "screening_id": "SCR-1",
                "screening_decision": "AUDIT_ONLY",
                "decision_reason_specific": "Context-only row is not an issuer catalyst.",
            }
        ],
    }
    negative_candidate = {
        "screening_id": "SCR-1",
        "candidate_id": "CAND-1",
        "semantic_verdict": "PASS",
        "primary_fact_id": "FACT-1",
        "primary_quote": "exact quoted evidence",
        "source_row_id": "SRC-1",
        "candidate_generation_eligible": False,
        "final_eligible": False,
        "forbidden_quote_role_detected": True,
        "local_predicate_owner_is_candidate": False,
    }
    candidate = _write_bundle(
        tmp_path / "candidate.md",
        {**evidence, "candidate_semantic_witness.jsonl": [negative_candidate]},
    )
    final = _write_bundle(
        tmp_path / "final.md",
        {**evidence, "final_evidence_witness.jsonl": [negative_candidate]},
    )

    candidate_audit = _semantic_audit(artifact_rows(candidate))
    final_audit = _semantic_audit(artifact_rows(final))

    assert candidate_audit["failure_count"] == 0
    assert any("forbidden_quote_role" in item for item in final_audit["failures"])
    assert any("predicate_owner" in item for item in final_audit["failures"])


def test_legacy_negative_witness_joins_fact_quote_and_screening_verdict(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-negative.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "source_type": "NEWS",
                    "title": "context-only market commentary",
                }
            ],
            "fact_ledger_blind.jsonl": [
                {
                    "fact_id": "FACT-1",
                    "source_id": "SRC-1",
                    "exact_quote": "context-only market commentary",
                }
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "screening_decision": "AUDIT_ONLY",
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "source_row_id": "SRC-1",
                    "fact_id": "FACT-1",
                    "screening_semantic_verdict": "PASS_AS_REJECTION_OR_AUDIT",
                    "fail_reasons": ["NO_LISTED_ISSUER_LOCAL_BINDING"],
                    "local_predicate_owner_is_candidate": False,
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0


def test_rankable_screening_can_be_rejected_by_later_semantic_witness(
    tmp_path: Path,
) -> None:
    blocks = {
        "source_ledger.jsonl": [
            {
                "source_id": "SRC-1",
                "source_type": "NEWS",
                "title": "issuer article without an allowed scoring catalyst",
            }
        ],
        "fact_ledger_blind.jsonl": [
            {
                "fact_id": "FACT-1",
                "source_id": "SRC-1",
                "exact_quote": "issuer article without an allowed scoring catalyst",
            }
        ],
        "candidate_screening.jsonl": [
            {
                "screening_id": "SCR-1",
                "candidate_id": "CAND-1",
                "screening_decision": "INCLUDE",
                "decision_reason_specific": "Retained for semantic review.",
            }
        ],
        "candidate_semantic_witness.jsonl": [
            {
                "screening_id": "SCR-1",
                "candidate_id": "CAND-1",
                "semantic_verdict": "FAIL",
                "primary_fact_id": "FACT-1",
                "primary_quote": "issuer article without an allowed scoring catalyst",
                "candidate_final_eligible": False,
                "proposed_final_entailment": False,
                "witness_outcome": "REJECTED_OR_CONTEXTUALIZED",
                "fail_reasons": ["no allowed positive catalyst relation"],
            }
        ],
    }
    rejected = _write_bundle(tmp_path / "rejected.md", blocks)
    incorrectly_final = _write_bundle(
        tmp_path / "incorrectly-final.md",
        {
            **blocks,
            "final_evidence_witness.jsonl": [
                {
                    "candidate_id": "CAND-1",
                    "primary_fact_id": "FACT-1",
                    "primary_quote": "issuer article without an allowed scoring catalyst",
                    "semantic_verdict": "PASS",
                    "local_predicate_owner_is_candidate": True,
                }
            ],
        },
    )

    rejected_audit = _semantic_audit(artifact_rows(rejected))
    final_audit = _semantic_audit(artifact_rows(incorrectly_final))

    assert rejected_audit["failure_count"] == 0
    assert any("negative_decision_mismatch" in failure for failure in final_audit["failures"])


def test_legacy_final_eligible_semantic_false_closes_rankable_negative(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-final-ineligible.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "NEWS", "title": "issuer notice"}],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1", "source_id": "SRC-1", "exact_quote": "issuer notice"}],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "screening_decision": "WATCH_SECONDARY",
                    "semantic_final_eligible": False,
                    "final_quality_tier": "AUDIT_ONLY",
                    "why_not_final_if_rejected": "No final-grade economic predicate.",
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "semantic_verdict": "FAIL",
                    "final_eligible_semantic": False,
                    "primary_fact_id": "FACT-1",
                    "primary_quote": "issuer notice",
                    "fail_reasons": ["UNRESOLVED_LOCAL_PREDICATE_OWNER"],
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0


def test_explicit_semantic_failure_closes_rankable_negative_without_entailment_alias(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "explicit-semantic-rejection.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "NEWS", "title": "issuer event"}],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1", "source_id": "SRC-1", "exact_quote": "issuer event"}],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "screening_decision": "WATCH_SECONDARY",
                    "decision_reason_specific": "Retained for semantic review.",
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "semantic_verdict": "FAIL",
                    "final_eligible": False,
                    "economic_mechanism_supported_by_quote": False,
                    "primary_fact_id": "FACT-1",
                    "primary_quote": "issuer event",
                    "fail_reasons": ["INCOMPATIBLE_OR_NONCONCRETE_FACT_ROLE"],
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0


def test_artifact_lineage_accepts_only_evidence_bound_semantic_alias_repair(
    tmp_path: Path,
) -> None:
    candidate = {
        "screening_id": "SCR-1",
        "candidate_id": "CAND-1",
        "candidate_company": "Legacy Issuer",
        "ticker": "000001",
        "source_row_id": "SRC-1",
        "primary_fact_id": "FACT-1",
        "primary_quote": "issuer acquisition",
        "local_predicate_owner": "Current Issuer Name",
        "local_predicate_owner_is_candidate": False,
        "target_issuer_is_article_subject": False,
    }
    entity = {
        "entity_resolution_id": "ER-1",
        "source_id": "SRC-1",
        "canonical_company": "Legacy Issuer",
        "local_predicate_owner": "Current Issuer Name",
        "ticker": "000001",
        "local_ticker_ownership_verified": True,
        "resolution_status": "RESOLVED_EXACT_LOCAL_PREDICATE",
    }
    final_witness = {
        "final_evidence_witness_id": "FEW-1",
        "candidate_id": "CAND-1",
        "candidate_company": "Legacy Issuer",
        "ticker": "000001",
        "primary_fact_id": "FACT-1",
        "primary_quote": "issuer acquisition",
        "semantic_verdict": "PASS",
        "issuer_role_anchor_valid": True,
        "local_predicate_owner_is_candidate": True,
        "target_issuer_is_article_subject": True,
    }
    source = _write_bundle(
        tmp_path / "source-alias.md",
        {
            "candidate_semantic_witness.jsonl": [candidate],
            "entity_resolution.jsonl": [entity],
            "final_evidence_witness.jsonl": [final_witness],
        },
    )
    repaired_candidate = {
        **candidate,
        "local_predicate_owner_is_candidate": True,
        "target_issuer_is_article_subject": True,
        "semantic_alias_repair_provenance": {
            "rule_id": "semantic_owner_from_verified_historical_alias.v1",
            "candidate_id": "CAND-1",
            "source_id": "SRC-1",
            "ticker": "000001",
            "entity_resolution_id": "ER-1",
            "final_evidence_witness_id": "FEW-1",
        },
    }
    repaired = _write_bundle(
        tmp_path / "repaired-alias.md",
        {
            "candidate_semantic_witness.jsonl": [repaired_candidate],
            "entity_resolution.jsonl": [entity],
            "final_evidence_witness.jsonl": [final_witness],
        },
    )

    audit = _artifact_lineage_audit(
        artifact_rows(source),
        artifact_rows(repaired),
    )

    assert audit["artifact_illegal_transform_count"] == 0
    source_semantic = _semantic_audit(artifact_rows(source))
    resolved = _resolved_source_semantic_failures(
        artifact_rows(source),
        artifact_rows(repaired),
        source_failures=set(source_semantic["failures"]),
    )
    assert len(resolved) == 1

    repaired_candidate["semantic_alias_repair_provenance"]["ticker"] = "999999"
    tampered = _write_bundle(
        tmp_path / "tampered-alias.md",
        {
            "candidate_semantic_witness.jsonl": [repaired_candidate],
            "entity_resolution.jsonl": [entity],
            "final_evidence_witness.jsonl": [final_witness],
        },
    )
    tampered_audit = _artifact_lineage_audit(
        artifact_rows(source),
        artifact_rows(tampered),
    )

    assert tampered_audit["artifact_illegal_transform_count"] == 1


def test_artifact_lineage_accepts_unique_declared_primary_fact_repair(
    tmp_path: Path,
) -> None:
    screening = {
        "screening_id": "SCR-1",
        "candidate_id": "CAND-1",
        "company": "Target Company",
        "source_fact_ids": ["FACT-GROUP", "FACT-TARGET"],
    }
    facts = [
        {
            "fact_id": "FACT-GROUP",
            "source_row_id": "SRC-GROUP",
            "exact_quote": "Peer One and Peer Two rose.",
        },
        {
            "fact_id": "FACT-TARGET",
            "source_row_id": "SRC-TARGET",
            "exact_quote": "Target Company rose after the policy announcement.",
        },
    ]
    candidate = {
        "screening_id": "SCR-1",
        "candidate_id": "CAND-1",
        "candidate_company": "Target Company",
        "primary_fact_id": "FACT-GROUP",
        "primary_quote": facts[0]["exact_quote"],
        "source_row_id": "SRC-GROUP",
    }
    final = {
        "final_evidence_witness_id": "FEW-1",
        "candidate_id": "CAND-1",
        "candidate_company": "Target Company",
        "primary_fact_id": "FACT-GROUP",
        "primary_quote": facts[0]["exact_quote"],
        "source_row_id": "SRC-GROUP",
    }
    provenance = {
        "rule_id": "primary_fact_from_unique_declared_candidate_surface.v1",
        "candidate_id": "CAND-1",
        "screening_id": "SCR-1",
        "candidate_company": "Target Company",
        "prior_primary_fact_id": "FACT-GROUP",
        "replacement_primary_fact_id": "FACT-TARGET",
        "replacement_fact_sha256": sha256_text(canonical_json(facts[1])),
    }
    repaired_candidate = {
        **candidate,
        "primary_fact_id": "FACT-TARGET",
        "primary_quote": facts[1]["exact_quote"],
        "source_row_id": "SRC-TARGET",
        "semantic_fact_reference_repair_provenance": provenance,
    }
    repaired_final = {
        **final,
        "primary_fact_id": "FACT-TARGET",
        "primary_quote": facts[1]["exact_quote"],
        "source_row_id": "SRC-TARGET",
        "semantic_fact_reference_repair_provenance": provenance,
    }
    source = _write_bundle(
        tmp_path / "source-primary-fact.md",
        {
            "candidate_screening.jsonl": [screening],
            "fact_ledger_blind.jsonl": facts,
            "candidate_semantic_witness.jsonl": [candidate],
            "final_evidence_witness.jsonl": [final],
        },
    )
    repaired = _write_bundle(
        tmp_path / "repaired-primary-fact.md",
        {
            "candidate_screening.jsonl": [screening],
            "fact_ledger_blind.jsonl": facts,
            "candidate_semantic_witness.jsonl": [repaired_candidate],
            "final_evidence_witness.jsonl": [repaired_final],
        },
    )

    audit = _artifact_lineage_audit(artifact_rows(source), artifact_rows(repaired))

    assert audit["artifact_illegal_transform_count"] == 0

    repaired_final["semantic_fact_reference_repair_provenance"] = {
        **provenance,
        "replacement_fact_sha256": "f" * 64,
    }
    tampered = _write_bundle(
        tmp_path / "tampered-primary-fact.md",
        {
            "candidate_screening.jsonl": [screening],
            "fact_ledger_blind.jsonl": facts,
            "candidate_semantic_witness.jsonl": [repaired_candidate],
            "final_evidence_witness.jsonl": [repaired_final],
        },
    )

    tampered_audit = _artifact_lineage_audit(
        artifact_rows(source),
        artifact_rows(tampered),
    )
    assert tampered_audit["artifact_illegal_transform_count"] == 1


def test_legacy_body_quote_with_raw_row_hash_requires_external_verification(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-hashed-body.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "source_type": "NEWS_CSV_ROW",
                    "title": "short title",
                    "raw_row_sha256": "a" * 64,
                }
            ],
            "fact_ledger_blind.jsonl": [
                {
                    "fact_id": "FACT-1",
                    "source_id": "SRC-1",
                    "exact_quote": "quote retained from the hashed source row body",
                    "quote_found_in_source_row": True,
                }
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "screening_decision": "AUDIT_ONLY",
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "source_row_id": "SRC-1",
                    "fact_id": "FACT-1",
                    "screening_semantic_verdict": "PASS_AS_REJECTION_OR_AUDIT",
                    "fail_reasons": ["NO_LISTED_ISSUER_LOCAL_BINDING"],
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0
    assert audit["external_quote_verification_required_count"] == 1


def test_final_semantic_pass_mirrors_may_be_added_from_semantic_result(
    tmp_path: Path,
) -> None:
    source_row = {
        "candidate_id": "CAND-1",
        "final_semantic_audit_id": "FSA-1",
        "semantic_result": "PASS",
        "local_predicate_owner_match": True,
        "economic_mechanism_supported": True,
    }
    repaired_row = {
        **source_row,
        "status": "PASS",
        "semantic_audit_status": "PASS",
        "semantic_verdict": "PASS",
    }
    source = _write_bundle(
        tmp_path / "source-semantic-result.md",
        {"final_semantic_audit.jsonl": [source_row]},
    )
    repaired = _write_bundle(
        tmp_path / "repaired-semantic-result.md",
        {"final_semantic_audit.jsonl": [repaired_row]},
    )

    audit = _artifact_lineage_audit(
        artifact_rows(source),
        artifact_rows(repaired),
    )

    assert audit["artifact_illegal_transform_count"] == 0


def test_final_semantic_pass_mirrors_are_allowed_from_audit_result(
    tmp_path: Path,
) -> None:
    source_row = {
        "candidate_id": "CAND-1",
        "final_semantic_audit_id": "FSA-1",
        "audit_result": "PASS",
    }
    repaired_row = {
        **source_row,
        "status": "PASS",
        "semantic_audit_status": "PASS",
        "semantic_verdict": "PASS",
    }
    source = _write_bundle(
        tmp_path / "source-audit-result.md",
        {"final_semantic_audit.jsonl": [source_row]},
    )
    repaired = _write_bundle(
        tmp_path / "repaired-audit-result.md",
        {"final_semantic_audit.jsonl": [repaired_row]},
    )

    audit = _artifact_lineage_audit(
        artifact_rows(source),
        artifact_rows(repaired),
    )

    assert audit["artifact_illegal_transform_count"] == 0


def test_final_semantic_verified_status_can_be_canonicalized_to_pass(
    tmp_path: Path,
) -> None:
    source_row = {
        "candidate_id": "CAND-1",
        "semantic_audit_status": "VERIFIED",
        "semantic_verdict": "PASS",
        "status": "PASS",
    }
    repaired_row = {**source_row, "semantic_audit_status": "PASS"}
    source = _write_bundle(
        tmp_path / "source-verified-semantic.md",
        {"final_semantic_audit.jsonl": [source_row]},
    )
    repaired = _write_bundle(
        tmp_path / "repaired-verified-semantic.md",
        {"final_semantic_audit.jsonl": [repaired_row]},
    )

    audit = _artifact_lineage_audit(
        artifact_rows(source),
        artifact_rows(repaired),
    )

    assert audit["artifact_illegal_transform_count"] == 0


def test_ineligible_null_outcome_mirror_is_an_allowed_transform() -> None:
    before = {
        "record_type": "supervised_issuer_day_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "training_exclusion_reason": "NO_TRADABLE_ROW_ON_D",
        "D_outcome": None,
        "payload": {"D_outcome": None},
    }
    after = {
        **before,
        "D_outcome": {},
    }

    assert _illegal_transform_paths(before, after) == []


def test_unverified_outcome_null_mirror_is_an_allowed_transform() -> None:
    before = {
        "record_id": "BD-UNVERIFIED-OUTCOME",
        "record_type": "supervised_issuer_day_case",
        "training_eligible": False,
        "training_exclusion_reason": "no_verified_D_outcome_row",
        "sample_weight": 0.0,
        "D_outcome": None,
        "payload": {"D_outcome": None},
    }
    after = {
        **before,
        "D_outcome": {},
    }
    assert _illegal_transform_paths(before, after) == []


def test_ineligible_nested_null_outcome_mirror_allows_legacy_reason() -> None:
    before = {
        "record_type": "supervised_issuer_day_case",
        "training_eligible": False,
        "training_exclusion_reason": "issuer_day_outcome_or_provenance_not_eligible",
        "sample_weight": 0.0,
        "payload": {"D_outcome": None, "response_class": "LOW_RESPONSE"},
    }
    after = {**before, "D_outcome": {}}

    assert _illegal_transform_paths(before, after) == []


def test_payload_company_mirror_is_an_allowed_existing_field_transform() -> None:
    before = {
        "record_type": "context_market_state_or_fact_case",
        "payload": {"name": "Example Issuer", "ticker": "000001"},
    }
    after = {
        **before,
        "company_name": "Example Issuer",
        "ticker": "000001",
    }

    assert _illegal_transform_paths(before, after) == []


def test_issuer_name_company_mirror_is_an_allowed_existing_field_transform() -> None:
    before = {
        "record_type": "supervised_issuer_day_case",
        "issuer_name": "Example Issuer",
        "ticker": "000001",
    }
    after = {
        **before,
        "company_name": "Example Issuer",
    }

    assert _illegal_transform_paths(before, after) == []


def test_fractional_issuer_weight_group_normalization_is_allowed() -> None:
    before = {
        "record_type": "supervised_direct_event_case",
        "issuer_day_case_id": "IDCASE-1",
        "issuer_day_weight_group_id": "IDCASE-1",
        "issuer_day_sample_weight_policy": "single_issuer_day_case",
        "trade_date": "2018-03-07",
        "ticker": "000001",
    }
    after = {
        **before,
        "issuer_day_weight_group_id": "2018-03-07:000001",
        "issuer_day_sample_weight_policy": "fractional_issuer_day_group",
    }

    assert _illegal_transform_paths(before, after) == []


def test_legacy_witness_status_and_source_fact_aliases_are_supported(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-witness-status.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "source_type": "NEWS_CSV_ROW",
                    "title": "issuer fact",
                }
            ],
            "fact_ledger_blind.jsonl": [
                {
                    "fact_id": "FACT-1",
                    "source_id": "SRC-1",
                    "exact_quote": "issuer fact",
                }
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "screening_decision": "REJECT_SEMANTIC_FALSE_POSITIVE",
                    "decision_reason_specific": "No safe listed issuer binding.",
                    "source_fact_ids": ["FACT-1"],
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "source_fact_ids": ["FACT-1"],
                    "screening_decision": "REJECT_SEMANTIC_FALSE_POSITIVE",
                    "witness_status": "PASS",
                }
            ],
            "final_evidence_witness.jsonl": [
                {
                    "candidate_id": "CAND-FINAL",
                    "source_fact_ids": ["FACT-1"],
                    "exact_quote": "issuer fact",
                    "semantic_entailment": "SUPPORTED",
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0


def test_legacy_regression_profile_is_importable_but_not_current_contract(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-regression.md",
        {
            "semantic_regression_tests.jsonl": [
                {
                    "semantic_fixture_id": "SFIX-01",
                    "expected": "INCLUDE",
                    "actual": "INCLUDE",
                    "passed": True,
                }
            ]
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0
    assert audit["legacy_regression_profile_valid"] is True
    assert audit["current_regression_contract_pass"] is False


def test_fixture_result_pass_is_legacy_only_when_expectations_match(
    tmp_path: Path,
) -> None:
    rows = [
        {
            "fixture_id": f"SEM-{index:03d}",
            "fixture_result": "PASS",
            "expected_candidate_eligible": False,
            "actual_candidate_eligible": False,
            "expected_reason": "GENERIC_COLLISION",
            "actual_reason": "GENERIC_COLLISION",
        }
        for index in range(1, 14)
    ]
    bundle = _write_bundle(
        tmp_path / "fixture-result.md",
        {"semantic_regression_tests.jsonl": rows},
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0
    assert audit["legacy_regression_profile_valid"] is True
    assert audit["current_regression_contract_pass"] is False


def test_rankable_nonfinal_candidate_can_record_forbidden_quote_role(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "rankable-nonfinal.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "source_type": "NEWS",
                    "title": "unconfirmed but issuer-local report",
                }
            ],
            "fact_ledger_blind.jsonl": [
                {
                    "fact_id": "FACT-1",
                    "source_id": "SRC-1",
                    "exact_quote": "unconfirmed but issuer-local report",
                }
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "screening_decision": "WATCH_SECONDARY",
                    "source_fact_ids": ["FACT-1"],
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "semantic_verdict": "PASS",
                    "primary_fact_id": "FACT-1",
                    "primary_quote": "unconfirmed but issuer-local report",
                    "candidate_generation_eligible": True,
                    "final_eligible": False,
                    "forbidden_quote_role_detected": True,
                    "local_predicate_owner_is_candidate": True,
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0


def test_legacy_pass_witness_can_mirror_screening_risk_flags(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-risk-flags.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "source_type": "NEWS_CSV_ROW",
                    "title": "issuer-local event",
                }
            ],
            "fact_ledger_blind.jsonl": [
                {
                    "fact_id": "FACT-1",
                    "source_id": "SRC-1",
                    "exact_quote": "issuer-local event",
                }
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "screening_decision": "INCLUDE",
                    "source_fact_ids": ["FACT-1"],
                    "semantic_risk_flags": ["INTENT_NOT_COMPLETED"],
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "fact_id": "FACT-1",
                    "screening_semantic_verdict": "PASS",
                    "fail_reasons": ["INTENT_NOT_COMPLETED"],
                    "candidate_company": "Issuer",
                    "local_predicate_owner": "Issuer",
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0


def test_source_semantic_pass_requires_legacy_corroboration() -> None:
    assert not _source_semantic_row_passes({"semantic_pass": True})
    assert _source_semantic_row_passes(
        {
            "semantic_pass": True,
            "article_subject_local_predicate_owner_verified": True,
            "economic_mechanism_supported_verified": True,
            "forbidden_quote_role_detected": False,
            "final_evidence_witness_id": "FEW-1",
        }
    )


def test_final_audit_joins_idless_witness_by_unique_candidate_id(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "idless-final-witness.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "source_type": "NEWS",
                    "title": "issuer signed a supply contract",
                }
            ],
            "fact_ledger_blind.jsonl": [
                {
                    "fact_id": "FACT-1",
                    "source_id": "SRC-1",
                    "exact_quote": "issuer signed a supply contract",
                }
            ],
            "final_evidence_witness.jsonl": [
                {
                    "candidate_id": "CAND-1",
                    "semantic_verdict": "PASS",
                    "primary_fact_id": "FACT-1",
                    "primary_quote": "issuer signed a supply contract",
                    "forbidden_quote_role_detected": False,
                    "local_predicate_owner_is_candidate": True,
                }
            ],
            "final_semantic_audit.jsonl": [
                {
                    "audit_id": "FSA-1",
                    "final_evidence_witness_id": "FEW-1",
                    "candidate_id": "CAND-1",
                    "semantic_pass": True,
                    "fail_reasons": [],
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0


def test_rejected_candidate_witness_without_fact_is_valid_negative(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "negative.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "row_id": "NEWS-1",
                    "source_type": "NEWS_CSV_ROW",
                    "title": "routine market notice",
                }
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "screening_decision": "AUDIT_ONLY",
                    "no_fact_rejection_reason": "ROUTINE_NOTICE",
                    "source_fact_ids": [],
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "row_id": "NEWS-1",
                    "source_quote": "routine market notice",
                    "accepted_for_rankable_pool": False,
                    "semantic_verdict": "REJECTED_OR_AUDIT",
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0


@pytest.mark.parametrize(("with_hashes", "expected_failures"), [(True, 0), (False, 1)])
def test_factless_negative_body_quote_requires_sha_bound_csv_source(
    tmp_path: Path,
    with_hashes: bool,
    expected_failures: int,
) -> None:
    source = {
        "source_id": "SRC-1",
        "source_type": "NEWS_CSV_ROW",
        "title": "affiliate headline",
        "input_file": "news_20240102.csv",
    }
    if with_hashes:
        source.update(
            {
                "content_sha256": "a" * 64,
                "raw_row_sha256": "b" * 64,
                "input_sha256": "c" * 64,
            }
        )
    bundle = _write_bundle(
        tmp_path / f"factless-negative-{with_hashes}.md",
        {
            "source_ledger.jsonl": [source],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "screening_decision": "REJECT_SEMANTIC_FALSE_POSITIVE",
                    "decision_reason_specific": "Affiliate is not the listed issuer.",
                    "source_fact_ids": [],
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "source_row_id": "SRC-1",
                    "primary_quote": "long body quote retained only in the CSV row",
                    "semantic_verdict": "FAIL",
                    "fail_reasons": ["affiliate owner mismatch"],
                    "local_predicate_owner_is_candidate": False,
                    "forbidden_quote_role_detected": True,
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == expected_failures
    assert audit["external_quote_verification_required_count"] == int(with_hashes)


def test_positive_candidate_witness_uses_screening_fact_and_external_body_hash(
    tmp_path: Path,
) -> None:
    content_sha = "a" * 64
    bundle = _write_bundle(
        tmp_path / "positive.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "row_id": "NEWS-1",
                    "source_type": "NEWS_CSV_ROW",
                    "title": "short title",
                    "body_missing": False,
                    "content_sha256": content_sha,
                }
            ],
            "fact_ledger_blind.jsonl": [
                {
                    "fact_id": "FACT-1",
                    "source_id": "SRC-1",
                    "exact_quote": "long body quote retained in the hashed CSV row",
                    "quote_found_in_source_row": True,
                }
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "screening_decision": "INCLUDE",
                    "source_fact_ids": ["FACT-1"],
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "candidate_company": "Issuer",
                    "local_predicate_owner": "Issuer",
                    "source_quote": "long body quote retained in the hashed CSV row",
                    "semantic_verdict": "PASS",
                }
            ],
        },
    )

    audit = _semantic_audit(artifact_rows(bundle))

    assert audit["failure_count"] == 0
    assert audit["external_quote_verification_required_count"] == 1


def test_negative_candidate_quote_is_checked_against_full_source_text(
    tmp_path: Path,
) -> None:
    blocks = {
        "source_ledger.jsonl": [
            {
                "source_id": "SRC-1",
                "source_type": "NEWS_CSV_ROW",
                "title": "short heading",
                "body": "the retained body contains exact negative evidence",
            }
        ],
        "fact_ledger_blind.jsonl": [
            {
                "fact_id": "FACT-1",
                "source_id": "SRC-1",
                "exact_quote": "exact negative evidence",
            }
        ],
        "candidate_screening.jsonl": [
            {
                "screening_id": "SCR-1",
                "screening_decision": "AUDIT_ONLY",
                "decision_reason_specific": "Not a rankable issuer catalyst.",
                "source_fact_ids": ["FACT-1"],
            }
        ],
        "candidate_semantic_witness.jsonl": [
            {
                "screening_id": "SCR-1",
                "candidate_id": "CAND-1",
                "fact_id": "FACT-1",
                "semantic_verdict": "PASS_AS_REJECTION",
            }
        ],
    }
    valid = _write_bundle(tmp_path / "body-quote.md", blocks)
    invalid_blocks = json.loads(json.dumps(blocks))
    invalid_blocks["source_ledger.jsonl"][0]["body"] = "unrelated body"
    invalid = _write_bundle(tmp_path / "missing-body-quote.md", invalid_blocks)

    valid_audit = _semantic_audit(artifact_rows(valid))
    invalid_audit = _semantic_audit(artifact_rows(invalid))

    assert valid_audit["failure_count"] == 0
    assert any("negative_quote_not_in_source" in failure for failure in invalid_audit["failures"])


def test_leader_policy_uses_declared_top20_and_ticker_join(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-1",
                    "code": "000001",
                    "high_return_pct": 12.0,
                    "amount_rank": 100,
                    "turnover_rank": 100,
                },
                {
                    "outcome_id": "OUT-2",
                    "code": "000002",
                    "high_return_pct": 1.0,
                    "amount_rank": 20,
                    "turnover_rank": 100,
                },
                {
                    "outcome_id": "OUT-3",
                    "code": "000003",
                    "high_return_pct": 1.0,
                    "amount_rank": 25,
                    "turnover_rank": 100,
                },
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-1",
                    "ticker": "000001",
                    "policy_memberships": ["HIGH10", "AMOUNT_TOP20"],
                },
                {
                    "outcome_leader_id": "LEAD-2",
                    "ticker": "000002",
                    "policy_inclusion_reasons": ["AMOUNT_TOP20", "TURNOVER_TOP20"],
                },
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-1"},
                {"outcome_leader_id": "LEAD-2"},
            ],
        },
    )

    census = census_source(bundle)
    population = _population_audit(
        artifact_rows(bundle),
        present_artifact_names=set(census.artifact_counts),
    )

    rule = population["rules"]["outcome_to_leader_census"]
    assert rule["missing_keys"] == []
    assert rule["extra_keys"] == []
    assert population["leader_amount_top_n"] == 20
    assert population["leader_turnover_top_n"] == 20


def test_leader_census_id_alias_joins_reverse_audit(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "leader-census-id.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-1",
                    "ticker": "000001",
                    "high_return_pct": 12.0,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "leader_census_id": "LEAD-1",
                    "outcome_row_id": "OUT-1",
                    "ticker": "000001",
                    "leader_policy_labels": ["HIGH_RETURN_GE_10"],
                }
            ],
            "outcome_to_news_audit.jsonl": [{"leader_census_id": "LEAD-1"}],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    rule = population["rules"]["leader_to_reverse_audit"]
    assert rule["missing_keys"] == []
    assert rule["extra_keys"] == []


def test_leader_population_reads_raw_snapshot_row_metrics(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "raw-snapshot-leader.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-1",
                    "raw_snapshot_row": {
                        "code": "000001",
                        "high_return_pct": 12.0,
                    },
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-1",
                    "outcome_id": "OUT-1",
                    "ticker": "000001",
                }
            ],
            "outcome_to_news_audit.jsonl": [{"outcome_leader_id": "LEAD-1"}],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    rule = population["rules"]["outcome_to_leader_census"]
    assert rule["missing_keys"] == []
    assert rule["extra_keys"] == []


def test_leader_policy_accepts_declared_aliases_and_nested_outcome_metrics(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "nested-leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_ledger_id": "OUT-A",
                    "D_response": {
                        "ticker": "TICKER-A",
                        "high_return_pct": 6.0,
                        "amount_rank": 90,
                        "turnover_rank": 90,
                        "upper_limit_touched": False,
                    },
                },
                {
                    "outcome_ledger_id": "OUT-B",
                    "D_response": {
                        "ticker": "TICKER-B",
                        "high_return_pct": 1.0,
                        "amount_rank": 30,
                        "turnover_rank": 90,
                        "upper_limit_touched": False,
                    },
                },
                {
                    "outcome_ledger_id": "OUT-C",
                    "fields": {
                        "ticker": "TICKER-C",
                        "high_return_pct": 1.0,
                        "amount_rank": 31,
                        "turnover_rank": 31,
                        "upper_limit_touched": False,
                    },
                },
            ],
            "outcome_leader_census.jsonl": [
                {
                    "leader_id": "LEAD-A",
                    "ticker": "TICKER-A",
                    "outcome_categories": ["HIGH_GE_5", "AMOUNT_TOP30", "TURNOVER_TOP30"],
                },
                {
                    "outcome_leader_id": "LEAD-B",
                    "ticker": "TICKER-B",
                    "inclusion_reasons": ["AMOUNT_RANK_TOP_30"],
                },
            ],
            "outcome_to_news_audit.jsonl": [
                {"leader_id": "LEAD-A"},
                {"outcome_leader_id": "LEAD-B"},
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 30
    assert population["leader_turnover_top_n"] == 30
    assert population["leader_high_return_threshold"] == 5.0
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_class_count_tokens_from_postmortem_summary(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "class-count-policy.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-1",
                    "ticker": "000001",
                    "high_return_pct": 12.0,
                    "amount_rank": 100,
                    "turnover_rank": 100,
                },
                {
                    "outcome_row_id": "OUT-2",
                    "ticker": "000002",
                    "high_return_pct": 1.0,
                    "amount_rank": 20,
                    "turnover_rank": 100,
                },
            ],
            "outcome_leader_census.jsonl": [
                {"outcome_leader_id": "LEAD-1", "ticker": "000001"},
                {"outcome_leader_id": "LEAD-2", "ticker": "000002"},
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-1"},
                {"outcome_leader_id": "LEAD-2"},
            ],
            "postmortem_summary.json": [
                {
                    "leader_census": {
                        "class_counts": {
                            "HIGH10": 1,
                            "AMOUNT_TOP20": 1,
                        }
                    }
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 20
    assert population["leader_high_return_threshold"] == 10.0
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_leader_policy_memberships_without_global_policy(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "leader-membership-policy.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-AMOUNT",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 5,
                    "turnover_rank": 100,
                },
                {
                    "outcome_row_id": "OUT-TURNOVER",
                    "ticker": "000002",
                    "high_return_pct": 1.0,
                    "amount_rank": 100,
                    "turnover_rank": 5,
                },
                {
                    "outcome_row_id": "OUT-OTHER",
                    "ticker": "000003",
                    "high_return_pct": 1.0,
                    "amount_rank": 30,
                    "turnover_rank": 100,
                },
                {
                    "outcome_row_id": "OUT-QUALIFYING",
                    "ticker": "000004",
                    "high_return_pct": 1.0,
                    "amount_rank": 25,
                    "turnover_rank": 100,
                },
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-AMOUNT",
                    "outcome_row_id": "OUT-AMOUNT",
                    "ticker": "000001",
                    "leader_policy_memberships": ["AMOUNT_RANK_TOP_30"],
                },
                {
                    "outcome_leader_id": "LEAD-TURNOVER",
                    "outcome_row_id": "OUT-TURNOVER",
                    "ticker": "000002",
                    "leader_policy_memberships": ["TURNOVER_RANK_TOP_30"],
                },
                {
                    "outcome_leader_id": "LEAD-INCLUSION",
                    "outcome_row_id": "OUT-OTHER",
                    "ticker": "000003",
                    "inclusion_flags": ["AMOUNT_RANK_TOP_30"],
                },
                {
                    "outcome_leader_id": "LEAD-QUALIFYING",
                    "outcome_row_id": "OUT-QUALIFYING",
                    "ticker": "000004",
                    "policy_criteria": ["amount_rank_top30"],
                },
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-AMOUNT"},
                {"outcome_leader_id": "LEAD-TURNOVER"},
                {"outcome_leader_id": "LEAD-INCLUSION"},
                {"outcome_leader_id": "LEAD-QUALIFYING"},
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 30
    assert population["leader_turnover_top_n"] == 30
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_numeric_threshold_fields_from_leader_rows(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "numeric-leader-policy.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-1",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 21,
                    "turnover_rank": 21,
                },
                {
                    "outcome_row_id": "OUT-2",
                    "ticker": "000002",
                    "high_return_pct": 1.0,
                    "amount_rank": 20,
                    "turnover_rank": 21,
                },
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-2",
                    "outcome_id": "OUT-2",
                    "ticker": "000002",
                    "amount_rank_top_group_threshold": 20,
                    "turnover_rank_top_group_threshold": 20,
                }
            ],
            "outcome_to_news_audit.jsonl": [{"outcome_leader_id": "LEAD-2"}],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 20
    assert population["leader_turnover_top_n"] == 20
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_accepts_legacy_reason_and_qualifier_aliases(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-leader-policy.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-1",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 100,
                    "turnover_rank": 100,
                },
                {
                    "outcome_row_id": "OUT-2",
                    "ticker": "000002",
                    "high_return_pct": 1.0,
                    "amount_rank": 30,
                    "turnover_rank": 100,
                },
                {
                    "outcome_row_id": "OUT-QUARANTINED",
                    "ticker": "000003",
                    "high_return_pct": None,
                    "amount_rank": 1,
                    "turnover_rank": 1,
                    "label_quality": "quarantined",
                    "data_quality_status": "blocked_by_corporate_action",
                },
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-1",
                    "outcome_row_id": "OUT-1",
                    "leader_qualifiers": ["AMOUNT_TOP100", "TURNOVER_TOP100"],
                },
                {
                    "outcome_leader_id": "LEAD-2",
                    "outcome_row_id": "OUT-2",
                    "leader_reasons": [
                        "AMOUNT_RANK_TOP_100",
                        "TURNOVER_RANK_TOP_100",
                    ],
                },
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-1"},
                {"outcome_leader_id": "LEAD-2"},
            ],
            "postmortem_summary.json": [
                {
                    "leader_policy": {
                        "amount_rank_top_group": 100,
                        "turnover_rank_top_group": 100,
                        "high_return_thresholds": [5, 10, 15, 20],
                    },
                    "quarantine_census": [{"ticker": "000003"}],
                }
            ],
        },
    )

    census = census_source(bundle)
    population = _population_audit(
        artifact_rows(bundle),
        present_artifact_names=set(census.artifact_counts),
    )

    assert population["leader_amount_top_n"] == 100
    assert population["leader_turnover_top_n"] == 100
    assert population["leader_high_return_threshold"] == 5.0
    assert population["leader_separate_quarantine_count"] == 1
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_group_max_threshold_aliases(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "group-max-leader-policy.md",
        {
            "postmortem_summary.json": [
                {
                    "leader_policy": {
                        "amount_rank_top_group_max": 50,
                        "turnover_rank_top_group_max": 50,
                    }
                }
            ],
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-1",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 50,
                    "turnover_rank": 200,
                },
                {
                    "outcome_row_id": "OUT-2",
                    "ticker": "000002",
                    "high_return_pct": 1.0,
                    "amount_rank": 200,
                    "turnover_rank": 50,
                },
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-1",
                    "outcome_row_id": "OUT-1",
                    "ticker": "000001",
                },
                {
                    "outcome_leader_id": "LEAD-2",
                    "outcome_row_id": "OUT-2",
                    "ticker": "000002",
                },
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-1"},
                {"outcome_leader_id": "LEAD-2"},
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 50
    assert population["leader_turnover_top_n"] == 50
    assert population["liquidity_policy_underspecified_count"] == 0


def test_leader_policy_reads_selection_policy_and_criteria_aliases(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "selection-policy-leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-HIGH5",
                    "ticker": "000001",
                    "high_return_pct": 5.0,
                    "amount_rank": 999,
                    "turnover_rank": 999,
                },
                {
                    "outcome_row_id": "OUT-AMOUNT",
                    "ticker": "000002",
                    "high_return_pct": 1.0,
                    "amount_rank": 30,
                    "turnover_rank": 999,
                },
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-HIGH5",
                    "outcome_row_id": "OUT-HIGH5",
                    "ticker": "000001",
                    "selection_policy": "HIGH_RETURN_GE_5",
                },
                {
                    "outcome_leader_id": "LEAD-AMOUNT",
                    "outcome_row_id": "OUT-AMOUNT",
                    "ticker": "000002",
                    "selection_criteria_met": ["AMOUNT_RANK_TOP_30"],
                },
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-HIGH5"},
                {"outcome_leader_id": "LEAD-AMOUNT"},
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_high_return_threshold"] == 5.0
    assert population["leader_amount_top_n"] == 30
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_outcome_leader_policy_reads_nested_snapshot_sections(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "nested-snapshot-leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-UPPER",
                    "ticker": "000001",
                    "upper_limit": {"upper_limit_touched": True},
                    "returns": {"high_return_pct": 1.0},
                    "ranks": {"amount_rank": 999, "turnover_rank": 999},
                },
                {
                    "outcome_id": "OUT-AMOUNT",
                    "ticker": "000002",
                    "upper_limit": {"upper_limit_touched": False},
                    "returns": {"high_return_pct": 1.0},
                    "ranks": {"amount_rank": 30, "turnover_rank": 999},
                },
            ],
            "outcome_leader_census.jsonl": [
                {"outcome_leader_id": "LEAD-UPPER", "outcome_id": "OUT-UPPER"},
                {"outcome_leader_id": "LEAD-AMOUNT", "outcome_id": "OUT-AMOUNT"},
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-UPPER"},
                {"outcome_leader_id": "LEAD-AMOUNT"},
            ],
            "postmortem_summary.json": [
                {"leader_policy": {"amount_rank_top_group": 30}}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_nested_criteria_membership(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "nested-criteria-leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-LIQUIDITY",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 20,
                    "turnover_rank": 999,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-LIQUIDITY",
                    "outcome_ledger_id": "OUT-LIQUIDITY",
                    "criteria": {"amount_top20": True},
                }
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-LIQUIDITY"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 20
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_policy_tag_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "policy-tag-leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-LIQUIDITY",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 30,
                    "turnover_rank": 999,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-LIQUIDITY",
                    "outcome_id": "OUT-LIQUIDITY",
                    "leader_policy_tags": ["AMOUNT_RANK_TOP_30"],
                }
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-LIQUIDITY"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 30
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_leader_basis_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "leader-basis-alias.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-TURNOVER",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 999,
                    "turnover_rank": 50,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-TURNOVER",
                    "source_outcome_row_id": "OUT-TURNOVER",
                    "leader_basis": ["turnover_rank_top_50"],
                }
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-TURNOVER"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_turnover_top_n"] == 50
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_leader_criteria_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "leader-criteria-alias.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-AMOUNT",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 20,
                    "turnover_rank": 999,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-AMOUNT",
                    "outcome_id": "OUT-AMOUNT",
                    "leader_criteria": ["amount_rank_top20"],
                }
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-AMOUNT"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 20
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_cohort_memberships_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "cohort-membership-leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-LIQUIDITY",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 30,
                    "turnover_rank": 999,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-LIQUIDITY",
                    "outcome_id": "OUT-LIQUIDITY",
                    "cohort_memberships": ["AMOUNT_RANK_TOP_30"],
                }
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-LIQUIDITY"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 30
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_high_return_rank_membership(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "high-rank-membership-leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-HIGH-RANK",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "high_return_rank": 30,
                    "amount_rank": 999,
                    "turnover_rank": 999,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-HIGH-RANK",
                    "outcome_id": "OUT-HIGH-RANK",
                    "cohort_memberships": ["HIGH_RETURN_TOP_30_CLEAN"],
                }
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-HIGH-RANK"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_high_return_rank_top_n"] == 30
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_verified_high_return_top_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "verified-high-return-top-alias.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-HIGH-RANK",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "high_return_rank": 30,
                    "amount_rank": 999,
                    "turnover_rank": 999,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-HIGH-RANK",
                    "outcome_id": "OUT-HIGH-RANK",
                    "inclusion_reasons": ["TOP30_VERIFIED_HIGH_RETURN"],
                }
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-HIGH-RANK"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_high_return_rank_top_n"] == 30
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_keeps_explicit_final_watchlist_join_membership(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "final-watchlist-join-leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-FINAL-JOIN",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "high_return_rank": 999,
                    "amount_rank": 999,
                    "turnover_rank": 999,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-FINAL-JOIN",
                    "outcome_id": "OUT-FINAL-JOIN",
                    "cohort_memberships": ["SEALED_FINAL_WATCHLIST_OUTCOME_JOIN"],
                }
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-FINAL-JOIN"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_final_watchlist_join_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "final-watchlist-join-alias.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-FINAL",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 999,
                    "turnover_rank": 999,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-FINAL",
                    "outcome_id": "OUT-FINAL",
                    "outcome_classes": ["FINAL_WATCHLIST_JOIN"],
                }
            ],
            "outcome_to_news_audit.jsonl": [{"outcome_leader_id": "LEAD-FINAL"}],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_reads_qualifying_reasons_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "qualifying-reasons-leaders.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-LIQUIDITY",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 30,
                    "turnover_rank": 999,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-LIQUIDITY",
                    "source_outcome_id": "OUT-LIQUIDITY",
                    "qualifying_reasons": ["AMOUNT_RANK_TOP30"],
                }
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-LIQUIDITY"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 30
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_leader_policy_flags_and_outcome_class_aliases_define_membership(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "leader-policy-flags.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-AMOUNT",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 20,
                    "turnover_rank": 999,
                },
                {
                    "outcome_row_id": "OUT-TURNOVER",
                    "ticker": "000002",
                    "high_return_pct": 1.0,
                    "amount_rank": 999,
                    "turnover_rank": 20,
                },
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-AMOUNT",
                    "outcome_row_id": "OUT-AMOUNT",
                    "leader_policy_flags": ["AMOUNT_TOP20"],
                },
                {
                    "outcome_leader_id": "LEAD-TURNOVER",
                    "outcome_row_id": "OUT-TURNOVER",
                    "outcome_class": "TURNOVER_TOP20",
                },
            ],
            "outcome_to_news_audit.jsonl": [
                {"outcome_leader_id": "LEAD-AMOUNT"},
                {"outcome_leader_id": "LEAD-TURNOVER"},
            ],
        },
    )

    census = census_source(bundle)
    population = _population_audit(
        artifact_rows(bundle),
        present_artifact_names=set(census.artifact_counts),
    )

    assert population["leader_amount_top_n"] == 20
    assert population["leader_turnover_top_n"] == 20
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_policy_leader_flags_alias_defines_liquidity_threshold(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "policy-leader-flags-alias.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-1",
                    "ticker": "000001",
                    "high_return_pct": 1.0,
                    "amount_rank": 999,
                    "turnover_rank": 50,
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-1",
                    "ticker": "000001",
                    "policy_leader_flags": ["AMOUNT_TOP50", "TURNOVER_TOP50"],
                }
            ],
        },
    )

    population = _population_audit(
        artifact_rows(bundle),
        present_artifact_names={
            "outcome_ledger.jsonl",
            "outcome_leader_census.jsonl",
        },
    )

    assert population["leader_turnover_top_n"] == 50
    assert population["liquidity_policy_underspecified_count"] == 0


def test_candidate_and_outcome_legacy_scalar_aliases_close_population(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-candidate-and-outcome-aliases.md",
        {
            "candidate_screening.jsonl": [
                {
                    "candidate_id": "CAND-1",
                    "ticker": "000001",
                    "screening_decision": "INCLUDE_PRIMARY",
                    "rankable": True,
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {"candidate_id": "CAND-1", "ticker": "000001"}
            ],
            "candidate_semantic_witness.jsonl": [{"candidate_id": "CAND-1"}],
            "outcome_ledger.jsonl": [
                {
                    "outcome_id": "OUT-1",
                    "ticker": "000001",
                    "high_return_pct": "12.5",
                    "amount_rank": "999",
                    "turnover_rank": "999",
                }
            ],
            "outcome_leader_census.jsonl": [
                {"leader_id": "LEAD-1", "outcome_id": "OUT-1"}
            ],
            "outcome_to_news_audit.jsonl": [{"leader_id": "LEAD-1"}],
        },
    )

    census = census_source(bundle)
    population = _population_audit(
        artifact_rows(bundle),
        present_artifact_names=set(census.artifact_counts),
    )

    assert population["rules"]["rankable_to_ranking_audit"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_ranking_rows_allow_multiple_tickers_per_screening_without_duplicate_key(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "ranking-multi-ticker.md",
        {
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "ticker": "000001",
                    "screening_decision": "WATCH",
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "candidate_ranking_id": "CRA-1",
                    "source_screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "ticker": "000001",
                },
                {
                    "candidate_ranking_id": "CRA-2",
                    "source_screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "ticker": "000002",
                },
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["duplicate_logical_key_count"] == 0


def test_negative_control_group_id_uses_screening_as_row_key(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "negative-group-rows.md",
        {
            "negative_control_cases.jsonl": [
                {
                    "case_id": "RANKCASE-1",
                    "source_screening_id": "SCR-1",
                    "source_fact_ids": ["FACT-1"],
                    "ticker": "000001",
                },
                {
                    "case_id": "RANKCASE-1",
                    "source_screening_id": "SCR-2",
                    "source_fact_ids": ["FACT-2"],
                    "ticker": "000002",
                },
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["duplicate_logical_key_count"] == 0


def test_nontraining_case_underfill_accepts_preseal_access_quarantine(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "preseal-quarantine-case.md",
        {
            "negative_control_cases.jsonl": [
                {
                    "negative_control_case_id": "NEG-1",
                    "training_eligible": False,
                    "sample_weight": 0.0,
                    "training_exclusion_reason": "PRESEAL_OUTCOME_ACCESS_ORDER_VIOLATION",
                    "screening_decision": "AUDIT_ONLY",
                }
            ],
            "brain_delta.jsonl": [],
        },
    )
    rows = artifact_rows(bundle)
    rules = {
        "case_to_brain:NEGATIVE": {
            "missing_keys": ["NEG-1"],
        }
    }

    assert _nontraining_case_underfill_only(
        {"negative_control_cases.jsonl": [rows[0]]},
        rules,
    ) is True


def test_quarantined_liquidity_leader_remains_when_explicitly_present(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "quarantined-top30-leader.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-Q",
                    "ticker": "000001",
                    "high_return_pct": None,
                    "amount_rank": 23,
                    "turnover_rank": 500,
                    "price_label_quality": "quarantined",
                }
            ],
            "outcome_leader_census.jsonl": [
                {
                    "outcome_leader_id": "LEAD-Q",
                    "outcome_row_id": "OUT-Q",
                    "ticker": "000001",
                    "leader_reasons": ["AMOUNT_RANK_TOP_30"],
                    "price_label_quality": "quarantined",
                }
            ],
            "outcome_to_news_audit.jsonl": [{"outcome_leader_id": "LEAD-Q"}],
            "postmortem_summary.json": [
                {
                    "leader_policy": {
                        "amount_rank_top_group": 30,
                        "turnover_rank_top_group": 30,
                        "high_return_thresholds": [10, 15, 20],
                    },
                    "quarantine_census": [{"ticker": "000001"}],
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["leader_amount_top_n"] == 30
    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_absent_new_listing_liquidity_row_is_not_missing_leader(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "absent-new-listing-leader.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-NEW",
                    "ticker": "000001",
                    "high_return_pct": None,
                    "amount_rank": 5,
                    "turnover_rank": 7,
                    "new_listing_or_no_reference": True,
                    "price_label_quality": "quarantined",
                    "data_quality_status": "blocked_no_reference",
                }
            ],
            "outcome_leader_census.jsonl": [],
            "outcome_to_news_audit.jsonl": [],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["outcome_to_leader_census"]["missing_keys"] == []
    assert population["rules"]["outcome_to_leader_census"]["extra_keys"] == []


def test_material_queue_may_include_extra_reviewed_negative_rows(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "extra-negative-review.md",
        {
            "row_disposition.jsonl": [
                {
                    "row_id": "ROW-1",
                    "source_id": "SRC-1",
                    "disposition": "DIRECT_ISSUER_MATERIAL",
                },
                {
                    "row_id": "ROW-2",
                    "source_id": "SRC-2",
                    "disposition": "NON_MARKET_NEWS",
                },
            ],
            "material_review_queue.jsonl": [
                {"queue_id": "Q-1", "source_id": "SRC-1"},
                {"queue_id": "Q-2", "source_id": "SRC-2"},
            ],
            "material_review.jsonl": [
                {"material_review_id": "R-1", "queue_id": "Q-1", "source_id": "SRC-1"},
                {"material_review_id": "R-2", "queue_id": "Q-2", "source_id": "SRC-2"},
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    rule = population["rules"]["disposition_to_material_queue"]
    assert rule["mode"] == "EXPECTED_SUBSET"
    assert rule["missing_keys"] == []


def test_material_review_only_legacy_shape_closes_queue_population(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "material-review-only.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "news_row"}],
            "row_disposition.jsonl": [
                {
                    "row_id": "SRC-1",
                    "source_id": "SRC-1",
                    "disposition": "DIRECT_ISSUER_MATERIAL",
                }
            ],
            "material_review.jsonl": [
                {
                    "material_review_id": "MR-1",
                    "source_id": "SRC-1",
                    "review_decision": "DIRECT_ISSUER_MATERIAL",
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["disposition_to_material_queue"]["missing_keys"] == []
    assert population["rules"]["material_queue_to_review"]["missing_keys"] == []


def test_material_queue_rejects_source_absent_from_dispositions(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "orphan-review.md",
        {
            "row_disposition.jsonl": [{"row_id": "ROW-1", "source_id": "SRC-1", "disposition": "NON_MARKET_NEWS"}],
            "material_review_queue.jsonl": [{"queue_id": "Q-ORPHAN", "source_id": "SRC-ORPHAN"}],
            "material_review.jsonl": [
                {
                    "material_review_id": "R-ORPHAN",
                    "queue_id": "Q-ORPHAN",
                    "source_id": "SRC-ORPHAN",
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    rule = population["rules"]["material_queue_to_disposition"]
    assert rule["missing_keys"] == ["SRC-ORPHAN"]


def test_population_joins_observed_source_aliases_and_excludes_file_descriptor(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "source-aliases.md",
        {
            "source_ledger.jsonl": [
                {"source_id": "SRC-FILE", "source_type": "NEWS_CSV_FILE"},
                {
                    "source_id": "SRC-ROW",
                    "row_id": "ROW-A",
                    "source_type": "NEWS_CSV_ROW",
                },
            ],
            "row_disposition.jsonl": [
                {
                    "source_id": "SRC-ROW",
                    "source_row_id": "ROW-A",
                    "disposition": "DIRECT_ISSUER_MATERIAL",
                }
            ],
            "material_review_queue.jsonl": [{"source_row_id": "ROW-A"}],
            "material_review.jsonl": [{"source_id": "SRC-ROW", "review_id": "REV-A"}],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-A",
                    "candidate_id": "CAND-A",
                    "ticker": "TICKER-A",
                    "screening_decision": "WATCH",
                    "material_review_id": "REV-A",
                }
            ],
            "candidate_semantic_witness.jsonl": [{"source_screening_id": "SCR-A"}],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-A",
                    "candidate_id": "CAND-A",
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    for rule_name in (
        "source_to_disposition",
        "material_review_to_screening",
        "screening_to_candidate_witness",
        "rankable_to_ranking_audit",
        "ranking_candidate_consistency",
    ):
        assert population["rules"][rule_name]["missing_keys"] == []
        assert population["rules"][rule_name]["extra_keys"] == []
    assert population["rules"]["source_to_disposition"]["expected_count"] == 1


def test_news_csv_raw_descriptor_is_not_a_row_population_requirement() -> None:
    assert repair_quality_module._is_aggregate_news_source(
        {"source_id": "SRC-FILE", "source_type": "NEWS_CSV_RAW"}
    )


def test_legacy_news_row_with_integer_row_id_and_article_payload_is_news() -> None:
    row = {
        "source_id": "SRC-001000",
        "row_id": 1000,
        "title": "기사 제목",
        "body": "기사 본문",
        "published_at": "2025-05-17T17:28:27+09:00",
    }

    assert not repair_quality_module._is_aggregate_news_source(row)
    assert repair_quality_module._is_news_source_row(row)


def test_material_review_to_screening_uses_source_aliases(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "material-review-source-alias.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-ROW",
                    "row_id": "NEWS-1",
                    "source_type": "NEWS_CSV_ROW",
                }
            ],
            "row_disposition.jsonl": [
                {
                    "source_id": "SRC-ROW",
                    "disposition": "DIRECT_ISSUER_MATERIAL",
                }
            ],
            "material_review_queue.jsonl": [{"source_row_id": "NEWS-1"}],
            "material_review.jsonl": [
                {"review_id": "MRV-1", "source_id": "SRC-ROW"}
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "source_row_id": "NEWS-1",
                    "screening_decision": "WATCH_SECONDARY",
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    rule = population["rules"]["material_review_to_screening"]
    assert rule["missing_keys"] == []
    assert rule["extra_keys"] == []


def test_material_review_to_screening_joins_witness_source_alias(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "material-review-witness-source-alias.md",
        {
            "source_ledger.jsonl": [
                {"source_id": "SRC-ROW", "row_id": "NEWS-1", "source_type": "NEWS_CSV_ROW"}
            ],
            "row_disposition.jsonl": [
                {"source_id": "SRC-ROW", "disposition": "DIRECT_ISSUER_MATERIAL"}
            ],
            "material_review_queue.jsonl": [{"source_id": "SRC-ROW"}],
            "material_review.jsonl": [{"material_review_id": "MRV-1", "source_id": "SRC-ROW"}],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "source_observation_ids": ["OBS-1"],
                    "screening_decision": "INCLUDE",
                }
            ],
            "candidate_semantic_witness.jsonl": [
                {"source_screening_id": "SCR-1", "source_row_id": "NEWS-1"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    rule = population["rules"]["material_review_to_screening"]
    assert rule["missing_keys"] == []
    assert rule["extra_keys"] == []


def test_completed_review_embedded_in_queue_closes_screening_relation(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "queue-embedded-review.md",
        {
            "source_ledger.jsonl": [
                {"source_id": "SRC-1", "source_type": "NEWS_CSV_ROW"}
            ],
            "row_disposition.jsonl": [
                {
                    "source_id": "SRC-1",
                    "disposition": "DIRECT_ISSUER_MATERIAL",
                }
            ],
            "material_review_queue.jsonl": [
                {
                    "material_review_id": "MR-1",
                    "source_id": "SRC-1",
                    "observation_id": "OBS-1",
                    "material_reviewed": True,
                    "review_decision": "ACCEPT_DIRECT_ISSUER_OBSERVATION",
                }
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "source_observation_ids": ["OBS-1"],
                    "screening_decision": "WATCH_SECONDARY",
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    rule = population["rules"]["material_review_to_screening"]
    assert rule["missing_keys"] == []
    assert rule["extra_keys"] == []


def test_candidate_witness_population_requires_rankable_screenings_only(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "rankable-witness-population.md",
        {
            "candidate_screening.jsonl": [
                {"screening_id": "SCR-A", "screening_decision": "AUDIT_ONLY"},
                {"screening_id": "SCR-B", "screening_decision": "INCLUDE"},
            ],
            "candidate_semantic_witness.jsonl": [
                {"source_screening_id": "SCR-B"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    rule = population["rules"]["screening_to_candidate_witness"]
    assert rule["missing_keys"] == []
    assert rule["extra_keys"] == []


def test_candidate_witness_allows_multiple_observations_per_screening(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "multiple-witness-observations.md",
        {
            "candidate_screening.jsonl": [
                {"screening_id": "SCR-A", "screening_decision": "INCLUDE"},
            ],
            "candidate_semantic_witness.jsonl": [
                {
                    "candidate_semantic_witness_id": "CSW-1",
                    "screening_id": "SCR-A",
                    "source_row_id": "NEWS-1",
                },
                {
                    "candidate_semantic_witness_id": "CSW-2",
                    "screening_id": "SCR-A",
                    "source_row_id": "NEWS-2",
                },
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["duplicate_logical_key_count"] == 0
    assert population["rules"]["screening_to_candidate_witness"]["missing_keys"] == []


def test_news_csv_input_descriptor_is_not_a_row_population_requirement(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "news-csv-input-descriptor.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-INPUT-CSV",
                    "source_type": "NEWS_CSV_INPUT",
                    "input_row_ids": [],
                    "title": "news_20180226.csv",
                },
                {
                    "source_id": "SRC-ROW-1",
                    "source_type": "NEWS_CSV_ROW",
                    "row_id": "ROW-1",
                    "title": "Issuer event",
                },
            ],
            "row_disposition.jsonl": [
                {
                    "source_id": "SRC-ROW-1",
                    "row_id": "ROW-1",
                    "disposition": "NON_MARKET_NEWS",
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["source_to_disposition"]["missing_keys"] == []


def test_ranking_may_collapse_ticker_but_must_match_screening_candidate(
    tmp_path: Path,
) -> None:
    screenings = [
        {
            "screening_id": "SCR-A",
            "candidate_id": "CAND-A",
            "ticker": "TICKER-A",
            "screening_decision": "INCLUDE",
        },
        {
            "screening_id": "SCR-B",
            "candidate_id": "CAND-B",
            "ticker": "TICKER-A",
            "screening_decision": "WATCH_SECONDARY",
        },
    ]
    valid = _write_bundle(
        tmp_path / "collapsed-ranking.md",
        {
            "candidate_screening.jsonl": screenings,
            "candidate_ranking_audit.jsonl": [{"source_screening_id": "SCR-A", "candidate_id": "CAND-A"}],
        },
    )
    invalid = _write_bundle(
        tmp_path / "mismatched-ranking.md",
        {
            "candidate_screening.jsonl": screenings,
            "candidate_ranking_audit.jsonl": [{"source_screening_id": "SCR-A", "candidate_id": "CAND-FORGED"}],
        },
    )

    valid_population = _population_audit(artifact_rows(valid))
    invalid_population = _population_audit(artifact_rows(invalid))

    assert valid_population["rules"]["rankable_to_ranking_audit"]["missing_keys"] == []
    assert valid_population["rules"]["ranking_candidate_consistency"]["extra_keys"] == []
    assert invalid_population["rules"]["ranking_candidate_consistency"]["extra_keys"] == ["SCR-A:candidate_id"]


def test_ranking_audit_rejection_rows_do_not_create_rankable_underfill(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "ranking-rejection.md",
        {
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-REJECT",
                    "candidate_id": "CAND-REJECT",
                    "ticker": "000001",
                    "screening_decision": "REJECT_SEMANTIC_FALSE_POSITIVE",
                },
                {
                    "screening_id": "SCR-WATCH",
                    "candidate_id": "CAND-WATCH",
                    "ticker": "000002",
                    "screening_decision": "WATCH_SECONDARY",
                },
            ],
            "candidate_ranking_audit.jsonl": [
                {"source_screening_id": "SCR-REJECT", "candidate_id": "CAND-REJECT"},
                {"source_screening_id": "SCR-WATCH", "candidate_id": "CAND-WATCH"},
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["ranking_to_rankable_screening"]["missing_keys"] == []
    assert population["rules"]["ranking_to_screening"]["missing_keys"] == []


def test_ranking_audit_reads_screening_record_ids_array_alias(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "ranking-screening-records.md",
        {
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-A",
                    "candidate_id": "CAND-A",
                    "ticker": "000001",
                    "screening_decision": "INCLUDE",
                },
                {
                    "screening_id": "SCR-B",
                    "candidate_id": "CAND-A",
                    "ticker": "000001",
                    "screening_decision": "WATCH_SECONDARY",
                },
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "candidate_id": "CAND-A",
                    "ticker": "000001",
                    "screening_record_ids": ["SCR-A", "SCR-B"],
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["ranking_to_screening"]["missing_keys"] == []
    assert population["rules"]["ranking_to_rankable_screening"]["missing_keys"] == []
    assert population["rules"]["rankable_to_ranking_audit"]["missing_keys"] == []


def test_missing_outcome_leader_and_reverse_rows_fails_population_gate(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "outcome_ledger.jsonl": [
                {
                    "outcome_row_id": "OUT-1",
                    "ticker": "000001",
                    "high_return_pct": 12.0,
                    "amount_rank": 100,
                    "turnover_rank": 100,
                    "upper_limit_touched": False,
                }
            ],
            "outcome_leader_census.jsonl": [],
            "outcome_to_news_audit.jsonl": [],
        },
    )

    census = census_source(source)
    population = _population_audit(
        artifact_rows(source),
        present_artifact_names=set(census.artifact_counts),
    )

    rule = population["rules"]["outcome_to_leader_census"]
    assert rule["missing_keys"] == ["OUT-1"]
    assert population["rules"]["leader_to_reverse_audit"]["actual_count"] == 0
    assert population["population_underfill_count"] > 0


def test_empty_required_jsonl_block_counts_as_present(tmp_path: Path) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {"candidate_screening.jsonl": []},
    )

    census = census_source(source)
    population = _population_audit(
        artifact_rows(source),
        present_artifact_names=set(census.artifact_counts),
    )

    assert census.artifact_counts["candidate_screening.jsonl"] == 1
    assert "candidate_screening.jsonl" not in population["missing_current_contract_blocks"]
    assert "source_ledger.jsonl" in population["missing_current_contract_blocks"]


def test_case_population_accepts_explicit_payload_case_id_alias(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source.md",
        {
            "negative_control_cases.jsonl": [{"negative_control_case_id": "NEG-1"}],
            "beneficiary_discovery_cases.jsonl": [{"beneficiary_discovery_case_id": "BDC-1"}],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-NEG",
                    "record_type": "negative_control_case",
                    "payload": {"case_id": "NEG-1"},
                },
                {
                    "record_id": "BD-BDC",
                    "record_type": "beneficiary_discovery_case",
                    "payload": {"case_id": "BDC-1"},
                },
            ],
        },
    )

    census = census_source(source)
    population = _population_audit(
        artifact_rows(source),
        present_artifact_names=set(census.artifact_counts),
    )

    assert population["rules"]["case_to_brain:NEGATIVE"]["missing_keys"] == []
    assert population["rules"]["case_to_brain:BENEFICIARY"]["missing_keys"] == []


def test_pair_case_aliases_are_one_source_row_not_two_cases(tmp_path: Path) -> None:
    case = {
        "blind_pair_id": "PAIR-1",
        "case_id": "BPC-1",
        "candidate_ids": ["CAND-1", "CAND-2"],
        "matched_fact_ids": ["FACT-1"],
    }
    source_record = {
        "record_id": "BD-PAIR-1",
        "record_type": "blind_leader_preference_pair",
        "candidate_ids": ["CAND-1", "CAND-2"],
        "source_fact_ids": ["FACT-1"],
    }
    derivation = {
        "rule_id": "case_id_from_unique_evidence_join.v2",
        "source_artifact": "blind_leader_preference_pairs.jsonl",
        "source_case_id": "BPC-1",
        "source_case_payload_sha256": sha256_text(canonical_json(case)),
        "target_field": "pair_id",
        "join_field": "candidate_id",
        "join_value": "CAND-1",
        "join_values": {"candidate_id": ["CAND-1", "CAND-2"]},
        "record_type_relation": (
            "blind_leader_preference_pairs.jsonl:None->blind_leader_preference_pair"
        ),
    }
    source = _write_bundle(
        tmp_path / "pair-alias-source.md",
        {
            "blind_leader_preference_pairs.jsonl": [case],
            "brain_delta.jsonl": [source_record],
        },
    )
    repaired = _write_bundle(
        tmp_path / "pair-alias-repaired.md",
        {
            "blind_leader_preference_pairs.jsonl": [case],
            "brain_delta.jsonl": [
                {
                    **source_record,
                    "pair_id": "BPC-1",
                    "repair_population_derivations": [derivation],
                }
            ],
        },
    )

    audit = _derived_case_population_audit(
        artifact_rows(source), artifact_rows(repaired)
    )

    assert audit["derived_case_link_count"] == 1
    assert audit["derived_case_link_failure_count"] == 0


def test_population_accepts_paircase_identity_alias(tmp_path: Path) -> None:
    source = _write_bundle(
        tmp_path / "paircase-alias.md",
        {
            "blind_leader_preference_pairs.jsonl": [{"pair_id": "PAIR-001"}],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-PAIR-001",
                    "record_type": "blind_leader_preference_pair",
                    "payload": {"pair_id": "PAIRCASE-001"},
                }
            ],
        },
    )

    census = census_source(source)
    population = _population_audit(
        artifact_rows(source),
        present_artifact_names=set(census.artifact_counts),
    )

    assert population["rules"]["case_to_brain:PAIR"]["missing_keys"] == []


def test_case_population_accepts_classification_bound_record_type_aliases(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "case-type-aliases.md",
        {
            "beneficiary_discovery_cases.jsonl": [
                {
                    "case_id": "BEN-1",
                    "classification": "SCREENED_OUT_BUT_WINNER",
                    "outcome_leader_id": "LEAD-1",
                    "ticker": "000001",
                }
            ],
            "ranking_error_cases.jsonl": [
                {
                    "case_id": "RERR-1",
                    "classification": "SCREENED_OUT_BUT_WINNER",
                    "outcome_leader_id": "LEAD-1",
                    "ticker": "000001",
                }
            ],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "candidate_ranking_error_case",
                    "beneficiary_case_id": "BEN-1",
                    "ranking_error_case_id": "RERR-1",
                    "outcome_leader_id": "LEAD-1",
                    "ticker": "000001",
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(source))

    assert population["rules"]["case_to_brain:BENEFICIARY"]["missing_keys"] == []
    assert population["rules"]["case_to_brain:RANKING"]["missing_keys"] == []


def test_case_population_accepts_beneficiary_discovery_class_alias(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "beneficiary-discovery-class.md",
        {
            "beneficiary_discovery_cases.jsonl": [
                {
                    "beneficiary_discovery_case_id": "BEN-1",
                    "discovery_class": "SEALED_SOURCE_PRESENT_BUT_NOT_FINAL",
                    "audit_id": "OUTNEWS-1",
                    "ticker": "000001",
                    "source_fact_ids": ["FACT-1"],
                    "source_inference_ids": ["INF-1"],
                }
            ],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "supervised_issuer_day_case",
                    "ticker": "000001",
                    "outcome_audit_ids": ["OUTNEWS-1"],
                    "source_fact_ids": ["FACT-1"],
                    "source_inference_ids": ["INF-1"],
                }
            ],
        },
    )

    census = census_source(source)
    population = _population_audit(
        artifact_rows(source),
        present_artifact_names=set(census.artifact_counts),
    )

    assert population["rules"]["case_to_brain:BENEFICIARY"]["missing_keys"] == []


def test_case_population_accepts_context_and_negative_alias_records(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "context-negative-aliases.md",
        {
            "context_market_state_or_fact_cases.jsonl": [
                {"context_case_id": "CTX-1", "context_type": "MARKET_STATE"}
            ],
            "negative_control_cases.jsonl": [
                {
                    "negative_control_id": "NEG-1",
                    "selection_reason": "EXPLICIT_NEGATIVE_CONTROL_SOURCE",
                    "ticker": "000001",
                },
                {
                    "negative_control_id": "NEG-2",
                    "selection_reason": "RANKABLE_NOT_FINAL_REPRESENTATIVE",
                    "ticker": "000002",
                },
            ],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-CTX",
                    "record_type": "mechanism_memory",
                    "payload": {"context_case_id": "CTX-1"},
                },
                {
                    "record_id": "BD-NEG",
                    "record_type": "counterexample",
                    "payload": {
                        "negative_control_id": "NEG-1",
                        "ticker": "000001",
                    },
                },
                {
                    "record_id": "BD-NEG-2",
                    "record_type": "counterexample",
                    "payload": {
                        "negative_control_id": "NEG-2",
                        "ticker": "000002",
                    },
                },
            ],
        },
    )

    census = census_source(source)
    population = _population_audit(
        artifact_rows(source),
        present_artifact_names=set(census.artifact_counts),
    )

    assert population["rules"]["case_to_brain:CONTEXT"]["missing_keys"] == []
    assert population["rules"]["case_to_brain:NEGATIVE"]["missing_keys"] == []


def test_noneligible_theme_case_may_join_context_record_by_source_fact(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "theme-context-alias.md",
        {
            "theme_formation_cases.jsonl": [
                {
                    "theme_case_id": "THEME-1",
                    "source_fact_ids": ["FACT-1"],
                    "source_row_ids": ["SRC-1"],
                    "training_eligible": False,
                    "training_exclusion_reason": "issuer_beneficiary_not_explicitly_bound",
                }
            ],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "context_market_state_or_fact_case",
                    "source_fact_ids": ["FACT-1"],
                    "provenance_source_ids": ["SRC-1"],
                    "training_eligible": False,
                    "sample_weight": 0.0,
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["case_to_brain:THEME"]["missing_keys"] == []


def test_theme_derivation_prefers_supervised_row_over_context_alias(
    tmp_path: Path,
) -> None:
    case = {
        "theme_case_id": "THEME-1",
        "theme_id": "THEME-OIL",
        "source_fact_ids": ["FACT-1"],
    }
    source_record = {
        "record_id": "BD-1",
        "record_type": "supervised_theme_formation_case",
        "theme_id": "THEME-OIL",
        "source_fact_ids": ["FACT-1"],
    }
    derivation = {
        "rule_id": "case_id_from_unique_evidence_join.v2",
        "source_artifact": "theme_formation_cases.jsonl",
        "source_case_id": "THEME-1",
        "source_case_payload_sha256": sha256_text(canonical_json(case)),
        "target_field": "theme_case_id",
        "join_field": "theme_id",
        "join_value": "THEME-OIL",
        "join_values": {"theme_id": ["THEME-OIL"]},
        "record_type_relation": (
            "theme_formation_cases.jsonl:None->supervised_theme_formation_case"
        ),
    }
    source = _write_bundle(
        tmp_path / "theme-derivation-source.md",
        {
            "theme_formation_cases.jsonl": [case],
            "brain_delta.jsonl": [
                source_record,
                {
                    "record_id": "BD-CONTEXT",
                    "record_type": "context_market_state_or_fact_case",
                    "theme_id": "THEME-OIL",
                    "source_fact_ids": ["FACT-1"],
                },
            ],
        },
    )
    repaired = _write_bundle(
        tmp_path / "theme-derivation-repaired.md",
        {
            "theme_formation_cases.jsonl": [case],
            "brain_delta.jsonl": [
                {**source_record, "theme_case_id": "THEME-1", "repair_population_derivations": [derivation]},
                {
                    "record_id": "BD-CONTEXT",
                    "record_type": "context_market_state_or_fact_case",
                    "theme_id": "THEME-OIL",
                    "source_fact_ids": ["FACT-1"],
                },
            ],
        },
    )

    audit = _derived_case_population_audit(
        artifact_rows(source), artifact_rows(repaired)
    )

    assert audit["derived_case_link_count"] == 1
    assert audit["derived_case_link_failure_count"] == 0


def test_theme_derivation_accepts_legacy_theme_row_with_context_alias(
    tmp_path: Path,
) -> None:
    case = {
        "theme_case_id": "THEME-LEGACY-1",
        "theme_id": "THEME-LEGACY",
        "source_fact_ids": ["FACT-LEGACY-1"],
    }
    legacy_record = {
        "record_id": "BD-LEGACY-THEME",
        "record_type": "theme_formation_case",
        "theme_id": "THEME-LEGACY",
        "source_fact_ids": ["FACT-LEGACY-1"],
    }
    derivation = {
        "rule_id": "case_id_from_unique_evidence_join.v2",
        "source_artifact": "theme_formation_cases.jsonl",
        "source_case_id": "THEME-LEGACY-1",
        "source_case_payload_sha256": sha256_text(canonical_json(case)),
        "target_field": "theme_case_id",
        "join_field": "theme_id",
        "join_value": "THEME-LEGACY",
        "join_values": {"theme_id": ["THEME-LEGACY"]},
        "record_type_relation": (
            "theme_formation_cases.jsonl:None->theme_formation_case"
        ),
    }
    source = _write_bundle(
        tmp_path / "theme-legacy-source.md",
        {
            "theme_formation_cases.jsonl": [case],
            "brain_delta.jsonl": [
                legacy_record,
                {
                    "record_id": "BD-LEGACY-CONTEXT",
                    "record_type": "context_market_state_or_fact_case",
                    "theme_id": "THEME-LEGACY",
                    "source_fact_ids": ["FACT-LEGACY-1"],
                },
            ],
        },
    )
    repaired = _write_bundle(
        tmp_path / "theme-legacy-repaired.md",
        {
            "theme_formation_cases.jsonl": [case],
            "brain_delta.jsonl": [
                {**legacy_record, "theme_case_id": "THEME-LEGACY-1", "repair_population_derivations": [derivation]},
                {
                    "record_id": "BD-LEGACY-CONTEXT",
                    "record_type": "context_market_state_or_fact_case",
                    "theme_id": "THEME-LEGACY",
                    "source_fact_ids": ["FACT-LEGACY-1"],
                },
            ],
        },
    )

    audit = _derived_case_population_audit(
        artifact_rows(source), artifact_rows(repaired)
    )

    assert audit["derived_case_link_count"] == 1
    assert audit["derived_case_link_failure_count"] == 0


def test_semantic_false_positive_newsless_case_may_join_entity_error(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "newsless-entity-error-alias.md",
        {
            "newsless_or_unexplained_cases.jsonl": [
                {
                    "newsless_case_id": "NLS-1",
                    "classification": "SEMANTIC_FALSE_POSITIVE",
                    "matched_fact_ids": ["FACT-1"],
                    "matched_source_row_ids": ["SRC-1"],
                    "training_eligible": False,
                }
            ],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "entity_resolution_error_case",
                    "source_fact_ids": ["FACT-1"],
                    "provenance_source_ids": ["SRC-1"],
                    "training_eligible": False,
                    "sample_weight": 0.0,
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["case_to_brain:NEWSLESS"]["missing_keys"] == []


def test_newsless_case_may_join_no_cutoff_candidate_generation_error(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "newsless-candidate-generation-alias.md",
        {
            "newsless_or_unexplained_cases.jsonl": [
                {
                    "newsless_case_id": "NLS-1",
                    "outcome_leader_id": "LEAD-1",
                    "ticker": "000001",
                    "sealed_source_match": "NONE",
                    "matched_fact_ids": [],
                    "training_eligible": False,
                    "explanation_status": "UNEXPLAINED_WITHIN_INPUT_WINDOW",
                }
            ],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "candidate_generation_error_case",
                    "outcome_leader_id": "LEAD-1",
                    "ticker": "000001",
                    "training_eligible": False,
                    "sample_weight": 0.0,
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["case_to_brain:NEWSLESS"]["missing_keys"] == []


@pytest.mark.parametrize(("ticker", "expected_missing"), [(None, []), ("000001", ["NEG-1"])])
def test_context_only_negative_case_may_map_to_explicit_counterexample(
    tmp_path: Path,
    ticker: str | None,
    expected_missing: list[str],
) -> None:
    case = {
        "negative_control_id": "NEG-1",
        "screening_decision": "AUDIT_ONLY",
        "semantic_risk_flags": ["NO_LOCAL_ISSUER_OWNER"],
        "ticker": ticker,
    }
    bundle = _write_bundle(
        tmp_path / f"negative-counterexample-{ticker or 'none'}.md",
        {
            "negative_control_cases.jsonl": [case],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "counterexample",
                    "ticker": ticker,
                    "payload": {
                        "negative_control_id": "NEG-1",
                        "canonical_type_repair": {
                            "from": "negative_control_case",
                            "to": "counterexample",
                            "reason": "NO_TRUTHFUL_LISTED_ISSUER_IDENTITY",
                        },
                    },
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["case_to_brain:NEGATIVE"]["missing_keys"] == expected_missing


def test_repair_receipt_occurrence_allows_only_record_id_namespacing(
    tmp_path: Path,
) -> None:
    source_row = {
        "schema_version": "nslab.brain_delta_repair_receipt.v1",
        "added_record_ids": ["BD-1"],
        "reclassified_record_ids": ["BD-2"],
        "output_record_count": 2,
    }
    source = _write_bundle(
        tmp_path / "source-receipt.md",
        {"brain_delta_repair_receipt.json": [source_row]},
    )
    repaired = _write_bundle(
        tmp_path / "repaired-receipt.md",
        {
            "brain_delta_repair_receipt.json": [
                {
                    **source_row,
                    "added_record_ids": ["EP-1__BD-1"],
                    "reclassified_record_ids": ["EP-1__BD-2"],
                }
            ]
        },
    )
    tampered = _write_bundle(
        tmp_path / "tampered-receipt.md",
        {
            "brain_delta_repair_receipt.json": [
                {
                    **source_row,
                    "added_record_ids": ["EP-1__BD-FORGED"],
                    "reclassified_record_ids": ["EP-1__BD-2"],
                }
            ]
        },
    )

    valid_audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(repaired),
    )
    invalid_audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(tampered),
    )

    assert valid_audit["artifact_occurrence_changed_count"] == 0
    assert invalid_audit["artifact_occurrence_changed_names"] == ["brain_delta_repair_receipt.json"]


def test_id_registry_occurrence_allows_only_record_id_namespacing(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source-id-registry.md",
        {
            "id_registry.json": [
                {
                    "episode_id": "EP-1",
                    "record_count": 1,
                    "records": [
                        {
                            "artifact": "record_provenance_closure_audit.jsonl",
                            "id": "PCA-1",
                            "record_id": "BD-1",
                            "status": "ACTIVE",
                        }
                    ],
                }
            ],
            "id_registry.jsonl": [
                {
                    "artifact": "record_provenance_closure_audit.jsonl",
                    "id": "PCA-1",
                    "record_id": "BD-1",
                    "status": "ACTIVE",
                }
            ],
        },
    )
    repaired = _write_bundle(
        tmp_path / "repaired-id-registry.md",
        {
            "id_registry.json": [
                {
                    "episode_id": "EP-1",
                    "record_count": 1,
                    "records": [
                        {
                            "artifact": "record_provenance_closure_audit.jsonl",
                            "id": "PCA-1",
                            "record_id": "EP-1__BD-1",
                            "status": "ACTIVE",
                        }
                    ],
                }
            ],
            "id_registry.jsonl": [
                {
                    "artifact": "record_provenance_closure_audit.jsonl",
                    "id": "PCA-1",
                    "record_id": "EP-1__BD-1",
                    "status": "ACTIVE",
                }
            ],
        },
    )
    tampered = _write_bundle(
        tmp_path / "tampered-id-registry.md",
        {
            "id_registry.json": [
                {
                    "episode_id": "EP-1",
                    "record_count": 1,
                    "records": [
                        {
                            "artifact": "record_provenance_closure_audit.jsonl",
                            "id": "PCA-1",
                            "record_id": "EP-1__BD-1",
                            "status": "DELETED",
                        }
                    ],
                }
            ],
            "id_registry.jsonl": [
                {
                    "artifact": "record_provenance_closure_audit.jsonl",
                    "id": "PCA-1",
                    "record_id": "EP-1__BD-1",
                    "status": "DELETED",
                }
            ],
        },
    )

    valid_audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(repaired),
    )
    invalid_audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(tampered),
    )

    assert valid_audit["artifact_occurrence_changed_count"] == 0
    assert invalid_audit["artifact_occurrence_changed_names"] == [
        "id_registry.json",
        "id_registry.jsonl",
    ]


def test_id_registry_summary_allows_brain_delta_id_namespacing(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source-id-registry-summary.md",
        {
            "id_registry.json": [
                {
                    "episode_id": "EP-1",
                    "brain_delta_record_count": 2,
                    "brain_delta_record_ids": ["BD-1", "BD-2"],
                    "counts_by_object_type": {"brain_delta": 2},
                    "id_count": 2,
                    "schema_version": "v1",
                }
            ]
        },
    )
    repaired = _write_bundle(
        tmp_path / "repaired-id-registry-summary.md",
        {
            "id_registry.json": [
                {
                    "episode_id": "EP-1",
                    "brain_delta_record_count": 2,
                    "brain_delta_record_ids": ["EP-1__BD-1", "EP-1__BD-2"],
                    "counts_by_object_type": {"brain_delta": 2},
                    "id_count": 2,
                    "schema_version": "v1",
                }
            ]
        },
    )

    audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(repaired),
    )

    assert audit["artifact_occurrence_changed_count"] == 0


def test_id_registry_id_sets_allows_brain_record_id_namespacing(
    tmp_path: Path,
) -> None:
    source_row = {
        "episode_id": "EP-1",
        "id_sets": {
            "brain_record_ids": ["BD-1", "BD-2"],
            "source_ids": ["SRC-1"],
        },
        "counts": {"brain_delta": 2},
        "generated_at": "2020-01-01T00:00:00+09:00",
    }
    source = _write_bundle(tmp_path / "source-id-sets.md", {"id_registry.json": [source_row]})
    repaired = _write_bundle(
        tmp_path / "repaired-id-sets.md",
        {
            "id_registry.json": [
                {
                    **source_row,
                    "id_sets": {
                        "brain_record_ids": ["EP-1__BD-1", "EP-1__BD-2"],
                        "source_ids": ["SRC-1"],
                    },
                }
            ]
        },
    )
    tampered = _write_bundle(
        tmp_path / "tampered-id-sets.md",
        {
            "id_registry.json": [
                {
                    **source_row,
                    "id_sets": {
                        "brain_record_ids": ["EP-1__BD-1", "EP-1__BD-FORGED"],
                        "source_ids": ["SRC-1"],
                    },
                }
            ]
        },
    )

    valid_audit = _artifact_occurrence_lineage_audit(
        census_source(source), census_source(repaired)
    )
    invalid_audit = _artifact_occurrence_lineage_audit(
        census_source(source), census_source(tampered)
    )

    assert valid_audit["artifact_occurrence_changed_count"] == 0
    assert invalid_audit["artifact_occurrence_changed_names"] == ["id_registry.json"]


def test_repair_log_allows_only_brain_id_namespacing(
    tmp_path: Path,
) -> None:
    source_row = {
        "repair_id": "REPAIR-1",
        "reclassified_record_ids": ["BD-1"],
        "new_brain_record_ids": ["BD-2"],
        "removed_negative_control_ids": ["NEG-1"],
        "status": "passed",
    }
    source = _write_bundle(tmp_path / "source-repair-log.md", {"repair_log.json": [source_row]})
    repaired = _write_bundle(
        tmp_path / "repaired-repair-log.md",
        {
            "repair_log.json": [
                {
                    **source_row,
                    "reclassified_record_ids": ["EP-1__BD-1"],
                    "new_brain_record_ids": ["EP-1__BD-2"],
                }
            ]
        },
    )
    tampered = _write_bundle(
        tmp_path / "tampered-repair-log.md",
        {
            "repair_log.json": [
                {
                    **source_row,
                    "reclassified_record_ids": ["EP-1__BD-FORGED"],
                }
            ]
        },
    )

    valid_audit = _artifact_occurrence_lineage_audit(
        census_source(source), census_source(repaired)
    )
    invalid_audit = _artifact_occurrence_lineage_audit(
        census_source(source), census_source(tampered)
    )

    assert valid_audit["artifact_occurrence_changed_count"] == 0
    assert invalid_audit["artifact_occurrence_changed_names"] == ["repair_log.json"]


def test_selection_artifacts_allow_only_record_id_namespacing(
    tmp_path: Path,
) -> None:
    names = (
        "context_case_source_selection.jsonl",
        "selected_negative_control_sources.jsonl",
    )
    source = _write_bundle(
        tmp_path / "source-selection.md",
        {
            name: [
                {
                    "selection_id": f"SEL-{index}",
                    "record_id": f"BD-{index}",
                    "screening_id": f"SCR-{index}",
                    "source_phase": "POSTMORTEM",
                }
            ]
            for index, name in enumerate(names, start=1)
        },
    )
    repaired = _write_bundle(
        tmp_path / "repaired-selection.md",
        {
            name: [
                {
                    "selection_id": f"SEL-{index}",
                    "record_id": f"EP-1__BD-{index}",
                    "screening_id": f"SCR-{index}",
                    "source_phase": "POSTMORTEM",
                }
            ]
            for index, name in enumerate(names, start=1)
        },
    )

    audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(repaired),
    )

    assert audit["artifact_occurrence_changed_count"] == 0


def test_rankable_mapping_allows_only_brain_record_id_namespacing(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source-rankable.md",
        {
            "rankable_candidate_brain_mapping.jsonl": [
                {
                    "brain_record_id": "BD-1",
                    "mapping_type": "NEGATIVE_CONTROL",
                    "source_population_id": "NEG-1",
                }
            ]
        },
    )
    repaired = _write_bundle(
        tmp_path / "repaired-rankable.md",
        {
            "rankable_candidate_brain_mapping.jsonl": [
                {
                    "brain_record_id": "EP-1__BD-1",
                    "mapping_type": "NEGATIVE_CONTROL",
                    "source_population_id": "NEG-1",
                }
            ]
        },
    )

    audit = _artifact_occurrence_lineage_audit(
        census_source(source),
        census_source(repaired),
    )

    assert audit["artifact_occurrence_changed_count"] == 0


def test_population_reference_artifacts_allow_brain_id_namespacing(
    tmp_path: Path,
) -> None:
    source = _write_bundle(
        tmp_path / "source-population-refs.md",
        {
            "negative_control_selection.jsonl": [
                {"brain_record_id": "BD-1", "selection_id": "NCS-1"}
            ],
            "postmortem_supervised_population.jsonl": [
                {"linked_brain_record_ids": ["BD-1", "BD-2"], "population_id": "POP-1"}
            ],
        },
    )
    repaired = _write_bundle(
        tmp_path / "repaired-population-refs.md",
        {
            "negative_control_selection.jsonl": [
                {"brain_record_id": "EP-1__BD-1", "selection_id": "NCS-1"}
            ],
            "postmortem_supervised_population.jsonl": [
                {
                    "linked_brain_record_ids": ["EP-1__BD-1", "EP-1__BD-2"],
                    "population_id": "POP-1",
                }
            ],
        },
    )

    audit = _artifact_occurrence_lineage_audit(census_source(source), census_source(repaired))

    assert audit["artifact_occurrence_changed_count"] == 0


def test_rewritten_postmortem_artifacts_allow_record_id_namespacing(
    tmp_path: Path,
) -> None:
    names = (
        "blind_leader_pairs.jsonl",
        "postmortem_error_cases.jsonl",
        "retrospective_theme_member_edges.jsonl",
    )
    source_blocks = {
        name: [{"record_id": f"BD-{index}", "value": name}]
        for index, name in enumerate(names, start=1)
    }
    repaired_blocks = {
        name: [{"record_id": f"EP-1__BD-{index}", "value": name}]
        for index, name in enumerate(names, start=1)
    }
    source = _write_bundle(tmp_path / "postmortem-source.md", source_blocks)
    repaired = _write_bundle(tmp_path / "postmortem-repaired.md", repaired_blocks)

    audit = _artifact_occurrence_lineage_audit(census_source(source), census_source(repaired))

    assert audit["artifact_occurrence_changed_count"] == 0


def test_brain_linkage_artifacts_allow_record_id_namespacing(
    tmp_path: Path,
) -> None:
    names = (
        "candidate_ranking_brain_linkage.jsonl",
        "candidate_screening_brain_linkage.jsonl",
        "final_watchlist_brain_linkage.jsonl",
        "outcome_audit_brain_linkage.jsonl",
    )
    source_blocks = {
        name: [{"brain_record_ids": ["BD-1", "BD-2"], "value": name}]
        for name in names
    }
    repaired_blocks = {
        name: [
            {
                "brain_record_ids": ["EP-1__BD-1", "EP-1__BD-2"],
                "value": name,
            }
        ]
        for name in names
    }
    source = _write_bundle(tmp_path / "linkage-source.md", source_blocks)
    repaired = _write_bundle(tmp_path / "linkage-repaired.md", repaired_blocks)

    audit = _artifact_occurrence_lineage_audit(
        census_source(source), census_source(repaired)
    )

    assert audit["artifact_occurrence_changed_count"] == 0


def test_final_relations_resolve_unique_rank_ticker_fact_legacy_join(
    tmp_path: Path,
) -> None:
    base = {
        "blind_prediction.json": [
            {
                "final_watchlist": [
                    {
                        "candidate_id": "CAND-1",
                        "rank": 1,
                        "ticker": "000001",
                        "source_fact_ids": ["FACT-1"],
                        "source_screening_id": "SCR-1",
                    }
                ]
            }
        ],
        "final_evidence_witness.jsonl": [{"rank": 1, "ticker": "000001", "fact_id": "FACT-1"}],
        "final_semantic_audit.jsonl": [
            {
                "rank": 1,
                "ticker": "000001",
                "fact_id": "FACT-1",
                "source_screening_id": "SCR-1",
            }
        ],
    }
    valid = _write_bundle(tmp_path / "valid-final-join.md", base)
    invalid = _write_bundle(
        tmp_path / "invalid-final-join.md",
        {
            **base,
            "final_semantic_audit.jsonl": [{"rank": 1, "ticker": "000001", "fact_id": "FACT-TAMPERED"}],
        },
    )

    valid_population = _population_audit(artifact_rows(valid))
    invalid_population = _population_audit(artifact_rows(invalid))

    assert valid_population["rules"]["final_to_evidence_witness"]["missing_keys"] == []
    assert valid_population["rules"]["final_to_semantic_audit"]["missing_keys"] == []
    assert invalid_population["rules"]["final_to_semantic_audit"]["missing_keys"] == ["CAND-1"]


def test_final_semantic_audit_accepts_final_rank_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "final-rank-alias.md",
        {
            "blind_prediction.json": [
                {
                    "final_watchlist": [
                        {
                            "candidate_id": "CAND-1",
                            "rank": 1,
                            "ticker": "000001",
                            "source_fact_ids": ["FACT-1"],
                            "source_screening_id": "SCR-1",
                        }
                    ]
                }
            ],
            "final_semantic_audit.jsonl": [
                {
                    "final_rank": 1,
                    "ticker": "000001",
                    "fact_id": "FACT-1",
                    "source_screening_id": "SCR-1",
                }
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["final_to_semantic_audit"]["missing_keys"] == []


def test_final_semantic_audit_joins_final_witness_id_alias(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "final-witness-id-alias.md",
        {
            "blind_prediction.json": [
                {
                    "final_watchlist": [
                        {
                            "candidate_id": "CAND-1",
                            "rank": 1,
                            "ticker": "000001",
                        }
                    ]
                }
            ],
            "final_evidence_witness.jsonl": [
                {"witness_id": "FWIT-1", "candidate_id": "CAND-1"}
            ],
            "final_semantic_audit.jsonl": [{"witness_id": "FWIT-1", "semantic_passed": True}],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["final_to_semantic_audit"]["missing_keys"] == []


def test_final_relations_join_candidate_screening_alias(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "final-screening-alias.md",
        {
            "blind_prediction.json": [
                {
                    "final_watchlist": [
                        {
                            "candidate_id": "CAND-1",
                            "rank": 1,
                            "ticker": "000001",
                            "source_screening_id": "SCR-1",
                        }
                    ]
                }
            ],
            "final_evidence_witness.jsonl": [
                {
                    "final_evidence_witness_id": "FEW-1",
                    "candidate_screening_id": "SCR-1",
                    "rank": 1,
                    "ticker": "000001",
                }
            ],
            "final_semantic_audit.jsonl": [
                {"final_evidence_witness_id": "FEW-1", "semantic_audit_status": "PASS"}
            ],
        },
    )

    population = _population_audit(artifact_rows(bundle))

    assert population["rules"]["final_to_evidence_witness"]["missing_keys"] == []
    assert population["rules"]["final_to_semantic_audit"]["missing_keys"] == []


def test_derived_case_population_link_requires_source_hash_and_join(
    tmp_path: Path,
) -> None:
    case = {
        "candidate_generation_error_case_id": "CGE-1",
        "outcome_leader_id": "LEAD-1",
        "ticker": "000001",
        "trade_date": "2021-03-24",
    }
    source_record = {
        "record_id": "BD-1",
        "record_type": "candidate_generation_error_case",
        "ticker": "000001",
        "trade_date": "2021-03-24",
        "payload": {"outcome_leader_id": "LEAD-1"},
    }
    derivation = {
        "rule_id": "case_id_from_unique_outcome_leader.v1",
        "source_artifact": "candidate_generation_error_cases.jsonl",
        "source_case_id": "CGE-1",
        "source_case_payload_sha256": sha256_text(canonical_json(case)),
        "target_field": "candidate_generation_error_case_id",
        "join_field": "outcome_leader_id",
        "join_value": "LEAD-1",
    }
    source = _write_bundle(
        tmp_path / "case-source.md",
        {
            "candidate_generation_error_cases.jsonl": [case],
            "brain_delta.jsonl": [source_record],
        },
    )
    repaired_record = {
        **source_record,
        "candidate_generation_error_case_id": "CGE-1",
        "repair_population_derivations": [derivation],
    }
    repaired = _write_bundle(
        tmp_path / "case-repaired.md",
        {
            "candidate_generation_error_cases.jsonl": [case],
            "brain_delta.jsonl": [repaired_record],
        },
    )

    valid = _derived_case_population_audit(artifact_rows(source), artifact_rows(repaired))
    derivation["source_case_payload_sha256"] = "0" * 64
    forged = _write_bundle(
        tmp_path / "case-forged.md",
        {
            "candidate_generation_error_cases.jsonl": [case],
            "brain_delta.jsonl": [repaired_record],
        },
    )
    invalid = _derived_case_population_audit(artifact_rows(source), artifact_rows(forged))

    assert valid["derived_case_link_count"] == 1
    assert valid["derived_case_link_failure_count"] == 0
    assert invalid["derived_case_link_failure_count"] == 1


def test_case_population_source_gap_ignores_other_ticker_fact_occurrence(
    tmp_path: Path,
) -> None:
    case = {
        "issuer_day_case_id": "DAY-1",
        "ticker": "000001",
        "trade_date": "2025-05-08",
        "combined_fact_ids": ["FACT-1", "FACT-2"],
    }
    source = _write_bundle(
        tmp_path / "split-gap.md",
        {
            "issuer_day_cases.jsonl": [case],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "supervised_direct_event_case",
                    "ticker": "000001",
                    "trade_date": "2025-05-08",
                    "source_fact_ids": ["FACT-1"],
                },
                {
                    "record_id": "BD-2",
                    "record_type": "context_market_state_or_fact_case",
                    "ticker": "000002",
                    "trade_date": "2025-05-08",
                    "source_fact_ids": ["FACT-2"],
                },
            ],
        },
    )
    rows = artifact_rows(source)
    derivation = {
        "source_artifact": "issuer_day_cases.jsonl",
        "source_case_id": "DAY-1",
    }

    assert _case_population_source_gap(
        derivation,
        source_by_name={
            "issuer_day_cases.jsonl": [
                row for row in rows if row.canonical_name == "issuer_day_cases.jsonl"
            ],
            "brain_delta.jsonl": [
                row for row in rows if row.canonical_name == "brain_delta.jsonl"
            ],
        },
    ) is True


def test_ranking_derivation_prefers_specific_error_row_over_mirror(
    tmp_path: Path,
) -> None:
    case = {
        "ranking_error_case_id": "RERR-1",
        "outcome_audit_id": "AUDIT-1",
        "source_fact_ids": ["FACT-1"],
        "ticker": "000001",
        "trade_date": "2021-03-24",
    }
    issuer_mirror = {
        "record_id": "BD-ISSUER",
        "record_type": "supervised_issuer_day_case",
        "outcome_audit_ids": ["AUDIT-1"],
        "source_fact_ids": ["FACT-1"],
        "ticker": "000001",
        "trade_date": "2021-03-24",
    }
    ranking_record = {
        "record_id": "BD-RANKING",
        "record_type": "candidate_ranking_error_case",
        "outcome_audit_ids": ["AUDIT-1"],
        "source_fact_ids": ["FACT-1"],
        "ticker": "000001",
        "trade_date": "2021-03-24",
    }
    derivation = {
        "rule_id": "case_id_from_unique_evidence_join.v2",
        "source_artifact": "ranking_error_cases.jsonl",
        "source_case_id": "RERR-1",
        "source_case_payload_sha256": sha256_text(canonical_json(case)),
        "target_field": "ranking_error_case_id",
        "join_field": "outcome_audit_id",
        "join_value": "AUDIT-1",
        "join_values": {"outcome_audit_id": ["AUDIT-1"], "fact_id": ["FACT-1"]},
        "record_type_relation": (
            "ranking_error_cases.jsonl:None->candidate_ranking_error_case"
        ),
    }
    source = _write_bundle(
        tmp_path / "ranking-alias-source.md",
        {
            "ranking_error_cases.jsonl": [case],
            "brain_delta.jsonl": [issuer_mirror, ranking_record],
        },
    )
    repaired = _write_bundle(
        tmp_path / "ranking-alias-repaired.md",
        {
            "ranking_error_cases.jsonl": [case],
            "brain_delta.jsonl": [
                issuer_mirror,
                {**ranking_record, "ranking_error_case_id": "RERR-1", "repair_population_derivations": [derivation]},
            ],
        },
    )

    audit = _derived_case_population_audit(artifact_rows(source), artifact_rows(repaired))

    assert audit["derived_case_link_count"] == 1
    assert audit["derived_case_link_failure_count"] == 0


@pytest.mark.parametrize(
    ("block_name", "case", "source_record", "target_field", "case_id"),
    [
        (
            "newsless_or_unexplained_cases.jsonl",
            {
                "newsless_case_id": "NEWSLESS-1",
                "outcome_audit_id": "AUDIT-1",
                "ticker": "000001",
                "trade_date": "2021-03-24",
            },
            {
                "record_id": "BD-1",
                "record_type": "newsless_or_unexplained_case",
                "ticker": "000001",
                "trade_date": "2021-03-24",
                "outcome_audit_ids": ["AUDIT-1"],
            },
            "newsless_case_id",
            "NEWSLESS-1",
        ),
        (
            "beneficiary_discovery_cases.jsonl",
            {
                "beneficiary_case_id": "BEN-1",
                "classification": "RANKING_MISS",
                "outcome_audit_id": "AUDIT-1",
                "ticker": "000001",
                "trade_date": "2021-03-24",
                "matched_fact_ids": ["FACT-1"],
            },
            {
                "record_id": "BD-1",
                "record_type": "candidate_ranking_error_case",
                "ticker": "000001",
                "trade_date": "2021-03-24",
                "outcome_audit_ids": ["AUDIT-1"],
                "source_fact_ids": ["FACT-1"],
                "payload": {"classification": "RANKING_MISS"},
            },
            "beneficiary_case_id",
            "BEN-1",
        ),
        (
            "newsless_or_unexplained_cases.jsonl",
            {
                "newsless_case_id": "NEWSLESS-LEGACY-1",
                "audit_id": "AUDIT-LEGACY-1",
                "ticker": "000002",
                "trade_date": "2021-03-24",
            },
            {
                "record_id": "BD-LEGACY-1",
                "record_type": "newsless_or_unexplained_case",
                "ticker": "000002",
                "trade_date": "2021-03-24",
                "payload": {"audit_id": "AUDIT-LEGACY-1"},
            },
            "newsless_case_id",
            "NEWSLESS-LEGACY-1",
        ),
    ],
)
def test_v2_case_population_derivation_recomputes_unique_evidence_join(
    tmp_path: Path,
    block_name: str,
    case: dict[str, object],
    source_record: dict[str, object],
    target_field: str,
    case_id: str,
) -> None:
    audit_value = str(case.get("outcome_audit_id") or case.get("audit_id") or "AUDIT-1")
    join_values = {"outcome_audit_id": [audit_value]}
    if case.get("matched_fact_ids"):
        join_values["fact_id"] = [str(case["matched_fact_ids"][0])]
    derivation = {
        "rule_id": "case_id_from_unique_evidence_join.v2",
        "source_artifact": block_name,
        "source_case_id": case_id,
        "source_case_payload_sha256": sha256_text(canonical_json(case)),
        "target_field": target_field,
        "join_field": "outcome_audit_id",
        "join_value": audit_value,
        "join_values": join_values,
        "record_type_relation": (f"{block_name}:{case.get('classification')}->{source_record['record_type']}"),
    }
    source = _write_bundle(
        tmp_path / f"v2-{target_field}-source.md",
        {block_name: [case], "brain_delta.jsonl": [source_record]},
    )
    repaired_record = {
        **source_record,
        target_field: case_id,
        "repair_population_derivations": [derivation],
    }
    repaired = _write_bundle(
        tmp_path / f"v2-{target_field}-repaired.md",
        {block_name: [case], "brain_delta.jsonl": [repaired_record]},
    )

    _, lineage_audit = _build_lineage(
        census_source(source),
        artifact_rows(source),
        [repaired_record],
    )
    derived_audit = _derived_case_population_audit(
        artifact_rows(source),
        artifact_rows(repaired),
    )

    assert lineage_audit["illegal_transform_count"] == 0
    assert derived_audit["derived_case_link_count"] == 1
    assert derived_audit["derived_case_link_failure_count"] == 0

    derivation["join_values"] = {"outcome_audit_id": ["AUDIT-FORGED"]}
    forged = _write_bundle(
        tmp_path / f"v2-{target_field}-forged.md",
        {block_name: [case], "brain_delta.jsonl": [repaired_record]},
    )
    forged_audit = _derived_case_population_audit(
        artifact_rows(source),
        artifact_rows(forged),
    )
    assert forged_audit["derived_case_link_failure_count"] == 1


def test_semantic_exclusion_uses_explicit_positive_contradiction_only(
    tmp_path: Path,
) -> None:
    blocks = {
        "candidate_semantic_witness.jsonl": [
            {
                "candidate_id": "CAND-NEGATIVE",
                "screening_id": "SCR-NEGATIVE",
                "semantic_verdict": "NOT_FINAL_AUDIT",
                "forbidden_quote_role_detected": True,
            }
        ],
        "final_evidence_witness.jsonl": [
            {
                "candidate_id": "CAND-POSITIVE",
                "primary_fact_id": "FACT-POSITIVE",
                "semantic_verdict": "PASS",
                "local_predicate_owner_is_candidate": False,
            }
        ],
        "brain_delta.jsonl": [
            {
                "record_id": "BD-POSITIVE",
                "record_type": "supervised_direct_event_case",
                "candidate_id": "CAND-POSITIVE",
                "training_eligible": True,
                "sample_weight": 1.0,
            },
            {
                "record_id": "BD-NEGATIVE",
                "record_type": "negative_control_case",
                "candidate_id": "CAND-NEGATIVE",
                "training_eligible": True,
                "sample_weight": 1.0,
            },
        ],
    }
    bundle = _write_bundle(tmp_path / "semantic-exclusion.md", blocks)
    rows = artifact_rows(bundle)
    records = blocks["brain_delta.jsonl"]

    relation_ids = semantic_exclusion_relation_ids(blocks)
    audit = _semantic_exclusion_audit(rows, records)

    assert relation_ids == {"CAND-POSITIVE", "FACT-POSITIVE"}
    assert audit["semantic_invalid_training_eligible_count"] == 1
    assert audit["semantic_invalid_training_eligible_record_ids"] == ["BD-POSITIVE"]


def test_temporal_audit_distinguishes_without_read_from_false_download_attestation(
    tmp_path: Path,
) -> None:
    base_blocks = {
        "blind_prediction.json": [{"cutoff_kst": "2024-01-02T08:59:59+09:00"}],
        "blind_seal_receipt.json": [
            {
                "receipt_written_before_any_outcome_access": True,
                "preseal_outcome_download_count": 0,
            }
        ],
        "brain_delta.jsonl": [],
    }
    safe = _write_bundle(
        tmp_path / "safe.md",
        {
            **base_blocks,
            "access_log.jsonl": [
                {
                    "action": "PREEXISTING_OUTCOME_FILENAME_QUARANTINED_WITHOUT_READ",
                    "logical_role": "outcome_snapshot",
                    "outcome_bytes_touched": False,
                },
                {
                    "action": "VERIFY",
                    "logical_role": "blind_seal_receipt",
                    "status": "PASS",
                },
                {
                    "action": "DOWNLOAD",
                    "logical_role": "outcome_snapshot",
                    "outcome_bytes_touched": True,
                },
            ],
        },
    )
    unsafe = _write_bundle(
        tmp_path / "unsafe.md",
        {
            **base_blocks,
            "access_log.jsonl": [
                {
                    "action": "VERIFY",
                    "logical_role": "blind_seal_receipt",
                    "status": "PASS",
                },
                {
                    "action": "DOWNLOAD",
                    "logical_role": "outcome_snapshot",
                    "outcome_bytes_touched": False,
                },
            ],
        },
    )

    safe_audit = _temporal_audit(artifact_rows(safe))
    unsafe_audit = _temporal_audit(artifact_rows(unsafe))

    assert safe_audit["access_attestation_conflict_count"] == 0
    assert safe_audit["failure_count"] == 0
    assert unsafe_audit["access_attestation_conflict_count"] == 1
    assert "access_log:outcome_access_attestation_conflict" in unsafe_audit["failures"]


def test_legacy_temporal_audit_accepts_event_alias_safe_d1_and_missing_known_at(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-temporal.md",
        {
            "blind_prediction.json": [{"cutoff_kst": "2018-04-04T08:59:59+09:00"}],
            "blind_seal_receipt.json": [
                {
                    "receipt_written_before_any_outcome_access": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "access_log.jsonl": [
                {
                    "event": "VERIFY_BLIND_SEAL_RECEIPT",
                    "logical_role": "blind_seal",
                    "verified": True,
                },
                {
                    "event": "OPENED_OUTCOME_RAW_POSTSEAL",
                    "logical_role": "outcome_snapshot",
                },
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "safe_D1_context": {
                        "snapshot_date": "2018-04-03",
                        "close_return_pct": 3.5,
                    },
                }
            ],
            "brain_delta.jsonl": [
                {
                    "record_id": "BD-1",
                    "record_type": "company_memory_delta",
                    "available_from": "2018-04-05T00:00:00+09:00",
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["failure_count"] == 0
    assert audit["blind_outcome_leak_count"] == 0
    assert audit["company_known_at_violation_count"] == 0


def test_temporal_cutoff_can_be_read_from_research_episode(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path / "episode-cutoff.md",
        {
            "blind_prediction.json": [{"outcome_accessed": False}],
            "research_episode.json": [{"cutoff_at": "2021-03-24T08:59:59+09:00"}],
            "blind_seal_receipt.json": [
                {
                    "receipt_written_before_any_outcome_access": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "source_type": "NEWS_CSV_ROW",
                    "used_in_blind": True,
                    "published_at_kst": "2021-03-24T08:59:00+09:00",
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["time_unverified_source_count"] == 0
    assert not any("after_cutoff" in failure for failure in audit["failures"])


def test_legacy_temporal_audit_accepts_operation_alias_and_safe_d1_used(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "operation-temporal.md",
        {
            "blind_prediction.json": [{"cutoff_kst": "2025-01-16T08:59:59+09:00"}],
            "blind_seal_receipt.json": [
                {
                    "receipt_written_before_any_outcome_access": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "access_log.jsonl": [
                {
                    "operation": "VERIFY_BLIND_SEAL_RECEIPT",
                    "logical_role": "blind_seal_receipt",
                    "status": "RECONSTRUCTED_VERIFIED",
                },
                {
                    "operation": "DOWNLOAD_READ_PARSE_HASH",
                    "logical_role": "outcome_snapshot",
                    "status": "VERIFIED",
                },
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": {
                        "snapshot_date": "2025-01-15",
                        "close_return_pct": -1.5,
                    },
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["failure_count"] == 0
    assert audit["blind_outcome_leak_count"] == 0


@pytest.mark.parametrize(
    ("snapshot_date", "snapshot_sha256", "expected_leaks"),
    [
        ("2018-01-15", "a" * 64, 0),
        ("2018-01-16", "a" * 64, 1),
        ("2018-01-15", "not-a-sha", 1),
    ],
)
def test_legacy_verified_p_snapshot_access_binds_undated_ranking_context(
    tmp_path: Path,
    snapshot_date: str,
    snapshot_sha256: str,
    expected_leaks: int,
) -> None:
    cutoff = "2018-01-16T08:59:59+09:00"
    bundle = _write_bundle(
        tmp_path / f"legacy-p-snapshot-{snapshot_date}-{snapshot_sha256[:3]}.md",
        {
            "blind_prediction.json": [
                {
                    "cutoff": cutoff,
                    "trade_date": "2018-01-16",
                    "blind_snapshot_date": snapshot_date,
                    "final_watchlist": [
                        {
                            "candidate_id": "CAND-1",
                            "p_snapshot_context": {
                                "snapshot_date": snapshot_date,
                                "p_close_return_pct": 1.5,
                            },
                        }
                    ],
                }
            ],
            "blind_packet_manifest.json": [
                {
                    "trade_date": "2018-01-16",
                    "cutoff": cutoff,
                    "sealed_before_outcome": True,
                }
            ],
            "blind_seal_receipt.json": [
                {
                    "trade_date": "2018-01-16",
                    "cutoff": cutoff,
                    "verification_status": "PASSED",
                    "blind_packet_manifest_sha256": "b" * 64,
                    "blind_packet_manifest_byte_size": 100,
                    "outcome_bytes_opened": False,
                    "preseal_outcome_access_all_zero": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "access_log.jsonl": [
                {
                    "method": "WEB_BROWSER_THEN_DOWNLOAD",
                    "phase": "BLIND",
                    "resource": "P_SNAPSHOT_RAW",
                    "result": "VERIFIED",
                    "sha256": snapshot_sha256,
                    "byte_size": 1000,
                    "row_count": 20,
                },
                {
                    "method": "WEB_BROWSER_THEN_DOWNLOAD_AFTER_SEAL",
                    "phase": "OUTCOME",
                    "resource": "D_OUTCOME_SNAPSHOT",
                    "result": "VERIFIED_POSTSEAL",
                },
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {
                        "safe_D1_context": {
                            "p_close_return_pct": 1.5,
                            "p_amount_rank": 10,
                        }
                    },
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["blind_outcome_leak_count"] == expected_leaks
    assert "access_log:seal_or_outcome_sequence_missing" not in audit["failures"]


def test_after_seal_alias_requires_a_verified_embedded_receipt(tmp_path: Path) -> None:
    cutoff = "2018-01-16T08:59:59+09:00"
    bundle = _write_bundle(
        tmp_path / "invalid-embedded-seal.md",
        {
            "blind_prediction.json": [{"cutoff": cutoff}],
            "blind_packet_manifest.json": [
                {
                    "trade_date": "2018-01-16",
                    "cutoff": cutoff,
                    "sealed_before_outcome": True,
                }
            ],
            "blind_seal_receipt.json": [
                {
                    "trade_date": "2018-01-16",
                    "cutoff": cutoff,
                    "verification_status": "FAILED",
                    "blind_packet_manifest_sha256": "b" * 64,
                    "blind_packet_manifest_byte_size": 100,
                    "outcome_bytes_opened": False,
                    "preseal_outcome_access_all_zero": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "access_log.jsonl": [
                {
                    "method": "WEB_BROWSER_THEN_DOWNLOAD_AFTER_SEAL",
                    "phase": "OUTCOME",
                    "resource": "D_OUTCOME_SNAPSHOT",
                    "result": "VERIFIED_POSTSEAL",
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert "access_log:seal_or_outcome_sequence_missing" in audit["failures"]


@pytest.mark.parametrize(
    ("snapshot_date", "ranking_value", "screening_id", "expected_leaks"),
    [
        ("2018-01-09", 80, "SCR-1", 0),
        ("2018-01-10", 80, "SCR-1", 2),
        ("2018-01-09", 81, "SCR-1", 1),
        ("2018-01-09", 80, "SCR-MISSING", 1),
    ],
)
def test_ranking_safe_d1_metrics_require_matching_prior_screening_context(
    tmp_path: Path,
    snapshot_date: str,
    ranking_value: int,
    screening_id: str,
    expected_leaks: int,
) -> None:
    bundle = _write_bundle(
        tmp_path / f"ranking-d1-{snapshot_date}-{ranking_value}-{screening_id}.md",
        {
            "blind_prediction.json": [{"cutoff_kst": "2018-01-10T08:59:59+09:00"}],
            "blind_seal_receipt.json": [{"receipt_written_before_any_outcome_access": True}],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "candidate_id": "CAND-1",
                    "ticker": "000001",
                    "safe_D1_context": {
                        "snapshot_date": snapshot_date,
                        "amount_rank": "80",
                        "high_return_pct": "2.5",
                        "return_5d_pct": "1.25",
                    },
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": screening_id,
                    "candidate_id": "CAND-1",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {
                        "safe_p_amount_rank": ranking_value,
                        "safe_p_high_return_pct": 2.5,
                        "safe_p_return_5d_pct": 1.25,
                    },
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["blind_outcome_leak_count"] == expected_leaks


def test_temporal_audit_joins_postseal_log_aliases_and_prior_context(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "postseal-aliases.md",
        {
            "blind_prediction.json": [{"cutoff_kst": "2024-02-02T08:59:59+09:00"}],
            "blind_seal_receipt.json": [
                {
                    "receipt_written_before_any_outcome_access": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "access_log.jsonl": [
                {
                    "method": "VERIFY",
                    "resource_type": "BLIND_SEAL_RECEIPT",
                    "status": "PASS",
                }
            ],
            "postseal_access_log.jsonl": [
                {
                    "access_type": "READ",
                    "artifact": "OUTCOME_SNAPSHOT",
                    "outcome_byte_access": True,
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-A",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {
                        "p_snapshot_context": {
                            "snapshot_date": "2024-02-01",
                            "close_return_pct": 1.0,
                        }
                    },
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["failure_count"] == 0
    assert audit["blind_outcome_leak_count"] == 0


def test_temporal_audit_accepts_legacy_blind_snapshot_source_metadata(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-blind-snapshot-source.md",
        {
            "blind_prediction.json": [
                {
                    "trade_date": "2018-01-18",
                    "previous_trade_date": "2018-01-17",
                    "cutoff_kst": "2018-01-18T08:59:59+09:00",
                }
            ],
            "blind_seal_receipt.json": [
                {
                    "receipt_written_before_any_outcome_access": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-META-BLIND",
                    "source_type": "research_daily_blind_snapshot",
                    "usage_phase": "BLIND",
                    "available_before_cutoff": True,
                    "time_verified": True,
                    "content_sha256": "a" * 64,
                    "title": "stock-web blind snapshot 2018-01-17",
                }
            ],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "p_snapshot_context": {"close_return_pct": 1.5},
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {"p_snapshot_context": {"close_return_pct": 1.5}},
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["failure_count"] == 0
    assert audit["blind_outcome_leak_count"] == 0


@pytest.mark.parametrize("snapshot_date", ["2025-01-16", None])
def test_safe_d1_container_cannot_hide_d_day_or_undated_outcome(
    tmp_path: Path,
    snapshot_date: str | None,
) -> None:
    context: dict[str, object] = {"close_return_pct": 9.0}
    if snapshot_date is not None:
        context["snapshot_date"] = snapshot_date
    bundle = _write_bundle(
        tmp_path / f"unsafe-safe-d1-{snapshot_date or 'missing'}.md",
        {
            "blind_prediction.json": [{"cutoff_kst": "2025-01-16T08:59:59+09:00"}],
            "blind_seal_receipt.json": [
                {
                    "receipt_written_before_any_outcome_access": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": context,
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["blind_outcome_leak_count"] == 1
    assert "blind_payload:outcome_fields_present" in audit["failures"]


def test_final_blind_candidate_cannot_use_time_unverified_source(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "unverified-final-source.md",
        {
            "blind_prediction.json": [
                {
                    "cutoff_kst": "2024-01-02T08:59:59+09:00",
                    "final_watchlist": [
                        {
                            "candidate_id": "CAND-1",
                            "source_screening_id": "SCR-1",
                            "source_fact_ids": ["FACT-1"],
                        }
                    ],
                }
            ],
            "blind_seal_receipt.json": [
                {
                    "receipt_written_before_any_outcome_access": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "source_type": "NEWS_CSV_ROW",
                    "used_in_blind": True,
                    "time_verified": False,
                }
            ],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1", "source_id": "SRC-1"}],
            "candidate_screening.jsonl": [
                {
                    "screening_id": "SCR-1",
                    "screening_decision": "INCLUDE",
                    "source_fact_ids": ["FACT-1"],
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["time_unverified_source_count"] == 1
    assert audit["blind_decision_time_unverified_source_count"] == 1
    assert "blind_prediction:time_unverified_source_used" in audit["failures"]


def test_provenance_closure_is_recomputed_through_fact_and_inference(
    tmp_path: Path,
) -> None:
    record = {
        "record_id": "BD-1",
        "record_type": "memory_claim",
        "training_eligible": True,
        "sample_weight": 1.0,
        "source_fact_ids": [],
        "source_inference_ids": ["INF-1"],
        "provenance_source_ids": [],
        "available_from": "2024-01-02T09:00:00+09:00",
    }
    repaired = _write_bundle(
        tmp_path / "repaired.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-REAL",
                    "source_type": "NEWS",
                    "title": "evidence",
                }
            ],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1", "source_id": "SRC-REAL"}],
            "inference_ledger_blind.jsonl": [{"inference_id": "INF-1", "source_fact_ids": ["FACT-1"]}],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-1",
                    "resolved_provenance_source_ids": ["SRC-FORGED"],
                    "source_fact_ids": ["FACT-FORGED"],
                    "source_inference_ids": ["INF-1"],
                    "closure_status": "CLOSED",
                    "training_eligible_after_closure": True,
                    "sample_weight_after_closure": 1.0,
                }
            ],
        },
    )

    provenance, eligibility = _provenance_and_eligibility_audit(
        artifact_rows(repaired),
        [record],
        [],
    )

    assert provenance["eligible_empty_source_count"] == 0
    assert provenance["eligible_unresolved_source_count"] == 0
    assert provenance["closure_content_mismatch_count"] == 1
    assert eligibility["false_to_true_count"] == 0


def test_event_edge_closure_excludes_lineage_checked_retrospective_outcome_source(
    tmp_path: Path,
) -> None:
    record = {
        "record_id": "BD-EVENT-1",
        "record_type": "event_ticker_edge",
        "source_phase": "RETROSPECTIVE_DISCOVERY",
        "training_eligible": True,
        "sample_weight": 1.0,
        "provenance_source_ids": ["SRC-NEWS-1"],
        "source_fact_ids": ["FACT-NEWS-1", "PFACT-OUT-1"],
        "source_inference_ids": ["INF-NEWS-1", "PINF-OUT-1"],
        "provenance_source_filter": {
            "rule_id": "event_ticker_edge_cutoff_safe_sources.v1",
            "removed_source_ids": ["SRC-OUTCOME-1"],
            "retained_source_ids": ["SRC-NEWS-1"],
        },
    }
    bundle = _write_bundle(
        tmp_path / "event-edge-retrospective-closure.md",
        {
            "source_ledger.jsonl": [
                {"source_id": "SRC-NEWS-1", "source_type": "NEWS_CSV_ROW"},
                {"source_id": "SRC-OUTCOME-1", "source_type": "RESEARCH_DAILY_OUTCOME_ROW"},
            ],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-NEWS-1", "source_id": "SRC-NEWS-1"}],
            "fact_ledger_postmortem.jsonl": [{"fact_id": "PFACT-OUT-1", "source_id": "SRC-OUTCOME-1"}],
            "inference_ledger_blind.jsonl": [
                {"inference_id": "INF-NEWS-1", "source_fact_ids": ["FACT-NEWS-1"]}
            ],
            "inference_ledger_postmortem.jsonl": [
                {"inference_id": "PINF-OUT-1", "source_fact_ids": ["PFACT-OUT-1"]}
            ],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-EVENT-1",
                    "resolved_provenance_source_ids": ["SRC-NEWS-1"],
                    "source_fact_ids": record["source_fact_ids"],
                    "source_inference_ids": record["source_inference_ids"],
                    "closure_status": "CLOSED",
                    "training_eligible_after_closure": True,
                    "sample_weight_after_closure": 1.0,
                }
            ],
        },
    )

    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )

    assert provenance["closure_content_mismatch_count"] == 0


def test_legacy_no_training_closure_does_not_require_source_flags(tmp_path: Path) -> None:
    record = {
        "record_id": "BD-NO-TRAIN",
        "record_type": "newsless_or_unexplained_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "training_exclusion_reason": "source_declared_ineligible_without_reason",
        "provenance_source_ids": [],
        "source_fact_ids": [],
        "source_inference_ids": [],
    }
    bundle = _write_bundle(
        tmp_path / "legacy-no-training-closure.md",
        {
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-NO-TRAIN",
                    "record_type": "newsless_or_unexplained_case",
                    "closure_status": "NOT_TRAINING_NO_CLOSURE_REQUIRED",
                    "training_eligible": False,
                    "training_eligible_after_closure": False,
                    "sample_weight_after_closure": 0.0,
                }
            ],
        },
    )
    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )
    assert provenance["closure_content_mismatch_count"] == 0


def test_absent_closure_artifact_is_allowed_for_explicit_zero_weight_rows(tmp_path: Path) -> None:
    record = {
        "record_id": "BD-NO-CLOSURE-ARTIFACT",
        "record_type": "supervised_issuer_day_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "training_exclusion_reason": "blind_packet_byte_exact_restoration_failed",
        "provenance_source_ids": ["SRC-1"],
        "source_fact_ids": ["FACT-1"],
        "source_inference_ids": ["INF-1"],
    }
    bundle = _write_bundle(
        tmp_path / "no-closure-artifact.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "NEWS_CSV_ROW"}],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1", "source_id": "SRC-1"}],
            "inference_ledger_blind.jsonl": [{"inference_id": "INF-1", "source_fact_ids": ["FACT-1"]}],
            "brain_delta.jsonl": [record],
        },
    )
    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )
    assert provenance["closure_content_mismatch_count"] == 0
    assert provenance["closure_artifact_absent_nontraining_count"] == 1


def test_absent_closure_artifact_is_legacy_warning_for_closed_eligible_row(
    tmp_path: Path,
) -> None:
    record = {
        "record_id": "BD-ELIGIBLE-NO-CLOSURE",
        "record_type": "memory_claim",
        "training_eligible": True,
        "sample_weight": 1.0,
        "provenance_source_ids": ["SRC-1"],
        "source_fact_ids": ["FACT-1"],
        "source_inference_ids": ["INF-1"],
    }
    bundle = _write_bundle(
        tmp_path / "eligible-no-closure-artifact.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "NEWS_CSV_ROW"}],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1", "source_id": "SRC-1"}],
            "inference_ledger_blind.jsonl": [
                {"inference_id": "INF-1", "source_fact_ids": ["FACT-1"]}
            ],
            "brain_delta.jsonl": [record],
        },
    )

    provenance, eligibility = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )

    assert provenance["eligible_unresolved_source_count"] == 0
    assert provenance["closure_content_mismatch_count"] == 0
    assert provenance["closure_artifact_absent_eligible_count"] == 1
    assert eligibility["false_to_true_count"] == 0


def test_blind_snapshot_context_does_not_require_legacy_row_count() -> None:
    assert _record_has_verified_context_provenance(
        {"record_type": "negative_control_case", "source_phase": "POSTMORTEM"},
        sources={"SRC-BLIND-SNAPSHOT"},
        source_rows_by_id={
            "SRC-BLIND-SNAPSHOT": {
                "source_type": "RESEARCH_DAILY_BLIND_SNAPSHOT",
                "usage_phase": "BLIND",
                "path": "/tmp/blind.csv",
                "sha256": "a" * 64,
            }
        },
    ) is True


def test_blind_snapshot_row_aliases_close_provenance() -> None:
    assert _record_has_verified_context_provenance(
        {"record_type": "newsless_or_unexplained_case", "source_phase": "POSTMORTEM"},
        sources={"SRC-BLIND-ROW"},
        source_rows_by_id={
            "SRC-BLIND-ROW": {
                "source_type": "RESEARCH_DAILY_BLIND_SNAPSHOT_ROW",
                "source_phase": "BLIND",
                "source_file": "blind_snapshot_20241024.csv",
                "source_file_sha256": "b" * 64,
            }
        },
    ) is True


def test_verified_pre_cutoff_postmortem_external_source_is_closed() -> None:
    assert _record_has_verified_context_provenance(
        {
            "record_type": "beneficiary_discovery_case",
            "source_phase": "POSTMORTEM",
            "trade_date": "2023-06-16",
        },
        sources={"PSRC-20230616-0002"},
        source_rows_by_id={
            "PSRC-20230616-0002": {
                "source_type": "POSTSEAL_OFFICIAL_DISCLOSURE",
                "source_phase": "POSTMORTEM",
                "cutoff_relation": "PRE_CUTOFF_EXTERNAL_NOT_IN_INPUT_CSV",
                "retrieval_status": "WEB_VERIFIED_TITLE_AND_DATE",
                "published_at_kst": "2023-06-15 00:00:00",
                "url": "https://kind.krx.co.kr/example",
                "exact_excerpt": "[공시] 주식분할 결정",
            }
        },
    ) is True


def test_postmortem_external_source_after_trade_date_is_not_closed() -> None:
    assert _record_has_verified_context_provenance(
        {
            "record_type": "beneficiary_discovery_case",
            "source_phase": "POSTMORTEM",
            "trade_date": "2023-06-16",
        },
        sources={"PSRC-AFTER"},
        source_rows_by_id={
            "PSRC-AFTER": {
                "source_type": "POSTSEAL_WEB_PAGE",
                "source_phase": "POSTMORTEM",
                "cutoff_relation": "PRE_CUTOFF_EXTERNAL_NOT_IN_INPUT_CSV",
                "retrieval_status": "WEB_VERIFIED",
                "published_at_kst": "2023-06-17 00:00:00",
                "url": "https://example.invalid/after",
                "exact_excerpt": "after cutoff",
            }
        },
    ) is False


def test_hashed_news_csv_file_is_verified_blind_context() -> None:
    assert _record_has_verified_context_provenance(
        {"record_type": "context_market_state_or_fact_case", "source_phase": "BLIND"},
        sources={"SRC-NEWS-CSV-FILE"},
        source_rows_by_id={
            "SRC-NEWS-CSV-FILE": {
                "source_type": "news_csv",
                "usage_phase": "BLIND",
                "available_before_cutoff": True,
                "time_verified": True,
                "content_sha256": "a" * 64,
                "input_row_ids": ["SRC-000001"],
            }
        },
    ) is True


def test_nontraining_case_underfill_requires_explicit_quarantine_reason() -> None:
    case = type(
        "ArtifactRowStub",
        (),
        {
            "row": {
                "direct_event_case_id": "DEC-1",
                "training_eligible": False,
                "sample_weight": 0.0,
                "training_exclusion_reason": "blind_packet_semantic_contract_failure",
                "screening_decision": "EXCLUDE",
            }
        },
    )()
    assert _nontraining_case_underfill_only(
        {"direct_event_cases.jsonl": [case]},
        {"case_to_brain:DIRECT_EVENT": {"missing_keys": ["DEC-1"]}},
    ) is True
    case.row["training_exclusion_reason"] = "missing evidence"
    assert _nontraining_case_underfill_only(
        {"direct_event_cases.jsonl": [case]},
        {"case_to_brain:DIRECT_EVENT": {"missing_keys": ["DEC-1"]}},
    ) is False


def test_legacy_nontraining_quarantine_status_is_a_closure_proof(tmp_path: Path) -> None:
    record = {
        "record_id": "BD-QUARANTINE",
        "record_type": "supervised_issuer_day_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "training_exclusion_reason": "context_only",
        "provenance_source_ids": ["SRC-1"],
        "source_fact_ids": ["FACT-1"],
        "source_inference_ids": ["INF-1"],
    }
    bundle = _write_bundle(
        tmp_path / "legacy-nontraining-quarantine.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "NEWS_CSV_ROW"}],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1", "source_id": "SRC-1"}],
            "inference_ledger_blind.jsonl": [{"inference_id": "INF-1", "source_fact_ids": ["FACT-1"]}],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "brain_record_id": "BD-QUARANTINE",
                    "record_type": "supervised_issuer_day_case",
                    "source_provenance_closed": True,
                    "status": "PASS_NONTRAINING_QUARANTINE",
                    "training_eligible": False,
                    "training_eligible_empty_provenance": False,
                    "unresolved_reference_ids": [],
                }
            ],
        },
    )
    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )

    assert provenance["closure_content_mismatch_count"] == 0


def test_closed_not_training_legacy_closure_may_omit_fact_flags(tmp_path: Path) -> None:
    record = {
        "record_id": "BD-CLOSED-NOT-TRAIN",
        "record_type": "candidate_generation_error_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "training_exclusion_reason": "semantic_contract_failed",
        "provenance_source_ids": ["SRC-1"],
        "source_fact_ids": [],
        "source_inference_ids": [],
    }
    bundle = _write_bundle(
        tmp_path / "legacy-closed-not-training.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "NEWS_CSV_ROW"}],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-CLOSED-NOT-TRAIN",
                    "closure_status": "CLOSED_NOT_TRAINING",
                    "resolved_provenance_source_ids": ["SRC-1"],
                    "training_eligible_after_closure": False,
                    "sample_weight_after_closure": 0.0,
                }
            ],
        },
    )
    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )
    assert provenance["closure_content_mismatch_count"] == 0


def test_closed_not_training_closure_may_omit_absent_inference_ids(tmp_path: Path) -> None:
    record = {
        "record_id": "BD-NO-INF",
        "record_type": "candidate_generation_error_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "training_exclusion_reason": "semantic_contract_failed",
        "provenance_source_ids": ["SRC-1"],
        "source_fact_ids": ["FACT-1"],
        "source_inference_ids": [],
    }
    bundle = _write_bundle(
        tmp_path / "legacy-no-inference-closure.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "NEWS_CSV_ROW"}],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-NO-INF",
                    "closure_status": "CLOSED_NOT_TRAINING",
                    "resolved_provenance_source_ids": ["SRC-1"],
                    "source_fact_ids": ["FACT-1"],
                    "training_eligible_after_closure": False,
                    "sample_weight_after_closure": 0.0,
                }
            ],
        },
    )
    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )
    assert provenance["closure_content_mismatch_count"] == 0


def test_legacy_pass_closure_alias_is_accepted_only_when_bound_to_record(tmp_path: Path) -> None:
    record = {
        "record_id": "BD-LEGACY-PASS",
        "record_type": "supervised_issuer_day_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "training_exclusion_reason": "legacy_episode_quarantine",
        "provenance_source_ids": ["SRC-1"],
        "source_fact_ids": ["FACT-1"],
        "source_inference_ids": ["INF-1"],
    }
    bundle = _write_bundle(
        tmp_path / "legacy-pass-closure.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "NEWS_CSV_ROW"}],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1", "source_id": "SRC-1"}],
            "inference_ledger_blind.jsonl": [{"inference_id": "INF-1", "source_fact_ids": ["FACT-1"]}],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-LEGACY-PASS",
                    "record_type": "supervised_issuer_day_case",
                    "closure_status": "PASS",
                    "provenance_source_ids": ["SRC-1"],
                    "unresolved_source_ids": [],
                    "training_eligible": False,
                    "sample_weight": 0.0,
                }
            ],
        },
    )

    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )

    assert provenance["closure_content_mismatch_count"] == 0


def test_legacy_passed_closure_alias_requires_resolve_attestations(tmp_path: Path) -> None:
    record = {
        "record_id": "BD-LEGACY-PASSED",
        "record_type": "supervised_issuer_day_case",
        "training_eligible": True,
        "sample_weight": 1.0,
        "provenance_source_ids": ["SRC-1"],
        "source_fact_ids": ["FACT-1"],
        "source_inference_ids": ["INF-1"],
    }
    bundle = _write_bundle(
        tmp_path / "legacy-passed-closure.md",
        {
            "source_ledger.jsonl": [{"source_id": "SRC-1", "source_type": "NEWS_CSV_ROW"}],
            "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1", "source_id": "SRC-1"}],
            "inference_ledger_blind.jsonl": [{"inference_id": "INF-1", "source_fact_ids": ["FACT-1"]}],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-LEGACY-PASSED",
                    "record_type": "supervised_issuer_day_case",
                    "closure_status": "PASSED",
                    "fact_ids_resolve": True,
                    "inference_ids_resolve": True,
                    "source_ids_resolve": True,
                    "required_evidence_nonempty": True,
                    "failure_reasons": [],
                    "training_eligible": True,
                }
            ],
        },
    )

    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )

    assert provenance["closure_content_mismatch_count"] == 0


@pytest.mark.parametrize(
    ("record_type", "cutoff_safe", "expected_unresolved"),
    [
        ("context_market_state_or_fact_case", True, 0),
        ("context_market_state_or_fact_case", False, 1),
        ("memory_claim", True, 1),
    ],
)
def test_context_record_accepts_only_verified_blind_price_snapshot_provenance(
    tmp_path: Path,
    record_type: str,
    cutoff_safe: bool,
    expected_unresolved: int,
) -> None:
    record = {
        "record_id": "BD-1",
        "record_type": record_type,
        "training_eligible": True,
        "sample_weight": 1.0,
        "source_fact_ids": ["PMFACT-1"],
        "source_inference_ids": ["PMINF-1"],
        "provenance_source_ids": ["SRC-P-SNAPSHOT"],
        "available_from": "2024-01-03T00:00:00+09:00",
    }
    bundle = _write_bundle(
        tmp_path / f"context-provenance-{record_type}-{cutoff_safe}.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-P-SNAPSHOT",
                    "source_type": "BLIND_PRICE_SNAPSHOT",
                    "available_before_cutoff": cutoff_safe,
                    "time_verified": True,
                    "used_in_blind": True,
                }
            ],
            "fact_ledger_postmortem.jsonl": [
                {
                    "fact_id": "PMFACT-1",
                    "source_row_id": "SRC-P-SNAPSHOT",
                }
            ],
            "inference_ledger_postmortem.jsonl": [{"inference_id": "PMINF-1", "source_fact_ids": ["PMFACT-1"]}],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-1",
                    "resolved_provenance_source_ids": ["SRC-P-SNAPSHOT"],
                    "source_fact_ids": ["PMFACT-1"],
                    "source_inference_ids": ["PMINF-1"],
                    "closure_status": "CLOSED",
                    "training_eligible_after_closure": True,
                    "sample_weight_after_closure": 1.0,
                }
            ],
        },
    )

    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )

    assert provenance["eligible_unresolved_source_count"] == expected_unresolved
    assert provenance["closure_content_mismatch_count"] == 0


def test_training_eligible_record_cannot_use_time_unverified_news_source(
    tmp_path: Path,
) -> None:
    record = {
        "record_id": "BD-1",
        "record_type": "memory_claim",
        "training_eligible": True,
        "sample_weight": 1.0,
        "provenance_source_ids": ["SRC-1"],
        "source_fact_ids": [],
        "source_inference_ids": [],
        "available_from": "2024-01-02T00:00:00+09:00",
    }
    bundle = _write_bundle(
        tmp_path / "unverified-time.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-1",
                    "source_type": "NEWS_CSV_ROW",
                    "time_verified": False,
                }
            ],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-1",
                    "resolved_provenance_source_ids": ["SRC-1"],
                    "source_fact_ids": [],
                    "source_inference_ids": [],
                    "closure_status": "CLOSED",
                    "training_eligible_after_closure": True,
                    "sample_weight_after_closure": 1.0,
                }
            ],
        },
    )

    provenance, _ = _provenance_and_eligibility_audit(artifact_rows(bundle), [record], [])

    assert provenance["eligible_time_unverified_source_count"] == 1


def test_postmortem_record_accepts_sealed_outcome_snapshot_provenance(
    tmp_path: Path,
) -> None:
    record = {
        "record_id": "BD-OUTCOME",
        "record_type": "newsless_or_unexplained_case",
        "source_phase": "SUPERVISED_POSTMORTEM",
        "training_eligible": True,
        "sample_weight": 1.0,
        "provenance_source_ids": ["SRC-OUTCOME"],
        "source_fact_ids": [],
        "source_inference_ids": [],
        "available_from": "2024-01-03T00:00:00+09:00",
    }
    bundle = _write_bundle(
        tmp_path / "postmortem-outcome-provenance.md",
        {
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-OUTCOME",
                    "source_type": "research_daily_outcome_snapshot",
                    "usage_phase": "POSTSEAL_OUTCOME",
                    "available_before_cutoff": False,
                    "time_verified": True,
                    "byte_size": 100,
                    "content_sha256": "a" * 64,
                }
            ],
            "brain_delta.jsonl": [record],
            "record_provenance_closure_audit.jsonl": [
                {
                    "record_id": "BD-OUTCOME",
                    "resolved_provenance_source_ids": ["SRC-OUTCOME"],
                    "source_fact_ids": [],
                    "source_inference_ids": [],
                    "closure_status": "CLOSED",
                    "training_eligible_after_closure": True,
                    "sample_weight_after_closure": 1.0,
                }
            ],
        },
    )

    provenance, _ = _provenance_and_eligibility_audit(
        artifact_rows(bundle),
        [record],
        [],
    )

    assert provenance["eligible_unresolved_source_count"] == 0


def test_evaluate_distinguishes_current_gold_from_importable_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        repair_quality_module,
        "_importer_audit",
        lambda _inspection: _passing_importer_audit(),
    )
    current = _write_bundle(
        tmp_path / "current.md",
        _current_contract_blocks(),
    )

    current_gate, _, _ = evaluate_bundle_quality(
        current,
        current,
        engine_digest="engine-sha",
        deterministic={"matches": True},
        ephemeral_store={"passed": True, "real_store_unchanged": True},
    )

    legacy_blocks = _current_contract_blocks()
    del legacy_blocks["candidate_semantic_witness.jsonl"]
    legacy = _write_bundle(tmp_path / "legacy.md", legacy_blocks)
    legacy_gate, _, _ = evaluate_bundle_quality(
        legacy,
        legacy,
        engine_digest="engine-sha",
        deterministic={"matches": True},
        ephemeral_store={"passed": True, "real_store_unchanged": True},
    )
    assert current_gate.current_gold_pass is True
    assert current_gate.final_status == RepairTaskState.REPAIRED_PASS
    assert legacy_gate.importable_legacy is True
    assert legacy_gate.current_gold_pass is False
    assert legacy_gate.ready_for_import_pass is True
    assert legacy_gate.final_status == RepairTaskState.REPAIRED_PASS


def test_empty_brain_delta_cannot_be_importable_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        repair_quality_module,
        "_importer_audit",
        lambda _inspection: _passing_importer_audit(),
    )
    bundle = _write_bundle(tmp_path / "empty-brain.md", {"brain_delta.jsonl": []})

    gate, _, _ = evaluate_bundle_quality(
        bundle,
        bundle,
        engine_digest="engine-sha",
        deterministic={"matches": True},
        ephemeral_store={"passed": True, "real_store_unchanged": True},
    )

    assert gate.ready_for_import_pass is False
    assert gate.importable_legacy is False
    assert gate.final_status == RepairTaskState.PRESERVED_SOURCE_PAYLOAD_ABSENT
    assert "SOURCE_PAYLOAD_ABSENT:brain_delta_record_count=0" in gate.blockers


def test_temporal_audit_accepts_sha_bound_bundle_level_prior_snapshot(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "prior-context.md",
        {
            "research_episode.json": [{"trade_date": "2018-01-03", "cutoff_at": "2018-01-03T08:59:59+09:00"}],
            "source_ledger.jsonl": [
                {
                    "source_id": "SRC-CALENDAR",
                    "source_type": "calendar_verification",
                    "previous_trade_date": "2018-01-02",
                },
                {
                    "source_id": "SRC-BLIND-SNAPSHOT",
                    "source_type": "core_file",
                    "logical_role": "previous_trade_date_price_snapshot",
                    "path": "blind_snapshot_20180102.csv",
                    "sha256": "a" * 64,
                    "usage_phase": "BLIND",
                    "cutoff_safe": True,
                },
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {"P_amount_rank": 10, "P_turnover_rank": 20},
                }
            ],
            "blind_seal_receipt.json": [{"receipt_written_before_any_outcome_access": True}],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["blind_outcome_leak_count"] == 0


def test_temporal_audit_accepts_verified_access_log_prior_snapshot_without_status(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-access-prior.md",
        {
            "blind_prediction.json": [
                {
                    "trade_date": "2018-01-22",
                    "previous_trade_date": "2018-01-19",
                }
            ],
            "phase_state.json": [
                {
                    "cutoff_at": "2018-01-22T08:59:59+09:00",
                    "blind_seal_receipt_verified": True,
                    "blind_sealed": True,
                }
            ],
            "access_log.jsonl": [
                {
                    "action": "FRESH_DOWNLOAD_VERIFY_PARSE",
                    "logical_role": "BLIND_SNAPSHOT_P",
                    "phase": "BLIND_INPUT",
                    "path": "blind_snapshot_20180119.csv",
                    "row_count": 2134,
                    "sha256": "a" * 64,
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {"safe_d1_close_return_pct": 2.5},
                }
            ],
            "blind_seal_receipt.json": [
                {"receipt_written_before_any_outcome_access": True}
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["blind_outcome_leak_count"] == 0


def test_temporal_audit_accepts_preseal_p_snapshot_resource_alias(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-resource-prior.md",
        {
            "blind_prediction.json": [
                {
                    "trade_date": "2018-01-23",
                    "previous_trade_date": "2018-01-22",
                }
            ],
            "phase_state.json": [
                {
                    "cutoff_at": "2018-01-23T08:59:59+09:00",
                    "blind_seal_receipt_verified": True,
                    "blind_sealed": True,
                }
            ],
            "access_log.jsonl": [
                {
                    "resource": "blind_snapshot_raw",
                    "phase": "PHASE_0",
                    "path": "blind_snapshot_20180122.csv",
                    "byte_size": 700000,
                    "sha256": "a" * 64,
                    "status": "VERIFIED_PRESEAL_SAFE_P_SNAPSHOT",
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {"safe_D1_close_return_pct": 2.5},
                }
            ],
            "blind_seal_receipt.json": [
                {"receipt_written_before_any_outcome_access": True}
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["blind_outcome_leak_count"] == 0


def test_temporal_audit_accepts_legacy_seal_and_postseal_status_aliases(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "legacy-sequence-status.md",
        {
            "blind_prediction.json": [
                {
                    "trade_date": "2018-01-23",
                    "previous_trade_date": "2018-01-22",
                    "cutoff_at": "2018-01-23T08:59:59+09:00",
                }
            ],
            "access_log.jsonl": [
                {
                    "access_id": "ACCESS-004",
                    "resource": "blind_seal_receipt",
                    "phase": "PHASE_5",
                    "status": "VERIFIED_BEFORE_OUTCOME_ACCESS",
                },
                {
                    "access_id": "ACCESS-005",
                    "resource": "outcome_snapshot_raw",
                    "phase": "PHASE_6",
                    "status": "VERIFIED_POSTSEAL_D_SNAPSHOT",
                },
            ],
            "blind_seal_receipt.json": [
                {"receipt_written_before_any_outcome_access": True}
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["failure_count"] == 0


@pytest.mark.parametrize("receipt_matches", [True, False])
def test_temporal_audit_accepts_sealed_manifest_level_prior_snapshot(
    tmp_path: Path,
    receipt_matches: bool,
) -> None:
    manifest_sha = "b" * 64
    bundle = _write_bundle(
        tmp_path / f"sealed-prior-{receipt_matches}.md",
        {
            "blind_prediction.json": [
                {
                    "trade_date": "2018-01-05",
                    "previous_trade_date": "2018-01-04",
                    "cutoff": "2018-01-05T08:59:59+09:00",
                }
            ],
            "blind_packet_manifest.json": [
                {
                    "blind_snapshot_file": "blind_snapshot_20180104.csv",
                    "blind_snapshot_sha256": "a" * 64,
                    "outcome_file_bytes_accessed": False,
                    "preseal_counters": {
                        "outcome_download_count": 0,
                        "outcome_parse_count": 0,
                    },
                }
            ],
            "blind_seal_receipt.json": [
                {
                    "blind_packet_manifest_sha256": manifest_sha,
                    "receipt_written_before_any_outcome_access": True,
                    "preseal_outcome_download_count": 0,
                    "preseal_outcome_parse_count": 0,
                    "sealed_artifacts": [
                        {
                            "name": "blind_packet_manifest.json",
                            "sha256": manifest_sha if receipt_matches else "c" * 64,
                        }
                    ],
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {"safe_d1_close_return_pct": 2.5},
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["blind_outcome_leak_count"] == (0 if receipt_matches else 1)


@pytest.mark.parametrize("tampered", [False, True])
def test_temporal_audit_accepts_v11_seal_and_postseal_prior_snapshot(
    tmp_path: Path,
    tampered: bool,
) -> None:
    bundle = _write_bundle(
        tmp_path / f"v11-sealed-prior-{tampered}.md",
        {
            "blind_prediction.json": [
                {
                    "trade_date": "2018-01-08",
                    "previous_trade_date": "2018-01-05",
                    "cutoff": "2018-01-08T08:59:59+09:00",
                }
            ],
            "blind_packet_manifest.json": [
                {
                    "blind_snapshot_path": "snapshots/2018/01/20180105.csv",
                    "blind_snapshot_sha256": "a" * 64,
                    "outcome_snapshot_not_downloaded": not tampered,
                    "preseal_outcome_access_count": 0,
                }
            ],
            "blind_seal_receipt.json": [
                {
                    "blind_packet_manifest_sha256": "b" * 64,
                    "blind_snapshot_sha256": "a" * 64,
                    "seal_verified": True,
                    "outcome_access_allowed_after_this_receipt": True,
                    "preseal_outcome_download_count": 0,
                    "preseal_outcome_parse_count": 0,
                }
            ],
            "access_log.jsonl": [
                {
                    "sequence": 16,
                    "action": "WRITE_AND_VERIFY_BLIND_SEAL",
                    "target": "blind_seal_receipt.json",
                    "logical_role": "BLIND_SEAL",
                    "seal_verified": True,
                }
            ],
            "postseal_access_log.jsonl": [
                {
                    "sequence": 17,
                    "action": "DOWNLOAD_VERIFY_PARSE",
                    "target": "snapshots/2018/01/20180108.csv",
                    "logical_role": "OUTCOME_SNAPSHOT",
                    "byte_access": True,
                    "blind_seal_verified_before_action": True,
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {"safe_d1_close_return_pct": 2.5},
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["blind_outcome_leak_count"] == (1 if tampered else 0)


def test_sealed_prior_context_cannot_hide_strong_outcome_payload(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "sealed-prior-hidden-outcome.md",
        {
            "blind_prediction.json": [
                {
                    "trade_date": "2018-01-08",
                    "previous_trade_date": "2018-01-05",
                    "cutoff": "2018-01-08T08:59:59+09:00",
                }
            ],
            "blind_packet_manifest.json": [
                {
                    "blind_snapshot_path": "snapshots/2018/01/20180105.csv",
                    "blind_snapshot_sha256": "a" * 64,
                    "outcome_snapshot_not_downloaded": True,
                    "preseal_outcome_access_count": 0,
                }
            ],
            "blind_seal_receipt.json": [
                {
                    "blind_packet_manifest_sha256": "b" * 64,
                    "blind_snapshot_sha256": "a" * 64,
                    "seal_verified": True,
                    "outcome_access_allowed_after_this_receipt": True,
                    "preseal_outcome_download_count": 0,
                }
            ],
            "access_log.jsonl": [
                {
                    "sequence": 16,
                    "action": "WRITE_AND_VERIFY_BLIND_SEAL",
                    "target": "blind_seal_receipt.json",
                    "logical_role": "BLIND_SEAL",
                    "seal_verified": True,
                }
            ],
            "postseal_access_log.jsonl": [
                {
                    "sequence": 17,
                    "action": "DOWNLOAD_VERIFY_PARSE",
                    "logical_role": "OUTCOME_SNAPSHOT",
                    "byte_access": True,
                }
            ],
            "candidate_ranking_audit.jsonl": [
                {
                    "source_screening_id": "SCR-1",
                    "safe_D1_context_used": True,
                    "ranking_inputs": {"p_snapshot_context": {"D_response": {"high_return_pct": 30.0}}},
                }
            ],
        },
    )

    audit = _temporal_audit(artifact_rows(bundle), require_current_contract=False)

    assert audit["blind_outcome_leak_count"] == 1




@pytest.mark.parametrize(
    "hidden_payload",
    [
        ('# Unclaimed appendix\n\n````jsonl\n{"record_id":"BD-LOST","record_type":"memory_claim"}\n'),
        (
            "# Unclaimed appendix\n\n"
            "~~~~jsonl\n"
            '{"record_id":"BD-1","record_type":"memory_claim"}\n'
            '{"record_id":"BD-2","record_type":\n'
            "~~~~~\n"
        ),
        ('# Unclaimed appendix\n\n{"record_id":"BD-LOST","record_type":"memory_claim"}\n'),
    ],
)
def test_evaluate_rejects_false_gold_when_source_only_raw_payload_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hidden_payload: str,
) -> None:
    monkeypatch.setattr(
        repair_quality_module,
        "_importer_audit",
        lambda _inspection: _passing_importer_audit(),
    )
    source = _write_bundle(tmp_path / "source.md", _current_contract_blocks())
    source.write_text(
        f"{source.read_text(encoding='utf-8')}\n{hidden_payload}",
        encoding="utf-8",
    )
    repaired = _write_bundle(tmp_path / "repaired.md", _current_contract_blocks())
    gate, _, _ = evaluate_bundle_quality(
        source,
        repaired,
        engine_digest="engine-sha",
        deterministic={"matches": True},
        ephemeral_store={"passed": True, "real_store_unchanged": True},
    )

    assert gate.current_gold_pass is False
    assert gate.mechanical_gold_ready is False
    assert gate.raw_census["unclaimed_machine_payload_count"] > 0
    assert any(blocker.startswith("RAW_CENSUS:unclaimed_machine_payload_count=") for blocker in gate.blockers)


def test_evaluate_raw_gate_rejects_claimed_parse_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        repair_quality_module,
        "_importer_audit",
        lambda _inspection: _passing_importer_audit(),
    )
    bundle = _write_bundle(tmp_path / "bundle.md", _current_contract_blocks())
    bundle.write_text(
        f'{bundle.read_text(encoding="utf-8")}\n## orphan.jsonl\n```jsonl\n{{"source_id":"SRC-LOST"\n```\n',
        encoding="utf-8",
    )
    census = census_source(bundle)
    gate, _, _ = evaluate_bundle_quality(
        bundle,
        bundle,
        engine_digest="engine-sha",
        deterministic={"matches": True},
        ephemeral_store={"passed": True, "real_store_unchanged": True},
    )

    assert census.unclaimed_machine_payloads == []
    assert gate.current_gold_pass is False
    assert gate.raw_census["source_artifact_parse_issue_count"] == 1
    assert "RAW_CENSUS:source_artifact_parse_issue_count=1" in gate.blockers


def test_evaluate_raw_gate_reconciles_record_type_token_lower_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        repair_quality_module,
        "_importer_audit",
        lambda _inspection: _passing_importer_audit(),
    )
    bundle = _write_bundle(tmp_path / "bundle.md", _current_contract_blocks())
    bundle.write_text(
        f"{bundle.read_text(encoding='utf-8')}\n"
        "## audit_notes.md\n"
        "```markdown\n"
        '{"record_id":"BD-HIDDEN","record_type":"memory_claim"}\n'
        "```\n",
        encoding="utf-8",
    )
    census = census_source(bundle)
    gate, _, _ = evaluate_bundle_quality(
        bundle,
        bundle,
        engine_digest="engine-sha",
        deterministic={"matches": True},
        ephemeral_store={"passed": True, "real_store_unchanged": True},
    )

    assert census.unclaimed_machine_payloads == []
    assert gate.current_gold_pass is False
    assert gate.raw_census["source_raw_record_type_token_count"] == 2
    assert gate.raw_census["source_unreconciled_record_type_token_count"] == 1
    assert "RAW_CENSUS:source_unreconciled_record_type_token_count=1" in gate.blockers


def test_evaluate_raw_gate_ignores_record_type_token_in_markdown_prose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        repair_quality_module,
        "_importer_audit",
        lambda _inspection: _passing_importer_audit(),
    )
    bundle = _write_bundle(tmp_path / "bundle.md", _current_contract_blocks())
    bundle.write_text(
        f"{bundle.read_text(encoding='utf-8')}\n"
        "# Notes\n\n"
        'The literal `"record_type":` is documented here as ordinary prose.\n',
        encoding="utf-8",
    )
    census = census_source(bundle)
    gate, _, _ = evaluate_bundle_quality(
        bundle,
        bundle,
        engine_digest="engine-sha",
        deterministic={"matches": True},
        ephemeral_store={"passed": True, "real_store_unchanged": True},
    )

    assert census.raw_record_type_token_count == 2
    assert gate.raw_census["source_unreconciled_record_type_token_count"] == 0
    assert gate.current_gold_pass is True


def _current_contract_blocks() -> dict[str, list[dict[str, object]]]:
    blocks: dict[str, list[dict[str, object]]] = {
        name: [] for name in repair_quality_module._CURRENT_GOLD_REQUIRED_BLOCKS
    }
    blocks["blind_prediction.json"] = [
        {
            "cutoff_kst": "2024-01-02T08:59:59+09:00",
            "final_watchlist": [],
        }
    ]
    blocks["blind_seal_receipt.json"] = [
        {
            "receipt_written_before_any_outcome_access": True,
            **dict.fromkeys(repair_quality_module._CURRENT_PRESEAL_COUNTERS, 0),
        }
    ]
    blocks["brain_delta.jsonl"] = [
        {
            "record_id": "BD-Q-1",
            "record_type": "research_question",
            "training_eligible": False,
            "sample_weight": 0.0,
            "training_exclusion_reason": "research_question_not_training_sample",
            "provenance_source_ids": [],
            "payload": {"question": "What should be measured next?"},
        }
    ]
    blocks["record_provenance_closure_audit.jsonl"] = [
        {
            "record_id": "BD-Q-1",
            "resolved_provenance_source_ids": [],
            "source_fact_ids": [],
            "source_inference_ids": [],
            "closure_status": "CLOSED",
            "training_eligible_after_closure": False,
            "sample_weight_after_closure": 0.0,
        }
    ]
    blocks["semantic_regression_tests.jsonl"] = [
        {
            "fixture_id": f"SEM-{index:03d}",
            "expected_verdict": "PASS",
            "actual_verdict": "PASS",
            "expected_fail_reason": None,
            "actual_fail_reason": None,
            "passed": True,
        }
        for index in range(1, 14)
    ]
    return blocks


def _passing_importer_audit() -> dict[str, object]:
    return {
        "validation_passed": True,
        "import_loss_audit_passed": True,
        "missing_normalized_record_count": 0,
        "extra_normalized_record_count": 0,
        "raw_normalized_record_count_matches": True,
        "training_eligible_count_matches_raw": True,
        "quarantined_record_count": 0,
        "missing_source_reference_count": 0,
        "missing_payload_reference_count": 0,
        "invalid_typed_payload_record_count": 0,
        "final_semantic_audit_fail_count": 0,
    }


def _write_bundle(
    path: Path,
    blocks: dict[str, list[dict[str, object]]],
) -> Path:
    parts: list[str] = []
    for name, rows in blocks.items():
        language = "json" if name.endswith(".json") else "jsonl"
        if language == "json":
            payload = json.dumps(rows[0], ensure_ascii=False, sort_keys=True)
        else:
            payload = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        parts.append(f"## {name}\n```{language}\n{payload}\n```\n")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
