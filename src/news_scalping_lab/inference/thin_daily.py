"""Production daily inference over a precompiled offline brain package.

This module deliberately owns a separate entrypoint from the legacy exhaustive
analyzer.  Historical records may be compiled offline or selected as bounded
witnesses by the brain index, but no historical payload is mapped by an LLM in
this call graph.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import BlindPrediction
from news_scalping_lab.contracts.offline_brain import (
    CurrentDayInterpretation,
    CurrentEventCapsule,
    DailyBrainContext,
    ThinDailyAnalysis,
    ThinDailyRunManifest,
)
from news_scalping_lab.inference.event_clustering import (
    EventCluster,
    EventClusteringResult,
    cluster_news_events,
)
from news_scalping_lab.ingest.news import (
    NewsBatch,
    load_news_csv,
)
from news_scalping_lab.llm.base import LLMProvider, count_provider_tokens
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.memory.runtime import production_embedding_method
from news_scalping_lab.retrieval.production_embedding import (
    create_configured_embedding_provider,
)
from news_scalping_lab.utils import (
    canonical_json,
    default_news_window_start,
    file_sha256,
    now_kst,
    relative_to_root,
    sha256_text,
    stable_id,
    write_json,
)

CURRENT_DAY_INTERPRETATION_PROMPT_VERSION = "thin_daily.current_day_interpretation.v1"
FINAL_MARKET_DECISION_PROMPT_VERSION = "thin_daily.final_market_decision.v1"
THIN_DAILY_ARCHITECTURE_VERSION = "one_time_brain_thin_daily.v1"
MAX_CURRENT_EVENT_PROMPT_BYTES = 180_000
MAX_EXACT_WITNESSES = 24

_SENTENCE_SPLIT_RE = re.compile(r"(?:[.!?]\s+|[\r\n]+)")
_NUMBER_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|[A-Za-z]{1,8}|\uac1c|\uba85|\uc8fc|\uc6d0|\uc5b5\uc6d0|\uc870\uc6d0)?"
)
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])(?:\d{6}|[A-Z]{1,6})(?![A-Za-z0-9])")
_KOREAN_LITERAL_RE = re.compile(r"[\uac00-\ud7a3A-Za-z0-9&()._-]{2,40}")
_PREDICATE_RE = re.compile(
    r"(?:signed|approved|confirmed|awarded|launched|acquired|sold|invested|"
    r"cancelled|canceled|withdrawn|denied|failed|"
    r"\uccb4\uacb0|\uc2b9\uc778|\ud655\uc815|\uc218\uc8fc|\uacf5\uae09|\ud22c\uc790|"
    r"\ucd9c\uc2dc|\uc778\uc218|\ub9e4\uac01|\ucde8\uc18c|\ucca0\ud68c|\uc911\ub2e8|\uc2e4\ud328)",
    re.IGNORECASE,
)
_MODALITY_RE = re.compile(
    r"(?:may|might|could|planned|expected|confirmed|signed|"
    r"\uc608\uc815|\uac80\ud1a0|\uacc4\ud68d|\ucd94\uc9c4|\ud655\uc815|\uccb4\uacb0|\uc2b9\uc778)",
    re.IGNORECASE,
)
_COUNTERPARTY_RE = re.compile(
    r"(?:\b(?:to|for|with)\s+)([A-Z][A-Za-z0-9&()._-]{1,39})|"
    r"([\uac00-\ud7a3A-Za-z0-9&()._-]{2,40})(?:\uc5d0\uac8c|\uc640|\uacfc)"
)


class DailyBrainContextProvider(Protocol):
    """Bounded local retrieval over a precompiled BrainPackage."""

    async def retrieve(
        self,
        *,
        interpretation: CurrentDayInterpretation,
        current_event_capsules: Sequence[CurrentEventCapsule],
        cutoff_at: datetime,
        max_exact_witnesses: int,
    ) -> DailyBrainContext:
        """Return cutoff-safe capsule/claim context without a corpus scan or LLM."""


class MissingBrainPackageProvider:
    """Fail closed until an immutable Offline Semantic Brain V2 is selected."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure_ready(self) -> None:
        pointer = self.root / "brain" / "current" / "brain_package_pointer.json"
        if pointer.is_file():
            raise RuntimeError("the selected BrainPackage exists but its bounded daily index reader is unavailable")
        raise FileNotFoundError(
            "Offline Semantic Brain V2 is not built or selected; analyze-daily will not fall back "
            "to the legacy historical raw-record path"
        )

    async def retrieve(
        self,
        *,
        interpretation: CurrentDayInterpretation,
        current_event_capsules: Sequence[CurrentEventCapsule],
        cutoff_at: datetime,
        max_exact_witnesses: int,
    ) -> DailyBrainContext:
        del interpretation, current_event_capsules, cutoff_at, max_exact_witnesses
        self.ensure_ready()
        raise AssertionError("unreachable")


