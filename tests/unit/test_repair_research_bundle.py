from news_scalping_lab.tools import repair_research_bundle as repair


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


def test_event_ticker_edge_path_type_is_normalized_from_direct_edge_type() -> None:
    record = {
        "record_type": "event_ticker_edge",
        "payload": {"edge_type": "DIRECT_EVENT_SUPPORTS_TICKER"},
    }

    assert repair._event_ticker_edge_path_type(record) == "DIRECT"


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


def test_bundle_level_provenance_is_materialized_to_record_level() -> None:
    record = {"blind_rank": 1}

    repair._materialize_bundle_level_provenance(
        record,
        known_source_ids={"SRC-BLIND-SNAPSHOT"},
    )

    assert record["provenance_source_ids"] == ["SRC-BLIND-SNAPSHOT"]


def test_fractional_weights_close_group_to_exact_one() -> None:
    weights = repair._fractional_weights(3)

    assert weights == [0.333333, 0.333333, 0.333334]
    assert sum(weights) == 1.0
