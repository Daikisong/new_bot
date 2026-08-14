"""Strict contracts for Phase 8 shadow replay evaluation."""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    NumericDistribution,
    Sha256,
    StrictMemoryContextModel,
)
from news_scalping_lab.utils import as_kst

ShadowArmId = Literal["A", "B", "C", "D", "E", "F"]
ShadowDatasetSplitName = Literal["BUILD", "CALIBRATION", "HOLDOUT"]
ShadowDatasetKind = Literal["SYNTHETIC_CONTRACT", "SEALED_HISTORICAL_REPLAY"]
ShadowConfidenceLabel = Literal[
    "very_high",
    "high",
    "medium",
    "low",
    "speculative",
]

SHADOW_ARM_IDS: tuple[str, ...] = ("A", "B", "C", "D", "E", "F")
SHADOW_ARM_FEATURES: dict[str, tuple[bool, bool, bool, bool, bool]] = {
    "A": (False, False, False, False, False),
    "B": (True, False, False, False, False),
    "C": (False, True, True, False, False),
    "D": (False, True, False, True, False),
    "E": (False, True, True, True, False),
    "F": (False, True, True, True, True),
}


class ShadowSplitAttestation(StrictMemoryContextModel):
    schema_version: Literal["nslab.shadow_split_attestation.v1"] = (
        "nslab.shadow_split_attestation.v1"
    )
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    issued_at: AwareDatetime
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    commitment_sha256: Sha256
    signature: Sha256


class ShadowDatasetAttestation(StrictMemoryContextModel):
    schema_version: Literal["nslab.shadow_dataset_attestation.v1"] = (
        "nslab.shadow_dataset_attestation.v1"
    )
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    issued_at: AwareDatetime
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    commitment_sha256: Sha256
    signature: Sha256


class ShadowArmAttestation(StrictMemoryContextModel):
    schema_version: Literal["nslab.shadow_arm_attestation.v1"] = (
        "nslab.shadow_arm_attestation.v1"
    )
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    issued_at: AwareDatetime
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    commitment_sha256: Sha256
    signature: Sha256


class ShadowTruthAttestation(StrictMemoryContextModel):
    schema_version: Literal["nslab.shadow_truth_attestation.v1"] = (
        "nslab.shadow_truth_attestation.v1"
    )
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    issued_at: AwareDatetime
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    commitment_sha256: Sha256
    signature: Sha256


class ShadowLoadAttestation(StrictMemoryContextModel):
    schema_version: Literal["nslab.shadow_load_attestation.v1"] = (
        "nslab.shadow_load_attestation.v1"
    )
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    issued_at: AwareDatetime
    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    commitment_sha256: Sha256
    signature: Sha256


class ShadowArmFeatures(StrictMemoryContextModel):
    legacy_top3: bool
    memory_cells: bool
    population_statistics: bool
    representatives: bool
    adaptive_drill_down: bool

    def values(self) -> tuple[bool, bool, bool, bool, bool]:
        return (
            self.legacy_top3,
            self.memory_cells,
            self.population_statistics,
            self.representatives,
            self.adaptive_drill_down,
        )


class ShadowAsOfSnapshot(StrictMemoryContextModel):
    snapshot_kind: Literal["LEGACY_TOP3_INDEX", "PRODUCTION_MEMORY_CELLS"]
    snapshot_id: str
    as_of_cutoff: AwareDatetime
    corpus_manifest_sha256: Sha256
    source_generation_sha256: Sha256
    embedding_model: str
    clustering_version: str
    normalizer_version: str
    snapshot_manifest: ArtifactReference
    brain_version: str
    brain_manifest: ArtifactReference
    immutable: bool = True

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        required = (
            self.snapshot_id,
            self.embedding_model,
            self.clustering_version,
            self.normalizer_version,
            self.brain_version,
            self.snapshot_manifest.artifact_path,
            self.brain_manifest.artifact_path,
        )
        if any(not value.strip() for value in required):
            raise ValueError("shadow snapshot identity must be non-empty")
        if not self.immutable:
            raise ValueError("historical replay requires an immutable as-of snapshot")
        return self