class ThinDailyAnalyzer:
    """Two-call daily analyzer backed by a one-time offline brain."""

    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMProvider | None = None,
        embedding_provider: Any | None = None,
        brain_context_provider: DailyBrainContextProvider | None = None,
    ) -> None:
        if settings.llm.max_retries > 1:
            raise ValueError("analyze-daily allows at most one structured repair per logical call")
        self.settings = settings
        self.root = settings.project_root
        base_llm = llm or create_llm_provider(settings)
        self.llm_model_config = _llm_model_config(settings, base_llm)
        self.llm = _trace_daily_llm(settings, base_llm, self.llm_model_config)
        self.embedding_provider = embedding_provider or create_configured_embedding_provider(
            settings,
            production=(settings.event_cluster_fallback_policy.value == "fail-closed"),
            llm_provider=base_llm,
        )
        if brain_context_provider is None:
            from news_scalping_lab.brain.offline_v2 import (
                BrainPackageDailyContextProvider,
            )

            brain_context_provider = BrainPackageDailyContextProvider(
                settings,
                embedding_provider=self.embedding_provider,
            )
        self.brain_context_provider = brain_context_provider

    async def analyze(
        self,
        *,
        news_csv: Path,
        trade_date: date,
        cutoff_at: datetime,
        d_minus_one_context_path: Path | None = None,
    ) -> ThinDailyAnalysis:
        started = monotonic()
        ensure_ready = getattr(self.brain_context_provider, "ensure_ready", None)
        if callable(ensure_ready):
            ensure_ready()
        full_batch = load_news_csv(news_csv, trade_date=trade_date)
        window_start = default_news_window_start(trade_date)
        cutoff_safe_batch = full_batch.within_window(window_start, cutoff_at)
        clustering = await cluster_news_events(
            full_batch.items,
            window_start_at=window_start,
            cutoff_at=cutoff_at,
            embedding_provider=self.embedding_provider,
            embedding_batch_size=self.settings.limits.event_cluster_embedding_batch_size,
            similarity_threshold=self.settings.limits.event_cluster_similarity_threshold,
            max_semantic_variants=self.settings.limits.event_cluster_max_semantic_variants,
            fallback_policy=self.settings.event_cluster_fallback_policy,
            max_retries=self.settings.llm.max_retries,
            production_runtime_identity=(
                production_embedding_method(self.settings, self.embedding_provider)
                if self.settings.event_cluster_fallback_policy.value == "fail-closed"
                else None
            ),
        )
        capsules = build_current_event_capsules(clustering)
        if not capsules:
            raise ValueError("analyze-daily requires at least one cutoff-safe material event cluster")
        prompt_capsules = project_current_event_capsules(
            capsules,
            max_bytes=MAX_CURRENT_EVENT_PROMPT_BYTES,
        )
        d_minus_one_context = _load_d_minus_one_context(
            d_minus_one_context_path,
            trade_date=trade_date,
        )
        run_id = stable_id(
            "THINRUN",
            THIN_DAILY_ARCHITECTURE_VERSION,
            full_batch.sha256,
            trade_date.isoformat(),
            cutoff_at.isoformat(),
            sha256_text(canonical_json([row.model_dump(mode="json") for row in capsules])),
            length=20,
        )
        output_root = self.root / "runs" / "thin_daily" / run_id
        output_root.mkdir(parents=True, exist_ok=True)

        capsules_path = output_root / "current_event_capsules.json"
        row_disposition_path = output_root / "row_dispositions.json"
        write_json(
            capsules_path,
            {
                "schema_version": "nslab.current_event_capsule_set.v1",
                "run_id": run_id,
                "items": [row.model_dump(mode="json") for row in capsules],
            },
        )
        write_json(
            row_disposition_path,
            _row_disposition_payload(
                run_id=run_id,
                full_batch=full_batch,
                clustering=clustering,
                window_start=window_start,
                cutoff_at=cutoff_at,
            ),
        )

        interpretation_prompt = _build_current_day_interpretation_prompt(
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            capsules=prompt_capsules,
            d_minus_one_context=d_minus_one_context,
        )
        interpretation = await self.llm.generate_structured(
            prompt=interpretation_prompt,
            response_model=CurrentDayInterpretation,
            purpose="current_day_interpretation",
        )
        interpretation = _validate_interpretation(interpretation, capsules=capsules)
        interpretation_path = output_root / "current_day_interpretation.json"
        write_json(interpretation_path, interpretation.model_dump(mode="json"))

        brain_context = await self.brain_context_provider.retrieve(
            interpretation=interpretation,
            current_event_capsules=capsules,
            cutoff_at=cutoff_at,
            max_exact_witnesses=MAX_EXACT_WITNESSES,
        )
        interpretation_sha256 = sha256_text(canonical_json(interpretation.model_dump(mode="json")))
        if brain_context.interpretation_sha256 != interpretation_sha256:
            raise ValueError("daily brain context is bound to a different interpretation")
        _validate_brain_context_as_of(brain_context, cutoff_at=cutoff_at)
        brain_context_path = output_root / "daily_brain_context.json"
        write_json(brain_context_path, brain_context.model_dump(mode="json"))

        final_prompt = _build_final_market_decision_prompt(
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            capsules=prompt_capsules,
            interpretation=interpretation,
            brain_context=brain_context,
            d_minus_one_context=d_minus_one_context,
        )
        prediction = await self.llm.generate_structured(
            prompt=final_prompt,
            response_model=BlindPrediction,
            purpose="final_market_decision",
        )
        prediction = _validate_and_seal_prediction(
            prediction,
            run_id=run_id,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            capsules=capsules,
            brain_context=brain_context,
        )

        prediction_path = output_root / "blind_prediction.json"
        report_path = output_root / "preopen_report.md"
        write_json(prediction_path, prediction.model_dump(mode="json"))
        report_text = _render_thin_daily_report(
            prediction,
            run_id=run_id,
            brain_context=brain_context,
        )
        report_path.write_text(report_text, encoding="utf-8", newline="\n")

        canonical_prediction_path = (
            self.settings.path(self.settings.output_dirs.predictions) / f"{trade_date.isoformat()}.json"
        )
        canonical_report_path = (
            self.settings.path(self.settings.output_dirs.reports) / f"{trade_date.isoformat()}_preopen.md"
        )
        write_json(canonical_prediction_path, prediction.model_dump(mode="json"))
        canonical_report_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_report_path.write_text(report_text, encoding="utf-8", newline="\n")

        prompt_hashes = {
            "current_day_interpretation": sha256_text(interpretation_prompt),
            "final_market_decision": sha256_text(final_prompt),
        }
        token_counts = {
            "current_day_interpretation": count_provider_tokens(self.llm, interpretation_prompt),
            "final_market_decision": count_provider_tokens(self.llm, final_prompt),
        }
        manifest = ThinDailyRunManifest(
            run_id=run_id,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            created_at=now_kst(),
            wall_clock_seconds=round(monotonic() - started, 6),
            news_file=relative_to_root(full_batch.path, self.root),
            news_sha256=full_batch.sha256,
            total_news_row_count=full_batch.row_count,
            cutoff_safe_news_row_count=cutoff_safe_batch.row_count,
            row_disposition_count=full_batch.row_count,
            material_event_cluster_count=len(clustering.material_clusters),
            current_event_capsule_count=len(capsules),
            current_event_capsule_bytes=len(
                canonical_json([row.model_dump(mode="json") for row in capsules]).encode("utf-8")
            ),
            current_event_prompt_bytes=len(interpretation_prompt.encode("utf-8")),
            daily_brain_context_bytes=len(canonical_json(brain_context.model_dump(mode="json")).encode("utf-8")),
            historical_raw_witness_count=len(brain_context.exact_witnesses),
            logical_llm_call_count=2,
            maximum_live_agent_call_count=2 * (1 + self.settings.llm.max_retries),
            historical_raw_daily_map_call_count=0,
            daily_import_call_count=0,
            daily_brain_rebuild_call_count=0,
            blind_web_search_call_count=0,
            online_full_corpus_scan_count=brain_context.online_full_corpus_scan_count,
            future_record_count=brain_context.future_record_count,
            llm_purposes=["current_day_interpretation", "final_market_decision"],
            llm_model_config=self.llm_model_config,
            brain_version=brain_context.brain_version,
            brain_package_root=brain_context.brain_package_root,
            current_event_capsules_artifact=relative_to_root(capsules_path, self.root),
            current_event_capsules_sha256=file_sha256(capsules_path),
            current_day_interpretation_artifact=relative_to_root(interpretation_path, self.root),
            current_day_interpretation_sha256=file_sha256(interpretation_path),
            daily_brain_context_artifact=relative_to_root(brain_context_path, self.root),
            daily_brain_context_sha256=file_sha256(brain_context_path),
            row_disposition_artifact=relative_to_root(row_disposition_path, self.root),
            row_disposition_sha256=file_sha256(row_disposition_path),
            prediction_artifact=relative_to_root(prediction_path, self.root),
            prediction_sha256=file_sha256(prediction_path),
            report_artifact=relative_to_root(report_path, self.root),
            report_sha256=file_sha256(report_path),
            prompt_hashes=prompt_hashes,
            token_counts=token_counts,
        )
        manifest_path = self.settings.path(self.settings.output_dirs.manifests) / (f"{run_id}.json")
        write_json(manifest_path, manifest.model_dump(mode="json"))
        return ThinDailyAnalysis(
            run_id=run_id,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            created_at=manifest.created_at,
            blind_prediction=prediction,
            context_manifest=manifest,
            report_path=relative_to_root(canonical_report_path, self.root),
            prediction_path=relative_to_root(canonical_prediction_path, self.root),
        )


