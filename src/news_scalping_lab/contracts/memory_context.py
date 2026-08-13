"""Strict contracts for population-aware memory retrieval.

These models define the data boundaries used by the phased memory upgrade.
They intentionally contain references, counts, and hashes rather than the full
research corpus so a daily context remains bounded as the corpus grows.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


class StrictMemoryContextModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=True,
    )


EvidencePolarity = Literal[
    "POSITIVE",
    "NEGATIVE",
    "NEAR_MISS",
    "UNEXPLAINED",
    "CONTEXT",
    "UNKNOWN",
]
RoutingDisposition = Literal["REASONING", "CONTEXT", "AUDIT", "QUARANTINED"]
NewsDisposition = Literal[
    "MATERIAL_FULL_RETRIEVAL",
    "MARKET_CONTEXT",
    "AUDIT_ONLY",
    "DUPLICATE",
]
IndependentUnitType = Literal[
    "event-issuer-day",
    "issuer-day",
    "theme-day",
    "theme-day-ticker-day",
    "theme-day-pair",
    "ticker-day",
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ArtifactReference(StrictMemoryContextModel):
    schema_version: Literal["nslab.memory_artifact_reference.v1"] = (
        "nslab.memory_artifact_reference.v1"
    )
    artifact_path: str
    sha256: Sha256
    item_count: int = Field(ge=0)


class RecordRoutingMetadata(StrictMemoryContextModel):
    """Four independent axes for using a stored brain record."""

    schema_version: Literal["nslab.record_routing_metadata.v1"] = (
        "nslab.record_routing_metadata.v1"
    )
    record_id: str
    record_type: str
    available_from: AwareDatetime
    evidence_polarity: EvidencePolarity
    training_eligible: bool
    label_quality: str
    routing_disposition: RoutingDisposition
    memory_lanes: list[str] = Field(default_factory=list)
    polarity_classifier_version: str
    threshold_source: str
    threshold_role: Literal["retrieval_calibration_only", "explicit_label"]
    provenance_source_ids: list[str] = Field(default_factory=list)


class NewsRowCoverage(StrictMemoryContextModel):
    row_number: int = Field(ge=1)
    event_id: str
    primary_cluster_id: str | None = None
    duplicate_parent_cluster_id: str | None = None
    disposition: NewsDisposition

    @model_validator(mode="after")
    def validate_assignment(self) -> Self:
        if self.disposition == "DUPLICATE":
            if self.duplicate_parent_cluster_id is None or self.primary_cluster_id is not None:
                raise ValueError(
                    "duplicate rows require only duplicate_parent_cluster_id"
                )
        elif self.primary_cluster_id is None or self.duplicate_parent_cluster_id is not None:
            raise ValueError("non-duplicate rows require only primary_cluster_id")
        return self


class NewsCoverageManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.news_coverage_manifest.v1"] = (
        "nslab.news_coverage_manifest.v1"
    )
    run_id: str
    trade_date: date
    cutoff_at: AwareDatetime
    input_news_sha256: Sha256
    input_row_count: int = Field(ge=0)
    covered_row_count: int = Field(ge=0)
    missing_row_count: int = Field(ge=0)
    duplicate_assignment_count: int = Field(ge=0)
    disposition_counts: dict[str, int] = Field(default_factory=dict)
    row_coverage_sha256: Sha256
    rows: list[NewsRowCoverage] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        if self.input_row_count != self.covered_row_count + self.missing_row_count:
            raise ValueError("input rows must equal covered plus missing rows")
        if self.covered_row_count != len(self.rows):
            raise ValueError("covered_row_count must equal rows length")
        if len({row.row_number for row in self.rows}) != len(self.rows):
            raise ValueError("news row numbers must be unique")
        observed = _string_counts(row.disposition for row in self.rows)
        if _positive_counts(self.disposition_counts) != observed:
            raise ValueError("disposition_counts must match covered rows")
        if self.duplicate_assignment_count != observed.get("DUPLICATE", 0):
            raise ValueError("duplicate_assignment_count must match duplicate rows")
        if any(row.row_number > self.input_row_count for row in self.rows):
            raise ValueError("news row number exceeds input_row_count")
        return self


class EventClusterEntry(StrictMemoryContextModel):
    cluster_id: str
    representative_event_id: str
    member_event_ids: list[str] = Field(default_factory=list)
    member_row_numbers: list[Annotated[int, Field(ge=1)]] = Field(
        default_factory=list
    )
    disposition: NewsDisposition
    exact_duplicate_count: int = Field(default=0, ge=0)
    semantic_duplicate_count: int = Field(default=0, ge=0)
    cluster_signature_sha256: Sha256

    @model_validator(mode="after")
    def validate_membership(self) -> Self:
        if not self.member_event_ids or not self.member_row_numbers:
            raise ValueError("clusters require event and row members")
        if self.representative_event_id not in self.member_event_ids:
            raise ValueError("representative_event_id must be a cluster member")
        if len(set(self.member_event_ids)) != len(self.member_event_ids):
            raise ValueError("cluster event members must be unique")
        if len(set(self.member_row_numbers)) != len(self.member_row_numbers):
            raise ValueError("cluster row members must be unique")
        if self.exact_duplicate_count + self.semantic_duplicate_count > (
            len(self.member_row_numbers) - 1
        ):
            raise ValueError("duplicate counts exceed non-representative members")
        return self


class EventClusterManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.event_cluster_manifest.v2"] = (
        "nslab.event_cluster_manifest.v2"
    )
    run_id: str
    trade_date: date
    cutoff_at: AwareDatetime
    clustering_version: str
    embedding_provider: str | None = None
    input_row_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    material_cluster_count: int = Field(ge=0)
    unassigned_row_count: int = Field(ge=0)
    duplicate_assignment_count: int = Field(ge=0)
    clusters: list[EventClusterEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cluster_counts(self) -> Self:
        if self.cluster_count != len(self.clusters):
            raise ValueError("cluster_count must equal clusters length")
        observed_material = sum(
            1
            for cluster in self.clusters
            if cluster.disposition == "MATERIAL_FULL_RETRIEVAL"
        )
        if self.material_cluster_count != observed_material:
            raise ValueError("material_cluster_count must match clusters")
        members = [row for cluster in self.clusters for row in cluster.member_row_numbers]
        duplicate_count = len(members) - len(set(members))
        if self.duplicate_assignment_count != duplicate_count:
            raise ValueError("duplicate_assignment_count must match cluster rows")
        if self.input_row_count != len(set(members)) + self.unassigned_row_count:
            raise ValueError("input rows must equal assigned unique rows plus unassigned")
        if any(row > self.input_row_count for row in members):
            raise ValueError("cluster row number exceeds input_row_count")
        return self


class MemoryCoverageManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.memory_coverage_manifest.v2"] = (
        "nslab.memory_coverage_manifest.v2"
    )
    run_id: str
    cutoff_at: AwareDatetime
    corpus_manifest_sha256: Sha256
    accepted_record_count: int = Field(ge=0)
    available_record_count: int = Field(ge=0)
    future_record_count: int = Field(ge=0)
    missing_record_count: int = Field(ge=0)
    unexpected_record_count: int = Field(ge=0)
    duplicate_record_count: int = Field(ge=0)
    available_record_ids: ArtifactReference
    record_hash_manifest: ArtifactReference
    coverage_complete: bool

    @model_validator(mode="after")
    def validate_record_coverage(self) -> Self:
        if self.accepted_record_count != self.available_record_count + self.future_record_count:
            raise ValueError("accepted records must equal available plus future records")
        if self.available_record_ids.item_count != self.available_record_count:
            raise ValueError("available record artifact count mismatch")
        if self.record_hash_manifest.item_count != self.available_record_count:
            raise ValueError("record hash artifact count mismatch")
        expected_complete = all(
            count == 0
            for count in (
                self.missing_record_count,
                self.unexpected_record_count,
                self.duplicate_record_count,
            )
        )
        if self.coverage_complete is not expected_complete:
            raise ValueError("coverage_complete conflicts with coverage counts")
        return self


class MemoryCellManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.memory_cell_manifest.v1"] = (
        "nslab.memory_cell_manifest.v1"
    )
    cell_id: str
    corpus_manifest_sha256: Sha256
    max_available_from: AwareDatetime
    embedding_model: str
    clustering_version: str
    normalizer_version: str
    cell_schema_version: str
    primary_member_count: int = Field(ge=0)
    secondary_member_count: int = Field(ge=0)
    independent_unit_count: int = Field(ge=0)
    membership_manifest: ArtifactReference
    parent_cell_ids: list[str] = Field(default_factory=list)
    child_cell_ids: list[str] = Field(default_factory=list)


class PopulationOutcomeSummary(StrictMemoryContextModel):
    observed_unit_count: int = Field(ge=0)
    missing_outcome_unit_count: int = Field(ge=0)
    upper_limit_touched_count: int = Field(ge=0)
    high_return_5_count: int = Field(ge=0)
    high_return_10_count: int = Field(ge=0)
    high_return_20_count: int = Field(ge=0)
    mean_high_return_pct: float | None = None
    median_high_return_pct: float | None = None
    mean_close_return_pct: float | None = None
    median_close_return_pct: float | None = None

    @model_validator(mode="after")
    def validate_outcome_counts(self) -> Self:
        if not (
            self.upper_limit_touched_count
            <= self.high_return_20_count
            <= self.high_return_10_count
            <= self.high_return_5_count
            <= self.observed_unit_count
        ):
            raise ValueError("outcome threshold counts must be nested")
        return self


class PopulationManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.population_manifest.v1"] = (
        "nslab.population_manifest.v1"
    )
    population_id: str
    run_id: str
    cluster_id: str
    cutoff_at: AwareDatetime
    corpus_manifest_sha256: Sha256
    selected_cell_ids: list[str] = Field(default_factory=list)
    membership_manifest_sha256: Sha256
    independent_unit_type: IndependentUnitType
    raw_record_count: int = Field(ge=0)
    independent_unit_count: int = Field(ge=0)
    effective_sample_size: float = Field(ge=0.0)
    polarity_counts: dict[str, int] = Field(default_factory=dict)
    eligibility_counts: dict[str, int] = Field(default_factory=dict)
    label_quality_counts: dict[str, int] = Field(default_factory=dict)
    time_slice_counts: dict[str, int] = Field(default_factory=dict)
    regime_counts: dict[str, int] = Field(default_factory=dict)
    outcome_summary: PopulationOutcomeSummary
    observed_population_rate_label: Literal["observed_population_rate"] = (
        "observed_population_rate"
    )

    @model_validator(mode="after")
    def validate_population_counts(self) -> Self:
        if self.independent_unit_count > self.raw_record_count:
            raise ValueError("independent units cannot exceed raw records")
        if self.effective_sample_size > self.independent_unit_count:
            raise ValueError("effective sample size cannot exceed independent units")
        if self.outcome_summary.observed_unit_count + (
            self.outcome_summary.missing_outcome_unit_count
        ) != self.independent_unit_count:
            raise ValueError("observed plus missing outcomes must equal independent units")
        for name, counts in (
            ("polarity", self.polarity_counts),
            ("eligibility", self.eligibility_counts),
            ("label quality", self.label_quality_counts),
        ):
            if sum(_positive_counts(counts).values()) != self.independent_unit_count:
                raise ValueError(f"{name} counts must equal independent units")
        return self


class RepresentativeStratum(StrictMemoryContextModel):
    stratum: str
    record_ids: list[str] = Field(default_factory=list)
    independent_unit_ids: list[str] = Field(default_factory=list)


class RepresentativeSetManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.representative_set_manifest.v1"] = (
        "nslab.representative_set_manifest.v1"
    )
    representative_set_id: str
    population_id: str
    population_manifest_sha256: Sha256
    selection_version: str
    selected_record_count: int = Field(ge=0)
    omitted_population_record_count: int = Field(ge=0)
    estimated_token_count: int = Field(ge=0)
    diversity_coverage_ratio: float = Field(ge=0.0, le=1.0)
    strata: list[RepresentativeStratum] = Field(default_factory=list)
    selected_record_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_selection_counts(self) -> Self:
        if self.selected_record_count != len(self.selected_record_ids):
            raise ValueError("selected_record_count must equal selected IDs")
        if len(set(self.selected_record_ids)) != len(self.selected_record_ids):
            raise ValueError("selected record IDs must be unique")
        stratum_ids = {
            record_id for stratum in self.strata for record_id in stratum.record_ids
        }
        if not stratum_ids.issubset(set(self.selected_record_ids)):
            raise ValueError("stratum records must belong to selected records")
        return self


class AdaptiveRetrievalIteration(StrictMemoryContextModel):
    iteration: int = Field(ge=0)
    trigger_reasons: list[str] = Field(default_factory=list)
    added_cell_ids: list[str] = Field(default_factory=list)
    added_record_ids: list[str] = Field(default_factory=list)
    information_gain: float = Field(ge=0.0)
    cumulative_token_count: int = Field(ge=0)


class AdaptiveRetrievalTrace(StrictMemoryContextModel):
    schema_version: Literal["nslab.adaptive_retrieval_trace.v1"] = (
        "nslab.adaptive_retrieval_trace.v1"
    )
    run_id: str
    cluster_id: str
    policy_version: str
    initial_cell_ids: list[str] = Field(default_factory=list)
    iterations: list[AdaptiveRetrievalIteration] = Field(default_factory=list)
    max_depth: int = Field(ge=0)
    max_cell_count: int = Field(ge=0)
    max_record_count: int = Field(ge=0)
    max_token_count: int = Field(ge=0)
    stopped_reason: str


class DailyMemoryContext(StrictMemoryContextModel):
    schema_version: Literal["nslab.daily_memory_context.v1"] = (
        "nslab.daily_memory_context.v1"
    )
    run_id: str
    trade_date: date
    cutoff_at: AwareDatetime
    corpus_manifest_sha256: Sha256
    news_coverage_manifest: ArtifactReference
    event_cluster_manifest: ArtifactReference
    memory_coverage_manifest: ArtifactReference
    population_manifests: list[ArtifactReference] = Field(default_factory=list)
    representative_set_manifests: list[ArtifactReference] = Field(default_factory=list)
    adaptive_retrieval_traces: list[ArtifactReference] = Field(default_factory=list)
    category_brain_manifest_sha256: Sha256 | None = None
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicting_record_ids: list[str] = Field(default_factory=list)
    unexplained_record_ids: list[str] = Field(default_factory=list)
    unresolved_disagreements: list[str] = Field(default_factory=list)
    estimated_token_count: int = Field(ge=0)
    context_complete: bool


class NumericDistribution(StrictMemoryContextModel):
    count: int = Field(ge=0)
    minimum: float = Field(ge=0.0)
    mean: float = Field(ge=0.0)
    p50: float = Field(ge=0.0)
    p95: float = Field(ge=0.0)
    p99: float = Field(ge=0.0)
    maximum: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_distribution(self) -> Self:
        if self.count == 0:
            if any(
                value != 0.0
                for value in (
                    self.minimum,
                    self.mean,
                    self.p50,
                    self.p95,
                    self.p99,
                    self.maximum,
                )
            ):
                raise ValueError("empty distributions must contain only zero values")
            return self
        if not (
            self.minimum
            <= self.p50
            <= self.p95
            <= self.p99
            <= self.maximum
        ):
            raise ValueError("distribution percentiles must be monotonic")
        if not self.minimum <= self.mean <= self.maximum:
            raise ValueError("distribution mean must be within min/max")
        return self


class IndependentUnitProfile(StrictMemoryContextModel):
    keyed_record_count: int = Field(ge=0)
    unique_unit_count: int = Field(ge=0)
    duplicate_record_count: int = Field(ge=0)
    dedup_ratio: float = Field(ge=0.0, le=1.0)


class LinearRetrievalBenchmark(StrictMemoryContextModel):
    benchmarked: bool
    algorithm: str
    source_index_status: str
    isolated_index_build_ms: float = Field(ge=0.0)
    includes_index_load_and_filter: bool
    query_count: int = Field(ge=0)
    repeat_count: int = Field(ge=0)
    scanned_record_count_per_query: int = Field(ge=0)
    latency_ms: NumericDistribution
    query_sha256s: list[Sha256] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_benchmark(self) -> Self:
        if self.query_count != len(self.query_sha256s):
            raise ValueError("query_count must equal query hashes")
        expected_samples = self.query_count * self.repeat_count
        if self.latency_ms.count != expected_samples:
            raise ValueError("latency count must equal query_count times repeats")
        if self.benchmarked is not (expected_samples > 0):
            raise ValueError("benchmarked conflicts with benchmark sample count")
        return self


class SweepBurdenEstimate(StrictMemoryContextModel):
    shard_size: int = Field(ge=1)
    accepted_episode_count: int = Field(ge=0)
    estimated_episode_shard_count: int = Field(ge=0)
    estimated_record_shard_count: int = Field(ge=0)
    estimated_total_shard_count: int = Field(ge=0)
    serialized_episode_artifact_bytes: int = Field(ge=0)
    serialized_record_artifact_bytes: int = Field(ge=0)
    serialized_total_artifact_bytes: int = Field(ge=0)
    estimated_episode_artifact_tokens: int = Field(ge=0)
    estimated_record_artifact_tokens: int = Field(ge=0)
    estimated_total_artifact_tokens: int = Field(ge=0)
    serialized_record_bytes: int = Field(ge=0)
    estimator_version: str

    @model_validator(mode="after")
    def validate_sweep_totals(self) -> Self:
        if self.estimated_total_shard_count != (
            self.estimated_episode_shard_count + self.estimated_record_shard_count
        ):
            raise ValueError("total shard count mismatch")
        if self.serialized_total_artifact_bytes != (
            self.serialized_episode_artifact_bytes
            + self.serialized_record_artifact_bytes
        ):
            raise ValueError("total sweep bytes mismatch")
        if self.estimated_total_artifact_tokens != (
            self.estimated_episode_artifact_tokens
            + self.estimated_record_artifact_tokens
        ):
            raise ValueError("total sweep tokens mismatch")
        return self


class BrainRecordCorpusProfile(StrictMemoryContextModel):
    schema_version: Literal["nslab.brain_record_corpus_profile.v1"] = (
        "nslab.brain_record_corpus_profile.v1"
    )
    source_root: str
    accepted_only: bool
    corpus_manifest_sha256: Sha256
    record_count: int = Field(ge=0)
    episode_count: int = Field(ge=0)
    training_eligible_record_count: int = Field(ge=0)
    known_typed_record_count: int = Field(ge=0)
    unknown_typed_record_count: int = Field(ge=0)
    record_counts_by_type: dict[str, int] = Field(default_factory=dict)
    record_counts_by_polarity: dict[str, int] = Field(default_factory=dict)
    record_counts_by_lane: dict[str, int] = Field(default_factory=dict)
    eligibility_polarity_crosstab: dict[str, dict[str, int]] = Field(
        default_factory=dict
    )
    outcome_field_coverage: dict[str, int] = Field(default_factory=dict)
    independent_unit_profiles: dict[str, IndependentUnitProfile] = Field(
        default_factory=dict
    )
    payload_bytes: NumericDistribution
    trade_year_counts: dict[str, int] = Field(default_factory=dict)
    available_year_counts: dict[str, int] = Field(default_factory=dict)
    regime_counts: dict[str, int] = Field(default_factory=dict)
    linear_retrieval: LinearRetrievalBenchmark
    sweep_burden: SweepBurdenEstimate

    @model_validator(mode="after")
    def validate_profile_counts(self) -> Self:
        if self.record_count != sum(_positive_counts(self.record_counts_by_type).values()):
            raise ValueError("record type counts must equal record_count")
        if self.record_count != sum(
            _positive_counts(self.record_counts_by_polarity).values()
        ):
            raise ValueError("polarity counts must equal record_count")
        if self.record_count != (
            self.known_typed_record_count + self.unknown_typed_record_count
        ):
            raise ValueError("typed status counts must equal record_count")
        eligibility_total = sum(
            sum(_positive_counts(counts).values())
            for counts in self.eligibility_polarity_crosstab.values()
        )
        if self.record_count != eligibility_total:
            raise ValueError("eligibility/polarity cross-tab must equal record_count")
        eligible_counts = self.eligibility_polarity_crosstab.get("eligible", {})
        if self.training_eligible_record_count != sum(eligible_counts.values()):
            raise ValueError("eligible cross-tab count mismatch")
        return self


class RepairedCorpusInventoryProfile(StrictMemoryContextModel):
    schema_version: Literal["nslab.repaired_corpus_inventory_profile.v1"] = (
        "nslab.repaired_corpus_inventory_profile.v1"
    )
    manifest_path: str
    manifest_sha256: Sha256
    entry_count: int = Field(ge=0)
    ready_for_import_count: int = Field(ge=0)
    declared_record_count: int = Field(ge=0)
    declared_training_eligible_record_count: int = Field(ge=0)
    ready_declared_record_count: int = Field(ge=0)
    ready_declared_training_eligible_record_count: int = Field(ge=0)
    non_ready_declared_record_count: int = Field(ge=0)
    non_ready_declared_training_eligible_record_count: int = Field(ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    engine_digest_counts: dict[str, int] = Field(default_factory=dict)
    filename_year_counts: dict[str, int] = Field(default_factory=dict)
    source_bytes: NumericDistribution
    repaired_bytes: NumericDistribution
    record_count_coverage_count: int = Field(ge=0)
    ready_record_count_coverage_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_inventory_counts(self) -> Self:
        if self.entry_count != sum(_positive_counts(self.status_counts).values()):
            raise ValueError("status counts must equal inventory entries")
        if self.ready_for_import_count > self.entry_count:
            raise ValueError("ready entries cannot exceed all entries")
        if self.declared_record_count != (
            self.ready_declared_record_count + self.non_ready_declared_record_count
        ):
            raise ValueError("ready/non-ready record totals mismatch")
        if self.declared_training_eligible_record_count != (
            self.ready_declared_training_eligible_record_count
            + self.non_ready_declared_training_eligible_record_count
        ):
            raise ValueError("ready/non-ready eligible totals mismatch")
        if self.ready_record_count_coverage_count > self.record_count_coverage_count:
            raise ValueError("ready coverage cannot exceed all coverage")
        return self


class BrainMemoryPhase0Baseline(StrictMemoryContextModel):
    schema_version: Literal["nslab.brain_memory_phase0_baseline.v1"] = (
        "nslab.brain_memory_phase0_baseline.v1"
    )
    corpus: BrainRecordCorpusProfile
    repaired_inventory: RepairedCorpusInventoryProfile | None = None


def _string_counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _positive_counts(counts: dict[str, int]) -> dict[str, int]:
    if any(value < 0 for value in counts.values()):
        raise ValueError("counts cannot be negative")
    return {key: value for key, value in sorted(counts.items()) if value > 0}
