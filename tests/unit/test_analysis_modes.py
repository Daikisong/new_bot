from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.context.session_pack import export_session_pack
from news_scalping_lab.contracts.models import BlindAnalysis, PathType, ResearchEpisode
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST


def test_normalize_analysis_mode_accepts_only_supported_modes() -> None:
    assert normalize_analysis_mode(" exhaustive ") == "exhaustive"
    assert normalize_analysis_mode("Brain") == "brain"
    assert normalize_analysis_mode("FAST") == "fast"

    with pytest.raises(ValueError, match="analysis mode must be one of"):
        normalize_analysis_mode("retrieval")


def test_context_assembler_rejects_unknown_analysis_mode(tmp_path) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))

    with pytest.raises(ValueError, match="analysis mode must be one of"):
        ContextAssembler(tmp_path).assemble(
            mode="typo",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            run_seed="seed",
        )


def test_context_run_id_changes_when_available_research_changes(tmp_path) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    assembler = ContextAssembler(tmp_path)

    before = assembler.assemble(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="same-news-and-model",
    )

    episode = ResearchEpisode(
        episode_id="EP-available",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Available lesson."),
        available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)

    after = ContextAssembler(tmp_path).assemble(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="same-news-and-model",
    )

    assert before.run_id != after.run_id
    assert before.accepted_episode_count == 0
    assert before.total_accepted_episode_count == 0
    assert before.available_episode_count == 0
    assert before.unavailable_episode_count == 0
    assert before.unavailable_episode_ids == []
    assert after.accepted_episode_count == 1
    assert after.total_accepted_episode_count == 1
    assert after.available_episode_count == 1
    assert after.unavailable_episode_count == 0
    assert after.unavailable_episode_ids == []
    assert after.swept_episode_ids == ["EP-available"]

    future_episode = ResearchEpisode(
        episode_id="EP-future",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 10, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Future lesson."),
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
    )
    store.save_episode(future_episode)
    store.accept(future_episode.episode_id)

    with_unavailable = ContextAssembler(tmp_path).assemble(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="same-news-and-model",
    )

    assert with_unavailable.run_id == after.run_id
    assert with_unavailable.accepted_episode_count == 1
    assert with_unavailable.total_accepted_episode_count == 2
    assert with_unavailable.available_episode_count == 1
    assert with_unavailable.unavailable_episode_count == 1
    assert with_unavailable.unavailable_episode_ids == ["EP-future"]
    assert with_unavailable.swept_episode_ids == ["EP-available"]


def test_context_assembler_filters_future_and_unknown_retrieved_episode_ids(
    tmp_path,
) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    store = ResearchStore(tmp_path)
    available = ResearchEpisode(
        episode_id="EP-available",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Available lesson."),
        available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
    )
    future = ResearchEpisode(
        episode_id="EP-future",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 10, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Future lesson."),
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
    )
    for episode in (available, future):
        store.save_episode(episode)
        store.accept(episode.episode_id)

    manifest = ContextAssembler(tmp_path).assemble(
        mode="fast",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="same-news-and-model",
        retrieved_episode_ids=[
            "EP-available",
            "EP-future",
            "EP-missing",
            "EP-available",
        ],
    )

    assert manifest.retrieved_episode_ids == ["EP-available"]
    assert manifest.excluded_retrieved_episode_ids == ["EP-future", "EP-missing"]
    assert manifest.unavailable_episode_ids == ["EP-future"]


@pytest.mark.asyncio
async def test_daily_analyzer_rejects_unknown_analysis_mode_before_writing_outputs(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)

    with pytest.raises(ValueError, match="analysis mode must be one of"):
        await DailyAnalyzer(settings).analyze(
            news_csv=tmp_path / "missing.csv",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            mode="semi-fast",
            web_search=False,
        )

    assert list((tmp_path / "predictions").glob("*.json")) == []
    assert list((tmp_path / "runs" / "manifests").glob("*.json")) == []


def test_session_pack_rejects_unknown_analysis_mode_before_writing_pack(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)

    with pytest.raises(ValueError, match="analysis mode must be one of"):
        export_session_pack(
            settings,
            news_csv=tmp_path / "missing.csv",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            mode="unknown",
        )

    assert not (tmp_path / "session_packs" / "2030-01-10").exists()


@pytest.mark.asyncio
async def test_fast_mode_keeps_open_world_candidates_when_retrieval_misses(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "fast_miss_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","UnseenFastCo, new catalyst",'
        '"No accepted research episode exists, so retrieval must miss without blocking."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="fast",
        web_search=False,
    )

    manifest = analysis.context_manifest
    prediction = analysis.blind_prediction
    assert manifest.mode == "fast"
    assert manifest.accepted_episode_count == 0
    assert manifest.swept_episode_count == 0
    assert manifest.retrieved_episode_ids == []
    assert prediction.candidates
    assert {candidate.path_type for candidate in prediction.candidates} >= {
        PathType.SINGLE_EVENT,
        PathType.THEME_BENEFICIARY,
        PathType.CONTINUATION,
    }
    beneficiary = next(
        candidate
        for candidate in prediction.candidates
        if candidate.path_type == PathType.THEME_BENEFICIARY
    )
    assert beneficiary.memory_episode_ids == []
    assert "memory has no exact precedent" in beneficiary.novel_reasoning
    assert "UnseenFastCo" in {candidate.company_name for candidate in prediction.candidates}
