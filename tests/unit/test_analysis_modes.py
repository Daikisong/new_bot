from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.context.session_pack import export_session_pack
from news_scalping_lab.inference.analyzer import DailyAnalyzer
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
