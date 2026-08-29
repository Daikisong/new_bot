from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.contracts.quality_evaluation import (
    PairedPredictionManifest,
    PredictionSeal,
    quality_full_profile,
)
from news_scalping_lab.evaluation.quality_reporting import (
    build_quality_score_report,
    render_quality_score,
)
from news_scalping_lab.utils import KST


def _metrics(*, hit: int, citation_rate: float) -> dict[str, object]:
    candidate_tickers = ["000001"] if hit else []
    metrics: dict[str, object] = {
        "recall_at_20": float(hit),
        "precision_at_20": float(hit),
        "mrr": float(hit),
        "brier": 0.2 if hit else 0.8,
        "leader_upper_limit_hit": float(hit),
        "leader_high20_hit": float(hit),
        "calibration_absolute_error": 0.1 if hit else 0.6,
        "calibration_pairs": [{"probability": 0.75, "outcome": float(hit)}],
        "population_brier": 0.2 if hit else 0.8,
        "population_brier_sum": 0.4 if hit else 1.6,
        "population_expected_calibration_error": 0.1 if hit else 0.6,
        "population_calibration_bins": [
            {
                "low": 0.0,
                "high": 1.0,
                "count": 2,
                "mean_probability": 0.5,
                "mean_outcome": 0.5,
            }
        ],
        "population_climatology_brier": 0.25,
        "population_climatology_brier_sum": 0.5,
        "population_brier_skill_vs_climatology": 0.2 if hit else -2.2,
        "population_count": 2,
        "population_positive_count": 1,
        "population_universe_sha256": "e" * 64,
        "population_universe_policy_version": (
            "nslab.brier_excludes_outcome_ineligible_rows.v1"
        ),
        "evaluation_universe_sha256": "d" * 64,
        "evaluation_universe_count": 2,
        "evaluation_universe_policy_version": (
            "nslab.d1_intersection_raw_outcome_eligible_labels.v1"
        ),
        "probability_policy_version": (
            "nslab.confidence_label_unconditional_high20_probability.v1"
        ),
        "selective_top20_brier": 0.2 if hit else None,
        "selective_top20_expected_calibration_error": 0.1 if hit else None,
        "leader_selection_accuracy": float(hit),
        "top_pick_upper_limit_hit": float(hit),
        "top_pick_high20_hit": float(hit),
        "generated_candidate_tickers": candidate_tickers,
        "ranked_candidate_tickers": candidate_tickers,
        "upper_limit_target_tickers": ["000001"],
        "high20_target_tickers": ["000001"],
        "high10_target_tickers": ["000001"],
        "upper_limit_target_count": 1,
        "high20_target_count": 1,
        "high10_target_count": 1,
        "final_memory_citation_rate": citation_rate,
    }
    for cutoff in (5, 10, 20):
        for target in ("upper_limit", "high20", "high10"):
            metrics[f"{target}_hit_count_at_{cutoff}"] = hit
            metrics[f"{target}_recall_at_{cutoff}"] = float(hit)
            metrics[f"{target}_no_positive_false_positive_count_at_{cutoff}"] = 0
        metrics[f"selected_count_at_{cutoff}"] = cutoff
        metrics[f"high20_precision_at_{cutoff}"] = hit / cutoff
        metrics[f"leader_recall_at_{cutoff}"] = float(hit)
        metrics[f"max_return_tie_aware_hit_at_{cutoff}"] = float(hit)
    metrics["outcome_ineligible_selected_count"] = 0
    return metrics


