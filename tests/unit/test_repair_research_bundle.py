from pathlib import Path

import pytest

from news_scalping_lab.records.models import SupervisedIssuerDayCase
from news_scalping_lab.tools import repair_research_bundle as repair


def test_source_ledger_source_row_id_is_materialized_as_source_id() -> None:
    rows = [
        {
            "source_row_id": "NEWS-1",
            "source_type": "NEWS_CSV_ROW",
            "title": "issuer event",
        },
        {"source_id": "SRC-2", "source_type": "NEWS_CSV_ROW"},
    ]

    repaired = repair._repair_source_ledger_rows(rows)

    assert repaired[0]["source_row_id"] == "NEWS-1"
    assert repaired[0]["source_id"] == "NEWS-1"
    assert repaired[1]["source_id"] == "SRC-2"


def test_strip_optional_fence_supports_tilde_markdown() -> None:
    block = "~~~~markdown\n# report\n~~~~"

    assert repair._strip_optional_fence(block) == "# report"


def test_compact_preserves_explicit_empty_payload() -> None:
    assert repair._compact({"payload": {}, "empty_list": []}) == {"payload": {}}


def test_quarantine_contract_is_preserved_in_all_embedded_manifests() -> None:
    status = "QUARANTINE_BLIND_POSTSEAL_MUTATION"
    front = repair._repair_front_matter(
        {"bundle_status": status, "brain_eligible": False},
        episode_id="EP-1",
        trade_date="2024-10-23",
        available_from="2024-10-24T00:00:00+09:00",
        record_count=2,
        training_count=0,
        quarantine_status=status,
    )
    manifest = repair._bundle_manifest(
        {},
        episode_id="EP-1",
        created_at=None,
        record_count=2,
        training_count=0,
        block_payloads={"brain_delta.jsonl": "{}"},
        quarantine_status=status,
    )
    contract = repair._direct_ingest_contract(
        episode_id="EP-1",
        record_count=2,
        training_count=0,
        sample_weight_summary={
            "status": "PASS",
            "issuer_day_weight_sum_mismatches": 0,
            "direct_event_weight_sum_mismatches": 0,
        },
        quarantine_status=status,
    )

    for value in (front, manifest, contract):
        assert value["bundle_status"] == status
        assert value["brain_eligible"] is False
        assert value["direct_brain_ingest_ready"] is False
        assert value["brain_ingest_blocked"] is True
    assert contract["fatal_blockers"] == ["SOURCE_DECLARED_QUARANTINE"]


def test_direct_repair_quarantine_guard_accepts_declared_blind_invalid_tuple() -> None:
    assert (
        repair._declared_quarantine_status(
            {
                "blind_valid": False,
                "brain_eligible": False,
                "outcome_research_performed": True,
            }
        )
        == "QUARANTINE_DECLARED_BLIND_INVALID"
    )


def test_source_reference_aliases_use_only_unambiguous_ledger_suffixes() -> None:
    rows = [
        {"source_id": "SRC-NEWS-000001"},
        {"source_id": "SRC-NEWS-000002"},
        {"source_id": "SRC-NEWS-ROW-000002"},
    ]

    aliases = repair._source_reference_aliases(rows)

    assert aliases["SRC-000001"] == "SRC-NEWS-000001"
    assert "SRC-000002" not in aliases

    payload = {
        "source_id": "SRC-000001",
        "nested": ["SRC-000001", "SRC-000002"],
    }
    normalized = repair._normalize_source_reference_aliases(payload, aliases)

    assert normalized == {
        "source_id": "SRC-NEWS-000001",
        "nested": ["SRC-000001", "SRC-000002"],
    }


def test_source_reference_aliases_resolve_legacy_news_row_id() -> None:
    aliases = repair._source_reference_aliases(
        [{"source_id": "SRC-000038"}],
        reference_rows=[{"source_id": "SRC-000038", "row_id": "NEWS-000038"}],
    )

    assert aliases["NEWS-000038"] == "SRC-000038"
    assert repair._normalize_source_reference_aliases(
        {"source_row_id": "NEWS-000038"},
        aliases,
    ) == {"source_row_id": "NEWS-000038"}
    assert repair._normalize_source_reference_aliases(
        {"row_id": "NEWS-000038", "source_row_id": "NEWS-000038"},
        aliases,
    ) == {"row_id": "NEWS-000038", "source_row_id": "NEWS-000038"}


def test_source_reference_aliases_include_input_row_id_lists() -> None:
    aliases = repair._source_reference_aliases(
        [{"source_id": "SRC-000528", "input_row_ids": ["ROW-000528"]}],
    )

    assert aliases["ROW-000528"] == "SRC-000528"


def test_outcome_identity_aliases_use_unique_ticker_mapping() -> None:
    blocks = {
        "outcome_ledger.jsonl": [
            {"outcome_ledger_id": "OUT-20200925-002131", "ticker": "298690"}
        ],
        "outcome_leader_census.jsonl": [
            {"outcome_id": "OUT-002131", "outcome_leader_id": "LEAD-000001", "ticker": "298690"}
        ],
        "outcome_to_news_audit.jsonl": [
            {"outcome_leader_id": "LEAD-20200925-0001", "ticker": "298690"}
        ],
    }

    repair._normalize_outcome_identity_aliases(blocks)

    assert blocks["outcome_leader_census.jsonl"][0]["outcome_id"] == "OUT-20200925-002131"
    assert blocks["outcome_to_news_audit.jsonl"][0]["outcome_leader_id"] == "LEAD-000001"


def test_outcome_identity_aliases_skip_ambiguous_ticker() -> None:
    blocks = {
        "outcome_ledger.jsonl": [
            {"outcome_ledger_id": "OUT-20200925-1", "ticker": "298690"},
            {"outcome_ledger_id": "OUT-20200925-2", "ticker": "298690"},
        ],
        "outcome_leader_census.jsonl": [
            {"outcome_id": "OUT-1", "outcome_leader_id": "LEAD-000001", "ticker": "298690"}
        ],
        "outcome_to_news_audit.jsonl": [
            {"outcome_leader_id": "LEAD-20200925-0001", "ticker": "298690"}
        ],
    }

    repair._normalize_outcome_identity_aliases(blocks)

    assert blocks["outcome_leader_census.jsonl"][0]["outcome_id"] == "OUT-1"
    assert blocks["outcome_to_news_audit.jsonl"][0]["outcome_leader_id"] == "LEAD-000001"


def test_outcome_identity_aliases_preserve_distinct_auxiliary_leader_id() -> None:
    blocks = {
        "outcome_ledger.jsonl": [
            {"outcome_id": "OUT-1", "ticker": "298690"}
        ],
        "outcome_leader_census.jsonl": [
            {
                "outcome_id": "OUT-1",
                "outcome_leader_id": "OL-000001",
                "ticker": "298690",
            }
        ],
        "outcome_to_news_audit.jsonl": [
            {
                "leader_id": "LEAD-000001",
                "outcome_leader_id": "OL-000001",
                "ticker": "298690",
            }
        ],
    }

    repair._normalize_outcome_identity_aliases(blocks)

    audit = blocks["outcome_to_news_audit.jsonl"][0]
    assert audit["leader_id"] == "LEAD-000001"
    assert audit["outcome_leader_id"] == "OL-000001"


def test_material_review_mrev_alias_requires_existing_mrv_row() -> None:
    aliases = repair._material_review_reference_aliases(
        [{"material_review_id": "MRV-000001"}],
    )

    assert aliases == {"MREV-000001": "MRV-000001"}
    assert repair._normalize_source_reference_aliases(
        {"source_material_review_ids": ["MREV-000001", "MREV-000002"]},
        aliases,
    ) == {"source_material_review_ids": ["MRV-000001", "MREV-000002"]}


def test_material_review_queue_fk_prevents_duplicate_derived_review() -> None:
    blocks = {
        "material_review_queue.jsonl": [
            {
                "material_review_queue_id": "MRQ-000004",
                "material_review_id": "MR-000004",
                "source_id": "NEWS-000004",
                "decision": "MATERIAL",
            }
        ],
        "material_review.jsonl": [
            {
                "material_review_id": "MR-000004",
                "material_review_queue_id": "MRQ-000004",
                "source_id": "SRC-000004",
                "decision": "MATERIAL",
            }
        ],
    }

    repair._materialize_missing_material_review_rows(blocks)

    assert len(blocks["material_review.jsonl"]) == 1


def test_material_review_queue_without_existing_fk_is_derived_once() -> None:
    blocks = {
        "material_review_queue.jsonl": [
            {
                "material_review_queue_id": "MRQ-000005",
                "material_review_id": "MR-000005",
                "source_id": "NEWS-000005",
                "decision": "MATERIAL",
            }
        ],
        "material_review.jsonl": [],
    }

    repair._materialize_missing_material_review_rows(blocks)

    assert len(blocks["material_review.jsonl"]) == 1
    assert blocks["material_review.jsonl"][0]["repair_derived_from_queue"] is True


