"""Resumable paired shadow runs for legacy and retrieval-first runtime variants."""

from __future__ import annotations

import csv
import json
import random
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.memory_context import DailyMemoryContext
from news_scalping_lab.contracts.models import BlindPrediction, DailyAnalysis
from news_scalping_lab.contracts.runtime_retrieval import RuntimeRetrievalTrace
from news_scalping_lab.evaluation.shadow import SHADOW_DAILY_P95_BUDGET_MS
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.memory.index import active_memory_snapshot_manifest
from news_scalping_lab.retrieval.production_embedding import (
    create_configured_embedding_provider,
)
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.utils import (
    file_sha256,
    now_kst,
    parse_datetime,
    read_json,
    sha256_text,
    write_json,
)

RUNTIME_VARIANT_SHADOW_VERSION = "nslab.runtime_variant_shadow.v1"
RUNTIME_VARIANT_SHADOW_ROOT = Path("runs/semantic_brain_upgrade/runtime_variant_shadow")
RuntimeVariantId = Literal["V0", "V1"]
_MARKET_METRIC_KEYS = (
    "recall_at_20",
    "precision_at_20",
    "mrr",
    "brier",
    "final_memory_citation_rate",
    "upper_limit_recall_at_5",
    "upper_limit_recall_at_10",
    "upper_limit_recall_at_20",
    "high20_recall_at_5",
    "high20_recall_at_10",
    "high20_recall_at_20",
    "high10_recall_at_5",
    "high10_recall_at_10",
    "high10_recall_at_20",
    "high20_precision_at_5",
    "high20_precision_at_10",
    "high20_precision_at_20",
    "leader_upper_limit_hit",
    "leader_high20_hit",
    "calibration_absolute_error",
)


@dataclass(frozen=True)
class RuntimeVariantShadowResult:
    report: dict[str, Any]
    report_path: Path
    progress_path: Path


