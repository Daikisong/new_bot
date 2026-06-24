"""Exhaustive memory sweep artifacts.

The sweep makes coverage concrete: every accepted, time-available episode is
assigned to exactly one shard and produces a persisted contribution. Retrieval
misses do not affect this path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from news_scalping_lab.brain.compiler import current_brain_version
from news_scalping_lab.contracts.models import ResearchEpisode
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import read_json, sha256_text, stable_id, write_json


@dataclass(frozen=True)
class SweepResult:
    accepted_episode_count: int
    swept_episode_ids: list[str]
    artifact_paths: list[str]
    shard_count: int
    cache_hits: int
    token_counts: dict[str, int]
    errors: list[str]


class MemorySweeper:
    def __init__(self, root: Path, *, shard_episode_count: int) -> None:
        self.root = root
        self.shard_episode_count = max(1, shard_episode_count)
        self.store = ResearchStore(root)
        self.cache_dir = root / "data" / "cache" / "memory_sweep"
        self.checkpoint_dir = root / "runs" / "checkpoints" / "memory_sweep"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def sweep(
        self,
        *,
        mode: str,
        trade_date: date,
        run_id: str,
        current_news_texts: list[str],
        first_pass_mechanisms: list[str],
    ) -> SweepResult:
        accepted = self._available_episodes(trade_date)
        if mode == "fast":
            return SweepResult(
                accepted_episode_count=len(accepted),
                swept_episode_ids=[],
                artifact_paths=[],
                shard_count=0,
                cache_hits=0,
                token_counts={"memory_sweep": 0},
                errors=[],
            )

        artifacts: list[str] = []
        swept_ids: list[str] = []
        cache_hits = 0
        run_dir = self.checkpoint_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        brain_version = current_brain_version(self.root) or "none"
        news_hash = sha256_text("\n---NEWS---\n".join(current_news_texts))
        shards = list(self._shards(accepted))

        for shard_index, shard in enumerate(shards, start=1):
            episode_ids = [episode.episode_id for episode in shard]
            shard_hash = sha256_text("|".join(episode_ids))
            cache_key = stable_id("SWEEP", brain_version, news_hash, shard_hash, mode, length=16)
            cache_path = self.cache_dir / f"{cache_key}.json"
            if cache_path.exists():
                payload = read_json(cache_path)
                payload["from_cache"] = True
                cache_hits += 1
            else:
                payload = self._build_contribution(
                    mode=mode,
                    trade_date=trade_date,
                    brain_version=brain_version,
                    news_hash=news_hash,
                    shard_index=shard_index,
                    episode_count=len(shard),
                    episodes=shard,
                    first_pass_mechanisms=first_pass_mechanisms,
                )
                write_json(cache_path, payload)
            run_path = run_dir / f"shard_{shard_index:04d}.json"
            write_json(run_path, payload)
            artifacts.append(run_path.relative_to(self.root).as_posix())
            swept_ids.extend(episode_ids)

        errors: list[str] = []
        if mode == "exhaustive" and set(swept_ids) != {episode.episode_id for episode in accepted}:
            errors.append("memory sweep did not cover every accepted episode")
        return SweepResult(
            accepted_episode_count=len(accepted),
            swept_episode_ids=swept_ids,
            artifact_paths=artifacts,
            shard_count=len(shards),
            cache_hits=cache_hits,
            token_counts={"memory_sweep": self._estimate_tokens(artifacts)},
            errors=errors,
        )

    def _available_episodes(self, trade_date: date) -> list[ResearchEpisode]:
        return [
            episode
            for episode in self.store.list_accepted()
            if episode.available_from.date() <= trade_date
        ]

    def _shards(self, episodes: list[ResearchEpisode]) -> list[list[ResearchEpisode]]:
        return [
            episodes[index : index + self.shard_episode_count]
            for index in range(0, len(episodes), self.shard_episode_count)
        ]

    def _build_contribution(
        self,
        *,
        mode: str,
        trade_date: date,
        brain_version: str,
        news_hash: str,
        shard_index: int,
        episode_count: int,
        episodes: list[ResearchEpisode],
        first_pass_mechanisms: list[str],
    ) -> dict[str, object]:
        episode_ids = [episode.episode_id for episode in episodes]
        summaries = [episode.blind_analysis.summary for episode in episodes]
        lessons = [
            mechanism
            for episode in episodes
            for mechanism in episode.blind_analysis.open_world_mechanisms
        ]
        return {
            "schema_version": "nslab.memory_sweep_contribution.v1",
            "mode": mode,
            "trade_date": trade_date.isoformat(),
            "brain_version": brain_version,
            "current_news_sha256": news_hash,
            "shard_index": shard_index,
            "episode_count": episode_count,
            "episode_ids": episode_ids,
            "related_lessons": lessons,
            "positive_analogs": summaries,
            "negative_analogs": [],
            "near_misses": [miss for episode in episodes for miss in episode.misses],
            "counterexamples": [
                claim.statement for episode in episodes for claim in episode.counterexamples
            ],
            "supporting_points": first_pass_mechanisms,
            "objections": [
                "Do not use this shard as a whitelist.",
                "Current evidence can still generate novel candidates absent from memory.",
            ],
            "new_candidate_paths": [
                "direct entity verification",
                "indirect beneficiary discovery",
                "D-1 continuation review",
            ],
            "from_cache": False,
        }

    def _estimate_tokens(self, artifact_paths: list[str]) -> int:
        char_count = 0
        for relative_path in artifact_paths:
            path = self.root / relative_path
            if path.exists():
                char_count += len(path.read_text(encoding="utf-8"))
        return max(1, char_count // 4) if char_count else 0
