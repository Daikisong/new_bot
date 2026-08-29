"""Strict contracts for retrieval-first runtime evidence assembly."""

from __future__ import annotations

import math
from datetime import date
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    Sha256,
    StrictMemoryContextModel,
)
from news_scalping_lab.utils import sha256_text

RuntimeRetrievalLane = Literal[
    "POSITIVE_ANALOG",
    "NEGATIVE_CONTROL",
    "NEAR_MISS",
    "COUNTEREXAMPLE",
    "NEWSLESS_OR_UNEXPLAINED",
    "CANDIDATE_GENERATION_ERROR",
    "CANDIDATE_RANKING_ERROR",
    "LEADER_SELECTION_PAIR",
    "THEME_FORMATION_SUCCESS",
    "THEME_FORMATION_FAILURE",
    "CONTINUATION_SUCCESS",
    "CONTINUATION_FAILURE",
    "RARE_MECHANISM",
]
RuntimeRetrievalStage = Literal[
    "ANN_CANDIDATE",
    "FTS_CANDIDATE",
    "CELL_MEMBER",
    "METADATA_FILTERED",
    "RERANKED",
    "LANE_SELECTED",
    "LLM_EXPOSED",
    "MEMO_REFERENCED",
    "FINAL_CITED",
    "DROPPED",
]


class RuntimeRetrievalBudget(StrictMemoryContextModel):
    schema_version: Literal["nslab.runtime_retrieval_budget.v1"] = "nslab.runtime_retrieval_budget.v1"
    initial_record_count: int = Field(ge=16, le=32)
    max_record_count: int = Field(ge=16, le=128)
    max_depth: int = Field(ge=1, le=3)
    batch_size: int = Field(ge=16, le=32)
    entropy: float = Field(ge=0.0, le=1.0)
    trigger_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        if self.max_record_count < self.initial_record_count:
            raise ValueError("runtime retrieval max budget cannot be below initial budget")
        if len(self.trigger_reasons) != len(set(self.trigger_reasons)):
            raise ValueError("runtime retrieval trigger reasons must be unique")
        if any(not item.strip() for item in self.trigger_reasons):
            raise ValueError("runtime retrieval trigger reasons cannot be blank")
        if not math.isfinite(self.entropy):
            raise ValueError("runtime retrieval entropy must be finite")
        return self


class RuntimeEvidenceMemo(StrictMemoryContextModel):
    schema_version: Literal["nslab.runtime_evidence_memo.v1"] = "nslab.runtime_evidence_memo.v1"
    memo_id: str
    cluster_id: str
    lane: RuntimeRetrievalLane
    source_record_ids: list[str] = Field(default_factory=list, min_length=1)
    source_record_hash_root: Sha256
    current_vs_history_similarities: list[str] = Field(default_factory=list)
    current_vs_history_differences: list[str] = Field(default_factory=list)
    supporting_conditions: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_memo(self) -> Self:
        if not self.memo_id.strip() or not self.cluster_id.strip():
            raise ValueError("runtime evidence memo identity cannot be blank")
        if len(self.source_record_ids) != len(set(self.source_record_ids)):
            raise ValueError("runtime evidence memo record IDs must be unique")
        for values in (
            self.current_vs_history_similarities,
            self.current_vs_history_differences,
            self.supporting_conditions,
            self.failure_conditions,
            self.unresolved_conflicts,
        ):
            if len(values) != len(set(values)) or any(not item.strip() for item in values):
                raise ValueError("runtime evidence memo text lists must be unique and non-empty")
        return self


