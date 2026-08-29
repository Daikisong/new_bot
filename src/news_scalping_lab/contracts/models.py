"""Canonical data contracts for research, memory, inference, and audits."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from news_scalping_lab.utils import as_kst, canonical_json, now_kst, sha256_text


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        use_enum_values=True,
        validate_assignment=True,
    )


def _looks_like_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


class ConfidenceLabel(StrEnum):
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    SPECULATIVE = "speculative"


class PathType(StrEnum):
    SINGLE_EVENT = "SINGLE_EVENT"
    THEME_BENEFICIARY = "THEME_BENEFICIARY"
    CONTINUATION = "CONTINUATION"
    HYBRID = "HYBRID"


class NewsNoveltyLabel(StrEnum):
    NEW = "new"
    FOLLOW_UP = "follow_up"
    RECYCLED = "recycled"
    UNCLEAR = "unclear"


class CandidateExpansionPath(StrEnum):
    SINGLE_EVENT = "SINGLE_EVENT"
    THEME_FORMATION = "THEME_FORMATION"
    BENEFICIARY_DISCOVERY = "BENEFICIARY_DISCOVERY"
    CONTINUATION = "CONTINUATION"


class CandidateVerificationStatus(StrEnum):
    SOURCE_COLLECTED = "source_collected"
    NEEDS_COMPANY_DISCOVERY = "needs_company_discovery"
    NO_CUTOFF_SAFE_SOURCE = "no_cutoff_safe_source"


class RelationClass(StrEnum):
    DIRECT = "DIRECT"
    FUNDAMENTAL = "FUNDAMENTAL"
    MARKET_MEMORY = "MARKET_MEMORY"
    CONTINUATION = "CONTINUATION"
    INFERRED_NEW = "INFERRED_NEW"


class ClaimStatus(StrEnum):
    TENTATIVE = "tentative"
    SUPPORTED = "supported"
    VALIDATED = "validated"
    DISPUTED = "disputed"
    RETIRED = "retired"


class FailureCode(StrEnum):
    INPUT_MISSING = "INPUT_MISSING"
    ENTITY_MISSING = "ENTITY_MISSING"
    THEME_MAP_MISSING = "THEME_MAP_MISSING"
    CONTINUATION_MISSING = "CONTINUATION_MISSING"
    RANKING_MISS = "RANKING_MISS"
    TIMING_IMPOSSIBLE = "TIMING_IMPOSSIBLE"
    NOVELTY_ERROR = "NOVELTY_ERROR"
    DIRECTNESS_ERROR = "DIRECTNESS_ERROR"
    LEADER_SELECTION_MISS = "LEADER_SELECTION_MISS"
    MARKET_REGIME_MISS = "MARKET_REGIME_MISS"
    HINDSIGHT_CONTAMINATION = "HINDSIGHT_CONTAMINATION"
    UNKNOWN = "UNKNOWN"


class Provenance(StrictModel):
    source_id: str
    source_type: str
    uri: str
    content_sha256: str | None = None
    excerpt: str | None = None
    observed_at: datetime | None = None


class NewsItem(StrictModel):
    event_id: str
    row_number: int
    published_at: datetime
    collected_at: datetime | None = None
    title: str
    body: str
    source_id: str
    provenance: list[Provenance] = Field(default_factory=list)

    @property
    def combined_text(self) -> str:
        return f"{self.title}\n{self.body}"


class BlindAnalysis(StrictModel):
    summary: str
    open_world_mechanisms: list[str] = Field(default_factory=list)
    initial_uncertainties: list[str] = Field(default_factory=list)
    excluded_after_cutoff_source_ids: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)


class Candidate(StrictModel):
    rank: int
    ticker: str
    company_name: str
    path_type: PathType
    event_ids: list[str] = Field(default_factory=list)
    claimed_theme_id: str | None = None
    claims_news_cause: bool = False
    thesis: str
    why_now: str
    causal_chain: list[str] = Field(default_factory=list)
    direct_evidence: list[str] = Field(default_factory=list)
    inferred_evidence: list[str] = Field(default_factory=list)
    market_memory_evidence: list[str] = Field(default_factory=list)
    prior_positive_cases: list[str] = Field(default_factory=list)
    prior_negative_cases: list[str] = Field(default_factory=list)
    prior_positive_record_ids: list[str] = Field(default_factory=list)
    prior_negative_record_ids: list[str] = Field(default_factory=list)
    novel_reasoning: str = ""
    counterarguments: list[str] = Field(default_factory=list)
    disconfirming_conditions: list[str] = Field(default_factory=list)
    confidence_label: ConfidenceLabel = ConfidenceLabel.MEDIUM
    evidence_quality: ConfidenceLabel = ConfidenceLabel.MEDIUM
    source_urls: list[str] = Field(default_factory=list)
    memory_episode_ids: list[str] = Field(default_factory=list)
    memory_record_ids: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)

    @field_validator("rank")
    @classmethod
    def rank_is_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("rank must be positive")
        return value

    @field_validator("claimed_theme_id")
    @classmethod
    def claimed_theme_id_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("claimed_theme_id cannot be blank")
        return value


class RedTeamAttackCheck(StrictModel):
    name: str
    status: str
    objection: str = ""
    evidence_source_ids: list[str] = Field(default_factory=list)
    passed_to_synthesis: bool = True


class RedTeamFinding(StrictModel):
    candidate_rank: int
    ticker: str
    company_name: str
    path_type: PathType
    attack_summary: str
    attack_checks: list[RedTeamAttackCheck] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    contrary_evidence: list[str] = Field(default_factory=list)
    disconfirming_conditions: list[str] = Field(default_factory=list)
    verification_questions: list[str] = Field(default_factory=list)
    passed_to_synthesis: bool = True


class RedTeamArtifact(StrictModel):
    schema_version: str = "nslab.red_team_artifact.v1"
    run_id: str
    source_prediction_id: str
    prompt_version: str
    prompt_sha256: str
    created_at: datetime
    candidate_count: int
    required_attack_checks: list[str] = Field(default_factory=list)
    candidate_findings: list[RedTeamFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class NewsNoveltyFinding(StrictModel):
    cluster_id: str
    cluster_index: int
    row_numbers: list[int] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    novelty: NewsNoveltyLabel = NewsNoveltyLabel.UNCLEAR
    first_public_evidence_at: datetime | None = None
    evidence_source_ids: list[str] = Field(default_factory=list)
    after_hours_new_disclosure: str = "unclear"
    recycled_news: str = "unclear"
    contract_stage: str = "unclear"
    attributable_amount: str | None = None
    customer: str | None = None
    period: str | None = None
    approval_stage: str | None = None
    dilution_or_financing_risks: list[str] = Field(default_factory=list)
    evidence_summary: str = ""
    uncertainties: list[str] = Field(default_factory=list)
    time_verified: bool = False


class NewsNoveltyReview(StrictModel):
    schema_version: str = "nslab.news_novelty_review.v1"
    run_id: str
    prompt_version: str
    prompt_sha256: str
    created_at: datetime
    cutoff_at: datetime
    review_mode: str
    cluster_count: int
    reviewed_cluster_count: int
    findings: list[NewsNoveltyFinding] = Field(default_factory=list)
    excluded_after_cutoff_source_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OpenWorldClusterFinding(StrictModel):
    cluster_id: str
    event_summary: str
    mechanisms: list[str] = Field(default_factory=list)
    direct_candidates: list[str] = Field(default_factory=list)
    potential_sectors: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_semantic_content(self) -> Self:
        if not self.event_summary.strip():
            raise ValueError("cluster finding requires an event summary")
        if not any(value.strip() for value in [*self.mechanisms, *self.uncertainties]):
            raise ValueError("cluster finding requires mechanisms or uncertainties")
        return self


class OpenWorldFirstAnalysis(StrictModel):
    schema_version: Literal["nslab.open_world_first_analysis.v2"] = "nslab.open_world_first_analysis.v2"
    run_id: str
    prompt_version: str
    prompt_sha256: str
    created_at: datetime
    cutoff_at: datetime
    event_ids: list[str] = Field(default_factory=list)
    source_cluster_ids: list[str] = Field(default_factory=list)
    analyzed_cluster_ids: list[str] = Field(default_factory=list)
    uncovered_cluster_ids: list[str] = Field(default_factory=list)
    analysis_batch_count: int = 0
    cluster_findings: list[OpenWorldClusterFinding] = Field(default_factory=list)
    event_clusters: list[str] = Field(default_factory=list)
    direct_company_events: list[str] = Field(default_factory=list)
    policy_industry_events: list[str] = Field(default_factory=list)
    mechanisms: list[str] = Field(default_factory=list)
    beneficiary_transmission_paths: list[str] = Field(default_factory=list)
    narrative_conversion_points: list[str] = Field(default_factory=list)
    direct_candidates: list[str] = Field(default_factory=list)
    potential_sectors: list[str] = Field(default_factory=list)
    beneficiary_investigation_questions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SemanticRetrievalQuery(StrictModel):
    category: str
    query: str
    rationale: str = ""
    related_cluster_ids: list[str] = Field(default_factory=list)
    coverage_query: bool = False


class SemanticRetrievalPlan(StrictModel):
    schema_version: str = "nslab.semantic_retrieval_plan.v1"
    run_id: str
    prompt_version: str
    prompt_sha256: str
    created_at: datetime
    cutoff_at: datetime
    queries: list[SemanticRetrievalQuery] = Field(default_factory=list)
    required_categories: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CandidateExpansionFinding(StrictModel):
    path: CandidateExpansionPath
    hypothesis: str
    candidate_names: list[str] = Field(default_factory=list)
    sector_hypotheses: list[str] = Field(default_factory=list)
    investigation_questions: list[str] = Field(default_factory=list)
    evidence_source_ids: list[str] = Field(default_factory=list)
    related_cluster_ids: list[str] = Field(default_factory=list)
    memory_episode_ids: list[str] = Field(default_factory=list)
    requires_web_company_discovery: bool = True
    d_minus_one_market_data_only: bool = False
    uncertainties: list[str] = Field(default_factory=list)


class CandidateExpansionReview(StrictModel):
    schema_version: str = "nslab.candidate_expansion.v1"
    run_id: str
    prompt_version: str
    prompt_sha256: str
    created_at: datetime
    cutoff_at: datetime
    required_paths: list[CandidateExpansionPath] = Field(default_factory=list)
    findings: list[CandidateExpansionFinding] = Field(default_factory=list)
    covered_cluster_ids: list[str] = Field(default_factory=list)
    audit_only_cluster_ids: list[str] = Field(default_factory=list)
    uncovered_cluster_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CandidateVerificationDimension(StrictModel):
    name: str
    status: CandidateVerificationStatus
    evidence_source_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CandidateVerificationFinding(StrictModel):
    subject_type: str
    candidate_rank: int
    candidate_ticker: str = ""
    candidate_company_name: str
    candidate_path_type: str
    candidate_expansion_path: str | None = None
    query: str
    source_count: int = 0
    excluded_source_count: int = 0
    accepted_source_ids: list[str] = Field(default_factory=list)
    excluded_source_ids: list[str] = Field(default_factory=list)
    verification_dimensions: list[CandidateVerificationDimension] = Field(default_factory=list)
    blind_safe_market_snapshot: dict[str, Any] = Field(default_factory=dict)
    d_minus_one_market_data_only: bool = False
    uncertainties: list[str] = Field(default_factory=list)


class CandidateVerificationReview(StrictModel):
    schema_version: str = "nslab.candidate_verification.v1"
    run_id: str
    created_at: datetime
    cutoff_at: datetime
    required_dimensions: list[str] = Field(default_factory=list)
    subject_count: int = 0
    findings: list[CandidateVerificationFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FinalSynthesisContextArtifact(StrictModel):
    schema_version: Literal[
        "nslab.final_synthesis_context.v2",
        "nslab.final_synthesis_context.v3",
    ] = "nslab.final_synthesis_context.v3"
    run_id: str
    prompt_version: str
    required_inputs: list[str] = Field(default_factory=list)
    payload_sha256: str
    input_summary: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class DominantSectorHypothesis(StrictModel):
    name: str
    triggering_events: list[str] = Field(default_factory=list)
    formation_mechanism: str
    expected_breadth: str
    direct_beneficiaries: list[str] = Field(default_factory=list)
    indirect_beneficiaries: list[str] = Field(default_factory=list)
    narrative_beneficiaries: list[str] = Field(default_factory=list)
    possible_leaders: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    supporting_cases: list[str] = Field(default_factory=list)
    contradicting_cases: list[str] = Field(default_factory=list)
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicting_record_ids: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)


class BlindPrediction(StrictModel):
    schema_version: str = "nslab.blind_prediction.v1"
    prediction_id: str
    trade_date: date
    cutoff_at: datetime
    created_at: datetime
    sealed_at: datetime | None = None
    blind_artifact_sha256: str | None = None
    blind_analysis: BlindAnalysis
    dominant_sectors: list[DominantSectorHypothesis] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    context_manifest_id: str | None = None


class OutcomeLabels(StrictModel):
    open_gap_pct: float | None = None
    intraday_high_return_pct: float | None = None
    close_return_pct: float | None = None
    upper_limit_touched: bool | None = None
    upper_limit_closed: bool | None = None
    upper_limit_released: bool | None = None
    one_price_upper_limit: bool | None = None
    volume: float | None = None
    amount: float | None = None
    turnover_ratio: float | None = None
    market_cap_previous_close: float | None = None
    intraday_fields_unavailable: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class Postmortem(StrictModel):
    summary: str
    hits: list[str] = Field(default_factory=list)
    misses: list[str] = Field(default_factory=list)
    false_positives: list[str] = Field(default_factory=list)
    failure_codes: list[FailureCode] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)


class EvaluationMetrics(StrictModel):
    candidate_count: int
    upper_limit_hits_at_5: int
    upper_limit_hits_at_10: int
    upper_limit_hits_at_20: int
    upper_limit_recall_at_5: float | None = None
    upper_limit_recall_at_10: float | None = None
    upper_limit_recall_at_20: float | None = None
    upper_limit_closed_recall_at_5: float | None = None
    upper_limit_closed_recall_at_10: float | None = None
    upper_limit_closed_recall_at_20: float | None = None
    high_return_5pct_recall_at_5: float | None = None
    high_return_5pct_recall_at_10: float | None = None
    high_return_5pct_recall_at_20: float | None = None
    high_return_10pct_recall_at_5: float | None = None
    high_return_10pct_recall_at_10: float | None = None
    high_return_10pct_recall_at_20: float | None = None
    high_return_15pct_recall_at_5: float | None = None
    high_return_15pct_recall_at_10: float | None = None
    high_return_15pct_recall_at_20: float | None = None
    high_return_20pct_recall_at_5: float | None = None
    high_return_20pct_recall_at_10: float | None = None
    high_return_20pct_recall_at_20: float | None = None
    recall_unavailable_reason: str | None = None
    precision_at_5: float | None = None
    precision_at_10: float | None = None
    theme_recall: float | None = None
    single_event_recall: float | None = None
    beneficiary_recall: float | None = None
    continuation_recall: float | None = None
    average_max_return_top_5: float | None = None
    average_max_return_top_10: float | None = None
    average_max_return_top_20: float | None = None
    gap_up_hit_rate: float | None = None
    false_positive_rate: float | None = None
    high_return_5pct_hit_rate: float | None = None
    high_return_10pct_hit_rate: float | None = None
    high_return_15pct_hit_rate: float | None = None
    high_return_20pct_hit_rate: float | None = None
    upper_limit_touched_count: int
    upper_limit_closed_count: int


class EligibilityMatrix(StrictModel):
    forecast_evaluation_eligible: bool = False
    direct_supervised_cases_eligible: bool = False
    theme_supervised_cases_eligible: bool = False
    leader_pair_training_eligible: bool = False
    retrospective_memory_eligible: bool = False
    brain_eligible: bool = False
    reasons: dict[str, str] = Field(default_factory=dict)


class EventTickerEdge(StrictModel):
    edge_id: str
    episode_id: str
    event_id: str
    ticker: str
    company_name: str
    relation_class: RelationClass
    relation_explanation: str
    directly_mentioned: bool
    fundamental_evidence: list[str] = Field(default_factory=list)
    narrative_evidence: list[str] = Field(default_factory=list)
    market_memory_evidence: list[str] = Field(default_factory=list)
    temporal_validity: str
    confidence_label: ConfidenceLabel = ConfidenceLabel.MEDIUM
    provenance: list[Provenance] = Field(default_factory=list)


class MemoryClaim(StrictModel):
    claim_id: str
    statement: str
    mechanism: str
    scope: str
    conditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    support_episode_ids: list[str] = Field(default_factory=list)
    contradiction_episode_ids: list[str] = Field(default_factory=list)
    near_miss_episode_ids: list[str] = Field(default_factory=list)
    status: ClaimStatus = ClaimStatus.TENTATIVE
    confidence_label: ConfidenceLabel = ConfidenceLabel.MEDIUM
    first_observed_at: date | None = None
    last_updated_at: datetime | None = None
    available_from: datetime
    provenance: list[Provenance] = Field(default_factory=list)


class MechanismMemory(StrictModel):
    mechanism_id: str
    natural_language_description: str
    causal_chain: list[str] = Field(default_factory=list)
    observed_variations: list[str] = Field(default_factory=list)
    successful_cases: list[str] = Field(default_factory=list)
    failed_cases: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    leader_selection_notes: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)


class CompanyMemory(StrictModel):
    ticker: str
    company_name: str
    aliases: list[str] = Field(default_factory=list)
    business_descriptions: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    customers: list[str] = Field(default_factory=list)
    supply_chain_roles: list[str] = Field(default_factory=list)
    prior_market_narratives: list[str] = Field(default_factory=list)
    prior_leader_occurrences: list[str] = Field(default_factory=list)
    contradictory_relations: list[str] = Field(default_factory=list)
    available_from: datetime
    known_at: datetime
    provenance: list[Provenance] = Field(default_factory=list)
    production_attestation: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_available_from(cls, value: Any) -> Any:
        if isinstance(value, dict) and "available_from" not in value and "known_at" in value:
            return {**value, "available_from": value["known_at"]}
        return value

    @model_validator(mode="after")
    def validate_temporal_availability(self) -> Self:
        if as_kst(self.known_at) < as_kst(self.available_from):
            raise ValueError("company memory known_at must not precede available_from")
        return self


class ResearchEpisode(StrictModel):
    schema_version: str = "nslab.research_episode.v1"
    episode_id: str
    trade_date: date
    cutoff_at: datetime
    created_at: datetime
    execution_protocol_version: str | None = None
    research_version: str
    input_news_files: list[str] = Field(default_factory=list)
    input_news_hashes: list[str] = Field(default_factory=list)
    input_audit: dict[str, Any] = Field(default_factory=dict)
    row_disposition_summary: dict[str, Any] = Field(default_factory=dict)
    blind_integrity: dict[str, Any] = Field(default_factory=dict)
    blind_artifact_sha256: str | None = None
    blind_seal_receipt: dict[str, Any] = Field(default_factory=dict)
    price_source_snapshot: dict[str, Any] = Field(default_factory=dict)
    blind_analysis: BlindAnalysis
    blind_predictions: list[Candidate] = Field(default_factory=list)
    outcome_labels: dict[str, OutcomeLabels] = Field(default_factory=dict)
    postmortem: Postmortem | None = None
    observed_events: list[NewsItem] = Field(default_factory=list)
    event_ticker_edges: list[EventTickerEdge] = Field(default_factory=list)
    lessons: list[MemoryClaim] = Field(default_factory=list)
    counterexamples: list[MemoryClaim] = Field(default_factory=list)
    misses: list[str] = Field(default_factory=list)
    eligibility_matrix: EligibilityMatrix = Field(default_factory=EligibilityMatrix)
    outcome_coverage_status: str = "UNKNOWN"
    provenance: list[Provenance] = Field(default_factory=list)
    available_from: datetime


class BrainManifest(StrictModel):
    schema_version: str = "nslab.brain_manifest.v1"
    brain_version: str
    created_at: datetime
    build_mode: str = "full"
    catalog_only: bool = False
    catalog_mode_reason: str | None = None
    deprecated_mode_alias: bool = False
    production_eligible: bool = False
    evidence_policy: str = "csv-memory-only-strict"
    web_provider: str = "disabled"
    web_required: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    codex_cli_version: str | None = None
    reasoning_effort: str | None = None
    live_agent_call_count: int = 0
    cache_hit_count: int = 0
    structured_validation_status: str | None = None
    oauth_health_check_status: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_revision: str | None = None
    embedding_artifact_sha256: str | None = None
    embedding_dimensions: int = 0
    embedding_normalization: str | None = None
    embedding_device: str | None = None
    last_full_rebuild_at: datetime | None = None
    updated_episode_id: str | None = None
    accepted_episode_count: int
    covered_episode_count: int
    covered_episode_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    compiled_claim_ids: list[str] = Field(default_factory=list)
    compiled_claim_count: int = 0
    compiled_claims_sha256: str | None = None
    source_hashes: dict[str, str] = Field(default_factory=dict)
    brain_record_cutoff_at: datetime | None = None
    excluded_future_record_count: int = 0
    excluded_future_record_ids_sha256: str | None = None
    excluded_future_episode_count: int = 0
    excluded_future_episode_ids_sha256: str | None = None
    production_memory_snapshot_id: str | None = None
    production_memory_corpus_sha256: str | None = None
    production_memory_source_generation_sha256: str | None = None
    production_memory_as_of_cutoff: datetime | None = None
    category_brain_index_manifest_artifact: str | None = None
    category_brain_index_manifest_sha256: str | None = None
    coverage_complete: bool


class PriceSnapshot(StrictModel):
    source_name: str
    source_ref: str | None = None
    as_of: datetime | None = None
    allowed_through: date | None = None
    notes: list[str] = Field(default_factory=list)


class ContextManifest(StrictModel):
    schema_version: str = "nslab.context_manifest.v1"
    run_id: str
    mode: str
    trade_date: date
    cutoff_at: datetime
    as_of: datetime
    created_at: datetime = Field(default_factory=now_kst)
    news_file: str | None = None
    news_sha256: str | None = None
    news_window_start_at: datetime | None = None
    news_window_end_at: datetime | None = None
    news_row_count: int = 0
    included_news_row_count: int = 0
    excluded_news_row_count: int = 0
    blind_context_mode: str = "NEWS_ONLY_STRICT"
    evidence_policy: Literal[
        "csv-memory-only-strict",
        "postclose-web-audit-optional",
    ] = "csv-memory-only-strict"
    web_provider: str = "disabled"
    web_required: bool = False
    blind_web_search_call_count: int = 0
    external_web_evidence_count: int = 0
    blind_price_repository_access_count: int = 0
    blind_current_price_access_count: int = 0
    blind_artifact_sha256: str | None = None
    prediction_artifact: str | None = None
    prediction_sha256: str | None = None
    report_artifact: str | None = None
    report_sha256: str | None = None
    blind_seal_receipt_artifact: str | None = None
    blind_seal_receipt_sha256: str | None = None
    phase_state_artifact: str | None = None
    phase_state_sha256: str | None = None
    no_d_outcome_exposed: bool = True
    continuation_analysis_status: str = "LIMITED_OR_UNAVAILABLE"
    brain_version: str | None = None
    compiler_mode: str | None = None
    brain_compiler_provider: str | None = None
    brain_compiler_model: str | None = None
    brain_compiler_catalog_only: bool | None = None
    brain_files: list[str] = Field(default_factory=list)
    brain_file_hashes: dict[str, str] = Field(default_factory=dict)
    shard_brain_files: list[str] = Field(default_factory=list)
    shard_brain_file_hashes: dict[str, str] = Field(default_factory=dict)
    accepted_episode_count: int
    total_accepted_episode_count: int = 0
    total_accepted_episode_ids: list[str] = Field(default_factory=list)
    available_episode_count: int = 0
    unavailable_episode_count: int = 0
    unavailable_episode_ids: list[str] = Field(default_factory=list)
    swept_episode_count: int
    swept_episode_ids: list[str] = Field(default_factory=list)
    retrieved_episode_ids: list[str] = Field(default_factory=list)
    excluded_retrieved_episode_ids: list[str] = Field(default_factory=list)
    counterexample_episode_ids: list[str] = Field(default_factory=list)
    accepted_record_count: int = 0
    available_record_count: int = 0
    available_record_ids: list[str] = Field(default_factory=list)
    training_eligible_available_record_count: int = 0
    training_eligible_available_record_ids: list[str] = Field(default_factory=list)
    swept_record_count: int = 0
    swept_record_ids: list[str] = Field(default_factory=list)
    missing_swept_record_ids: list[str] = Field(default_factory=list)
    unexpected_swept_record_ids: list[str] = Field(default_factory=list)
    duplicate_swept_record_ids: list[str] = Field(default_factory=list)
    memory_coverage_manifest_artifact: str | None = None
    memory_coverage_manifest_sha256: str | None = None
    memory_coverage_corpus_sha256: str | None = None
    memory_coverage_cache_hit: bool = False
    retrieved_record_ids: list[str] = Field(default_factory=list)
    excluded_retrieved_record_ids: list[str] = Field(default_factory=list)
    counterexample_record_ids: list[str] = Field(default_factory=list)
    record_sweep_artifacts: list[str] = Field(default_factory=list)
    record_sweep_artifact_hashes: dict[str, str] = Field(default_factory=dict)
    record_sweep_shard_count: int = 0
    record_sweep_cache_hits: int = 0
    memory_sweep_artifacts: list[str] = Field(default_factory=list)
    memory_sweep_artifact_hashes: dict[str, str] = Field(default_factory=dict)
    memory_sweep_shard_count: int = 0
    memory_sweep_cache_hits: int = 0
    row_disposition_artifact: str | None = None
    row_disposition_sha256: str | None = None
    row_disposition_coverage_ratio: float = 0.0
    row_disposition_summary: dict[str, Any] = Field(default_factory=dict)
    event_cluster_artifact: str | None = None
    event_cluster_sha256: str | None = None
    event_cluster_count: int = 0
    event_cluster_summary: dict[str, Any] = Field(default_factory=dict)
    news_coverage_manifest_artifact: str | None = None
    news_coverage_manifest_sha256: str | None = None
    event_cluster_manifest_artifact: str | None = None
    event_cluster_manifest_sha256: str | None = None
    event_clustering_result_sha256: str | None = None
    evaluation_profile: str | None = None
    prediction_input_boundary_version: Literal[
        "SEALED_BLIND_INPUT.v3"
    ] | None = None
    sealed_blind_input_manifest_sha256: str | None = None
    shared_pre_retrieval_context_artifact: str | None = None
    shared_pre_retrieval_context_sha256: str | None = None
    shared_pre_retrieval_manifest_artifact: str | None = None
    shared_pre_retrieval_manifest_sha256: str | None = None
    shared_pre_retrieval_summary: dict[str, Any] = Field(default_factory=dict)
    parsed_news_root_sha256: str | None = None
    d_minus_one_context_artifact: str | None = None
    d_minus_one_context_sha256: str | None = None
    d_minus_one_candidate_universe_root_sha256: str | None = None
    d_minus_one_snapshot_root_sha256: str | None = None
    d_minus_one_source_revision_sha256: str | None = None
    d_minus_one_snapshot_session_date: date | None = None
    d_minus_one_payload_sha256: str | None = None
    d_minus_one_consumed_payload_sha256: str | None = None
    d_minus_one_projection_status: Literal["PENDING", "BOUND"] | None = None
    d_minus_one_projection_policy: str | None = None
    d_minus_one_projection_root_sha256: str | None = None
    d_minus_one_projection_requested_ticker_count: int | None = None
    d_minus_one_projection_snapshot_count: int | None = None
    d_minus_one_projection_missing_ticker_count: int | None = None
    open_world_first_analysis_artifact: str | None = None
    open_world_first_analysis_sha256: str | None = None
    open_world_first_analysis_summary: dict[str, Any] = Field(default_factory=dict)
    news_novelty_review_artifact: str | None = None
    news_novelty_review_sha256: str | None = None
    news_novelty_review_count: int = 0
    news_novelty_review_summary: dict[str, Any] = Field(default_factory=dict)
    semantic_retrieval_plan_artifact: str | None = None
    semantic_retrieval_plan_sha256: str | None = None
    semantic_retrieval_query_count: int = 0
    semantic_retrieval_artifact: str | None = None
    semantic_retrieval_sha256: str | None = None
    semantic_retrieval_episode_ids: list[str] = Field(default_factory=list)
    excluded_semantic_retrieval_episode_ids: list[str] = Field(default_factory=list)
    semantic_retrieval_record_ids: list[str] = Field(default_factory=list)
    excluded_semantic_retrieval_record_ids: list[str] = Field(default_factory=list)
    semantic_retrieval_summary: dict[str, Any] = Field(default_factory=dict)
    semantic_cluster_coverage_artifact: str | None = None
    semantic_cluster_coverage_sha256: str | None = None
    semantic_cluster_coverage_query_count: int = 0
    semantic_cluster_coverage_ids: list[str] = Field(default_factory=list)
    semantic_cluster_coverage_missing_ids: list[str] = Field(default_factory=list)
    semantic_cluster_coverage_promoted_record_ids: list[str] = Field(default_factory=list)
    semantic_cluster_coverage_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_expansion_artifact: str | None = None
    candidate_expansion_sha256: str | None = None
    candidate_expansion_count: int = 0
    candidate_expansion_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_expansion_cluster_coverage_ids: list[str] = Field(default_factory=list)
    candidate_expansion_audit_only_cluster_ids: list[str] = Field(default_factory=list)
    candidate_expansion_uncovered_cluster_ids: list[str] = Field(default_factory=list)
    source_ledger_artifact: str | None = None
    source_ledger_sha256: str | None = None
    source_ledger_entry_count: int = 0
    source_ledger_summary: dict[str, Any] = Field(default_factory=dict)
    red_team_artifacts: list[str] = Field(default_factory=list)
    red_team_summary: dict[str, Any] = Field(default_factory=dict)
    token_counts: dict[str, int] = Field(default_factory=dict)
    truncations: list[str] = Field(default_factory=list)
    web_queries: list[str] = Field(default_factory=list)
    web_sources: list[str] = Field(default_factory=list)
    excluded_web_source_ids: list[str] = Field(default_factory=list)
    web_source_artifact: str | None = None
    web_source_sha256: str | None = None
    excluded_web_source_artifact: str | None = None
    excluded_web_source_sha256: str | None = None
    excluded_web_source_count: int = 0
    candidate_web_check_artifact: str | None = None
    candidate_web_check_sha256: str | None = None
    candidate_web_check_count: int = 0
    candidate_web_check_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_web_source_ids: list[str] = Field(default_factory=list)
    candidate_verification_artifact: str | None = None
    candidate_verification_sha256: str | None = None
    candidate_verification_count: int = 0
    candidate_verification_summary: dict[str, Any] = Field(default_factory=dict)
    final_synthesis_context_artifact: str | None = None
    final_synthesis_context_sha256: str | None = None
    final_synthesis_context_summary: dict[str, Any] = Field(default_factory=dict)
    daily_memory_context_artifact: str | None = None
    daily_memory_context_sha256: str | None = None
    daily_memory_context_summary: dict[str, Any] = Field(default_factory=dict)
    beneficiary_graph_artifact: str | None = None
    beneficiary_graph_sha256: str | None = None
    excluded_candidate_web_check_artifact: str | None = None
    excluded_candidate_web_check_sha256: str | None = None
    excluded_candidate_web_source_ids: list[str] = Field(default_factory=list)
    excluded_candidate_web_check_count: int = 0
    included_company_memory_files: list[str] = Field(default_factory=list)
    omitted_company_memory_files: list[dict[str, str]] = Field(default_factory=list)
    included_market_context_files: list[str] = Field(default_factory=list)
    omitted_market_context_files: list[dict[str, str]] = Field(default_factory=list)
    price_snapshot: PriceSnapshot
    llm_model_config: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    prompt_batch_hashes: dict[str, list[str]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

    def bind_beneficiary_graph(self, *, artifact_path: str, sha256: str) -> None:
        if not artifact_path.strip() or not _looks_like_sha256(sha256):
            raise ValueError("beneficiary graph artifact binding is invalid")
        object.__setattr__(self, "beneficiary_graph_artifact", artifact_path)
        object.__setattr__(self, "beneficiary_graph_sha256", sha256)

    def bind_daily_memory_context(self, *, artifact_path: str, sha256: str) -> None:
        if not artifact_path.strip() or not _looks_like_sha256(sha256):
            raise ValueError("daily memory context artifact binding is invalid")
        if not self.beneficiary_graph_artifact or not self.beneficiary_graph_sha256:
            raise ValueError("daily memory context requires a beneficiary graph")
        object.__setattr__(self, "daily_memory_context_artifact", artifact_path)
        object.__setattr__(self, "daily_memory_context_sha256", sha256)

    def bind_shared_pre_retrieval_context(
        self,
        *,
        context_artifact_path: str,
        context_sha256: str,
        manifest_artifact_path: str,
        manifest_sha256: str,
        parsed_news_root_sha256: str,
    ) -> None:
        if not all(
            value.strip()
            for value in (context_artifact_path, manifest_artifact_path)
        ) or not all(
            _looks_like_sha256(value)
            for value in (
                context_sha256,
                manifest_sha256,
                parsed_news_root_sha256,
            )
        ):
            raise ValueError("shared pre-retrieval binding is invalid")
        object.__setattr__(self, "evaluation_profile", "QUALITY_FULL")
        object.__setattr__(
            self,
            "shared_pre_retrieval_context_artifact",
            context_artifact_path,
        )
        object.__setattr__(
            self,
            "shared_pre_retrieval_context_sha256",
            context_sha256,
        )
        object.__setattr__(
            self,
            "shared_pre_retrieval_manifest_artifact",
            manifest_artifact_path,
        )
        object.__setattr__(
            self,
            "shared_pre_retrieval_manifest_sha256",
            manifest_sha256,
        )
        object.__setattr__(
            self,
            "parsed_news_root_sha256",
            parsed_news_root_sha256,
        )

    def bind_sealed_blind_input(self, *, manifest_sha256: str) -> None:
        if not _looks_like_sha256(manifest_sha256):
            raise ValueError("sealed blind input manifest hash must be SHA-256")
        object.__setattr__(self, "evaluation_profile", "QUALITY_FULL")
        object.__setattr__(
            self,
            "prediction_input_boundary_version",
            "SEALED_BLIND_INPUT.v3",
        )
        object.__setattr__(
            self,
            "sealed_blind_input_manifest_sha256",
            manifest_sha256,
        )

    def bind_d_minus_one_context(
        self,
        *,
        artifact_path: str,
        sha256: str,
        candidate_universe_root_sha256: str,
        snapshot_root_sha256: str,
        source_revision_sha256: str,
        snapshot_session_date: date | None,
        payload_sha256: str,
    ) -> None:
        if not artifact_path.strip() or not all(
            _looks_like_sha256(value)
            for value in (
                sha256,
                candidate_universe_root_sha256,
                snapshot_root_sha256,
                source_revision_sha256,
                payload_sha256,
            )
        ):
            raise ValueError("D-1 context artifact binding is invalid")
        object.__setattr__(self, "evaluation_profile", "QUALITY_FULL")
        object.__setattr__(self, "d_minus_one_context_artifact", artifact_path)
        object.__setattr__(self, "d_minus_one_context_sha256", sha256)
        object.__setattr__(
            self,
            "d_minus_one_candidate_universe_root_sha256",
            candidate_universe_root_sha256,
        )
        object.__setattr__(
            self,
            "d_minus_one_snapshot_root_sha256",
            snapshot_root_sha256,
        )
        object.__setattr__(
            self,
            "d_minus_one_source_revision_sha256",
            source_revision_sha256,
        )
        object.__setattr__(
            self,
            "d_minus_one_snapshot_session_date",
            snapshot_session_date,
        )
        object.__setattr__(self, "d_minus_one_payload_sha256", payload_sha256)
        object.__setattr__(self, "d_minus_one_projection_status", "PENDING")

    def bind_d_minus_one_prompt_projection(
        self,
        *,
        policy: str,
        consumed_payload_sha256: str,
        projection_root_sha256: str,
        requested_ticker_count: int,
        snapshot_count: int,
        missing_ticker_count: int,
    ) -> None:
        if not policy.strip() or not all(
            _looks_like_sha256(value)
            for value in (
                consumed_payload_sha256,
                projection_root_sha256,
            )
        ):
            raise ValueError("D-1 prompt projection binding is invalid")
        counts = (
            requested_ticker_count,
            snapshot_count,
            missing_ticker_count,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counts
        ):
            raise ValueError("D-1 prompt projection counts are invalid")
        if snapshot_count + missing_ticker_count != requested_ticker_count:
            raise ValueError("D-1 prompt projection disposition is incomplete")
        object.__setattr__(
            self,
            "d_minus_one_consumed_payload_sha256",
            consumed_payload_sha256,
        )
        object.__setattr__(self, "d_minus_one_projection_policy", policy)
        object.__setattr__(
            self,
            "d_minus_one_projection_root_sha256",
            projection_root_sha256,
        )
        object.__setattr__(
            self,
            "d_minus_one_projection_requested_ticker_count",
            requested_ticker_count,
        )
        object.__setattr__(
            self,
            "d_minus_one_projection_snapshot_count",
            snapshot_count,
        )
        object.__setattr__(
            self,
            "d_minus_one_projection_missing_ticker_count",
            missing_ticker_count,
        )
        object.__setattr__(self, "d_minus_one_projection_status", "BOUND")

    @model_validator(mode="after")
    def validate_prompt_batches(self) -> Self:
        for purpose, batch_hashes in self.prompt_batch_hashes.items():
            if purpose not in self.prompt_hashes:
                raise ValueError("prompt batch hashes require an aggregate prompt hash")
            if len(batch_hashes) != len(set(batch_hashes)):
                raise ValueError("prompt batch hashes must be unique per purpose")
            if any(not _looks_like_sha256(value) for value in batch_hashes):
                raise ValueError("prompt batch hashes must be SHA-256 values")
            aggregate = batch_hashes[0] if len(batch_hashes) == 1 else sha256_text(canonical_json(batch_hashes))
            if self.prompt_hashes[purpose] != aggregate:
                raise ValueError("prompt batch aggregate hash mismatch")
        daily_configured = self.daily_memory_context_artifact is not None
        daily_hash_configured = self.daily_memory_context_sha256 is not None
        graph_configured = self.beneficiary_graph_artifact is not None
        graph_hash_configured = self.beneficiary_graph_sha256 is not None
        if daily_configured != daily_hash_configured:
            raise ValueError("daily memory artifact and hash must be configured together")
        if graph_configured != graph_hash_configured:
            raise ValueError("beneficiary graph artifact and hash must be configured together")
        shared_context_configured = (
            self.shared_pre_retrieval_context_artifact is not None
        )
        shared_context_hash_configured = (
            self.shared_pre_retrieval_context_sha256 is not None
        )
        shared_manifest_configured = (
            self.shared_pre_retrieval_manifest_artifact is not None
        )
        shared_manifest_hash_configured = (
            self.shared_pre_retrieval_manifest_sha256 is not None
        )
        if shared_context_configured != shared_context_hash_configured:
            raise ValueError(
                "shared pre-retrieval context artifact and hash must match"
            )
        if shared_manifest_configured != shared_manifest_hash_configured:
            raise ValueError(
                "shared pre-retrieval manifest artifact and hash must match"
            )
        if shared_context_configured != shared_manifest_configured:
            raise ValueError(
                "shared pre-retrieval context and manifest must be bound together"
            )
        if shared_context_configured and self.evaluation_profile != "QUALITY_FULL":
            raise ValueError(
                "shared pre-retrieval context requires QUALITY_FULL profile"
            )
        if shared_context_configured and not _looks_like_sha256(
            str(self.parsed_news_root_sha256)
        ):
            raise ValueError("shared pre-retrieval context requires parsed news root")
        if not shared_context_configured and self.parsed_news_root_sha256 is not None:
            raise ValueError("parsed news root requires shared pre-retrieval context")
        d_minus_one_values = (
            self.d_minus_one_context_artifact,
            self.d_minus_one_context_sha256,
            self.d_minus_one_candidate_universe_root_sha256,
            self.d_minus_one_snapshot_root_sha256,
            self.d_minus_one_source_revision_sha256,
            self.d_minus_one_payload_sha256,
        )
        d_minus_one_configured = [value is not None for value in d_minus_one_values]
        if any(d_minus_one_configured) and not all(d_minus_one_configured):
            raise ValueError("D-1 context artifact and roots must be bound together")
        if all(d_minus_one_configured):
            if not shared_context_configured or self.evaluation_profile != "QUALITY_FULL":
                raise ValueError("D-1 context binding requires shared QUALITY_FULL context")
            if not all(
                _looks_like_sha256(str(value))
                for value in (
                    self.d_minus_one_context_sha256,
                    self.d_minus_one_candidate_universe_root_sha256,
                    self.d_minus_one_snapshot_root_sha256,
                    self.d_minus_one_source_revision_sha256,
                    self.d_minus_one_payload_sha256,
                )
            ):
                raise ValueError("D-1 context binding requires SHA-256 roots")
        projection_values = (
            self.d_minus_one_consumed_payload_sha256,
            self.d_minus_one_projection_policy,
            self.d_minus_one_projection_root_sha256,
            self.d_minus_one_projection_requested_ticker_count,
            self.d_minus_one_projection_snapshot_count,
            self.d_minus_one_projection_missing_ticker_count,
        )
        projection_configured = [value is not None for value in projection_values]
        if any(projection_configured) and not all(projection_configured):
            raise ValueError("D-1 prompt projection binding is incomplete")
        if not any(d_minus_one_configured):
            if self.d_minus_one_projection_status is not None:
                raise ValueError("D-1 projection status requires a full context")
        elif (
            not all(projection_configured)
            and self.d_minus_one_projection_status != "PENDING"
        ):
            raise ValueError("D-1 prompt projection must remain pending until bound")
        if all(projection_configured):
            if self.d_minus_one_projection_status != "BOUND":
                raise ValueError("D-1 prompt projection must be marked bound")
            if not all(
                _looks_like_sha256(str(value))
                for value in (
                    self.d_minus_one_consumed_payload_sha256,
                    self.d_minus_one_projection_root_sha256,
                )
            ):
                raise ValueError("D-1 projection binding requires SHA-256 roots")
            requested_count = self.d_minus_one_projection_requested_ticker_count
            snapshot_count = self.d_minus_one_projection_snapshot_count
            missing_count = self.d_minus_one_projection_missing_ticker_count
            if not all(
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                for value in (requested_count, snapshot_count, missing_count)
            ):
                raise ValueError("D-1 projection counts are invalid")
            assert isinstance(requested_count, int)
            assert isinstance(snapshot_count, int)
            assert isinstance(missing_count, int)
            if (
                snapshot_count + missing_count
                != requested_count
            ):
                raise ValueError("D-1 projection disposition is incomplete")
        boundary_configured = self.prediction_input_boundary_version is not None
        blind_input_hash_configured = (
            self.sealed_blind_input_manifest_sha256 is not None
        )
        if boundary_configured != blind_input_hash_configured:
            raise ValueError(
                "sealed blind input boundary and manifest hash must match"
            )
        if blind_input_hash_configured and not _looks_like_sha256(
            str(self.sealed_blind_input_manifest_sha256)
        ):
            raise ValueError("sealed blind input manifest hash must be SHA-256")
        if boundary_configured and self.evaluation_profile != "QUALITY_FULL":
            raise ValueError("sealed blind inputs require QUALITY_FULL profile")
        if daily_configured and not graph_configured:
            raise ValueError("daily memory context requires a beneficiary graph")
        phase7_prompt = self.llm_model_config.get("final_synthesis_prompt_version") == "synthesis.final.v3"
        if phase7_prompt and not (daily_configured and graph_configured):
            raise ValueError("final synthesis v3 requires daily memory and graph artifacts")
        if daily_hash_configured and not _looks_like_sha256(str(self.daily_memory_context_sha256)):
            raise ValueError("daily memory context hash must be SHA-256")
        if graph_hash_configured and not _looks_like_sha256(str(self.beneficiary_graph_sha256)):
            raise ValueError("beneficiary graph hash must be SHA-256")
        if self.evidence_policy == "csv-memory-only-strict":
            if self.web_required:
                raise ValueError("CSV memory-only evidence cannot require web")
            if self.blind_web_search_call_count != 0:
                raise ValueError("CSV memory-only evidence forbids BLIND web calls")
            if self.external_web_evidence_count != 0:
                raise ValueError("CSV memory-only evidence forbids external web evidence")
            if self.web_sources or self.candidate_web_source_ids or self.candidate_web_check_count:
                raise ValueError("CSV memory-only evidence cannot bind web-derived sources")
        return self


class DailyAnalysis(StrictModel):
    schema_version: str = "nslab.daily_analysis.v1"
    run_id: str
    trade_date: date
    cutoff_at: datetime
    created_at: datetime
    mode: str
    blind_prediction: BlindPrediction
    context_manifest: ContextManifest
    report_path: str
    prediction_path: str