def _paired() -> PairedPredictionManifest:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    reference = {"artifact_path": "sealed/artifact.json", "sha256": "a" * 64}
    common = {
        "case_id": "CASE-1",
        "sealed_at": cutoff,
        "cutoff_at": cutoff,
        "blind_input_manifest": reference,
        "news_sha256": "b" * 64,
        "parsed_news_root_sha256": "6" * 64,
        "shared_context_sha256": "c" * 64,
        "brain_manifest": reference,
        "coverage_manifest": reference,
        "memory_snapshot_id": "MEMIDX-test",
        "d_minus_one_context": {
            "artifact_path": "sealed/d-minus-one.json",
            "sha256": "d" * 64,
        },
        "d_minus_one_context_sha256": "d" * 64,
        "d_minus_one_candidate_universe_root_sha256": "4" * 64,
        "d_minus_one_snapshot_root_sha256": "5" * 64,
        "d_minus_one_source_revision_sha256": "8" * 64,
        "d_minus_one_snapshot_session_date": date(2030, 1, 9),
        "d_minus_one_payload_sha256": "7" * 64,
        "d_minus_one_consumed_payload_sha256": "7" * 64,
        "d_minus_one_projection_policy": (
            "ALL_PRELIMINARY_CANDIDATE_TICKERS_EXACT_SEALED_SUBSET.v1"
        ),
        "d_minus_one_projection_root_sha256": "9" * 64,
        "d_minus_one_projection_requested_ticker_count": 0,
        "d_minus_one_projection_snapshot_count": 0,
        "d_minus_one_projection_missing_ticker_count": 0,
        "candidate_universe_policy_sha256": "e" * 64,
        "final_citation_count": 1,
    }
    seals = [
        PredictionSeal(
            **common,
            variant_id=variant,
            variant_architecture_sha256=(
                "0" if variant == "V0" else "1"
            )
            * 64,
            prediction={
                "artifact_path": f"predictions/{variant}.json",
                "sha256": "f" * 64,
            },
            context_manifest={
                "artifact_path": f"manifests/{variant}.json",
                "sha256": "1" * 64,
            },
        )
        for variant in ("V0", "V1")
    ]
    return PairedPredictionManifest(
        run_id="QPRED-report",
        profile=quality_full_profile(
            provider="codex-oauth",
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
        ),
        blind_selection=reference,
        expected_case_ids=["CASE-1"],
        expected_variant_architecture_sha256={"V0": "0" * 64, "V1": "1" * 64},
        seals=seals,
        paired_case_ids=["CASE-1"],
        all_predictions_sealed=True,
    )


