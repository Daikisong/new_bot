from __future__ import annotations

from datetime import date, datetime, time

import pytest
from typer.testing import CliRunner

from news_scalping_lab.cli import app
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.session_pack import SessionPackBudgetExceededError, export_session_pack
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, read_json

RUNNER = CliRunner()


def _episode(
    episode_id: str,
    *,
    summary: str,
    available_day: date,
    available_time: time = time(0, 0, 0),
) -> ResearchEpisode:
    trade_day = date(2030, 1, 9)
    return ResearchEpisode(
        episode_id=episode_id,
        trade_date=trade_day,
        cutoff_at=datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST),
        created_at=datetime.combine(trade_day, time(16, 0, 0), tzinfo=KST),
        research_version="test-v1",
        input_news_files=[],
        input_news_hashes=[],
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(
            summary=summary,
            open_world_mechanisms=["current evidence -> open-world path"],
        ),
        available_from=datetime.combine(available_day, available_time, tzinfo=KST),
    )


def test_session_pack_blocks_when_available_episode_exceeds_budget(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.session_pack_token_budget = 500
    ensure_project_dirs(settings)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","PackCo, catalyst","Session pack current news."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    small = _episode("EP-small", summary="Short useful lesson.", available_day=date(2030, 1, 10))
    large = _episode(
        "EP-large",
        summary="Long lesson. " * 300,
        available_day=date(2030, 1, 10),
    )
    future = _episode("EP-future", summary="Future postmortem.", available_day=date(2030, 1, 11))
    after_cutoff = _episode(
        "EP-after-cutoff",
        summary="Same-day after-cutoff postmortem.",
        available_day=date(2030, 1, 10),
        available_time=time(9, 30, 0),
    )
    for episode in (small, large, future, after_cutoff):
        store.save_episode(episode)
        store.accept(episode.episode_id)
    shard_dir = tmp_path / "memory" / "shard_brains" / "current"
    shard_dir.mkdir(parents=True)
    (shard_dir / "shard_0001.md").write_text(
        "# Shard Brain 0001\n\nEP-small\n",
        encoding="utf-8",
    )
    (shard_dir / "shard_0002.md").write_text(
        "# Shard Brain 0002\n\nEP-large\n",
        encoding="utf-8",
    )

    with pytest.raises(SessionPackBudgetExceededError) as exc_info:
        export_session_pack(
            settings,
            news_csv=news_csv,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            mode="brain",
        )

    output_dir = exc_info.value.output_dir
    manifest = read_json(output_dir / "manifest.json")
    memory_cases = (output_dir / "memory_cases.md").read_text(encoding="utf-8")
    research_brain = (output_dir / "research_brain.md").read_text(encoding="utf-8")

    assert manifest["blocked"] is True
    assert manifest["accepted_episode_count"] == 4
    assert manifest["cutoff_at"] == "2030-01-10T08:59:59+09:00"
    assert manifest["available_episode_count"] == 2
    assert manifest["included_episode_ids"] == []
    assert manifest["brain_version"].startswith("brain-asof-")
    assert all(
        path.startswith("runs/checkpoints/brain_context/SESSION-")
        for path in manifest["brain_files"]
    )
    assert manifest["shard_brain_count"] == 1
    assert all(
        path.startswith("runs/checkpoints/brain_context/SESSION-")
        for path in manifest["shard_brain_files"]
    )
    assert set(manifest["brain_file_hashes"]) == set(manifest["brain_files"])
    assert set(manifest["shard_brain_file_hashes"]) == set(manifest["shard_brain_files"])
    assert set(manifest["omitted_episode_ids"]) == {
        "EP-small",
        "EP-large",
        "EP-future",
        "EP-after-cutoff",
    }
    assert set(manifest["unavailable_episode_ids"]) == {"EP-future", "EP-after-cutoff"}
    assert {item["reason"] for item in manifest["truncations"]} == {
        "session_pack_token_budget_exceeded",
        "episode_available_from_after_cutoff",
    }
    assert "session pack omitted available episodes due to token budget" in manifest["errors"]
    assert "session pack excluded future-unavailable episodes" in manifest["errors"]
    assert "EP-small" not in memory_cases
    assert "EP-large" not in memory_cases
    assert "EP-future" not in memory_cases
    assert "EP-after-cutoff" not in memory_cases
    assert "# Shard Brain Summaries" in research_brain
    assert "Shard Brain 0001" in research_brain
    assert "EP-small" in research_brain
    assert "EP-large" in research_brain
    assert set(manifest["pack_file_hashes"]) == {
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "current_news.md",
        "company_memory.md",
        "market_context.md",
    }
    assert manifest["pack_sha256"]
    assert manifest["token_counts"]["memory_cases.md"] > 0
    assert "session pack omitted available episodes due to token budget" in exc_info.value.errors


def test_session_pack_cli_exits_nonzero_when_available_episode_exceeds_budget(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.session_pack_token_budget = 500
    ensure_project_dirs(settings)
    (tmp_path / "configs" / "default.yaml").write_text(
        "limits:\n  session_pack_token_budget: 500\n",
        encoding="utf-8",
    )
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","PackCo, catalyst","Session pack current news."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    for episode in (
        _episode("EP-small", summary="Short useful lesson.", available_day=date(2030, 1, 10)),
        _episode("EP-large", summary="Long lesson. " * 300, available_day=date(2030, 1, 10)),
    ):
        store.save_episode(episode)
        store.accept(episode.episode_id)
    monkeypatch.chdir(tmp_path)

    result = RUNNER.invoke(
        app,
        [
            "context",
            "export-session-pack",
            "--news",
            str(news_csv),
            "--trade-date",
            "2030-01-10",
            "--cutoff",
            "2030-01-10T08:59:59+09:00",
            "--mode",
            "brain",
        ],
    )

    assert result.exit_code == 1
    assert "session pack omitted available episodes due to token budget" in result.output
    manifest = read_json(tmp_path / "session_packs" / "2030-01-10" / "manifest.json")
    assert manifest["blocked"] is True


def test_session_pack_uses_as_of_brain_context_when_current_contains_future_episode(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","PackCo, catalyst","Session pack current news."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    available = _episode(
        "EP-available",
        summary="Available lesson.",
        available_day=date(2030, 1, 10),
    )
    future = _episode(
        "EP-after-cutoff",
        summary="Future after cutoff lesson.",
        available_day=date(2030, 1, 10),
        available_time=time(9, 30, 0),
    )
    for episode in (available, future):
        store.save_episode(episode)
        store.accept(episode.episode_id)
    brain_dir = tmp_path / "brain" / "current"
    brain_dir.mkdir(parents=True, exist_ok=True)
    (brain_dir / "00_world_model.md").write_text(
        "Unsafe future context EP-after-cutoff",
        encoding="utf-8",
    )

    output_dir = export_session_pack(
        settings,
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="brain",
    )

    manifest = read_json(output_dir / "manifest.json")
    research_brain = (output_dir / "research_brain.md").read_text(encoding="utf-8")
    assert manifest["brain_version"].startswith("brain-asof-")
    assert all(
        path.startswith("runs/checkpoints/brain_context/SESSION-")
        for path in manifest["brain_files"]
    )
    assert "session pack excluded future-unavailable episodes" in manifest["errors"]
    assert "EP-available" in research_brain
    assert "EP-after-cutoff" not in research_brain
    assert not any("context file contains future episode" in item for item in manifest["errors"])
