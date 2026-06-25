from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, file_sha256, read_json


def _src_hashes(repo_root: Path) -> dict[str, str]:
    source_root = repo_root / "src"
    if not source_root.exists():
        return {}
    return {
        path.relative_to(repo_root).as_posix(): file_sha256(path)
        for path in sorted(source_root.rglob("*.py"))
    }


def _file_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


@pytest.mark.asyncio
async def test_research_addition_updates_brain_and_future_context_without_source_changes(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_before = _src_hashes(repo_root)
    settings = Settings(project_root=tmp_path)
    settings.limits.shard_episode_count = 1
    ensure_project_dirs(settings)
    project_source_dir = tmp_path / "src" / "news_scalping_lab"
    project_source_dir.mkdir(parents=True)
    (project_source_dir / "sentinel.py").write_text(
        '"""Sentinel production source that research import must not edit."""\n',
        encoding="utf-8",
    )
    project_src_before = _src_hashes(tmp_path)
    importer = ResearchImporter(tmp_path)
    store = ResearchStore(tmp_path)
    compiler = BrainCompiler(tmp_path)

    first_source = tmp_path / "research_20300108.md"
    first_source.write_text(
        "# Research 2030-01-08\n\n"
        "Blind notes: a direct company catalyst can become relevant after ownership review.\n"
        "Postmortem: leader selection failed when the relation was only narrative.",
        encoding="utf-8",
    )
    first_episode = await importer.import_path_async(first_source, mode="semantic")
    store.accept(first_episode.episode_id)
    first_manifest = compiler.rebuild(mode="full")

    second_source = tmp_path / "research_20300109.md"
    second_source.write_text(
        "# Research 2030-01-09\n\n"
        "Blind notes: a new infrastructure path produced a candidate absent from memory.\n"
        "Postmortem: counterexample kept the indirect beneficiary thesis tentative.",
        encoding="utf-8",
    )
    project_before_second_import = _file_hashes(tmp_path)
    brain_before_second_import = _file_hashes(tmp_path / "brain")
    memory_before_second_import = _file_hashes(tmp_path / "memory")
    second_episode = await importer.import_path_async(second_source, mode="semantic")
    store.accept(second_episode.episode_id)
    second_manifest = compiler.update(episode_id=second_episode.episode_id)

    assert _src_hashes(repo_root) == src_before
    assert _src_hashes(tmp_path) == project_src_before
    changed_after_second_import = _changed_paths(
        project_before_second_import,
        _file_hashes(tmp_path),
    )
    assert not any(path.startswith("src/") for path in changed_after_second_import)
    assert any(path.startswith("research/") for path in changed_after_second_import)
    assert _file_hashes(tmp_path / "brain") != brain_before_second_import
    assert _file_hashes(tmp_path / "memory") != memory_before_second_import
    assert second_manifest.brain_version != first_manifest.brain_version
    assert second_manifest.accepted_episode_count == first_manifest.accepted_episode_count + 1
    assert second_manifest.covered_episode_count == 2
    assert len(second_manifest.claim_ids) == len(first_manifest.claim_ids) + 1
    assert second_episode.episode_id in second_manifest.covered_episode_ids

    coverage = read_json(tmp_path / "brain" / "current" / "coverage_manifest.json")
    assert coverage["coverage_complete"] is True
    assert second_episode.episode_id in coverage["covered_episode_ids"]

    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-11","08:00:00","AdditionFlowCo, new catalyst",'
        '"Future context must include the newly accepted research episode."\n',
        encoding="utf-8",
    )
    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 11),
        cutoff_at=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    assert manifest.accepted_episode_count == 2
    assert manifest.swept_episode_count == 2
    assert set(manifest.swept_episode_ids) == {
        first_episode.episode_id,
        second_episode.episode_id,
    }
    assert second_episode.episode_id in manifest.swept_episode_ids
    assert _src_hashes(repo_root) == src_before
    assert _src_hashes(tmp_path) == project_src_before
