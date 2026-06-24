"""Daily blind analysis pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.config import Settings
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.context.sweep import MemorySweeper
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    ConfidenceLabel,
    ContextManifest,
    DailyAnalysis,
    DominantSectorHypothesis,
    NewsItem,
    PathType,
)
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.prices.base import BlindPriceGuard, PriceSource
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.reporting.render import render_preopen_report
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.utils import canonical_json, now_kst, sha256_text, stable_id, write_json
from news_scalping_lab.warehouse import WarehouseStore
from news_scalping_lab.web.provider import MockWebResearchProvider, TemporalWebGuard


class ExhaustiveCoverageError(RuntimeError):
    """Raised when exhaustive mode fails to sweep every accepted episode exactly once."""


class DailyAnalyzer:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMProvider | None = None,
        retrieval: LocalRetrievalStore | None = None,
        price_source: PriceSource | None = None,
    ) -> None:
        self.settings = settings
        self.root = settings.project_root
        self.llm = self._trace_llm(llm or create_llm_provider(settings))
        self.fallback_llm = DeterministicMockLLMProvider()
        self.retrieval = retrieval or LocalRetrievalStore(self.root)
        self.price_source = price_source or create_price_source(self.settings)

    async def analyze(
        self,
        *,
        news_csv: Path,
        trade_date: date,
        cutoff_at: datetime,
        mode: str = "exhaustive",
        web_search: bool = False,
    ) -> DailyAnalysis:
        batch = load_news_csv(news_csv, trade_date=trade_date).before_or_at(cutoff_at)
        run_seed = sha256_text(f"{batch.sha256}|{trade_date}|{cutoff_at.isoformat()}|{mode}")
        web_queries = self._build_web_queries(batch.items)
        retrieved_ids = self.retrieval.search(" ".join(web_queries), limit=20)
        manifest = ContextAssembler(self.root).assemble(
            mode=mode,
            trade_date=trade_date,
            run_seed=run_seed,
            retrieved_episode_ids=retrieved_ids,
            web_queries=web_queries,
        )

        web_guard = TemporalWebGuard(MockWebResearchProvider())
        if web_search:
            for query in web_queries[:5]:
                results = await web_guard.search(query, cutoff_at=cutoff_at)
                manifest.web_sources.extend(result.url for result in results)

        price_guard = BlindPriceGuard(self.price_source, trade_date=trade_date)
        manifest.price_snapshot.source_name = price_guard.source_name

        news_texts = [
            item.combined_text for item in batch.items[: self.settings.limits.max_news_items_for_mock]
        ]
        first_pass_mechanisms = self._infer_first_pass_mechanisms(news_texts)
        sweep = MemorySweeper(
            self.root,
            shard_episode_count=self.settings.limits.shard_episode_count,
        ).sweep(
            mode=mode,
            trade_date=trade_date,
            run_id=manifest.run_id,
            current_news_texts=news_texts,
            first_pass_mechanisms=first_pass_mechanisms,
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

        prediction = await self._generate_prediction(
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            news_texts=news_texts,
            event_ids=[item.event_id for item in batch.items],
            retrieved_episode_ids=retrieved_ids,
            excluded_source_ids=web_guard.excluded_source_ids,
            first_pass_mechanisms=first_pass_mechanisms,
            context_payload={
                "run_id": manifest.run_id,
                "brain_version": manifest.brain_version,
                "accepted_episode_count": manifest.accepted_episode_count,
                "swept_episode_count": manifest.swept_episode_count,
                "swept_episode_ids": manifest.swept_episode_ids,
                "memory_sweep_artifacts": manifest.memory_sweep_artifacts,
                "web_queries": manifest.web_queries,
                "web_sources": manifest.web_sources,
            },
        )
        prediction = self._seal(prediction)
        manifest.web_sources = sorted(set(manifest.web_sources))
        manifest.prompt_hashes["blind_analysis"] = sha256_text(
            canonical_json(prediction.model_dump(mode="json"))
        )

        prediction_dir = self.settings.path(self.settings.output_dirs.predictions)
        report_dir = self.settings.path(self.settings.output_dirs.reports)
        manifest_dir = self.settings.path(self.settings.output_dirs.manifests)
        prediction_path = prediction_dir / f"{trade_date.isoformat()}.json"
        report_path = report_dir / f"{trade_date.isoformat()}_preopen.md"
        manifest_path = manifest_dir / f"{manifest.run_id}.json"
        write_json(prediction_path, prediction.model_dump(mode="json"))
        write_json(manifest_path, manifest.model_dump(mode="json"))
        WarehouseStore(self.root).write_prediction(prediction)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_preopen_report(prediction, manifest), encoding="utf-8")
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

    def _build_web_queries(self, items: Sequence[NewsItem]) -> list[str]:
        queries: list[str] = []
        for item in items[:10]:
            title = getattr(item, "title", "")
            if title:
                queries.append(f"verify listing relationship novelty {title[:80]}")
        if not queries:
            queries.append("open-world market catalyst company discovery")
        queries.extend(
            [
                "positive analogs negative analogs near misses counterexamples",
                "leader selection cases theme formation failures",
            ]
        )
        return queries

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
        excluded_source_ids: list[str],
        first_pass_mechanisms: list[str],
        context_payload: dict[str, Any],
    ) -> BlindPrediction:
        prompt = self._build_blind_prediction_prompt(
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            news_texts=news_texts,
            event_ids=event_ids,
            retrieved_episode_ids=retrieved_episode_ids,
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
                excluded_source_ids=excluded_source_ids,
                first_pass_mechanisms=first_pass_mechanisms,
            )
        return self._normalize_prediction(
            prediction,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            event_ids=event_ids,
            excluded_source_ids=excluded_source_ids,
            prompt=prompt,
        )

    def _build_blind_prediction_prompt(
        self,
        *,
        trade_date: date,
        cutoff_at: datetime,
        news_texts: list[str],
        event_ids: list[str],
        retrieved_episode_ids: list[str],
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
    ) -> BlindPrediction:
        analysis = prediction.blind_analysis.model_copy(
            update={
                "excluded_after_cutoff_source_ids": sorted(
                    {
                        *prediction.blind_analysis.excluded_after_cutoff_source_ids,
                        *excluded_source_ids,
                    }
                )
            }
        )
        normalized_candidates = []
        for index, candidate in enumerate(prediction.candidates, start=1):
            candidate_event_ids = candidate.event_ids or event_ids[:1]
            normalized_candidates.append(
                candidate.model_copy(update={"rank": index, "event_ids": candidate_event_ids})
            )
        return prediction.model_copy(
            update={
                "prediction_id": stable_id(
                    "PRED",
                    "daily_blind_analysis",
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
            model_config={"provider": type(provider).__name__, "configured": self.settings.llm_provider},
            default_metadata={"prompt_version": "daily_blind_analysis.v1"},
        )

    def _make_prediction(
        self,
        *,
        trade_date: date,
        cutoff_at: datetime,
        news_texts: list[str],
        event_ids: list[str],
        retrieved_episode_ids: list[str],
        excluded_source_ids: list[str],
        first_pass_mechanisms: list[str] | None = None,
    ) -> BlindPrediction:
        joined = "\n---NEWS---\n".join(news_texts)
        mechanisms = first_pass_mechanisms or self.fallback_llm.infer_mechanisms(joined)
        mentions = self.fallback_llm.extract_company_mentions(news_texts, limit=6)
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
                    prior_positive_cases=retrieved_episode_ids[:3],
                    prior_negative_cases=[],
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
                    memory_episode_ids=retrieved_episode_ids[:3],
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
                prior_positive_cases=retrieved_episode_ids[:3],
                novel_reasoning="A new beneficiary can be investigated even when retrieval returns no cases.",
                counterarguments=["theme breadth may fail", "indirect relation may be too weak"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
                source_urls=[f"news://{event_ids[0]}" if event_ids else "news://current-batch"],
                memory_episode_ids=retrieved_episode_ids[:3],
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
                counterarguments=["already exhausted", "no current catalyst overlap"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
                source_urls=["price://blind-safe-d-minus-one"],
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
            supporting_cases=retrieved_episode_ids[:5],
            contradicting_cases=[],
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
