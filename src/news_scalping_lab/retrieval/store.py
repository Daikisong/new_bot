"""Simple local retrieval store.

This is deliberately a supporting tool. Empty retrieval results are valid and must
not block candidate generation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from news_scalping_lab.contracts.models import ResearchEpisode
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import is_available_as_of


class LocalRetrievalStore:
    def __init__(self, root: Path, *, force_empty: bool = False) -> None:
        self.root = root
        self.force_empty = force_empty
        self.store = ResearchStore(root)

    def add_episode(self, episode: ResearchEpisode) -> None:
        self.store.save_episode(episode)
        self.store.accept(episode.episode_id)

    def search(self, query: str, *, limit: int = 10) -> list[str]:
        return self.search_semantic(query, limit=limit)

    def search_semantic(self, query: str, *, limit: int = 10) -> list[str]:
        if self.force_empty:
            return []
        query_terms = _terms(query)
        scored: list[tuple[float, str]] = []
        for episode in self.store.list_accepted():
            score = _semantic_score(query_terms, _episode_text(episode))
            scored.append((score, episode.episode_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if any(score > 0 for score, _episode_id in scored):
            return [episode_id for score, episode_id in scored if score > 0][:limit]
        return [episode_id for _score, episode_id in scored[:limit]]

    def list_all_episodes(self) -> list[ResearchEpisode]:
        return self.store.list_accepted()

    def get_available_as_of(self, cutoff_at: datetime) -> list[ResearchEpisode]:
        return [
            episode
            for episode in self.store.list_accepted()
            if is_available_as_of(episode.available_from, cutoff_at)
        ]


def _episode_text(episode: ResearchEpisode) -> str:
    return " ".join(
        [
            episode.episode_id,
            episode.blind_analysis.summary,
            " ".join(episode.blind_analysis.open_world_mechanisms),
            " ".join(episode.blind_analysis.initial_uncertainties),
            " ".join(edge.relation_explanation for edge in episode.event_ticker_edges),
            " ".join(claim.mechanism for claim in episode.lessons),
            " ".join(claim.mechanism for claim in episode.counterexamples),
            " ".join(episode.misses),
        ]
    )


def _semantic_score(query_terms: set[str], document: str) -> float:
    if not query_terms:
        return 0.0
    document_terms = _terms(document)
    if not document_terms:
        return 0.0
    overlap = len(query_terms & document_terms)
    query_bigrams = _bigrams(" ".join(sorted(query_terms)))
    document_bigrams = _bigrams(" ".join(sorted(document_terms)))
    bigram_overlap = len(query_bigrams & document_bigrams)
    return overlap + (bigram_overlap / max(1, len(query_bigrams)))


def _terms(text: str) -> set[str]:
    normalized = "".join(character.lower() if character.isalnum() else " " for character in text)
    return {term for term in normalized.split() if len(term) > 1}


def _bigrams(text: str) -> set[str]:
    compact = "".join(character for character in text.lower() if character.isalnum())
    return {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}