def build_current_event_capsules(
    clustering: EventClusteringResult,
) -> list[CurrentEventCapsule]:
    return [_current_event_capsule(cluster) for cluster in clustering.material_clusters]


def project_current_event_capsules(
    capsules: Sequence[CurrentEventCapsule],
    *,
    max_bytes: int,
) -> list[CurrentEventCapsule]:
    if max_bytes < 1:
        raise ValueError("current event prompt byte budget must be positive")
    full = list(capsules)
    if _capsule_bytes(full) <= max_bytes:
        return full
    compact = [
        row.model_copy(
            update={
                "predicate_exact_sentences": row.predicate_exact_sentences[:1],
                "issuer_company_literals": row.issuer_company_literals[:4],
                "ticker_literals": row.ticker_literals[:4],
                "counterparty_literals": row.counterparty_literals[:2],
                "numeric_unit_literals": row.numeric_unit_literals[:4],
                "modality_literals": row.modality_literals[:2],
                "projection_tier": "COMPACT",
            }
        )
        for row in full
    ]
    if _capsule_bytes(compact) <= max_bytes:
        return compact
    identity_only = [
        row.model_copy(
            update={
                "representative_title": row.representative_title[:180],
                "predicate_exact_sentences": [],
                "issuer_company_literals": [],
                "ticker_literals": row.ticker_literals[:2],
                "counterparty_literals": [],
                "numeric_unit_literals": row.numeric_unit_literals[:2],
                "modality_literals": row.modality_literals[:1],
                "projection_tier": "IDENTITY_ONLY",
            }
        )
        for row in full
    ]
    if _capsule_bytes(identity_only) > max_bytes:
        raise ValueError("all current material clusters cannot fit the single-call identity projection")
    return identity_only


