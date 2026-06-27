"""Daily blind analysis pipeline."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.config import Settings
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.context.final_synthesis import (
    final_synthesis_input_summary,
    string_list,
)
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.context.sweep import MemorySweeper
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
    OpenWorldFirstAnalysis,
    PathType,
    Provenance,
    RedTeamArtifact,
    SemanticRetrievalPlan,
    SemanticRetrievalQuery,
)
from news_scalping_lab.inference.red_team import (
    PROMPT_VERSION as RED_TEAM_PROMPT_VERSION,
)
from news_scalping_lab.inference.red_team import (
    apply_red_team_findings,
    run_red_team_pass,
)
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.memory import MemoryStore
from news_scalping_lab.memory.company import CompanyMemoryStore
from news_scalping_lab.prices.base import (
    BlindPriceAccessError,
    BlindPriceGuard,
    PriceRecord,
    PriceSource,
)
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.records.models import CANDIDATE_ERROR_RECORD_TYPES
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.reporting.render import render_preopen_report
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    canonical_json,
    default_news_window_start,
    file_sha256,
    is_available_as_of,
    now_kst,
    parse_datetime,
    read_json,
    relative_to_root,
    sha256_text,
    stable_id,
    write_json,
)
from news_scalping_lab.warehouse import WarehouseStore
from news_scalping_lab.web.factory import create_web_provider
from news_scalping_lab.web.provider import (
    TemporalWebGuard,
    WebResearchProvider,
    WebSearchExclusion,
    WebSearchResult,
)


class ExhaustiveCoverageError(RuntimeError):
    """Raised when exhaustive mode fails to sweep every required context item."""


class FutureContextLeakError(RuntimeError):
    """Raised when the active brain context contains future-unavailable research."""


DAILY_BLIND_PROMPT_VERSION = "daily_blind_analysis.v1"
OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION = "open_world_first_analysis.v1"
NEWS_NOVELTY_REVIEW_PROMPT_VERSION = "news_novelty_review.v1"
SEMANTIC_RETRIEVAL_PLAN_PROMPT_VERSION = "semantic_retrieval_plan.v1"
CANDIDATE_EXPANSION_PROMPT_VERSION = "candidate_expansion.v1"
FINAL_SYNTHESIS_PROMPT_VERSION = "synthesis.final.v1"
SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES = (
    "positive_analogs",
    "negative_controls",
    "near_misses",
    "counterexamples",
    "leader_selection_pairs",
    "theme_formation_failures",
    "candidate_generation_errors",
)
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


class DailyAnalyzer:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMProvider | None = None,
        retrieval: MemoryStore | None = None,
        price_source: PriceSource | None = None,
        web_provider: WebResearchProvider | None = None,
    ) -> None:
        self.settings = settings
        self.root = settings.project_root
        base_llm = llm or create_llm_provider(settings)
        self.llm_model_config = self._llm_model_config(base_llm)
        self.llm = self._trace_llm(base_llm)
        self.fallback_llm = DeterministicMockLLMProvider()
        self.retrieval = retrieval or LocalRetrievalStore(self.root)
        self.price_source = price_source or self._configured_blind_price_source(settings)
        self.web_provider = web_provider or create_web_provider(self.settings)

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
    ) -> DailyAnalysis:
        mode = normalize_analysis_mode(mode)
        full_batch = load_news_csv(news_csv, trade_date=trade_date)
        news_window_start_at = default_news_window_start(trade_date)
        batch = full_batch.within_window(news_window_start_at, cutoff_at)
        run_seed = sha256_text(
            canonical_json(
                {
                    "analysis_mode": mode,
                    "cutoff_at": cutoff_at.isoformat(),
                    "llm_model_config": self.llm_model_config,
                    "news_sha256": batch.sha256,
                    "trade_date": trade_date.isoformat(),
                    "web_search": web_search,
                }
            )
        )
        blind_news_items = batch.items[: self.settings.limits.max_news_items_for_mock]
        news_texts = [item.combined_text for item in blind_news_items]
        event_ids = [item.event_id for item in batch.items]
        open_world_first_analysis, open_world_prompt_hash, open_world_prompt_tokens = (
            await self._run_open_world_first_analysis(
                news_texts=news_texts,
                event_ids=event_ids,
                cutoff_at=cutoff_at,
            )
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
        )
        retrieved_record_ids, excluded_retrieved_record_ids = (
            self._filter_retrieved_record_ids_available_as_of(
                raw_retrieved_record_ids,
                cutoff_at=cutoff_at,
            )
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
        manifest.news_file = relative_to_root(full_batch.path, self.root)
        manifest.news_sha256 = full_batch.sha256
        manifest.news_window_start_at = news_window_start_at
        manifest.news_window_end_at = cutoff_at
        manifest.news_row_count = full_batch.row_count
        manifest.included_news_row_count = batch.row_count
        manifest.excluded_news_row_count = full_batch.row_count - batch.row_count
        manifest.llm_model_config = {**self.llm_model_config, "analysis_mode": mode}
        manifest.excluded_retrieved_episode_ids = excluded_retrieved_ids
        manifest.excluded_retrieved_record_ids = excluded_retrieved_record_ids
        self._write_open_world_first_analysis_artifact(
            analysis=open_world_first_analysis,
            manifest=manifest,
            prompt_sha256=open_world_prompt_hash,
            cutoff_at=cutoff_at,
        )
        manifest.token_counts["open_world_first_analysis_prompt"] = (
            open_world_prompt_tokens
        )
        self._write_row_disposition_artifact(
            full_items=full_batch.items,
            included_items=batch.items,
            news_window_start_at=news_window_start_at,
            cutoff_at=cutoff_at,
            manifest=manifest,
        )
        self._write_event_cluster_artifact(
            news_items=batch.items,
            cutoff_at=cutoff_at,
            manifest=manifest,
        )
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

        manifest.price_snapshot.source_name = self._blind_price_source_name()
        manifest.price_snapshot.source_ref = self._blind_price_source_ref()

        _news_novelty_review, novelty_prompt_hash, novelty_prompt_tokens = (
            await self._run_news_novelty_review(
                news_texts=news_texts,
                manifest=manifest,
                cutoff_at=cutoff_at,
            )
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
        )
        manifest.accepted_episode_count = sweep.accepted_episode_count
        manifest.swept_episode_count = len(sweep.swept_episode_ids)
        manifest.swept_episode_ids = sweep.swept_episode_ids
        manifest.accepted_record_count = sweep.accepted_record_count
        manifest.available_record_count = sweep.available_record_count
        manifest.available_record_ids = sweep.available_record_ids
        manifest.training_eligible_available_record_count = (
            sweep.training_eligible_available_record_count
        )
        manifest.training_eligible_available_record_ids = (
            sweep.training_eligible_available_record_ids
        )
        manifest.swept_record_count = len(sweep.swept_record_ids)
        manifest.swept_record_ids = sweep.swept_record_ids
        manifest.memory_sweep_artifacts = sweep.artifact_paths
        manifest.record_sweep_artifacts = sweep.record_artifact_paths
        manifest.memory_sweep_artifact_hashes = {
            artifact_path: file_sha256(self.root / artifact_path)
            for artifact_path in sweep.artifact_paths
        }
        manifest.record_sweep_artifact_hashes = {
            artifact_path: file_sha256(self.root / artifact_path)
            for artifact_path in sweep.record_artifact_paths
        }
        manifest.memory_sweep_shard_count = sweep.shard_count
        manifest.record_sweep_shard_count = sweep.record_shard_count
        manifest.memory_sweep_cache_hits = sweep.cache_hits
        manifest.record_sweep_cache_hits = sweep.record_cache_hits
        manifest.token_counts.update(sweep.token_counts)
        manifest.token_counts["current_news"] = sum(len(text) for text in news_texts) // 4
        manifest.errors.extend(sweep.errors)
        self._fail_if_exhaustive_coverage_incomplete(manifest)
        _semantic_plan, semantic_prompt_hash, semantic_prompt_tokens = (
            await self._run_semantic_retrieval_plan(
                news_texts=news_texts,
                first_pass_mechanisms=first_pass_mechanisms,
                manifest=manifest,
                cutoff_at=cutoff_at,
            )
        )
        self._write_semantic_retrieval_artifact(
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        self._refresh_counterexample_record_ids_from_retrieval(manifest)
        manifest.token_counts["semantic_retrieval_plan_prompt"] = semantic_prompt_tokens
        _candidate_expansion, expansion_prompt_hash, expansion_prompt_tokens = (
            await self._run_candidate_expansion(
                news_texts=news_texts,
                first_pass_mechanisms=first_pass_mechanisms,
                manifest=manifest,
                cutoff_at=cutoff_at,
            )
        )
        manifest.token_counts["candidate_expansion_prompt"] = expansion_prompt_tokens

        prediction_retrieved_record_ids = self._prediction_retrieved_record_ids(manifest)
        prediction, blind_prompt_hash, blind_prompt_tokens = await self._generate_prediction(
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            news_texts=news_texts,
            event_ids=event_ids,
            retrieved_episode_ids=retrieved_ids,
            counterexample_episode_ids=manifest.counterexample_episode_ids,
            retrieved_record_ids=prediction_retrieved_record_ids,
            counterexample_record_ids=manifest.counterexample_record_ids,
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
                "accepted_record_count": manifest.accepted_record_count,
                "available_record_count": manifest.available_record_count,
                "available_record_ids": manifest.available_record_ids,
                "swept_record_count": manifest.swept_record_count,
                "swept_record_ids": manifest.swept_record_ids,
                "training_eligible_available_record_count": (
                    manifest.training_eligible_available_record_count
                ),
                "training_eligible_available_record_ids": (
                    manifest.training_eligible_available_record_ids
                ),
                "memory_sweep_artifacts": manifest.memory_sweep_artifacts,
                "record_sweep_artifacts": manifest.record_sweep_artifacts,
                "record_sweep_artifact_hashes": manifest.record_sweep_artifact_hashes,
                "record_sweep_shard_count": manifest.record_sweep_shard_count,
                "record_sweep_cache_hits": manifest.record_sweep_cache_hits,
                "event_cluster_artifact": manifest.event_cluster_artifact,
                "event_cluster_summary": manifest.event_cluster_summary,
                "open_world_first_analysis_artifact": (
                    manifest.open_world_first_analysis_artifact
                ),
                "open_world_first_analysis_summary": (
                    manifest.open_world_first_analysis_summary
                ),
                "news_novelty_review_artifact": manifest.news_novelty_review_artifact,
                "news_novelty_review_summary": manifest.news_novelty_review_summary,
                "semantic_retrieval_plan_artifact": (
                    manifest.semantic_retrieval_plan_artifact
                ),
                "semantic_retrieval_artifact": manifest.semantic_retrieval_artifact,
                "semantic_retrieval_episode_ids": manifest.semantic_retrieval_episode_ids,
                "excluded_semantic_retrieval_episode_ids": (
                    manifest.excluded_semantic_retrieval_episode_ids
                ),
                "semantic_retrieval_record_ids": manifest.semantic_retrieval_record_ids,
                "excluded_semantic_retrieval_record_ids": (
                    manifest.excluded_semantic_retrieval_record_ids
                ),
                "semantic_retrieval_summary": manifest.semantic_retrieval_summary,
                "candidate_expansion_artifact": manifest.candidate_expansion_artifact,
                "candidate_expansion_summary": manifest.candidate_expansion_summary,
                "web_queries": manifest.web_queries,
                "web_sources": manifest.web_sources,
                "excluded_web_source_ids": manifest.excluded_web_source_ids,
                "web_source_artifact": manifest.web_source_artifact,
                "candidate_web_source_ids": manifest.candidate_web_source_ids,
                "candidate_verification_artifact": (
                    manifest.candidate_verification_artifact
                ),
                "candidate_verification_summary": (
                    manifest.candidate_verification_summary
                ),
                "excluded_candidate_web_source_ids": (
                    manifest.excluded_candidate_web_source_ids
                ),
                "candidate_web_check_artifact": manifest.candidate_web_check_artifact,
            },
        )
        manifest.token_counts["blind_analysis_prompt"] = blind_prompt_tokens
        prediction = prediction.model_copy(update={"context_manifest_id": manifest.run_id})
        d_minus_one_market_data = self._collect_d_minus_one_market_data(
            candidates=prediction.candidates,
            manifest=manifest,
        )
        if web_search:
            await self._collect_candidate_web_checks(
                prediction=prediction,
                manifest=manifest,
                cutoff_at=cutoff_at,
                d_minus_one_market_data=d_minus_one_market_data,
            )
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
                finding.passed_to_synthesis
                and all(check.passed_to_synthesis for check in finding.attack_checks)
                for finding in red_team.artifact.candidate_findings
            ),
        }
        manifest.token_counts["red_team_prompt"] = red_team.prompt_token_estimate
        company_delta_result = CompanyMemoryStore(self.root).apply_record_deltas(
            as_of=cutoff_at
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
        prediction, final_synthesis_prompt_hash, final_synthesis_prompt_tokens = (
            await self._run_final_synthesis(
                prediction=prediction,
                manifest=manifest,
                news_texts=news_texts,
                event_ids=event_ids,
                retrieved_episode_ids=retrieved_ids,
                excluded_source_ids=[],
                first_pass_mechanisms=first_pass_mechanisms,
                red_team_artifact=red_team.artifact,
                d_minus_one_market_data=d_minus_one_market_data,
                company_memory_context=company_memory_context,
                market_memory_context=market_memory_context,
            )
        )
        prediction = apply_red_team_findings(prediction, red_team.artifact)
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
        CompanyMemoryStore(self.root).upsert_from_candidates(
            prediction.candidates,
            prediction_path=prediction_path,
            known_at=prediction.cutoff_at,
        )
        write_json(manifest_path, manifest.model_dump(mode="json"))
        warehouse = WarehouseStore(self.root)
        warehouse.write_prediction(prediction)
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
        news_texts: list[str],
        event_ids: list[str],
        cutoff_at: datetime,
    ) -> tuple[OpenWorldFirstAnalysis, str, int]:
        prompt = self._build_open_world_first_analysis_prompt(
            news_texts=news_texts,
            event_ids=event_ids,
            cutoff_at=cutoff_at,
        )
        prompt_sha256 = sha256_text(prompt)
        try:
            analysis = await self.llm.generate_structured(
                prompt=prompt,
                response_model=OpenWorldFirstAnalysis,
                purpose="open_world_first_analysis",
            )
        except NotImplementedError:
            analysis = self._fallback_open_world_first_analysis(
                news_texts=news_texts,
                event_ids=event_ids,
                cutoff_at=cutoff_at,
                prompt_sha256=prompt_sha256,
            )
        normalized = self._normalize_open_world_first_analysis(
            analysis,
            news_texts=news_texts,
            event_ids=event_ids,
            cutoff_at=cutoff_at,
            prompt_sha256=prompt_sha256,
        )
        return normalized, prompt_sha256, max(1, len(prompt) // 4)

    def _build_open_world_first_analysis_prompt(
        self,
        *,
        news_texts: list[str],
        event_ids: list[str],
        cutoff_at: datetime,
    ) -> str:
        payload = {
            "schema": "nslab.open_world_first_analysis.v1",
            "prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
            "cutoff_at": cutoff_at.isoformat(),
            "event_ids": event_ids,
            "current_news": news_texts,
            "forbidden_inputs": [
                "past research search results",
                "semantic retrieval hits",
                "D-day prices or outcomes",
                "cutoff-after evidence",
            ],
            "required_fields": [
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
            "the current_news payload, before any past research or semantic retrieval. "
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
        cutoff_at: datetime,
        prompt_sha256: str,
    ) -> OpenWorldFirstAnalysis:
        fallback = self._fallback_open_world_first_analysis(
            news_texts=news_texts,
            event_ids=event_ids,
            cutoff_at=cutoff_at,
            prompt_sha256=prompt_sha256,
        )

        def list_or_fallback(
            values: list[str],
            fallback_values: list[str],
        ) -> list[str]:
            cleaned = _unique_preserving_order(
                [" ".join(value.split()) for value in values if value.strip()]
            )
            return cleaned or fallback_values

        return analysis.model_copy(
            update={
                "run_id": analysis.run_id or fallback.run_id,
                "prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION,
                "prompt_sha256": prompt_sha256,
                "cutoff_at": cutoff_at,
                "event_ids": _unique_preserving_order(event_ids),
                "event_clusters": list_or_fallback(
                    analysis.event_clusters,
                    fallback.event_clusters,
                ),
                "direct_company_events": list_or_fallback(
                    analysis.direct_company_events,
                    fallback.direct_company_events,
                ),
                "policy_industry_events": list_or_fallback(
                    analysis.policy_industry_events,
                    fallback.policy_industry_events,
                ),
                "mechanisms": list_or_fallback(
                    analysis.mechanisms,
                    fallback.mechanisms,
                ),
                "beneficiary_transmission_paths": list_or_fallback(
                    analysis.beneficiary_transmission_paths,
                    fallback.beneficiary_transmission_paths,
                ),
                "narrative_conversion_points": list_or_fallback(
                    analysis.narrative_conversion_points,
                    fallback.narrative_conversion_points,
                ),
                "direct_candidates": list_or_fallback(
                    analysis.direct_candidates,
                    fallback.direct_candidates,
                ),
                "potential_sectors": list_or_fallback(
                    analysis.potential_sectors,
                    fallback.potential_sectors,
                ),
                "beneficiary_investigation_questions": list_or_fallback(
                    analysis.beneficiary_investigation_questions,
                    fallback.beneficiary_investigation_questions,
                ),
                "uncertainties": list_or_fallback(
                    analysis.uncertainties,
                    fallback.uncertainties,
                ),
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
            f"{mechanism} -> direct, indirect, and market-memory beneficiary investigation"
            for mechanism in mechanisms
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
            potential_sectors=[
                "open-world sector hypothesis to be named by LLM and verified by sources"
            ],
            beneficiary_investigation_questions=[
                "Which listed entities have direct, supply-chain, infrastructure, regional, or market-memory exposure?",
                "Which candidates fail directness, novelty, dilution, or D-1 absorption checks?",
            ],
            uncertainties=[
                "listing status and ticker precision are unverified at Pass 0",
                "economic ownership and customer attribution require cutoff-safe evidence",
                "D-1 market absorption must be checked without D-day prices",
            ],
            notes=[
                "Fallback Pass 0 used current news only and did not inspect past research."
            ],
        )

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
            "event_cluster_count": len(normalized.event_clusters),
            "direct_company_event_count": len(normalized.direct_company_events),
            "policy_industry_event_count": len(normalized.policy_industry_events),
            "mechanism_count": len(normalized.mechanisms),
            "transmission_path_count": len(normalized.beneficiary_transmission_paths),
            "narrative_conversion_point_count": len(
                normalized.narrative_conversion_points
            ),
            "direct_candidate_count": len(normalized.direct_candidates),
            "potential_sector_count": len(normalized.potential_sectors),
            "investigation_question_count": len(
                normalized.beneficiary_investigation_questions
            ),
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
                    "collected_at": (
                        item.collected_at.isoformat() if item.collected_at is not None else None
                    ),
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
                    "provenance_source_ids": [
                        provenance.source_id for provenance in item.provenance
                    ],
                }
            )
        artifact_relative = (
            Path("runs")
            / "checkpoints"
            / "row_disposition"
            / manifest.run_id
            / "row_disposition.jsonl"
        )
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
        news_items: list[NewsItem],
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> None:
        clusters: dict[str, list[NewsItem]] = {}
        for item in news_items:
            clusters.setdefault(_event_cluster_fingerprint(item), []).append(item)
        rows: list[dict[str, Any]] = []
        for cluster_index, (fingerprint, items) in enumerate(clusters.items(), start=1):
            published = sorted(item.published_at for item in items)
            cutoff_safe_published = [value for value in published if value <= cutoff_at]
            rows.append(
                {
                    "schema_version": "nslab.news_event_cluster.v1",
                    "run_id": manifest.run_id,
                    "cluster_id": stable_id("EVCL", manifest.run_id, fingerprint),
                    "cluster_index": cluster_index,
                    "cluster_method": "exact_normalized_title_body_v1",
                    "cluster_key_sha256": fingerprint,
                    "row_numbers": [item.row_number for item in items],
                    "event_ids": [item.event_id for item in items],
                    "source_ids": [item.source_id for item in items],
                    "row_count": len(items),
                    "exact_duplicate_count": max(0, len(items) - 1),
                    "first_published_at": published[0].isoformat(),
                    "last_published_at_before_cutoff": (
                        max(cutoff_safe_published).isoformat()
                        if cutoff_safe_published
                        else None
                    ),
                    "cutoff_at": cutoff_at.isoformat(),
                    "time_verified": bool(cutoff_safe_published)
                    and max(cutoff_safe_published) <= cutoff_at,
                    "representative_title_sha256": sha256_text(items[0].title),
                    "representative_body_sha256": sha256_text(items[0].body),
                    "novelty": "unclear",
                    "novelty_basis": (
                        "Deterministic exact duplicate clustering only; final novelty "
                        "requires LLM/web review."
                    ),
                    "requires_llm_novelty_review": True,
                }
            )
        artifact_relative = (
            Path("runs")
            / "checkpoints"
            / "event_clusters"
            / manifest.run_id
            / "event_clusters.jsonl"
        )
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        exact_duplicate_cluster_count = sum(
            1 for row in rows if int(row["exact_duplicate_count"]) > 0
        )
        manifest.event_cluster_artifact = artifact_relative.as_posix()
        manifest.event_cluster_sha256 = sha256_text(payload)
        manifest.event_cluster_count = len(rows)
        manifest.event_cluster_summary = {
            "source_row_count": len(news_items),
            "cluster_count": len(rows),
            "exact_duplicate_count": sum(int(row["exact_duplicate_count"]) for row in rows),
            "exact_duplicate_cluster_count": exact_duplicate_cluster_count,
            "semantic_duplicate_cluster_count": 0,
            "cluster_method": "exact_normalized_title_body_v1",
            "novelty_review_required": True,
        }

    async def _run_news_novelty_review(
        self,
        *,
        news_texts: list[str],
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> tuple[NewsNoveltyReview, str, int]:
        prompt = self._build_news_novelty_review_prompt(
            news_texts=news_texts,
            manifest=manifest,
            cutoff_at=cutoff_at,
        )
        prompt_sha256 = sha256_text(prompt)
        try:
            review = await self.llm.generate_structured(
                prompt=prompt,
                response_model=NewsNoveltyReview,
                purpose="news_novelty_review",
            )
        except NotImplementedError:
            review = self._fallback_news_novelty_review(
                manifest=manifest,
                cutoff_at=cutoff_at,
                prompt_sha256=prompt_sha256,
            )
        normalized = self._normalize_news_novelty_review(
            review,
            manifest=manifest,
            cutoff_at=cutoff_at,
            prompt_sha256=prompt_sha256,
        )
        artifact_relative = (
            Path("runs")
            / "checkpoints"
            / "news_novelty_reviews"
            / manifest.run_id
            / "news_novelty_review.json"
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
            "excluded_after_cutoff_source_count": len(
                normalized.excluded_after_cutoff_source_ids
            ),
        }
        return normalized, prompt_sha256, max(1, len(prompt) // 4)

    def _build_news_novelty_review_prompt(
        self,
        *,
        news_texts: list[str],
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> str:
        payload = {
            "schema": "nslab.news_novelty_review.v1",
            "prompt_version": NEWS_NOVELTY_REVIEW_PROMPT_VERSION,
            "run_id": manifest.run_id,
            "cutoff_at": cutoff_at.isoformat(),
            "review_mode": manifest.blind_context_mode,
            "current_news": news_texts,
            "event_clusters": self._read_event_cluster_context(manifest),
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
    ) -> NewsNoveltyReview:
        cluster_rows = self._read_event_cluster_context(manifest)
        cluster_by_id = {
            str(row["cluster_id"]): row
            for row in cluster_rows
            if isinstance(row, dict) and isinstance(row.get("cluster_id"), str)
        }
        allowed_source_ids = self._allowed_news_novelty_source_ids(cluster_rows, manifest)
        normalized_findings: list[NewsNoveltyFinding] = []
        seen_cluster_ids: set[str] = set()
        for finding in review.findings:
            cluster_row = cluster_by_id.get(finding.cluster_id)
            if cluster_row is None:
                raise ValueError(
                    "news novelty review referenced unknown cluster_id: "
                    f"{finding.cluster_id}"
                )
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
                "news novelty review used cutoff-after first_public_evidence_at: "
                f"{first_public_at.isoformat()}"
            )
        evidence_source_ids = _unique_preserving_order(
            finding.evidence_source_ids
            or [str(source_id) for source_id in cluster_row.get("source_ids", [])]
        )
        unknown_source_ids = sorted(
            source_id for source_id in evidence_source_ids if source_id not in allowed_source_ids
        )
        if unknown_source_ids:
            raise ValueError(
                "news novelty review referenced unknown evidence_source_ids: "
                + ", ".join(unknown_source_ids)
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
        cluster_rows = self._read_event_cluster_context(manifest)
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
            Path("runs")
            / "checkpoints"
            / "semantic_retrieval"
            / manifest.run_id
            / "semantic_retrieval_plan.json"
        )
        artifact_path = self.root / artifact_relative
        write_json(artifact_path, normalized.model_dump(mode="json"))
        artifact_text = artifact_path.read_text(encoding="utf-8")
        manifest.semantic_retrieval_plan_artifact = artifact_relative.as_posix()
        manifest.semantic_retrieval_plan_sha256 = sha256_text(artifact_text)
        manifest.semantic_retrieval_query_count = len(normalized.queries)
        return normalized, prompt_sha256, max(1, len(prompt) // 4)

    def _build_semantic_retrieval_plan_prompt(
        self,
        *,
        news_texts: list[str],
        first_pass_mechanisms: list[str],
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> str:
        payload = {
            "schema": "nslab.semantic_retrieval_plan.v1",
            "prompt_version": SEMANTIC_RETRIEVAL_PLAN_PROMPT_VERSION,
            "run_id": manifest.run_id,
            "cutoff_at": cutoff_at.isoformat(),
            "required_categories": list(SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES),
            "current_news": news_texts,
            "open_world_first_analysis": self._read_open_world_first_analysis_context(
                manifest
            )
            or first_pass_mechanisms,
            "news_novelty_review": self._read_news_novelty_review_context(manifest),
            "memory_sweep_artifacts": manifest.memory_sweep_artifacts,
        }
        return (
            "Create additional semantic retrieval queries as SemanticRetrievalPlan. "
            "Queries must be mechanism-oriented and must cover every required category: "
            "positive analogs, negative controls, near misses, counterexamples, "
            "leader-selection pairs, theme-formation failures, and candidate-generation "
            "errors. Do not use exact keyword matching as a gate and do not request "
            "cutoff-after evidence.\n"
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
            available_record_ids, unavailable_record_ids = (
                self._filter_retrieved_record_ids_available_as_of(
                    raw_record_ids,
                    cutoff_at=cutoff_at,
                )
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
            Path("runs")
            / "checkpoints"
            / "semantic_retrieval"
            / manifest.run_id
            / "semantic_retrieval.jsonl"
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

    def _refresh_counterexample_record_ids_from_retrieval(
        self,
        manifest: ContextManifest,
    ) -> None:
        store = BrainRecordStore(self.root)
        if manifest.mode in {"exhaustive", "brain"}:
            source_record_ids = manifest.available_record_ids
        else:
            source_record_ids = [
                *manifest.retrieved_record_ids,
                *manifest.semantic_retrieval_record_ids,
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
            if record.record_type == "counterexample":
                counterexample_ids.append(record.record_id)
        manifest.counterexample_record_ids = counterexample_ids

    def _prediction_retrieved_record_ids(self, manifest: ContextManifest) -> list[str]:
        return _unique_preserving_order(
            [
                *manifest.retrieved_record_ids,
                *manifest.semantic_retrieval_record_ids,
            ]
        )

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
            Path("runs")
            / "checkpoints"
            / "candidate_expansion"
            / manifest.run_id
            / "candidate_expansion.json"
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
                {
                    candidate
                    for finding in normalized.findings
                    for candidate in finding.candidate_names
                }
            ),
            "requires_web_company_discovery_count": sum(
                1 for finding in normalized.findings if finding.requires_web_company_discovery
            ),
            "continuation_d_minus_one_only_verified": all(
                finding.d_minus_one_market_data_only
                for finding in normalized.findings
                if finding.path == CandidateExpansionPath.CONTINUATION
            ),
        }
        return normalized, prompt_sha256, max(1, len(prompt) // 4)

    def _build_candidate_expansion_prompt(
        self,
        *,
        news_texts: list[str],
        first_pass_mechanisms: list[str],
        manifest: ContextManifest,
        cutoff_at: datetime,
    ) -> str:
        payload = {
            "schema": "nslab.candidate_expansion.v1",
            "prompt_version": CANDIDATE_EXPANSION_PROMPT_VERSION,
            "run_id": manifest.run_id,
            "cutoff_at": cutoff_at.isoformat(),
            "required_paths": [path.value for path in CANDIDATE_EXPANSION_REQUIRED_PATHS],
            "current_news": news_texts,
            "open_world_first_analysis": self._read_open_world_first_analysis_context(
                manifest
            )
            or first_pass_mechanisms,
            "news_novelty_review": self._read_news_novelty_review_context(manifest),
            "additional_semantic_retrieval": self._read_semantic_retrieval_context(manifest),
            "d_minus_one_only_for_continuation": True,
        }
        return (
            "Expand open-world candidate routes as CandidateExpansionReview. Execute "
            "four independent paths: SINGLE_EVENT, THEME_FORMATION, "
            "BENEFICIARY_DISCOVERY, and CONTINUATION. Do not restrict candidates to "
            "existing memory. Do not use D-day prices or cutoff-after information. "
            "For CONTINUATION, mark d_minus_one_market_data_only true. Return "
            "investigation questions for web/company verification instead of hardcoded "
            "ticker or theme maps.\n"
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
        allowed_episode_ids = set(manifest.semantic_retrieval_episode_ids) | set(
            manifest.retrieved_episode_ids
        )
        for finding in review.findings:
            if finding.path not in CANDIDATE_EXPANSION_REQUIRED_PATHS:
                continue
            unknown_sources = sorted(
                source_id
                for source_id in finding.evidence_source_ids
                if source_id not in allowed_source_ids
            )
            if unknown_sources:
                raise ValueError(
                    "candidate expansion referenced unknown evidence_source_ids: "
                    + ", ".join(unknown_sources)
                )
            unknown_clusters = sorted(
                cluster_id
                for cluster_id in finding.related_cluster_ids
                if cluster_id not in allowed_cluster_ids
            )
            if unknown_clusters:
                raise ValueError(
                    "candidate expansion referenced unknown related_cluster_ids: "
                    + ", ".join(unknown_clusters)
                )
            unknown_episodes = sorted(
                episode_id
                for episode_id in finding.memory_episode_ids
                if episode_id not in allowed_episode_ids
            )
            if unknown_episodes:
                raise ValueError(
                    "candidate expansion referenced unavailable memory_episode_ids: "
                    + ", ".join(unknown_episodes)
                )
            if (
                finding.path == CandidateExpansionPath.CONTINUATION
                and not finding.d_minus_one_market_data_only
            ):
                finding = finding.model_copy(update={"d_minus_one_market_data_only": True})
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
        return review.model_copy(
            update={
                "run_id": manifest.run_id,
                "prompt_version": CANDIDATE_EXPANSION_PROMPT_VERSION,
                "prompt_sha256": prompt_sha256,
                "cutoff_at": cutoff_at,
                "required_paths": list(CANDIDATE_EXPANSION_REQUIRED_PATHS),
                "findings": findings,
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
        path: CandidateExpansionPath,
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
        path_text = path.value.lower().replace("_", " ")
        return CandidateExpansionFinding(
            path=path,
            hypothesis=f"{path_text} route requires open-world review of {mechanism}.",
            candidate_names=[f"{path.value}_DISCOVERY_REQUIRED"],
            sector_hypotheses=[f"{path_text} hypothesis from current catalyst"],
            investigation_questions=[
                f"Which listed entities fit the {path_text} route before cutoff?",
                "Which directness, novelty, and market-memory checks can disconfirm it?",
            ],
            evidence_source_ids=sorted(source_ids)[:5],
            related_cluster_ids=cluster_ids,
            memory_episode_ids=manifest.semantic_retrieval_episode_ids[:5],
            requires_web_company_discovery=path
            in {
                CandidateExpansionPath.SINGLE_EVENT,
                CandidateExpansionPath.THEME_FORMATION,
                CandidateExpansionPath.BENEFICIARY_DISCOVERY,
            },
            d_minus_one_market_data_only=path == CandidateExpansionPath.CONTINUATION,
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
        artifact_relative = (
            Path("runs")
            / "checkpoints"
            / "web_sources"
            / manifest.run_id
            / "web_sources.jsonl"
        )
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        manifest.web_source_artifact = artifact_relative.as_posix()
        manifest.web_source_sha256 = sha256_text(payload)
        excluded_artifact_relative = (
            Path("runs")
            / "checkpoints"
            / "web_sources"
            / manifest.run_id
            / "excluded_web_sources.jsonl"
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
            Path("runs")
            / "checkpoints"
            / "candidate_web_checks"
            / manifest.run_id
            / "candidate_web_checks.jsonl"
        )
        artifact_path = self.root / artifact_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(canonical_json(row) + "\n" for row in rows)
        artifact_path.write_text(payload, encoding="utf-8")
        manifest.candidate_web_check_artifact = artifact_relative.as_posix()
        manifest.candidate_web_check_sha256 = sha256_text(payload)
        manifest.candidate_web_check_count = len(rows)
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
            "expansion_paths": sorted(
                {
                    str(subject.expansion_path)
                    for subject in subjects
                    if subject.expansion_path
                }
            ),
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
        manifest.excluded_candidate_web_check_artifact = (
            excluded_artifact_relative.as_posix()
        )
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
            accepted = [
                row
                for row in rows
                if _candidate_web_check_row_key(row) == key
            ]
            excluded = [
                row
                for row in excluded_rows
                if _candidate_web_check_row_key(row) == key
            ]
            accepted_source_ids = _unique_preserving_order(
                [
                    str(row["source_id"])
                    for row in accepted
                    if isinstance(row.get("source_id"), str)
                ]
            )
            excluded_source_ids = _unique_preserving_order(
                [
                    str(row["source_id"])
                    for row in excluded
                    if isinstance(row.get("source_id"), str)
                ]
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
            created_at=now_kst(),
            cutoff_at=cutoff_at,
            required_dimensions=list(CANDIDATE_WEB_VERIFICATION_FOCUS),
            subject_count=len(subjects),
            findings=findings,
            notes=[
                "Pass 5 checklist records cutoff-safe verification coverage; final synthesis judges substance."
            ],
        )
        artifact_relative = (
            Path("runs")
            / "checkpoints"
            / "candidate_verifications"
            / manifest.run_id
            / "candidate_verification.json"
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
            "subjects_without_cutoff_safe_sources": sum(
                1 for finding in findings if not finding.accepted_source_ids
            ),
            "candidate_expansion_subject_count": sum(
                1 for finding in findings if finding.subject_type == "candidate_expansion"
            ),
            "d_minus_one_only_subject_count": sum(
                1 for finding in findings if finding.d_minus_one_market_data_only
            ),
            "d_minus_one_snapshot_count": sum(
                1
                for finding in findings
                if finding.blind_safe_market_snapshot.get("status") == "snapshot"
            ),
            "d_minus_one_snapshot_unavailable_count": sum(
                1
                for finding in findings
                if finding.blind_safe_market_snapshot.get("status") != "snapshot"
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
                notes = [
                    "expansion subject has no confirmed ticker yet; web/company discovery must resolve it"
                ]
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
                        investigation_questions=tuple(
                            _string_values(finding.get("investigation_questions"))[:5]
                        ),
                        sector_hypotheses=tuple(
                            _string_values(finding.get("sector_hypotheses"))[:5]
                        ),
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
                url.removeprefix("news://")
                for url in candidate.source_urls
                if url.startswith("news://")
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
                    "collected_at": (
                        item.collected_at.isoformat() if item.collected_at is not None else None
                    ),
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
        rows.extend(
            self._candidate_web_check_ledger_rows(manifest, retrieved_at=retrieved_at)
        )

        artifact_relative = (
            Path("runs")
            / "checkpoints"
            / "source_ledger"
            / manifest.run_id
            / "source_ledger.jsonl"
        )
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
            "blind_artifact_sha256": prediction.blind_artifact_sha256,
            "blind_prediction_path": prediction_relative,
            "row_disposition_sha256": manifest.row_disposition_sha256,
            "row_disposition_coverage_ratio": manifest.row_disposition_coverage_ratio,
            "source_ledger_sha256": manifest.source_ledger_sha256,
            "no_d_outcome_exposed": manifest.no_d_outcome_exposed,
            "validation": {
                "blind_web_search_call_count": manifest.blind_web_search_call_count,
                "blind_price_repository_access_count": (
                    manifest.blind_price_repository_access_count
                ),
                "blind_current_price_access_count": manifest.blind_current_price_access_count,
                "canonical_blind_hash_verified": True,
            },
        }
        receipt_relative = (
            Path("runs")
            / "checkpoints"
            / "blind_seal"
            / manifest.run_id
            / "blind_seal_receipt.json"
        )
        phase_relative = (
            Path("runs")
            / "checkpoints"
            / "phase_state"
            / manifest.run_id
            / "phase_state.json"
        )
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
        prediction_retrieved_record_ids = self._prediction_retrieved_record_ids(manifest)
        normalized = self._normalize_prediction(
            synthesized,
            trade_date=prediction.trade_date,
            cutoff_at=prediction.cutoff_at,
            event_ids=event_ids,
            excluded_source_ids=excluded_source_ids,
            prompt=prompt,
            purpose="final_synthesis",
            default_positive_case_ids=manifest.retrieved_episode_ids[:3],
            default_negative_case_ids=manifest.counterexample_episode_ids[:3],
            default_positive_record_ids=_record_ids_without(
                prediction_retrieved_record_ids,
                manifest.counterexample_record_ids,
            )[:5],
            default_negative_record_ids=manifest.counterexample_record_ids[:5],
        )
        normalized = normalized.model_copy(update={"context_manifest_id": manifest.run_id})
        if not normalized.blind_analysis.open_world_mechanisms:
            normalized = normalized.model_copy(
                update={
                    "blind_analysis": normalized.blind_analysis.model_copy(
                        update={"open_world_mechanisms": first_pass_mechanisms}
                    )
                }
            )
        return normalized, prompt_sha256, max(1, len(prompt) // 4)

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
        payload: dict[str, Any] = {
            "schema": "nslab.blind_prediction.v1",
            "prompt_version": FINAL_SYNTHESIS_PROMPT_VERSION,
            "required_inputs": [
                "current_news",
                "open_world_first_analysis",
                "news_novelty_review",
                "additional_semantic_retrieval",
                "open_world_candidate_expansion",
                "web_research",
                "global_brain",
                "all_shard_brains",
                "all_shard_contributions",
                "record_level_shard_contributions",
                "retrieved_raw_episodes",
                "retrieved_records",
                "positive_cases",
                "negative_cases",
                "positive_record_ids",
                "negative_record_ids",
                "counterexamples",
                "counterexample_records",
                "candidate_research",
                "candidate_web_checks",
                "candidate_verification",
                "red_team_output",
                "d_minus_one_market_data",
                "company_memory",
                "market_memory",
            ],
            "run_id": manifest.run_id,
            "trade_date": prediction.trade_date.isoformat(),
            "cutoff_at": prediction.cutoff_at.isoformat(),
            "brain_compiler": {
                "mode": manifest.compiler_mode,
                "provider": manifest.brain_compiler_provider,
                "model": manifest.brain_compiler_model,
                "catalog_only": manifest.brain_compiler_catalog_only,
            },
            "current_news": news_texts,
            "open_world_first_analysis": self._read_open_world_first_analysis_context(
                manifest
            )
            or first_pass_mechanisms,
            "event_clusters": self._read_event_cluster_context(manifest),
            "news_novelty_review": self._read_news_novelty_review_context(manifest),
            "additional_semantic_retrieval": self._read_semantic_retrieval_context(
                manifest
            ),
            "open_world_candidate_expansion": self._read_candidate_expansion_context(
                manifest
            ),
            "web_research": {
                "queries": manifest.web_queries,
                "included_sources": manifest.web_sources,
                "sources": self._read_web_source_context(manifest),
                "excluded_after_cutoff_source_ids": manifest.excluded_web_source_ids,
            },
            "global_brain": self._read_brain_context(manifest),
            "all_shard_brains": self._read_shard_brain_context(manifest),
            "all_shard_contributions": self._read_json_artifacts(
                manifest.memory_sweep_artifacts
            ),
            "memory_sweep_artifacts": manifest.memory_sweep_artifacts,
            "record_level_shard_contributions": self._read_json_artifacts(
                manifest.record_sweep_artifacts
            ),
            "record_sweep_artifacts": manifest.record_sweep_artifacts,
            "record_sweep_artifact_hashes": manifest.record_sweep_artifact_hashes,
            "record_sweep_shard_count": manifest.record_sweep_shard_count,
            "record_sweep_cache_hits": manifest.record_sweep_cache_hits,
            "accepted_record_count": manifest.accepted_record_count,
            "available_record_count": manifest.available_record_count,
            "retrieved_raw_episode_ids": manifest.retrieved_episode_ids,
            "excluded_retrieved_episode_ids": manifest.excluded_retrieved_episode_ids,
            "retrieved_record_ids": manifest.retrieved_record_ids,
            "excluded_retrieved_record_ids": manifest.excluded_retrieved_record_ids,
            "available_record_ids": manifest.available_record_ids,
            "training_eligible_available_record_count": (
                manifest.training_eligible_available_record_count
            ),
            "training_eligible_available_record_ids": (
                manifest.training_eligible_available_record_ids
            ),
            "swept_record_count": manifest.swept_record_count,
            "swept_record_ids": manifest.swept_record_ids,
            "semantic_retrieval_record_ids": manifest.semantic_retrieval_record_ids,
            "excluded_semantic_retrieval_record_ids": (
                manifest.excluded_semantic_retrieval_record_ids
            ),
            "retrieved_raw_episodes": self._read_retrieved_episode_context(manifest),
            "retrieved_records": self._read_retrieved_record_context(manifest),
            "positive_cases": _candidate_case_refs(prediction, "prior_positive_cases"),
            "negative_cases": _candidate_case_refs(prediction, "prior_negative_cases"),
            "positive_record_ids": _candidate_case_refs(
                prediction, "prior_positive_record_ids"
            ),
            "negative_record_ids": _candidate_case_refs(
                prediction, "prior_negative_record_ids"
            ),
            "counterexamples": self._read_counterexample_context(manifest),
            "counterexample_record_ids": manifest.counterexample_record_ids,
            "counterexample_records": self._read_counterexample_record_context(manifest),
            "candidate_research": prediction.model_dump(mode="json"),
            "candidate_web_checks": self._read_candidate_web_check_context(manifest),
            "candidate_verification": self._read_candidate_verification_context(
                manifest
            ),
            "red_team_output": red_team_artifact.model_dump(mode="json"),
            "d_minus_one_market_data": d_minus_one_market_data,
            "company_memory": company_memory_context,
            "market_memory": market_memory_context,
        }
        return payload

    def _build_final_synthesis_prompt(self, payload: dict[str, Any]) -> str:
        return (
            f"{self._load_synthesis_prompt().strip()}\n"
            "Return the final BlindPrediction. Keep qualitative confidence only, "
            "preserve red-team objections in candidate counterarguments, use only "
            "timestamp-verified web_research.sources, candidate_web_checks, "
            "candidate_verification, cutoff-safe company_memory, and "
            "cutoff-safe market_memory. Do not use "
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
        summary = final_synthesis_input_summary(payload)
        artifact = FinalSynthesisContextArtifact(
            run_id=manifest.run_id,
            prompt_version=FINAL_SYNTHESIS_PROMPT_VERSION,
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
        manifest.final_synthesis_context_artifact = artifact_path.relative_to(
            self.root
        ).as_posix()
        manifest.final_synthesis_context_sha256 = sha256_text(
            artifact_path.read_text(encoding="utf-8")
        )
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
        directory = self.root / "memory" / "company_memory"
        if not directory.exists():
            return []
        contexts: list[dict[str, Any]] = []
        included: list[str] = []
        omitted: list[dict[str, str]] = []
        for path in sorted(directory.glob("*.json")):
            relative_path = relative_to_root(path, self.root)
            try:
                memory = CompanyMemory.model_validate(read_json(path))
            except Exception:
                omitted.append({"path": relative_path, "reason": "invalid_company_memory_schema"})
                manifest.errors.append(f"company memory omitted due to invalid schema: {relative_path}")
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
            included.append(relative_path)
            contexts.append(
                {
                    "path": relative_path,
                    "sha256": sha256_text(canonical_json(memory.model_dump(mode="json"))),
                    "memory": memory.model_dump(mode="json"),
                }
            )
        manifest.included_company_memory_files = included
        manifest.omitted_company_memory_files = omitted
        return contexts

    def _collect_market_memory_context(
        self,
        *,
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> list[dict[str, Any]]:
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
                    payload["skipped_tickers"].append(
                        {"ticker": candidate.ticker, "reason": "ticker_not_verified"}
                    )
                    continue
                payload["skipped_tickers"].append(
                    {"ticker": ticker, "reason": "news_only_blind_price_access_disabled"}
                )
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
                payload["skipped_tickers"].append(
                    {"ticker": candidate.ticker, "reason": "ticker_not_verified"}
                )
                continue
            self._mark_d_minus_one_price_access(manifest)
            manifest.blind_price_repository_access_count += 1
            try:
                snapshot = guard.get_snapshot(ticker, as_of=allowed_through)
            except BlindPriceAccessError as exc:
                manifest.blind_current_price_access_count += 1
                payload.setdefault("errors", []).append(str(exc))
                payload["skipped_tickers"].append(
                    {"ticker": ticker, "reason": "blind_price_guard_rejected_access"}
                )
                continue
            if snapshot is None:
                payload["skipped_tickers"].append(
                    {"ticker": ticker, "reason": "d_minus_one_snapshot_unavailable"}
                )
                continue
            payload["snapshots"].append(_price_record_payload(snapshot))
        payload["blind_context_mode"] = manifest.blind_context_mode
        payload["blind_price_repository_access_count"] = (
            manifest.blind_price_repository_access_count
        )
        payload["blind_current_price_access_count"] = manifest.blind_current_price_access_count
        return payload

    def _mark_d_minus_one_price_access(self, manifest: ContextManifest) -> None:
        if manifest.blind_context_mode == "NEWS_ONLY_STRICT":
            manifest.blind_context_mode = "D_MINUS_ONE_PRICE_BLIND"
        elif manifest.blind_context_mode == "CUTOFF_SAFE_WEB_BLIND":
            manifest.blind_context_mode = "CUTOFF_SAFE_WEB_AND_D_MINUS_ONE_PRICE_BLIND"

    def _read_brain_context(self, manifest: ContextManifest) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for relative_path in manifest.brain_files:
            path = self.root / relative_path
            if not path.exists() or not path.is_file():
                files.append({"path": relative_path, "missing": True})
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
                    "text": path.read_text(encoding="utf-8"),
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
                    "counterexamples": [
                        claim.model_dump(mode="json") for claim in episode.counterexamples
                    ],
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
                "candidate_expansion_hypothesis": row.get(
                    "candidate_expansion_hypothesis"
                ),
                "candidate_investigation_questions": row.get(
                    "candidate_investigation_questions"
                ),
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
            checks.append(
                check
            )
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
        future_episode_ids = [
            episode.episode_id
            for episode in ResearchStore(self.root).list_accepted()
            if not is_available_as_of(episode.available_from, cutoff_at)
        ]
        leaked_ids = [
            episode_id
            for episode_id in future_episode_ids
            if self._context_files_contain_episode_id(manifest, episode_id)
        ]
        if not leaked_ids:
            return
        manifest.errors.append(
            "brain context contains future-unavailable episodes: " + ", ".join(leaked_ids)
        )
        manifest_dir = self.settings.path(self.settings.output_dirs.manifests)
        manifest_path = manifest_dir / f"{manifest.run_id}.json"
        write_json(manifest_path, manifest.model_dump(mode="json"))
        raise FutureContextLeakError(
            "brain context contains future-unavailable episodes; see "
            f"{manifest_path.relative_to(self.root).as_posix()}"
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
        episode_id_coverage_complete = self._exhaustive_episode_id_coverage_complete(
            manifest
        )
        record_id_coverage_complete = Counter(manifest.available_record_ids) == Counter(
            manifest.swept_record_ids
        )
        if (
            manifest.accepted_episode_count == manifest.swept_episode_count
            and episode_id_coverage_complete
            and manifest.available_record_count == manifest.swept_record_count
            and record_id_coverage_complete
            and not manifest.errors
        ):
            return
        if manifest.accepted_episode_count != manifest.swept_episode_count:
            manifest.errors.append(
                "exhaustive mode requires swept_episode_count == accepted_episode_count"
            )
        if not episode_id_coverage_complete:
            manifest.errors.append(
                "exhaustive mode requires swept_episode_ids to match available accepted episode ids"
            )
        if manifest.available_record_count != manifest.swept_record_count:
            manifest.errors.append(
                "exhaustive mode requires swept_record_count == available_record_count"
            )
        if not record_id_coverage_complete:
            manifest.errors.append(
                "exhaustive mode requires swept_record_ids to match available_record_ids"
            )
        manifest_dir = self.settings.path(self.settings.output_dirs.manifests)
        manifest_path = manifest_dir / f"{manifest.run_id}.json"
        write_json(manifest_path, manifest.model_dump(mode="json"))
        raise ExhaustiveCoverageError(
            "exhaustive memory coverage failed; see "
            f"{manifest_path.relative_to(self.root).as_posix()}"
        )

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
        return normalized, sha256_text(prompt), max(1, len(prompt) // 4)

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
        observed_at = now_kst()
        fallback_positive_case_ids = _unique_preserving_order(
            list(default_positive_case_ids or [])
        )[:3]
        fallback_negative_case_ids = _unique_preserving_order(
            list(default_negative_case_ids or [])
        )[:3]
        fallback_positive_record_ids = _unique_preserving_order(
            list(default_positive_record_ids or [])
        )[:5]
        fallback_negative_record_ids = _unique_preserving_order(
            list(default_negative_record_ids or [])
        )[:5]
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
        sectors = prediction.dominant_sectors or [
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
                        "supporting_cases": sector.supporting_cases
                        or fallback_positive_case_ids,
                        "contradicting_cases": sector.contradicting_cases
                        or fallback_negative_case_ids,
                        "supporting_record_ids": sector.supporting_record_ids
                        or fallback_positive_record_ids,
                        "contradicting_record_ids": sector.contradicting_record_ids
                        or fallback_negative_record_ids,
                        "provenance": sector_provenance,
                    }
                )
            )
        normalized_candidates = []
        for index, candidate in enumerate(prediction.candidates, start=1):
            candidate_event_ids = candidate.event_ids or event_ids[:1]
            prior_positive_cases = (
                candidate.prior_positive_cases or fallback_positive_case_ids
            )
            prior_negative_cases = (
                candidate.prior_negative_cases or fallback_negative_case_ids
            )
            prior_positive_record_ids = (
                candidate.prior_positive_record_ids or fallback_positive_record_ids
            )
            prior_negative_record_ids = (
                candidate.prior_negative_record_ids or fallback_negative_record_ids
            )
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
                "created_at": now_kst(),
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
                "open_world_first_analysis": {
                    "prompt_version": OPEN_WORLD_FIRST_ANALYSIS_PROMPT_VERSION
                },
                "news_novelty_review": {
                    "prompt_version": NEWS_NOVELTY_REVIEW_PROMPT_VERSION
                },
                "semantic_retrieval_plan": {
                    "prompt_version": SEMANTIC_RETRIEVAL_PLAN_PROMPT_VERSION
                },
                "candidate_expansion": {
                    "prompt_version": CANDIDATE_EXPANSION_PROMPT_VERSION
                },
                "daily_blind_analysis": {"prompt_version": DAILY_BLIND_PROMPT_VERSION},
                "red_team_candidate_review": {"prompt_version": RED_TEAM_PROMPT_VERSION},
                "final_synthesis": {"prompt_version": FINAL_SYNTHESIS_PROMPT_VERSION},
            },
            max_retries=self.settings.llm.max_retries,
        )

    def _llm_model_config(self, provider: LLMProvider) -> dict[str, Any]:
        if isinstance(provider, TracingLLMProvider):
            return dict(provider.model_config)
        config: dict[str, Any] = {
            "configured_provider": self.settings.llm_provider,
            "provider_class": type(provider).__name__,
            "max_concurrency": self.settings.limits.max_concurrency,
            "shard_episode_count": self.settings.limits.shard_episode_count,
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
        memory_case_ids = _unique_preserving_order(
            [*prior_positive_cases, *prior_negative_cases]
        )
        prior_positive_record_ids = _record_ids_without(
            retrieved_record_ids,
            counterexample_record_ids,
        )[:5]
        prior_negative_record_ids = _unique_preserving_order(counterexample_record_ids)[:5]
        memory_record_ids = _unique_preserving_order(
            [*prior_positive_record_ids, *prior_negative_record_ids]
        )
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
                candidate.company_name
                for candidate in candidates
                if candidate.path_type == PathType.SINGLE_EVENT
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
        sealed = prediction.model_copy(
            update={"sealed_at": now_kst(), "blind_artifact_sha256": None}
        )
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
    }
    return aliases.get(normalized)


def _semantic_record_filters(category: str) -> dict[str, Any]:
    if category == "positive_analogs":
        return {"training_eligible": True}
    if category in {"negative_controls", "near_misses"}:
        return {"training_eligible": False}
    if category == "counterexamples":
        return {"record_type": "counterexample"}
    if category == "leader_selection_pairs":
        return {"record_type": "blind_leader_preference_pair"}
    if category == "theme_formation_failures":
        return {"record_type": "supervised_theme_formation_case"}
    if category == "candidate_generation_errors":
        return {"record_type": sorted(CANDIDATE_ERROR_RECORD_TYPES)}
    return {}


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
    return [
        record_id
        for record_id in _unique_preserving_order(record_ids)
        if record_id not in excluded
    ]


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
        (
            str(row["candidate_expansion_path"])
            if row.get("candidate_expansion_path") is not None
            else None
        ),
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
