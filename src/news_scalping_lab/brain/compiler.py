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
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.records.models import BrainRecordEnvelope, CompiledBrainClaim
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import AsyncEmbeddingProviderAdapter
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
CATEGORY_RECORD_TYPE_ROUTES = {
    "single_event": {"supervised_direct_event_case", "supervised_issuer_day_case"},
    "theme_formation": {"supervised_theme_formation_case"},
    "beneficiary_discovery": {"beneficiary_discovery_case"},
    "leader_selection": {"blind_leader_preference_pair"},
    "continuation": {
        "mechanism_memory",
        "memory_claim",
        "company_memory_delta",
        "event_ticker_edge",
    },
    "failure_modes": {
        "candidate_generation_error_case",
        "candidate_ranking_error_case",
        "row_disposition_error_case",
        "entity_resolution_error_case",
    },
    "counterexamples": {"counterexample"},
    "market_memory": {"memory_claim", "mechanism_memory", "company_memory_delta"},
}
EMPTY_BRAIN_CREATED_AT = datetime(1970, 1, 1, tzinfo=KST)
SHARD_BRAIN_EPISODE_COUNT = 10
CATALOG_COMPILER_VERSION = "nslab.brain.catalog.compiler.v3"
LLM_FULL_COMPILER_VERSION = "nslab.brain.llm_full.compiler.v2"
LLM_FULL_RECORD_SHARD_SIZE = 50
LLM_PROMPT_MAX_PAYLOAD_FIELDS = 32
LLM_PROMPT_MAX_LIST_ITEMS = 12
LLM_PROMPT_MAX_STRING_LENGTH = 800
LLM_PROMPT_PAYLOAD_FIELDS = (
    "issuer_day_case_id",
    "case_id",
    "blind_pair_id",
    "error_id",
    "mechanism_id",
    "claim_id",
    "counterexample_id",
    "question_id",
    "edge_id",
    "ticker",
    "company_name",
    "event_id",
    "event_ids",
    "fact_ids",
    "inference_ids",
    "blind_fact_ids",
    "blind_inference_ids",
    "source_ids",
    "safe_D1_features",
    "D_outcome",
    "outcome",
    "response_class",
    "sample_weight",
    "label_quality",
    "attribution_status",
    "path_type",
    "candidate_path_type",
    "theme_id",
    "peer_universe",
    "blind_preferred_candidate_id",
    "blind_rejected_candidate_id",
    "outcome_preferred_candidate_id",
    "outcome_rejected_candidate_id",
    "blind_preferred_ticker",
    "blind_rejected_ticker",
    "outcome_winner_ticker",
    "blind_preference_correct",
    "original_decision",
    "corrected_decision",
    "correction_mode",
    "correction_rationale",
    "error_type",
    "relation_class",
    "relation_explanation",
    "directly_mentioned",
    "known_at",
    "business_descriptions",
    "supply_chain_roles",
    "prior_market_narratives",
    "contradictory_relations",
    "statement",
    "mechanism",
    "scope",
    "conditions",
    "boundary_conditions",
    "failure_modes",
)
LLM_PROMPT_PAYLOAD_DUPLICATE_FIELDS = {
    "schema_version",
    "record_id",
    "record_type",
    "episode_id",
    "trade_date",
    "available_from",
    "training_target",
}


