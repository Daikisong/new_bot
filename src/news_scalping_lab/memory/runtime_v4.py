"""Lane-balanced retrieval v4 and bounded runtime evidence mini-maps."""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from news_scalping_lab.contracts.models import BlindPrediction
from news_scalping_lab.contracts.runtime_retrieval import (
    RuntimeEvidenceMemo,
    RuntimeEvidenceMemoBatch,
    RuntimeRetrievalBudget,
    RuntimeRetrievalLane,
    RuntimeRetrievalStage,
    RuntimeRetrievalTrace,
    RuntimeRetrievalTraceRow,
)
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.memory.index import ProductionMemoryIndex
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import (
    record_evidence_polarity,
    record_memory_lanes,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    read_json,
    relative_to_root,
    sha256_text,
    stable_id,
)

RUNTIME_RETRIEVAL_POLICY_VERSION = "adaptive_population_drilldown.v4"
RUNTIME_DAILY_CONTEXT_VERSION = "daily_population_context.v2"
RUNTIME_EVIDENCE_MAP_REDUCE_VERSION = "runtime_evidence_map_reduce.v1"
RUNTIME_RETRIEVAL_ROOT = Path("runs/checkpoints/runtime_retrieval_v4")
RUNTIME_EVIDENCE_ROOT = Path("runs/checkpoints/runtime_evidence_memos")
RUNTIME_INITIAL_MIN_RECORDS = 16
RUNTIME_INITIAL_MAX_RECORDS = 32
RUNTIME_DEEP_MAX_RECORDS = 128
RUNTIME_DEEP_DEFAULT_RECORDS = 96
RUNTIME_MAX_DEPTH = 3
RUNTIME_EVIDENCE_BATCH_SIZE = 16
RUNTIME_EVIDENCE_MAX_PROMPT_CHARS = 240_000

RUNTIME_LANES: tuple[RuntimeRetrievalLane, ...] = (
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
)

_BASE_LANE_MAP: dict[str, RuntimeRetrievalLane] = {
    "positive_analogs": "POSITIVE_ANALOG",
    "negative_controls": "NEGATIVE_CONTROL",
    "near_misses": "NEAR_MISS",
    "counterexamples": "COUNTEREXAMPLE",
    "newsless_or_unexplained": "NEWSLESS_OR_UNEXPLAINED",
    "candidate_generation_errors": "CANDIDATE_GENERATION_ERROR",
    "leader_selection_pairs": "LEADER_SELECTION_PAIR",
    "theme_formation_failures": "THEME_FORMATION_FAILURE",
}
_RANKING_ERROR_TYPES = frozenset({"candidate_ranking_error_case", "ranking_error_case"})
_GENERATION_ERROR_TYPES = frozenset(
    {
        "candidate_generation_error_case",
        "event_thesis_selection_error_case",
        "row_disposition_error_case",
        "entity_resolution_error_case",
    }
)
_THEME_TYPES = frozenset({"supervised_theme_formation_case", "theme_formation_case"})


@dataclass(frozen=True)
class SemanticExposureState:
    payload_exposed: bool | None = None
    claim_referenced: bool | None = None
    rare_payload: bool = False
    evidence_group_size: str = "unknown"


class SemanticExposureResolver(Protocol):
    def __call__(self, record_id: str) -> SemanticExposureState: ...


@dataclass(frozen=True)
class RuntimeCandidate:
    record: BrainRecordEnvelope
    independent_unit_id: str
    cell_ids: tuple[str, ...]
    relevance_score: float
    lanes: tuple[RuntimeRetrievalLane, ...]
    exposure: SemanticExposureState
    ann_rank: int | None = None
    fts_rank: int | None = None
    replay_available_from: datetime | None = None


@dataclass(frozen=True)
class RuntimeRetrievalBuildResult:
    trace: RuntimeRetrievalTrace
    trace_path: Path
    selected_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeEvidenceBuildResult:
    memos: tuple[RuntimeEvidenceMemo, ...]
    memo_path: Path
    trace: RuntimeRetrievalTrace
    trace_path: Path


def runtime_record_lanes(
    record: BrainRecordEnvelope,
    *,
    exposure: SemanticExposureState | None = None,
) -> tuple[RuntimeRetrievalLane, ...]:
    """Project generic record metadata into evidence lanes without domain lookup tables."""

    lanes = {_BASE_LANE_MAP[lane] for lane in record_memory_lanes(record) if lane in _BASE_LANE_MAP}
    record_type = record.record_type.casefold()
    polarity = str(record_evidence_polarity(record).value)
    if record_type in _RANKING_ERROR_TYPES:
        lanes.discard("CANDIDATE_GENERATION_ERROR")
        lanes.add("CANDIDATE_RANKING_ERROR")
    elif record_type in _GENERATION_ERROR_TYPES:
        lanes.add("CANDIDATE_GENERATION_ERROR")
    if record_type in _THEME_TYPES:
        lanes.add("THEME_FORMATION_SUCCESS" if polarity == "POSITIVE" else "THEME_FORMATION_FAILURE")
    continuation_marker = canonical_json(
        {
            "training_target": record.training_target,
            "record_type": record.record_type,
            "payload_keys": sorted(record.payload),
        }
    ).casefold()
    if "continuation" in continuation_marker:
        lanes.add("CONTINUATION_SUCCESS" if polarity == "POSITIVE" else "CONTINUATION_FAILURE")
    if exposure is not None and exposure.rare_payload:
        lanes.add("RARE_MECHANISM")
    return tuple(lane for lane in RUNTIME_LANES if lane in lanes)