async def run_runtime_variant_shadow(
    root: Path,
    *,
    settings: Settings,
    selection_path: Path,
    split: Literal["CALIBRATION", "HOLDOUT"] = "CALIBRATION",
    case_limit: int | None = None,
) -> RuntimeVariantShadowResult:
    """Run paired variants with identical news/truth and persist after every call."""

    root = root.resolve()
    selection_path = selection_path.resolve()
    selection = _read_selection(selection_path)
    split_cases = [item for item in selection["cases"] if item.get("split") == split]
    cases = list(split_cases)
    if case_limit is not None:
        if case_limit < 1:
            raise ValueError("shadow case limit must be positive")
        cases = cases[:case_limit]
    if not cases:
        raise ValueError(f"semantic upgrade split has no {split} cases")
    brain_path = root / "brain" / "current" / "brain_manifest.json"
    brain = read_json(brain_path)
    if not isinstance(brain, dict) or brain.get("build_mode") != "llm-full":
        raise ValueError("runtime variant shadow requires an llm-full evaluation brain")
    snapshot_id = brain.get("production_memory_snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("evaluation brain is missing its BUILD memory snapshot")
    coverage = read_json(root / "brain" / "current" / "coverage_manifest.json")
    if not isinstance(coverage, dict) or coverage.get("coverage_scope") != "EVALUATION_REPLAY_BUILD":
        raise ValueError("runtime variant shadow requires evaluation BUILD coverage")
    active_snapshot = active_memory_snapshot_manifest(root)
    if active_snapshot is None or active_snapshot.snapshot_id != snapshot_id or not active_snapshot.evaluation_only:
        raise ValueError("runtime variant shadow requires an evaluation-only snapshot")
    identity = {
        "schema_version": RUNTIME_VARIANT_SHADOW_VERSION,
        "selection_sha256": file_sha256(selection_path),
        "split": split,
        "evaluation_scope": ("FORMAL_SPLIT" if len(cases) == len(split_cases) else "SMOKE"),
        "formal_split_case_count": len(split_cases),
        "case_limit": case_limit,
        "case_ids": [str(item["episode_id"]) for item in cases],
        "brain_manifest_sha256": file_sha256(brain_path),
        "memory_snapshot_id": snapshot_id,
        "variants": ["V0", "V1"],
        "common_supporting_vector_search": "DISABLED_FORCE_EMPTY",
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm.model,
        "llm_reasoning_effort": settings.llm.reasoning_effort,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.local_embedding_model,
        "embedding_revision": settings.local_embedding_revision,
        "evidence_policy": settings.evidence_policy.value,
        "web_provider": settings.web_provider,
    }
    run_id = (
        "RVSHADOW-"
        + sha256_text(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")))[:20].upper()
    )
    output_dir = root / RUNTIME_VARIANT_SHADOW_ROOT / run_id
    progress_path = output_dir / "progress.json"
    progress = _load_progress(progress_path, identity=identity, run_id=run_id)

    base_llm = create_llm_provider(settings)
    embedding_provider = create_configured_embedding_provider(
        settings,
        production=True,
        llm_provider=base_llm,
    )
    retrieval = LocalRetrievalStore(root, force_empty=True)
    analyzers = {
        "V0": DailyAnalyzer(
            settings,
            llm=base_llm,
            retrieval=retrieval,
            embedding_provider=embedding_provider,
            runtime_retrieval_variant="legacy",
        ),
        "V1": DailyAnalyzer(
            settings,
            llm=base_llm,
            retrieval=retrieval,
            embedding_provider=embedding_provider,
            runtime_retrieval_variant="v4",
        ),
    }
    completed = {(str(row["case_id"]), str(row["variant_id"])) for row in progress["observations"]}
    for case in cases:
        case_id = str(case["episode_id"])
        news_csv, news_sha256, cutoff_at = _materialize_case_news(
            root,
            output_dir=output_dir,
            case=case,
        )
        truth_path = _artifact_path(root, case["outcome_ledger"])
        _verify_reference(case["outcome_ledger"], truth_path)
        for variant_id in ("V0", "V1"):
            if (case_id, variant_id) in completed:
                continue
            runtime_before = _runtime_counter_snapshot(
                root,
                llm=base_llm,
                embedding_provider=embedding_provider,
            )
            started = time.perf_counter()
            analysis = await analyzers[variant_id].analyze(
                news_csv=news_csv,
                trade_date=parse_datetime(cutoff_at).date(),
                cutoff_at=parse_datetime(cutoff_at),
                mode="exhaustive",
                web_search=False,
            )
            elapsed = time.perf_counter() - started
            runtime_metrics = _runtime_counter_delta(
                root,
                before=runtime_before,
                llm=base_llm,
                embedding_provider=embedding_provider,
            )
            observation = _observation(
                root,
                case=case,
                variant_id=variant_id,
                news_sha256=news_sha256,
                truth_path=truth_path,
                analysis=analysis,
                elapsed_seconds=elapsed,
                memory_snapshot_id=snapshot_id,
                runtime_metrics=runtime_metrics,
            )
            progress["observations"].append(observation)
            progress["completed_observation_count"] = len(progress["observations"])
            write_json(progress_path, progress)
            completed.add((case_id, variant_id))

    report = _build_report(progress, expected_case_count=len(cases))
    report_path = output_dir / "runtime_variant_shadow_report.json"
    write_json(report_path, report)
    (output_dir / "runtime_variant_shadow_report.md").write_text(
        _render_report(report),
        encoding="utf-8",
    )
    return RuntimeVariantShadowResult(
        report=report,
        report_path=report_path,
        progress_path=progress_path,
    )


def _read_selection(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "nslab.semantic_upgrade_split_selection.v1"
        or not isinstance(payload.get("cases"), list)
    ):
        raise ValueError("semantic upgrade selection artifact is invalid")
    return payload


def _load_progress(
    path: Path,
    *,
    identity: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": RUNTIME_VARIANT_SHADOW_VERSION,
            "run_id": run_id,
            "identity": identity,
            "observations": [],
            "completed_observation_count": 0,
            "production_activation_status": "NOT_PRODUCTION_ACTIVATED",
        }
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("identity") != identity:
        raise ValueError("existing runtime shadow progress has a different identity")
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError("runtime shadow progress observations are invalid")
    return payload


def _materialize_case_news(
    root: Path,
    *,
    output_dir: Path,
    case: dict[str, Any],
) -> tuple[Path, str, str]:
    case_id = str(case["episode_id"])
    index_path = _artifact_path(root, case["normalized_index"])
    source_path = _artifact_path(root, case["source_ledger"])
    _verify_reference(case["normalized_index"], index_path)
    _verify_reference(case["source_ledger"], source_path)
    index = read_json(index_path)
    if not isinstance(index, dict) or not isinstance(index.get("cutoff_at"), str):
        raise ValueError(f"shadow case index is invalid: {case_id}")
    cutoff_at = str(index["cutoff_at"])
    rows: list[dict[str, str]] = []
    with source_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("source ledger row must be an object")
            if item.get("available_before_cutoff") is not True:
                continue
            published = str(item.get("published_at_kst", ""))
            if "T" not in published:
                raise ValueError("source ledger row has no KST publication time")
            row_date, row_time = published.split("T", 1)
            rows.append(
                {
                    "date": row_date,
                    "time": row_time,
                    "title": str(item.get("title", "")),
                    "body": str(item.get("body", "")),
                }
            )
    if not rows:
        raise ValueError(f"shadow case has no cutoff-safe news: {case_id}")
    csv_path = output_dir / "inputs" / f"{case_id}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "time", "title", "body"])
            writer.writeheader()
            writer.writerows(rows)
    receipt_path = csv_path.with_suffix(".receipt.json")
    receipt = {
        "schema_version": "nslab.shadow_news_reconstruction.v1",
        "episode_id": case_id,
        "source_ledger_sha256": file_sha256(source_path),
        "normalized_index_sha256": file_sha256(index_path),
        "news_csv_sha256": file_sha256(csv_path),
        "row_count": len(rows),
        "cutoff_at": cutoff_at,
    }
    if receipt_path.exists() and read_json(receipt_path) != receipt:
        raise ValueError(f"shadow news reconstruction drifted: {case_id}")
    write_json(receipt_path, receipt)
    return csv_path, str(receipt["news_csv_sha256"]), cutoff_at


def _artifact_path(root: Path, reference: Any) -> Path:
    if not isinstance(reference, dict) or not isinstance(reference.get("artifact_path"), str):
        raise ValueError("shadow artifact reference is invalid")
    path = Path(str(reference["artifact_path"]))
    return (path if path.is_absolute() else root / path).resolve()


def _verify_reference(reference: Any, path: Path) -> None:
    if not path.is_file() or not isinstance(reference, dict) or file_sha256(path) != reference.get("sha256"):
        raise ValueError(f"shadow source artifact failed hash verification: {path}")


def _observation(
    root: Path,
    *,
    case: dict[str, Any],
    variant_id: RuntimeVariantId,
    news_sha256: str,
    truth_path: Path,
    analysis: DailyAnalysis,
    elapsed_seconds: float,
    memory_snapshot_id: str,
    runtime_metrics: dict[str, Any],
) -> dict[str, Any]:
    prediction = analysis.blind_prediction
    manifest = analysis.context_manifest
    if manifest.blind_web_search_call_count or manifest.external_web_evidence_count:
        raise ValueError("runtime variant shadow attempted BLIND web access")
    trace_stats = _trace_stats(root, manifest)
    if trace_stats["memory_snapshot_id"] != memory_snapshot_id:
        raise ValueError("runtime variant shadow used the wrong BUILD snapshot")
    metrics = _prediction_metrics(prediction, truth_path)
    general_citation_rate = _final_memory_citation_rate(prediction)
    runtime_citation_rate = _runtime_final_candidate_citation_rate(
        prediction,
        trace_stats=trace_stats,
    )
    metrics["general_memory_citation_rate"] = general_citation_rate
    metrics["final_memory_citation_rate"] = runtime_citation_rate if variant_id == "V1" else general_citation_rate
    prediction_artifact = manifest.prediction_artifact
    if not prediction_artifact:
        raise ValueError("runtime variant shadow prediction artifact is missing")
    return {
        "case_id": str(case["episode_id"]),
        "trade_date": str(case["trade_date"]),
        "split": str(case["split"]),
        "variant_id": variant_id,
        "runtime_retrieval_variant": ("legacy" if variant_id == "V0" else "v4"),
        "news_sha256": news_sha256,
        "truth_sha256": file_sha256(truth_path),
        "prediction_sha256": file_sha256(root / prediction_artifact),
        "context_manifest_sha256": file_sha256(root / "runs" / "manifests" / f"{manifest.run_id}.json"),
        "pre_retrieval_identity": {
            "event_clustering_result_sha256": (manifest.event_clustering_result_sha256),
            "open_world_first_analysis_sha256": (manifest.open_world_first_analysis_sha256),
            "news_novelty_review_sha256": manifest.news_novelty_review_sha256,
            "open_world_prompt_sha256": manifest.prompt_hashes.get("open_world_first_analysis"),
            "news_novelty_prompt_sha256": manifest.prompt_hashes.get("news_novelty_review"),
        },
        "run_id": manifest.run_id,
        "memory_snapshot_id": trace_stats["memory_snapshot_id"],
        "blind_web_call_count": manifest.blind_web_search_call_count,
        "future_record_count": trace_stats["future_record_count"],
        "online_full_scan_count": trace_stats["online_full_scan_count"],
        "adaptive_trace_count": trace_stats["adaptive_trace_count"],
        "runtime_trace_count": trace_stats["runtime_trace_count"],
        "selected_record_count": trace_stats["selected_record_count"],
        "offline_unexposed_recovered_count": trace_stats["offline_unexposed_recovered_count"],
        "offline_unexposed_candidate_count": trace_stats["offline_unexposed_candidate_count"],
        "offline_unexposed_llm_exposed_count": trace_stats["offline_unexposed_llm_exposed_count"],
        "offline_unexposed_final_cited_count": trace_stats["offline_unexposed_final_cited_count"],
        "rare_mechanism_recovered_count": trace_stats["rare_mechanism_recovered_count"],
        "final_cited_record_count": trace_stats["final_cited_record_count"],
        "runtime_final_cited_candidate_count": len(trace_stats["runtime_final_candidate_ids"]),
        "selected_independent_unit_count": len(trace_stats["selected_independent_unit_ids"]),
        "selected_year_counts": dict(trace_stats["selected_year_counts"]),
        "issuer_day_duplicate_rate": (
            1.0 - len(trace_stats["selected_independent_unit_ids"]) / trace_stats["selected_record_count"]
            if trace_stats["selected_record_count"]
            else 0.0
        ),
        "lane_selected_counts": trace_stats["lane_selected_counts"],
        "elapsed_seconds": elapsed_seconds,
        "prompt_token_count": sum(manifest.token_counts.values()),
        "runtime_metrics": runtime_metrics,
        "metrics": metrics,
    }


def _runtime_counter_snapshot(
    root: Path,
    *,
    llm: Any,
    embedding_provider: Any,
) -> dict[str, Any]:
    identity_reader = getattr(llm, "identity", None)
    identity = identity_reader() if callable(identity_reader) else {}
    if not isinstance(identity, dict):
        identity = {}
    trace_dir = root / "runs" / "traces"
    return {
        "oauth_live_agent_call_count": int(identity.get("live_agent_call_count") or 0),
        "oauth_cache_event_count": int(identity.get("cache_hit_count") or 0),
        "embedding_query_count": int(getattr(embedding_provider, "embedding_query_count", 0) or 0),
        "embedding_text_count": int(getattr(embedding_provider, "embedding_text_count", 0) or 0),
        "embedding_input_char_count": int(getattr(embedding_provider, "embedding_input_char_count", 0) or 0),
        "trace_files": {path.resolve().as_posix() for path in trace_dir.glob("*.json") if path.is_file()},
        "process_memory": _process_memory_snapshot(),
        "captured_at": now_kst().isoformat(),
    }


def _runtime_counter_delta(
    root: Path,
    *,
    before: dict[str, Any],
    llm: Any,
    embedding_provider: Any,
) -> dict[str, Any]:
    after = _runtime_counter_snapshot(
        root,
        llm=llm,
        embedding_provider=embedding_provider,
    )
    new_trace_paths = sorted(set(after["trace_files"]) - set(before["trace_files"]))
    trace_counts: Counter[str] = Counter()
    prompt_tokens = 0
    completion_tokens = 0
    trace_started_at = []
    for path_text in new_trace_paths:
        payload = read_json(Path(path_text))
        if not isinstance(payload, dict):
            continue
        operation = str(payload.get("operation") or "unknown")
        status = str(payload.get("status") or "unknown")
        started_at = payload.get("started_at")
        if isinstance(started_at, str):
            trace_started_at.append(parse_datetime(started_at))
        trace_counts[f"operation:{operation}"] += 1
        trace_counts[f"status:{status}"] += 1
        token_usage = payload.get("token_usage")
        if isinstance(token_usage, dict):
            prompt_tokens += int(token_usage.get("prompt_tokens_estimate") or 0)
            completion_tokens += int(token_usage.get("completion_tokens_estimate") or 0)
    before_memory = before.get("process_memory")
    after_memory = after.get("process_memory")
    captured_at = before.get("captured_at")
    pre_llm_latency_seconds = None
    if isinstance(captured_at, str) and trace_started_at:
        pre_llm_latency_seconds = max(
            0.0,
            (min(trace_started_at) - parse_datetime(captured_at)).total_seconds(),
        )
    return {
        "logical_llm_call_count": sum(
            count
            for key, count in trace_counts.items()
            if key in {"operation:generate_text", "operation:generate_structured"}
        ),
        "oauth_live_agent_call_count": (
            int(after["oauth_live_agent_call_count"]) - int(before["oauth_live_agent_call_count"])
        ),
        "llm_checkpoint_hit_count": int(trace_counts["status:checkpoint_hit"]),
        "oauth_cache_event_count": (int(after["oauth_cache_event_count"]) - int(before["oauth_cache_event_count"])),
        "llm_prompt_tokens_estimate": prompt_tokens,
        "llm_completion_tokens_estimate": completion_tokens,
        "embedding_query_count": (int(after["embedding_query_count"]) - int(before["embedding_query_count"])),
        "embedding_text_count": (int(after["embedding_text_count"]) - int(before["embedding_text_count"])),
        "embedding_input_char_count": (
            int(after["embedding_input_char_count"]) - int(before["embedding_input_char_count"])
        ),
        "new_llm_trace_count": len(new_trace_paths),
        "process_rss_before_bytes": _memory_value(before_memory, "rss_bytes"),
        "process_rss_after_bytes": _memory_value(after_memory, "rss_bytes"),
        "process_peak_working_set_bytes": _memory_value(
            after_memory,
            "peak_working_set_bytes",
        ),
        "peak_memory_scope": "PROCESS_LIFETIME" if after_memory else "UNAVAILABLE",
        "pre_llm_latency_seconds": pre_llm_latency_seconds,
        "pre_llm_latency_status": (
            "MEASURED_TO_FIRST_LLM_TRACE" if pre_llm_latency_seconds is not None else "UNAVAILABLE_NO_LLM_TRACE"
        ),
    }


def _process_memory_snapshot() -> dict[str, int] | None:
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None
    memory = psutil.Process().memory_info()
    peak = getattr(memory, "peak_wset", None)
    return {
        "rss_bytes": int(memory.rss),
        "peak_working_set_bytes": int(peak) if isinstance(peak, int) else int(memory.rss),
    }


def _memory_value(value: Any, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    observed = value.get(key)
    return int(observed) if isinstance(observed, int) else None


def _trace_stats(root: Path, manifest: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "adaptive_trace_count": 0,
        "runtime_trace_count": 0,
        "selected_record_count": 0,
        "offline_unexposed_recovered_count": 0,
        "offline_unexposed_candidate_count": 0,
        "offline_unexposed_llm_exposed_count": 0,
        "offline_unexposed_final_cited_count": 0,
        "rare_mechanism_recovered_count": 0,
        "final_cited_record_count": 0,
        "future_record_count": 0,
        "online_full_scan_count": 0,
        "lane_selected_counts": {},
        "runtime_final_candidate_ids": set(),
        "selected_independent_unit_ids": set(),
        "selected_year_counts": Counter(),
        "memory_snapshot_id": None,
    }
    artifact = manifest.daily_memory_context_artifact
    if not artifact:
        return base
    context = DailyMemoryContext.model_validate(read_json(root / artifact))
    base["memory_snapshot_id"] = context.memory_snapshot_id
    base["adaptive_trace_count"] = len(context.adaptive_retrieval_traces)
    base["runtime_trace_count"] = len(context.runtime_retrieval_traces)
    lanes: Counter[str] = Counter()
    for reference in context.runtime_retrieval_traces:
        trace = RuntimeRetrievalTrace.model_validate(read_json(root / reference.artifact_path))
        base["offline_unexposed_recovered_count"] += trace.offline_unexposed_recovered_count
        base["offline_unexposed_llm_exposed_count"] += trace.offline_unexposed_llm_exposed_count
        base["offline_unexposed_final_cited_count"] += trace.offline_unexposed_final_cited_count
        base["rare_mechanism_recovered_count"] += trace.rare_mechanism_recovered_count
        base["online_full_scan_count"] += trace.online_full_scan_count
        lanes.update(trace.lane_selected_counts)
        for row in trace.rows:
            base["offline_unexposed_candidate_count"] += row.offline_payload_exposed is False
            base["selected_record_count"] += "LANE_SELECTED" in row.stages
            if "LANE_SELECTED" in row.stages:
                base["selected_independent_unit_ids"].add(row.independent_unit_id)
                base["selected_year_counts"][str(row.source_trade_date.year)] += 1
            base["final_cited_record_count"] += "FINAL_CITED" in row.stages
            base["runtime_final_candidate_ids"].update(row.final_candidate_ids)
            base["future_record_count"] += (
                row.replay_available_from or row.available_from
            ) > manifest.cutoff_at or row.source_trade_date >= manifest.trade_date
    base["lane_selected_counts"] = dict(sorted(lanes.items()))
    summary = manifest.daily_memory_context_summary
    if isinstance(summary, dict):
        base["final_cited_record_count"] = int(summary.get("runtime_final_cited_record_count") or 0)
    return base


def _prediction_metrics(prediction: BlindPrediction, truth_path: Path) -> dict[str, Any]:
    outcomes: dict[str, dict[str, Any]] = {}
    with truth_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                outcomes[str(row["ticker"]).zfill(6)] = row
    high20 = {
        ticker
        for ticker, row in outcomes.items()
        if float(row.get("high_return_pct") or 0.0) >= 20.0 or row.get("upper_limit_touched") is True
    }
    high10 = {
        ticker
        for ticker, row in outcomes.items()
        if float(row.get("high_return_pct") or 0.0) >= 10.0 or row.get("upper_limit_touched") is True
    }
    upper_limit = {ticker for ticker, row in outcomes.items() if row.get("upper_limit_touched") is True}
    ranked = [item.ticker.zfill(6) for item in prediction.candidates[:20]]
    hits = [ticker for ticker in ranked if ticker in high20]
    reciprocal_rank = next(
        (1.0 / rank for rank, ticker in enumerate(ranked, start=1) if ticker in high20),
        0.0,
    )
    probabilities = {
        "very_high": 0.9,
        "high": 0.75,
        "medium": 0.5,
        "low": 0.25,
        "speculative": 0.1,
    }
    brier_terms = [
        (probabilities.get(str(item.confidence_label).lower(), 0.5) - float(item.ticker.zfill(6) in high20)) ** 2
        for item in prediction.candidates[:20]
    ]
    calibration_pairs = [
        {
            "probability": probabilities.get(str(item.confidence_label).lower(), 0.5),
            "outcome": float(item.ticker.zfill(6) in high20),
        }
        for item in prediction.candidates[:20]
    ]
    metrics: dict[str, Any] = {
        "recall_at_20": _recall_at(ranked, high20, 20),
        "precision_at_20": len(hits) / len(ranked) if ranked else 0.0,
        "mrr": reciprocal_rank,
        "brier": sum(brier_terms) / len(brier_terms) if brier_terms else 1.0,
        "leader_upper_limit_hit": float(bool(ranked) and ranked[0] in upper_limit),
        "leader_high20_hit": float(bool(ranked) and ranked[0] in high20),
        "calibration_absolute_error": (
            abs(
                sum(float(row["probability"]) for row in calibration_pairs) / len(calibration_pairs)
                - sum(float(row["outcome"]) for row in calibration_pairs) / len(calibration_pairs)
            )
            if calibration_pairs
            else 1.0
        ),
        "calibration_pairs": calibration_pairs,
        "upper_limit_target_count": len(upper_limit),
        "high20_target_count": len(high20),
        "high10_target_count": len(high10),
    }
    for cutoff in (5, 10, 20):
        metrics[f"upper_limit_hit_count_at_{cutoff}"] = len(set(ranked[:cutoff]).intersection(upper_limit))
        metrics[f"high20_hit_count_at_{cutoff}"] = len(set(ranked[:cutoff]).intersection(high20))
        metrics[f"high10_hit_count_at_{cutoff}"] = len(set(ranked[:cutoff]).intersection(high10))
        metrics[f"selected_count_at_{cutoff}"] = len(ranked[:cutoff])
        metrics[f"upper_limit_recall_at_{cutoff}"] = _recall_at(ranked, upper_limit, cutoff)
        metrics[f"high20_recall_at_{cutoff}"] = _recall_at(ranked, high20, cutoff)
        metrics[f"high10_recall_at_{cutoff}"] = _recall_at(ranked, high10, cutoff)
        metrics[f"high20_precision_at_{cutoff}"] = _precision_at(ranked, high20, cutoff)
    return metrics


def _recall_at(ranked: list[str], target: set[str], cutoff: int) -> float:
    if not target:
        return 1.0
    return len(set(ranked[:cutoff]).intersection(target)) / len(target)


def _precision_at(ranked: list[str], target: set[str], cutoff: int) -> float:
    selected = ranked[:cutoff]
    if not selected:
        return 0.0
    return len([ticker for ticker in selected if ticker in target]) / len(selected)


def _final_memory_citation_rate(prediction: BlindPrediction) -> float:
    claims = [
        bool(candidate.memory_record_ids or candidate.prior_positive_record_ids or candidate.prior_negative_record_ids)
        for candidate in prediction.candidates
    ]
    return sum(claims) / len(claims) if claims else 1.0


def _runtime_final_candidate_citation_rate(
    prediction: BlindPrediction,
    *,
    trace_stats: dict[str, Any],
) -> float:
    expected = {f"candidate:{candidate.rank}:{candidate.ticker}" for candidate in prediction.candidates}
    observed = trace_stats.get("runtime_final_candidate_ids")
    cited = set(observed) if isinstance(observed, set) else set()
    return len(expected.intersection(cited)) / len(expected) if expected else 1.0


def _build_report(progress: dict[str, Any], *, expected_case_count: int) -> dict[str, Any]:
    observations = list(progress["observations"])
    by_variant = {variant: [row for row in observations if row["variant_id"] == variant] for variant in ("V0", "V1")}
    paired = len(by_variant["V0"]) == len(by_variant["V1"]) == expected_case_count
    formal_split_case_count = int(progress.get("identity", {}).get("formal_split_case_count") or expected_case_count)
    formal_split_closed = paired and expected_case_count == formal_split_case_count
    paired_rows: dict[str, dict[str, dict[str, Any]]] = {}
    for row in observations:
        paired_rows.setdefault(str(row["case_id"]), {})[str(row["variant_id"])] = row
    pre_retrieval_identity_match = paired and all(
        rows["V0"].get("pre_retrieval_identity") == rows["V1"].get("pre_retrieval_identity")
        for rows in paired_rows.values()
        if set(rows) >= {"V0", "V1"}
    )
    paired_source_identity_match = paired and all(
        tuple(rows["V0"].get(key) for key in ("news_sha256", "truth_sha256", "memory_snapshot_id"))
        == tuple(rows["V1"].get(key) for key in ("news_sha256", "truth_sha256", "memory_snapshot_id"))
        for rows in paired_rows.values()
        if set(rows) >= {"V0", "V1"}
    )

    def mean(rows: list[dict[str, Any]], key: str) -> float | None:
        values = [float(row["metrics"][key]) for row in rows if key in row["metrics"]]
        return sum(values) / len(values) if values else None

    metrics = {
        variant: {
            **{key: mean(rows, key) for key in _MARKET_METRIC_KEYS},
            "expected_calibration_error": _expected_calibration_error(rows),
            **_aggregate_micro_market_metrics(rows),
        }
        for variant, rows in by_variant.items()
    }
    future = sum(int(row["future_record_count"]) for row in observations)
    web = sum(int(row["blind_web_call_count"]) for row in observations)
    full_scan = sum(int(row["online_full_scan_count"]) for row in observations)
    v0_recovered = sum(int(row["offline_unexposed_recovered_count"]) for row in by_variant["V0"])
    recovered = sum(int(row["offline_unexposed_recovered_count"]) for row in by_variant["V1"])
    recovered_delta = recovered - v0_recovered
    variant_runtime: dict[str, dict[str, Any]] = {}
    for variant, rows in by_variant.items():
        elapsed = sorted(float(row["elapsed_seconds"]) for row in rows)
        lanes: Counter[str] = Counter()
        for row in rows:
            lanes.update(row["lane_selected_counts"])
        runtime_metrics = [row.get("runtime_metrics") for row in rows if isinstance(row.get("runtime_metrics"), dict)]
        pre_llm_latency = sorted(
            float(item["pre_llm_latency_seconds"])
            for item in runtime_metrics
            if isinstance(item.get("pre_llm_latency_seconds"), (int, float))
        )
        logical_calls = _sum_runtime_metric(
            runtime_metrics,
            "logical_llm_call_count",
        )
        checkpoint_hits = _sum_runtime_metric(
            runtime_metrics,
            "llm_checkpoint_hit_count",
        )
        years: Counter[str] = Counter()
        for row in rows:
            years.update(row.get("selected_year_counts") or {})
        selected_records = sum(int(row["selected_record_count"]) for row in rows)
        selected_units = sum(int(row.get("selected_independent_unit_count") or 0) for row in rows)
        offline_candidates = sum(int(row.get("offline_unexposed_candidate_count") or 0) for row in rows)
        offline_selected = sum(int(row["offline_unexposed_recovered_count"]) for row in rows)
        offline_llm_exposed = sum(int(row.get("offline_unexposed_llm_exposed_count") or 0) for row in rows)
        offline_final_cited = sum(int(row.get("offline_unexposed_final_cited_count") or 0) for row in rows)
        variant_runtime[variant] = {
            "adaptive_trace_count": sum(int(row["adaptive_trace_count"]) for row in rows),
            "runtime_trace_count": sum(int(row["runtime_trace_count"]) for row in rows),
            "selected_record_count": sum(int(row["selected_record_count"]) for row in rows),
            "selected_independent_unit_count": selected_units,
            "selected_year_counts": dict(sorted(years.items())),
            "selected_year_count": len(years),
            "issuer_day_duplicate_rate": (1.0 - selected_units / selected_records if selected_records else 0.0),
            "offline_unexposed_recovered_count": offline_selected,
            "offline_unexposed_candidate_count": offline_candidates,
            "offline_unexposed_selection_rate": (offline_selected / offline_candidates if offline_candidates else None),
            "offline_unexposed_llm_exposed_count": offline_llm_exposed,
            "offline_unexposed_runtime_exposure_rate": (
                offline_llm_exposed / offline_selected if offline_selected else None
            ),
            "offline_unexposed_final_cited_count": offline_final_cited,
            "offline_unexposed_final_citation_rate": (
                offline_final_cited / offline_selected if offline_selected else None
            ),
            "rare_mechanism_recovered_count": sum(int(row["rare_mechanism_recovered_count"]) for row in rows),
            "final_cited_record_count": sum(int(row["final_cited_record_count"]) for row in rows),
            "runtime_final_cited_candidate_count": sum(
                int(row.get("runtime_final_cited_candidate_count") or 0) for row in rows
            ),
            "lane_selected_counts": dict(sorted(lanes.items())),
            "mean_elapsed_seconds": (sum(elapsed) / len(elapsed) if elapsed else None),
            "p95_elapsed_seconds": _percentile(elapsed, 0.95),
            "total_prompt_token_count": sum(int(row["prompt_token_count"]) for row in rows),
            "logical_llm_call_count": logical_calls,
            "oauth_live_agent_call_count": _sum_runtime_metric(
                runtime_metrics,
                "oauth_live_agent_call_count",
            ),
            "llm_checkpoint_hit_count": checkpoint_hits,
            "llm_checkpoint_hit_rate": (checkpoint_hits / logical_calls if logical_calls else None),
            "oauth_cache_event_count": _sum_runtime_metric(
                runtime_metrics,
                "oauth_cache_event_count",
            ),
            "llm_prompt_tokens_estimate": _sum_runtime_metric(
                runtime_metrics,
                "llm_prompt_tokens_estimate",
            ),
            "llm_completion_tokens_estimate": _sum_runtime_metric(
                runtime_metrics,
                "llm_completion_tokens_estimate",
            ),
            "embedding_query_count": _sum_runtime_metric(
                runtime_metrics,
                "embedding_query_count",
            ),
            "embedding_text_count": _sum_runtime_metric(
                runtime_metrics,
                "embedding_text_count",
            ),
            "embedding_input_char_count": _sum_runtime_metric(
                runtime_metrics,
                "embedding_input_char_count",
            ),
            "process_peak_working_set_bytes": max(
                (
                    int(item["process_peak_working_set_bytes"])
                    for item in runtime_metrics
                    if isinstance(item.get("process_peak_working_set_bytes"), int)
                ),
                default=None,
            ),
            "peak_memory_scope": ("PROCESS_LIFETIME" if runtime_metrics else "UNAVAILABLE"),
            "pre_llm_latency_p50_seconds": _percentile(pre_llm_latency, 0.50),
            "pre_llm_latency_p95_seconds": _percentile(pre_llm_latency, 0.95),
            "pre_llm_latency_status": (
                "MEASURED_TO_FIRST_LLM_TRACE"
                if len(pre_llm_latency) == len(rows) and rows
                else "PARTIAL_OR_UNAVAILABLE"
            ),
        }
    gate_checks = {
        "paired_case_closure": paired,
        "formal_split_case_closure": formal_split_closed,
        "paired_source_identity_match": paired_source_identity_match,
        "paired_pre_retrieval_identity_match": pre_retrieval_identity_match,
        "future_leak_zero": future == 0,
        "blind_web_zero": web == 0,
        "online_full_scan_zero": full_scan == 0,
        "offline_unexposed_recovery_increased_vs_v0": recovered_delta > 0,
        "adaptive_trace_every_v1_case": bool(by_variant["V1"])
        and all(int(row["adaptive_trace_count"]) > 0 for row in by_variant["V1"]),
        "runtime_trace_every_v1_case": bool(by_variant["V1"])
        and all(int(row["runtime_trace_count"]) > 0 for row in by_variant["V1"]),
        "negative_or_counterexample_inclusion_nonzero": sum(
            int(variant_runtime["V1"]["lane_selected_counts"].get(lane, 0))
            for lane in ("NEGATIVE_CONTROL", "COUNTEREXAMPLE", "NEAR_MISS")
        )
        > 0,
        "final_memory_citation_at_least_95pct": (
            metrics["V1"]["final_memory_citation_rate"] is not None
            and float(metrics["V1"]["final_memory_citation_rate"]) >= 0.95
        ),
        "daily_p95_within_phase8_budget": (
            variant_runtime["V1"]["p95_elapsed_seconds"] is not None
            and float(variant_runtime["V1"]["p95_elapsed_seconds"]) <= SHADOW_DAILY_P95_BUDGET_MS / 1000.0
        ),
        "known_relevant_recall_labeled": False,
    }
    paired_bootstrap = _paired_bootstrap(
        observations,
        run_id=str(progress["run_id"]),
    )
    return {
        "schema_version": RUNTIME_VARIANT_SHADOW_VERSION,
        "run_id": progress["run_id"],
        "identity": progress["identity"],
        "expected_case_count": expected_case_count,
        "formal_split_case_count": formal_split_case_count,
        "evaluation_scope": progress.get("identity", {}).get(
            "evaluation_scope",
            "UNKNOWN",
        ),
        "completed_observation_count": len(observations),
        "paired_case_count": min(len(by_variant["V0"]), len(by_variant["V1"])),
        "variant_metrics": metrics,
        "variant_runtime": variant_runtime,
        "paired_bootstrap": paired_bootstrap,
        "v0_offline_unexposed_recovered_count": v0_recovered,
        "v1_offline_unexposed_recovered_count": recovered,
        "offline_unexposed_recovered_delta": recovered_delta,
        "future_record_count": future,
        "blind_web_call_count": web,
        "online_full_scan_count": full_scan,
        "known_relevant_recall_status": "UNAVAILABLE_REQUIRES_SEALED_RELEVANCE_LABELS",
        "theme_metric_status": "UNAVAILABLE_OUTCOME_LEDGER_HAS_NO_SECTOR_TRUTH",
        "newsless_hallucination_status": ("UNAVAILABLE_SELECTED_SHADOW_CASES_ALL_HAVE_CURRENT_NEWS"),
        "rare_recovery_metric_status": ("AUDIT_DEFINED_RARE_REASONING_PAYLOAD_PROXY_NOT_LABELED_RECALL"),
        "common_supporting_vector_search": "DISABLED_FORCE_EMPTY",
        "cost_status": "UNAVAILABLE_CODEX_OAUTH_NO_METERED_COST_RECEIPT",
        "system_metric_notes": {
            "prompt_and_completion_tokens": "CONSERVATIVE_ESTIMATES_FROM_LLM_TRACES",
            "oauth_calls": "LIVE_CODEX_AGENT_EXECUTIONS",
            "checkpoint_hits": "LOCAL_CONTENT_ADDRESSED_LLM_CHECKPOINT_REUSE",
            "embedding_queries": "LOCAL_SENTENCE_TRANSFORMER_ENCODE_INVOCATIONS",
            "peak_memory": "PROCESS_LIFETIME_HIGH_WATER_MARK",
            "pre_llm_latency": "ANALYSIS_START_TO_FIRST_LLM_TRACE",
        },
        "retrieval_metric_notes": {
            "year_diversity": "SOURCE_TRADE_DATE_FROM_RUNTIME_TRACE",
            "issuer_day_duplicate_rate": ("ONE_MINUS_DISTINCT_INDEPENDENT_UNITS_OVER_SELECTED_RECORDS"),
            "regime_diversity": "UNAVAILABLE_RUNTIME_TRACE_HAS_NO_REGIME_LABEL",
            "unsupported_memory_assertion_rate": ("UNAVAILABLE_NO_SEALED_CLAIM_LEVEL_SUPPORT_LABELS"),
        },
        "daily_p95_budget_seconds": SHADOW_DAILY_P95_BUDGET_MS / 1000.0,
        "gate_checks": gate_checks,
        "runtime_gate": "PASS" if all(gate_checks.values()) else "HOLD",
        "compiler_v8_decision": "HOLD_UNTIL_RUNTIME_GATE_PASS",
        "full_rebuild_decision": "HOLD",
        "production_activation_status": "NOT_PRODUCTION_ACTIVATED",
    }


def _sum_runtime_metric(rows: list[dict[str, Any]], key: str) -> int:
    return sum(int(row.get(key) or 0) for row in rows if isinstance(row.get(key), int))


def _expected_calibration_error(
    rows: list[dict[str, Any]],
    *,
    bin_count: int = 5,
) -> float | None:
    pairs = [
        pair
        for row in rows
        for pair in row.get("metrics", {}).get("calibration_pairs", [])
        if isinstance(pair, dict)
        and isinstance(pair.get("probability"), (int, float))
        and isinstance(pair.get("outcome"), (int, float))
    ]
    if not pairs:
        return None
    error = 0.0
    for bin_index in range(bin_count):
        low = bin_index / bin_count
        high = (bin_index + 1) / bin_count
        members = [
            pair
            for pair in pairs
            if low <= float(pair["probability"])
            and (
                float(pair["probability"]) <= high if bin_index == bin_count - 1 else float(pair["probability"]) < high
            )
        ]
        if not members:
            continue
        confidence = sum(float(pair["probability"]) for pair in members) / len(members)
        accuracy = sum(float(pair["outcome"]) for pair in members) / len(members)
        error += len(members) / len(pairs) * abs(confidence - accuracy)
    return error


def _aggregate_micro_market_metrics(
    rows: list[dict[str, Any]],
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    targets = {
        "upper_limit": sum(int(row.get("metrics", {}).get("upper_limit_target_count") or 0) for row in rows),
        "high20": sum(int(row.get("metrics", {}).get("high20_target_count") or 0) for row in rows),
        "high10": sum(int(row.get("metrics", {}).get("high10_target_count") or 0) for row in rows),
    }
    for target, target_count in targets.items():
        result[f"micro_{target}_target_count"] = float(target_count)
        for cutoff in (5, 10, 20):
            hits = sum(int(row.get("metrics", {}).get(f"{target}_hit_count_at_{cutoff}") or 0) for row in rows)
            result[f"micro_{target}_recall_at_{cutoff}"] = hits / target_count if target_count else None
    for cutoff in (5, 10, 20):
        selected = sum(int(row.get("metrics", {}).get(f"selected_count_at_{cutoff}") or 0) for row in rows)
        high20_hits = sum(int(row.get("metrics", {}).get(f"high20_hit_count_at_{cutoff}") or 0) for row in rows)
        result[f"micro_high20_precision_at_{cutoff}"] = high20_hits / selected if selected else None
    return result


def _paired_bootstrap(
    observations: list[dict[str, Any]],
    *,
    run_id: str,
    resample_count: int = 10_000,
) -> dict[str, Any]:
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for row in observations:
        by_case.setdefault(str(row["case_id"]), {})[str(row["variant_id"])] = row
    paired = [rows for _case_id, rows in sorted(by_case.items()) if set(rows) >= {"V0", "V1"}]
    seed_text = f"{run_id}|paired-bootstrap|{resample_count}"
    seed_sha256 = sha256_text(seed_text)
    rng = random.Random(int(seed_sha256[:16], 16))
    result: dict[str, Any] = {
        "schema_version": "nslab.runtime_variant_paired_bootstrap.v1",
        "paired_case_count": len(paired),
        "resample_count": resample_count,
        "seed_sha256": seed_sha256,
        "metrics": {},
    }
    if not paired:
        return result
    for metric in (
        "recall_at_20",
        "precision_at_20",
        "mrr",
        "brier",
        "final_memory_citation_rate",
    ):
        raw_deltas = [float(rows["V1"]["metrics"][metric]) - float(rows["V0"]["metrics"][metric]) for rows in paired]
        improvement_deltas = [-value if metric == "brier" else value for value in raw_deltas]
        bootstrapped = sorted(
            sum(improvement_deltas[rng.randrange(len(improvement_deltas))] for _ in improvement_deltas)
            / len(improvement_deltas)
            for _ in range(resample_count)
        )
        result["metrics"][metric] = {
            "raw_v1_minus_v0": sum(raw_deltas) / len(raw_deltas),
            "improvement_direction_mean": sum(improvement_deltas) / len(improvement_deltas),
            "improvement_ci95_low": _percentile(bootstrapped, 0.025),
            "improvement_ci95_high": _percentile(bootstrapped, 0.975),
            "bootstrap_positive_share": sum(value > 0.0 for value in bootstrapped) / len(bootstrapped),
        }
    return result


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, int((len(values) - 1) * quantile + 0.999999)))
    return values[index]


def _render_report(report: dict[str, Any]) -> str:
    metrics = report["variant_metrics"]
    runtime = report["variant_runtime"]

    def value(section: dict[str, Any], key: str) -> str:
        observed = section.get(key)
        if isinstance(observed, float):
            return f"{observed:.6f}"
        return str(observed) if observed is not None else "unavailable"

    return "\n".join(
        [
            "# Runtime Variant Shadow",
            "",
            f"- Run: `{report['run_id']}`",
            f"- Scope: `{report['evaluation_scope']}`",
            f"- Paired cases: {report['paired_case_count']} / {report['expected_case_count']}",
            f"- Formal split cases: {report['formal_split_case_count']}",
            f"- Runtime gate: **{report['runtime_gate']}**",
            f"- V1 offline-unexposed recovered: {report['v1_offline_unexposed_recovered_count']}",
            f"- Future leak: {report['future_record_count']}",
            f"- BLIND web calls: {report['blind_web_call_count']}",
            f"- Online full scans: {report['online_full_scan_count']}",
            "- Known-relevant recall: unavailable until sealed relevance labels exist.",
            "- Rare recovery: audit-defined payload rarity proxy, not labeled recall.",
            "- Production activation: NOT_PRODUCTION_ACTIVATED",
            "",
            "## Market Metrics",
            "",
            "| Metric | V0 | V1 |",
            "| --- | ---: | ---: |",
            *[
                f"| {key} | {value(metrics['V0'], key)} | {value(metrics['V1'], key)} |"
                for key in (
                    "micro_upper_limit_recall_at_20",
                    "micro_high20_recall_at_20",
                    "micro_high10_recall_at_20",
                    "micro_high20_precision_at_20",
                    "brier",
                    "expected_calibration_error",
                    "leader_high20_hit",
                    "final_memory_citation_rate",
                )
            ],
            "",
            "## Runtime Metrics",
            "",
            "| Metric | V0 | V1 |",
            "| --- | ---: | ---: |",
            *[
                f"| {key} | {value(runtime['V0'], key)} | {value(runtime['V1'], key)} |"
                for key in (
                    "offline_unexposed_candidate_count",
                    "offline_unexposed_recovered_count",
                    "offline_unexposed_llm_exposed_count",
                    "offline_unexposed_final_cited_count",
                    "oauth_live_agent_call_count",
                    "llm_prompt_tokens_estimate",
                    "llm_completion_tokens_estimate",
                    "embedding_query_count",
                    "p95_elapsed_seconds",
                    "process_peak_working_set_bytes",
                )
            ],
            "",
        ]
    )