@dataclass(frozen=True)
class LLMFullCompileResult:
    category_outputs: dict[str, str]
    manifest: dict[str, Any]
    run_metadata: dict[str, Any]


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
        brain_record_hashes = _brain_record_hashes(self.root)
        created_at = _deterministic_rebuild_timestamp(episodes)
        version = _deterministic_brain_version(
            covered_episode_ids=covered_ids,
            source_hashes=source_hashes,
            brain_record_hashes=brain_record_hashes,
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
            catalog_only=True,
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
        if isinstance(provider, DeterministicMockLLMProvider):
            raise ValueError("llm-full brain rebuild cannot use the mock LLM provider")
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
        compiled_claims = _compiled_claims_from_records(records)
        manifest = BrainManifest(
            brain_version=version,
            created_at=created_at,
            build_mode="llm-full",
            catalog_only=False,
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
                compiled_claims=compiled_claims,
            )
        )
        self._write_current(
            manifest,
            claims,
            category_outputs=llm_compile.category_outputs,
            llm_compile_metadata=llm_compile.manifest,
            llm_compile_run_metadata=llm_compile.run_metadata,
            compiled_claims=compiled_claims,
        )
        self._write_mechanism_memory(manifest, [])
        self._write_shard_brains(manifest, accepted_episodes)
        self._write_immutable_snapshot(version)
        (self.root / "brain" / "HEAD").write_text(version + "\n", encoding="utf-8")
        if previous_version != version:
            write_rebuild_diff(self.root, previous_version, version)
        LocalRetrievalStore(
            self.root,
            embedding_provider=AsyncEmbeddingProviderAdapter(
                provider,
                embedding_method=_llm_embedding_method(
                    provider_name=settings.llm_provider,
                    model=getattr(provider, "embedding_model", None)
                    or settings.llm.embedding_model
                    or "configured",
                ),
            ),
        ).rebuild_index()
        WarehouseStore(self.root).rebuild_all()
        return manifest

    def update(self, *, episode_id: str, mode: str = "full") -> BrainManifest:
        if mode not in {"full", "catalog", "llm-full"}:
            raise ValueError("only full, catalog, and llm-full update modes are supported")
        episode = self._resolve_update_episode(episode_id)
        self._ensure_update_episode_accepted(episode)
        if mode == "llm-full":
            return self.rebuild(mode="llm-full")
        episodes = self.store.list_accepted()
        source_hashes = self.store.accepted_hashes()
        brain_record_hashes = _brain_record_hashes(self.root)
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
            return self.rebuild(mode=mode)

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
            brain_record_hashes=brain_record_hashes,
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
            catalog_only=True,
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

    def _ensure_update_episode_accepted(self, episode: ResearchEpisode) -> None:
        accepted_ids = {item.episode_id for item in self.store.list_accepted()}
        if episode.episode_id in accepted_ids:
            return
        if episode.research_version == "evaluation-postmortem-v1":
            self.store.accept(episode.episode_id)
            return
        raise ValueError(
            "brain update requires an accepted episode; run "
            f"`nslab research accept {episode.episode_id}` first"
        )

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
        compiled_claims: list[CompiledBrainClaim] | None = None,
        llm_compile_run_metadata: dict[str, Any] | None = None,
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
        compiled_claims_path = self.current_dir / "compiled_claims.jsonl"
        if compiled_claims is not None:
            compiled_claims_path.write_text(
                "".join(claim.model_dump_json() + "\n" for claim in compiled_claims),
                encoding="utf-8",
            )
        elif compiled_claims_path.exists():
            compiled_claims_path.unlink()
        write_json(self.current_dir / "coverage_manifest.json", self._coverage_manifest(manifest))
        records = BrainRecordStore(self.root).list_records()
        record_coverage = self._record_coverage_manifest(manifest, records=records)
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
            _brain_compile_diagnostic_report(
                manifest=manifest,
                claims=claims,
                compiled_claims=compiled_claims,
                records=records,
                record_coverage=record_coverage,
                llm_compile_metadata=llm_compile_metadata,
                llm_compile_run_metadata=llm_compile_run_metadata,
            ),
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
            f"Catalog only: `{manifest.catalog_only}`",
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

    def _record_coverage_manifest(
        self,
        manifest: BrainManifest,
        *,
        records: list[BrainRecordEnvelope] | None = None,
    ) -> dict[str, object]:
        if records is None:
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
            "catalog_only": manifest.catalog_only,
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
            "catalog_only": manifest.catalog_only,
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
    brain_record_hashes: dict[str, str] | None = None,
    shard_episode_count: int = SHARD_BRAIN_EPISODE_COUNT,
) -> str:
    payload = {
        "brain_files": BRAIN_FILES,
        "brain_record_hashes": brain_record_hashes or {},
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
    brain_record_hashes: dict[str, str] | None = None,
    shard_episode_count: int = SHARD_BRAIN_EPISODE_COUNT,
) -> str:
    return _deterministic_brain_version(
        covered_episode_ids=covered_episode_ids,
        source_hashes=source_hashes,
        brain_record_hashes=brain_record_hashes,
        shard_episode_count=shard_episode_count,
    )


