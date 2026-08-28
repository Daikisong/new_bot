from __future__ import annotations

import pytest

from news_scalping_lab.evaluation.variant_shadow import (
    ShadowVariantObservation,
    compare_shadow_variants,
)
from news_scalping_lab.utils import sha256_text


def _observations() -> list[ShadowVariantObservation]:
    rows = []
    for case_index in range(3):
        for variant_index, variant_id in enumerate(("V0", "V1", "V2")):
            rows.append(
                ShadowVariantObservation(
                    case_id=f"CASE-{case_index}",
                    split="HOLDOUT",
                    arm_id="F",
                    variant_id=variant_id,
                    current_news_sha256=sha256_text(f"news-{case_index}"),
                    truth_sha256=sha256_text(f"truth-{case_index}"),
                    prediction_sha256=sha256_text(
                        f"prediction-{case_index}-{variant_id}"
                    ),
                    retrieval_trace_count=1,
                    metrics={"recall_at_20": 0.4 + variant_index * 0.1},
                )
            )
    return rows


def test_v0_v1_v2_same_current_news_and_truth() -> None:
    rows = _observations()
    rows[1] = rows[1].model_copy(
        update={"current_news_sha256": sha256_text("different-news")}
    )

    with pytest.raises(ValueError, match="identical current news"):
        compare_shadow_variants(rows, seed="fixed-seed")


def test_shadow_traces_nonzero_for_memory_arms() -> None:
    with pytest.raises(ValueError, match="require retrieval traces"):
        ShadowVariantObservation(
            case_id="CASE-1",
            split="HOLDOUT",
            arm_id="F",
            variant_id="V1",
            current_news_sha256=sha256_text("news"),
            truth_sha256=sha256_text("truth"),
            prediction_sha256=sha256_text("prediction"),
            retrieval_trace_count=0,
            metrics={"recall_at_20": 1.0},
        )


def test_paired_metrics_deterministic() -> None:
    first = compare_shadow_variants(_observations(), seed="fixed-seed")
    second = compare_shadow_variants(_observations(), seed="fixed-seed")

    assert first == second
    assert first.metrics[0].v1_minus_v0 > 0.0


def test_shadow_web_calls_zero() -> None:
    with pytest.raises(ValueError):
        ShadowVariantObservation.model_validate(
            {
                **_observations()[0].model_dump(mode="json"),
                "blind_web_call_count": 1,
            }
        )
