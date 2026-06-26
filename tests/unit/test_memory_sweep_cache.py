from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import pytest

from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.sweep import MemorySweeper
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, read_json, write_json


def test_memory_sweep_cache_key_uses_news_prompt_and_model_config(tmp_path) -> None:
    _seed_accepted_episodes(tmp_path, count=2)
    BrainCompiler(tmp_path).rebuild(mode="full")
    cutoff = datetime(2030, 1, 12, 8, 59, 59, tzinfo=KST)
    sweeper = MemorySweeper(tmp_path, shard_episode_count=10)

    first = sweeper.sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-cache-first",
        current_news_texts=["same current news"],
        first_pass_mechanisms=["same mechanism"],
        model_config={"provider": "mock"},
        prompt_version="memory-sweep-test-v1",
    )
    repeat = sweeper.sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-cache-repeat",
        current_news_texts=["same current news"],
        first_pass_mechanisms=["same mechanism"],
        model_config={"provider": "mock"},
        prompt_version="memory-sweep-test-v1",
    )
    changed_news = sweeper.sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-cache-news",
        current_news_texts=["changed current news"],
        first_pass_mechanisms=["same mechanism"],
        model_config={"provider": "mock"},
        prompt_version="memory-sweep-test-v1",
    )
    changed_prompt = sweeper.sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-cache-prompt",
        current_news_texts=["same current news"],
        first_pass_mechanisms=["same mechanism"],
        model_config={"provider": "mock"},
        prompt_version="memory-sweep-test-v2",
    )
    changed_model = sweeper.sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-cache-model",
        current_news_texts=["same current news"],
        first_pass_mechanisms=["same mechanism"],
        model_config={"provider": "other"},
        prompt_version="memory-sweep-test-v1",
    )

    assert first.cache_hits == 0
    assert repeat.cache_hits == 1
    assert changed_news.cache_hits == 0
    assert changed_prompt.cache_hits == 0
    assert changed_model.cache_hits == 0
    repeat_payload = read_json(tmp_path / repeat.artifact_paths[0])
    assert repeat_payload["from_cache"] is True
    assert repeat_payload["episode_ids"] == ["EP-cache-000", "EP-cache-001"]
    assert set(repeat_payload["episode_shard_source_hashes"]) == {
        "EP-cache-000",
        "EP-cache-001",
    }


def test_memory_sweep_cache_key_uses_episode_source_hashes(tmp_path) -> None:
    _seed_accepted_episodes(tmp_path, count=1)
    BrainCompiler(tmp_path).rebuild(mode="full")
    cutoff = datetime(2030, 1, 12, 8, 59, 59, tzinfo=KST)
    sweeper = MemorySweeper(tmp_path, shard_episode_count=10)

    first = sweeper.sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-source-hash-first",
        current_news_texts=["same current news"],
        first_pass_mechanisms=["same mechanism"],
        model_config={"provider": "mock"},
    )
    first_payload = read_json(tmp_path / first.artifact_paths[0])
    accepted_path = tmp_path / "research" / "accepted" / "EP-cache-000.json"
    accepted_payload = read_json(accepted_path)
    accepted_payload["blind_analysis"]["summary"] = "Mutated lesson with same episode ID."
    write_json(accepted_path, accepted_payload)

    after_mutation = sweeper.sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-source-hash-mutated",
        current_news_texts=["same current news"],
        first_pass_mechanisms=["same mechanism"],
        model_config={"provider": "mock"},
    )

    assert first.cache_hits == 0
    assert after_mutation.cache_hits == 0
    mutated_payload = read_json(tmp_path / after_mutation.artifact_paths[0])
    assert mutated_payload["from_cache"] is False
    assert (
        mutated_payload["episode_shard_source_hashes"]["EP-cache-000"]
        != first_payload["episode_shard_source_hashes"]["EP-cache-000"]
    )
    assert mutated_payload["episode_shard_sha256"] != first_payload["episode_shard_sha256"]


