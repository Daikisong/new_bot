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

from news_scalping_lab.brain.compiler import current_brain_version
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.contracts.models import ResearchEpisode
from news_scalping_lab.records.models import (
    CANDIDATE_ERROR_RECORD_TYPES,
    BrainRecordEnvelope,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    canonical_json,
    is_available_as_of,
    read_json,
    sha256_text,
    stable_id,
    write_json,
)

MEMORY_SWEEP_PROMPT_VERSION = "memory_sweep.shard_analysis.v1"


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


class MemorySweeper:
    def __init__(self, root: Path, *, shard_episode_count: int) -> None:
        self.root = root
        self.shard_episode_count = max(1, shard_episode_count)
        self.store = ResearchStore(root)
        self.cache_dir = root / "data" / "cache" / "memory_sweep"
        self.checkpoint_dir = root / "runs" / "checkpoints" / "memory_sweep"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

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
    ) -> SweepResult:
        mode = normalize_analysis_mode(mode)
        cache_model_config = model_config or {}
        model_config_hash = sha256_text(canonical_json(cache_model_config))
        accepted = self._available_episodes(cutoff_at)
        all_records = BrainRecordStore(self.root).list_records()
        available_records = [
            record
            for record in all_records
            if is_available_as_of(record.available_from, cutoff_at)
        ]
        available_record_ids = [record.record_id for record in available_records]
        training_eligible_available_record_ids = [
            record.record_id for record in available_records if record.training_eligible
        ]
        if mode == "fast":
            return SweepResult(
                accepted_episode_count=len(accepted),
                swept_episode_ids=[],
                accepted_record_count=len(all_records),
                available_record_count=len(available_records),
                available_record_ids=available_record_ids,
                training_eligible_available_record_count=len(
                    training_eligible_available_record_ids
                ),
                training_eligible_available_record_ids=(
                    training_eligible_available_record_ids
                ),
                swept_record_ids=[],
                artifact_paths=[],
                record_artifact_paths=[],
                shard_count=0,
                record_shard_count=0,
                cache_hits=0,
                record_cache_hits=0,
                token_counts={"memory_sweep": 0, "record_memory_sweep": 0},
                errors=[],
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

        errors: list[str] = []
        if mode == "exhaustive":
            expected_ids = [episode.episode_id for episode in accepted]
            expected_counts = Counter(expected_ids)
            swept_counts = Counter(swept_ids)
            missing_ids = sorted((expected_counts - swept_counts).elements())
            duplicate_ids = sorted(
                episode_id
                for episode_id, count in swept_counts.items()
                if count > expected_counts.get(episode_id, 0)
            )
            unexpected_ids = sorted(set(swept_counts) - set(expected_counts))
            if missing_ids:
                errors.append(
                    "memory sweep missing accepted episodes: " + ", ".join(missing_ids)
                )
            if duplicate_ids:
                errors.append(
                    "memory sweep duplicated accepted episodes: " + ", ".join(duplicate_ids)
                )
            if unexpected_ids:
                errors.append(
                    "memory sweep included unavailable episodes: " + ", ".join(unexpected_ids)
                )
            expected_record_ids = [record.record_id for record in available_records]
            expected_record_counts = Counter(expected_record_ids)
            swept_record_counts = Counter(swept_record_ids)
            missing_record_ids = sorted(
                (expected_record_counts - swept_record_counts).elements()
            )
            duplicate_record_ids = sorted(
                record_id
                for record_id, count in swept_record_counts.items()
                if count > expected_record_counts.get(record_id, 0)
            )
            unexpected_record_ids = sorted(
                set(swept_record_counts) - set(expected_record_counts)
            )
            if missing_record_ids:
                errors.append(
                    "record memory sweep missing available records: "
                    + ", ".join(missing_record_ids)
                )
            if duplicate_record_ids:
                errors.append(
                    "record memory sweep duplicated available records: "
                    + ", ".join(duplicate_record_ids)
                )
            if unexpected_record_ids:
                errors.append(
                    "record memory sweep included unavailable records: "
                    + ", ".join(unexpected_record_ids)
                )
        return SweepResult(
            accepted_episode_count=len(accepted),
            swept_episode_ids=swept_ids,
            accepted_record_count=len(all_records),
            available_record_count=len(available_records),
            available_record_ids=available_record_ids,
            training_eligible_available_record_count=len(
                training_eligible_available_record_ids
            ),
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
        )

    def _available_episodes(self, cutoff_at: datetime) -> list[ResearchEpisode]:
        return [
            episode
            for episode in self.store.list_accepted()
            if is_available_as_of(episode.available_from, cutoff_at)
        ]

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
        lessons = [
            mechanism
            for episode in episodes
            for mechanism in episode.blind_analysis.open_world_mechanisms
        ]
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
            "counterexamples": [
                claim.statement for episode in episodes for claim in episode.counterexamples
            ],
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
            "shard_index": shard_index,
            "record_count": len(records),
            "record_ids": [record.record_id for record in records],
            "record_types": dict(Counter(record.record_type for record in records)),
            "training_targets": dict(
                Counter(record.training_target or "UNKNOWN" for record in records)
            ),
            "positive_analogs": [
                _record_summary(record)
                for record in records
                if record.record_type
                in {
                    "supervised_issuer_day_case",
                    "supervised_direct_event_case",
                    "supervised_theme_formation_case",
                    "beneficiary_discovery_case",
                    "memory_claim",
                    "mechanism_memory",
                }
            ],
            "negative_analogs": [
                _record_summary(record)
                for record in records
                if "error_case" in record.record_type
                or record.record_type in {"counterexample"}
            ],
            "negative_controls": [
                _record_summary(record)
                for record in records
                if "error_case" in record.record_type
                or record.record_type in {"counterexample"}
            ],
            "near_misses": [
                _record_summary(record)
                for record in records
                if _is_near_miss_record(record)
            ],
            "counterexamples": [
                _record_summary(record)
                for record in records
                if record.record_type == "counterexample"
            ],
            "leader_selection_pairs": [
                _record_summary(record)
                for record in records
                if record.record_type == "blind_leader_preference_pair"
            ],
            "theme_formation_failures": [
                _record_summary(record)
                for record in records
                if record.record_type == "supervised_theme_formation_case"
            ],
            "candidate_generation_errors": [
                _record_summary(record)
                for record in records
                if record.record_type in CANDIDATE_ERROR_RECORD_TYPES
            ],
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
    return {
        record.record_id: record.normalized_payload_sha256
        for record in records
    }


def _record_shard_hash(record_source_hashes: dict[str, str]) -> str:
    return sha256_text(
        canonical_json(
            [
                {"record_id": record_id, "source_sha256": source_hash}
                for record_id, source_hash in sorted(record_source_hashes.items())
            ]
        )
    )


def _record_summary(record: BrainRecordEnvelope) -> dict[str, object]:
    payload = record.payload
    return {
        "record_id": record.record_id,
        "episode_id": record.episode_id,
        "record_type": record.record_type,
        "training_target": record.training_target,
        "evidence_phase": record.evidence_phase,
        "training_eligible": record.training_eligible,
        "available_from": record.available_from.isoformat(),
        "response_class": payload.get("response_class"),
        "ticker": payload.get("ticker"),
        "theme_id": payload.get("theme_id"),
        "path_type": payload.get("path_type"),
        "confidence_label": record.confidence_label,
    }


def _is_near_miss_record(record: BrainRecordEnvelope) -> bool:
    response_class = record.payload.get("response_class")
    if isinstance(response_class, str) and "near_miss" in response_class:
        return True
    outcome = record.payload.get("outcome")
    if isinstance(outcome, dict):
        outcome_response = outcome.get("response_class")
        return isinstance(outcome_response, str) and "near_miss" in outcome_response
    return False
