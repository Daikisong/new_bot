"""Pure UI view models for rendered analysis results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.models import (
    Candidate,
    DailyAnalysis,
    DominantSectorHypothesis,
    PathType,
)
from news_scalping_lab.utils import read_json


@dataclass(frozen=True)
class ArtifactLinks:
    prediction_json: Path
    report_markdown: Path
    context_manifest_json: Path
    source_ledger_jsonl: Path | None = None
    candidate_web_checks_jsonl: Path | None = None
    excluded_candidate_web_checks_jsonl: Path | None = None


@dataclass(frozen=True)
class SweepShardStatus:
    shard_index: int | None
    status: str
    episode_count: int
    episode_ids: list[str]
    from_cache: bool
    artifact_path: Path
    error: str | None = None


@dataclass(frozen=True)
class CandidateEvidenceView:
    rank: int
    ticker: str
    company_name: str
    path_type: str
    thesis: str
    why_now: str
    confidence_label: str
    evidence_quality: str
    causal_chain: list[str]
    direct_evidence: list[str]
    inferred_evidence: list[str]
    market_memory_evidence: list[str]
    prior_positive_cases: list[str]
    prior_negative_cases: list[str]
    novel_reasoning: str
    counterarguments: list[str]
    disconfirming_conditions: list[str]
    memory_episode_ids: list[str]
    source_urls: list[str]


@dataclass(frozen=True)
class AnalysisViewModel:
    run_id: str
    mode: str
    brain_version: str
    accepted_episode_count: int
    swept_episode_count: int
    memory_sweep_shard_count: int
    memory_sweep_cache_hits: int
    memory_sweep_shards: list[SweepShardStatus]
    coverage_errors: list[str]
    dominant_sectors: list[DominantSectorHypothesis]
    candidates_by_path: dict[str, list[CandidateEvidenceView]]
    artifacts: ArtifactLinks


def build_analysis_view_model(root: Path, analysis: DailyAnalysis) -> AnalysisViewModel:
    manifest = analysis.context_manifest
    return AnalysisViewModel(
        run_id=analysis.run_id,
        mode=analysis.mode,
        brain_version=manifest.brain_version or "none",
        accepted_episode_count=manifest.accepted_episode_count,
        swept_episode_count=manifest.swept_episode_count,
        memory_sweep_shard_count=manifest.memory_sweep_shard_count,
        memory_sweep_cache_hits=manifest.memory_sweep_cache_hits,
        memory_sweep_shards=_memory_sweep_shards(root, manifest.memory_sweep_artifacts),
        coverage_errors=manifest.errors,
        dominant_sectors=analysis.blind_prediction.dominant_sectors,
        candidates_by_path=_candidates_by_path(analysis.blind_prediction.candidates),
        artifacts=ArtifactLinks(
            prediction_json=root / analysis.prediction_path,
            report_markdown=root / analysis.report_path,
            context_manifest_json=root / "runs" / "manifests" / f"{analysis.run_id}.json",
            source_ledger_jsonl=_optional_artifact_path(root, manifest.source_ledger_artifact),
            candidate_web_checks_jsonl=_optional_artifact_path(
                root, manifest.candidate_web_check_artifact
            ),
            excluded_candidate_web_checks_jsonl=_optional_artifact_path(
                root, manifest.excluded_candidate_web_check_artifact
            ),
        ),
    )


def _candidates_by_path(candidates: list[Candidate]) -> dict[str, list[CandidateEvidenceView]]:
    grouped: dict[str, list[Candidate]] = {path_type.value: [] for path_type in PathType}
    for candidate in sorted(candidates, key=lambda item: item.rank):
        grouped.setdefault(str(candidate.path_type), []).append(candidate)
    return {
        path_type: [_candidate_evidence_view(candidate) for candidate in path_candidates]
        for path_type, path_candidates in grouped.items()
    }


def _candidate_evidence_view(candidate: Candidate) -> CandidateEvidenceView:
    return CandidateEvidenceView(
        rank=candidate.rank,
        ticker=candidate.ticker,
        company_name=candidate.company_name,
        path_type=str(candidate.path_type),
        thesis=candidate.thesis,
        why_now=candidate.why_now,
        confidence_label=str(candidate.confidence_label),
        evidence_quality=str(candidate.evidence_quality),
        causal_chain=candidate.causal_chain,
        direct_evidence=candidate.direct_evidence,
        inferred_evidence=candidate.inferred_evidence,
        market_memory_evidence=candidate.market_memory_evidence,
        prior_positive_cases=candidate.prior_positive_cases,
        prior_negative_cases=candidate.prior_negative_cases,
        novel_reasoning=candidate.novel_reasoning,
        counterarguments=candidate.counterarguments,
        disconfirming_conditions=candidate.disconfirming_conditions,
        memory_episode_ids=candidate.memory_episode_ids,
        source_urls=candidate.source_urls,
    )


def _memory_sweep_shards(root: Path, artifact_paths: list[str]) -> list[SweepShardStatus]:
    shards: list[SweepShardStatus] = []
    for relative_path in artifact_paths:
        artifact_path = root / relative_path
        if not artifact_path.exists():
            shards.append(
                SweepShardStatus(
                    shard_index=None,
                    status="missing",
                    episode_count=0,
                    episode_ids=[],
                    from_cache=False,
                    artifact_path=artifact_path,
                    error="artifact does not exist",
                )
            )
            continue
        try:
            payload = read_json(artifact_path)
        except ValueError as exc:
            shards.append(
                SweepShardStatus(
                    shard_index=None,
                    status="unreadable",
                    episode_count=0,
                    episode_ids=[],
                    from_cache=False,
                    artifact_path=artifact_path,
                    error=str(exc),
                )
            )
            continue
        shards.append(_sweep_status_from_payload(artifact_path, payload))
    return sorted(
        shards,
        key=lambda item: (
            item.shard_index is None,
            item.shard_index if item.shard_index is not None else 0,
            item.artifact_path.as_posix(),
        ),
    )


def _sweep_status_from_payload(artifact_path: Path, payload: dict[str, Any]) -> SweepShardStatus:
    from_cache = bool(payload.get("from_cache", False))
    return SweepShardStatus(
        shard_index=_optional_int(payload.get("shard_index")),
        status="cached" if from_cache else "completed",
        episode_count=_optional_int(payload.get("episode_count")) or 0,
        episode_ids=[item for item in payload.get("episode_ids", []) if isinstance(item, str)],
        from_cache=from_cache,
        artifact_path=artifact_path,
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _optional_artifact_path(root: Path, relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    return root / relative_path
