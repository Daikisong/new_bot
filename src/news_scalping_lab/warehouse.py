"""DuckDB/Parquet warehouse projections.

The warehouse is a derived, reproducible projection of canonical JSON data. It is
not the source of truth for research memory; source files remain immutable under
`research/`, `memory/`, and `brain/`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from news_scalping_lab.contracts.models import (
    BlindPrediction,
    CompanyMemory,
    MechanismMemory,
    ResearchEpisode,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import read_json


class WarehouseStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / "warehouse"
        self.dir.mkdir(parents=True, exist_ok=True)

    def rebuild_all(self) -> dict[str, int]:
        store = ResearchStore(self.root)
        episodes = store.list_accepted()
        counts = {
            "events": self.write_events(episodes),
            "event_sources": self.write_event_sources(episodes),
            "research_episodes": self.write_research_episodes(episodes),
            "event_ticker_edges": self.write_event_ticker_edges(episodes),
            "market_memory": self.write_market_memory(episodes),
            "mechanism_memory": self.write_mechanism_memory_from_files(),
            "company_memory": self.write_company_memory_from_files(),
            "predictions": self.write_predictions_from_files(),
            "daily_outcomes": self.write_daily_outcomes_from_files(),
        }
        return counts

    def write_events(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            for event in episode.observed_events:
                rows.append(
                    {
                        "event_id": event.event_id,
                        "episode_id": episode.episode_id,
                        "trade_date": episode.trade_date.isoformat(),
                        "published_at": event.published_at.isoformat(),
                        "row_number": event.row_number,
                        "title": event.title,
                        "body": event.body,
                        "source_id": event.source_id,
                        "provenance_json": _json(event.provenance),
                    }
                )
        self._write_rows("events.parquet", rows)
        return len(rows)

    def write_event_sources(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for episode in episodes:
            for event in episode.observed_events:
                for source in event.provenance:
                    key = (episode.episode_id, event.event_id, source.source_id, source.uri)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "source_id": source.source_id,
                            "event_id": event.event_id,
                            "episode_id": episode.episode_id,
                            "source_type": source.source_type,
                            "uri": source.uri,
                            "content_sha256": source.content_sha256,
                            "excerpt": source.excerpt,
                            "observed_at": (
                                source.observed_at.isoformat() if source.observed_at else None
                            ),
                        }
                    )
        self._write_rows("event_sources.parquet", rows)
        return len(rows)

    def write_research_episodes(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            rows.append(
                {
                    "episode_id": episode.episode_id,
                    "trade_date": episode.trade_date.isoformat(),
                    "cutoff_at": episode.cutoff_at.isoformat(),
                    "available_from": episode.available_from.isoformat(),
                    "execution_protocol_version": episode.execution_protocol_version,
                    "outcome_coverage_status": episode.outcome_coverage_status,
                    "research_version": episode.research_version,
                    "lesson_count": len(episode.lessons),
                    "counterexample_count": len(episode.counterexamples),
                    "candidate_count": len(episode.blind_predictions),
                    "eligibility_matrix_json": _json(episode.eligibility_matrix),
                    "provenance_json": _json(episode.provenance),
                }
            )
        self._write_rows("research_episodes.parquet", rows)
        return len(rows)

    def write_event_ticker_edges(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            for edge in episode.event_ticker_edges:
                rows.append(
                    {
                        "edge_id": edge.edge_id,
                        "episode_id": edge.episode_id,
                        "event_id": edge.event_id,
                        "ticker": edge.ticker,
                        "company_name": edge.company_name,
                        "relation_class": str(edge.relation_class),
                        "confidence_label": str(edge.confidence_label),
                        "directly_mentioned": edge.directly_mentioned,
                        "provenance_json": _json(edge.provenance),
                    }
                )
        self._write_rows("event_ticker_edges.parquet", rows)
        return len(rows)

    def write_market_memory(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            for claim in [*episode.lessons, *episode.counterexamples]:
                rows.append(
                    {
                        "claim_id": claim.claim_id,
                        "episode_id": episode.episode_id,
                        "trade_date": episode.trade_date.isoformat(),
                        "available_from": claim.available_from.isoformat(),
                        "status": str(claim.status),
                        "confidence_label": str(claim.confidence_label),
                        "statement": claim.statement,
                        "mechanism": claim.mechanism,
                        "support_episode_ids_json": _json(claim.support_episode_ids),
                        "contradiction_episode_ids_json": _json(claim.contradiction_episode_ids),
                    }
                )
        self._write_rows("market_memory.parquet", rows)
        return len(rows)

    def write_mechanism_memory_from_files(self) -> int:
        rows: list[dict[str, Any]] = []
        path = self.root / "memory" / "mechanisms" / "current" / "mechanisms.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                memory = MechanismMemory.model_validate(json.loads(line))
                rows.append(
                    {
                        "mechanism_id": memory.mechanism_id,
                        "natural_language_description": memory.natural_language_description,
                        "causal_chain_json": _json(memory.causal_chain),
                        "observed_variations_json": _json(memory.observed_variations),
                        "successful_cases_json": _json(memory.successful_cases),
                        "failed_cases_json": _json(memory.failed_cases),
                        "boundary_conditions_json": _json(memory.boundary_conditions),
                        "leader_selection_notes_json": _json(memory.leader_selection_notes),
                        "provenance_json": _json(memory.provenance),
                    }
                )
        self._write_rows("mechanism_memory.parquet", rows)
        return len(rows)

    def write_company_memory_from_files(self) -> int:
        rows: list[dict[str, Any]] = []
        for path in sorted((self.root / "memory" / "company_memory").glob("*.json")):
            memory = CompanyMemory.model_validate(read_json(path))
            rows.append(
                {
                    "ticker": memory.ticker,
                    "company_name": memory.company_name,
                    "known_at": memory.known_at.isoformat(),
                    "aliases_json": _json(memory.aliases),
                    "business_descriptions_json": _json(memory.business_descriptions),
                    "locations_json": _json(memory.locations),
                    "customers_json": _json(memory.customers),
                    "supply_chain_roles_json": _json(memory.supply_chain_roles),
                    "prior_market_narratives_json": _json(memory.prior_market_narratives),
                    "prior_leader_occurrences_json": _json(memory.prior_leader_occurrences),
                    "contradictory_relations_json": _json(memory.contradictory_relations),
                    "provenance_json": _json(memory.provenance),
                }
            )
        self._write_rows("company_memory.parquet", rows)
        return len(rows)

    def write_prediction(self, prediction: BlindPrediction) -> None:
        rows = [
            {
                "prediction_id": prediction.prediction_id,
                "trade_date": prediction.trade_date.isoformat(),
                "cutoff_at": prediction.cutoff_at.isoformat(),
                "sealed_at": prediction.sealed_at.isoformat() if prediction.sealed_at else None,
                "blind_artifact_sha256": prediction.blind_artifact_sha256,
                "candidate_count": len(prediction.candidates),
                "dominant_sector_count": len(prediction.dominant_sectors),
            }
        ]
        self._append_or_replace_by_key("predictions.parquet", rows, "prediction_id")

    def write_predictions_from_files(self) -> int:
        rows: list[dict[str, Any]] = []
        for path in sorted((self.root / "predictions").glob("*.json")):
            data = read_json(path)
            rows.append(
                {
                    "prediction_id": data.get("prediction_id"),
                    "trade_date": data.get("trade_date"),
                    "cutoff_at": data.get("cutoff_at"),
                    "sealed_at": data.get("sealed_at"),
                    "blind_artifact_sha256": data.get("blind_artifact_sha256"),
                    "candidate_count": len(data.get("candidates", [])),
                    "dominant_sector_count": len(data.get("dominant_sectors", [])),
                }
            )
        self._write_rows("predictions.parquet", rows)
        return len(rows)

    def write_daily_outcomes_from_files(self) -> int:
        rows: list[dict[str, Any]] = []
        for path in sorted((self.root / "reports").glob("*_postmortem.json")):
            data = read_json(path)
            rows.append(
                {
                    "trade_date": data.get("trade_date"),
                    "blind_prediction_id": data.get("blind_prediction_id"),
                    "created_at": data.get("created_at"),
                    "outcome_count": len(data.get("outcomes", {})),
                    "postmortem_json": _json(data.get("postmortem", {})),
                }
            )
        self._write_rows("daily_outcomes.parquet", rows)
        return len(rows)

    def write_empty(self, filename: str) -> None:
        self._write_rows(filename, [])

    def counts(self) -> dict[str, int | str]:
        result: dict[str, int | str] = {}
        for path in sorted(self.dir.glob("*.parquet")):
            try:
                count = duckdb.sql(f"select count(*) from read_parquet('{path.as_posix()}')").fetchone()
                result[path.name] = int(count[0]) if count else 0
            except duckdb.Error as exc:
                result[path.name] = f"ERROR: {exc}"
        return result

    def _append_or_replace_by_key(self, filename: str, rows: list[dict[str, Any]], key: str) -> None:
        path = self.dir / filename
        existing = _read_rows(path) if path.exists() else []
        incoming_keys = {row[key] for row in rows}
        merged = [row for row in existing if row.get(key) not in incoming_keys]
        merged.extend(rows)
        self._write_rows(filename, merged)

    def _write_rows(self, filename: str, rows: list[dict[str, Any]]) -> None:
        path = self.dir / filename
        if not rows:
            table = pa.Table.from_arrays(
                [pa.array([], type=pa.string())],
                names=["_empty"],
            )
            pq.write_table(table, path)  # type: ignore[no-untyped-call]
            return
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, path)  # type: ignore[no-untyped-call]


def _read_rows(path: Path) -> list[dict[str, Any]]:
    table = pq.read_table(path)  # type: ignore[no-untyped-call]
    rows = cast(list[dict[str, Any]], table.to_pylist())
    if not rows:
        return []
    if set(rows[0]) == {"_empty"}:
        return []
    return rows


def _json(value: Any) -> str:
    if isinstance(value, list):
        return json.dumps(
            [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value],
            ensure_ascii=False,
            sort_keys=True,
        )
    if hasattr(value, "model_dump"):
        return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def previous_trade_day(trade_day: date) -> date:
    return date.fromordinal(trade_day.toordinal() - 1)