def _observation(variant: str, *, hit: int) -> dict[str, object]:
    cluster = {
        "cluster_id": "CLUSTER-1",
        "trace_id": f"TRACE-{variant}",
        "trace_artifact_path": f"traces/{variant}.json",
        "trace_sha256": ("4" if variant == "V0" else "5") * 64,
        "memory_snapshot_id": "MEMIDX-test",
        "evidence_memo_artifact_path": f"memos/{variant}.jsonl",
        "evidence_memo_sha256": ("6" if variant == "V0" else "7") * 64,
        "searched_record_ids": ["EP-2029__REC-1", "EP-2029__REC-2"],
        "selected_record_ids": ["EP-2029__REC-1", "EP-2029__REC-2"],
        "llm_exposed_record_ids": ["EP-2029__REC-1"],
        "memo_referenced_record_ids": ["EP-2029__REC-1"],
        "stock_cited_record_ids": ["EP-2029__REC-1"],
        "sector_cited_record_ids": [],
        "final_cited_record_ids": ["EP-2029__REC-1"],
        "selected_unused_record_ids": ["EP-2029__REC-2"],
        "offline_unexposed_searched_record_ids": [
            "EP-2029__REC-1",
            "EP-2029__REC-2",
        ],
        "offline_unexposed_selected_record_ids": [
            "EP-2029__REC-1",
            "EP-2029__REC-2",
        ],
        "offline_unexposed_llm_exposed_record_ids": ["EP-2029__REC-1"],
        "offline_unexposed_final_cited_record_ids": ["EP-2029__REC-1"],
        "rare_selected_record_ids": ["EP-2029__REC-2"],
        "independent_unit_ids": ["UNIT-1"],
        "episode_ids": ["EP-2029"],
        "year_counts": {"2029": 2},
        "lane_stage_counts": {
            "POSITIVE_ANALOG": {"searched": 2, "selected": 2, "exposed": 1}
        },
    }
    retrieval = {
        "memory_snapshot_id": "MEMIDX-test",
        "adaptive_trace_count": 1,
        "clusters": [cluster],
        **{
            key: value
            for key, value in cluster.items()
            if key.endswith("_ids")
        },
        "year_counts": {"2029": 2},
        "lane_stage_counts": cluster["lane_stage_counts"],
        "searched_record_occurrence_count": 2,
        "selected_record_occurrence_count": 2,
        "llm_exposed_record_occurrence_count": 1,
        "memo_referenced_record_occurrence_count": 1,
        "stock_cited_record_occurrence_count": 1,
        "sector_cited_record_occurrence_count": 0,
        "final_cited_record_occurrence_count": 1,
        "selected_unused_record_occurrence_count": 1,
    }
    return {
        "case_id": "CASE-1",
        "trade_date": date(2030, 1, 10),
        "variant_id": variant,
        "metrics": _metrics(hit=hit, citation_rate=1.0),
        "retrieval": retrieval,
        "citation_closure": {
            "prediction_memory_record_ids": ["EP-2029__REC-1"],
            "allowed_context_record_ids": [
                "EP-2029__REC-1",
                "EP-2029__REC-2",
            ],
            "runtime_final_cited_record_ids": ["EP-2029__REC-1"],
            "legacy_final_cited_record_ids": [],
            "orphan_record_ids": [],
            "orphan_final_target_ids": [],
            "closure_verified": True,
        },
        "safety": {
            "future_record_count": 0,
            "blind_web_call_count": 0,
            "online_full_scan_count": 0,
            "outcome_reference_count_during_prediction": 0,
            "orphan_citation_count": 0,
            "wrong_snapshot_count": 0,
            "snapshot_closure_verified": True,
            "forbidden_shared_key_count": 0,
            "shared_digest_closure_verified": True,
        },
        "efficiency": {
            "elapsed_accounting_status": "EXACT",
            "elapsed_exact_completed_seconds": 10.0,
            "elapsed_lower_bound_seconds": 10.0,
            "elapsed_upper_bound_seconds": 10.0,
            "contains_recovered_attempts": False,
            "recovered_interrupted_attempt_count": 0,
            "attempt_count": 1,
            "attempt_ledger_sha256": ("8" if variant == "V0" else "9") * 64,
            "runtime_metrics": {
                "logical_llm_call_count": 2,
                "oauth_live_agent_call_count": 1,
                "llm_checkpoint_hit_count": 1,
                "llm_prompt_tokens_estimate": 50,
                "llm_completion_tokens_estimate": 10,
                "llm_retry_count": 0,
                "llm_error_trace_count": 0,
            },
            "runtime_metric_statuses": {
                "logical_llm_call_count": "EXACT",
                "oauth_live_agent_call_count": "EXACT",
                "llm_checkpoint_hit_count": "EXACT",
                "llm_prompt_tokens_estimate": "EXACT",
                "llm_completion_tokens_estimate": "EXACT",
                "llm_retry_count": "EXACT",
                "llm_error_trace_count": "EXACT",
            },
            "runtime_metrics_accounting_status": "EXACT",
        },
        "shared_stage": {
            "attempt_count": 1,
            "build_attempt_count": 1,
            "cache_load_attempt_count": 0,
            "elapsed_accounting_status": "EXACT",
            "elapsed_exact_completed_seconds": 5.0,
            "elapsed_lower_bound_seconds": 5.0,
            "elapsed_upper_bound_seconds": 5.0,
            "contains_recovered_attempts": False,
            "recovered_interrupted_attempt_count": 0,
            "runtime_metrics": {
                "logical_llm_call_count": 3,
                "oauth_live_agent_call_count": 2,
                "llm_checkpoint_hit_count": 1,
                "llm_prompt_tokens_estimate": 100,
                "llm_completion_tokens_estimate": 20,
            },
            "runtime_metric_statuses": {
                "logical_llm_call_count": "EXACT",
                "oauth_live_agent_call_count": "EXACT",
                "llm_checkpoint_hit_count": "EXACT",
                "llm_prompt_tokens_estimate": "EXACT",
                "llm_completion_tokens_estimate": "EXACT",
            },
            "runtime_metrics_accounting_status": "EXACT",
            "attempt_ledger_sha256": "a" * 64,
        },
        "shared_context_sha256": "c" * 64,
        "evaluation_universe_sha256": "d" * 64,
        "evaluation_universe_count": 2,
        "population_universe_sha256": "e" * 64,
        "population_universe_count": 2,
        "market_universe_policy_version": (
            "nslab.d1_intersection_raw_outcome_eligible_labels.v1"
        ),
        "brier_population_policy_version": (
            "nslab.brier_excludes_outcome_ineligible_rows.v1"
        ),
        "probability_policy_version": (
            "nslab.confidence_label_unconditional_high20_probability.v1"
        ),
    }