def _current_event_capsule(cluster: EventCluster) -> CurrentEventCapsule:
    representative = cluster.representative
    exact_sentences = _predicate_sentences(representative.title, representative.body)
    combined_projection = "\n".join([representative.title, *exact_sentences])
    member_combined = "\n".join(item.combined_text for item in cluster.members)
    number_sets = {tuple(_unique(_NUMBER_UNIT_RE.findall(item.combined_text))) for item in cluster.members}
    polarity_states = {bool(_PREDICATE_RE.search(item.combined_text)) for item in cluster.members}
    conflicts: list[str] = []
    if len(number_sets) > 1:
        conflicts.append("NUMERIC_VARIANT")
    if len(polarity_states) > 1:
        conflicts.append("PREDICATE_VARIANT")
    return CurrentEventCapsule(
        cluster_id=cluster.cluster_id,
        source_row_ids=sorted(item.row_number for item in cluster.members),
        event_ids=sorted(item.event_id for item in cluster.members),
        source_ids=sorted(item.source_id for item in cluster.members),
        representative_title=representative.title,
        predicate_exact_sentences=exact_sentences,
        issuer_company_literals=_issuer_literals(representative.title),
        ticker_literals=_unique(_TICKER_RE.findall(member_combined)),
        counterparty_literals=_counterparties(combined_projection),
        numeric_unit_literals=_unique(_NUMBER_UNIT_RE.findall(combined_projection)),
        modality_literals=_unique(match.group(0) for match in _MODALITY_RE.finditer(combined_projection)),
        published_times=sorted({item.published_at for item in cluster.members}),
        exact_duplicate_count=cluster.exact_duplicate_count,
        semantic_duplicate_count=cluster.semantic_duplicate_count,
        conflict_flags=conflicts,
    )


