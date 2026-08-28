"""Bounded adaptive cell expansion over immutable population evidence."""

from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from news_scalping_lab.contracts.memory_context import (
    POPULATION_PURPOSE_LANES,
    AdaptiveRetrievalIteration,
    AdaptiveRetrievalTrace,
    AdaptiveTriggerEvidence,
    ArtifactReference,
    PopulationManifest,
    RepresentativeSetManifest,
    RoutingDisposition,
)
from news_scalping_lab.memory.beneficiary import beneficiary_trigger_evidence
from news_scalping_lab.memory.diversity import (
    RepresentativeSelectionBudgetError,
    RepresentativeSelector,
    _inspect_built_representative,
    _require_finite_vector,
)
from news_scalping_lab.memory.index import ProductionMemoryIndex
from news_scalping_lab.memory.population import (
    PopulationRetriever,
    _inspect_built_population,
)
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    read_json,
    relative_to_root,
    sha256_text,
    write_json,
)

ADAPTIVE_RETRIEVAL_POLICY_VERSION = "adaptive_population_drilldown.v3"
ADAPTIVE_ARTIFACT_ROOT = Path("runs/adaptive")
ADAPTIVE_TRACE_FILE = "adaptive_retrieval_trace.json"
ADAPTIVE_MAX_DEPTH = 2
ADAPTIVE_MAX_CELL_COUNT = 12
ADAPTIVE_MAX_RECORD_COUNT = 32
ADAPTIVE_MAX_TOKEN_COUNT = 72_000
ADAPTIVE_MIN_INFORMATION_GAIN = 0.03
ADAPTIVE_CELLS_PER_ITERATION = 2
ADAPTIVE_SMALL_EFFECTIVE_SAMPLE_SIZE = 12.0


