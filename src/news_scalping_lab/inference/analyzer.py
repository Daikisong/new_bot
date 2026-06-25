"""Daily blind analysis pipeline."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.config import Settings
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.context.sweep import MemorySweeper
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    CompanyMemory,
    ConfidenceLabel,
    ContextManifest,
    DailyAnalysis,
    DominantSectorHypothesis,
    NewsItem,
    PathType,
    Provenance,
    RedTeamArtifact,
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
from news_scalping_lab.prices.base import PriceSource
from news_scalping_lab.reporting.render import render_preopen_report
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    canonical_json,
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
    """Raised when exhaustive mode fails to sweep every accepted episode exactly once."""


class FutureContextLeakError(RuntimeError):
    """Raised when the active brain context contains future-unavailable research."""


DAILY_BLIND_PROMPT_VERSION = "daily_blind_analysis.v1"
FINAL_SYNTHESIS_PROMPT_VERSION = "synthesis.final.v1"


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
        self.price_source = price_source
        self.web_provider = web_provider or create_web_provider(self.settings)

    def _blind_price_source_name(self) -> str:
        if self.price_source is not None:
            return self.price_source.source_name
        if self.settings.price_provider == "mock":
            return "mock-price"
        return f"{self.settings.price_provider}-deferred-news-only"

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
        batch = full_batch.before_or_at(cutoff_at)
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
        web_queries = self._build_web_queries(batch.items)
        raw_retrieved_ids = self.retrieval.search_semantic(" ".join(web_queries), limit=20)
        retrieved_ids, excluded_retrieved_ids = self._filter_retrieved_ids_available_as_of(
            raw_retrieved_ids,
            cutoff_at=cutoff_at,
        )
        event_ids = [item.event_id for item in batch.items]
        manifest = ContextAssembler(self.root).assemble(
            mode=mode,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            run_seed=run_seed,
            retrieved_episode_ids=retrieved_ids,
            web_queries=web_queries,
        )
        manifest.news_file = relative_to_root(full_batch.path, self.root)
        manifest.news_sha256 = full_batch.sha256
        manifest.news_row_count = full_batch.row_count
        manifest.included_news_row_count = batch.row_count
        manifest.excluded_news_row_count = full_batch.row_count - batch.row_count
        manifest.llm_model_config = {**self.llm_model_config, "analysis_mode": mode}
        manifest.excluded_retrieved_episode_ids = excluded_retrieved_ids
        self._write_row_disposition_artifact(
            full_items=full_batch.items,
            included_items=batch.items,
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

        blind_news_items = batch.items[: self.settings.limits.max_news_items_for_mock]
        news_texts = [item.combined_text for item in blind_news_items]
        first_pass_mechanisms = self._infer_first_pass_mechanisms(news_texts)
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
        )
        manifest.accepted_episode_count = sweep.accepted_episode_count
        manifest.swept_episode_count = len(sweep.swept_episode_ids)
        manifest.swept_episode_ids = sweep.swept_episode_ids
        manifest.memory_sweep_artifacts = sweep.artifact_paths
        manifest.memory_sweep_shard_count = sweep.shard_count
        manifest.memory_sweep_cache_hits = sweep.cache_hits
        manifest.token_counts.update(sweep.token_counts)
        manifest.token_counts["current_news"] = sum(len(text) for text in news_texts) // 4
        manifest.errors.extend(sweep.errors)
        self._fail_if_exhaustive_coverage_incomplete(manifest)

        prediction, blind_prompt_hash, blind_prompt_tokens = await self._generate_prediction(
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            news_texts=news_texts,
            event_ids=event_ids,
            retrieved_episode_ids=retrieved_ids,
            counterexample_episode_ids=manifest.counterexample_episode_ids,
            excluded_source_ids=[],
            first_pass_mechanisms=first_pass_mechanisms,
            context_payload={
                "run_id": manifest.run_id,
                "brain_version": manifest.brain_version,
                "accepted_episode_count": manifest.accepted_episode_count,
                "swept_episode_count": manifest.swept_episode_count,
                "swept_episode_ids": manifest.swept_episode_ids,
                "retrieved_episode_ids": manifest.retrieved_episode_ids,
                "excluded_retrieved_episode_ids": manifest.excluded_retrieved_episode_ids,
                "counterexample_episode_ids": manifest.counterexample_episode_ids,
                "memory_sweep_artifacts": manifest.memory_sweep_artifacts,
                "web_queries": manifest.web_queries,
                "web_sources": manifest.web_sources,
                "excluded_web_source_ids": manifest.excluded_web_source_ids,
                "web_source_artifact": manifest.web_source_artifact,
                "candidate_web_source_ids": manifest.candidate_web_source_ids,
                "excluded_candidate_web_source_ids": (
                    manifest.excluded_candidate_web_source_ids
                ),
                "candidate_web_check_artifact": manifest.candidate_web_check_artifact,
            },
        )
        manifest.token_counts["blind_analysis_prompt"] = blind_prompt_tokens
        prediction = prediction.model_copy(update={"context_manifest_id": manifest.run_id})
        if web_search:
            await self._collect_candidate_web_checks(
                prediction=prediction,
                manifest=manifest,
                cutoff_at=cutoff_at,
            )
        red_team = await run_red_team_pass(
            root=self.root,
            llm=self.llm,
            prediction=prediction,
            manifest=manifest,
        )
        prediction = apply_red_team_findings(prediction, red_team.artifact)
        manifest.red_team_artifacts = [red_team.artifact_path]
        manifest.token_counts["red_team_prompt"] = red_team.prompt_token_estimate
        d_minus_one_market_data = self._collect_d_minus_one_market_data(
            candidates=prediction.candidates,
            manifest=manifest,
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
        WarehouseStore(self.root).write_prediction(prediction)
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

    def _write_row_disposition_artifact(
        self,
        *,
        full_items: list[NewsItem],
        included_items: list[NewsItem],
        cutoff_at: datetime,
        manifest: ContextManifest,
    ) -> None:
        included_event_ids = {item.event_id for item in included_items}
        rows: list[dict[str, Any]] = []
        summary = {
            "total_rows": len(full_items),
            "included_before_cutoff": 0,
            "excluded_after_cutoff": 0,
        }
        for item in full_items:
            included = item.event_id in included_event_ids and item.published_at <= cutoff_at
            disposition = "INCLUDED_BEFORE_CUTOFF" if included else "EXCLUDED_AFTER_CUTOFF"
            summary_key = "included_before_cutoff" if included else "excluded_after_cutoff"
            summary[summary_key] += 1
            rows.append(
                {
                    "schema_version": "nslab.row_disposition.v1",
                    "run_id": manifest.run_id,
                    "row_number": item.row_number,
                    "event_id": item.event_id,
                    "published_at": item.published_at.isoformat(),
                    "source_id": item.source_id,
                    "disposition": disposition,
                    "eligible_for_blind_evidence": included,
                    "reason": (
                        "published_at <= cutoff_at"
                        if included
                        else "published_at > cutoff_at"
                    ),
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
            "snippet": result.snippet,
            "published_at": published_at.isoformat() if published_at else None,
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
            "snippet": result.snippet,
            "published_at": published_at.isoformat() if published_at else None,
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
    ) -> None:
        guard = TemporalWebGuard(self.web_provider)
        rows: list[dict[str, Any]] = []
        excluded_rows: list[dict[str, Any]] = []
        for candidate in sorted(prediction.candidates, key=lambda item: item.rank):
            query = self._candidate_web_check_query(candidate)
            manifest.blind_web_search_call_count += 1
            prior_exclusion_count = len(guard.excluded_sources)
            for result in await guard.search(query, cutoff_at=cutoff_at):
                rows.append(
                    self._candidate_web_check_row(
                        result,
                        candidate=candidate,
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
                        candidate=candidate,
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

    def _candidate_web_check_query(self, candidate: Candidate) -> str:
        focus = " ".join(
            [
                candidate.company_name,
                candidate.ticker,
                str(candidate.path_type),
                candidate.thesis,
                candidate.why_now,
            ]
        )
        return (
            "candidate verification listing ticker direct relation novelty disclosure "
            f"D-1 absorption liquidity competing leaders {focus[:500]}"
        )

    def _candidate_web_check_row(
        self,
        result: WebSearchResult,
        *,
        candidate: Candidate,
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
            "candidate_rank": candidate.rank,
            "candidate_ticker": candidate.ticker,
            "candidate_company_name": candidate.company_name,
            "candidate_path_type": str(candidate.path_type),
            "verification_focus": [
                "listed_security_and_exact_ticker",
                "direct_relation_to_current_news",
                "novelty_and_recent_disclosure",
                "D_minus_one_absorption",
                "liquidity_and_competing_leaders",
            ],
        }

    def _excluded_candidate_web_check_row(
        self,
        exclusion: WebSearchExclusion,
        *,
        candidate: Candidate,
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
            "candidate_rank": candidate.rank,
            "candidate_ticker": candidate.ticker,
            "candidate_company_name": candidate.company_name,
            "candidate_path_type": str(candidate.path_type),
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
                    "published_at": item.published_at.isoformat(),
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
                    "published_at": payload["published_at"],
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
                    "published_at": payload["published_at"],
                    "retrieved_at": retrieved_at.isoformat(),
                    "time_verified": payload["time_verified"],
                    "available_before_cutoff": payload["available_before_cutoff"],
                    "usage_phase": "BLIND",
                    "input_row_ids": [],
                    "event_ids": [],
                    "candidate_rank": payload["candidate_rank"],
                    "candidate_company_name": payload["candidate_company_name"],
                    "candidate_ticker": payload["candidate_ticker"],
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
        prompt = self._build_final_synthesis_prompt(
            prediction=prediction,
            manifest=manifest,
            news_texts=news_texts,
            first_pass_mechanisms=first_pass_mechanisms,
            red_team_artifact=red_team_artifact,
            d_minus_one_market_data=d_minus_one_market_data,
            company_memory_context=company_memory_context,
            market_memory_context=market_memory_context,
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

    def _build_final_synthesis_prompt(
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
    ) -> str:
        payload = {
            "schema": "nslab.blind_prediction.v1",
            "prompt_version": FINAL_SYNTHESIS_PROMPT_VERSION,
            "required_inputs": [
                "current_news",
                "open_world_first_analysis",
                "web_research",
                "global_brain",
                "all_shard_brains",
                "all_shard_contributions",
                "retrieved_raw_episodes",
                "positive_cases",
                "negative_cases",
                "counterexamples",
                "candidate_research",
                "candidate_web_checks",
                "red_team_output",
                "d_minus_one_market_data",
                "company_memory",
                "market_memory",
            ],
            "run_id": manifest.run_id,
            "trade_date": prediction.trade_date.isoformat(),
            "cutoff_at": prediction.cutoff_at.isoformat(),
            "current_news": news_texts,
            "open_world_first_analysis": first_pass_mechanisms,
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
            "retrieved_raw_episode_ids": manifest.retrieved_episode_ids,
            "excluded_retrieved_episode_ids": manifest.excluded_retrieved_episode_ids,
            "retrieved_raw_episodes": self._read_retrieved_episode_context(manifest),
            "positive_cases": _candidate_case_refs(prediction, "prior_positive_cases"),
            "negative_cases": _candidate_case_refs(prediction, "prior_negative_cases"),
            "counterexamples": self._read_counterexample_context(manifest),
            "candidate_research": prediction.model_dump(mode="json"),
            "candidate_web_checks": self._read_candidate_web_check_context(manifest),
            "red_team_output": red_team_artifact.model_dump(mode="json"),
            "d_minus_one_market_data": d_minus_one_market_data,
            "company_memory": company_memory_context,
            "market_memory": market_memory_context,
        }
        return (
            f"{self._load_synthesis_prompt().strip()}\n"
            "Return the final BlindPrediction. Keep qualitative confidence only, "
            "preserve red-team objections in candidate counterarguments, use only "
            "timestamp-verified web_research.sources, candidate_web_checks, "
            "cutoff-safe company_memory, and cutoff-safe market_memory. Do not use "
            "D-day prices, outcomes, unverified web results, or cutoff-after "
            "sources during BLIND.\n"
            "---FINAL_SYNTHESIS_PAYLOAD---\n"
            f"{canonical_json(payload)}"
        )

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
            "allowed_through": allowed_through.isoformat() if allowed_through else None,
            "blind_context_mode": manifest.blind_context_mode,
            "blind_price_repository_access_count": manifest.blind_price_repository_access_count,
            "blind_current_price_access_count": manifest.blind_current_price_access_count,
            "snapshots": [],
            "skipped_tickers": [],
        }
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
            payload["skipped_tickers"].append(
                {"ticker": ticker, "reason": "news_only_blind_price_access_disabled"}
            )
        return payload

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
            sources.append(
                {
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
            )
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
            checks.append(
                {
                    "candidate_rank": row.get("candidate_rank"),
                    "candidate_ticker": row.get("candidate_ticker"),
                    "candidate_company_name": row.get("candidate_company_name"),
                    "candidate_path_type": row.get("candidate_path_type"),
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
            )
        return checks

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
        if manifest.accepted_episode_count == manifest.swept_episode_count and not manifest.errors:
            return
        if manifest.accepted_episode_count != manifest.swept_episode_count:
            manifest.errors.append(
                "exhaustive mode requires swept_episode_count == accepted_episode_count"
            )
        manifest_dir = self.settings.path(self.settings.output_dirs.manifests)
        manifest_path = manifest_dir / f"{manifest.run_id}.json"
        write_json(manifest_path, manifest.model_dump(mode="json"))
        raise ExhaustiveCoverageError(
            "exhaustive memory coverage failed; see "
            f"{manifest_path.relative_to(self.root).as_posix()}"
        )

    async def _generate_prediction(
        self,
        *,
        trade_date: date,
        cutoff_at: datetime,
        news_texts: list[str],
        event_ids: list[str],
        retrieved_episode_ids: list[str],
        counterexample_episode_ids: list[str],
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
    ) -> BlindPrediction:
        prompt_hash = sha256_text(prompt)
        observed_at = now_kst()
        fallback_positive_case_ids = _unique_preserving_order(
            list(default_positive_case_ids or [])
        )[:3]
        fallback_negative_case_ids = _unique_preserving_order(
            list(default_negative_case_ids or [])
        )[:3]
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
            memory_episode_ids = _unique_preserving_order(
                [
                    *candidate.memory_episode_ids,
                    *prior_positive_cases,
                    *prior_negative_cases,
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
                        "memory_episode_ids": memory_episode_ids,
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
                "daily_blind_analysis": {"prompt_version": DAILY_BLIND_PROMPT_VERSION},
                "red_team_candidate_review": {"prompt_version": RED_TEAM_PROMPT_VERSION},
                "final_synthesis": {"prompt_version": FINAL_SYNTHESIS_PROMPT_VERSION},
            },
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
                novel_reasoning="A new beneficiary can be investigated even when retrieval returns no cases.",
                counterarguments=["theme breadth may fail", "indirect relation may be too weak"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
                source_urls=[f"news://{event_ids[0]}" if event_ids else "news://current-batch"],
                memory_episode_ids=memory_case_ids,
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
                counterarguments=["already exhausted", "no current catalyst overlap"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
                source_urls=["price://blind-safe-d-minus-one"],
                memory_episode_ids=memory_case_ids,
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