def _predicate_sentences(title: str, body: str) -> list[str]:
    candidates = [title, *_SENTENCE_SPLIT_RE.split(body)]
    selected: list[str] = []
    for raw in candidates:
        sentence = " ".join(raw.split()).strip()
        if not sentence:
            continue
        if sentence == title or _PREDICATE_RE.search(sentence) or _NUMBER_UNIT_RE.search(sentence):
            clipped = sentence[:360]
            if clipped not in selected:
                selected.append(clipped)
        if len(selected) >= 3:
            break
    return selected


def _issuer_literals(title: str) -> list[str]:
    prefix = re.split(r"[,;:|]", title, maxsplit=1)[0]
    return _unique(value for value in _KOREAN_LITERAL_RE.findall(prefix) if not value.isdigit())[:6]


def _counterparties(text: str) -> list[str]:
    values: list[str] = []
    for match in _COUNTERPARTY_RE.finditer(text):
        value = match.group(1) or match.group(2)
        if value:
            values.append(value)
    return _unique(values)


def _build_current_day_interpretation_prompt(
    *,
    trade_date: date,
    cutoff_at: datetime,
    capsules: Sequence[CurrentEventCapsule],
    d_minus_one_context: dict[str, Any],
) -> str:
    payload = {
        "schema": CURRENT_DAY_INTERPRETATION_PROMPT_VERSION,
        "trade_date": trade_date.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "required_cluster_ids": [row.cluster_id for row in capsules],
        "current_event_capsules": [row.model_dump(mode="json") for row in capsules],
        "d_minus_one_safe_context": d_minus_one_context,
    }
    return (
        "Interpret every current event capsule in one open-world pass. Do not use a "
        "historical candidate list as a gate. Return every required cluster ID exactly "
        "once, plus mechanisms, candidate archetypes, beneficiary paths, uncertainties, "
        "and retrieval queries. Do not infer D-day outcomes or use web evidence.\n"
        "---CURRENT_EVENT_CAPSULES---\n"
        f"{canonical_json(payload)}"
    )


def _build_final_market_decision_prompt(
    *,
    trade_date: date,
    cutoff_at: datetime,
    capsules: Sequence[CurrentEventCapsule],
    interpretation: CurrentDayInterpretation,
    brain_context: DailyBrainContext,
    d_minus_one_context: dict[str, Any],
) -> str:
    capsule_ids = [row.capsule_id for row in brain_context.selected_semantic_capsules]
    claim_ids = [row.claim_id for row in brain_context.selected_mechanism_claims]
    record_ids = sorted(
        {witness.record_id for witness in brain_context.exact_witnesses}
        | {
            record_id
            for row in brain_context.selected_semantic_capsules
            for record_id in [
                *row.supporting_record_ids,
                *row.contradicting_record_ids,
                *row.near_miss_record_ids,
                *row.counterexample_record_ids,
                *row.newsless_or_unexplained_record_ids,
                *row.error_record_ids,
            ]
        }
    )
    payload = {
        "schema": FINAL_MARKET_DECISION_PROMPT_VERSION,
        "trade_date": trade_date.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "event_ids": sorted({event_id for row in capsules for event_id in row.event_ids}),
        "source_row_ids": sorted({row_id for row in capsules for row_id in row.source_row_ids}),
        "current_news": [row.representative_title for row in capsules],
        "current_event_capsules": [row.model_dump(mode="json") for row in capsules],
        "current_day_interpretation": interpretation.model_dump(mode="json"),
        "daily_brain_context": brain_context.model_dump(mode="json"),
        "d_minus_one_safe_context": d_minus_one_context,
        "first_pass_mechanisms": interpretation.policy_industry_macro_mechanisms,
        "retrieved_record_ids": record_ids,
        "positive_record_ids": record_ids,
        "negative_record_ids": sorted(
            {
                record_id
                for row in brain_context.selected_semantic_capsules
                for record_id in [
                    *row.contradicting_record_ids,
                    *row.counterexample_record_ids,
                    *row.near_miss_record_ids,
                ]
            }
        ),
        "allowed_semantic_capsule_ids": capsule_ids,
        "allowed_mechanism_claim_ids": claim_ids,
    }
    return (
        "Produce the final blind pre-open market decision as BlindPrediction in one call. "
        "Perform dominant-sector, direct single-news, policy/industry beneficiary, leader, "
        "continuation, ranking, and red-team reasoning together. Do not launch subcalls. "
        "Every cited event, source row, semantic capsule, mechanism claim, population root, "
        "and record must come from the payload. Cite capsule IDs for summarized evidence and "
        "record IDs only for exact witnesses or capsule provenance. Do not use web or D-day "
        "outcomes.\n"
        "---BLIND_ANALYSIS_PAYLOAD---\n"
        f"{canonical_json(payload)}"
    )