class ShadowCandidateObservation(StrictMemoryContextModel):
    rank: int = Field(ge=1, le=20)
    ticker: str
    company_name: str
    confidence_label: ShadowConfidenceLabel
    claimed_theme_id: str | None = None
    claims_news_cause: bool = False
    memory_record_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        if not self.ticker.strip() or not self.company_name.strip():
            raise ValueError("shadow candidate identity must be non-empty")
        if self.claimed_theme_id is not None and not self.claimed_theme_id.strip():
            raise ValueError("claimed_theme_id cannot be blank")
        if len(self.memory_record_ids) != len(set(self.memory_record_ids)):
            raise ValueError("candidate memory record ids must be unique")
        return self


class ShadowRetrievedRecord(StrictMemoryContextModel):
    record_id: str
    independent_unit_id: str
    record_type: str
    memory_lanes: list[str] = Field(default_factory=list)
    evidence_polarity: str
    ticker: str | None = None
    trade_date: date
    available_from: AwareDatetime
    regime_cluster: str

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        required = (
            self.record_id,
            self.independent_unit_id,
            self.record_type,
            self.evidence_polarity,
            self.regime_cluster,
        )
        if any(not value.strip() for value in required):
            raise ValueError("shadow retrieved record identity must be non-empty")
        if not self.memory_lanes or any(not value.strip() for value in self.memory_lanes):
            raise ValueError("shadow retrieved records require memory lanes")
        if len(self.memory_lanes) != len(set(self.memory_lanes)):
            raise ValueError("shadow retrieved record lanes must be unique")
        if self.ticker is not None and not self.ticker.strip():
            raise ValueError("shadow retrieved record ticker cannot be blank")
        return self


