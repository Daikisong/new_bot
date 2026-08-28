"""Export JSON schemas for canonical contracts."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from news_scalping_lab.contracts.memory_context import (
    AdaptiveRetrievalTrace,
    AdaptiveTriggerEvidence,
    BeneficiaryGraphArtifact,
    BeneficiaryGraphPath,
    CategoryBrainGuidance,
    CategoryBrainIndexManifest,
    CategoryBrainQueryPlan,
    CategoryClaimInclusionProof,
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
    RepresentativeRecord,
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
from news_scalping_lab.contracts.production import (
    ProductionBatchImportReceipt,
    ProductionCompanyMemoryAttestation,
    ProductionCurrentPointer,
    ProductionImportInventoryManifest,
    ProductionRecordArtifactManifest,
    ProductionReleaseArtifactManifest,
    ProductionReleaseConfigurationManifest,
    ProductionReleaseManifest,
    ProductionReleaseTransaction,
)
from news_scalping_lab.contracts.runtime_retrieval import (
    RuntimeEvidenceMemo,
    RuntimeEvidenceMemoBatch,
    RuntimeRetrievalTrace,
)
from news_scalping_lab.contracts.shadow_evaluation import (
    ShadowEvaluationManifest,
    ShadowReplayDataset,
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
    "representative_record.schema.json": RepresentativeRecord,
    "representative_set_manifest.schema.json": RepresentativeSetManifest,
    "adaptive_retrieval_trace.schema.json": AdaptiveRetrievalTrace,
    "adaptive_trigger_evidence.schema.json": AdaptiveTriggerEvidence,
    "beneficiary_graph.schema.json": BeneficiaryGraphArtifact,
    "beneficiary_graph_path.schema.json": BeneficiaryGraphPath,
    "category_brain_guidance.schema.json": CategoryBrainGuidance,
    "category_brain_query_plan.schema.json": CategoryBrainQueryPlan,
    "category_claim_inclusion_proof.schema.json": CategoryClaimInclusionProof,
    "category_brain_index_manifest.schema.json": CategoryBrainIndexManifest,
    "daily_memory_context.schema.json": DailyMemoryContext,
    "runtime_evidence_memo.schema.json": RuntimeEvidenceMemo,
    "runtime_evidence_memo_batch.schema.json": RuntimeEvidenceMemoBatch,
    "runtime_retrieval_trace.schema.json": RuntimeRetrievalTrace,
    "shadow_replay_dataset.schema.json": ShadowReplayDataset,
    "shadow_evaluation_manifest.schema.json": ShadowEvaluationManifest,
    "production_import_inventory_manifest.schema.json": (
        ProductionImportInventoryManifest
    ),
    "production_batch_import_receipt.schema.json": ProductionBatchImportReceipt,
    "production_company_memory_attestation.schema.json": (
        ProductionCompanyMemoryAttestation
    ),
    "production_record_artifact_manifest.schema.json": (
        ProductionRecordArtifactManifest
    ),
    "production_release_artifact_manifest.schema.json": (
        ProductionReleaseArtifactManifest
    ),
    "production_release_transaction.schema.json": ProductionReleaseTransaction,
    "production_release_configuration.schema.json": (
        ProductionReleaseConfigurationManifest
    ),
    "production_release_manifest.schema.json": ProductionReleaseManifest,
    "production_current_pointer.schema.json": ProductionCurrentPointer,
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