def _validate_interpretation(
    interpretation: CurrentDayInterpretation,
    *,
    capsules: Sequence[CurrentEventCapsule],
) -> CurrentDayInterpretation:
    expected = [row.cluster_id for row in capsules]
    if len(interpretation.analyzed_cluster_ids) != len(set(interpretation.analyzed_cluster_ids)):
        raise ValueError("current-day interpretation contains duplicate cluster IDs")
    if set(interpretation.analyzed_cluster_ids) != set(expected):
        raise ValueError("current-day interpretation did not cover every material cluster")
    return interpretation.model_copy(update={"analyzed_cluster_ids": expected})


def _validate_and_seal_prediction(
    prediction: BlindPrediction,
    *,
    run_id: str,
    trade_date: date,
    cutoff_at: datetime,
    capsules: Sequence[CurrentEventCapsule],
    brain_context: DailyBrainContext,
) -> BlindPrediction:
    allowed_events = {event_id for row in capsules for event_id in row.event_ids}
    allowed_rows = {row_id for row in capsules for row_id in row.source_row_ids}
    allowed_capsules = {row.capsule_id for row in brain_context.selected_semantic_capsules}
    allowed_claims = {row.claim_id for row in brain_context.selected_mechanism_claims}
    allowed_records = {witness.record_id for witness in brain_context.exact_witnesses} | {
        record_id
        for row in brain_context.selected_semantic_capsules
        for record_id in [
            *row.supporting_record_ids,
            *row.contradicting_record_ids,
            *row.near_miss_record_ids,
            *row.counterexample_record_ids,
            *row.newsless_or_unexplained_record_ids,
            *row.error_record_ids,
        ]
    }
    allowed_population_roots = {
        str(row["population_root"])
        for row in brain_context.population_statistics
        if isinstance(row.get("population_root"), str)
    }
    cited_brain_ids: set[str] = set()
    for candidate in prediction.candidates:
        if not candidate.source_row_ids:
            raise ValueError("every final candidate must cite current source rows")
        if not set(candidate.event_ids).issubset(allowed_events):
            raise ValueError("final decision cited an event outside current capsules")
        if not set(candidate.source_row_ids).issubset(allowed_rows):
            raise ValueError("final decision cited an unknown source row")
        if not set(candidate.semantic_capsule_ids).issubset(allowed_capsules):
            raise ValueError("final decision cited an unselected semantic capsule")
        if not set(candidate.mechanism_claim_ids).issubset(allowed_claims):
            raise ValueError("final decision cited an unselected mechanism claim")
        if not set(candidate.population_manifest_roots).issubset(allowed_population_roots):
            raise ValueError("final decision cited an unselected population root")
        cited_records = {
            *candidate.memory_record_ids,
            *candidate.prior_positive_record_ids,
            *candidate.prior_negative_record_ids,
        }
        if not cited_records.issubset(allowed_records):
            raise ValueError("final decision cited a record outside daily brain context")
        cited_brain_ids.update(candidate.semantic_capsule_ids)
        cited_brain_ids.update(candidate.mechanism_claim_ids)
    for sector in prediction.dominant_sectors:
        if not set(sector.triggering_events).issubset(allowed_events):
            raise ValueError("sector decision cited an event outside current capsules")
        if not set(sector.semantic_capsule_ids).issubset(allowed_capsules):
            raise ValueError("sector decision cited an unselected semantic capsule")
        if not set(sector.mechanism_claim_ids).issubset(allowed_claims):
            raise ValueError("sector decision cited an unselected mechanism claim")
        if not set(sector.population_manifest_roots).issubset(allowed_population_roots):
            raise ValueError("sector decision cited an unselected population root")
        if not {
            *sector.supporting_record_ids,
            *sector.contradicting_record_ids,
        }.issubset(allowed_records):
            raise ValueError("sector decision cited a record outside daily brain context")
        cited_brain_ids.update(sector.semantic_capsule_ids)
        cited_brain_ids.update(sector.mechanism_claim_ids)
    if (allowed_capsules or allowed_claims) and not cited_brain_ids:
        raise ValueError("final decision did not cite the selected offline brain")
    normalized = prediction.model_copy(
        update={
            "trade_date": trade_date,
            "cutoff_at": cutoff_at,
            "context_manifest_id": run_id,
            "sealed_at": None,
            "blind_artifact_sha256": None,
        }
    )
    sealed = normalized.model_copy(update={"sealed_at": now_kst()})
    digest = sha256_text(canonical_json(sealed.model_dump(mode="json")))
    return sealed.model_copy(update={"blind_artifact_sha256": digest})