def test_namespace_record_identity_makes_record_id_global() -> None:
    record = {
        "record_id": "BD-000001",
        "brain_delta_id": "BD-000001",
        "case_id": "BD-000001",
        "payload": {"record_id": "BD-000001"},
    }

    repair._namespace_record_identity(record, episode_id="NSLAB-20241218-abc")

    expected = "NSLAB-20241218-abc__BD-000001"
    assert record["record_id"] == expected
    assert record["brain_delta_id"] == expected
    assert record["case_id"] == expected
    assert record["payload"]["record_id"] == expected


def test_missing_episode_id_gets_content_addressed_namespace(tmp_path: Path) -> None:
    source = tmp_path / "legacy-bundle.md"
    source.write_bytes(b"legacy bundle bytes")

    first = repair._derive_episode_id(source, "2021-03-30")
    second = repair._derive_episode_id(source, "2021-03-30")

    assert first == second
    assert first.startswith("NSLAB-20210330-")
    assert len(first.rsplit("-", 1)[-1]) == 12


def test_source_ids_can_be_derived_from_fact_and_inference_ledgers() -> None:
    fact_sources = {
        "FACT-1": ["NEWS-1"],
        "FACT-2": ["NEWS-2"],
    }
    inference_facts = {"INF-1": ["FACT-2"]}

    source_ids = repair._source_ids_from_fact_inference(
        ["FACT-1"],
        ["INF-1"],
        fact_source_ids_by_id=fact_sources,
        inference_fact_ids_by_id=inference_facts,
        known_source_ids={"NEWS-1", "NEWS-2"},
    )

    assert source_ids == ["NEWS-1", "NEWS-2"]


def test_ledger_rows_include_postmortem_variants_without_duplicates() -> None:
    blocks = {
        "fact_ledger_blind.jsonl": [{"fact_id": "FACT-1"}],
        "fact_ledger_postmortem.jsonl": [
            {"fact_id": "FACT-POST-1"},
            {"fact_id": "FACT-1"},
        ],
        "postmortem_fact_ledger.jsonl": [{"fact_id": "PFACT-1"}],
        "inference_ledger_blind.jsonl": [{"inference_id": "INF-1"}],
        "inference_ledger_postmortem.jsonl": [{"inference_id": "INF-POST-1"}],
        "postmortem_inference_ledger.jsonl": [{"inference_id": "PINF-1"}],
    }

    assert [row["fact_id"] for row in repair._ledger_rows(blocks, "fact_ledger")] == [
        "FACT-1",
        "FACT-POST-1",
        "PFACT-1",
    ]
    assert [
        row["inference_id"]
        for row in repair._ledger_rows(blocks, "inference_ledger")
    ] == ["INF-1", "INF-POST-1", "PINF-1"]


def test_unknown_postmortem_reference_tokens_are_not_typed_import_refs() -> None:
    row = {
        "record_id": "BD-1",
        "record_type": "candidate_generation_error_case",
        "episode_id": "OLD-EPISODE",
        "trade_date": "2018-03-27",
        "ticker": "000001",
        "company_name": "Issuer",
        "training_eligible": True,
        "sample_weight": 1.0,
        "provenance_source_ids": ["SRC-NEWS-1"],
        "source_fact_ids": ["FACT-1", "PMFACT-OUTCOME-1"],
        "source_inference_ids": ["INF-1", "PMINF-AUDIT-1"],
        "payload": {
            "source_fact_ids": ["FACT-1", "PMFACT-OUTCOME-1"],
            "source_inference_ids": ["INF-1", "PMINF-AUDIT-1"],
        },
    }

    repaired = repair._existing_direct_ingest_case(
        row,
        index=1,
        episode_id="NEW-EPISODE",
        trade_date="2018-03-27",
        available_from="2018-03-28T00:00:00+09:00",
        known_source_ids={"SRC-NEWS-1"},
        source_rows_by_id={"SRC-NEWS-1": {"source_type": "news_csv_row"}},
        known_fact_ids={"FACT-1"},
        known_inference_ids={"INF-1"},
        fact_source_ids_by_id={"FACT-1": ["SRC-NEWS-1"]},
        inference_fact_ids_by_id={"INF-1": ["FACT-1"]},
    )

    assert repaired["source_fact_ids"] == ["FACT-1"]
    assert repaired["source_inference_ids"] == ["INF-1"]
    assert repaired["payload"]["source_fact_ids"] == ["FACT-1"]
    assert repaired["payload"]["source_inference_ids"] == ["INF-1"]
    assert repaired["legacy_unresolved_fact_tokens"] == ["PMFACT-OUTCOME-1"]
    assert repaired["legacy_unresolved_inference_tokens"] == ["PMINF-AUDIT-1"]
    assert repaired["training_eligible"] is True


def test_unresolved_source_reference_downgrades_placeholder_only_training() -> None:
    record = {
        "record_id": "BD-SOURCE-1",
        "record_type": "context_market_state_or_fact_case",
        "training_eligible": True,
        "sample_weight": 1.0,
        "provenance_source_ids": ["SRC-PRIOR-DAY"],
    }

    repair._downgrade_unresolved_reference_training(
        record,
        reason="unresolved_provenance_source_reference",
    )

    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == (
        "unresolved_provenance_source_reference"
    )


def test_eligible_legacy_record_without_weight_gets_deterministic_default() -> None:
    row = {
        "record_id": "BD-WEIGHT-1",
        "record_type": "negative_control_case",
        "trade_date": "2018-03-27",
        "ticker": "000001",
        "company_name": "Issuer",
        "training_eligible": True,
        "provenance_source_ids": ["SRC-NEWS-1"],
        "payload": {},
    }

    repaired = repair._existing_direct_ingest_case(
        row,
        index=1,
        episode_id="EPISODE",
        trade_date="2018-03-27",
        available_from="2018-03-28T00:00:00+09:00",
        known_source_ids={"SRC-NEWS-1"},
        source_rows_by_id={"SRC-NEWS-1": {"source_type": "news_csv_row"}},
        known_fact_ids=set(),
        known_inference_ids=set(),
        fact_source_ids_by_id={},
        inference_fact_ids_by_id={},
    )

    assert repaired["training_eligible"] is True
    assert repaired["sample_weight"] == 1.0


def test_single_related_ticker_alias_closes_direct_event_weight_group() -> None:
    row = {
        "record_id": "BD-DIRECT-1",
        "record_type": "supervised_direct_event_case",
        "trade_date": "2018-08-21",
        "related_tickers": ["000100"],
        "training_eligible": True,
        "sample_weight": 0.5,
        "provenance_source_ids": ["SRC-NEWS-1"],
        "payload": {"direct_event_case_id": "DEC-1"},
    }

    repaired = repair._existing_direct_ingest_case(
        row,
        index=1,
        episode_id="EPISODE",
        trade_date="2018-08-21",
        available_from="2018-08-22T00:00:00+09:00",
        known_source_ids={"SRC-NEWS-1"},
        source_rows_by_id={"SRC-NEWS-1": {"source_type": "news_csv_row"}},
        known_fact_ids=set(),
        known_inference_ids=set(),
        fact_source_ids_by_id={},
        inference_fact_ids_by_id={},
    )

    assert repaired["ticker"] == "000100"


def test_empty_typed_reference_lists_are_preserved() -> None:
    value = {
        "correction_clause_support": [
            {"supported_by_fact_ids": []},
        ],
    }

    repair._sanitize_unknown_typed_references(
        value,
        known_fact_ids={"FACT-1"},
        known_inference_ids=set(),
    )

    assert value == {"correction_clause_support": [{"supported_by_fact_ids": []}]}


def test_repair_derivation_metadata_preserves_legacy_fact_tokens() -> None:
    value = {
        "repair_population_derivations": [
            {
                "join_values": {"fact_id": ["PMFACT-1"]},
                "derivation_inputs": ["PMFACT-1", "PMINF-1"],
            }
        ]
    }

    repair._sanitize_unknown_typed_references(
        value,
        known_fact_ids=set(),
        known_inference_ids=set(),
    )

    assert value["repair_population_derivations"][0]["join_values"] == {
        "fact_id": ["PMFACT-1"]
    }
    assert value["repair_population_derivations"][0]["derivation_inputs"] == [
        "PMFACT-1",
        "PMINF-1",
    ]


def test_case_population_aliases_cover_aggregate_and_theme_miss_records() -> None:
    assert "supervised_direct_event_case" in repair._case_population_record_types(
        "issuer_day_cases.jsonl",
        {},
        canonical_record_type="supervised_issuer_day_case",
    )
    assert "candidate_generation_error_case" in repair._case_population_record_types(
        "beneficiary_discovery_cases.jsonl",
        {"discovery_type": "SEALED_THEME_MEMBER_NOT_GENERATED"},
        canonical_record_type="beneficiary_discovery_case",
    )


def test_unresolved_postmortem_only_reference_downgrades_price_only_training() -> None:
    row = {
        "record_id": "BD-2",
        "record_type": "newsless_or_unexplained_case",
        "trade_date": "2018-03-27",
        "ticker": "000002",
        "company_name": "Issuer",
        "training_eligible": True,
        "sample_weight": 1.0,
        "provenance_source_ids": ["SRC-PRICE-1"],
        "source_fact_ids": ["PMFACT-OUTCOME-2"],
        "source_inference_ids": ["PMINF-AUDIT-2"],
        "payload": {},
    }

    repaired = repair._existing_direct_ingest_case(
        row,
        index=2,
        episode_id="EPISODE",
        trade_date="2018-03-27",
        available_from="2018-03-28T00:00:00+09:00",
        known_source_ids={"SRC-PRICE-1"},
        source_rows_by_id={"SRC-PRICE-1": {"source_type": "research_daily_access"}},
        known_fact_ids=set(),
        known_inference_ids=set(),
        fact_source_ids_by_id={},
        inference_fact_ids_by_id={},
    )

    assert repaired["training_eligible"] is False
    assert repaired["sample_weight"] == 0.0
    assert repaired["training_exclusion_reason"] == (
        "unresolved_postmortem_fact_inference_reference"
    )
    assert repaired["legacy_unresolved_fact_tokens"] == ["PMFACT-OUTCOME-2"]
    assert repaired["legacy_unresolved_inference_tokens"] == ["PMINF-AUDIT-2"]


def test_event_ticker_edge_path_type_is_normalized_from_direct_edge_type() -> None:
    record = {
        "record_type": "event_ticker_edge",
        "payload": {"edge_type": "DIRECT_EVENT_SUPPORTS_TICKER"},
    }

    assert repair._event_ticker_edge_path_type(record) == "DIRECT"


def test_event_ticker_edge_relation_class_preserves_detail_and_sets_canonical_enum() -> None:
    record = {
        "record_type": "event_ticker_edge",
        "payload": {"relation_class": "NAMED_ACQUISITION_TARGET"},
    }

    repair._repair_event_ticker_edge_cutoff(record, source_rows_by_id={})

    assert record["relation_class"] == "INFERRED_NEW"
    assert record["payload"]["relation_class"] == "NAMED_ACQUISITION_TARGET"


def test_event_ticker_edge_filters_postmortem_sources_with_provenance() -> None:
    record = {
        "record_type": "event_ticker_edge",
        "source_phase": "POSTMORTEM",
        "training_eligible": True,
        "provenance_source_ids": ["SRC-NEWS-1", "SRC-OUTCOME-1"],
    }

    repair._repair_event_ticker_edge_cutoff(
        record,
        source_rows_by_id={
            "SRC-NEWS-1": {"time_verified": True, "available_before_cutoff": True},
            "SRC-OUTCOME-1": {
                "source_type": "RESEARCH_DAILY_OUTCOME_ROW",
                "available_from": "2030-01-02T00:00:00+09:00",
            },
        },
    )

    assert record["provenance_source_ids"] == ["SRC-NEWS-1"]
    assert record["provenance_source_filter"] == {
        "rule_id": "event_ticker_edge_cutoff_safe_sources.v1",
        "removed_source_ids": ["SRC-OUTCOME-1"],
        "retained_source_ids": ["SRC-NEWS-1"],
    }


def test_candidate_semantic_owner_is_repaired_from_verified_alias_evidence() -> None:
    row = {
        "candidate_id": "CAND-1",
        "candidate_company": "Legacy Issuer",
        "ticker": "000001",
        "source_row_id": "SRC-1",
        "primary_fact_id": "FACT-1",
        "primary_quote": "The issuer signed the acquisition.",
        "semantic_verdict": "PASS",
        "final_eligible_semantic": True,
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
        "primary_quote": "The issuer signed the acquisition.",
        "semantic_verdict": "PASS",
        "issuer_role_anchor_valid": True,
        "local_predicate_owner_is_candidate": True,
        "target_issuer_is_article_subject": True,
    }

    repaired = repair._repair_candidate_semantic_alias_rows(
        [row],
        entity_resolution_rows=[entity],
        final_witness_rows=[final_witness],
    )[0]

    assert repaired["local_predicate_owner_is_candidate"] is True
    assert repaired["target_issuer_is_article_subject"] is True
    assert repaired["semantic_alias_repair_provenance"] == {
        "rule_id": "semantic_owner_from_verified_historical_alias.v1",
        "candidate_id": "CAND-1",
        "source_id": "SRC-1",
        "ticker": "000001",
        "entity_resolution_id": "ER-1",
        "final_evidence_witness_id": "FEW-1",
    }


