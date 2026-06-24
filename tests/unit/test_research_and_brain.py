from __future__ import annotations

from datetime import date, datetime, time
from typing import TypeVar

from pydantic import BaseModel

from news_scalping_lab.brain.audit import audit_brain
from news_scalping_lab.brain.compiler import BrainCompiler, current_brain_file_hashes
from news_scalping_lab.brain.diff import build_brain_diff, write_brain_diff
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
    shard_manifest = read_json(tmp_path / "memory" / "shard_brains" / "current" / "manifest.json")
    assert shard_manifest["brain_version"] == manifest.brain_version
    assert shard_manifest["shard_count"] == 1
    shard_path = tmp_path / shard_manifest["shard_files"][0]
    assert episode.episode_id in shard_path.read_text(encoding="utf-8")
    assert (tmp_path / "memory" / "shard_brains" / manifest.brain_version).exists()
    assert WarehouseStore(tmp_path).counts()["research_episodes.parquet"] == 1


def test_brain_diff_compares_versioned_snapshots(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    store = ResearchStore(tmp_path)
    compiler = BrainCompiler(tmp_path)

    source_a = tmp_path / "research_20300110.md"
    source_a.write_text("First abstract mechanism note for 2030-01-10.", encoding="utf-8")
    episode_a = ResearchImporter(tmp_path).import_path(source_a, mode="semantic")
    store.accept(episode_a.episode_id)
    manifest_a = compiler.rebuild(mode="full")

    source_b = tmp_path / "research_20300111.md"
    source_b.write_text("Second abstract mechanism note for 2030-01-11.", encoding="utf-8")
    episode_b = ResearchImporter(tmp_path).import_path(source_b, mode="semantic")
    store.accept(episode_b.episode_id)
    manifest_b = compiler.rebuild(mode="full")

    diff = build_brain_diff(tmp_path, manifest_a.brain_version, manifest_b.brain_version)
    diff_path = write_brain_diff(tmp_path, manifest_a.brain_version, manifest_b.brain_version)

    assert diff["changed"]
    assert diff["added_episode_ids"] == [episode_b.episode_id]
    assert diff["removed_episode_ids"] == []
    file_changes = diff["file_changes"]
    assert isinstance(file_changes, list)
    assert any(
        isinstance(change, dict) and change["file"] == "brain_manifest.json"
        for change in file_changes
    )
    assert diff_path.exists()
    assert (tmp_path / "brain" / "diffs" / f"{manifest_b.brain_version}.md").exists()


def test_brain_rebuild_is_deterministic_for_same_accepted_episodes(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("Repeatable mechanism note for 2030-01-10.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    ResearchStore(tmp_path).accept(episode.episode_id)

    compiler = BrainCompiler(tmp_path)
    first_manifest = compiler.rebuild(mode="full")
    first_hashes = current_brain_file_hashes(tmp_path)
    first_claims = (tmp_path / "brain" / "current" / "claims.jsonl").read_text(encoding="utf-8")
    second_manifest = compiler.rebuild(mode="full")
    second_hashes = current_brain_file_hashes(tmp_path)
    second_claims = (tmp_path / "brain" / "current" / "claims.jsonl").read_text(encoding="utf-8")

    assert second_manifest.model_dump(mode="json") == first_manifest.model_dump(mode="json")
    assert second_hashes == first_hashes
    assert second_claims == first_claims


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
    assert trace["operation"] == "generate_structured"
    assert trace["prompt_version"] == "semantic_import.v1"
    assert trace["input"]["response_model"] == "SemanticResearchDraft"
    assert trace["output"]["trade_date"] == "2040-02-03"