class ShadowSystemObservation(StrictMemoryContextModel):
    pre_llm_latency_ms: float = Field(ge=0.0)
    daily_analysis_latency_ms: float = Field(ge=0.0)
    llm_input_tokens: int = Field(ge=0)
    llm_output_tokens: int = Field(ge=0)
    embedding_query_count: int = Field(ge=0)
    cache_hit_count: int = Field(ge=0)
    cache_lookup_count: int = Field(ge=0)
    peak_memory_bytes: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0.0)
    online_full_scan_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_cache_counts(self) -> Self:
        if self.cache_hit_count > self.cache_lookup_count:
            raise ValueError("cache hits cannot exceed cache lookups")
        values = (
            self.pre_llm_latency_ms,
            self.daily_analysis_latency_ms,
            self.estimated_cost_usd,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("shadow system telemetry must be finite")
        return self


class ShadowExecutionIdentity(StrictMemoryContextModel):
    schema_version: Literal["nslab.shadow_execution_identity.v1"] = (
        "nslab.shadow_execution_identity.v1"
    )
    execution_mode: ShadowDatasetKind
    runner_protocol_version: str
    llm_provider: str
    llm_model: str
    prompt_version: str
    inference_config_sha256: Sha256
    started_at: AwareDatetime
    completed_at: AwareDatetime
    production_provider_attested: bool

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        required = (
            self.runner_protocol_version,
            self.llm_provider,
            self.llm_model,
            self.prompt_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("shadow execution identity must be non-empty")
        if as_kst(self.completed_at) < as_kst(self.started_at):
            raise ValueError("shadow execution cannot finish before it starts")
        if (
            self.execution_mode == "SEALED_HISTORICAL_REPLAY"
            and not self.production_provider_attested
        ):
            raise ValueError("historical shadow replay requires provider attestation")
        if (
            self.execution_mode == "SYNTHETIC_CONTRACT"
            and self.production_provider_attested
        ):
            raise ValueError("synthetic shadow execution cannot attest production providers")
        return self


class ShadowArmObservation(StrictMemoryContextModel):
    arm_id: ShadowArmId
    features: ShadowArmFeatures
    execution: ShadowExecutionIdentity
    execution_attestation: ShadowArmAttestation
    prediction_id: str
    candidates: list[ShadowCandidateObservation] = Field(default_factory=list, max_length=20)
    retrieved_records: list[ShadowRetrievedRecord] = Field(default_factory=list)
    as_of_snapshot: ShadowAsOfSnapshot | None = None
    source_artifacts: list[ArtifactReference] = Field(default_factory=list, min_length=1)
    telemetry: ShadowSystemObservation

    @model_validator(mode="after")
    def validate_arm(self) -> Self:
        if self.features.values() != SHADOW_ARM_FEATURES[self.arm_id]:
            raise ValueError("shadow arm feature flags do not match the canonical arm")
        if not self.prediction_id.strip():
            raise ValueError("shadow arm prediction_id must be non-empty")
        ranks = [candidate.rank for candidate in self.candidates]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("shadow candidate ranks must be contiguous and ordered")
        tickers = [candidate.ticker for candidate in self.candidates]
        if len(tickers) != len(set(tickers)):
            raise ValueError("shadow candidate tickers must be unique per arm")
        record_ids = [record.record_id for record in self.retrieved_records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("shadow retrieved record ids must be unique per arm")
        candidate_record_ids = {
            record_id
            for candidate in self.candidates
            for record_id in candidate.memory_record_ids
        }
        if not candidate_record_ids.issubset(record_ids):
            raise ValueError("candidate memory provenance must belong to the retrieved pool")
        if len({ref.artifact_path for ref in self.source_artifacts}) != len(
            self.source_artifacts
        ):
            raise ValueError("shadow arm source artifacts must be unique")
        expected_source_count = (
            1 if self.execution.execution_mode == "SYNTHETIC_CONTRACT" else 3
        )
        if len(self.source_artifacts) != expected_source_count:
            raise ValueError(
                "shadow arm source closure must contain observation only for synthetic "
                "or observation, prediction, and context for historical replay"
            )
        attested_at = as_kst(self.execution_attestation.issued_at)
        completed_at = as_kst(self.execution.completed_at)
        if not completed_at <= attested_at <= completed_at + timedelta(minutes=5):
            raise ValueError(
                "shadow arm attestation must be issued within five minutes of execution"
            )
        memory_enabled = self.arm_id != "A"
        if memory_enabled is not (self.as_of_snapshot is not None):
            raise ValueError("shadow memory arms require one as-of snapshot")
        if self.as_of_snapshot is not None:
            expected_kind = (
                "LEGACY_TOP3_INDEX"
                if self.arm_id == "B"
                else "PRODUCTION_MEMORY_CELLS"
            )
            if self.as_of_snapshot.snapshot_kind != expected_kind:
                raise ValueError("shadow arm snapshot kind does not match the arm")
        if self.arm_id == "A" and (
            self.retrieved_records
            or any(candidate.memory_record_ids for candidate in self.candidates)
        ):
            raise ValueError("no-memory arm cannot contain memory provenance")
        return self


class ShadowOutcomeTruth(StrictMemoryContextModel):
    ticker: str
    high_return_pct: float
    close_return_pct: float
    upper_limit_touched: bool
    candidate_relevant: bool
    actual_theme_id: str | None = None
    is_theme_leader: bool = False
    newsless: bool = False

    @model_validator(mode="after")
    def validate_truth(self) -> Self:
        if not self.ticker.strip():
            raise ValueError("shadow outcome ticker must be non-empty")
        if not math.isfinite(self.high_return_pct) or not math.isfinite(
            self.close_return_pct
        ):
            raise ValueError("shadow outcome returns must be finite")
        if self.actual_theme_id is not None and not self.actual_theme_id.strip():
            raise ValueError("actual_theme_id cannot be blank")
        if self.is_theme_leader and self.actual_theme_id is None:
            raise ValueError("theme leaders require an actual_theme_id")
        return self


class ShadowReplayCase(StrictMemoryContextModel):
    case_id: str
    split: ShadowDatasetSplitName
    trade_date: date
    replay_cutoff_at: AwareDatetime
    news_artifact: ArtifactReference
    truth_artifact: ArtifactReference
    truth_attestation: ShadowTruthAttestation
    postmortem_artifact: ArtifactReference | None = None
    outcome_universe_complete: bool
    outcomes: list[ShadowOutcomeTruth] = Field(min_length=1)
    known_relevant_record_ids: list[str] = Field(default_factory=list)
    negative_control_record_ids: list[str] = Field(default_factory=list)
    counterexample_record_ids: list[str] = Field(default_factory=list)
    long_tail_beneficiary_tickers: list[str] = Field(default_factory=list)
    arms: list[ShadowArmObservation] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def validate_case(self) -> Self:
        if re.fullmatch(r"[A-Za-z0-9_-]+", self.case_id) is None:
            raise ValueError("shadow replay case_id must be one canonical path segment")
        if as_kst(self.replay_cutoff_at).date() != self.trade_date:
            raise ValueError("shadow replay cutoff date must equal trade_date")
        if as_kst(self.truth_attestation.issued_at) <= as_kst(
            self.replay_cutoff_at
        ):
            raise ValueError("shadow truth must be attested after the replay cutoff")
        if not self.outcome_universe_complete:
            raise ValueError("shadow replay requires a complete outcome universe")
        historical = (
            bool(self.arms)
            and self.arms[0].execution.execution_mode
            == "SEALED_HISTORICAL_REPLAY"
        )
        if historical is not (self.postmortem_artifact is not None):
            raise ValueError(
                "historical shadow replay requires one postmortem artifact"
            )
        truth_tickers = [outcome.ticker for outcome in self.outcomes]
        if len(truth_tickers) != len(set(truth_tickers)):
            raise ValueError("shadow outcome tickers must be unique")
        if [arm.arm_id for arm in self.arms] != list(SHADOW_ARM_IDS):
            raise ValueError("shadow replay arms must be ordered A through F exactly once")
        prediction_ids = [arm.prediction_id for arm in self.arms]
        if len(prediction_ids) != len(set(prediction_ids)):
            raise ValueError("shadow arm prediction ids must be unique")
        executions = [arm.execution for arm in self.arms]
        execution_contracts = {
            (
                item.execution_mode,
                item.runner_protocol_version,
                item.llm_provider,
                item.llm_model,
                item.prompt_version,
                item.inference_config_sha256,
                item.production_provider_attested,
            )
            for item in executions
        }
        if len(execution_contracts) != 1:
            raise ValueError("shadow arms must use one execution contract")
        production_snapshots = [
            arm.as_of_snapshot.model_dump(mode="json")
            for arm in self.arms
            if arm.arm_id in {"C", "D", "E", "F"}
            and arm.as_of_snapshot is not None
        ]
        if len(production_snapshots) != 4 or any(
            snapshot != production_snapshots[0]
            for snapshot in production_snapshots[1:]
        ):
            raise ValueError("shadow C-F arms must share one production snapshot")
        memory_snapshots = [
            arm.as_of_snapshot
            for arm in self.arms
            if arm.arm_id in {"B", "C", "D", "E", "F"}
            and arm.as_of_snapshot is not None
        ]
        comparison_identities = {
            (
                as_kst(snapshot.as_of_cutoff),
                snapshot.corpus_manifest_sha256,
                snapshot.source_generation_sha256,
                snapshot.brain_version,
                snapshot.brain_manifest.artifact_path,
                snapshot.brain_manifest.sha256,
            )
            for snapshot in memory_snapshots
        }
        if len(memory_snapshots) != 5 or len(comparison_identities) != 1:
            raise ValueError(
                "shadow B-F arms must share corpus generation cutoff and brain"
            )
        truth_set = set(truth_tickers)
        for arm in self.arms:
            if any(candidate.ticker not in truth_set for candidate in arm.candidates):
                raise ValueError("shadow candidates must be covered by the outcome universe")
            snapshot = arm.as_of_snapshot
            if snapshot is not None and as_kst(snapshot.as_of_cutoff) > as_kst(
                self.replay_cutoff_at
            ):
                raise ValueError("shadow replay snapshot is after the replay cutoff")
            if any(
                as_kst(record.available_from) > as_kst(self.replay_cutoff_at)
                or record.trade_date > self.trade_date
                for record in arm.retrieved_records
            ):
                raise ValueError("shadow replay retrieved cutoff-after memory")
        sets = (
            self.known_relevant_record_ids,
            self.negative_control_record_ids,
            self.counterexample_record_ids,
            self.long_tail_beneficiary_tickers,
        )
        if any(len(values) != len(set(values)) for values in sets):
            raise ValueError("shadow truth identity lists must be unique")
        return self


class ShadowDatasetSplit(StrictMemoryContextModel):
    build_start: date
    build_end: date
    calibration_start: date
    calibration_end: date
    holdout_start: date
    holdout_end: date
    calibration_dates: list[date] = Field(min_length=1)
    holdout_dates: list[date] = Field(min_length=1)
    sealed_at: AwareDatetime
    pre_registration_attestation: ShadowSplitAttestation
    split_manifest: ArtifactReference

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if not (
            self.build_start
            <= self.build_end
            < self.calibration_start
            <= self.calibration_end
            < self.holdout_start
            <= self.holdout_end
        ):
            raise ValueError("shadow dataset ranges must be ordered and disjoint")
        if as_kst(self.sealed_at).date() >= self.calibration_start:
            raise ValueError("shadow split must be sealed before calibration starts")
        if as_kst(self.pre_registration_attestation.issued_at) != as_kst(
            self.sealed_at
        ):
            raise ValueError("shadow split sealed_at must equal attestation issued_at")
        for values, start, end, label in (
            (
                self.calibration_dates,
                self.calibration_start,
                self.calibration_end,
                "calibration",
            ),
            (self.holdout_dates, self.holdout_start, self.holdout_end, "holdout"),
        ):
            if values != sorted(set(values)) or any(
                value < start or value > end for value in values
            ):
                raise ValueError(f"shadow {label} dates must be unique, ordered, and in range")
        return self

    def split_for(self, trade_date: date) -> str | None:
        if self.build_start <= trade_date <= self.build_end:
            return "BUILD"
        if self.calibration_start <= trade_date <= self.calibration_end:
            return "CALIBRATION"
        if self.holdout_start <= trade_date <= self.holdout_end:
            return "HOLDOUT"
        return None


class ShadowLoadProfile(StrictMemoryContextModel):
    record_count: Literal[50_000, 200_000, 600_000]
    measured: bool
    production_shape: bool
    real_embedding_provider: bool
    embedding_dimensions: int = Field(ge=0)
    embedding_provider: str
    embedding_model: str
    profiler_version: str
    workload_sha256: Sha256 | None = None
    workload_artifact: ArtifactReference | None = None
    load_attestation: ShadowLoadAttestation | None = None
    sample_run_ids: list[str] = Field(default_factory=list)
    sample_started_at: list[AwareDatetime] = Field(default_factory=list)
    sample_completed_at: list[AwareDatetime] = Field(default_factory=list)
    sample_artifacts: list[ArtifactReference] = Field(default_factory=list)
    pre_llm_latency_samples_ms: list[float] = Field(default_factory=list)
    daily_analysis_latency_samples_ms: list[float] = Field(default_factory=list)
    peak_memory_samples_bytes: list[int] = Field(default_factory=list)
    online_full_scan_samples: list[int] = Field(default_factory=list)
    pre_llm_latency_ms: NumericDistribution
    daily_analysis_latency_ms: NumericDistribution
    peak_memory_bytes: int = Field(ge=0)
    online_full_scan_count: int = Field(ge=0)
    profile_artifact: ArtifactReference | None = None
    source_snapshot_manifest: ArtifactReference | None = None
    source_snapshot_id: str | None = None
    source_generation_sha256: Sha256 | None = None
    corpus_manifest_sha256: Sha256 | None = None
    blocker_reason: str | None = None

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        if self.measured:
            if (
                self.profile_artifact is None
                or self.source_snapshot_manifest is None
                or self.source_snapshot_id is None
                or self.source_generation_sha256 is None
                or self.corpus_manifest_sha256 is None
                or self.workload_artifact is None
                or self.load_attestation is None
                or self.blocker_reason is not None
            ):
                raise ValueError(
                    "measured load profiles require profile and snapshot evidence"
                )
            if not self.profiler_version.strip() or self.workload_sha256 is None:
                raise ValueError("measured load profiles require workload identity")
            if (
                len(self.pre_llm_latency_samples_ms) < 5
                or len(self.daily_analysis_latency_samples_ms) < 5
                or len(self.peak_memory_samples_bytes) < 5
                or len(self.online_full_scan_samples) < 5
            ):
                raise ValueError("measured load profiles require at least five raw samples")
            if any(not value.strip() for value in (self.embedding_provider, self.embedding_model)):
                raise ValueError("measured load profiles require provider identity")
            sample_count = len(self.pre_llm_latency_samples_ms)
            if (
                len(self.daily_analysis_latency_samples_ms) != sample_count
                or len(self.peak_memory_samples_bytes) != sample_count
                or len(self.online_full_scan_samples) != sample_count
                or len(self.sample_run_ids) != sample_count
                or len(self.sample_started_at) != sample_count
                or len(self.sample_completed_at) != sample_count
                or len(self.sample_artifacts) != sample_count
                or len(set(self.sample_run_ids)) != sample_count
                or len(
                    {artifact.artifact_path for artifact in self.sample_artifacts}
                )
                != sample_count
                or any(not value.strip() for value in self.sample_run_ids)
                or any(
                    as_kst(completed) < as_kst(started)
                    for started, completed in zip(
                        self.sample_started_at,
                        self.sample_completed_at,
                        strict=True,
                    )
                )
            ):
                raise ValueError("load profile execution ledger is invalid")
            latest_completed = max(
                as_kst(value) for value in self.sample_completed_at
            )
            attested_at = as_kst(self.load_attestation.issued_at)
            if not latest_completed <= attested_at <= latest_completed + timedelta(
                minutes=5
            ):
                raise ValueError(
                    "load attestation must be issued within five minutes of completion"
                )
        else:
            if self.blocker_reason is None or not self.blocker_reason.strip():
                raise ValueError("unmeasured load profiles require a blocker reason")
            if any(
                (
                    self.pre_llm_latency_samples_ms,
                    self.daily_analysis_latency_samples_ms,
                    self.peak_memory_samples_bytes,
                    self.online_full_scan_samples,
                )
            ):
                raise ValueError("unmeasured load profiles cannot contain samples")
            if any(
                value is not None
                for value in (
                    self.profile_artifact,
                    self.source_snapshot_manifest,
                    self.source_snapshot_id,
                    self.source_generation_sha256,
                    self.corpus_manifest_sha256,
                    self.workload_sha256,
                    self.workload_artifact,
                )
            ):
                raise ValueError("unmeasured load profiles cannot claim source evidence")
            if (
                self.sample_run_ids
                or self.sample_started_at
                or self.sample_completed_at
                or self.sample_artifacts
                or self.load_attestation is not None
            ):
                raise ValueError("unmeasured load profiles cannot contain execution ledger")
        if any(value < 0 for value in self.peak_memory_samples_bytes):
            raise ValueError("load profile memory samples must be non-negative")
        if any(value < 0 for value in self.online_full_scan_samples):
            raise ValueError("load profile scan samples must be non-negative")
        if self.pre_llm_latency_ms != _numeric_distribution(
            self.pre_llm_latency_samples_ms
        ) or self.daily_analysis_latency_ms != _numeric_distribution(
            self.daily_analysis_latency_samples_ms
        ):
            raise ValueError("load profile latency summaries do not match raw samples")
        expected_peak = max(self.peak_memory_samples_bytes, default=0)
        if self.peak_memory_bytes != expected_peak:
            raise ValueError("load profile peak memory does not match raw samples")
        if self.online_full_scan_count != sum(self.online_full_scan_samples):
            raise ValueError("load profile scan count does not match raw samples")
        return self


class ShadowReplayDataset(StrictMemoryContextModel):
    schema_version: Literal["nslab.shadow_replay_dataset.v1"] = (
        "nslab.shadow_replay_dataset.v1"
    )
    dataset_id: str
    dataset_kind: ShadowDatasetKind
    created_at: AwareDatetime
    dataset_attestation: ShadowDatasetAttestation
    split: ShadowDatasetSplit
    cases: list[ShadowReplayCase] = Field(min_length=2)
    load_profiles: list[ShadowLoadProfile] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_dataset(self) -> Self:
        if not self.dataset_id.strip():
            raise ValueError("shadow replay dataset_id must be non-empty")
        if as_kst(self.dataset_attestation.issued_at) != as_kst(self.created_at):
            raise ValueError("shadow dataset created_at must equal attestation issued_at")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("shadow replay case ids must be unique")
        dates = [case.trade_date for case in self.cases]
        if len(dates) != len(set(dates)):
            raise ValueError("shadow replay trade dates must be unique")
        for case in self.cases:
            expected = self.split.split_for(case.trade_date)
            if expected != case.split:
                raise ValueError("shadow case split does not match sealed date ranges")
            if any(
                arm.execution.execution_mode != self.dataset_kind
                for arm in case.arms
            ):
                raise ValueError("shadow case execution mode must match dataset kind")
            if any(
                as_kst(arm.execution.completed_at) > as_kst(self.created_at)
                for arm in case.arms
            ):
                raise ValueError("shadow dataset cannot predate arm execution")
            if as_kst(case.truth_attestation.issued_at) > as_kst(self.created_at):
                raise ValueError("shadow dataset cannot predate truth attestation")
            if any(
                as_kst(arm.execution.started_at) < as_kst(self.split.sealed_at)
                for arm in case.arms
            ):
                raise ValueError("shadow arm execution cannot predate split sealing")
        for profile in self.load_profiles:
            if not profile.measured:
                continue
            if any(
                as_kst(value) > as_kst(self.created_at)
                for value in profile.sample_completed_at
            ):
                raise ValueError("shadow dataset cannot predate load samples")
            if (
                profile.load_attestation is None
                or as_kst(profile.load_attestation.issued_at)
                > as_kst(self.created_at)
            ):
                raise ValueError("shadow dataset cannot predate load attestation")
        observed_splits = {case.split for case in self.cases}
        if not {"CALIBRATION", "HOLDOUT"}.issubset(observed_splits):
            raise ValueError("shadow replay requires calibration and holdout cases")
        for split_name, expected_dates in (
            ("CALIBRATION", self.split.calibration_dates),
            ("HOLDOUT", self.split.holdout_dates),
        ):
            observed_dates = sorted(
                case.trade_date for case in self.cases if case.split == split_name
            )
            if observed_dates != expected_dates:
                raise ValueError(f"shadow {split_name.lower()} date coverage is incomplete")
        if [profile.record_count for profile in self.load_profiles] != [
            50_000,
            200_000,
            600_000,
        ]:
            raise ValueError("shadow load profiles must be ordered 50k, 200k, 600k")
        return self


class ShadowRate(StrictMemoryContextModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_rate(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("shadow rate numerator cannot exceed denominator")
        expected = None if self.denominator == 0 else self.numerator / self.denominator
        if self.value != expected:
            raise ValueError("shadow rate value does not match its counts")
        return self


class ShadowCalibrationBucket(StrictMemoryContextModel):
    arm_id: ShadowArmId
    confidence_label: ShadowConfidenceLabel
    calibration_observation_count: int = Field(ge=0)
    calibrated_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    holdout_observation_count: int = Field(ge=0)
    holdout_positive_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_bucket(self) -> Self:
        if self.holdout_positive_count > self.holdout_observation_count:
            raise ValueError("calibration bucket positives cannot exceed observations")
        if (self.calibration_observation_count == 0) is not (
            self.calibrated_probability is None
        ):
            raise ValueError("calibrated probability requires calibration observations")
        return self


class ShadowArmMetrics(StrictMemoryContextModel):
    arm_id: ShadowArmId
    holdout_case_count: int = Field(ge=0)
    candidate_recall_at_5: ShadowRate
    candidate_recall_at_10: ShadowRate
    candidate_recall_at_20: ShadowRate
    high_10_recall: ShadowRate
    high_20_recall: ShadowRate
    false_positive_rate: ShadowRate
    leader_error_rate: ShadowRate
    theme_over_expansion_rate: ShadowRate
    newsless_hallucination_rate: ShadowRate
    known_relevant_record_recall: ShadowRate
    negative_control_inclusion_rate: ShadowRate
    counterexample_inclusion_rate: ShadowRate
    long_tail_beneficiary_recall: ShadowRate
    issuer_day_duplicate_rate: float = Field(ge=0.0, le=1.0)
    unique_year_count: int = Field(ge=0)
    unique_regime_count: int = Field(ge=0)
    brier_score: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_calibration_error: float | None = Field(default=None, ge=0.0, le=1.0)
    calibration_coverage: ShadowRate
    pre_llm_latency_ms: NumericDistribution
    daily_analysis_latency_ms: NumericDistribution
    llm_input_tokens: NumericDistribution
    llm_output_tokens: NumericDistribution
    embedding_query_count: NumericDistribution
    cache_hit_rate: ShadowRate
    peak_memory_bytes: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0.0)
    online_full_scan_count: int = Field(ge=0)


class ShadowBiasAudit(StrictMemoryContextModel):
    schema_version: Literal["nslab.shadow_bias_audit.v1"] = (
        "nslab.shadow_bias_audit.v1"
    )
    checks: dict[str, bool]
    passed: bool
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_audit(self) -> Self:
        expected_passed = bool(self.checks) and all(self.checks.values())
        if self.passed is not expected_passed:
            raise ValueError("shadow bias audit status does not match checks")
        if self.passed is not (not self.errors):
            raise ValueError("shadow bias audit errors do not match status")
        return self


class ShadowExitGate(StrictMemoryContextModel):
    checks: dict[str, bool]
    passed: bool
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gate(self) -> Self:
        expected_passed = bool(self.checks) and all(self.checks.values())
        if self.passed is not expected_passed:
            raise ValueError("shadow exit gate status does not match checks")
        if self.passed is not (not self.blockers):
            raise ValueError("shadow exit gate blockers do not match status")
        return self


class ShadowEvaluationManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.shadow_evaluation_manifest.v1"] = (
        "nslab.shadow_evaluation_manifest.v1"
    )
    evaluation_id: str
    created_at: AwareDatetime
    dataset_id: str
    dataset_sha256: Sha256
    protocol_version: str
    metric_version: str
    calibration_version: str
    bias_audit_version: str
    system_budget_version: str
    calibration_case_count: int = Field(ge=0)
    holdout_case_count: int = Field(ge=0)
    arm_metrics: list[ShadowArmMetrics] = Field(min_length=6, max_length=6)
    calibration_buckets: ArtifactReference
    case_results: ArtifactReference
    source_dataset: ArtifactReference
    bias_audit: ShadowBiasAudit
    load_profiles: list[ShadowLoadProfile] = Field(min_length=3, max_length=3)
    exit_gate: ShadowExitGate
    production_ready: bool

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if not self.evaluation_id.strip() or not self.dataset_id.strip():
            raise ValueError("shadow evaluation identity must be non-empty")
        if [metrics.arm_id for metrics in self.arm_metrics] != list(SHADOW_ARM_IDS):
            raise ValueError("shadow arm metrics must be ordered A through F")
        if [profile.record_count for profile in self.load_profiles] != [
            50_000,
            200_000,
            600_000,
        ]:
            raise ValueError("shadow manifest load profiles must be ordered")
        if self.production_ready is not self.exit_gate.passed:
            raise ValueError("shadow production readiness must equal exit gate")
        return self


def _numeric_distribution(values: list[float]) -> NumericDistribution:
    ordered = sorted(float(value) for value in values)
    if any(not math.isfinite(value) or value < 0.0 for value in ordered):
        raise ValueError("load profile samples must be finite and non-negative")
    if not ordered:
        return NumericDistribution(
            count=0,
            minimum=0.0,
            mean=0.0,
            p50=0.0,
            p95=0.0,
            p99=0.0,
            maximum=0.0,
        )
    return NumericDistribution(
        count=len(ordered),
        minimum=ordered[0],
        mean=sum(ordered) / len(ordered),
        p50=_percentile(ordered, 0.50),
        p95=_percentile(ordered, 0.95),
        p99=_percentile(ordered, 0.99),
        maximum=ordered[-1],
    )


def _percentile(values: list[float], quantile: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction
