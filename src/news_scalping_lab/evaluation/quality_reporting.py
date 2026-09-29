"""Fail-closed reporting for sealed QUALITY_FULL runtime comparisons."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from news_scalping_lab.contracts.quality_evaluation import PairedPredictionManifest
from news_scalping_lab.evaluation.quality_observations import QualityCaseObservation
from news_scalping_lab.evaluation.runtime_variant_shadow import (
    _aggregate_micro_market_metrics,
    _expected_calibration_error,
    _paired_bootstrap,
)
from news_scalping_lab.utils import canonical_json, now_kst

_VARIANTS = ("V0", "V1")
_CUTOFFS = (5, 10, 20)
_MEAN_MARKET_KEYS = (
    "mrr",
    "population_brier",
    "population_expected_calibration_error",
    "population_brier_skill_vs_climatology",
    "leader_selection_accuracy",
    "top_pick_upper_limit_hit",
    "top_pick_high20_hit",
    "selective_top20_brier",
    "selective_top20_expected_calibration_error",
    "final_memory_citation_rate",
    *(f"upper_limit_recall_at_{cutoff}" for cutoff in _CUTOFFS),
    *(f"high20_recall_at_{cutoff}" for cutoff in _CUTOFFS),
    *(f"high10_recall_at_{cutoff}" for cutoff in _CUTOFFS),
    *(f"high20_precision_at_{cutoff}" for cutoff in _CUTOFFS),
    *(f"leader_recall_at_{cutoff}" for cutoff in _CUTOFFS),
    *(f"max_return_tie_aware_hit_at_{cutoff}" for cutoff in _CUTOFFS),
)
_LOWER_IS_BETTER = frozenset(
    {
        "population_brier",
        "population_expected_calibration_error",
        "selective_top20_brier",
        "selective_top20_expected_calibration_error",
    }
)


def build_quality_score_report(
    *,
    paired: PairedPredictionManifest,
    paired_manifest_sha256: str,
    observations: Sequence[dict[str, Any] | QualityCaseObservation],
    outcome_hashes: dict[str, str],
) -> dict[str, Any]:
    """Validate case closure, preserve raw observations, and derive aggregates."""

    typed = [
        row
        if isinstance(row, QualityCaseObservation)
        else QualityCaseObservation.model_validate(row)
        for row in observations
    ]
    _verify_observation_closure(paired, typed, outcome_hashes=outcome_hashes)
    by_variant = {
        variant: [row for row in typed if row.variant_id == variant]
        for variant in _VARIANTS
    }
    market = {variant: _market_metrics(rows) for variant, rows in by_variant.items()}
    retrieval = {
        variant: _retrieval_metrics(rows) for variant, rows in by_variant.items()
    }
    citations = {
        variant: _citation_metrics(rows) for variant, rows in by_variant.items()
    }
    efficiency = {
        variant: _efficiency_metrics(rows) for variant, rows in by_variant.items()
    }
    error_taxonomy = {
        variant: _error_taxonomy(rows) for variant, rows in by_variant.items()
    }
    safety = _safety_metrics(paired, typed)
    shared = _shared_stage_accounting(typed)
    paired_case_comparison = _paired_case_comparison(typed)
    gate_checks = {
        "all_predictions_sealed_before_outcomes": paired.all_predictions_sealed,
        "paired_case_closure": len(paired.paired_case_ids)
        == len(paired.expected_case_ids),
        "shared_context_identity_match": all(
            row["shared_context_identity_match"] for row in paired_case_comparison
        ),
        "evaluation_universe_identity_match": all(
            row["evaluation_universe_identity_match"]
            for row in paired_case_comparison
        ),
        "future_record_zero": safety["future_record_count"] == 0,
        "blind_web_zero": safety["blind_web_call_count"] == 0,
        "online_full_scan_zero": safety["online_full_scan_count"] == 0,
        "outcome_pre_access_zero": safety[
            "outcome_reference_count_during_prediction"
        ]
        == 0,
        "orphan_citation_zero": safety["orphan_citation_count"] == 0,
        "wrong_snapshot_zero": safety["wrong_snapshot_count"] == 0,
        "citation_closure_verified": safety["citation_closure_verified"],
        "snapshot_closure_verified": safety["snapshot_closure_verified"],
        "forbidden_shared_key_zero": safety["forbidden_shared_key_count"] == 0,
        "shared_digest_closure_verified": safety[
            "shared_digest_closure_verified"
        ],
    }
    dumped_observations = [row.model_dump(mode="json") for row in typed]
    return {
        "schema_version": "nslab.quality_runtime_score.v4",
        "run_id": paired.run_id,
        "scored_at": now_kst().isoformat(),
        "profile": paired.profile.model_dump(mode="json"),
        "paired_prediction_manifest_sha256": paired_manifest_sha256,
        "prediction_seal_count": len(paired.seals),
        "paired_case_count": len(paired.paired_case_ids),
        "outcome_opened_after_all_seals": True,
        "outcome_hashes": dict(sorted(outcome_hashes.items())),
        "case_observations": sorted(
            dumped_observations,
            key=lambda row: (str(row["trade_date"]), str(row["variant_id"])),
        ),
        "market_metrics": market,
        "retrieval_metrics": retrieval,
        "citation_metrics": citations,
        "error_taxonomy": error_taxonomy,
        "paired_comparison": {
            "aggregate": _paired_aggregate_comparison(
                market,
                retrieval,
                citations,
                error_taxonomy,
            ),
            "by_case": paired_case_comparison,
            "win_tie_loss": _win_tie_loss(paired_case_comparison),
        },
        "paired_bootstrap": _paired_bootstrap(
            dumped_observations,
            run_id=paired.run_id,
        ),
        "shared_stage_accounting": shared,
        "efficiency_observations_non_blocking": efficiency,
        "metric_availability": {
            "theme_breadth_precision_recall": (
                "UNAVAILABLE_OUTCOME_SELECTION_HAS_NO_SEALED_SECTOR_TRUTH"
            ),
            "newsless_hallucination": (
                "UNAVAILABLE_OUTCOME_SELECTION_HAS_NO_SEALED_NEWSLESS_TRUTH"
            ),
            "known_relevance_recall": (
                "RELEVANCE_LABEL_UNAVAILABLE_NON_BLOCKING"
            ),
            "unsupported_memory_assertion_count": (
                "UNAVAILABLE_NO_SEALED_CLAIM_LEVEL_SUPPORT_LABELS"
            ),
            "regime_diversity": (
                "UNAVAILABLE_RUNTIME_TRACE_HAS_NO_RECORD_REGIME_BINDING"
            ),
            "candidate_generation_vs_ranking": (
                "AVAILABLE_FROM_SEALED_GENERATED_AND_RANKED_TICKER_SETS"
            ),
        },
        "safety": safety,
        "quality_gate_checks": gate_checks,
        "quality_evaluation_status": (
            "PREDICTIVELY_EVALUATED"
            if all(gate_checks.values())
            else "INVALID_SAFETY_OR_PAIR_CLOSURE"
        ),
        "latency_is_blocking": False,
        "token_is_blocking": False,
        "call_count_is_blocking": False,
        "production_activation_status": "NOT_PRODUCTION_ACTIVATED",
    }


def render_quality_score(report: dict[str, Any]) -> str:
    """Render date-level and aggregate evidence retained by the JSON report."""

    market = report["market_metrics"]
    retrieval = report["retrieval_metrics"]
    citations = report["citation_metrics"]
    efficiency = report["efficiency_observations_non_blocking"]
    safety = report["safety"]
    shared = report["shared_stage_accounting"]
    market_keys = [
        *(f"micro_upper_limit_recall_at_{cutoff}" for cutoff in _CUTOFFS),
        *(f"micro_high20_recall_at_{cutoff}" for cutoff in _CUTOFFS),
        *(f"micro_high10_recall_at_{cutoff}" for cutoff in _CUTOFFS),
        *(f"micro_high20_precision_at_{cutoff}" for cutoff in _CUTOFFS),
        "leader_selection_accuracy",
        "top_pick_high20_hit",
        "population_brier",
        "population_expected_calibration_error",
        "population_brier_skill_vs_climatology",
        *(f"leader_recall_at_{cutoff}" for cutoff in _CUTOFFS),
        *(f"max_return_tie_aware_hit_at_{cutoff}" for cutoff in _CUTOFFS),
    ]
    lines = [
        "# QUALITY_FULL Runtime Variant Score",
        "",
        f"Status: `{report['quality_evaluation_status']}`.",
        f"Paired cases: `{report['paired_case_count']}`.",
        "Latency, token, and call counts are observations only and do not gate quality.",
        "",
        "## Date Results",
        "",
        "| Date | Case | Variant | High20 R@20 | Exact leader | Searched | "
        "Selected | LLM exposed | Memo | Stock cited | Sector cited | Unused | "
        "Elapsed accounting | Calls | Prompt tokens |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["case_observations"]:
        retrieval_row = row["retrieval"]
        runtime = row["efficiency"]["runtime_metrics"]
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["trade_date"]),
                    str(row["case_id"]),
                    str(row["variant_id"]),
                    _display(row["metrics"].get("high20_recall_at_20")),
                    _display(row["metrics"].get("leader_selection_accuracy")),
                    str(retrieval_row["searched_record_occurrence_count"]),
                    str(retrieval_row["selected_record_occurrence_count"]),
                    str(retrieval_row["llm_exposed_record_occurrence_count"]),
                    str(retrieval_row["memo_referenced_record_occurrence_count"]),
                    str(retrieval_row["stock_cited_record_occurrence_count"]),
                    str(retrieval_row["sector_cited_record_occurrence_count"]),
                    str(retrieval_row["selected_unused_record_occurrence_count"]),
                    _elapsed_display(row["efficiency"]),
                    _display(runtime.get("logical_llm_call_count")),
                    _display(runtime.get("llm_prompt_tokens_estimate")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "### Date Market Detail",
            "",
            "| Date | Variant | Upper R@5/10/20 | High20 R@5/10/20 | "
            "High10 R@5/10/20 | High20 P@5/10/20 | Leader | "
            "Max-return hit@20 | Brier | ECE |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in report["case_observations"]:
        metrics = row["metrics"]
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["trade_date"]),
                    str(row["variant_id"]),
                    _cutoff_triplet(metrics, "upper_limit_recall_at"),
                    _cutoff_triplet(metrics, "high20_recall_at"),
                    _cutoff_triplet(metrics, "high10_recall_at"),
                    _cutoff_triplet(metrics, "high20_precision_at"),
                    _display(metrics.get("leader_selection_accuracy")),
                    _display(metrics.get("max_return_tie_aware_hit_at_20")),
                    _display(metrics.get("population_brier")),
                    _display(
                        metrics.get("population_expected_calibration_error")
                    ),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Market",
            "",
            "| Metric | V0 | V1 | V1 - V0 |",
            "| --- | ---: | ---: | ---: |",
            *[
                f"| {key} | {_display(market['V0'].get(key))} | "
                f"{_display(market['V1'].get(key))} | "
                f"{_display(report['paired_comparison']['aggregate']['market_v1_minus_v0'].get(key))} |"
                for key in market_keys
            ],
            "",
            "## Retrieval",
            "",
            "| Metric | V0 | V1 |",
            "| --- | ---: | ---: |",
            *[
                f"| {key} | {_display(retrieval['V0'].get(key))} | "
                f"{_display(retrieval['V1'].get(key))} |"
                for key in (
                    "searched_record_occurrence_count",
                    "searched_record_unique_count",
                    "selected_record_occurrence_count",
                    "selected_record_unique_count",
                    "llm_exposed_record_occurrence_count",
                    "memo_referenced_record_occurrence_count",
                    "stock_cited_record_occurrence_count",
                    "sector_cited_record_occurrence_count",
                    "selected_unused_record_occurrence_count",
                    "offline_unexposed_retrieval_rate",
                    "offline_unexposed_payload_exposure_rate",
                    "offline_unexposed_final_citation_rate",
                    "rare_mechanism_recovered_unique_count",
                    "unique_episode_count",
                    "unique_year_count",
                )
            ],
            "",
            "## Citations",
            "",
            "| Metric | V0 | V1 |",
            "| --- | ---: | ---: |",
            *[
                f"| {key} | {_display(citations['V0'].get(key))} | "
                f"{_display(citations['V1'].get(key))} |"
                for key in (
                    "prediction_cited_record_occurrence_count",
                    "prediction_cited_record_unique_count",
                    "runtime_final_cited_record_unique_count",
                    "legacy_final_cited_record_unique_count",
                    "mean_final_memory_citation_rate",
                    "cases_with_zero_final_citations",
                    "citation_closure_verified",
                    "orphan_citation_count",
                )
            ],
            "",
            "## Efficiency",
            "",
            "| Metric | V0 | V1 |",
            "| --- | ---: | ---: |",
            *[
                f"| {key} | {_display(efficiency['V0'].get(key))} | "
                f"{_display(efficiency['V1'].get(key))} |"
                for key in (
                    "wall_clock_accounting_status",
                    "wall_clock_exact_completed_seconds",
                    "wall_clock_lower_bound_seconds",
                    "wall_clock_upper_bound_seconds",
                    "contains_recovered_attempts",
                    "runtime_metrics_accounting_status",
                    "attempt_count",
                    "logical_llm_call_count",
                    "oauth_live_agent_call_count",
                    "llm_checkpoint_hit_count",
                    "llm_prompt_tokens_estimate",
                    "llm_completion_tokens_estimate",
                    "llm_retry_count",
                    "process_peak_working_set_bytes",
                )
            ],
            "",
            "## Shared Stage",
            "",
            "The `without_dedup` values below are an explicitly derived counterfactual, not a second observed run.",
            f"Observed logical calls: `{shared['observed_shared_once']['logical_llm_call_count']}`.",
            f"Counterfactual calls without dedup: `{shared['derived_without_dedup']['logical_llm_call_count']}`.",
            f"Observed prompt tokens: `{shared['observed_shared_once']['prompt_tokens_estimate']}`.",
            "Observed shared elapsed bounds: "
            f"`{shared['observed_shared_once']['elapsed_lower_bound_seconds']}`.."
            f"`{shared['observed_shared_once']['elapsed_upper_bound_seconds']}` "
            f"(`{shared['observed_shared_once']['elapsed_accounting_status']}`).",
            f"Shared cache-load count: `{shared['observed_shared_once']['cache_load_count']}`.",
            "Counterfactual prompt tokens without dedup: "
            f"`{shared['derived_without_dedup']['prompt_tokens_estimate']}`.",
            "",
            "## Safety",
            "",
            *[f"- `{key}`: `{value}`" for key, value in sorted(safety.items())],
            "",
            "## Error Taxonomy",
            "",
            *[
                f"- `{variant}`: `{taxonomy}`"
                for variant, taxonomy in sorted(report["error_taxonomy"].items())
            ],
            "",
            "Production activation: `NOT_PRODUCTION_ACTIVATED`.",
            "",
        ]
    )
    return "\n".join(lines)


def _verify_observation_closure(
    paired: PairedPredictionManifest,
    observations: list[QualityCaseObservation],
    *,
    outcome_hashes: dict[str, str],
) -> None:
    expected = {
        (case_id, variant)
        for case_id in paired.expected_case_ids
        for variant in _VARIANTS
    }
    observed = [(row.case_id, row.variant_id) for row in observations]
    if len(observed) != len(set(observed)) or set(observed) != expected:
        raise ValueError("quality observations do not exactly close over paired cases")
    if set(outcome_hashes) != set(paired.expected_case_ids):
        raise ValueError("quality outcome hashes do not close over paired cases")
    seals = {(seal.case_id, seal.variant_id): seal for seal in paired.seals}
    for row in observations:
        seal = seals.get((row.case_id, row.variant_id))
        if seal is None:
            raise ValueError("quality observation has no prediction seal")
        if row.shared_context_sha256 != seal.shared_context_sha256:
            raise ValueError("quality observation shared context differs from seal")
        if row.retrieval.memory_snapshot_id != seal.memory_snapshot_id:
            raise ValueError("quality observation snapshot differs from seal")
        if row.safety.future_record_count != seal.future_record_count:
            raise ValueError("quality observation future count differs from seal")
        if row.safety.blind_web_call_count != seal.blind_web_call_count:
            raise ValueError("quality observation BLIND web count differs from seal")
        if row.safety.online_full_scan_count != seal.online_full_scan_count:
            raise ValueError("quality observation full-scan count differs from seal")
        if (
            row.safety.outcome_reference_count_during_prediction
            != seal.outcome_reference_count
        ):
            raise ValueError("quality observation outcome count differs from seal")
    by_case: dict[str, list[QualityCaseObservation]] = {}
    for row in observations:
        by_case.setdefault(row.case_id, []).append(row)
    for case_id, rows in by_case.items():
        if len(rows) != 2:
            raise ValueError(f"quality case {case_id} is not paired")
        parity = {
            (
                row.trade_date,
                row.shared_context_sha256,
                row.evaluation_universe_sha256,
                row.evaluation_universe_count,
                row.population_universe_sha256,
                row.population_universe_count,
                row.market_universe_policy_version,
                row.brier_population_policy_version,
                row.probability_policy_version,
                canonical_json(row.shared_stage.model_dump(mode="json")),
            )
            for row in rows
        }
        if len(parity) != 1:
            raise ValueError(f"quality case {case_id} observation parity drifted")


def _market_metrics(rows: list[QualityCaseObservation]) -> dict[str, Any]:
    dumped = [row.model_dump(mode="python") for row in rows]
    metrics = {key: _mean_metric(rows, key) for key in _MEAN_MARKET_KEYS}
    metrics.update(_aggregate_micro_market_metrics(dumped))
    for cutoff in _CUTOFFS:
        metrics[f"precision_at_{cutoff}"] = metrics[
            f"high20_precision_at_{cutoff}"
        ]
        for target in ("upper_limit", "high20", "high10"):
            metrics[f"{target}_no_positive_case_count"] = sum(
                int(row.metrics[f"{target}_target_count"] == 0) for row in rows
            )
            false_positive_key = (
                f"{target}_no_positive_false_positive_count_at_{cutoff}"
            )
            metrics[false_positive_key] = sum(
                int(row.metrics[false_positive_key]) for row in rows
            )
    metrics["selective_top20_expected_calibration_error_pooled"] = (
        _expected_calibration_error(dumped)
    )
    metrics["evaluation_universe_count"] = sum(
        row.evaluation_universe_count for row in rows
    )
    return metrics


def _retrieval_metrics(rows: list[QualityCaseObservation]) -> dict[str, Any]:
    retrievals = [row.retrieval for row in rows]
    occurrence_fields = (
        "searched_record_occurrence_count",
        "selected_record_occurrence_count",
        "llm_exposed_record_occurrence_count",
        "memo_referenced_record_occurrence_count",
        "stock_cited_record_occurrence_count",
        "sector_cited_record_occurrence_count",
        "final_cited_record_occurrence_count",
        "selected_unused_record_occurrence_count",
    )
    result: dict[str, Any] = {
        field: sum(getattr(item, field) for item in retrievals)
        for field in occurrence_fields
    }
    id_fields = (
        "searched_record_ids",
        "selected_record_ids",
        "llm_exposed_record_ids",
        "memo_referenced_record_ids",
        "stock_cited_record_ids",
        "sector_cited_record_ids",
        "final_cited_record_ids",
        "selected_unused_record_ids",
        "offline_unexposed_searched_record_ids",
        "offline_unexposed_selected_record_ids",
        "offline_unexposed_llm_exposed_record_ids",
        "offline_unexposed_final_cited_record_ids",
        "rare_selected_record_ids",
        "independent_unit_ids",
        "episode_ids",
    )
    unique: dict[str, set[str]] = {
        field: {
            value for item in retrievals for value in getattr(item, field)
        }
        for field in id_fields
    }
    for field, values in unique.items():
        result[field.removesuffix("_ids") + "_unique_count"] = len(values)
    offline_searched_occurrences = sum(
        len(cluster.offline_unexposed_searched_record_ids)
        for item in retrievals
        for cluster in item.clusters
    )
    offline_selected_occurrences = sum(
        len(cluster.offline_unexposed_selected_record_ids)
        for item in retrievals
        for cluster in item.clusters
    )
    offline_exposed_occurrences = sum(
        len(cluster.offline_unexposed_llm_exposed_record_ids)
        for item in retrievals
        for cluster in item.clusters
    )
    offline_cited_occurrences = sum(
        len(cluster.offline_unexposed_final_cited_record_ids)
        for item in retrievals
        for cluster in item.clusters
    )
    result.update(
        {
            "offline_unexposed_searched_record_occurrence_count": (
                offline_searched_occurrences
            ),
            "offline_unexposed_selected_record_occurrence_count": (
                offline_selected_occurrences
            ),
            "offline_unexposed_llm_exposed_record_occurrence_count": (
                offline_exposed_occurrences
            ),
            "offline_unexposed_final_cited_record_occurrence_count": (
                offline_cited_occurrences
            ),
            "offline_unexposed_retrieval_rate": _ratio(
                offline_selected_occurrences,
                offline_searched_occurrences,
            ),
            "offline_unexposed_payload_exposure_rate": _ratio(
                offline_exposed_occurrences,
                offline_selected_occurrences,
            ),
            "offline_unexposed_final_citation_rate": _ratio(
                offline_cited_occurrences,
                offline_selected_occurrences,
            ),
            "runtime_trace_count": sum(len(item.clusters) for item in retrievals),
            "adaptive_trace_count": sum(
                item.adaptive_trace_count for item in retrievals
            ),
            "unique_episode_count": len(unique["episode_ids"]),
            "unique_year_count": len(
                {year for item in retrievals for year in item.year_counts}
            ),
            "rare_mechanism_recovered_unique_count": len(
                unique["rare_selected_record_ids"]
            ),
        }
    )
    lanes: dict[str, Counter[str]] = {}
    years: Counter[str] = Counter()
    for item in retrievals:
        years.update(item.year_counts)
        for lane, stages in item.lane_stage_counts.items():
            lanes.setdefault(lane, Counter()).update(stages)
    result["lane_stage_counts"] = {
        lane: dict(sorted(stages.items()))
        for lane, stages in sorted(lanes.items())
    }
    result["selected_year_counts"] = dict(sorted(years.items()))
    return result


def _citation_metrics(rows: list[QualityCaseObservation]) -> dict[str, Any]:
    prediction_ids = {
        record_id
        for row in rows
        for record_id in row.citation_closure.prediction_memory_record_ids
    }
    runtime_ids = {
        record_id
        for row in rows
        for record_id in row.citation_closure.runtime_final_cited_record_ids
    }
    legacy_ids = {
        record_id
        for row in rows
        for record_id in row.citation_closure.legacy_final_cited_record_ids
    }
    rates = [
        float(row.metrics["final_memory_citation_rate"])
        for row in rows
        if isinstance(row.metrics.get("final_memory_citation_rate"), (int, float))
    ]
    orphan_count = sum(row.safety.orphan_citation_count for row in rows)
    return {
        "prediction_cited_record_occurrence_count": sum(
            len(row.citation_closure.prediction_memory_record_ids) for row in rows
        ),
        "prediction_cited_record_unique_count": len(prediction_ids),
        "runtime_final_cited_record_unique_count": len(runtime_ids),
        "legacy_final_cited_record_unique_count": len(legacy_ids),
        "mean_final_memory_citation_rate": (
            sum(rates) / len(rates) if rates else None
        ),
        "cases_with_zero_final_citations": sum(
            int(not row.citation_closure.prediction_memory_record_ids)
            for row in rows
        ),
        "citation_closure_verified": all(
            row.citation_closure.closure_verified for row in rows
        ),
        "orphan_citation_count": orphan_count,
    }


def _efficiency_metrics(rows: list[QualityCaseObservation]) -> dict[str, Any]:
    runtime_metrics, runtime_statuses = _aggregate_runtime_metrics(rows)
    recovered = any(row.efficiency.contains_recovered_attempts for row in rows)
    unavailable = any(
        status in {"UNAVAILABLE", "PARTIAL_LOWER_BOUND"}
        for status in runtime_statuses.values()
    )
    result: dict[str, Any] = {
        "wall_clock_accounting_status": (
            "BOUNDED_RECOVERY" if recovered else "EXACT"
        ),
        "wall_clock_exact_completed_seconds": sum(
            row.efficiency.elapsed_exact_completed_seconds for row in rows
        ),
        "wall_clock_lower_bound_seconds": sum(
            row.efficiency.elapsed_lower_bound_seconds for row in rows
        ),
        "wall_clock_upper_bound_seconds": sum(
            row.efficiency.elapsed_upper_bound_seconds for row in rows
        ),
        "contains_recovered_attempts": recovered,
        "recovered_interrupted_attempt_count": sum(
            row.efficiency.recovered_interrupted_attempt_count for row in rows
        ),
        "runtime_metrics_accounting_status": (
            "RECOVERED_PARTIAL"
            if recovered and unavailable
            else "RECOVERED_LOWER_BOUND"
            if recovered
            else "PARTIAL_UNAVAILABLE"
            if unavailable
            else "EXACT"
        ),
        "attempt_count": sum(row.efficiency.attempt_count for row in rows),
        **runtime_metrics,
        "runtime_metric_statuses": runtime_statuses,
        "attempt_ledger_sha256s": sorted(
            row.efficiency.attempt_ledger_sha256 for row in rows
        ),
    }
    logical_value = result.get("logical_llm_call_count")
    logical = (
        int(logical_value)
        if isinstance(logical_value, int | float)
        and not isinstance(logical_value, bool)
        else None
    )
    checkpoint_value = result.get("llm_checkpoint_hit_count")
    result["llm_checkpoint_hit_rate"] = (
        int(checkpoint_value) / logical
        if logical
        and isinstance(checkpoint_value, int | float)
        and not isinstance(checkpoint_value, bool)
        else None
    )
    return result


def _aggregate_runtime_metrics(
    rows: list[QualityCaseObservation],
) -> tuple[dict[str, int | float | None], dict[str, str]]:
    return _aggregate_runtime_metric_maps(
        [
            (
                row.efficiency.runtime_metrics,
                row.efficiency.runtime_metric_statuses,
            )
            for row in rows
        ]
    )


def _aggregate_runtime_metric_maps(
    rows: list[tuple[Mapping[str, Any], Mapping[str, str]]],
) -> tuple[dict[str, int | float | None], dict[str, str]]:
    keys = sorted(
        {
            key
            for _metrics, metric_statuses in rows
            for key in metric_statuses
        }
    )
    values: dict[str, int | float | None] = {}
    statuses: dict[str, str] = {}
    for key in keys:
        observations = [
            (
                metric_values.get(key),
                metric_statuses.get(key, "UNAVAILABLE"),
            )
            for metric_values, metric_statuses in rows
        ]
        numeric = [
            value
            for value, _status in observations
            if isinstance(value, int | float) and not isinstance(value, bool)
        ]
        if not numeric:
            values[key] = None
            statuses[key] = "UNAVAILABLE"
            continue
        values[key] = (
            max(numeric)
            if key == "process_peak_working_set_bytes"
            else sum(numeric)
        )
        observed_statuses = {status for _value, status in observations}
        if observed_statuses == {"EXACT"}:
            statuses[key] = "EXACT"
        elif "UNAVAILABLE" in observed_statuses or "PARTIAL_LOWER_BOUND" in (
            observed_statuses
        ):
            statuses[key] = "PARTIAL_LOWER_BOUND"
        else:
            statuses[key] = "LOWER_BOUND"
    return values, statuses


def _error_taxonomy(rows: list[QualityCaseObservation]) -> dict[str, int]:
    result: Counter[str] = Counter()
    for row in rows:
        metrics = row.metrics
        generated = set(
            _required_string_list(
                metrics,
                "generated_candidate_tickers",
            )
        )
        ranked = _required_string_list(metrics, "ranked_candidate_tickers")
        for target in ("upper_limit", "high20", "high10"):
            truth = set(
                _required_string_list(metrics, f"{target}_target_tickers")
            )
            result[f"{target}_candidate_generation_miss_count"] += len(
                truth - generated
            )
            generated_truth = truth & generated
            for cutoff in _CUTOFFS:
                result[f"{target}_candidate_ranking_miss_at_{cutoff}_count"] += len(
                    generated_truth - set(ranked[:cutoff])
                )
                result[f"{target}_ranked_false_positive_at_{cutoff}_count"] += len(
                    set(ranked[:cutoff]) - truth
                )
        if metrics.get("leader_selection_accuracy") == 0.0:
            result["leader_selection_miss_case_count"] += 1
        result["zero_final_citation_case_count"] += int(
            not row.citation_closure.prediction_memory_record_ids
        )
        result["outcome_ineligible_selection_count"] += int(
            metrics.get("outcome_ineligible_selected_count") or 0
        )
    return dict(sorted(result.items()))


def _safety_metrics(
    paired: PairedPredictionManifest,
    rows: list[QualityCaseObservation],
) -> dict[str, Any]:
    safety = {
        "future_record_count": sum(row.safety.future_record_count for row in rows),
        "blind_web_call_count": sum(row.safety.blind_web_call_count for row in rows),
        "online_full_scan_count": sum(
            row.safety.online_full_scan_count for row in rows
        ),
        "outcome_reference_count_during_prediction": sum(
            row.safety.outcome_reference_count_during_prediction for row in rows
        ),
        "orphan_citation_count": sum(
            row.safety.orphan_citation_count for row in rows
        ),
        "wrong_snapshot_count": sum(
            row.safety.wrong_snapshot_count for row in rows
        ),
        "citation_closure_verified": all(
            row.citation_closure.closure_verified for row in rows
        ),
        "snapshot_closure_verified": all(
            row.safety.snapshot_closure_verified for row in rows
        ),
        "forbidden_shared_key_count": sum(
            row.safety.forbidden_shared_key_count for row in rows
        ),
        "shared_digest_closure_verified": all(
            row.safety.shared_digest_closure_verified for row in rows
        ),
    }
    if safety["future_record_count"] != sum(
        seal.future_record_count for seal in paired.seals
    ):
        raise ValueError("quality safety future evidence differs from seals")
    return safety


def _shared_stage_accounting(
    observations: list[QualityCaseObservation],
) -> dict[str, Any]:
    by_case: dict[str, QualityCaseObservation] = {}
    for row in observations:
        existing = by_case.get(row.case_id)
        if existing is not None and row.shared_stage != existing.shared_stage:
            raise ValueError("paired shared-stage observations differ")
        by_case[row.case_id] = row
    shared_rows = [row.shared_stage for row in by_case.values()]
    runtime_metrics, runtime_statuses = _aggregate_runtime_metric_maps(
        [
            (row.runtime_metrics, row.runtime_metric_statuses)
            for row in shared_rows
        ]
    )
    recovered = any(row.contains_recovered_attempts for row in shared_rows)
    observed = {
        "case_count": len(by_case),
        **runtime_metrics,
        "runtime_metric_statuses": runtime_statuses,
        "runtime_metrics_accounting_status": (
            "RECOVERED_PARTIAL"
            if recovered
            and any(
                status in {"UNAVAILABLE", "PARTIAL_LOWER_BOUND"}
                for status in runtime_statuses.values()
            )
            else "RECOVERED_LOWER_BOUND"
            if recovered
            else "PARTIAL_UNAVAILABLE"
            if any(status == "UNAVAILABLE" for status in runtime_statuses.values())
            else "EXACT"
        ),
        "logical_llm_call_count": runtime_metrics.get("logical_llm_call_count"),
        "live_llm_call_count": runtime_metrics.get("oauth_live_agent_call_count"),
        "checkpoint_hit_count": runtime_metrics.get("llm_checkpoint_hit_count"),
        "prompt_tokens_estimate": runtime_metrics.get(
            "llm_prompt_tokens_estimate"
        ),
        "completion_tokens_estimate": runtime_metrics.get(
            "llm_completion_tokens_estimate"
        ),
        "elapsed_accounting_status": (
            "BOUNDED_RECOVERY" if recovered else "EXACT"
        ),
        "elapsed_exact_completed_seconds": sum(
            row.elapsed_exact_completed_seconds for row in shared_rows
        ),
        "elapsed_lower_bound_seconds": sum(
            row.elapsed_lower_bound_seconds for row in shared_rows
        ),
        "elapsed_upper_bound_seconds": sum(
            row.elapsed_upper_bound_seconds for row in shared_rows
        ),
        "contains_recovered_attempts": recovered,
        "recovered_interrupted_attempt_count": sum(
            row.recovered_interrupted_attempt_count for row in shared_rows
        ),
        "cache_load_count": sum(
            row.cache_load_attempt_count for row in shared_rows
        ),
        "build_count": sum(
            row.build_attempt_count for row in shared_rows
        ),
        "attempt_count": sum(
            row.attempt_count for row in shared_rows
        ),
        "attempt_ledger_sha256s": sorted(
            row.attempt_ledger_sha256 for row in shared_rows
        ),
    }
    counterfactual = {
        key: value * 2 if isinstance(value, (int, float)) else None
        for key, value in observed.items()
        if key
        not in {
            "case_count",
            "attempt_ledger_sha256s",
            "runtime_metric_statuses",
            "runtime_metrics_accounting_status",
            "elapsed_accounting_status",
            "contains_recovered_attempts",
        }
    }
    return {
        "method": "OBSERVED_SHARED_ONCE_PLUS_DERIVED_TWO_VARIANT_COUNTERFACTUAL",
        "observed_shared_once": observed,
        "derived_without_dedup": counterfactual,
        "derivation": "each paired variant would repeat the identical shared artifact once",
    }


def _elapsed_display(efficiency: dict[str, Any]) -> str:
    lower = efficiency.get("elapsed_lower_bound_seconds")
    upper = efficiency.get("elapsed_upper_bound_seconds")
    status = efficiency.get("elapsed_accounting_status")
    if not isinstance(lower, int | float) or not isinstance(upper, int | float):
        return "UNAVAILABLE"
    if status == "EXACT":
        return f"{float(lower):.3f} exact"
    return f"{float(lower):.3f}..{float(upper):.3f} bounded"


def _paired_case_comparison(
    observations: list[QualityCaseObservation],
) -> list[dict[str, Any]]:
    by_case: dict[str, dict[str, QualityCaseObservation]] = {}
    for row in observations:
        by_case.setdefault(row.case_id, {})[row.variant_id] = row
    result: list[dict[str, Any]] = []
    for case_id, variants in sorted(by_case.items()):
        if set(variants) != set(_VARIANTS):
            raise ValueError("paired comparison is missing a runtime variant")
        v0 = variants["V0"]
        v1 = variants["V1"]
        result.append(
            {
                "case_id": case_id,
                "trade_date": v0.trade_date.isoformat(),
                "shared_context_identity_match": (
                    v0.shared_context_sha256 == v1.shared_context_sha256
                ),
                "evaluation_universe_identity_match": (
                    v0.evaluation_universe_sha256
                    == v1.evaluation_universe_sha256
                    and v0.evaluation_universe_count
                    == v1.evaluation_universe_count
                    and v0.population_universe_sha256
                    == v1.population_universe_sha256
                    and v0.population_universe_count
                    == v1.population_universe_count
                    and v0.market_universe_policy_version
                    == v1.market_universe_policy_version
                    and v0.brier_population_policy_version
                    == v1.brier_population_policy_version
                    and v0.probability_policy_version
                    == v1.probability_policy_version
                ),
                "market_v1_minus_v0": _numeric_deltas(v0.metrics, v1.metrics),
                "retrieval_v1_minus_v0": _numeric_deltas(
                    _case_retrieval_numbers(v0),
                    _case_retrieval_numbers(v1),
                ),
            }
        )
    return result


def _paired_aggregate_comparison(
    market: dict[str, dict[str, Any]],
    retrieval: dict[str, dict[str, Any]],
    citations: dict[str, dict[str, Any]],
    error_taxonomy: dict[str, dict[str, int]],
) -> dict[str, Any]:
    return {
        "market_v1_minus_v0": _numeric_deltas(market["V0"], market["V1"]),
        "retrieval_v1_minus_v0": _numeric_deltas(
            retrieval["V0"], retrieval["V1"]
        ),
        "citation_v1_minus_v0": _numeric_deltas(
            citations["V0"], citations["V1"]
        ),
        "error_taxonomy_v1_minus_v0": _numeric_deltas(
            error_taxonomy["V0"], error_taxonomy["V1"]
        ),
    }


def _win_tie_loss(case_rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    metrics = sorted(
        {
            metric
            for row in case_rows
            for metric, value in row["market_v1_minus_v0"].items()
            if isinstance(value, (int, float))
        }
    )
    result: dict[str, dict[str, int]] = {}
    for metric in metrics:
        wins = ties = losses = 0
        for row in case_rows:
            value = row["market_v1_minus_v0"].get(metric)
            if not isinstance(value, (int, float)):
                continue
            improvement = -float(value) if metric in _LOWER_IS_BETTER else float(value)
            if improvement > 0:
                wins += 1
            elif improvement < 0:
                losses += 1
            else:
                ties += 1
        result[metric] = {"V1_win": wins, "tie": ties, "V1_loss": losses}
    return result


def _case_retrieval_numbers(row: QualityCaseObservation) -> dict[str, int]:
    retrieval = row.retrieval
    return {
        "searched_record_occurrence_count": retrieval.searched_record_occurrence_count,
        "selected_record_occurrence_count": retrieval.selected_record_occurrence_count,
        "llm_exposed_record_occurrence_count": retrieval.llm_exposed_record_occurrence_count,
        "memo_referenced_record_occurrence_count": retrieval.memo_referenced_record_occurrence_count,
        "stock_cited_record_occurrence_count": retrieval.stock_cited_record_occurrence_count,
        "sector_cited_record_occurrence_count": retrieval.sector_cited_record_occurrence_count,
        "selected_unused_record_occurrence_count": retrieval.selected_unused_record_occurrence_count,
    }


def _numeric_deltas(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    return {
        key: float(right[key]) - float(left[key])
        for key in sorted(set(left) & set(right))
        if isinstance(left[key], (int, float))
        and not isinstance(left[key], bool)
        and isinstance(right[key], (int, float))
        and not isinstance(right[key], bool)
    }


def _mean_metric(rows: list[QualityCaseObservation], key: str) -> float | None:
    values = [
        float(row.metrics[key])
        for row in rows
        if isinstance(row.metrics.get(key), (int, float))
        and not isinstance(row.metrics.get(key), bool)
    ]
    return sum(values) / len(values) if values else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _required_string_list(metrics: dict[str, Any], key: str) -> list[str]:
    value = metrics.get(key)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"quality metrics require a valid {key} list")
    if value != list(dict.fromkeys(value)):
        raise ValueError(f"quality metrics {key} must not contain duplicates")
    return value


def _display(value: Any) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _cutoff_triplet(metrics: dict[str, Any], prefix: str) -> str:
    return "/".join(_display(metrics.get(f"{prefix}_{cutoff}")) for cutoff in _CUTOFFS)


def _runtime_int(value: int | float | str | None) -> int:
    return int(value) if isinstance(value, (int, float)) else 0
