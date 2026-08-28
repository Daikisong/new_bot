"""Paired V0/V1/V2 comparison contracts layered over Phase 8 feature arms."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from news_scalping_lab.contracts.shadow_evaluation import ShadowArmId
from news_scalping_lab.utils import canonical_json, sha256_text

ShadowVariantId = Literal["V0", "V1", "V2"]
ShadowVariantSplit = Literal["CALIBRATION", "HOLDOUT"]
SHADOW_VARIANT_IDS: tuple[ShadowVariantId, ...] = ("V0", "V1", "V2")


class ShadowVariantObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    split: ShadowVariantSplit
    arm_id: ShadowArmId
    variant_id: ShadowVariantId
    current_news_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truth_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prediction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieval_trace_count: int = Field(ge=0)
    blind_web_call_count: Literal[0] = 0
    metrics: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if not self.case_id.strip() or not self.metrics:
            raise ValueError("shadow variant observation identity and metrics are required")
        if any(
            not name.strip() or not math.isfinite(value)
            for name, value in self.metrics.items()
        ):
            raise ValueError("shadow variant metrics must be named and finite")
        if self.arm_id != "A" and self.retrieval_trace_count < 1:
            raise ValueError("memory-enabled shadow arms require retrieval traces")
        return self


class ShadowVariantMetricComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arm_id: ShadowArmId
    metric: str
    case_count: int = Field(ge=1)
    means: dict[ShadowVariantId, float]
    v1_minus_v0: float
    v2_minus_v1: float
    v2_minus_v0: float
    v1_minus_v0_bootstrap_direction_rate: float = Field(ge=0.0, le=1.0)
    v2_minus_v1_bootstrap_direction_rate: float = Field(ge=0.0, le=1.0)


class ShadowVariantComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["nslab.shadow_variant_comparison.v1"] = (
        "nslab.shadow_variant_comparison.v1"
    )
    seed: str
    seed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1)
    observation_count: int = Field(ge=1)
    current_news_truth_parity: Literal[True] = True
    shadow_web_calls_zero: Literal[True] = True
    paired_metrics_deterministic: Literal[True] = True
    metrics: list[ShadowVariantMetricComparison]


def compare_shadow_variants(
    observations: list[ShadowVariantObservation],
    *,
    seed: str,
    bootstrap_iterations: int = 2_000,
) -> ShadowVariantComparison:
    if not seed.strip() or bootstrap_iterations < 100:
        raise ValueError("shadow variant comparison requires a seed and bootstrap budget")
    grouped: dict[
        tuple[str, ShadowArmId],
        dict[ShadowVariantId, ShadowVariantObservation],
    ] = defaultdict(dict)
    for observation in observations:
        key = (observation.case_id, observation.arm_id)
        if observation.variant_id in grouped[key]:
            raise ValueError("shadow variant observations must be unique per case and arm")
        grouped[key][observation.variant_id] = observation
    if not grouped:
        raise ValueError("shadow variant comparison requires observations")
    for key, variants in grouped.items():
        if tuple(sorted(variants)) != SHADOW_VARIANT_IDS:
            raise ValueError(f"shadow case/arm is missing V0/V1/V2: {key}")
        news_hashes = {item.current_news_sha256 for item in variants.values()}
        truth_hashes = {item.truth_sha256 for item in variants.values()}
        splits = {item.split for item in variants.values()}
        metric_sets = {tuple(sorted(item.metrics)) for item in variants.values()}
        if len(news_hashes) != 1 or len(truth_hashes) != 1 or len(splits) != 1:
            raise ValueError("V0/V1/V2 must use identical current news, truth, and split")
        if len(metric_sets) != 1:
            raise ValueError("V0/V1/V2 metric surfaces must be identical")

    comparisons: list[ShadowVariantMetricComparison] = []
    for arm_id in ("A", "B", "C", "D", "E", "F"):
        arm_groups = [
            variants for (_case_id, arm), variants in sorted(grouped.items()) if arm == arm_id
        ]
        if not arm_groups:
            continue
        metric_names = sorted(next(iter(arm_groups[0].values())).metrics)
        for metric in metric_names:
            values = {
                variant: [rows[variant].metrics[metric] for rows in arm_groups]
                for variant in SHADOW_VARIANT_IDS
            }
            means = {
                variant: sum(rows) / len(rows) for variant, rows in values.items()
            }
            v1_v0 = [
                right - left
                for left, right in zip(values["V0"], values["V1"], strict=True)
            ]
            v2_v1 = [
                right - left
                for left, right in zip(values["V1"], values["V2"], strict=True)
            ]
            comparisons.append(
                ShadowVariantMetricComparison(
                    arm_id=arm_id,
                    metric=metric,
                    case_count=len(arm_groups),
                    means=means,
                    v1_minus_v0=means["V1"] - means["V0"],
                    v2_minus_v1=means["V2"] - means["V1"],
                    v2_minus_v0=means["V2"] - means["V0"],
                    v1_minus_v0_bootstrap_direction_rate=(
                        _bootstrap_direction_rate(
                            v1_v0,
                            seed=f"{seed}|{arm_id}|{metric}|V1-V0",
                            iterations=bootstrap_iterations,
                        )
                    ),
                    v2_minus_v1_bootstrap_direction_rate=(
                        _bootstrap_direction_rate(
                            v2_v1,
                            seed=f"{seed}|{arm_id}|{metric}|V2-V1",
                            iterations=bootstrap_iterations,
                        )
                    ),
                )
            )
    return ShadowVariantComparison(
        seed=seed,
        seed_sha256=sha256_text(seed),
        case_count=len({case_id for case_id, _arm in grouped}),
        observation_count=len(observations),
        metrics=comparisons,
    )


def _bootstrap_direction_rate(
    differences: list[float],
    *,
    seed: str,
    iterations: int,
) -> float:
    if not differences:
        raise ValueError("paired bootstrap requires observations")
    nonnegative = 0
    count = len(differences)
    for iteration in range(iterations):
        sampled = [
            differences[
                int(
                    sha256_text(
                        canonical_json([seed, iteration, position])
                    )[:16],
                    16,
                )
                % count
            ]
            for position in range(count)
        ]
        if sum(sampled) / count >= 0.0:
            nonnegative += 1
    return nonnegative / iterations