def test_semantic_primary_fact_uses_unique_declared_candidate_surface() -> None:
    screening = {
        "screening_id": "SCR-1",
        "candidate_id": "CAND-1",
        "company": "Target Company",
        "source_fact_ids": ["FACT-GROUP", "FACT-TARGET"],
    }
    candidate_witness = {
        "screening_id": "SCR-1",
        "candidate_id": "CAND-1",
        "candidate_company": "Target Company",
        "primary_fact_id": "FACT-GROUP",
        "primary_quote": "Peer One and Peer Two rose.",
        "source_row_id": "SRC-GROUP",
    }
    final_witness = {
        "candidate_id": "CAND-1",
        "candidate_company": "Target Company",
        "primary_fact_id": "FACT-GROUP",
        "primary_quote": "Peer One and Peer Two rose.",
        "source_row_id": "SRC-GROUP",
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

    repaired_candidates, repaired_finals = repair._repair_semantic_primary_fact_references(
        [candidate_witness],
        [final_witness],
        screening_rows=[screening],
        fact_rows=facts,
    )

    for repaired in (*repaired_candidates, *repaired_finals):
        assert repaired["primary_fact_id"] == "FACT-TARGET"
        assert repaired["primary_quote"] == facts[1]["exact_quote"]
        assert repaired["source_row_id"] == "SRC-TARGET"
        provenance = repaired["semantic_fact_reference_repair_provenance"]
        assert provenance["prior_primary_fact_id"] == "FACT-GROUP"
        assert provenance["replacement_primary_fact_id"] == "FACT-TARGET"


def test_semantic_primary_fact_is_not_guessed_when_candidate_surface_is_ambiguous() -> None:
    witness = {
        "screening_id": "SCR-1",
        "candidate_id": "CAND-1",
        "candidate_company": "Target Company",
        "primary_fact_id": "FACT-GROUP",
        "primary_quote": "A market group moved.",
        "source_row_id": "SRC-GROUP",
    }
    facts = [
        {
            "fact_id": "FACT-GROUP",
            "source_row_id": "SRC-GROUP",
            "exact_quote": "A market group moved.",
        },
        {
            "fact_id": "FACT-A",
            "source_row_id": "SRC-A",
            "exact_quote": "Target Company was named in one report.",
        },
        {
            "fact_id": "FACT-B",
            "source_row_id": "SRC-B",
            "exact_quote": "Target Company was also named in another report.",
        },
    ]

    repaired, _ = repair._repair_semantic_primary_fact_references(
        [witness],
        [],
        screening_rows=[
            {
                "screening_id": "SCR-1",
                "candidate_id": "CAND-1",
                "company": "Target Company",
                "source_fact_ids": ["FACT-GROUP", "FACT-A", "FACT-B"],
            }
        ],
        fact_rows=facts,
    )

    assert repaired == [witness]


def test_selected_negative_control_source_becomes_known_negative_control() -> None:
    record = {
        "record_type": "selected_negative_control_source",
        "training_eligible": True,
        "payload": {
            "ticker": "000120",
            "name": "CJ대한통운",
            "rejection_reason": "negative/financing overhang",
        },
    }

    repair._standardize_custom_record_type(record, payload=record["payload"])

    assert record["record_type"] == "negative_control_case"
    assert record["legacy_record_type"] == "selected_negative_control_source"
    assert record["training_target"] == "candidate_exclusion_calibration"
    assert record["rejection_or_exclusion_reason"] == "negative/financing overhang"


def test_custom_false_positive_alias_becomes_negative_control() -> None:
    record = {
        "record_id": "BD-FP-1",
        "record_type": "blind_false_positive",
        "training_eligible": True,
        "provenance_source_ids": ["SRC-1"],
        "ticker": "000001",
        "company": "Issuer",
        "error_type": "no_same_day_price_confirmation",
        "D_high_return_pct": 2.8,
    }

    repair._standardize_custom_record_type(record, payload=record)

    assert record["record_type"] == "negative_control_case"
    assert record["legacy_record_type"] == "blind_false_positive"
    assert record["training_target"] == "candidate_exclusion_calibration"
    assert record["rejection_or_exclusion_reason"] == (
        "no_same_day_price_confirmation"
    )
    assert record["outcome_high_return_pct"] == 2.8


def test_custom_pairwise_alias_becomes_ranking_error() -> None:
    record = {
        "record_id": "BD-PAIR-1",
        "record_type": "pairwise_rank_delta",
        "training_eligible": True,
        "provenance_source_ids": ["SRC-1"],
        "winner_ticker": "000002",
        "winner_company": "Winner",
        "winner_high_return_pct": 12.5,
        "comparison_note": "winner was omitted from the final watchlist",
    }

    repair._standardize_custom_record_type(record, payload=record)

    assert record["record_type"] == "ranking_error_case"
    assert record["legacy_record_type"] == "pairwise_rank_delta"
    assert record["training_target"] == "candidate_ranking_correction"
    assert record["corrected_ticker"] == "000002"
    assert record["corrected_company_name"] == "Winner"
    assert record["outcome_high_return_pct"] == 12.5


def test_missed_outcome_leader_uses_audit_decision_alias() -> None:
    record = {
        "record_id": "BD-MISS-1",
        "record_type": "missed_outcome_leader",
        "training_eligible": True,
        "audit_decision": "NEWSLESS_OR_THEME_ONLY_LEADER",
        "ticker": "000003",
        "company": "Newsless Issuer",
        "D_high_return_pct": 29.9,
    }

    repair._standardize_custom_record_type(record, payload=record)

    assert record["record_type"] == "newsless_or_unexplained_case"
    assert record["legacy_record_type"] == "missed_outcome_leader"
    assert record["training_target"] == "newsless_outcome_calibration"
    assert record["no_catalyst_asserted"] is True


def test_missed_outcome_leader_does_not_invent_correction_mode() -> None:
    record = {
        "record_id": "BD-MISS-2",
        "record_type": "MISSED_OUTCOME_LEADER",
        "training_eligible": True,
        "label": "MISSED_NO_PREMARKET_EVIDENCE",
        "ticker": "000004",
        "company": "Unexplained Issuer",
    }

    repair._standardize_custom_record_type(record, payload=record)

    assert record["record_type"] == "candidate_generation_error_case"
    assert record["error_type"] == "MISSED_NO_PREMARKET_EVIDENCE"
    assert record["missed_ticker"] == "000004"
    assert record["missed_company_name"] == "Unexplained Issuer"
    assert "correction_mode" not in record


@pytest.mark.parametrize(
    ("source_type", "expected_type", "expected_target"),
    [
        ("FINAL_CANDIDATE_OUTCOME", "supervised_issuer_day_case", "issuer_day_price_response"),
        ("CUTLINE_EXCLUSION_OUTCOME", "negative_control_case", "candidate_exclusion_calibration"),
        ("SEMANTIC_GUARD_CASE", "negative_control_case", "candidate_exclusion_calibration"),
    ],
)
def test_runner_record_type_aliases_are_importable_without_new_judgment(
    source_type: str,
    expected_type: str,
    expected_target: str,
) -> None:
    record = {
        "record_id": f"BD-{source_type}",
        "record_type": source_type,
        "training_eligible": True,
        "source_ids": ["SRC-1"],
        "ticker": "000005",
        "company": "Source Company",
        "label": "SOURCE_LABEL",
        "error_mode": "SOURCE_MODE",
    }

    repair._standardize_custom_record_type(record, payload=record)

    assert record["record_type"] == expected_type
    assert record["legacy_record_type"] == source_type
    assert record["training_target"] == expected_target
    assert record["label"] == "SOURCE_LABEL"
    assert record["error_mode"] == "SOURCE_MODE"


def test_outcome_leader_reverse_audit_keeps_unrecognized_audit_classification() -> None:
    payload = {
        "classification": "NO_CUTOFF_NEWS_MATCH",
        "leader_id": "LEAD-1",
        "high_return_pct": 10.6,
    }
    record = {
        "brain_record_id": "BD-OTN-1",
        "record_type": "outcome_leader_reverse_audit_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "payload": payload,
    }

    repair._standardize_custom_record_type(record, payload=payload)

    assert record["record_type"] == "outcome_leader_reverse_audit_case"
    assert record["legacy_record_type"] == "outcome_leader_reverse_audit_case"
    assert "lesson" not in record
    assert record["payload"] == payload


def test_rankable_candidate_case_is_preserved_but_not_exportable() -> None:
    record = {
        "record_type": "rankable_candidate_case",
        "training_eligible": True,
        "sample_weight": 1.0,
        "payload": {"ranking_audit": {"selected_final": True}},
    }

    repair._standardize_custom_record_type(record, payload=record["payload"])

    assert record["record_type"] == "rankable_candidate_case"
    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == "rankable_candidate_audit_not_training_type"


def test_training_record_without_provenance_is_preserved_but_not_exportable() -> None:
    record = {
        "record_id": "BD-1",
        "record_type": "candidate_generation_error_case",
        "training_eligible": True,
        "sample_weight": 1.0,
    }

    repair._drop_training_without_provenance(record)

    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == "missing_provenance_source_ids"


def test_ineligible_legacy_record_gets_explicit_downstream_metadata() -> None:
    record = {
        "record_id": "BD-INELIGIBLE",
        "record_type": "negative_control_case",
        "training_eligible": False,
        "sample_weight": 1.0,
        "eligibility_reason": "legacy exclusion",
    }

    repair._normalize_ineligible_training_metadata([record])

    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == "source_declared_ineligible_without_reason"
    assert record["eligibility_reason"] == (
        "legacy exclusion; source_declared_ineligible_without_reason"
    )


def test_unsealed_preference_pair_is_preserved_but_not_exportable() -> None:
    record = {
        "record_id": "BD-PAIR",
        "record_type": "blind_leader_preference_pair",
        "training_eligible": True,
        "sample_weight": 1.0,
    }

    repair._drop_unsealed_preference_pair(record)

    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == "sealed_preference_pair_missing"


def test_selected_comparator_shape_is_a_sealed_preference_pair() -> None:
    record = {
        "record_type": "blind_leader_preference_pair",
        "training_eligible": True,
        "sample_weight": 1.0,
        "payload": {
            "blind_preference": "selected",
            "selected_candidate_id": "CAND-SELECTED",
            "comparator_candidate_id": "CAND-COMPARATOR",
        },
    }

    repair._drop_unsealed_preference_pair(record)

    assert record["training_eligible"] is True
    assert record["sample_weight"] == 1.0
    assert "training_exclusion_reason" not in record


def test_blind_selected_ticker_shape_is_a_sealed_preference_pair() -> None:
    record = {
        "record_type": "blind_leader_preference_pair",
        "training_eligible": True,
        "sample_weight": 1.0,
        "payload": {
            "blind_selected_ticker": "000001",
            "comparator_ticker": "000002",
        },
    }

    repair._drop_unsealed_preference_pair(record)

    assert record["training_eligible"] is True
    assert record["sample_weight"] == 1.0


def test_left_right_preference_alias_is_a_sealed_preference_pair() -> None:
    record = {
        "record_type": "blind_leader_preference_pair",
        "training_eligible": True,
        "sample_weight": 1.0,
        "payload": {
            "blind_preference": "LEFT",
            "left_ticker": "000001",
            "right_ticker": "000002",
        },
    }

    repair._drop_unsealed_preference_pair(record)

    assert record["training_eligible"] is True
    assert record["sample_weight"] == 1.0


def test_preferred_comparison_ticker_shape_is_a_sealed_preference_pair() -> None:
    record = {
        "record_type": "blind_leader_preference_pair",
        "training_eligible": True,
        "sample_weight": 1.0,
        "payload": {
            "sealed_pair_id": "PAIR-1",
            "preferred_ticker": "000001",
            "comparison_ticker": "000002",
        },
    }

    repair._drop_unsealed_preference_pair(record)

    assert record["training_eligible"] is True
    assert record["sample_weight"] == 1.0


def test_canonical_theme_record_keeps_existing_training_target() -> None:
    record = {
        "record_type": "theme_formation_case",
        "training_target": "theme_formation_in_sealed_universe",
        "payload": {},
    }

    repair._standardize_custom_record_type(record, payload=record["payload"])

    assert record["training_target"] == "theme_formation_in_sealed_universe"


def test_nested_research_question_fields_are_promoted_without_payload_loss() -> None:
    nested_question = {
        "question_id": "RQ-1",
        "question": "Which signal should be measured next?",
        "priority": "HIGH",
        "source_phase": "POSTMORTEM",
    }
    record = {
        "record_type": "research_question",
        "payload": {"question": nested_question},
    }

    repair._normalize_known_record_scalar_types(
        record,
        payload=record["payload"],
    )

    assert record["question_id"] == "RQ-1"
    assert record["question"] == "Which signal should be measured next?"
    assert record["priority"] == "HIGH"
    assert record["payload"]["question"] == nested_question


def test_nested_label_quality_is_promoted_and_canonicalized() -> None:
    record = {
        "record_type": "supervised_issuer_day_case",
        "payload": {"label_quality": "VERIFIED"},
    }

    repair._normalize_known_record_scalar_types(
        record,
        payload=record["payload"],
    )

    assert record["label_quality"] == "verified"
    assert record["payload"]["label_quality"] == "VERIFIED"


def test_missing_outcome_label_is_excluded_without_dropping_audit_record() -> None:
    record = {
        "record_type": "negative_control_case",
        "training_eligible": True,
        "sample_weight": 1.0,
        "payload": {"label_quality": "missing", "negative_control_type": "CORRECT_SEMANTIC_REJECTION"},
    }

    excluded = repair._exclude_unverifiable_outcome_training_records([record])

    assert excluded == 1
    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == "outcome_label_quality_unverified"


def test_outcome_only_context_is_kept_as_zero_weight_memory() -> None:
    record = {
        "record_type": "newsless_or_unexplained_case",
        "training_eligible": True,
        "sample_weight": 0.5,
        "provenance_source_ids": ["SRC-OUTCOME"],
    }

    excluded = repair._exclude_outcome_only_training_records(
        [record],
        source_rows_by_id={
            "SRC-OUTCOME": {
                "source_type": "RESEARCH_DAILY_OUTCOME_SNAPSHOT",
            }
        },
    )

    assert excluded == 1
    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == "outcome_only_or_nonstrong_label"


def test_outcome_only_context_accepts_row_source_alias() -> None:
    record = {
        "record_type": "newsless_or_unexplained_case",
        "training_eligible": True,
        "sample_weight": 0.5,
        "provenance_source_ids": ["SRC-OUTCOME-ROW"],
    }

    excluded = repair._exclude_outcome_only_training_records(
        [record],
        source_rows_by_id={
            "SRC-OUTCOME-ROW": {
                "source_type": "RESEARCH_DAILY_OUTCOME_SNAPSHOT_ROW",
            }
        },
    )

    assert excluded == 1
    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == "outcome_only_or_nonstrong_label"


def test_outcome_only_issuer_case_is_zero_weight_memory() -> None:
    record = {
        "record_type": "supervised_issuer_day_case",
        "training_eligible": True,
        "sample_weight": 1.0,
        "provenance_source_ids": ["SRC-OUTCOME"],
        "payload": {"case_label": "OUTCOME_LEADER_MISS"},
    }

    excluded = repair._exclude_outcome_only_training_records(
        [record],
        source_rows_by_id={
            "SRC-OUTCOME": {
                "source_type": "RESEARCH_DAILY_OUTCOME_SNAPSHOT",
            }
        },
    )

    assert excluded == 1
    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == "outcome_only_or_nonstrong_label"


def test_retrospective_theme_with_upgrade_guard_is_zero_weight_memory() -> None:
    record = {
        "record_type": "theme_formation_case",
        "source_phase": "RETROSPECTIVE_DISCOVERY",
        "training_eligible": True,
        "sample_weight": 1.0,
        "payload": {"blind_hit_upgrade_prohibited": True},
        "provenance_source_ids": ["SRC-OUTCOME"],
    }

    excluded = repair._exclude_outcome_only_training_records(
        [record],
        source_rows_by_id={
            "SRC-OUTCOME": {
                "source_type": "RESEARCH_DAILY_OUTCOME_SNAPSHOT",
            }
        },
    )

    assert excluded == 1
    assert record["training_eligible"] is False
    assert record["sample_weight"] == 0.0
    assert record["training_exclusion_reason"] == "outcome_only_or_nonstrong_label"


@pytest.mark.parametrize(
    ("record_type", "training_target", "payload"),
    [
        (
            "mechanism_memory",
            "mechanism_memory_update",
            {
                "mechanism_id": "MECH-1",
                "mechanism": "A preserved semantic mechanism",
                "supporting_record_ids": ["BD-1"],
            },
        ),
        (
            "supervised_theme_formation_case",
            "theme_peer_selection",
            {
                "theme_id": "THEME-1",
                "peer_universe": ["CAND-1", "CAND-2"],
                "chosen_leader_ticker": "CAND-1",
                "D_outcome": {"response_class": "positive"},
            },
        ),
    ],
)
def test_known_record_types_keep_type_target_and_semantic_payload(
    record_type: str,
    training_target: str,
    payload: dict[str, object],
) -> None:
    record = {
        "record_type": record_type,
        "training_target": training_target,
        "payload": payload,
    }

    repair._standardize_custom_record_type(record, payload=payload)

    assert record["record_type"] == record_type
    assert record["training_target"] == training_target
    assert record["payload"] == payload
    assert "legacy_record_type" not in record


def test_unknown_theme_like_record_type_is_not_guessed_into_a_canonical_type() -> None:
    payload = {"mechanism": "A future first-class semantic payload"}
    record = {
        "record_type": "future_theme_mechanism_record",
        "training_target": "future_theme_target",
        "payload": payload,
    }

    repair._standardize_custom_record_type(record, payload=payload)

    assert record["record_type"] == "future_theme_mechanism_record"
    assert record["training_target"] == "future_theme_target"
    assert record["payload"] == payload
    assert "legacy_record_type" not in record


def test_context_entity_name_is_not_promoted_to_company_name() -> None:
    payload = {"entity_name": "EU 재생에너지 패스트트랙"}
    record = {
        "record_type": "context_market_state_or_fact_case",
        "payload": payload,
    }

    repair._standardize_custom_record_type(record, payload=payload)

    assert "company_name" not in record
    assert record["payload"] == payload


def test_declared_theme_legacy_alias_is_still_canonicalized() -> None:
    record = {
        "record_id": "BD-THEME",
        "record_type": "theme_outcome_case",
        "payload": {"lesson": "Preserved lesson"},
    }

    repair._standardize_custom_record_type(record, payload=record["payload"])

    assert record["record_type"] == "theme_formation_case"
    assert record["legacy_record_type"] == "theme_outcome_case"
    assert record["training_target"] == "theme_formation_response"
    assert record["payload"] == {"lesson": "Preserved lesson"}


def test_declared_legacy_alias_keeps_original_spelling() -> None:
    record = {
        "record_id": "BD-THEME",
        "record_type": "THEME_OUTCOME_CASE",
        "payload": {"lesson": "Preserved lesson"},
    }

    repair._standardize_custom_record_type(record, payload=record["payload"])

    assert record["record_type"] == "theme_formation_case"
    assert record["legacy_record_type"] == "THEME_OUTCOME_CASE"


def test_ineligible_known_record_keeps_null_raw_outcome_with_valid_typed_mirror() -> None:
    source = {
        "record_id": "BD-NO-ROW",
        "record_type": "supervised_issuer_day_case",
        "training_eligible": False,
        "sample_weight": 0.0,
        "eligibility_reason": "no_tradable_row",
        "D_outcome": None,
        "payload": {
            "issuer_day_case_id": "IDAY-NO-ROW",
            "D_outcome": None,
            "response_class": "no_tradable_row",
        },
    }

    repaired = repair._existing_direct_ingest_case(
        source,
        index=1,
        episode_id="NSLAB-SOURCE-EPISODE",
        trade_date="2020-01-02",
        available_from="2020-01-03T00:00:00+09:00",
        known_source_ids=set(),
        source_rows_by_id={},
        known_fact_ids=set(),
        known_inference_ids=set(),
        fact_source_ids_by_id={},
        inference_fact_ids_by_id={},
    )

    assert source["D_outcome"] is None
    assert source["payload"]["D_outcome"] is None
    assert repaired["payload"]["D_outcome"] is None
    assert repaired["D_outcome"] == {}

    importer_payload = dict(repaired)
    for key, value in repaired["payload"].items():
        importer_payload.setdefault(key, value)
    SupervisedIssuerDayCase.model_validate(importer_payload)


def test_research_question_numeric_priority_is_canonicalized_to_text() -> None:
    repaired = repair._existing_direct_ingest_case(
        {
            "record_id": "BD-Q-1",
            "record_type": "research_question",
            "training_eligible": False,
            "sample_weight": 0.0,
            "training_exclusion_reason": "research_question_not_training_sample",
            "provenance_source_ids": ["SRC-OUTCOME"],
            "payload": {
                "question": "What should be measured next?",
                "priority": 1,
                "status": "OPEN",
            },
        },
        index=1,
        episode_id="NSLAB-Q-1",
        trade_date="2018-12-10",
        available_from="2018-12-11T00:00:00+09:00",
        known_source_ids={"SRC-OUTCOME"},
        source_rows_by_id={"SRC-OUTCOME": {"source_type": "research_daily_outcome_snapshot"}},
        known_fact_ids=set(),
        known_inference_ids=set(),
        fact_source_ids_by_id={},
        inference_fact_ids_by_id={},
    )

    assert repaired["payload"]["priority"] == "1"


def test_missing_bundle_level_provenance_is_not_fabricated() -> None:
    record = {"blind_rank": 1}

    # A routing/snapshot marker is not evidence for this record.  Repair must
    # preserve the absence and let the eligibility gate quarantine it.
    assert "provenance_source_ids" not in record


def test_case_population_join_accepts_zero_padding_id_alias() -> None:
    case = {
        "beneficiary_discovery_case_id": "BEN-0001",
        "outcome_audit_id": "OUTNEWS-0004",
        "source_fact_ids": ["FACT-00033"],
        "ticker": "056090",
    }
    record = {
        "record_type": "beneficiary_discovery_case",
        "outcome_audit_ids": ["OUTNEWS-000004"],
        "source_fact_ids": ["FACT-00033"],
        "ticker": "056090",
    }
    evidence = repair._case_population_join_evidence(case, record)
    assert evidence is not None
    assert evidence["primary_join_field"] == "outcome_audit_id"


def test_fractional_weights_close_group_to_exact_one() -> None:
    weights = repair._fractional_weights(3)

    assert weights == [0.333333, 0.333333, 0.333334]
    assert sum(weights) == 1.0


def test_cross_record_references_follow_namespaced_record_ids() -> None:
    record = {
        "record_id": "EP__BD-1",
        "supporting_record_ids": ["BD-2"],
        "payload": {"contradicting_record_ids": ["BD-3", "EXTERNAL"]},
    }

    repair._rewrite_cross_record_references(
        record,
        {"BD-1": "EP__BD-1", "BD-2": "EP__BD-2", "BD-3": "EP__BD-3"},
    )

    assert record["supporting_record_ids"] == ["EP__BD-2"]
    assert record["payload"]["contradicting_record_ids"] == ["EP__BD-3", "EXTERNAL"]


def test_semantic_failure_excludes_only_linked_positive_training_record() -> None:
    positive = {
        "record_id": "BD-POSITIVE",
        "record_type": "supervised_issuer_day_case",
        "training_target": "issuer_day_response",
        "candidate_id": "CAND-1",
        "training_eligible": True,
        "sample_weight": 1.0,
    }
    negative = {
        "record_id": "BD-NEGATIVE",
        "record_type": "negative_control_case",
        "training_target": "negative_control",
        "candidate_id": "CAND-1",
        "training_eligible": True,
        "sample_weight": 1.0,
    }

    excluded = repair._exclude_semantically_invalid_training_records(
        [positive, negative],
        semantic_relation_ids={"CAND-1"},
    )

    assert excluded == 1
    assert positive["training_eligible"] is False
    assert positive["sample_weight"] == 0.0
    assert positive["training_exclusion_reason"] == "semantic_contract_failed"
    assert positive["eligibility_reason"] == "semantic_contract_failed"
    assert positive["semantic_exclusion_relation_ids"] == ["CAND-1"]
    assert negative["training_eligible"] is True
    assert negative["sample_weight"] == 1.0


def test_pass_with_semantic_verdict_is_not_laundered_to_pass() -> None:
    row = {
        "semantic_verdict": "PASS_WITH_WARNING",
        "pass": True,
        "fail_reasons": ["witness_missing"],
    }

    repaired = repair._repair_semantic_audit_row(row)

    assert repaired["semantic_verdict"] == "PASS_WITH_WARNING"
    assert repaired.get("semantic_audit_status") != "PASS"


def test_plain_verdict_pass_is_mirrored_to_canonical_semantic_fields() -> None:
    row = {"verdict": "PASS", "fail_reasons": []}

    repaired = repair._repair_semantic_audit_row(row)

    assert repaired["status"] == "PASS"
    assert repaired["semantic_verdict"] == "PASS"
    assert repaired["semantic_audit_status"] == "PASS"


def test_legacy_audit_result_pass_is_mirrored_to_canonical_semantic_fields() -> None:
    row = {
        "audit_result": "PASS",
        "final_evidence_witness_id": "FEW-001",
        "fail_reasons": [],
    }

    repaired = repair._repair_semantic_audit_row(row)

    assert repaired["status"] == "PASS"
    assert repaired["semantic_verdict"] == "PASS"
    assert repaired["semantic_audit_status"] == "PASS"


def test_legacy_semantic_result_pass_is_mirrored_to_canonical_semantic_fields() -> None:
    repaired = repair._repair_semantic_audit_row(
        {
            "semantic_result": "PASS",
            "final_evidence_witness_id": "FEW-001",
            "fail_reasons": [],
        },
    )

    assert repaired["status"] == "PASS"
    assert repaired["semantic_verdict"] == "PASS"
    assert repaired["semantic_audit_status"] == "PASS"


def test_corroborated_legacy_semantic_pass_is_mirrored() -> None:
    row = {
        "semantic_pass": True,
        "article_subject_local_predicate_owner_verified": True,
        "economic_mechanism_supported_verified": True,
        "forbidden_quote_role_detected": False,
        "final_evidence_witness_id": "FEW-1",
    }

    repaired = repair._repair_semantic_audit_row(row)

    assert repaired["semantic_verdict"] == "PASS"
    assert repaired["semantic_audit_status"] == "PASS"


def test_uncorroborated_legacy_semantic_pass_is_not_laundered() -> None:
    repaired = repair._repair_semantic_audit_row({"semantic_pass": True})

    assert "semantic_verdict" not in repaired
    assert "semantic_audit_status" not in repaired


def test_company_memory_missing_known_at_uses_available_from() -> None:
    repaired = repair._existing_direct_ingest_case(
        {
            "record_id": "BD-COMPANY",
            "record_type": "company_memory_delta",
            "training_eligible": False,
            "sample_weight": 0.0,
            "payload": {},
        },
        index=1,
        episode_id="NSLAB-20180404-test",
        trade_date="2018-04-04",
        available_from="2018-04-05T00:00:00+09:00",
        known_source_ids=set(),
        source_rows_by_id={},
        known_fact_ids=set(),
        known_inference_ids=set(),
        fact_source_ids_by_id={},
        inference_fact_ids_by_id={},
    )

    assert repaired["known_at"] == "2018-04-05T00:00:00+09:00"


def test_existing_record_uses_canonical_episode_and_preserves_legacy_id() -> None:
    repaired = repair._existing_direct_ingest_case(
        {
            "record_id": "BD-1",
            "episode_id": "LEGACY-EPISODE",
            "record_type": "negative_control_case",
            "training_eligible": False,
            "sample_weight": 0.0,
            "training_exclusion_reason": "not_training",
            "payload": {},
        },
        index=1,
        episode_id="CANONICAL-EPISODE",
        trade_date="2018-04-04",
        available_from="2018-04-05T00:00:00+09:00",
        known_source_ids=set(),
        source_rows_by_id={},
        known_fact_ids=set(),
        known_inference_ids=set(),
        fact_source_ids_by_id={},
        inference_fact_ids_by_id={},
    )

    assert repaired["episode_id"] == "CANONICAL-EPISODE"
    assert repaired["legacy_source_episode_id"] == "LEGACY-EPISODE"


def test_mistyped_related_event_ids_are_preserved_as_domain_ids() -> None:
    records = [
        {
            "record_id": "BD-1",
            "issuer_day_case_id": "2025-01-16:002210",
            "related_event_ids": [
                "2025-01-16:002210",
                "EVT-REAL",
                "UNKNOWN-REFERENCE",
            ],
        }
    ]

    repair._normalize_mistyped_related_event_references(
        records,
        json_blocks={},
        jsonl_blocks={"event_clusters_blind.jsonl": [{"event_id": "EVT-REAL"}]},
    )

    assert records[0]["related_event_ids"] == ["EVT-REAL", "UNKNOWN-REFERENCE"]
    assert records[0]["related_domain_ids"] == ["2025-01-16:002210"]
    assert records[0]["legacy_mistyped_event_reference_values"] == ["2025-01-16:002210"]


def test_mistyped_selected_blind_event_ids_are_preserved_as_screening_ids() -> None:
    records = [
        {
            "record_id": "BD-1",
            "selected_blind_event_ids": ["SCR-1"],
        }
    ]

    repair._normalize_mistyped_related_event_references(
        records,
        json_blocks={},
        jsonl_blocks={"candidate_screening.jsonl": [{"screening_id": "SCR-1"}]},
    )

    assert records[0]["selected_blind_event_ids"] == []
    assert records[0]["selected_blind_screening_ids"] == ["SCR-1"]
    assert records[0]["related_domain_ids"] == ["SCR-1"]
    assert records[0]["legacy_mistyped_event_reference_values"] == ["SCR-1"]


def test_mistyped_missed_relevant_event_ids_are_preserved_as_domain_ids() -> None:
    records = [
        {
            "record_id": "BD-1",
            "record_type": "candidate_ranking_error_case",
            "missed_more_relevant_event_ids": ["OBS-1"],
        }
    ]
    repair._normalize_mistyped_related_event_references(
        records,
        json_blocks={},
        jsonl_blocks={
            "material_review.jsonl": [{"observation_id": "OBS-1"}],
        },
    )
    assert records[0]["missed_more_relevant_event_ids"] == []
    assert records[0]["missed_more_relevant_domain_ids"] == ["OBS-1"]


def test_nested_screening_ids_are_not_exposed_as_event_references() -> None:
    records = [
        {
            "record_id": "BD-1",
            "payload": {
                "issuer_day_case": {
                    "all_event_ids": ["SCR-1", "EVT-REAL"],
                    "screening_ids": ["SCR-1"],
                }
            },
        }
    ]

    repair._normalize_mistyped_related_event_references(
        records,
        json_blocks={},
        jsonl_blocks={
            "candidate_screening.jsonl": [{"screening_id": "SCR-1"}],
            "event_clusters_blind.jsonl": [{"event_id": "EVT-REAL"}],
        },
    )

    nested = records[0]["payload"]["issuer_day_case"]
    assert nested["all_event_ids"] == ["EVT-REAL"]
    assert nested["screening_ids"] == ["SCR-1"]
    assert records[0]["legacy_mistyped_event_reference_values"] == ["SCR-1"]
    assert records[0]["related_domain_ids"] == ["SCR-1"]


def test_sealed_case_ids_are_not_exposed_as_event_references() -> None:
    records = [
        {
            "record_id": "BD-1",
            "ranking_error_case_id": "REC-1",
            "sealed_event_ids": ["REC-1"],
        }
    ]

    repair._normalize_mistyped_related_event_references(
        records,
        json_blocks={},
        jsonl_blocks={"ranking_error_cases.jsonl": [{"case_id": "REC-1"}]},
    )

    assert records[0]["sealed_event_ids"] == []
    assert records[0]["sealed_domain_ids"] == ["REC-1"]
    assert records[0]["legacy_mistyped_event_reference_values"] == ["REC-1"]


def test_news_row_identity_is_material_news_provenance_without_source_type() -> None:
    assert repair._has_news_source(
        ["NEWS-000034"],
        source_rows_by_id={
            "NEWS-000034": {
                "source_row_id": "NEWS-000034",
                "title": "기사 제목",
                "body": "기사 본문",
            }
        },
    ) is True


def test_outcome_namespace_is_not_material_news_provenance() -> None:
    assert repair._has_news_source(
        ["OUTCOME-000034"],
        source_rows_by_id={
            "OUTCOME-000034": {
                "source_row_id": "OUTCOME-000034",
                "title": "결과",
                "body": "결과 본문",
            }
        },
    ) is False


def test_direct_event_fact_id_is_not_fabricated_as_event() -> None:
    repaired = repair._existing_direct_ingest_case(
        {
            "record_id": "BD-DIRECT",
            "record_type": "supervised_direct_event_case",
            "direct_event_id": "FACT-1",
            "training_eligible": True,
            "sample_weight": 1.0,
            "payload": {},
        },
        index=1,
        episode_id="NSLAB-20180314-test",
        trade_date="2018-03-14",
        available_from="2018-03-15T00:00:00+09:00",
        known_source_ids=set(),
        source_rows_by_id={},
        known_fact_ids={"FACT-1"},
        known_inference_ids=set(),
        fact_source_ids_by_id={},
        inference_fact_ids_by_id={},
    )

    assert "direct_event_id" not in repaired
    assert repaired["direct_event_fact_id"] == "FACT-1"
    assert repaired["legacy_mistyped_event_reference_values"] == ["FACT-1"]
    assert repaired["related_domain_ids"] == ["FACT-1"]


def test_case_population_ids_are_materialized_only_from_unique_leader_join() -> None:
    records = [
        {
            "record_id": "BD-1",
            "record_type": "candidate_generation_error_case",
            "ticker": "000001",
            "trade_date": "2021-03-24",
            "payload": {"outcome_leader_id": "LEAD-1"},
        }
    ]
    blocks = {
        "candidate_generation_error_cases.jsonl": [
            {
                "candidate_generation_error_case_id": "CGE-1",
                "outcome_leader_id": "LEAD-1",
                "ticker": "000001",
                "trade_date": "2021-03-24",
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert records[0]["candidate_generation_error_case_id"] == "CGE-1"
    derivation = records[0]["repair_population_derivations"][0]
    assert derivation["rule_id"] == "case_id_from_unique_evidence_join.v2"
    assert derivation["source_case_id"] == "CGE-1"


def test_case_population_id_is_not_materialized_from_ambiguous_join() -> None:
    records = [
        {
            "record_id": "BD-1",
            "record_type": "ranking_error_case",
            "payload": {"outcome_leader_id": "LEAD-1"},
        }
    ]
    blocks = {
        "ranking_error_cases.jsonl": [
            {"ranking_error_case_id": "RKE-1", "outcome_leader_id": "LEAD-1"},
            {"ranking_error_case_id": "RKE-2", "outcome_leader_id": "LEAD-1"},
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert "ranking_error_case_id" not in records[0]
    assert "repair_population_derivations" not in records[0]


def test_case_population_id_uses_unique_outcome_audit_join() -> None:
    records = [
        {
            "record_id": "BD-1",
            "record_type": "newsless_or_unexplained_case",
            "ticker": "000001",
            "trade_date": "2021-03-24",
            "outcome_audit_ids": ["AUDIT-1"],
            "source_fact_ids": [],
        }
    ]
    blocks = {
        "newsless_or_unexplained_cases.jsonl": [
            {
                "newsless_case_id": "NEWSLESS-1",
                "outcome_audit_id": "AUDIT-1",
                "ticker": "000001",
                "trade_date": "2021-03-24",
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert records[0]["newsless_case_id"] == "NEWSLESS-1"
    derivation = records[0]["repair_population_derivations"][0]
    assert derivation["join_field"] == "outcome_audit_id"
    assert derivation["join_value"] == "AUDIT-1"


def test_ranking_case_prefers_specific_error_record_over_mirror() -> None:
    records = [
        {
            "record_id": "BD-ISSUER",
            "record_type": "supervised_issuer_day_case",
            "ticker": "000001",
            "trade_date": "2021-03-24",
            "outcome_audit_ids": ["AUDIT-1"],
            "source_fact_ids": ["FACT-1"],
        },
        {
            "record_id": "BD-RANKING",
            "record_type": "candidate_ranking_error_case",
            "ticker": "000001",
            "trade_date": "2021-03-24",
            "outcome_audit_ids": ["AUDIT-1"],
            "source_fact_ids": ["FACT-1"],
        },
    ]
    blocks = {
        "ranking_error_cases.jsonl": [
            {
                "ranking_error_case_id": "RERR-1",
                "outcome_audit_id": "AUDIT-1",
                "source_fact_ids": ["FACT-1"],
                "ticker": "000001",
                "trade_date": "2021-03-24",
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert "ranking_error_case_id" not in records[0]
    assert records[1]["ranking_error_case_id"] == "RERR-1"


def test_ranking_case_id_uses_unique_ticker_date_join_when_legacy_case_has_no_fk() -> None:
    records = [
        {
            "record_id": "BD-RANK-TICKER",
            "record_type": "ranking_error_case",
            "ticker": "000660",
            "trade_date": "2020-09-24",
        }
    ]
    blocks = {
        "ranking_error_cases.jsonl": [
            {
                "ranking_error_case_id": "RE-1",
                "classification": "RANKING_MISS",
                "ticker": "000660",
                "trade_date": "2020-09-24",
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert records[0]["ranking_error_case_id"] == "RE-1"
    assert records[0]["repair_population_derivations"][0]["join_field"] == "ticker"


def test_ranking_case_id_does_not_use_ambiguous_ticker_date_join() -> None:
    records = [
        {
            "record_id": "BD-RANK-TICKER",
            "record_type": "ranking_error_case",
            "ticker": "000660",
            "trade_date": "2020-09-24",
        }
    ]
    blocks = {
        "ranking_error_cases.jsonl": [
            {"ranking_error_case_id": "RE-1", "ticker": "000660", "trade_date": "2020-09-24"},
            {"ranking_error_case_id": "RE-2", "ticker": "000660", "trade_date": "2020-09-24"},
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert "ranking_error_case_id" not in records[0]


def test_negative_control_case_id_uses_legacy_screening_join() -> None:
    records = [
        {
            "record_id": "BD-NEG-1",
            "record_type": "row_disposition_error_case",
            "payload": {
                "candidate_id": "CAND-1",
                "source_screening_id": "SCR-1",
            },
        }
    ]
    blocks = {
        "negative_control_cases.jsonl": [
            {
                "negative_control_case_id": "NEG-1",
                "candidate_id": "CAND-1",
                "source_screening_id": "SCR-1",
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert records[0]["negative_control_id"] == "NEG-1"
    derivation = records[0]["repair_population_derivations"][0]
    assert derivation["join_field"] == "candidate_id"
    assert derivation["join_value"] == "CAND-1"


def test_theme_case_id_uses_unique_theme_join() -> None:
    records = [
        {
            "record_id": "BD-THEME-1",
            "record_type": "supervised_theme_formation_case",
            "ticker": "000001",
            "trade_date": "2018-03-09",
            "payload": {"theme_id": "THEME-1"},
        }
    ]
    blocks = {
        "theme_formation_cases.jsonl": [
            {
                "theme_case_id": "TFC-1",
                "theme_id": "THEME-1",
                "ticker": "000001",
                "trade_date": "2018-03-09",
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert records[0]["theme_case_id"] == "TFC-1"
    assert records[0]["repair_population_derivations"][0]["join_field"] == "theme_id"


def test_legacy_case_ids_use_sealed_fact_aliases() -> None:
    records = [
        {
            "record_id": "BD-IDC",
            "record_type": "supervised_issuer_day_case",
            "source_fact_ids": ["FACT-IDC"],
        },
        {
            "record_id": "BD-DEC",
            "record_type": "supervised_direct_event_case",
            "source_fact_ids": ["FACT-DEC"],
        },
        {
            "record_id": "BD-PAIR",
            "record_type": "blind_leader_preference_pair",
            "source_fact_ids": ["FACT-PAIR"],
        },
    ]
    blocks = {
        "issuer_day_cases.jsonl": [{"issuer_day_case_id": "IDC-1", "sealed_fact_ids": ["FACT-IDC"]}],
        "direct_event_cases.jsonl": [{"direct_event_case_id": "DEC-1", "sealed_fact_ids": ["FACT-DEC"]}],
        "blind_leader_preference_pairs.jsonl": [{"pair_id": "PAIR-1", "blind_selected_fact_ids": ["FACT-PAIR"]}],
    }

    repair._materialize_case_population_ids(records, blocks)

    assert records[0]["issuer_day_case_id"] == "IDC-1"
    assert records[1]["direct_event_case_id"] == "DEC-1"
    assert records[2]["pair_id"] == "PAIR-1"


def test_theme_case_id_uses_sealed_source_fact_alias() -> None:
    records = [
        {
            "record_id": "BD-THEME-FACT",
            "record_type": "theme_formation_case",
            "source_fact_ids": ["FACT-THEME"],
        }
    ]
    blocks = {
        "theme_formation_cases.jsonl": [
            {
                "theme_case_id": "THEME-FACT-1",
                "sealed_source_fact_ids": ["FACT-THEME"],
                "theme": "EV_BATTERY_AND_MATERIALS",
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert records[0]["theme_case_id"] == "THEME-FACT-1"
    assert records[0]["repair_population_derivations"][0]["join_field"] == "fact_id"


def test_trigger_event_ids_are_preserved_by_event_placeholders() -> None:
    rows = repair._materialize_referenced_event_placeholders(
        [],
        [{"record_id": "BD-1", "trigger_event_ids": ["EVT-1", "EVT-2"]}],
        trade_date="2018-03-07",
    )

    assert [row["event_id"] for row in rows] == ["EVT-1", "EVT-2"]
    assert all(row["provenance_placeholder"] is True for row in rows)


def test_related_event_ids_are_preserved_by_event_placeholders() -> None:
    rows = repair._materialize_referenced_event_placeholders(
        [],
        [{"record_id": "BD-1", "related_event_ids": ["EVT-RELATED"]}],
        trade_date="2018-03-07",
    )

    assert [row["event_id"] for row in rows] == ["EVT-RELATED"]
    assert rows[0]["provenance_placeholder"] is True


def test_direct_event_ids_are_preserved_by_event_placeholders() -> None:
    rows = repair._materialize_referenced_event_placeholders(
        [],
        [{"record_id": "BD-1", "payload": {"direct_event_id": "DEV-1"}}],
        trade_date="2018-03-07",
    )

    assert [row["event_id"] for row in rows] == ["DEV-1"]
    assert rows[0]["provenance_placeholder"] is True


def test_nested_observation_event_id_is_preserved_by_event_placeholders() -> None:
    rows = repair._materialize_referenced_event_placeholders(
        [],
        [
            {
                "record_id": "BD-1",
                "payload": {
                    "observation": {
                        "event_id": "EVT-NESTED",
                    }
                },
            }
        ],
        trade_date="2018-03-07",
    )

    assert [row["event_id"] for row in rows] == ["EVT-NESTED"]
    assert rows[0]["provenance_placeholder"] is True


def test_numeric_identifier_fields_are_canonicalized_without_metric_coercion() -> None:
    records = [
        {
            "record_type": "context_market_state_or_fact_case",
            "row_id": 762,
            "payload": {"row_id": 762, "amount_rank": 3},
        }
    ]

    repair._normalize_numeric_identifier_fields(records)

    assert records[0]["row_id"] == "762"
    assert records[0]["payload"]["row_id"] == "762"
    assert records[0]["payload"]["amount_rank"] == 3


def test_case_population_id_accepts_legacy_audit_id_alias() -> None:
    records = [
        {
            "record_id": "BD-1",
            "record_type": "newsless_or_unexplained_case",
            "ticker": "000001",
            "payload": {"audit_id": "AUDIT-1"},
        }
    ]
    blocks = {
        "newsless_or_unexplained_cases.jsonl": [
            {
                "newsless_case_id": "NEWSLESS-1",
                "audit_id": "AUDIT-1",
                "ticker": "000001",
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert records[0]["newsless_case_id"] == "NEWSLESS-1"
    assert records[0]["repair_population_derivations"][0]["join_value"] == "AUDIT-1"


def test_beneficiary_case_can_link_to_matching_error_record() -> None:
    records = [
        {
            "record_id": "BD-1",
            "record_type": "candidate_ranking_error_case",
            "ticker": "000001",
            "trade_date": "2021-03-24",
            "outcome_audit_ids": ["AUDIT-1"],
            "source_fact_ids": ["FACT-1"],
            "payload": {"classification": "RANKING_MISS"},
        }
    ]
    blocks = {
        "beneficiary_discovery_cases.jsonl": [
            {
                "beneficiary_case_id": "BEN-1",
                "classification": "RANKING_MISS",
                "outcome_audit_id": "AUDIT-1",
                "ticker": "000001",
                "trade_date": "2021-03-24",
                "matched_fact_ids": ["FACT-1"],
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert records[0]["beneficiary_case_id"] == "BEN-1"
    derivation = records[0]["repair_population_derivations"][0]
    assert derivation["source_artifact"] == "beneficiary_discovery_cases.jsonl"
    assert derivation["record_type_relation"].endswith("->candidate_ranking_error_case")


def test_case_population_id_is_not_materialized_when_fact_evidence_conflicts() -> None:
    records = [
        {
            "record_id": "BD-1",
            "record_type": "beneficiary_discovery_case",
            "ticker": "000001",
            "outcome_audit_ids": ["AUDIT-1"],
            "source_fact_ids": ["FACT-OTHER"],
        }
    ]
    blocks = {
        "beneficiary_discovery_cases.jsonl": [
            {
                "beneficiary_case_id": "BEN-1",
                "outcome_audit_id": "AUDIT-1",
                "ticker": "000001",
                "matched_fact_ids": ["FACT-1"],
            }
        ]
    }

    repair._materialize_case_population_ids(records, blocks)

    assert "beneficiary_case_id" not in records[0]
    assert "repair_population_derivations" not in records[0]


def test_atomic_write_preserves_existing_output_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "bundle.md"
    output.write_text("old", encoding="utf-8")

    def fail_replace(source: object, target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(repair.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        repair._atomic_write_text(output, "new")

    assert output.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob("*.partial")) == []


def test_repair_bundle_refuses_to_overwrite_source(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("original", encoding="utf-8")

    with pytest.raises(ValueError, match="must not overwrite"):
        repair.repair_bundle(source, source)

    assert source.read_text(encoding="utf-8") == "original"


def test_repair_bundle_is_byte_deterministic(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "fixtures" / "research_bundles" / "synthetic_v11_bundle.md"
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"

    repair.repair_bundle(source, first)
    repair.repair_bundle(source, second)

    assert first.read_bytes() == second.read_bytes()
    assert "repaired_at:" not in first.read_text(encoding="utf-8")
    assert "external_quality_gate_required: false" in first.read_text(encoding="utf-8")


def test_available_from_ignores_created_at_from_a_later_run() -> None:
    resolved = repair._resolve_available_from(
        {
            "available_from": "2026-07-11T22:32:10+09:00",
            "created_at": "2026-07-11T22:32:10+09:00",
            "next_trade_date": "2018-03-15",
        },
        {},
        {},
        trade_date="2018-03-14",
    )

    assert resolved == "2018-03-15T00:00:00+09:00"


def test_provenance_closure_expands_facts_referenced_by_inference() -> None:
    rows = [
        {
            "record_id": "BD-1",
            "source_fact_ids": ["FACT-DIRECT"],
            "source_inference_ids": ["INF-1"],
            "resolved_provenance_source_ids": ["SRC-DIRECT"],
        }
    ]
    records = [
        {
            "record_id": "BD-1",
            "record_type": "negative_control_case",
            "training_eligible": True,
            "sample_weight": 1.0,
            "provenance_source_ids": ["SRC-DIRECT"],
            "source_fact_ids": ["FACT-DIRECT"],
            "source_inference_ids": ["INF-1"],
        }
    ]

    repaired = repair._repair_provenance_closure_rows(
        rows,
        records,
        fact_source_ids_by_id={
            "FACT-DIRECT": ["SRC-DIRECT"],
            "FACT-INFERRED": ["SRC-INFERRED"],
        },
        inference_fact_ids_by_id={"INF-1": ["FACT-INFERRED"]},
    )

    assert repaired[0]["source_fact_ids"] == ["FACT-DIRECT", "FACT-INFERRED"]
    assert repaired[0]["resolved_provenance_source_ids"] == [
        "SRC-DIRECT",
        "SRC-INFERRED",
    ]
