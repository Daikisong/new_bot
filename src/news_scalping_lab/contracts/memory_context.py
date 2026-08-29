"""Strict contracts for population-aware memory retrieval.

These models define the data boundaries used by the phased memory upgrade.
They intentionally contain references, counts, and hashes rather than the full
research corpus so a daily context remains bounded as the corpus grows.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from news_scalping_lab.utils import as_kst, sha256_text


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
    embedding_model: str | None = None
    embedding_revision: str | None = None
    embedding_artifact_sha256: Sha256 | None = None
    embedding_dimensions: int = Field(default=0, ge=0)
    embedding_fallback_policy: str = "allow-deterministic-fallback"
    deterministic_fallback_used: bool = False
    embedding_retry_count: int = Field(default=0, ge=0)
    embedding_failure_type: str | None = None
    production_runtime_identity: str = "local-or-test"
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
        if self.embedding_fallback_policy == "fail-closed":
            if self.embedding_status != "PROVIDER":
                raise ValueError("fail-closed clustering requires provider embeddings")
            if self.deterministic_fallback_used:
                raise ValueError("fail-closed clustering forbids deterministic fallback")
            if self.embedding_dimensions < 1:
                raise ValueError("fail-closed clustering requires embedding dimensions")
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

    schema_version: Literal["nslab.memory_cell_snapshot_manifest.v3"] = (
        "nslab.memory_cell_snapshot_manifest.v3"
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
    availability_mode: Literal[
        "source_available_from",
        "replay_available_from",
    ] = "source_available_from"
    availability_projection_version: str | None = None
    availability_projection: ArtifactReference | None = None
    evaluation_only: bool = False
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
        if self.availability_mode == "replay_available_from":
            if (
                not self.evaluation_only
                or not self.availability_projection_version
                or self.availability_projection is None
                or self.availability_projection.item_count < 1
                or self.cutoff_identity != explicit_cutoff_identity
            ):
                raise ValueError(
                    "replay availability requires an explicit evaluation projection"
                )
        elif (
            self.evaluation_only
            or self.availability_projection_version is not None
            or self.availability_projection is not None
        ):
            raise ValueError(
                "source availability snapshots cannot carry a replay projection"
            )
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
    population_unit_count: int = Field(ge=0)
    selected_unit_count: int = Field(ge=0)
    record_ids: list[str] = Field(default_factory=list)
    independent_unit_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stratum(self) -> Self:
        if not self.stratum.strip():
            raise ValueError("representative stratum must be non-empty")
        if self.selected_unit_count != len(set(self.independent_unit_ids)):
            raise ValueError("selected stratum units must match independent unit IDs")
        if self.selected_unit_count > self.population_unit_count:
            raise ValueError("selected stratum units cannot exceed population units")
        if len(self.record_ids) != len(set(self.record_ids)):
            raise ValueError("representative stratum record IDs must be unique")
        return self


class RepresentativeRecord(StrictMemoryContextModel):
    schema_version: Literal["nslab.representative_record.v1"] = (
        "nslab.representative_record.v1"
    )
    rank: int = Field(ge=1)
    record_id: str
    independent_unit_id: str
    trade_date: date
    source_sha256: Sha256
    provenance_source_ids: list[str] = Field(default_factory=list)
    record_label_quality: str
    strata: list[str] = Field(default_factory=list)
    context_excerpt: str
    relevance_score: float
    diversity_score: float
    facility_score: float
    distribution_score: float
    selection_score: float
    estimated_token_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_representative_record(self) -> Self:
        required = (
            self.record_id,
            self.independent_unit_id,
            self.record_label_quality,
            self.context_excerpt,
        )
        if any(not value.strip() for value in required):
            raise ValueError("representative record identifiers and excerpt are required")
        if not self.provenance_source_ids or any(
            not value.strip() for value in self.provenance_source_ids
        ):
            raise ValueError("representative record provenance must be non-empty")
        if not self.strata or len(self.strata) != len(set(self.strata)):
            raise ValueError("representative record strata must be non-empty and unique")
        for score in (
            self.relevance_score,
            self.diversity_score,
            self.facility_score,
            self.distribution_score,
            self.selection_score,
        ):
            if not math.isfinite(score):
                raise ValueError("representative scores must be finite")
        return self


class RepresentativeSetManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.representative_set_manifest.v3"] = (
        "nslab.representative_set_manifest.v3"
    )
    representative_set_id: str
    run_id: str
    cluster_id: str
    cutoff_at: AwareDatetime
    query_text: str
    query_sha256: Sha256
    query_embedding_sha256: Sha256
    population_id: str
    population_manifest_sha256: Sha256
    memory_snapshot_id: str
    source_generation_sha256: Sha256
    corpus_manifest_sha256: Sha256
    selection_version: str
    embedding_model: str
    candidate_pool_count: int = Field(ge=0)
    target_selected_record_count: int = Field(ge=1)
    population_record_count: int = Field(ge=0)
    population_unit_count: int = Field(ge=0)
    selected_record_count: int = Field(ge=0)
    selected_unit_count: int = Field(ge=0)
    omitted_population_record_count: int = Field(ge=0)
    omitted_population_unit_count: int = Field(ge=0)
    max_selected_record_count: int = Field(ge=1)
    max_candidate_pool_count: int = Field(ge=1)
    max_token_count: int = Field(ge=1)
    max_trade_date_concentration: int = Field(ge=1)
    max_unit_key_concentration: int = Field(ge=1)
    estimated_token_count: int = Field(ge=0, le=48_000)
    diversity_coverage_ratio: float = Field(ge=0.0, le=1.0)
    max_distribution_share_error: float = Field(ge=0.0, le=1.0)
    distribution_share_error_tolerance: float = Field(ge=0.0, le=1.0)
    strata: list[RepresentativeStratum] = Field(default_factory=list)
    selected_record_ids: list[str] = Field(default_factory=list)
    selected_independent_unit_ids: list[str] = Field(default_factory=list)
    representative_records: ArtifactReference

    @model_validator(mode="after")
    def validate_selection_counts(self) -> Self:
        if self.selected_record_count != len(self.selected_record_ids):
            raise ValueError("selected_record_count must equal selected IDs")
        if not self.query_text.strip() or self.query_sha256 != sha256_text(
            self.query_text
        ):
            raise ValueError("representative query text and hash conflict")
        if self.selected_unit_count != len(self.selected_independent_unit_ids):
            raise ValueError("selected_unit_count must equal selected unit IDs")
        if len(set(self.selected_record_ids)) != len(self.selected_record_ids):
            raise ValueError("selected record IDs must be unique")
        if len(set(self.selected_independent_unit_ids)) != len(
            self.selected_independent_unit_ids
        ):
            raise ValueError("selected independent unit IDs must be unique")
        if self.selected_record_count != self.selected_unit_count:
            raise ValueError("representative selection requires one record per unit")
        if self.population_record_count != (
            self.selected_record_count + self.omitted_population_record_count
        ):
            raise ValueError("representative population record counts conflict")
        if self.population_unit_count != (
            self.selected_unit_count + self.omitted_population_unit_count
        ):
            raise ValueError("representative population unit counts conflict")
        if self.candidate_pool_count > self.population_record_count:
            raise ValueError("candidate pool cannot exceed population records")
        if self.selected_record_count > self.max_selected_record_count:
            raise ValueError("representative selection exceeds record budget")
        if self.target_selected_record_count > self.max_selected_record_count:
            raise ValueError("representative target exceeds record budget")
        if self.selected_record_count < self.target_selected_record_count:
            raise ValueError("representative selection did not reach its target")
        if self.candidate_pool_count > self.max_candidate_pool_count:
            raise ValueError("representative candidate pool exceeds budget")
        if self.estimated_token_count > self.max_token_count:
            raise ValueError("representative selection exceeds token budget")
        if self.max_distribution_share_error > self.distribution_share_error_tolerance:
            raise ValueError("representative distribution error exceeds tolerance")
        if self.representative_records.item_count != self.selected_record_count:
            raise ValueError("representative artifact count mismatch")
        stratum_ids = {
            record_id for stratum in self.strata for record_id in stratum.record_ids
        }
        if not stratum_ids.issubset(set(self.selected_record_ids)):
            raise ValueError("stratum records must belong to selected records")
        return self


class AdaptiveTriggerEvidence(StrictMemoryContextModel):
    schema_version: Literal["nslab.adaptive_trigger_evidence.v1"] = (
        "nslab.adaptive_trigger_evidence.v1"
    )
    kind: Literal["MULTI_HOP_BENEFICIARY"]
    source_artifact: ArtifactReference
    cutoff_at: AwareDatetime
    event_cluster_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)
    derivation_version: Literal["beneficiary_graph_trigger.v1"] = (
        "beneficiary_graph_trigger.v1"
    )

    @model_validator(mode="after")
    def validate_trigger_evidence(self) -> Self:
        if self.source_artifact.item_count != 1:
            raise ValueError("adaptive trigger evidence requires one source artifact")
        for values in (
            self.event_cluster_ids,
            self.source_ids,
            self.query_terms,
        ):
            if not values or len(values) != len(set(values)):
                raise ValueError(
                    "adaptive trigger evidence lists must be non-empty and unique"
                )
            if any(not value.strip() for value in values):
                raise ValueError("adaptive trigger evidence values must be non-empty")
        return self


class AdaptiveRetrievalIteration(StrictMemoryContextModel):
    iteration: int = Field(ge=1)
    trigger_reasons: list[str] = Field(default_factory=list)
    expansion_query_sha256: Sha256
    expansion_embedding_sha256: Sha256
    expansion_memory_lanes: list[str] = Field(default_factory=list)
    expansion_regime_clusters: list[str] = Field(default_factory=list)
    added_cell_ids: list[str] = Field(default_factory=list)
    added_record_ids: list[str] = Field(default_factory=list)
    total_cell_count: int = Field(ge=0)
    total_record_count: int = Field(ge=0)
    population_manifest: ArtifactReference
    representative_set_manifest: ArtifactReference
    information_gain: float = Field(ge=0.0, le=1.0)
    cumulative_token_count: int = Field(ge=0)


class AdaptiveRetrievalTrace(StrictMemoryContextModel):
    schema_version: Literal["nslab.adaptive_retrieval_trace.v4"] = (
        "nslab.adaptive_retrieval_trace.v4"
    )
    trace_id: str
    run_id: str
    cluster_id: str
    cutoff_at: AwareDatetime
    query_text: str
    query_sha256: Sha256
    query_embedding_sha256: Sha256
    policy_version: str
    trigger_evidence: list[AdaptiveTriggerEvidence] = Field(default_factory=list)
    initial_population_manifest: ArtifactReference
    initial_representative_set_manifest: ArtifactReference
    initial_cell_ids: list[str] = Field(default_factory=list)
    iterations: list[AdaptiveRetrievalIteration] = Field(default_factory=list)
    max_depth: int = Field(ge=1)
    max_cell_count: int = Field(ge=1)
    max_record_count: int = Field(ge=1)
    max_token_count: int = Field(ge=1)
    min_information_gain: float = Field(ge=0.0, le=1.0)
    final_cell_ids: list[str] = Field(default_factory=list)
    final_population_manifest: ArtifactReference
    final_representative_set_manifest: ArtifactReference
    stopped_reason: str

    @model_validator(mode="after")
    def validate_adaptive_trace(self) -> Self:
        if not self.query_text.strip() or self.query_sha256 != sha256_text(
            self.query_text
        ):
            raise ValueError("adaptive query text and hash conflict")
        if [item.iteration for item in self.iterations] != list(
            range(1, len(self.iterations) + 1)
        ):
            raise ValueError("adaptive iterations must be contiguous")
        if len(self.iterations) > self.max_depth:
            raise ValueError("adaptive trace exceeds depth budget")
        for artifact in (
            self.initial_population_manifest,
            self.initial_representative_set_manifest,
            self.final_population_manifest,
            self.final_representative_set_manifest,
        ):
            if artifact.item_count != 1:
                raise ValueError("adaptive manifest references must contain one item")
        if len(set(self.initial_cell_ids)) != len(self.initial_cell_ids) or len(
            set(self.final_cell_ids)
        ) != len(self.final_cell_ids):
            raise ValueError("adaptive cell identifiers must be unique")
        if not set(self.initial_cell_ids).issubset(set(self.final_cell_ids)):
            raise ValueError("adaptive final cells must retain initial cells")
        if len(self.final_cell_ids) > self.max_cell_count:
            raise ValueError("adaptive trace exceeds cell budget")
        if self.iterations:
            cumulative_tokens = [
                item.cumulative_token_count for item in self.iterations
            ]
            if cumulative_tokens != sorted(cumulative_tokens):
                raise ValueError("adaptive token counts must be monotonic")
            if any(
                item.population_manifest.item_count != 1
                or item.representative_set_manifest.item_count != 1
                for item in self.iterations
            ):
                raise ValueError("adaptive iteration references must contain one item")
            final = self.iterations[-1]
            if final.total_record_count > self.max_record_count:
                raise ValueError("adaptive trace exceeds record budget")
            if final.cumulative_token_count > self.max_token_count:
                raise ValueError("adaptive trace exceeds token budget")
        if not self.stopped_reason.strip():
            raise ValueError("adaptive trace requires a stopped reason")
        evidence_keys = [
            (item.kind, item.source_artifact.sha256)
            for item in self.trigger_evidence
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("adaptive trigger evidence must be unique")
        if any(
            as_kst(item.cutoff_at) != as_kst(self.cutoff_at)
            or self.cluster_id not in item.event_cluster_ids
            for item in self.trigger_evidence
        ):
            raise ValueError("adaptive trigger evidence identity mismatch")
        return self


class CategoryBrainGuidance(StrictMemoryContextModel):
    claim_id: str
    category: str
    statement: str
    mechanism: str
    status: str
    confidence_label: str
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicting_record_ids: list[str] = Field(default_factory=list)
    source_artifact_path: str
    source_artifact_sha256: Sha256
    usage: Literal["QUERY_GUIDANCE_NOT_EVIDENCE"] = "QUERY_GUIDANCE_NOT_EVIDENCE"

    @model_validator(mode="after")
    def validate_guidance(self) -> Self:
        required = (
            self.claim_id,
            self.category,
            self.statement,
            self.mechanism,
            self.status,
            self.confidence_label,
            self.source_artifact_path,
        )
        if any(not value.strip() for value in required):
            raise ValueError("category brain guidance fields must be non-empty")
        if not self.supporting_record_ids and not self.contradicting_record_ids:
            raise ValueError("category guidance requires record provenance")
        if (
            len(self.supporting_record_ids) != len(set(self.supporting_record_ids))
            or len(self.contradicting_record_ids)
            != len(set(self.contradicting_record_ids))
            or set(self.supporting_record_ids).intersection(
                self.contradicting_record_ids
            )
        ):
            raise ValueError("category guidance record provenance must be disjoint")
        return self


class CategoryBrainQueryPlan(StrictMemoryContextModel):
    schema_version: Literal["nslab.category_brain_query_plan.v1"] = (
        "nslab.category_brain_query_plan.v1"
    )
    cluster_id: str
    original_query: str
    original_query_sha256: Sha256
    query_embedding_sha256: Sha256
    embedding_model: str
    selected_claim_ids: list[str] = Field(default_factory=list, max_length=3)
    claim_embedding_sha256s: dict[str, Sha256] = Field(default_factory=dict)
    selection_scores: dict[str, float] = Field(default_factory=dict)
    expanded_query: str
    expanded_query_sha256: Sha256
    source_artifact_path: str
    source_artifact_sha256: Sha256
    usage: Literal["QUERY_PLANNER_NOT_EVIDENCE"] = "QUERY_PLANNER_NOT_EVIDENCE"

    @model_validator(mode="after")
    def validate_query_plan(self) -> Self:
        required = (
            self.cluster_id,
            self.original_query,
            self.embedding_model,
            self.expanded_query,
            self.source_artifact_path,
        )
        if any(not value.strip() for value in required):
            raise ValueError("category brain query plan fields must be non-empty")
        if self.original_query_sha256 != sha256_text(self.original_query):
            raise ValueError("category brain original query hash mismatch")
        if self.expanded_query_sha256 != sha256_text(self.expanded_query):
            raise ValueError("category brain expanded query hash mismatch")
        if len(self.selected_claim_ids) != len(set(self.selected_claim_ids)):
            raise ValueError("category brain selected claims must be unique")
        expected = set(self.selected_claim_ids)
        if (
            set(self.claim_embedding_sha256s) != expected
            or set(self.selection_scores) != expected
            or any(not math.isfinite(value) for value in self.selection_scores.values())
        ):
            raise ValueError("category brain query plan claim metadata mismatch")
        return self


class CategoryClaimMerkleStep(StrictMemoryContextModel):
    position: Literal["LEFT", "RIGHT"]
    sha256: Sha256


class CategoryClaimInclusionProof(StrictMemoryContextModel):
    schema_version: Literal["nslab.category_claim_inclusion_proof.v1"] = (
        "nslab.category_claim_inclusion_proof.v1"
    )
    claim_id: str
    claim_payload_sha256: Sha256
    leaf_index: int = Field(ge=0)
    leaf_count: int = Field(gt=0)
    siblings: list[CategoryClaimMerkleStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_inclusion_proof(self) -> Self:
        if not self.claim_id.strip() or self.leaf_index >= self.leaf_count:
            raise ValueError("category claim inclusion proof identity is invalid")
        return self


class CategoryBrainIndexManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.category_brain_index_manifest.v1"] = (
        "nslab.category_brain_index_manifest.v1"
    )
    brain_version: str
    brain_record_cutoff_at: AwareDatetime
    index_version: str
    embedding_model: str
    embedding_dimensions: int = Field(gt=0)
    claim_count: int = Field(ge=0)
    claim_payload_merkle_root_sha256: Sha256
    claims_artifact: ArtifactReference
    vector_ledger: ArtifactReference
    database_artifact_path: str
    database_sha256: Sha256
    hnsw_index_ready: bool

    @model_validator(mode="after")
    def validate_category_index(self) -> Self:
        if any(
            not value.strip()
            for value in (
                self.brain_version,
                self.index_version,
                self.embedding_model,
                self.database_artifact_path,
            )
        ):
            raise ValueError("category brain index identity fields must be non-empty")
        if (
            self.claims_artifact.item_count != self.claim_count
            or self.vector_ledger.item_count != self.claim_count
            or not self.hnsw_index_ready
        ):
            raise ValueError("category brain index readiness is incomplete")
        return self


class BeneficiaryGraphPath(StrictMemoryContextModel):
    path_id: str
    event_cluster_ids: list[str] = Field(default_factory=list)
    mechanism_steps: list[str] = Field(default_factory=list)
    narrative_context: list[str] = Field(default_factory=list)
    business_roles: list[str] = Field(default_factory=list)
    company_memory_artifact_paths: list[str] = Field(default_factory=list)
    ticker: str
    company_name: str
    source_ids: list[str] = Field(default_factory=list)
    candidate_rank: int = Field(ge=1)
    candidate_path_type: str
    status: Literal["OPEN_WORLD_CANDIDATE_REQUIRES_VERIFICATION"] = (
        "OPEN_WORLD_CANDIDATE_REQUIRES_VERIFICATION"
    )

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        required = (
            self.path_id,
            self.ticker,
            self.company_name,
            self.candidate_path_type,
        )
        if any(not value.strip() for value in required):
            raise ValueError("beneficiary graph path identifiers must be non-empty")
        if not self.event_cluster_ids or not self.mechanism_steps:
            raise ValueError("beneficiary graph paths require event and mechanism steps")
        if not self.source_ids or any(not value.strip() for value in self.source_ids):
            raise ValueError("beneficiary graph paths require non-empty provenance")
        for values in (
            self.event_cluster_ids,
            self.mechanism_steps,
            self.narrative_context,
            self.business_roles,
            self.company_memory_artifact_paths,
            self.source_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("beneficiary graph path lists must be unique")
        return self


class BeneficiaryGraphArtifact(StrictMemoryContextModel):
    schema_version: Literal["nslab.beneficiary_graph.v2"] = (
        "nslab.beneficiary_graph.v2"
    )
    run_id: str
    cutoff_at: AwareDatetime
    event_cluster_manifest: ArtifactReference
    company_memory_artifact_sha256s: dict[str, Sha256] = Field(default_factory=dict)
    excluded_company_memory_artifact_paths: list[str] = Field(default_factory=list)
    reviewed_company_memory_count: int = Field(ge=0)
    reviewed_company_memory_root_sha256: Sha256
    unmatched_company_memory_count: int = Field(ge=0)
    candidate_input_artifact: ArtifactReference
    candidate_input_sha256: Sha256
    candidate_count: int = Field(ge=0)
    path_count: int = Field(ge=0)
    paths: list[BeneficiaryGraphPath] = Field(default_factory=list)
    unresolved_candidate_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        if (
            not self.run_id.strip()
            or self.event_cluster_manifest.item_count != 1
            or self.candidate_input_artifact.item_count != self.candidate_count
        ):
            raise ValueError("beneficiary graph identity is invalid")
        if self.path_count != len(self.paths):
            raise ValueError("beneficiary graph path count mismatch")
        path_ids = [item.path_id for item in self.paths]
        if len(path_ids) != len(set(path_ids)):
            raise ValueError("beneficiary graph path identifiers must be unique")
        if len(self.unresolved_candidate_ids) != len(
            set(self.unresolved_candidate_ids)
        ):
            raise ValueError("beneficiary graph unresolved candidates must be unique")
        if any(
            not path.strip()
            for path in self.company_memory_artifact_sha256s
        ):
            raise ValueError("beneficiary graph company memory paths must be non-empty")
        if self.unmatched_company_memory_count + len(
            self.company_memory_artifact_sha256s
        ) > self.reviewed_company_memory_count:
            raise ValueError("beneficiary graph company memory review counts are invalid")
        if (
            len(self.excluded_company_memory_artifact_paths)
            != len(set(self.excluded_company_memory_artifact_paths))
            or set(self.excluded_company_memory_artifact_paths).intersection(
                self.company_memory_artifact_sha256s
            )
        ):
            raise ValueError("beneficiary graph excluded company memory paths are invalid")
        return self


class DailyMemoryContext(StrictMemoryContextModel):
    schema_version: Literal["nslab.daily_memory_context.v2"] = (
        "nslab.daily_memory_context.v2"
    )
    run_id: str
    trade_date: date
    cutoff_at: AwareDatetime
    corpus_manifest_sha256: Sha256
    news_coverage_manifest: ArtifactReference
    event_cluster_manifest: ArtifactReference
    event_clusters: ArtifactReference
    memory_coverage_manifest: ArtifactReference
    memory_snapshot_id: str
    source_generation_sha256: Sha256
    material_event_cluster_ids: list[str] = Field(default_factory=list)
    runtime_retrieval_cluster_ids: list[str] = Field(default_factory=list)
    uncovered_material_event_cluster_ids: list[str] = Field(default_factory=list)
    built_population_keys: list[str] = Field(default_factory=list)
    uncovered_population_purposes: dict[str, list[PopulationPurpose]] = Field(
        default_factory=dict
    )
    deferred_population_purposes: list[Literal["leader_selection"]] = Field(
        default_factory=lambda: cast(
            list[Literal["leader_selection"]], ["leader_selection"]
        )
    )
    population_manifests: list[ArtifactReference] = Field(default_factory=list)
    representative_set_manifests: list[ArtifactReference] = Field(default_factory=list)
    adaptive_retrieval_traces: list[ArtifactReference] = Field(default_factory=list)
    runtime_retrieval_traces: list[ArtifactReference] = Field(default_factory=list)
    runtime_evidence_traces: list[ArtifactReference] = Field(default_factory=list)
    runtime_evidence_memos: list[ArtifactReference] = Field(default_factory=list)
    runtime_evidence_pack_manifest: ArtifactReference | None = None
    runtime_evidence_assignment_count: int = Field(default=0, ge=0)
    runtime_evidence_unique_record_count: int = Field(default=0, ge=0)
    runtime_evidence_packed_call_count: int = Field(default=0, ge=0)
    runtime_evidence_avoided_payload_occurrence_count: int = Field(
        default=0,
        ge=0,
    )
    category_brain_manifest: ArtifactReference
    category_brain_index_manifest: ArtifactReference
    category_selected_claims: ArtifactReference
    category_selected_claim_proofs: dict[str, CategoryClaimInclusionProof] = Field(
        default_factory=dict
    )
    category_query_plans: list[CategoryBrainQueryPlan] = Field(default_factory=list)
    category_guidance: list[CategoryBrainGuidance] = Field(default_factory=list)
    beneficiary_graph: ArtifactReference
    final_beneficiary_graph: ArtifactReference | None = None
    compact_final_context: ArtifactReference
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicting_record_ids: list[str] = Field(default_factory=list)
    unexplained_record_ids: list[str] = Field(default_factory=list)
    unresolved_disagreements: list[str] = Field(default_factory=list)
    estimated_token_count: int = Field(ge=0)
    context_complete: bool

    @model_validator(mode="before")
    @classmethod
    def migrate_runtime_retrieval_scope(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and "runtime_retrieval_cluster_ids" not in value
            and value.get("runtime_retrieval_traces")
        ):
            return {
                **value,
                "runtime_retrieval_cluster_ids": list(
                    value.get("material_event_cluster_ids") or []
                ),
            }
        return value

    @model_validator(mode="after")
    def validate_daily_memory_context(self) -> Self:
        if not self.run_id.strip() or not self.memory_snapshot_id.strip():
            raise ValueError("daily memory context identity must be non-empty")
        if len(self.material_event_cluster_ids) != len(
            set(self.material_event_cluster_ids)
        ):
            raise ValueError("daily memory event cluster IDs must be unique")
        if (
            len(self.runtime_retrieval_cluster_ids)
            != len(set(self.runtime_retrieval_cluster_ids))
            or not set(self.runtime_retrieval_cluster_ids).issubset(
                self.material_event_cluster_ids
            )
        ):
            raise ValueError("daily runtime retrieval cluster IDs are invalid")
        if (
            len(self.uncovered_material_event_cluster_ids)
            != len(set(self.uncovered_material_event_cluster_ids))
            or not set(self.uncovered_material_event_cluster_ids).issubset(
                self.material_event_cluster_ids
            )
        ):
            raise ValueError("daily memory uncovered event clusters are invalid")
        if len(self.built_population_keys) != len(set(self.built_population_keys)):
            raise ValueError("daily memory population keys must be unique")
        if set(self.uncovered_population_purposes) != set(
            self.material_event_cluster_ids
        ):
            raise ValueError("daily memory purpose coverage must include every material cluster")
        attempted_purposes = {"catalyst_response", "candidate_error", "newsless"}
        if any(
            len(purposes) != len(set(purposes))
            or not set(purposes).issubset(attempted_purposes)
            for purposes in self.uncovered_population_purposes.values()
        ):
            raise ValueError("daily memory uncovered population purposes are invalid")
        if self.deferred_population_purposes != ["leader_selection"]:
            raise ValueError("daily memory deferred purpose contract mismatch")
        for references in (
            self.population_manifests,
            self.representative_set_manifests,
            self.adaptive_retrieval_traces,
            self.runtime_retrieval_traces,
            self.runtime_evidence_traces,
        ):
            if any(item.item_count != 1 for item in references):
                raise ValueError("daily memory manifest references must contain one item")
            paths = [item.artifact_path for item in references]
            if len(paths) != len(set(paths)):
                raise ValueError("daily memory manifest references must be unique")
        if any(item.item_count < 1 for item in self.runtime_evidence_memos):
            raise ValueError("runtime evidence memo artifacts cannot be empty")
        memo_paths = [item.artifact_path for item in self.runtime_evidence_memos]
        if len(memo_paths) != len(set(memo_paths)):
            raise ValueError("runtime evidence memo references must be unique")
        pack_counts = (
            self.runtime_evidence_assignment_count,
            self.runtime_evidence_unique_record_count,
            self.runtime_evidence_packed_call_count,
        )
        if self.runtime_evidence_pack_manifest is None:
            if any(pack_counts) or self.runtime_evidence_avoided_payload_occurrence_count:
                raise ValueError("runtime evidence pack counts require a manifest")
        elif (
            self.runtime_evidence_pack_manifest.item_count != 1
            or any(count < 1 for count in pack_counts)
            or self.runtime_evidence_unique_record_count
            > self.runtime_evidence_assignment_count
            or self.runtime_evidence_avoided_payload_occurrence_count
            > self.runtime_evidence_assignment_count
        ):
            raise ValueError("runtime evidence pack manifest counts are invalid")
        if self.beneficiary_graph.item_count != 1:
            raise ValueError("daily memory beneficiary graph must contain one artifact")
        if (
            self.final_beneficiary_graph is not None
            and self.final_beneficiary_graph.item_count != 1
        ):
            raise ValueError(
                "daily memory final beneficiary graph must contain one artifact"
            )
        if self.compact_final_context.item_count != 1:
            raise ValueError("daily compact context must contain one artifact")
        if self.category_brain_manifest.item_count != 1:
            raise ValueError("daily category brain manifest must contain one artifact")
        claim_ids = [item.claim_id for item in self.category_guidance]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("daily category guidance claims must be unique")
        plan_clusters = [item.cluster_id for item in self.category_query_plans]
        if len(plan_clusters) != len(set(plan_clusters)):
            raise ValueError("daily category query plans must be unique by cluster")
        if self.context_complete and set(plan_clusters) != set(
            self.material_event_cluster_ids
        ):
            raise ValueError("daily category query plans must cover material clusters")
        selected_claim_ids = {
            claim_id
            for plan in self.category_query_plans
            for claim_id in plan.selected_claim_ids
        } | set(claim_ids)
        if (
            set(self.category_selected_claim_proofs) != selected_claim_ids
            or self.category_selected_claims.item_count != len(selected_claim_ids)
            or any(
                claim_id != proof.claim_id
                for claim_id, proof in self.category_selected_claim_proofs.items()
            )
        ):
            raise ValueError("daily category selected claim proofs are incomplete")
        record_roles = (
            self.supporting_record_ids,
            self.contradicting_record_ids,
            self.unexplained_record_ids,
        )
        if any(len(values) != len(set(values)) for values in record_roles):
            raise ValueError("daily memory record roles must be unique")
        if sum(len(set(values)) for values in record_roles) != len(
            set().union(*(set(values) for values in record_roles))
        ):
            raise ValueError("daily memory record roles must be disjoint")
        if self.context_complete and (
            len(self.population_manifests)
            != len(self.representative_set_manifests)
            or len(self.population_manifests) != len(self.adaptive_retrieval_traces)
        ):
            raise ValueError(
                "complete daily memory requires aligned population, representative, and adaptive artifacts"
            )
        if self.context_complete and self.runtime_retrieval_traces and (
            len(self.runtime_retrieval_traces)
            != len(self.runtime_retrieval_cluster_ids)
        ):
            raise ValueError(
                "runtime retrieval requires one trace per memory-enabled event cluster"
            )
        if self.runtime_evidence_traces and (
            len(self.runtime_evidence_traces) != len(self.runtime_retrieval_traces)
            or len(self.runtime_evidence_memos) != len(self.runtime_evidence_traces)
        ):
            raise ValueError(
                "runtime evidence traces and memo artifacts must align with retrieval traces"
            )
        return self


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
