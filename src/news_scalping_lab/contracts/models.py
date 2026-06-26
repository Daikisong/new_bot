"""Canonical data contracts for research, memory, inference, and audits."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from news_scalping_lab.utils import now_kst


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        use_enum_values=True,
        validate_assignment=True,
    )


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
    thesis: str
    why_now: str
    causal_chain: list[str] = Field(default_factory=list)
    direct_evidence: list[str] = Field(default_factory=list)
    inferred_evidence: list[str] = Field(default_factory=list)
    market_memory_evidence: list[str] = Field(default_factory=list)
    prior_positive_cases: list[str] = Field(default_factory=list)
    prior_negative_cases: list[str] = Field(default_factory=list)
    novel_reasoning: str = ""
    counterarguments: list[str] = Field(default_factory=list)
    disconfirming_conditions: list[str] = Field(default_factory=list)
    confidence_label: ConfidenceLabel = ConfidenceLabel.MEDIUM
    evidence_quality: ConfidenceLabel = ConfidenceLabel.MEDIUM
    source_urls: list[str] = Field(default_factory=list)
    memory_episode_ids: list[str] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)

    @field_validator("rank")
    @classmethod
    def rank_is_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("rank must be positive")
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


class OpenWorldFirstAnalysis(StrictModel):
    schema_version: str = "nslab.open_world_first_analysis.v1"
    run_id: str
    prompt_version: str
    prompt_sha256: str
    created_at: datetime
    cutoff_at: datetime
    event_ids: list[str] = Field(default_factory=list)
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
    verification_dimensions: list[CandidateVerificationDimension] = Field(
        default_factory=list
    )
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
    schema_version: str = "nslab.final_synthesis_context.v1"
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
    known_at: datetime
    provenance: list[Provenance] = Field(default_factory=list)


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
    last_full_rebuild_at: datetime | None = None
    updated_episode_id: str | None = None
    accepted_episode_count: int
    covered_episode_count: int
    covered_episode_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    source_hashes: dict[str, str] = Field(default_factory=dict)
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
    blind_web_search_call_count: int = 0
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
    semantic_retrieval_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_expansion_artifact: str | None = None
    candidate_expansion_sha256: str | None = None
    candidate_expansion_count: int = 0
    candidate_expansion_summary: dict[str, Any] = Field(default_factory=dict)
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
    errors: list[str] = Field(default_factory=list)


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