def dynamic_runtime_budget(candidates: list[RuntimeCandidate]) -> RuntimeRetrievalBudget:
    polarity_counts = Counter(str(record_evidence_polarity(candidate.record).value) for candidate in candidates)
    regime_values = {_payload_text(candidate.record.payload, "regime_cluster") for candidate in candidates} - {""}
    lane_values = {lane for candidate in candidates for lane in candidate.lanes}
    entropy = _normalized_entropy(polarity_counts)
    triggers: list[str] = []
    if polarity_counts.get("POSITIVE") and (polarity_counts.get("NEGATIVE") or polarity_counts.get("NEAR_MISS")):
        triggers.append("POLARITY_CONFLICT")
    if len(regime_values) > 1:
        triggers.append("REGIME_DISAGREEMENT")
    if "RARE_MECHANISM" in lane_values:
        triggers.append("RARE_MECHANISM_PRESENT")
    if "NEWSLESS_OR_UNEXPLAINED" in lane_values:
        triggers.append("NEWSLESS_EVIDENCE_PRESENT")
    if "LEADER_SELECTION_PAIR" in lane_values:
        triggers.append("LEADER_COMPARISON_PRESENT")
    initial = min(
        RUNTIME_INITIAL_MAX_RECORDS,
        max(RUNTIME_INITIAL_MIN_RECORDS, 16 + round(entropy * 16)),
    )
    if len(triggers) >= 4 or entropy >= 0.8:
        maximum = RUNTIME_DEEP_MAX_RECORDS
        depth = RUNTIME_MAX_DEPTH
    elif triggers:
        maximum = RUNTIME_DEEP_DEFAULT_RECORDS
        depth = 3 if len(triggers) >= 2 else 2
    else:
        maximum = RUNTIME_INITIAL_MAX_RECORDS
        depth = 1
    return RuntimeRetrievalBudget(
        initial_record_count=initial,
        max_record_count=maximum,
        max_depth=depth,
        batch_size=RUNTIME_EVIDENCE_BATCH_SIZE,
        entropy=entropy,
        trigger_reasons=sorted(triggers),
    )


def select_runtime_candidates(
    candidates: list[RuntimeCandidate],
    *,
    budget: RuntimeRetrievalBudget,
) -> tuple[list[tuple[RuntimeCandidate, RuntimeRetrievalLane]], list[RuntimeCandidate]]:
    """Select every available lane first, then fill by relevance and diversity."""

    ordered = sorted(
        candidates,
        key=lambda item: (-item.relevance_score, item.record.record_id),
    )
    selected: list[tuple[RuntimeCandidate, RuntimeRetrievalLane]] = []
    selected_ids: set[str] = set()
    for lane in RUNTIME_LANES:
        lane_rows = [item for item in ordered if lane in item.lanes]
        if not lane_rows:
            continue
        preferred = sorted(
            lane_rows,
            key=lambda item: (
                -_relevance_band(item.relevance_score),
                item.exposure.payload_exposed is not False,
                -item.relevance_score,
                item.record.record_id,
            ),
        )
        candidate = next(
            (item for item in preferred if item.record.record_id not in selected_ids),
            None,
        )
        if candidate is None:
            continue
        selected.append((candidate, lane))
        selected_ids.add(candidate.record.record_id)
    for candidate in ordered:
        if len(selected) >= budget.max_record_count:
            break
        if candidate.record.record_id in selected_ids or not candidate.lanes:
            continue
        lane = next(
            (
                value
                for value in candidate.lanes
                if not any(selected_lane == value for _item, selected_lane in selected)
            ),
            candidate.lanes[0],
        )
        selected.append((candidate, lane))
        selected_ids.add(candidate.record.record_id)
    dropped = [item for item in ordered if item.record.record_id not in selected_ids]
    return selected, dropped


