"""Versioned research brain compiler."""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.brain.diff import write_rebuild_diff
from news_scalping_lab.contracts.models import (
    BrainManifest,
    ClaimStatus,
    ConfidenceLabel,
    MechanismMemory,
    MemoryClaim,
    Provenance,
    ResearchEpisode,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, canonical_json, file_sha256, stable_id, write_json
from news_scalping_lab.warehouse import WarehouseStore

BRAIN_FILES = [
    "00_world_model.md",
    "01_single_event_patterns.md",
    "02_theme_formation_patterns.md",
    "03_beneficiary_discovery.md",
    "04_leader_selection.md",
    "05_continuation_patterns.md",
    "06_failure_modes.md",
    "07_counterexamples.md",
    "08_market_memory.md",
]
EMPTY_BRAIN_CREATED_AT = datetime(1970, 1, 1, tzinfo=KST)
SHARD_BRAIN_EPISODE_COUNT = 10


class BrainCompiler:
    def __init__(self, root: Path, store: ResearchStore | None = None) -> None:
        self.root = root
        self.store = store or ResearchStore(root)
        self.current_dir = root / "brain" / "current"
        self.snapshots_dir = root / "brain" / "snapshots"
        self.diffs_dir = root / "brain" / "diffs"
        self.claims_dir = root / "memory" / "claims"
        self.mechanisms_dir = root / "memory" / "mechanisms"
        self.current_mechanisms_dir = self.mechanisms_dir / "current"
        self.shard_brains_dir = root / "memory" / "shard_brains"
        self.current_shard_brains_dir = self.shard_brains_dir / "current"
        for directory in (
            self.current_dir,
            self.snapshots_dir,
            self.diffs_dir,
            self.claims_dir,
            self.current_mechanisms_dir,
            self.current_shard_brains_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def rebuild(self, *, mode: str = "full") -> BrainManifest:
        if mode != "full":
            raise ValueError("only full rebuild is currently supported")
        previous_version = current_brain_version(self.root)
        episodes = self.store.list_accepted()
        covered_ids = [episode.episode_id for episode in episodes]
        source_hashes = self.store.accepted_hashes()
        created_at = _deterministic_rebuild_timestamp(episodes)
        version = _deterministic_brain_version(
            covered_episode_ids=covered_ids,
            source_hashes=source_hashes,
        )
        claims = _dedupe_claims(
            [
                claim
                for episode in episodes
                for claim in self._claims_from_episode(
                    episode=episode,
                    last_updated_at=_episode_content_timestamp(episode),
                )
            ]
        )
        mechanisms = _dedupe_mechanisms(
            [
                mechanism
                for episode in episodes
                for mechanism in self._mechanisms_from_episode(
                    episode=episode,
                    source_hash=source_hashes.get(episode.episode_id),
                )
            ]
        )
        manifest = BrainManifest(
            brain_version=version,
            created_at=created_at,
            accepted_episode_count=len(episodes),
            covered_episode_count=len(covered_ids),
            covered_episode_ids=covered_ids,
            claim_ids=[claim.claim_id for claim in claims],
            source_hashes=source_hashes,
            coverage_complete=len(covered_ids) == len(episodes),
        )
        self._write_current(manifest, claims)
        self._write_mechanism_memory(manifest, mechanisms)
        self._write_shard_brains(manifest, episodes)
        snapshot_dir = self.snapshots_dir / version
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        shutil.copytree(self.current_dir, snapshot_dir)
        (self.root / "brain" / "HEAD").write_text(version + "\n", encoding="utf-8")
        if previous_version != version:
            write_rebuild_diff(self.root, previous_version, version)
        WarehouseStore(self.root).rebuild_all()
        return manifest

    def update(self, *, episode_id: str) -> BrainManifest:
        # The safe incremental implementation is a full replay until drift-aware
        # merging is calibrated. The command surface stays stable.
        episode = self._resolve_update_episode(episode_id)
        accepted_ids = {episode.episode_id for episode in self.store.list_accepted()}
        if episode.episode_id not in accepted_ids:
            if episode.research_version == "evaluation-postmortem-v1":
                self.store.accept(episode.episode_id)
            else:
                raise ValueError(
                    "brain update requires an accepted episode; run "
                    f"`nslab research accept {episode.episode_id}` first"
                )
        return self.rebuild(mode="full")

    def _resolve_update_episode(self, identifier: str) -> ResearchEpisode:
        try:
            return self.store.get_episode(identifier)
        except FileNotFoundError as exc:
            try:
                trade_date = date.fromisoformat(identifier)
            except ValueError:
                raise FileNotFoundError(f"episode not found: {identifier}") from exc
        matches = [
            episode
            for episode in [*self.store.list_accepted(), *self.store.list_episodes()]
            if episode.trade_date == trade_date
            and episode.research_version == "evaluation-postmortem-v1"
        ]
        if not matches:
            raise ValueError(
                "brain update could not resolve a postmortem episode for trade date "
                f"{identifier}; run `nslab evaluate --trade-date {identifier}` first"
            )
        return max(matches, key=lambda episode: (episode.created_at, episode.episode_id))

    def _claim_from_episode(self, *, episode_id: str, last_updated_at: datetime) -> MemoryClaim:
        episode = self.store.get_episode(episode_id)
        statement = (
            "Episode contributes an abstract market-mechanism lesson; apply only with "
            "its conditions, failures, and counterexamples."
        )
        mechanism = (
            "; ".join(episode.blind_analysis.open_world_mechanisms[:3])
            or episode.blind_analysis.summary
        )
        return MemoryClaim(
            claim_id=stable_id("CL", episode.episode_id, mechanism),
            statement=statement,
            mechanism=mechanism,
            scope="episode-derived abstract mechanism",
            conditions=[
                "must be available as of the analysis date",
                "must be checked against counterexamples",
            ],
            failure_modes=["overgeneralization", "hindsight contamination", "directness error"],
            support_episode_ids=[episode.episode_id],
            contradiction_episode_ids=[],
            near_miss_episode_ids=episode.misses,
            status=ClaimStatus.TENTATIVE,
            confidence_label=ConfidenceLabel.MEDIUM,
            first_observed_at=episode.trade_date,
            last_updated_at=last_updated_at,
            available_from=episode.available_from,
            provenance=episode.provenance,
        )

    def _claims_from_episode(
        self,
        *,
        episode: ResearchEpisode,
        last_updated_at: datetime,
    ) -> list[MemoryClaim]:
        return [
            self._claim_from_episode(
                episode_id=episode.episode_id,
                last_updated_at=last_updated_at,
            ),
            *[
                _claim_with_episode_defaults(
                    claim,
                    episode=episode,
                    last_updated_at=last_updated_at,
                )
                for claim in [*episode.lessons, *episode.counterexamples]
            ],
        ]

    def _mechanisms_from_episode(
        self,
        *,
        episode: ResearchEpisode,
        source_hash: str | None,
    ) -> list[MechanismMemory]:
        mechanism_texts = episode.blind_analysis.open_world_mechanisms or [
            episode.blind_analysis.summary
        ]
        provenance = _episode_provenance(episode=episode, source_hash=source_hash)
        memories: list[MechanismMemory] = []
        for index, mechanism_text in enumerate(mechanism_texts, start=1):
            description = mechanism_text.strip()
            if not description:
                continue
            causal_chain = _causal_chain(description)
            memories.append(
                MechanismMemory(
                    mechanism_id=stable_id("MM", episode.episode_id, index, description),
                    natural_language_description=description,
                    causal_chain=causal_chain,
                    observed_variations=[episode.blind_analysis.summary],
                    successful_cases=[episode.episode_id],
                    failed_cases=episode.misses,
                    boundary_conditions=[
                        "available only on or after source episode available_from",
                        *episode.blind_analysis.initial_uncertainties,
                    ],
                    leader_selection_notes=[
                        "Preserve as an abstract mechanism; do not translate into ticker or theme maps."
                    ],
                    provenance=provenance,
                )
            )
        return memories

    def _write_current(self, manifest: BrainManifest, claims: list[MemoryClaim]) -> None:
        self.current_dir.mkdir(parents=True, exist_ok=True)
        for file_name in BRAIN_FILES:
            title = file_name.removesuffix(".md").replace("_", " ").title()
            body = self._brain_file_body(title, manifest, claims)
            (self.current_dir / file_name).write_text(body, encoding="utf-8")
        claims_path = self.current_dir / "claims.jsonl"
        claims_path.write_text(
            "".join(claim.model_dump_json() + "\n" for claim in claims),
            encoding="utf-8",
        )
        self.claims_dir.mkdir(parents=True, exist_ok=True)
        (self.claims_dir / "claims.jsonl").write_text(
            claims_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        write_json(self.current_dir / "coverage_manifest.json", self._coverage_manifest(manifest))
        write_json(self.current_dir / "brain_manifest.json", manifest.model_dump(mode="json"))

    def _write_mechanism_memory(
        self,
        manifest: BrainManifest,
        mechanisms: list[MechanismMemory],
    ) -> None:
        if self.current_mechanisms_dir.exists():
            shutil.rmtree(self.current_mechanisms_dir)
        self.current_mechanisms_dir.mkdir(parents=True, exist_ok=True)
        mechanisms_path = self.current_mechanisms_dir / "mechanisms.jsonl"
        mechanisms_path.write_text(
            "".join(memory.model_dump_json() + "\n" for memory in mechanisms),
            encoding="utf-8",
        )
        write_json(
            self.current_mechanisms_dir / "manifest.json",
            {
                "schema_version": "nslab.mechanism_memory_manifest.v1",
                "brain_version": manifest.brain_version,
                "mechanism_count": len(mechanisms),
                "covered_episode_ids": manifest.covered_episode_ids,
                "mechanism_ids": [memory.mechanism_id for memory in mechanisms],
                "mechanisms_sha256": file_sha256(mechanisms_path),
            },
        )
        versioned_dir = self.mechanisms_dir / manifest.brain_version
        if versioned_dir.exists():
            shutil.rmtree(versioned_dir)
        shutil.copytree(self.current_mechanisms_dir, versioned_dir)

    def _write_shard_brains(
        self,
        manifest: BrainManifest,
        episodes: list[ResearchEpisode],
    ) -> None:
        if self.current_shard_brains_dir.exists():
            shutil.rmtree(self.current_shard_brains_dir)
        self.current_shard_brains_dir.mkdir(parents=True, exist_ok=True)
        shard_files: list[str] = []
        for shard_index, shard in enumerate(_episode_shards(episodes), start=1):
            path = self.current_shard_brains_dir / f"shard_{shard_index:04d}.md"
            path.write_text(
                self._shard_brain_body(
                    manifest=manifest,
                    shard_index=shard_index,
                    episodes=shard,
                ),
                encoding="utf-8",
            )
            shard_files.append(path.relative_to(self.root).as_posix())
        write_json(
            self.current_shard_brains_dir / "manifest.json",
            {
                "schema_version": "nslab.shard_brain_manifest.v1",
                "brain_version": manifest.brain_version,
                "shard_episode_count": SHARD_BRAIN_EPISODE_COUNT,
                "shard_count": len(shard_files),
                "shard_files": shard_files,
                "covered_episode_ids": manifest.covered_episode_ids,
            },
        )
        versioned_dir = self.shard_brains_dir / manifest.brain_version
        if versioned_dir.exists():
            shutil.rmtree(versioned_dir)
        shutil.copytree(self.current_shard_brains_dir, versioned_dir)

    def _shard_brain_body(
        self,
        *,
        manifest: BrainManifest,
        shard_index: int,
        episodes: list[ResearchEpisode],
    ) -> str:
        lines = [
            f"# Shard Brain {shard_index:04d}",
            "",
            f"Brain version: `{manifest.brain_version}`",
            f"Episode count: {len(episodes)}",
            "",
            (
                "This shard is a compact, data-derived summary of accepted episodes. "
                "It is not a whitelist, ticker map, or keyword gate."
            ),
            "",
        ]
        if not episodes:
            lines.append("No accepted research episodes are assigned to this shard.")
            return "\n".join(lines).rstrip() + "\n"
        for episode in episodes:
            mechanisms = episode.blind_analysis.open_world_mechanisms or [
                episode.blind_analysis.summary
            ]
            counterexamples = [
                counterexample.statement for counterexample in episode.counterexamples
            ]
            miss_lines = [f"  - {miss}" for miss in episode.misses] or ["  - none recorded"]
            counterexample_lines = [f"  - {item}" for item in counterexamples] or [
                "  - none recorded"
            ]
            lines.extend(
                [
                    f"## {episode.episode_id}",
                    "",
                    f"- Trade date: {episode.trade_date.isoformat()}",
                    f"- Available from: {episode.available_from.isoformat()}",
                    f"- Blind summary: {episode.blind_analysis.summary}",
                    "- Mechanisms:",
                    *[f"  - {mechanism}" for mechanism in mechanisms],
                    "- Misses and near misses:",
                    *miss_lines,
                    "- Counterexamples:",
                    *counterexample_lines,
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    def _brain_file_body(
        self, title: str, manifest: BrainManifest, claims: list[MemoryClaim]
    ) -> str:
        lines = [
            f"# {title}",
            "",
            f"Brain version: `{manifest.brain_version}`",
            f"Accepted episodes covered: {manifest.covered_episode_count}/{manifest.accepted_episode_count}",
            "",
            "This file stores abstract mechanisms and cautions. It is not a keyword map, ticker list, or score table.",
            "",
        ]
        if not claims:
            lines.extend(
                [
                    "No accepted research episodes are available yet.",
                    (
                        "Daily analysis must still run open-world reasoning from current news "
                        "and web/company verification."
                    ),
                ]
            )
        else:
            for claim in claims:
                lines.extend(
                    [
                        f"## {claim.claim_id}",
                        "",
                        claim.statement,
                        "",
                        f"- Mechanism: {claim.mechanism}",
                        f"- Support episodes: {', '.join(claim.support_episode_ids)}",
                        f"- Available from: {claim.available_from.isoformat()}",
                        f"- Failure modes: {', '.join(claim.failure_modes)}",
                        "",
                    ]
                )
        return "\n".join(lines).rstrip() + "\n"

    def _coverage_manifest(self, manifest: BrainManifest) -> dict[str, object]:
        missing = sorted(set(self.store.accepted_hashes()) - set(manifest.covered_episode_ids))
        return {
            "brain_version": manifest.brain_version,
            "created_at": manifest.created_at.isoformat(),
            "accepted_episode_count": manifest.accepted_episode_count,
            "covered_episode_count": manifest.covered_episode_count,
            "covered_episode_ids": manifest.covered_episode_ids,
            "missing_episode_ids": missing,
            "coverage_complete": not missing and manifest.coverage_complete,
        }


def _deterministic_brain_version(*, covered_episode_ids: list[str], source_hashes: dict[str, str]) -> str:
    payload = {
        "brain_files": BRAIN_FILES,
        "covered_episode_ids": covered_episode_ids,
        "source_hashes": source_hashes,
        "schema": "nslab.brain.rebuild.v1",
    }
    return stable_id("brain", canonical_json(payload), length=10)


def _episode_shards(episodes: list[ResearchEpisode]) -> list[list[ResearchEpisode]]:
    return [
        episodes[index : index + SHARD_BRAIN_EPISODE_COUNT]
        for index in range(0, len(episodes), SHARD_BRAIN_EPISODE_COUNT)
    ]


def _claim_with_episode_defaults(
    claim: MemoryClaim,
    *,
    episode: ResearchEpisode,
    last_updated_at: datetime,
) -> MemoryClaim:
    return claim.model_copy(
        update={
            "support_episode_ids": claim.support_episode_ids or [episode.episode_id],
            "first_observed_at": claim.first_observed_at or episode.trade_date,
            "last_updated_at": claim.last_updated_at or last_updated_at,
            "provenance": claim.provenance or episode.provenance,
        }
    )


def _dedupe_claims(claims: list[MemoryClaim]) -> list[MemoryClaim]:
    deduped: list[MemoryClaim] = []
    seen: set[str] = set()
    for claim in claims:
        if claim.claim_id in seen:
            continue
        seen.add(claim.claim_id)
        deduped.append(claim)
    return deduped


def _dedupe_mechanisms(mechanisms: list[MechanismMemory]) -> list[MechanismMemory]:
    deduped: list[MechanismMemory] = []
    seen: set[str] = set()
    for mechanism in mechanisms:
        if mechanism.mechanism_id in seen:
            continue
        seen.add(mechanism.mechanism_id)
        deduped.append(mechanism)
    return deduped


def _causal_chain(description: str) -> list[str]:
    parts = [part.strip(" -") for part in description.split("->")]
    return [part for part in parts if part] or [description]


def _episode_provenance(
    *,
    episode: ResearchEpisode,
    source_hash: str | None,
) -> list[Provenance]:
    if episode.provenance:
        return episode.provenance
    return [
        Provenance(
            source_id=stable_id("SRC", "accepted_episode", episode.episode_id),
            source_type="accepted_research_episode",
            uri=f"research/accepted/{episode.episode_id}.json",
            content_sha256=source_hash,
            excerpt=episode.blind_analysis.summary,
            observed_at=_ensure_timezone(episode.created_at),
        )
    ]


def _deterministic_rebuild_timestamp(episodes: list[ResearchEpisode]) -> datetime:
    if not episodes:
        return EMPTY_BRAIN_CREATED_AT
    return max(_episode_content_timestamp(episode) for episode in episodes)


def _episode_content_timestamp(episode: ResearchEpisode) -> datetime:
    timestamps = [
        _ensure_timezone(episode.created_at),
        _ensure_timezone(episode.cutoff_at),
        _ensure_timezone(episode.available_from),
    ]
    if episode.postmortem is not None:
        for provenance in episode.postmortem.provenance:
            observed_at = _with_timezone(provenance.observed_at)
            if observed_at is not None:
                timestamps.append(observed_at)
    return max(timestamps)


def _with_timezone(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _ensure_timezone(value)


def _ensure_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value


def current_brain_version(root: Path) -> str | None:
    head = root / "brain" / "HEAD"
    if not head.exists():
        return None
    value = head.read_text(encoding="utf-8").strip()
    return value or None


def current_brain_file_hashes(root: Path) -> dict[str, str]:
    current_dir = root / "brain" / "current"
    if not current_dir.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(current_dir.glob("*"))
        if path.is_file()
    }
