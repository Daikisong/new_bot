"""Memory store protocols."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from news_scalping_lab.contracts.models import ResearchEpisode


class MemoryStore(Protocol):
    """Accepted research memory interface used by analysis and exports."""

    def add_episode(self, episode: ResearchEpisode) -> None:
        """Add a canonical research episode to accepted memory."""

    def search_semantic(self, query: str, *, limit: int = 10) -> list[str]:
        """Return relevant episode IDs without acting as a candidate gate."""

    def list_all_episodes(self) -> list[ResearchEpisode]:
        """List all accepted research episodes."""

    def get_available_as_of(self, cutoff_at: datetime) -> list[ResearchEpisode]:
        """List accepted episodes available by ``cutoff_at``."""
