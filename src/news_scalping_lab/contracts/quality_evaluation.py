"""Quality-first runtime evaluation contracts.

These contracts keep blind prediction inputs physically separate from outcome
references.  Efficiency is recorded for QUALITY_FULL runs, but it is never a
readiness or abort condition.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from news_scalping_lab.utils import canonical_json, sha256_text

QualityEvaluationProfileName = Literal["QUALITY_FULL", "LIVE_OPERATIONAL"]

QUALITY_FULL_RUNTIME_PROVIDER = "codex-oauth"
QUALITY_FULL_RUNTIME_MODEL = "gpt-5.6-sol"
QUALITY_FULL_RUNTIME_REASONING_EFFORT = "xhigh"


class QualityEvaluationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.quality_evaluation_profile.v1"] = "nslab.quality_evaluation_profile.v1"
    profile: QualityEvaluationProfileName
    provider: str
    model: str
    reasoning_effort: str
    wall_clock_limit_seconds: float | None = Field(default=None, gt=0.0)
    daily_p95_gate_seconds: float | None = Field(default=None, gt=0.0)
    latency_is_blocking: bool = False
    token_is_blocking: bool = False
    call_count_is_blocking: bool = False
    checkpoint_resume_required: bool = True
    live_latency_target_source: Literal["DISABLED", "USER_DEFINED_ONLY"] = "DISABLED"

    @model_validator(mode="after")
    def validate_profile_contract(self) -> Self:
        if not all(value.strip() for value in (self.provider, self.model, self.reasoning_effort)):
            raise ValueError("quality evaluation model identity cannot be blank")
        if self.profile == "QUALITY_FULL":
            if self.wall_clock_limit_seconds is not None:
                raise ValueError("QUALITY_FULL cannot have a wall-clock limit")
            if self.daily_p95_gate_seconds is not None:
                raise ValueError("QUALITY_FULL cannot have a daily latency gate")
            if any(
                (
                    self.latency_is_blocking,
                    self.token_is_blocking,
                    self.call_count_is_blocking,
                )
            ):
                raise ValueError("QUALITY_FULL efficiency observations cannot block")
            if self.live_latency_target_source != "DISABLED":
                raise ValueError("QUALITY_FULL has no operational latency target")
        elif self.live_latency_target_source != "USER_DEFINED_ONLY":
            raise ValueError("LIVE_OPERATIONAL targets must be user-defined")
        return self


def quality_full_profile(
    *,
    provider: str,
    model: str,
    reasoning_effort: str,
) -> QualityEvaluationProfile:
    return QualityEvaluationProfile(
        profile="QUALITY_FULL",
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        wall_clock_limit_seconds=None,
        daily_p95_gate_seconds=None,
        latency_is_blocking=False,
        token_is_blocking=False,
        call_count_is_blocking=False,
        checkpoint_resume_required=True,
        live_latency_target_source="DISABLED",
    )


def quality_full_runtime_profile(
    *,
    provider: str,
    model: str,
    reasoning_effort: str,
) -> QualityEvaluationProfile:
    """Build the one model identity allowed for a formal QUALITY_FULL run."""

    profile = quality_full_profile(
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    actual = (profile.provider, profile.model, profile.reasoning_effort)
    required = (
        QUALITY_FULL_RUNTIME_PROVIDER,
        QUALITY_FULL_RUNTIME_MODEL,
        QUALITY_FULL_RUNTIME_REASONING_EFFORT,
    )
    if actual != required:
        raise ValueError(
            "formal QUALITY_FULL runtime requires "
            f"{QUALITY_FULL_RUNTIME_PROVIDER}/{QUALITY_FULL_RUNTIME_MODEL}/"
            f"{QUALITY_FULL_RUNTIME_REASONING_EFFORT}"
        )
    return profile


class QualityArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        if not self.artifact_path.strip():
            raise ValueError("quality artifact path cannot be blank")
        return self


_FORBIDDEN_BLIND_KEY_TOKENS = frozenset({"outcome", "outcomes", "truth", "postmortem", "winner"})
_SAFE_ZERO_ACCOUNTING_KEYS = frozenset({"outcome_access_count", "outcome_reference_count"})


def reject_forbidden_blind_payload_keys(payload: object) -> None:
    """Reject recursively nested result-bearing fields before model coercion."""

    discovered: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                raw_text = str(raw_key).strip()
                camel_split = re.sub(
                    r"(?<=[a-z0-9])(?=[A-Z])",
                    "_",
                    raw_text,
                )
                key = camel_split.casefold()
                normalized = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
                tokens = set(normalized.split("_")) if normalized else set()
                compact = normalized.replace("_", "")
                return_tokens = {"return", "returns"}
                result_qualifiers = {
                    "actual",
                    "close",
                    "d0",
                    "d1",
                    "day",
                    "high",
                    "intraday",
                    "label",
                    "next",
                    "observed",
                    "pct",
                    "percent",
                    "percentage",
                    "rate",
                    "realized",
                    "session",
                    "target",
                }
                if (
                    tokens & _FORBIDDEN_BLIND_KEY_TOKENS
                    or any(token in compact for token in _FORBIDDEN_BLIND_KEY_TOKENS)
                ) and not (normalized in _SAFE_ZERO_ACCOUNTING_KEYS and child == 0):
                    discovered.add(str(raw_key))
                if (tokens & return_tokens and tokens & result_qualifiers) or (
                    any(token in compact for token in return_tokens)
                    and any(qualifier in compact for qualifier in result_qualifiers)
                ):
                    discovered.add(str(raw_key))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    if discovered:
        raise ValueError(
            "blind payload contains forbidden outcome fields or result-bearing fields: " + ", ".join(sorted(discovered))
        )


class SharedMapReduceNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    level: int = Field(ge=0)
    kind: Literal["MAP", "REDUCE"]
    child_node_ids: list[str] = Field(default_factory=list)
    covered_cluster_ids: list[str] = Field(default_factory=list)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output: QualityArtifactReference
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    checkpoint_hit: bool = False
    live_call_count: int = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_node(self) -> Self:
        if not self.node_id.strip() or not self.covered_cluster_ids:
            raise ValueError("shared map/reduce nodes require identity and coverage")
        if len(self.covered_cluster_ids) != len(set(self.covered_cluster_ids)):
            raise ValueError("shared map/reduce node cluster coverage must be unique")
        if self.kind == "MAP" and self.child_node_ids:
            raise ValueError("shared MAP nodes cannot have children")
        if self.kind == "REDUCE" and not self.child_node_ids:
            raise ValueError("shared REDUCE nodes require children")
        return self


class SharedOpenWorldReduceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["nslab.shared_open_world_reduce.v1"] = "nslab.shared_open_world_reduce.v1"
    node_id: str
    child_node_ids: list[str] = Field(default_factory=list)
    covered_cluster_ids: list[str] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def validate_reduce_coverage(self) -> Self:
        if not self.node_id.strip() or not self.child_node_ids:
            raise ValueError("shared open-world reduce output requires children")
        if not self.covered_cluster_ids:
            raise ValueError("shared open-world reduce output requires coverage")
        if len(self.covered_cluster_ids) != len(set(self.covered_cluster_ids)):
            raise ValueError("shared open-world reduce coverage must be unique")
        if not self.mechanisms and not self.uncertainties:
            raise ValueError("shared open-world reduce output requires mechanisms or uncertainty")
        return self


class SharedDownstreamDigest(BaseModel):
    """Bounded prompt surface backed by the complete shared-stage ledgers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.shared_downstream_digest.v1"] = "nslab.shared_downstream_digest.v1"
    context_id: str
    trade_date: date
    cutoff_at: datetime
    material_cluster_ids: list[str] = Field(default_factory=list)
    material_cluster_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    open_world_source: QualityArtifactReference
    novelty_source: QualityArtifactReference
    open_world_root: dict[str, Any]
    novelty_projection_fields: list[str] = Field(default_factory=list)
    novelty_omitted_fields: list[str] = Field(default_factory=list)
    novelty_findings: list[dict[str, Any]] = Field(default_factory=list)
    prompt_surface_policy: Literal["COMPLETE_CLUSTER_IDENTITY_WITH_ROOT_SYNTHESIS_AND_HASHED_VERBOSE_FIELDS.v1"] = (
        "COMPLETE_CLUSTER_IDENTITY_WITH_ROOT_SYNTHESIS_AND_HASHED_VERBOSE_FIELDS.v1"
    )
    first_n_shortcut_used: Literal[False] = False
    silent_truncation_used: Literal[False] = False

    @model_validator(mode="after")
    def validate_complete_prompt_surface(self) -> Self:
        if not self.context_id.strip() or not self.material_cluster_ids:
            raise ValueError("shared downstream digest requires identity and coverage")
        finding_ids = [str(finding.get("cluster_id", "")) for finding in self.novelty_findings]
        if finding_ids != self.material_cluster_ids:
            raise ValueError("shared downstream digest must preserve material cluster order")
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("shared downstream digest cluster IDs must be unique")
        root_ids = self.open_world_root.get("covered_cluster_ids")
        if root_ids != self.material_cluster_ids:
            raise ValueError("shared downstream root must cover every material cluster")
        for finding in self.novelty_findings:
            omitted_sha256 = finding.get("omitted_payload_sha256")
            if not isinstance(omitted_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", omitted_sha256):
                raise ValueError("shared downstream finding requires an omitted-payload hash")
        return self


class SharedDMinusOneSnapshot(BaseModel):
    """One strictly typed, cutoff-safe market row sealed before prediction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticker: str
    trade_date: date
    open: float | None = Field(default=None, allow_inf_nan=False)
    high: float | None = Field(default=None, allow_inf_nan=False)
    low: float | None = Field(default=None, allow_inf_nan=False)
    close: float | None = Field(default=None, allow_inf_nan=False)
    volume: float | None = Field(default=None, allow_inf_nan=False)
    amount: float | None = Field(default=None, allow_inf_nan=False)
    market_cap: float | None = Field(default=None, allow_inf_nan=False)
    listed_shares: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if not self.ticker.strip() or self.ticker != self.ticker.strip().upper():
            raise ValueError("shared D-1 snapshot ticker must be normalized")
        for field_name in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "market_cap",
            "listed_shares",
        ):
            value = getattr(self, field_name)
            if value is not None and not math.isfinite(value):
                raise ValueError("shared D-1 snapshot values must be finite")
        return self


class SharedDMinusOneContext(BaseModel):
    """Candidate-independent D-1 market payload shared by every eval arm."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.shared_d_minus_one_context.v4"] = "nslab.shared_d_minus_one_context.v4"
    status: Literal[
        "D_MINUS_ONE_FIXED_UNIVERSE",
        "D_MINUS_ONE_FIXED_UNIVERSE_EMPTY",
    ]
    trade_date: date
    cutoff_at: datetime
    allowed_through: date
    source_name: str
    source_ref: str | None = None
    candidate_independent_context: Literal[True] = True
    fixed_universe_contract: Literal["SEALED_CUTOFF_SAFE_SNAPSHOT_UNIVERSE.v3"] = (
        "SEALED_CUTOFF_SAFE_SNAPSHOT_UNIVERSE.v3"
    )
    universe_membership_policy: Literal["LATEST_AVAILABLE_MARKET_SESSION_ON_OR_BEFORE_D_MINUS_ONE.v1"] = (
        "LATEST_AVAILABLE_MARKET_SESSION_ON_OR_BEFORE_D_MINUS_ONE.v1"
    )
    snapshot_session_date: date | None = None
    source_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_universe: list[str] = Field(default_factory=list)
    candidate_universe_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshots: list[SharedDMinusOneSnapshot] = Field(default_factory=list)
    snapshot_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skipped_tickers: list[dict[str, str]] = Field(default_factory=list, max_length=0)
    sealed_snapshot_count: int = Field(ge=0)
    privileged_source_snapshot_count: int = Field(ge=0)
    privileged_source_query_count: Literal[1] = 1
    price_repository_access_count: Literal[0] = 0
    d_day_access_count: Literal[0] = 0
    outcome_access_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_fixed_universe_closure(self) -> Self:
        if self.cutoff_at.utcoffset() is None or self.cutoff_at.date() != self.trade_date:
            raise ValueError("shared D-1 cutoff is invalid")
        if self.allowed_through != self.trade_date - timedelta(days=1):
            raise ValueError("shared D-1 allowed-through date is not exact D-1")
        if not self.source_name.strip():
            raise ValueError("shared D-1 source name cannot be blank")
        universe = self.candidate_universe
        if universe != sorted(set(universe)) or any(not value.strip() for value in universe):
            raise ValueError("shared D-1 candidate universe must be sorted and unique")
        if self.candidate_universe_root_sha256 != sha256_text(canonical_json(universe)):
            raise ValueError("shared D-1 candidate universe root is invalid")
        snapshot_tickers: list[str] = []
        for row in self.snapshots:
            if row.trade_date > self.allowed_through:
                raise ValueError("shared D-1 snapshot exceeds allowed-through date")
            if row.trade_date != self.snapshot_session_date:
                raise ValueError("shared D-1 snapshot is stale for the sealed market session")
            snapshot_tickers.append(row.ticker)
        if snapshot_tickers != sorted(set(snapshot_tickers)):
            raise ValueError("shared D-1 snapshots must be sorted and unique")
        snapshot_payload = [row.model_dump(mode="json") for row in self.snapshots]
        if self.snapshot_root_sha256 != sha256_text(canonical_json(snapshot_payload)):
            raise ValueError("shared D-1 snapshot root is invalid")
        if self.skipped_tickers:
            raise ValueError("shared D-1 context cannot expose unavailable ticker identities")
        if universe != snapshot_tickers:
            raise ValueError("shared D-1 universe must equal its cutoff-safe snapshots")
        if self.sealed_snapshot_count != len(universe):
            raise ValueError("shared D-1 sealed snapshot accounting is incomplete")
        if self.privileged_source_snapshot_count < self.sealed_snapshot_count:
            raise ValueError("shared D-1 privileged source accounting is incomplete")
        if bool(universe) != bool(self.snapshot_session_date):
            raise ValueError("shared D-1 market session identity is incomplete")
        expected_status = "D_MINUS_ONE_FIXED_UNIVERSE" if universe else "D_MINUS_ONE_FIXED_UNIVERSE_EMPTY"
        if self.status != expected_status:
            raise ValueError("shared D-1 status differs from its fixed universe")
        return self