def _brain_record_hashes(root: Path) -> dict[str, str]:
    return {
        record.record_id: record.normalized_payload_sha256
        for record in BrainRecordStore(root).list_records()
    }


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


def _llm_embedding_method(*, provider_name: str, model: str) -> str:
    return f"llm_embedding:{provider_name}:{model}"


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


def _category_guidance(category: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "focus": _category_focus(category),
        "must_cite_record_ids": True,
        "must_state_boundary_conditions": True,
        "do_not_create_lookup_tables": True,
    }
    guidance: dict[str, dict[str, Any]] = {
        "world_model": {
            "primary_record_types": "all record types",
            "synthesis_targets": [
                "cross-category mechanisms",
                "temporal availability rules",
                "common failure and contradiction patterns",
            ],
            "review_targets": [
                "claims that ignore record evidence phases",
                "rules validated by a single episode",
            ],
        },
        "single_event": {
            "primary_record_types": [
                "supervised_direct_event_case",
                "supervised_issuer_day_case",
            ],
            "synthesis_targets": [
                "direct event response mechanisms",
                "issuer-day sample-weight and attribution boundaries",
                "safe D-1 features that remain blind-safe",
            ],
            "review_targets": [
                "outcome leakage through D-day labels",
                "overstated direct attribution",
            ],
        },
        "theme_formation": {
            "primary_record_types": ["supervised_theme_formation_case"],
            "synthesis_targets": [
                "theme breadth and formation thresholds",
                "positive theme evidence versus failed formations",
                "peer-universe and path-type boundaries",
            ],
            "review_targets": [
                "theme labels used as whitelists",
                "thin breadth or single-name overreach",
            ],
        },
        "beneficiary_discovery": {
            "primary_record_types": ["beneficiary_discovery_case"],
            "synthesis_targets": [
                "indirect relation discovery patterns",
                "verification gaps and source requirements",
                "new-company handling without source-code mappings",
            ],
            "review_targets": [
                "unsupported beneficiary leaps",
                "company memory used before available_from",
            ],
        },
        "leader_selection": {
            "primary_record_types": ["blind_leader_preference_pair"],
            "synthesis_targets": [
                "sealed blind preference evidence",
                "leader versus rejected-candidate boundaries",
                "correction cases when blind preference loses",
            ],
            "review_targets": [
                "postmortem-only winner information leaking into blind rules",
                "cross-product preferences not backed by sealed pairs",
            ],
        },
        "continuation": {
            "primary_record_types": [
                "mechanism_memory",
                "memory_claim",
                "company_memory_delta",
                "event_ticker_edge",
            ],
            "synthesis_targets": [
                "continuation conditions available before cutoff",
                "market-memory and company-memory temporal boundaries",
                "decay or exhaustion signals",
            ],
            "review_targets": [
                "D-day price dependence",
                "future company-memory deltas",
            ],
        },
        "failure_modes": {
            "primary_record_types": [
                "candidate_generation_error_case",
                "candidate_ranking_error_case",
                "row_disposition_error_case",
                "entity_resolution_error_case",
            ],
            "synthesis_targets": [
                "generation misses",
                "ranking mistakes",
                "row disposition and entity-resolution corrections",
            ],
            "review_targets": [
                "failure labels treated as positive predictors",
                "corrections without original decision context",
            ],
        },
        "counterexamples": {
            "primary_record_types": ["counterexample"],
            "synthesis_targets": [
                "negative controls",
                "near misses",
                "contradicting record boundaries",
            ],
            "review_targets": [
                "positive claims without contradiction checks",
                "negative evidence hidden in generic caveats",
            ],
        },
        "market_memory": {
            "primary_record_types": [
                "memory_claim",
                "mechanism_memory",
                "company_memory_delta",
            ],
            "synthesis_targets": [
                "as-of market memory",
                "company-memory deltas and contradictory relations",
                "mechanism memory usable without source-code maps",
            ],
            "review_targets": [
                "future-known relationships",
                "outcome-only association promoted as memory",
            ],
        },
    }
    selected: dict[str, Any] = guidance.get(
        category,
        {
            "primary_record_types": [],
            "synthesis_targets": ["category-specific evidence"],
            "review_targets": ["unsupported generalization"],
        },
    )
    return {**base, **selected}


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