def _validate_brain_context_as_of(
    context: DailyBrainContext,
    *,
    cutoff_at: datetime,
) -> None:
    future_capsules = [row.capsule_id for row in context.selected_semantic_capsules if row.available_from > cutoff_at]
    future_claims = [row.claim_id for row in context.selected_mechanism_claims if row.available_from > cutoff_at]
    future_witnesses = [row.record_id for row in context.exact_witnesses if row.available_from > cutoff_at]
    if future_capsules or future_claims or future_witnesses:
        raise ValueError("daily brain context contains cutoff-after knowledge")
    selected_capsule_ids = {row.capsule_id for row in context.selected_semantic_capsules}
    selected_record_ids = {
        record_id
        for row in context.selected_semantic_capsules
        for record_id in [
            *row.supporting_record_ids,
            *row.contradicting_record_ids,
            *row.near_miss_record_ids,
            *row.counterexample_record_ids,
            *row.newsless_or_unexplained_record_ids,
            *row.error_record_ids,
        ]
    }
    for claim in context.selected_mechanism_claims:
        if not {
            *claim.supporting_capsule_ids,
            *claim.contradicting_capsule_ids,
        }.issubset(selected_capsule_ids):
            raise ValueError("daily mechanism claim does not close over selected capsules")
        if not {
            *claim.supporting_record_ids,
            *claim.contradicting_record_ids,
        }.issubset(selected_record_ids):
            raise ValueError("daily mechanism claim does not close over selected records")