class AdaptiveRetriever:
    """Expand cells only when population evidence exposes a configured trigger."""

    def __init__(
        self,
        root: Path,
        *,
        memory_index: ProductionMemoryIndex,
    ) -> None:
        self.root = root.resolve()
        self.memory_index = memory_index
        self.population_retriever = PopulationRetriever(
            self.root,
            memory_index=memory_index,
        )
        self.representative_selector = RepresentativeSelector(
            self.root,
            memory_index=memory_index,
        )

    def run(
        self,
        *,
        initial_population_manifest_path: Path,
        initial_representative_set_manifest_path: Path,
        query: str,
        max_depth: int = ADAPTIVE_MAX_DEPTH,
        max_cell_count: int = ADAPTIVE_MAX_CELL_COUNT,
        max_record_count: int = ADAPTIVE_MAX_RECORD_COUNT,
        max_token_count: int = ADAPTIVE_MAX_TOKEN_COUNT,
        min_information_gain: float = ADAPTIVE_MIN_INFORMATION_GAIN,
        trigger_evidence: list[AdaptiveTriggerEvidence] | None = None,
    ) -> tuple[AdaptiveRetrievalTrace, Path]:
        trace = self._execute(
            initial_population_manifest_path=initial_population_manifest_path,
            initial_representative_set_manifest_path=(
                initial_representative_set_manifest_path
            ),
            query=query,
            max_depth=max_depth,
            max_cell_count=max_cell_count,
            max_record_count=max_record_count,
            max_token_count=max_token_count,
            min_information_gain=min_information_gain,
            trigger_evidence=list(trigger_evidence or []),
            force_database_verification=False,
        )
        output_dir = (
            self.root
            / ADAPTIVE_ARTIFACT_ROOT
            / _safe_segment(trace.run_id, field="run_id")
            / _safe_segment(trace.cluster_id, field="cluster_id")
            / trace.trace_id
        )
        _require_under(output_dir, self.root / ADAPTIVE_ARTIFACT_ROOT)
        path = output_dir / ADAPTIVE_TRACE_FILE
        _write_immutable_trace(path, trace)
        inspection = _inspect_built_adaptive(
            self.root,
            trace_path=path,
            expected_trace=trace,
        )
        if inspection["passed"] is not True:
            raise ValueError(
                "adaptive trace failed self-inspection: "
                + ", ".join(inspection["errors"])
            )
        return trace, path

    def inspect(
        self,
        trace_path: Path,
        *,
        force_database_verification: bool = True,
    ) -> dict[str, Any]:
        path = trace_path.resolve()
        base: dict[str, Any] = {
            "trace_path": relative_to_root(path, self.root),
            "passed": False,
            "errors": [],
        }
        try:
            raw_trace = read_json(path)
        except (OSError, ValueError) as exc:
            return {**base, "errors": [f"adaptive_trace_invalid:{exc}"]}
        if (
            isinstance(raw_trace, dict)
            and raw_trace.get("schema_version")
            in {
                "nslab.adaptive_retrieval_trace.v1",
                "nslab.adaptive_retrieval_trace.v2",
                "nslab.adaptive_retrieval_trace.v3",
            }
        ):
            return {
                **base,
                "errors": ["adaptive_trace_schema_legacy_v1"],
                "legacy_read_compatible": True,
            }
        try:
            trace = AdaptiveRetrievalTrace.model_validate(raw_trace)
            expected_path = (
                self.root
                / ADAPTIVE_ARTIFACT_ROOT
                / _safe_segment(trace.run_id, field="run_id")
                / _safe_segment(trace.cluster_id, field="cluster_id")
                / _safe_segment(trace.trace_id, field="trace_id")
                / ADAPTIVE_TRACE_FILE
            ).resolve()
        except ValueError as exc:
            return {**base, "errors": [f"adaptive_trace_invalid:{exc}"]}
        errors = []
        if path != expected_path:
            errors.append("adaptive_trace_path_mismatch")
        initial_population_path = self.root / (
            trace.initial_population_manifest.artifact_path
        )
        initial_representative_path = self.root / (
            trace.initial_representative_set_manifest.artifact_path
        )
        for name, artifact in (
            ("initial_population", trace.initial_population_manifest),
            ("initial_representative", trace.initial_representative_set_manifest),
            ("final_population", trace.final_population_manifest),
            ("final_representative", trace.final_representative_set_manifest),
        ):
            artifact_path = (self.root / artifact.artifact_path).resolve()
            if not artifact_path.exists():
                errors.append(f"adaptive_{name}_missing")
            elif file_sha256(artifact_path) != artifact.sha256:
                errors.append(f"adaptive_{name}_hash_mismatch")
        for iteration in trace.iterations:
            for name, artifact in (
                ("population", iteration.population_manifest),
                ("representative", iteration.representative_set_manifest),
            ):
                artifact_path = (self.root / artifact.artifact_path).resolve()
                if not artifact_path.exists():
                    errors.append(
                        f"adaptive_iteration_{iteration.iteration}_{name}_missing"
                    )
                elif file_sha256(artifact_path) != artifact.sha256:
                    errors.append(
                        f"adaptive_iteration_{iteration.iteration}_{name}_hash_mismatch"
                    )
        if not errors:
            try:
                recomputed = self._execute(
                    initial_population_manifest_path=initial_population_path,
                    initial_representative_set_manifest_path=(
                        initial_representative_path
                    ),
                    query=trace.query_text,
                    max_depth=trace.max_depth,
                    max_cell_count=trace.max_cell_count,
                    max_record_count=trace.max_record_count,
                    max_token_count=trace.max_token_count,
                    min_information_gain=trace.min_information_gain,
                    trigger_evidence=trace.trigger_evidence,
                    force_database_verification=force_database_verification,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"adaptive_trace_recompute_failed:{exc}")
            else:
                if recomputed != trace:
                    errors.append("adaptive_trace_recomputed_mismatch")
        return {
            **base,
            "passed": not errors,
            "errors": errors,
            "trace": trace.model_dump(mode="json"),
        }

    def _execute(
        self,
        *,
        initial_population_manifest_path: Path,
        initial_representative_set_manifest_path: Path,
        query: str,
        max_depth: int,
        max_cell_count: int,
        max_record_count: int,
        max_token_count: int,
        min_information_gain: float,
        trigger_evidence: list[AdaptiveTriggerEvidence],
        force_database_verification: bool,
    ) -> AdaptiveRetrievalTrace:
        query_text = query.strip()
        if not query_text:
            raise ValueError("adaptive retrieval requires a query")
        if min(max_depth, max_cell_count, max_record_count, max_token_count) < 1:
            raise ValueError("adaptive retrieval budgets must be positive")
        if not 0.0 <= min_information_gain <= 1.0:
            raise ValueError("adaptive information gain must be within zero and one")
        population_path = initial_population_manifest_path.resolve()
        representative_path = initial_representative_set_manifest_path.resolve()
        initial_population = PopulationManifest.model_validate(read_json(population_path))
        initial_representative = RepresentativeSetManifest.model_validate(
            read_json(representative_path)
        )
        if force_database_verification:
            population_inspection = self.population_retriever.inspect(
                population_path,
                force_database_verification=True,
            )
            representative_inspection = self.representative_selector.inspect(
                representative_path,
                force_database_verification=True,
            )
        else:
            population_inspection = _inspect_built_population(
                self.root,
                manifest_path=population_path,
                expected_manifest=initial_population,
            )
            representative_inspection = _inspect_built_representative(
                self.root,
                manifest_path=representative_path,
                expected_manifest=initial_representative,
            )
        if population_inspection["passed"] is not True:
            raise ValueError("initial adaptive population is not current")
        if representative_inspection["passed"] is not True:
            raise ValueError("initial adaptive representative set is not current")
        if initial_representative.population_id != initial_population.population_id:
            raise ValueError("adaptive initial representative population mismatch")
        if initial_representative.query_text != query_text:
            raise ValueError("adaptive query differs from representative query")
        _validate_trigger_evidence(
            self.root,
            trigger_evidence,
            cluster_id=initial_population.cluster_id,
            cutoff_at=initial_population.cutoff_at,
        )
        query_vectors = self.memory_index.embedding_provider.embed_texts([query_text])
        if len(query_vectors) != 1:
            raise ValueError("embedding provider returned the wrong query vector count")
        query_vector = query_vectors[0]
        _require_finite_vector(query_vector, field="adaptive query")
        query_embedding_sha256 = sha256_text(canonical_json(query_vector))
        if query_embedding_sha256 != initial_representative.query_embedding_sha256:
            raise ValueError("adaptive query embedding differs from representative query")
        if len(initial_population.selected_cell_ids) > max_cell_count:
            raise ValueError("adaptive initial cells exceed the cell budget")
        if initial_representative.selected_record_count > max_record_count:
            raise ValueError("adaptive initial records exceed the record budget")
        if initial_representative.estimated_token_count > max_token_count:
            raise ValueError("adaptive initial context exceeds the token budget")
        trace_identity = {
            "schema_version": "nslab.adaptive_retrieval_identity.v1",
            "run_id": initial_population.run_id,
            "cluster_id": initial_population.cluster_id,
            "cutoff_at": initial_population.cutoff_at.isoformat(),
            "query_sha256": sha256_text(query_text),
            "query_embedding_sha256": query_embedding_sha256,
            "initial_population_sha256": file_sha256(population_path),
            "initial_representative_sha256": file_sha256(representative_path),
            "policy_version": ADAPTIVE_RETRIEVAL_POLICY_VERSION,
            "max_depth": max_depth,
            "max_cell_count": max_cell_count,
            "max_record_count": max_record_count,
            "max_token_count": max_token_count,
            "min_information_gain": min_information_gain,
            "trigger_evidence": [
                item.model_dump(mode="json") for item in trigger_evidence
            ],
        }
        current_population = initial_population
        current_population_path = population_path
        current_representative = initial_representative
        current_representative_path = representative_path
        current_cells = list(initial_population.selected_cell_ids)
        iterations: list[AdaptiveRetrievalIteration] = []
        cumulative_tokens = initial_representative.estimated_token_count
        stopped_reason = "NO_TRIGGER"
        expansion_embedding_sha256s: list[str] = []
        for iteration_index in range(1, max_depth + 1):
            triggers = _trigger_reasons(
                current_population,
                current_representative,
                trigger_evidence,
            )
            if not triggers:
                stopped_reason = "NO_TRIGGER"
                break
            expansion_query = _expansion_query(
                query_text,
                triggers,
                trigger_evidence=trigger_evidence,
            )
            expansion_lanes, expansion_regimes = _expansion_plan(
                current_population,
                current_representative,
                triggers,
            )
            expansion_vectors = self.memory_index.embedding_provider.embed_texts(
                [expansion_query]
            )
            if len(expansion_vectors) != 1:
                raise ValueError("embedding provider returned the wrong expansion vector count")
            _require_finite_vector(
                expansion_vectors[0],
                field="adaptive expansion",
            )
            expansion_embedding_sha256 = sha256_text(
                canonical_json(expansion_vectors[0])
            )
            expansion_embedding_sha256s.append(expansion_embedding_sha256)
            search_candidates = self.memory_index.search_cells(
                expansion_query,
                cutoff_at=initial_population.cutoff_at,
                limit=max(1, max_cell_count * 4),
                query_vector=expansion_vectors[0],
                included_memory_lanes=expansion_lanes or None,
                included_regime_clusters=expansion_regimes or None,
            )
            candidate_cell_ids = [item.cell_id for item in search_candidates]
            available_cells = [
                cell_id
                for cell_id in candidate_cell_ids
                if cell_id not in set(current_cells)
            ]
            remaining_cell_budget = max_cell_count - len(current_cells)
            if remaining_cell_budget <= 0:
                stopped_reason = "MAX_CELL_COUNT"
                break
            added_cells = available_cells[
                : min(ADAPTIVE_CELLS_PER_ITERATION, remaining_cell_budget)
            ]
            if not added_cells:
                stopped_reason = "NO_ADDITIONAL_CELL"
                break
            expanded_cells = [*current_cells, *added_cells]
            population_result = self.population_retriever.build(
                run_id=current_population.run_id,
                cluster_id=current_population.cluster_id,
                cutoff_at=current_population.cutoff_at,
                selected_cell_ids=expanded_cells,
                independent_unit_type=current_population.independent_unit_type,
                population_purpose=current_population.population_purpose,
                routing_dispositions=cast(
                    tuple[RoutingDisposition, ...],
                    tuple(current_population.routing_dispositions),
                ),
                query_regime_cluster=current_population.query_regime_cluster,
            )
            try:
                representative_result = self.representative_selector.build(
                    population_manifest_path=population_result.manifest_path,
                    query=query_text,
                )
            except RepresentativeSelectionBudgetError:
                stopped_reason = "REPRESENTATIVE_BUDGET_SATURATED"
                break
            next_population = population_result.manifest
            next_representative = representative_result.manifest
            next_cumulative_tokens = (
                cumulative_tokens + next_representative.estimated_token_count
            )
            if next_representative.selected_record_count > max_record_count:
                stopped_reason = "MAX_RECORD_COUNT"
                break
            if next_cumulative_tokens > max_token_count:
                stopped_reason = "MAX_TOKEN_COUNT"
                break
            information_gain = _information_gain(
                current_population,
                current_representative,
                next_population,
                next_representative,
            )
            added_records = sorted(
                set(next_representative.selected_record_ids)
                - set(current_representative.selected_record_ids)
            )
            iterations.append(
                AdaptiveRetrievalIteration(
                    iteration=iteration_index,
                    trigger_reasons=triggers,
                    expansion_query_sha256=sha256_text(expansion_query),
                    expansion_embedding_sha256=expansion_embedding_sha256,
                    expansion_memory_lanes=list(expansion_lanes),
                    expansion_regime_clusters=list(expansion_regimes),
                    added_cell_ids=added_cells,
                    added_record_ids=added_records,
                    total_cell_count=len(expanded_cells),
                    total_record_count=next_representative.selected_record_count,
                    population_manifest=_artifact_reference(
                        self.root,
                        population_result.manifest_path,
                    ),
                    representative_set_manifest=_artifact_reference(
                        self.root,
                        representative_result.manifest_path,
                    ),
                    information_gain=information_gain,
                    cumulative_token_count=next_cumulative_tokens,
                )
            )
            current_population = next_population
            current_population_path = population_result.manifest_path
            current_representative = next_representative
            current_representative_path = representative_result.manifest_path
            current_cells = expanded_cells
            cumulative_tokens = next_cumulative_tokens
            if information_gain < min_information_gain:
                stopped_reason = "MIN_INFORMATION_GAIN"
                break
            stopped_reason = "MAX_DEPTH"
        trace_identity["expansion_embedding_sha256s"] = expansion_embedding_sha256s
        trace_id = "ADAPT-" + sha256_text(canonical_json(trace_identity))[:20].upper()
        return AdaptiveRetrievalTrace(
            trace_id=trace_id,
            run_id=initial_population.run_id,
            cluster_id=initial_population.cluster_id,
            cutoff_at=initial_population.cutoff_at,
            query_text=query_text,
            query_sha256=sha256_text(query_text),
            query_embedding_sha256=query_embedding_sha256,
            policy_version=ADAPTIVE_RETRIEVAL_POLICY_VERSION,
            trigger_evidence=trigger_evidence,
            initial_population_manifest=_artifact_reference(
                self.root,
                population_path,
            ),
            initial_representative_set_manifest=_artifact_reference(
                self.root,
                representative_path,
            ),
            initial_cell_ids=list(initial_population.selected_cell_ids),
            iterations=iterations,
            max_depth=max_depth,
            max_cell_count=max_cell_count,
            max_record_count=max_record_count,
            max_token_count=max_token_count,
            min_information_gain=min_information_gain,
            final_cell_ids=current_cells,
            final_population_manifest=_artifact_reference(
                self.root,
                current_population_path,
            ),
            final_representative_set_manifest=_artifact_reference(
                self.root,
                current_representative_path,
            ),
            stopped_reason=stopped_reason,
        )


