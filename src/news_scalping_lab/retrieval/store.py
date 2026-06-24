"""Simple local retrieval store.

This is deliberately a supporting tool. Empty retrieval results are valid and must
not block candidate generation.
"""

from __future__ import annotations

from pathlib import Path

from news_scalping_lab.storage import ResearchStore


class LocalRetrievalStore:
    def __init__(self, root: Path, *, force_empty: bool = False) -> None:
        self.root = root
        self.force_empty = force_empty
        self.store = ResearchStore(root)

    def search(self, query: str, *, limit: int = 10) -> list[str]:
        if self.force_empty:
            return []
        lowered = query.lower()
        matches: list[str] = []
        for episode in self.store.list_accepted():
            haystack = " ".join(
                [
                    episode.episode_id,
                    episode.blind_analysis.summary,
                    " ".join(episode.blind_analysis.open_world_mechanisms),
                ]
            ).lower()
            if any(token and token in haystack for token in lowered.split()):
                matches.append(episode.episode_id)
            if len(matches) >= limit:
                break
        return matches
