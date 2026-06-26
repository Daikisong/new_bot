from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import Candidate, PathType
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.utils import KST, read_json


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

    first_analysis = await _analyze_region_event(settings, "RegionAlpha", trade_day, cutoff)
    second_analysis = await _analyze_region_event(settings, "RegionBeta", trade_day, cutoff)
    first = first_analysis.blind_prediction
    second = second_analysis.blind_prediction

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
    first_pass = _open_world_first_analysis(settings.project_root, first_analysis.run_id)
    second_pass = _open_world_first_analysis(settings.project_root, second_analysis.run_id)
    assert first_pass["beneficiary_investigation_questions"] == (
        second_pass["beneficiary_investigation_questions"]
    )
    first_pass_questions = " ".join(first_pass["beneficiary_investigation_questions"])
    for mechanism in (
        "construction/execution",
        "supply-chain",
        "power",
        "water",
        "logistics",
        "regional-asset",
        "market-memory",
    ):
        assert mechanism in first_pass_questions


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
    return analysis


def _open_world_first_analysis(project_root, run_id: str):
    manifest = read_json(project_root / "runs" / "manifests" / f"{run_id}.json")
    return read_json(project_root / manifest["open_world_first_analysis_artifact"])


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