class DMinusOneProjectionRequest(BaseModel):
    """Auditable reason that one ticker entered the bounded D-1 prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticker: str
    source_kind: Literal["PRELIMINARY_PREDICTION_CANDIDATE"] = "PRELIMINARY_PREDICTION_CANDIDATE"
    candidate_ranks: list[int] = Field(min_length=1)
    event_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if not self.ticker.strip() or self.ticker != self.ticker.strip().upper():
            raise ValueError("D-1 projection request ticker must be normalized")
        if self.candidate_ranks != sorted(set(self.candidate_ranks)) or any(rank < 1 for rank in self.candidate_ranks):
            raise ValueError("D-1 projection candidate ranks must be sorted and unique")
        if self.event_ids != sorted(set(self.event_ids)):
            raise ValueError("D-1 projection event IDs must be sorted and unique")
        return self


class DMinusOnePromptProjection(BaseModel):
    """Bounded exact subset of the full sealed D-1 universe used in prompts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.d_minus_one_prompt_projection.v1"] = "nslab.d_minus_one_prompt_projection.v1"
    projection_policy: Literal["ALL_PRELIMINARY_CANDIDATE_TICKERS_EXACT_SEALED_SUBSET.v1"] = (
        "ALL_PRELIMINARY_CANDIDATE_TICKERS_EXACT_SEALED_SUBSET.v1"
    )
    trade_date: date
    cutoff_at: datetime
    allowed_through: date
    source_name: str
    source_ref: str | None = None
    full_context: QualityArtifactReference
    full_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_universe_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    full_snapshot_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_session_date: date | None = None
    full_snapshot_count: int = Field(ge=0)
    request_sources: list[DMinusOneProjectionRequest] = Field(default_factory=list)
    requested_tickers: list[str] = Field(default_factory=list)
    requested_ticker_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshots: list[SharedDMinusOneSnapshot] = Field(default_factory=list)
    missing_tickers: list[str] = Field(default_factory=list)
    projection_snapshot_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if self.cutoff_at.utcoffset() is None or self.cutoff_at.date() != self.trade_date:
            raise ValueError("D-1 prompt projection cutoff is invalid")
        if self.allowed_through != self.trade_date - timedelta(days=1):
            raise ValueError("D-1 prompt projection allowed-through is invalid")
        if not self.source_name.strip():
            raise ValueError("D-1 prompt projection source cannot be blank")
        source_tickers = [request.ticker for request in self.request_sources]
        if source_tickers != sorted(set(source_tickers)):
            raise ValueError("D-1 projection request sources must be sorted and unique")
        if self.requested_tickers != source_tickers:
            raise ValueError("D-1 requested tickers differ from candidate derivation")
        if self.requested_ticker_root_sha256 != sha256_text(canonical_json(self.requested_tickers)):
            raise ValueError("D-1 requested ticker root is invalid")
        snapshot_tickers = [row.ticker for row in self.snapshots]
        if snapshot_tickers != sorted(set(snapshot_tickers)):
            raise ValueError("D-1 projection snapshots must be sorted and unique")
        if any(
            row.trade_date != self.snapshot_session_date or row.trade_date > self.allowed_through
            for row in self.snapshots
        ):
            raise ValueError("D-1 projection snapshot session is invalid")
        if self.missing_tickers != sorted(set(self.missing_tickers)):
            raise ValueError("D-1 projection missing tickers must be sorted and unique")
        if set(snapshot_tickers).intersection(self.missing_tickers):
            raise ValueError("D-1 projection ticker cannot be both present and missing")
        if sorted([*snapshot_tickers, *self.missing_tickers]) != self.requested_tickers:
            raise ValueError("D-1 projection does not dispose every requested ticker")
        if self.full_snapshot_count < len(self.snapshots):
            raise ValueError("D-1 projection exceeds the full sealed snapshot count")
        snapshot_payload = [row.model_dump(mode="json") for row in self.snapshots]
        if self.projection_snapshot_root_sha256 != sha256_text(canonical_json(snapshot_payload)):
            raise ValueError("D-1 projection snapshot root is invalid")
        identity_payload = self.model_dump(
            mode="json",
            exclude={"projection_root_sha256"},
        )
        if self.projection_root_sha256 != sha256_text(canonical_json(identity_payload)):
            raise ValueError("D-1 prompt projection root is invalid")
        return self


class SharedPreRetrievalContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.shared_pre_retrieval_context.v3"] = "nslab.shared_pre_retrieval_context.v3"
    context_id: str
    profile: Literal["QUALITY_FULL"] = "QUALITY_FULL"
    trade_date: date
    cutoff_at: datetime
    news_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str
    model: str
    reasoning_effort: str
    code_semantic_version: str
    parsed_news_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_cluster_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_sha256_root: str = Field(pattern=r"^[0-9a-f]{64}$")
    component_artifact_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    downstream_digest_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_row_ids: list[str] = Field(default_factory=list)
    event_cluster_ids: list[str] = Field(default_factory=list)
    material_cluster_ids: list[str] = Field(default_factory=list)
    low_signal_cluster_ids: list[str] = Field(default_factory=list)
    event_clustering_result: QualityArtifactReference
    row_disposition_ledger: QualityArtifactReference
    event_cluster_ledger: QualityArtifactReference
    news_coverage_manifest: QualityArtifactReference
    event_cluster_manifest: QualityArtifactReference
    open_world_first_analysis: QualityArtifactReference
    news_novelty_review: QualityArtifactReference
    downstream_digest: QualityArtifactReference
    d_minus_one_safe_context: QualityArtifactReference
    map_reduce_nodes: list[SharedMapReduceNode] = Field(default_factory=list)
    root_node_id: str
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    logical_llm_call_count: int = Field(ge=0)
    novelty_logical_llm_call_count: int = Field(ge=0)
    provider_checkpoint_commitment_count: int = Field(ge=0)
    committed_prompt_tokens_estimate: int = Field(ge=0)
    committed_completion_tokens_estimate: int = Field(ge=0)
    first_n_shortcut_used: Literal[False] = False
    silent_truncation_used: Literal[False] = False
    outcome_reference_count: Literal[0] = 0
    blind_web_call_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_complete_coverage(self) -> Self:
        if not self.context_id.strip() or not self.code_semantic_version.strip():
            raise ValueError("shared context identity cannot be blank")
        if not all(value.strip() for value in (self.provider, self.model, self.reasoning_effort)):
            raise ValueError("shared context model identity cannot be blank")
        if len(self.source_row_ids) != len(set(self.source_row_ids)):
            raise ValueError("shared context source row IDs must be unique")
        if len(self.event_cluster_ids) != len(set(self.event_cluster_ids)):
            raise ValueError("shared context cluster IDs must be unique")
        if set(self.material_cluster_ids) & set(self.low_signal_cluster_ids):
            raise ValueError("material and low-signal clusters must be disjoint")
        if set(self.event_cluster_ids) != (set(self.material_cluster_ids) | set(self.low_signal_cluster_ids)):
            raise ValueError("shared context must disposition every event cluster")
        nodes = {node.node_id: node for node in self.map_reduce_nodes}
        root = nodes.get(self.root_node_id)
        if root is None:
            raise ValueError("shared context map/reduce root is missing")
        if set(root.covered_cluster_ids) != set(self.material_cluster_ids):
            raise ValueError("shared root must cover every material cluster")
        for node in self.map_reduce_nodes:
            if any(child not in nodes for child in node.child_node_ids):
                raise ValueError("shared reduce node references an unknown child")
        if self.logical_llm_call_count != (len(self.map_reduce_nodes) + self.novelty_logical_llm_call_count):
            raise ValueError("shared logical call count must include map/reduce plus novelty")
        if self.provider_checkpoint_commitment_count != self.logical_llm_call_count:
            raise ValueError("shared provider checkpoint commitments must cover every logical call")
        return self


class SharedPreRetrievalContextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.shared_pre_retrieval_context_manifest.v2"] = (
        "nslab.shared_pre_retrieval_context_manifest.v2"
    )
    context_id: str
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lookup_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_semantic_version: str
    parsed_news_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_cluster_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_sha256_root: str = Field(pattern=r"^[0-9a-f]{64}$")
    component_artifact_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    downstream_digest_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context: QualityArtifactReference
    news_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trade_date: date
    cutoff_at: datetime
    source_row_count: int = Field(ge=1)
    event_cluster_count: int = Field(ge=1)
    material_cluster_count: int = Field(ge=1)
    low_signal_cluster_count: int = Field(ge=0)
    source_row_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_cluster_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_cluster_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete_row_coverage: Literal[True] = True
    complete_material_tree_coverage: Literal[True] = True
    production_activation_status: Literal["NOT_PRODUCTION_ACTIVATED"] = "NOT_PRODUCTION_ACTIVATED"

    @model_validator(mode="after")
    def validate_content_identity(self) -> Self:
        if not self.context_id.strip() or not self.code_semantic_version.strip():
            raise ValueError("shared context manifest identity cannot be blank")
        return self


class SealedBlindCaseInputManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.sealed_blind_case_input.v2"] = "nslab.sealed_blind_case_input.v2"
    input_id: str
    episode_id: str
    trade_date: date
    cutoff_at: datetime
    news_csv: QualityArtifactReference
    d_minus_one_context: QualityArtifactReference
    d_minus_one_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_candidate_universe_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_snapshot_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_source_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_snapshot_session_date: date | None = None
    d_minus_one_canonicalization_version: Literal["quality_sealed_d_minus_one.v1"] = "quality_sealed_d_minus_one.v1"
    cutoff_safe_news_row_count: int = Field(ge=1)
    source_row_ids: list[str] = Field(min_length=1)
    source_row_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ledger_commitment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_metadata_commitment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonicalization_version: str
    cutoff_derivation: Literal[
        "NORMALIZED_INDEX",
        "TRADE_DATE_08_59_59_KST",
    ]
    outcome_reference_count: Literal[0] = 0
    forbidden_reference_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_sealed_input(self) -> Self:
        if not all(
            value.strip()
            for value in (
                self.input_id,
                self.episode_id,
                self.canonicalization_version,
            )
        ):
            raise ValueError("sealed blind input identity cannot be blank")
        if self.cutoff_at.utcoffset() is None:
            raise ValueError("sealed blind input cutoff must be timezone-aware")
        if self.cutoff_at.date() != self.trade_date:
            raise ValueError("sealed blind input cutoff and trade date differ")
        if len(self.source_row_ids) != len(set(self.source_row_ids)):
            raise ValueError("sealed blind input source row IDs must be unique")
        if len(self.source_row_ids) != self.cutoff_safe_news_row_count:
            raise ValueError("sealed blind input source row coverage is incomplete")
        if self.source_row_root_sha256 != sha256_text(canonical_json(self.source_row_ids)):
            raise ValueError("sealed blind input source row root is invalid")
        return self


class BlindRuntimeCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str
    trade_date: date
    split: Literal["CALIBRATION", "HOLDOUT", "POST_CUTOFF"]
    cutoff_at: datetime
    blind_input_manifest: QualityArtifactReference
    news_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_safe_news_row_count: int = Field(ge=1)
    d_minus_one_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_candidate_universe_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_snapshot_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_source_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_snapshot_session_date: date | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if not self.episode_id.strip():
            raise ValueError("blind runtime case ID cannot be blank")
        if self.cutoff_at.utcoffset() is None or self.cutoff_at.date() != self.trade_date:
            raise ValueError("blind runtime case cutoff is invalid")
        return self


class BlindRuntimeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.blind_runtime_selection.v3"] = "nslab.blind_runtime_selection.v3"
    selection_id: str
    source_selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_policy: str
    cases: list[BlindRuntimeCase] = Field(min_length=1)
    outcome_reference_count: Literal[0] = 0
    production_activation_status: Literal["NOT_PRODUCTION_ACTIVATED"] = "NOT_PRODUCTION_ACTIVATED"

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if not self.selection_id.strip() or not self.selection_policy.strip():
            raise ValueError("blind runtime selection identity cannot be blank")
        case_ids = [case.episode_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("blind runtime selection cases must be unique")
        return self


class RuntimeOutcomeCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str
    trade_date: date
    split: Literal["CALIBRATION", "HOLDOUT", "POST_CUTOFF"]
    outcome_ledger: QualityArtifactReference


class RuntimeOutcomeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.runtime_outcome_selection.v1"] = "nslab.runtime_outcome_selection.v1"
    selection_id: str
    blind_selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: list[RuntimeOutcomeCase] = Field(min_length=1)
    available_to_prediction_process: Literal[False] = False


class PredictionSeal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.quality_prediction_seal.v5"] = "nslab.quality_prediction_seal.v5"
    case_id: str
    variant_id: Literal["V0", "V1", "V2"]
    variant_architecture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sealed_at: datetime
    cutoff_at: datetime
    prediction_input_boundary_version: Literal["SEALED_BLIND_INPUT.v3"] = "SEALED_BLIND_INPUT.v3"
    blind_input_manifest: QualityArtifactReference
    news_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parsed_news_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shared_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    brain_manifest: QualityArtifactReference
    coverage_manifest: QualityArtifactReference
    memory_snapshot_id: str
    d_minus_one_context: QualityArtifactReference
    d_minus_one_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_candidate_universe_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_snapshot_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_source_revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_snapshot_session_date: date | None = None
    d_minus_one_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_consumed_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_projection_policy: Literal["ALL_PRELIMINARY_CANDIDATE_TICKERS_EXACT_SEALED_SUBSET.v1"]
    d_minus_one_projection_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    d_minus_one_projection_requested_ticker_count: int = Field(ge=0)
    d_minus_one_projection_snapshot_count: int = Field(ge=0)
    d_minus_one_projection_missing_ticker_count: int = Field(ge=0)
    candidate_universe_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prediction: QualityArtifactReference
    context_manifest: QualityArtifactReference
    final_citation_count: int = Field(ge=0)
    future_record_count: Literal[0] = 0
    blind_web_call_count: Literal[0] = 0
    online_full_scan_count: Literal[0] = 0
    outcome_reference_count: Literal[0] = 0
    efficiency: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_d_minus_one_binding(self) -> Self:
        if self.d_minus_one_context.sha256 != self.d_minus_one_context_sha256:
            raise ValueError("prediction seal D-1 artifact hash differs from its binding")
        if (
            self.d_minus_one_projection_snapshot_count + self.d_minus_one_projection_missing_ticker_count
            != self.d_minus_one_projection_requested_ticker_count
        ):
            raise ValueError("prediction seal D-1 projection disposition is incomplete")
        return self


def _default_expected_variant_ids() -> list[Literal["V0", "V1", "V2"]]:
    return ["V0", "V1"]


class PairedPredictionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["nslab.paired_prediction_manifest.v5"] = "nslab.paired_prediction_manifest.v5"
    run_id: str
    prediction_code_version: Literal["nslab.quality_runtime_prediction_code.v2"] = (
        "nslab.quality_runtime_prediction_code.v2"
    )
    profile: QualityEvaluationProfile
    blind_selection: QualityArtifactReference
    expected_case_ids: list[str] = Field(min_length=1)
    expected_variant_ids: list[Literal["V0", "V1", "V2"]] = Field(
        default_factory=_default_expected_variant_ids,
        min_length=1,
    )
    expected_variant_architecture_sha256: dict[str, str]
    shared_preparation_ledgers: dict[str, QualityArtifactReference] = Field(default_factory=dict)
    seals: list[PredictionSeal] = Field(default_factory=list)
    paired_case_ids: list[str] = Field(default_factory=list)
    all_predictions_sealed: bool = False
    outcome_opened: Literal[False] = False
    production_activation_status: Literal["NOT_PRODUCTION_ACTIVATED"] = "NOT_PRODUCTION_ACTIVATED"

    @model_validator(mode="after")
    def validate_seal_closure(self) -> Self:
        expected = set(self.expected_case_ids)
        if len(expected) != len(self.expected_case_ids):
            raise ValueError("paired prediction expected case IDs must be unique")
        expected_variants = set(self.expected_variant_ids)
        if len(expected_variants) != len(self.expected_variant_ids) or self.expected_variant_ids != sorted(
            self.expected_variant_ids
        ):
            raise ValueError("paired prediction expected variant IDs must be sorted and unique")
        if set(self.expected_variant_architecture_sha256) != expected_variants or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None for value in self.expected_variant_architecture_sha256.values()
        ):
            raise ValueError("paired prediction expected variant architectures are invalid")
        if self.shared_preparation_ledgers and (set(self.shared_preparation_ledgers) != expected):
            raise ValueError("paired prediction shared preparation ledgers differ from cases")
        observed: dict[str, set[str]] = {}
        seals_by_case: dict[str, list[PredictionSeal]] = {}
        for seal in self.seals:
            if seal.case_id not in expected:
                raise ValueError("paired prediction seal has an unexpected case")
            if seal.variant_id not in expected_variants:
                raise ValueError("paired prediction seal has an unexpected variant")
            if seal.variant_architecture_sha256 != self.expected_variant_architecture_sha256[seal.variant_id]:
                raise ValueError("paired prediction variant architecture drifted")
            if seal.variant_id in observed.setdefault(seal.case_id, set()):
                raise ValueError("paired prediction seal is duplicated")
            observed[seal.case_id].add(seal.variant_id)
            seals_by_case.setdefault(seal.case_id, []).append(seal)
        parity_fields = (
            "cutoff_at",
            "prediction_input_boundary_version",
            "blind_input_manifest",
            "news_sha256",
            "parsed_news_root_sha256",
            "shared_context_sha256",
            "memory_snapshot_id",
            "d_minus_one_context",
            "d_minus_one_context_sha256",
            "d_minus_one_candidate_universe_root_sha256",
            "d_minus_one_snapshot_root_sha256",
            "d_minus_one_source_revision_sha256",
            "d_minus_one_snapshot_session_date",
            "d_minus_one_payload_sha256",
            "d_minus_one_projection_policy",
        )
        for case_id, case_seals in seals_by_case.items():
            for field in parity_fields:
                if len({str(getattr(seal, field)) for seal in case_seals}) != 1:
                    raise ValueError(f"paired prediction {field} differs for case {case_id}")
            v7_case_seals = [seal for seal in case_seals if seal.variant_id in {"V0", "V1"}]
            if len({seal.candidate_universe_policy_sha256 for seal in v7_case_seals}) > 1:
                raise ValueError(f"paired prediction V0/V1 candidate universe policy differs for case {case_id}")
        v7_seals = [seal for seal in self.seals if seal.variant_id in {"V0", "V1"}]
        for field in (
            "brain_manifest",
            "coverage_manifest",
        ):
            if len({str(getattr(seal, field)) for seal in v7_seals}) > 1:
                raise ValueError(f"paired prediction V0/V1 {field} differs")
        paired = sorted(case_id for case_id, variants in observed.items() if variants == expected_variants)
        if self.paired_case_ids != paired:
            raise ValueError("paired prediction case closure does not match seals")
        if self.all_predictions_sealed != (set(paired) == expected):
            raise ValueError("paired prediction all-sealed flag is inconsistent")
        return self