def _trigger_reasons(
    population: PopulationManifest,
    representative: RepresentativeSetManifest,
    trigger_evidence: list[AdaptiveTriggerEvidence] | None = None,
) -> list[str]:
    reasons = []
    if population.effective_sample_size < ADAPTIVE_SMALL_EFFECTIVE_SAMPLE_SIZE:
        reasons.append("SMALL_EFFECTIVE_SAMPLE_SIZE")
    positive = population.polarity_counts.get("POSITIVE", 0)
    negative = population.polarity_counts.get("NEGATIVE", 0)
    near_miss = population.polarity_counts.get("NEAR_MISS", 0)
    if positive and (negative or near_miss):
        reasons.append("POLARITY_CONFLICT")
    nonempty_regimes = {
        key
        for key, count in population.regime_counts.items()
        if count and key not in {"UNKNOWN", "CONFLICTING"}
    }
    if population.regime_counts.get("CONFLICTING", 0) or len(nonempty_regimes) > 1:
        reasons.append("REGIME_DISAGREEMENT")
    if representative.diversity_coverage_ratio < 0.75:
        reasons.append("LOW_REPRESENTATIVE_COVERAGE")
    if population.population_purpose == "newsless" and (
        population.polarity_counts.get("UNEXPLAINED", 0)
        > population.independent_unit_count // 2
    ):
        reasons.append("HIGH_UNEXPLAINED_SHARE")
    reasons.extend(item.kind for item in trigger_evidence or [])
    return sorted(set(reasons))