def build_runtime_retrieval_trace(
    root: Path,
    *,
    run_id: str,
    cluster_id: str,
    query_text: str,
    cutoff_at: datetime,
    memory_snapshot_id: str,
    candidates: list[RuntimeCandidate],
    source_population_manifests: list[Any],
    source_representative_manifests: list[Any],
) -> RuntimeRetrievalBuildResult:
    root = root.resolve()
    budget = dynamic_runtime_budget(candidates)
    selected, dropped = select_runtime_candidates(candidates, budget=budget)
    rows: list[RuntimeRetrievalTraceRow] = []
    for candidate, lane in selected:
        stages = _candidate_source_stages(candidate)
        stages.extend(["CELL_MEMBER", "METADATA_FILTERED", "RERANKED", "LANE_SELECTED"])
        rows.append(
            _trace_row(
                candidate,
                lane=lane,
                stages=stages,
                selection_reason=(
                    "lane_coverage_offline_unexposed_diversity"
                    if candidate.exposure.payload_exposed is False
                    else "lane_coverage_then_relevance"
                ),
            )
        )
    for candidate in dropped:
        stages = _candidate_source_stages(candidate)
        stages.extend(["CELL_MEMBER", "METADATA_FILTERED", "RERANKED", "DROPPED"])
        rows.append(
            _trace_row(
                candidate,
                lane=candidate.lanes[0] if candidate.lanes else None,
                stages=stages,
                drop_reason=("NO_ELIGIBLE_RUNTIME_LANE" if not candidate.lanes else "DYNAMIC_RECORD_BUDGET"),
            )
        )
    rows.sort(key=lambda item: item.record_id)
    trace = _make_trace(
        run_id=run_id,
        cluster_id=cluster_id,
        query_text=query_text,
        cutoff_at=cutoff_at,
        memory_snapshot_id=memory_snapshot_id,
        budget=budget,
        rows=rows,
        source_population_manifests=source_population_manifests,
        source_representative_manifests=source_representative_manifests,
    )
    path = root / RUNTIME_RETRIEVAL_ROOT / _safe_segment(run_id) / _safe_segment(cluster_id) / f"{trace.trace_id}.json"
    _write_immutable_model(path, trace)
    return RuntimeRetrievalBuildResult(
        trace=trace,
        trace_path=path,
        selected_record_ids=tuple(row.record_id for row in rows if "LANE_SELECTED" in row.stages),
    )


async def build_runtime_evidence_memos(
    root: Path,
    *,
    retrieval: RuntimeRetrievalBuildResult,
    memory_index: ProductionMemoryIndex,
    llm: LLMProvider,
) -> RuntimeEvidenceBuildResult:
    root = root.resolve()
    trace = retrieval.trace
    selected_rows = [row for row in trace.rows if "LANE_SELECTED" in row.stages]
    if not selected_rows:
        raise ValueError("runtime evidence mapping requires selected records")
    selected_record_ids = [row.record_id for row in selected_rows]
    _snapshot, source_records = memory_index.representative_source_records(
        selected_record_ids,
        cutoff_at=trace.cutoff_at,
    )
    if {item.record_id for item in source_records} != set(selected_record_ids):
        raise ValueError("runtime evidence records are not closed over the memory snapshot")
    records = _load_records_by_ids(root, set(selected_record_ids))
    memos: list[RuntimeEvidenceMemo] = []
    for start in range(0, len(selected_rows), trace.budget.batch_size):
        batch_rows = selected_rows[start : start + trace.budget.batch_size]
        prompt = _runtime_evidence_prompt(
            trace,
            batch_rows=batch_rows,
            records=records,
        )
        if len(prompt) > RUNTIME_EVIDENCE_MAX_PROMPT_CHARS:
            raise ValueError("runtime evidence mini-map prompt exceeds its character budget")
        try:
            generated = await llm.generate_structured(
                prompt=prompt,
                response_model=RuntimeEvidenceMemoBatch,
                purpose=(f"runtime_evidence_map:{trace.cluster_id}:{start // trace.budget.batch_size + 1:03d}"),
            )
        except NotImplementedError:
            generated = _fallback_memo_batch(trace, batch_rows=batch_rows, records=records)
        memos.extend(
            _normalize_memo_batch(
                generated,
                trace=trace,
                batch_rows=batch_rows,
                records=records,
            ).memos
        )
    memo_by_record: dict[str, list[str]] = defaultdict(list)
    for memo in memos:
        for record_id in memo.source_record_ids:
            memo_by_record[record_id].append(memo.memo_id)
    if set(memo_by_record) != {row.record_id for row in selected_rows}:
        raise ValueError("selected runtime records are not fully covered by evidence memos")
    memo_path = (
        root
        / RUNTIME_EVIDENCE_ROOT
        / _safe_segment(trace.run_id)
        / _safe_segment(trace.cluster_id)
        / "runtime_evidence_memos.jsonl"
    )
    memo_bytes = "".join(canonical_json(memo.model_dump(mode="json")) + "\n" for memo in memos).encode("utf-8")
    _write_immutable_bytes(memo_path, memo_bytes)
    updated_rows = []
    for row in trace.rows:
        memo_ids = sorted(set(memo_by_record.get(row.record_id, [])))
        if memo_ids:
            stages = [*row.stages, "LLM_EXPOSED", "MEMO_REFERENCED"]
            updated_rows.append(
                row.model_copy(
                    update={
                        "stages": stages,
                        "runtime_payload_exposed": True,
                        "evidence_memo_ids": memo_ids,
                    }
                )
            )
        else:
            updated_rows.append(row)
    updated = _make_trace(
        run_id=trace.run_id,
        cluster_id=trace.cluster_id,
        query_text=trace.query_text,
        cutoff_at=trace.cutoff_at,
        memory_snapshot_id=trace.memory_snapshot_id,
        budget=trace.budget,
        rows=updated_rows,
        source_population_manifests=trace.source_population_manifests,
        source_representative_manifests=trace.source_representative_manifests,
        evidence_memo_artifact={
            "artifact_path": relative_to_root(memo_path, root),
            "sha256": file_sha256(memo_path),
            "item_count": len(memos),
        },
    )
    trace_path = retrieval.trace_path.with_name(retrieval.trace_path.stem + ".evidence.json")
    _write_immutable_model(trace_path, updated)
    return RuntimeEvidenceBuildResult(
        memos=tuple(memos),
        memo_path=memo_path,
        trace=updated,
        trace_path=trace_path,
    )


