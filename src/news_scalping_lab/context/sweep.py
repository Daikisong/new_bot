"""Exhaustive memory sweep artifacts.

The sweep makes coverage concrete: every accepted, time-available episode is
assigned to exactly one shard and produces a persisted contribution. Retrieval
misses do not affect this path.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from news_scalping_lab.brain.compiler import current_brain_version
from news_scalping_lab.context.memory_coverage import (
    build_memory_coverage_manifest,
    build_memory_coverage_manifest_from_snapshot,
)
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.contracts.memory_context import MemoryCellSnapshotManifest
from news_scalping_lab.contracts.models import ResearchEpisode
from news_scalping_lab.memory.index import (
    active_memory_snapshot_manifest,
    load_snapshot_replay_availability,
)
from news_scalping_lab.records.hashing import (
    brain_record_envelope_sha256,
    brain_record_routing_root_sha256,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import (
    CANDIDATE_GENERATION_ERRORS_LANE,
    COUNTEREXAMPLES_LANE,
    LEADER_SELECTION_PAIRS_LANE,
    NEAR_MISSES_LANE,
    NEGATIVE_CONTROLS_LANE,
    NEWSLESS_OR_UNEXPLAINED_LANE,
    POLARITY_CLASSIFIER_VERSION,
    POSITIVE_ANALOGS_LANE,
    THEME_FORMATION_FAILURES_LANE,
    record_memory_lanes,
    record_outcome_payload,
    record_response_class,
    record_routing_metadata,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    is_available_as_of,
    read_json,
    sha256_text,
    stable_id,
    write_json,
)

MEMORY_SWEEP_PROMPT_VERSION = "memory_sweep.shard_analysis.v3"
RECORD_SWEEP_LANE_FIELDS = {
    "positive_analogs": POSITIVE_ANALOGS_LANE,
    "negative_analogs": NEGATIVE_CONTROLS_LANE,
    "negative_controls": NEGATIVE_CONTROLS_LANE,
    "near_misses": NEAR_MISSES_LANE,
    "counterexamples": COUNTEREXAMPLES_LANE,
    "leader_selection_pairs": LEADER_SELECTION_PAIRS_LANE,
    "theme_formation_failures": THEME_FORMATION_FAILURES_LANE,
    "candidate_generation_errors": CANDIDATE_GENERATION_ERRORS_LANE,
    "newsless_or_unexplained": NEWSLESS_OR_UNEXPLAINED_LANE,
}


@dataclass(frozen=True)
class SweepResult:
    accepted_episode_count: int
    swept_episode_ids: list[str]
    accepted_record_count: int
    available_record_count: int
    available_record_ids: list[str]
    training_eligible_available_record_count: int
    training_eligible_available_record_ids: list[str]
    swept_record_ids: list[str]
    artifact_paths: list[str]
    record_artifact_paths: list[str]
    shard_count: int
    record_shard_count: int
    cache_hits: int
    record_cache_hits: int
    token_counts: dict[str, int]
    errors: list[str]
    memory_coverage_manifest_path: str | None = None
    memory_coverage_manifest_sha256: str | None = None
    memory_coverage_cache_hit: bool = False
    corpus_manifest_sha256: str | None = None


@dataclass(frozen=True)
class SweepPayloadBurden:
    accepted_episode_count: int
    accepted_record_count: int
    episode_shard_count: int
    record_shard_count: int
    episode_artifact_bytes: int
    record_artifact_bytes: int
    episode_artifact_tokens: int
    record_artifact_tokens: int


class MemorySweeper:
    def __init__(self, root: Path, *, shard_episode_count: int) -> None:
        self.root = root
        self.shard_episode_count = max(1, shard_episode_count)
        self.store = ResearchStore(root)
        self.cache_dir = root / "data" / "cache" / "memory_sweep"
        self.checkpoint_dir = root / "runs" / "checkpoints" / "memory_sweep"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def estimate_payload_burden(self) -> SweepPayloadBurden:
        """Serialize current sweep payloads in memory without writing artifacts."""

        records = BrainRecordStore(self.root).list_records()
        episodes, _findings = self._available_episodes(
            datetime.max.replace(tzinfo=KST),
            records_present=bool(records),
        )
        trade_date = max(
            (
                *(episode.trade_date for episode in episodes),
                *(record.trade_date for record in records),
            ),
            default=date(1970, 1, 1),
        )
        cutoff_at = datetime.max.replace(tzinfo=KST)
        brain_version = current_brain_version(self.root) or "none"
        news_hash = sha256_text("")
        model_config_hash = sha256_text(canonical_json({}))
        episode_payloads: list[dict[str, object]] = []
        for shard_index, episode_shard in enumerate(self._shards(episodes), start=1):
            source_hashes = _episode_source_hashes(episode_shard, self.store.accepted_hashes())
            shard_hash = _episode_shard_hash(source_hashes)
            episode_payloads.append(
                self._build_contribution(
                    cache_key=stable_id("PROFILE-SWEEP", shard_hash, length=16),
                    mode="exhaustive",
                    trade_date=trade_date,
                    cutoff_at=cutoff_at,
                    brain_version=brain_version,
                    news_hash=news_hash,
                    shard_hash=shard_hash,
                    shard_index=shard_index,
                    episode_count=len(episode_shard),
                    episodes=episode_shard,
                    episode_source_hashes=source_hashes,
                    first_pass_mechanisms=[],
                    prompt_version=MEMORY_SWEEP_PROMPT_VERSION,
                    model_config_hash=model_config_hash,
                )
            )
        record_payloads: list[dict[str, object]] = []
        for shard_index, record_shard in enumerate(self._record_shards(records), start=1):
            source_hashes = _record_source_hashes(record_shard)
            shard_hash = _record_shard_hash(source_hashes)
            record_payloads.append(
                self._build_record_contribution(
                    cache_key=stable_id("PROFILE-RECSWEEP", shard_hash, length=16),
                    mode="exhaustive",
                    trade_date=trade_date,
                    cutoff_at=cutoff_at,
                    brain_version=brain_version,
                    news_hash=news_hash,
                    shard_hash=shard_hash,
                    shard_index=shard_index,
                    records=record_shard,
                    record_source_hashes=source_hashes,
                    first_pass_mechanisms=[],
                    prompt_version=MEMORY_SWEEP_PROMPT_VERSION,
                    model_config_hash=model_config_hash,
                )
            )
        episode_bytes, episode_tokens = _serialized_payload_burden(episode_payloads)
        record_bytes, record_tokens = _serialized_payload_burden(record_payloads)
        return SweepPayloadBurden(
            accepted_episode_count=len(episodes),
            accepted_record_count=len(records),
            episode_shard_count=len(episode_payloads),
            record_shard_count=len(record_payloads),
            episode_artifact_bytes=episode_bytes,
            record_artifact_bytes=record_bytes,
            episode_artifact_tokens=episode_tokens,
            record_artifact_tokens=record_tokens,
        )

    def sweep(
        self,
        *,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        run_id: str,
        current_news_texts: list[str],
        first_pass_mechanisms: list[str],
        model_config: dict[str, object] | None = None,
        brain_version: str | None = None,
        prompt_version: str = MEMORY_SWEEP_PROMPT_VERSION,
        emit_legacy_contributions: bool = True,
    ) -> SweepResult:
        mode = normalize_analysis_mode(mode)
        cache_model_config = model_config or {}
        model_config_hash = sha256_text(canonical_json(cache_model_config))
        record_store = BrainRecordStore(self.root)
        active_snapshot = active_memory_snapshot_manifest(self.root)
        evaluation_snapshot = (
            active_snapshot
            if active_snapshot is not None and active_snapshot.evaluation_only
            else None
        )
        if not emit_legacy_contributions:
            coverage = (
                build_memory_coverage_manifest_from_snapshot(
                    self.root,
                    snapshot=evaluation_snapshot,
                    cutoff_at=cutoff_at,
                    run_id=run_id,
                )
                if evaluation_snapshot is not None
                else build_memory_coverage_manifest(
                    self.root,
                    records=record_store.iter_records(),
                    cutoff_at=cutoff_at,
                    run_id=run_id,
                )
            )
            if evaluation_snapshot is not None:
                evaluation_episode_ids = _evaluation_covered_episode_ids(
                    self.root,
                    snapshot=evaluation_snapshot,
                )
                accepted: list[ResearchEpisode] = []
                accepted_store_findings: list[str] = []
            else:
                accepted, accepted_store_findings = self._available_episodes(
                    cutoff_at,
                    records_present=coverage.manifest.accepted_record_count > 0,
                    evaluation_snapshot=None,
                )
                evaluation_episode_ids = [
                    episode.episode_id for episode in accepted
                ]
            coverage_errors = list(accepted_store_findings)
            if not coverage.manifest.coverage_complete:
                coverage_errors.append("memory coverage manifest is incomplete")
            swept_episode_ids = (
                [] if mode == "fast" else evaluation_episode_ids
            )
            production_swept_record_ids = [] if mode == "fast" else coverage.available_record_ids
            return SweepResult(
                accepted_episode_count=len(evaluation_episode_ids),
                swept_episode_ids=swept_episode_ids,
                accepted_record_count=coverage.manifest.accepted_record_count,
                available_record_count=coverage.manifest.available_record_count,
                available_record_ids=coverage.available_record_ids,
                training_eligible_available_record_count=len(coverage.training_eligible_available_record_ids),
                training_eligible_available_record_ids=(coverage.training_eligible_available_record_ids),
                swept_record_ids=production_swept_record_ids,
                artifact_paths=[],
                record_artifact_paths=[],
                shard_count=0,
                record_shard_count=0,
                cache_hits=0,
                record_cache_hits=0,
                token_counts={"memory_sweep": 0, "record_memory_sweep": 0},
                errors=coverage_errors,
                memory_coverage_manifest_path=coverage.manifest_path,
                memory_coverage_manifest_sha256=coverage.manifest_sha256,
                memory_coverage_cache_hit=coverage.cache_hit,
                corpus_manifest_sha256=coverage.manifest.corpus_manifest_sha256,
            )

        if evaluation_snapshot is not None:
            raise ValueError(
                "evaluation replay supports compact coverage sweeps only"
            )

        all_records = record_store.list_records()
        accepted, accepted_store_findings = self._available_episodes(
            cutoff_at,
            records_present=bool(all_records),
        )
        available_records = [record for record in all_records if is_available_as_of(record.available_from, cutoff_at)]
        available_record_ids = [record.record_id for record in available_records]
        training_eligible_available_record_ids = [
            record.record_id for record in available_records if record.training_eligible
        ]
        coverage = build_memory_coverage_manifest(
            self.root,
            records=all_records,
            cutoff_at=cutoff_at,
            run_id=run_id,
        )
        if mode == "fast":
            return SweepResult(
                accepted_episode_count=len(accepted),
                swept_episode_ids=[],
                accepted_record_count=len(all_records),
                available_record_count=len(available_records),
                available_record_ids=available_record_ids,
                training_eligible_available_record_count=len(training_eligible_available_record_ids),
                training_eligible_available_record_ids=(training_eligible_available_record_ids),
                swept_record_ids=[],
                artifact_paths=[],
                record_artifact_paths=[],
                shard_count=0,
                record_shard_count=0,
                cache_hits=0,
                record_cache_hits=0,
                token_counts={"memory_sweep": 0, "record_memory_sweep": 0},
                errors=accepted_store_findings,
                memory_coverage_manifest_path=coverage.manifest_path,
                memory_coverage_manifest_sha256=coverage.manifest_sha256,
                memory_coverage_cache_hit=coverage.cache_hit,
                corpus_manifest_sha256=coverage.manifest.corpus_manifest_sha256,
            )

        artifacts: list[str] = []
        record_artifacts: list[str] = []
        swept_ids: list[str] = []
        swept_record_ids: list[str] = []
        cache_hits = 0
        record_cache_hits = 0
        run_dir = self.checkpoint_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        effective_brain_version = brain_version or current_brain_version(self.root) or "none"
        news_hash = sha256_text("\n---NEWS---\n".join(current_news_texts))
        accepted_hashes = self.store.accepted_hashes()
        shards = list(self._shards(accepted))
        record_shards = list(self._record_shards(available_records))

        for shard_index, shard in enumerate(shards, start=1):
            episode_ids = [episode.episode_id for episode in shard]
            episode_source_hashes = _episode_source_hashes(shard, accepted_hashes)
            shard_hash = _episode_shard_hash(episode_source_hashes)
            cache_key = stable_id(
                "SWEEP",
                effective_brain_version,
                news_hash,
                shard_hash,
                mode,
                cutoff_at.isoformat(),
                prompt_version,
                model_config_hash,
                length=16,
            )
            cache_path = self.cache_dir / f"{cache_key}.json"
            cached_payload = self._read_cached_contribution(
                cache_path=cache_path,
                cache_key=cache_key,
                mode=mode,
                trade_date=trade_date,
                cutoff_at=cutoff_at,
                brain_version=effective_brain_version,
                news_hash=news_hash,
                shard_hash=shard_hash,
                episode_ids=episode_ids,
                episode_source_hashes=episode_source_hashes,
                prompt_version=prompt_version,
                model_config_hash=model_config_hash,
            )
            if cached_payload is not None:
                payload = cached_payload
                cache_hits += 1
            else:
                payload = self._build_contribution(
                    cache_key=cache_key,
                    mode=mode,
                    trade_date=trade_date,
                    cutoff_at=cutoff_at,
                    brain_version=effective_brain_version,
                    news_hash=news_hash,
                    shard_hash=shard_hash,
                    shard_index=shard_index,
                    episode_count=len(shard),
                    episodes=shard,
                    episode_source_hashes=episode_source_hashes,
                    first_pass_mechanisms=first_pass_mechanisms,
                    prompt_version=prompt_version,
                    model_config_hash=model_config_hash,
                )
                write_json(cache_path, payload)
            run_path = run_dir / f"shard_{shard_index:04d}.json"
            write_json(run_path, payload)
            artifacts.append(run_path.relative_to(self.root).as_posix())
            swept_ids.extend(episode_ids)

        for shard_index, record_shard in enumerate(record_shards, start=1):
            record_ids = [record.record_id for record in record_shard]
            record_source_hashes = _record_source_hashes(record_shard)
            shard_hash = _record_shard_hash(record_source_hashes)
            cache_key = stable_id(
                "RECSWEEP",
                effective_brain_version,
                news_hash,
                shard_hash,
                mode,
                cutoff_at.isoformat(),
                prompt_version,
                model_config_hash,
                POLARITY_CLASSIFIER_VERSION,
                length=16,
            )
            cache_path = self.cache_dir / f"{cache_key}.json"
            cached_record_payload = self._read_cached_record_contribution(
                cache_path=cache_path,
                cache_key=cache_key,
                mode=mode,
                trade_date=trade_date,
                cutoff_at=cutoff_at,
                brain_version=effective_brain_version,
                news_hash=news_hash,
                shard_hash=shard_hash,
                record_ids=record_ids,
                records=record_shard,
                record_source_hashes=record_source_hashes,
                prompt_version=prompt_version,
                model_config_hash=model_config_hash,
            )
            if cached_record_payload is not None:
                record_payload = cached_record_payload
                record_cache_hits += 1
            else:
                record_payload = self._build_record_contribution(
                    cache_key=cache_key,
                    mode=mode,
                    trade_date=trade_date,
                    cutoff_at=cutoff_at,
                    brain_version=effective_brain_version,
                    news_hash=news_hash,
                    shard_hash=shard_hash,
                    shard_index=shard_index,
                    records=record_shard,
                    record_source_hashes=record_source_hashes,
                    first_pass_mechanisms=first_pass_mechanisms,
                    prompt_version=prompt_version,
                    model_config_hash=model_config_hash,
                )
                write_json(cache_path, record_payload)
            run_path = run_dir / f"record_shard_{shard_index:04d}.json"
            write_json(run_path, record_payload)
            record_artifacts.append(run_path.relative_to(self.root).as_posix())
            swept_record_ids.extend(record_ids)

        errors: list[str] = list(accepted_store_findings)
        if mode == "exhaustive":
            expected_ids = [episode.episode_id for episode in accepted]
            expected_counts = Counter(expected_ids)
            swept_counts = Counter(swept_ids)
            missing_ids = sorted((expected_counts - swept_counts).elements())
            duplicate_ids = sorted(
                episode_id for episode_id, count in swept_counts.items() if count > expected_counts.get(episode_id, 0)
            )
            unexpected_ids = sorted(set(swept_counts) - set(expected_counts))
            if missing_ids:
                errors.append("memory sweep missing accepted episodes: " + ", ".join(missing_ids))
            if duplicate_ids:
                errors.append("memory sweep duplicated accepted episodes: " + ", ".join(duplicate_ids))
            if unexpected_ids:
                errors.append("memory sweep included unavailable episodes: " + ", ".join(unexpected_ids))
            expected_record_ids = [record.record_id for record in available_records]
            expected_record_counts = Counter(expected_record_ids)
            swept_record_counts = Counter(swept_record_ids)
            missing_record_ids = sorted((expected_record_counts - swept_record_counts).elements())
            duplicate_record_ids = sorted(
                record_id
                for record_id, count in swept_record_counts.items()
                if count > expected_record_counts.get(record_id, 0)
            )
            unexpected_record_ids = sorted(set(swept_record_counts) - set(expected_record_counts))
            if missing_record_ids:
                errors.append("record memory sweep missing available records: " + ", ".join(missing_record_ids))
            if duplicate_record_ids:
                errors.append("record memory sweep duplicated available records: " + ", ".join(duplicate_record_ids))
            if unexpected_record_ids:
                errors.append("record memory sweep included unavailable records: " + ", ".join(unexpected_record_ids))
        return SweepResult(
            accepted_episode_count=len(accepted),
            swept_episode_ids=swept_ids,
            accepted_record_count=len(all_records),
            available_record_count=len(available_records),
            available_record_ids=available_record_ids,
            training_eligible_available_record_count=len(training_eligible_available_record_ids),
            training_eligible_available_record_ids=training_eligible_available_record_ids,
            swept_record_ids=swept_record_ids,
            artifact_paths=artifacts,
            record_artifact_paths=record_artifacts,
            shard_count=len(shards),
            record_shard_count=len(record_shards),
            cache_hits=cache_hits,
            record_cache_hits=record_cache_hits,
            token_counts={
                "memory_sweep": self._estimate_tokens(artifacts),
                "record_memory_sweep": self._estimate_tokens(record_artifacts),
            },
            errors=errors,
            memory_coverage_manifest_path=coverage.manifest_path,
            memory_coverage_manifest_sha256=coverage.manifest_sha256,
            memory_coverage_cache_hit=coverage.cache_hit,
            corpus_manifest_sha256=coverage.manifest.corpus_manifest_sha256,
        )

    def _available_episodes(
        self,
        cutoff_at: datetime,
        *,
        records_present: bool,
        evaluation_snapshot: MemoryCellSnapshotManifest | None = None,
    ) -> tuple[list[ResearchEpisode], list[str]]:
        try:
            source_episodes = self.store.list_accepted()
            if evaluation_snapshot is None:
                accepted = [
                    episode
                    for episode in source_episodes
                    if is_available_as_of(episode.available_from, cutoff_at)
                ]
            else:
                brain = read_json(
                    self.root / "brain" / "current" / "brain_manifest.json"
                )
                covered_ids = (
                    set(brain.get("covered_episode_ids", []))
                    if isinstance(brain, dict)
                    else set()
                )
                replay = load_snapshot_replay_availability(
                    self.root,
                    evaluation_snapshot,
                )
                if replay is None or not covered_ids:
                    raise ValueError(
                        "evaluation episode coverage is not bound to replay memory"
                    )
                accepted = []
                for episode in source_episodes:
                    if episode.episode_id not in covered_ids:
                        continue
                    override = replay.get(episode.episode_id)
                    if (
                        override is None
                        or override.source_trade_date != episode.trade_date
                    ):
                        raise ValueError(
                            "evaluation episode lacks replay availability: "
                            f"{episode.episode_id}"
                        )
                    projected = episode.model_copy(
                        update={
                            "available_from": override.replay_available_from
                        }
                    )
                    if is_available_as_of(projected.available_from, cutoff_at):
                        accepted.append(projected)
                if {episode.episode_id for episode in accepted} != covered_ids:
                    raise ValueError(
                        "evaluation brain episode coverage differs from replay scope"
                    )
        except (
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValidationError,
            TypeError,
            ValueError,
        ):
            if records_present:
                return [], ["accepted episode store is unreadable"]
            raise
        return accepted, []

    def _shards(self, episodes: list[ResearchEpisode]) -> list[list[ResearchEpisode]]:
        return [
            episodes[index : index + self.shard_episode_count]
            for index in range(0, len(episodes), self.shard_episode_count)
        ]

    def _record_shards(
        self,
        records: list[BrainRecordEnvelope],
    ) -> list[list[BrainRecordEnvelope]]:
        return [
            records[index : index + self.shard_episode_count]
            for index in range(0, len(records), self.shard_episode_count)
        ]

    def _read_cached_contribution(
        self,
        *,
        cache_path: Path,
        cache_key: str,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        brain_version: str,
        news_hash: str,
        shard_hash: str,
        episode_ids: list[str],
        episode_source_hashes: dict[str, str],
        prompt_version: str,
        model_config_hash: str,
    ) -> dict[str, object] | None:
        if not cache_path.exists():
            return None
        try:
            payload = read_json(cache_path)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        if not self._cache_matches(
            payload,
            cache_key=cache_key,
            mode=mode,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            brain_version=brain_version,
            news_hash=news_hash,
            shard_hash=shard_hash,
            episode_ids=episode_ids,
            episode_source_hashes=episode_source_hashes,
            prompt_version=prompt_version,
            model_config_hash=model_config_hash,
        ):
            return None
        cached = {str(key): value for key, value in payload.items()}
        cached["from_cache"] = True
        return cached

    def _read_cached_record_contribution(
        self,
        *,
        cache_path: Path,
        cache_key: str,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        brain_version: str,
        news_hash: str,
        shard_hash: str,
        record_ids: list[str],
        records: list[BrainRecordEnvelope],
        record_source_hashes: dict[str, str],
        prompt_version: str,
        model_config_hash: str,
    ) -> dict[str, object] | None:
        if not cache_path.exists():
            return None
        try:
            payload = read_json(cache_path)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        if not self._record_cache_matches(
            payload,
            cache_key=cache_key,
            mode=mode,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            brain_version=brain_version,
            news_hash=news_hash,
            shard_hash=shard_hash,
            record_ids=record_ids,
            records=records,
            record_source_hashes=record_source_hashes,
            prompt_version=prompt_version,
            model_config_hash=model_config_hash,
        ):
            return None
        cached = {str(key): value for key, value in payload.items()}
        cached["from_cache"] = True
        return cached

    def _cache_matches(
        self,
        payload: dict[Any, Any],
        *,
        cache_key: str,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        brain_version: str,
        news_hash: str,
        shard_hash: str,
        episode_ids: list[str],
        episode_source_hashes: dict[str, str],
        prompt_version: str,
        model_config_hash: str,
    ) -> bool:
        return (
            payload.get("schema_version") == "nslab.memory_sweep_contribution.v1"
            and payload.get("cache_key") == cache_key
            and payload.get("mode") == mode
            and payload.get("trade_date") == trade_date.isoformat()
            and payload.get("cutoff_at") == cutoff_at.isoformat()
            and payload.get("brain_version") == brain_version
            and payload.get("current_news_sha256") == news_hash
            and payload.get("episode_shard_sha256") == shard_hash
            and payload.get("episode_ids") == episode_ids
            and payload.get("episode_shard_source_hashes") == episode_source_hashes
            and payload.get("prompt_version") == prompt_version
            and payload.get("model_config_sha256") == model_config_hash
        )

    def _record_cache_matches(
        self,
        payload: dict[Any, Any],
        *,
        cache_key: str,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        brain_version: str,
        news_hash: str,
        shard_hash: str,
        record_ids: list[str],
        records: list[BrainRecordEnvelope],
        record_source_hashes: dict[str, str],
        prompt_version: str,
        model_config_hash: str,
    ) -> bool:
        return (
            payload.get("schema_version") == "nslab.record_memory_sweep_contribution.v1"
            and payload.get("cache_key") == cache_key
            and payload.get("mode") == mode
            and payload.get("trade_date") == trade_date.isoformat()
            and payload.get("cutoff_at") == cutoff_at.isoformat()
            and payload.get("brain_version") == brain_version
            and payload.get("current_news_sha256") == news_hash
            and payload.get("record_shard_sha256") == shard_hash
            and payload.get("record_ids") == record_ids
            and payload.get("record_shard_source_hashes") == record_source_hashes
            and payload.get("record_source_hash_kind") == "canonical_full_envelope_sha256"
            and payload.get("routing_classifier_version") == POLARITY_CLASSIFIER_VERSION
            and payload.get("record_routing_sha256")
            == brain_record_routing_root_sha256(records)
            and _payload_lane_projection(payload)
            == record_sweep_lane_projection(records)
            and payload.get("prompt_version") == prompt_version
            and payload.get("model_config_sha256") == model_config_hash
        )

    def _build_contribution(
        self,
        *,
        cache_key: str,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        brain_version: str,
        news_hash: str,
        shard_hash: str,
        shard_index: int,
        episode_count: int,
        episodes: list[ResearchEpisode],
        episode_source_hashes: dict[str, str],
        first_pass_mechanisms: list[str],
        prompt_version: str,
        model_config_hash: str,
    ) -> dict[str, object]:
        episode_ids = [episode.episode_id for episode in episodes]
        summaries = [episode.blind_analysis.summary for episode in episodes]
        lessons = [mechanism for episode in episodes for mechanism in episode.blind_analysis.open_world_mechanisms]
        return {
            "schema_version": "nslab.memory_sweep_contribution.v1",
            "cache_key": cache_key,
            "mode": mode,
            "trade_date": trade_date.isoformat(),
            "cutoff_at": cutoff_at.isoformat(),
            "brain_version": brain_version,
            "prompt_version": prompt_version,
            "model_config_sha256": model_config_hash,
            "current_news_sha256": news_hash,
            "episode_shard_sha256": shard_hash,
            "episode_shard_source_hashes": episode_source_hashes,
            "shard_index": shard_index,
            "episode_count": episode_count,
            "episode_ids": episode_ids,
            "related_lessons": lessons,
            "positive_analogs": summaries,
            "negative_analogs": [],
            "negative_controls": [],
            "near_misses": [miss for episode in episodes for miss in episode.misses],
            "counterexamples": [claim.statement for episode in episodes for claim in episode.counterexamples],
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

    def _build_record_contribution(
        self,
        *,
        cache_key: str,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        brain_version: str,
        news_hash: str,
        shard_hash: str,
        shard_index: int,
        records: list[BrainRecordEnvelope],
        record_source_hashes: dict[str, str],
        first_pass_mechanisms: list[str],
        prompt_version: str,
        model_config_hash: str,
    ) -> dict[str, object]:
        lane_projection = record_sweep_lane_projection(records)
        return {
            "schema_version": "nslab.record_memory_sweep_contribution.v1",
            "cache_key": cache_key,
            "mode": mode,
            "trade_date": trade_date.isoformat(),
            "cutoff_at": cutoff_at.isoformat(),
            "brain_version": brain_version,
            "prompt_version": prompt_version,
            "model_config_sha256": model_config_hash,
            "current_news_sha256": news_hash,
            "record_shard_sha256": shard_hash,
            "record_shard_source_hashes": record_source_hashes,
            "record_source_hash_kind": "canonical_full_envelope_sha256",
            "routing_classifier_version": POLARITY_CLASSIFIER_VERSION,
            "record_routing_sha256": brain_record_routing_root_sha256(records),
            "shard_index": shard_index,
            "record_count": len(records),
            "record_ids": [record.record_id for record in records],
            "record_types": dict(Counter(record.record_type for record in records)),
            "training_targets": dict(Counter(record.training_target or "UNKNOWN" for record in records)),
            **lane_projection,
            "supporting_points": first_pass_mechanisms,
            "objections": [
                "Do not treat record retrieval misses as candidate blockers.",
                "Respect every record.available_from cutoff before applying memory.",
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


def _evaluation_covered_episode_ids(
    root: Path,
    *,
    snapshot: MemoryCellSnapshotManifest,
) -> list[str]:
    brain = read_json(root / "brain" / "current" / "brain_manifest.json")
    covered = brain.get("covered_episode_ids") if isinstance(brain, dict) else None
    if (
        not isinstance(covered, list)
        or brain.get("production_memory_snapshot_id") != snapshot.snapshot_id
    ):
        raise ValueError("evaluation brain episode coverage is invalid")
    covered_ids = sorted(
        {str(value) for value in covered if isinstance(value, str) and value}
    )
    replay = load_snapshot_replay_availability(root, snapshot)
    if not covered_ids or replay is None or not set(covered_ids).issubset(replay):
        raise ValueError(
            "evaluation brain episode coverage is not bound to replay availability"
        )
    return covered_ids


def _episode_source_hashes(
    episodes: list[ResearchEpisode],
    accepted_hashes: dict[str, str],
) -> dict[str, str]:
    return {
        episode.episode_id: accepted_hashes.get(episode.episode_id)
        or sha256_text(canonical_json(episode.model_dump(mode="json")))
        for episode in episodes
    }


def _episode_shard_hash(episode_source_hashes: dict[str, str]) -> str:
    return sha256_text(
        canonical_json(
            [
                {"episode_id": episode_id, "source_sha256": source_hash}
                for episode_id, source_hash in sorted(episode_source_hashes.items())
            ]
        )
    )


def _record_source_hashes(records: list[BrainRecordEnvelope]) -> dict[str, str]:
    return {record.record_id: brain_record_envelope_sha256(record) for record in records}


def _record_shard_hash(record_source_hashes: dict[str, str]) -> str:
    return sha256_text(
        canonical_json(
            [
                {"record_id": record_id, "source_sha256": source_hash}
                for record_id, source_hash in sorted(record_source_hashes.items())
            ]
        )
    )


def _serialized_payload_burden(
    payloads: list[dict[str, object]],
) -> tuple[int, int]:
    serialized = [json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n" for payload in payloads]
    return (
        sum(len(payload.encode("utf-8")) for payload in serialized),
        sum(max(1, len(payload) // 4) for payload in serialized),
    )


def _record_summary(record: BrainRecordEnvelope) -> dict[str, object]:
    payload = record.payload
    routing = record_routing_metadata(record)
    return {
        "record_id": record.record_id,
        "episode_id": record.episode_id,
        "record_type": record.record_type,
        "training_target": record.training_target,
        "evidence_phase": record.evidence_phase,
        "training_eligible": record.training_eligible,
        "eligibility_reason": record.eligibility_reason,
        "training_exclusion_reason": payload.get("training_exclusion_reason"),
        "evidence_polarity": routing.evidence_polarity,
        "label_quality": routing.label_quality,
        "routing_disposition": routing.routing_disposition,
        "polarity_classifier_version": routing.polarity_classifier_version,
        "threshold_source": routing.threshold_source,
        "threshold_role": routing.threshold_role,
        "memory_lanes": routing.memory_lanes,
        "available_from": record.available_from.isoformat(),
        "response_class": record_response_class(payload),
        "outcome": record_outcome_payload(payload),
        "ticker": payload.get("ticker"),
        "theme_id": payload.get("theme_id"),
        "path_type": payload.get("path_type"),
        "confidence_label": record.confidence_label,
    }


def record_sweep_lane_projection(
    records: list[BrainRecordEnvelope],
) -> dict[str, list[dict[str, object]]]:
    lanes_by_record_id = {record.record_id: record_memory_lanes(record) for record in records}
    projection = {
        field: [
            _record_summary(record)
            for record in records
            if lane in lanes_by_record_id[record.record_id]
        ]
        for field, lane in RECORD_SWEEP_LANE_FIELDS.items()
    }
    projection["negative_analogs"] = list(projection["negative_controls"])
    return projection


def _payload_lane_projection(
    payload: dict[Any, Any],
) -> dict[str, object]:
    return {field: payload.get(field) for field in RECORD_SWEEP_LANE_FIELDS}
