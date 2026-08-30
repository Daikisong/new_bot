"""Daily blind analysis pipeline."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import ValidationError

from news_scalping_lab.config import Settings
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.context.final_synthesis import (
    FINAL_SYNTHESIS_REQUIRED_INPUTS_SHARED_V2,
    FINAL_SYNTHESIS_REQUIRED_INPUTS_SHARED_V3,
    FINAL_SYNTHESIS_REQUIRED_INPUTS_V2,
    FINAL_SYNTHESIS_REQUIRED_INPUTS_V3,
    FINAL_SYNTHESIS_V2_PROMPT_VERSION,
    FINAL_SYNTHESIS_V3_PROMPT_VERSION,
    final_synthesis_input_summary,
    phase7_beneficiary_graph_prompt_projection,
    phase7_daily_prompt_projection,
    string_list,
)
from news_scalping_lab.context.memory_coverage import (
    inspect_memory_coverage_manifest,
)
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.context.sweep import MemorySweeper
from news_scalping_lab.contracts.memory_context import (
    AdaptiveRetrievalTrace,
    BeneficiaryGraphArtifact,
    DailyMemoryContext,
    EventClusterEntry,
    EventClusterManifest,
    MemoryCellSnapshotManifest,
    NewsCoverageManifest,
    NewsRowCoverage,
)
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    CandidateExpansionFinding,
    CandidateExpansionPath,
    CandidateExpansionReview,
    CandidateVerificationDimension,
    CandidateVerificationFinding,
    CandidateVerificationReview,
    CandidateVerificationStatus,
    CompanyMemory,
    ConfidenceLabel,
    ContextManifest,
    DailyAnalysis,
    DominantSectorHypothesis,
    FinalSynthesisContextArtifact,
    NewsItem,
    NewsNoveltyFinding,
    NewsNoveltyLabel,
    NewsNoveltyReview,
    OpenWorldClusterFinding,
    OpenWorldFirstAnalysis,
    PathType,
    Provenance,
    RedTeamArtifact,
    SemanticRetrievalPlan,
    SemanticRetrievalQuery,
)
from news_scalping_lab.contracts.quality_evaluation import (
    DMinusOneProjectionRequest,
    DMinusOnePromptProjection,
    QualityArtifactReference,
    SharedDMinusOneContext,
    SharedDownstreamDigest,
    SharedPreRetrievalContext,
    SharedPreRetrievalContextManifest,
    reject_forbidden_blind_payload_keys,
)
from news_scalping_lab.contracts.runtime_retrieval import (
    RuntimeEvidenceMemo,
    RuntimeRetrievalTrace,
)
from news_scalping_lab.inference.event_clustering import (
    EVENT_CLUSTERING_VERSION,
    EventClusteringResult,
    OpenWorldClusterInput,
    cluster_news_events,
    event_clustering_from_payload,
    open_world_cluster_inputs,
)
from news_scalping_lab.inference.red_team import (
    PROMPT_VERSION as RED_TEAM_PROMPT_VERSION,
)
from news_scalping_lab.inference.red_team import (
    apply_red_team_findings,
    run_red_team_pass,
)
from news_scalping_lab.ingest.news import (
    NewsBatch,
    load_news_csv,
    news_batch_content_root,
)
from news_scalping_lab.llm.base import (
    TOKEN_COUNTING_VERSION,
    EmbeddingProvider,
    LLMProvider,
    count_provider_tokens,
)
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.memory import MemoryStore
from news_scalping_lab.memory.beneficiary import build_beneficiary_graph
from news_scalping_lab.memory.company import (
    CompanyMemoryDeltaApplyResult,
    CompanyMemoryStore,
    production_company_memory_attestation_required,
)
from news_scalping_lab.memory.daily_context import (
    attach_runtime_evidence_to_daily_context,
    bind_final_beneficiary_graph_to_daily_context,
    build_daily_memory_context,
)
from news_scalping_lab.memory.index import (
    ProductionMemoryIndex,
    active_memory_snapshot_manifest,
    inspect_current_memory_index,
    inspect_verified_evaluation_memory_index,
)
from news_scalping_lab.memory.runtime import production_embedding_method
from news_scalping_lab.memory.runtime_v4 import (
    RuntimeEvidenceBuildResult,
    RuntimeRetrievalBuildResult,
    build_runtime_evidence_memos_packed,
    finalize_runtime_retrieval_trace,
)
from news_scalping_lab.policies import EvidencePolicy, web_required_for_policy
from news_scalping_lab.prices.base import (
    BlindPriceAccessError,
    BlindPriceGuard,
    PriceRecord,
    PriceSource,
)
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.records.hashing import brain_record_envelope_sha256
from news_scalping_lab.records.models import (
    CANDIDATE_ERROR_RECORD_TYPES,
    BrainRecordEnvelope,
)
from news_scalping_lab.records.routing import (
    COUNTEREXAMPLES_LANE,
    MEMORY_RETRIEVAL_LANES,
    NEWSLESS_OR_UNEXPLAINED_LANE,
    RecordEvidencePolarity,
    RecordRoutingDisposition,
    record_is_positive_support,
    record_routing_metadata,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.reporting.render import render_preopen_report
from news_scalping_lab.retrieval.embedding import AsyncEmbeddingProviderAdapter
from news_scalping_lab.retrieval.production_embedding import (
    ProductionEmbeddingUnavailableError,
    create_configured_embedding_provider,
)
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    default_news_window_start,
    file_sha256,
    is_available_as_of,
    now_kst,
    parse_datetime,
    read_json,
    relative_to_root,
    sha256_bytes,
    sha256_text,
    stable_id,
    write_json,
)
from news_scalping_lab.warehouse import WarehouseStore
from news_scalping_lab.web.factory import create_web_provider
from news_scalping_lab.web.provider import (
    TemporalWebGuard,
    UnexpectedWebAccessError,
    WebResearchProvider,
    WebSearchExclusion,
    WebSearchResult,
)


class ExhaustiveCoverageError(RuntimeError):
    """Raised when exhaustive mode fails to sweep every required context item."""


class ClusterCoverageError(RuntimeError):
    """Raised when an event cluster is not covered by retrieval audit."""


class OpenWorldCoverageError(RuntimeError):
    """Raised when Pass 0 does not attest every dispatched material cluster."""


class FutureContextLeakError(RuntimeError):
    """Raised when the active brain context contains future-unavailable research."""


class FinalSynthesisBudgetError(RuntimeError):
    """Raised when bounded reasoning context exceeds the configured prompt budget."""


DAILY_BLIND_PROMPT_VERSION = "daily_blind_analysis.v1"
OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION = "open_world_first_analysis.v2"
NEWS_NOVELTY_REVIEW_PROMPT_VERSION = "news_novelty_review.v1"
SEMANTIC_RETRIEVAL_PLAN_PROMPT_VERSION = "semantic_retrieval_plan.v2"
CANDIDATE_EXPANSION_PROMPT_VERSION = "candidate_expansion.v1"
FINAL_SYNTHESIS_PROMPT_VERSION = FINAL_SYNTHESIS_V2_PROMPT_VERSION
SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES = MEMORY_RETRIEVAL_LANES
CANDIDATE_EXPANSION_REQUIRED_PATHS = (
    CandidateExpansionPath.SINGLE_EVENT,
    CandidateExpansionPath.THEME_FORMATION,
    CandidateExpansionPath.BENEFICIARY_DISCOVERY,
    CandidateExpansionPath.CONTINUATION,
)
CANDIDATE_WEB_VERIFICATION_FOCUS = (
    "listed_security_and_exact_ticker",
    "business_location_customer_supply_chain_relation",
    "prior_market_narratives_and_theme_memory",
    "current_news_relation_vs_name_similarity",
    "recent_disclosures_and_news",
    "market_cap_and_shares_outstanding",
    "D_minus_one_trading_value_turnover_limit_up",
    "multi_day_pre_absorption",
    "liquidity_and_competing_leaders",
)


@dataclass(frozen=True)
class CandidateWebCheckSubject:
    subject_type: str
    rank: int
    ticker: str
    company_name: str
    path_type: str
    thesis: str
    why_now: str
    expansion_path: str | None = None
    expansion_hypothesis: str | None = None
    investigation_questions: tuple[str, ...] = ()
    sector_hypotheses: tuple[str, ...] = ()


def _read_verified_blind_artifact(
    path: Path,
    *,
    expected_sha256: str,
) -> object:
    """Hash, decode, scan, and parse one artifact from the same byte buffer."""

    if not path.is_file():
        raise ValueError(f"shared pre-retrieval artifact is missing: {path}")
    payload_bytes = path.read_bytes()
    if sha256_bytes(payload_bytes) != expected_sha256:
        raise ValueError(f"shared pre-retrieval artifact hash mismatch: {path}")
    try:
        payload_text = payload_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"shared pre-retrieval artifact is not UTF-8: {path}") from exc
    if path.suffix.casefold() == ".jsonl":
        rows: list[object] = []
        for line_number, line in enumerate(payload_text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"shared pre-retrieval JSONL is invalid at line {line_number}: {path}") from exc
            reject_forbidden_blind_payload_keys(row)
            rows.append(row)
        return rows
    if path.suffix.casefold() != ".json":
        raise ValueError("shared pre-retrieval artifacts must be JSON or JSONL")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"shared pre-retrieval JSON is invalid: {path}") from exc
    reject_forbidden_blind_payload_keys(payload)
    return payload


def _resolve_and_read_shared_reference(
    root: Path,
    reference: QualityArtifactReference,
) -> object:
    logical = Path(reference.artifact_path)
    if logical.is_absolute() or ".." in logical.parts:
        raise ValueError("shared pre-retrieval artifact reference is unsafe")
    resolved_root = root.resolve()
    path = (resolved_root / logical).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("shared pre-retrieval component escapes the project root") from exc
    return _read_verified_blind_artifact(
        path,
        expected_sha256=reference.sha256,
    )


class DailyAnalyzer:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMProvider | None = None,
        retrieval: MemoryStore | None = None,
        price_source: PriceSource | None = None,
        web_provider: WebResearchProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        runtime_retrieval_variant: Literal["legacy", "v4"] = "v4",
        configure_price_source: bool = True,
    ) -> None:
        if runtime_retrieval_variant not in {"legacy", "v4"}:
            raise ValueError("runtime retrieval variant must be legacy or v4")
        self.settings = settings
        self.root = settings.project_root
        base_llm = llm or create_llm_provider(settings)
        self.llm_model_config = self._llm_model_config(base_llm)
        self.runtime_retrieval_variant = runtime_retrieval_variant
        self.llm_model_config["runtime_retrieval_variant"] = runtime_retrieval_variant
        self.llm = self._trace_llm(base_llm)
        self.embedding_provider = embedding_provider or create_configured_embedding_provider(
            settings,
            production=(settings.event_cluster_fallback_policy.value == "fail-closed"),
            llm_provider=base_llm,
        )
        self.fallback_llm = DeterministicMockLLMProvider()
        self.retrieval = retrieval or LocalRetrievalStore(self.root)
        self.price_source = (
            price_source
            if price_source is not None
            else (self._configured_blind_price_source(settings) if configure_price_source else None)
        )
        self._sealed_d_minus_one_only = not configure_price_source
        self.web_provider = web_provider or create_web_provider(self.settings)
        active_snapshot = active_memory_snapshot_manifest(self.root)
        self._evaluation_memory_snapshot: MemoryCellSnapshotManifest | None = (
            active_snapshot if active_snapshot is not None and active_snapshot.evaluation_only else None
        )
        self._evaluation_company_record_cache: tuple[list[BrainRecordEnvelope], list[BrainRecordEnvelope]] | None = None

    def _configured_blind_price_source(self, settings: Settings) -> PriceSource | None:
        if settings.price_provider.strip().lower() == "mock":
            return None
        source = create_price_source(settings)
        return source

    def _blind_price_source_name(self) -> str:
        if self.price_source is not None:
            return self.price_source.source_name
        if self.settings.price_provider == "mock":
            return "mock-price"
        return f"{self.settings.price_provider}-deferred-news-only"

    def _blind_price_source_ref(self) -> str | None:
        if self.price_source is None:
            if self.settings.price_provider == "mock":
                return "mock://prices/news-only"
            return None
        source_root = getattr(self.price_source, "root", None)
        if isinstance(source_root, Path):
            try:
                return relative_to_root(source_root, self.root)
            except ValueError:
                return source_root.as_posix()
        source_name = getattr(self.price_source, "source_name", None)
        if isinstance(source_name, str) and source_name.strip():
            return f"provider://{source_name.strip()}"
        return f"provider://{self.price_source.__class__.__name__}"

    async def analyze(
        self,
        *,
        news_csv: Path,
        trade_date: date,
        cutoff_at: datetime,
        mode: str = "exhaustive",
        web_search: bool = False,
        shared_pre_retrieval_context_path: Path | None = None,
        shared_pre_retrieval_context_sha256: str | None = None,
        shared_pre_retrieval_manifest_sha256: str | None = None,
        sealed_blind_input_manifest_sha256: str | None = None,
        preloaded_news_batch: NewsBatch | None = None,
        shadow_preloaded_news_batch: NewsBatch | None = None,
        shared_d_minus_one_context_path: Path | None = None,
    ) -> DailyAnalysis:
        mode = normalize_analysis_mode(mode)
        evidence_policy = EvidencePolicy.parse(self.settings.evidence_policy)
        if web_search:
            raise UnexpectedWebAccessError(
                "BLIND analysis never permits external web search; use the separate post-close audit command"
            )
        quality_full_injection = (
            shared_pre_retrieval_context_path,
            shared_pre_retrieval_context_sha256,
            shared_pre_retrieval_manifest_sha256,
            sealed_blind_input_manifest_sha256,
            preloaded_news_batch,
            shared_d_minus_one_context_path,
        )
        configured_injections = [value is not None for value in quality_full_injection]
        if any(configured_injections) and not all(configured_injections):
            raise ValueError(
                "QUALITY_FULL shared context, sealed input, preloaded news, and D-1 artifact must be injected together"
            )
        if shadow_preloaded_news_batch is not None and any(configured_injections):
            raise ValueError(
                "shadow preloaded news cannot be combined with QUALITY_FULL injection"
            )
        if self._sealed_d_minus_one_only and not all(configured_injections):
            raise ValueError("sealed-D1-only analysis requires the complete immutable QUALITY_FULL input package")
        if sealed_blind_input_manifest_sha256 is not None and (
            len(sealed_blind_input_manifest_sha256) != 64
            or any(character not in "0123456789abcdef" for character in sealed_blind_input_manifest_sha256)
        ):
            raise ValueError("sealed blind input manifest hash must be SHA-256")
        selected_preloaded_news_batch = (
            preloaded_news_batch
            if preloaded_news_batch is not None
            else shadow_preloaded_news_batch
        )
        full_batch = (
            self._validate_preloaded_news_batch(
                news_csv=news_csv,
                trade_date=trade_date,
                batch=selected_preloaded_news_batch,
            )
            if selected_preloaded_news_batch is not None
            else load_news_csv(news_csv, trade_date=trade_date)
        )
        parsed_news_root_sha256 = news_batch_content_root(full_batch)
        news_window_start_at = default_news_window_start(trade_date)
        batch = full_batch.within_window(news_window_start_at, cutoff_at)
        shared_context: SharedPreRetrievalContext | None = None
        shared_context_sha256: str | None = None
        shared_context_manifest: SharedPreRetrievalContextManifest | None = None
        shared_context_manifest_path: Path | None = None
        shared_context_manifest_sha256: str | None = None
        shared_d_minus_one_context: SharedDMinusOneContext | None = None
        shared_d_minus_one_context_sha256: str | None = None
        resolved_shared_d_minus_one_context_path: Path | None = None
        if shared_pre_retrieval_context_path is not None:
            (
                shared_context,
                shared_context_sha256,
                shared_context_manifest,
                shared_context_manifest_path,
                shared_context_manifest_sha256,
            ) = self._load_shared_pre_retrieval_context(
                path=shared_pre_retrieval_context_path,
                expected_context_sha256=shared_pre_retrieval_context_sha256,
                expected_manifest_sha256=shared_pre_retrieval_manifest_sha256,
                news_sha256=full_batch.sha256,
                trade_date=trade_date,
                cutoff_at=cutoff_at,
            )
            event_clustering = event_clustering_from_payload(
                _resolve_and_read_shared_reference(
                    self.root,
                    shared_context.event_clustering_result,
                )
            )
            if (
                shared_context.parsed_news_root_sha256 != parsed_news_root_sha256
                or shared_context_manifest.parsed_news_root_sha256 != parsed_news_root_sha256
            ):
                raise ValueError("shared parsed-news content identity drifted")
        else:
            try:
                event_clustering = await cluster_news_events(
                    full_batch.items,
                    window_start_at=news_window_start_at,
                    cutoff_at=cutoff_at,
                    embedding_provider=self.embedding_provider,
                    embedding_batch_size=(self.settings.limits.event_cluster_embedding_batch_size),
                    similarity_threshold=(self.settings.limits.event_cluster_similarity_threshold),
                    max_semantic_variants=(self.settings.limits.event_cluster_max_semantic_variants),
                    fallback_policy=self.settings.event_cluster_fallback_policy,
                    max_retries=self.settings.llm.max_retries,
                    production_runtime_identity=(
                        production_embedding_method(
                            self.settings,
                            self.embedding_provider,
                        )
                        if self.settings.event_cluster_fallback_policy.value == "fail-closed"
                        else None
                    ),
                )
            except ProductionEmbeddingUnavailableError as exc:
                self._write_embedding_failure_receipt(
                    news_sha256=full_batch.sha256,
                    trade_date=trade_date,
                    cutoff_at=cutoff_at,
                    error=exc,
                )
                raise
        if shared_d_minus_one_context_path is not None:
            if shared_context is None:
                raise ValueError("shared D-1 context requires a shared pre-retrieval context")
            (
                shared_d_minus_one_context,
                shared_d_minus_one_context_sha256,
                resolved_shared_d_minus_one_context_path,
            ) = self._load_shared_d_minus_one_context(
                path=shared_d_minus_one_context_path,
                expected_artifact_path=(shared_context.d_minus_one_safe_context.artifact_path),
                expected_sha256=shared_context.d_minus_one_safe_context.sha256,
                trade_date=trade_date,
                cutoff_at=cutoff_at,
            )
        clustering_result_sha256 = sha256_text(
            canonical_json(
                {
                    "clustering_version": event_clustering.clustering_version,
                    "embedding_method": event_clustering.embedding_method,
                    "embedding_status": event_clustering.embedding_status,
                    "embedding_model": event_clustering.embedding_model,
                    "embedding_revision": event_clustering.embedding_revision,
                    "embedding_artifact_sha256": (event_clustering.embedding_artifact_sha256),
                    "embedding_dimensions": event_clustering.embedding_dimensions,
                    "embedding_fallback_policy": (event_clustering.embedding_fallback_policy),
                    "deterministic_fallback_used": (event_clustering.deterministic_fallback_used),
                    "production_runtime_identity": (event_clustering.production_runtime_identity),
                    "clusters": [
                        {
                            "cluster_id": cluster.cluster_id,
                            "disposition": cluster.disposition,
                            "row_numbers": [item.row_number for item in cluster.members],
                            "event_ids": [item.event_id for item in cluster.members],
                            "source_ids": [item.source_id for item in cluster.members],
                            "signature": cluster.cluster_signature_sha256,
                        }
                        for cluster in event_clustering.clusters
                    ],
                }
            )
        )
        run_seed = sha256_text(
            canonical_json(
                {
                    "analysis_mode": mode,
                    "clustering_result_sha256": clustering_result_sha256,
                    "cutoff_at": cutoff_at.isoformat(),
                    "llm_model_config": self.llm_model_config,
                    "news_sha256": full_batch.sha256,
                    "parsed_news_root_sha256": parsed_news_root_sha256,
                    "trade_date": trade_date.isoformat(),
                    "web_search": web_search,
                    "evidence_policy": evidence_policy.value,
                    "web_provider": self.settings.web_provider,
                    "shared_pre_retrieval_context_sha256": (shared_context_sha256),
                    "prediction_input_boundary_version": (
                        "SEALED_BLIND_INPUT.v3" if sealed_blind_input_manifest_sha256 is not None else None
                    ),
                    "sealed_blind_input_manifest_sha256": (sealed_blind_input_manifest_sha256),
                    "shared_d_minus_one_context_sha256": (shared_d_minus_one_context_sha256),
                    "shared_d_minus_one_candidate_universe_root_sha256": (
                        shared_d_minus_one_context.candidate_universe_root_sha256
                        if shared_d_minus_one_context is not None
                        else None
                    ),
                }
            )
        )
        open_world_inputs = open_world_cluster_inputs(event_clustering)
        news_texts = [item.representative_text for item in open_world_inputs]
        event_ids = [event_id for cluster in open_world_inputs for event_id in cluster.event_ids]
        if shared_context is not None:
            open_world_first_analysis = OpenWorldFirstAnalysis.model_validate(
                _resolve_and_read_shared_reference(
                    self.root,
                    shared_context.open_world_first_analysis,
                )
            )
            if (
                open_world_first_analysis.source_cluster_ids != shared_context.material_cluster_ids
                or open_world_first_analysis.analyzed_cluster_ids != shared_context.material_cluster_ids
                or open_world_first_analysis.uncovered_cluster_ids
            ):
                raise OpenWorldCoverageError("shared pre-retrieval open-world coverage drifted")
            open_world_prompt_hash = shared_context.prompt_hashes["open_world_map_reduce"]
            open_world_prompt_tokens = 0
            open_world_prompt_batch_hashes = [open_world_prompt_hash]
        else:
            (
                open_world_first_analysis,
                open_world_prompt_hash,
                open_world_prompt_tokens,
                open_world_prompt_batch_hashes,
            ) = await self._run_open_world_first_analysis(
                clusters=open_world_inputs,
                cutoff_at=cutoff_at,
            )
        first_pass_mechanisms = open_world_first_analysis.mechanisms
        web_queries = self._build_web_queries(batch.items)
        raw_retrieved_ids = self.retrieval.search_semantic(" ".join(web_queries), limit=20)
        retrieved_ids, excluded_retrieved_ids = self._filter_retrieved_ids_available_as_of(
            raw_retrieved_ids,
            cutoff_at=cutoff_at,
        )
        raw_retrieved_record_ids = self._search_memory_records(
            query=" ".join([*web_queries, *first_pass_mechanisms]),
            limit=20,
            filters={
                "training_eligible": True,
                "evidence_polarity": RecordEvidencePolarity.POSITIVE.value,
                "label_quality": "verified",
                "routing_disposition": RecordRoutingDisposition.REASONING.value,
            },
        )
        retrieved_record_ids, excluded_retrieved_record_ids = self._filter_retrieved_record_ids_available_as_of(
            raw_retrieved_record_ids,
            cutoff_at=cutoff_at,
        )
        manifest = ContextAssembler(
            self.root,
            shard_episode_count=self.settings.limits.shard_episode_count,
        ).assemble(
            mode=mode,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            run_seed=run_seed,
            retrieved_episode_ids=retrieved_ids,
            retrieved_record_ids=retrieved_record_ids,
            web_queries=web_queries,
        )
        manifest.evidence_policy = evidence_policy.value
        manifest.web_provider = self.settings.web_provider.strip().lower()
        manifest.web_required = web_required_for_policy(evidence_policy)
        manifest.news_file = relative_to_root(full_batch.path, self.root)
        manifest.news_sha256 = full_batch.sha256
        manifest.news_window_start_at = news_window_start_at
        manifest.news_window_end_at = cutoff_at
        manifest.news_row_count = full_batch.row_count
        manifest.included_news_row_count = batch.row_count
        manifest.excluded_news_row_count = full_batch.row_count - batch.row_count
        manifest.llm_model_config = {**self.llm_model_config, "analysis_mode": mode}
        manifest.event_clustering_result_sha256 = clustering_result_sha256
        if sealed_blind_input_manifest_sha256 is not None:
            manifest.bind_sealed_blind_input(manifest_sha256=sealed_blind_input_manifest_sha256)
        manifest.excluded_retrieved_episode_ids = excluded_retrieved_ids
        manifest.excluded_retrieved_record_ids = excluded_retrieved_record_ids
        if (
            shared_context is not None
            and shared_context_sha256 is not None
            and shared_context_manifest is not None
            and shared_context_manifest_path is not None
            and shared_context_manifest_sha256 is not None
        ):
            self._bind_shared_pre_retrieval_context(
                manifest=manifest,
                context=shared_context,
                context_sha256=shared_context_sha256,
                context_manifest=shared_context_manifest,
                context_manifest_path=shared_context_manifest_path,
                context_manifest_sha256=shared_context_manifest_sha256,
                parsed_news_root_sha256=parsed_news_root_sha256,
                event_clustering=event_clustering,
                open_world_analysis=open_world_first_analysis,
            )
        else:
            self._write_open_world_first_analysis_artifact(
                analysis=open_world_first_analysis,
                manifest=manifest,
                prompt_sha256=open_world_prompt_hash,
                cutoff_at=cutoff_at,
            )
            self._write_row_disposition_artifact(
                full_items=full_batch.items,
                included_items=batch.items,
                news_window_start_at=news_window_start_at,
                cutoff_at=cutoff_at,
                manifest=manifest,
            )
            self._write_event_cluster_artifact(
                result=event_clustering,
                cutoff_at=cutoff_at,
                manifest=manifest,
            )
            self._write_news_coverage_manifests(
                result=event_clustering,
                news_sha256=full_batch.sha256,
                trade_date=trade_date,
                cutoff_at=cutoff_at,
                manifest=manifest,
            )
        manifest.token_counts["open_world_first_analysis_prompt"] = open_world_prompt_tokens
        self._fail_if_brain_context_contains_unavailable_episodes(
            cutoff_at=cutoff_at,
            manifest=manifest,
        )

        if web_search:
            manifest.blind_context_mode = "CUTOFF_SAFE_WEB_BLIND"
            await self._collect_cutoff_safe_web_sources(
                manifest=manifest,
                cutoff_at=cutoff_at,
            )

        manifest.price_snapshot.source_name = (
            shared_d_minus_one_context.source_name
            if shared_d_minus_one_context is not None
            else self._blind_price_source_name()
        )
        manifest.price_snapshot.source_ref = (
            shared_d_minus_one_context.source_ref
            if shared_d_minus_one_context is not None
            else self._blind_price_source_ref()
        )
        if (
            shared_d_minus_one_context is not None
            and shared_d_minus_one_context_sha256 is not None
            and resolved_shared_d_minus_one_context_path is not None
        ):
            self._bind_shared_d_minus_one_context(
                manifest=manifest,
                context=shared_d_minus_one_context,
                context_sha256=shared_d_minus_one_context_sha256,
                context_path=resolved_shared_d_minus_one_context_path,
            )

        if shared_context is not None:
            _news_novelty_review = NewsNoveltyReview.model_validate(
                _resolve_and_read_shared_reference(
                    self.root,
                    shared_context.news_novelty_review,
                )
            )
            novelty_prompt_hash = shared_context.prompt_hashes["news_novelty_review"]
            novelty_prompt_tokens = 0
            novelty_prompt_batch_hashes = [novelty_prompt_hash]
        else:
            (
                _news_novelty_review,
                novelty_prompt_hash,
                novelty_prompt_tokens,
                novelty_prompt_batch_hashes,
            ) = await self._run_news_novelty_review(
                manifest=manifest,
                cutoff_at=cutoff_at,
            )
        manifest.token_counts["news_novelty_review_prompt"] = novelty_prompt_tokens
        sweep = MemorySweeper(
            self.root,
            shard_episode_count=self.settings.limits.shard_episode_count,
        ).sweep(
            mode=mode,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            run_id=manifest.run_id,
            current_news_texts=news_texts,
            first_pass_mechanisms=first_pass_mechanisms,
            model_config=self.llm_model_config,
            brain_version=manifest.brain_version,
            emit_legacy_contributions=False,
        )
        manifest.accepted_episode_count = sweep.accepted_episode_count
        manifest.swept_episode_count = len(sweep.swept_episode_ids)
        manifest.swept_episode_ids = sweep.swept_episode_ids
        manifest.accepted_record_count = sweep.accepted_record_count
        manifest.available_record_count = sweep.available_record_count
        manifest.available_record_ids = sweep.available_record_ids
        manifest.training_eligible_available_record_count = sweep.training_eligible_available_record_count
        manifest.training_eligible_available_record_ids = sweep.training_eligible_available_record_ids
        manifest.swept_record_count = len(sweep.swept_record_ids)
        manifest.swept_record_ids = sweep.swept_record_ids
        manifest.memory_coverage_manifest_artifact = sweep.memory_coverage_manifest_path
        manifest.memory_coverage_manifest_sha256 = sweep.memory_coverage_manifest_sha256
        manifest.memory_coverage_corpus_sha256 = sweep.corpus_manifest_sha256
        manifest.memory_coverage_cache_hit = sweep.memory_coverage_cache_hit
        manifest.memory_sweep_artifacts = sweep.artifact_paths
        manifest.record_sweep_artifacts = sweep.record_artifact_paths
        manifest.memory_sweep_artifact_hashes = {
            artifact_path: file_sha256(self.root / artifact_path) for artifact_path in sweep.artifact_paths
        }
        manifest.record_sweep_artifact_hashes = {
            artifact_path: file_sha256(self.root / artifact_path) for artifact_path in sweep.record_artifact_paths
        }
        manifest.memory_sweep_shard_count = sweep.shard_count
        manifest.record_sweep_shard_count = sweep.record_shard_count
        manifest.memory_sweep_cache_hits = sweep.cache_hits
        manifest.record_sweep_cache_hits = sweep.record_cache_hits
        manifest.token_counts.update(sweep.token_counts)
        manifest.token_counts["current_news"] = sum(len(text) for text in news_texts) // 4
        for error in sweep.errors:
            if error not in manifest.errors:
                manifest.errors.append(error)
        self._fail_if_exhaustive_coverage_incomplete(manifest)
        self._fail_if_memory_coverage_incomplete(manifest)
        if self.runtime_retrieval_variant == "v4":
            self._build_pre_candidate_beneficiary_graph_context(manifest=manifest)
            await self._maybe_build_daily_memory_context(
                manifest=manifest,
                prediction=None,
            )
        _semantic_plan, semantic_prompt_hash, semantic_prompt_tokens = await self._run_semantic_retrieval_plan(
            news_texts=news_texts,
            first_pass_mechanisms=first_pass_mechanisms,
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        self._write_semantic_retrieval_artifact(
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        self._write_semantic_cluster_coverage_artifact(
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        self._refresh_counterexample_record_ids_from_retrieval(manifest)
        manifest.token_counts["semantic_retrieval_plan_prompt"] = semantic_prompt_tokens
        _candidate_expansion, expansion_prompt_hash, expansion_prompt_tokens = await self._run_candidate_expansion(
            news_texts=news_texts,
            first_pass_mechanisms=first_pass_mechanisms,
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        manifest.token_counts["candidate_expansion_prompt"] = expansion_prompt_tokens

        prediction_retrieved_record_ids = self._prediction_retrieved_record_ids(manifest)
        positive_record_ids, negative_record_ids = self._prediction_record_polarities(prediction_retrieved_record_ids)
        prediction, blind_prompt_hash, blind_prompt_tokens = await self._generate_prediction(
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            news_texts=news_texts,
            event_ids=event_ids,
            retrieved_episode_ids=retrieved_ids,
            counterexample_episode_ids=manifest.counterexample_episode_ids,
            retrieved_record_ids=positive_record_ids,
            counterexample_record_ids=negative_record_ids,
            excluded_source_ids=[],
            first_pass_mechanisms=first_pass_mechanisms,
            context_payload={
                "run_id": manifest.run_id,
                "brain_version": manifest.brain_version,
                "compiler_mode": manifest.compiler_mode,
                "brain_compiler_provider": manifest.brain_compiler_provider,
                "brain_compiler_model": manifest.brain_compiler_model,
                "brain_compiler_catalog_only": manifest.brain_compiler_catalog_only,
                "accepted_episode_count": manifest.accepted_episode_count,
                "swept_episode_count": manifest.swept_episode_count,
                "swept_episode_ids": manifest.swept_episode_ids,
                "retrieved_episode_ids": manifest.retrieved_episode_ids,
                "excluded_retrieved_episode_ids": manifest.excluded_retrieved_episode_ids,
                "counterexample_episode_ids": manifest.counterexample_episode_ids,
                "retrieved_record_ids": manifest.retrieved_record_ids,
                "excluded_retrieved_record_ids": manifest.excluded_retrieved_record_ids,
                "counterexample_record_ids": manifest.counterexample_record_ids,
                "prediction_retrieved_record_ids": prediction_retrieved_record_ids,
                "positive_record_ids": positive_record_ids,
                "negative_record_ids": negative_record_ids,
                "accepted_record_count": manifest.accepted_record_count,
                "available_record_count": manifest.available_record_count,
                "training_eligible_available_record_count": (manifest.training_eligible_available_record_count),
                "memory_coverage_manifest": self._memory_coverage_context(manifest),
                "event_cluster_artifact": manifest.event_cluster_artifact,
                "event_cluster_summary": manifest.event_cluster_summary,
                "open_world_first_analysis_artifact": (manifest.open_world_first_analysis_artifact),
                "open_world_first_analysis_summary": (manifest.open_world_first_analysis_summary),
                "news_novelty_review_artifact": manifest.news_novelty_review_artifact,
                "news_novelty_review_summary": manifest.news_novelty_review_summary,
                "semantic_retrieval_plan_artifact": (manifest.semantic_retrieval_plan_artifact),
                "semantic_retrieval_artifact": manifest.semantic_retrieval_artifact,
                "semantic_retrieval_episode_ids": manifest.semantic_retrieval_episode_ids,
                "excluded_semantic_retrieval_episode_ids": (manifest.excluded_semantic_retrieval_episode_ids),
                "semantic_retrieval_record_ids": manifest.semantic_retrieval_record_ids,
                "excluded_semantic_retrieval_record_ids": (manifest.excluded_semantic_retrieval_record_ids),
                "semantic_retrieval_summary": manifest.semantic_retrieval_summary,
                "semantic_cluster_coverage_artifact": (manifest.semantic_cluster_coverage_artifact),
                "semantic_cluster_coverage_ids": manifest.semantic_cluster_coverage_ids,
                "semantic_cluster_coverage_missing_ids": (manifest.semantic_cluster_coverage_missing_ids),
                "semantic_cluster_coverage_promoted_record_ids": (
                    manifest.semantic_cluster_coverage_promoted_record_ids
                ),
                "semantic_cluster_coverage_summary": (manifest.semantic_cluster_coverage_summary),
                "candidate_expansion_artifact": manifest.candidate_expansion_artifact,
                "candidate_expansion_summary": manifest.candidate_expansion_summary,
                "retrieval_first_memory": self._candidate_generation_memory_context(manifest),
                "candidate_expansion_cluster_coverage_ids": (manifest.candidate_expansion_cluster_coverage_ids),
                "candidate_expansion_audit_only_cluster_ids": (manifest.candidate_expansion_audit_only_cluster_ids),
                "candidate_expansion_uncovered_cluster_ids": (manifest.candidate_expansion_uncovered_cluster_ids),
                "web_queries": manifest.web_queries,
                "web_sources": manifest.web_sources,
                "excluded_web_source_ids": manifest.excluded_web_source_ids,
                "web_source_artifact": manifest.web_source_artifact,
                "candidate_web_source_ids": manifest.candidate_web_source_ids,
                "candidate_verification_artifact": (manifest.candidate_verification_artifact),
                "candidate_verification_summary": (manifest.candidate_verification_summary),
                "excluded_candidate_web_source_ids": (manifest.excluded_candidate_web_source_ids),
                "candidate_web_check_artifact": manifest.candidate_web_check_artifact,
            },
        )
        manifest.token_counts["blind_analysis_prompt"] = blind_prompt_tokens
        prediction = prediction.model_copy(update={"context_manifest_id": manifest.run_id})
        d_minus_one_market_data = (
            shared_d_minus_one_context.model_dump(mode="json")
            if shared_d_minus_one_context is not None
            else self._collect_d_minus_one_market_data(
                candidates=prediction.candidates,
                manifest=manifest,
            )
        )
        if web_search:
            await self._collect_candidate_web_checks(
                prediction=prediction,
                manifest=manifest,
                cutoff_at=cutoff_at,
                d_minus_one_market_data=d_minus_one_market_data,
            )
        else:
            blind_subjects = self._candidate_web_check_subjects(
                prediction,
                manifest,
            )
            self._write_candidate_verification_artifact(
                manifest=manifest,
                subjects=blind_subjects,
                rows=[],
                excluded_rows=[],
                cutoff_at=cutoff_at,
                d_minus_one_market_data=d_minus_one_market_data,
            )
            manifest.candidate_web_check_summary = {
                "status": "CSV_MEMORY_ONLY_STRICT_ZERO_WEB",
                "subject_count": len(blind_subjects),
                "source_count": 0,
                "excluded_source_count": 0,
                "blind_web_search_call_count": 0,
            }
        red_team = await run_red_team_pass(
            root=self.root,
            llm=self.llm,
            prediction=prediction,
            manifest=manifest,
        )
        prediction = apply_red_team_findings(prediction, red_team.artifact)
        manifest.red_team_artifacts = [red_team.artifact_path]
        manifest.red_team_summary = {
            "candidate_count": red_team.artifact.candidate_count,
            "required_attack_checks": red_team.artifact.required_attack_checks,
            "required_attack_check_count": len(red_team.artifact.required_attack_checks),
            "finding_count": len(red_team.artifact.candidate_findings),
            "all_findings_passed_to_synthesis": all(
                finding.passed_to_synthesis and all(check.passed_to_synthesis for check in finding.attack_checks)
                for finding in red_team.artifact.candidate_findings
            ),
        }
        manifest.token_counts["red_team_prompt"] = red_team.prompt_token_estimate
        company_store = self._company_memory_store()
        company_delta_result = self._apply_company_memory_record_deltas(
            company_store,
            as_of=cutoff_at,
        )
        if company_delta_result.skipped_invalid_record_ids:
            manifest.errors.append(
                "invalid company_memory_delta records skipped: "
                + ", ".join(company_delta_result.skipped_invalid_record_ids)
            )
        company_memory_context = self._collect_company_memory_context(
            cutoff_at=cutoff_at,
            manifest=manifest,
        )
        market_memory_context = self._collect_market_memory_context(
            cutoff_at=cutoff_at,
            manifest=manifest,
        )
        self._build_beneficiary_graph_context(
            prediction=prediction,
            manifest=manifest,
            company_memory_context=company_memory_context,
        )
        if self.runtime_retrieval_variant == "v4" and manifest.daily_memory_context_artifact:
            self._bind_final_beneficiary_graph_to_daily_memory(manifest=manifest)
        elif self.runtime_retrieval_variant == "legacy":
            await self._maybe_build_daily_memory_context(
                manifest=manifest,
                prediction=prediction,
            )
        final_d_minus_one_market_data = d_minus_one_market_data
        if shared_d_minus_one_context is not None and shared_context is not None:
            projection = self._build_d_minus_one_prompt_projection(
                context=shared_d_minus_one_context,
                context_reference=shared_context.d_minus_one_safe_context,
                candidates=prediction.candidates,
            )
            final_d_minus_one_market_data = projection.model_dump(mode="json")
            manifest.bind_d_minus_one_prompt_projection(
                policy=projection.projection_policy,
                consumed_payload_sha256=sha256_text(canonical_json(final_d_minus_one_market_data)),
                projection_root_sha256=projection.projection_root_sha256,
                requested_ticker_count=len(projection.requested_tickers),
                snapshot_count=len(projection.snapshots),
                missing_ticker_count=len(projection.missing_tickers),
            )
        prediction, final_synthesis_prompt_hash, final_synthesis_prompt_tokens = await self._run_final_synthesis(
            prediction=prediction,
            manifest=manifest,
            news_texts=news_texts,
            event_ids=event_ids,
            retrieved_episode_ids=retrieved_ids,
            excluded_source_ids=[],
            first_pass_mechanisms=first_pass_mechanisms,
            red_team_artifact=red_team.artifact,
            d_minus_one_market_data=final_d_minus_one_market_data,
            company_memory_context=company_memory_context,
            market_memory_context=market_memory_context,
        )
        prediction = apply_red_team_findings(prediction, red_team.artifact)
        self._finalize_runtime_retrieval_traces(
            manifest=manifest,
            prediction=prediction,
        )
        agent_identity = self._current_agent_identity()
        if agent_identity:
            manifest.llm_model_config["agent_identity"] = agent_identity
        self._enforce_evidence_policy(manifest)
        self._write_source_ledger_artifact(
            news_items=batch.items,
            prediction=prediction,
            cutoff_at=cutoff_at,
            manifest=manifest,
        )
        manifest.token_counts["final_synthesis_prompt"] = final_synthesis_prompt_tokens
        prediction = self._seal(prediction)
        manifest.web_sources = sorted(set(manifest.web_sources))
        manifest.prompt_hashes["open_world_first_analysis"] = open_world_prompt_hash
        manifest.prompt_hashes["news_novelty_review"] = novelty_prompt_hash
        manifest.prompt_batch_hashes["open_world_first_analysis"] = open_world_prompt_batch_hashes
        manifest.prompt_batch_hashes["news_novelty_review"] = novelty_prompt_batch_hashes
        manifest.prompt_hashes["semantic_retrieval_plan"] = semantic_prompt_hash
        manifest.prompt_hashes["candidate_expansion"] = expansion_prompt_hash
        manifest.prompt_hashes["blind_analysis"] = blind_prompt_hash
        manifest.prompt_hashes["red_team_candidate_review"] = red_team.artifact.prompt_sha256
        manifest.prompt_hashes["final_synthesis"] = final_synthesis_prompt_hash

        prediction_dir = self.settings.path(self.settings.output_dirs.predictions)
        report_dir = self.settings.path(self.settings.output_dirs.reports)
        manifest_dir = self.settings.path(self.settings.output_dirs.manifests)
        prediction_path = prediction_dir / f"{trade_date.isoformat()}.json"
        report_path = report_dir / f"{trade_date.isoformat()}_preopen.md"
        run_output_dir = self.root / "runs" / "checkpoints" / "output_artifacts" / manifest.run_id
        run_prediction_path = run_output_dir / "blind_prediction.json"
        run_report_path = run_output_dir / "preopen_report.md"
        manifest_path = manifest_dir / f"{manifest.run_id}.json"
        run_prediction_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(run_prediction_path, prediction.model_dump(mode="json"))
        report_text = render_preopen_report(prediction, manifest)
        run_report_path.write_text(report_text, encoding="utf-8")
        manifest.prediction_artifact = run_prediction_path.relative_to(self.root).as_posix()
        manifest.prediction_sha256 = file_sha256(run_prediction_path)
        manifest.report_artifact = run_report_path.relative_to(self.root).as_posix()
        manifest.report_sha256 = sha256_text(report_text)
        self._write_blind_seal_artifacts(
            prediction=prediction,
            prediction_path=run_prediction_path,
            manifest=manifest,
        )
        write_json(prediction_path, prediction.model_dump(mode="json"))
        company_memory_attestation_key = (
            self.settings.env_value("NSLAB_PRODUCTION_PROMOTION_HMAC_KEY")
            if production_company_memory_attestation_required(self.root)
            else None
        )
        self._company_memory_store().upsert_from_candidates(
            prediction.candidates,
            prediction_path=run_prediction_path,
            known_at=prediction.cutoff_at,
            attestation_key=company_memory_attestation_key,
        )
        write_json(manifest_path, manifest.model_dump(mode="json"))
        warehouse = WarehouseStore(self.root)
        warehouse.write_prediction(prediction)
        if self._evaluation_memory_snapshot is None:
            warehouse.write_company_memory_from_files()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")
        return DailyAnalysis(
            run_id=manifest.run_id,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            created_at=now_kst(),
            mode=mode,
            blind_prediction=prediction,
            context_manifest=manifest,
            report_path=report_path.relative_to(self.root).as_posix(),
            prediction_path=prediction_path.relative_to(self.root).as_posix(),
        )

    async def _run_open_world_first_analysis(
        self,
        *,
        clusters: list[OpenWorldClusterInput],
        cutoff_at: datetime,
    ) -> tuple[OpenWorldFirstAnalysis, str, int, list[str]]:
        cluster_batches = self._open_world_cluster_batches(clusters)
        batch_count = max(1, len(cluster_batches))
        analyses: list[OpenWorldFirstAnalysis] = []
        prompt_hashes: list[str] = []
        token_count = 0
        for batch_index, cluster_batch in enumerate(cluster_batches, start=1):
            news_texts = [member_news for cluster in cluster_batch for member_news in cluster.member_news]
            event_ids = [event_id for cluster in cluster_batch for event_id in cluster.event_ids]
            cluster_ids = [cluster.cluster_id for cluster in cluster_batch]
            prompt = self._build_open_world_first_analysis_prompt(
                clusters=cluster_batch,
                cutoff_at=cutoff_at,
            )
            if len(prompt) > self.settings.limits.open_world_max_prompt_chars:
                raise OpenWorldCoverageError("open-world prompt exceeds the configured hard character budget")
            prompt_sha256 = sha256_text(prompt)
            prompt_hashes.append(prompt_sha256)
            token_count += count_provider_tokens(self.llm, prompt)
            try:
                analysis = await self.llm.generate_structured(
                    prompt=prompt,
                    response_model=OpenWorldFirstAnalysis,
                    purpose=(
                        "open_world_first_analysis"
                        if batch_count == 1
                        else f"open_world_first_analysis.batch_{batch_index:04d}"
                    ),
                )
            except NotImplementedError:
                analysis = self._fallback_open_world_first_analysis(
                    news_texts=news_texts,
                    event_ids=event_ids,
                    cutoff_at=cutoff_at,
                    prompt_sha256=prompt_sha256,
                ).model_copy(
                    update={
                        "source_cluster_ids": cluster_ids,
                        "analyzed_cluster_ids": cluster_ids,
                        "uncovered_cluster_ids": [],
                        "analysis_batch_count": 1,
                        "cluster_findings": self._fallback_cluster_findings(cluster_batch),
                    }
                )
            analyses.append(
                self._normalize_open_world_first_analysis(
                    analysis,
                    news_texts=news_texts,
                    event_ids=event_ids,
                    cluster_ids=cluster_ids,
                    cutoff_at=cutoff_at,
                    prompt_sha256=prompt_sha256,
                )
            )
        if not analyses:
            empty_prompt_hash = sha256_text(canonical_json([]))
            analyses.append(
                self._fallback_open_world_first_analysis(
                    news_texts=[],
                    event_ids=[],
                    cutoff_at=cutoff_at,
                    prompt_sha256=empty_prompt_hash,
                ).model_copy(
                    update={
                        "source_cluster_ids": [],
                        "analyzed_cluster_ids": [],
                        "uncovered_cluster_ids": [],
                        "analysis_batch_count": 0,
                    }
                )
            )
        aggregate_prompt_sha256 = (
            prompt_hashes[0] if len(prompt_hashes) == 1 else sha256_text(canonical_json(prompt_hashes))
        )
        merged = self._merge_open_world_analyses(
            analyses,
            clusters=clusters,
            cutoff_at=cutoff_at,
            prompt_sha256=aggregate_prompt_sha256,
            analysis_batch_count=len(prompt_hashes),
        )
        return merged, aggregate_prompt_sha256, token_count, prompt_hashes

    def _open_world_cluster_batches(
        self,
        clusters: list[OpenWorldClusterInput],
    ) -> list[list[OpenWorldClusterInput]]:
        max_clusters = max(1, self.settings.limits.open_world_cluster_batch_size)
        max_chars = max(1, self.settings.limits.open_world_max_prompt_chars)
        batches: list[list[OpenWorldClusterInput]] = []
        current: list[OpenWorldClusterInput] = []
        for cluster in clusters:
            single_prompt_chars = len(
                self._build_open_world_first_analysis_prompt(
                    clusters=[cluster],
                    cutoff_at=datetime(2000, 1, 1, tzinfo=KST),
                )
            )
            if single_prompt_chars > max_chars:
                raise OpenWorldCoverageError("one event cluster exceeds the bounded open-world prompt budget")
            tentative = [*current, cluster]
            tentative_chars = len(
                self._build_open_world_first_analysis_prompt(
                    clusters=tentative,
                    cutoff_at=datetime(2000, 1, 1, tzinfo=KST),
                )
            )
            if current and (len(current) >= max_clusters or tentative_chars > max_chars):
                batches.append(current)
                current = []
            current.append(cluster)
        if current:
            batches.append(current)
        return batches

    def _merge_open_world_analyses(
        self,
        analyses: list[OpenWorldFirstAnalysis],
        *,
        clusters: list[OpenWorldClusterInput],
        cutoff_at: datetime,
        prompt_sha256: str,
        analysis_batch_count: int,
    ) -> OpenWorldFirstAnalysis:
        source_cluster_ids = [cluster.cluster_id for cluster in clusters]
        analyzed_cluster_ids = _unique_preserving_order(
            [cluster_id for analysis in analyses for cluster_id in analysis.analyzed_cluster_ids]
        )
        uncovered_cluster_ids = sorted(set(source_cluster_ids) - set(analyzed_cluster_ids))

        def merged_list(field_name: str) -> list[str]:
            return _unique_preserving_order(
                [
                    value
                    for analysis in analyses
                    for value in getattr(analysis, field_name)
                    if isinstance(value, str) and value.strip()
                ]
            )

        return OpenWorldFirstAnalysis(
            run_id="RUN-open-world-first-analysis-pending",
            prompt_version=OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
            prompt_sha256=prompt_sha256,
            created_at=now_kst(),
            cutoff_at=cutoff_at,
            event_ids=_unique_preserving_order([event_id for cluster in clusters for event_id in cluster.event_ids]),
            source_cluster_ids=source_cluster_ids,
            analyzed_cluster_ids=analyzed_cluster_ids,
            uncovered_cluster_ids=uncovered_cluster_ids,
            analysis_batch_count=analysis_batch_count,
            event_clusters=merged_list("event_clusters"),
            direct_company_events=merged_list("direct_company_events"),
            policy_industry_events=merged_list("policy_industry_events"),
            mechanisms=merged_list("mechanisms"),
            beneficiary_transmission_paths=merged_list("beneficiary_transmission_paths"),
            narrative_conversion_points=merged_list("narrative_conversion_points"),
            direct_candidates=merged_list("direct_candidates"),
            potential_sectors=merged_list("potential_sectors"),
            beneficiary_investigation_questions=merged_list("beneficiary_investigation_questions"),
            uncertainties=merged_list("uncertainties"),
            cluster_findings=[finding for analysis in analyses for finding in analysis.cluster_findings],
            notes=merged_list("notes"),
        )

    def _build_open_world_first_analysis_prompt(
        self,
        *,
        clusters: list[OpenWorldClusterInput],
        cutoff_at: datetime,
    ) -> str:
        payload = {
            "schema": "nslab.open_world_first_analysis.v2",
            "prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
            "cutoff_at": cutoff_at.isoformat(),
            "current_event_clusters": [
                {
                    "cluster_id": cluster.cluster_id,
                    "event_ids": list(cluster.event_ids),
                    "row_numbers": list(cluster.row_numbers),
                    "representative_news": cluster.representative_text,
                    "member_news": list(cluster.member_news),
                }
                for cluster in clusters
            ],
            "required_cluster_ids": [cluster.cluster_id for cluster in clusters],
            "forbidden_inputs": [
                "past research search results",
                "semantic retrieval hits",
                "D-day prices or outcomes",
                "cutoff-after evidence",
            ],
            "required_fields": [
                "cluster_findings",
                "event_clusters",
                "direct_company_events",
                "policy_industry_events",
                "mechanisms",
                "beneficiary_transmission_paths",
                "narrative_conversion_points",
                "direct_candidates",
                "potential_sectors",
                "beneficiary_investigation_questions",
                "uncertainties",
            ],
        }
        return (
            "Run Pass 0 open-world first read as OpenWorldFirstAnalysis. Use only "
            "current_event_clusters, before any past research or semantic retrieval. "
            "Every required_cluster_id must be analyzed exactly once in this batch. "
            "Do not fit candidates to memory. Generate free-form mechanisms, possible "
            "direct candidates, sector hypotheses, beneficiary investigation questions, "
            "and uncertainties without hardcoded ticker/theme mappings.\n"
            "---OPEN_WORLD_FIRST_ANALYSIS_PAYLOAD---\n"
            f"{canonical_json(payload)}"
        )

    def _normalize_open_world_first_analysis(
        self,
        analysis: OpenWorldFirstAnalysis,
        *,
        news_texts: list[str],
        event_ids: list[str],
        cluster_ids: list[str],
        cutoff_at: datetime,
        prompt_sha256: str,
    ) -> OpenWorldFirstAnalysis:
        if (
            analysis.source_cluster_ids != cluster_ids
            or analysis.uncovered_cluster_ids
            or analysis.analysis_batch_count != 1
            or len(analysis.analyzed_cluster_ids) != len(cluster_ids)
            or len(set(analysis.analyzed_cluster_ids)) != len(cluster_ids)
        ):
            raise OpenWorldCoverageError("open-world Pass 0 cluster coverage does not match the dispatched batch")

        def cleaned(values: list[str]) -> list[str]:
            return _unique_preserving_order([" ".join(value.split()) for value in values if value.strip()])

        event_clusters = cleaned(analysis.event_clusters)
        mechanisms = cleaned(analysis.mechanisms)
        uncertainties = cleaned(analysis.uncertainties)
        finding_ids = [finding.cluster_id for finding in analysis.cluster_findings]
        if finding_ids != cluster_ids:
            raise OpenWorldCoverageError("open-world Pass 0 cluster findings do not match the dispatched batch")
        if cluster_ids and not event_clusters:
            raise OpenWorldCoverageError("open-world Pass 0 omitted event-cluster summaries for a material batch")
        if cluster_ids and not mechanisms and not uncertainties:
            raise OpenWorldCoverageError(
                "open-world Pass 0 omitted both mechanisms and uncertainties for a material batch"
            )

        notes = list(analysis.notes)
        if analysis.analyzed_cluster_ids != cluster_ids:
            model_echo_sha256 = sha256_text(
                canonical_json(
                    {
                        "analyzed_cluster_ids": analysis.analyzed_cluster_ids,
                        "required_cluster_ids": cluster_ids,
                    }
                )
            )
            notes.append(
                "Analyzed-cluster identity is bound from the deterministic batch ledger; "
                f"the non-authoritative model echo is committed as {model_echo_sha256}."
            )

        return analysis.model_copy(
            update={
                "run_id": analysis.run_id,
                "prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
                "prompt_sha256": prompt_sha256,
                "cutoff_at": cutoff_at,
                "event_ids": _unique_preserving_order(event_ids),
                "source_cluster_ids": cluster_ids,
                "analyzed_cluster_ids": cluster_ids,
                "uncovered_cluster_ids": [],
                "analysis_batch_count": 1,
                "cluster_findings": analysis.cluster_findings,
                "event_clusters": event_clusters,
                "direct_company_events": cleaned(analysis.direct_company_events),
                "policy_industry_events": cleaned(analysis.policy_industry_events),
                "mechanisms": mechanisms,
                "beneficiary_transmission_paths": cleaned(analysis.beneficiary_transmission_paths),
                "narrative_conversion_points": cleaned(analysis.narrative_conversion_points),
                "direct_candidates": cleaned(analysis.direct_candidates),
                "potential_sectors": cleaned(analysis.potential_sectors),
                "beneficiary_investigation_questions": cleaned(analysis.beneficiary_investigation_questions),
                "uncertainties": uncertainties,
                "notes": cleaned(notes),
            }
        )

    def _fallback_open_world_first_analysis(
        self,
        *,
        news_texts: list[str],
        event_ids: list[str],
        cutoff_at: datetime,
        prompt_sha256: str,
    ) -> OpenWorldFirstAnalysis:
        mechanisms = self._infer_first_pass_mechanisms(news_texts)
        mentions = self.fallback_llm.extract_company_mentions(news_texts, limit=6)
        event_clusters = [
            f"current-news cluster {index}: {text.splitlines()[0][:120]}"
            for index, text in enumerate(news_texts, start=1)
            if text.strip()
        ] or ["current-news batch requires open-world event clustering"]
        direct_company_events = [
            f"{mention}: directly mentioned current-news event requires listing and economic verification"
            for mention in mentions
        ] or ["no direct company mention extracted before web/company verification"]
        transmission_paths = [
            f"{mechanism} -> direct, indirect, and market-memory beneficiary investigation" for mechanism in mechanisms
        ]
        return OpenWorldFirstAnalysis(
            run_id="RUN-open-world-first-analysis-pending",
            prompt_version=OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
            prompt_sha256=prompt_sha256,
            created_at=now_kst(),
            cutoff_at=cutoff_at,
            event_ids=_unique_preserving_order(event_ids),
            event_clusters=event_clusters,
            direct_company_events=direct_company_events,
            policy_industry_events=[
                "current catalyst may form a policy or industry narrative; verify breadth and novelty"
            ],
            mechanisms=mechanisms,
            beneficiary_transmission_paths=transmission_paths,
            narrative_conversion_points=[
                "current evidence becomes market narrative only if cutoff-safe sources support novelty and breadth"
            ],
            direct_candidates=mentions or ["UNVERIFIED_DIRECT_CANDIDATE"],
            potential_sectors=["open-world sector hypothesis to be named by LLM and verified by sources"],
            beneficiary_investigation_questions=[
                "Which listed entities have direct, supply-chain, infrastructure, regional, or market-memory exposure?",
                "Which candidates fail directness, novelty, dilution, or D-1 absorption checks?",
            ],
            uncertainties=[
                "listing status and ticker precision are unverified at Pass 0",
                "economic ownership and customer attribution require cutoff-safe evidence",
                "D-1 market absorption must be checked without D-day prices",
            ],
            notes=["Fallback Pass 0 used current news only and did not inspect past research."],
        )

    def _fallback_cluster_findings(
        self,
        clusters: list[OpenWorldClusterInput],
    ) -> list[OpenWorldClusterFinding]:
        findings: list[OpenWorldClusterFinding] = []
        for cluster in clusters:
            mechanisms = self._infer_first_pass_mechanisms(list(cluster.member_news))
            mentions = self.fallback_llm.extract_company_mentions(
                list(cluster.member_news),
                limit=6,
            )
            findings.append(
                OpenWorldClusterFinding(
                    cluster_id=cluster.cluster_id,
                    event_summary=cluster.representative_text.splitlines()[0][:240],
                    mechanisms=mechanisms,
                    direct_candidates=mentions,
                    uncertainties=["fallback cluster semantics require cutoff-safe provider verification"],
                )
            )
        return findings

    def _write_open_world_first_analysis_artifact(
        self,
        *,
        analysis: OpenWorldFirstAnalysis,
        manifest: ContextManifest,
        prompt_sha256: str,
        cutoff_at: datetime,
    ) -> None:
        normalized = analysis.model_copy(
            update={
                "run_id": manifest.run_id,
                "prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
                "prompt_sha256": prompt_sha256,
                "cutoff_at": cutoff_at,
            }
        )
        artifact_relative = (
            Path("runs")
            / "checkpoints"
            / "open_world_first_analysis"
            / manifest.run_id
            / "open_world_first_analysis.json"
        )
        artifact_path = self.root / artifact_relative
        write_json(artifact_path, normalized.model_dump(mode="json"))
        artifact_text = artifact_path.read_text(encoding="utf-8")
        manifest.open_world_first_analysis_artifact = artifact_relative.as_posix()
        manifest.open_world_first_analysis_sha256 = sha256_text(artifact_text)
        manifest.open_world_first_analysis_summary = {
            "source_cluster_count": len(normalized.source_cluster_ids),
            "analyzed_cluster_count": len(normalized.analyzed_cluster_ids),
            "uncovered_cluster_count": len(normalized.uncovered_cluster_ids),
            "analysis_batch_count": normalized.analysis_batch_count,
            "cluster_finding_count": len(normalized.cluster_findings),
            "event_cluster_count": len(normalized.event_clusters),
            "direct_company_event_count": len(normalized.direct_company_events),
            "policy_industry_event_count": len(normalized.policy_industry_events),
            "mechanism_count": len(normalized.mechanisms),
            "transmission_path_count": len(normalized.beneficiary_transmission_paths),
            "narrative_conversion_point_count": len(normalized.narrative_conversion_points),
            "direct_candidate_count": len(normalized.direct_candidates),
            "potential_sector_count": len(normalized.potential_sectors),
            "investigation_question_count": len(normalized.beneficiary_investigation_questions),
            "uncertainty_count": len(normalized.uncertainties),
        }

    def _read_open_world_first_analysis_context(
        self,
        manifest: ContextManifest,
    ) -> dict[str, Any]:
        if not manifest.open_world_first_analysis_artifact:
            return {}
        payload = read_json(self.root / manifest.open_world_first_analysis_artifact)
        return payload if isinstance(payload, dict) else {}

    def _write_row_disposition_artifact(
        self,
        *,
        full_items: list[NewsItem],
        included_items: list[NewsItem],
        news_window_start_at: datetime,
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> None:
        included_event_ids = {item.event_id for item in included_items}
        rows: list[dict[str, Any]] = []
        summary = {
            "total_rows": len(full_items),
            "included_in_news_window": 0,
            "included_before_cutoff": 0,
            "excluded_before_window": 0,
            "excluded_after_cutoff": 0,
            "missing_collected_at": 0,
        }
        for item in full_items:
            in_news_window = news_window_start_at <= item.published_at <= cutoff_at
            included = item.event_id in included_event_ids and in_news_window
            if included:
                disposition = "INCLUDED_IN_NEWS_WINDOW"
                reason = "news_window_start_at <= published_at <= cutoff_at"
                summary["included_in_news_window"] += 1
                summary["included_before_cutoff"] += 1
            elif item.published_at > cutoff_at:
                disposition = "EXCLUDED_AFTER_CUTOFF"
                reason = "published_at > cutoff_at"
                summary["excluded_after_cutoff"] += 1
            else:
                disposition = "EXCLUDED_BEFORE_WINDOW"
                reason = "published_at < news_window_start_at"
                summary["excluded_before_window"] += 1
            if item.collected_at is None:
                summary["missing_collected_at"] += 1
            rows.append(
                {
                    "schema_version": "nslab.row_disposition.v1",
                    "run_id": manifest.run_id,
                    "row_number": item.row_number,
                    "event_id": item.event_id,
                    "published_at": item.published_at.isoformat(),
                    "collected_at": (item.collected_at.isoformat() if item.collected_at is not None else None),
                    "collected_at_present": item.collected_at is not None,
                    "news_window_start_at": news_window_start_at.isoformat(),
                    "cutoff_at": cutoff_at.isoformat(),
                    "within_news_window": in_news_window,
                    "source_id": item.source_id,
                    "disposition": disposition,
                    "eligible_for_blind_evidence": included,
                    "reason": reason,
                    "title_sha256": sha256_text(item.title),
                    "body_sha256": sha256_text(item.body),
                    "title_chars": len(item.title),
                    "body_chars": len(item.body),
                    "provenance_source_ids": [provenance.source_id for provenance in item.provenance],
                }
            )
        artifact_relative = Path("runs") / "checkpoints" / "row_disposition" / manifest.run_id / "row_disposition.jsonl"
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        coverage_ratio = len(rows) / len(full_items) if full_items else 1.0
        manifest.row_disposition_artifact = artifact_relative.as_posix()
        manifest.row_disposition_sha256 = sha256_text(payload)
        manifest.row_disposition_coverage_ratio = coverage_ratio
        manifest.row_disposition_summary = {
            **summary,
            "coverage_ratio": coverage_ratio,
        }

    def _write_event_cluster_artifact(
        self,
        *,
        result: EventClusteringResult,
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> None:
        rows: list[dict[str, Any]] = []
        for cluster_index, cluster in enumerate(result.material_clusters, start=1):
            items = list(cluster.members)
            published = sorted(item.published_at for item in items)
            cutoff_safe_published = [value for value in published if value <= cutoff_at]
            rows.append(
                {
                    "schema_version": "nslab.news_event_cluster.v1",
                    "run_id": manifest.run_id,
                    "cluster_id": cluster.cluster_id,
                    "cluster_index": cluster_index,
                    "cluster_method": result.clustering_version,
                    "cluster_key_sha256": cluster.cluster_signature_sha256,
                    "row_numbers": [item.row_number for item in items],
                    "event_ids": [item.event_id for item in items],
                    "source_ids": [item.source_id for item in items],
                    "row_count": len(items),
                    "exact_duplicate_count": cluster.exact_duplicate_count,
                    "semantic_duplicate_count": cluster.semantic_duplicate_count,
                    "minimum_semantic_similarity": (cluster.minimum_semantic_similarity),
                    "disposition": cluster.disposition,
                    "eligible_for_blind_evidence": True,
                    "first_published_at": published[0].isoformat(),
                    "last_published_at_before_cutoff": (
                        max(cutoff_safe_published).isoformat() if cutoff_safe_published else None
                    ),
                    "cutoff_at": cutoff_at.isoformat(),
                    "time_verified": bool(cutoff_safe_published) and max(cutoff_safe_published) <= cutoff_at,
                    "representative_title_sha256": sha256_text(cluster.representative.title),
                    "representative_body_sha256": sha256_text(cluster.representative.body),
                    "representative_title_excerpt": (cluster.representative.title[:240]),
                    "representative_body_excerpt": cluster.representative.body[:600],
                    "member_news_excerpts": [
                        {
                            "row_number": item.row_number,
                            "event_id": item.event_id,
                            "title_sha256": sha256_text(item.title),
                            "body_sha256": sha256_text(item.body),
                            "title_excerpt": item.title[:240],
                            "body_excerpt": item.body[:600],
                        }
                        for item in items
                    ],
                    "novelty": "unclear",
                    "novelty_basis": (
                        "Cutoff-safe semantic clustering groups duplicate coverage only; "
                        "final novelty still requires LLM/web review."
                    ),
                    "requires_llm_novelty_review": True,
                }
            )
        artifact_relative = Path("runs") / "checkpoints" / "event_clusters" / manifest.run_id / "event_clusters.jsonl"
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        exact_duplicate_cluster_count = sum(1 for row in rows if int(row["exact_duplicate_count"]) > 0)
        semantic_duplicate_cluster_count = sum(1 for row in rows if int(row["semantic_duplicate_count"]) > 0)
        manifest.event_cluster_artifact = artifact_relative.as_posix()
        manifest.event_cluster_sha256 = sha256_text(payload)
        manifest.event_cluster_count = len(rows)
        manifest.event_cluster_summary = {
            "source_row_count": result.cutoff_safe_row_count,
            "all_input_row_count": result.input_row_count,
            "audit_only_row_count": result.audit_only_row_count,
            "cluster_count": len(rows),
            "exact_duplicate_count": sum(int(row["exact_duplicate_count"]) for row in rows),
            "exact_duplicate_cluster_count": exact_duplicate_cluster_count,
            "semantic_duplicate_count": sum(int(row["semantic_duplicate_count"]) for row in rows),
            "semantic_duplicate_cluster_count": semantic_duplicate_cluster_count,
            "cluster_method": result.clustering_version,
            "embedding_method": result.embedding_method,
            "embedding_status": result.embedding_status,
            "embedding_provider": result.embedding_provider,
            "embedding_model": result.embedding_model,
            "embedding_revision": result.embedding_revision,
            "embedding_artifact_sha256": result.embedding_artifact_sha256,
            "embedding_dimensions": result.embedding_dimensions,
            "embedding_fallback_policy": result.embedding_fallback_policy,
            "deterministic_fallback_used": result.deterministic_fallback_used,
            "embedding_retry_count": result.embedding_retry_count,
            "embedding_failure_type": result.embedding_failure_type,
            "production_runtime_identity": result.production_runtime_identity,
            "warnings": list(result.warnings),
            "novelty_review_required": True,
        }

    def _write_news_coverage_manifests(
        self,
        *,
        result: EventClusteringResult,
        news_sha256: str,
        trade_date: date,
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> None:
        coverage_rows: list[NewsRowCoverage] = []
        cluster_entries: list[EventClusterEntry] = []
        for cluster in result.clusters:
            for index, item in enumerate(cluster.members):
                disposition = cluster.disposition if index == 0 else "DUPLICATE"
                coverage_rows.append(
                    NewsRowCoverage(
                        row_number=item.row_number,
                        event_id=item.event_id,
                        source_id=item.source_id,
                        primary_cluster_id=(cluster.cluster_id if disposition != "DUPLICATE" else None),
                        duplicate_parent_cluster_id=(cluster.cluster_id if disposition == "DUPLICATE" else None),
                        disposition=disposition,
                    )
                )
            cluster_entries.append(
                EventClusterEntry(
                    cluster_id=cluster.cluster_id,
                    representative_event_id=cluster.representative.event_id,
                    member_event_ids=[item.event_id for item in cluster.members],
                    member_source_ids=[item.source_id for item in cluster.members],
                    member_row_numbers=[item.row_number for item in cluster.members],
                    disposition=cluster.disposition,
                    exact_duplicate_count=cluster.exact_duplicate_count,
                    semantic_duplicate_count=cluster.semantic_duplicate_count,
                    cluster_signature_sha256=cluster.cluster_signature_sha256,
                )
            )
        coverage_rows.sort(key=lambda row: row.row_number)
        disposition_counts = dict(sorted(Counter(row.disposition for row in coverage_rows).items()))
        coverage_hash = sha256_text(canonical_json([row.model_dump(mode="json") for row in coverage_rows]))
        coverage_manifest = NewsCoverageManifest(
            run_id=manifest.run_id,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            input_news_sha256=news_sha256,
            input_row_count=result.input_row_count,
            covered_row_count=len(coverage_rows),
            missing_row_count=result.input_row_count - len(coverage_rows),
            duplicate_assignment_count=disposition_counts.get("DUPLICATE", 0),
            disposition_counts=disposition_counts,
            row_coverage_sha256=coverage_hash,
            rows=coverage_rows,
        )
        event_manifest = EventClusterManifest(
            run_id=manifest.run_id,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            clustering_version=result.clustering_version,
            embedding_provider=result.embedding_method,
            embedding_status=result.embedding_status,
            embedding_model=result.embedding_model,
            embedding_revision=result.embedding_revision,
            embedding_artifact_sha256=result.embedding_artifact_sha256,
            embedding_dimensions=result.embedding_dimensions,
            embedding_fallback_policy=result.embedding_fallback_policy,
            deterministic_fallback_used=result.deterministic_fallback_used,
            embedding_retry_count=result.embedding_retry_count,
            embedding_failure_type=result.embedding_failure_type,
            production_runtime_identity=result.production_runtime_identity,
            embedding_batch_size=self.settings.limits.event_cluster_embedding_batch_size,
            similarity_threshold=self.settings.limits.event_cluster_similarity_threshold,
            max_semantic_variants=self.settings.limits.event_cluster_max_semantic_variants,
            input_row_count=result.input_row_count,
            cluster_count=len(cluster_entries),
            material_cluster_count=len(result.material_clusters),
            unassigned_row_count=0,
            duplicate_assignment_count=0,
            clusters=cluster_entries,
        )
        base = Path("runs") / "checkpoints" / "event_clusters" / manifest.run_id
        coverage_relative = base / "news_coverage_manifest.json"
        event_relative = base / "event_cluster_manifest.json"
        write_json(
            self.root / coverage_relative,
            coverage_manifest.model_dump(mode="json"),
        )
        write_json(self.root / event_relative, event_manifest.model_dump(mode="json"))
        manifest.news_coverage_manifest_artifact = coverage_relative.as_posix()
        manifest.news_coverage_manifest_sha256 = sha256_text(
            (self.root / coverage_relative).read_text(encoding="utf-8")
        )
        manifest.event_cluster_manifest_artifact = event_relative.as_posix()
        manifest.event_cluster_manifest_sha256 = sha256_text((self.root / event_relative).read_text(encoding="utf-8"))

    async def _run_news_novelty_review(
        self,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> tuple[NewsNoveltyReview, str, int, list[str]]:
        cluster_rows = self._read_event_cluster_context(manifest)
        batch_size = max(1, self.settings.limits.novelty_cluster_batch_size)
        batch_count = max(1, (len(cluster_rows) + batch_size - 1) // batch_size)
        partial_reviews: list[NewsNoveltyReview] = []
        prompt_hashes: list[str] = []
        token_count = 0
        for batch_index, start in enumerate(range(0, len(cluster_rows), batch_size), start=1):
            batch_rows = cluster_rows[start : start + batch_size]
            prompt = self._build_news_novelty_review_prompt(
                cluster_rows=batch_rows,
                manifest=manifest,
                cutoff_at=cutoff_at,
            )
            prompt_sha256 = sha256_text(prompt)
            prompt_hashes.append(prompt_sha256)
            token_count += count_provider_tokens(self.llm, prompt)
            try:
                review = await self.llm.generate_structured(
                    prompt=prompt,
                    response_model=NewsNoveltyReview,
                    purpose=(
                        "news_novelty_review" if batch_count == 1 else f"news_novelty_review.batch_{batch_index:04d}"
                    ),
                )
            except NotImplementedError:
                review = self._fallback_news_novelty_review_for_rows(
                    cluster_rows=batch_rows,
                    manifest=manifest,
                    cutoff_at=cutoff_at,
                    prompt_sha256=prompt_sha256,
                )
            partial_reviews.append(
                self._normalize_news_novelty_review(
                    review,
                    manifest=manifest,
                    cutoff_at=cutoff_at,
                    prompt_sha256=prompt_sha256,
                    cluster_rows=batch_rows,
                )
            )
        aggregate_prompt_sha256 = (
            prompt_hashes[0] if len(prompt_hashes) == 1 else sha256_text(canonical_json(prompt_hashes))
        )
        findings = sorted(
            [finding for review in partial_reviews for finding in review.findings],
            key=lambda item: item.cluster_index,
        )
        normalized = NewsNoveltyReview(
            run_id=manifest.run_id,
            prompt_version=NEWS_NOVELTY_REVIEW_PROMPT_VERSION,
            prompt_sha256=aggregate_prompt_sha256,
            created_at=now_kst(),
            cutoff_at=cutoff_at,
            review_mode=manifest.blind_context_mode,
            cluster_count=len(cluster_rows),
            reviewed_cluster_count=len(findings),
            findings=findings,
            excluded_after_cutoff_source_ids=manifest.excluded_web_source_ids,
            notes=_unique_preserving_order([note for review in partial_reviews for note in review.notes]),
        )
        artifact_relative = (
            Path("runs") / "checkpoints" / "news_novelty_reviews" / manifest.run_id / "news_novelty_review.json"
        )
        artifact_path = self.root / artifact_relative
        write_json(artifact_path, normalized.model_dump(mode="json"))
        artifact_text = artifact_path.read_text(encoding="utf-8")
        novelty_counts = {
            label.value: sum(1 for finding in normalized.findings if finding.novelty == label)
            for label in NewsNoveltyLabel
        }
        manifest.news_novelty_review_artifact = artifact_relative.as_posix()
        manifest.news_novelty_review_sha256 = sha256_text(artifact_text)
        manifest.news_novelty_review_count = normalized.reviewed_cluster_count
        manifest.news_novelty_review_summary = {
            "cluster_count": normalized.cluster_count,
            "reviewed_cluster_count": normalized.reviewed_cluster_count,
            "review_mode": normalized.review_mode,
            "novelty_counts": novelty_counts,
            "time_verified_count": sum(1 for finding in normalized.findings if finding.time_verified),
            "excluded_after_cutoff_source_count": len(normalized.excluded_after_cutoff_source_ids),
        }
        return normalized, aggregate_prompt_sha256, token_count, prompt_hashes

    def _build_news_novelty_review_prompt(
        self,
        *,
        cluster_rows: list[dict[str, Any]],
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> str:
        payload = {
            "schema": "nslab.news_novelty_review.v1",
            "prompt_version": NEWS_NOVELTY_REVIEW_PROMPT_VERSION,
            "run_id": manifest.run_id,
            "cutoff_at": cutoff_at.isoformat(),
            "review_mode": manifest.blind_context_mode,
            "current_news": [
                "\n".join(
                    [
                        str(row.get("representative_title_excerpt", "")),
                        str(row.get("representative_body_excerpt", "")),
                    ]
                )
                for row in cluster_rows
            ],
            "event_clusters": cluster_rows,
            "cutoff_safe_web_sources": self._read_web_source_context(manifest),
            "excluded_after_cutoff_source_ids": manifest.excluded_web_source_ids,
            "required_checks": [
                "first_public_evidence_at",
                "after_hours_new_disclosure",
                "recycled_news",
                "contract_stage",
                "attributable_amount",
                "customer",
                "period",
                "approval_stage",
                "dilution_or_financing_risks",
            ],
        }
        return (
            "Review pre-open news event clusters for novelty and directness as "
            "NewsNoveltyReview. Use only current_news, event_clusters, and "
            "cutoff_safe_web_sources. Do not use cutoff-after evidence. Preserve every "
            "cluster_id in the output and cite only provided evidence_source_ids. Mark "
            "uncertain fields as unclear instead of guessing.\n"
            "---NEWS_NOVELTY_REVIEW_PAYLOAD---\n"
            f"{canonical_json(payload)}"
        )

    def _normalize_news_novelty_review(
        self,
        review: NewsNoveltyReview,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
        prompt_sha256: str,
        cluster_rows: list[dict[str, Any]] | None = None,
    ) -> NewsNoveltyReview:
        effective_cluster_rows = (
            cluster_rows if cluster_rows is not None else self._read_event_cluster_context(manifest)
        )
        cluster_by_id = {
            str(row["cluster_id"]): row
            for row in effective_cluster_rows
            if isinstance(row, dict) and isinstance(row.get("cluster_id"), str)
        }
        allowed_source_ids = self._allowed_news_novelty_source_ids(effective_cluster_rows, manifest)
        normalized_findings: list[NewsNoveltyFinding] = []
        seen_cluster_ids: set[str] = set()
        for finding in review.findings:
            cluster_row = cluster_by_id.get(finding.cluster_id)
            if cluster_row is None:
                raise ValueError(f"news novelty review referenced unknown cluster_id: {finding.cluster_id}")
            normalized_findings.append(
                self._normalize_news_novelty_finding(
                    finding,
                    cluster_row=cluster_row,
                    cutoff_at=cutoff_at,
                    allowed_source_ids=allowed_source_ids,
                )
            )
            seen_cluster_ids.add(finding.cluster_id)
        for cluster_id, cluster_row in cluster_by_id.items():
            if cluster_id in seen_cluster_ids:
                continue
            normalized_findings.append(
                self._fallback_news_novelty_finding(
                    cluster_row=cluster_row,
                    cutoff_at=cutoff_at,
                )
            )
        normalized_findings.sort(key=lambda item: item.cluster_index)
        return review.model_copy(
            update={
                "run_id": manifest.run_id,
                "prompt_version": NEWS_NOVELTY_REVIEW_PROMPT_VERSION,
                "prompt_sha256": prompt_sha256,
                "cutoff_at": cutoff_at,
                "review_mode": manifest.blind_context_mode,
                "cluster_count": len(cluster_by_id),
                "reviewed_cluster_count": len(normalized_findings),
                "findings": normalized_findings,
                "excluded_after_cutoff_source_ids": manifest.excluded_web_source_ids,
            }
        )

    def _normalize_news_novelty_finding(
        self,
        finding: NewsNoveltyFinding,
        *,
        cluster_row: dict[str, Any],
        cutoff_at: datetime,
        allowed_source_ids: set[str],
    ) -> NewsNoveltyFinding:
        first_public_at = finding.first_public_evidence_at
        if first_public_at is None:
            first_public_at = _optional_datetime(cluster_row.get("first_published_at"))
        if first_public_at is not None and first_public_at.tzinfo is None:
            first_public_at = first_public_at.replace(tzinfo=cutoff_at.tzinfo)
        if first_public_at is not None and first_public_at > cutoff_at:
            raise ValueError(
                f"news novelty review used cutoff-after first_public_evidence_at: {first_public_at.isoformat()}"
            )
        evidence_source_ids = _unique_preserving_order(
            finding.evidence_source_ids or [str(source_id) for source_id in cluster_row.get("source_ids", [])]
        )
        unknown_source_ids = sorted(
            source_id for source_id in evidence_source_ids if source_id not in allowed_source_ids
        )
        if unknown_source_ids:
            raise ValueError(
                "news novelty review referenced unknown evidence_source_ids: " + ", ".join(unknown_source_ids)
            )
        return finding.model_copy(
            update={
                "cluster_index": int(cluster_row["cluster_index"]),
                "row_numbers": [int(value) for value in cluster_row.get("row_numbers", [])],
                "event_ids": [str(value) for value in cluster_row.get("event_ids", [])],
                "evidence_source_ids": evidence_source_ids,
                "first_public_evidence_at": first_public_at,
                "time_verified": first_public_at is not None and first_public_at <= cutoff_at,
            }
        )

    def _fallback_news_novelty_review(
        self,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
        prompt_sha256: str,
    ) -> NewsNoveltyReview:
        return self._fallback_news_novelty_review_for_rows(
            cluster_rows=self._read_event_cluster_context(manifest),
            manifest=manifest,
            cutoff_at=cutoff_at,
            prompt_sha256=prompt_sha256,
        )

    def _fallback_news_novelty_review_for_rows(
        self,
        *,
        cluster_rows: list[dict[str, Any]],
        manifest: ContextManifest,
        cutoff_at: datetime,
        prompt_sha256: str,
    ) -> NewsNoveltyReview:
        findings = [
            self._fallback_news_novelty_finding(
                cluster_row=cluster_row,
                cutoff_at=cutoff_at,
            )
            for cluster_row in cluster_rows
        ]
        return NewsNoveltyReview(
            run_id=manifest.run_id,
            prompt_version=NEWS_NOVELTY_REVIEW_PROMPT_VERSION,
            prompt_sha256=prompt_sha256,
            created_at=now_kst(),
            cutoff_at=cutoff_at,
            review_mode=manifest.blind_context_mode,
            cluster_count=len(cluster_rows),
            reviewed_cluster_count=len(findings),
            findings=findings,
            excluded_after_cutoff_source_ids=manifest.excluded_web_source_ids,
            notes=["Fallback novelty review: semantic LLM review was unavailable."],
        )

    def _fallback_news_novelty_finding(
        self,
        *,
        cluster_row: dict[str, Any],
        cutoff_at: datetime,
    ) -> NewsNoveltyFinding:
        first_public_at = _optional_datetime(cluster_row.get("first_published_at"))
        if first_public_at is not None and first_public_at.tzinfo is None:
            first_public_at = first_public_at.replace(tzinfo=cutoff_at.tzinfo)
        source_ids = [str(value) for value in cluster_row.get("source_ids", [])]
        return NewsNoveltyFinding(
            cluster_id=str(cluster_row["cluster_id"]),
            cluster_index=int(cluster_row["cluster_index"]),
            row_numbers=[int(value) for value in cluster_row.get("row_numbers", [])],
            event_ids=[str(value) for value in cluster_row.get("event_ids", [])],
            novelty=NewsNoveltyLabel.UNCLEAR,
            first_public_evidence_at=first_public_at,
            evidence_source_ids=source_ids,
            after_hours_new_disclosure="unclear",
            recycled_news="unclear",
            contract_stage="unclear",
            evidence_summary=(
                "Current news cluster is cutoff-safe, but semantic novelty, contract stage, "
                "attributable amount, customer, period, approval stage, and dilution risks "
                "remain unclear without stronger reviewed evidence."
            ),
            uncertainties=[
                "semantic novelty requires cutoff-safe LLM/web review",
                "contract economics and counterfactors are not deterministically inferable",
            ],
            time_verified=first_public_at is not None and first_public_at <= cutoff_at,
        )

    def _allowed_news_novelty_source_ids(
        self,
        cluster_rows: list[dict[str, Any]],
        manifest: ContextManifest,
    ) -> set[str]:
        source_ids: set[str] = set()
        for row in cluster_rows:
            for source_id in row.get("source_ids", []):
                if isinstance(source_id, str):
                    source_ids.add(source_id)
        source_ids.update(manifest.web_sources)
        return source_ids

    async def _run_semantic_retrieval_plan(
        self,
        *,
        news_texts: list[str],
        first_pass_mechanisms: list[str],
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> tuple[SemanticRetrievalPlan, str, int]:
        prompt = self._build_semantic_retrieval_plan_prompt(
            news_texts=news_texts,
            first_pass_mechanisms=first_pass_mechanisms,
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        prompt_sha256 = sha256_text(prompt)
        try:
            plan = await self.llm.generate_structured(
                prompt=prompt,
                response_model=SemanticRetrievalPlan,
                purpose="semantic_retrieval_plan",
            )
        except NotImplementedError:
            plan = self._fallback_semantic_retrieval_plan(
                manifest=manifest,
                cutoff_at=cutoff_at,
                prompt_sha256=prompt_sha256,
                first_pass_mechanisms=first_pass_mechanisms,
            )
        normalized = self._normalize_semantic_retrieval_plan(
            plan,
            manifest=manifest,
            cutoff_at=cutoff_at,
            prompt_sha256=prompt_sha256,
            first_pass_mechanisms=first_pass_mechanisms,
        )
        artifact_relative = (
            Path("runs") / "checkpoints" / "semantic_retrieval" / manifest.run_id / "semantic_retrieval_plan.json"
        )
        artifact_path = self.root / artifact_relative
        write_json(artifact_path, normalized.model_dump(mode="json"))
        artifact_text = artifact_path.read_text(encoding="utf-8")
        manifest.semantic_retrieval_plan_artifact = artifact_relative.as_posix()
        manifest.semantic_retrieval_plan_sha256 = sha256_text(artifact_text)
        manifest.semantic_retrieval_query_count = len(normalized.queries)
        return normalized, prompt_sha256, count_provider_tokens(self.llm, prompt)

    def _build_semantic_retrieval_plan_prompt(
        self,
        *,
        news_texts: list[str],
        first_pass_mechanisms: list[str],
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> str:
        shared_current_event = self._read_shared_downstream_context(manifest)
        payload = {
            "schema": "nslab.semantic_retrieval_plan.v1",
            "prompt_version": SEMANTIC_RETRIEVAL_PLAN_PROMPT_VERSION,
            "run_id": manifest.run_id,
            "cutoff_at": cutoff_at.isoformat(),
            "required_categories": list(SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES),
            "memory_sweep_artifacts": manifest.memory_sweep_artifacts,
            "retrieval_first_memory": self._candidate_generation_memory_context(manifest),
        }
        if shared_current_event is not None:
            payload["shared_current_event_digest"] = shared_current_event
            payload["required_inputs"] = list(
                FINAL_SYNTHESIS_REQUIRED_INPUTS_SHARED_V2
            )
        else:
            payload["current_news"] = news_texts
            payload["open_world_first_analysis"] = (
                self._read_open_world_first_analysis_context(manifest) or first_pass_mechanisms
            )
            payload["news_novelty_review"] = self._read_news_novelty_review_context(manifest)
        return (
            "Create additional semantic retrieval queries as SemanticRetrievalPlan. "
            "Queries must be mechanism-oriented and must cover every required category: "
            "positive analogs, negative controls, near misses, counterexamples, "
            "leader-selection pairs, theme-formation failures, candidate-generation "
            "errors, and newsless or unexplained outcomes. Do not use exact keyword "
            "matching as a gate and do not request cutoff-after evidence. Treat "
            "retrieval_first_memory as comparative support, failure boundaries, and "
            "query expansion after the open-world pass, never as a gate that deletes "
            "a current-news mechanism.\n"
            "---SEMANTIC_RETRIEVAL_PLAN_PAYLOAD---\n"
            f"{canonical_json(payload)}"
        )

    def _normalize_semantic_retrieval_plan(
        self,
        plan: SemanticRetrievalPlan,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
        prompt_sha256: str,
        first_pass_mechanisms: list[str],
    ) -> SemanticRetrievalPlan:
        queries: list[SemanticRetrievalQuery] = []
        seen: set[tuple[str, str]] = set()
        for query in plan.queries:
            category = _normalize_semantic_retrieval_category(query.category)
            if category is None:
                continue
            text = " ".join(query.query.split())
            if not text:
                continue
            key = (category, text)
            if key in seen:
                continue
            seen.add(key)
            queries.append(
                query.model_copy(
                    update={
                        "category": category,
                        "query": text,
                    }
                )
            )
        existing_categories = {query.category for query in queries}
        for category in SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES:
            if category in existing_categories:
                continue
            queries.append(
                self._fallback_semantic_retrieval_query(
                    category=category,
                    first_pass_mechanisms=first_pass_mechanisms,
                )
            )
        queries.sort(
            key=lambda item: (
                SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES.index(item.category),
                item.query,
            )
        )
        return plan.model_copy(
            update={
                "run_id": manifest.run_id,
                "prompt_version": SEMANTIC_RETRIEVAL_PLAN_PROMPT_VERSION,
                "prompt_sha256": prompt_sha256,
                "cutoff_at": cutoff_at,
                "queries": queries,
                "required_categories": list(SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES),
            }
        )

    def _fallback_semantic_retrieval_plan(
        self,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
        prompt_sha256: str,
        first_pass_mechanisms: list[str],
    ) -> SemanticRetrievalPlan:
        queries = [
            self._fallback_semantic_retrieval_query(
                category=category,
                first_pass_mechanisms=first_pass_mechanisms,
            )
            for category in SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES
        ]
        return SemanticRetrievalPlan(
            run_id=manifest.run_id,
            prompt_version=SEMANTIC_RETRIEVAL_PLAN_PROMPT_VERSION,
            prompt_sha256=prompt_sha256,
            created_at=now_kst(),
            cutoff_at=cutoff_at,
            queries=queries,
            required_categories=list(SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES),
            notes=["Fallback semantic retrieval plan: LLM query planning was unavailable."],
        )

    def _fallback_semantic_retrieval_query(
        self,
        *,
        category: str,
        first_pass_mechanisms: list[str],
    ) -> SemanticRetrievalQuery:
        mechanism_text = " ".join(first_pass_mechanisms[:2]) or "current catalyst"
        category_text = category.replace("_", " ")
        return SemanticRetrievalQuery(
            category=category,
            query=f"{category_text} structural analogs {mechanism_text}",
            rationale="Required Pass 3 category query generated without domain maps.",
        )

    def _write_semantic_retrieval_artifact(
        self,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> None:
        plan = self._read_semantic_retrieval_plan(manifest)
        rows: list[dict[str, Any]] = []
        included_episode_ids: list[str] = []
        excluded_episode_ids: list[str] = []
        included_record_ids: list[str] = []
        excluded_record_ids: list[str] = []
        for query_index, query in enumerate(plan.queries, start=1):
            raw_episode_ids = self.retrieval.search_semantic(query.query, limit=5)
            available_ids, unavailable_ids = self._filter_retrieved_ids_available_as_of(
                raw_episode_ids,
                cutoff_at=cutoff_at,
            )
            record_filters = _semantic_record_filters(query.category)
            raw_record_ids = self._search_memory_records(
                query=query.query,
                limit=5,
                filters=record_filters,
            )
            available_record_ids, unavailable_record_ids = self._filter_retrieved_record_ids_available_as_of(
                raw_record_ids,
                cutoff_at=cutoff_at,
            )
            included_episode_ids.extend(available_ids)
            excluded_episode_ids.extend(unavailable_ids)
            included_record_ids.extend(available_record_ids)
            excluded_record_ids.extend(unavailable_record_ids)
            rows.append(
                {
                    "schema_version": "nslab.semantic_retrieval_result.v1",
                    "run_id": manifest.run_id,
                    "query_index": query_index,
                    "category": query.category,
                    "query": query.query,
                    "query_sha256": sha256_text(query.query),
                    "rationale": query.rationale,
                    "raw_episode_ids": raw_episode_ids,
                    "included_episode_ids": available_ids,
                    "excluded_episode_ids": unavailable_ids,
                    "raw_record_ids": raw_record_ids,
                    "included_record_ids": available_record_ids,
                    "excluded_record_ids": unavailable_record_ids,
                    "record_retrieval_filters": record_filters,
                    "result_count": len(available_ids),
                    "record_result_count": len(available_record_ids),
                    "excluded_count": len(unavailable_ids),
                    "excluded_record_count": len(unavailable_record_ids),
                    "cutoff_at": cutoff_at.isoformat(),
                }
            )
        included_episode_ids = _unique_preserving_order(included_episode_ids)
        excluded_episode_ids = _unique_preserving_order(excluded_episode_ids)
        included_record_ids = _unique_preserving_order(included_record_ids)
        excluded_record_ids = _unique_preserving_order(excluded_record_ids)
        artifact_relative = (
            Path("runs") / "checkpoints" / "semantic_retrieval" / manifest.run_id / "semantic_retrieval.jsonl"
        )
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        category_counts = {
            category: sum(1 for row in rows if row["category"] == category)
            for category in SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES
        }
        manifest.semantic_retrieval_artifact = artifact_relative.as_posix()
        manifest.semantic_retrieval_sha256 = sha256_text(payload)
        manifest.semantic_retrieval_episode_ids = included_episode_ids
        manifest.excluded_semantic_retrieval_episode_ids = excluded_episode_ids
        manifest.semantic_retrieval_record_ids = included_record_ids
        manifest.excluded_semantic_retrieval_record_ids = excluded_record_ids
        manifest.semantic_retrieval_summary = {
            "required_categories": list(SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES),
            "category_query_counts": category_counts,
            "query_count": len(rows),
            "included_episode_count": len(included_episode_ids),
            "excluded_episode_count": len(excluded_episode_ids),
            "included_record_count": len(included_record_ids),
            "excluded_record_count": len(excluded_record_ids),
            "record_retrieval_zero_is_valid": True,
            "retrieval_zero_is_valid": True,
        }

    def _write_semantic_cluster_coverage_artifact(
        self,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> None:
        cluster_rows = [
            row
            for row in self._read_event_cluster_context(manifest)
            if isinstance(row, dict) and isinstance(row.get("cluster_id"), str)
        ]
        rows: list[dict[str, Any]] = []
        raw_record_ids_all: list[str] = []
        included_record_ids_by_lane: list[list[str]] = []
        query_index = 0
        lanes = _cluster_coverage_lanes(self.settings.limits.cluster_coverage_lanes)
        record_limit = (
            self.settings.limits.cluster_coverage_record_limit_per_lane
            if self.settings.limits.cluster_coverage_record_limit_per_lane > 0
            else self.settings.limits.cluster_coverage_record_limit_per_query
        )
        for cluster_position, cluster_row in enumerate(cluster_rows, start=1):
            cluster_id = str(cluster_row["cluster_id"])
            title = str(cluster_row.get("representative_title_excerpt") or "")
            body = str(cluster_row.get("representative_body_excerpt") or "")
            for lane in lanes:
                query_index += 1
                query = " ".join(
                    [
                        "cluster coverage balanced lane",
                        f"lane={lane}",
                        f"cluster_id={cluster_id}",
                        f"title={title}",
                        f"body={body}",
                        _cluster_coverage_lane_instruction(lane),
                    ]
                ).strip()
                raw_episode_ids = self.retrieval.search_semantic(query, limit=5)
                available_ids, unavailable_ids = self._filter_retrieved_ids_available_as_of(
                    raw_episode_ids,
                    cutoff_at=cutoff_at,
                )
                record_filters = _semantic_record_filters(lane)
                raw_record_ids = self._search_memory_records(
                    query=query,
                    limit=record_limit,
                    filters=record_filters,
                )
                available_record_ids, unavailable_record_ids = self._filter_retrieved_record_ids_available_as_of(
                    raw_record_ids,
                    cutoff_at=cutoff_at,
                )
                raw_record_ids_all.extend(raw_record_ids)
                included_record_ids_by_lane.append(available_record_ids)
                rows.append(
                    {
                        "schema_version": "nslab.semantic_cluster_coverage_result.v1",
                        "run_id": manifest.run_id,
                        "cluster_id": cluster_id,
                        "cluster_index": int(cluster_row.get("cluster_index") or cluster_position),
                        "query_index": query_index,
                        "category": lane,
                        "query": query,
                        "query_sha256": sha256_text(query),
                        "related_cluster_ids": [cluster_id],
                        "coverage_query": True,
                        "retrieval_lane": lane,
                        "source_cluster_indices": [int(cluster_row.get("cluster_index") or cluster_position)],
                        "source_event_ids": _string_values(cluster_row.get("event_ids")),
                        "source_ids": _string_values(cluster_row.get("source_ids")),
                        "raw_episode_ids": raw_episode_ids,
                        "included_episode_ids": available_ids,
                        "excluded_episode_ids": unavailable_ids,
                        "raw_record_ids": raw_record_ids,
                        "included_record_ids": available_record_ids,
                        "excluded_record_ids": unavailable_record_ids,
                        "record_retrieval_filters": record_filters,
                        "result_count": len(available_ids),
                        "record_result_count": len(available_record_ids),
                        "excluded_count": len(unavailable_ids),
                        "excluded_record_count": len(unavailable_record_ids),
                        "cutoff_at": cutoff_at.isoformat(),
                    }
                )
        covered_ids = _unique_preserving_order(
            [str(row["cluster_id"]) for row in rows if isinstance(row.get("cluster_id"), str)]
        )
        all_cluster_ids = _unique_preserving_order([str(row["cluster_id"]) for row in cluster_rows])
        missing_ids = [cluster_id for cluster_id in all_cluster_ids if cluster_id not in covered_ids]
        promoted_record_ids = self._promote_cluster_coverage_record_ids(
            included_record_ids_by_lane,
            existing_record_ids=manifest.semantic_retrieval_record_ids,
        )
        artifact_relative = (
            Path("runs") / "checkpoints" / "semantic_retrieval" / manifest.run_id / "semantic_cluster_coverage.jsonl"
        )
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        manifest.semantic_cluster_coverage_artifact = artifact_relative.as_posix()
        manifest.semantic_cluster_coverage_sha256 = sha256_text(payload)
        manifest.semantic_cluster_coverage_query_count = len(rows)
        manifest.semantic_cluster_coverage_ids = covered_ids
        manifest.semantic_cluster_coverage_missing_ids = missing_ids
        manifest.semantic_cluster_coverage_promoted_record_ids = promoted_record_ids
        manifest.semantic_cluster_coverage_summary = {
            "cluster_coverage_source_count": len(all_cluster_ids),
            "cluster_coverage_query_count": len(rows),
            "cluster_coverage_lane_count": len(lanes),
            "cluster_coverage_lanes": lanes,
            "cluster_coverage_record_limit_per_lane": record_limit,
            "cluster_coverage_lane_query_counts": {
                lane: sum(1 for row in rows if row.get("retrieval_lane") == lane) for lane in lanes
            },
            "cluster_coverage_covered_count": len(covered_ids),
            "cluster_coverage_missing_count": len(missing_ids),
            "cluster_coverage_missing_ids": missing_ids,
            "cluster_coverage_raw_record_id_count": len(_unique_preserving_order(raw_record_ids_all)),
            "cluster_coverage_promoted_record_count": len(promoted_record_ids),
            "cluster_coverage_promoted_record_ids": promoted_record_ids,
            "cluster_coverage_promotion_limit": (self.settings.limits.cluster_coverage_promoted_record_limit),
            "record_retrieval_zero_is_valid": True,
            "retrieval_zero_is_valid": True,
        }
        if missing_ids:
            message = "semantic cluster coverage missing event clusters: " + ", ".join(missing_ids)
            if message not in manifest.errors:
                manifest.errors.append(message)
            raise ClusterCoverageError(message)

    def _promote_cluster_coverage_record_ids(
        self,
        record_ids_by_lane: list[list[str]],
        *,
        existing_record_ids: list[str],
    ) -> list[str]:
        limit = self.settings.limits.cluster_coverage_promoted_record_limit
        if limit <= 0:
            return []
        promoted: list[str] = []
        seen = set(existing_record_ids)
        max_depth = max((len(ids) for ids in record_ids_by_lane), default=0)
        for depth in range(max_depth):
            for record_ids in record_ids_by_lane:
                if depth >= len(record_ids):
                    continue
                record_id = record_ids[depth]
                if record_id in seen:
                    continue
                seen.add(record_id)
                promoted.append(record_id)
                if len(promoted) >= limit:
                    return promoted
        return promoted

    def _refresh_counterexample_record_ids_from_retrieval(
        self,
        manifest: ContextManifest,
    ) -> None:
        store = BrainRecordStore(self.root)
        source_record_ids = [
            *manifest.retrieved_record_ids,
            *manifest.semantic_retrieval_record_ids,
            *manifest.semantic_cluster_coverage_promoted_record_ids,
        ]
        counterexample_ids: list[str] = []
        available_record_id_set = set(manifest.available_record_ids)
        for record_id in _unique_preserving_order(source_record_ids):
            if record_id not in available_record_id_set:
                continue
            try:
                record = store.get_record(record_id)
            except FileNotFoundError:
                continue
            routing = record_routing_metadata(record)
            if record.record_type == "counterexample" and COUNTEREXAMPLES_LANE in routing.memory_lanes:
                counterexample_ids.append(record.record_id)
        manifest.counterexample_record_ids = counterexample_ids

    def _prediction_retrieved_record_ids(self, manifest: ContextManifest) -> list[str]:
        return _unique_preserving_order(
            [
                *manifest.retrieved_record_ids,
                *manifest.semantic_retrieval_record_ids,
            ]
        )

    def _prediction_record_polarities(
        self,
        record_ids: Sequence[str],
    ) -> tuple[list[str], list[str]]:
        store = BrainRecordStore(self.root)
        positive: list[str] = []
        negative: list[str] = []
        for record_id in _unique_preserving_order(record_ids):
            try:
                record = store.get_record(record_id)
            except FileNotFoundError:
                continue
            routing = record_routing_metadata(record)
            if record_is_positive_support(record):
                positive.append(record_id)
            elif (
                record.record_type not in CANDIDATE_ERROR_RECORD_TYPES
                and routing.routing_disposition == RecordRoutingDisposition.REASONING.value
                and routing.evidence_polarity == RecordEvidencePolarity.NEGATIVE.value
            ):
                negative.append(record_id)
        return positive, negative

    def _read_semantic_retrieval_plan(
        self,
        manifest: ContextManifest,
    ) -> SemanticRetrievalPlan:
        if not manifest.semantic_retrieval_plan_artifact:
            return self._fallback_semantic_retrieval_plan(
                manifest=manifest,
                cutoff_at=manifest.cutoff_at,
                prompt_sha256="",
                first_pass_mechanisms=[],
            )
        payload = read_json(self.root / manifest.semantic_retrieval_plan_artifact)
        return SemanticRetrievalPlan.model_validate(payload)

    async def _run_candidate_expansion(
        self,
        *,
        news_texts: list[str],
        first_pass_mechanisms: list[str],
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> tuple[CandidateExpansionReview, str, int]:
        prompt = self._build_candidate_expansion_prompt(
            news_texts=news_texts,
            first_pass_mechanisms=first_pass_mechanisms,
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        prompt_sha256 = sha256_text(prompt)
        try:
            review = await self.llm.generate_structured(
                prompt=prompt,
                response_model=CandidateExpansionReview,
                purpose="candidate_expansion",
            )
        except NotImplementedError:
            review = self._fallback_candidate_expansion(
                manifest=manifest,
                cutoff_at=cutoff_at,
                prompt_sha256=prompt_sha256,
                first_pass_mechanisms=first_pass_mechanisms,
            )
        normalized = self._normalize_candidate_expansion(
            review,
            manifest=manifest,
            cutoff_at=cutoff_at,
            prompt_sha256=prompt_sha256,
            first_pass_mechanisms=first_pass_mechanisms,
        )
        artifact_relative = (
            Path("runs") / "checkpoints" / "candidate_expansion" / manifest.run_id / "candidate_expansion.json"
        )
        artifact_path = self.root / artifact_relative
        write_json(artifact_path, normalized.model_dump(mode="json"))
        artifact_text = artifact_path.read_text(encoding="utf-8")
        path_counts = {
            path.value: sum(1 for finding in normalized.findings if finding.path == path)
            for path in CANDIDATE_EXPANSION_REQUIRED_PATHS
        }
        manifest.candidate_expansion_artifact = artifact_relative.as_posix()
        manifest.candidate_expansion_sha256 = sha256_text(artifact_text)
        manifest.candidate_expansion_count = len(normalized.findings)
        manifest.candidate_expansion_summary = {
            "required_paths": [path.value for path in CANDIDATE_EXPANSION_REQUIRED_PATHS],
            "path_counts": path_counts,
            "finding_count": len(normalized.findings),
            "candidate_name_count": len(
                {candidate for finding in normalized.findings for candidate in finding.candidate_names}
            ),
            "requires_web_company_discovery_count": sum(
                1 for finding in normalized.findings if finding.requires_web_company_discovery
            ),
            "continuation_d_minus_one_only_verified": all(
                finding.d_minus_one_market_data_only
                for finding in normalized.findings
                if finding.path == CandidateExpansionPath.CONTINUATION
            ),
            "cluster_coverage_source_count": manifest.event_cluster_count,
            "cluster_coverage_covered_count": len(manifest.candidate_expansion_cluster_coverage_ids),
            "cluster_coverage_missing_count": len(manifest.candidate_expansion_uncovered_cluster_ids),
            "cluster_coverage_missing_ids": (manifest.candidate_expansion_uncovered_cluster_ids),
            "audit_only_cluster_count": len(manifest.candidate_expansion_audit_only_cluster_ids),
        }
        return normalized, prompt_sha256, count_provider_tokens(self.llm, prompt)

    def _build_candidate_expansion_prompt(
        self,
        *,
        news_texts: list[str],
        first_pass_mechanisms: list[str],
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> str:
        shared_current_event = self._read_shared_downstream_context(manifest)
        payload = {
            "schema": "nslab.candidate_expansion.v1",
            "prompt_version": CANDIDATE_EXPANSION_PROMPT_VERSION,
            "run_id": manifest.run_id,
            "cutoff_at": cutoff_at.isoformat(),
            "required_paths": [path.value for path in CANDIDATE_EXPANSION_REQUIRED_PATHS],
            "additional_semantic_retrieval": self._read_semantic_retrieval_context(manifest),
            "semantic_cluster_coverage": self._read_semantic_cluster_coverage_context(manifest),
            "retrieval_first_memory": self._candidate_generation_memory_context(manifest),
            "d_minus_one_only_for_continuation": True,
        }
        if shared_current_event is not None:
            payload["shared_current_event_digest"] = shared_current_event
        else:
            payload["current_news"] = news_texts
            payload["open_world_first_analysis"] = (
                self._read_open_world_first_analysis_context(manifest) or first_pass_mechanisms
            )
            payload["news_novelty_review"] = self._read_news_novelty_review_context(manifest)
        return (
            "Expand open-world candidate routes as CandidateExpansionReview. Execute "
            "four independent paths: SINGLE_EVENT, THEME_FORMATION, "
            "BENEFICIARY_DISCOVERY, and CONTINUATION. Do not restrict candidates to "
            "existing memory. Do not use D-day prices or cutoff-after information. "
            "For CONTINUATION, mark d_minus_one_market_data_only true. Return "
            "investigation questions for web/company verification instead of hardcoded "
            "ticker or theme maps. Use retrieval_first_memory to recover missed "
            "beneficiaries, ranking failures, counterexamples, and rare mechanisms, "
            "but do not suppress an open-world route solely because history lacks it.\n"
            "---CANDIDATE_EXPANSION_PAYLOAD---\n"
            f"{canonical_json(payload)}"
        )

    def _normalize_candidate_expansion(
        self,
        review: CandidateExpansionReview,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
        prompt_sha256: str,
        first_pass_mechanisms: list[str],
    ) -> CandidateExpansionReview:
        findings: list[CandidateExpansionFinding] = []
        existing_paths: set[CandidateExpansionPath] = set()
        allowed_source_ids = self._candidate_expansion_allowed_source_ids(manifest)
        allowed_cluster_ids = self._candidate_expansion_allowed_cluster_ids(manifest)
        allowed_episode_ids = set(manifest.semantic_retrieval_episode_ids) | set(manifest.retrieved_episode_ids)
        for finding in review.findings:
            if finding.path not in CANDIDATE_EXPANSION_REQUIRED_PATHS:
                continue
            fallback = self._fallback_candidate_expansion_finding(
                path=finding.path,
                manifest=manifest,
                first_pass_mechanisms=first_pass_mechanisms,
            )
            unknown_sources = sorted(
                source_id for source_id in finding.evidence_source_ids if source_id not in allowed_source_ids
            )
            if unknown_sources:
                raise ValueError(
                    "candidate expansion referenced unknown evidence_source_ids: " + ", ".join(unknown_sources)
                )
            unknown_clusters = sorted(
                cluster_id for cluster_id in finding.related_cluster_ids if cluster_id not in allowed_cluster_ids
            )
            if unknown_clusters:
                raise ValueError(
                    "candidate expansion referenced unknown related_cluster_ids: " + ", ".join(unknown_clusters)
                )
            unknown_episodes = sorted(
                episode_id for episode_id in finding.memory_episode_ids if episode_id not in allowed_episode_ids
            )
            if unknown_episodes:
                raise ValueError(
                    "candidate expansion referenced unavailable memory_episode_ids: " + ", ".join(unknown_episodes)
                )
            if finding.path == CandidateExpansionPath.CONTINUATION and not finding.d_minus_one_market_data_only:
                finding = finding.model_copy(update={"d_minus_one_market_data_only": True})
            finding = finding.model_copy(
                update={
                    "candidate_names": finding.candidate_names or fallback.candidate_names,
                    "sector_hypotheses": finding.sector_hypotheses or fallback.sector_hypotheses,
                    "investigation_questions": finding.investigation_questions or fallback.investigation_questions,
                    "uncertainties": finding.uncertainties or fallback.uncertainties,
                }
            )
            findings.append(finding)
            existing_paths.add(finding.path)
        for path in CANDIDATE_EXPANSION_REQUIRED_PATHS:
            if path in existing_paths:
                continue
            findings.append(
                self._fallback_candidate_expansion_finding(
                    path=path,
                    manifest=manifest,
                    first_pass_mechanisms=first_pass_mechanisms,
                )
            )
        findings.sort(key=lambda item: CANDIDATE_EXPANSION_REQUIRED_PATHS.index(item.path))
        all_cluster_ids = _unique_preserving_order(
            [
                str(row["cluster_id"])
                for row in self._read_event_cluster_context(manifest)
                if isinstance(row, dict) and isinstance(row.get("cluster_id"), str)
            ]
        )
        finding_cluster_ids = _unique_preserving_order(
            [
                cluster_id
                for finding in findings
                for cluster_id in finding.related_cluster_ids
                if cluster_id in all_cluster_ids
            ]
        )
        semantic_covered_ids = [
            cluster_id for cluster_id in manifest.semantic_cluster_coverage_ids if cluster_id in all_cluster_ids
        ]
        covered_cluster_ids = finding_cluster_ids
        audit_only_cluster_ids = [
            cluster_id for cluster_id in semantic_covered_ids if cluster_id not in set(covered_cluster_ids)
        ]
        uncovered_cluster_ids = [
            cluster_id
            for cluster_id in all_cluster_ids
            if cluster_id not in set(covered_cluster_ids) and cluster_id not in set(audit_only_cluster_ids)
        ]
        manifest.candidate_expansion_cluster_coverage_ids = _unique_preserving_order(
            [*covered_cluster_ids, *audit_only_cluster_ids]
        )
        manifest.candidate_expansion_audit_only_cluster_ids = audit_only_cluster_ids
        manifest.candidate_expansion_uncovered_cluster_ids = uncovered_cluster_ids
        if uncovered_cluster_ids:
            message = "candidate expansion missing event cluster coverage: " + ", ".join(uncovered_cluster_ids)
            if message not in manifest.errors:
                manifest.errors.append(message)
            raise ClusterCoverageError(message)
        return review.model_copy(
            update={
                "run_id": manifest.run_id,
                "prompt_version": CANDIDATE_EXPANSION_PROMPT_VERSION,
                "prompt_sha256": prompt_sha256,
                "cutoff_at": cutoff_at,
                "required_paths": list(CANDIDATE_EXPANSION_REQUIRED_PATHS),
                "findings": findings,
                "covered_cluster_ids": covered_cluster_ids,
                "audit_only_cluster_ids": audit_only_cluster_ids,
                "uncovered_cluster_ids": uncovered_cluster_ids,
            }
        )

    def _fallback_candidate_expansion(
        self,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
        prompt_sha256: str,
        first_pass_mechanisms: list[str],
    ) -> CandidateExpansionReview:
        findings = [
            self._fallback_candidate_expansion_finding(
                path=path,
                manifest=manifest,
                first_pass_mechanisms=first_pass_mechanisms,
            )
            for path in CANDIDATE_EXPANSION_REQUIRED_PATHS
        ]
        return CandidateExpansionReview(
            run_id=manifest.run_id,
            prompt_version=CANDIDATE_EXPANSION_PROMPT_VERSION,
            prompt_sha256=prompt_sha256,
            created_at=now_kst(),
            cutoff_at=cutoff_at,
            required_paths=list(CANDIDATE_EXPANSION_REQUIRED_PATHS),
            findings=findings,
            notes=["Fallback candidate expansion: LLM route expansion was unavailable."],
        )

    def _fallback_candidate_expansion_finding(
        self,
        *,
        path: CandidateExpansionPath | str,
        manifest: ContextManifest,
        first_pass_mechanisms: list[str],
    ) -> CandidateExpansionFinding:
        mechanism = first_pass_mechanisms[0] if first_pass_mechanisms else "current catalyst"
        cluster_ids = [
            str(row["cluster_id"])
            for row in self._read_event_cluster_context(manifest)
            if isinstance(row, dict) and isinstance(row.get("cluster_id"), str)
        ][:3]
        source_ids = self._candidate_expansion_allowed_source_ids(manifest)
        path_value = path.value if isinstance(path, CandidateExpansionPath) else path
        normalized_path = CandidateExpansionPath(path_value)
        path_text = path_value.lower().replace("_", " ")
        return CandidateExpansionFinding(
            path=normalized_path,
            hypothesis=f"{path_text} route requires open-world review of {mechanism}.",
            candidate_names=[f"{path_value}_DISCOVERY_REQUIRED"],
            sector_hypotheses=[f"{path_text} hypothesis from current catalyst"],
            investigation_questions=[
                f"Which listed entities fit the {path_text} route before cutoff?",
                "Which directness, novelty, and market-memory checks can disconfirm it?",
            ],
            evidence_source_ids=sorted(source_ids)[:5],
            related_cluster_ids=cluster_ids,
            memory_episode_ids=manifest.semantic_retrieval_episode_ids[:5],
            requires_web_company_discovery=normalized_path
            in {
                CandidateExpansionPath.SINGLE_EVENT,
                CandidateExpansionPath.THEME_FORMATION,
                CandidateExpansionPath.BENEFICIARY_DISCOVERY,
            },
            d_minus_one_market_data_only=(normalized_path == CandidateExpansionPath.CONTINUATION),
            uncertainties=["candidate route must be verified by Pass 5 web/company checks"],
        )

    def _candidate_expansion_allowed_source_ids(self, manifest: ContextManifest) -> set[str]:
        source_ids: set[str] = set(manifest.web_sources)
        for row in self._read_event_cluster_context(manifest):
            for source_id in row.get("source_ids", []):
                if isinstance(source_id, str):
                    source_ids.add(source_id)
        return source_ids

    def _candidate_expansion_allowed_cluster_ids(self, manifest: ContextManifest) -> set[str]:
        return {
            str(row["cluster_id"])
            for row in self._read_event_cluster_context(manifest)
            if isinstance(row, dict) and isinstance(row.get("cluster_id"), str)
        }

    async def _collect_cutoff_safe_web_sources(
        self,
        *,
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> None:
        guard = TemporalWebGuard(self.web_provider)
        rows: list[dict[str, Any]] = []
        excluded_rows: list[dict[str, Any]] = []
        for query in manifest.web_queries:
            manifest.blind_web_search_call_count += 1
            prior_exclusion_count = len(guard.excluded_sources)
            for result in await guard.search(query, cutoff_at=cutoff_at):
                rows.append(
                    self._web_source_row(
                        result,
                        query=query,
                        cutoff_at=cutoff_at,
                        opened_text=await guard.open(result.url, cutoff_at=cutoff_at),
                    )
                )
            for exclusion in guard.excluded_sources[prior_exclusion_count:]:
                excluded_rows.append(
                    self._excluded_web_source_row(
                        exclusion,
                        query=query,
                        cutoff_at=cutoff_at,
                    )
                )
        manifest.excluded_web_source_ids = _unique_preserving_order(
            [*manifest.excluded_web_source_ids, *guard.excluded_source_ids]
        )
        manifest.web_sources = _unique_preserving_order(
            [row["source_id"] for row in rows if isinstance(row.get("source_id"), str)]
        )
        manifest.external_web_evidence_count = len(rows)
        artifact_relative = Path("runs") / "checkpoints" / "web_sources" / manifest.run_id / "web_sources.jsonl"
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        manifest.web_source_artifact = artifact_relative.as_posix()
        manifest.web_source_sha256 = sha256_text(payload)
        excluded_artifact_relative = (
            Path("runs") / "checkpoints" / "web_sources" / manifest.run_id / "excluded_web_sources.jsonl"
        )
        excluded_artifact_path = self.root / excluded_artifact_relative
        excluded_payload = "".join(canonical_json(row) + "\n" for row in excluded_rows)
        excluded_artifact_path.write_text(excluded_payload, encoding="utf-8")
        manifest.excluded_web_source_artifact = excluded_artifact_relative.as_posix()
        manifest.excluded_web_source_sha256 = sha256_text(excluded_payload)
        manifest.excluded_web_source_count = len(excluded_rows)

    def _web_source_row(
        self,
        result: WebSearchResult,
        *,
        query: str,
        cutoff_at: datetime,
        opened_text: str,
    ) -> dict[str, Any]:
        published_at = result.published_at
        content_fingerprint = canonical_json(
            {
                "title": result.title,
                "url": result.url,
                "snippet": result.snippet,
                "opened_text": opened_text,
            }
        )
        return {
            "schema_version": "nslab.web_source.v1",
            "source_id": result.source_id,
            "query": query,
            "title": result.title,
            "url": result.url,
            "source_url": result.url,
            "snippet": result.snippet,
            "published_at": published_at.isoformat() if published_at else None,
            "timestamp_precision": result.timestamp_precision,
            "retrieved_at": now_kst().isoformat(),
            "cutoff_at": cutoff_at.isoformat(),
            "time_verified": published_at is not None and published_at <= cutoff_at,
            "available_before_cutoff": published_at is not None and published_at <= cutoff_at,
            "content_sha256": sha256_text(content_fingerprint),
            "opened_text_sha256": sha256_text(opened_text),
            "opened_text_excerpt": _excerpt(opened_text),
        }

    def _excluded_web_source_row(
        self,
        exclusion: WebSearchExclusion,
        *,
        query: str,
        cutoff_at: datetime,
    ) -> dict[str, Any]:
        result = exclusion.result
        published_at = result.published_at
        available_before_cutoff = published_at is not None and published_at <= cutoff_at
        return {
            "schema_version": "nslab.excluded_web_source.v1",
            "source_id": result.source_id,
            "query": query,
            "title": result.title,
            "url": result.url,
            "source_url": result.url,
            "snippet": result.snippet,
            "published_at": published_at.isoformat() if published_at else None,
            "timestamp_precision": result.timestamp_precision,
            "retrieved_at": now_kst().isoformat(),
            "cutoff_at": cutoff_at.isoformat(),
            "exclusion_reason": exclusion.reason,
            "time_verified": False,
            "available_before_cutoff": available_before_cutoff,
            "content_sha256": sha256_text(
                canonical_json(
                    {
                        "title": result.title,
                        "url": result.url,
                        "snippet": result.snippet,
                    }
                )
            ),
        }

    async def _collect_candidate_web_checks(
        self,
        *,
        prediction: BlindPrediction,
        manifest: ContextManifest,
        cutoff_at: datetime,
        d_minus_one_market_data: dict[str, Any],
    ) -> None:
        guard = TemporalWebGuard(self.web_provider)
        rows: list[dict[str, Any]] = []
        excluded_rows: list[dict[str, Any]] = []
        subjects = self._candidate_web_check_subjects(prediction, manifest)
        for subject in subjects:
            query = self._candidate_web_check_query(subject)
            manifest.blind_web_search_call_count += 1
            prior_exclusion_count = len(guard.excluded_sources)
            for result in await guard.search(query, cutoff_at=cutoff_at):
                rows.append(
                    self._candidate_web_check_row(
                        result,
                        subject=subject,
                        manifest=manifest,
                        query=query,
                        cutoff_at=cutoff_at,
                        opened_text=await guard.open(result.url, cutoff_at=cutoff_at),
                    )
                )
            for exclusion in guard.excluded_sources[prior_exclusion_count:]:
                excluded_rows.append(
                    self._excluded_candidate_web_check_row(
                        exclusion,
                        subject=subject,
                        manifest=manifest,
                        query=query,
                        cutoff_at=cutoff_at,
                    )
                )
        manifest.candidate_web_source_ids = _unique_preserving_order(
            [row["source_id"] for row in rows if isinstance(row.get("source_id"), str)]
        )
        manifest.excluded_candidate_web_source_ids = _unique_preserving_order(
            [*manifest.excluded_candidate_web_source_ids, *guard.excluded_source_ids]
        )
        artifact_relative = (
            Path("runs") / "checkpoints" / "candidate_web_checks" / manifest.run_id / "candidate_web_checks.jsonl"
        )
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        manifest.candidate_web_check_artifact = artifact_relative.as_posix()
        manifest.candidate_web_check_sha256 = sha256_text(payload)
        manifest.candidate_web_check_count = len(rows)
        manifest.external_web_evidence_count += len(rows)
        manifest.candidate_web_check_summary = {
            "subject_count": len(subjects),
            "final_candidate_subject_count": sum(
                1 for subject in subjects if subject.subject_type == "final_candidate"
            ),
            "candidate_expansion_subject_count": sum(
                1 for subject in subjects if subject.subject_type == "candidate_expansion"
            ),
            "verification_focus": list(CANDIDATE_WEB_VERIFICATION_FOCUS),
            "source_count": len(rows),
            "excluded_source_count": len(excluded_rows),
            "expansion_paths": sorted({str(subject.expansion_path) for subject in subjects if subject.expansion_path}),
        }
        self._write_candidate_verification_artifact(
            manifest=manifest,
            subjects=subjects,
            rows=rows,
            excluded_rows=excluded_rows,
            cutoff_at=cutoff_at,
            d_minus_one_market_data=d_minus_one_market_data,
        )
        excluded_artifact_relative = (
            Path("runs")
            / "checkpoints"
            / "candidate_web_checks"
            / manifest.run_id
            / "excluded_candidate_web_checks.jsonl"
        )
        excluded_artifact_path = self.root / excluded_artifact_relative
        excluded_payload = "".join(canonical_json(row) + "\n" for row in excluded_rows)
        excluded_artifact_path.write_text(excluded_payload, encoding="utf-8")
        manifest.excluded_candidate_web_check_artifact = excluded_artifact_relative.as_posix()
        manifest.excluded_candidate_web_check_sha256 = sha256_text(excluded_payload)
        manifest.excluded_candidate_web_check_count = len(excluded_rows)

    def _write_candidate_verification_artifact(
        self,
        *,
        manifest: ContextManifest,
        subjects: Sequence[CandidateWebCheckSubject],
        rows: Sequence[dict[str, Any]],
        excluded_rows: Sequence[dict[str, Any]],
        cutoff_at: datetime,
        d_minus_one_market_data: dict[str, Any],
    ) -> None:
        findings: list[CandidateVerificationFinding] = []
        for subject in subjects:
            key = _candidate_web_check_subject_key(subject)
            accepted = [row for row in rows if _candidate_web_check_row_key(row) == key]
            excluded = [row for row in excluded_rows if _candidate_web_check_row_key(row) == key]
            accepted_source_ids = _unique_preserving_order(
                [str(row["source_id"]) for row in accepted if isinstance(row.get("source_id"), str)]
            )
            excluded_source_ids = _unique_preserving_order(
                [str(row["source_id"]) for row in excluded if isinstance(row.get("source_id"), str)]
            )
            findings.append(
                CandidateVerificationFinding(
                    subject_type=subject.subject_type,
                    candidate_rank=subject.rank,
                    candidate_ticker=subject.ticker,
                    candidate_company_name=subject.company_name,
                    candidate_path_type=subject.path_type,
                    candidate_expansion_path=subject.expansion_path,
                    query=self._candidate_web_check_query(subject),
                    source_count=len(accepted),
                    excluded_source_count=len(excluded),
                    accepted_source_ids=accepted_source_ids,
                    excluded_source_ids=excluded_source_ids,
                    verification_dimensions=self._candidate_verification_dimensions(
                        subject=subject,
                        accepted_source_ids=accepted_source_ids,
                    ),
                    blind_safe_market_snapshot=self._candidate_verification_market_snapshot(
                        subject=subject,
                        d_minus_one_market_data=d_minus_one_market_data,
                    ),
                    d_minus_one_market_data_only=(
                        subject.path_type == CandidateExpansionPath.CONTINUATION
                        or subject.path_type == str(PathType.CONTINUATION)
                    ),
                    uncertainties=self._candidate_verification_uncertainties(
                        subject=subject,
                        accepted_source_ids=accepted_source_ids,
                        excluded_source_ids=excluded_source_ids,
                    ),
                )
            )
        review = CandidateVerificationReview(
            run_id=manifest.run_id,
            created_at=cutoff_at,
            cutoff_at=cutoff_at,
            required_dimensions=list(CANDIDATE_WEB_VERIFICATION_FOCUS),
            subject_count=len(subjects),
            findings=findings,
            notes=["Pass 5 checklist records cutoff-safe verification coverage; final synthesis judges substance."],
        )
        artifact_relative = (
            Path("runs") / "checkpoints" / "candidate_verifications" / manifest.run_id / "candidate_verification.json"
        )
        artifact_path = self.root / artifact_relative
        write_json(artifact_path, review.model_dump(mode="json"))
        artifact_text = artifact_path.read_text(encoding="utf-8")
        manifest.candidate_verification_artifact = artifact_relative.as_posix()
        manifest.candidate_verification_sha256 = sha256_text(artifact_text)
        manifest.candidate_verification_count = len(findings)
        status_counts: dict[str, int] = {}
        for finding in findings:
            for dimension in finding.verification_dimensions:
                status_counts[dimension.status] = status_counts.get(dimension.status, 0) + 1
        manifest.candidate_verification_summary = {
            "subject_count": len(subjects),
            "finding_count": len(findings),
            "required_dimensions": list(CANDIDATE_WEB_VERIFICATION_FOCUS),
            "status_counts": status_counts,
            "subjects_without_cutoff_safe_sources": sum(1 for finding in findings if not finding.accepted_source_ids),
            "candidate_expansion_subject_count": sum(
                1 for finding in findings if finding.subject_type == "candidate_expansion"
            ),
            "d_minus_one_only_subject_count": sum(1 for finding in findings if finding.d_minus_one_market_data_only),
            "d_minus_one_snapshot_count": sum(
                1 for finding in findings if finding.blind_safe_market_snapshot.get("status") == "snapshot"
            ),
            "d_minus_one_snapshot_unavailable_count": sum(
                1 for finding in findings if finding.blind_safe_market_snapshot.get("status") != "snapshot"
            ),
        }

    def _candidate_verification_market_snapshot(
        self,
        *,
        subject: CandidateWebCheckSubject,
        d_minus_one_market_data: dict[str, Any],
    ) -> dict[str, Any]:
        ticker = subject.ticker.strip().upper()
        allowed_through = d_minus_one_market_data.get("allowed_through")
        base = {
            "ticker": subject.ticker,
            "allowed_through": allowed_through,
            "source_name": d_minus_one_market_data.get("source_name"),
        }
        if not ticker:
            return {
                **base,
                "status": "unavailable",
                "reason": "ticker_not_resolved_for_candidate_discovery",
            }
        if ticker in {"UNKNOWN", "UNVERIFIED"}:
            return {
                **base,
                "status": "unavailable",
                "reason": "ticker_not_verified",
            }
        snapshots = d_minus_one_market_data.get("snapshots")
        if isinstance(snapshots, list):
            for snapshot in snapshots:
                if not isinstance(snapshot, dict):
                    continue
                snapshot_ticker = str(snapshot.get("ticker") or "").strip().upper()
                if snapshot_ticker == ticker:
                    return {
                        **base,
                        "status": "snapshot",
                        "snapshot": snapshot,
                    }
        skipped = d_minus_one_market_data.get("skipped_tickers")
        if isinstance(skipped, list):
            for skipped_row in skipped:
                if not isinstance(skipped_row, dict):
                    continue
                skipped_ticker = str(skipped_row.get("ticker") or "").strip().upper()
                if skipped_ticker == ticker:
                    return {
                        **base,
                        "status": "unavailable",
                        "reason": str(skipped_row.get("reason") or "unknown"),
                    }
        return {
            **base,
            "status": "unavailable",
            "reason": "d_minus_one_snapshot_not_collected_for_subject",
        }

    def _candidate_verification_dimensions(
        self,
        *,
        subject: CandidateWebCheckSubject,
        accepted_source_ids: Sequence[str],
    ) -> list[CandidateVerificationDimension]:
        dimensions: list[CandidateVerificationDimension] = []
        for name in CANDIDATE_WEB_VERIFICATION_FOCUS:
            status = CandidateVerificationStatus.SOURCE_COLLECTED
            notes = ["cutoff-safe web source collected for final synthesis"]
            if not accepted_source_ids:
                status = CandidateVerificationStatus.NO_CUTOFF_SAFE_SOURCE
                notes = ["no cutoff-safe web source collected for this dimension"]
            elif (
                name == "listed_security_and_exact_ticker"
                and subject.subject_type == "candidate_expansion"
                and not subject.ticker
            ):
                status = CandidateVerificationStatus.NEEDS_COMPANY_DISCOVERY
                notes = ["expansion subject has no confirmed ticker yet; web/company discovery must resolve it"]
            dimensions.append(
                CandidateVerificationDimension(
                    name=name,
                    status=status,
                    evidence_source_ids=list(accepted_source_ids),
                    notes=notes,
                )
            )
        return dimensions

    def _candidate_verification_uncertainties(
        self,
        *,
        subject: CandidateWebCheckSubject,
        accepted_source_ids: Sequence[str],
        excluded_source_ids: Sequence[str],
    ) -> list[str]:
        uncertainties: list[str] = []
        if not accepted_source_ids:
            uncertainties.append("no cutoff-safe web source was collected")
        if excluded_source_ids:
            uncertainties.append("some web sources were excluded as cutoff-unsafe")
        if subject.subject_type == "candidate_expansion" and not subject.ticker:
            uncertainties.append("exact listed security and ticker remain unresolved")
        if subject.expansion_path == CandidateExpansionPath.CONTINUATION:
            uncertainties.append("continuation must remain limited to D-1 market data")
        return uncertainties

    def _candidate_web_check_subjects(
        self,
        prediction: BlindPrediction,
        manifest: ContextManifest,
    ) -> list[CandidateWebCheckSubject]:
        subjects: list[CandidateWebCheckSubject] = [
            CandidateWebCheckSubject(
                subject_type="final_candidate",
                rank=candidate.rank,
                ticker=candidate.ticker,
                company_name=candidate.company_name,
                path_type=str(candidate.path_type),
                thesis=candidate.thesis,
                why_now=candidate.why_now,
            )
            for candidate in sorted(prediction.candidates, key=lambda item: item.rank)
        ]
        expansion = self._read_candidate_expansion_context(manifest)
        findings = expansion.get("findings") if isinstance(expansion, dict) else None
        if not isinstance(findings, list):
            return subjects
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            path = str(finding.get("path") or "UNKNOWN")
            candidate_names = _string_values(finding.get("candidate_names"))
            if not candidate_names:
                candidate_names = _string_values(finding.get("sector_hypotheses"))[:1]
            if not candidate_names:
                candidate_names = [f"{path}_DISCOVERY_REQUIRED"]
            for candidate_name in candidate_names:
                subjects.append(
                    CandidateWebCheckSubject(
                        subject_type="candidate_expansion",
                        rank=0,
                        ticker="",
                        company_name=candidate_name,
                        path_type=path,
                        thesis=str(finding.get("hypothesis") or ""),
                        why_now=str(finding.get("hypothesis") or ""),
                        expansion_path=path,
                        expansion_hypothesis=str(finding.get("hypothesis") or ""),
                        investigation_questions=tuple(_string_values(finding.get("investigation_questions"))[:5]),
                        sector_hypotheses=tuple(_string_values(finding.get("sector_hypotheses"))[:5]),
                    )
                )
        return _dedupe_candidate_web_check_subjects(subjects)

    def _candidate_web_check_query(self, subject: CandidateWebCheckSubject) -> str:
        focus = " ".join(
            [
                subject.company_name,
                subject.ticker,
                subject.path_type,
                subject.thesis,
                subject.why_now,
                " ".join(subject.investigation_questions),
                " ".join(subject.sector_hypotheses),
            ]
        )
        return (
            "candidate verification listed security exact ticker business location "
            "customer supply chain relation prior market narrative theme memory "
            "current news relation name similarity recent disclosure news market cap "
            "shares outstanding D-1 trading value turnover limit up multi-day "
            f"pre-absorption liquidity competing leaders {focus[:700]}"
        )

    def _candidate_web_check_row(
        self,
        result: WebSearchResult,
        *,
        subject: CandidateWebCheckSubject,
        manifest: ContextManifest,
        query: str,
        cutoff_at: datetime,
        opened_text: str,
    ) -> dict[str, Any]:
        source_row = self._web_source_row(
            result,
            query=query,
            cutoff_at=cutoff_at,
            opened_text=opened_text,
        )
        return {
            **source_row,
            "schema_version": "nslab.candidate_web_check.v1",
            "run_id": manifest.run_id,
            "candidate_subject_type": subject.subject_type,
            "candidate_rank": subject.rank,
            "candidate_ticker": subject.ticker,
            "candidate_company_name": subject.company_name,
            "candidate_path_type": subject.path_type,
            "candidate_expansion_path": subject.expansion_path,
            "candidate_expansion_hypothesis": subject.expansion_hypothesis,
            "candidate_investigation_questions": list(subject.investigation_questions),
            "candidate_sector_hypotheses": list(subject.sector_hypotheses),
            "verification_focus": list(CANDIDATE_WEB_VERIFICATION_FOCUS),
        }

    def _excluded_candidate_web_check_row(
        self,
        exclusion: WebSearchExclusion,
        *,
        subject: CandidateWebCheckSubject,
        manifest: ContextManifest,
        query: str,
        cutoff_at: datetime,
    ) -> dict[str, Any]:
        row = self._excluded_web_source_row(
            exclusion,
            query=query,
            cutoff_at=cutoff_at,
        )
        return {
            **row,
            "schema_version": "nslab.excluded_candidate_web_check.v1",
            "run_id": manifest.run_id,
            "candidate_subject_type": subject.subject_type,
            "candidate_rank": subject.rank,
            "candidate_ticker": subject.ticker,
            "candidate_company_name": subject.company_name,
            "candidate_path_type": subject.path_type,
            "candidate_expansion_path": subject.expansion_path,
        }

    def _write_source_ledger_artifact(
        self,
        *,
        news_items: list[NewsItem],
        prediction: BlindPrediction,
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> None:
        item_by_event_id = {item.event_id: item for item in news_items}
        used_event_ids: list[str] = []
        for sector in prediction.dominant_sectors:
            used_event_ids.extend(sector.triggering_events)
        for candidate in prediction.candidates:
            used_event_ids.extend(candidate.event_ids)
            used_event_ids.extend(
                url.removeprefix("news://") for url in candidate.source_urls if url.startswith("news://")
            )
        used_event_ids = _unique_preserving_order(
            [event_id for event_id in used_event_ids if event_id in item_by_event_id]
        )
        if not used_event_ids and news_items:
            used_event_ids = [news_items[0].event_id]

        retrieved_at = now_kst()
        rows: list[dict[str, Any]] = []
        for event_id in used_event_ids:
            item = item_by_event_id[event_id]
            provenance = item.provenance[0] if item.provenance else None
            rows.append(
                {
                    "schema_version": "nslab.source_ledger.v1",
                    "run_id": manifest.run_id,
                    "source_id": item.source_id,
                    "source_type": "news_csv_row",
                    "title": item.title,
                    "publisher": None,
                    "url": provenance.uri if provenance else f"news://{item.event_id}",
                    "source_url": provenance.uri if provenance else f"news://{item.event_id}",
                    "published_at": item.published_at.isoformat(),
                    "collected_at": (item.collected_at.isoformat() if item.collected_at is not None else None),
                    "collected_at_present": item.collected_at is not None,
                    "retrieved_at": retrieved_at.isoformat(),
                    "time_verified": True,
                    "available_before_cutoff": item.published_at <= cutoff_at,
                    "usage_phase": "BLIND",
                    "input_row_ids": [item.row_number],
                    "event_ids": [item.event_id],
                    "content_sha256": sha256_text(item.combined_text),
                    "notes": (
                        "Cutoff-safe blind news source; full body remains in the input CSV "
                        "and is not duplicated in source_ledger."
                    ),
                }
            )
        rows.extend(self._web_source_ledger_rows(manifest, retrieved_at=retrieved_at))
        rows.extend(self._candidate_web_check_ledger_rows(manifest, retrieved_at=retrieved_at))

        artifact_relative = Path("runs") / "checkpoints" / "source_ledger" / manifest.run_id / "source_ledger.jsonl"
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        manifest.source_ledger_artifact = artifact_relative.as_posix()
        manifest.source_ledger_sha256 = sha256_text(payload)
        manifest.source_ledger_entry_count = len(rows)
        manifest.source_ledger_summary = {
            "total_sources": len(rows),
            "blind_sources": sum(1 for row in rows if row["usage_phase"] == "BLIND"),
            "outcome_sources": 0,
            "postmortem_sources": 0,
        }

    def _web_source_ledger_rows(
        self,
        manifest: ContextManifest,
        *,
        retrieved_at: datetime,
    ) -> list[dict[str, Any]]:
        if not manifest.web_source_artifact:
            return []
        artifact_path = self.root / manifest.web_source_artifact
        if not artifact_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in artifact_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            rows.append(
                {
                    "schema_version": "nslab.source_ledger.v1",
                    "run_id": manifest.run_id,
                    "source_id": payload["source_id"],
                    "source_type": "web_search_result",
                    "title": payload["title"],
                    "publisher": None,
                    "url": payload["url"],
                    "source_url": payload.get("source_url", payload["url"]),
                    "published_at": payload["published_at"],
                    "timestamp_precision": payload.get("timestamp_precision"),
                    "retrieved_at": retrieved_at.isoformat(),
                    "time_verified": payload["time_verified"],
                    "available_before_cutoff": payload["available_before_cutoff"],
                    "usage_phase": "BLIND",
                    "input_row_ids": [],
                    "event_ids": [],
                    "content_sha256": payload["content_sha256"],
                    "notes": (
                        "Cutoff-safe web source admitted by TemporalWebGuard; body/content "
                        "is represented only by hashes in the source ledger."
                    ),
                }
            )
        return rows

    def _candidate_web_check_ledger_rows(
        self,
        manifest: ContextManifest,
        *,
        retrieved_at: datetime,
    ) -> list[dict[str, Any]]:
        if not manifest.candidate_web_check_artifact:
            return []
        artifact_path = self.root / manifest.candidate_web_check_artifact
        if not artifact_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in artifact_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            rows.append(
                {
                    "schema_version": "nslab.source_ledger.v1",
                    "run_id": manifest.run_id,
                    "source_id": payload["source_id"],
                    "source_type": "candidate_web_check",
                    "title": payload["title"],
                    "publisher": None,
                    "url": payload["url"],
                    "source_url": payload.get("source_url", payload["url"]),
                    "published_at": payload["published_at"],
                    "timestamp_precision": payload.get("timestamp_precision"),
                    "retrieved_at": retrieved_at.isoformat(),
                    "time_verified": payload["time_verified"],
                    "available_before_cutoff": payload["available_before_cutoff"],
                    "usage_phase": "BLIND",
                    "input_row_ids": [],
                    "event_ids": [],
                    "candidate_rank": payload["candidate_rank"],
                    "candidate_subject_type": payload.get("candidate_subject_type"),
                    "candidate_company_name": payload["candidate_company_name"],
                    "candidate_ticker": payload["candidate_ticker"],
                    "candidate_path_type": payload.get("candidate_path_type"),
                    "candidate_expansion_path": payload.get("candidate_expansion_path"),
                    "content_sha256": payload["content_sha256"],
                    "notes": (
                        "Cutoff-safe candidate-specific web verification source; "
                        "opened content is represented only by hashes and excerpt artifacts."
                    ),
                }
            )
        return rows

    def _write_blind_seal_artifacts(
        self,
        *,
        prediction: BlindPrediction,
        prediction_path: Path,
        manifest: ContextManifest,
    ) -> None:
        if prediction.sealed_at is None or prediction.blind_artifact_sha256 is None:
            raise ValueError("prediction must be sealed before writing blind seal artifacts")
        prediction_relative = prediction_path.relative_to(self.root).as_posix()
        receipt = {
            "schema_version": "nslab.blind_seal_receipt.v1",
            "run_id": manifest.run_id,
            "prediction_id": prediction.prediction_id,
            "trade_date": prediction.trade_date.isoformat(),
            "cutoff_at": prediction.cutoff_at.isoformat(),
            "sealed_at": prediction.sealed_at.isoformat(),
            "phase": "BLIND_SEALED",
            "blind_context_mode": manifest.blind_context_mode,
            "evidence_policy": manifest.evidence_policy,
            "web_provider": manifest.web_provider,
            "web_required": manifest.web_required,
            "blind_artifact_sha256": prediction.blind_artifact_sha256,
            "blind_prediction_path": prediction_relative,
            "row_disposition_sha256": manifest.row_disposition_sha256,
            "row_disposition_coverage_ratio": manifest.row_disposition_coverage_ratio,
            "source_ledger_sha256": manifest.source_ledger_sha256,
            "no_d_outcome_exposed": manifest.no_d_outcome_exposed,
            "validation": {
                "blind_web_search_call_count": manifest.blind_web_search_call_count,
                "external_web_evidence_count": (manifest.external_web_evidence_count),
                "blind_price_repository_access_count": (manifest.blind_price_repository_access_count),
                "blind_current_price_access_count": manifest.blind_current_price_access_count,
                "canonical_blind_hash_verified": True,
            },
        }
        receipt_relative = Path("runs") / "checkpoints" / "blind_seal" / manifest.run_id / "blind_seal_receipt.json"
        phase_relative = Path("runs") / "checkpoints" / "phase_state" / manifest.run_id / "phase_state.json"
        receipt_path = self.root / receipt_relative
        phase_path = self.root / phase_relative
        write_json(receipt_path, receipt)
        receipt_sha256 = sha256_text(receipt_path.read_text(encoding="utf-8"))
        phase_state = {
            "schema_version": "nslab.phase_state.v1",
            "run_id": manifest.run_id,
            "phase": "BLIND_SEALED",
            "completed_phases": [f"PHASE_A_{manifest.blind_context_mode}"],
            "trade_date": prediction.trade_date.isoformat(),
            "cutoff_at": prediction.cutoff_at.isoformat(),
            "sealed_at": prediction.sealed_at.isoformat(),
            "blind_seal_receipt_sha256": receipt_sha256,
        }
        write_json(phase_path, phase_state)
        manifest.blind_artifact_sha256 = prediction.blind_artifact_sha256
        manifest.blind_seal_receipt_artifact = receipt_relative.as_posix()
        manifest.blind_seal_receipt_sha256 = receipt_sha256
        manifest.phase_state_artifact = phase_relative.as_posix()
        manifest.phase_state_sha256 = sha256_text(phase_path.read_text(encoding="utf-8"))

    def _filter_retrieved_ids_available_as_of(
        self,
        retrieved_ids: list[str],
        *,
        cutoff_at: datetime,
    ) -> tuple[list[str], list[str]]:
        store = ResearchStore(self.root)
        included: list[str] = []
        excluded: list[str] = []
        seen: set[str] = set()
        for episode_id in retrieved_ids:
            if episode_id in seen:
                continue
            seen.add(episode_id)
            try:
                episode = store.get_episode(episode_id)
            except FileNotFoundError:
                excluded.append(episode_id)
                continue
            if is_available_as_of(episode.available_from, cutoff_at):
                included.append(episode_id)
            else:
                excluded.append(episode_id)
        return included, excluded

    def _search_memory_records(
        self,
        *,
        query: str,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[str]:
        search_records = getattr(self.retrieval, "search_records", None)
        if not callable(search_records):
            return []
        result = search_records(query, limit=limit, **(filters or {}))
        if not isinstance(result, list):
            return []
        return [record_id for record_id in result if isinstance(record_id, str)]

    def _filter_retrieved_record_ids_available_as_of(
        self,
        retrieved_ids: list[str],
        *,
        cutoff_at: datetime,
    ) -> tuple[list[str], list[str]]:
        store = BrainRecordStore(self.root)
        included: list[str] = []
        excluded: list[str] = []
        seen: set[str] = set()
        for record_id in retrieved_ids:
            if record_id in seen:
                continue
            seen.add(record_id)
            try:
                record = store.get_record(record_id)
            except FileNotFoundError:
                excluded.append(record_id)
                continue
            if is_available_as_of(record.available_from, cutoff_at):
                included.append(record_id)
            else:
                excluded.append(record_id)
        return included, excluded

    async def _run_final_synthesis(
        self,
        *,
        prediction: BlindPrediction,
        manifest: ContextManifest,
        news_texts: list[str],
        event_ids: list[str],
        retrieved_episode_ids: list[str],
        excluded_source_ids: list[str],
        first_pass_mechanisms: list[str],
        red_team_artifact: RedTeamArtifact,
        d_minus_one_market_data: dict[str, Any],
        company_memory_context: list[dict[str, Any]],
        market_memory_context: list[dict[str, Any]],
    ) -> tuple[BlindPrediction, str, int]:
        payload = self._build_final_synthesis_payload(
            prediction=prediction,
            manifest=manifest,
            news_texts=news_texts,
            first_pass_mechanisms=first_pass_mechanisms,
            red_team_artifact=red_team_artifact,
            d_minus_one_market_data=d_minus_one_market_data,
            company_memory_context=company_memory_context,
            market_memory_context=market_memory_context,
        )
        self._write_final_synthesis_context_artifact(
            manifest=manifest,
            payload=payload,
        )
        prompt = self._build_final_synthesis_prompt(payload)
        prompt_tokens = count_provider_tokens(self.llm, prompt)
        if (
            prompt_tokens > self.settings.limits.final_synthesis_token_budget
            and self._final_synthesis_token_budget_is_blocking(manifest)
        ):
            manifest.errors.append(
                "final synthesis prompt exceeds token budget: "
                f"{prompt_tokens} > {self.settings.limits.final_synthesis_token_budget}"
            )
            raise FinalSynthesisBudgetError(manifest.errors[-1])
        if prompt_tokens > self.settings.limits.final_synthesis_token_budget:
            manifest.final_synthesis_context_summary.update(
                {
                    "quality_full_token_budget_observation": "EXCEEDED_NON_BLOCKING",
                    "observed_prompt_tokens": prompt_tokens,
                    "configured_token_budget": (self.settings.limits.final_synthesis_token_budget),
                }
            )
        prompt_sha256 = sha256_text(prompt)
        try:
            synthesized = await self.llm.generate_structured(
                prompt=prompt,
                response_model=BlindPrediction,
                purpose="final_synthesis",
            )
        except NotImplementedError:
            synthesized = prediction
        if not synthesized.candidates:
            synthesized = prediction
        phase7_memory = self._phase7_final_memory_context(manifest)
        if phase7_memory is not None:
            synthesized, matched_candidate_count = self._bind_verified_final_candidate_identities(
                source_prediction=prediction,
                synthesized_prediction=synthesized,
            )
            manifest.final_synthesis_context_summary.update(
                {
                    "verified_candidate_identity_binding": True,
                    "source_candidate_count": len(prediction.candidates),
                    "synthesized_identity_match_count": matched_candidate_count,
                    "rejected_replacement_candidate_count": (len(prediction.candidates) - matched_candidate_count),
                }
            )
            self._require_phase7_final_candidate_identity(
                source_prediction=prediction,
                synthesized_prediction=synthesized,
                manifest=manifest,
            )
            positive_record_ids = self._phase7_preferred_record_ids(
                phase7_memory,
                role_record_ids=phase7_memory.supporting_record_ids,
            )
            negative_record_ids = self._phase7_preferred_record_ids(
                phase7_memory,
                role_record_ids=phase7_memory.contradicting_record_ids,
            )
            positive_episode_ids: list[str] = []
            negative_episode_ids: list[str] = []
        else:
            prediction_retrieved_record_ids = self._prediction_retrieved_record_ids(manifest)
            positive_record_ids, negative_record_ids = self._prediction_record_polarities(
                prediction_retrieved_record_ids
            )
            positive_episode_ids = manifest.retrieved_episode_ids[:3]
            negative_episode_ids = manifest.counterexample_episode_ids[:3]
        normalized = self._normalize_prediction(
            synthesized,
            trade_date=prediction.trade_date,
            cutoff_at=prediction.cutoff_at,
            event_ids=event_ids,
            excluded_source_ids=excluded_source_ids,
            prompt=prompt,
            purpose="final_synthesis",
            default_positive_case_ids=positive_episode_ids,
            default_negative_case_ids=negative_episode_ids,
            default_positive_record_ids=positive_record_ids[:5],
            default_negative_record_ids=negative_record_ids[:5],
        )
        if phase7_memory is not None:
            normalized, rejected_memory_reference_count = self._bind_phase7_memory_provenance(
                prediction=normalized,
                context=phase7_memory,
                preferred_positive_record_ids=positive_record_ids,
                preferred_negative_record_ids=negative_record_ids,
            )
            manifest.final_synthesis_context_summary.update(
                {
                    "phase7_memory_provenance_binding": True,
                    "rejected_unselected_memory_reference_count": (rejected_memory_reference_count),
                }
            )
            self._require_phase7_final_memory_ids(normalized, phase7_memory)
        normalized = normalized.model_copy(update={"context_manifest_id": manifest.run_id})
        if not normalized.blind_analysis.open_world_mechanisms:
            normalized = normalized.model_copy(
                update={
                    "blind_analysis": normalized.blind_analysis.model_copy(
                        update={"open_world_mechanisms": first_pass_mechanisms}
                    )
                }
            )
        return normalized, prompt_sha256, prompt_tokens

    @staticmethod
    def _bind_phase7_memory_provenance(
        *,
        prediction: BlindPrediction,
        context: DailyMemoryContext,
        preferred_positive_record_ids: list[str],
        preferred_negative_record_ids: list[str],
    ) -> tuple[BlindPrediction, int]:
        supporting = set(context.supporting_record_ids)
        contradicting = set(context.contradicting_record_ids)
        unexplained = set(context.unexplained_record_ids)
        selected = supporting | contradicting | unexplained
        preferred_positive = [record_id for record_id in preferred_positive_record_ids if record_id in supporting][:5]
        preferred_negative = [record_id for record_id in preferred_negative_record_ids if record_id in contradicting][
            :5
        ]
        rejected_count = 0
        candidates: list[Candidate] = []
        for candidate in prediction.candidates:
            original_record_ids = {
                *candidate.prior_positive_record_ids,
                *candidate.prior_negative_record_ids,
                *candidate.memory_record_ids,
            }
            positive = [
                record_id for record_id in candidate.prior_positive_record_ids if record_id in supporting
            ] or preferred_positive
            negative = [
                record_id for record_id in candidate.prior_negative_record_ids if record_id in contradicting
            ] or preferred_negative
            other_selected = [record_id for record_id in candidate.memory_record_ids if record_id in unexplained]
            memory_record_ids = _unique_preserving_order([*positive, *negative, *other_selected])
            rejected_count += len(original_record_ids - selected)
            rejected_count += len(
                {
                    *candidate.prior_positive_cases,
                    *candidate.prior_negative_cases,
                    *candidate.memory_episode_ids,
                }
            )
            candidates.append(
                candidate.model_copy(
                    update={
                        "prior_positive_cases": [],
                        "prior_negative_cases": [],
                        "memory_episode_ids": [],
                        "prior_positive_record_ids": positive,
                        "prior_negative_record_ids": negative,
                        "memory_record_ids": memory_record_ids,
                    }
                )
            )
        sectors: list[DominantSectorHypothesis] = []
        for sector in prediction.dominant_sectors:
            original_record_ids = {
                *sector.supporting_record_ids,
                *sector.contradicting_record_ids,
            }
            sector_supporting = [
                record_id for record_id in sector.supporting_record_ids if record_id in supporting
            ] or preferred_positive
            sector_contradicting = [
                record_id for record_id in sector.contradicting_record_ids if record_id in contradicting
            ] or preferred_negative
            rejected_count += len(original_record_ids - selected)
            rejected_count += len({*sector.supporting_cases, *sector.contradicting_cases})
            sectors.append(
                sector.model_copy(
                    update={
                        "supporting_cases": [],
                        "contradicting_cases": [],
                        "supporting_record_ids": sector_supporting,
                        "contradicting_record_ids": sector_contradicting,
                    }
                )
            )
        return (
            prediction.model_copy(
                update={
                    "candidates": candidates,
                    "dominant_sectors": sectors,
                }
            ),
            rejected_count,
        )

    @staticmethod
    def _bind_verified_final_candidate_identities(
        *,
        source_prediction: BlindPrediction,
        synthesized_prediction: BlindPrediction,
    ) -> tuple[BlindPrediction, int]:
        synthesized_by_identity = {
            _final_candidate_identity(candidate): candidate for candidate in synthesized_prediction.candidates
        }
        bound_candidates: list[Candidate] = []
        matched_count = 0
        for source in source_prediction.candidates:
            matched = synthesized_by_identity.get(_final_candidate_identity(source))
            if matched is None:
                bound_candidates.append(source)
                continue
            matched_count += 1
            bound_candidates.append(
                matched.model_copy(
                    update={
                        "rank": source.rank,
                        "ticker": source.ticker,
                        "company_name": source.company_name,
                        "path_type": source.path_type,
                        "event_ids": source.event_ids,
                        "claimed_theme_id": source.claimed_theme_id,
                        "claims_news_cause": source.claims_news_cause,
                        "source_urls": source.source_urls,
                        "prior_positive_cases": source.prior_positive_cases,
                        "prior_negative_cases": source.prior_negative_cases,
                        "prior_positive_record_ids": (source.prior_positive_record_ids),
                        "prior_negative_record_ids": (source.prior_negative_record_ids),
                        "memory_episode_ids": source.memory_episode_ids,
                        "memory_record_ids": source.memory_record_ids,
                        "provenance": source.provenance,
                    }
                )
            )
        return (
            synthesized_prediction.model_copy(update={"candidates": bound_candidates}),
            matched_count,
        )

    def _phase7_final_memory_context(
        self,
        manifest: ContextManifest,
    ) -> DailyMemoryContext | None:
        artifact = manifest.daily_memory_context_artifact
        if not artifact:
            return None
        path = (self.root / artifact).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("Phase 7 daily memory path escapes the project root") from exc
        if (
            not path.is_file()
            or not manifest.daily_memory_context_sha256
            or file_sha256(path) != manifest.daily_memory_context_sha256
        ):
            raise ValueError("Phase 7 daily memory artifact is missing or stale")
        context = DailyMemoryContext.model_validate(read_json(path))
        if context.run_id != manifest.run_id or context.cutoff_at != manifest.cutoff_at:
            raise ValueError("Phase 7 daily memory identity mismatch")
        return context

    def _phase7_preferred_record_ids(
        self,
        context: DailyMemoryContext,
        *,
        role_record_ids: list[str],
    ) -> list[str]:
        allowed = set(role_record_ids)
        runtime_selected: list[str] = []
        for reference in context.runtime_evidence_traces:
            trace = RuntimeRetrievalTrace.model_validate(read_json(self.root / reference.artifact_path))
            runtime_selected.extend(
                row.record_id
                for row in trace.rows
                if (row.record_id in allowed and "LANE_SELECTED" in row.stages and "MEMO_REFERENCED" in row.stages)
            )
        return _unique_preserving_order([*runtime_selected, *role_record_ids])

    def _require_phase7_final_candidate_identity(
        self,
        *,
        source_prediction: BlindPrediction,
        synthesized_prediction: BlindPrediction,
        manifest: ContextManifest,
    ) -> None:
        source_keys = [_final_candidate_identity(item) for item in source_prediction.candidates]
        synthesized_keys = [_final_candidate_identity(item) for item in synthesized_prediction.candidates]
        if len(source_keys) != len(set(source_keys)) or synthesized_keys != source_keys:
            raise ValueError("Phase 7 final synthesis changed the verified candidate identity set")
        verification_path = manifest.candidate_verification_artifact
        graph_path = manifest.beneficiary_graph_artifact
        if not verification_path or not graph_path:
            raise ValueError("Phase 7 final candidates require verification and graph artifacts")
        verification_resolved = (self.root / verification_path).resolve()
        graph_resolved = (self.root / graph_path).resolve()
        try:
            verification_resolved.relative_to(self.root.resolve())
            graph_resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("Phase 7 final candidate artifact escapes the project root") from exc
        if not manifest.candidate_verification_sha256:
            raise ValueError("Phase 7 final candidate verification hash binding is missing")
        if not verification_resolved.is_file():
            raise ValueError("Phase 7 final candidate verification artifact is missing")
        if sha256_text(verification_resolved.read_text(encoding="utf-8")) != manifest.candidate_verification_sha256:
            raise ValueError("Phase 7 final candidate verification artifact is stale")
        if not manifest.beneficiary_graph_sha256:
            raise ValueError("Phase 7 beneficiary graph hash binding is missing")
        if not graph_resolved.is_file():
            raise ValueError("Phase 7 beneficiary graph artifact is missing")
        if sha256_text(graph_resolved.read_text(encoding="utf-8")) != manifest.beneficiary_graph_sha256:
            raise ValueError("Phase 7 beneficiary graph artifact is stale")
        verification = CandidateVerificationReview.model_validate(read_json(verification_resolved))
        graph = BeneficiaryGraphArtifact.model_validate(read_json(graph_resolved))
        if (
            verification.run_id != manifest.run_id
            or verification.cutoff_at != manifest.cutoff_at
            or graph.run_id != manifest.run_id
            or graph.cutoff_at != manifest.cutoff_at
        ):
            raise ValueError("Phase 7 final candidate artifact identity mismatch")
        verification_keys = {
            (
                item.candidate_rank,
                item.candidate_ticker.strip().upper(),
                item.candidate_company_name.strip(),
                item.candidate_path_type.strip().upper(),
            )
            for item in verification.findings
            if item.subject_type == "final_candidate"
        }
        graph_keys = {
            (
                item.candidate_rank,
                item.ticker.strip().upper(),
                item.company_name.strip(),
                item.candidate_path_type.strip().upper(),
            )
            for item in graph.paths
        }
        if not set(source_keys).issubset(verification_keys & graph_keys):
            raise ValueError("Phase 7 final candidate is not closed by verification and graph evidence")

    @staticmethod
    def _require_phase7_final_memory_ids(
        prediction: BlindPrediction,
        context: DailyMemoryContext,
    ) -> None:
        supporting = set(context.supporting_record_ids)
        contradicting = set(context.contradicting_record_ids)
        selected = supporting | contradicting | set(context.unexplained_record_ids)
        for candidate in prediction.candidates:
            if (
                not set(candidate.prior_positive_record_ids).issubset(supporting)
                or not set(candidate.prior_negative_record_ids).issubset(contradicting)
                or not set(candidate.memory_record_ids).issubset(selected)
                or candidate.memory_episode_ids
                or candidate.prior_positive_cases
                or candidate.prior_negative_cases
            ):
                raise ValueError("Phase 7 final candidate memory provenance is not selected")
        for sector in prediction.dominant_sectors:
            if (
                not set(sector.supporting_record_ids).issubset(supporting)
                or not set(sector.contradicting_record_ids).issubset(contradicting)
                or sector.supporting_cases
                or sector.contradicting_cases
            ):
                raise ValueError("Phase 7 final sector memory provenance is not selected")

    def _build_final_synthesis_payload(
        self,
        *,
        prediction: BlindPrediction,
        manifest: ContextManifest,
        news_texts: list[str],
        first_pass_mechanisms: list[str],
        red_team_artifact: RedTeamArtifact,
        d_minus_one_market_data: dict[str, Any],
        company_memory_context: list[dict[str, Any]],
        market_memory_context: list[dict[str, Any]],
    ) -> dict[str, Any]:
        shared_current_event = self._read_shared_downstream_context(manifest)
        payload: dict[str, Any] = {
            "schema": "nslab.blind_prediction.v1",
            "prompt_version": FINAL_SYNTHESIS_PROMPT_VERSION,
            "required_inputs": list(FINAL_SYNTHESIS_REQUIRED_INPUTS_V2),
            "run_id": manifest.run_id,
            "trade_date": prediction.trade_date.isoformat(),
            "cutoff_at": prediction.cutoff_at.isoformat(),
            "brain_compiler": {
                "mode": manifest.compiler_mode,
                "provider": manifest.brain_compiler_provider,
                "model": manifest.brain_compiler_model,
                "catalog_only": manifest.brain_compiler_catalog_only,
            },
            "additional_semantic_retrieval": self._read_semantic_retrieval_context(manifest),
            "semantic_cluster_coverage": self._read_semantic_cluster_coverage_context(manifest),
            "open_world_candidate_expansion": self._read_candidate_expansion_context(manifest),
            "web_research": {
                "queries": manifest.web_queries,
                "included_sources": manifest.web_sources,
                "sources": self._read_web_source_context(manifest),
                "excluded_after_cutoff_source_ids": manifest.excluded_web_source_ids,
            },
            "global_brain": self._read_brain_context(manifest),
            "all_shard_brains": self._read_shard_brain_context(manifest),
            "memory_coverage_manifest": self._memory_coverage_context(manifest),
            "retrieved_raw_episode_ids": manifest.retrieved_episode_ids,
            "excluded_retrieved_episode_ids": manifest.excluded_retrieved_episode_ids,
            "retrieved_record_ids": manifest.retrieved_record_ids,
            "excluded_retrieved_record_ids": manifest.excluded_retrieved_record_ids,
            "semantic_retrieval_record_ids": manifest.semantic_retrieval_record_ids,
            "excluded_semantic_retrieval_record_ids": (manifest.excluded_semantic_retrieval_record_ids),
            "semantic_cluster_coverage_ids": manifest.semantic_cluster_coverage_ids,
            "semantic_cluster_coverage_missing_ids": (manifest.semantic_cluster_coverage_missing_ids),
            "semantic_cluster_coverage_promoted_record_ids": (manifest.semantic_cluster_coverage_promoted_record_ids),
            "candidate_expansion_cluster_coverage_ids": (manifest.candidate_expansion_cluster_coverage_ids),
            "candidate_expansion_cluster_coverage_missing_ids": (manifest.candidate_expansion_uncovered_cluster_ids),
            "candidate_expansion_uncovered_cluster_ids": (manifest.candidate_expansion_uncovered_cluster_ids),
            "candidate_expansion_audit_only_cluster_ids": (manifest.candidate_expansion_audit_only_cluster_ids),
            "retrieved_raw_episodes": self._read_retrieved_episode_context(manifest),
            "retrieved_records": self._read_retrieved_record_context(manifest),
            "positive_cases": _candidate_case_refs(prediction, "prior_positive_cases"),
            "negative_cases": _candidate_case_refs(prediction, "prior_negative_cases"),
            "positive_record_ids": _candidate_case_refs(prediction, "prior_positive_record_ids"),
            "negative_record_ids": _candidate_case_refs(prediction, "prior_negative_record_ids"),
            "counterexamples": self._read_counterexample_context(manifest),
            "counterexample_record_ids": manifest.counterexample_record_ids,
            "counterexample_records": self._read_counterexample_record_context(manifest),
            "candidate_research": prediction.model_dump(mode="json"),
            "candidate_web_checks": self._read_candidate_web_check_context(manifest),
            "candidate_verification": self._read_candidate_verification_context(manifest),
            "red_team_output": red_team_artifact.model_dump(mode="json"),
            "d_minus_one_market_data": d_minus_one_market_data,
            "company_memory": self._company_memory_prompt_context(
                prediction=prediction,
                contexts=company_memory_context,
            ),
            "market_memory": market_memory_context,
        }
        if shared_current_event is not None:
            payload["shared_current_event_digest"] = shared_current_event
            payload["required_inputs"] = list(FINAL_SYNTHESIS_REQUIRED_INPUTS_SHARED_V2)
        else:
            payload["current_news"] = news_texts
            payload["open_world_first_analysis"] = (
                self._read_open_world_first_analysis_context(manifest) or first_pass_mechanisms
            )
            payload["event_clusters"] = self._read_event_cluster_context(manifest)
            payload["news_novelty_review"] = self._read_news_novelty_review_context(manifest)
        if manifest.daily_memory_context_artifact:
            payload["prompt_version"] = FINAL_SYNTHESIS_V3_PROMPT_VERSION
            payload["required_inputs"] = list(
                FINAL_SYNTHESIS_REQUIRED_INPUTS_SHARED_V3
                if shared_current_event is not None
                else FINAL_SYNTHESIS_REQUIRED_INPUTS_V3
            )
            for key in (
                "global_brain",
                "all_shard_brains",
                "retrieved_raw_episodes",
                "retrieved_records",
                "positive_cases",
                "negative_cases",
                "positive_record_ids",
                "negative_record_ids",
                "counterexamples",
                "counterexample_records",
            ):
                payload.pop(key, None)
            semantic_context = payload.get("additional_semantic_retrieval")
            if isinstance(semantic_context, dict):
                semantic_context.pop("episodes", None)
                semantic_context.pop("records", None)
            cluster_coverage = payload.get("semantic_cluster_coverage")
            if isinstance(cluster_coverage, dict):
                cluster_coverage.pop("promoted_records", None)
            payload["daily_memory_context"] = self._daily_memory_context_payload(manifest)
            payload["beneficiary_graph"] = self._beneficiary_graph_payload(manifest)
        return payload

    @staticmethod
    def _final_synthesis_token_budget_is_blocking(
        manifest: ContextManifest,
    ) -> bool:
        return manifest.llm_model_config.get("evaluation_profile") != "QUALITY_FULL"

    @staticmethod
    def _company_memory_prompt_context(
        *,
        prediction: BlindPrediction,
        contexts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidate_tickers = {
            candidate.ticker.strip().upper() for candidate in prediction.candidates if candidate.ticker.strip()
        }
        candidate_names = {
            candidate.company_name.strip() for candidate in prediction.candidates if candidate.company_name.strip()
        }
        selected: list[dict[str, Any]] = []
        population_refs: list[dict[str, str]] = []
        for context in contexts:
            path = str(context.get("path", ""))
            sha256 = str(context.get("sha256", ""))
            population_refs.append({"path": path, "sha256": sha256})
            memory = context.get("memory")
            if not isinstance(memory, dict):
                continue
            ticker = str(memory.get("ticker", "")).strip().upper()
            names = {
                str(memory.get("company_name", "")).strip(),
                *{str(alias).strip() for alias in memory.get("aliases", []) if str(alias).strip()},
            }
            if (ticker and ticker in candidate_tickers) or bool(names & candidate_names):
                selected.append(context)
        return {
            "policy": "FINAL_CANDIDATE_IDENTITY_MATCH_WITH_FULL_POPULATION_ROOT.v1",
            "population_count": len(contexts),
            "population_artifact_root_sha256": sha256_text(canonical_json(population_refs)),
            "candidate_tickers": sorted(candidate_tickers),
            "candidate_company_names": sorted(candidate_names),
            "selected_count": len(selected),
            "selected": selected,
            "unselected_count": len(contexts) - len(selected),
            "full_population_remains_in_context_manifest": True,
            "silent_truncation_used": False,
        }

    def _memory_coverage_context(
        self,
        manifest: ContextManifest,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "artifact_path": manifest.memory_coverage_manifest_artifact,
            "sha256": manifest.memory_coverage_manifest_sha256,
            "corpus_manifest_sha256": manifest.memory_coverage_corpus_sha256,
            "accepted_record_count": manifest.accepted_record_count,
            "available_record_count": manifest.available_record_count,
            "training_eligible_available_record_count": (manifest.training_eligible_available_record_count),
            "coverage_complete": False,
        }
        artifact_ref = manifest.memory_coverage_manifest_artifact
        if not artifact_ref:
            return context
        artifact_path = self.root / artifact_ref
        if not artifact_path.exists():
            return context
        payload = read_json(artifact_path)
        if isinstance(payload, dict):
            context["manifest"] = payload
            context["coverage_complete"] = payload.get("coverage_complete") is True
        return context

    def _build_beneficiary_graph_context(
        self,
        *,
        prediction: BlindPrediction,
        manifest: ContextManifest,
        company_memory_context: list[dict[str, Any]],
    ) -> None:
        if not manifest.event_cluster_manifest_artifact:
            raise ValueError("beneficiary graph requires the event cluster manifest")
        artifact, path = build_beneficiary_graph(
            self.root,
            run_id=manifest.run_id,
            cutoff_at=manifest.cutoff_at,
            event_cluster_manifest_path=(self.root / manifest.event_cluster_manifest_artifact),
            candidates=prediction.candidates,
            company_memory_context=company_memory_context,
        )
        manifest.bind_beneficiary_graph(
            artifact_path=relative_to_root(path, self.root),
            sha256=sha256_text(path.read_text(encoding="utf-8")),
        )
        manifest.daily_memory_context_summary["beneficiary_graph_path_count"] = artifact.path_count
        manifest.daily_memory_context_summary["beneficiary_graph_unresolved_candidate_count"] = len(
            artifact.unresolved_candidate_ids
        )

    def _build_pre_candidate_beneficiary_graph_context(
        self,
        *,
        manifest: ContextManifest,
    ) -> None:
        if not manifest.event_cluster_manifest_artifact:
            raise ValueError("retrieval-first graph requires event clusters")
        artifact, path = build_beneficiary_graph(
            self.root,
            run_id=manifest.run_id,
            cutoff_at=manifest.cutoff_at,
            event_cluster_manifest_path=(self.root / manifest.event_cluster_manifest_artifact),
            candidates=[],
            company_memory_context=[],
        )
        manifest.bind_beneficiary_graph(
            artifact_path=relative_to_root(path, self.root),
            sha256=file_sha256(path),
        )
        manifest.daily_memory_context_summary.update(
            {
                "retrieval_graph_artifact": relative_to_root(path, self.root),
                "retrieval_graph_sha256": file_sha256(path),
                "retrieval_graph_candidate_count": artifact.candidate_count,
                "retrieval_before_candidate_generation": True,
            }
        )

    def _bind_final_beneficiary_graph_to_daily_memory(
        self,
        *,
        manifest: ContextManifest,
    ) -> None:
        if not manifest.daily_memory_context_artifact or not manifest.beneficiary_graph_artifact:
            raise ValueError("retrieval-first final graph binding is incomplete")
        context, path = bind_final_beneficiary_graph_to_daily_context(
            self.root,
            context_path=self.root / manifest.daily_memory_context_artifact,
            beneficiary_graph_path=self.root / manifest.beneficiary_graph_artifact,
        )
        manifest.bind_daily_memory_context(
            artifact_path=relative_to_root(path, self.root),
            sha256=file_sha256(path),
        )
        manifest.daily_memory_context_summary.update(
            {
                "final_beneficiary_graph_bound": True,
                "estimated_token_count": context.estimated_token_count,
            }
        )

    def _candidate_generation_memory_context(
        self,
        manifest: ContextManifest,
    ) -> dict[str, Any]:
        if self.runtime_retrieval_variant != "v4" or not manifest.daily_memory_context_artifact:
            return {}
        context = self._daily_memory_context_payload(manifest)
        compact = context.get("compact_context")
        if not isinstance(compact, dict):
            raise ValueError("retrieval-first candidate context is missing compact memory")
        return {
            "policy": "OPEN_WORLD_FIRST_THEN_RETRIEVAL_BEFORE_CANDIDATES",
            "memory_used_as_candidate_gate": False,
            "memory_snapshot_id": context.get("memory_snapshot_id"),
            "runtime_retrieval_cluster_ids": context.get("runtime_retrieval_cluster_ids"),
            "runtime_evidence_traces": context.get("runtime_evidence_traces"),
            "runtime_evidence_memos": context.get("runtime_evidence_memos"),
            "compact_context": compact,
        }

    def _runtime_retrieval_scope_all_material_cluster_ids(
        self,
        *,
        manifest: ContextManifest,
    ) -> set[str]:
        event_manifest_path = self.root / str(manifest.event_cluster_manifest_artifact)
        event_manifest = EventClusterManifest.model_validate(read_json(event_manifest_path))
        selected = {
            cluster.cluster_id
            for cluster in event_manifest.clusters
            if cluster.disposition == "MATERIAL_FULL_RETRIEVAL"
        }
        scope_payload = {
            "schema_version": "nslab.runtime_retrieval_scope.v1",
            "run_id": manifest.run_id,
            "policy": "ALL_OPEN_WORLD_MATERIAL_CLUSTERS_BEFORE_CANDIDATES",
            "event_cluster_manifest": {
                "artifact_path": relative_to_root(event_manifest_path, self.root),
                "sha256": file_sha256(event_manifest_path),
            },
            "prediction_event_ids": [],
            "selected_cluster_ids": sorted(selected),
            "all_material_cluster_count": len(selected),
            "selected_cluster_count": len(selected),
            "memory_used_as_candidate_gate": False,
        }
        scope_path = (
            self.root
            / "runs"
            / "checkpoints"
            / "runtime_retrieval_scope"
            / manifest.run_id
            / "runtime_retrieval_scope.json"
        )
        write_json(scope_path, scope_payload)
        manifest.daily_memory_context_summary.update(
            {
                "runtime_retrieval_scope_artifact": relative_to_root(
                    scope_path,
                    self.root,
                ),
                "runtime_retrieval_scope_sha256": file_sha256(scope_path),
                "runtime_retrieval_scope_all_material_cluster_count": len(selected),
                "runtime_retrieval_scope_memory_candidate_gate": False,
                "runtime_retrieval_precedes_candidate_generation": True,
            }
        )
        return selected

    def _runtime_retrieval_scope_cluster_ids(
        self,
        *,
        manifest: ContextManifest,
        prediction: BlindPrediction,
    ) -> set[str]:
        event_manifest_path = self.root / str(manifest.event_cluster_manifest_artifact)
        event_manifest = EventClusterManifest.model_validate(read_json(event_manifest_path))
        event_ids = {event_id for candidate in prediction.candidates for event_id in candidate.event_ids} | {
            event_id for sector in prediction.dominant_sectors for event_id in sector.triggering_events
        }
        material_clusters = {
            cluster.cluster_id: cluster
            for cluster in event_manifest.clusters
            if cluster.disposition == "MATERIAL_FULL_RETRIEVAL"
        }
        selected = {
            cluster_id
            for cluster_id, cluster in material_clusters.items()
            if event_ids.intersection(cluster.member_event_ids)
        }
        graph_path = self.root / str(manifest.beneficiary_graph_artifact)
        graph = BeneficiaryGraphArtifact.model_validate(read_json(graph_path))
        selected.update(
            cluster_id
            for path in graph.paths
            for cluster_id in path.event_cluster_ids
            if cluster_id in material_clusters
        )
        if event_ids and not selected:
            raise ValueError("open-world prediction event provenance did not resolve to material clusters")
        scope_payload = {
            "schema_version": "nslab.runtime_retrieval_scope.v1",
            "run_id": manifest.run_id,
            "policy": "OPEN_WORLD_PREDICTION_EVENTS_BEFORE_MEMORY",
            "event_cluster_manifest": {
                "artifact_path": relative_to_root(event_manifest_path, self.root),
                "sha256": file_sha256(event_manifest_path),
            },
            "prediction_event_ids": sorted(event_ids),
            "selected_cluster_ids": sorted(selected),
            "all_material_cluster_count": len(material_clusters),
            "selected_cluster_count": len(selected),
            "memory_used_as_candidate_gate": False,
        }
        scope_path = (
            self.root
            / "runs"
            / "checkpoints"
            / "runtime_retrieval_scope"
            / manifest.run_id
            / "runtime_retrieval_scope.json"
        )
        write_json(scope_path, scope_payload)
        manifest.daily_memory_context_summary.update(
            {
                "runtime_retrieval_scope_artifact": relative_to_root(scope_path, self.root),
                "runtime_retrieval_scope_sha256": file_sha256(scope_path),
                "runtime_retrieval_scope_all_material_cluster_count": len(material_clusters),
                "runtime_retrieval_scope_memory_candidate_gate": False,
            }
        )
        return selected

    async def _maybe_build_daily_memory_context(
        self,
        *,
        manifest: ContextManifest,
        prediction: BlindPrediction | None,
    ) -> None:
        readiness = (
            inspect_verified_evaluation_memory_index(self.root)
            if self._evaluation_memory_snapshot is not None
            else inspect_current_memory_index(self.root)
        )
        if readiness.get("production_ready") is not True:
            manifest.daily_memory_context_summary.update(
                {
                    "status": "SKIPPED_PRODUCTION_MEMORY_NOT_READY",
                    "production_ready": False,
                }
            )
            return
        required_paths = {
            "news_coverage_manifest": manifest.news_coverage_manifest_artifact,
            "event_cluster_manifest": manifest.event_cluster_manifest_artifact,
            "event_clusters": manifest.event_cluster_artifact,
            "memory_coverage_manifest": manifest.memory_coverage_manifest_artifact,
            "beneficiary_graph": manifest.beneficiary_graph_artifact,
        }
        missing = sorted(key for key, value in required_paths.items() if not value)
        if missing:
            raise ValueError("daily memory context is missing required artifacts: " + ", ".join(missing))
        snapshot_payload = readiness.get("manifest")
        if not isinstance(snapshot_payload, dict):
            raise ValueError("production memory readiness omitted its manifest")
        embedding_method = snapshot_payload.get("embedding_model")
        if not isinstance(embedding_method, str) or not embedding_method.strip():
            raise ValueError("production memory snapshot embedding model is missing")
        active_embedding_method = production_embedding_method(
            self.settings,
            self.embedding_provider,
        )
        if embedding_method != active_embedding_method:
            raise ValueError("active embedding provider differs from the production memory snapshot")
        provider = AsyncEmbeddingProviderAdapter(
            self.embedding_provider,
            embedding_method=active_embedding_method,
            production_capability_attested=True,
        )
        memory_index = ProductionMemoryIndex(
            self.root,
            embedding_provider=provider,
            production=True,
        )
        if self.runtime_retrieval_variant == "v4":
            retrieval_cluster_ids = (
                self._runtime_retrieval_scope_all_material_cluster_ids(
                    manifest=manifest,
                )
                if prediction is None
                else self._runtime_retrieval_scope_cluster_ids(
                    manifest=manifest,
                    prediction=prediction,
                )
            )
        else:
            retrieval_cluster_ids = set()
        context, path = await asyncio.to_thread(
            build_daily_memory_context,
            self.root,
            memory_index=memory_index,
            run_id=manifest.run_id,
            trade_date=manifest.trade_date,
            cutoff_at=manifest.cutoff_at,
            corpus_manifest_sha256=str(manifest.memory_coverage_corpus_sha256),
            news_coverage_manifest_path=(self.root / str(required_paths["news_coverage_manifest"])),
            event_cluster_manifest_path=(self.root / str(required_paths["event_cluster_manifest"])),
            event_cluster_artifact_path=(self.root / str(required_paths["event_clusters"])),
            memory_coverage_manifest_path=(self.root / str(required_paths["memory_coverage_manifest"])),
            beneficiary_graph_path=(self.root / str(required_paths["beneficiary_graph"])),
            retrieval_cluster_ids=retrieval_cluster_ids,
            allow_distribution_shortfall=(manifest.llm_model_config.get("evaluation_profile") == "QUALITY_FULL"),
        )
        retrieval_results: list[RuntimeRetrievalBuildResult] = []
        for reference in context.runtime_retrieval_traces:
            trace_path = self.root / reference.artifact_path
            trace = RuntimeRetrievalTrace.model_validate(read_json(trace_path))
            retrieval_result = RuntimeRetrievalBuildResult(
                trace=trace,
                trace_path=trace_path,
                selected_record_ids=tuple(row.record_id for row in trace.rows if "LANE_SELECTED" in row.stages),
            )
            if not retrieval_result.selected_record_ids:
                raise ValueError(f"runtime retrieval trace selected no cutoff-safe evidence: {trace.cluster_id}")
            retrieval_results.append(retrieval_result)
        evidence_results: list[RuntimeEvidenceBuildResult] = []
        packed_evidence = None
        if retrieval_results:
            packed_evidence = await build_runtime_evidence_memos_packed(
                self.root,
                retrievals=retrieval_results,
                memory_index=memory_index,
                llm=self.llm,
            )
            evidence_results = list(packed_evidence.evidence_results)
        if evidence_results:
            assert packed_evidence is not None
            context, path = attach_runtime_evidence_to_daily_context(
                self.root,
                context_path=path,
                evidence_results=evidence_results,
                pack_manifest=packed_evidence.manifest,
                pack_manifest_path=packed_evidence.manifest_path,
            )
        manifest.bind_daily_memory_context(
            artifact_path=relative_to_root(path, self.root),
            sha256=sha256_text(path.read_text(encoding="utf-8")),
        )
        manifest.daily_memory_context_summary.update(
            {
                "status": "COMPLETE",
                "production_ready": True,
                "memory_snapshot_id": context.memory_snapshot_id,
                "material_event_cluster_count": len(context.material_event_cluster_ids),
                "uncovered_material_event_cluster_count": len(context.uncovered_material_event_cluster_ids),
                "population_count": len(context.population_manifests),
                "representative_set_count": len(context.representative_set_manifests),
                "category_guidance_count": len(context.category_guidance),
                "category_query_plan_count": len(context.category_query_plans),
                "runtime_retrieval_scope_cluster_count": len(retrieval_cluster_ids),
                "runtime_retrieval_variant": self.runtime_retrieval_variant,
                "runtime_retrieval_trace_count": len(context.runtime_retrieval_traces),
                "runtime_evidence_trace_count": len(context.runtime_evidence_traces),
                "runtime_evidence_memo_count": sum(
                    reference.item_count for reference in context.runtime_evidence_memos
                ),
                "runtime_evidence_assignment_count": (
                    context.runtime_evidence_assignment_count
                ),
                "runtime_evidence_unique_record_count": (
                    context.runtime_evidence_unique_record_count
                ),
                "runtime_evidence_packed_call_count": (
                    context.runtime_evidence_packed_call_count
                ),
                "runtime_evidence_avoided_payload_occurrence_count": (
                    context.runtime_evidence_avoided_payload_occurrence_count
                ),
                "runtime_selected_record_count": sum(
                    sum("LANE_SELECTED" in row.stages for row in result.trace.rows) for result in evidence_results
                ),
                "runtime_offline_unexposed_recovered_count": sum(
                    result.trace.offline_unexposed_recovered_count for result in evidence_results
                ),
                "runtime_online_full_scan_count": sum(
                    result.trace.online_full_scan_count for result in evidence_results
                ),
                "typed_trigger_evidence_count": sum(
                    len(
                        AdaptiveRetrievalTrace.model_validate(
                            read_json(self.root / reference.artifact_path)
                        ).trigger_evidence
                    )
                    for reference in context.adaptive_retrieval_traces
                ),
                "estimated_token_count": context.estimated_token_count,
                "context_complete": context.context_complete,
            }
        )
        manifest.llm_model_config["final_synthesis_prompt_version"] = FINAL_SYNTHESIS_V3_PROMPT_VERSION
        if isinstance(self.llm, TracingLLMProvider):
            self.llm.model_config["final_synthesis_prompt_version"] = FINAL_SYNTHESIS_V3_PROMPT_VERSION
            self.llm.purpose_metadata["final_synthesis"] = {"prompt_version": FINAL_SYNTHESIS_V3_PROMPT_VERSION}

    def _finalize_runtime_retrieval_traces(
        self,
        *,
        manifest: ContextManifest,
        prediction: BlindPrediction,
    ) -> None:
        context_ref = manifest.daily_memory_context_artifact
        if not context_ref:
            return
        context = DailyMemoryContext.model_validate(read_json(self.root / context_ref))
        if not context.runtime_evidence_traces:
            return
        final_references: list[dict[str, Any]] = []
        selected_count = 0
        cited_count = 0
        online_full_scan_count = 0
        for trace_reference, memo_reference in zip(
            context.runtime_evidence_traces,
            context.runtime_evidence_memos,
            strict=True,
        ):
            trace_path = self.root / trace_reference.artifact_path
            trace = RuntimeRetrievalTrace.model_validate(read_json(trace_path))
            memo_path = self.root / memo_reference.artifact_path
            memos = tuple(
                RuntimeEvidenceMemo.model_validate(json.loads(line))
                for line in memo_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            evidence = RuntimeEvidenceBuildResult(
                memos=memos,
                memo_path=memo_path,
                trace=trace,
                trace_path=trace_path,
            )
            final_trace, final_path = finalize_runtime_retrieval_trace(
                self.root,
                evidence=evidence,
                prediction=prediction,
            )
            selected_count += sum("LANE_SELECTED" in row.stages for row in final_trace.rows)
            cited_count += sum("FINAL_CITED" in row.stages for row in final_trace.rows)
            online_full_scan_count += final_trace.online_full_scan_count
            final_references.append(
                {
                    "cluster_id": final_trace.cluster_id,
                    "artifact_path": relative_to_root(final_path, self.root),
                    "sha256": file_sha256(final_path),
                    "selected_record_count": sum("LANE_SELECTED" in row.stages for row in final_trace.rows),
                    "final_cited_record_count": sum("FINAL_CITED" in row.stages for row in final_trace.rows),
                }
            )
        final_manifest = {
            "schema_version": "nslab.runtime_retrieval_final_manifest.v1",
            "run_id": manifest.run_id,
            "prediction_id": prediction.prediction_id,
            "trace_count": len(final_references),
            "selected_record_count": selected_count,
            "final_cited_record_count": cited_count,
            "final_citation_rate": (cited_count / selected_count if selected_count else 0.0),
            "traces": final_references,
            "blind_web_call_count": manifest.blind_web_search_call_count,
            "online_full_scan_count": online_full_scan_count,
        }
        final_manifest_path = (
            self.root
            / "runs"
            / "checkpoints"
            / "runtime_retrieval_v4"
            / manifest.run_id
            / "runtime_retrieval_final_manifest.json"
        )
        write_json(final_manifest_path, final_manifest)
        manifest.daily_memory_context_summary.update(
            {
                "runtime_retrieval_final_manifest_artifact": relative_to_root(final_manifest_path, self.root),
                "runtime_retrieval_final_manifest_sha256": file_sha256(final_manifest_path),
                "runtime_final_cited_record_count": cited_count,
                "runtime_final_citation_rate": final_manifest["final_citation_rate"],
            }
        )

    def _daily_memory_context_payload(
        self,
        manifest: ContextManifest,
    ) -> dict[str, Any]:
        artifact_ref = manifest.daily_memory_context_artifact
        if not artifact_ref or not manifest.daily_memory_context_sha256:
            raise ValueError("final synthesis v3 requires daily memory context")
        path = self.root / artifact_ref
        payload = read_json(path)
        if not isinstance(payload, dict):
            raise ValueError("daily memory context artifact is invalid")
        compact_ref = payload.get("compact_final_context")
        if not isinstance(compact_ref, dict):
            raise ValueError("daily memory compact context reference is missing")
        compact_path_value = compact_ref.get("artifact_path")
        compact_hash = compact_ref.get("sha256")
        if not isinstance(compact_path_value, str) or not isinstance(compact_hash, str):
            raise ValueError("daily memory compact context reference is invalid")
        compact_path = self.root / compact_path_value
        if file_sha256(compact_path) != compact_hash:
            raise ValueError("daily memory compact context hash mismatch")
        compact = read_json(compact_path)
        if not isinstance(compact, dict):
            raise ValueError("daily memory compact context is invalid")
        return {
            **phase7_daily_prompt_projection(
                daily=payload,
                compact=compact,
                artifact_path=artifact_ref,
                sha256=manifest.daily_memory_context_sha256,
            )
        }

    def _beneficiary_graph_payload(
        self,
        manifest: ContextManifest,
    ) -> dict[str, Any]:
        artifact_ref = manifest.beneficiary_graph_artifact
        if not artifact_ref or not manifest.beneficiary_graph_sha256:
            raise ValueError("final synthesis v3 requires beneficiary graph")
        path = self.root / artifact_ref
        payload = read_json(path)
        if not isinstance(payload, dict):
            raise ValueError("beneficiary graph artifact is invalid")
        return phase7_beneficiary_graph_prompt_projection(
            graph=payload,
            artifact_path=artifact_ref,
            sha256=manifest.beneficiary_graph_sha256,
        )

    def _build_final_synthesis_prompt(self, payload: dict[str, Any]) -> str:
        return (
            f"{self._load_synthesis_prompt().strip()}\n"
            "Return the final BlindPrediction. Keep qualitative confidence only, "
            "preserve red-team objections in candidate counterarguments, use only "
            "timestamp-verified web_research.sources, candidate_web_checks, "
            "candidate_verification, cutoff-safe company_memory, and "
            "cutoff-safe market_memory. Do not use "
            "category_brain_guidance as evidence; it is query guidance only. "
            "Every memory-dependent claim must cite the compact population or "
            "representative record provenance. When runtime_evidence_memos are "
            "present, every candidate or sector that uses memory must cite at least "
            "one of their source_record_ids in its record provenance. Preserve "
            "supporting, contradicting, and unresolved evidence separately. Do not use "
            "D-day prices, outcomes, unverified web results, or cutoff-after "
            "sources during BLIND.\n"
            "---FINAL_SYNTHESIS_PAYLOAD---\n"
            f"{canonical_json(payload)}"
        )

    def _write_final_synthesis_context_artifact(
        self,
        *,
        manifest: ContextManifest,
        payload: dict[str, Any],
    ) -> None:
        if manifest.shared_pre_retrieval_context_artifact:
            expected_shared_digest = self._read_shared_downstream_context(manifest)
            consumed_shared_digest = payload.get("shared_current_event_digest")
            reject_forbidden_blind_payload_keys(consumed_shared_digest)
            if expected_shared_digest is None or consumed_shared_digest != expected_shared_digest:
                raise ValueError("final synthesis consumed a different shared downstream digest")
        if manifest.d_minus_one_payload_sha256 is not None:
            consumed_payload = payload.get("d_minus_one_market_data")
            if not isinstance(consumed_payload, dict):
                raise ValueError("final synthesis omitted the shared D-1 payload")
            projection = DMinusOnePromptProjection.model_validate(consumed_payload)
            consumed_sha256 = sha256_text(canonical_json(consumed_payload))
            if (
                consumed_sha256 != manifest.d_minus_one_consumed_payload_sha256
                or projection.full_payload_sha256 != manifest.d_minus_one_payload_sha256
                or projection.full_context.sha256 != manifest.d_minus_one_context_sha256
                or projection.full_context.artifact_path != manifest.d_minus_one_context_artifact
                or projection.candidate_universe_root_sha256 != manifest.d_minus_one_candidate_universe_root_sha256
                or projection.full_snapshot_root_sha256 != manifest.d_minus_one_snapshot_root_sha256
                or projection.source_revision_sha256 != manifest.d_minus_one_source_revision_sha256
                or projection.snapshot_session_date != manifest.d_minus_one_snapshot_session_date
                or projection.projection_policy != manifest.d_minus_one_projection_policy
                or projection.projection_root_sha256 != manifest.d_minus_one_projection_root_sha256
                or len(projection.requested_tickers) != manifest.d_minus_one_projection_requested_ticker_count
                or len(projection.snapshots) != manifest.d_minus_one_projection_snapshot_count
                or len(projection.missing_tickers) != manifest.d_minus_one_projection_missing_ticker_count
            ):
                raise ValueError("final synthesis consumed a different D-1 prompt projection")
        summary = final_synthesis_input_summary(payload)
        artifact = FinalSynthesisContextArtifact(
            schema_version=(
                "nslab.final_synthesis_context.v3"
                if payload.get("prompt_version") == FINAL_SYNTHESIS_V3_PROMPT_VERSION
                else "nslab.final_synthesis_context.v2"
            ),
            run_id=manifest.run_id,
            prompt_version=str(payload.get("prompt_version")),
            required_inputs=string_list(payload.get("required_inputs")),
            payload_sha256=sha256_text(canonical_json(payload)),
            input_summary=summary,
            payload=payload,
        )
        artifact_path = (
            self.root
            / "runs"
            / "checkpoints"
            / "final_synthesis_context"
            / manifest.run_id
            / "final_synthesis_context.json"
        )
        write_json(artifact_path, artifact.model_dump(mode="json"))
        manifest.final_synthesis_context_artifact = artifact_path.relative_to(self.root).as_posix()
        manifest.final_synthesis_context_sha256 = sha256_text(artifact_path.read_text(encoding="utf-8"))
        manifest.final_synthesis_context_summary = summary

    def _read_event_cluster_context(self, manifest: ContextManifest) -> list[dict[str, Any]]:
        if not manifest.event_cluster_artifact:
            return []
        path = self.root / manifest.event_cluster_artifact
        if not path.exists():
            return [{"path": manifest.event_cluster_artifact, "missing": True}]
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
        return rows

    def _read_news_novelty_review_context(self, manifest: ContextManifest) -> dict[str, Any]:
        if not manifest.news_novelty_review_artifact:
            return {}
        path = self.root / manifest.news_novelty_review_artifact
        if not path.exists():
            return {"path": manifest.news_novelty_review_artifact, "missing": True}
        payload = read_json(path)
        return payload if isinstance(payload, dict) else {}

    def _read_shared_downstream_context(
        self,
        manifest: ContextManifest,
    ) -> dict[str, Any] | None:
        if not manifest.shared_pre_retrieval_context_artifact:
            return None
        if not manifest.shared_pre_retrieval_context_sha256:
            raise ValueError("shared downstream context has no sealed hash")
        context_reference = QualityArtifactReference(
            artifact_path=manifest.shared_pre_retrieval_context_artifact,
            sha256=manifest.shared_pre_retrieval_context_sha256,
        )
        context = SharedPreRetrievalContext.model_validate(
            _resolve_and_read_shared_reference(self.root, context_reference)
        )
        payload = _resolve_and_read_shared_reference(
            self.root,
            context.downstream_digest,
        )
        digest = SharedDownstreamDigest.model_validate(payload)
        if digest.context_id != context.context_id:
            raise ValueError("shared downstream digest identity drifted")
        return digest.model_dump(mode="json")

    def _read_semantic_retrieval_context(self, manifest: ContextManifest) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        if manifest.semantic_retrieval_artifact:
            path = self.root / manifest.semantic_retrieval_artifact
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rows.append(json.loads(line))
        episodes: list[dict[str, Any]] = []
        store = ResearchStore(self.root)
        for episode_id in manifest.semantic_retrieval_episode_ids:
            try:
                episode = store.get_episode(episode_id)
            except FileNotFoundError:
                episodes.append({"episode_id": episode_id, "missing": True})
                continue
            episodes.append(episode.model_dump(mode="json"))
        records: list[dict[str, Any]] = []
        record_store = BrainRecordStore(self.root)
        for record_id in manifest.semantic_retrieval_record_ids:
            try:
                record = record_store.get_record(record_id)
            except FileNotFoundError:
                records.append({"record_id": record_id, "missing": True})
                continue
            records.append(record.model_dump(mode="json"))
        return {
            "plan_artifact": manifest.semantic_retrieval_plan_artifact,
            "artifact": manifest.semantic_retrieval_artifact,
            "summary": manifest.semantic_retrieval_summary,
            "rows": rows,
            "included_episode_ids": manifest.semantic_retrieval_episode_ids,
            "episodes": episodes,
            "excluded_episode_ids": manifest.excluded_semantic_retrieval_episode_ids,
            "included_record_ids": manifest.semantic_retrieval_record_ids,
            "records": records,
            "excluded_record_ids": manifest.excluded_semantic_retrieval_record_ids,
        }

    def _read_semantic_cluster_coverage_context(
        self,
        manifest: ContextManifest,
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        artifact_sha256: str | None = None
        if manifest.semantic_cluster_coverage_artifact:
            path = self.root / manifest.semantic_cluster_coverage_artifact
            if path.exists():
                artifact_sha256 = file_sha256(path)
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rows.append(json.loads(line))
        row_identities = [
            {
                "cluster_id": row.get("cluster_id"),
                "category": row.get("category"),
                "query_sha256": row.get("query_sha256"),
                "included_episode_ids": row.get("included_episode_ids", []),
                "excluded_episode_ids": row.get("excluded_episode_ids", []),
                "included_record_ids": row.get("included_record_ids", []),
                "excluded_record_ids": row.get("excluded_record_ids", []),
            }
            for row in rows
        ]
        hit_rows = [
            identity
            for identity in row_identities
            if any(
                identity[key]
                for key in (
                    "included_episode_ids",
                    "excluded_episode_ids",
                    "included_record_ids",
                    "excluded_record_ids",
                )
            )
        ]
        lane_counts = Counter(str(row.get("category", "")) for row in rows)
        lane_hit_counts = Counter(
            str(row.get("category", ""))
            for row, identity in zip(rows, row_identities, strict=True)
            if identity in hit_rows
        )
        return {
            "artifact": manifest.semantic_cluster_coverage_artifact,
            "artifact_sha256": (manifest.semantic_cluster_coverage_sha256 if artifact_sha256 is not None else None),
            "summary": manifest.semantic_cluster_coverage_summary,
            "full_row_count": len(rows),
            "full_row_identity_root_sha256": sha256_text(canonical_json(row_identities)),
            "prompt_rows_policy": ("NONZERO_RESULT_ROWS_ONLY_WITH_COMPLETE_LANE_COUNTS_AND_FULL_ROW_ROOT.v1"),
            "lane_row_counts": dict(sorted(lane_counts.items())),
            "lane_hit_row_counts": dict(sorted(lane_hit_counts.items())),
            "hit_rows": hit_rows,
            "covered_cluster_count": len(manifest.semantic_cluster_coverage_ids),
            "covered_cluster_root_sha256": sha256_text(canonical_json(manifest.semantic_cluster_coverage_ids)),
            "missing_cluster_ids": manifest.semantic_cluster_coverage_missing_ids,
            "promoted_record_ids": (manifest.semantic_cluster_coverage_promoted_record_ids),
            "full_rows_silently_truncated": False,
        }

    def _read_record_context_by_ids(self, record_ids: list[str]) -> list[dict[str, Any]]:
        record_store = BrainRecordStore(self.root)
        records: list[dict[str, Any]] = []
        for record_id in record_ids:
            try:
                record = record_store.get_record(record_id)
            except FileNotFoundError:
                records.append({"record_id": record_id, "missing": True})
                continue
            records.append(record.model_dump(mode="json"))
        return records

    def _read_candidate_expansion_context(self, manifest: ContextManifest) -> dict[str, Any]:
        if not manifest.candidate_expansion_artifact:
            return {}
        path = self.root / manifest.candidate_expansion_artifact
        if not path.exists():
            return {"path": manifest.candidate_expansion_artifact, "missing": True}
        payload = read_json(path)
        return payload if isinstance(payload, dict) else {}

    def _collect_company_memory_context(
        self,
        *,
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> list[dict[str, Any]]:
        company_store = self._company_memory_store(create=False)
        directory = company_store.dir
        if not directory.exists():
            return []
        contexts: list[dict[str, Any]] = []
        included: list[str] = []
        omitted: list[dict[str, str]] = []
        attestation_required = production_company_memory_attestation_required(self.root)
        attestation_key = (
            self.settings.env_value("NSLAB_PRODUCTION_PROMOTION_HMAC_KEY")
            if attestation_required and hasattr(self, "settings")
            else None
        )
        for path in sorted(directory.glob("*.json")):
            relative_path = relative_to_root(path, self.root)
            try:
                memory = CompanyMemory.model_validate(read_json(path))
            except Exception:
                omitted.append({"path": relative_path, "reason": "invalid_company_memory_schema"})
                manifest.errors.append(f"company memory omitted due to invalid schema: {relative_path}")
                continue
            source_types = {provenance.source_type for provenance in memory.provenance}
            if attestation_required and source_types != {"company_memory_delta_record"}:
                attestation_error: str | None
                if attestation_key is None:
                    attestation_error = "production_company_memory_attestation_key_missing"
                else:
                    attestation_error = company_store.candidate_attestation_error(
                        path,
                        memory=memory,
                        key_value=attestation_key,
                    )
                if attestation_error is not None:
                    omitted.append(
                        {
                            "path": relative_path,
                            "reason": "untrusted_company_memory_attestation",
                        }
                    )
                    manifest.errors.append(attestation_error)
                    continue
            if not is_available_as_of(memory.known_at, cutoff_at):
                omitted.append(
                    {
                        "path": relative_path,
                        "reason": "company_memory_known_after_cutoff",
                        "known_at": memory.known_at.isoformat(),
                    }
                )
                continue
            if not is_available_as_of(memory.available_from, cutoff_at):
                omitted.append(
                    {
                        "path": relative_path,
                        "reason": "company_memory_available_after_cutoff",
                        "available_from": memory.available_from.isoformat(),
                    }
                )
                continue
            included.append(relative_path)
            contexts.append(
                {
                    "path": relative_path,
                    "sha256": file_sha256(path),
                    "memory": memory.model_dump(
                        mode="json",
                        exclude={"production_attestation"},
                    ),
                }
            )
        manifest.included_company_memory_files = included
        manifest.omitted_company_memory_files = omitted
        return contexts

    def _company_memory_store(
        self,
        *,
        create: bool = True,
    ) -> CompanyMemoryStore:
        snapshot = getattr(self, "_evaluation_memory_snapshot", None)
        directory = (
            self.root / "memory" / "company_memory_evaluation" / snapshot.snapshot_id / self.runtime_retrieval_variant
            if snapshot is not None
            else None
        )
        return CompanyMemoryStore(
            self.root,
            create=create,
            directory=directory,
        )

    def _apply_company_memory_record_deltas(
        self,
        store: CompanyMemoryStore,
        *,
        as_of: datetime,
    ) -> CompanyMemoryDeltaApplyResult:
        if self._evaluation_memory_snapshot is None:
            return store.apply_record_deltas(as_of=as_of)
        delta_records, identity_records = self._evaluation_company_memory_record_sets()
        result = store.apply_record_delta_records(
            delta_records,
            as_of=as_of,
            identity_records=identity_records,
        )
        if result.skipped_invalid_record_ids:
            raise ValueError(
                "evaluation snapshot company-memory identity closure failed: "
                + ", ".join(result.skipped_invalid_record_ids)
            )
        return result

    def _evaluation_company_memory_record_sets(
        self,
    ) -> tuple[list[BrainRecordEnvelope], list[BrainRecordEnvelope]]:
        snapshot = self._evaluation_memory_snapshot
        if snapshot is None:
            raise ValueError("evaluation company memory requires a replay snapshot")
        if self._evaluation_company_record_cache is not None:
            return self._evaluation_company_record_cache
        database_path = (self.root / snapshot.database.artifact_path).resolve()
        try:
            database_path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("evaluation company-memory snapshot database escapes the project") from exc
        if not database_path.is_file() or file_sha256(database_path) != snapshot.database.sha256:
            raise ValueError("evaluation company-memory snapshot database is unavailable or drifted")
        connection = duckdb.connect(
            str(database_path),
            read_only=True,
        )
        try:
            rows = connection.execute(
                """
                WITH delta_episodes AS (
                    SELECT DISTINCT episode_id
                    FROM records
                    WHERE record_type = 'company_memory_delta'
                )
                SELECT r.record_id,
                       r.episode_id,
                       r.record_type,
                       r.available_from,
                       r.source_sha256
                FROM records AS r
                INNER JOIN delta_episodes AS d USING (episode_id)
                ORDER BY r.episode_id, r.record_id
                """
            ).fetchall()
        finally:
            connection.close()
        expected_by_episode: dict[
            str,
            dict[str, tuple[str, datetime, str]],
        ] = {}
        for record_id, episode_id, record_type, available_from, source_sha256 in rows:
            expected = expected_by_episode.setdefault(str(episode_id), {})
            normalized_record_id = str(record_id)
            if normalized_record_id in expected:
                raise ValueError("evaluation company-memory snapshot has duplicate record IDs")
            expected[normalized_record_id] = (
                str(record_type),
                parse_datetime(str(available_from)),
                str(source_sha256),
            )
        identity_records: list[BrainRecordEnvelope] = []
        record_store = BrainRecordStore(self.root)
        for episode_id, expected in sorted(expected_by_episode.items()):
            observed: dict[str, BrainRecordEnvelope] = {}
            for record in record_store.read_episode_records(episode_id):
                if record.record_id not in expected:
                    continue
                if record.record_id in observed:
                    raise ValueError(f"evaluation company-memory source has duplicate record IDs: {record.record_id}")
                observed[record.record_id] = record
            if set(observed) != set(expected):
                raise ValueError(f"evaluation company-memory record closure mismatch: {episode_id}")
            for record_id, (record_type, available_from, source_sha256) in expected.items():
                record = observed[record_id]
                if record.episode_id != episode_id or record.record_type != record_type:
                    raise ValueError(f"evaluation company-memory record identity mismatch: {record_id}")
                if brain_record_envelope_sha256(record) != source_sha256:
                    raise ValueError(f"evaluation company-memory source hash mismatch: {record_id}")
                identity_records.append(record.model_copy(update={"available_from": available_from}))
        delta_records = [record for record in identity_records if record.record_type == "company_memory_delta"]
        self._evaluation_company_record_cache = (
            delta_records,
            identity_records,
        )
        return self._evaluation_company_record_cache

    def _collect_market_memory_context(
        self,
        *,
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> list[dict[str, Any]]:
        if self._evaluation_memory_snapshot is not None:
            manifest.included_market_context_files = []
            manifest.omitted_market_context_files = [
                {
                    "path": "memory/market_memory",
                    "reason": "evaluation_uses_build_snapshot_records_only",
                }
            ]
            return []
        directory = self.root / "memory" / "market_memory"
        if not directory.exists():
            return []
        contexts: list[dict[str, Any]] = []
        included: list[str] = []
        omitted: list[dict[str, str]] = []
        for path in sorted(directory.glob("*")):
            if not path.is_file():
                continue
            relative_path = relative_to_root(path, self.root)
            if path.suffix.lower() == ".jsonl":
                contexts.extend(
                    self._collect_market_memory_jsonl(
                        path,
                        relative_path=relative_path,
                        cutoff_at=cutoff_at,
                        included=included,
                        omitted=omitted,
                    )
                )
                continue
            if path.suffix.lower() == ".json":
                contexts.extend(
                    self._collect_market_memory_json(
                        path,
                        relative_path=relative_path,
                        cutoff_at=cutoff_at,
                        included=included,
                        omitted=omitted,
                    )
                )
                continue
            if path.suffix.lower() in {".md", ".txt"}:
                omitted.append({"path": relative_path, "reason": "missing_temporal_scope"})
        manifest.included_market_context_files = included
        manifest.omitted_market_context_files = omitted
        return contexts

    def _collect_market_memory_jsonl(
        self,
        path: Path,
        *,
        relative_path: str,
        cutoff_at: datetime,
        included: list[str],
        omitted: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            entry_path = f"{relative_path}#L{line_number}"
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                omitted.append({"path": entry_path, "reason": "invalid_jsonl"})
                continue
            context = self._market_memory_payload_context(
                payload,
                entry_path=entry_path,
                cutoff_at=cutoff_at,
                omitted=omitted,
            )
            if context is None:
                continue
            included.append(entry_path)
            contexts.append(context)
        return contexts

    def _collect_market_memory_json(
        self,
        path: Path,
        *,
        relative_path: str,
        cutoff_at: datetime,
        included: list[str],
        omitted: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        try:
            payload = read_json(path)
        except Exception:
            omitted.append({"path": relative_path, "reason": "invalid_json"})
            return []
        if isinstance(payload, list):
            contexts: list[dict[str, Any]] = []
            for index, item in enumerate(payload):
                entry_path = f"{relative_path}#{index}"
                context = self._market_memory_payload_context(
                    item,
                    entry_path=entry_path,
                    cutoff_at=cutoff_at,
                    omitted=omitted,
                )
                if context is None:
                    continue
                included.append(entry_path)
                contexts.append(context)
            return contexts
        context = self._market_memory_payload_context(
            payload,
            entry_path=relative_path,
            cutoff_at=cutoff_at,
            omitted=omitted,
        )
        if context is None:
            return []
        included.append(relative_path)
        return [context]

    def _market_memory_payload_context(
        self,
        payload: object,
        *,
        entry_path: str,
        cutoff_at: datetime,
        omitted: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            omitted.append({"path": entry_path, "reason": "non_object_json"})
            return None
        timestamp, reason = _payload_temporal_scope(payload)
        if timestamp is None:
            omitted.append({"path": entry_path, "reason": reason})
            return None
        if not is_available_as_of(timestamp, cutoff_at):
            omitted.append(
                {
                    "path": entry_path,
                    "reason": f"{reason}_after_cutoff",
                    "available_at": timestamp.isoformat(),
                }
            )
            return None
        return {
            "path": entry_path,
            "sha256": sha256_text(canonical_json(payload)),
            "memory": payload,
        }

    def _validate_preloaded_news_batch(
        self,
        *,
        news_csv: Path,
        trade_date: date,
        batch: NewsBatch,
    ) -> NewsBatch:
        resolved_news = news_csv.resolve()
        if batch.path.resolve() != resolved_news:
            raise ValueError("preloaded news batch path differs from requested CSV")
        current_sha256 = file_sha256(resolved_news)
        if batch.sha256 != current_sha256:
            raise ValueError("preloaded news batch hash differs from requested CSV")
        if batch.trade_date != trade_date:
            raise ValueError("preloaded news batch trade date differs from request")
        if [item.row_number for item in batch.items] != list(range(1, len(batch.items) + 1)):
            raise ValueError("preloaded news batch row identity is invalid")
        for item in batch.items:
            if not item.provenance or any(row.content_sha256 != batch.sha256 for row in item.provenance):
                raise ValueError("preloaded news batch provenance hash is invalid")
        return batch

    @staticmethod
    def _build_d_minus_one_prompt_projection(
        *,
        context: SharedDMinusOneContext,
        context_reference: QualityArtifactReference,
        candidates: list[Candidate],
    ) -> DMinusOnePromptProjection:
        requests_by_ticker: dict[str, tuple[set[int], set[str]]] = {}
        for candidate in candidates:
            ticker = candidate.ticker.strip().upper()
            if not ticker or ticker in {"UNKNOWN", "UNVERIFIED"}:
                continue
            ranks, event_ids = requests_by_ticker.setdefault(
                ticker,
                (set(), set()),
            )
            ranks.add(candidate.rank)
            event_ids.update(event_id.strip() for event_id in candidate.event_ids if event_id.strip())
        request_sources = [
            DMinusOneProjectionRequest(
                ticker=ticker,
                candidate_ranks=sorted(values[0]),
                event_ids=sorted(values[1]),
            )
            for ticker, values in sorted(requests_by_ticker.items())
        ]
        requested_tickers = [request.ticker for request in request_sources]
        snapshot_by_ticker = {row.ticker: row for row in context.snapshots}
        snapshots = [snapshot_by_ticker[ticker] for ticker in requested_tickers if ticker in snapshot_by_ticker]
        missing_tickers = sorted(ticker for ticker in requested_tickers if ticker not in snapshot_by_ticker)
        projection_snapshot_root_sha256 = sha256_text(
            canonical_json([row.model_dump(mode="json") for row in snapshots])
        )
        identity_payload = {
            "schema_version": "nslab.d_minus_one_prompt_projection.v1",
            "projection_policy": ("ALL_PRELIMINARY_CANDIDATE_TICKERS_EXACT_SEALED_SUBSET.v1"),
            "trade_date": context.trade_date.isoformat(),
            "cutoff_at": context.cutoff_at.isoformat(),
            "allowed_through": context.allowed_through.isoformat(),
            "source_name": context.source_name,
            "source_ref": context.source_ref,
            "full_context": context_reference.model_dump(mode="json"),
            "full_payload_sha256": sha256_text(canonical_json(context.model_dump(mode="json"))),
            "candidate_universe_root_sha256": (context.candidate_universe_root_sha256),
            "full_snapshot_root_sha256": context.snapshot_root_sha256,
            "source_revision_sha256": context.source_revision_sha256,
            "snapshot_session_date": (
                context.snapshot_session_date.isoformat() if context.snapshot_session_date is not None else None
            ),
            "full_snapshot_count": context.sealed_snapshot_count,
            "request_sources": [request.model_dump(mode="json") for request in request_sources],
            "requested_tickers": requested_tickers,
            "requested_ticker_root_sha256": sha256_text(canonical_json(requested_tickers)),
            "snapshots": [row.model_dump(mode="json") for row in snapshots],
            "missing_tickers": missing_tickers,
            "projection_snapshot_root_sha256": (projection_snapshot_root_sha256),
        }
        return DMinusOnePromptProjection.model_validate(
            {
                **identity_payload,
                "projection_root_sha256": sha256_text(canonical_json(identity_payload)),
            }
        )

    def _load_shared_d_minus_one_context(
        self,
        *,
        path: Path,
        expected_artifact_path: str,
        expected_sha256: str,
        trade_date: date,
        cutoff_at: datetime,
    ) -> tuple[SharedDMinusOneContext, str, Path]:
        resolved = path.resolve()
        expected = (self.root / expected_artifact_path).resolve()
        if resolved != expected:
            raise ValueError("shared D-1 path differs from shared context binding")
        try:
            relative = resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("shared D-1 context escapes project root") from exc
        parts = relative.parts
        if (
            len(parts) != 6
            or parts[:4]
            != (
                "runs",
                "semantic_brain_upgrade",
                "quality_full",
                "blind_inputs",
            )
            or not parts[4].startswith("QINPUT-")
            or parts[5:] != ("d_minus_one_safe_context.json",)
        ):
            raise ValueError("shared D-1 context is outside the sealed QINPUT allowlist")
        payload_bytes = resolved.read_bytes()
        payload_sha256 = sha256_bytes(payload_bytes)
        if payload_sha256 != expected_sha256:
            raise ValueError("shared D-1 context hash differs from shared binding")
        try:
            raw_payload = json.loads(payload_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("shared D-1 context payload is invalid") from exc
        reject_forbidden_blind_payload_keys(raw_payload)
        context = SharedDMinusOneContext.model_validate(raw_payload)
        if (
            context.trade_date != trade_date
            or context.cutoff_at != cutoff_at
            or context.allowed_through != trade_date - timedelta(days=1)
        ):
            raise ValueError("shared D-1 temporal identity differs from analysis")
        return context, payload_sha256, resolved

    def _bind_shared_d_minus_one_context(
        self,
        *,
        manifest: ContextManifest,
        context: SharedDMinusOneContext,
        context_sha256: str,
        context_path: Path,
    ) -> None:
        if (
            manifest.trade_date != context.trade_date
            or manifest.cutoff_at != context.cutoff_at
            or manifest.price_snapshot.allowed_through != context.allowed_through
            or manifest.price_snapshot.source_name != context.source_name
            or manifest.price_snapshot.source_ref != context.source_ref
        ):
            raise ValueError("shared D-1 context differs from analysis manifest")
        manifest.bind_d_minus_one_context(
            artifact_path=relative_to_root(context_path, self.root),
            sha256=context_sha256,
            candidate_universe_root_sha256=(context.candidate_universe_root_sha256),
            snapshot_root_sha256=context.snapshot_root_sha256,
            source_revision_sha256=context.source_revision_sha256,
            snapshot_session_date=context.snapshot_session_date,
            payload_sha256=sha256_text(canonical_json(context.model_dump(mode="json"))),
        )
        manifest.blind_price_repository_access_count = context.price_repository_access_count
        manifest.blind_current_price_access_count = context.d_day_access_count
        if context.price_repository_access_count:
            self._mark_d_minus_one_price_access(manifest)

    def _collect_d_minus_one_market_data(
        self,
        *,
        candidates: list[Candidate],
        manifest: ContextManifest,
    ) -> dict[str, Any]:
        allowed_through = manifest.price_snapshot.allowed_through
        payload: dict[str, Any] = {
            "status": "NEWS_ONLY_STRICT_NO_PRICE_ACCESS",
            "source_name": manifest.price_snapshot.source_name,
            "source_ref": manifest.price_snapshot.source_ref,
            "allowed_through": allowed_through.isoformat() if allowed_through else None,
            "blind_context_mode": manifest.blind_context_mode,
            "blind_price_repository_access_count": manifest.blind_price_repository_access_count,
            "blind_current_price_access_count": manifest.blind_current_price_access_count,
            "snapshots": [],
            "skipped_tickers": [],
        }
        if self.price_source is None:
            for candidate in candidates:
                ticker = candidate.ticker.strip().upper()
                if not ticker or ticker in {"UNKNOWN", "UNVERIFIED"}:
                    payload["skipped_tickers"].append({"ticker": candidate.ticker, "reason": "ticker_not_verified"})
                    continue
                payload["skipped_tickers"].append({"ticker": ticker, "reason": "news_only_blind_price_access_disabled"})
            return payload
        if allowed_through is None:
            payload["status"] = "D_MINUS_ONE_PRICE_SNAPSHOT_UNAVAILABLE"
            payload["errors"] = ["price_snapshot_allowed_through_missing"]
            return payload

        payload["status"] = "D_MINUS_ONE_PRICE_SNAPSHOTS"
        guard = BlindPriceGuard(self.price_source, trade_date=manifest.trade_date)
        seen: set[str] = set()
        for candidate in candidates:
            ticker = candidate.ticker.strip().upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            if ticker in {"UNKNOWN", "UNVERIFIED"}:
                payload["skipped_tickers"].append({"ticker": candidate.ticker, "reason": "ticker_not_verified"})
                continue
            self._mark_d_minus_one_price_access(manifest)
            manifest.blind_price_repository_access_count += 1
            try:
                snapshot = guard.get_snapshot(ticker, as_of=allowed_through)
            except BlindPriceAccessError as exc:
                manifest.blind_current_price_access_count += 1
                payload.setdefault("errors", []).append(str(exc))
                payload["skipped_tickers"].append({"ticker": ticker, "reason": "blind_price_guard_rejected_access"})
                continue
            if snapshot is None:
                payload["skipped_tickers"].append({"ticker": ticker, "reason": "d_minus_one_snapshot_unavailable"})
                continue
            payload["snapshots"].append(_price_record_payload(snapshot))
        payload["blind_context_mode"] = manifest.blind_context_mode
        payload["blind_price_repository_access_count"] = manifest.blind_price_repository_access_count
        payload["blind_current_price_access_count"] = manifest.blind_current_price_access_count
        return payload

    def _mark_d_minus_one_price_access(self, manifest: ContextManifest) -> None:
        if manifest.blind_context_mode == "NEWS_ONLY_STRICT":
            manifest.blind_context_mode = "D_MINUS_ONE_PRICE_BLIND"
        elif manifest.blind_context_mode == "CUTOFF_SAFE_WEB_BLIND":
            manifest.blind_context_mode = "CUTOFF_SAFE_WEB_AND_D_MINUS_ONE_PRICE_BLIND"

    def _read_brain_context(self, manifest: ContextManifest) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        reference_only_metadata = {
            "brain_manifest.json",
            "coverage_manifest.json",
            "record_coverage_manifest.json",
        }
        for relative_path in manifest.brain_files:
            path = self.root / relative_path
            if not path.exists() or not path.is_file():
                files.append({"path": relative_path, "missing": True})
                continue
            if path.name in reference_only_metadata:
                files.append(
                    {
                        "path": relative_path,
                        "sha256": manifest.brain_file_hashes.get(relative_path),
                        "byte_size": path.stat().st_size,
                        "prompt_projection": "REFERENCE_ONLY_METADATA.v1",
                    }
                )
                continue
            files.append(
                {
                    "path": relative_path,
                    "sha256": manifest.brain_file_hashes.get(relative_path),
                    "text": path.read_text(encoding="utf-8"),
                }
            )
        return files

    def _read_shard_brain_context(self, manifest: ContextManifest) -> list[dict[str, Any]]:
        """Return immutable shard references; raw shard bodies stay off the prompt path."""

        files: list[dict[str, Any]] = []
        for relative_path in manifest.shard_brain_files:
            path = self.root / relative_path
            if not path.exists() or not path.is_file():
                files.append({"path": relative_path, "missing": True})
                continue
            files.append(
                {
                    "path": relative_path,
                    "sha256": manifest.shard_brain_file_hashes.get(relative_path),
                    "byte_size": path.stat().st_size,
                }
            )
        return files

    def _read_retrieved_episode_context(self, manifest: ContextManifest) -> list[dict[str, Any]]:
        store = ResearchStore(self.root)
        contexts: list[dict[str, Any]] = []
        for episode_id in manifest.retrieved_episode_ids:
            try:
                episode = store.get_episode(episode_id)
            except FileNotFoundError:
                contexts.append({"episode_id": episode_id, "missing": True})
                continue
            contexts.append(
                {
                    "episode_id": episode.episode_id,
                    "trade_date": episode.trade_date.isoformat(),
                    "available_from": episode.available_from.isoformat(),
                    "episode": episode.model_dump(mode="json"),
                }
            )
        return contexts

    def _read_counterexample_context(self, manifest: ContextManifest) -> list[dict[str, Any]]:
        store = ResearchStore(self.root)
        contexts: list[dict[str, Any]] = []
        for episode_id in manifest.counterexample_episode_ids:
            try:
                episode = store.get_episode(episode_id)
            except FileNotFoundError:
                contexts.append({"episode_id": episode_id, "missing": True})
                continue
            contexts.append(
                {
                    "episode_id": episode.episode_id,
                    "trade_date": episode.trade_date.isoformat(),
                    "available_from": episode.available_from.isoformat(),
                    "counterexamples": [claim.model_dump(mode="json") for claim in episode.counterexamples],
                    "misses": episode.misses,
                }
            )
        return contexts

    def _read_retrieved_record_context(self, manifest: ContextManifest) -> list[dict[str, Any]]:
        store = BrainRecordStore(self.root)
        contexts: list[dict[str, Any]] = []
        for record_id in self._prediction_retrieved_record_ids(manifest):
            try:
                record = store.get_record(record_id)
            except FileNotFoundError:
                contexts.append({"record_id": record_id, "missing": True})
                continue
            contexts.append(record.model_dump(mode="json"))
        return contexts

    def _read_counterexample_record_context(
        self,
        manifest: ContextManifest,
    ) -> list[dict[str, Any]]:
        store = BrainRecordStore(self.root)
        contexts: list[dict[str, Any]] = []
        for record_id in manifest.counterexample_record_ids:
            try:
                record = store.get_record(record_id)
            except FileNotFoundError:
                contexts.append({"record_id": record_id, "missing": True})
                continue
            contexts.append(record.model_dump(mode="json"))
        return contexts

    def _read_json_artifacts(self, relative_paths: list[str]) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for relative_path in relative_paths:
            path = self.root / relative_path
            if not path.exists() or not path.is_file():
                artifacts.append({"path": relative_path, "missing": True})
                continue
            payload = read_json(path)
            artifacts.append({"path": relative_path, "payload": payload})
        return artifacts

    def _read_web_source_context(self, manifest: ContextManifest) -> list[dict[str, Any]]:
        if not manifest.web_source_artifact:
            return []
        path = self.root / manifest.web_source_artifact
        if not path.exists() or not path.is_file():
            return [{"path": manifest.web_source_artifact, "missing": True}]
        sources: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            source = {
                "source_id": row.get("source_id"),
                "query": row.get("query"),
                "title": row.get("title"),
                "url": row.get("url"),
                "snippet": row.get("snippet"),
                "published_at": row.get("published_at"),
                "time_verified": row.get("time_verified"),
                "content_sha256": row.get("content_sha256"),
                "opened_text_excerpt": row.get("opened_text_excerpt"),
            }
            if "timestamp_precision" in row:
                source["timestamp_precision"] = row.get("timestamp_precision")
            sources.append(source)
        return sources

    def _read_candidate_web_check_context(
        self,
        manifest: ContextManifest,
    ) -> list[dict[str, Any]]:
        if not manifest.candidate_web_check_artifact:
            return []
        path = self.root / manifest.candidate_web_check_artifact
        if not path.exists() or not path.is_file():
            return [{"path": manifest.candidate_web_check_artifact, "missing": True}]
        checks: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            check = {
                "candidate_rank": row.get("candidate_rank"),
                "candidate_ticker": row.get("candidate_ticker"),
                "candidate_company_name": row.get("candidate_company_name"),
                "candidate_path_type": row.get("candidate_path_type"),
                "candidate_subject_type": row.get("candidate_subject_type"),
                "candidate_expansion_path": row.get("candidate_expansion_path"),
                "candidate_expansion_hypothesis": row.get("candidate_expansion_hypothesis"),
                "candidate_investigation_questions": row.get("candidate_investigation_questions"),
                "verification_focus": row.get("verification_focus"),
                "source_id": row.get("source_id"),
                "query": row.get("query"),
                "title": row.get("title"),
                "url": row.get("url"),
                "snippet": row.get("snippet"),
                "published_at": row.get("published_at"),
                "time_verified": row.get("time_verified"),
                "content_sha256": row.get("content_sha256"),
                "opened_text_excerpt": row.get("opened_text_excerpt"),
            }
            if "timestamp_precision" in row:
                check["timestamp_precision"] = row.get("timestamp_precision")
            checks.append(check)
        return checks

    def _read_candidate_verification_context(
        self,
        manifest: ContextManifest,
    ) -> dict[str, Any]:
        if not manifest.candidate_verification_artifact:
            return {}
        path = self.root / manifest.candidate_verification_artifact
        if not path.exists():
            return {"path": manifest.candidate_verification_artifact, "missing": True}
        payload = read_json(path)
        return payload if isinstance(payload, dict) else {}

    def _load_synthesis_prompt(self) -> str:
        path = self.root / "prompts" / "synthesis" / "final.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return (
            "Synthesize current news, global brain, swept memory, counterexamples, "
            "candidate research, cutoff-verified web evidence, and red-team objections. "
            "In BLIND, D-day prices and cutoff-after evidence must remain unavailable."
        )

    def _build_web_queries(self, items: Sequence[NewsItem]) -> list[str]:
        queries: list[str] = []
        for item in items[:10]:
            title = getattr(item, "title", "")
            if title:
                snippet = title[:80]
                queries.extend(
                    [
                        f"verify listing ticker novelty direct relation {snippet}",
                        f"beneficiary supply chain infrastructure relationship {snippet}",
                        f"D-1 absorption continuation leader review {snippet}",
                    ]
                )
        if not queries:
            queries.append("open-world market catalyst company discovery")
        queries.extend(
            [
                "causal mechanism analogs for current catalyst",
                "market narrative propagation analogs and breadth formation",
                "direct company news versus policy-derived beneficiary cases",
                "successful analog cases with strong pre-open evidence",
                "failed analog cases false positives directness novelty absorption",
                "near misses candidates not selected as leaders",
                "counterexamples superficially similar opposite outcome",
                "unexpected leader selection in first-seen policy or industry event",
                "positive analogs negative analogs near misses counterexamples",
                "leader selection cases theme formation failures",
                "candidate generation errors missed beneficiaries and row disposition failures",
            ]
        )
        return _unique_preserving_order(queries)

    def _fail_if_brain_context_contains_unavailable_episodes(
        self,
        *,
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> None:
        future_episode_ids = _future_unavailable_episode_ids_for_brain_context_check(
            self.root,
            cutoff_at=cutoff_at,
        )
        if future_episode_ids is None:
            if "accepted episode store is unreadable" not in manifest.errors:
                manifest.errors.append("accepted episode store is unreadable")
            return
        leaked_ids = [
            episode_id
            for episode_id in future_episode_ids
            if self._context_files_contain_episode_id(manifest, episode_id)
        ]
        if not leaked_ids:
            return
        manifest.errors.append("brain context contains future-unavailable episodes: " + ", ".join(leaked_ids))
        manifest_dir = self.settings.path(self.settings.output_dirs.manifests)
        manifest_path = manifest_dir / f"{manifest.run_id}.json"
        write_json(manifest_path, manifest.model_dump(mode="json"))
        raise FutureContextLeakError(
            f"brain context contains future-unavailable episodes; see {manifest_path.relative_to(self.root).as_posix()}"
        )

    def _context_files_contain_episode_id(
        self,
        manifest: ContextManifest,
        episode_id: str,
    ) -> bool:
        for relative_path in [*manifest.brain_files, *manifest.shard_brain_files]:
            path = self.root / relative_path
            if path.exists() and path.is_file() and episode_id in path.read_text(encoding="utf-8"):
                return True
        return False

    def _fail_if_exhaustive_coverage_incomplete(self, manifest: ContextManifest) -> None:
        if manifest.mode != "exhaustive":
            return
        episode_id_coverage_complete = self._exhaustive_episode_id_coverage_complete(manifest)
        record_id_coverage_complete = Counter(manifest.available_record_ids) == Counter(manifest.swept_record_ids)
        record_coverage_delta = self._exhaustive_record_coverage_delta(manifest)
        manifest.missing_swept_record_ids = record_coverage_delta["missing"]
        manifest.unexpected_swept_record_ids = record_coverage_delta["unexpected"]
        manifest.duplicate_swept_record_ids = record_coverage_delta["duplicate"]
        if (
            manifest.accepted_episode_count == manifest.swept_episode_count
            and episode_id_coverage_complete
            and manifest.available_record_count == manifest.swept_record_count
            and record_id_coverage_complete
            and not manifest.errors
        ):
            return
        if manifest.accepted_episode_count != manifest.swept_episode_count:
            manifest.errors.append("exhaustive mode requires swept_episode_count == accepted_episode_count")
        if not episode_id_coverage_complete:
            manifest.errors.append("exhaustive mode requires swept_episode_ids to match available accepted episode ids")
        if manifest.available_record_count != manifest.swept_record_count:
            manifest.errors.append("exhaustive mode requires swept_record_count == available_record_count")
        if not record_id_coverage_complete:
            manifest.errors.append("exhaustive mode requires swept_record_ids to match available_record_ids")
        manifest_dir = self.settings.path(self.settings.output_dirs.manifests)
        manifest_path = manifest_dir / f"{manifest.run_id}.json"
        write_json(manifest_path, manifest.model_dump(mode="json"))
        raise ExhaustiveCoverageError(
            f"exhaustive memory coverage failed; see {manifest_path.relative_to(self.root).as_posix()}"
        )

    def _fail_if_memory_coverage_incomplete(self, manifest: ContextManifest) -> None:
        inspection = inspect_memory_coverage_manifest(
            self.root,
            manifest.model_dump(mode="json"),
            verify_current_store=True,
        )
        if inspection.get("passed") is True:
            return
        for error in inspection.get("errors", []):
            message = f"memory coverage: {error}"
            if message not in manifest.errors:
                manifest.errors.append(message)
        manifest_dir = self.settings.path(self.settings.output_dirs.manifests)
        manifest_path = manifest_dir / f"{manifest.run_id}.json"
        write_json(manifest_path, manifest.model_dump(mode="json"))
        raise ExhaustiveCoverageError(
            f"{manifest.mode} memory coverage failed before reasoning; see "
            f"{manifest_path.relative_to(self.root).as_posix()}"
        )

    @staticmethod
    def _exhaustive_record_coverage_delta(
        manifest: ContextManifest,
    ) -> dict[str, list[str]]:
        expected_counts = Counter(manifest.available_record_ids)
        swept_counts = Counter(manifest.swept_record_ids)
        missing = sorted((expected_counts - swept_counts).elements())
        unexpected = sorted((swept_counts - expected_counts).elements())
        duplicate = sorted(record_id for record_id, count in swept_counts.items() if count > 1)
        return {
            "missing": missing,
            "unexpected": unexpected,
            "duplicate": duplicate,
        }

    @staticmethod
    def _exhaustive_episode_id_coverage_complete(manifest: ContextManifest) -> bool:
        if not manifest.total_accepted_episode_ids:
            return True
        expected_available_episode_counts = Counter(manifest.total_accepted_episode_ids)
        expected_available_episode_counts.subtract(manifest.unavailable_episode_ids)
        expected_available_episode_counts += Counter()
        return Counter(manifest.swept_episode_ids) == expected_available_episode_counts

    async def _generate_prediction(
        self,
        *,
        trade_date: date,
        cutoff_at: datetime,
        news_texts: list[str],
        event_ids: list[str],
        retrieved_episode_ids: list[str],
        counterexample_episode_ids: list[str],
        retrieved_record_ids: list[str],
        counterexample_record_ids: list[str],
        excluded_source_ids: list[str],
        first_pass_mechanisms: list[str],
        context_payload: dict[str, Any],
    ) -> tuple[BlindPrediction, str, int]:
        prompt = self._build_blind_prediction_prompt(
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            news_texts=news_texts,
            event_ids=event_ids,
            retrieved_episode_ids=retrieved_episode_ids,
            counterexample_episode_ids=counterexample_episode_ids,
            retrieved_record_ids=retrieved_record_ids,
            counterexample_record_ids=counterexample_record_ids,
            excluded_source_ids=excluded_source_ids,
            first_pass_mechanisms=first_pass_mechanisms,
            context_payload=context_payload,
        )
        prediction = await self.llm.generate_structured(
            prompt=prompt,
            response_model=BlindPrediction,
            purpose="daily_blind_analysis",
        )
        if not prediction.candidates:
            prediction = self._make_prediction(
                trade_date=trade_date,
                cutoff_at=cutoff_at,
                news_texts=news_texts,
                event_ids=event_ids,
                retrieved_episode_ids=retrieved_episode_ids,
                counterexample_episode_ids=counterexample_episode_ids,
                retrieved_record_ids=retrieved_record_ids,
                counterexample_record_ids=counterexample_record_ids,
                excluded_source_ids=excluded_source_ids,
                first_pass_mechanisms=first_pass_mechanisms,
            )
        normalized = self._normalize_prediction(
            prediction,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            event_ids=event_ids,
            excluded_source_ids=excluded_source_ids,
            prompt=prompt,
            purpose="daily_blind_analysis",
            default_positive_case_ids=retrieved_episode_ids[:3],
            default_negative_case_ids=counterexample_episode_ids[:3],
            default_positive_record_ids=_record_ids_without(
                retrieved_record_ids,
                counterexample_record_ids,
            )[:5],
            default_negative_record_ids=counterexample_record_ids[:5],
        )
        return normalized, sha256_text(prompt), count_provider_tokens(self.llm, prompt)

    def _build_blind_prediction_prompt(
        self,
        *,
        trade_date: date,
        cutoff_at: datetime,
        news_texts: list[str],
        event_ids: list[str],
        retrieved_episode_ids: list[str],
        counterexample_episode_ids: list[str],
        retrieved_record_ids: list[str],
        counterexample_record_ids: list[str],
        excluded_source_ids: list[str],
        first_pass_mechanisms: list[str],
        context_payload: dict[str, Any],
    ) -> str:
        payload = {
            "schema": "nslab.blind_prediction.v1",
            "trade_date": trade_date.isoformat(),
            "cutoff_at": cutoff_at.isoformat(),
            "event_ids": event_ids,
            "retrieved_episode_ids": retrieved_episode_ids,
            "counterexample_episode_ids": counterexample_episode_ids,
            "retrieved_record_ids": retrieved_record_ids,
            "counterexample_record_ids": counterexample_record_ids,
            "positive_record_ids": _record_ids_without(
                retrieved_record_ids,
                counterexample_record_ids,
            ),
            "negative_record_ids": counterexample_record_ids,
            "excluded_after_cutoff_source_ids": excluded_source_ids,
            "first_pass_mechanisms": first_pass_mechanisms,
            "context": context_payload,
            "current_news": news_texts,
        }
        return (
            "Create a blind pre-open Korean market research prediction as BlindPrediction.\n"
            "Do not use D-day prices, D-day outcomes, cutoff-after sources, fixed ticker maps, "
            "or exact-keyword retrieval as a candidate gate.\n"
            "Generate open-world candidates even when retrieved_episode_ids is empty. "
            "Use qualitative confidence labels only.\n"
            "---BLIND_ANALYSIS_PAYLOAD---\n"
            f"{canonical_json(payload)}"
        )

    def _normalize_prediction(
        self,
        prediction: BlindPrediction,
        *,
        trade_date: date,
        cutoff_at: datetime,
        event_ids: list[str],
        excluded_source_ids: list[str],
        prompt: str,
        purpose: str,
        default_positive_case_ids: Sequence[str] | None = None,
        default_negative_case_ids: Sequence[str] | None = None,
        default_positive_record_ids: Sequence[str] | None = None,
        default_negative_record_ids: Sequence[str] | None = None,
    ) -> BlindPrediction:
        prompt_hash = sha256_text(prompt)
        observed_at = cutoff_at
        fallback_positive_case_ids = _unique_preserving_order(list(default_positive_case_ids or []))[:3]
        fallback_negative_case_ids = _unique_preserving_order(list(default_negative_case_ids or []))[:3]
        fallback_positive_record_ids = _unique_preserving_order(list(default_positive_record_ids or []))[:5]
        fallback_negative_record_ids = _unique_preserving_order(list(default_negative_record_ids or []))[:5]
        analysis_provenance = _append_unique_provenance(
            prediction.blind_analysis.provenance,
            Provenance(
                source_id=stable_id("SRC", purpose, "blind_analysis", prompt_hash),
                source_type=f"{purpose}_blind_analysis",
                uri=f"prompt://{purpose}/{prompt_hash}",
                content_sha256=prompt_hash,
                excerpt="; ".join(event_ids[:5]) or None,
                observed_at=observed_at,
            ),
        )
        analysis = prediction.blind_analysis.model_copy(
            update={
                "excluded_after_cutoff_source_ids": sorted(
                    {
                        *prediction.blind_analysis.excluded_after_cutoff_source_ids,
                        *excluded_source_ids,
                    }
                ),
                "provenance": analysis_provenance,
            }
        )
        sectors = (
            []
            if not event_ids
            else prediction.dominant_sectors
            or [
                DominantSectorHypothesis(
                    name="open-world catalyst cluster",
                    triggering_events=event_ids[:5],
                    formation_mechanism=(
                        analysis.open_world_mechanisms[0]
                        if analysis.open_world_mechanisms
                        else "current catalyst -> open-world sector hypothesis"
                    ),
                    expected_breadth="requires web verification and memory comparison",
                    direct_beneficiaries=[
                        candidate.company_name
                        for candidate in prediction.candidates
                        if candidate.path_type == PathType.SINGLE_EVENT
                    ][:5],
                    indirect_beneficiaries=[
                        candidate.company_name
                        for candidate in prediction.candidates
                        if candidate.path_type != PathType.SINGLE_EVENT
                    ][:5],
                    possible_leaders=[
                        candidate.company_name
                        for candidate in sorted(prediction.candidates, key=lambda item: item.rank)[:5]
                    ],
                    failure_conditions=[
                        "web evidence fails listing or relation verification",
                        "D-1 market already absorbed the catalyst",
                        "memory counterexamples outweigh support",
                    ],
                )
            ]
        )
        normalized_sectors = []
        for index, sector in enumerate(sectors, start=1):
            sector_event_ids = sector.triggering_events or event_ids[:1]
            sector_provenance = _append_unique_provenance(
                sector.provenance,
                Provenance(
                    source_id=stable_id(
                        "SRC",
                        purpose,
                        "dominant_sector",
                        str(index),
                        sector.name,
                        prompt_hash,
                    ),
                    source_type=f"{purpose}_dominant_sector",
                    uri=f"sector://{purpose}/{trade_date.isoformat()}/{index}",
                    content_sha256=prompt_hash,
                    excerpt="; ".join(sector_event_ids[:5]) or None,
                    observed_at=observed_at,
                ),
            )
            normalized_sectors.append(
                sector.model_copy(
                    update={
                        "triggering_events": sector_event_ids,
                        "supporting_cases": sector.supporting_cases or fallback_positive_case_ids,
                        "contradicting_cases": sector.contradicting_cases or fallback_negative_case_ids,
                        "supporting_record_ids": sector.supporting_record_ids or fallback_positive_record_ids,
                        "contradicting_record_ids": sector.contradicting_record_ids or fallback_negative_record_ids,
                        "provenance": sector_provenance,
                    }
                )
            )
        normalized_candidates = []
        candidates = prediction.candidates if event_ids else []
        for index, candidate in enumerate(candidates, start=1):
            candidate_event_ids = candidate.event_ids or event_ids[:1]
            prior_positive_cases = candidate.prior_positive_cases or fallback_positive_case_ids
            prior_negative_cases = candidate.prior_negative_cases or fallback_negative_case_ids
            prior_positive_record_ids = candidate.prior_positive_record_ids or fallback_positive_record_ids
            prior_negative_record_ids = candidate.prior_negative_record_ids or fallback_negative_record_ids
            memory_episode_ids = _unique_preserving_order(
                [
                    *candidate.memory_episode_ids,
                    *prior_positive_cases,
                    *prior_negative_cases,
                ]
            )
            memory_record_ids = _unique_preserving_order(
                [
                    *candidate.memory_record_ids,
                    *prior_positive_record_ids,
                    *prior_negative_record_ids,
                ]
            )
            candidate_provenance = _append_unique_provenance(
                candidate.provenance,
                Provenance(
                    source_id=stable_id(
                        "SRC",
                        purpose,
                        "candidate",
                        str(index),
                        candidate.company_name,
                        prompt_hash,
                    ),
                    source_type=f"{purpose}_candidate",
                    uri=f"candidate://{purpose}/{trade_date.isoformat()}/{index}",
                    content_sha256=prompt_hash,
                    excerpt="; ".join(candidate_event_ids[:5]) or None,
                    observed_at=observed_at,
                ),
            )
            normalized_candidates.append(
                candidate.model_copy(
                    update={
                        "rank": index,
                        "event_ids": candidate_event_ids,
                        "prior_positive_cases": prior_positive_cases,
                        "prior_negative_cases": prior_negative_cases,
                        "prior_positive_record_ids": prior_positive_record_ids,
                        "prior_negative_record_ids": prior_negative_record_ids,
                        "memory_episode_ids": memory_episode_ids,
                        "memory_record_ids": memory_record_ids,
                        "provenance": candidate_provenance,
                    }
                )
            )
        return prediction.model_copy(
            update={
                "prediction_id": stable_id(
                    "PRED",
                    purpose,
                    trade_date.isoformat(),
                    cutoff_at.isoformat(),
                    sha256_text(prompt),
                ),
                "trade_date": trade_date,
                "cutoff_at": cutoff_at,
                "created_at": cutoff_at,
                "sealed_at": None,
                "blind_artifact_sha256": None,
                "blind_analysis": analysis,
                "dominant_sectors": normalized_sectors,
                "candidates": normalized_candidates,
            }
        )

    def _infer_first_pass_mechanisms(self, news_texts: list[str]) -> list[str]:
        return self.fallback_llm.infer_mechanisms("\n---NEWS---\n".join(news_texts))

    def _trace_llm(self, provider: LLMProvider) -> LLMProvider:
        if isinstance(provider, TracingLLMProvider):
            return provider
        return TracingLLMProvider(
            provider,
            trace_dir=self.settings.path(self.settings.output_dirs.traces),
            model_config=self.llm_model_config,
            default_metadata={"prompt_version": DAILY_BLIND_PROMPT_VERSION},
            purpose_metadata={
                "open_world_first_analysis": {"prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION},
                "daily_event_clustering": {"prompt_version": EVENT_CLUSTERING_VERSION},
                "news_novelty_review": {"prompt_version": NEWS_NOVELTY_REVIEW_PROMPT_VERSION},
                "semantic_retrieval_plan": {"prompt_version": SEMANTIC_RETRIEVAL_PLAN_PROMPT_VERSION},
                "candidate_expansion": {"prompt_version": CANDIDATE_EXPANSION_PROMPT_VERSION},
                "daily_blind_analysis": {"prompt_version": DAILY_BLIND_PROMPT_VERSION},
                "red_team_candidate_review": {"prompt_version": RED_TEAM_PROMPT_VERSION},
                "final_synthesis": {"prompt_version": FINAL_SYNTHESIS_PROMPT_VERSION},
            },
            max_retries=self.settings.llm.max_retries,
        )

    def _load_shared_pre_retrieval_context(
        self,
        *,
        path: Path,
        expected_context_sha256: str | None,
        expected_manifest_sha256: str | None,
        news_sha256: str,
        trade_date: date,
        cutoff_at: datetime,
    ) -> tuple[
        SharedPreRetrievalContext,
        str,
        SharedPreRetrievalContextManifest,
        Path,
        str,
    ]:
        if expected_context_sha256 is None or expected_manifest_sha256 is None:
            raise ValueError("shared pre-retrieval expected hashes are required")
        root = self.root.resolve()
        context_path = path.resolve() if path.is_absolute() else (root / path).resolve()
        try:
            context_path.relative_to(root)
        except ValueError as exc:
            raise ValueError("shared pre-retrieval context escapes the project root") from exc
        context_manifest_path = context_path.parent / "shared_pre_retrieval_context_manifest.json"
        if not context_path.is_file() or not context_manifest_path.is_file():
            raise ValueError("shared pre-retrieval context is incomplete")
        raw_manifest = _read_verified_blind_artifact(
            context_manifest_path,
            expected_sha256=expected_manifest_sha256,
        )
        context_manifest = SharedPreRetrievalContextManifest.model_validate(raw_manifest)
        expected_context_path = (root / context_manifest.context.artifact_path).resolve()
        if expected_context_path != context_path:
            raise ValueError("shared pre-retrieval manifest context path drifted")
        raw_context = _read_verified_blind_artifact(
            context_path,
            expected_sha256=expected_context_sha256,
        )
        context = SharedPreRetrievalContext.model_validate(raw_context)
        if (
            context_manifest.context_id != context.context_id
            or context_manifest.context.sha256 != expected_context_sha256
            or context_manifest.context.artifact_path != relative_to_root(context_path, root)
        ):
            raise ValueError("shared pre-retrieval manifest binding drifted")
        if context.news_sha256 != news_sha256 or context.trade_date != trade_date or context.cutoff_at != cutoff_at:
            raise ValueError("shared pre-retrieval current-news identity drifted")
        if (
            context.provider != self.settings.llm_provider
            or context.model != self.settings.llm.model
            or context.reasoning_effort != str(self.settings.llm.reasoning_effort or "")
        ):
            raise ValueError("shared pre-retrieval model identity drifted")
        references = [
            context.event_clustering_result,
            context.row_disposition_ledger,
            context.event_cluster_ledger,
            context.news_coverage_manifest,
            context.event_cluster_manifest,
            context.open_world_first_analysis,
            context.news_novelty_review,
            context.downstream_digest,
            *[node.output for node in context.map_reduce_nodes],
        ]
        for reference in references:
            _resolve_and_read_shared_reference(root, reference)
        return (
            context,
            expected_context_sha256,
            context_manifest,
            context_manifest_path,
            expected_manifest_sha256,
        )

    def _bind_shared_pre_retrieval_context(
        self,
        *,
        manifest: ContextManifest,
        context: SharedPreRetrievalContext,
        context_sha256: str,
        context_manifest: SharedPreRetrievalContextManifest,
        context_manifest_path: Path,
        context_manifest_sha256: str,
        parsed_news_root_sha256: str,
        event_clustering: EventClusteringResult,
        open_world_analysis: OpenWorldFirstAnalysis,
    ) -> None:
        manifest.bind_shared_pre_retrieval_context(
            context_artifact_path=context_manifest.context.artifact_path,
            context_sha256=context_sha256,
            manifest_artifact_path=relative_to_root(
                context_manifest_path,
                self.root,
            ),
            manifest_sha256=context_manifest_sha256,
            parsed_news_root_sha256=parsed_news_root_sha256,
        )
        manifest.shared_pre_retrieval_summary = {
            "context_id": context.context_id,
            "source_row_count": len(context.source_row_ids),
            "event_cluster_count": len(context.event_cluster_ids),
            "material_cluster_count": len(context.material_cluster_ids),
            "low_signal_cluster_count": len(context.low_signal_cluster_ids),
            "map_reduce_node_count": len(context.map_reduce_nodes),
            "root_node_id": context.root_node_id,
            "shared_logical_llm_call_count": context.logical_llm_call_count,
            "shared_provider_checkpoint_commitment_count": (context.provider_checkpoint_commitment_count),
            "shared_committed_prompt_tokens_estimate": (context.committed_prompt_tokens_estimate),
            "shared_committed_completion_tokens_estimate": (context.committed_completion_tokens_estimate),
            "outcome_reference_count": context.outcome_reference_count,
        }
        manifest.llm_model_config["evaluation_profile"] = "QUALITY_FULL"
        manifest.llm_model_config["shared_pre_retrieval_context_id"] = context.context_id
        manifest.row_disposition_artifact = context.row_disposition_ledger.artifact_path
        manifest.row_disposition_sha256 = context.row_disposition_ledger.sha256
        manifest.row_disposition_coverage_ratio = 1.0
        manifest.row_disposition_summary = {
            "total_rows": len(context.source_row_ids),
            "covered_rows": len(context.source_row_ids),
            "coverage_ratio": 1.0,
            "shared_pre_retrieval": True,
        }
        manifest.event_cluster_artifact = context.event_cluster_ledger.artifact_path
        manifest.event_cluster_sha256 = context.event_cluster_ledger.sha256
        manifest.event_cluster_count = len(context.event_cluster_ids)
        manifest.event_cluster_summary = {
            "source_row_count": event_clustering.cutoff_safe_row_count,
            "all_input_row_count": event_clustering.input_row_count,
            "audit_only_row_count": event_clustering.audit_only_row_count,
            "cluster_count": len(context.event_cluster_ids),
            "material_cluster_count": len(context.material_cluster_ids),
            "cluster_method": event_clustering.clustering_version,
            "embedding_method": event_clustering.embedding_method,
            "embedding_status": event_clustering.embedding_status,
            "first_n_shortcut_used": False,
            "silent_truncation_used": False,
            "shared_pre_retrieval": True,
        }
        shared_news_coverage = NewsCoverageManifest.model_validate(
            _resolve_and_read_shared_reference(
                self.root,
                context.news_coverage_manifest,
            )
        )
        shared_event_manifest = EventClusterManifest.model_validate(
            _resolve_and_read_shared_reference(
                self.root,
                context.event_cluster_manifest,
            )
        )
        binding_dir = self.root / "runs" / "checkpoints" / "event_clusters" / manifest.run_id
        news_coverage_path = binding_dir / "news_coverage_manifest.json"
        event_manifest_path = binding_dir / "event_cluster_manifest.json"
        write_json(
            news_coverage_path,
            shared_news_coverage.model_copy(update={"run_id": manifest.run_id}).model_dump(mode="json"),
        )
        write_json(
            event_manifest_path,
            shared_event_manifest.model_copy(update={"run_id": manifest.run_id}).model_dump(mode="json"),
        )
        manifest.news_coverage_manifest_artifact = relative_to_root(
            news_coverage_path,
            self.root,
        )
        manifest.news_coverage_manifest_sha256 = file_sha256(news_coverage_path)
        manifest.event_cluster_manifest_artifact = relative_to_root(
            event_manifest_path,
            self.root,
        )
        manifest.event_cluster_manifest_sha256 = file_sha256(event_manifest_path)
        manifest.open_world_first_analysis_artifact = context.open_world_first_analysis.artifact_path
        manifest.open_world_first_analysis_sha256 = context.open_world_first_analysis.sha256
        manifest.open_world_first_analysis_summary = {
            "source_cluster_count": len(open_world_analysis.source_cluster_ids),
            "analyzed_cluster_count": len(open_world_analysis.analyzed_cluster_ids),
            "uncovered_cluster_count": len(open_world_analysis.uncovered_cluster_ids),
            "analysis_batch_count": open_world_analysis.analysis_batch_count,
            "cluster_finding_count": len(open_world_analysis.cluster_findings),
            "mechanism_count": len(open_world_analysis.mechanisms),
            "shared_pre_retrieval": True,
        }
        novelty = NewsNoveltyReview.model_validate(
            _resolve_and_read_shared_reference(
                self.root,
                context.news_novelty_review,
            )
        )
        manifest.news_novelty_review_artifact = context.news_novelty_review.artifact_path
        manifest.news_novelty_review_sha256 = context.news_novelty_review.sha256
        manifest.news_novelty_review_count = novelty.reviewed_cluster_count
        manifest.news_novelty_review_summary = {
            "cluster_count": novelty.cluster_count,
            "reviewed_cluster_count": novelty.reviewed_cluster_count,
            "review_mode": novelty.review_mode,
            "shared_pre_retrieval": True,
        }

    def _enforce_evidence_policy(self, manifest: ContextManifest) -> None:
        if (
            manifest.evaluation_profile == "QUALITY_FULL"
            and manifest.d_minus_one_context_sha256 is not None
            and manifest.d_minus_one_projection_status != "BOUND"
        ):
            raise ValueError("QUALITY_FULL final synthesis did not bind a D-1 prompt projection")
        if EvidencePolicy.parse(manifest.evidence_policy) is not EvidencePolicy.CSV_MEMORY_ONLY_STRICT:
            return
        if manifest.blind_web_search_call_count != 0:
            raise UnexpectedWebAccessError("CSV_MEMORY_ONLY_STRICT observed a BLIND web call")
        if (
            manifest.external_web_evidence_count != 0
            or manifest.web_sources
            or manifest.candidate_web_source_ids
            or manifest.candidate_web_check_count
        ):
            raise UnexpectedWebAccessError("CSV_MEMORY_ONLY_STRICT observed external web evidence")

    def _write_embedding_failure_receipt(
        self,
        *,
        news_sha256: str,
        trade_date: date,
        cutoff_at: datetime,
        error: ProductionEmbeddingUnavailableError,
    ) -> None:
        failure_id = stable_id(
            "EMBEDFAIL",
            news_sha256,
            trade_date.isoformat(),
            cutoff_at.isoformat(),
            length=16,
        )
        path = self.root / "runs" / "checkpoints" / "failures" / failure_id / "embedding_failure.json"
        write_json(
            path,
            {
                "schema_version": "nslab.production_embedding_failure.v1",
                "failure_id": failure_id,
                "trade_date": trade_date.isoformat(),
                "cutoff_at": cutoff_at.isoformat(),
                "news_sha256": news_sha256,
                "embedding_provider": self.settings.embedding_provider,
                "embedding_fallback_policy": (self.settings.event_cluster_fallback_policy.value),
                "failure_type": type(error).__name__,
                "normal_prediction_written": False,
                "daily_memory_context_started": False,
                "final_synthesis_started": False,
            },
        )

    def _current_agent_identity(self) -> dict[str, Any]:
        provider: Any = self.llm
        if isinstance(provider, TracingLLMProvider):
            provider = provider.provider
        identity = getattr(provider, "identity", None)
        payload = identity() if callable(identity) else None
        return payload if isinstance(payload, dict) else {}

    def _llm_model_config(self, provider: LLMProvider) -> dict[str, Any]:
        phase1_config = {
            "event_clustering_version": EVENT_CLUSTERING_VERSION,
            "event_cluster_embedding_batch_size": (self.settings.limits.event_cluster_embedding_batch_size),
            "event_cluster_similarity_threshold": (self.settings.limits.event_cluster_similarity_threshold),
            "event_cluster_max_semantic_variants": (self.settings.limits.event_cluster_max_semantic_variants),
            "open_world_cluster_batch_size": (self.settings.limits.open_world_cluster_batch_size),
            "open_world_max_prompt_chars": (self.settings.limits.open_world_max_prompt_chars),
            "novelty_cluster_batch_size": (self.settings.limits.novelty_cluster_batch_size),
            "final_synthesis_prompt_version": FINAL_SYNTHESIS_PROMPT_VERSION,
            "final_synthesis_token_budget": (self.settings.limits.final_synthesis_token_budget),
            "token_counting_version": TOKEN_COUNTING_VERSION,
        }
        if isinstance(provider, TracingLLMProvider):
            traced_config = {**dict(provider.model_config), **phase1_config}
            provider.model_config = dict(traced_config)
            return traced_config
        config: dict[str, Any] = {
            "configured_provider": self.settings.llm_provider,
            "provider_class": type(provider).__name__,
            "max_concurrency": self.settings.limits.max_concurrency,
            "shard_episode_count": self.settings.limits.shard_episode_count,
            **phase1_config,
        }
        model = getattr(provider, "model", None)
        if isinstance(model, str) and model:
            config["model"] = model
        embedding_model = getattr(provider, "embedding_model", None)
        if isinstance(embedding_model, str) and embedding_model:
            config["embedding_model"] = embedding_model
        reasoning_effort = getattr(provider, "reasoning_effort", None)
        if isinstance(reasoning_effort, str) and reasoning_effort:
            config["reasoning_effort"] = reasoning_effort
        max_output_tokens = getattr(provider, "max_output_tokens", None)
        if isinstance(max_output_tokens, int):
            config["max_output_tokens"] = max_output_tokens
        config["max_retries"] = self.settings.llm.max_retries
        identity = getattr(provider, "identity", None)
        if callable(identity):
            payload = identity()
            if isinstance(payload, dict):
                config["agent_identity"] = payload
        return config

    def _make_prediction(
        self,
        *,
        trade_date: date,
        cutoff_at: datetime,
        news_texts: list[str],
        event_ids: list[str],
        retrieved_episode_ids: list[str],
        counterexample_episode_ids: list[str],
        retrieved_record_ids: list[str],
        counterexample_record_ids: list[str],
        excluded_source_ids: list[str],
        first_pass_mechanisms: list[str] | None = None,
    ) -> BlindPrediction:
        joined = "\n---NEWS---\n".join(news_texts)
        mechanisms = first_pass_mechanisms or self.fallback_llm.infer_mechanisms(joined)
        mentions = self.fallback_llm.extract_company_mentions(news_texts, limit=6)
        prior_positive_cases = _unique_preserving_order(retrieved_episode_ids)[:3]
        prior_negative_cases = _unique_preserving_order(counterexample_episode_ids)[:3]
        memory_case_ids = _unique_preserving_order([*prior_positive_cases, *prior_negative_cases])
        prior_positive_record_ids = _record_ids_without(
            retrieved_record_ids,
            counterexample_record_ids,
        )[:5]
        prior_negative_record_ids = _unique_preserving_order(counterexample_record_ids)[:5]
        memory_record_ids = _unique_preserving_order([*prior_positive_record_ids, *prior_negative_record_ids])
        candidates: list[Candidate] = []
        for rank, company in enumerate(mentions[:4], start=1):
            candidates.append(
                Candidate(
                    rank=rank,
                    ticker="UNKNOWN",
                    company_name=company,
                    path_type=PathType.SINGLE_EVENT,
                    event_ids=event_ids[:1],
                    thesis=(
                        "Directly mentioned entity is a blind candidate pending listing, "
                        "novelty, relation, and D-1 absorption checks."
                    ),
                    why_now="It appears in the pre-cutoff news batch.",
                    causal_chain=[
                        "pre-cutoff news event",
                        "direct entity or owner verification",
                        "D-1 market absorption check",
                        "red-team directness review",
                    ],
                    direct_evidence=[company],
                    inferred_evidence=["created by open-world pass before memory lookup"],
                    market_memory_evidence=[],
                    prior_positive_cases=prior_positive_cases,
                    prior_negative_cases=prior_negative_cases,
                    prior_positive_record_ids=prior_positive_record_ids,
                    prior_negative_record_ids=prior_negative_record_ids,
                    novel_reasoning="Candidate is not required to exist in memory before investigation.",
                    counterarguments=[
                        "listing status or ticker may be unverified",
                        "news may not be economically attributable to the listed entity",
                    ],
                    disconfirming_conditions=[
                        "cutoff-after evidence only",
                        "not a listed security",
                        "event fully reflected before D-1 close",
                    ],
                    confidence_label=ConfidenceLabel.SPECULATIVE,
                    evidence_quality=ConfidenceLabel.LOW,
                    source_urls=[f"news://{event_ids[0]}" if event_ids else "news://current-batch"],
                    memory_episode_ids=memory_case_ids,
                    memory_record_ids=memory_record_ids,
                )
            )

        next_rank = len(candidates) + 1
        candidates.append(
            Candidate(
                rank=next_rank,
                ticker="UNKNOWN",
                company_name="BENEFICIARY_DISCOVERY_REQUIRED",
                path_type=PathType.THEME_BENEFICIARY,
                event_ids=event_ids[:3],
                thesis="Policy, industry, or supply-chain beneficiaries require web/company discovery.",
                why_now="Open-world mechanism pass found possible indirect paths before retrieval gating.",
                causal_chain=[
                    "current catalyst",
                    "beneficiary path discovery",
                    "company verification",
                ],
                direct_evidence=[],
                inferred_evidence=mechanisms[:2],
                market_memory_evidence=[],
                prior_positive_cases=prior_positive_cases,
                prior_negative_cases=prior_negative_cases,
                prior_positive_record_ids=prior_positive_record_ids,
                prior_negative_record_ids=prior_negative_record_ids,
                novel_reasoning="A new beneficiary can be investigated even when retrieval returns no cases.",
                counterarguments=["theme breadth may fail", "indirect relation may be too weak"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
                source_urls=[f"news://{event_ids[0]}" if event_ids else "news://current-batch"],
                memory_episode_ids=memory_case_ids,
                memory_record_ids=memory_record_ids,
            )
        )
        candidates.append(
            Candidate(
                rank=next_rank + 1,
                ticker="UNKNOWN",
                company_name="D_MINUS_ONE_LEADER_REVIEW",
                path_type=PathType.CONTINUATION,
                event_ids=[],
                thesis="Recent leaders must be checked using only D-1 and earlier market data.",
                why_now="Continuation is evaluated separately from current-news directness.",
                causal_chain=[
                    "D-1 market memory",
                    "current catalyst overlap",
                    "continuation red-team",
                ],
                direct_evidence=[],
                inferred_evidence=["requires blind-safe price provider"],
                market_memory_evidence=["D-day prices are blocked during blind analysis"],
                prior_positive_cases=prior_positive_cases,
                prior_negative_cases=prior_negative_cases,
                prior_positive_record_ids=prior_positive_record_ids,
                prior_negative_record_ids=prior_negative_record_ids,
                counterarguments=["already exhausted", "no current catalyst overlap"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
                source_urls=["price://blind-safe-d-minus-one"],
                memory_episode_ids=memory_case_ids,
                memory_record_ids=memory_record_ids,
            )
        )

        sector = DominantSectorHypothesis(
            name="open-world catalyst cluster",
            triggering_events=event_ids[:5],
            formation_mechanism=mechanisms[0],
            expected_breadth="requires web verification and memory comparison",
            direct_beneficiaries=[
                candidate.company_name for candidate in candidates if candidate.path_type == PathType.SINGLE_EVENT
            ],
            indirect_beneficiaries=["BENEFICIARY_DISCOVERY_REQUIRED"],
            narrative_beneficiaries=[],
            possible_leaders=[candidate.company_name for candidate in candidates[:5]],
            failure_conditions=[
                "retrieved counterexamples outweigh support",
                "web evidence fails listing or relation verification",
                "D-1 market already absorbed the catalyst",
            ],
            supporting_cases=_unique_preserving_order(retrieved_episode_ids)[:5],
            contradicting_cases=_unique_preserving_order(counterexample_episode_ids)[:5],
            supporting_record_ids=_unique_preserving_order(retrieved_record_ids)[:5],
            contradicting_record_ids=_unique_preserving_order(counterexample_record_ids)[:5],
        )
        return BlindPrediction(
            prediction_id=stable_id("PRED", trade_date.isoformat(), cutoff_at.isoformat(), joined),
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            created_at=now_kst(),
            blind_analysis=BlindAnalysis(
                summary="Open-world blind analysis over current news, followed by memory and web verification hooks.",
                open_world_mechanisms=mechanisms,
                initial_uncertainties=[
                    "listing and ticker verification",
                    "novelty relative to pre-window information",
                    "directness versus narrative relation",
                    "D-1 market absorption",
                ],
                excluded_after_cutoff_source_ids=excluded_source_ids,
            ),
            dominant_sectors=[sector],
            candidates=candidates,
        )

    def _seal(self, prediction: BlindPrediction) -> BlindPrediction:
        sealed = prediction.model_copy(update={"sealed_at": now_kst(), "blind_artifact_sha256": None})
        digest = sha256_text(canonical_json(sealed.model_dump(mode="json")))
        return sealed.model_copy(update={"blind_artifact_sha256": digest})


def _candidate_case_refs(prediction: BlindPrediction, field_name: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for candidate in prediction.candidates:
        value = getattr(candidate, field_name)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, str) or not item or item in seen:
                continue
            seen.add(item)
            refs.append(item)
    return refs


def _event_cluster_fingerprint(item: NewsItem) -> str:
    normalized = "\n".join(
        [
            " ".join(item.title.casefold().split()),
            " ".join(item.body.casefold().split()),
        ]
    )
    return sha256_text(normalized)


def _optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return parse_datetime(value)


def _price_record_payload(record: PriceRecord) -> dict[str, Any]:
    return {
        "ticker": record.ticker,
        "trade_date": record.trade_date.isoformat(),
        "open": record.open,
        "high": record.high,
        "low": record.low,
        "close": record.close,
        "volume": record.volume,
        "amount": record.amount,
        "market_cap": record.market_cap,
        "listed_shares": record.listed_shares,
    }


def _normalize_semantic_retrieval_category(value: str) -> str | None:
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "positive": "positive_analogs",
        "positive_analog": "positive_analogs",
        "positive_analogs": "positive_analogs",
        "negative": "negative_controls",
        "negative_analog": "negative_controls",
        "negative_analogs": "negative_controls",
        "negative_control": "negative_controls",
        "negative_controls": "negative_controls",
        "near_miss": "near_misses",
        "near_misses": "near_misses",
        "counterexample": "counterexamples",
        "counterexamples": "counterexamples",
        "leader_selection": "leader_selection_pairs",
        "leader_selection_case": "leader_selection_pairs",
        "leader_selection_cases": "leader_selection_pairs",
        "leader_selection_pair": "leader_selection_pairs",
        "leader_selection_pairs": "leader_selection_pairs",
        "theme_formation_failure": "theme_formation_failures",
        "theme_formation_failures": "theme_formation_failures",
        "candidate_generation_error": "candidate_generation_errors",
        "candidate_generation_errors": "candidate_generation_errors",
        "candidate_generation_failure": "candidate_generation_errors",
        "candidate_generation_failures": "candidate_generation_errors",
        "newsless": NEWSLESS_OR_UNEXPLAINED_LANE,
        "unexplained": NEWSLESS_OR_UNEXPLAINED_LANE,
        "newsless_or_unexplained": NEWSLESS_OR_UNEXPLAINED_LANE,
        "newsless_or_unexplained_cases": NEWSLESS_OR_UNEXPLAINED_LANE,
    }
    return aliases.get(normalized)


def _semantic_record_filters(category: str) -> dict[str, Any]:
    return {"memory_lane": category} if category in MEMORY_RETRIEVAL_LANES else {}


def _cluster_coverage_lanes(configured_lanes: Sequence[str]) -> list[str]:
    lanes = _unique_preserving_order(
        [
            normalized
            for lane in configured_lanes
            for normalized in [_normalize_semantic_retrieval_category(lane)]
            if normalized in SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES
        ]
    )
    return lanes or list(SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES)


def _cluster_coverage_lane_instruction(lane: str) -> str:
    instructions = {
        "positive_analogs": "retrieve prior records where a similar catalyst worked",
        "negative_controls": "retrieve similar-looking records that did not work",
        "near_misses": "retrieve records that almost worked but failed key conditions",
        "counterexamples": "retrieve records contradicting the bullish interpretation",
        "leader_selection_pairs": "retrieve records about choosing the correct leader",
        "theme_formation_failures": "retrieve records where theme formation failed",
        "candidate_generation_errors": ("retrieve records about missed, noisy, or wrongly ranked candidates"),
        NEWSLESS_OR_UNEXPLAINED_LANE: ("retrieve strong or unusual outcomes with no cutoff-safe explanatory news"),
    }
    return instructions.get(lane, "retrieve balanced historical evidence for this lane")


def _future_unavailable_episode_ids_for_brain_context_check(
    root: Path,
    *,
    cutoff_at: datetime,
) -> list[str] | None:
    try:
        accepted = ResearchStore(root).list_accepted()
    except (
        OSError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        return None
    active_snapshot = active_memory_snapshot_manifest(root)
    if active_snapshot is not None and active_snapshot.evaluation_only:
        brain = read_json(root / "brain" / "current" / "brain_manifest.json")
        covered = set(brain.get("covered_episode_ids", [])) if isinstance(brain, dict) else set()
        if not covered:
            return None
        return [episode.episode_id for episode in accepted if episode.episode_id not in covered]
    return [episode.episode_id for episode in accepted if not is_available_as_of(episode.available_from, cutoff_at)]


def _append_unique_provenance(
    existing: list[Provenance],
    item: Provenance,
) -> list[Provenance]:
    seen = {entry.source_id for entry in existing}
    if item.source_id in seen:
        return existing
    return [*existing, item]


def _unique_preserving_order(values: Sequence[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _record_ids_without(record_ids: Sequence[str], excluded_record_ids: Sequence[str]) -> list[str]:
    excluded = set(excluded_record_ids)
    return [record_id for record_id in _unique_preserving_order(record_ids) if record_id not in excluded]


def _string_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _dedupe_candidate_web_check_subjects(
    subjects: Sequence[CandidateWebCheckSubject],
) -> list[CandidateWebCheckSubject]:
    unique: list[CandidateWebCheckSubject] = []
    seen: set[tuple[str, str, str, str]] = set()
    for subject in subjects:
        key = (
            subject.subject_type,
            subject.ticker.strip().casefold(),
            subject.company_name.strip().casefold(),
            subject.path_type.strip().casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(subject)
    return unique


def _candidate_web_check_subject_key(
    subject: CandidateWebCheckSubject,
) -> tuple[str, int, str, str, str, str | None]:
    return (
        subject.subject_type,
        subject.rank,
        subject.ticker,
        subject.company_name,
        subject.path_type,
        subject.expansion_path,
    )


def _final_candidate_identity(candidate: Candidate) -> tuple[int, str, str, str]:
    return (
        candidate.rank,
        candidate.ticker.strip().upper(),
        candidate.company_name.strip(),
        str(candidate.path_type).strip().upper(),
    )


def _candidate_web_check_row_key(
    row: dict[str, Any],
) -> tuple[str, int, str, str, str, str | None]:
    rank = row.get("candidate_rank")
    return (
        str(row.get("candidate_subject_type") or ""),
        rank if isinstance(rank, int) else 0,
        str(row.get("candidate_ticker") or ""),
        str(row.get("candidate_company_name") or ""),
        str(row.get("candidate_path_type") or ""),
        (str(row["candidate_expansion_path"]) if row.get("candidate_expansion_path") is not None else None),
    )


def _excerpt(text: str, *, limit: int = 1200) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _payload_temporal_scope(payload: dict[str, object]) -> tuple[datetime | None, str]:
    for field in ("available_from", "known_at"):
        raw_value = payload.get(field)
        if not isinstance(raw_value, str):
            continue
        try:
            return parse_datetime(raw_value), field
        except ValueError:
            return None, f"invalid_{field}"
    return None, "missing_temporal_scope"
