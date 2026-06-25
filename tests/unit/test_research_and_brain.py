from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import TypeVar

import pytest
import typer
from pydantic import BaseModel, ValidationError
from typer.testing import CliRunner

from news_scalping_lab.audits.coverage import audit_coverage
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.brain.audit import audit_brain
from news_scalping_lab.brain.compiler import BrainCompiler, current_brain_file_hashes
from news_scalping_lab.brain.diff import build_brain_diff, write_brain_diff
from news_scalping_lab.cli import app, audit_coverage_cmd
from news_scalping_lab.cli import brain_audit as cli_brain_audit
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BrainManifest,
    MechanismMemory,
    MemoryClaim,
    Provenance,
    ResearchEpisode,
)
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.research_import.semantic import SemanticResearchDraft
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, file_sha256, next_trading_day, read_json, sha256_text
from news_scalping_lab.warehouse import WarehouseStore

T = TypeVar("T", bound=BaseModel)
RUNNER = CliRunner()


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


def _batch_episode(episode_id: str, summary: str) -> ResearchEpisode:
    trade_day = date(2030, 1, 10)
    return ResearchEpisode(
        episode_id=episode_id,
        trade_date=trade_day,
        cutoff_at=datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST),
        created_at=datetime.combine(trade_day, time(16, 0, 0), tzinfo=KST),
        research_version="batch-import-test-v1",
        price_source_snapshot={"source": "batch-test"},
        blind_analysis=BlindAnalysis(
            summary=summary,
            open_world_mechanisms=[f"{episode_id} -> batch import -> brain rebuild"],
        ),
        available_from=datetime.combine(date(2030, 1, 11), time(0, 0, 0), tzinfo=KST),
    )