def finalize_runtime_retrieval_trace(
    root: Path,
    *,
    evidence: RuntimeEvidenceBuildResult,
    prediction: BlindPrediction,
) -> tuple[RuntimeRetrievalTrace, Path]:
    candidate_ids_by_record: dict[str, list[str]] = defaultdict(list)
    for candidate in prediction.candidates:
        candidate_id = f"candidate:{candidate.rank}:{candidate.ticker}"
        for record_id in candidate.memory_record_ids:
            candidate_ids_by_record[record_id].append(candidate_id)
    sector_ids_by_record: dict[str, list[str]] = defaultdict(list)
    for index, sector in enumerate(prediction.dominant_sectors, start=1):
        sector_id = f"sector:{index}:{sha256_text(sector.name)[:12]}"
        for record_id in (
            *sector.supporting_record_ids,
            *sector.contradicting_record_ids,
        ):
            sector_ids_by_record[record_id].append(sector_id)
    rows: list[RuntimeRetrievalTraceRow] = []
    for row in evidence.trace.rows:
        candidate_ids = sorted(set(candidate_ids_by_record.get(row.record_id, [])))
        sector_ids = sorted(set(sector_ids_by_record.get(row.record_id, [])))
        if candidate_ids or sector_ids:
            rows.append(
                row.model_copy(
                    update={
                        "stages": [*row.stages, "FINAL_CITED"],
                        "final_candidate_ids": candidate_ids,
                        "final_sector_ids": sector_ids,
                    }
                )
            )
        else:
            rows.append(row)
    trace = _make_trace(
        run_id=evidence.trace.run_id,
        cluster_id=evidence.trace.cluster_id,
        query_text=evidence.trace.query_text,
        cutoff_at=evidence.trace.cutoff_at,
        memory_snapshot_id=evidence.trace.memory_snapshot_id,
        budget=evidence.trace.budget,
        rows=rows,
        source_population_manifests=evidence.trace.source_population_manifests,
        source_representative_manifests=evidence.trace.source_representative_manifests,
        evidence_memo_artifact=evidence.trace.evidence_memo_artifact,
    )
    path = evidence.trace_path.with_name(evidence.trace_path.stem + ".final.json")
    _write_immutable_model(path, trace)
    return trace, path


