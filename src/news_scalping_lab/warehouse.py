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

from news_scalping_lab.contracts.models import BlindPrediction, ResearchEpisode
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
            "research_episodes": self.write_research_episodes(episodes),
            "event_ticker_edges": self.write_event_ticker_edges(episodes),
            "market_memory": self.write_market_memory(episodes),
            "predictions": self.write_predictions_from_files(),
            "daily_outcomes": self.write_daily_outcomes_from_files(),
        }
        self.write_empty("events.parquet")
        self.write_empty("event_sources.parquet")
        return counts

    def write_research_episodes(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            rows.append(
                {
                    "episode_id": episode.episode_id,
                    "trade_date": episode.trade_date.isoformat(),
                    "cutoff_at": episode.cutoff_at.isoformat(),
                    "available_from": episode.available_from.isoformat(),
                    "research_version": episode.research_version,
                    "lesson_count": len(episode.lessons),
                    "counterexample_count": len(episode.counterexamples),
                    "candidate_count": len(episode.blind_predictions),
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