def _brain_compile_diagnostic_report(
    *,
    manifest: BrainManifest,
    claims: list[MemoryClaim],
    compiled_claims: list[CompiledBrainClaim] | None,
    records: list[BrainRecordEnvelope],
    record_coverage: dict[str, object],
    llm_compile_metadata: dict[str, Any] | None,
    llm_compile_run_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    llm_compile: dict[str, Any] = (
        llm_compile_metadata if isinstance(llm_compile_metadata, dict) else {}
    )
    llm_compile_present = bool(llm_compile)
    compiler_provider = _string_from_mapping(
        llm_compile,
        "provider",
        default="deterministic_catalog",
    )
    compiler_model = _string_from_mapping(
        llm_compile,
        "model",
        default=CATALOG_COMPILER_VERSION,
    )
    compiler_version = _string_from_mapping(
        llm_compile,
        "compiler_version",
        default=CATALOG_COMPILER_VERSION,
    )
    category_claim_ids = _category_claim_ids(
        claims=claims,
        compiled_claims=compiled_claims,
    )
    category_source_record_type_counts = _category_source_record_type_counts(
        llm_compile=llm_compile,
        records=records,
    )
    category_source_record_counts = _category_source_record_counts(
        llm_compile,
        category_source_record_type_counts,
    )
    return {
        "schema_version": "nslab.brain_compile_diagnostics.v1",
        "brain_version": manifest.brain_version,
        "compiler_mode": manifest.build_mode,
        "catalog_only": manifest.catalog_only,
        "compiler_provider": compiler_provider,
        "compiler_model": compiler_model,
        "compiler_version": compiler_version,
        "accepted_episode_count": manifest.accepted_episode_count,
        "covered_episode_count": manifest.covered_episode_count,
        "claim_count": len(claims),
        "compiled_claim_count": len(compiled_claims or []),
        "compiled_claims_file_present": compiled_claims is not None,
        "category_file_count": len(BRAIN_FILES),
        "category_files": BRAIN_FILES,
        "category_claim_counts": {
            category: len(claim_ids)
            for category, claim_ids in sorted(category_claim_ids.items())
        },
        "category_claim_ids": category_claim_ids,
        "category_source_record_counts": category_source_record_counts,
        "category_source_record_type_counts": category_source_record_type_counts,
        "record_coverage": _record_coverage_summary(record_coverage),
        "llm_compile_present": llm_compile_present,
        "llm_compile": llm_compile_metadata,
        "llm_compile_run_present": isinstance(llm_compile_run_metadata, dict)
        and bool(llm_compile_run_metadata),
        "llm_compile_run": llm_compile_run_metadata,
    }


def _category_claim_ids(
    *,
    claims: list[MemoryClaim],
    compiled_claims: list[CompiledBrainClaim] | None,
) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {}
    for file_name in BRAIN_FILES:
        category = _brain_category(file_name)
        if compiled_claims is not None:
            category_ids = _compiled_claim_ids_for_category(compiled_claims, category)
        else:
            category_ids = [
                claim.claim_id for claim in _claims_for_category(claims, category)
            ]
        ids[category] = category_ids
    return dict(sorted(ids.items()))


def _category_source_record_counts(
    llm_compile: dict[str, Any],
    category_source_record_type_counts: dict[str, dict[str, int]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    categories = llm_compile.get("categories")
    if isinstance(categories, list):
        for category in categories:
            if not isinstance(category, dict):
                continue
            category_name = category.get("category")
            count = category.get("source_record_count")
            if isinstance(category_name, str) and isinstance(count, int):
                counts[category_name] = count
    for category_name, type_counts in category_source_record_type_counts.items():
        counts.setdefault(category_name, sum(type_counts.values()))
    return dict(sorted(counts.items()))


def _category_source_record_type_counts(
    *,
    llm_compile: dict[str, Any],
    records: list[BrainRecordEnvelope],
) -> dict[str, dict[str, int]]:
    records_by_id = {record.record_id: record for record in records}
    categories = llm_compile.get("categories")
    if isinstance(categories, list):
        exact_counts: dict[str, dict[str, int]] = {}
        for category in categories:
            if not isinstance(category, dict):
                continue
            category_name = category.get("category")
            record_ids = category.get("source_record_ids")
            if not isinstance(category_name, str) or not isinstance(record_ids, list):
                continue
            category_records = [
                records_by_id[record_id]
                for record_id in record_ids
                if isinstance(record_id, str) and record_id in records_by_id
            ]
            counts = _record_type_counts(category_records)
            unknown_count = sum(
                1
                for record_id in record_ids
                if isinstance(record_id, str) and record_id not in records_by_id
            )
            if unknown_count:
                counts["UNKNOWN_RECORD"] = unknown_count
            exact_counts[category_name] = dict(sorted(counts.items()))
        if exact_counts:
            return dict(sorted(exact_counts.items()))

    fallback_counts: dict[str, dict[str, int]] = {}
    for file_name in BRAIN_FILES:
        category = _brain_category(file_name)
        if category == "world_model":
            category_records = records
        else:
            allowed = CATEGORY_RECORD_TYPE_ROUTES.get(category, set())
            category_records = [
                record for record in records if record.record_type in allowed
            ]
        fallback_counts[category] = _record_type_counts(category_records)
    return dict(sorted(fallback_counts.items()))


def _record_type_counts(records: list[BrainRecordEnvelope]) -> dict[str, int]:
    return dict(sorted(Counter(record.record_type for record in records).items()))


def _record_coverage_summary(record_coverage: dict[str, object]) -> dict[str, object]:
    keys = (
        "accepted_record_count",
        "available_record_count",
        "training_eligible_available_record_count",
        "compiled_record_count",
        "swept_record_count",
        "unswept_record_ids",
        "record_counts_by_type",
        "record_counts_by_evidence_phase",
        "record_counts_by_training_target",
        "ineligible_record_count",
        "audit_only_record_count",
        "coverage_complete",
    )
    return {key: record_coverage.get(key) for key in keys if key in record_coverage}


def _string_from_mapping(
    payload: dict[str, Any],
    key: str,
    *,
    default: str,
) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) and value else default


def _compiled_claims_from_records(
    records: list[BrainRecordEnvelope],
) -> list[CompiledBrainClaim]:
    claims: list[CompiledBrainClaim] = []
    for record in sorted(records, key=lambda item: item.record_id):
        target = str(record.training_target or record.record_type)
        is_negative_evidence = record.record_type in {
            "candidate_generation_error_case",
            "candidate_ranking_error_case",
            "row_disposition_error_case",
            "entity_resolution_error_case",
            "counterexample",
        }
        claims.append(
            CompiledBrainClaim(
                claim_id=stable_id("CC", record.record_id, record.normalized_payload_sha256),
                category=_compiled_claim_category(record),
                statement=_record_claim_statement(record),
                mechanism=target,
                scope=f"record-derived {record.record_type}",
                conditions=[
                    "record must be available as of the analysis cutoff",
                    "apply only with current category brain and retrieved counterexamples",
                ],
                boundary_conditions=[
                    "do not promote one record to a validated rule without broader evidence",
                    "respect the source record evidence phase and label quality",
                ],
                failure_modes=[
                    "overgeneralization",
                    "hindsight contamination",
                ],
                supporting_record_ids=[record.record_id],
                contradicting_record_ids=[],
                supporting_episode_ids=[record.episode_id],
                contradicting_episode_ids=[],
                positive_case_count=0 if is_negative_evidence else 1,
                negative_case_count=1 if is_negative_evidence else 0,
                near_miss_count=1 if is_negative_evidence else 0,
                confidence_label=record.confidence_label,
                status="supported" if record.training_eligible else "tentative",
                available_from=record.available_from,
                provenance={
                    "source_type": "brain_record",
                    "record_id": record.record_id,
                    "episode_id": record.episode_id,
                    "record_type": record.record_type,
                    "normalized_payload_sha256": record.normalized_payload_sha256,
                },
            )
        )
    return claims


def _compiled_claim_category(record: BrainRecordEnvelope) -> str:
    mapping = {
        "supervised_direct_event_case": "single_event",
        "supervised_issuer_day_case": "single_event",
        "supervised_theme_formation_case": "theme_formation",
        "beneficiary_discovery_case": "beneficiary_discovery",
        "blind_leader_preference_pair": "leader_selection",
        "candidate_generation_error_case": "failure_modes",
        "candidate_ranking_error_case": "failure_modes",
        "row_disposition_error_case": "failure_modes",
        "entity_resolution_error_case": "failure_modes",
        "counterexample": "counterexamples",
        "memory_claim": "market_memory",
        "mechanism_memory": "market_memory",
        "company_memory_delta": "market_memory",
    }
    return mapping.get(record.record_type, "world_model")


async def _compile_llm_category_outputs(
    *,
    root: Path,
    provider: LLMProvider,
    records: list[BrainRecordEnvelope],
    brain_version: str,
    provider_name: str,
    model: str,
    compiled_claims: list[CompiledBrainClaim],
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
        output, cache_key, cache_hit = await _cached_generate_text(
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
                "cache_hit": cache_hit,
                "summary": output,
            }
        )
    outputs: dict[str, str] = {}
    categories: list[dict[str, Any]] = []
    for file_name in BRAIN_FILES:
        category = _brain_category(file_name)
        category_records = _records_for_category(records, category)
        category_compiled_claim_ids = _compiled_claim_ids_for_category(
            compiled_claims,
            category,
        )
        prompt = _brain_category_prompt(
            category=category,
            records=category_records,
            shard_summaries=shard_summaries,
            brain_version=brain_version,
            provider_name=provider_name,
            model=model,
        )
        synthesis, synthesis_cache_key, synthesis_cache_hit = await _cached_generate_text(
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
        review, review_cache_key, review_cache_hit = await _cached_generate_text(
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
                "compiled_claim_count": len(category_compiled_claim_ids),
                "compiled_claim_ids": category_compiled_claim_ids,
                "synthesis_cache_key": synthesis_cache_key,
                "synthesis_cache_hit": synthesis_cache_hit,
                "review_cache_key": review_cache_key,
                "review_cache_hit": review_cache_hit,
            }
        )
    llm_cache_hit_count = sum(
        1 for shard in shard_summaries if shard.get("cache_hit") is True
    ) + sum(
        int(category.get("synthesis_cache_hit") is True)
        + int(category.get("review_cache_hit") is True)
        for category in categories
    )
    llm_generation_count = len(shard_summaries) + len(categories) * 2
    llm_live_call_count = llm_generation_count - llm_cache_hit_count
    manifest = {
        "schema_version": "nslab.llm_full_brain_compile_manifest.v1",
        "compiler_version": LLM_FULL_COMPILER_VERSION,
        "brain_version": brain_version,
        "provider": provider_name,
        "model": model,
        "source_record_count": len(sorted_records),
        "compiled_claim_count": len(compiled_claims),
        "llm_generation_count": llm_generation_count,
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
        "categories": [
            {
                key: value
                for key, value in category.items()
                if key not in {"synthesis_cache_hit", "review_cache_hit"}
            }
            for category in categories
        ],
    }
    run_metadata = {
        "schema_version": "nslab.llm_full_brain_compile_run.v1",
        "brain_version": brain_version,
        "provider": provider_name,
        "model": model,
        "llm_generation_count": llm_generation_count,
        "llm_live_call_count": llm_live_call_count,
        "llm_cache_hit_count": llm_cache_hit_count,
        "llm_cache_miss_count": llm_live_call_count,
        "all_outputs_from_cache": llm_generation_count > 0 and llm_live_call_count == 0,
        "record_shards": [
            {
                "shard_index": shard["shard_index"],
                "cache_key": shard["cache_key"],
                "record_count": shard["record_count"],
                "cache_hit": shard["cache_hit"],
            }
            for shard in shard_summaries
        ],
        "categories": [
            {
                "category": category["category"],
                "file_name": category["file_name"],
                "source_record_count": category["source_record_count"],
                "synthesis_cache_key": category["synthesis_cache_key"],
                "synthesis_cache_hit": category["synthesis_cache_hit"],
                "review_cache_key": category["review_cache_key"],
                "review_cache_hit": category["review_cache_hit"],
            }
            for category in categories
        ],
    }
    return LLMFullCompileResult(
        category_outputs=outputs,
        manifest=manifest,
        run_metadata=run_metadata,
    )


def _compiled_claim_ids_for_category(
    claims: list[CompiledBrainClaim],
    category: str,
) -> list[str]:
    if category == "world_model":
        return [claim.claim_id for claim in claims]
    return [claim.claim_id for claim in claims if claim.category == category]


def _records_for_category(
    records: list[BrainRecordEnvelope],
    category: str,
) -> list[BrainRecordEnvelope]:
    if category == "world_model":
        return records
    allowed = CATEGORY_RECORD_TYPE_ROUTES.get(category, set())
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
            "category_guidance": _category_guidance(category),
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
            "category_guidance": _category_guidance(category),
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
    compact: dict[str, Any] = {
        "record_id": record.record_id,
        "record_type": record.record_type,
        "training_target": record.training_target,
        "evidence_phase": record.evidence_phase,
        "status": record.status,
        "confidence_label": record.confidence_label,
        "provenance_source_ids": record.provenance_source_ids[:5],
    }
    routing_features = _record_routing_features(record)
    if routing_features:
        compact["routing_features"] = routing_features
    payload_summary = _compact_payload_for_llm_prompt(record.payload)
    if payload_summary:
        compact["payload_summary"] = payload_summary
    return compact


def _record_routing_features(record: BrainRecordEnvelope) -> dict[str, Any]:
    payload = record.payload
    routing = {
        "record_type": record.record_type,
        "training_target": record.training_target,
        "evidence_phase": record.evidence_phase,
        "path_type": payload.get("path_type") or payload.get("candidate_path_type"),
        "response_class": payload.get("response_class"),
        "attribution_status": payload.get("attribution_status"),
        "error_type": payload.get("error_type"),
        "correction_mode": payload.get("correction_mode"),
        "theme_id": payload.get("theme_id"),
        "relation_class": payload.get("relation_class"),
        "blind_preference_correct": payload.get("blind_preference_correct"),
    }
    return {
        key: value
        for key, value in routing.items()
        if not _empty_prompt_value(value)
    }


def _compact_payload_for_llm_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for key in LLM_PROMPT_PAYLOAD_FIELDS:
        if key not in payload:
            continue
        value = _compact_prompt_value(payload[key], depth=0)
        if not _empty_prompt_value(value):
            selected[key] = value
        if len(selected) >= LLM_PROMPT_MAX_PAYLOAD_FIELDS:
            return selected
    if selected:
        return selected
    for key in sorted(payload):
        if key in LLM_PROMPT_PAYLOAD_DUPLICATE_FIELDS:
            continue
        value = _compact_prompt_value(payload[key], depth=0)
        if not _empty_prompt_value(value):
            selected[key] = value
        if len(selected) >= LLM_PROMPT_MAX_PAYLOAD_FIELDS:
            break
    return selected


def _compact_prompt_value(value: Any, *, depth: int) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) <= LLM_PROMPT_MAX_STRING_LENGTH:
            return stripped
        return stripped[:LLM_PROMPT_MAX_STRING_LENGTH] + "...[truncated]"
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, list):
        if depth >= 4:
            return f"[nested list with {len(value)} items]"
        return [
            compact_item
            for item in value[:LLM_PROMPT_MAX_LIST_ITEMS]
            if not _empty_prompt_value(
                compact_item := _compact_prompt_value(item, depth=depth + 1)
            )
        ]
    if isinstance(value, dict):
        if depth >= 4:
            return f"[nested object with {len(value)} keys]"
        selected: dict[str, Any] = {}
        for key, nested in sorted(value.items(), key=lambda item: str(item[0]))[
            :LLM_PROMPT_MAX_PAYLOAD_FIELDS
        ]:
            compact_nested = _compact_prompt_value(nested, depth=depth + 1)
            if not _empty_prompt_value(compact_nested):
                selected[str(key)] = compact_nested
        return selected
    if value is None:
        return None
    return str(value)


def _empty_prompt_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


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