def test_quality_report_preserves_complete_case_and_metric_surfaces() -> None:
    report = build_quality_score_report(
        paired=_paired(),
        paired_manifest_sha256="2" * 64,
        observations=[
            _observation("V0", hit=0),
            _observation("V1", hit=1),
        ],
        outcome_hashes={"CASE-1": "3" * 64},
    )

    assert report["schema_version"] == "nslab.quality_runtime_score.v4"
    assert len(report["case_observations"]) == 2
    assert report["market_metrics"]["V1"]["upper_limit_recall_at_5"] == 1.0
    assert report["market_metrics"]["V1"]["precision_at_10"] == 0.1
    assert report["retrieval_metrics"]["V1"][
        "offline_unexposed_payload_exposure_rate"
    ] == 0.5
    assert report["citation_metrics"]["V1"]["citation_closure_verified"] is True
    assert report["error_taxonomy"]["V0"][
        "high20_candidate_generation_miss_count"
    ] == 1
    shared = report["shared_stage_accounting"]
    assert shared["observed_shared_once"]["logical_llm_call_count"] == 3
    assert shared["derived_without_dedup"]["logical_llm_call_count"] == 6
    assert report["quality_evaluation_status"] == "PREDICTIVELY_EVALUATED"
    markdown = render_quality_score(report)
    assert "explicitly derived counterfactual" in markdown
    assert "## Error Taxonomy" in markdown


def test_variant_contract_allows_case_local_candidate_policy() -> None:
    first = _paired()
    second_seals = [
        seal.model_copy(
            update={
                "case_id": "CASE-2",
                "candidate_universe_policy_sha256": "9" * 64,
            }
        )
        for seal in first.seals
    ]

    paired = PairedPredictionManifest(
        run_id="QPRED-two-cases",
        profile=first.profile,
        blind_selection=first.blind_selection,
        expected_case_ids=["CASE-1", "CASE-2"],
        expected_variant_architecture_sha256=(
            first.expected_variant_architecture_sha256
        ),
        seals=[*first.seals, *second_seals],
        paired_case_ids=["CASE-1", "CASE-2"],
        all_predictions_sealed=True,
    )

    assert paired.all_predictions_sealed is True


def test_variant_contract_rejects_case_local_v0_v1_policy_drift() -> None:
    paired = _paired()
    drifted = paired.seals[1].model_copy(
        update={"candidate_universe_policy_sha256": "9" * 64}
    )
    with pytest.raises(ValueError, match="candidate universe policy differs"):
        PairedPredictionManifest(
            run_id=paired.run_id,
            profile=paired.profile,
            blind_selection=paired.blind_selection,
            expected_case_ids=paired.expected_case_ids,
            expected_variant_architecture_sha256=(
                paired.expected_variant_architecture_sha256
            ),
            seals=[paired.seals[0], drifted],
            paired_case_ids=["CASE-1"],
            all_predictions_sealed=True,
        )


def test_variant_contract_allows_candidate_derived_projection_differences() -> None:
    paired = _paired()
    drifted_projection = paired.seals[1].model_copy(
        update={
            "d_minus_one_consumed_payload_sha256": "2" * 64,
            "d_minus_one_projection_root_sha256": "3" * 64,
            "d_minus_one_projection_requested_ticker_count": 2,
            "d_minus_one_projection_snapshot_count": 1,
            "d_minus_one_projection_missing_ticker_count": 1,
        }
    )

    observed = PairedPredictionManifest(
        run_id=paired.run_id,
        profile=paired.profile,
        blind_selection=paired.blind_selection,
        expected_case_ids=paired.expected_case_ids,
        expected_variant_architecture_sha256=(
            paired.expected_variant_architecture_sha256
        ),
        seals=[paired.seals[0], drifted_projection],
        paired_case_ids=["CASE-1"],
        all_predictions_sealed=True,
    )

    assert observed.all_predictions_sealed is True
