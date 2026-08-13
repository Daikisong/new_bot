"""Export JSON schemas for canonical contracts."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from news_scalping_lab.contracts.memory_context import (
    AdaptiveRetrievalTrace,
    DailyMemoryContext,
    EventClusterManifest,
    MemoryCellManifest,
    MemoryCellMembership,
    MemoryCellSnapshotManifest,
    MemoryCoverageManifest,
    NewsCoverageManifest,
    PopulationCubeRow,
    PopulationManifest,
    RecordRoutingMetadata,
    RepresentativeSetManifest,
)
from news_scalping_lab.contracts.models import (
    BlindPrediction,
    BrainManifest,
    Candidate,
    CandidateExpansionReview,
    CandidateVerificationReview,
    CompanyMemory,
    ContextManifest,
    DailyAnalysis,
    EventTickerEdge,
    FinalSynthesisContextArtifact,
    MechanismMemory,
    MemoryClaim,
    NewsNoveltyReview,
    OpenWorldFirstAnalysis,
    Postmortem,
    RedTeamArtifact,
    ResearchEpisode,
    SemanticRetrievalPlan,
)
from news_scalping_lab.research_import.semantic import SemanticResearchDraft
from news_scalping_lab.utils import write_json

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "research_episode.schema.json": ResearchEpisode,
    "blind_prediction.schema.json": BlindPrediction,
    "postmortem.schema.json": Postmortem,
    "memory_claim.schema.json": MemoryClaim,
    "mechanism_memory.schema.json": MechanismMemory,
    "company_memory.schema.json": CompanyMemory,
    "event_ticker_edge.schema.json": EventTickerEdge,
    "brain_manifest.schema.json": BrainManifest,
    "daily_analysis.schema.json": DailyAnalysis,
    "candidate.schema.json": Candidate,
    "candidate_expansion_review.schema.json": CandidateExpansionReview,
    "candidate_verification_review.schema.json": CandidateVerificationReview,
    "final_synthesis_context.schema.json": FinalSynthesisContextArtifact,
    "context_manifest.schema.json": ContextManifest,
    "red_team_artifact.schema.json": RedTeamArtifact,
    "open_world_first_analysis.schema.json": OpenWorldFirstAnalysis,
    "news_novelty_review.schema.json": NewsNoveltyReview,
    "semantic_retrieval_plan.schema.json": SemanticRetrievalPlan,
    "semantic_research_draft.schema.json": SemanticResearchDraft,
    "record_routing_metadata.schema.json": RecordRoutingMetadata,
    "news_coverage_manifest.schema.json": NewsCoverageManifest,
    "event_cluster_manifest.schema.json": EventClusterManifest,
    "memory_coverage_manifest.schema.json": MemoryCoverageManifest,
    "memory_cell_manifest.schema.json": MemoryCellManifest,
    "memory_cell_membership.schema.json": MemoryCellMembership,
    "memory_cell_snapshot_manifest.schema.json": MemoryCellSnapshotManifest,
    "population_manifest.schema.json": PopulationManifest,
    "population_cube_row.schema.json": PopulationCubeRow,
    "representative_set_manifest.schema.json": RepresentativeSetManifest,
    "adaptive_retrieval_trace.schema.json": AdaptiveRetrievalTrace,
    "daily_memory_context.schema.json": DailyMemoryContext,
}


def export_json_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, model in SCHEMA_MODELS.items():
        path = output_dir / filename
        write_json(path, model.model_json_schema())
        written.append(path)
    return written


if __name__ == "__main__":
    for schema_path in export_json_schemas(Path("schemas")):
        print(schema_path)
