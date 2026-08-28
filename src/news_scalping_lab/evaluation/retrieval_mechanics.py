"""Seeded production-artifact mechanics smoke for current retrieval wiring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    DailyMemoryContext,
    EventClusterEntry,
    EventClusterManifest,
    MemoryCoverageManifest,
    NewsCoverageManifest,
    NewsRowCoverage,
)
from news_scalping_lab.contracts.runtime_retrieval import (
    RuntimeEvidenceMemo,
    RuntimeRetrievalTrace,
)
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.memory.beneficiary import build_beneficiary_graph
from news_scalping_lab.memory.daily_context import (
    DAILY_MEMORY_CONTEXT_ROOT,
    attach_runtime_evidence_to_daily_context,
    build_daily_memory_context,
)
from news_scalping_lab.memory.index import ProductionMemoryIndex
from news_scalping_lab.memory.runtime_v4 import (
    RuntimeRetrievalBuildResult,
    build_runtime_evidence_memos,
)
from news_scalping_lab.records.models import NormalizedEpisodeIndex
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    read_json,
    relative_to_root,
    sha256_text,
    stable_id,
    write_json,
)

RETRIEVAL_MECHANICS_VERSION = "nslab.retrieval_mechanics_smoke.v1"
RETRIEVAL_MECHANICS_ROOT = Path("runs/semantic_brain_upgrade/retrieval_mechanics")
RETRIEVAL_MECHANICS_DIAGNOSTIC = Path(
    "diagnostics/retrieval_mechanics_report.json"
)
RETRIEVAL_MECHANICS_SEED = "NSLAB-RETRIEVAL-MECHANICS-20260828-v1"
RETRIEVAL_MECHANICS_CASE_COUNT = 12


@dataclass(frozen=True)
class RetrievalMechanicsCase:
    episode_id: str
    trade_date: date
    next_trade_date: date
    query: str
    features: tuple[str, ...]
    year: int
    source_index_path: Path
    source_prediction_path: Path
    source_outcome_path: Path


@dataclass(frozen=True)
class RetrievalMechanicsRunResult:
    selection_manifest_path: Path
    report: dict[str, Any]
    report_path: Path


def select_retrieval_mechanics_cases(
    root: Path,
    *,
    seed: str = RETRIEVAL_MECHANICS_SEED,
    count: int = RETRIEVAL_MECHANICS_CASE_COUNT,
) -> list[RetrievalMechanicsCase]:
    root = root.resolve()
    candidates: list[RetrievalMechanicsCase] = []
    for episode_dir in sorted((root / "research" / "episodes").iterdir()):
        if not episode_dir.is_dir():
            continue
        index_path = episode_dir / "normalized_episode_index.json"
        prediction_path = episode_dir / "raw_blocks" / "blind_prediction.json"
        outcome_path = episode_dir / "raw_blocks" / "outcome_ledger.jsonl"
        source_path = episode_dir / "raw_blocks" / "source_ledger.jsonl"
        if not all(path.exists() for path in (index_path, prediction_path, outcome_path, source_path)):
            continue
        if outcome_path.stat().st_size == 0 or source_path.stat().st_size == 0:
            continue
        try:
            index = NormalizedEpisodeIndex.model_validate(read_json(index_path))
            prediction = read_json(prediction_path)
        except (OSError, ValueError):
            continue
        if (
            index.blind_valid is False
            or index.next_trade_date is None
            or not isinstance(prediction, dict)
        ):
            continue
        query = _blind_query(prediction)
        if not query:
            continue
        features = _case_features(index.record_count_by_type)
        candidates.append(
            RetrievalMechanicsCase(
                episode_id=index.episode_id,
                trade_date=index.trade_date,
                next_trade_date=index.next_trade_date,
                query=query,
                features=features,
                year=index.trade_date.year,
                source_index_path=index_path,
                source_prediction_path=prediction_path,
                source_outcome_path=outcome_path,
            )
        )
    if len(candidates) < count:
        raise ValueError(
            f"retrieval mechanics requires {count} complete Gold cases; found {len(candidates)}"
        )
    selected: list[RetrievalMechanicsCase] = []
    covered_years: set[int] = set()
    covered_features: set[str] = set()
    remaining = list(candidates)
    while len(selected) < count:
        remaining.sort(
            key=lambda item: (
                -(item.year not in covered_years),
                -len(set(item.features) - covered_features),
                sha256_text(f"{seed}|{item.episode_id}"),
            )
        )
        chosen = remaining.pop(0)
        selected.append(chosen)
        covered_years.add(chosen.year)
        covered_features.update(chosen.features)
    return sorted(selected, key=lambda item: (item.trade_date, item.episode_id))


async def run_retrieval_mechanics_smoke(
    root: Path,
    *,
    memory_index: ProductionMemoryIndex,
    llm: LLMProvider,
    cutoff_at: datetime,
    seed: str = RETRIEVAL_MECHANICS_SEED,
    count: int = RETRIEVAL_MECHANICS_CASE_COUNT,
) -> RetrievalMechanicsRunResult:
    root = root.resolve()
    cases = select_retrieval_mechanics_cases(root, seed=seed, count=count)
    output_dir = root / RETRIEVAL_MECHANICS_ROOT
    selection_manifest_path = output_dir / "selection_manifest.json"
    selection_payload = {
        "schema_version": "nslab.retrieval_mechanics_selection.v1",
        "seed": seed,
        "seed_sha256": sha256_text(seed),
        "case_count": len(cases),
        "selection_policy": "GREEDY_YEAR_AND_RECORD_LANE_COVERAGE_THEN_SEEDED_HASH",
        "cases": [_case_manifest(root, case) for case in cases],
    }
    write_json(selection_manifest_path, selection_payload)
    snapshot = memory_index.resolve_snapshot(cutoff_at=cutoff_at)
    case_reports: list[dict[str, Any]] = []
    for case in cases:
        run_id = "MECH-" + sha256_text(
            f"{seed}|{case.episode_id}|{as_kst(cutoff_at).isoformat()}"
        )[:20].upper()
        cluster_id = "MECHCL-" + sha256_text(case.episode_id)[:16].upper()
        source_paths = _write_case_sources(
            root,
            run_id=run_id,
            cluster_id=cluster_id,
            case=case,
            cutoff_at=cutoff_at,
            snapshot_manifest=snapshot,
        )
        _graph, graph_path = build_beneficiary_graph(
            root,
            run_id=run_id,
            cutoff_at=cutoff_at,
            event_cluster_manifest_path=source_paths["event_manifest"],
            candidates=[],
            company_memory_context=[],
        )
        context_path = (
            root / DAILY_MEMORY_CONTEXT_ROOT / run_id / "daily_memory_context.json"
        )
        context = (
            DailyMemoryContext.model_validate(read_json(context_path))
            if context_path.exists()
            else None
        )
        if context is None or not context.runtime_evidence_traces:
            context, context_path = await asyncio.to_thread(
                build_daily_memory_context,
                root,
                memory_index=memory_index,
                run_id=run_id,
                trade_date=case.trade_date,
                cutoff_at=cutoff_at,
                corpus_manifest_sha256=snapshot.corpus_manifest_sha256,
                news_coverage_manifest_path=source_paths["news_manifest"],
                event_cluster_manifest_path=source_paths["event_manifest"],
                event_cluster_artifact_path=source_paths["event_rows"],
                memory_coverage_manifest_path=source_paths["memory_coverage"],
                beneficiary_graph_path=graph_path,
                retrieval_cluster_ids={cluster_id},
            )
            if context is None:
                raise AssertionError("daily memory context build returned no context")
            evidence_results = []
            for reference in context.runtime_retrieval_traces:
                trace_path = root / reference.artifact_path
                trace = RuntimeRetrievalTrace.model_validate(read_json(trace_path))
                retrieval = RuntimeRetrievalBuildResult(
                    trace=trace,
                    trace_path=trace_path,
                    selected_record_ids=tuple(
                        row.record_id
                        for row in trace.rows
                        if "LANE_SELECTED" in row.stages
                    ),
                )
                if not retrieval.selected_record_ids:
                    raise ValueError(
                        f"retrieval mechanics selected no records: {case.episode_id}"
                    )
                evidence_results.append(
                    await build_runtime_evidence_memos(
                        root,
                        retrieval=retrieval,
                        memory_index=memory_index,
                        llm=llm,
                    )
                )
            context, context_path = attach_runtime_evidence_to_daily_context(
                root,
                context_path=context_path,
                evidence_results=evidence_results,
            )
        inspection = inspect_retrieval_mechanics_context(
            root,
            context_path,
            memory_index=memory_index,
        )
        traces = [
            RuntimeRetrievalTrace.model_validate(
                read_json(root / reference.artifact_path)
            )
            for reference in context.runtime_evidence_traces
        ]
        rows = [row for trace in traces for row in trace.rows]
        case_reports.append(
            {
                "episode_id": case.episode_id,
                "trade_date": case.trade_date.isoformat(),
                "run_id": run_id,
                "material_cluster_count": len(context.material_event_cluster_ids),
                "adaptive_trace_count": len(context.adaptive_retrieval_traces),
                "runtime_trace_count": len(traces),
                "population_count": len(context.population_manifests),
                "representative_set_count": len(context.representative_set_manifests),
                "selected_record_count": sum(
                    "LANE_SELECTED" in row.stages for row in rows
                ),
                "llm_exposed_record_count": sum(row.runtime_payload_exposed for row in rows),
                "offline_unexposed_recovered_count": sum(
                    trace.offline_unexposed_recovered_count for trace in traces
                ),
                "rare_mechanism_recovered_count": sum(
                    trace.rare_mechanism_recovered_count for trace in traces
                ),
                "lane_selected_counts": _sum_counts(
                    trace.lane_selected_counts for trace in traces
                ),
                "future_record_count": sum(
                    as_kst(row.available_from) > as_kst(cutoff_at)
                    or row.source_trade_date > as_kst(cutoff_at).date()
                    for row in rows
                ),
                "web_call_count": sum(trace.blind_web_call_count for trace in traces),
                "online_full_scan_count": sum(
                    trace.online_full_scan_count for trace in traces
                ),
                "memory_snapshot_id": context.memory_snapshot_id,
                "context_complete": context.context_complete,
                "context_inspection_passed": inspection.get("passed") is True,
                "context_inspection_errors": inspection.get("errors", []),
                "daily_context_artifact": relative_to_root(context_path, root),
                "daily_context_sha256": file_sha256(context_path),
                "trace_source_closure_100pct": inspection.get("passed") is True,
            }
        )
    report = _mechanics_report(
        cases=case_reports,
        snapshot_id=snapshot.snapshot_id,
        selection_manifest_path=selection_manifest_path,
        root=root,
        cutoff_at=cutoff_at,
    )
    run_report_path = output_dir / "retrieval_mechanics_report.json"
    write_json(run_report_path, report)
    report_path = root / RETRIEVAL_MECHANICS_DIAGNOSTIC
    write_json(report_path, report)
    report_path.with_suffix(".md").write_text(
        retrieval_mechanics_report_markdown(report),
        encoding="utf-8",
    )
    return RetrievalMechanicsRunResult(
        selection_manifest_path=selection_manifest_path,
        report=report,
        report_path=report_path,
    )


def inspect_retrieval_mechanics_context(
    root: Path,
    context_path: Path,
    *,
    memory_index: ProductionMemoryIndex,
) -> dict[str, Any]:
    """Verify Gate A closure without repeating expensive semantic retrieval."""

    root = root.resolve()
    errors: list[str] = []
    try:
        context = DailyMemoryContext.model_validate(read_json(context_path))
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"mechanics_context_invalid:{exc}"]}
    try:
        snapshot = memory_index.resolve_snapshot(cutoff_at=context.cutoff_at)
    except (OSError, ValueError) as exc:
        errors.append(f"mechanics_snapshot_invalid:{exc}")
        snapshot = None
    if snapshot is None or snapshot.snapshot_id != context.memory_snapshot_id:
        errors.append("mechanics_snapshot_identity_mismatch")
    references = [
        context.news_coverage_manifest,
        context.event_cluster_manifest,
        context.event_clusters,
        context.memory_coverage_manifest,
        context.category_brain_manifest,
        context.category_brain_index_manifest,
        context.category_selected_claims,
        *context.population_manifests,
        *context.representative_set_manifests,
        *context.adaptive_retrieval_traces,
        *context.runtime_retrieval_traces,
        *context.runtime_evidence_traces,
        *context.runtime_evidence_memos,
        context.beneficiary_graph,
        context.compact_final_context,
    ]
    for reference in references:
        path = (root / reference.artifact_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append("mechanics_artifact_path_escape")
            continue
        if not path.exists():
            errors.append("mechanics_artifact_missing")
        elif file_sha256(path) != reference.sha256:
            errors.append("mechanics_artifact_hash_mismatch")
    population_refs = {
        canonical_json(item.model_dump(mode="json"))
        for item in context.population_manifests
    }
    representative_refs = {
        canonical_json(item.model_dump(mode="json"))
        for item in context.representative_set_manifests
    }
    trace_clusters: list[str] = []
    selected_ids: set[str] = set()
    for reference in context.runtime_evidence_traces:
        try:
            trace = RuntimeRetrievalTrace.model_validate(
                read_json(root / reference.artifact_path)
            )
        except (OSError, ValueError) as exc:
            errors.append(f"mechanics_runtime_trace_invalid:{exc}")
            continue
        trace_clusters.append(trace.cluster_id)
        if (
            trace.run_id != context.run_id
            or trace.memory_snapshot_id != context.memory_snapshot_id
            or as_kst(trace.cutoff_at) != as_kst(context.cutoff_at)
        ):
            errors.append("mechanics_runtime_trace_identity_mismatch")
        if any(
            canonical_json(item.model_dump(mode="json")) not in population_refs
            for item in trace.source_population_manifests
        ):
            errors.append("mechanics_runtime_population_source_detached")
        if any(
            canonical_json(item.model_dump(mode="json")) not in representative_refs
            for item in trace.source_representative_manifests
        ):
            errors.append("mechanics_runtime_representative_source_detached")
        for row in trace.rows:
            if (
                row.available_from > trace.cutoff_at
                or row.source_trade_date > trace.cutoff_at.date()
            ):
                errors.append("mechanics_runtime_future_record")
            if "LANE_SELECTED" in row.stages:
                selected_ids.add(row.record_id)
                if not row.runtime_payload_exposed or not row.evidence_memo_ids:
                    errors.append("mechanics_runtime_selected_record_not_mapped")
        if trace.blind_web_call_count or trace.online_full_scan_count:
            errors.append("mechanics_runtime_forbidden_evidence_access")
    if trace_clusters != context.runtime_retrieval_cluster_ids:
        errors.append("mechanics_runtime_cluster_coverage_mismatch")
    memo_record_ids: set[str] = set()
    for reference in context.runtime_evidence_memos:
        for line in (root / reference.artifact_path).read_text(
            encoding="utf-8"
        ).splitlines():
            if not line.strip():
                continue
            try:
                memo = RuntimeEvidenceMemo.model_validate_json(line)
            except ValueError as exc:
                errors.append(f"mechanics_runtime_memo_invalid:{exc}")
                continue
            memo_record_ids.update(memo.source_record_ids)
    if memo_record_ids != selected_ids:
        errors.append("mechanics_runtime_memo_record_coverage_mismatch")
    if not context.context_complete:
        errors.append("mechanics_context_incomplete")
    return {"passed": not errors, "errors": sorted(set(errors))}


def retrieval_mechanics_report_markdown(report: dict[str, Any]) -> str:
    status = "PASS" if report.get("passed") is True else "FAIL"
    lines = [
        "# Retrieval Mechanics Gate A",
        "",
        f"- Status: `{status}`",
        f"- Cases: `{report.get('case_count', 0)}`",
        f"- Material clusters: `{report.get('material_cluster_count', 0)}`",
        f"- Runtime traces: `{report.get('runtime_trace_count', 0)}`",
        f"- Selected records: `{report.get('selected_record_count', 0)}`",
        f"- LLM-exposed records: `{report.get('llm_exposed_record_count', 0)}`",
        "- Offline-unexposed recovered: "
        f"`{report.get('offline_unexposed_recovered_count', 0)}`",
        "- Rare mechanisms recovered: "
        f"`{report.get('rare_mechanism_recovered_count', 0)}`",
        f"- Future records: `{report.get('future_record_count', 0)}`",
        f"- Blind web calls: `{report.get('web_call_count', 0)}`",
        f"- Online full scans: `{report.get('online_full_scan_count', 0)}`",
        f"- Memory snapshot: `{report.get('memory_snapshot_id', '')}`",
        "- Production activation: "
        f"`{report.get('production_activation_status', '')}`",
        "",
        "## Case Results",
        "",
        "| Trade date | Episode | Selected | Unexposed | Rare | Future | Web | Full scan |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("cases", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            "| {trade_date} | {episode_id} | {selected_record_count} | "
            "{offline_unexposed_recovered_count} | {rare_mechanism_recovered_count} | "
            "{future_record_count} | {web_call_count} | {online_full_scan_count} |".format(
                **row
            )
        )
    return "\n".join(lines) + "\n"


def _write_case_sources(
    root: Path,
    *,
    run_id: str,
    cluster_id: str,
    case: RetrievalMechanicsCase,
    cutoff_at: datetime,
    snapshot_manifest: Any,
) -> dict[str, Path]:
    base = root / RETRIEVAL_MECHANICS_ROOT / "cases" / run_id
    event_id = stable_id("MECH-EVENT", case.episode_id, case.query, length=20)
    source_id = stable_id("MECH-SOURCE", case.episode_id, length=20)
    signature = sha256_text(canonical_json([event_id]))
    event_row = {
        "schema_version": "nslab.news_event_cluster.v1",
        "run_id": run_id,
        "cluster_id": cluster_id,
        "cluster_index": 1,
        "cluster_method": "sealed_blind_gold_replay.v1",
        "cluster_key_sha256": signature,
        "row_numbers": [1],
        "event_ids": [event_id],
        "source_ids": [source_id],
        "row_count": 1,
        "exact_duplicate_count": 0,
        "semantic_duplicate_count": 0,
        "minimum_semantic_similarity": None,
        "disposition": "MATERIAL_FULL_RETRIEVAL",
        "eligible_for_blind_evidence": True,
        "first_published_at": datetime.combine(
            case.trade_date,
            datetime.min.time(),
            tzinfo=cutoff_at.tzinfo,
        ).isoformat(),
        "last_published_at_before_cutoff": datetime.combine(
            case.trade_date,
            datetime.min.time(),
            tzinfo=cutoff_at.tzinfo,
        ).isoformat(),
        "cutoff_at": as_kst(cutoff_at).isoformat(),
        "time_verified": True,
        "representative_title_sha256": sha256_text(case.query),
        "representative_body_sha256": sha256_text(case.query),
        "representative_title_excerpt": case.query[:240],
        "representative_body_excerpt": case.query[:600],
        "member_news_excerpts": [],
        "novelty": "sealed_blind_replay",
        "novelty_basis": "Original Gold blind prediction before outcome access.",
        "requires_llm_novelty_review": False,
    }
    event_rows_path = base / "event_clusters.jsonl"
    event_rows_path.parent.mkdir(parents=True, exist_ok=True)
    event_rows_path.write_text(canonical_json(event_row) + "\n", encoding="utf-8")
    news_manifest = NewsCoverageManifest(
        run_id=run_id,
        trade_date=case.trade_date,
        cutoff_at=cutoff_at,
        input_news_sha256=file_sha256(case.source_prediction_path),
        input_row_count=1,
        covered_row_count=1,
        missing_row_count=0,
        duplicate_assignment_count=0,
        disposition_counts={"MATERIAL_FULL_RETRIEVAL": 1},
        row_coverage_sha256=sha256_text(canonical_json([event_id, source_id])),
        rows=[
            NewsRowCoverage(
                row_number=1,
                event_id=event_id,
                source_id=source_id,
                primary_cluster_id=cluster_id,
                disposition="MATERIAL_FULL_RETRIEVAL",
            )
        ],
    )
    event_manifest = EventClusterManifest(
        run_id=run_id,
        trade_date=case.trade_date,
        cutoff_at=cutoff_at,
        clustering_version="sealed_blind_gold_replay.v1",
        embedding_provider="existing-production-memory-query",
        embedding_status="PROVIDER",
        embedding_model=snapshot_manifest.embedding_model,
        embedding_dimensions=snapshot_manifest.embedding_dimensions,
        embedding_fallback_policy="replay-derived",
        deterministic_fallback_used=False,
        production_runtime_identity=snapshot_manifest.embedding_model,
        embedding_batch_size=1,
        similarity_threshold=1.0,
        max_semantic_variants=1,
        input_row_count=1,
        cluster_count=1,
        material_cluster_count=1,
        unassigned_row_count=0,
        duplicate_assignment_count=0,
        clusters=[
            EventClusterEntry(
                cluster_id=cluster_id,
                representative_event_id=event_id,
                member_event_ids=[event_id],
                member_source_ids=[source_id],
                member_row_numbers=[1],
                disposition="MATERIAL_FULL_RETRIEVAL",
                cluster_signature_sha256=signature,
            )
        ],
    )
    news_path = base / "news_coverage_manifest.json"
    event_path = base / "event_cluster_manifest.json"
    write_json(news_path, news_manifest.model_dump(mode="json"))
    write_json(event_path, event_manifest.model_dump(mode="json"))
    source_hash_ref = snapshot_manifest.source_record_hashes
    memory_coverage = MemoryCoverageManifest(
        run_id=run_id,
        cutoff_at=cutoff_at,
        corpus_manifest_sha256=snapshot_manifest.corpus_manifest_sha256,
        accepted_record_count=snapshot_manifest.record_count,
        available_record_count=snapshot_manifest.record_count,
        future_record_count=0,
        missing_record_count=0,
        unexpected_record_count=0,
        duplicate_record_count=0,
        available_record_ids=ArtifactReference(
            artifact_path=source_hash_ref.artifact_path,
            sha256=source_hash_ref.sha256,
            item_count=source_hash_ref.item_count,
        ),
        record_hash_manifest=source_hash_ref,
        accepted_record_hash_manifest=source_hash_ref,
        coverage_complete=True,
    )
    memory_coverage_path = base / "memory_coverage_manifest.json"
    write_json(memory_coverage_path, memory_coverage.model_dump(mode="json"))
    return {
        "news_manifest": news_path,
        "event_manifest": event_path,
        "event_rows": event_rows_path,
        "memory_coverage": memory_coverage_path,
    }


def _blind_query(prediction: dict[str, Any]) -> str:
    rows = prediction.get("final_watchlist")
    if not isinstance(rows, list):
        rows = prediction.get("candidates")
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        values = [
            row.get("company"),
            row.get("company_name"),
            row.get("why_now"),
            row.get("thesis"),
        ]
        query = " | ".join(
            str(value).strip()
            for value in values
            if isinstance(value, str) and value.strip()
        )
        if query:
            return query[:2_400]
    return ""


def _case_features(record_counts: dict[str, int]) -> tuple[str, ...]:
    features = []
    if record_counts.get("supervised_direct_event_case", 0):
        features.append("DIRECT_EVENT")
    if sum(
        record_counts.get(name, 0)
        for name in ("supervised_theme_formation_case", "theme_formation_case")
    ):
        features.append("THEME_FORMATION")
    if record_counts.get("newsless_or_unexplained_case", 0):
        features.append("NEWSLESS")
    if record_counts.get("blind_leader_preference_pair", 0):
        features.append("LEADER_SELECTION")
    if sum(
        record_counts.get(name, 0)
        for name in (
            "candidate_generation_error_case",
            "candidate_ranking_error_case",
            "ranking_error_case",
        )
    ):
        features.append("CANDIDATE_ERROR")
    return tuple(sorted(features or ["GENERAL_GOLD"]))


def _case_manifest(root: Path, case: RetrievalMechanicsCase) -> dict[str, Any]:
    return {
        "episode_id": case.episode_id,
        "trade_date": case.trade_date.isoformat(),
        "next_trade_date": case.next_trade_date.isoformat(),
        "year": case.year,
        "features": list(case.features),
        "query_sha256": sha256_text(case.query),
        "source_index_path": relative_to_root(case.source_index_path, root),
        "source_index_sha256": file_sha256(case.source_index_path),
        "source_prediction_path": relative_to_root(case.source_prediction_path, root),
        "source_prediction_sha256": file_sha256(case.source_prediction_path),
        "source_outcome_path": relative_to_root(case.source_outcome_path, root),
        "source_outcome_sha256": file_sha256(case.source_outcome_path),
        "blind_valid": True,
        "outcome_universe_complete": True,
    }


def _sum_counts(rows: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        for key, value in row.items():
            result[key] = result.get(key, 0) + int(value)
    return dict(sorted(result.items()))


def _mechanics_report(
    *,
    cases: list[dict[str, Any]],
    snapshot_id: str,
    selection_manifest_path: Path,
    root: Path,
    cutoff_at: datetime,
) -> dict[str, Any]:
    material_clusters = sum(int(row["material_cluster_count"]) for row in cases)
    runtime_traces = sum(int(row["runtime_trace_count"]) for row in cases)
    passed = (
        len(cases) == RETRIEVAL_MECHANICS_CASE_COUNT
        and runtime_traces == material_clusters
        and all(row["trace_source_closure_100pct"] for row in cases)
        and all(row["future_record_count"] == 0 for row in cases)
        and all(row["web_call_count"] == 0 for row in cases)
        and all(row["online_full_scan_count"] == 0 for row in cases)
        and all(row["memory_snapshot_id"] == snapshot_id for row in cases)
    )
    return {
        "schema_version": RETRIEVAL_MECHANICS_VERSION,
        "gate": "A_EXISTING_RUNTIME_MECHANICS",
        "passed": passed,
        "cutoff_at": as_kst(cutoff_at).isoformat(),
        "case_count": len(cases),
        "material_cluster_count": material_clusters,
        "adaptive_trace_count": sum(int(row["adaptive_trace_count"]) for row in cases),
        "runtime_trace_count": runtime_traces,
        "selected_record_count": sum(int(row["selected_record_count"]) for row in cases),
        "llm_exposed_record_count": sum(int(row["llm_exposed_record_count"]) for row in cases),
        "offline_unexposed_recovered_count": sum(
            int(row["offline_unexposed_recovered_count"]) for row in cases
        ),
        "rare_mechanism_recovered_count": sum(
            int(row["rare_mechanism_recovered_count"]) for row in cases
        ),
        "future_record_count": sum(int(row["future_record_count"]) for row in cases),
        "web_call_count": sum(int(row["web_call_count"]) for row in cases),
        "online_full_scan_count": sum(
            int(row["online_full_scan_count"]) for row in cases
        ),
        "memory_snapshot_id": snapshot_id,
        "selection_manifest": {
            "artifact_path": relative_to_root(selection_manifest_path, root),
            "sha256": file_sha256(selection_manifest_path),
        },
        "cases": cases,
        "production_activation_status": "NOT_PRODUCTION_ACTIVATED",
    }
