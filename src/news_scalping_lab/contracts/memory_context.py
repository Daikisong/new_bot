"""Strict contracts for population-aware memory retrieval.

These models define the data boundaries used by the phased memory upgrade.
They intentionally contain references, counts, and hashes rather than the full
research corpus so a daily context remains bounded as the corpus grows.
"""

from __future__ import annotations

import math
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

from news_scalping_lab.utils import as_kst


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
RecordLabelQualityValue = Literal[
    "verified",
    "quarantined",
    "no_tradable_row",
    "missing",
    "ambiguous",
    "conflicting",
    "not_applicable",
]
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
PopulationPurpose = Literal[
    "catalyst_response",
    "candidate_error",
    "newsless",
    "leader_selection",
]
POPULATION_PURPOSE_LANES: dict[str, tuple[str, ...]] = {
    "catalyst_response": (
        "positive_analogs",
        "negative_controls",
        "near_misses",
        "counterexamples",
        "theme_formation_failures",
    ),
    "candidate_error": ("candidate_generation_errors",),
    "newsless": ("newsless_or_unexplained",),
    "leader_selection": ("leader_selection_pairs",),
}
POPULATION_PURPOSE_UNIT_TYPES: dict[str, frozenset[str]] = {
    "catalyst_response": frozenset(
        {
            "event-issuer-day",
            "issuer-day",
            "theme-day",
            "theme-day-ticker-day",
        }
    ),
    "candidate_error": frozenset(
        {"event-issuer-day", "issuer-day", "theme-day-ticker-day"}
    ),
    "newsless": frozenset({"ticker-day"}),
    "leader_selection": frozenset({"theme-day-pair"}),
}
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

    schema_version: Literal["nslab.record_routing_metadata.v2"] = (
        "nslab.record_routing_metadata.v2"
    )
    record_id: str
    record_type: str
    available_from: AwareDatetime
    evidence_polarity: EvidencePolarity
    training_eligible: bool
    label_quality: RecordLabelQualityValue
    routing_disposition: RoutingDisposition
    memory_lanes: list[str] = Field(default_factory=list)
    polarity_classifier_version: str
    threshold_source: str
    threshold_role: Literal["retrieval_calibration_only", "explicit_label"]
    provenance_source_ids: list[str] = Field(default_factory=list)


class NewsRowCoverage(StrictMemoryContextModel):
    row_number: int = Field(ge=1)
    event_id: str
    source_id: str
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
    member_source_ids: list[str] = Field(default_factory=list)
    member_row_numbers: list[Annotated[int, Field(ge=1)]] = Field(
        default_factory=list
    )
    disposition: NewsDisposition
    exact_duplicate_count: int = Field(default=0, ge=0)
    semantic_duplicate_count: int = Field(default=0, ge=0)
    cluster_signature_sha256: Sha256

    @model_validator(mode="after")
    def validate_membership(self) -> Self:
        if not self.member_event_ids or not self.member_source_ids or not self.member_row_numbers:
            raise ValueError("clusters require event, source, and row members")
        if self.representative_event_id not in self.member_event_ids:
            raise ValueError("representative_event_id must be a cluster member")
        if len(self.member_event_ids) != len(self.member_row_numbers) or len(
            self.member_source_ids
        ) != len(self.member_row_numbers):
            raise ValueError("cluster event, source, and row member counts must match")
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
    embedding_provider: str
    embedding_status: str
    embedding_batch_size: int = Field(ge=1)
    similarity_threshold: float = Field(ge=0.0, le=1.0)
    max_semantic_variants: int = Field(ge=1)
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
    accepted_record_hash_manifest: ArtifactReference | None = None
    coverage_complete: bool

    @model_validator(mode="after")
    def validate_record_coverage(self) -> Self:
        if self.accepted_record_count != self.available_record_count + self.future_record_count:
            raise ValueError("accepted records must equal available plus future records")
        if self.available_record_ids.item_count != self.available_record_count:
            raise ValueError("available record artifact count mismatch")
        if self.record_hash_manifest.item_count != self.available_record_count:
            raise ValueError("record hash artifact count mismatch")
        if (
            self.accepted_record_hash_manifest is not None
            and self.accepted_record_hash_manifest.item_count
            != self.accepted_record_count
        ):
            raise ValueError("accepted record hash artifact count mismatch")
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


