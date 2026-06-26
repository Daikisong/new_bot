from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import PathType
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.utils import KST, read_json


@pytest.mark.asyncio
async def test_new_company_absent_from_memory_remains_open_world_candidate(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    BrainCompiler(tmp_path).rebuild(mode="full")
    company_memory_dir = tmp_path / "memory" / "company_memory"
    assert list(company_memory_dir.glob("*.json")) == []

    trade_day = date(2030, 1, 10)
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    csv_path = tmp_path / "new_company.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00",'
        '"NovelIssuerCo, pre-cutoff direct catalyst",'
        '"A newly mentioned company requires listing, directness, and novelty checks."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=csv_path,
        trade_date=trade_day,
        cutoff_at=cutoff,
        mode="exhaustive",
        web_search=False,
    )

    direct_candidates = [
        candidate
        for candidate in analysis.blind_prediction.candidates
        if candidate.path_type == PathType.SINGLE_EVENT
        and candidate.company_name == "NovelIssuerCo"
    ]
    assert len(direct_candidates) == 1
    direct = direct_candidates[0]
    assert direct.ticker == "UNKNOWN"
    assert direct.memory_episode_ids == []
    assert direct.event_ids
    assert direct.source_urls == [f"news://{direct.event_ids[0]}"]
    assert "static list" in direct.novel_reasoning or "memory" in direct.novel_reasoning
    assert "listing status" in "; ".join(direct.counterarguments)
    assert "not listed" in direct.disconfirming_conditions

    manifest = read_json(tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json")
    assert manifest["accepted_episode_count"] == 0
    assert manifest["swept_episode_count"] == 0
    assert manifest["retrieved_episode_ids"] == []
    assert manifest["candidate_expansion_summary"][
        "requires_web_company_discovery_count"
    ] >= 3
    expansion = read_json(tmp_path / manifest["candidate_expansion_artifact"])
    single_event_findings = [
        finding
        for finding in expansion["findings"]
        if finding["path"] == "SINGLE_EVENT"
    ]
    assert single_event_findings
    assert single_event_findings[0]["requires_web_company_discovery"] is True
    assert any(
        "listed entities" in question
        for question in single_event_findings[0]["investigation_questions"]
    )
