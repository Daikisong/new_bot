"""Context manifest assembly."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb
from pydantic import ValidationError

from news_scalping_lab.brain.compiler import (
    BRAIN_FILES,
    CATALOG_COMPILER_VERSION,
    SHARD_BRAIN_EPISODE_COUNT,
    BrainCompiler,
    current_brain_file_hashes,
    current_brain_version,
)
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.contracts.models import (
    BrainManifest,
    ContextManifest,
    MemoryClaim,
    PriceSnapshot,
    ResearchEpisode,
)
from news_scalping_lab.memory.index import (
    active_memory_snapshot_manifest,
    load_snapshot_replay_availability,
)
from news_scalping_lab.records.hashing import brain_record_envelope_sha256
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    canonical_json,
    default_news_window_start,
    file_sha256,
    is_available_as_of,
    now_kst,
    read_json,
    stable_id,
    write_json,
)


@dataclass(frozen=True)
class BrainContextFiles:
    brain_version: str | None
    compiler_mode: str | None
    brain_compiler_provider: str | None
    brain_compiler_model: str | None
    brain_compiler_catalog_only: bool | None
    brain_file_hashes: dict[str, str]
    shard_brain_file_hashes: dict[str, str]


@dataclass(frozen=True)
class BrainCompilerMetadata:
    mode: str | None
    provider: str | None
    model: str | None
    catalog_only: bool | None


@dataclass(frozen=True)
class _EvaluationContextPopulation:
    snapshot_id: str
    accepted_episode_ids: tuple[str, ...]
    accepted_episodes: tuple[ResearchEpisode, ...]
    available_record_ids: tuple[str, ...]
    training_eligible_record_ids: tuple[str, ...]
    counterexample_record_ids: tuple[str, ...]
    record_hashes: dict[str, str]


_EVALUATION_CONTEXT_CACHE: dict[
    tuple[str, str, str],
    _EvaluationContextPopulation,
] = {}


class ContextAssembler:
    def __init__(
        self,
        root: Path,
        store: ResearchStore | None = None,
        *,
        shard_episode_count: int = SHARD_BRAIN_EPISODE_COUNT,
    ) -> None:
        self.root = root
        self.store = store or ResearchStore(root)
        self.shard_episode_count = max(1, shard_episode_count)

    def assemble(
        self,
        *,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        run_seed: str,
        retrieved_episode_ids: list[str] | None = None,
        retrieved_record_ids: list[str] | None = None,
        web_queries: list[str] | None = None,
    ) -> ContextManifest:
        mode = normalize_analysis_mode(mode)
        raw_retrieved_ids = retrieved_episode_ids or []
        raw_retrieved_record_ids = retrieved_record_ids or []
        web_query_list = web_queries or []
        evaluation = _evaluation_context_population(self.root, self.store)
        evaluation_episode_ids: list[str] | None = None
        if evaluation is None:
            all_records = BrainRecordStore(self.root).list_records()
            all_accepted, accepted_store_findings = _read_accepted_episodes_for_context(
                self.store,
                records=all_records,
            )
            accepted = [
                episode
                for episode in all_accepted
                if is_available_as_of(episode.available_from, cutoff_at)
            ]
            unavailable = [
                episode
                for episode in all_accepted
                if not is_available_as_of(episode.available_from, cutoff_at)
            ]
            available_records = [
                record
                for record in all_records
                if is_available_as_of(record.available_from, cutoff_at)
            ]
            unavailable_records = [
                record
                for record in all_records
                if not is_available_as_of(record.available_from, cutoff_at)
            ]
            available_record_ids = [
                record.record_id for record in available_records
            ]
            training_eligible_available_record_ids = [
                record.record_id
                for record in available_records
                if record.training_eligible
            ]
            available_record_hashes = {
                record.record_id: brain_record_envelope_sha256(record)
                for record in available_records
            }
            retrieved_record_ids, excluded_retrieved_record_ids = (
                _filter_retrieved_record_ids_available_as_of(
                    raw_retrieved_record_ids,
                    available_records=available_records,
                    unavailable_records=unavailable_records,
                )
            )
            available_counterexample_record_ids = [
                record.record_id
                for record in available_records
                if record.record_type == "counterexample"
            ]
            accepted_record_count = len(all_records)
        else:
            all_records = []
            accepted_store_findings = []
            accepted = list(evaluation.accepted_episodes)
            evaluation_episode_ids = list(evaluation.accepted_episode_ids)
            all_accepted = accepted
            unavailable = []
            available_records = []
            unavailable_records = []
            available_record_ids = list(evaluation.available_record_ids)
            training_eligible_available_record_ids = list(
                evaluation.training_eligible_record_ids
            )
            available_record_hashes = dict(evaluation.record_hashes)
            available_id_set = set(available_record_ids)
            retrieved_record_ids = [
                record_id
                for record_id in raw_retrieved_record_ids
                if record_id in available_id_set
            ]
            excluded_retrieved_record_ids = [
                record_id
                for record_id in raw_retrieved_record_ids
                if record_id not in available_id_set
            ]
            available_counterexample_record_ids = list(
                evaluation.counterexample_record_ids
            )
            accepted_record_count = len(available_record_ids)
        accepted_ids = (
            evaluation_episode_ids
            if evaluation_episode_ids is not None
            else [episode.episode_id for episode in accepted]
        )
        all_accepted_ids = (
            list(evaluation_episode_ids)
            if evaluation_episode_ids is not None
            else [episode.episode_id for episode in all_accepted]
        )
        unavailable_ids = [episode.episode_id for episode in unavailable]
        retrieved_ids, excluded_retrieved_ids = _filter_retrieved_ids_available_as_of(
            raw_retrieved_ids,
            accepted=accepted,
            unavailable=unavailable,
        )
        accepted_hashes = self._accepted_hashes_for(accepted_ids)
        run_id = stable_id(
            "RUN",
            canonical_json(
                {
                    "accepted_episode_hashes": accepted_hashes,
                    "accepted_episode_ids": accepted_ids,
                    "available_record_hashes": available_record_hashes,
                    "available_record_ids": available_record_ids,
                    "cutoff_at": cutoff_at.isoformat(),
                    "excluded_retrieved_episode_ids": excluded_retrieved_ids,
                    "excluded_retrieved_record_ids": excluded_retrieved_record_ids,
                    "mode": mode,
                    "retrieved_episode_ids": retrieved_ids,
                    "retrieved_record_ids": retrieved_record_ids,
                    "run_seed": run_seed,
                    "trade_date": trade_date.isoformat(),
                    "web_queries": web_query_list,
                }
            ),
        )
        counterexample_ids = [episode.episode_id for episode in accepted if episode.counterexamples]
        swept_ids = accepted_ids if mode in {"exhaustive", "brain"} else []
        swept_record_ids = available_record_ids if mode in {"exhaustive", "brain"} else []
        retrieved_record_id_set = set(retrieved_record_ids)
        counterexample_record_ids = [
            record_id
            for record_id in available_counterexample_record_ids
            if mode in {"exhaustive", "brain"}
            or record_id in retrieved_record_id_set
        ]
        errors: list[str] = []
        errors.extend(accepted_store_findings)
        if mode == "exhaustive" and len(swept_ids) != len(accepted_ids):
            errors.append("exhaustive coverage mismatch")
        if mode == "exhaustive" and len(swept_record_ids) != len(available_record_ids):
            errors.append("exhaustive record coverage mismatch")
        brain_context = self._brain_context_files(
            run_id=run_id,
            cutoff_at=cutoff_at,
            accepted=accepted,
            required_episode_ids=accepted_ids,
        )
        manifest = ContextManifest(
            run_id=run_id,
            mode=mode,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            as_of=cutoff_at,
            created_at=now_kst(),
            news_window_start_at=default_news_window_start(trade_date),
            news_window_end_at=cutoff_at,
            brain_version=brain_context.brain_version,
            compiler_mode=brain_context.compiler_mode,
            brain_compiler_provider=brain_context.brain_compiler_provider,
            brain_compiler_model=brain_context.brain_compiler_model,
            brain_compiler_catalog_only=brain_context.brain_compiler_catalog_only,
            brain_files=list(brain_context.brain_file_hashes.keys()),
            brain_file_hashes=brain_context.brain_file_hashes,
            shard_brain_files=list(brain_context.shard_brain_file_hashes.keys()),
            shard_brain_file_hashes=brain_context.shard_brain_file_hashes,
            accepted_episode_count=len(accepted_ids),
            total_accepted_episode_count=len(all_accepted_ids),
            total_accepted_episode_ids=all_accepted_ids,
            available_episode_count=len(accepted_ids),
            unavailable_episode_count=len(unavailable_ids),
            unavailable_episode_ids=unavailable_ids,
            swept_episode_count=len(swept_ids),
            swept_episode_ids=swept_ids,
            retrieved_episode_ids=retrieved_ids,
            excluded_retrieved_episode_ids=excluded_retrieved_ids,
            counterexample_episode_ids=counterexample_ids,
            accepted_record_count=accepted_record_count,
            available_record_count=len(available_record_ids),
            available_record_ids=available_record_ids,
            training_eligible_available_record_count=len(training_eligible_available_record_ids),
            training_eligible_available_record_ids=training_eligible_available_record_ids,
            swept_record_count=len(swept_record_ids),
            swept_record_ids=swept_record_ids,
            retrieved_record_ids=retrieved_record_ids,
            excluded_retrieved_record_ids=excluded_retrieved_record_ids,
            counterexample_record_ids=counterexample_record_ids,
            token_counts={},
            truncations=[],
            web_queries=web_query_list,
            web_sources=[],
            price_snapshot=PriceSnapshot(
                source_name="blind-guarded",
                source_ref="provider://blind-guarded",
                as_of=cutoff_at,
                allowed_through=date.fromordinal(trade_date.toordinal() - 1),
            ),
            llm_model_config={"provider": "mock-compatible", "mode": mode},
            prompt_hashes={},
            errors=errors,
        )
        if mode == "exhaustive" and manifest.swept_episode_count != manifest.accepted_episode_count:
            manifest.errors.append("swept_episode_count must equal accepted_episode_count")
        if mode == "exhaustive" and manifest.swept_record_count != manifest.available_record_count:
            manifest.errors.append("swept_record_count must equal available_record_count")
        return manifest

    def _accepted_hashes_for(self, accepted_ids: list[str]) -> dict[str, str]:
        hashes = self.store.accepted_hashes()
        return {episode_id: hashes[episode_id] for episode_id in accepted_ids if episode_id in hashes}

    def _brain_context_files(
        self,
        *,
        run_id: str,
        cutoff_at: datetime,
        accepted: list[ResearchEpisode],
        required_episode_ids: list[str] | None = None,
    ) -> BrainContextFiles:
        current_hashes = current_brain_file_hashes(self.root)
        current_shard_hashes = current_shard_brain_file_hashes(self.root)
        accepted_ids = required_episode_ids or [
            episode.episode_id for episode in accepted
        ]
        if self._current_context_is_safe_and_complete(
            cutoff_at=cutoff_at,
            accepted_ids=accepted_ids,
            brain_file_hashes=current_hashes,
            shard_brain_file_hashes=current_shard_hashes,
        ):
            return self._write_current_brain_context_checkpoint(
                run_id=run_id,
                brain_version=current_brain_version(self.root),
                brain_file_hashes=current_hashes,
                shard_brain_file_hashes=current_shard_hashes,
            )
        active_snapshot = active_memory_snapshot_manifest(self.root)
        if active_snapshot is not None and active_snapshot.evaluation_only:
            raise ValueError(
                "evaluation context cannot synthesize a replacement brain"
            )
        return self._write_as_of_brain_context(
            run_id=run_id,
            cutoff_at=cutoff_at,
            accepted=accepted,
        )

    def _current_context_is_safe_and_complete(
        self,
        *,
        cutoff_at: datetime,
        accepted_ids: list[str],
        brain_file_hashes: dict[str, str],
        shard_brain_file_hashes: dict[str, str],
    ) -> bool:
        if accepted_ids and not brain_file_hashes:
            return False
        active_snapshot = active_memory_snapshot_manifest(self.root)
        if active_snapshot is not None and active_snapshot.evaluation_only:
            coverage_ids = self._current_brain_coverage_ids()
            brain = read_json(
                self.root / "brain" / "current" / "brain_manifest.json"
            )
            return (
                coverage_ids is not None
                and set(coverage_ids) == set(accepted_ids)
                and len(coverage_ids) == len(accepted_ids)
                and isinstance(brain, dict)
                and brain.get("production_memory_snapshot_id")
                == active_snapshot.snapshot_id
            )
        if accepted_ids and not shard_brain_file_hashes:
            return False
        if self._current_shard_episode_count() != self.shard_episode_count:
            return False
        all_accepted = _read_accepted_episodes_for_current_context_check(self.store)
        if all_accepted is None:
            return False
        future_episode_ids = [
            episode.episode_id for episode in all_accepted if not is_available_as_of(episode.available_from, cutoff_at)
        ]
        if self._context_files_contain_any_episode_id(
            [*brain_file_hashes, *shard_brain_file_hashes],
            future_episode_ids,
        ):
            return False
        coverage_ids = self._current_brain_coverage_ids()
        return coverage_ids is not None and coverage_ids == accepted_ids

    def _current_brain_coverage_ids(self) -> list[str] | None:
        coverage_path = self.root / "brain" / "current" / "coverage_manifest.json"
        if not coverage_path.exists():
            return None
        try:
            data = json.loads(coverage_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        covered = data.get("covered_episode_ids")
        if not isinstance(covered, list):
            return None
        return [episode_id for episode_id in covered if isinstance(episode_id, str)]

    def _current_shard_episode_count(self) -> int | None:
        manifest_path = self.root / "memory" / "shard_brains" / "current" / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        value = payload.get("shard_episode_count")
        return value if isinstance(value, int) else None

    def _context_files_contain_any_episode_id(
        self,
        relative_paths: list[str],
        episode_ids: list[str],
    ) -> bool:
        if not episode_ids:
            return False
        for relative_path in relative_paths:
            path = self.root / relative_path
            if not path.exists() or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if any(episode_id in text for episode_id in episode_ids):
                return True
        return False

    def _write_current_brain_context_checkpoint(
        self,
        *,
        run_id: str,
        brain_version: str | None,
        brain_file_hashes: dict[str, str],
        shard_brain_file_hashes: dict[str, str],
    ) -> BrainContextFiles:
        root = self.root / "runs" / "checkpoints" / "brain_context" / run_id
        if root.exists():
            shutil.rmtree(root)
        brain_dir = root / "brain"
        shard_dir = root / "shards"
        brain_dir.mkdir(parents=True, exist_ok=True)
        shard_dir.mkdir(parents=True, exist_ok=True)
        for relative_path in brain_file_hashes:
            source = self.root / relative_path
            if source.is_file():
                shutil.copy2(source, brain_dir / source.name)
        for relative_path in shard_brain_file_hashes:
            source = self.root / relative_path
            if source.is_file():
                shutil.copy2(source, shard_dir / source.name)
        compiler_metadata = _current_brain_compiler_metadata(self.root)
        return BrainContextFiles(
            brain_version=brain_version,
            compiler_mode=compiler_metadata.mode,
            brain_compiler_provider=compiler_metadata.provider,
            brain_compiler_model=compiler_metadata.model,
            brain_compiler_catalog_only=compiler_metadata.catalog_only,
            brain_file_hashes=_file_hashes_relative_to_root(self.root, brain_dir),
            shard_brain_file_hashes=_file_hashes_relative_to_root(self.root, shard_dir),
        )

    def _write_as_of_brain_context(
        self,
        *,
        run_id: str,
        cutoff_at: datetime,
        accepted: list[ResearchEpisode],
    ) -> BrainContextFiles:
        root = self.root / "runs" / "checkpoints" / "brain_context" / run_id
        if root.exists():
            shutil.rmtree(root)
        brain_dir = root / "brain"
        shard_dir = root / "shards"
        brain_dir.mkdir(parents=True, exist_ok=True)
        shard_dir.mkdir(parents=True, exist_ok=True)

        accepted_ids = [episode.episode_id for episode in accepted]
        accepted_id_set = set(accepted_ids)
        source_hashes = {
            episode_id: digest
            for episode_id, digest in self.store.accepted_hashes().items()
            if episode_id in accepted_id_set
        }
        version = stable_id(
            "brain-asof",
            cutoff_at.isoformat(),
            accepted_ids,
            canonical_json(source_hashes),
            self.shard_episode_count,
            length=10,
        )
        compiler = BrainCompiler(
            self.root,
            store=self.store,
            shard_episode_count=self.shard_episode_count,
        )
        claims = _dedupe_claims(
            [
                claim
                for episode in accepted
                for claim in compiler._claims_from_episode(
                    episode=episode,
                    last_updated_at=episode.available_from,
                )
            ]
        )
        manifest = BrainManifest(
            brain_version=version,
            created_at=cutoff_at,
            build_mode="asof_context",
            catalog_only=False,
            catalog_mode_reason=None,
            deprecated_mode_alias=False,
            production_eligible=False,
            last_full_rebuild_at=None,
            updated_episode_id=None,
            accepted_episode_count=len(accepted),
            covered_episode_count=len(accepted),
            covered_episode_ids=accepted_ids,
            claim_ids=[claim.claim_id for claim in claims],
            source_hashes=source_hashes,
            coverage_complete=True,
        )

        for file_name in BRAIN_FILES:
            title = file_name.removesuffix(".md").replace("_", " ").title()
            (brain_dir / file_name).write_text(
                compiler._brain_file_body(title, manifest, claims, file_name=file_name),
                encoding="utf-8",
            )
        (brain_dir / "claims.jsonl").write_text(
            "".join(claim.model_dump_json() + "\n" for claim in claims),
            encoding="utf-8",
        )
        write_json(
            brain_dir / "coverage_manifest.json",
            {
                "brain_version": manifest.brain_version,
                "created_at": manifest.created_at.isoformat(),
                "build_mode": manifest.build_mode,
                "last_full_rebuild_at": None,
                "updated_episode_id": None,
                "accepted_episode_count": manifest.accepted_episode_count,
                "covered_episode_count": manifest.covered_episode_count,
                "covered_episode_ids": manifest.covered_episode_ids,
                "missing_episode_ids": [],
                "coverage_complete": True,
                "as_of_cutoff_at": cutoff_at.isoformat(),
            },
        )
        write_json(brain_dir / "brain_manifest.json", manifest.model_dump(mode="json"))

        for shard_index, shard in enumerate(_episode_shards(accepted, self.shard_episode_count), start=1):
            (shard_dir / f"shard_{shard_index:04d}.md").write_text(
                compiler._shard_brain_body(
                    manifest=manifest,
                    shard_index=shard_index,
                    episodes=shard,
                ),
                encoding="utf-8",
            )
        return BrainContextFiles(
            brain_version=version,
            compiler_mode="asof_context",
            brain_compiler_provider="deterministic_catalog",
            brain_compiler_model=CATALOG_COMPILER_VERSION,
            brain_compiler_catalog_only=False,
            brain_file_hashes=_file_hashes_relative_to_root(self.root, brain_dir),
            shard_brain_file_hashes=_file_hashes_relative_to_root(self.root, shard_dir),
        )


def current_shard_brain_file_hashes(root: Path) -> dict[str, str]:
    shard_dir = root / "memory" / "shard_brains" / "current"
    if not shard_dir.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(shard_dir.glob("*.md"))
        if path.is_file()
    }


def _evaluation_context_population(
    root: Path,
    store: ResearchStore,
) -> _EvaluationContextPopulation | None:
    snapshot = active_memory_snapshot_manifest(root)
    if snapshot is None or not snapshot.evaluation_only:
        return None
    brain_path = root / "brain" / "current" / "brain_manifest.json"
    brain = read_json(brain_path)
    if not isinstance(brain, dict):
        raise ValueError("evaluation brain manifest is invalid")
    covered_ids_raw = brain.get("covered_episode_ids")
    if not isinstance(covered_ids_raw, list):
        raise ValueError("evaluation brain episode coverage is missing")
    covered_ids = {
        str(value) for value in covered_ids_raw if isinstance(value, str)
    }
    if (
        not covered_ids
        or brain.get("production_memory_snapshot_id") != snapshot.snapshot_id
    ):
        raise ValueError("evaluation brain is not bound to active replay memory")
    cache_key = (
        str(root.resolve()),
        snapshot.snapshot_id,
        file_sha256(brain_path),
    )
    cached = _EVALUATION_CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    replay = load_snapshot_replay_availability(root, snapshot)
    if replay is None:
        raise ValueError("evaluation context requires replay availability")
    missing_replay_ids = sorted(covered_ids - set(replay))
    if missing_replay_ids:
        raise ValueError(
            "evaluation brain coverage is missing replay availability: "
            + ", ".join(missing_replay_ids[:10])
        )
    accepted: list[ResearchEpisode] = []
    for episode in store.list_accepted():
        if episode.episode_id not in covered_ids:
            continue
        override = replay.get(episode.episode_id)
        if override is None or override.source_trade_date != episode.trade_date:
            raise ValueError(
                f"evaluation episode projection is missing: {episode.episode_id}"
            )
        accepted.append(
            episode.model_copy(
                update={"available_from": override.replay_available_from}
            )
        )
    connection = duckdb.connect(
        str(root / snapshot.database.artifact_path),
        read_only=True,
    )
    try:
        rows = connection.execute(
            """
            SELECT record_id, record_type, training_eligible, source_sha256
            FROM records
            ORDER BY record_id
            """
        ).fetchall()
    finally:
        connection.close()
    if len(rows) != snapshot.record_count:
        raise ValueError("evaluation context record set differs from the snapshot")
    population = _EvaluationContextPopulation(
        snapshot_id=snapshot.snapshot_id,
        accepted_episode_ids=tuple(sorted(covered_ids)),
        accepted_episodes=tuple(
            sorted(accepted, key=lambda item: (item.trade_date, item.episode_id))
        ),
        available_record_ids=tuple(str(row[0]) for row in rows),
        training_eligible_record_ids=tuple(
            str(row[0]) for row in rows if bool(row[2])
        ),
        counterexample_record_ids=tuple(
            str(row[0]) for row in rows if str(row[1]) == "counterexample"
        ),
        record_hashes={str(row[0]): str(row[3]) for row in rows},
    )
    _EVALUATION_CONTEXT_CACHE[cache_key] = population
    return population


def _read_accepted_episodes_for_context(
    store: ResearchStore,
    *,
    records: list[BrainRecordEnvelope],
) -> tuple[list[ResearchEpisode], list[str]]:
    try:
        return store.list_accepted(), []
    except (
        OSError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        if records:
            return [], ["accepted episode store is unreadable"]
        raise


def _read_accepted_episodes_for_current_context_check(
    store: ResearchStore,
) -> list[ResearchEpisode] | None:
    try:
        return store.list_accepted()
    except (
        OSError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        return None


def _episode_shards(
    episodes: list[ResearchEpisode],
    shard_episode_count: int = SHARD_BRAIN_EPISODE_COUNT,
) -> list[list[ResearchEpisode]]:
    shard_size = max(1, shard_episode_count)
    return [episodes[index : index + shard_size] for index in range(0, len(episodes), shard_size)]


def _file_hashes_relative_to_root(root: Path, directory: Path) -> dict[str, str]:
    if not directory.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): file_sha256(path) for path in sorted(directory.glob("*")) if path.is_file()
    }


def _current_brain_compiler_metadata(root: Path) -> BrainCompilerMetadata:
    brain_manifest = _read_json_object(root / "brain" / "current" / "brain_manifest.json")
    llm_manifest = _read_json_object(root / "brain" / "current" / "llm_compile_manifest.json")
    compiler_mode = _string_value(brain_manifest.get("build_mode"))
    if compiler_mode is None and llm_manifest:
        compiler_mode = "llm-full"
    provider = _string_value(llm_manifest.get("provider"))
    model = _string_value(llm_manifest.get("model"))
    if compiler_mode in {"catalog", "full", "incremental"}:
        provider = provider or "deterministic_catalog"
        model = model or CATALOG_COMPILER_VERSION
    return BrainCompilerMetadata(
        mode=compiler_mode,
        provider=provider,
        model=model,
        catalog_only=_bool_value(brain_manifest.get("catalog_only")),
    )


def _read_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _bool_value(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _dedupe_claims(claims: list[MemoryClaim]) -> list[MemoryClaim]:
    deduped: list[MemoryClaim] = []
    seen: set[str] = set()
    for claim in claims:
        if claim.claim_id in seen:
            continue
        seen.add(claim.claim_id)
        deduped.append(claim)
    return deduped


def _filter_retrieved_ids_available_as_of(
    retrieved_ids: list[str],
    *,
    accepted: list[ResearchEpisode],
    unavailable: list[ResearchEpisode],
) -> tuple[list[str], list[str]]:
    available_ids = {episode.episode_id for episode in accepted}
    unavailable_ids = {episode.episode_id for episode in unavailable}
    included: list[str] = []
    excluded: list[str] = []
    seen: set[str] = set()
    for episode_id in retrieved_ids:
        if episode_id in seen:
            continue
        seen.add(episode_id)
        if episode_id in available_ids:
            included.append(episode_id)
        elif episode_id in unavailable_ids or episode_id:
            excluded.append(episode_id)
    return included, excluded


def _filter_retrieved_record_ids_available_as_of(
    retrieved_ids: list[str],
    *,
    available_records: list[BrainRecordEnvelope],
    unavailable_records: list[BrainRecordEnvelope],
) -> tuple[list[str], list[str]]:
    available_ids = {record.record_id for record in available_records}
    unavailable_ids = {record.record_id for record in unavailable_records}
    included: list[str] = []
    excluded: list[str] = []
    seen: set[str] = set()
    for record_id in retrieved_ids:
        if record_id in seen:
            continue
        seen.add(record_id)
        if record_id in available_ids:
            included.append(record_id)
        elif record_id in unavailable_ids or record_id:
            excluded.append(record_id)
    return included, excluded