class RuntimeEvidenceMemoBatch(StrictMemoryContextModel):
    schema_version: Literal["nslab.runtime_evidence_memo_batch.v1"] = "nslab.runtime_evidence_memo_batch.v1"
    cluster_id: str
    source_record_ids: list[str] = Field(default_factory=list, min_length=1)
    memos: list[RuntimeEvidenceMemo] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if not self.cluster_id.strip():
            raise ValueError("runtime evidence memo batch cluster cannot be blank")
        if len(self.source_record_ids) != len(set(self.source_record_ids)):
            raise ValueError("runtime evidence memo batch records must be unique")
        covered = {record_id for memo in self.memos for record_id in memo.source_record_ids}
        if covered != set(self.source_record_ids):
            raise ValueError("every selected record must enter exactly the memo batch coverage")
        if any(memo.cluster_id != self.cluster_id for memo in self.memos):
            raise ValueError("runtime evidence memo batch cluster identity mismatch")
        return self


class RuntimeEvidenceMemoPack(StrictMemoryContextModel):
    """Structured response for one prompt-packed, multi-cluster evidence call."""

    schema_version: Literal["nslab.runtime_evidence_memo_pack.v1"] = (
        "nslab.runtime_evidence_memo_pack.v1"
    )
    cluster_ids: list[str] = Field(default_factory=list, min_length=1)
    source_record_ids: list[str] = Field(default_factory=list, min_length=1)
    batches: list[RuntimeEvidenceMemoBatch] = Field(
        default_factory=list,
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_pack(self) -> Self:
        if (
            self.cluster_ids != sorted(set(self.cluster_ids))
            or self.source_record_ids != sorted(set(self.source_record_ids))
        ):
            raise ValueError("runtime evidence pack identities must be sorted and unique")
        batch_clusters = [batch.cluster_id for batch in self.batches]
        if batch_clusters != sorted(set(batch_clusters)):
            raise ValueError("runtime evidence pack batches must be sorted by unique cluster")
        if batch_clusters != self.cluster_ids:
            raise ValueError("runtime evidence pack batch cluster coverage is incomplete")
        covered = {
            record_id
            for batch in self.batches
            for record_id in batch.source_record_ids
        }
        if covered != set(self.source_record_ids):
            raise ValueError("runtime evidence pack record coverage is incomplete")
        return self


class RuntimeEvidencePackPlanNode(StrictMemoryContextModel):
    pack_id: str
    purpose: str
    prompt_sha256: Sha256
    prompt_chars: int = Field(ge=1)
    cluster_ids: list[str] = Field(default_factory=list, min_length=1)
    source_record_ids: list[str] = Field(default_factory=list, min_length=1)
    assignment_count: int = Field(ge=1)
    assignment_root_sha256: Sha256

    @model_validator(mode="after")
    def validate_node(self) -> Self:
        if not self.pack_id.strip() or not self.purpose.strip():
            raise ValueError("runtime evidence pack plan identity cannot be blank")
        if (
            self.cluster_ids != sorted(set(self.cluster_ids))
            or self.source_record_ids != sorted(set(self.source_record_ids))
        ):
            raise ValueError("runtime evidence pack plan identities must be sorted and unique")
        if self.assignment_count < len(self.source_record_ids):
            raise ValueError("runtime evidence pack plan assignments trail record coverage")
        return self


class RuntimeEvidencePackPlan(StrictMemoryContextModel):
    """Immutable call topology written before the first packed LLM request."""

    schema_version: Literal["nslab.runtime_evidence_pack_plan.v1"] = (
        "nslab.runtime_evidence_pack_plan.v1"
    )
    run_id: str
    cutoff_at: AwareDatetime
    memory_snapshot_id: str
    policy_version: Literal["runtime_evidence_cross_cluster_pack.v1"] = (
        "runtime_evidence_cross_cluster_pack.v1"
    )
    max_prompt_chars: int = Field(ge=1)
    assignment_count: int = Field(ge=1)
    unique_record_count: int = Field(ge=1)
    unpacked_payload_occurrence_count: int = Field(ge=1)
    planned_payload_occurrence_count: int = Field(ge=1)
    avoided_payload_occurrence_count: int = Field(ge=0)
    assignment_root_sha256: Sha256
    source_record_root_sha256: Sha256
    packs: list[RuntimeEvidencePackPlanNode] = Field(default_factory=list, min_length=1)
    first_n_shortcut_used: Literal[False] = False
    silent_truncation_used: Literal[False] = False

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        pack_ids = [pack.pack_id for pack in self.packs]
        if len(pack_ids) != len(set(pack_ids)):
            raise ValueError("runtime evidence pack plan identifiers must be unique")
        if any(pack.prompt_chars > self.max_prompt_chars for pack in self.packs):
            raise ValueError("runtime evidence pack plan exceeds the prompt budget")
        if self.assignment_count != sum(pack.assignment_count for pack in self.packs):
            raise ValueError("runtime evidence pack plan assignment count is stale")
        if self.unpacked_payload_occurrence_count != self.assignment_count:
            raise ValueError("runtime evidence pack plan unpacked count is stale")
        if self.planned_payload_occurrence_count != sum(
            len(pack.source_record_ids) for pack in self.packs
        ):
            raise ValueError("runtime evidence pack plan payload count is stale")
        if self.avoided_payload_occurrence_count != (
            self.unpacked_payload_occurrence_count
            - self.planned_payload_occurrence_count
        ):
            raise ValueError("runtime evidence pack plan avoided count is stale")
        if self.unique_record_count > self.planned_payload_occurrence_count:
            raise ValueError("runtime evidence pack plan unique count exceeds payloads")
        return self


class RuntimeEvidencePackNode(StrictMemoryContextModel):
    pack_id: str
    purpose: str
    prompt_sha256: Sha256
    prompt_chars: int = Field(ge=1)
    cluster_ids: list[str] = Field(default_factory=list, min_length=1)
    source_record_ids: list[str] = Field(default_factory=list, min_length=1)
    assignment_count: int = Field(ge=1)
    assignment_root_sha256: Sha256
    output: ArtifactReference
    provider_checkpoint: ArtifactReference | None = None
    provider_checkpoint_id: str | None = None
    provider_output_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_node(self) -> Self:
        if not self.pack_id.strip() or not self.purpose.strip():
            raise ValueError("runtime evidence pack node identity cannot be blank")
        if (
            self.cluster_ids != sorted(set(self.cluster_ids))
            or self.source_record_ids != sorted(set(self.source_record_ids))
        ):
            raise ValueError("runtime evidence pack node identities must be sorted and unique")
        if self.output.item_count != len(self.cluster_ids):
            raise ValueError("runtime evidence pack node output count is stale")
        if self.assignment_count < len(self.source_record_ids):
            raise ValueError("runtime evidence pack assignments cannot trail record coverage")
        checkpoint_fields = (
            self.provider_checkpoint is not None,
            self.provider_checkpoint_id is not None,
            self.provider_output_sha256 is not None,
        )
        if any(checkpoint_fields) and not all(checkpoint_fields):
            raise ValueError("runtime evidence pack checkpoint commitment is incomplete")
        if self.provider_checkpoint is not None and self.provider_checkpoint.item_count != 1:
            raise ValueError("runtime evidence pack checkpoint count is stale")
        return self


class RuntimeEvidencePackManifest(StrictMemoryContextModel):
    schema_version: Literal["nslab.runtime_evidence_pack_manifest.v1"] = (
        "nslab.runtime_evidence_pack_manifest.v1"
    )
    run_id: str
    cutoff_at: AwareDatetime
    memory_snapshot_id: str
    policy_version: Literal["runtime_evidence_cross_cluster_pack.v1"] = (
        "runtime_evidence_cross_cluster_pack.v1"
    )
    max_prompt_chars: int = Field(ge=1)
    cluster_ids: list[str] = Field(default_factory=list, min_length=1)
    assignment_count: int = Field(ge=1)
    unique_record_count: int = Field(ge=1)
    unpacked_payload_occurrence_count: int = Field(ge=1)
    packed_payload_occurrence_count: int = Field(ge=1)
    avoided_payload_occurrence_count: int = Field(ge=0)
    assignment_root_sha256: Sha256
    source_record_root_sha256: Sha256
    plan: ArtifactReference
    packs: list[RuntimeEvidencePackNode] = Field(default_factory=list, min_length=1)
    first_n_shortcut_used: Literal[False] = False
    silent_truncation_used: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if not self.run_id.strip() or not self.memory_snapshot_id.strip():
            raise ValueError("runtime evidence pack manifest identity cannot be blank")
        if self.plan.item_count != len(self.packs):
            raise ValueError("runtime evidence pack plan count is stale")
        if self.cluster_ids != sorted(set(self.cluster_ids)):
            raise ValueError("runtime evidence pack clusters must be sorted and unique")
        pack_ids = [pack.pack_id for pack in self.packs]
        if len(pack_ids) != len(set(pack_ids)):
            raise ValueError("runtime evidence pack identifiers must be unique")
        if any(pack.prompt_chars > self.max_prompt_chars for pack in self.packs):
            raise ValueError("runtime evidence pack exceeds the prompt budget")
        if set(self.cluster_ids) != {
            cluster_id for pack in self.packs for cluster_id in pack.cluster_ids
        }:
            raise ValueError("runtime evidence pack cluster coverage is incomplete")
        if self.assignment_count != sum(pack.assignment_count for pack in self.packs):
            raise ValueError("runtime evidence pack assignment count is stale")
        if self.unpacked_payload_occurrence_count != self.assignment_count:
            raise ValueError("runtime evidence unpacked occurrence count is stale")
        if self.packed_payload_occurrence_count != sum(
            len(pack.source_record_ids) for pack in self.packs
        ):
            raise ValueError("runtime evidence packed occurrence count is stale")
        if self.avoided_payload_occurrence_count != (
            self.unpacked_payload_occurrence_count
            - self.packed_payload_occurrence_count
        ):
            raise ValueError("runtime evidence avoided occurrence count is stale")
        if self.unique_record_count > self.packed_payload_occurrence_count:
            raise ValueError("runtime evidence unique record count exceeds packed payloads")
        return self


class RuntimeRetrievalTraceRow(StrictMemoryContextModel):
    schema_version: Literal["nslab.runtime_retrieval_trace_row.v1"] = "nslab.runtime_retrieval_trace_row.v1"
    record_id: str
    independent_unit_id: str
    source_trade_date: date
    available_from: AwareDatetime
    replay_available_from: AwareDatetime | None = None
    cell_ids: list[str] = Field(default_factory=list)
    ann_rank: int | None = Field(default=None, ge=1)
    fts_rank: int | None = Field(default=None, ge=1)
    rerank_score: float | None = None
    lane: RuntimeRetrievalLane | None = None
    stages: list[RuntimeRetrievalStage] = Field(default_factory=list)
    selection_reason: str | None = None
    drop_reason: str | None = None
    offline_payload_exposed: bool | None = None
    offline_claim_referenced: bool | None = None
    rare_payload: bool = False
    evidence_group_size: Literal["small", "large", "unknown"] = "unknown"
    runtime_payload_exposed: bool = False
    evidence_memo_ids: list[str] = Field(default_factory=list)
    final_candidate_ids: list[str] = Field(default_factory=list)
    final_sector_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_trace_row(self) -> Self:
        if not self.record_id.strip() or not self.independent_unit_id.strip():
            raise ValueError("runtime retrieval trace record identity cannot be blank")
        if len(self.cell_ids) != len(set(self.cell_ids)) or not self.cell_ids:
            raise ValueError("runtime retrieval trace requires unique cell IDs")
        if len(self.stages) != len(set(self.stages)):
            raise ValueError("runtime retrieval stages must be unique")
        if "DROPPED" in self.stages:
            if not self.drop_reason or "LANE_SELECTED" in self.stages:
                raise ValueError("dropped trace rows require a reason and cannot be selected")
        elif self.drop_reason is not None:
            raise ValueError("non-dropped trace rows cannot have a drop reason")
        if "LANE_SELECTED" in self.stages and (self.lane is None or not self.selection_reason):
            raise ValueError("selected trace rows require lane and selection reason")
        if self.runtime_payload_exposed != ("LLM_EXPOSED" in self.stages):
            raise ValueError("runtime payload exposure must match the LLM stage")
        if bool(self.evidence_memo_ids) != ("MEMO_REFERENCED" in self.stages):
            raise ValueError("runtime evidence memo IDs must match the memo stage")
        if bool(self.final_candidate_ids or self.final_sector_ids) != ("FINAL_CITED" in self.stages):
            raise ValueError("runtime final provenance must match the final cited stage")
        if self.rerank_score is not None and not math.isfinite(self.rerank_score):
            raise ValueError("runtime rerank score must be finite")
        if self.replay_available_from is not None and self.replay_available_from.date() <= self.source_trade_date:
            raise ValueError("runtime replay availability must follow the source trade date")
        for values in (
            self.evidence_memo_ids,
            self.final_candidate_ids,
            self.final_sector_ids,
        ):
            if len(values) != len(set(values)) or any(not item.strip() for item in values):
                raise ValueError("runtime trace provenance lists must be unique and non-empty")
        return self


class RuntimeRetrievalTrace(StrictMemoryContextModel):
    schema_version: Literal["nslab.runtime_retrieval_trace.v1"] = "nslab.runtime_retrieval_trace.v1"
    trace_id: str
    run_id: str
    cluster_id: str
    query_text: str
    query_sha256: Sha256
    cutoff_at: AwareDatetime
    memory_snapshot_id: str
    policy_version: Literal["adaptive_population_drilldown.v4"] = "adaptive_population_drilldown.v4"
    budget: RuntimeRetrievalBudget
    source_population_manifests: list[ArtifactReference] = Field(default_factory=list)
    source_representative_manifests: list[ArtifactReference] = Field(default_factory=list)
    evidence_memo_artifact: ArtifactReference | None = None
    rows: list[RuntimeRetrievalTraceRow] = Field(default_factory=list)
    lane_candidate_counts: dict[str, int] = Field(default_factory=dict)
    lane_selected_counts: dict[str, int] = Field(default_factory=dict)
    offline_unexposed_recovered_count: int = Field(ge=0)
    offline_unexposed_llm_exposed_count: int = Field(ge=0)
    offline_unexposed_final_cited_count: int = Field(ge=0)
    rare_mechanism_recovered_count: int = Field(ge=0)
    online_full_scan_count: Literal[0] = 0
    blind_web_call_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_trace(self) -> Self:
        if any(
            not value.strip()
            for value in (
                self.trace_id,
                self.run_id,
                self.cluster_id,
                self.query_text,
                self.memory_snapshot_id,
            )
        ):
            raise ValueError("runtime retrieval trace identity cannot be blank")
        if self.query_sha256 != sha256_text(self.query_text):
            raise ValueError("runtime retrieval trace query hash mismatch")
        record_ids = [row.record_id for row in self.rows]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("runtime retrieval trace records must be unique")
        observed_candidates: dict[str, int] = {}
        observed_selected: dict[str, int] = {}
        for row in self.rows:
            effective_available_from = row.replay_available_from or row.available_from
            if effective_available_from > self.cutoff_at or row.source_trade_date >= self.cutoff_at.date():
                raise ValueError("runtime retrieval trace contains evidence after the cutoff")
            if row.lane is not None:
                observed_candidates[row.lane] = observed_candidates.get(row.lane, 0) + 1
            if "LANE_SELECTED" in row.stages and row.lane is not None:
                observed_selected[row.lane] = observed_selected.get(row.lane, 0) + 1
        if self.lane_candidate_counts != dict(sorted(observed_candidates.items())):
            raise ValueError("runtime retrieval lane candidate counts are stale")
        if self.lane_selected_counts != dict(sorted(observed_selected.items())):
            raise ValueError("runtime retrieval lane selected counts are stale")
        selected = sum("LANE_SELECTED" in row.stages for row in self.rows)
        if selected > self.budget.max_record_count:
            raise ValueError("runtime retrieval trace exceeds the hard record budget")
        return self