def _load_d_minus_one_context(
    path: Path | None,
    *,
    trade_date: date,
) -> dict[str, Any]:
    if path is None:
        return {
            "schema_version": "nslab.d_minus_one_safe_context.inline.v1",
            "status": "NOT_PROVIDED",
            "allowed_through": None,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("D-1 context must be a JSON object")
    allowed_through = payload.get("allowed_through")
    if not isinstance(allowed_through, str):
        raise ValueError("D-1 context must declare allowed_through")
    if date.fromisoformat(allowed_through) >= trade_date:
        raise ValueError("D-1 context cannot include the trade date or future")
    return payload


def _row_disposition_payload(
    *,
    run_id: str,
    full_batch: NewsBatch,
    clustering: EventClusteringResult,
    window_start: datetime,
    cutoff_at: datetime,
) -> dict[str, Any]:
    cluster_by_event = {item.event_id: cluster for cluster in clustering.clusters for item in cluster.members}
    rows = []
    for item in sorted(full_batch.items, key=lambda row: row.row_number):
        cluster = cluster_by_event.get(item.event_id)
        rows.append(
            {
                "row_number": item.row_number,
                "event_id": item.event_id,
                "source_id": item.source_id,
                "published_at": item.published_at.isoformat(),
                "cluster_id": cluster.cluster_id if cluster is not None else None,
                "disposition": cluster.disposition if cluster is not None else "UNCLUSTERED",
                "within_cutoff_window": window_start <= item.published_at <= cutoff_at,
            }
        )
    if len(rows) != full_batch.row_count:
        raise ValueError("row disposition coverage is incomplete")
    return {
        "schema_version": "nslab.thin_daily_row_dispositions.v1",
        "run_id": run_id,
        "row_count": len(rows),
        "rows": rows,
    }


def _render_thin_daily_report(
    prediction: BlindPrediction,
    *,
    run_id: str,
    brain_context: DailyBrainContext,
) -> str:
    lines = [
        "# Pre-open Decision",
        "",
        f"- Run: `{run_id}`",
        f"- Trade date: `{prediction.trade_date.isoformat()}`",
        f"- Cutoff: `{prediction.cutoff_at.isoformat()}`",
        f"- Brain: `{brain_context.brain_version}`",
        f"- Brain package root: `{brain_context.brain_package_root}`",
        "- Daily logical LLM calls: `2`",
        "- Historical raw daily map calls: `0`",
        "- BLIND web calls: `0`",
        "",
        "## Dominant Sectors",
        "",
    ]
    for sector in prediction.dominant_sectors:
        lines.extend(
            [
                f"### {sector.name}",
                f"- Mechanism: {sector.formation_mechanism}",
                f"- Capsule IDs: {', '.join(sector.semantic_capsule_ids) or 'none'}",
                f"- Claim IDs: {', '.join(sector.mechanism_claim_ids) or 'none'}",
                f"- Failure conditions: {'; '.join(sector.failure_conditions) or 'none'}",
                "",
            ]
        )
    lines.extend(["## Ranked Candidates", ""])
    for candidate in sorted(prediction.candidates, key=lambda row: row.rank):
        lines.extend(
            [
                f"### {candidate.rank}. {candidate.company_name} ({candidate.ticker})",
                f"- Path: `{candidate.path_type}`",
                f"- Thesis: {candidate.thesis}",
                f"- Source rows: {', '.join(str(value) for value in candidate.source_row_ids) or 'none'}",
                f"- Capsule IDs: {', '.join(candidate.semantic_capsule_ids) or 'none'}",
                f"- Claim IDs: {', '.join(candidate.mechanism_claim_ids) or 'none'}",
                f"- Supporting records: {', '.join(candidate.prior_positive_record_ids) or 'none'}",
                f"- Contradicting records: {', '.join(candidate.prior_negative_record_ids) or 'none'}",
                f"- Counterarguments: {'; '.join(candidate.counterarguments) or 'none'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _capsule_bytes(capsules: Sequence[CurrentEventCapsule]) -> int:
    return len(canonical_json([row.model_dump(mode="json") for row in capsules]).encode("utf-8"))


def _unique(values: Sequence[str] | Any) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = " ".join(str(value).split()).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def _trace_daily_llm(
    settings: Settings,
    provider: LLMProvider,
    model_config: dict[str, Any],
) -> LLMProvider:
    if isinstance(provider, TracingLLMProvider):
        if provider.max_retries > 0:
            raise ValueError("analyze-daily requires a tracing provider with zero outer retries")
        return provider
    return TracingLLMProvider(
        provider,
        trace_dir=settings.path(settings.output_dirs.traces),
        model_config=model_config,
        default_metadata={"architecture_version": THIN_DAILY_ARCHITECTURE_VERSION},
        purpose_metadata={
            "current_day_interpretation": {"prompt_version": CURRENT_DAY_INTERPRETATION_PROMPT_VERSION},
            "final_market_decision": {"prompt_version": FINAL_MARKET_DECISION_PROMPT_VERSION},
        },
        max_retries=0,
    )


def _llm_model_config(settings: Settings, provider: LLMProvider) -> dict[str, Any]:
    return {
        "provider": str(getattr(provider, "provider_name", settings.llm_provider)),
        "model": str(getattr(provider, "model", settings.llm.model)),
        "reasoning_effort": getattr(provider, "reasoning_effort", settings.llm.reasoning_effort),
        "structured_repair_retries": settings.llm.max_retries,
        "architecture_version": THIN_DAILY_ARCHITECTURE_VERSION,
    }
