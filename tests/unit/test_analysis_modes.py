from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.context.session_pack import export_session_pack
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
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
    assert after.accepted_episode_count == 1
    assert after.swept_episode_ids == ["EP-available"]


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