def _tree_hashes(root: Path, relative_dir: str) -> dict[str, str]:
    base = root / relative_dir
    return {
        path.relative_to(base).as_posix(): file_sha256(path)
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


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
    assert manifest.build_mode == "full"
    assert manifest.last_full_rebuild_at == manifest.created_at
    assert manifest.updated_episode_id is None
    assert audit["coverage_complete"]
    assert audit["brain_build_mode"] == "full"
    assert audit["last_full_rebuild"] == manifest.created_at.isoformat()
    assert episode.episode_id in manifest.covered_episode_ids
    shard_manifest = read_json(tmp_path / "memory" / "shard_brains" / "current" / "manifest.json")
    assert shard_manifest["brain_version"] == manifest.brain_version
    assert shard_manifest["shard_count"] == 1
    shard_path = tmp_path / shard_manifest["shard_files"][0]
    assert episode.episode_id in shard_path.read_text(encoding="utf-8")
    assert (tmp_path / "memory" / "shard_brains" / manifest.brain_version).exists()
    mechanisms_manifest = read_json(tmp_path / "memory" / "mechanisms" / "current" / "manifest.json")
    mechanisms_path = tmp_path / "memory" / "mechanisms" / "current" / "mechanisms.jsonl"
    mechanisms = [
        MechanismMemory.model_validate(json.loads(line))
        for line in mechanisms_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert mechanisms_manifest["brain_version"] == manifest.brain_version
    assert mechanisms_manifest["mechanism_count"] == len(mechanisms) == 3
    assert mechanisms_manifest["covered_episode_ids"] == [episode.episode_id]
    assert mechanisms_manifest["mechanisms_sha256"] == file_sha256(mechanisms_path)
    assert mechanisms[0].successful_cases == [episode.episode_id]
    assert mechanisms[0].provenance
    assert (tmp_path / "memory" / "mechanisms" / manifest.brain_version).exists()
    assert WarehouseStore(tmp_path).counts()["research_episodes.parquet"] == 1


def test_brain_rebuild_uses_configurable_shard_episode_count(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    store = ResearchStore(tmp_path)
    for index in range(3):
        episode = _batch_episode(
            f"EP-shard-config-{index}",
            f"Shard config lesson {index}.",
        )
        store.save_episode(episode)
        store.accept(episode.episode_id)

    default_manifest = BrainCompiler(tmp_path).rebuild(mode="full")
    manifest = BrainCompiler(tmp_path, shard_episode_count=2).rebuild(mode="full")
    shard_manifest = read_json(
        tmp_path / "memory" / "shard_brains" / "current" / "manifest.json"
    )

    assert manifest.brain_version != default_manifest.brain_version
    assert shard_manifest["brain_version"] == manifest.brain_version
    assert shard_manifest["shard_episode_count"] == 2
    assert shard_manifest["shard_count"] == 2
    assert len(shard_manifest["shard_files"]) == 2
    first_shard = (tmp_path / shard_manifest["shard_files"][0]).read_text(
        encoding="utf-8"
    )
    second_shard = (tmp_path / shard_manifest["shard_files"][1]).read_text(
        encoding="utf-8"
    )
    assert "EP-shard-config-0" in first_shard
    assert "EP-shard-config-1" in first_shard
    assert "EP-shard-config-2" in second_shard


def test_cli_import_batch_accepts_by_default_and_rebuilds_brain(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    inbox = tmp_path / "data" / "inbox" / "research"
    (inbox / "one.json").write_text(
        _batch_episode("EP-batch-one", "First batch lesson.").model_dump_json(),
        encoding="utf-8-sig",
    )
    (inbox / "two.json").write_text(
        _batch_episode("EP-batch-two", "Second batch lesson.").model_dump_json(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    imported = RUNNER.invoke(app, ["research", "import-batch", str(inbox), "--mode", "strict"])

    assert imported.exit_code == 0, imported.output
    imported_output = json.loads(imported.output)
    assert imported_output["imported_episode_ids"] == ["EP-batch-one", "EP-batch-two"]
    assert imported_output["accepted_episode_ids"] == ["EP-batch-one", "EP-batch-two"]

    rebuilt = RUNNER.invoke(app, ["brain", "rebuild", "--mode", "full"])

    assert rebuilt.exit_code == 0, rebuilt.output
    manifest = json.loads(rebuilt.output)
    assert manifest["accepted_episode_count"] == 2
    assert manifest["covered_episode_ids"] == ["EP-batch-one", "EP-batch-two"]
    assert ResearchStore(tmp_path).accepted_hashes().keys() == {
        "EP-batch-one",
        "EP-batch-two",
    }


def test_cli_import_batch_can_leave_episodes_unaccepted(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    inbox = tmp_path / "data" / "inbox" / "research"
    (inbox / "one.json").write_text(
        _batch_episode("EP-staged-only", "Staged batch lesson.").model_dump_json(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    imported = RUNNER.invoke(
        app,
        ["research", "import-batch", str(inbox), "--mode", "strict", "--no-accept"],
    )

    assert imported.exit_code == 0, imported.output
    imported_output = json.loads(imported.output)
    assert imported_output["imported_episode_ids"] == ["EP-staged-only"]
    assert imported_output["accepted_episode_ids"] == []
    assert ResearchStore(tmp_path).list_accepted() == []


def test_strict_import_preserves_raw_source_and_provenance_hash(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "strict_episode.json"
    source.write_text(
        _batch_episode("EP-strict-source", "Strict source preservation lesson.").model_dump_json(),
        encoding="utf-8",
    )

    episode = ResearchImporter(tmp_path).import_path(source, mode="strict")

    strict_provenance = [
        item for item in episode.provenance if item.source_type == "strict_research_json"
    ]
    assert len(strict_provenance) == 1
    preserved_raw = Path(strict_provenance[0].uri)
    assert preserved_raw.exists()
    assert preserved_raw.parent == tmp_path / "data" / "raw" / "research"
    assert strict_provenance[0].content_sha256 == file_sha256(preserved_raw)
    assert ResearchStore(tmp_path).get_episode("EP-strict-source").episode_id == (
        "EP-strict-source"
    )
    audit = audit_provenance(tmp_path)
    assert audit["passed"], audit["findings"]
    assert audit["checked_research_episode_files"] == 1


def test_strict_import_rejects_invalid_episode_without_saving(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "invalid_episode.json"
    source.write_text('{"episode_id":"EP-invalid"}', encoding="utf-8")

    with pytest.raises(ValidationError):
        ResearchImporter(tmp_path).import_path(source, mode="strict")

    assert ResearchStore(tmp_path).list_episodes() == []
    raw_files = list((tmp_path / "data" / "raw" / "research").glob("*invalid_episode.json"))
    assert len(raw_files) == 1


def test_research_accept_reject_stages_are_mutually_exclusive(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("Stage transition research note for 2030-01-10.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    store = ResearchStore(tmp_path)

    accepted_path = store.accept(episode.episode_id)
    rejected_path = store.reject(episode.episode_id)

    assert (tmp_path / "research" / "episodes" / f"{episode.episode_id}.json").exists()
    assert not accepted_path.exists()
    assert rejected_path.exists()
    assert [item.episode_id for item in store.list_accepted()] == []
    assert [item.episode_id for item in store.list_rejected()] == [episode.episode_id]

    accepted_path = store.accept(episode.episode_id)

    assert accepted_path.exists()
    assert not rejected_path.exists()
    assert [item.episode_id for item in store.list_accepted()] == [episode.episode_id]
    assert [item.episode_id for item in store.list_rejected()] == []


def test_brain_rebuild_uses_accepted_snapshot_when_canonical_episode_changes(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("Accepted snapshot note for 2030-01-10.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    store = ResearchStore(tmp_path)
    store.accept(episode.episode_id)

    changed_mechanism = "changed canonical mechanism must not enter accepted brain"
    changed = episode.model_copy(
        update={
            "blind_analysis": BlindAnalysis(
                summary="Changed canonical summary should not affect accepted brain.",
                open_world_mechanisms=[changed_mechanism],
            )
        }
    )
    store.save_episode(changed)

    fetched = store.get_episode(episode.episode_id)
    assert fetched.blind_analysis.open_world_mechanisms == (
        episode.blind_analysis.open_world_mechanisms
    )

    manifest = BrainCompiler(tmp_path).rebuild(mode="full")
    claims_text = (tmp_path / "brain" / "current" / "claims.jsonl").read_text(encoding="utf-8")

    assert changed_mechanism not in claims_text
    assert episode.blind_analysis.open_world_mechanisms[0] in claims_text
    assert episode.episode_id in manifest.covered_episode_ids


def test_brain_rebuild_includes_imported_lessons_and_counterexamples_as_claims(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("Imported lesson and counterexample note for 2030-01-10.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    provenance = Provenance(
        source_id="SRC-imported-claim-test",
        source_type="test_research",
        uri="test://imported-claim",
        content_sha256=sha256_text("imported-claim"),
    )
    lesson = MemoryClaim(
        claim_id="CL-imported-lesson",
        statement="Imported lesson must become a brain claim.",
        mechanism="strict research lesson -> brain claim",
        scope="test",
        support_episode_ids=[],
        contradiction_episode_ids=[episode.episode_id],
        available_from=episode.available_from,
        provenance=[],
    )
    counterexample = MemoryClaim(
        claim_id="CL-imported-counterexample",
        statement="Imported counterexample must remain available to synthesis.",
        mechanism="counterexample preservation",
        scope="test",
        support_episode_ids=[episode.episode_id],
        contradiction_episode_ids=[episode.episode_id],
        available_from=episode.available_from,
        provenance=[provenance],
    )
    enriched_episode = episode.model_copy(
        update={
            "lessons": [lesson],
            "counterexamples": [counterexample],
            "provenance": [provenance],
        }
    )
    store = ResearchStore(tmp_path)
    store.save_episode(enriched_episode)
    store.accept(enriched_episode.episode_id)

    manifest = BrainCompiler(tmp_path).rebuild(mode="full")
    claims = [
        json.loads(line)
        for line in (tmp_path / "brain" / "current" / "claims.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    claims_by_id = {claim["claim_id"]: claim for claim in claims}

    assert "CL-imported-lesson" in manifest.claim_ids
    assert "CL-imported-counterexample" in manifest.claim_ids
    assert claims_by_id["CL-imported-lesson"]["support_episode_ids"] == [
        enriched_episode.episode_id
    ]
    assert claims_by_id["CL-imported-lesson"]["provenance"][0]["source_id"] == (
        "SRC-imported-claim-test"
    )
    assert claims_by_id["CL-imported-counterexample"]["contradiction_episode_ids"] == [
        enriched_episode.episode_id
    ]
    assert audit_brain(tmp_path)["passed"] is True


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
    first_snapshot_hashes = _tree_hashes(
        tmp_path,
        f"brain/snapshots/{first_manifest.brain_version}",
    )
    first_shard_current_hashes = _tree_hashes(tmp_path, "memory/shard_brains/current")
    first_shard_version_hashes = _tree_hashes(
        tmp_path,
        f"memory/shard_brains/{first_manifest.brain_version}",
    )
    first_mechanism_current_hashes = _tree_hashes(tmp_path, "memory/mechanisms/current")
    first_mechanism_version_hashes = _tree_hashes(
        tmp_path,
        f"memory/mechanisms/{first_manifest.brain_version}",
    )
    first_diff_hash = file_sha256(
        tmp_path / "brain" / "diffs" / f"{first_manifest.brain_version}.md"
    )
    first_claims = (tmp_path / "brain" / "current" / "claims.jsonl").read_text(encoding="utf-8")
    first_mechanisms = (tmp_path / "memory" / "mechanisms" / "current" / "mechanisms.jsonl").read_text(
        encoding="utf-8"
    )
    second_manifest = compiler.rebuild(mode="full")
    second_hashes = current_brain_file_hashes(tmp_path)
    second_snapshot_hashes = _tree_hashes(
        tmp_path,
        f"brain/snapshots/{second_manifest.brain_version}",
    )
    second_shard_current_hashes = _tree_hashes(tmp_path, "memory/shard_brains/current")
    second_shard_version_hashes = _tree_hashes(
        tmp_path,
        f"memory/shard_brains/{second_manifest.brain_version}",
    )
    second_mechanism_current_hashes = _tree_hashes(tmp_path, "memory/mechanisms/current")
    second_mechanism_version_hashes = _tree_hashes(
        tmp_path,
        f"memory/mechanisms/{second_manifest.brain_version}",
    )
    second_diff_hash = file_sha256(
        tmp_path / "brain" / "diffs" / f"{second_manifest.brain_version}.md"
    )
    second_claims = (tmp_path / "brain" / "current" / "claims.jsonl").read_text(encoding="utf-8")
    second_mechanisms = (tmp_path / "memory" / "mechanisms" / "current" / "mechanisms.jsonl").read_text(
        encoding="utf-8"
    )

    assert second_manifest.model_dump(mode="json") == first_manifest.model_dump(mode="json")
    assert second_hashes == first_hashes
    assert second_snapshot_hashes == first_snapshot_hashes
    assert second_shard_current_hashes == first_shard_current_hashes
    assert second_shard_version_hashes == first_shard_version_hashes
    assert second_mechanism_current_hashes == first_mechanism_current_hashes
    assert second_mechanism_version_hashes == first_mechanism_version_hashes
    assert second_diff_hash == first_diff_hash
    assert second_claims == first_claims
    assert second_mechanisms == first_mechanisms


def test_brain_rebuild_refuses_to_overwrite_existing_snapshot(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("Immutable snapshot note for 2030-01-10.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    ResearchStore(tmp_path).accept(episode.episode_id)

    compiler = BrainCompiler(tmp_path)
    manifest = compiler.rebuild(mode="full")
    snapshot_manifest = (
        tmp_path / "brain" / "snapshots" / manifest.brain_version / "brain_manifest.json"
    )
    original_snapshot = snapshot_manifest.read_text(encoding="utf-8")
    snapshot_manifest.write_text(
        original_snapshot.replace(manifest.brain_version, "brain-corrupted", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="immutable brain snapshot"):
        compiler.rebuild(mode="full")

    assert "brain-corrupted" in snapshot_manifest.read_text(encoding="utf-8")


def test_brain_audit_validates_claim_support_provenance_and_temporal_order(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("Audited mechanism note for 2030-01-10.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    ResearchStore(tmp_path).accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")

    provenance = Provenance(
        source_id="SRC-audit",
        source_type="test",
        uri="test://audit",
        content_sha256=sha256_text("audit"),
    )
    claims = [
        MemoryClaim(
            claim_id="CL-no-support",
            statement="Missing support should fail audit.",
            mechanism="missing support",
            scope="audit fixture",
            support_episode_ids=[],
            available_from=episode.available_from,
            provenance=[provenance],
        ),
        MemoryClaim(
            claim_id="CL-unknown-support",
            statement="Unknown support should fail audit.",
            mechanism="unknown support",
            scope="audit fixture",
            support_episode_ids=["EP-unknown"],
            available_from=episode.available_from,
            provenance=[provenance],
        ),
        MemoryClaim(
            claim_id="CL-no-provenance",
            statement="Missing provenance should fail audit.",
            mechanism="missing provenance",
            scope="audit fixture",
            support_episode_ids=[episode.episode_id],
            available_from=episode.available_from,
            provenance=[],
        ),
        MemoryClaim(
            claim_id="CL-temporal-leak",
            statement="Claim availability cannot precede support availability.",
            mechanism="temporal leak",
            scope="audit fixture",
            support_episode_ids=[episode.episode_id],
            available_from=episode.cutoff_at,
            provenance=[provenance],
        ),
        MemoryClaim(
            claim_id="CL-single-support-warning",
            statement="Single-support generalization should be surfaced as a warning.",
            mechanism="single support",
            scope="audit fixture",
            support_episode_ids=[episode.episode_id],
            available_from=episode.available_from,
            provenance=[provenance],
        ),
    ]
    (tmp_path / "brain" / "current" / "claims.jsonl").write_text(
        "".join(claim.model_dump_json() + "\n" for claim in claims),
        encoding="utf-8",
    )

    audit = audit_brain(tmp_path)

    assert audit["coverage_complete"] is True
    assert audit["passed"] is False
    assert audit["claims_without_support"] == ["CL-no-support"]
    assert audit["claims_with_unknown_support"] == ["CL-unknown-support: EP-unknown"]
    assert audit["claims_without_provenance"] == ["CL-no-provenance"]
    assert audit["claim_temporal_leaks"] == [
        f"CL-temporal-leak: available_from precedes support {episode.episode_id}"
    ]
    assert audit["single_support_claims_without_contradictions"] == [
        "CL-no-provenance",
        "CL-temporal-leak",
        "CL-single-support-warning",
    ]


def test_coverage_audit_requires_current_vector_index_and_synced_warehouse(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("Coverage audit derivative state note.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    ResearchStore(tmp_path).accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")

    passed = audit_coverage(tmp_path)

    assert passed["passed"] is True
    assert passed["coverage_complete"] is True
    assert passed["vector_index_current"] is True
    assert passed["warehouse_synced"] is True
    assert passed["warehouse_research_episode_count"] == 1
    assert passed["warehouse_missing_files"] == []
    assert passed["warehouse_unreadable_files"] == []
    assert passed["warehouse_required_files_present"] is True

    (tmp_path / "memory" / "vector_index" / "episodes.jsonl").write_text(
        "tampered vector payload\n",
        encoding="utf-8",
    )
    WarehouseStore(tmp_path).write_empty("research_episodes.parquet")
    (tmp_path / "warehouse" / "events.parquet").unlink()
    (tmp_path / "warehouse" / "event_sources.parquet").write_text(
        "not a parquet file",
        encoding="utf-8",
    )

    failed = audit_coverage(tmp_path)

    assert failed["passed"] is False
    assert failed["coverage_complete"] is True
    assert failed["vector_index_current"] is False
    assert failed["warehouse_synced"] is False
    assert failed["warehouse_required_files_present"] is False
    assert failed["warehouse_missing_files"] == ["events.parquet"]
    assert failed["warehouse_unreadable_files"] == ["event_sources.parquet"]
    assert "vector_index: status is invalid" in failed["findings"]
    assert (
        "warehouse: research_episodes.parquet count 0 != accepted_episode_count 1"
        in failed["findings"]
    )
    assert "warehouse: missing required parquet file: events.parquet" in failed["findings"]
    assert (
        "warehouse: unreadable required parquet file: event_sources.parquet"
        in failed["findings"]
    )


def test_brain_audit_validates_mechanism_memory_cases_and_provenance(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("Mechanism audit note for 2030-01-10.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    ResearchStore(tmp_path).accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    mechanisms = [
        MechanismMemory(
            mechanism_id="MM-no-cases",
            natural_language_description="case-free mechanism should fail audit",
            provenance=[],
        ),
        MechanismMemory(
            mechanism_id="MM-unknown-success",
            natural_language_description="unknown success case should fail audit",
            successful_cases=["EP-unknown"],
            provenance=[],
        ),
    ]
    (tmp_path / "memory" / "mechanisms" / "current" / "mechanisms.jsonl").write_text(
        "".join(mechanism.model_dump_json() + "\n" for mechanism in mechanisms),
        encoding="utf-8",
    )

    audit = audit_brain(tmp_path)

    assert audit["passed"] is False
    assert audit["mechanisms_without_cases"] == ["MM-no-cases"]
    assert audit["mechanisms_with_unknown_success_cases"] == [
        "MM-unknown-success: EP-unknown"
    ]
    assert audit["mechanisms_without_provenance"] == [
        "MM-no-cases",
        "MM-unknown-success",
    ]


def test_brain_audit_cli_fails_on_hard_claim_findings(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("CLI audit mechanism note for 2030-01-10.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    ResearchStore(tmp_path).accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    claim = MemoryClaim(
        claim_id="CL-cli-no-provenance",
        statement="CLI audit should fail hard findings even when coverage is complete.",
        mechanism="cli audit exit code",
        scope="audit fixture",
        support_episode_ids=[episode.episode_id],
        available_from=episode.available_from,
        provenance=[],
    )
    (tmp_path / "brain" / "current" / "claims.jsonl").write_text(
        claim.model_dump_json() + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(typer.Exit) as brain_exit:
        cli_brain_audit()

    assert brain_exit.value.exit_code == 1
    first_output = capsys.readouterr().out
    assert '"coverage_complete": true' in first_output
    assert '"passed": false' in first_output
    assert "CL-cli-no-provenance" in first_output

    with pytest.raises(typer.Exit) as coverage_exit:
        audit_coverage_cmd()

    assert coverage_exit.value.exit_code == 1
    second_output = capsys.readouterr().out
    assert '"coverage_complete": true' in second_output
    assert '"passed": false' in second_output
    assert "CL-cli-no-provenance" in second_output


def test_brain_update_requires_accepted_episode(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    source = tmp_path / "research_20300110.md"
    source.write_text("Unaccepted update note for 2030-01-10.", encoding="utf-8")
    episode = ResearchImporter(tmp_path).import_path(source, mode="semantic")
    compiler = BrainCompiler(tmp_path)

    try:
        compiler.update(episode_id=episode.episode_id)
    except ValueError as exc:
        assert "brain update requires an accepted episode" in str(exc)
    else:
        raise AssertionError("brain update should reject unaccepted episodes")

    ResearchStore(tmp_path).accept(episode.episode_id)
    manifest = compiler.update(episode_id=episode.episode_id)

    assert manifest.accepted_episode_count == 1
    assert episode.episode_id in manifest.covered_episode_ids


def test_brain_update_incrementally_merges_new_episode_without_full_rebuild(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    store = ResearchStore(tmp_path)
    compiler = BrainCompiler(tmp_path)

    source_a = tmp_path / "research_20300110.md"
    source_a.write_text("First incremental update note for 2030-01-10.", encoding="utf-8")
    episode_a = ResearchImporter(tmp_path).import_path(source_a, mode="semantic")
    store.accept(episode_a.episode_id)
    first_manifest = compiler.rebuild(mode="full")

    source_b = tmp_path / "research_20300111.md"
    source_b.write_text("Second incremental update note for 2030-01-11.", encoding="utf-8")
    episode_b = ResearchImporter(tmp_path).import_path(source_b, mode="semantic")
    store.accept(episode_b.episode_id)

    def fail_rebuild(self: BrainCompiler, *, mode: str = "full") -> BrainManifest:
        raise AssertionError("brain update should not fall back to full rebuild")

    monkeypatch.setattr(BrainCompiler, "rebuild", fail_rebuild)

    updated = compiler.update(episode_id=episode_b.episode_id)
    brain_audit = audit_brain(tmp_path)

    assert updated.brain_version != first_manifest.brain_version
    assert updated.build_mode == "incremental"
    assert updated.last_full_rebuild_at == first_manifest.created_at
    assert updated.updated_episode_id == episode_b.episode_id
    assert updated.accepted_episode_count == 2
    assert updated.covered_episode_ids == [
        episode_a.episode_id,
        episode_b.episode_id,
    ]
    assert updated.coverage_complete is True
    assert (tmp_path / "brain" / "HEAD").read_text(encoding="utf-8").strip() == updated.brain_version
    assert (tmp_path / "brain" / "snapshots" / updated.brain_version).exists()
    assert (tmp_path / "memory" / "mechanisms" / updated.brain_version).exists()
    assert (tmp_path / "memory" / "shard_brains" / updated.brain_version).exists()
    claims_payload = (tmp_path / "brain" / "current" / "claims.jsonl").read_text(
        encoding="utf-8"
    )
    mechanisms_payload = (
        tmp_path / "memory" / "mechanisms" / "current" / "mechanisms.jsonl"
    ).read_text(encoding="utf-8")
    shard_manifest = read_json(tmp_path / "memory" / "shard_brains" / "current" / "manifest.json")
    coverage = audit_coverage(tmp_path)

    assert episode_b.episode_id in claims_payload
    assert episode_b.episode_id in mechanisms_payload
    assert shard_manifest["covered_episode_ids"] == [
        episode_a.episode_id,
        episode_b.episode_id,
    ]
    assert coverage["passed"] is True
    assert coverage["vector_index_current"] is True
    assert coverage["warehouse_synced"] is True
    assert brain_audit["brain_build_mode"] == "incremental"
    assert brain_audit["updated_episode_id"] == episode_b.episode_id
    assert brain_audit["last_full_rebuild"] == first_manifest.created_at.isoformat()
    assert WarehouseStore(tmp_path).counts()["research_episodes.parquet"] == 2


def test_semantic_import_uses_structured_llm_output_and_writes_trace(tmp_path) -> None:
    source = tmp_path / "freeform_notes.md"
    source.write_text(
        "Free-form research note without a parseable date.\n"
        "Second sentence should be covered by source segment audit.",
        encoding="utf-8",
    )
    llm = RecordingSemanticLLM()

    episode = ResearchImporter(tmp_path, llm=llm).import_path(source, mode="semantic")

    assert len(llm.calls) == 1
    assert llm.calls[0]["purpose"] == "research_import.semantic"
    assert llm.calls[0]["response_model"] is SemanticResearchDraft
    assert episode.trade_date == date(2040, 2, 3)
    assert episode.available_from.date() == next_trading_day(episode.trade_date)
    assert episode.price_source_snapshot == {"source": "recording-test"}
    assert episode.provenance[0].source_type == "semantic_llm_structured_import"
    preserved_raw = tmp_path / episode.provenance[0].uri
    semantic_audit = episode.input_audit["semantic_import"]
    assert preserved_raw.exists()
    assert semantic_audit["source_path"] == episode.provenance[0].uri
    assert semantic_audit["source_sha256"] == file_sha256(preserved_raw)
    assert semantic_audit["source_text_sha256"] == sha256_text(
        preserved_raw.read_text(encoding="utf-8")
    )
    assert semantic_audit["source_segment_count"] == len(
        semantic_audit["source_segments"]
    )
    assert semantic_audit["source_segment_count"] >= 2
    first_segment = semantic_audit["source_segments"][0]
    assert first_segment["excerpt"] == "Free-form research note without a parseable date."
    assert first_segment["text_sha256"] == sha256_text(str(first_segment["excerpt"]))
    assert semantic_audit["output_field_source_ids"] == {
        "blind_analysis.summary": [episode.provenance[0].source_id],
        "blind_analysis.open_world_mechanisms": [episode.provenance[0].source_id],
        "blind_analysis.initial_uncertainties": [episode.provenance[0].source_id],
    }

    traces = list((tmp_path / "runs" / "traces").glob("TRACE-*.json"))
    assert len(traces) == 1
    trace = read_json(traces[0])
    assert trace["purpose"] == "research_import.semantic"
    assert trace["operation"] == "generate_structured"
    assert trace["prompt_version"] == "semantic_import.v1"
    assert trace["input"]["response_model"] == "SemanticResearchDraft"
    assert trace["output"]["trade_date"] == "2040-02-03"
