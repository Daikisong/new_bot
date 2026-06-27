"""Canonical brain record contracts.

The record envelope is intentionally separate from ``ResearchEpisode``. Newer
research bundles can contain far richer supervised records than the legacy
episode model can represent, so the raw payload is preserved first and typed
models are used only as a validation layer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from news_scalping_lab.contracts.models import RelationClass

VALID_OUTCOME_LABEL_QUALITIES = frozenset(
    {
        "verified",
        "quarantined",
        "no_tradable_row",
    }
)


class StrictRecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlexiblePayloadModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class LegacyResearchEpisodeV1(FlexiblePayloadModel):
    schema_version: str = "nslab.research_episode.v1"
    episode_id: str
    trade_date: date
    cutoff_at: datetime
    available_from: datetime


class ResearchBundleEnvelope(StrictRecordModel):
    schema_version: str = "nslab.research_bundle_envelope.v1"
    bundle_schema_version: str
    manifest_schema_version: str | None = None
    episode_schema_version: str | None = None
    episode_id: str
    trade_date: date
    cutoff_at: datetime | None = None
    available_from: datetime
    bundle_status: str | None = None
    blind_valid: bool | None = None
    raw_bundle_sha256: str
    raw_block_hashes: dict[str, str] = Field(default_factory=dict)
    raw_block_counts: dict[str, int] = Field(default_factory=dict)
    raw_block_paths: dict[str, str] = Field(default_factory=dict)
    normalized_episode_index_path: str | None = None
    record_manifest_path: str | None = None
    provenance_closure_status: str = "unchecked"
    adapter_name: str
    import_status: str = "normalized"


class NormalizedEpisodeIndex(StrictRecordModel):
    schema_version: str = "nslab.normalized_episode_index.v1"
    episode_id: str
    trade_date: date
    previous_trade_date: date | None = None
    next_trade_date: date | None = None
    window_start: datetime | None = None
    cutoff_at: datetime | None = None
    available_from: datetime
    bundle_status: str | None = None
    blind_valid: bool | None = None
    research_daily_source: str | None = None
    entity_quality_summary: dict[str, Any] = Field(default_factory=dict)
    fact_quality_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_screening_summary: dict[str, Any] = Field(default_factory=dict)
    entity_resolution_summary: dict[str, Any] = Field(default_factory=dict)
    winner_census: dict[str, Any] = Field(default_factory=dict)
    raw_block_names: list[str] = Field(default_factory=list)
    record_ids: list[str] = Field(default_factory=list)
    record_count_by_type: dict[str, int] = Field(default_factory=dict)
    training_eligible_record_count: int = 0
    source_ids: list[str] = Field(default_factory=list)


class BrainRecordEnvelope(StrictRecordModel):
    schema_version: str = "nslab.brain_record_envelope.v1"
    record_id: str
    record_type: str
    episode_id: str
    trade_date: date
    available_from: datetime
    training_target: str | None = None
    evidence_phase: str = "POSTMORTEM"
    training_eligible: bool = False
    eligibility_reason: str | None = None
    status: str = "tentative"
    confidence_label: str = "low"
    provenance_source_ids: list[str] = Field(default_factory=list)
    raw_payload_sha256: str
    normalized_payload_sha256: str
    typed_payload_status: Literal["KNOWN_TYPED_PAYLOAD", "UNKNOWN_TYPED_PAYLOAD"]
    source_block: str = "brain_delta.jsonl"
    source_line: int | None = None
    payload: dict[str, Any]

    @field_validator("record_id", "record_type", "episode_id")
    @classmethod
    def required_string(cls, value: str) -> str:
        if not value:
            raise ValueError("value must be non-empty")
        return value


class SupervisedIssuerDayCase(FlexiblePayloadModel):
    record_type: Literal["supervised_issuer_day_case"]
    issuer_day_case_id: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    theme_id: str | None = None
    path_type: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    inference_ids: list[str] = Field(default_factory=list)
    safe_D1_features: dict[str, Any] = Field(default_factory=dict)
    D_outcome: dict[str, Any] = Field(default_factory=dict)
    outcome: dict[str, Any] = Field(default_factory=dict)
    response_class: str | None = None
    sample_weight: float | None = None
    event_level_weights: dict[str, float] = Field(default_factory=dict)
    label_quality: str | None = None
    attribution_status: str | None = None
    fact_entailment_verified: bool | None = None
    cross_event_leak_verified: bool | None = None


class SupervisedDirectEventCase(FlexiblePayloadModel):
    record_type: Literal["supervised_direct_event_case"]
    case_id: str | None = None
    issuer_day_case_id: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    theme_id: str | None = None
    path_type: str | None = None
    event_id: str | None = None
    observation_id: str | None = None
    screening_id: str | None = None
    candidate_decision: str | None = None
    blind_candidate_ids: list[str] = Field(default_factory=list)
    blind_rank: int | None = None
    blind_fact_ids: list[str] = Field(default_factory=list)
    blind_inference_ids: list[str] = Field(default_factory=list)
    safe_D1_features: dict[str, Any] = Field(default_factory=dict)
    D_outcome: dict[str, Any] = Field(default_factory=dict)
    outcome: dict[str, Any] = Field(default_factory=dict)
    response_class: str | None = None
    sample_weight: float | None = None
    label_quality: str | None = None
    attribution_status: str | None = None
    fact_entailment_verified: bool | None = None
    cross_event_leak_verified: bool | None = None


class SupervisedThemeFormationCase(FlexiblePayloadModel):
    record_type: Literal["supervised_theme_formation_case"]
    theme_id: str | None = None
    theme_name: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    inference_ids: list[str] = Field(default_factory=list)
    peer_universe: list[str] = Field(default_factory=list)
    chosen_leader_ticker: str | None = None
    chosen_leader_company_name: str | None = None
    rejected_candidate_tickers: list[str] = Field(default_factory=list)
    safe_D1_features: dict[str, Any] = Field(default_factory=dict)
    D_outcome: dict[str, Any] = Field(default_factory=dict)
    outcome: dict[str, Any] = Field(default_factory=dict)
    response_class: str | None = None
    sample_weight: float | None = None
    label_quality: str | None = None
    attribution_status: str | None = None


class ThemeFormationCase(FlexiblePayloadModel):
    record_type: Literal["theme_formation_case"]
    ticker: str | None = None
    company_name: str | None = None
    name: str | None = None
    candidate_lane: str | None = None
    lesson: str | None = None
    source_fact_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    chosen_leader_ticker: str | None = None
    chosen_leader_company_name: str | None = None
    outcome_high_return_pct: float | None = None
    upper_limit_touched: bool | None = None
    sample_weight: float | None = None


class BeneficiaryDiscoveryCase(FlexiblePayloadModel):
    record_type: Literal["beneficiary_discovery_case"]
    case_id: str | None = None
    event_id: str | None = None
    theme_id: str | None = None
    candidate_ticker: str | None = None
    candidate_company_name: str | None = None
    candidate_path_type: str | None = None
    beneficiary_relation: str | None = None
    beneficiary_relation_evidence: list[str] = Field(default_factory=list)
    blind_candidate_ids: list[str] = Field(default_factory=list)
    outcome_ticker: str | None = None
    outcome_company_name: str | None = None
    correction_mode: str | None = None
    sample_weight: float | None = None


class BlindLeaderPreferencePair(FlexiblePayloadModel):
    record_type: Literal["blind_leader_preference_pair"]
    blind_pair_id: str | None = None
    blind_preferred_candidate_id: str | None = None
    blind_rejected_candidate_id: str | None = None
    outcome_preferred_candidate_id: str | None = None
    blind_preferred_ticker: str | None = None
    blind_preferred_company_name: str | None = None
    blind_rejected_ticker: str | None = None
    blind_rejected_company_name: str | None = None
    outcome_winner_ticker: str | None = None
    outcome_winner_company_name: str | None = None
    blind_preference_correct: bool | None = None
    training_mode: str | None = None
    correction_mode: str | None = None


class CandidateGenerationErrorCase(FlexiblePayloadModel):
    record_type: Literal["candidate_generation_error_case"]
    error_id: str | None = None
    error_type: str | None = None
    correction_mode: str | None = None
    missed_ticker: str | None = None
    missed_company_name: str | None = None
    missed_theme_id: str | None = None
    missed_path_type: str | None = None
    source_candidate_ids: list[str] = Field(default_factory=list)
    correction_record_ids: list[str] = Field(default_factory=list)


class CandidateRankingErrorCase(FlexiblePayloadModel):
    record_type: Literal["candidate_ranking_error_case"]
    error_id: str | None = None
    error_type: str | None = None
    correction_mode: str | None = None
    blind_preferred_ticker: str | None = None
    blind_rejected_ticker: str | None = None
    outcome_winner_ticker: str | None = None
    corrected_ticker: str | None = None
    corrected_company_name: str | None = None
    correction_record_ids: list[str] = Field(default_factory=list)


class RankingErrorCase(FlexiblePayloadModel):
    record_type: Literal["ranking_error_case"]
    audit_id: str | None = None
    error_id: str | None = None
    classification: str | None = None
    error_type: str | None = None
    correction: str | None = None
    correction_mode: str | None = None
    matched_screening_ids: list[str] = Field(default_factory=list)
    name_on_D: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    outcome_high_return_pct: float | None = None


class NewslessOrUnexplainedCase(FlexiblePayloadModel):
    record_type: Literal["newsless_or_unexplained_case"]
    audit_id: str | None = None
    input_news_hit_status: str | None = None
    lesson: str | None = None
    name_on_D: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    no_catalyst_asserted: bool | None = None
    outcome_high_return_pct: float | None = None


class NegativeControlCase(FlexiblePayloadModel):
    record_type: Literal["negative_control_case"]
    screening_id: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    name: str | None = None
    candidate_lane: str | None = None
    lesson: str | None = None
    rejection_or_exclusion_reason: str | None = None
    outcome_high_return_pct: float | None = None
    upper_limit_touched: bool | None = None


class ContextMarketStateOrFactCase(FlexiblePayloadModel):
    record_type: Literal["context_market_state_or_fact_case"]
    entity_name: str | None = None
    company_name: str | None = None
    fact_id: str | None = None
    fact_ids: list[str] = Field(default_factory=list)
    fact_type: str | None = None
    issuer_scoped: bool | None = None
    lesson: str | None = None
    row_id: str | None = None
    source_id: str | None = None


class RowDispositionErrorCase(FlexiblePayloadModel):
    record_type: Literal["row_disposition_error_case"]
    error_id: str | None = None
    row_id: str | None = None
    original_disposition: str | None = None
    corrected_disposition: str | None = None
    candidate_ticker: str | None = None
    candidate_company_name: str | None = None
    correction_mode: str | None = None


class EntityResolutionErrorCase(FlexiblePayloadModel):
    record_type: Literal["entity_resolution_error_case"]
    error_id: str | None = None
    unresolved_entity: str | None = None
    original_ticker: str | None = None
    original_company_name: str | None = None
    corrected_ticker: str | None = None
    corrected_company_name: str | None = None
    correction_mode: str | None = None


class MemoryClaimRecord(FlexiblePayloadModel):
    record_type: Literal["memory_claim"]
    claim_id: str | None = None
    statement: str | None = None
    mechanism: str | None = None
    scope: str | None = None
    conditions: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicting_record_ids: list[str] = Field(default_factory=list)


class MechanismMemoryRecord(FlexiblePayloadModel):
    record_type: Literal["mechanism_memory"]
    mechanism_id: str | None = None
    mechanism: str | None = None
    scope: str | None = None
    conditions: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicting_record_ids: list[str] = Field(default_factory=list)


class CounterexampleRecord(FlexiblePayloadModel):
    record_type: Literal["counterexample"]
    counterexample_id: str | None = None
    statement: str | None = None
    mechanism: str | None = None
    contradicted_claim_ids: list[str] = Field(default_factory=list)
    supporting_record_ids: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    response_class: str | None = None


class EventTickerEdgeRecord(FlexiblePayloadModel):
    record_type: Literal["event_ticker_edge"]
    edge_id: str | None = None
    event_id: str | None = None
    ticker: str | None = None
    company_name: str | None = None
    relation_class: RelationClass | None = None
    relation_explanation: str | None = None
    directly_mentioned: bool | None = None
    path_type: str | None = None
    known_at: datetime | None = None
    source_kind: str | None = None


class CompanyMemoryDeltaRecord(FlexiblePayloadModel):
    record_type: Literal["company_memory_delta"]
    ticker: str | None = None
    company_name: str | None = None
    known_at: datetime | None = None
    aliases: list[str] | None = None
    business_descriptions: list[str] | None = None
    locations: list[str] | None = None
    customers: list[str] | None = None
    supply_chain_roles: list[str] | None = None
    prior_market_narratives: list[str] | None = None
    prior_leader_occurrences: list[str] | None = None
    contradictory_relations: list[str] | None = None


class ResearchQuestionRecord(FlexiblePayloadModel):
    record_type: Literal["research_question"]
    question_id: str | None = None
    question: str | None = None
    status: str | None = None
    priority: str | None = None
    answerable_after: datetime | None = None
    related_record_ids: list[str] = Field(default_factory=list)


KNOWN_RECORD_PAYLOAD_MODELS: dict[str, type[FlexiblePayloadModel]] = {
    "supervised_issuer_day_case": SupervisedIssuerDayCase,
    "supervised_direct_event_case": SupervisedDirectEventCase,
    "supervised_theme_formation_case": SupervisedThemeFormationCase,
    "theme_formation_case": ThemeFormationCase,
    "beneficiary_discovery_case": BeneficiaryDiscoveryCase,
    "blind_leader_preference_pair": BlindLeaderPreferencePair,
    "candidate_generation_error_case": CandidateGenerationErrorCase,
    "candidate_ranking_error_case": CandidateRankingErrorCase,
    "ranking_error_case": RankingErrorCase,
    "newsless_or_unexplained_case": NewslessOrUnexplainedCase,
    "negative_control_case": NegativeControlCase,
    "context_market_state_or_fact_case": ContextMarketStateOrFactCase,
    "row_disposition_error_case": RowDispositionErrorCase,
    "entity_resolution_error_case": EntityResolutionErrorCase,
    "memory_claim": MemoryClaimRecord,
    "mechanism_memory": MechanismMemoryRecord,
    "counterexample": CounterexampleRecord,
    "event_ticker_edge": EventTickerEdgeRecord,
    "company_memory_delta": CompanyMemoryDeltaRecord,
    "research_question": ResearchQuestionRecord,
}

TRAINING_RECORD_TYPES = {
    "supervised_issuer_day_case",
    "supervised_direct_event_case",
    "supervised_theme_formation_case",
    "theme_formation_case",
    "beneficiary_discovery_case",
    "blind_leader_preference_pair",
    "candidate_generation_error_case",
    "candidate_ranking_error_case",
    "ranking_error_case",
    "newsless_or_unexplained_case",
    "negative_control_case",
    "context_market_state_or_fact_case",
    "row_disposition_error_case",
    "entity_resolution_error_case",
}

CANDIDATE_ERROR_RECORD_TYPES = frozenset(
    {
        "candidate_generation_error_case",
        "candidate_ranking_error_case",
        "ranking_error_case",
        "row_disposition_error_case",
        "entity_resolution_error_case",
    }
)


class CompiledBrainClaim(StrictRecordModel):
    schema_version: str = "nslab.compiled_brain_claim.v1"
    claim_id: str
    category: str
    statement: str
    mechanism: str
    scope: str
    conditions: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    supporting_record_ids: list[str] = Field(default_factory=list)
    contradicting_record_ids: list[str] = Field(default_factory=list)
    supporting_episode_ids: list[str] = Field(default_factory=list)
    contradicting_episode_ids: list[str] = Field(default_factory=list)
    positive_case_count: int = 0
    negative_case_count: int = 0
    near_miss_count: int = 0
    confidence_label: str = "low"
    status: Literal["tentative", "supported", "validated", "disputed", "retired"] = "tentative"
    available_from: datetime
    provenance: dict[str, Any] = Field(default_factory=dict)
