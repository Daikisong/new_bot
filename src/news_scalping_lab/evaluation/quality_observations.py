"""Strict observations consumed by QUALITY_FULL score reporting."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalClusterObservation(_StrictObservation):
    cluster_id: str
    trace_id: str
    trace_artifact_path: str
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory_snapshot_id: str
    evidence_memo_artifact_path: str | None = None
    evidence_memo_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    searched_record_ids: list[str]
    selected_record_ids: list[str]
    llm_exposed_record_ids: list[str]
    memo_referenced_record_ids: list[str]
    stock_cited_record_ids: list[str]
    sector_cited_record_ids: list[str]
    final_cited_record_ids: list[str]
    selected_unused_record_ids: list[str]
    offline_unexposed_searched_record_ids: list[str]
    offline_unexposed_selected_record_ids: list[str]
    offline_unexposed_llm_exposed_record_ids: list[str]
    offline_unexposed_final_cited_record_ids: list[str]
    rare_selected_record_ids: list[str]
    independent_unit_ids: list[str]
    episode_ids: list[str]
    year_counts: dict[str, int]
    lane_stage_counts: dict[str, dict[str, int]]

    @model_validator(mode="after")
    def validate_stage_closure(self) -> Self:
        if any(
            not value.strip()
            for value in (
                self.cluster_id,
                self.trace_id,
                self.trace_artifact_path,
                self.memory_snapshot_id,
            )
        ):
            raise ValueError("retrieval cluster observation identity cannot be blank")
        memo_pair = (
            self.evidence_memo_artifact_path is not None,
            self.evidence_memo_sha256 is not None,
        )
        if memo_pair[0] != memo_pair[1]:
            raise ValueError("retrieval memo artifact path/hash must be paired")
        named_lists = {
            name: values
            for name, values in self.model_dump().items()
            if name.endswith("_ids") and isinstance(values, list)
        }
        for name, values in named_lists.items():
            if values != sorted(set(values)) or any(not item.strip() for item in values):
                raise ValueError(f"{name} must contain sorted unique non-empty IDs")
        searched = set(self.searched_record_ids)
        selected = set(self.selected_record_ids)
        exposed = set(self.llm_exposed_record_ids)
        memo = set(self.memo_referenced_record_ids)
        stock = set(self.stock_cited_record_ids)
        sector = set(self.sector_cited_record_ids)
        final = set(self.final_cited_record_ids)
        if not selected.issubset(searched):
            raise ValueError("selected records must be searched records")
        if not exposed.issubset(selected) or not memo.issubset(exposed):
            raise ValueError("retrieval payload and memo stages are not monotonic")
        if not (stock | sector).issubset(memo) or final != stock | sector:
            raise ValueError("final citations must close over memo-backed records")
        if set(self.selected_unused_record_ids) != selected - final:
            raise ValueError("selected-unused records do not match final citation closure")
        offline_searched = set(self.offline_unexposed_searched_record_ids)
        if not offline_searched.issubset(searched):
            raise ValueError("offline-unexposed searched records are outside search results")
        for values, parent, label in (
            (self.offline_unexposed_selected_record_ids, selected, "selected"),
            (self.offline_unexposed_llm_exposed_record_ids, exposed, "exposed"),
            (self.offline_unexposed_final_cited_record_ids, final, "cited"),
        ):
            observed = set(values)
            if not observed.issubset(offline_searched & parent):
                raise ValueError(
                    f"offline-unexposed {label} records are outside their stage"
                )
        if not set(self.rare_selected_record_ids).issubset(selected):
            raise ValueError("rare recovered records must be selected")
        if any(count < 0 for count in self.year_counts.values()):
            raise ValueError("retrieval year counts cannot be negative")
        if any(
            count < 0
            for stages in self.lane_stage_counts.values()
            for count in stages.values()
        ):
            raise ValueError("retrieval lane stage counts cannot be negative")
        return self


class RetrievalCaseObservation(_StrictObservation):
    memory_snapshot_id: str
    adaptive_trace_count: int = Field(ge=0)
    clusters: list[RetrievalClusterObservation]
    searched_record_ids: list[str]
    selected_record_ids: list[str]
    llm_exposed_record_ids: list[str]
    memo_referenced_record_ids: list[str]
    stock_cited_record_ids: list[str]
    sector_cited_record_ids: list[str]
    final_cited_record_ids: list[str]
    selected_unused_record_ids: list[str]
    offline_unexposed_searched_record_ids: list[str]
    offline_unexposed_selected_record_ids: list[str]
    offline_unexposed_llm_exposed_record_ids: list[str]
    offline_unexposed_final_cited_record_ids: list[str]
    rare_selected_record_ids: list[str]
    independent_unit_ids: list[str]
    episode_ids: list[str]
    year_counts: dict[str, int]
    lane_stage_counts: dict[str, dict[str, int]]
    searched_record_occurrence_count: int = Field(ge=0)
    selected_record_occurrence_count: int = Field(ge=0)
    llm_exposed_record_occurrence_count: int = Field(ge=0)
    memo_referenced_record_occurrence_count: int = Field(ge=0)
    stock_cited_record_occurrence_count: int = Field(ge=0)
    sector_cited_record_occurrence_count: int = Field(ge=0)
    final_cited_record_occurrence_count: int = Field(ge=0)
    selected_unused_record_occurrence_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_case_closure(self) -> Self:
        if not self.memory_snapshot_id.strip():
            raise ValueError("retrieval case snapshot identity cannot be blank")
        cluster_ids = [cluster.cluster_id for cluster in self.clusters]
        if cluster_ids != sorted(set(cluster_ids)):
            raise ValueError("retrieval clusters must be sorted and unique")
        for field_name in (
            "searched_record_ids",
            "selected_record_ids",
            "llm_exposed_record_ids",
            "memo_referenced_record_ids",
            "stock_cited_record_ids",
            "sector_cited_record_ids",
            "final_cited_record_ids",
            "selected_unused_record_ids",
            "offline_unexposed_searched_record_ids",
            "offline_unexposed_selected_record_ids",
            "offline_unexposed_llm_exposed_record_ids",
            "offline_unexposed_final_cited_record_ids",
            "rare_selected_record_ids",
            "independent_unit_ids",
            "episode_ids",
        ):
            observed = getattr(self, field_name)
            expected = sorted(
                {
                    item
                    for cluster in self.clusters
                    for item in getattr(cluster, field_name)
                }
            )
            if observed != expected:
                raise ValueError(f"retrieval case {field_name} is stale")
        expected_occurrences = {
            "searched_record_occurrence_count": "searched_record_ids",
            "selected_record_occurrence_count": "selected_record_ids",
            "llm_exposed_record_occurrence_count": "llm_exposed_record_ids",
            "memo_referenced_record_occurrence_count": "memo_referenced_record_ids",
            "stock_cited_record_occurrence_count": "stock_cited_record_ids",
            "sector_cited_record_occurrence_count": "sector_cited_record_ids",
            "final_cited_record_occurrence_count": "final_cited_record_ids",
            "selected_unused_record_occurrence_count": "selected_unused_record_ids",
        }
        for count_field, ids_field in expected_occurrences.items():
            expected_count = sum(
                len(getattr(cluster, ids_field)) for cluster in self.clusters
            )
            if getattr(self, count_field) != expected_count:
                raise ValueError(f"retrieval case {count_field} is stale")
        expected_years: dict[str, int] = {}
        expected_lanes: dict[str, dict[str, int]] = {}
        for cluster in self.clusters:
            for year, count in cluster.year_counts.items():
                expected_years[year] = expected_years.get(year, 0) + count
            for lane, stages in cluster.lane_stage_counts.items():
                target = expected_lanes.setdefault(lane, {})
                for stage, count in stages.items():
                    target[stage] = target.get(stage, 0) + count
        expected_lanes = {
            lane: dict(sorted(stages.items()))
            for lane, stages in sorted(expected_lanes.items())
        }
        if self.year_counts != dict(sorted(expected_years.items())):
            raise ValueError("retrieval case year counts are stale")
        if self.lane_stage_counts != expected_lanes:
            raise ValueError("retrieval case lane stage counts are stale")
        return self


class CitationClosureObservation(_StrictObservation):
    prediction_memory_record_ids: list[str]
    allowed_context_record_ids: list[str]
    runtime_final_cited_record_ids: list[str]
    legacy_final_cited_record_ids: list[str]
    orphan_record_ids: list[str]
    orphan_final_target_ids: list[str]
    closure_verified: bool

    @model_validator(mode="after")
    def validate_citations(self) -> Self:
        for field_name in (
            "prediction_memory_record_ids",
            "allowed_context_record_ids",
            "runtime_final_cited_record_ids",
            "legacy_final_cited_record_ids",
            "orphan_record_ids",
            "orphan_final_target_ids",
        ):
            values = getattr(self, field_name)
            if values != sorted(set(values)):
                raise ValueError(f"citation {field_name} must be sorted and unique")
        expected = not self.orphan_record_ids and not self.orphan_final_target_ids
        if self.closure_verified is not expected:
            raise ValueError("citation closure flag is stale")
        return self


class SafetyObservation(_StrictObservation):
    future_record_count: int = Field(ge=0)
    blind_web_call_count: int = Field(ge=0)
    online_full_scan_count: int = Field(ge=0)
    outcome_reference_count_during_prediction: int = Field(ge=0)
    orphan_citation_count: int = Field(ge=0)
    wrong_snapshot_count: int = Field(ge=0)
    snapshot_closure_verified: bool
    forbidden_shared_key_count: int = Field(ge=0)
    shared_digest_closure_verified: bool


class RuntimeEfficiencyObservation(_StrictObservation):
    elapsed_accounting_status: Literal["EXACT", "BOUNDED_RECOVERY"]
    elapsed_exact_completed_seconds: float = Field(ge=0.0)
    elapsed_lower_bound_seconds: float = Field(ge=0.0)
    elapsed_upper_bound_seconds: float = Field(ge=0.0)
    contains_recovered_attempts: bool
    recovered_interrupted_attempt_count: int = Field(ge=0)
    runtime_metrics: dict[str, int | float | str | None]
    runtime_metric_statuses: dict[
        str,
        Literal[
            "EXACT",
            "LOWER_BOUND",
            "PARTIAL_LOWER_BOUND",
            "UNAVAILABLE",
        ],
    ]
    runtime_metrics_accounting_status: Literal[
        "EXACT",
        "PARTIAL_UNAVAILABLE",
        "RECOVERED_LOWER_BOUND",
        "RECOVERED_PARTIAL",
    ]
    attempt_count: int = Field(ge=1)
    attempt_ledger_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if not (
            self.elapsed_exact_completed_seconds
            <= self.elapsed_lower_bound_seconds
            <= self.elapsed_upper_bound_seconds
        ):
            raise ValueError("runtime elapsed bounds are not monotonic")
        recovered = self.recovered_interrupted_attempt_count > 0
        if self.contains_recovered_attempts is not recovered:
            raise ValueError("runtime recovered-attempt flag is stale")
        if recovered != (self.elapsed_accounting_status == "BOUNDED_RECOVERY"):
            raise ValueError("runtime elapsed accounting status is stale")
        if not recovered and not (
            self.elapsed_exact_completed_seconds
            == self.elapsed_lower_bound_seconds
            == self.elapsed_upper_bound_seconds
        ):
            raise ValueError("exact runtime accounting must have equal bounds")
        if set(self.runtime_metrics) != set(self.runtime_metric_statuses):
            raise ValueError("runtime metric values and statuses differ")
        for key, status in self.runtime_metric_statuses.items():
            value = self.runtime_metrics[key]
            if (status == "UNAVAILABLE") is not (value is None):
                raise ValueError("runtime unavailable metric status is stale")
        return self


class SharedStageObservation(RuntimeEfficiencyObservation):
    build_attempt_count: int = Field(ge=0)
    cache_load_attempt_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_call_closure(self) -> Self:
        if self.attempt_count != (
            self.build_attempt_count
            + self.cache_load_attempt_count
            + self.recovered_interrupted_attempt_count
        ):
            raise ValueError("shared-stage attempt accounting does not close")
        return self


class QualityCaseObservation(_StrictObservation):
    schema_version: Literal["nslab.quality_case_observation.v1"] = (
        "nslab.quality_case_observation.v1"
    )
    case_id: str
    trade_date: date
    variant_id: Literal["V0", "V1"]
    metrics: dict[str, Any]
    retrieval: RetrievalCaseObservation
    citation_closure: CitationClosureObservation
    safety: SafetyObservation
    efficiency: RuntimeEfficiencyObservation
    shared_stage: SharedStageObservation
    shared_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_universe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_universe_count: int = Field(ge=1)
    population_universe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    population_universe_count: int = Field(ge=1)
    market_universe_policy_version: str
    brier_population_policy_version: str
    probability_policy_version: str

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if not all(
            value.strip()
            for value in (
                self.case_id,
                self.market_universe_policy_version,
                self.brier_population_policy_version,
                self.probability_policy_version,
            )
        ):
            raise ValueError("quality observation identity cannot be blank")
        if (
            self.metrics.get("evaluation_universe_sha256")
            != self.evaluation_universe_sha256
            or self.metrics.get("evaluation_universe_count")
            != self.evaluation_universe_count
            or self.metrics.get("population_universe_sha256")
            != self.population_universe_sha256
            or self.metrics.get("population_count")
            != self.population_universe_count
            or self.metrics.get("evaluation_universe_policy_version")
            != self.market_universe_policy_version
            or self.metrics.get("population_universe_policy_version")
            != self.brier_population_policy_version
            or self.metrics.get("probability_policy_version")
            != self.probability_policy_version
        ):
            raise ValueError("quality observation market universe binding is stale")
        if self.safety.orphan_citation_count != len(
            self.citation_closure.orphan_record_ids
        ) + len(self.citation_closure.orphan_final_target_ids):
            raise ValueError("quality observation orphan citation count is stale")
        if self.safety.snapshot_closure_verified != (
            self.safety.wrong_snapshot_count == 0
        ):
            raise ValueError("quality observation snapshot closure flag is stale")
        if self.safety.shared_digest_closure_verified != (
            self.safety.forbidden_shared_key_count == 0
        ):
            raise ValueError("quality observation shared digest safety flag is stale")
        return self