_TRIGGER_QUERY_FOCUS = {
    "SMALL_EFFECTIVE_SAMPLE_SIZE": "boundary evidence older comparable cases",
    "POLARITY_CONFLICT": "negative near miss contradiction failure evidence",
    "REGIME_DISAGREEMENT": "different market regime comparison evidence",
    "LOW_REPRESENTATIVE_COVERAGE": "minority path quality role evidence",
    "HIGH_UNEXPLAINED_SHARE": "newsless unexplained alternative catalyst evidence",
    "MULTI_HOP_BENEFICIARY": "multi hop beneficiary mechanism business role evidence",
}


def _expansion_query(
    query: str,
    triggers: list[str],
    *,
    trigger_evidence: list[AdaptiveTriggerEvidence] | None = None,
) -> str:
    focus = " ".join(_TRIGGER_QUERY_FOCUS[trigger] for trigger in sorted(triggers))
    evidence_terms = " ".join(
        term
        for evidence in trigger_evidence or []
        if evidence.kind in triggers
        for term in evidence.query_terms
    )
    suffix = f" {evidence_terms}" if evidence_terms else ""
    return f"{query.strip()} | drill-down: {focus}{suffix}"


def _expansion_plan(
    population: PopulationManifest,
    representative: RepresentativeSetManifest,
    triggers: list[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    lanes = set()
    if "POLARITY_CONFLICT" in triggers:
        lanes.update(
            {
                "negative_controls",
                "near_misses",
                "counterexamples",
                "theme_formation_failures",
            }
        )
    if "HIGH_UNEXPLAINED_SHARE" in triggers:
        lanes.add("newsless_or_unexplained")
    if "MULTI_HOP_BENEFICIARY" in triggers:
        lanes.update(POPULATION_PURPOSE_LANES[population.population_purpose])
    if "LOW_REPRESENTATIVE_COVERAGE" in triggers:
        lanes.update(
            item.stratum.removeprefix("lane:")
            for item in representative.strata
            if item.stratum.startswith("lane:") and item.selected_unit_count == 0
        )
    if not lanes and "SMALL_EFFECTIVE_SAMPLE_SIZE" in triggers:
        lanes.update(POPULATION_PURPOSE_LANES[population.population_purpose])
    lanes.intersection_update(POPULATION_PURPOSE_LANES[population.population_purpose])
    regimes = set()
    if "REGIME_DISAGREEMENT" in triggers:
        candidates = [
            (count, regime)
            for regime, count in population.regime_counts.items()
            if count and regime not in {"UNKNOWN", "CONFLICTING"}
            and regime != population.query_regime_cluster
        ]
        if candidates:
            regimes.add(min(candidates)[1])
    return tuple(sorted(lanes)), tuple(sorted(regimes))


def _information_gain(
    previous_population: PopulationManifest,
    previous_representative: RepresentativeSetManifest,
    current_population: PopulationManifest,
    current_representative: RepresentativeSetManifest,
) -> float:
    ess_gain = max(
        0.0,
        current_population.effective_sample_size
        - previous_population.effective_sample_size,
    ) / max(1.0, ADAPTIVE_SMALL_EFFECTIVE_SAMPLE_SIZE)
    coverage_gain = max(
        0.0,
        current_representative.diversity_coverage_ratio
        - previous_representative.diversity_coverage_ratio,
    )
    conflict_gain = max(
        0.0,
        _uncertainty_score(previous_population, previous_representative)
        - _uncertainty_score(current_population, current_representative),
    )
    return min(1.0, 0.35 * ess_gain + 0.35 * coverage_gain + 0.30 * conflict_gain)


def _uncertainty_score(
    population: PopulationManifest,
    representative: RepresentativeSetManifest,
) -> float:
    total = max(1, population.independent_unit_count)
    positive = population.polarity_counts.get("POSITIVE", 0)
    negative = population.polarity_counts.get("NEGATIVE", 0)
    near_miss = population.polarity_counts.get("NEAR_MISS", 0)
    polarity_conflict = min(positive, negative + near_miss) / total
    regime_counts = [
        count
        for regime, count in population.regime_counts.items()
        if count and regime != "UNKNOWN"
    ]
    regime_total = sum(regime_counts)
    regime_conflict = (
        -sum(
            (count / regime_total) * math.log2(count / regime_total)
            for count in regime_counts
        )
        / math.log2(len(regime_counts))
        if len(regime_counts) > 1 and regime_total
        else 0.0
    )
    missing_outcome = population.outcome_summary.missing_outcome_unit_count / total
    coverage_gap = 1.0 - representative.diversity_coverage_ratio
    return min(
        1.0,
        0.30 * polarity_conflict
        + 0.25 * regime_conflict
        + 0.20 * missing_outcome
        + 0.25 * coverage_gap,
    )


def _inspect_built_adaptive(
    root: Path,
    *,
    trace_path: Path,
    expected_trace: AdaptiveRetrievalTrace,
) -> dict[str, Any]:
    """Verify trace serialization and source hashes without replaying retrieval."""

    errors: list[str] = []
    try:
        observed = AdaptiveRetrievalTrace.model_validate(read_json(trace_path))
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"adaptive_trace_invalid:{exc}"]}
    if observed != expected_trace:
        errors.append("adaptive_trace_serialization_mismatch")
    references = [
        observed.initial_population_manifest,
        observed.initial_representative_set_manifest,
        observed.final_population_manifest,
        observed.final_representative_set_manifest,
        *(
            reference
            for iteration in observed.iterations
            for reference in (
                iteration.population_manifest,
                iteration.representative_set_manifest,
            )
        ),
        *(item.source_artifact for item in observed.trigger_evidence),
    ]
    for reference in references:
        path = (root / reference.artifact_path).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            errors.append("adaptive_source_path_escape")
            continue
        if not path.exists():
            errors.append("adaptive_source_missing")
        elif file_sha256(path) != reference.sha256:
            errors.append("adaptive_source_hash_mismatch")
    return {"passed": not errors, "errors": sorted(set(errors))}


