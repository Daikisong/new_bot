"""File-backed project stores."""

from __future__ import annotations

import shutil
from pathlib import Path

from news_scalping_lab.contracts.models import ResearchEpisode
from news_scalping_lab.utils import file_sha256, read_json, write_json


class ResearchStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.episodes_dir = root / "research" / "episodes"
        self.accepted_dir = root / "research" / "accepted"
        self.rejected_dir = root / "research" / "rejected"
        for directory in (self.episodes_dir, self.accepted_dir, self.rejected_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def save_episode(self, episode: ResearchEpisode) -> Path:
        path = self.episodes_dir / f"{episode.episode_id}.json"
        write_json(path, episode.model_dump(mode="json"))
        return path

    def get_episode(self, episode_id: str) -> ResearchEpisode:
        candidates = [
            self.episodes_dir / f"{episode_id}.json",
            self.accepted_dir / f"{episode_id}.json",
            self.rejected_dir / f"{episode_id}.json",
        ]
        for path in candidates:
            if path.exists():
                return ResearchEpisode.model_validate(read_json(path))
        raise FileNotFoundError(f"episode not found: {episode_id}")

    def list_episodes(self) -> list[ResearchEpisode]:
        return self._list_from(self.episodes_dir)

    def list_accepted(self) -> list[ResearchEpisode]:
        return self._list_from(self.accepted_dir)

    def list_rejected(self) -> list[ResearchEpisode]:
        return self._list_from(self.rejected_dir)

    def accept(self, episode_id: str) -> Path:
        source = self.episodes_dir / f"{episode_id}.json"
        if not source.exists():
            source = self.rejected_dir / f"{episode_id}.json"
        if not source.exists():
            raise FileNotFoundError(f"episode not found: {episode_id}")
        target = self.accepted_dir / source.name
        shutil.copy2(source, target)
        rejected_copy = self.rejected_dir / source.name
        if rejected_copy.exists():
            rejected_copy.unlink()
        return target

    def reject(self, episode_id: str) -> Path:
        source = self.episodes_dir / f"{episode_id}.json"
        if not source.exists():
            source = self.accepted_dir / f"{episode_id}.json"
        if not source.exists():
            raise FileNotFoundError(f"episode not found: {episode_id}")
        target = self.rejected_dir / source.name
        shutil.copy2(source, target)
        accepted_copy = self.accepted_dir / source.name
        if accepted_copy.exists():
            accepted_copy.unlink()
        return target

    def accepted_hashes(self) -> dict[str, str]:
        return {path.stem: file_sha256(path) for path in sorted(self.accepted_dir.glob("*.json"))}

    def _list_from(self, directory: Path) -> list[ResearchEpisode]:
        episodes = [
            ResearchEpisode.model_validate(read_json(path))
            for path in sorted(directory.glob("*.json"))
        ]
        return sorted(episodes, key=lambda episode: (episode.trade_date, episode.episode_id))