def candidates_from_daily_artifacts(
    root: Path,
    *,
    cluster_id: str,
    population_manifest_paths: list[Path],
    representative_manifest_paths: list[Path],
    exposure_resolver: SemanticExposureResolver | None = None,
    ann_rank_by_cell: dict[str, int] | None = None,
    fts_rank_by_cell: dict[str, int] | None = None,
    memory_index: ProductionMemoryIndex | None = None,
    cutoff_at: datetime | None = None,
    memory_snapshot_id: str | None = None,
) -> list[RuntimeCandidate]:
    """Build a bounded payload pool from the complete matched-cell population."""

    root = root.resolve()
    member_by_record: dict[str, dict[str, Any]] = {}
    for manifest_path in population_manifest_paths:
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("cluster_id") != cluster_id:
            continue
        member_ref = manifest.get("member_records")
        if not isinstance(member_ref, dict):
            continue
        for row in _read_jsonl(root / str(member_ref["artifact_path"])):
            record_id = str(row.get("record_id") or "")
            if not record_id:
                continue
            existing = member_by_record.setdefault(
                record_id,
                {
                    **row,
                    "matched_cell_ids": set(),
                    "memory_lanes": set(),
                },
            )
            existing["matched_cell_ids"].update(row.get("matched_cell_ids") or [])
            existing["memory_lanes"].update(row.get("memory_lanes") or [])
    representative_by_record: dict[str, dict[str, Any]] = {}
    for manifest_path in representative_manifest_paths:
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("cluster_id") != cluster_id:
            continue
        ref = manifest.get("representative_records")
        if not isinstance(ref, dict):
            continue
        for row in _read_jsonl(root / str(ref["artifact_path"])):
            record_id = str(row.get("record_id") or "")
            if not record_id:
                continue
            previous = representative_by_record.get(record_id)
            if previous is None or float(row.get("selection_score") or 0.0) > float(
                previous.get("selection_score") or 0.0
            ):
                representative_by_record[record_id] = row
    candidate_headers: list[
        tuple[
            str,
            dict[str, Any],
            tuple[RuntimeRetrievalLane, ...],
            SemanticExposureState,
            float,
        ]
    ] = []
    for record_id, member in sorted(member_by_record.items()):
        exposure = exposure_resolver(record_id) if exposure_resolver is not None else SemanticExposureState()
        lanes = _member_runtime_lanes(member, exposure=exposure)
        if not lanes:
            continue
        representative = representative_by_record.get(record_id)
        score = (
            float(representative.get("selection_score") or 0.0)
            if representative is not None
            else _population_member_relevance(
                member,
                ann_rank_by_cell=ann_rank_by_cell,
                fts_rank_by_cell=fts_rank_by_cell,
            )
        )
        candidate_headers.append((record_id, member, lanes, exposure, score))

    # Preserve every available evidence lane before filling the bounded payload pool.
    # This scans only the already materialized matched-cell population artifact; raw
    # record payloads are loaded only for the selected bounded candidates below.
    selected_headers: dict[
        str,
        tuple[
            str,
            dict[str, Any],
            tuple[RuntimeRetrievalLane, ...],
            SemanticExposureState,
            float,
        ],
    ] = {}
    per_lane_limit = max(1, RUNTIME_DEEP_MAX_RECORDS // len(RUNTIME_LANES))
    for lane in RUNTIME_LANES:
        lane_headers = sorted(
            (item for item in candidate_headers if lane in item[2]),
            key=lambda item: (
                -_relevance_band(item[4]),
                item[3].payload_exposed is not False,
                item[3].claim_referenced is not False,
                -item[4],
                item[0],
            ),
        )
        independent_units: set[str] = set()
        lane_count = 0
        for item in lane_headers:
            independent_unit_id = str(item[1].get("independent_unit_id") or "")
            if independent_unit_id in independent_units:
                continue
            selected_headers.setdefault(item[0], item)
            independent_units.add(independent_unit_id)
            lane_count += 1
            if lane_count >= per_lane_limit:
                break
    for item in sorted(
        candidate_headers,
        key=lambda value: (
            value[0] not in representative_by_record,
            value[3].payload_exposed is not False,
            -value[4],
            value[0],
        ),
    ):
        if len(selected_headers) >= RUNTIME_DEEP_MAX_RECORDS:
            break
        selected_headers.setdefault(item[0], item)

    records = _load_records_by_ids(root, set(selected_headers))
    effective_available_from_by_record: dict[str, datetime] = {}
    if memory_index is not None and selected_headers:
        if cutoff_at is None or memory_snapshot_id is None:
            raise ValueError("runtime candidate availability requires cutoff and snapshot identity")
        resolved_snapshot, effective_available_from_by_record = memory_index.effective_available_from_for_records(
            list(selected_headers),
            cutoff_at=cutoff_at,
        )
        if resolved_snapshot.snapshot_id != memory_snapshot_id:
            raise ValueError("runtime candidate availability used the wrong snapshot")
    candidates: list[RuntimeCandidate] = []
    for record_id, member, _header_lanes, exposure, score in sorted(
        selected_headers.values(), key=lambda item: item[0]
    ):
        record = records[record_id]
        effective_available_from = effective_available_from_by_record.get(record_id)
        replay_available_from = (
            as_kst(effective_available_from)
            if effective_available_from is not None
            and as_kst(effective_available_from) != as_kst(record.available_from)
            else None
        )
        cells = tuple(sorted(str(value) for value in member["matched_cell_ids"]))
        ann_ranks = [
            ann_rank_by_cell[value] for value in cells if ann_rank_by_cell is not None and value in ann_rank_by_cell
        ]
        fts_ranks = [
            fts_rank_by_cell[value] for value in cells if fts_rank_by_cell is not None and value in fts_rank_by_cell
        ]
        candidates.append(
            RuntimeCandidate(
                record=record,
                independent_unit_id=str(member.get("independent_unit_id") or ""),
                cell_ids=cells,
                relevance_score=score,
                lanes=runtime_record_lanes(record, exposure=exposure),
                exposure=exposure,
                ann_rank=min(ann_ranks) if ann_ranks else None,
                fts_rank=min(fts_ranks) if fts_ranks else None,
                replay_available_from=replay_available_from,
            )
        )
    return candidates


def _member_runtime_lanes(
    member: dict[str, Any],
    *,
    exposure: SemanticExposureState,
) -> tuple[RuntimeRetrievalLane, ...]:
    lanes = {_BASE_LANE_MAP[lane] for lane in member.get("memory_lanes") or [] if lane in _BASE_LANE_MAP}
    record_type = str(member.get("record_type") or "").casefold()
    polarity = str(member.get("evidence_polarity") or "").upper()
    if record_type in _RANKING_ERROR_TYPES:
        lanes.discard("CANDIDATE_GENERATION_ERROR")
        lanes.add("CANDIDATE_RANKING_ERROR")
    elif record_type in _GENERATION_ERROR_TYPES:
        lanes.add("CANDIDATE_GENERATION_ERROR")
    if record_type in _THEME_TYPES:
        lanes.add("THEME_FORMATION_SUCCESS" if polarity == "POSITIVE" else "THEME_FORMATION_FAILURE")
    continuation_marker = canonical_json(
        {
            "record_type": record_type,
            "path_type": member.get("path_type"),
            "memory_lanes": sorted(member.get("memory_lanes") or []),
        }
    ).casefold()
    if "continuation" in continuation_marker:
        lanes.add("CONTINUATION_SUCCESS" if polarity == "POSITIVE" else "CONTINUATION_FAILURE")
    if exposure.rare_payload:
        lanes.add("RARE_MECHANISM")
    return tuple(lane for lane in RUNTIME_LANES if lane in lanes)


def _population_member_relevance(
    member: dict[str, Any],
    *,
    ann_rank_by_cell: dict[str, int] | None,
    fts_rank_by_cell: dict[str, int] | None,
) -> float:
    cells = [str(value) for value in member.get("matched_cell_ids") or []]
    ranks = [
        rank
        for cell_id in cells
        for rank in (
            (ann_rank_by_cell or {}).get(cell_id),
            (fts_rank_by_cell or {}).get(cell_id),
        )
        if rank is not None
    ]
    retrieval_score = 1.0 / (1.0 + min(ranks)) if ranks else 0.0
    sample_weight = float(member.get("sample_weight") or 0.0)
    return retrieval_score + min(max(sample_weight, 0.0), 1.0) * 1e-3


def _relevance_band(score: float) -> int:
    """Keep exposure diversity subordinate to semantic relevance."""

    return math.floor(score * 20.0)


def _make_trace(
    *,
    run_id: str,
    cluster_id: str,
    query_text: str,
    cutoff_at: datetime,
    memory_snapshot_id: str,
    budget: RuntimeRetrievalBudget,
    rows: list[RuntimeRetrievalTraceRow],
    source_population_manifests: list[Any],
    source_representative_manifests: list[Any],
    evidence_memo_artifact: Any | None = None,
) -> RuntimeRetrievalTrace:
    lane_candidates = Counter(row.lane for row in rows if row.lane is not None)
    lane_selected = Counter(row.lane for row in rows if row.lane is not None and "LANE_SELECTED" in row.stages)
    identity = {
        "run_id": run_id,
        "cluster_id": cluster_id,
        "query_sha256": sha256_text(query_text),
        "cutoff_at": as_kst(cutoff_at).isoformat(),
        "memory_snapshot_id": memory_snapshot_id,
        "policy_version": RUNTIME_RETRIEVAL_POLICY_VERSION,
        "record_ids": [row.record_id for row in rows],
    }
    return RuntimeRetrievalTrace(
        trace_id="RTRV4-" + sha256_text(canonical_json(identity))[:20].upper(),
        run_id=run_id,
        cluster_id=cluster_id,
        query_text=query_text,
        query_sha256=sha256_text(query_text),
        cutoff_at=cutoff_at,
        memory_snapshot_id=memory_snapshot_id,
        budget=budget,
        source_population_manifests=source_population_manifests,
        source_representative_manifests=source_representative_manifests,
        evidence_memo_artifact=evidence_memo_artifact,
        rows=rows,
        lane_candidate_counts=dict(sorted(cast(dict[str, int], lane_candidates).items())),
        lane_selected_counts=dict(sorted(cast(dict[str, int], lane_selected).items())),
        offline_unexposed_recovered_count=sum(
            row.offline_payload_exposed is False and "LANE_SELECTED" in row.stages for row in rows
        ),
        offline_unexposed_llm_exposed_count=sum(
            row.offline_payload_exposed is False and row.runtime_payload_exposed for row in rows
        ),
        offline_unexposed_final_cited_count=sum(
            row.offline_payload_exposed is False and "FINAL_CITED" in row.stages for row in rows
        ),
        rare_mechanism_recovered_count=sum(row.rare_payload and "LANE_SELECTED" in row.stages for row in rows),
    )


def _trace_row(
    candidate: RuntimeCandidate,
    *,
    lane: RuntimeRetrievalLane | None,
    stages: list[RuntimeRetrievalStage],
    selection_reason: str | None = None,
    drop_reason: str | None = None,
) -> RuntimeRetrievalTraceRow:
    return RuntimeRetrievalTraceRow(
        record_id=candidate.record.record_id,
        independent_unit_id=candidate.independent_unit_id,
        source_trade_date=candidate.record.trade_date,
        available_from=candidate.record.available_from,
        replay_available_from=candidate.replay_available_from,
        cell_ids=list(candidate.cell_ids),
        ann_rank=candidate.ann_rank,
        fts_rank=candidate.fts_rank,
        rerank_score=candidate.relevance_score,
        lane=lane,
        stages=list(dict.fromkeys(stages)),
        selection_reason=selection_reason,
        drop_reason=drop_reason,
        offline_payload_exposed=candidate.exposure.payload_exposed,
        offline_claim_referenced=candidate.exposure.claim_referenced,
        rare_payload=candidate.exposure.rare_payload,
        evidence_group_size=cast(Any, candidate.exposure.evidence_group_size),
    )


def _candidate_source_stages(
    candidate: RuntimeCandidate,
) -> list[RuntimeRetrievalStage]:
    stages: list[RuntimeRetrievalStage] = []
    if candidate.ann_rank is not None:
        stages.append("ANN_CANDIDATE")
    if candidate.fts_rank is not None:
        stages.append("FTS_CANDIDATE")
    if not stages:
        stages.extend(["ANN_CANDIDATE", "FTS_CANDIDATE"])
    return stages


def _runtime_evidence_prompt(
    trace: RuntimeRetrievalTrace,
    *,
    batch_rows: list[RuntimeRetrievalTraceRow],
    records: dict[str, BrainRecordEnvelope],
) -> str:
    payload = {
        "schema_version": "nslab.runtime_evidence_map_input.v1",
        "map_reduce_version": RUNTIME_EVIDENCE_MAP_REDUCE_VERSION,
        "cluster_id": trace.cluster_id,
        "current_event_query": trace.query_text,
        "requirements": {
            "cover_every_source_record_exactly_at_least_once": True,
            "separate_support_from_failure_and_unresolved_conflict": True,
            "do_not_infer_from_counts_or_hashes": True,
            "do_not_use_information_after_cutoff": as_kst(trace.cutoff_at).isoformat(),
        },
        "records": [
            {
                "record_id": row.record_id,
                "lane": row.lane,
                "payload_sha256": sha256_text(canonical_json(records[row.record_id].payload)),
                "payload": records[row.record_id].payload,
            }
            for row in batch_rows
        ],
    }
    return (
        "Map the supplied cutoff-safe historical records into compact evidence memos. "
        "Return RuntimeEvidenceMemoBatch JSON. Every record payload is evidence, not "
        "a vote, and every source_record_id must appear in at least one memo.\n"
        "---RUNTIME_EVIDENCE_MAP_INPUT---\n" + canonical_json(payload)
    )


def _normalize_memo_batch(
    generated: RuntimeEvidenceMemoBatch,
    *,
    trace: RuntimeRetrievalTrace,
    batch_rows: list[RuntimeRetrievalTraceRow],
    records: dict[str, BrainRecordEnvelope],
) -> RuntimeEvidenceMemoBatch:
    allowed = {row.record_id for row in batch_rows}
    normalized: list[RuntimeEvidenceMemo] = []
    covered: set[str] = set()
    lane_by_record = {row.record_id: row.lane for row in batch_rows}
    for memo in generated.memos:
        source_ids = sorted(set(memo.source_record_ids).intersection(allowed))
        if not source_ids:
            continue
        lane = cast(RuntimeRetrievalLane, lane_by_record[source_ids[0]])
        normalized.append(
            memo.model_copy(
                update={
                    "memo_id": _memo_id(trace.cluster_id, lane, source_ids, records),
                    "cluster_id": trace.cluster_id,
                    "lane": lane,
                    "source_record_ids": source_ids,
                    "source_record_hash_root": _record_hash_root(source_ids, records),
                }
            )
        )
        covered.update(source_ids)
    for record_id in sorted(allowed - covered):
        lane = cast(RuntimeRetrievalLane, lane_by_record[record_id])
        normalized.append(
            _fallback_memo(
                trace.cluster_id,
                lane=lane,
                record_ids=[record_id],
                records=records,
            )
        )
    return RuntimeEvidenceMemoBatch(
        cluster_id=trace.cluster_id,
        source_record_ids=sorted(allowed),
        memos=normalized,
    )


def _fallback_memo_batch(
    trace: RuntimeRetrievalTrace,
    *,
    batch_rows: list[RuntimeRetrievalTraceRow],
    records: dict[str, BrainRecordEnvelope],
) -> RuntimeEvidenceMemoBatch:
    grouped: dict[RuntimeRetrievalLane, list[str]] = defaultdict(list)
    for row in batch_rows:
        grouped[cast(RuntimeRetrievalLane, row.lane)].append(row.record_id)
    memos = [
        _fallback_memo(
            trace.cluster_id,
            lane=lane,
            record_ids=record_ids,
            records=records,
        )
        for lane, record_ids in grouped.items()
    ]
    return RuntimeEvidenceMemoBatch(
        cluster_id=trace.cluster_id,
        source_record_ids=sorted(row.record_id for row in batch_rows),
        memos=memos,
    )


def _fallback_memo(
    cluster_id: str,
    *,
    lane: RuntimeRetrievalLane,
    record_ids: list[str],
    records: dict[str, BrainRecordEnvelope],
) -> RuntimeEvidenceMemo:
    source_ids = sorted(set(record_ids))
    return RuntimeEvidenceMemo(
        memo_id=_memo_id(cluster_id, lane, source_ids, records),
        cluster_id=cluster_id,
        lane=lane,
        source_record_ids=source_ids,
        source_record_hash_root=_record_hash_root(source_ids, records),
        current_vs_history_similarities=["Selected by semantic retrieval for the current event query."],
        current_vs_history_differences=[
            "No model-authored comparison was available; preserve the raw payload boundary."
        ],
        supporting_conditions=[],
        failure_conditions=[],
        unresolved_conflicts=["Requires final synthesis review."],
    )


def _memo_id(
    cluster_id: str,
    lane: RuntimeRetrievalLane,
    record_ids: list[str],
    records: dict[str, BrainRecordEnvelope],
) -> str:
    return stable_id(
        "RMEMO",
        cluster_id,
        lane,
        _record_hash_root(record_ids, records),
        length=20,
    )


def _record_hash_root(
    record_ids: list[str],
    records: dict[str, BrainRecordEnvelope],
) -> str:
    return sha256_text(
        canonical_json(
            [
                {
                    "record_id": record_id,
                    "payload_sha256": sha256_text(canonical_json(records[record_id].payload)),
                }
                for record_id in sorted(record_ids)
            ]
        )
    )


def _normalized_entropy(counts: Counter[str]) -> float:
    total = sum(counts.values())
    if total <= 0 or len(counts) <= 1:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counts.values() if count) / math.log2(
        len(counts)
    )