def test_memory_sweep_cache_key_uses_brain_version(tmp_path) -> None:
    _seed_accepted_episodes(tmp_path, count=1)
    BrainCompiler(tmp_path).rebuild(mode="full")
    cutoff = datetime(2030, 1, 12, 8, 59, 59, tzinfo=KST)
    sweeper = MemorySweeper(tmp_path, shard_episode_count=10)

    first = sweeper.sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-brain-version-first",
        current_news_texts=["same current news"],
        first_pass_mechanisms=["same mechanism"],
        model_config={"provider": "mock"},
    )
    first_payload = read_json(tmp_path / first.artifact_paths[0])
    (tmp_path / "brain" / "HEAD").write_text(
        "brain-cache-version-changed\n",
        encoding="utf-8",
    )

    changed_brain = sweeper.sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-brain-version-changed",
        current_news_texts=["same current news"],
        first_pass_mechanisms=["same mechanism"],
        model_config={"provider": "mock"},
    )

    assert first.cache_hits == 0
    assert changed_brain.cache_hits == 0
    changed_payload = read_json(tmp_path / changed_brain.artifact_paths[0])
    assert changed_payload["from_cache"] is False
    assert changed_payload["brain_version"] == "brain-cache-version-changed"
    assert changed_payload["cache_key"] != first_payload["cache_key"]


def test_memory_sweep_reuses_completed_shard_after_intermediate_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_accepted_episodes(tmp_path, count=2)
    BrainCompiler(tmp_path).rebuild(mode="full")
    cutoff = datetime(2030, 1, 12, 8, 59, 59, tzinfo=KST)
    original_build_contribution = MemorySweeper._build_contribution

    def fail_on_second_shard(self: MemorySweeper, **kwargs: Any) -> dict[str, object]:
        if kwargs["shard_index"] == 2:
            raise RuntimeError("simulated shard failure")
        return original_build_contribution(self, **kwargs)

    monkeypatch.setattr(MemorySweeper, "_build_contribution", fail_on_second_shard)
    failing_sweeper = MemorySweeper(tmp_path, shard_episode_count=1)
    with pytest.raises(RuntimeError, match="simulated shard failure"):
        failing_sweeper.sweep(
            mode="exhaustive",
            trade_date=date(2030, 1, 12),
            cutoff_at=cutoff,
            run_id="RUN-partial-failure",
            current_news_texts=["current news"],
            first_pass_mechanisms=["mechanism"],
            model_config={"provider": "mock"},
        )

    monkeypatch.setattr(MemorySweeper, "_build_contribution", original_build_contribution)
    recovered = MemorySweeper(tmp_path, shard_episode_count=1).sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 12),
        cutoff_at=cutoff,
        run_id="RUN-partial-retry",
        current_news_texts=["current news"],
        first_pass_mechanisms=["mechanism"],
        model_config={"provider": "mock"},
    )

    assert recovered.cache_hits == 1
    assert recovered.swept_episode_ids == ["EP-cache-000", "EP-cache-001"]
    first_retry_payload = read_json(tmp_path / recovered.artifact_paths[0])
    second_retry_payload = read_json(tmp_path / recovered.artifact_paths[1])
    assert first_retry_payload["from_cache"] is True
    assert second_retry_payload["from_cache"] is False


def _seed_accepted_episodes(root, *, count: int) -> None:
    ensure_project_dirs(Settings(project_root=root))
    store = ResearchStore(root)
    for index in range(count):
        episode = ResearchEpisode(
            episode_id=f"EP-cache-{index:03d}",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
            research_version="memory-sweep-cache-test-v1",
            price_source_snapshot={"source": "cache-test"},
            blind_analysis=BlindAnalysis(
                summary=f"Cache test lesson {index:03d}.",
                open_world_mechanisms=[f"cache-test mechanism {index:03d}"],
            ),
            available_from=datetime.combine(date(2030, 1, 11), time(0, 0, 0), tzinfo=KST),
        )
        store.save_episode(episode)
        store.accept(episode.episode_id)
