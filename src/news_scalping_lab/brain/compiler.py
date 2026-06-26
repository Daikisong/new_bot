"""Versioned research brain compiler."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.brain.diff import write_rebuild_diff
from news_scalping_lab.config import load_settings
from news_scalping_lab.contracts.models import (
    BrainManifest,
    ClaimStatus,
    ConfidenceLabel,
    MechanismMemory,
    MemoryClaim,
    Provenance,
    ResearchEpisode,
)
from news_scalping_lab.diagnostic_reports import write_diagnostic_report
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    stable_id,
    write_json,
)
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
CATALOG_COMPILER_VERSION = "nslab.brain.catalog.compiler.v3"
LLM_FULL_COMPILER_VERSION = "nslab.brain.llm_full.compiler.v2"
LLM_FULL_RECORD_SHARD_SIZE = 50


@dataclass(frozen=True)
class LLMFullCompileResult:
    category_outputs: dict[str, str]
    manifest: dict[str, Any]


class BrainCompiler:
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
        if mode == "llm-full":
            return self._rebuild_llm_full()
        if mode not in {"full", "catalog"}:
            raise ValueError("only full rebuild is currently supported")
        previous_version = current_brain_version(self.root)
        episodes = self.store.list_accepted()
        covered_ids = [episode.episode_id for episode in episodes]
        source_hashes = self.store.accepted_hashes()
        created_at = _deterministic_rebuild_timestamp(episodes)
        version = _deterministic_brain_version(
            covered_episode_ids=covered_ids,
            source_hashes=source_hashes,
            shard_episode_count=self.shard_episode_count,
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
            build_mode="full" if mode == "full" else "catalog",
            last_full_rebuild_at=created_at,
            updated_episode_id=None,
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
        self._write_immutable_snapshot(version)
        (self.root / "brain" / "HEAD").write_text(version + "\n", encoding="utf-8")
        if previous_version != version:
            write_rebuild_diff(self.root, previous_version, version)
        LocalRetrievalStore(self.root).rebuild_index()
        WarehouseStore(self.root).rebuild_all()
        return manifest

    def _rebuild_llm_full(self) -> BrainManifest:
        settings = load_settings(self.root)
        if settings.llm_provider.strip().lower() == "mock":
            raise ValueError("llm-full brain rebuild requires a real LLM provider")
        if settings.llm.provider.strip().lower() == "mock":
            raise ValueError("llm-full brain rebuild requires a non-mock model profile")
        records = BrainRecordStore(self.root).list_records()
        if not records:
            raise ValueError("llm-full brain rebuild requires normalized brain records")
        provider = create_llm_provider(settings)
        previous_version = current_brain_version(self.root)
        accepted_episodes = self.store.list_accepted()
        covered_ids = sorted(
            {
                *[episode.episode_id for episode in accepted_episodes],
                *[record.episode_id for record in records],
            }
        )
        source_hashes = {
            **self.store.accepted_hashes(),
            **{
                f"record:{record.record_id}": record.normalized_payload_sha256
                for record in records
            },
        }
        created_at = max(record.available_from for record in records)
        version = stable_id(
            "brain",
            canonical_json(
                {
                    "schema": "nslab.brain.llm_full.v1",
                    "covered_episode_ids": covered_ids,
                    "source_hashes": source_hashes,
                    "model": settings.llm.model,
                    "provider": settings.llm_provider,
                }
            ),
            length=10,
        )
        claims = self._claims_from_records(records)
        manifest = BrainManifest(
            brain_version=version,
            created_at=created_at,
            build_mode="llm-full",
            last_full_rebuild_at=created_at,
            updated_episode_id=None,
            accepted_episode_count=len(accepted_episodes),
            covered_episode_count=len(covered_ids),
            covered_episode_ids=covered_ids,
            claim_ids=[claim.claim_id for claim in claims],
            source_hashes=source_hashes,
            coverage_complete=True,
        )
        llm_compile = asyncio.run(
            _compile_llm_category_outputs(
                root=self.root,
                provider=provider,
                records=records,
                brain_version=version,
                provider_name=settings.llm_provider,
                model=settings.llm.model,
            )
        )
        self._write_current(
            manifest,
            claims,
            category_outputs=llm_compile.category_outputs,
            llm_compile_metadata=llm_compile.manifest,
        )
        self._write_mechanism_memory(manifest, [])
        self._write_shard_brains(manifest, accepted_episodes)
        self._write_immutable_snapshot(version)
        (self.root / "brain" / "HEAD").write_text(version + "\n", encoding="utf-8")
        if previous_version != version:
            write_rebuild_diff(self.root, previous_version, version)
        LocalRetrievalStore(self.root).rebuild_index()
        WarehouseStore(self.root).rebuild_all()
        return manifest

    def update(self, *, episode_id: str, mode: str = "full") -> BrainManifest:
        if mode == "llm-full":
            self._resolve_update_episode(episode_id)
            return self.rebuild(mode="llm-full")
        if mode not in {"full", "catalog"}:
            raise ValueError("only full, catalog, and llm-full update modes are supported")
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
        episodes = self.store.list_accepted()
        source_hashes = self.store.accepted_hashes()
        try:
            current_manifest = self._read_current_manifest()
        except ValueError:
            current_manifest = None
        if (
            current_manifest is None
            or self._current_shard_episode_count() != self.shard_episode_count
            or not _can_incrementally_update(
                current_manifest=current_manifest,
                episode_id=episode.episode_id,
                episodes=episodes,
                source_hashes=source_hashes,
            )
        ):
            return self.rebuild(mode="full")

        covered_ids = [accepted_episode.episode_id for accepted_episode in episodes]
        if (
            current_manifest.covered_episode_ids == covered_ids
            and current_manifest.source_hashes == source_hashes
        ):
            LocalRetrievalStore(self.root).rebuild_index()
            WarehouseStore(self.root).rebuild_all()
            return current_manifest

        previous_version = current_manifest.brain_version
        created_at = _deterministic_rebuild_timestamp(episodes)
        version = _deterministic_brain_version(
            covered_episode_ids=covered_ids,
            source_hashes=source_hashes,
            shard_episode_count=self.shard_episode_count,
        )
        try:
            claims = _sort_claims_by_episode_order(
                _dedupe_claims(
                    [
                        *self._read_current_claims(),
                        *self._claims_from_episode(
                            episode=episode,
                            last_updated_at=_episode_content_timestamp(episode),
                        ),
                    ]
                ),
                covered_ids,
            )
            mechanisms = _sort_mechanisms_by_episode_order(
                _dedupe_mechanisms(
                    [
                        *self._read_current_mechanisms(),
                        *self._mechanisms_from_episode(
                            episode=episode,
                            source_hash=source_hashes.get(episode.episode_id),
                        ),
                    ]
                ),
                covered_ids,
            )
        except ValueError:
            return self.rebuild(mode="full")
        manifest = BrainManifest(
            brain_version=version,
            created_at=created_at,
            build_mode="incremental",
            last_full_rebuild_at=(
                current_manifest.last_full_rebuild_at or current_manifest.created_at
            ),
            updated_episode_id=episode.episode_id,
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
        self._write_immutable_snapshot(version)
        (self.root / "brain" / "HEAD").write_text(version + "\n", encoding="utf-8")
        if previous_version != version:
            write_rebuild_diff(self.root, previous_version, version)
        LocalRetrievalStore(self.root).rebuild_index()
        WarehouseStore(self.root).rebuild_all()
        return manifest

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

    def _claims_from_records(self, records: list[BrainRecordEnvelope]) -> list[MemoryClaim]:
        claims: list[MemoryClaim] = []
        for record in records:
            if not record.training_eligible:
                continue
            statement = _record_claim_statement(record)
            claims.append(
                MemoryClaim(
                    claim_id=stable_id("CL", record.record_id, record.normalized_payload_sha256),
                    statement=statement,
                    mechanism=str(record.training_target or record.record_type),
                    scope=f"record-derived {record.record_type}",
                    conditions=[
                        "use only when the record is available as of the analysis cutoff",
                        "check counterexamples and negative controls before applying",
                    ],
                    failure_modes=["overgeneralization", "hindsight contamination"],
                    support_episode_ids=[record.episode_id],
                    contradiction_episode_ids=[],
                    near_miss_episode_ids=[],
                    status=ClaimStatus.TENTATIVE,
                    confidence_label=ConfidenceLabel.LOW,
                    first_observed_at=record.trade_date,
                    last_updated_at=record.available_from,
                    available_from=record.available_from,
                    provenance=[
                        Provenance(
                            source_id=record.record_id,
                            source_type="brain_record",
                            uri=f"memory/records/{record.episode_id}.jsonl#{record.record_id}",
                            content_sha256=record.normalized_payload_sha256,
                            observed_at=record.available_from,
                        )
                    ],
                )
            )
        return _dedupe_claims(claims)

    def _write_current(
        self,
        manifest: BrainManifest,
        claims: list[MemoryClaim],
        *,
        category_outputs: dict[str, str] | None = None,
        llm_compile_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.current_dir.mkdir(parents=True, exist_ok=True)
        for file_name in BRAIN_FILES:
            title = file_name.removesuffix(".md").replace("_", " ").title()
            body = (
                category_outputs[file_name]
                if category_outputs is not None and file_name in category_outputs
                else self._brain_file_body(title, manifest, claims, file_name=file_name)
            )
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
        record_coverage = self._record_coverage_manifest(manifest)
        write_json(self.current_dir / "record_coverage_manifest.json", record_coverage)
        llm_manifest_path = self.current_dir / "llm_compile_manifest.json"
        if llm_compile_metadata is not None:
            write_json(llm_manifest_path, llm_compile_metadata)
        elif llm_manifest_path.exists():
            llm_manifest_path.unlink()
        write_json(self.current_dir / "brain_manifest.json", manifest.model_dump(mode="json"))
        write_diagnostic_report(
            self.root,
            "brain_compile_report",
            {
                "brain_version": manifest.brain_version,
                "compiler_mode": manifest.build_mode,
                "accepted_episode_count": manifest.accepted_episode_count,
                "covered_episode_count": manifest.covered_episode_count,
                "claim_count": len(claims),
                "category_file_count": len(BRAIN_FILES),
                "category_files": BRAIN_FILES,
                "llm_compile": llm_compile_metadata,
            },
        )
        write_diagnostic_report(self.root, "record_coverage_report", record_coverage)

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
        _copy_immutable_directory(
            source_dir=self.current_mechanisms_dir,
            target_dir=versioned_dir,
            label="mechanism memory",
        )

    def _write_shard_brains(
        self,
        manifest: BrainManifest,
        episodes: list[ResearchEpisode],
    ) -> None:
        if self.current_shard_brains_dir.exists():
            shutil.rmtree(self.current_shard_brains_dir)
        self.current_shard_brains_dir.mkdir(parents=True, exist_ok=True)
        shard_files: list[str] = []
        for shard_index, shard in enumerate(
            _episode_shards(episodes, self.shard_episode_count), start=1
        ):
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
                "shard_episode_count": self.shard_episode_count,
                "shard_count": len(shard_files),
                "shard_files": shard_files,
                "covered_episode_ids": manifest.covered_episode_ids,
            },
        )
        versioned_dir = self.shard_brains_dir / manifest.brain_version
        _copy_immutable_directory(
            source_dir=self.current_shard_brains_dir,
            target_dir=versioned_dir,
            label="shard brain memory",
        )

    def _read_current_manifest(self) -> BrainManifest | None:
        path = self.current_dir / "brain_manifest.json"
        if not path.exists():
            return None
        return BrainManifest.model_validate(read_json(path))

    def _read_current_claims(self) -> list[MemoryClaim]:
        return _read_claim_jsonl(self.current_dir / "claims.jsonl")

    def _read_current_mechanisms(self) -> list[MechanismMemory]:
        return _read_mechanism_jsonl(self.current_mechanisms_dir / "mechanisms.jsonl")

    def _write_immutable_snapshot(self, version: str) -> None:
        _copy_immutable_directory(
            source_dir=self.current_dir,
            target_dir=self.snapshots_dir / version,
            label="brain snapshot",
        )

    def _current_shard_episode_count(self) -> int | None:
        path = self.current_shard_brains_dir / "manifest.json"
        if not path.exists():
            return None
        payload = read_json(path)
        value = payload.get("shard_episode_count") if isinstance(payload, dict) else None
        return value if isinstance(value, int) else None

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
        self,
        title: str,
        manifest: BrainManifest,
        claims: list[MemoryClaim],
        *,
        file_name: str,
    ) -> str:
        category = _brain_category(file_name)
        category_claims = _claims_for_category(claims, category)
        lines = [
            f"# {title}",
            "",
            f"Brain version: `{manifest.brain_version}`",
            f"Accepted episodes covered: {manifest.covered_episode_count}/{manifest.accepted_episode_count}",
            f"Build mode: `{manifest.build_mode}`",
            f"Category: `{category}`",
            "",
            "This file stores abstract mechanisms and cautions. It is not a keyword map, ticker list, or score table.",
            f"Evidence focus: {_category_focus(category)}",
            "",
        ]
        if not category_claims:
            lines.extend(
                [
                    f"No category-specific claims are available yet for `{category}`.",
                    (
                        "Daily analysis must still run open-world reasoning from current news "
                        f"and web/company verification before applying `{category}` patterns."
                    ),
                ]
            )
        else:
            for claim in category_claims:
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

    def _record_coverage_manifest(self, manifest: BrainManifest) -> dict[str, object]:
        records = BrainRecordStore(self.root).list_records()
        record_counts_by_type = Counter(record.record_type for record in records)
        record_counts_by_phase = Counter(record.evidence_phase for record in records)
        record_counts_by_target = Counter(
            record.training_target or "UNKNOWN" for record in records
        )
        record_ids = [record.record_id for record in records]
        return {
            "schema_version": "nslab.record_coverage_manifest.v1",
            "brain_version": manifest.brain_version,
            "build_mode": manifest.build_mode,
            "accepted_episode_count": manifest.accepted_episode_count,
            "accepted_record_count": len(records),
            "available_record_count": len(records),
            "training_eligible_available_record_count": sum(
                1 for record in records if record.training_eligible
            ),
            "compiled_record_count": len(records),
            "swept_record_count": len(records),
            "swept_record_ids": record_ids,
            "unswept_record_ids": [],
            "record_counts_by_type": dict(sorted(record_counts_by_type.items())),
            "record_counts_by_evidence_phase": dict(sorted(record_counts_by_phase.items())),
            "record_counts_by_training_target": dict(sorted(record_counts_by_target.items())),
            "ineligible_record_count": sum(
                1 for record in records if not record.training_eligible
            ),
            "audit_only_record_count": sum(
                1 for record in records if record.evidence_phase == "AUDIT"
            ),
            "coverage_complete": True,
        }

    def _coverage_manifest(self, manifest: BrainManifest) -> dict[str, object]:
        missing = sorted(set(self.store.accepted_hashes()) - set(manifest.covered_episode_ids))
        return {
            "brain_version": manifest.brain_version,
            "created_at": manifest.created_at.isoformat(),
            "build_mode": manifest.build_mode,
            "last_full_rebuild_at": (
                manifest.last_full_rebuild_at.isoformat()
                if manifest.last_full_rebuild_at is not None
                else None
            ),
            "updated_episode_id": manifest.updated_episode_id,
            "accepted_episode_count": manifest.accepted_episode_count,
            "covered_episode_count": manifest.covered_episode_count,
            "covered_episode_ids": manifest.covered_episode_ids,
            "missing_episode_ids": missing,
            "coverage_complete": not missing and manifest.coverage_complete,
        }


def _deterministic_brain_version(
    *,
    covered_episode_ids: list[str],
    source_hashes: dict[str, str],
    shard_episode_count: int = SHARD_BRAIN_EPISODE_COUNT,
) -> str:
    payload = {
        "brain_files": BRAIN_FILES,
        "compiler_version": CATALOG_COMPILER_VERSION,
        "covered_episode_ids": covered_episode_ids,
        "shard_episode_count": max(1, shard_episode_count),
        "source_hashes": source_hashes,
        "schema": "nslab.brain.rebuild.v1",
    }
    return stable_id("brain", canonical_json(payload), length=10)


def expected_brain_version(
    *,
    covered_episode_ids: list[str],
    source_hashes: dict[str, str],
    shard_episode_count: int = SHARD_BRAIN_EPISODE_COUNT,
) -> str:
    return _deterministic_brain_version(
        covered_episode_ids=covered_episode_ids,
        source_hashes=source_hashes,
        shard_episode_count=shard_episode_count,
    )


def _episode_shards(
    episodes: list[ResearchEpisode],
    shard_episode_count: int = SHARD_BRAIN_EPISODE_COUNT,
) -> list[list[ResearchEpisode]]:
    shard_size = max(1, shard_episode_count)
    return [
        episodes[index : index + shard_size]
        for index in range(0, len(episodes), shard_size)
    ]


def _can_incrementally_update(
    *,
    current_manifest: BrainManifest,
    episode_id: str,
    episodes: list[ResearchEpisode],
    source_hashes: dict[str, str],
) -> bool:
    if not current_manifest.coverage_complete:
        return False
    covered_ids = [episode.episode_id for episode in episodes]
    if (
        current_manifest.covered_episode_ids == covered_ids
        and current_manifest.source_hashes == source_hashes
    ):
        return True
    previous_ids = [current_id for current_id in covered_ids if current_id != episode_id]
    if current_manifest.covered_episode_ids != previous_ids:
        return False
    previous_hashes = {
        previous_id: source_hashes[previous_id]
        for previous_id in previous_ids
        if previous_id in source_hashes
    }
    return len(previous_hashes) == len(previous_ids) and current_manifest.source_hashes == previous_hashes


def _read_claim_jsonl(path: Path) -> list[MemoryClaim]:
    if not path.exists():
        raise ValueError(f"missing current claims file: {path}")
    claims: list[MemoryClaim] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            claims.append(MemoryClaim.model_validate_json(line))
    return claims


def _read_mechanism_jsonl(path: Path) -> list[MechanismMemory]:
    if not path.exists():
        raise ValueError(f"missing current mechanisms file: {path}")
    mechanisms: list[MechanismMemory] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            mechanisms.append(MechanismMemory.model_validate_json(line))
    return mechanisms


def _sort_claims_by_episode_order(
    claims: list[MemoryClaim],
    covered_episode_ids: list[str],
) -> list[MemoryClaim]:
    order = _episode_order(covered_episode_ids)
    return sorted(claims, key=lambda claim: (_claim_episode_order(claim, order), claim.claim_id))


def _sort_mechanisms_by_episode_order(
    mechanisms: list[MechanismMemory],
    covered_episode_ids: list[str],
) -> list[MechanismMemory]:
    order = _episode_order(covered_episode_ids)
    return sorted(
        mechanisms,
        key=lambda mechanism: (_mechanism_episode_order(mechanism, order), mechanism.mechanism_id),
    )


def _episode_order(covered_episode_ids: list[str]) -> dict[str, int]:
    return {episode_id: index for index, episode_id in enumerate(covered_episode_ids)}


def _claim_episode_order(claim: MemoryClaim, order: dict[str, int]) -> int:
    episode_ids = [
        *claim.support_episode_ids,
        *claim.contradiction_episode_ids,
        *claim.near_miss_episode_ids,
    ]
    return min((order[episode_id] for episode_id in episode_ids if episode_id in order), default=len(order))


def _mechanism_episode_order(mechanism: MechanismMemory, order: dict[str, int]) -> int:
    episode_ids = [*mechanism.successful_cases, *mechanism.failed_cases]
    return min((order[episode_id] for episode_id in episode_ids if episode_id in order), default=len(order))


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


def _directory_file_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): file_sha256(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _copy_immutable_directory(*, source_dir: Path, target_dir: Path, label: str) -> None:
    if target_dir.exists():
        if _directory_file_hashes(target_dir) != _directory_file_hashes(source_dir):
            raise ValueError(
                f"immutable {label} already exists with different content: "
                f"{target_dir.name}"
            )
        return
    shutil.copytree(source_dir, target_dir)


def _brain_category(file_name: str) -> str:
    if "single_event" in file_name:
        return "single_event"
    if "theme_formation" in file_name:
        return "theme_formation"
    if "beneficiary" in file_name:
        return "beneficiary_discovery"
    if "leader" in file_name:
        return "leader_selection"
    if "continuation" in file_name:
        return "continuation"
    if "failure" in file_name:
        return "failure_modes"
    if "counterexamples" in file_name:
        return "counterexamples"
    if "market_memory" in file_name:
        return "market_memory"
    return "world_model"


def _category_focus(category: str) -> str:
    focuses = {
        "world_model": "global mechanisms, temporal boundaries, and cross-category cautions",
        "single_event": "direct event response patterns and issuer-day evidence",
        "theme_formation": "multi-name narrative formation and breadth conditions",
        "beneficiary_discovery": "indirect beneficiary discovery paths and verification gaps",
        "leader_selection": "leader preference evidence, ranking misses, and near ties",
        "continuation": "continuation setups that remain blind-safe without D-day prices",
        "failure_modes": "candidate generation, ranking, disposition, and entity-resolution errors",
        "counterexamples": "negative controls, near misses, and disconfirming boundaries",
        "market_memory": "reusable memory claims, mechanisms, and company-memory deltas",
    }
    return focuses.get(category, "category-specific evidence and boundaries")


def _claims_for_category(
    claims: list[MemoryClaim],
    category: str,
) -> list[MemoryClaim]:
    if category == "world_model":
        return claims[:20]
    predicates = {
        "single_event": ("direct", "issuer", "single", "event"),
        "theme_formation": ("theme", "formation"),
        "beneficiary_discovery": ("beneficiary", "discovery"),
        "leader_selection": ("leader", "preference", "ranking"),
        "continuation": ("continuation",),
        "failure_modes": ("error", "failure", "miss"),
        "counterexamples": ("counterexample", "negative"),
        "market_memory": ("memory", "market"),
    }
    needles = predicates.get(category, ())
    selected = [
        claim
        for claim in claims
        if any(
            needle in f"{claim.statement} {claim.mechanism} {claim.scope}".lower()
            for needle in needles
        )
    ]
    return selected


def _record_claim_statement(record: BrainRecordEnvelope) -> str:
    response_class = record.payload.get("response_class")
    target = record.training_target or record.record_type
    if isinstance(response_class, str) and response_class:
        return (
            f"{record.record_type} supports studying {target} with "
            f"observed response_class={response_class}."
        )
    return f"{record.record_type} supports studying {target} with preserved provenance."


async def _compile_llm_category_outputs(
    *,
    root: Path,
    provider: LLMProvider,
    records: list[BrainRecordEnvelope],
    brain_version: str,
    provider_name: str,
    model: str,
) -> LLMFullCompileResult:
    cache_dir = root / "brain" / "llm_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    sorted_records = sorted(records, key=lambda record: record.record_id)
    shard_summaries: list[dict[str, Any]] = []
    for shard_index, shard in enumerate(
        _record_shards(sorted_records, LLM_FULL_RECORD_SHARD_SIZE),
        start=1,
    ):
        prompt = _brain_record_shard_prompt(
            shard_index=shard_index,
            records=shard,
            brain_version=brain_version,
            provider_name=provider_name,
            model=model,
        )
        output, cache_key, _ = await _cached_generate_text(
            provider=provider,
            cache_dir=cache_dir,
            purpose=f"brain_compile:shard:{shard_index:04d}",
            prompt=prompt,
            record_ids=[record.record_id for record in shard],
            record_hashes={
                record.record_id: record.normalized_payload_sha256
                for record in shard
            },
            provider_name=provider_name,
            model=model,
        )
        shard_summaries.append(
            {
                "shard_index": shard_index,
                "cache_key": cache_key,
                "record_ids": [record.record_id for record in shard],
                "record_count": len(shard),
                "summary": output,
            }
        )
    outputs: dict[str, str] = {}
    categories: list[dict[str, Any]] = []
    for file_name in BRAIN_FILES:
        category = _brain_category(file_name)
        category_records = _records_for_category(records, category)
        prompt = _brain_category_prompt(
            category=category,
            records=category_records,
            shard_summaries=shard_summaries,
            brain_version=brain_version,
            provider_name=provider_name,
            model=model,
        )
        synthesis, synthesis_cache_key, _ = await _cached_generate_text(
            provider=provider,
            cache_dir=cache_dir,
            purpose=f"brain_compile:synthesis:{category}",
            prompt=prompt,
            record_ids=[record.record_id for record in category_records],
            record_hashes={
                record.record_id: record.normalized_payload_sha256
                for record in category_records
            },
            provider_name=provider_name,
            model=model,
        )
        review_prompt = _brain_category_review_prompt(
            category=category,
            synthesis=synthesis,
            records=category_records,
            shard_summaries=shard_summaries,
            brain_version=brain_version,
            provider_name=provider_name,
            model=model,
        )
        review, review_cache_key, _ = await _cached_generate_text(
            provider=provider,
            cache_dir=cache_dir,
            purpose=f"brain_compile:review:{category}",
            prompt=review_prompt,
            record_ids=[record.record_id for record in category_records],
            record_hashes={
                record.record_id: record.normalized_payload_sha256
                for record in category_records
            },
            provider_name=provider_name,
            model=model,
        )
        outputs[file_name] = (
            f"# {file_name.removesuffix('.md').replace('_', ' ').title()}\n\n"
            f"Brain version: `{brain_version}`\n"
            f"Build mode: `llm-full`\n"
            f"Provider: `{provider_name}`\n"
            f"Model: `{model}`\n"
            f"Category: `{category}`\n"
            f"Source record count: {len(category_records)}\n\n"
            "## Category Synthesis\n\n"
            f"{synthesis.strip()}\n\n"
            "## Contradiction And Boundary Review\n\n"
            f"{review.strip()}\n\n"
            "## Supporting Records\n\n"
            + "\n".join(f"- `{record.record_id}` ({record.record_type})" for record in category_records[:200])
            + "\n"
        )
        categories.append(
            {
                "category": category,
                "file_name": file_name,
                "source_record_count": len(category_records),
                "source_record_ids": [record.record_id for record in category_records],
                "synthesis_cache_key": synthesis_cache_key,
                "review_cache_key": review_cache_key,
            }
        )
    manifest = {
        "schema_version": "nslab.llm_full_brain_compile_manifest.v1",
        "compiler_version": LLM_FULL_COMPILER_VERSION,
        "brain_version": brain_version,
        "provider": provider_name,
        "model": model,
        "source_record_count": len(sorted_records),
        "record_shard_size": LLM_FULL_RECORD_SHARD_SIZE,
        "record_shard_count": len(shard_summaries),
        "record_shards": [
            {
                key: value
                for key, value in shard.items()
                if key != "summary"
                and key != "cache_hit"
            }
            for shard in shard_summaries
        ],
        "category_count": len(categories),
        "categories": categories,
    }
    return LLMFullCompileResult(category_outputs=outputs, manifest=manifest)


def _records_for_category(
    records: list[BrainRecordEnvelope],
    category: str,
) -> list[BrainRecordEnvelope]:
    mapping = {
        "single_event": {"supervised_direct_event_case", "supervised_issuer_day_case"},
        "theme_formation": {"supervised_theme_formation_case"},
        "beneficiary_discovery": {"beneficiary_discovery_case"},
        "leader_selection": {"blind_leader_preference_pair"},
        "failure_modes": {
            "candidate_generation_error_case",
            "candidate_ranking_error_case",
            "row_disposition_error_case",
            "entity_resolution_error_case",
        },
        "counterexamples": {"counterexample"},
        "market_memory": {"memory_claim", "mechanism_memory", "company_memory_delta"},
    }
    if category == "world_model":
        return records
    allowed = mapping.get(category, set())
    selected = [record for record in records if record.record_type in allowed]
    return selected or records[: min(20, len(records))]


def _record_shards(
    records: list[BrainRecordEnvelope],
    shard_size: int,
) -> list[list[BrainRecordEnvelope]]:
    if not records:
        return []
    size = max(1, shard_size)
    return [records[index : index + size] for index in range(0, len(records), size)]


async def _cached_generate_text(
    *,
    provider: LLMProvider,
    cache_dir: Path,
    purpose: str,
    prompt: str,
    record_ids: list[str],
    record_hashes: dict[str, str],
    provider_name: str,
    model: str,
) -> tuple[str, str, bool]:
    prompt_sha = sha256_text(prompt)
    cache_payload = {
        "compiler_version": LLM_FULL_COMPILER_VERSION,
        "purpose": purpose,
        "prompt_sha256": prompt_sha,
        "provider": provider_name,
        "model": model,
        "record_ids": record_ids,
        "record_hashes": record_hashes,
    }
    cache_key = stable_id("LLMBRAIN", canonical_json(cache_payload), length=20)
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        cached = read_json(cache_path)
        output = cached.get("output") if isinstance(cached, dict) else None
        if isinstance(output, str):
            return output, cache_key, True
    output = await provider.generate_text(prompt=prompt, purpose=purpose)
    write_json(
        cache_path,
        {
            "schema_version": "nslab.llm_brain_compile_cache.v1",
            "cache_key": cache_key,
            **cache_payload,
            "output_sha256": sha256_text(output),
            "output": output,
        },
    )
    return output, cache_key, False


def _brain_record_shard_prompt(
    *,
    shard_index: int,
    records: list[BrainRecordEnvelope],
    brain_version: str,
    provider_name: str,
    model: str,
) -> str:
    compact_records = [_compact_record_for_prompt(record) for record in records]
    return json.dumps(
        {
            "instruction": (
                "Summarize this record shard for a research brain map pass. "
                "Extract reusable mechanisms, success/failure boundaries, near "
                "misses, contradictions, and unresolved research questions. Cite "
                "record IDs and avoid ticker, company, theme, region, or "
                "beneficiary lookup rules."
            ),
            "compiler_version": LLM_FULL_COMPILER_VERSION,
            "brain_version": brain_version,
            "shard_index": shard_index,
            "provider": provider_name,
            "model": model,
            "records": compact_records,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _brain_category_prompt(
    *,
    category: str,
    records: list[BrainRecordEnvelope],
    shard_summaries: list[dict[str, Any]],
    brain_version: str,
    provider_name: str,
    model: str,
) -> str:
    compact_records = [_compact_record_for_prompt(record) for record in records[:200]]
    return json.dumps(
        {
            "instruction": (
                "Reduce shard summaries and selected raw records into "
                "category-specific research brain claims. Avoid ticker, company, "
                "theme, region, or beneficiary lookup rules. Every claim must cite "
                "supporting record IDs, state boundary conditions, and identify "
                "contradicting or near-miss records when present."
            ),
            "compiler_version": LLM_FULL_COMPILER_VERSION,
            "brain_version": brain_version,
            "category": category,
            "provider": provider_name,
            "model": model,
            "record_shard_summaries": _compact_shard_summaries(shard_summaries),
            "records": compact_records,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _brain_category_review_prompt(
    *,
    category: str,
    synthesis: str,
    records: list[BrainRecordEnvelope],
    shard_summaries: list[dict[str, Any]],
    brain_version: str,
    provider_name: str,
    model: str,
) -> str:
    return json.dumps(
        {
            "instruction": (
                "Review this category synthesis for contradictions, overreach, "
                "missing boundary conditions, and claims supported only by a "
                "single episode. Return concise corrections and unresolved risks. "
                "Cite record IDs when possible."
            ),
            "compiler_version": LLM_FULL_COMPILER_VERSION,
            "brain_version": brain_version,
            "category": category,
            "provider": provider_name,
            "model": model,
            "record_shard_summaries": _compact_shard_summaries(shard_summaries),
            "records": [_compact_record_for_prompt(record) for record in records[:200]],
            "synthesis": synthesis,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _compact_record_for_prompt(record: BrainRecordEnvelope) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "record_type": record.record_type,
        "training_target": record.training_target,
        "evidence_phase": record.evidence_phase,
        "response_class": record.payload.get("response_class"),
        "path_type": record.payload.get("path_type"),
        "status": record.status,
        "confidence_label": record.confidence_label,
        "provenance_source_ids": record.provenance_source_ids[:5],
    }


def _compact_shard_summaries(shard_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "shard_index": shard["shard_index"],
            "record_ids": shard["record_ids"],
            "record_count": shard["record_count"],
            "summary": shard["summary"],
        }
        for shard in shard_summaries
    ]