def _payload_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    return value.strip().upper() if isinstance(value, str) else ""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"runtime retrieval JSONL row is not an object: {path}")
                rows.append(value)
    return rows


def _load_records_by_ids(
    root: Path,
    record_ids: set[str],
) -> dict[str, BrainRecordEnvelope]:
    """Read each likely episode shard once; never load the full corpus in memory."""

    if not record_ids:
        return {}
    store = BrainRecordStore(root)
    found: dict[str, BrainRecordEnvelope] = {}
    likely_episodes = {record_id.rsplit("__", 1)[0] for record_id in record_ids if "__" in record_id}
    checked: set[Path] = set()
    for episode_id in sorted(likely_episodes):
        path = store.records_dir / f"{episode_id}.jsonl"
        if not path.exists():
            continue
        checked.add(path.resolve())
        for record in store.read_episode_records(episode_id):
            if record.record_id in record_ids:
                found[record.record_id] = record
    missing = record_ids - set(found)
    record_files = sorted(store.records_dir.glob("*.jsonl"))
    if missing and not record_files:
        for record in store.list_records():
            if record.record_id in missing:
                found[record.record_id] = record
                missing.remove(record.record_id)
                if not missing:
                    break
    if missing:
        for path in record_files:
            if path.resolve() in checked:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = BrainRecordEnvelope.model_validate_json(line)
                if record.record_id in missing:
                    found[record.record_id] = record
                    missing.remove(record.record_id)
                    if not missing:
                        break
            if not missing:
                break
    if missing:
        raise FileNotFoundError("runtime retrieval records are missing: " + ", ".join(sorted(missing)))
    return found


def _safe_segment(value: str) -> str:
    stripped = value.strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if (
        not stripped
        or stripped != value
        or stripped in {".", ".."}
        or any(character not in allowed for character in stripped)
    ):
        raise ValueError("runtime retrieval identity contains unsafe path characters")
    return stripped


def _write_immutable_model(path: Path, model: Any) -> None:
    payload = canonical_json(model.model_dump(mode="json")).encode("utf-8")
    _write_immutable_bytes(path, payload)


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable runtime retrieval artifact conflict: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
