from __future__ import annotations

from datetime import date, datetime, time
from typing import TypeVar

from pydantic import BaseModel

from news_scalping_lab.brain.audit import audit_brain
from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.research_import.semantic import SemanticResearchDraft
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, read_json
from news_scalping_lab.warehouse import WarehouseStore

T = TypeVar("T", bound=BaseModel)


class RecordingSemanticLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        raise AssertionError("semantic import should request structured output")

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        self.calls.append(
            {"prompt": prompt, "response_model": response_model, "purpose": purpose}
        )
        assert response_model is SemanticResearchDraft
        draft = SemanticResearchDraft(
            trade_date=date(2040, 2, 3),
            cutoff_at=datetime.combine(date(2040, 2, 3), time(8, 59, 59), tzinfo=KST),
            summary="Structured import supplied by the LLM provider.",
            open_world_mechanisms=["free-form source -> structured episode draft"],
            initial_uncertainties=["review raw source before acceptance"],
            price_source_snapshot={"source": "recording-test"},
        )
        return draft  # type: ignore[return-value]

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        return [[0.0] for _ in texts]


def test_semantic_import_accept_and_brain_rebuild(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("# Research\n\nBlind and postmortem notes for 2030-01-10.", encoding="utf-8")

    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    ResearchStore(tmp_path).accept(episode.episode_id)
    manifest = BrainCompiler(tmp_path).rebuild(mode="full")
    audit = audit_brain(tmp_path)

    assert manifest.accepted_episode_count == 1
    assert manifest.coverage_complete
    assert audit["coverage_complete"]
    assert episode.episode_id in manifest.covered_episode_ids
    assert WarehouseStore(tmp_path).counts()["research_episodes.parquet"] == 1


def test_semantic_import_uses_structured_llm_output_and_writes_trace(tmp_path) -> None:
    source = tmp_path / "freeform_notes.md"
    source.write_text("Free-form research note without a parseable date.", encoding="utf-8")
    llm = RecordingSemanticLLM()

    episode = ResearchImporter(tmp_path, llm=llm).import_path(source, mode="semantic")

    assert len(llm.calls) == 1
    assert llm.calls[0]["purpose"] == "research_import.semantic"
    assert llm.calls[0]["response_model"] is SemanticResearchDraft
    assert episode.trade_date == date(2040, 2, 3)
    assert episode.price_source_snapshot == {"source": "recording-test"}
    assert episode.provenance[0].source_type == "semantic_llm_structured_import"

    traces = list((tmp_path / "runs" / "traces").glob("TRACE-*.json"))
    assert len(traces) == 1
    trace = read_json(traces[0])
    assert trace["purpose"] == "research_import.semantic"
    assert trace["response_model"] == "SemanticResearchDraft"
    assert trace["output"]["trade_date"] == "2040-02-03"