def _artifact_reference(root: Path, path: Path) -> ArtifactReference:
    return ArtifactReference(
        artifact_path=relative_to_root(path, root),
        sha256=file_sha256(path),
        item_count=1,
    )


def _validate_trigger_evidence(
    root: Path,
    evidence_rows: list[AdaptiveTriggerEvidence],
    *,
    cluster_id: str,
    cutoff_at: datetime,
) -> None:
    seen: set[tuple[str, str]] = set()
    for evidence in evidence_rows:
        key = (evidence.kind, evidence.source_artifact.sha256)
        if key in seen:
            raise ValueError("adaptive trigger evidence is duplicated")
        seen.add(key)
        if (
            as_kst(evidence.cutoff_at) != as_kst(cutoff_at)
            or cluster_id not in evidence.event_cluster_ids
        ):
            raise ValueError("adaptive trigger evidence identity mismatch")
        path = (root / evidence.source_artifact.artifact_path).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("adaptive trigger evidence escapes the project root") from exc
        if not path.exists() or file_sha256(path) != evidence.source_artifact.sha256:
            raise ValueError("adaptive trigger evidence artifact mismatch")
        expected = beneficiary_trigger_evidence(
            root,
            path,
            cluster_id=cluster_id,
        )
        if expected != evidence:
            raise ValueError("adaptive trigger evidence derivation mismatch")


def _write_immutable_trace(path: Path, trace: AdaptiveRetrievalTrace) -> None:
    if path.exists():
        existing = AdaptiveRetrievalTrace.model_validate(read_json(path))
        if existing != trace:
            raise ValueError("immutable adaptive trace conflict")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    write_json(temporary, trace.model_dump(mode="json"))
    os.replace(temporary, path)


def _safe_segment(value: str, *, field: str) -> str:
    stripped = value.strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if (
        not stripped
        or value != stripped
        or stripped in {".", ".."}
        or any(character not in allowed for character in stripped)
    ):
        raise ValueError(f"{field} contains unsafe path characters")
    return stripped


def _require_under(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("adaptive artifact path escapes its root") from exc
