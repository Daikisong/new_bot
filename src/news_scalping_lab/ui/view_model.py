"""Pure UI view models for rendered analysis results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from news_scalping_lab.contracts.models import (
    Candidate,
    DailyAnalysis,
    DominantSectorHypothesis,
    PathType,
)


@dataclass(frozen=True)
class ArtifactLinks:
    prediction_json: Path
    report_markdown: Path
    context_manifest_json: Path


@dataclass(frozen=True)
class AnalysisViewModel:
    run_id: str
    mode: str
    brain_version: str
    accepted_episode_count: int
    swept_episode_count: int
    memory_sweep_shard_count: int
    memory_sweep_cache_hits: int
    coverage_errors: list[str]
    dominant_sectors: list[DominantSectorHypothesis]
    candidates_by_path: dict[str, list[Candidate]]
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
        coverage_errors=manifest.errors,
        dominant_sectors=analysis.blind_prediction.dominant_sectors,
        candidates_by_path=_candidates_by_path(analysis.blind_prediction.candidates),
        artifacts=ArtifactLinks(
            prediction_json=root / analysis.prediction_path,
            report_markdown=root / analysis.report_path,
            context_manifest_json=root / "runs" / "manifests" / f"{analysis.run_id}.json",
        ),
    )


def _candidates_by_path(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    grouped: dict[str, list[Candidate]] = {path_type.value: [] for path_type in PathType}
    for candidate in sorted(candidates, key=lambda item: item.rank):
        grouped.setdefault(str(candidate.path_type), []).append(candidate)
    return grouped
