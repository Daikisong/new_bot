"""Time-safe historical replay snapshots derived without mutating production records."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.memory_context import MemoryCellSnapshotManifest
from news_scalping_lab.memory.index import (
    ProductionMemoryIndex,
    ReplayAvailabilityOverride,
)
from news_scalping_lab.records.models import NormalizedEpisodeIndex
from news_scalping_lab.utils import (
    KST,
    as_kst,
    file_sha256,
    next_trading_day,
    read_json,
    sha256_text,
    write_json,
)

REPLAY_AVAILABILITY_VERSION = "replay_explicit_or_conservative.v2"
REPLAY_SNAPSHOT_VERSION = "nslab.shadow_replay_as_of_snapshot.v1"
REPLAY_SNAPSHOT_ROOT = Path("runs/semantic_brain_upgrade/replay_snapshots")
REPLAY_SNAPSHOT_STREAM_BATCH_SIZE = 1024


@dataclass(frozen=True)
class ReplayAvailability:
    record_id: str
    source_trade_date: date
    replay_available_from: datetime


@dataclass(frozen=True)
class ShadowReplaySnapshotResult:
    memory_snapshot: MemoryCellSnapshotManifest
    receipt: dict[str, Any]
    receipt_path: Path


def replay_available_from(
    *,
    source_trade_date: date,
    next_actual_trading_day: date,
) -> datetime:
    if next_actual_trading_day <= source_trade_date:
        raise ValueError("replay availability requires the next actual trading day")
    return datetime.combine(next_actual_trading_day, time(0, 0), tzinfo=KST)


def replay_record_is_available(
    availability: ReplayAvailability,
    *,
    replay_trade_date: date,
) -> bool:
    return (
        availability.source_trade_date < replay_trade_date
        and as_kst(availability.replay_available_from).date() <= replay_trade_date
    )


def build_shadow_as_of_snapshot(
    root: Path,
    *,
    memory_index: ProductionMemoryIndex,
    build_cutoff: datetime,
    source_snapshot_id: str,
    holdout_record_ids: set[str],
    calibration_record_ids: set[str] | None = None,
    replay_availability_by_episode: Mapping[
        str, ReplayAvailabilityOverride
    ]
    | None = None,
) -> ShadowReplaySnapshotResult:
    """Recompute cutoff-safe centroids while reusing immutable per-record vectors."""

    root = root.resolve()
    source_snapshot = next(
        (
            manifest
            for manifest in memory_index.list_snapshots()
            if manifest.snapshot_id == source_snapshot_id
        ),
        None,
    )
    if source_snapshot is None:
        raise ValueError("shadow replay source snapshot is missing")
    availability = dict(
        replay_availability_by_episode
        or build_replay_availability_projection(root)
    )
    snapshot = memory_index.build(
        as_of=build_cutoff,
        promote_current=False,
        stage_only=True,
        cutoff_mode="explicit",
        reuse_embeddings_from_snapshot_id=source_snapshot_id,
        replay_availability_by_episode=availability,
    )
    if snapshot.retained_record_count != snapshot.record_count:
        raise ValueError("shadow replay attempted to generate new record embeddings")
    if snapshot.record_count >= source_snapshot.record_count:
        raise ValueError("historical replay snapshot did not exclude future records")
    source_hash_path = root / snapshot.source_record_hashes.artifact_path
    included_record_ids = _record_ids_from_hash_ledger(source_hash_path)
    calibration_ids = set(calibration_record_ids or set())
    calibration_overlap = sorted(included_record_ids.intersection(calibration_ids))
    holdout_overlap = sorted(included_record_ids.intersection(holdout_record_ids))
    if calibration_overlap:
        raise ValueError(
            "calibration records entered the BUILD replay snapshot: "
            + ", ".join(calibration_overlap[:10])
        )
    if holdout_overlap:
        raise ValueError(
            "holdout records entered the BUILD replay snapshot: "
            + ", ".join(holdout_overlap[:10])
        )
    receipt = {
        "schema_version": REPLAY_SNAPSHOT_VERSION,
        "availability_version": REPLAY_AVAILABILITY_VERSION,
        "snapshot_id": snapshot.snapshot_id,
        "source_snapshot_id": source_snapshot_id,
        "build_cutoff": as_kst(build_cutoff).isoformat(),
        "record_count": snapshot.record_count,
        "excluded_future_record_count": snapshot.excluded_future_record_count,
        "retained_embedding_count": snapshot.retained_record_count,
        "generated_embedding_count": (
            snapshot.record_count - snapshot.retained_record_count
        ),
        "source_snapshot_record_count": source_snapshot.record_count,
        "centroid_population_record_count": snapshot.record_count,
        "full_corpus_centroids_used": False,
        "holdout_record_count": len(holdout_record_ids),
        "holdout_overlap_count": 0,
        "calibration_record_count": len(calibration_ids),
        "calibration_overlap_count": 0,
        "evaluation_record_count": len(calibration_ids | holdout_record_ids),
        "holdout_record_ids_sha256": sha256_text(
            "\n".join(sorted(holdout_record_ids))
        ),
        "calibration_record_ids_sha256": sha256_text(
            "\n".join(sorted(calibration_ids))
        ),
        "source_record_hashes_sha256": file_sha256(source_hash_path),
        "immutable": True,
        "production_available_from_mutated": False,
        "availability_mode": snapshot.availability_mode,
        "availability_projection_version": (
            snapshot.availability_projection_version
        ),
        "availability_projection_sha256": (
            snapshot.availability_projection.sha256
            if snapshot.availability_projection is not None
            else None
        ),
        "availability_projection_episode_count": len(availability),
        "availability_derivation_counts": dict(
            sorted(Counter(item.derivation for item in availability.values()).items())
        ),
    }
    output_dir = root / REPLAY_SNAPSHOT_ROOT / snapshot.snapshot_id
    receipt_path = output_dir / "shadow_replay_snapshot_receipt.json"
    write_json(receipt_path, receipt)
    return ShadowReplaySnapshotResult(
        memory_snapshot=snapshot,
        receipt=receipt,
        receipt_path=receipt_path,
    )


def build_replay_availability_projection(
    root: Path,
) -> dict[str, ReplayAvailabilityOverride]:
    """Derive a sealed, conservative next-session timestamp for every episode."""

    root = root.resolve()
    records_dir = root / "memory" / "records"
    episodes_dir = root / "research" / "episodes"
    result: dict[str, ReplayAvailabilityOverride] = {}
    for record_path in sorted(records_dir.glob("*.jsonl")):
        episode_id = record_path.stem
        index_path = episodes_dir / episode_id / "normalized_episode_index.json"
        if not index_path.is_file():
            raise FileNotFoundError(
                f"replay availability index is missing: {episode_id}"
            )
        index = NormalizedEpisodeIndex.model_validate(read_json(index_path))
        if index.episode_id != episode_id:
            raise ValueError("replay availability episode identity mismatch")
        if index.next_trade_date is not None:
            replay_date = index.next_trade_date
            derivation = "NORMALIZED_INDEX_NEXT_TRADE_DATE"
        else:
            candidates = _plausible_record_availability_dates(
                record_path,
                trade_date=index.trade_date,
            )
            if len(candidates) == 1:
                replay_date = next(iter(candidates))
                derivation = "RECORD_NEXT_SESSION_TIMESTAMP"
            elif candidates:
                raise ValueError(
                    f"replay availability has conflicting record dates: {episode_id}"
                )
            else:
                # Delaying a record is acceptable for replay; making it available
                # before the unknown real next session is not.
                replay_date = next_trading_day(index.trade_date) + timedelta(days=7)
                derivation = "CONSERVATIVE_WEEK_DELAY_FALLBACK"
        result[episode_id] = ReplayAvailabilityOverride(
            episode_id=episode_id,
            source_trade_date=index.trade_date,
            replay_available_from=replay_available_from(
                source_trade_date=index.trade_date,
                next_actual_trading_day=replay_date,
            ),
            derivation=derivation,
        )
    if not result:
        raise ValueError("replay availability projection has no record episodes")
    if len(result) != sum(1 for path in episodes_dir.iterdir() if path.is_dir()):
        raise ValueError("replay availability does not cover every research episode")
    return result


def _plausible_record_availability_dates(
    path: Path,
    *,
    trade_date: date,
) -> set[date]:
    latest_plausible = trade_date + timedelta(days=7)
    result: set[date] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = read_json_line(line)
            raw = row.get("available_from")
            if not isinstance(raw, str):
                raise ValueError("replay source record availability is invalid")
            observed = as_kst(datetime.fromisoformat(raw)).date()
            if trade_date < observed <= latest_plausible:
                result.add(observed)
    return result


def _record_ids_from_hash_ledger(path: Path) -> set[str]:
    record_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = read_json_line(line)
            record_id = value.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                raise ValueError("replay source hash ledger record ID is invalid")
            record_ids.add(record_id)
    return record_ids


def read_json_line(line: str) -> dict[str, Any]:
    import json

    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("replay source hash ledger row is invalid")
    return value
