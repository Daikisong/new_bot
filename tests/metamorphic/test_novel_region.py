from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import Candidate, PathType
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.utils import KST


def test_region_name_change_keeps_mechanism_shape() -> None:
    llm = DeterministicMockLLMProvider()
    first = llm.infer_mechanisms("RegionA large advanced industrial campus construction.")
    second = llm.infer_mechanisms("RegionB large advanced industrial campus construction.")

    assert first == second
    assert "catalyst" in first[0]


@pytest.mark.asyncio
async def test_region_name_change_keeps_analysis_path_shape(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    BrainCompiler(tmp_path).rebuild(mode="full")
    trade_day = date(2030, 1, 10)
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)

    first = await _analyze_region_event(settings, "RegionAlpha", trade_day, cutoff)
    second = await _analyze_region_event(settings, "RegionBeta", trade_day, cutoff)

    assert first.blind_analysis.open_world_mechanisms == second.blind_analysis.open_world_mechanisms
    assert [candidate.path_type for candidate in first.candidates] == [
        candidate.path_type for candidate in second.candidates
    ]
    assert _candidate_signature(_by_path(first.candidates, PathType.THEME_BENEFICIARY)) == (
        _candidate_signature(_by_path(second.candidates, PathType.THEME_BENEFICIARY))
    )
    assert _candidate_signature(_by_path(first.candidates, PathType.CONTINUATION)) == (
        _candidate_signature(_by_path(second.candidates, PathType.CONTINUATION))
    )
    assert first.candidates[0].company_name != second.candidates[0].company_name


async def _analyze_region_event(
    settings: Settings,
    region_name: str,
    trade_day: date,
    cutoff: datetime,
):
    csv_path = settings.project_root / f"{region_name}.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        f'1,1,"{trade_day.isoformat()}","08:00:00",'
        f'"{region_name} advanced industrial campus construction",'
        '"Large facility investment creates direct and indirect beneficiary paths."\n',
        encoding="utf-8",
    )
    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=csv_path,
        trade_date=trade_day,
        cutoff_at=cutoff,
        mode="exhaustive",
        web_search=False,
    )
    return analysis.blind_prediction


def _by_path(candidates: list[Candidate], path_type: PathType) -> Candidate:
    for candidate in candidates:
        if candidate.path_type == path_type:
            return candidate
    raise AssertionError(f"missing candidate path: {path_type}")


def _candidate_signature(candidate: Candidate) -> tuple[PathType, str, tuple[str, ...], str]:
    return (
        candidate.path_type,
        candidate.thesis,
        tuple(candidate.causal_chain),
        candidate.why_now,
    )
