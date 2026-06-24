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
from news_scalping_lab.prices.base import BlindPriceGuard, PriceSource
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.reporting.render import render_preopen_report
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    canonical_json,
    is_available_as_of,
    now_kst,
    read_json,
    sha256_text,
    stable_id,
    write_json,
)
from news_scalping_lab.warehouse import WarehouseStore
from news_scalping_lab.web.factory import create_web_provider
from news_scalping_lab.web.provider import TemporalWebGuard, WebResearchProvider


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
        self.llm = self._trace_llm(llm or create_llm_provider(settings))
        self.fallback_llm = DeterministicMockLLMProvider()
        self.retrieval = retrieval or LocalRetrievalStore(self.root)
        self.price_source = price_source or create_price_source(self.settings)
        self.web_provider = web_provider or create_web_provider(self.settings)

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
        manifest.excluded_retrieved_episode_ids = excluded_retrieved_ids
        self._fail_if_brain_context_contains_unavailable_episodes(
            cutoff_at=cutoff_at,
            manifest=manifest,
        )

        web_guard = TemporalWebGuard(self.web_provider)
        if web_search:
            for query in web_queries[:5]:
                results = await web_guard.search(query, cutoff_at=cutoff_at)
                manifest.web_sources.extend(result.url for result in results)
            manifest.excluded_web_source_ids = sorted(set(web_guard.excluded_source_ids))

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
            cutoff_at=cutoff_at,
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

        prediction, blind_prompt_hash, blind_prompt_tokens = await self._generate_prediction(
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            news_texts=news_texts,
            event_ids=event_ids,
            retrieved_episode_ids=retrieved_ids,
            excluded_source_ids=web_guard.excluded_source_ids,
            first_pass_mechanisms=first_pass_mechanisms,
            context_payload={
                "run_id": manifest.run_id,
                "brain_version": manifest.brain_version,
                "accepted_episode_count": manifest.accepted_episode_count,
                "swept_episode_count": manifest.swept_episode_count,
                "swept_episode_ids": manifest.swept_episode_ids,
                "retrieved_episode_ids": manifest.retrieved_episode_ids,
                "excluded_retrieved_episode_ids": manifest.excluded_retrieved_episode_ids,
                "memory_sweep_artifacts": manifest.memory_sweep_artifacts,
                "web_queries": manifest.web_queries,
                "web_sources": manifest.web_sources,
                "excluded_web_source_ids": manifest.excluded_web_source_ids,
            },
        )
        manifest.token_counts["blind_analysis_prompt"] = blind_prompt_tokens
        prediction = prediction.model_copy(update={"context_manifest_id": manifest.run_id})
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
            price_guard=price_guard,
            manifest=manifest,
        )
        prediction, final_synthesis_prompt_hash, final_synthesis_prompt_tokens = (
            await self._run_final_synthesis(
                prediction=prediction,
                manifest=manifest,
                news_texts=news_texts,
                event_ids=event_ids,
                retrieved_episode_ids=retrieved_ids,
                excluded_source_ids=web_guard.excluded_source_ids,
                first_pass_mechanisms=first_pass_mechanisms,
                red_team_artifact=red_team.artifact,
                d_minus_one_market_data=d_minus_one_market_data,
            )
        )
        prediction = apply_red_team_findings(prediction, red_team.artifact)
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
        manifest_path = manifest_dir / f"{manifest.run_id}.json"
        write_json(prediction_path, prediction.model_dump(mode="json"))
        CompanyMemoryStore(self.root).upsert_from_candidates(
            prediction.candidates,
            prediction_path=prediction_path,
            known_at=prediction.cutoff_at,
        )
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
    ) -> tuple[BlindPrediction, str, int]:
        prompt = self._build_final_synthesis_prompt(
            prediction=prediction,
            manifest=manifest,
            news_texts=news_texts,
            first_pass_mechanisms=first_pass_mechanisms,
            red_team_artifact=red_team_artifact,
            d_minus_one_market_data=d_minus_one_market_data,
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
                "red_team_output",
                "d_minus_one_market_data",
            ],
            "run_id": manifest.run_id,
            "trade_date": prediction.trade_date.isoformat(),
            "cutoff_at": prediction.cutoff_at.isoformat(),
            "current_news": news_texts,
            "open_world_first_analysis": first_pass_mechanisms,
            "web_research": {
                "queries": manifest.web_queries,
                "included_sources": manifest.web_sources,
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
            "red_team_output": red_team_artifact.model_dump(mode="json"),
            "d_minus_one_market_data": d_minus_one_market_data,
        }
        return (
            f"{self._load_synthesis_prompt().strip()}\n"
            "Return the final BlindPrediction. Keep qualitative confidence only, "
            "preserve red-team objections in candidate counterarguments, and do not use "
            "D-day prices, outcomes, or cutoff-after sources.\n"
            "---FINAL_SYNTHESIS_PAYLOAD---\n"
            f"{canonical_json(payload)}"
        )

    def _collect_d_minus_one_market_data(
        self,
        *,
        candidates: list[Candidate],
        price_guard: BlindPriceGuard,
        manifest: ContextManifest,
    ) -> dict[str, Any]:
        allowed_through = manifest.price_snapshot.allowed_through
        payload: dict[str, Any] = {
            "source_name": price_guard.source_name,
            "allowed_through": allowed_through.isoformat() if allowed_through else None,
            "snapshots": [],
            "skipped_tickers": [],
        }
        if allowed_through is None:
            return payload
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
            snapshot = price_guard.get_snapshot(ticker, as_of=allowed_through)
            payload["snapshots"].append(
                {
                    "ticker": ticker,
                    "as_of": allowed_through.isoformat(),
                    "record": snapshot.__dict__ if snapshot is not None else None,
                }
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

    def _load_synthesis_prompt(self) -> str:
        path = self.root / "prompts" / "synthesis" / "final.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return (
            "Synthesize current news, web verification, global brain, swept memory, "
            "counterexamples, candidate research, red-team objections, and D-1 market data."
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
        normalized = self._normalize_prediction(
            prediction,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            event_ids=event_ids,
            excluded_source_ids=excluded_source_ids,
            prompt=prompt,
            purpose="daily_blind_analysis",
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
        purpose: str,
    ) -> BlindPrediction:
        prompt_hash = sha256_text(prompt)
        observed_at = now_kst()
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
        normalized_candidates = []
        for index, candidate in enumerate(prediction.candidates, start=1):
            candidate_event_ids = candidate.event_ids or event_ids[:1]
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
            default_metadata={"prompt_version": DAILY_BLIND_PROMPT_VERSION},
            purpose_metadata={
                "daily_blind_analysis": {"prompt_version": DAILY_BLIND_PROMPT_VERSION},
                "red_team_candidate_review": {"prompt_version": RED_TEAM_PROMPT_VERSION},
                "final_synthesis": {"prompt_version": FINAL_SYNTHESIS_PROMPT_VERSION},
            },
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
