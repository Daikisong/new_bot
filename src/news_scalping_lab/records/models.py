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
    event_ids: list[str] = Field(default_factory=list)
    sample_weight: float | None = None


class SupervisedDirectEventCase(FlexiblePayloadModel):
    record_type: Literal["supervised_direct_event_case"]
    case_id: str | None = None
    issuer_day_case_id: str | None = None
    ticker: str | None = None
    event_id: str | None = None
    sample_weight: float | None = None


class SupervisedThemeFormationCase(FlexiblePayloadModel):
    record_type: Literal["supervised_theme_formation_case"]
    theme_id: str | None = None


class BeneficiaryDiscoveryCase(FlexiblePayloadModel):
    record_type: Literal["beneficiary_discovery_case"]
    case_id: str | None = None


class BlindLeaderPreferencePair(FlexiblePayloadModel):
    record_type: Literal["blind_leader_preference_pair"]
    blind_pair_id: str | None = None
    blind_preferred_candidate_id: str | None = None
    blind_rejected_candidate_id: str | None = None
    outcome_preferred_candidate_id: str | None = None


class CandidateGenerationErrorCase(FlexiblePayloadModel):
    record_type: Literal["candidate_generation_error_case"]
    error_id: str | None = None


class CandidateRankingErrorCase(FlexiblePayloadModel):
    record_type: Literal["candidate_ranking_error_case"]
    error_id: str | None = None


class RowDispositionErrorCase(FlexiblePayloadModel):
    record_type: Literal["row_disposition_error_case"]
    error_id: str | None = None


class EntityResolutionErrorCase(FlexiblePayloadModel):
    record_type: Literal["entity_resolution_error_case"]
    error_id: str | None = None


class MemoryClaimRecord(FlexiblePayloadModel):
    record_type: Literal["memory_claim"]
    claim_id: str | None = None


class MechanismMemoryRecord(FlexiblePayloadModel):
    record_type: Literal["mechanism_memory"]
    mechanism_id: str | None = None


class CounterexampleRecord(FlexiblePayloadModel):
    record_type: Literal["counterexample"]
    counterexample_id: str | None = None


class EventTickerEdgeRecord(FlexiblePayloadModel):
    record_type: Literal["event_ticker_edge"]
    edge_id: str | None = None
    event_id: str | None = None
    ticker: str | None = None


class CompanyMemoryDeltaRecord(FlexiblePayloadModel):
    record_type: Literal["company_memory_delta"]
    ticker: str | None = None
    company_name: str | None = None


class ResearchQuestionRecord(FlexiblePayloadModel):
    record_type: Literal["research_question"]
    question_id: str | None = None


KNOWN_RECORD_PAYLOAD_MODELS: dict[str, type[FlexiblePayloadModel]] = {
    "supervised_issuer_day_case": SupervisedIssuerDayCase,
    "supervised_direct_event_case": SupervisedDirectEventCase,
    "supervised_theme_formation_case": SupervisedThemeFormationCase,
    "beneficiary_discovery_case": BeneficiaryDiscoveryCase,
    "blind_leader_preference_pair": BlindLeaderPreferencePair,
    "candidate_generation_error_case": CandidateGenerationErrorCase,
    "candidate_ranking_error_case": CandidateRankingErrorCase,
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
    "beneficiary_discovery_case",
    "blind_leader_preference_pair",
    "candidate_generation_error_case",
    "candidate_ranking_error_case",
    "row_disposition_error_case",
    "entity_resolution_error_case",
}

CANDIDATE_ERROR_RECORD_TYPES = frozenset(
    {
        "candidate_generation_error_case",
        "candidate_ranking_error_case",
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
