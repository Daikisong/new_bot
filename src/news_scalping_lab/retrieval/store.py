"""Simple local retrieval store.

This is deliberately a supporting tool. Empty retrieval results are valid and must
not block candidate generation.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.models import ResearchEpisode
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
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
VECTOR_INDEX_BRAIN_RECORDS = "brain_records.jsonl"
VECTOR_INDEX_MANIFEST = "manifest.json"

_FILTER_VALUE_KEYS = {
    "ticker": "tickers",
    "company_name": "company_names",
    "theme_id": "theme_ids",
    "path_type": "path_types",
    "response_class": "response_classes",
}

_PAYLOAD_FILTER_ALIASES: dict[str, tuple[str, ...]] = {
    "ticker": (
        "ticker",
        "candidate_ticker",
        "outcome_ticker",
        "chosen_leader_ticker",
        "rejected_candidate_tickers",
        "peer_universe",
        "blind_preferred_ticker",
        "blind_rejected_ticker",
        "outcome_winner_ticker",
        "missed_ticker",
        "corrected_ticker",
        "issuer_ticker",
    ),
    "company_name": (
        "company_name",
        "candidate_company_name",
        "outcome_company_name",
        "chosen_leader_company_name",
        "blind_preferred_company_name",
        "blind_rejected_company_name",
        "outcome_winner_company_name",
        "missed_company_name",
        "corrected_company_name",
        "company_name_on_D",
        "issuer_company_name",
    ),
    "theme_id": (
        "theme_id",
        "theme_ids",
        "candidate_theme_id",
        "missed_theme_ids",
        "missed_theme_id",
        "corrected_theme_ids",
        "corrected_theme_id",
    ),
    "path_type": (
        "path_type",
        "candidate_path_type",
        "missed_path_type",
        "corrected_path_type",
    ),
    "response_class": (
        "response_class",
        "candidate_response_class",
        "blind_response_class",
        "outcome_response_class",
    ),
}

_PAYLOAD_NESTED_FILTER_ALIASES: dict[str, tuple[tuple[str, str], ...]] = {
    "ticker": (
        ("D_outcome", "ticker"),
        ("D_outcome", "code"),
        ("outcome", "ticker"),
        ("outcome", "code"),
        ("issuer_day_outcome", "ticker"),
        ("issuer_day_outcome", "code"),
    ),
    "company_name": (
        ("D_outcome", "company_name"),
        ("D_outcome", "company_name_on_D"),
        ("D_outcome", "name"),
        ("outcome", "company_name"),
        ("outcome", "company_name_on_D"),
        ("outcome", "name"),
        ("issuer_day_outcome", "company_name"),
        ("issuer_day_outcome", "company_name_on_D"),
        ("issuer_day_outcome", "name"),
    ),
    "response_class": (
        ("D_outcome", "response_class"),
        ("outcome", "response_class"),
        ("issuer_day_outcome", "response_class"),
    ),
}


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
        self.brain_records_path = self.index_dir / VECTOR_INDEX_BRAIN_RECORDS
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

    def search_records(
        self,
        query: str,
        *,
        limit: int = 10,
        record_type: str | tuple[str, ...] | list[str] | set[str] | None = None,
        training_target: str | None = None,
        trade_date_from: str | None = None,
        trade_date_to: str | None = None,
        available_from: datetime | None = None,
        ticker: str | None = None,
        company_name: str | None = None,
        theme_id: str | None = None,
        path_type: str | None = None,
        response_class: str | None = None,
        training_eligible: bool | None = None,
        evidence_phase: str | None = None,
        confidence_label: str | None = None,
    ) -> list[str]:
        if self.force_empty:
            return []
        records = self._current_brain_records()
        if records is None:
            self.rebuild_index()
            records = self._current_brain_records()
        if not records:
            return []
        filtered = [
            record
            for record in records
            if _matches_optional_string_filter(record.get("record_type"), record_type)
            and (
                training_target is None
                or record.get("training_target") == training_target
            )
            and _date_in_range(
                str(record.get("trade_date", "")),
                trade_date_from=trade_date_from,
                trade_date_to=trade_date_to,
            )
            and (
                available_from is None
                or _datetime_leq(str(record.get("available_from", "")), available_from)
            )
            and _matches_index_filter(record, "ticker", ticker)
            and _matches_index_filter(record, "company_name", company_name)
            and _matches_index_filter(record, "theme_id", theme_id)
            and _matches_index_filter(record, "path_type", path_type)
            and _matches_index_filter(record, "response_class", response_class)
            and (
                training_eligible is None
                or record.get("training_eligible") is training_eligible
            )
            and (
                evidence_phase is None
                or record.get("evidence_phase") == evidence_phase
            )
            and (
                confidence_label is None
                or record.get("confidence_label") == confidence_label
            )
        ]
        query_terms = text_terms(query)
        query_vector = self.embedding_provider.embed_texts([query])[0]
        scored: list[tuple[float, str]] = []
        for record in filtered:
            record_id = str(record["record_id"])
            document_terms = {str(term) for term in record["terms"]}
            embedding = [float(value) for value in record["embedding"]]
            overlap = len(query_terms & document_terms)
            vector_score = _cosine_similarity(query_vector, embedding)
            scored.append((overlap + vector_score, record_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [record_id for _score, record_id in scored[:limit]]

    def list_all_episodes(self) -> list[ResearchEpisode]:
        return self.store.list_accepted()

    def get_available_as_of(self, cutoff_at: datetime) -> list[ResearchEpisode]:
        return [
            episode
            for episode in self.store.list_accepted()
            if is_available_as_of(episode.available_from, cutoff_at)
        ]

    def get_records_available_as_of(self, cutoff_at: datetime) -> list[BrainRecordEnvelope]:
        return [
            record
            for record in BrainRecordStore(self.root).list_records()
            if is_available_as_of(record.available_from, cutoff_at)
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
        brain_records = BrainRecordStore(self.root).list_records()
        record_texts = [_brain_record_text(record) for record in brain_records]
        record_vectors = self.embedding_provider.embed_texts(record_texts)
        dimensions = _vector_dimensions(
            vectors,
            record_vectors,
            fallback=getattr(self.embedding_provider, "dimensions", VECTOR_DIMENSIONS),
        )
        indexed_brain_records: list[dict[str, object]] = []
        for record, text, vector in zip(brain_records, record_texts, record_vectors, strict=True):
            payload = record.payload
            filter_values = _brain_record_filter_values(payload)
            indexed_brain_records.append(
                {
                    "record_id": record.record_id,
                    "episode_id": record.episode_id,
                    "record_type": record.record_type,
                    "training_target": record.training_target,
                    "evidence_phase": record.evidence_phase,
                    "confidence_label": record.confidence_label,
                    "trade_date": record.trade_date.isoformat(),
                    "available_from": record.available_from.isoformat(),
                    "training_eligible": record.training_eligible,
                    "ticker": _first_filter_value(filter_values["ticker"]),
                    "company_name": _first_filter_value(filter_values["company_name"]),
                    "theme_id": _first_filter_value(filter_values["theme_id"]),
                    "path_type": _first_filter_value(filter_values["path_type"]),
                    "response_class": _first_filter_value(filter_values["response_class"]),
                    "tickers": filter_values["ticker"],
                    "company_names": filter_values["company_name"],
                    "theme_ids": filter_values["theme_id"],
                    "path_types": filter_values["path_type"],
                    "response_classes": filter_values["response_class"],
                    "text_sha256": sha256_text(text),
                    "terms": sorted(text_terms(text)),
                    "embedding": vector,
                }
            )
        episode_index_payload = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
        )
        self.records_path.write_text(episode_index_payload, encoding="utf-8")
        brain_record_payload = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in indexed_brain_records
        )
        self.brain_records_path.write_text(brain_record_payload, encoding="utf-8")
        accepted_hashes = self.store.accepted_hashes()
        brain_record_hashes = {
            record.record_id: record.normalized_payload_sha256 for record in brain_records
        }
        manifest = {
            "schema_version": VECTOR_INDEX_SCHEMA_VERSION,
            "embedding_method": self.embedding_provider.embedding_method,
            "dimensions": dimensions,
            "record_count": len(records),
            "brain_record_count": len(indexed_brain_records),
            "accepted_episode_count": len(accepted_hashes),
            "accepted_hashes": accepted_hashes,
            "brain_record_hashes": brain_record_hashes,
            "records_file": VECTOR_INDEX_RECORDS,
            "records_sha256": sha256_text(episode_index_payload),
            "brain_records_file": VECTOR_INDEX_BRAIN_RECORDS,
            "brain_records_sha256": sha256_text(brain_record_payload),
        }
        write_json(self.manifest_path, manifest)
        return manifest

    def inspect_index(self) -> dict[str, object]:
        return inspect_vector_index(self.root)

    def _current_records(self) -> list[dict[str, Any]] | None:
        inspection = inspect_vector_index(self.root)
        if inspection.get("status") != "current":
            return None
        dimensions = _inspection_dimensions(inspection)
        try:
            records = [
                json.loads(line)
                for line in self.records_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            return None
        if not all(_is_index_record(record, dimensions=dimensions) for record in records):
            return None
        return records

    def _current_brain_records(self) -> list[dict[str, Any]] | None:
        inspection = inspect_vector_index(self.root)
        if inspection.get("status") != "current":
            return None
        dimensions = _inspection_dimensions(inspection)
        try:
            records = [
                json.loads(line)
                for line in self.brain_records_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            return None
        if not all(
            _is_brain_index_record(record, dimensions=dimensions) for record in records
        ):
            return None
        return records


def inspect_vector_index(root: Path) -> dict[str, object]:
    index_dir = root / "memory" / "vector_index"
    manifest_path = index_dir / VECTOR_INDEX_MANIFEST
    records_path = index_dir / VECTOR_INDEX_RECORDS
    brain_records_path = index_dir / VECTOR_INDEX_BRAIN_RECORDS
    base: dict[str, object] = {
        "path": index_dir.as_posix(),
        "exists": index_dir.exists(),
        "manifest_exists": manifest_path.exists(),
        "records_exists": records_path.exists(),
        "brain_records_exists": brain_records_path.exists(),
        "status": "missing",
        "record_count": 0,
        "brain_record_count": 0,
        "accepted_episode_count": len(ResearchStore(root).accepted_hashes()),
        "source_brain_record_count": len(BrainRecordStore(root).list_records()),
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
            "brain_record_count": manifest.get("brain_record_count", 0),
            "indexed_accepted_episode_count": manifest.get("accepted_episode_count", 0),
        }
    )
    try:
        records_payload = records_path.read_text(encoding="utf-8")
        brain_records_payload = (
            brain_records_path.read_text(encoding="utf-8")
            if brain_records_path.exists()
            else ""
        )
    except OSError:
        return {**base, "status": "invalid"}
    accepted_hashes = ResearchStore(root).accepted_hashes()
    brain_record_hashes = {
        record.record_id: record.normalized_payload_sha256
        for record in BrainRecordStore(root).list_records()
    }
    if manifest.get("schema_version") != VECTOR_INDEX_SCHEMA_VERSION:
        return {**base, "status": "invalid"}
    if manifest.get("records_sha256") != sha256_text(records_payload):
        return {**base, "status": "invalid"}
    if manifest.get("brain_records_sha256", sha256_text("")) != sha256_text(
        brain_records_payload
    ):
        return {**base, "status": "invalid"}
    if manifest.get("accepted_hashes") != accepted_hashes:
        return {**base, "status": "stale"}
    if manifest.get("brain_record_hashes", {}) != brain_record_hashes:
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


def _brain_record_text(record: BrainRecordEnvelope) -> str:
    payload_text = json.dumps(record.payload, ensure_ascii=False, sort_keys=True)
    return " ".join(
        [
            record.record_id,
            record.record_type,
            record.training_target or "",
            record.evidence_phase,
            record.status,
            record.confidence_label,
            payload_text,
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


def _is_index_record(value: object, *, dimensions: int) -> bool:
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
        and len(embedding) == dimensions
        and all(isinstance(item, int | float) for item in embedding)
    )


def _is_brain_index_record(value: object, *, dimensions: int) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("record_id"), str):
        return False
    if not isinstance(value.get("record_type"), str):
        return False
    terms = value.get("terms")
    embedding = value.get("embedding")
    return (
        isinstance(terms, list)
        and all(isinstance(term, str) for term in terms)
        and isinstance(embedding, list)
        and len(embedding) == dimensions
        and all(isinstance(item, int | float) for item in embedding)
    )


def _vector_dimensions(
    *vector_groups: list[list[float]],
    fallback: object,
) -> int:
    dimensions = _positive_int(fallback)
    for vectors in vector_groups:
        for vector in vectors:
            if dimensions is None:
                dimensions = len(vector)
            elif len(vector) != dimensions:
                raise ValueError(
                    f"embedding dimensions mismatch: expected {dimensions}, got {len(vector)}"
                )
    return dimensions or VECTOR_DIMENSIONS


def _inspection_dimensions(inspection: dict[str, object]) -> int:
    return _positive_int(inspection.get("dimensions")) or VECTOR_DIMENSIONS


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _date_in_range(
    raw_value: str,
    *,
    trade_date_from: str | None,
    trade_date_to: str | None,
) -> bool:
    if trade_date_from is None and trade_date_to is None:
        return True
    try:
        value = date.fromisoformat(raw_value)
    except ValueError:
        return False
    if trade_date_from is not None:
        try:
            lower = date.fromisoformat(trade_date_from)
        except ValueError:
            return False
        if value < lower:
            return False
    if trade_date_to is not None:
        try:
            upper = date.fromisoformat(trade_date_to)
        except ValueError:
            return False
        if value > upper:
            return False
    return True


def _datetime_leq(raw_value: str, upper: datetime) -> bool:
    try:
        value = datetime.fromisoformat(raw_value)
    except ValueError:
        return False
    return value <= upper


def _matches_optional_string_filter(
    value: object,
    expected: str | tuple[str, ...] | list[str] | set[str] | None,
) -> bool:
    if expected is None:
        return True
    if isinstance(expected, str):
        return value == expected
    return isinstance(value, str) and value in expected


def _brain_record_filter_values(payload: dict[str, Any]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for field_name, aliases in _PAYLOAD_FILTER_ALIASES.items():
        collected: list[str] = []
        for alias in aliases:
            collected.extend(_string_values(payload.get(alias)))
        for parent_key, child_key in _PAYLOAD_NESTED_FILTER_ALIASES.get(field_name, ()):
            parent = payload.get(parent_key)
            if isinstance(parent, dict):
                collected.extend(_string_values(parent.get(child_key)))
        values[field_name] = _unique_strings(collected)
    return values


def _matches_index_filter(
    record: dict[str, Any],
    field_name: str,
    expected: str | None,
) -> bool:
    if expected is None:
        return True
    value_key = _FILTER_VALUE_KEYS[field_name]
    values = _string_values(record.get(field_name)) + _string_values(record.get(value_key))
    return expected in set(values)


def _first_filter_value(values: list[str]) -> str | None:
    return values[0] if values else None


def _string_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, int | float) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, list | tuple | set):
        values: list[str] = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []


def _unique_strings(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
