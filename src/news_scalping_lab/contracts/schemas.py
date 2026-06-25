"""Export JSON schemas for canonical contracts."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from news_scalping_lab.contracts.models import (
    BlindPrediction,
    BrainManifest,
    Candidate,
    CandidateExpansionReview,
    CompanyMemory,
    ContextManifest,
    DailyAnalysis,
    EventTickerEdge,
    MechanismMemory,
    MemoryClaim,
    NewsNoveltyReview,
    Postmortem,
    RedTeamArtifact,
    ResearchEpisode,
    SemanticRetrievalPlan,
)
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
    "context_manifest.schema.json": ContextManifest,
    "red_team_artifact.schema.json": RedTeamArtifact,
    "news_novelty_review.schema.json": NewsNoveltyReview,
    "semantic_retrieval_plan.schema.json": SemanticRetrievalPlan,
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