class MemoryCellMembership(StrictMemoryContextModel):
    """One record's versioned membership in an as-of memory-cell snapshot."""

    schema_version: Literal["nslab.memory_cell_membership.v1"] = (
        "nslab.memory_cell_membership.v1"
    )
    record_id: str
    primary_cell_id: str
    secondary_cell_ids: list[str] = Field(default_factory=list)
    independent_unit_id: str
    membership_score: float = Field(ge=0.0, le=1.0)
    membership_rule: str
    membership_rule_version: str
    available_from: AwareDatetime
    routing_disposition: RoutingDisposition

    @model_validator(mode="after")
    def validate_cell_membership(self) -> Self:
        required = (
            self.record_id,
            self.primary_cell_id,
            self.independent_unit_id,
            self.membership_rule,
            self.membership_rule_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("memory cell membership identifiers must be non-empty")
        if len(set(self.secondary_cell_ids)) != len(self.secondary_cell_ids):
            raise ValueError("secondary cell identifiers must be unique")
        if self.primary_cell_id in self.secondary_cell_ids:
            raise ValueError("primary cell cannot also be a secondary cell")
        if any(not cell_id.strip() for cell_id in self.secondary_cell_ids):
            raise ValueError("secondary cell identifiers must be non-empty")
        return self


class MemoryCellEntry(StrictMemoryContextModel):
    """Compact cell metadata; full member rows live in the membership artifact."""

    schema_version: Literal["nslab.memory_cell_entry.v1"] = (
        "nslab.memory_cell_entry.v1"
    )
    cell_id: str
    signature: str
    primary_member_count: int = Field(ge=1)
    reasoning_member_count: int = Field(ge=0)
    secondary_member_count: int = Field(ge=0)
    independent_unit_count: int = Field(ge=1)
    centroid_sha256: Sha256
    reasoning_centroid_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_cell_entry(self) -> Self:
        if not self.cell_id.strip() or not self.signature.strip():
            raise ValueError("memory cell identifiers must be non-empty")
        if self.independent_unit_count > self.primary_member_count:
            raise ValueError("independent units cannot exceed primary members")
        if self.reasoning_member_count > self.primary_member_count:
            raise ValueError("reasoning members cannot exceed primary members")
        if (self.reasoning_member_count > 0) is not (
            self.reasoning_centroid_sha256 is not None
        ):
            raise ValueError("reasoning centroid presence must match reasoning members")
        return self


class MemoryCellSnapshotManifest(StrictMemoryContextModel):
    """Immutable logical identity for metadata, FTS, HNSW, and cell membership."""

    schema_version: Literal["nslab.memory_cell_snapshot_manifest.v2"] = (
        "nslab.memory_cell_snapshot_manifest.v2"
    )
    snapshot_id: str
    corpus_manifest_sha256: Sha256
    source_generation_sha256: Sha256
    as_of_cutoff: AwareDatetime
    cutoff_identity: str
    max_available_from: AwareDatetime
    embedding_provider: str
    embedding_model: str
    real_embedding: bool
    embedding_dimensions: int = Field(ge=1)
    clustering_version: str
    normalizer_version: str
    cell_schema_version: str
    polarity_classifier_version: str
    population_projection_version: str
    routing_metadata_sha256: Sha256
    record_hash_kind: Literal["canonical_full_envelope_sha256"] = (
        "canonical_full_envelope_sha256"
    )
    record_count: int = Field(ge=0)
    excluded_future_record_count: int = Field(ge=0)
    next_available_from: AwareDatetime | None = None
    reasoning_record_count: int = Field(ge=0)
    context_record_count: int = Field(ge=0)
    audit_record_count: int = Field(ge=0)
    quarantined_record_count: int = Field(ge=0)
    cell_count: int = Field(ge=0)
    primary_membership_count: int = Field(ge=0)
    secondary_membership_count: int = Field(ge=0)
    independent_unit_count: int = Field(ge=0)
    unsupported_reasoning_record_count: int = Field(ge=0)
    unsupported_reasoning_record_ids_sha256: Sha256
    parent_snapshot_id: str | None = None
    retained_record_count: int = Field(ge=0)
    added_record_count: int = Field(ge=0)
    source_record_hashes: ArtifactReference
    excluded_future_record_hashes: ArtifactReference
    routing_metadata: ArtifactReference
    embedding_hashes: ArtifactReference
    cell_entries: ArtifactReference
    memberships: ArtifactReference
    database: ArtifactReference
    metadata_index_ready: bool
    fts_index_ready: bool
    hnsw_index_ready: bool
    provenance_graph_ready: bool
    production_ready: bool

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        required = (
            self.snapshot_id,
            self.cutoff_identity,
            self.embedding_provider,
            self.embedding_model,
            self.clustering_version,
            self.normalizer_version,
            self.cell_schema_version,
            self.polarity_classifier_version,
            self.population_projection_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("memory cell snapshot identifiers must be non-empty")
        explicit_cutoff_identity = f"explicit:{self.as_of_cutoff.isoformat()}"
        if self.cutoff_identity not in {"live_partition", explicit_cutoff_identity}:
            raise ValueError("memory snapshot cutoff identity is invalid")
        disposition_total = (
            self.reasoning_record_count
            + self.context_record_count
            + self.audit_record_count
            + self.quarantined_record_count
        )
        if disposition_total != self.record_count:
            raise ValueError("routing disposition counts must equal record_count")
        if self.primary_membership_count != self.record_count:
            raise ValueError("every indexed record requires exactly one primary membership")
        if self.source_record_hashes.item_count != self.record_count:
            raise ValueError("source record hash artifact count mismatch")
        if (
            self.excluded_future_record_hashes.item_count
            != self.excluded_future_record_count
        ):
            raise ValueError("future record hash artifact count mismatch")
        if (self.excluded_future_record_count > 0) is not (
            self.next_available_from is not None
        ):
            raise ValueError("next_available_from must match future record presence")
        if (
            self.next_available_from is not None
            and as_kst(self.next_available_from) <= as_kst(self.as_of_cutoff)
        ):
            raise ValueError("next_available_from must be after as_of_cutoff")
        if self.routing_metadata.item_count != self.record_count:
            raise ValueError("routing metadata artifact count mismatch")
        if self.embedding_hashes.item_count != self.record_count:
            raise ValueError("embedding hash artifact count mismatch")
        if self.memberships.item_count != self.record_count:
            raise ValueError("membership artifact count mismatch")
        if self.cell_entries.item_count != self.cell_count:
            raise ValueError("cell entry artifact count mismatch")
        if self.database.item_count != self.record_count:
            raise ValueError("database artifact count mismatch")
        if self.retained_record_count + self.added_record_count != self.record_count:
            raise ValueError("retained plus added records must equal record_count")
        readiness = (
            self.real_embedding
            and self.record_count > 0
            and self.unsupported_reasoning_record_count == 0
            and self.metadata_index_ready
            and self.fts_index_ready
            and self.hnsw_index_ready
            and self.provenance_graph_ready
        )
        if self.production_ready is not readiness:
            raise ValueError("production_ready conflicts with index readiness")
        return self


class PopulationOutcomeSummary(StrictMemoryContextModel):
    distribution_weighting: Literal["independent_unit_sample_weight.v1"] = (
        "independent_unit_sample_weight.v1"
    )
    observed_unit_count: int = Field(ge=0)
    missing_outcome_unit_count: int = Field(ge=0)
    upper_limit_touched_count: int = Field(ge=0)
    high_return_5_count: int = Field(ge=0)
    high_return_10_count: int = Field(ge=0)
    high_return_20_count: int = Field(ge=0)
    mean_high_return_pct: float | None = None
    median_high_return_pct: float | None = None
    p10_high_return_pct: float | None = None
    p25_high_return_pct: float | None = None
    p75_high_return_pct: float | None = None
    p90_high_return_pct: float | None = None
    mean_close_return_pct: float | None = None
    median_close_return_pct: float | None = None
    p10_close_return_pct: float | None = None
    p25_close_return_pct: float | None = None
    p75_close_return_pct: float | None = None
    p90_close_return_pct: float | None = None

    @model_validator(mode="after")
    def validate_outcome_counts(self) -> Self:
        if any(
            value is not None and not math.isfinite(value)
            for value in (
                self.mean_high_return_pct,
                self.median_high_return_pct,
                self.p10_high_return_pct,
                self.p25_high_return_pct,
                self.p75_high_return_pct,
                self.p90_high_return_pct,
                self.mean_close_return_pct,
                self.median_close_return_pct,
                self.p10_close_return_pct,
                self.p25_close_return_pct,
                self.p75_close_return_pct,
                self.p90_close_return_pct,
            )
        ):
            raise ValueError("population outcome means and medians must be finite")
        for values in (
            (
                self.p10_high_return_pct,
                self.p25_high_return_pct,
                self.p75_high_return_pct,
                self.p90_high_return_pct,
            ),
            (
                self.p10_close_return_pct,
                self.p25_close_return_pct,
                self.p75_close_return_pct,
                self.p90_close_return_pct,
            ),
        ):
            observed = [value for value in values if value is not None]
            if observed and (len(observed) != 4 or observed != sorted(observed)):
                raise ValueError("population outcome percentiles must be complete and ordered")
        if not (
            self.high_return_20_count
            <= self.high_return_10_count
            <= self.high_return_5_count
            <= self.observed_unit_count
            and self.upper_limit_touched_count <= self.observed_unit_count
        ):
            raise ValueError("outcome threshold counts must be nested")
        return self


class PopulationObservedRate(StrictMemoryContextModel):
    metric: Literal[
        "upper_limit_touched",
        "high_return_5",
        "high_return_10",
        "high_return_20",
    ]
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    weighted_numerator: float = Field(ge=0.0)
    weighted_denominator: float = Field(ge=0.0)
    observed_population_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    lower_bound: float | None = Field(default=None, ge=0.0, le=1.0)
    upper_bound: float | None = Field(default=None, ge=0.0, le=1.0)
    interval_method: Literal["trade_date_block_bootstrap.v1"] = (
        "trade_date_block_bootstrap.v1"
    )
    bootstrap_iterations: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_rate(self) -> Self:
        if any(
            not math.isfinite(value)
            for value in (self.weighted_numerator, self.weighted_denominator)
        ):
            raise ValueError("weighted observed population counts must be finite")
        if self.numerator > self.denominator:
            raise ValueError("rate numerator cannot exceed denominator")
        if self.weighted_numerator > self.weighted_denominator:
            raise ValueError("weighted rate numerator cannot exceed denominator")
        if (self.denominator == 0) is not (self.weighted_denominator == 0.0):
            raise ValueError("count and weighted denominators must both be empty or non-empty")
        if self.denominator == 0:
            if any(
                value is not None
                for value in (
                    self.observed_population_rate,
                    self.lower_bound,
                    self.upper_bound,
                )
            ):
                raise ValueError("empty observed population cannot have a rate")
        else:
            if self.observed_population_rate is None:
                raise ValueError("non-empty observed population requires a rate")
            expected = self.weighted_numerator / self.weighted_denominator
            if abs(self.observed_population_rate - expected) > 1e-12:
                raise ValueError("observed rate must equal the weighted observed rate")
            if not (
                self.lower_bound is not None
                and self.upper_bound is not None
                and self.lower_bound <= self.upper_bound
            ):
                raise ValueError("observed rate interval bounds are invalid")
        return self


class PopulationCubeRow(StrictMemoryContextModel):
    cell_id: str
    memory_lane: str
    time_slice: str
    regime_cluster: str
    record_type: str
    path_type: str
    label_quality: str
    raw_record_count: int = Field(ge=0)
    independent_unit_count: int = Field(ge=0)
    effective_sample_size: float = Field(ge=0.0)
    polarity_counts: dict[str, int] = Field(default_factory=dict)
    outcome_summary: PopulationOutcomeSummary
    member_record_ids_sha256: Sha256
    independent_unit_ids_sha256: Sha256

    @model_validator(mode="after")
    def validate_cube_row(self) -> Self:
        if not math.isfinite(self.effective_sample_size):
            raise ValueError("cube effective sample size must be finite")
        if self.independent_unit_count > self.raw_record_count:
            raise ValueError("cube independent units cannot exceed raw records")
        if self.effective_sample_size > self.independent_unit_count:
            raise ValueError("cube effective sample size cannot exceed units")
        if sum(_positive_counts(self.polarity_counts).values()) != (
            self.independent_unit_count
        ):
            raise ValueError("cube polarity counts must equal units")
        if self.outcome_summary.observed_unit_count + (
            self.outcome_summary.missing_outcome_unit_count
        ) != self.independent_unit_count:
            raise ValueError("cube outcome counts must equal units")
        return self


class PopulationManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.population_manifest.v2"] = (
        "nslab.population_manifest.v2"
    )
    population_id: str
    run_id: str
    cluster_id: str
    cutoff_at: AwareDatetime
    memory_snapshot_id: str
    source_generation_sha256: Sha256
    corpus_manifest_sha256: Sha256
    statistics_version: str
    cube_version: str
    selection_budget_version: str
    purpose_classifier_version: str
    purpose_record_types_sha256: Sha256
    max_selected_record_count: int = Field(ge=1)
    max_cube_row_count: int = Field(ge=1)
    bootstrap_version: Literal["trade_date_block_bootstrap.v1"] = (
        "trade_date_block_bootstrap.v1"
    )
    selected_cell_ids: list[str] = Field(default_factory=list)
    routing_dispositions: list[RoutingDisposition] = Field(default_factory=list)
    membership_manifest_sha256: Sha256
    independent_unit_type: IndependentUnitType
    population_purpose: PopulationPurpose
    included_memory_lanes: list[str] = Field(default_factory=list)
    query_regime_cluster: str | None = None
    raw_record_count: int = Field(ge=0)
    independent_unit_count: int = Field(ge=0)
    effective_sample_size: float = Field(ge=0.0)
    polarity_counts: dict[str, int] = Field(default_factory=dict)
    eligibility_counts: dict[str, int] = Field(default_factory=dict)
    label_quality_counts: dict[str, int] = Field(default_factory=dict)
    time_slice_counts: dict[str, int] = Field(default_factory=dict)
    regime_counts: dict[str, int] = Field(default_factory=dict)
    outcome_summary: PopulationOutcomeSummary
    observed_rates: list[PopulationObservedRate] = Field(default_factory=list)
    member_records: ArtifactReference
    independent_units: ArtifactReference
    cube_rows: ArtifactReference
    observed_population_rate_label: Literal["observed_population_rate"] = (
        "observed_population_rate"
    )

    @model_validator(mode="after")
    def validate_population_counts(self) -> Self:
        if not math.isfinite(self.effective_sample_size):
            raise ValueError("population effective sample size must be finite")
        if self.independent_unit_count > self.raw_record_count:
            raise ValueError("independent units cannot exceed raw records")
        if self.effective_sample_size > self.independent_unit_count:
            raise ValueError("effective sample size cannot exceed independent units")
        if not self.selected_cell_ids or len(self.selected_cell_ids) != len(
            set(self.selected_cell_ids)
        ):
            raise ValueError("selected cells must be non-empty and unique")
        if not self.routing_dispositions or len(self.routing_dispositions) != len(
            set(self.routing_dispositions)
        ):
            raise ValueError("routing dispositions must be non-empty and unique")
        if self.routing_dispositions != ["REASONING"]:
            raise ValueError("observed populations require REASONING disposition only")
        if not self.included_memory_lanes or len(self.included_memory_lanes) != len(
            set(self.included_memory_lanes)
        ):
            raise ValueError("included memory lanes must be non-empty and unique")
        if self.query_regime_cluster is not None and (
            not self.query_regime_cluster
            or self.query_regime_cluster != self.query_regime_cluster.strip().upper()
        ):
            raise ValueError("query regime cluster must be canonical uppercase text")
        expected_lanes = POPULATION_PURPOSE_LANES[self.population_purpose]
        if tuple(sorted(self.included_memory_lanes)) != tuple(sorted(expected_lanes)):
            raise ValueError("included memory lanes conflict with population purpose")
        if self.independent_unit_type not in POPULATION_PURPOSE_UNIT_TYPES[
            self.population_purpose
        ]:
            raise ValueError("independent unit type conflicts with population purpose")
        if self.member_records.item_count != self.raw_record_count:
            raise ValueError("member record artifact count mismatch")
        if self.raw_record_count > self.max_selected_record_count:
            raise ValueError("population exceeds selected record budget")
        if self.cube_rows.item_count > self.max_cube_row_count:
            raise ValueError("population exceeds cube row budget")
        if self.independent_units.item_count != self.independent_unit_count:
            raise ValueError("independent unit artifact count mismatch")
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
        if len(self.observed_rates) != 4 or {
            item.metric for item in self.observed_rates
        } != {
            "upper_limit_touched",
            "high_return_5",
            "high_return_10",
            "high_return_20",
        }:
            raise ValueError("all observed population rate metrics are required")
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
    record_counts_by_label_quality: dict[str, int] = Field(default_factory=dict)
    record_counts_by_routing_disposition: dict[str, int] = Field(default_factory=dict)
    record_counts_by_lane: dict[str, int] = Field(default_factory=dict)
    eligibility_polarity_crosstab: dict[str, dict[str, int]] = Field(
        default_factory=dict
    )
    routing_four_axis_crosstab: dict[str, int] = Field(default_factory=dict)
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
        if self.record_count != sum(
            _positive_counts(self.record_counts_by_label_quality).values()
        ):
            raise ValueError("label quality counts must equal record_count")
        if self.record_count != sum(
            _positive_counts(self.record_counts_by_routing_disposition).values()
        ):
            raise ValueError("routing disposition counts must equal record_count")
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
        if self.record_count != sum(
            _positive_counts(self.routing_four_axis_crosstab).values()
        ):
            raise ValueError("four-axis routing cross-tab must equal record_count")
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
