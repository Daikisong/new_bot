from __future__ import annotations

from datetime import date, datetime, time

from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.session_pack import export_session_pack
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, read_json


def _episode(
    episode_id: str,
    *,
    summary: str,
    available_day: date,
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
        available_from=datetime.combine(available_day, time(0, 0, 0), tzinfo=KST),
    )


def test_session_pack_manifest_records_omissions_and_hashes(tmp_path) -> None:
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
    for episode in (small, large, future):
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

    output_dir = export_session_pack(
        settings,
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        mode="brain",
    )

    manifest = read_json(output_dir / "manifest.json")
    memory_cases = (output_dir / "memory_cases.md").read_text(encoding="utf-8")
    research_brain = (output_dir / "research_brain.md").read_text(encoding="utf-8")

    assert manifest["accepted_episode_count"] == 3
    assert manifest["available_episode_count"] == 2
    assert manifest["included_episode_ids"] == ["EP-small"]
    assert manifest["shard_brain_count"] == 2
    assert set(manifest["shard_brain_files"]) == {
        "memory/shard_brains/current/shard_0001.md",
        "memory/shard_brains/current/shard_0002.md",
    }
    assert set(manifest["shard_brain_file_hashes"]) == set(manifest["shard_brain_files"])
    assert set(manifest["omitted_episode_ids"]) == {"EP-large", "EP-future"}
    assert manifest["unavailable_episode_ids"] == ["EP-future"]
    assert {item["reason"] for item in manifest["truncations"]} == {
        "session_pack_token_budget_exceeded",
        "episode_available_from_after_trade_date",
    }
    assert "session pack omitted available episodes due to token budget" in manifest["errors"]
    assert "session pack excluded future-unavailable episodes" in manifest["errors"]
    assert "EP-small" in memory_cases
    assert "EP-large" not in memory_cases
    assert "EP-future" not in memory_cases
    assert "# Shard Brain Summaries" in research_brain
    assert "Shard Brain 0001" in research_brain
    assert "Shard Brain 0002" in research_brain
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
