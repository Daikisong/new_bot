"""Context manifest assembly."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.brain.compiler import (
    BRAIN_FILES,
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
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    is_available_as_of,
    stable_id,
    write_json,
)


@dataclass(frozen=True)
class BrainContextFiles:
    brain_version: str | None
    brain_file_hashes: dict[str, str]
    shard_brain_file_hashes: dict[str, str]


class ContextAssembler:
    def __init__(self, root: Path, store: ResearchStore | None = None) -> None:
        self.root = root
        self.store = store or ResearchStore(root)

    def assemble(
        self,
        *,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        run_seed: str,
        retrieved_episode_ids: list[str] | None = None,
        web_queries: list[str] | None = None,
    ) -> ContextManifest:
        mode = normalize_analysis_mode(mode)
        retrieved_ids = retrieved_episode_ids or []
        web_query_list = web_queries or []
        accepted = [
            episode
            for episode in self.store.list_accepted()
            if is_available_as_of(episode.available_from, cutoff_at)
        ]
        accepted_ids = [episode.episode_id for episode in accepted]
        accepted_hashes = self._accepted_hashes_for(accepted_ids)
        run_id = stable_id(
            "RUN",
            canonical_json(
                {
                    "accepted_episode_hashes": accepted_hashes,
                    "accepted_episode_ids": accepted_ids,
                    "cutoff_at": cutoff_at.isoformat(),
                    "mode": mode,
                    "retrieved_episode_ids": retrieved_ids,
                    "run_seed": run_seed,
                    "trade_date": trade_date.isoformat(),
                    "web_queries": web_query_list,
                }
            ),
        )
        counterexample_ids = [
            episode.episode_id for episode in accepted if episode.counterexamples
        ]
        swept_ids = accepted_ids if mode in {"exhaustive", "brain"} else []
        errors: list[str] = []
        if mode == "exhaustive" and len(swept_ids) != len(accepted_ids):
            errors.append("exhaustive coverage mismatch")
        brain_context = self._brain_context_files(
            run_id=run_id,
            cutoff_at=cutoff_at,
            accepted=accepted,
        )
        manifest = ContextManifest(
            run_id=run_id,
            mode=mode,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            brain_version=brain_context.brain_version,
            brain_files=list(brain_context.brain_file_hashes.keys()),
            brain_file_hashes=brain_context.brain_file_hashes,
            shard_brain_files=list(brain_context.shard_brain_file_hashes.keys()),
            shard_brain_file_hashes=brain_context.shard_brain_file_hashes,
            accepted_episode_count=len(accepted_ids),
            swept_episode_count=len(swept_ids),
            swept_episode_ids=swept_ids,
            retrieved_episode_ids=retrieved_ids,
            counterexample_episode_ids=counterexample_ids,
            token_counts={},
            truncations=[],
            web_queries=web_query_list,
            web_sources=[],
            price_snapshot=PriceSnapshot(
                source_name="blind-guarded",
                allowed_through=date.fromordinal(trade_date.toordinal() - 1),
            ),
            llm_model_config={"provider": "mock-compatible", "mode": mode},
            prompt_hashes={},
            errors=errors,
        )
        if mode == "exhaustive" and manifest.swept_episode_count != manifest.accepted_episode_count:
            manifest.errors.append("swept_episode_count must equal accepted_episode_count")
        return manifest

    def _accepted_hashes_for(self, accepted_ids: list[str]) -> dict[str, str]:
        hashes = self.store.accepted_hashes()
        return {
            episode_id: hashes[episode_id]
            for episode_id in accepted_ids
            if episode_id in hashes
        }

    def _brain_context_files(
        self,
        *,
        run_id: str,
        cutoff_at: datetime,
        accepted: list[ResearchEpisode],
    ) -> BrainContextFiles:
        current_hashes = current_brain_file_hashes(self.root)
        current_shard_hashes = current_shard_brain_file_hashes(self.root)
        accepted_ids = [episode.episode_id for episode in accepted]
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
        if accepted_ids and not shard_brain_file_hashes:
            return False
        future_episode_ids = [
            episode.episode_id
            for episode in self.store.list_accepted()
            if not is_available_as_of(episode.available_from, cutoff_at)
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
        return BrainContextFiles(
            brain_version=brain_version,
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
            length=10,
        )
        compiler = BrainCompiler(self.root, store=self.store)
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
                compiler._brain_file_body(title, manifest, claims),
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
                "accepted_episode_count": manifest.accepted_episode_count,
                "covered_episode_count": manifest.covered_episode_count,
                "covered_episode_ids": manifest.covered_episode_ids,
                "missing_episode_ids": [],
                "coverage_complete": True,
                "as_of_cutoff_at": cutoff_at.isoformat(),
            },
        )
        write_json(brain_dir / "brain_manifest.json", manifest.model_dump(mode="json"))

        for shard_index, shard in enumerate(_episode_shards(accepted), start=1):
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


def _episode_shards(episodes: list[ResearchEpisode]) -> list[list[ResearchEpisode]]:
    return [
        episodes[index : index + SHARD_BRAIN_EPISODE_COUNT]
        for index in range(0, len(episodes), SHARD_BRAIN_EPISODE_COUNT)
    ]


def _file_hashes_relative_to_root(root: Path, directory: Path) -> dict[str, str]:
    if not directory.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(directory.glob("*"))
        if path.is_file()
    }


def _dedupe_claims(claims: list[MemoryClaim]) -> list[MemoryClaim]:
    deduped: list[MemoryClaim] = []
    seen: set[str] = set()
    for claim in claims:
        if claim.claim_id in seen:
            continue
        seen.add(claim.claim_id)
        deduped.append(claim)
    return deduped
