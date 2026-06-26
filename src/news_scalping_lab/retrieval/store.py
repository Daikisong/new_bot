"""Simple local retrieval store.

This is deliberately a supporting tool. Empty retrieval results are valid and must
not block candidate generation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.models import ResearchEpisode
from news_scalping_lab.retrieval.embedding import (
    VECTOR_DIMENSIONS,
    DeterministicHashEmbeddingProvider,
    LocalEmbeddingProvider,
    text_bigrams,
    text_terms,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import is_available_as_of, read_json, sha256_text, write_json

VECTOR_INDEX_SCHEMA_VERSION = "nslab.local_vector_index.v1"
VECTOR_INDEX_RECORDS = "episodes.jsonl"
VECTOR_INDEX_MANIFEST = "manifest.json"


class LocalRetrievalStore:
    def __init__(
        self,
        root: Path,
        *,
        force_empty: bool = False,
        embedding_provider: LocalEmbeddingProvider | None = None,
    ) -> None:
        self.root = root
        self.force_empty = force_empty
        self.embedding_provider = embedding_provider or DeterministicHashEmbeddingProvider()
        self.store = ResearchStore(root)
        self.index_dir = root / "memory" / "vector_index"
        self.records_path = self.index_dir / VECTOR_INDEX_RECORDS
        self.manifest_path = self.index_dir / VECTOR_INDEX_MANIFEST

    def add_episode(self, episode: ResearchEpisode) -> None:
        self.store.save_episode(episode)
        self.store.accept(episode.episode_id)
        self.rebuild_index()

    def search(self, query: str, *, limit: int = 10) -> list[str]:
        return self.search_semantic(query, limit=limit)

    def search_semantic(self, query: str, *, limit: int = 10) -> list[str]:
        if self.force_empty:
            return []
        records = self._current_records()
        if records is None:
            self.rebuild_index()
            records = self._current_records()
        if records:
            return _rank_index_records(
                query,
                records,
                limit=limit,
                embedding_provider=self.embedding_provider,
            )
        query_terms = text_terms(query)
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

    def rebuild_index(self) -> dict[str, object]:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, object]] = []
        episodes = self.store.list_accepted()
        texts = [_episode_text(episode) for episode in episodes]
        vectors = self.embedding_provider.embed_texts(texts)
        for episode, text, vector in zip(episodes, texts, vectors, strict=True):
            records.append(
                {
                    "episode_id": episode.episode_id,
                    "available_from": episode.available_from.isoformat(),
                    "text_sha256": sha256_text(text),
                    "terms": sorted(text_terms(text)),
                    "embedding": vector,
                }
            )
        payload = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
        )
        self.records_path.write_text(payload, encoding="utf-8")
        accepted_hashes = self.store.accepted_hashes()
        manifest = {
            "schema_version": VECTOR_INDEX_SCHEMA_VERSION,
            "embedding_method": self.embedding_provider.embedding_method,
            "dimensions": self.embedding_provider.dimensions,
            "record_count": len(records),
            "accepted_episode_count": len(accepted_hashes),
            "accepted_hashes": accepted_hashes,
            "records_file": VECTOR_INDEX_RECORDS,
            "records_sha256": sha256_text(payload),
        }
        write_json(self.manifest_path, manifest)
        return manifest

    def inspect_index(self) -> dict[str, object]:
        return inspect_vector_index(self.root)

    def _current_records(self) -> list[dict[str, Any]] | None:
        inspection = inspect_vector_index(self.root)
        if inspection.get("status") != "current":
            return None
        try:
            records = [
                json.loads(line)
                for line in self.records_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            return None
        if not all(_is_index_record(record) for record in records):
            return None
        return records


def inspect_vector_index(root: Path) -> dict[str, object]:
    index_dir = root / "memory" / "vector_index"
    manifest_path = index_dir / VECTOR_INDEX_MANIFEST
    records_path = index_dir / VECTOR_INDEX_RECORDS
    base: dict[str, object] = {
        "path": index_dir.as_posix(),
        "exists": index_dir.exists(),
        "manifest_exists": manifest_path.exists(),
        "records_exists": records_path.exists(),
        "status": "missing",
        "record_count": 0,
        "accepted_episode_count": len(ResearchStore(root).accepted_hashes()),
    }
    if not manifest_path.exists() or not records_path.exists():
        return base
    try:
        manifest = read_json(manifest_path)
    except (OSError, json.JSONDecodeError):
        return {**base, "status": "invalid"}
    if not isinstance(manifest, dict):
        return {**base, "status": "invalid"}
    base.update(
        {
            "schema_version": manifest.get("schema_version"),
            "embedding_method": manifest.get("embedding_method"),
            "dimensions": manifest.get("dimensions"),
            "record_count": manifest.get("record_count", 0),
            "indexed_accepted_episode_count": manifest.get("accepted_episode_count", 0),
        }
    )
    try:
        records_payload = records_path.read_text(encoding="utf-8")
    except OSError:
        return {**base, "status": "invalid"}
    accepted_hashes = ResearchStore(root).accepted_hashes()
    if manifest.get("schema_version") != VECTOR_INDEX_SCHEMA_VERSION:
        return {**base, "status": "invalid"}
    if manifest.get("records_sha256") != sha256_text(records_payload):
        return {**base, "status": "invalid"}
    if manifest.get("accepted_hashes") != accepted_hashes:
        return {**base, "status": "stale"}
    return {**base, "status": "current"}


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
    document_terms = text_terms(document)
    if not document_terms:
        return 0.0
    overlap = len(query_terms & document_terms)
    query_bigrams = text_bigrams(" ".join(sorted(query_terms)))
    document_bigrams = text_bigrams(" ".join(sorted(document_terms)))
    bigram_overlap = len(query_bigrams & document_bigrams)
    return overlap + (bigram_overlap / max(1, len(query_bigrams)))


def _rank_index_records(
    query: str,
    records: list[dict[str, Any]],
    *,
    limit: int,
    embedding_provider: LocalEmbeddingProvider,
) -> list[str]:
    query_terms = text_terms(query)
    query_vector = embedding_provider.embed_texts([query])[0]
    scored: list[tuple[float, str]] = []
    for record in records:
        episode_id = str(record["episode_id"])
        document_terms = {str(term) for term in record["terms"]}
        embedding = [float(value) for value in record["embedding"]]
        overlap = len(query_terms & document_terms)
        vector_score = _cosine_similarity(query_vector, embedding)
        scored.append((overlap + vector_score, episode_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [episode_id for _score, episode_id in scored[:limit]]


def _is_index_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("episode_id"), str):
        return False
    terms = value.get("terms")
    embedding = value.get("embedding")
    return (
        isinstance(terms, list)
        and all(isinstance(term, str) for term in terms)
        and isinstance(embedding, list)
        and len(embedding) == VECTOR_DIMENSIONS
        and all(isinstance(item, int | float) for item in embedding)
    )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
