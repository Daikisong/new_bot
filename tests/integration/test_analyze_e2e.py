from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, read_json


@pytest.mark.asyncio
async def test_analyze_retrieval_miss_still_outputs_candidates(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","가상회사, 신규 사업 검토","상장 여부와 직접성을 검증해야 한다."\n',
        encoding="utf-8",
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    analyzer = DailyAnalyzer(settings, retrieval=LocalRetrievalStore(tmp_path, force_empty=True))
    analysis = await analyzer.analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=True,
    )

    assert (
        analysis.context_manifest.accepted_episode_count
        == analysis.context_manifest.swept_episode_count
    )
    assert analysis.blind_prediction.candidates
    assert (tmp_path / analysis.report_path).exists()
    assert (tmp_path / analysis.prediction_path).exists()
    assert audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))["passed"]
    assert audit_provenance(tmp_path)["passed"]


@pytest.mark.asyncio
async def test_exhaustive_analyze_persists_all_memory_sweep_shards(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.shard_episode_count = 1
    ensure_project_dirs(settings)
    importer = ResearchImporter(tmp_path)
    store = ResearchStore(tmp_path)
    for day in (8, 9):
        source = tmp_path / f"research_203001{day}.md"
        source.write_text(
            f"# Research 2030-01-{day}\n\nMechanism notes and counterexamples.",
            encoding="utf-8",
        )
        episode = await importer.import_path_async(source, mode="semantic")
        store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")

    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-11","08:00:00","SampleCo, new facility","Needs open-world review."\n',
        encoding="utf-8",
    )
    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 11),
        cutoff_at=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    assert manifest.accepted_episode_count == 2
    assert manifest.swept_episode_count == 2
    assert manifest.memory_sweep_shard_count == 2
    assert len(manifest.memory_sweep_artifacts) == 2
    swept_from_artifacts: set[str] = set()
    for artifact in manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        swept_from_artifacts.update(payload["episode_ids"])
    assert swept_from_artifacts == set(manifest.swept_episode_ids)
