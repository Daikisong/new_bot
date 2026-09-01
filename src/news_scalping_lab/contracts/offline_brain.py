"""Contracts for the one-time semantic brain and thin daily inference."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from news_scalping_lab.contracts.models import BlindPrediction, StrictModel

CompactDigestText = Annotated[str, Field(min_length=1, max_length=120)]


class ExactWitness(StrictModel):
    record_id: str
    excerpt: str
    available_from: datetime
    provenance_root: str


class SemanticUnitAssignment(StrictModel):
    record_id: str
    primary_semantic_unit_id: str
    secondary_semantic_unit_ids: list[str] = Field(default_factory=list)
    assignment_basis: list[str] = Field(default_factory=list)
    outlier: bool = False


class SemanticMemoryCapsule(StrictModel):
    schema_version: Literal["nslab.semantic_memory_capsule.v2"] = (
        "nslab.semantic_memory_capsule.v2"
    )
    capsule_id: str
    category: str
    semantic_unit_id: str
    member_record_count: int
    member_independent_unit_count: int
    member_record_root: str
    record_type_distribution: dict[str, int] = Field(default_factory=dict)
    polarity_distribution: dict[str, int] = Field(default_factory=dict)
    label_quality_distribution: dict[str, int] = Field(default_factory=dict)
    time_distribution: dict[str, int] = Field(default_factory=dict)
    regime_distribution: dict[str, int] = Field(default_factory=dict)
    event_or_mechanism_summary: str
    economic_transmission: list[str] = Field(default_factory=list)
    market_narrative: list[str] = Field(default_factory=list)
    applicable_conditions: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    novelty_modality_distinctions: list[str] = Field(default_factory=list)
    leader_selection_implications: list[str] = Field(default_factory=list)
    beneficiary_implications: list[str] = Field(default_factory=list)
    continuation_implications: list[str] = Field(default_factory=list)
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicting_record_ids: list[str] = Field(default_factory=list)
    near_miss_record_ids: list[str] = Field(default_factory=list)
    counterexample_record_ids: list[str] = Field(default_factory=list)
    newsless_or_unexplained_record_ids: list[str] = Field(default_factory=list)
    error_record_ids: list[str] = Field(default_factory=list)
    representative_exact_witnesses: list[ExactWitness] = Field(default_factory=list)
    available_from: datetime
    provenance_root: str
    embedding: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_membership(self) -> Self:
        if self.member_record_count < 1:
            raise ValueError("semantic capsule must contain at least one record")
        member_ids = {
            *self.supporting_record_ids,
            *self.contradicting_record_ids,
            *self.near_miss_record_ids,
            *self.counterexample_record_ids,
            *self.newsless_or_unexplained_record_ids,
            *self.error_record_ids,
        }
        if len(member_ids) > self.member_record_count:
            raise ValueError("semantic capsule role IDs exceed its member population")
        return self


class SynthesizedMechanismClaim(StrictModel):
    schema_version: Literal["nslab.synthesized_mechanism_claim.v2"] = (
        "nslab.synthesized_mechanism_claim.v2"
    )
    claim_id: str
    category: str
    statement: str
    mechanism: str
    conditions: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    supporting_capsule_ids: list[str] = Field(default_factory=list)
    contradicting_capsule_ids: list[str] = Field(default_factory=list)
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicting_record_ids: list[str] = Field(default_factory=list)
    source_node_ids: list[str] = Field(default_factory=list)
    available_from: datetime
    confidence: str
    status: str
    embedding: list[float] = Field(default_factory=list)


class SemanticCapsuleDraft(StrictModel):
    semantic_unit_id: str
    event_or_mechanism_summary: str
    economic_transmission: list[str] = Field(default_factory=list)
    market_narrative: list[str] = Field(default_factory=list)
    applicable_conditions: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    novelty_modality_distinctions: list[str] = Field(default_factory=list)
    leader_selection_implications: list[str] = Field(default_factory=list)
    beneficiary_implications: list[str] = Field(default_factory=list)
    continuation_implications: list[str] = Field(default_factory=list)


class SemanticCapsuleDraftBatch(StrictModel):
    schema_version: Literal["nslab.semantic_capsule_draft_batch.v1"] = (
        "nslab.semantic_capsule_draft_batch.v1"
    )
    node_id: str
    semantic_unit_ids: list[str]
    capsules: list[SemanticCapsuleDraft]

    @model_validator(mode="after")
    def validate_unit_closure(self) -> Self:
        expected = self.semantic_unit_ids
        actual = [row.semantic_unit_id for row in self.capsules]
        if len(expected) != len(set(expected)) or len(actual) != len(set(actual)):
            raise ValueError("semantic capsule draft unit IDs must be unique")
        if set(expected) != set(actual):
            raise ValueError("semantic capsule draft batch omitted or added units")
        return self


class LongPayloadChunkDigest(StrictModel):
    chunk_id: str
    semantic_unit_id: str
    record_id: str
    chunk_index: int = Field(ge=0)
    chunk_count: int = Field(ge=1)
    document_sha256: str
    chunk_sha256: str
    summary: str = Field(min_length=1, max_length=240)
    material_facts: list[CompactDigestText] = Field(default_factory=list, max_length=2)
    mechanisms: list[CompactDigestText] = Field(default_factory=list, max_length=2)
    entities: list[CompactDigestText] = Field(default_factory=list, max_length=2)
    numeric_and_time_facts: list[CompactDigestText] = Field(default_factory=list, max_length=2)
    caveats: list[CompactDigestText] = Field(default_factory=list, max_length=2)


class LongPayloadDigestBatch(StrictModel):
    schema_version: Literal["nslab.long_payload_digest_batch.v1"] = (
        "nslab.long_payload_digest_batch.v1"
    )
    node_id: str
    chunk_ids: list[str]
    digests: list[LongPayloadChunkDigest]

    @model_validator(mode="after")
    def validate_chunk_closure(self) -> Self:
        actual = [row.chunk_id for row in self.digests]
        if len(self.chunk_ids) != len(set(self.chunk_ids)) or len(actual) != len(set(actual)):
            raise ValueError("long payload chunk IDs must be unique")
        if set(self.chunk_ids) != set(actual):
            raise ValueError("long payload digest batch omitted or added chunks")
        return self


class MechanismClaimDraft(StrictModel):
    statement: str
    mechanism: str
    conditions: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    supporting_capsule_ids: list[str] = Field(default_factory=list)
    contradicting_capsule_ids: list[str] = Field(default_factory=list)
    confidence: str
    status: str


class SemanticReduceNode(StrictModel):
    schema_version: Literal["nslab.semantic_reduce_node.v1"] = (
        "nslab.semantic_reduce_node.v1"
    )
    node_id: str
    child_node_ids: list[str]
    covered_capsule_ids: list[str]
    synthesis: str
    mechanisms: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    claims: list[MechanismClaimDraft] = Field(default_factory=list)


class OfflineCompileManifest(StrictModel):
    schema_version: Literal["nslab.offline_compile_manifest.v2"] = (
        "nslab.offline_compile_manifest.v2"
    )
    compile_id: str
    brain_version: str
    source_project: str
    source_memory_snapshot_id: str
    source_memory_manifest_sha256: str
    source_pointer_manifest_sha256: str
    source_pointer_manifest_hash_match: bool
    source_manifest_override_attested: bool
    record_corpus_root: str
    record_count: int
    embedding_identity: str
    embedding_reused: bool
    import_reused: bool
    semantic_splitter_version: str
    full_population_embedding_geometry: bool
    split_p90_cosine_distance: float
    split_max_cosine_distance: float
    semantic_unit_count: int
    leaf_node_count: int
    reduce_node_count: int
    category_root_count: int
    child_omission_count: int
    first_n_shortcut_used: bool
    silent_truncation_count: int
    representative_payload_char_count: int
    representative_payload_full_read_count: int
    representative_payload_truncated_count: int
    chunked_representative_record_count: int
    long_payload_chunk_count: int
    long_payload_chunk_map_call_count: int
    llm_call_count: int
    prompt_token_count: int
    reused_semantic_capsule_count: int = 0
    recompiled_semantic_capsule_count: int = 0
    reused_reduce_node_count: int = 0
    recompiled_reduce_node_count: int = 0
    provider: str
    model: str
    reasoning_effort: str
    max_concurrency: int = Field(ge=1)
    started_at: datetime
    completed_at: datetime


class SemanticInfluenceManifest(StrictModel):
    schema_version: Literal["nslab.semantic_influence_manifest.v2"] = (
        "nslab.semantic_influence_manifest.v2"
    )
    brain_version: str
    record_count: int
    primary_assignment_count: int
    distinct_primary_assigned_record_count: int
    unassigned_record_count: int
    duplicate_primary_assignment_count: int
    semantic_unit_count: int
    rare_outlier_unit_count: int
    rare_outlier_represented_unit_count: int
    unrepresented_reasoning_unit_count: int
    leaf_covered_semantic_unit_count: int
    reduce_covered_capsule_count: int
    final_covered_capsule_count: int
    population_contribution_record_count: int
    representative_payload_exposed_record_count: int
    representative_payload_not_exposed_record_count: int
    representative_payload_exposure_ratio: float
    representative_payload_char_count: int
    representative_payload_full_read_count: int
    representative_payload_truncated_count: int
    chunked_representative_record_count: int
    long_payload_chunk_count: int
    record_membership_root: str
    representative_record_root: str
    representative_payload_read_root: str
    leaf_coverage_root: str
    reduce_tree_root: str

    @model_validator(mode="after")
    def validate_semantic_exposure_accounting(self) -> Self:
        if self.population_contribution_record_count != self.record_count:
            raise ValueError("semantic population contribution must cover every record")
        if (
            self.representative_payload_exposed_record_count
            + self.representative_payload_not_exposed_record_count
            != self.record_count
        ):
            raise ValueError("representative payload exposure counts do not close")
        if self.representative_payload_full_read_count != (
            self.representative_payload_exposed_record_count
        ):
            raise ValueError("every representative payload must be fully read")
        if self.representative_payload_truncated_count:
            raise ValueError("representative payload truncation is forbidden")
        return self


class BrainPackagePointer(StrictModel):
    schema_version: Literal["nslab.brain_package_pointer.v1"] = (
        "nslab.brain_package_pointer.v1"
    )
    brain_version: str
    package_path: str
    manifest_sha256: str
    package_root: str
    production_activated: bool = False


class BrainPackageManifest(StrictModel):
    schema_version: Literal["nslab.brain_package_manifest.v2"] = (
        "nslab.brain_package_manifest.v2"
    )
    brain_version: str
    created_at: datetime
    build_cutoff: datetime
    record_count: int
    semantic_unit_count: int
    semantic_capsule_count: int
    synthesized_mechanism_claim_count: int
    population_contribution_record_count: int
    representative_payload_exposed_record_count: int
    representative_payload_not_exposed_record_count: int
    representative_payload_exposure_ratio: float
    representative_payload_read_root: str
    representative_payload_char_count: int
    representative_payload_full_read_count: int
    representative_payload_truncated_count: int
    chunked_representative_record_count: int
    long_payload_chunk_count: int
    record_corpus_root: str
    memory_snapshot_root: str
    warehouse_root: str
    embedding_identity: str
    compiler_version: str
    provider: str
    model: str
    reasoning_effort: str
    capsule_root: str
    mechanism_claim_root: str
    category_brain_root: str
    package_root: str
    assignment_coverage_ratio: float
    unassigned_record_count: int
    duplicate_primary_assignment_count: int
    rare_outlier_unit_coverage_ratio: float
    unrepresented_reasoning_unit_count: int
    child_omission_count: int
    semantic_capsule_hnsw_index_ready: bool
    mechanism_claim_hnsw_index_ready: bool
    daily_ann_query_plan_verified: bool
    production_eligible: bool = False

    @model_validator(mode="after")
    def validate_payload_accounting(self) -> Self:
        if self.population_contribution_record_count != self.record_count:
            raise ValueError("BrainPackage population contribution is incomplete")
        if (
            self.representative_payload_exposed_record_count
            + self.representative_payload_not_exposed_record_count
            != self.record_count
        ):
            raise ValueError("BrainPackage representative exposure counts do not close")
        if self.representative_payload_full_read_count != (
            self.representative_payload_exposed_record_count
        ):
            raise ValueError("BrainPackage did not fully read every representative payload")
        if self.representative_payload_truncated_count:
            raise ValueError("BrainPackage contains truncated representative payloads")
        return self


class CurrentEventCapsule(StrictModel):
    schema_version: Literal["nslab.current_event_capsule.v1"] = (
        "nslab.current_event_capsule.v1"
    )
    cluster_id: str
    source_row_ids: list[int]
    event_ids: list[str]
    source_ids: list[str]
    representative_title: str
    predicate_exact_sentences: list[str] = Field(default_factory=list)
    issuer_company_literals: list[str] = Field(default_factory=list)
    ticker_literals: list[str] = Field(default_factory=list)
    counterparty_literals: list[str] = Field(default_factory=list)
    numeric_unit_literals: list[str] = Field(default_factory=list)
    modality_literals: list[str] = Field(default_factory=list)
    published_times: list[datetime] = Field(default_factory=list)
    exact_duplicate_count: int = 0
    semantic_duplicate_count: int = 0
    conflict_flags: list[str] = Field(default_factory=list)
    projection_tier: Literal["FULL", "COMPACT", "IDENTITY_ONLY"] = "FULL"

    @field_validator("source_row_ids", "event_ids", "source_ids")
    @classmethod
    def required_coverage_ids(cls, value: list[Any]) -> list[Any]:
        if not value:
            raise ValueError("current event capsule coverage IDs cannot be empty")
        return value


class CurrentDayInterpretation(StrictModel):
    schema_version: Literal["nslab.current_day_interpretation.v1"] = (
        "nslab.current_day_interpretation.v1"
    )
    analyzed_cluster_ids: list[str]
    event_map: list[str] = Field(default_factory=list)
    direct_issuer_events: list[str] = Field(default_factory=list)
    policy_industry_macro_mechanisms: list[str] = Field(default_factory=list)
    candidate_archetypes: list[str] = Field(default_factory=list)
    potential_sectors: list[str] = Field(default_factory=list)
    beneficiary_paths: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)


class DailyBrainContext(StrictModel):
    schema_version: Literal["nslab.daily_brain_context.v1"] = (
        "nslab.daily_brain_context.v1"
    )
    brain_version: str
    brain_package_root: str
    interpretation_sha256: str
    selected_semantic_capsules: list[SemanticMemoryCapsule] = Field(
        default_factory=list
    )
    selected_mechanism_claims: list[SynthesizedMechanismClaim] = Field(
        default_factory=list
    )
    population_statistics: list[dict[str, Any]] = Field(default_factory=list)
    current_vs_history_differences: list[str] = Field(default_factory=list)
    beneficiary_graph: list[dict[str, Any]] = Field(default_factory=list)
    leader_selection_memory: list[dict[str, Any]] = Field(default_factory=list)
    continuation_memory: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_contradictions: list[str] = Field(default_factory=list)
    exact_witnesses: list[ExactWitness] = Field(default_factory=list)
    retrieval_query_count: int = 0
    index_query_count: int = 0
    online_full_corpus_scan_count: int = 0
    future_record_count: int = 0

    @model_validator(mode="after")
    def validate_daily_bounds(self) -> Self:
        if len(self.exact_witnesses) > 24:
            raise ValueError("daily brain context cannot expose more than 24 raw witnesses")
        if self.online_full_corpus_scan_count != 0:
            raise ValueError("daily brain context cannot perform a full corpus scan")
        if self.future_record_count != 0:
            raise ValueError("daily brain context cannot contain future records")
        return self


class ThinDailyRunManifest(StrictModel):
    schema_version: Literal["nslab.thin_daily_run_manifest.v1"] = (
        "nslab.thin_daily_run_manifest.v1"
    )
    run_id: str
    trade_date: date
    cutoff_at: datetime
    created_at: datetime
    wall_clock_seconds: float
    evidence_policy: Literal["CSV_MEMORY_ONLY_STRICT"] = "CSV_MEMORY_ONLY_STRICT"
    news_file: str
    news_sha256: str
    total_news_row_count: int
    cutoff_safe_news_row_count: int
    row_disposition_count: int
    material_event_cluster_count: int
    current_event_capsule_count: int
    current_event_capsule_bytes: int
    current_event_prompt_bytes: int
    daily_brain_context_bytes: int
    historical_raw_witness_count: int
    logical_llm_call_count: int
    maximum_live_agent_call_count: int
    historical_raw_daily_map_call_count: int
    daily_import_call_count: int
    daily_brain_rebuild_call_count: int
    blind_web_search_call_count: int
    online_full_corpus_scan_count: int
    future_record_count: int
    llm_purposes: list[str]
    llm_model_config: dict[str, Any]
    brain_version: str
    brain_package_root: str
    current_event_capsules_artifact: str
    current_event_capsules_sha256: str
    current_day_interpretation_artifact: str
    current_day_interpretation_sha256: str
    daily_brain_context_artifact: str
    daily_brain_context_sha256: str
    row_disposition_artifact: str
    row_disposition_sha256: str
    prediction_artifact: str
    prediction_sha256: str
    report_artifact: str
    report_sha256: str
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    token_counts: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_architecture_contract(self) -> Self:
        if self.wall_clock_seconds < 0:
            raise ValueError("daily wall-clock duration cannot be negative")
        if self.logical_llm_call_count != 2:
            raise ValueError("thin daily inference must use exactly two logical LLM calls")
        if self.maximum_live_agent_call_count > 4:
            raise ValueError("thin daily inference cannot exceed four live calls including repairs")
        forbidden = (
            self.historical_raw_daily_map_call_count,
            self.daily_import_call_count,
            self.daily_brain_rebuild_call_count,
            self.blind_web_search_call_count,
            self.online_full_corpus_scan_count,
            self.future_record_count,
        )
        if any(forbidden):
            raise ValueError("thin daily inference violated an offline/online boundary")
        return self


class ThinDailyAnalysis(StrictModel):
    schema_version: Literal["nslab.thin_daily_analysis.v1"] = (
        "nslab.thin_daily_analysis.v1"
    )
    run_id: str
    trade_date: date
    cutoff_at: datetime
    created_at: datetime
    blind_prediction: BlindPrediction
    context_manifest: ThinDailyRunManifest
    report_path: str
    prediction_path: str
