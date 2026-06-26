from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

import news_scalping_lab.inference.analyzer as analyzer_module
from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.final_synthesis import final_synthesis_input_summary
from news_scalping_lab.context.sweep import SweepResult
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    CandidateExpansionFinding,
    CandidateExpansionPath,
    CandidateExpansionReview,
    ClaimStatus,
    ConfidenceLabel,
    ContextManifest,
    MemoryClaim,
    NewsNoveltyFinding,
    NewsNoveltyLabel,
    NewsNoveltyReview,
    OpenWorldFirstAnalysis,
    OutcomeLabels,
    PathType,
    PriceSnapshot,
    RedTeamArtifact,
    RedTeamFinding,
    ResearchEpisode,
    SemanticRetrievalPlan,
    SemanticRetrievalQuery,
)
from news_scalping_lab.inference.analyzer import (
    DailyAnalyzer,
    ExhaustiveCoverageError,
)
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    write_json,
)
from news_scalping_lab.web.provider import WebSearchResult

T = TypeVar("T", bound=BaseModel)


def _write_minimal_stock_web_atlas(root: Path) -> None:
    atlas = root / "atlas"
    (atlas / "ohlcv_tradable_by_symbol_year").mkdir(parents=True)
    write_json(
        atlas / "manifest.json",
        {
            "source_name": "stock-web-integration-test",
            "calibration_shard_root": "atlas/ohlcv_tradable_by_symbol_year",
        },
    )
    write_json(
        atlas / "schema.json",
        {
            "tradable_shard_columns": {
                "d": "date",
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
            }
        },
    )


class OutcomeTrapPriceSource:
    source_name = "outcome-trap"

    def __init__(self) -> None:
        self.outcome_calls: list[tuple[str, date]] = []
        self.snapshot_calls: list[tuple[str, date]] = []

    def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
        return [
            PriceRecord(
                ticker=ticker,
                trade_date=through,
                close=100.0,
            )
        ]

    def get_snapshot(self, ticker: str, *, as_of: date) -> PriceRecord | None:
        self.snapshot_calls.append((ticker, as_of))
        return PriceRecord(ticker=ticker, trade_date=as_of, close=100.0)

    def get_outcome(self, ticker: str, *, trade_date: date) -> OutcomeLabels:
        self.outcome_calls.append((ticker, trade_date))
        raise AssertionError("blind analysis must not request D-day outcome labels")


class RecordingBlindLLM:
    def __init__(
        self,
        *,
        expected_final_prompt_substring: str | None = None,
        forbidden_final_prompt_substrings: list[str] | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.expected_final_prompt_substring = expected_final_prompt_substring
        self.forbidden_final_prompt_substrings = forbidden_final_prompt_substrings or []

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        raise AssertionError("daily analyzer should request structured output")

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        self.calls.append(
            {"prompt": prompt, "response_model": response_model, "purpose": purpose}
        )
        if response_model is OpenWorldFirstAnalysis:
            analysis = OpenWorldFirstAnalysis(
                run_id="RUN-provider-open-world",
                prompt_version="test",
                prompt_sha256="test",
                created_at=datetime(1999, 1, 1, 8, 25, 0, tzinfo=KST),
                cutoff_at=datetime(1999, 1, 1, 8, 59, 59, tzinfo=KST),
                event_ids=["EVT-provider"],
                event_clusters=["provider current-news cluster"],
                direct_company_events=["ProviderCandidate direct current-news event"],
                policy_industry_events=["provider policy or industry route"],
                mechanisms=["provider current news -> open-world candidate"],
                beneficiary_transmission_paths=["provider mechanism -> beneficiary discovery"],
                narrative_conversion_points=["provider narrative conversion point"],
                direct_candidates=["ProviderCandidate"],
                potential_sectors=["provider generated sector"],
                beneficiary_investigation_questions=["provider investigation question"],
                uncertainties=["provider first-pass uncertainty"],
            )
            return analysis  # type: ignore[return-value]
        if response_model is NewsNoveltyReview:
            review = NewsNoveltyReview(
                run_id="RUN-provider-novelty",
                prompt_version="test",
                prompt_sha256="test",
                created_at=datetime(1999, 1, 1, 8, 35, 0, tzinfo=KST),
                cutoff_at=datetime(1999, 1, 1, 8, 59, 59, tzinfo=KST),
                review_mode="NEWS_ONLY_STRICT",
                cluster_count=1,
                reviewed_cluster_count=1,
                findings=[
                    NewsNoveltyFinding(
                        cluster_id=_first_prompt_cluster_id(prompt),
                        cluster_index=1,
                        row_numbers=[1],
                        event_ids=["EVT-provider"],
                        novelty=NewsNoveltyLabel.UNCLEAR,
                        first_public_evidence_at=datetime(
                            1999, 1, 1, 8, 0, 0, tzinfo=KST
                        ),
                        evidence_source_ids=_first_prompt_cluster_source_ids(prompt),
                        evidence_summary="Provider novelty review keeps uncertainty.",
                        uncertainties=["provider novelty uncertainty"],
                    )
                ],
            )
            return review  # type: ignore[return-value]
        if response_model is SemanticRetrievalPlan:
            plan = SemanticRetrievalPlan(
                run_id="RUN-provider-semantic-retrieval",
                prompt_version="test",
                prompt_sha256="test",
                created_at=datetime(1999, 1, 1, 8, 40, 0, tzinfo=KST),
                cutoff_at=datetime(1999, 1, 1, 8, 59, 59, tzinfo=KST),
                required_categories=[
                    "positive_analogs",
                    "negative_analogs",
                    "near_misses",
                    "counterexamples",
                    "leader_selection_cases",
                    "theme_formation_failures",
                ],
                queries=[
                    SemanticRetrievalQuery(
                        category=category,
                        query=f"{category} ProviderCo structural memory",
                        rationale="provider semantic query",
                    )
                    for category in [
                        "positive_analogs",
                        "negative_analogs",
                        "near_misses",
                        "counterexamples",
                        "leader_selection_cases",
                        "theme_formation_failures",
                    ]
                ],
            )
            return plan  # type: ignore[return-value]
        if response_model is CandidateExpansionReview:
            review = CandidateExpansionReview(
                run_id="RUN-provider-candidate-expansion",
                prompt_version="test",
                prompt_sha256="test",
                created_at=datetime(1999, 1, 1, 8, 42, 0, tzinfo=KST),
                cutoff_at=datetime(1999, 1, 1, 8, 59, 59, tzinfo=KST),
                required_paths=[
                    CandidateExpansionPath.SINGLE_EVENT,
                    CandidateExpansionPath.THEME_FORMATION,
                    CandidateExpansionPath.BENEFICIARY_DISCOVERY,
                    CandidateExpansionPath.CONTINUATION,
                ],
                findings=[
                    CandidateExpansionFinding(
                        path=path,
                        hypothesis=f"{path.value} provider route",
                        candidate_names=[f"{path.value}_DISCOVERY_REQUIRED"],
                        investigation_questions=["provider route question"],
                        d_minus_one_market_data_only=(
                            path == CandidateExpansionPath.CONTINUATION
                        ),
                    )
                    for path in [
                        CandidateExpansionPath.SINGLE_EVENT,
                        CandidateExpansionPath.THEME_FORMATION,
                        CandidateExpansionPath.BENEFICIARY_DISCOVERY,
                        CandidateExpansionPath.CONTINUATION,
                    ]
                ],
            )
            return review  # type: ignore[return-value]
        if response_model is RedTeamArtifact:
            artifact = RedTeamArtifact(
                run_id="RUN-provider-red-team",
                source_prediction_id="PRED-provider-raw",
                prompt_version="test",
                prompt_sha256="test",
                created_at=datetime(1999, 1, 1, 8, 30, 0, tzinfo=KST),
                candidate_count=1,
                candidate_findings=[
                    RedTeamFinding(
                        candidate_rank=1,
                        ticker="UNKNOWN",
                        company_name="ProviderCandidate",
                        path_type=PathType.SINGLE_EVENT,
                        attack_summary="Provider red-team finding.",
                        objections=["provider red-team objection"],
                        disconfirming_conditions=["provider disconfirming condition"],
                        verification_questions=["provider verification question"],
                    )
                ],
            )
            return artifact  # type: ignore[return-value]
        assert response_model is BlindPrediction
        if purpose == "final_synthesis":
            assert "red_team_output" in prompt
            assert "d_minus_one_market_data" in prompt
            assert "candidate_web_checks" in prompt
            assert "candidate_verification" in prompt
            assert "NEWS_ONLY_STRICT_NO_PRICE_ACCESS" in prompt
            assert "retrieved_raw_episodes" in prompt
            assert "all_shard_brains" in prompt
            assert "all_shard_contributions" in prompt
            if self.expected_final_prompt_substring is not None:
                assert self.expected_final_prompt_substring in prompt
            for forbidden in self.forbidden_final_prompt_substrings:
                assert forbidden not in prompt
            prediction = BlindPrediction(
                prediction_id="PRED-provider-final",
                trade_date=date(1999, 1, 1),
                cutoff_at=datetime(1999, 1, 1, 8, 59, 59, tzinfo=KST),
                created_at=datetime(1999, 1, 1, 8, 45, 0, tzinfo=KST),
                blind_analysis=BlindAnalysis(
                    summary="Provider final synthesis.",
                    open_world_mechanisms=["provider final synthesis mechanism"],
                ),
                candidates=[
                    Candidate(
                        rank=1,
                        ticker="UNKNOWN",
                        company_name="ProviderCandidate",
                        path_type=PathType.SINGLE_EVENT,
                        thesis="Final synthesized candidate.",
                        why_now="The final synthesizer saw red-team output.",
                        causal_chain=["payload", "red-team", "final synthesis"],
                    )
                ],
            )
            return prediction  # type: ignore[return-value]
        prediction = BlindPrediction(
            prediction_id="PRED-provider-raw",
            trade_date=date(1999, 1, 1),
            cutoff_at=datetime(1999, 1, 1, 8, 59, 59, tzinfo=KST),
            created_at=datetime(1999, 1, 1, 8, 0, 0, tzinfo=KST),
            blind_analysis=BlindAnalysis(
                summary="Provider-generated blind analysis.",
                open_world_mechanisms=["provider current news -> open-world candidate"],
            ),
            candidates=[
                Candidate(
                    rank=99,
                    ticker="UNKNOWN",
                    company_name="ProviderCandidate",
                    path_type=PathType.SINGLE_EVENT,
                    thesis="Structured provider candidate.",
                    why_now="The provider saw the current news payload.",
                    causal_chain=["payload", "provider reasoning"],
                )
            ],
        )
        return prediction  # type: ignore[return-value]

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        return [[0.0] for _ in texts]


def _first_prompt_cluster_id(prompt: str) -> str:
    payload = _news_novelty_prompt_payload(prompt)
    clusters = payload.get("event_clusters", [])
    if isinstance(clusters, list) and clusters and isinstance(clusters[0], dict):
        cluster_id = clusters[0].get("cluster_id")
        if isinstance(cluster_id, str):
            return cluster_id
    return "EVCL-provider"


def _first_prompt_cluster_source_ids(prompt: str) -> list[str]:
    payload = _news_novelty_prompt_payload(prompt)
    clusters = payload.get("event_clusters", [])
    if isinstance(clusters, list) and clusters and isinstance(clusters[0], dict):
        source_ids = clusters[0].get("source_ids", [])
        return [source_id for source_id in source_ids if isinstance(source_id, str)]
    return []


def _news_novelty_prompt_payload(prompt: str) -> dict[str, object]:
    marker = "---NEWS_NOVELTY_REVIEW_PAYLOAD---"
    if marker not in prompt:
        return {}
    payload = json.loads(prompt.split(marker, maxsplit=1)[-1])
    return payload if isinstance(payload, dict) else {}


def _llm_call(llm: RecordingBlindLLM, purpose: str) -> dict[str, object]:
    return next(call for call in llm.calls if call["purpose"] == purpose)


class BrokenMemorySweeper:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def sweep(self, *args: object, **kwargs: object) -> SweepResult:
        return SweepResult(
            accepted_episode_count=1,
            swept_episode_ids=[],
            accepted_record_count=0,
            available_record_count=0,
            training_eligible_available_record_count=0,
            swept_record_ids=[],
            artifact_paths=[],
            record_artifact_paths=[],
            shard_count=0,
            record_shard_count=0,
            cache_hits=0,
            record_cache_hits=0,
            token_counts={"memory_sweep": 0, "record_memory_sweep": 0},
            errors=["memory sweep missing accepted episodes: EP-missing"],
        )


class MixedTemporalWebProvider:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, datetime]] = []
        self.open_calls: list[str] = []

    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        self.search_calls.append((query, cutoff_at))
        return [
            WebSearchResult(
                source_id=f"WEB-SAFE-{len(self.search_calls)}",
                title=f"safe {query}",
                url="mock://safe-pipeline",
                snippet="Published before cutoff and may enter blind evidence.",
                published_at=cutoff_at - timedelta(minutes=1),
            ),
            WebSearchResult(
                source_id=f"WEB-FUTURE-{len(self.search_calls)}",
                title=query,
                url="mock://future-pipeline",
                snippet="Published after cutoff and must not enter blind evidence.",
                published_at=cutoff_at + timedelta(seconds=1),
            )
        ]

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        self.open_calls.append(url)
        return f"cutoff-safe opened verification text for {url} at {cutoff_at.isoformat()}"

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        return result.published_at is None or result.published_at <= cutoff_at


def _episode_with_counterexample() -> ResearchEpisode:
    trade_day = date(2030, 1, 9)
    available_from = datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST)
    return ResearchEpisode(
        episode_id="EP-counterexample",
        trade_date=trade_day,
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
        research_version="test-v1",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(
            summary="Counterexample research summary.",
            open_world_mechanisms=["same-looking catalyst -> weak directness failure"],
        ),
        counterexamples=[
            MemoryClaim(
                claim_id="CL-counterexample",
                statement="Same-looking catalyst failed when direct listed-entity ownership was absent.",
                mechanism="directness failure counterexample",
                scope="counterexample fixture",
                conditions=["verify economic ownership before leader selection"],
                failure_modes=["directness error"],
                support_episode_ids=["EP-counterexample"],
                status=ClaimStatus.DISPUTED,
                confidence_label=ConfidenceLabel.MEDIUM,
                available_from=available_from,
            )
        ],
        misses=["loose narrative relation"],
        available_from=available_from,
    )


def _retrieval_episode(
    episode_id: str,
    *,
    summary: str,
    available_day: date,
    available_time: time = time(0, 0, 0),
) -> ResearchEpisode:
    trade_day = date(2030, 1, 9)
    return ResearchEpisode(
        episode_id=episode_id,
        trade_date=trade_day,
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
        research_version="test-v1",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(
            summary=summary,
            open_world_mechanisms=["ProviderCo catalyst -> retrieved raw episode context"],
        ),
        available_from=datetime.combine(available_day, available_time, tzinfo=KST),
    )


@pytest.mark.asyncio
async def test_analyze_retrieval_miss_still_outputs_candidates(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","가상회사, 신규 사업 검토","상장 여부와 직접성을 검증해야 한다."\n'
        '1,2,"2030-01-10","09:30:00","장중 결과성 기사","cutoff 이후 행은 BLIND 근거에서 제외한다."\n',
        encoding="utf-8",
    )
    csv_path.write_text(
        "page,row,date,time,collected_at,title,body\n"
        '1,1,"2030-01-09","14:59:00","2030-01-09T15:00:00+09:00","Old catalyst","before window row must be excluded."\n'
        '1,2,"2030-01-10","08:00:00","2030-01-10T08:00:30+09:00","Current catalyst","included pre-open row."\n'
        '1,3,"2030-01-10","09:30:00","2030-01-10T09:30:30+09:00","After cutoff","cutoff-after row must be excluded."\n',
        encoding="utf-8",
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    analyzer = DailyAnalyzer(settings, retrieval=LocalRetrievalStore(tmp_path, force_empty=True))
    analysis = await analyzer.analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    assert (
        analysis.context_manifest.accepted_episode_count
        == analysis.context_manifest.swept_episode_count
    )
    assert analysis.blind_prediction.candidates
    assert analysis.blind_prediction.candidates[0].company_name != "Create"
    path_types = {candidate.path_type for candidate in analysis.blind_prediction.candidates}
    assert PathType.THEME_BENEFICIARY in path_types
    assert PathType.CONTINUATION in path_types
    assert (tmp_path / analysis.report_path).exists()
    assert (tmp_path / analysis.prediction_path).exists()
    assert analysis.blind_prediction.context_manifest_id == analysis.context_manifest.run_id
    saved_prediction = read_json(tmp_path / analysis.prediction_path)
    assert saved_prediction["context_manifest_id"] == analysis.context_manifest.run_id
    assert saved_prediction["blind_analysis"]["provenance"][0]["source_type"].endswith(
        "_blind_analysis"
    )
    assert saved_prediction["dominant_sectors"]
    assert all(sector["provenance"] for sector in saved_prediction["dominant_sectors"])
    assert {
        sector["provenance"][-1]["source_type"] for sector in saved_prediction["dominant_sectors"]
    } == {"final_synthesis_dominant_sector"}
    assert all(candidate["provenance"] for candidate in saved_prediction["candidates"])
    assert {
        candidate["provenance"][-1]["source_type"] for candidate in saved_prediction["candidates"]
    } == {"final_synthesis_candidate"}
    assert len(analysis.context_manifest.red_team_artifacts) == 1
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json")
    assert saved_manifest["trade_date"] == "2030-01-10"
    assert saved_manifest["cutoff_at"] == "2030-01-10T08:59:59+09:00"
    assert saved_manifest["as_of"] == "2030-01-10T08:59:59+09:00"
    assert saved_manifest["price_snapshot"]["as_of"] == "2030-01-10T08:59:59+09:00"
    assert saved_manifest["news_window_start_at"] == "2030-01-09T15:30:00+09:00"
    assert saved_manifest["news_window_end_at"] == "2030-01-10T08:59:59+09:00"
    assert saved_manifest["news_file"] == "news.csv"
    assert saved_manifest["news_sha256"] == file_sha256(csv_path)
    assert saved_manifest["news_row_count"] == 3
    assert saved_manifest["included_news_row_count"] == 1
    assert saved_manifest["excluded_news_row_count"] == 2
    assert saved_manifest["model_config"] == {
        "analysis_mode": "exhaustive",
        "configured_provider": "mock",
        "max_output_tokens": 4096,
        "max_concurrency": 4,
        "max_retries": 0,
        "model": "deterministic-mock",
        "provider_class": "DeterministicMockLLMProvider",
        "reasoning_effort": "low",
        "shard_episode_count": 20,
    }
    assert saved_manifest["red_team_artifacts"] == analysis.context_manifest.red_team_artifacts
    assert saved_manifest["prompt_hashes"]["red_team_candidate_review"]
    assert saved_manifest["prompt_hashes"]["final_synthesis"]
    assert saved_manifest["final_synthesis_context_artifact"]
    final_context_path = tmp_path / saved_manifest["final_synthesis_context_artifact"]
    final_context = read_json(final_context_path)
    assert (
        sha256_text(final_context_path.read_text(encoding="utf-8"))
        == saved_manifest["final_synthesis_context_sha256"]
    )
    assert final_context["schema_version"] == "nslab.final_synthesis_context.v1"
    assert final_context["run_id"] == analysis.run_id
    assert final_context["prompt_version"] == "synthesis.final.v1"
    assert final_context["payload_sha256"] == sha256_text(
        canonical_json(final_context["payload"])
    )
    assert final_context["required_inputs"] == final_context["payload"]["required_inputs"]
    assert "current_news" in final_context["required_inputs"]
    assert "red_team_output" in final_context["required_inputs"]
    assert "d_minus_one_market_data" in final_context["required_inputs"]
    assert final_context["input_summary"]["current_news_count"] == 1
    assert final_context["input_summary"]["candidate_count"] >= 1
    assert final_context["input_summary"]["red_team_finding_count"] >= 1
    assert final_context["input_summary"] == final_synthesis_input_summary(
        final_context["payload"]
    )
    assert (
        saved_manifest["final_synthesis_context_summary"]
        == final_context["input_summary"]
    )
    run_prediction_path = tmp_path / saved_manifest["prediction_artifact"]
    run_report_path = tmp_path / saved_manifest["report_artifact"]
    assert run_prediction_path.exists()
    assert run_report_path.exists()
    assert file_sha256(run_prediction_path) == saved_manifest["prediction_sha256"]
    assert (
        sha256_text(run_report_path.read_text(encoding="utf-8"))
        == saved_manifest["report_sha256"]
    )
    assert read_json(run_prediction_path)["context_manifest_id"] == analysis.run_id
    assert saved_manifest["row_disposition_artifact"]
    assert saved_manifest["row_disposition_coverage_ratio"] == 1.0
    assert saved_manifest["row_disposition_summary"] == {
        "coverage_ratio": 1.0,
        "excluded_before_window": 1,
        "excluded_after_cutoff": 1,
        "included_in_news_window": 1,
        "included_before_cutoff": 1,
        "missing_collected_at": 0,
        "total_rows": 3,
    }
    row_disposition_path = tmp_path / saved_manifest["row_disposition_artifact"]
    row_disposition_text = row_disposition_path.read_text(encoding="utf-8")
    row_dispositions = [
        json.loads(line) for line in row_disposition_text.splitlines() if line.strip()
    ]
    assert sha256_text(row_disposition_text) == saved_manifest["row_disposition_sha256"]
    assert [row["row_number"] for row in row_dispositions] == [1, 2, 3]
    assert [row["disposition"] for row in row_dispositions] == [
        "EXCLUDED_BEFORE_WINDOW",
        "INCLUDED_IN_NEWS_WINDOW",
        "EXCLUDED_AFTER_CUTOFF",
    ]
    assert [row["within_news_window"] for row in row_dispositions] == [False, True, False]
    assert [row["collected_at_present"] for row in row_dispositions] == [True, True, True]
    assert row_dispositions[1]["news_window_start_at"] == "2030-01-09T15:30:00+09:00"
    assert row_dispositions[1]["cutoff_at"] == "2030-01-10T08:59:59+09:00"
    assert "title" not in row_dispositions[0]
    assert "body" not in row_dispositions[0]
    assert row_dispositions[0]["title_sha256"]
    assert row_dispositions[0]["body_sha256"]
    assert saved_manifest["event_cluster_artifact"]
    assert saved_manifest["event_cluster_count"] == 1
    assert saved_manifest["event_cluster_summary"] == {
        "source_row_count": 1,
        "cluster_count": 1,
        "exact_duplicate_count": 0,
        "exact_duplicate_cluster_count": 0,
        "semantic_duplicate_cluster_count": 0,
        "cluster_method": "exact_normalized_title_body_v1",
        "novelty_review_required": True,
    }
    event_cluster_path = tmp_path / saved_manifest["event_cluster_artifact"]
    event_cluster_text = event_cluster_path.read_text(encoding="utf-8")
    event_clusters = [
        json.loads(line) for line in event_cluster_text.splitlines() if line.strip()
    ]
    assert sha256_text(event_cluster_text) == saved_manifest["event_cluster_sha256"]
    assert len(event_clusters) == 1
    assert event_clusters[0]["schema_version"] == "nslab.news_event_cluster.v1"
    assert event_clusters[0]["row_numbers"] == [2]
    assert event_clusters[0]["row_count"] == 1
    assert event_clusters[0]["exact_duplicate_count"] == 0
    assert event_clusters[0]["first_published_at"] == "2030-01-10T08:00:00+09:00"
    assert (
        event_clusters[0]["last_published_at_before_cutoff"]
        == "2030-01-10T08:00:00+09:00"
    )
    assert event_clusters[0]["cutoff_at"] == "2030-01-10T08:59:59+09:00"
    assert event_clusters[0]["time_verified"] is True
    assert event_clusters[0]["novelty"] == "unclear"
    assert event_clusters[0]["requires_llm_novelty_review"] is True
    assert "title" not in event_clusters[0]
    assert "body" not in event_clusters[0]
    assert event_clusters[0]["representative_title_sha256"]
    assert event_clusters[0]["representative_body_sha256"]
    assert saved_manifest["news_novelty_review_artifact"]
    assert saved_manifest["news_novelty_review_count"] == 1
    assert saved_manifest["news_novelty_review_summary"] == {
        "cluster_count": 1,
        "reviewed_cluster_count": 1,
        "review_mode": "NEWS_ONLY_STRICT",
        "novelty_counts": {
            "new": 0,
            "follow_up": 0,
            "recycled": 0,
            "unclear": 1,
        },
        "time_verified_count": 1,
        "excluded_after_cutoff_source_count": 0,
    }
    novelty_review_path = tmp_path / saved_manifest["news_novelty_review_artifact"]
    novelty_review_text = novelty_review_path.read_text(encoding="utf-8")
    novelty_review = json.loads(novelty_review_text)
    assert sha256_text(novelty_review_text) == saved_manifest["news_novelty_review_sha256"]
    assert novelty_review["schema_version"] == "nslab.news_novelty_review.v1"
    assert novelty_review["run_id"] == analysis.run_id
    assert novelty_review["prompt_sha256"]
    assert novelty_review["cluster_count"] == 1
    assert novelty_review["reviewed_cluster_count"] == 1
    assert novelty_review["findings"][0]["cluster_id"] == event_clusters[0]["cluster_id"]
    assert novelty_review["findings"][0]["row_numbers"] == [2]
    assert novelty_review["findings"][0]["event_ids"] == event_clusters[0]["event_ids"]
    assert novelty_review["findings"][0]["novelty"] == "unclear"
    assert novelty_review["findings"][0]["first_public_evidence_at"] == (
        "2030-01-10T08:00:00+09:00"
    )
    assert novelty_review["findings"][0]["time_verified"] is True
    assert saved_manifest["source_ledger_artifact"]
    assert saved_manifest["source_ledger_entry_count"] == 1
    assert saved_manifest["source_ledger_summary"] == {
        "blind_sources": 1,
        "outcome_sources": 0,
        "postmortem_sources": 0,
        "total_sources": 1,
    }
    source_ledger_path = tmp_path / saved_manifest["source_ledger_artifact"]
    source_ledger_text = source_ledger_path.read_text(encoding="utf-8")
    source_ledger_rows = [
        json.loads(line) for line in source_ledger_text.splitlines() if line.strip()
    ]
    assert sha256_text(source_ledger_text) == saved_manifest["source_ledger_sha256"]
    assert len(source_ledger_rows) == 1
    assert source_ledger_rows[0]["source_type"] == "news_csv_row"
    assert source_ledger_rows[0]["usage_phase"] == "BLIND"
    assert source_ledger_rows[0]["available_before_cutoff"] is True
    assert source_ledger_rows[0]["collected_at_present"] is True
    assert source_ledger_rows[0]["input_row_ids"] == [2]
    assert "body" not in source_ledger_rows[0]
    assert saved_manifest["blind_artifact_sha256"] == saved_prediction["blind_artifact_sha256"]
    receipt_path = tmp_path / saved_manifest["blind_seal_receipt_artifact"]
    receipt_text = receipt_path.read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)
    assert sha256_text(receipt_text) == saved_manifest["blind_seal_receipt_sha256"]
    assert receipt["phase"] == "BLIND_SEALED"
    assert receipt["blind_artifact_sha256"] == saved_prediction["blind_artifact_sha256"]
    assert receipt["blind_prediction_path"] == saved_manifest["prediction_artifact"]
    phase_state_path = tmp_path / saved_manifest["phase_state_artifact"]
    phase_state_text = phase_state_path.read_text(encoding="utf-8")
    phase_state = json.loads(phase_state_text)
    assert sha256_text(phase_state_text) == saved_manifest["phase_state_sha256"]
    assert phase_state["phase"] == "BLIND_SEALED"
    assert (
        phase_state["blind_seal_receipt_sha256"]
        == saved_manifest["blind_seal_receipt_sha256"]
    )
    assert saved_manifest["token_counts"]["blind_analysis_prompt"] > 0
    assert saved_manifest["token_counts"]["news_novelty_review_prompt"] > 0
    assert saved_manifest["semantic_retrieval_plan_artifact"]
    assert saved_manifest["semantic_retrieval_artifact"]
    assert saved_manifest["semantic_retrieval_query_count"] == 6
    assert saved_manifest["semantic_retrieval_summary"] == {
        "required_categories": [
            "positive_analogs",
            "negative_analogs",
            "near_misses",
            "counterexamples",
            "leader_selection_cases",
            "theme_formation_failures",
        ],
        "category_query_counts": {
            "positive_analogs": 1,
            "negative_analogs": 1,
            "near_misses": 1,
            "counterexamples": 1,
            "leader_selection_cases": 1,
            "theme_formation_failures": 1,
        },
        "query_count": 6,
        "included_episode_count": 0,
        "excluded_episode_count": 0,
        "included_record_count": 0,
        "excluded_record_count": 0,
        "record_retrieval_zero_is_valid": True,
        "retrieval_zero_is_valid": True,
    }
    semantic_plan_path = tmp_path / saved_manifest["semantic_retrieval_plan_artifact"]
    semantic_plan_text = semantic_plan_path.read_text(encoding="utf-8")
    semantic_plan = json.loads(semantic_plan_text)
    assert sha256_text(semantic_plan_text) == saved_manifest["semantic_retrieval_plan_sha256"]
    assert semantic_plan["schema_version"] == "nslab.semantic_retrieval_plan.v1"
    assert {query["category"] for query in semantic_plan["queries"]} == set(
        saved_manifest["semantic_retrieval_summary"]["required_categories"]
    )
    semantic_results_path = tmp_path / saved_manifest["semantic_retrieval_artifact"]
    semantic_results_text = semantic_results_path.read_text(encoding="utf-8")
    semantic_rows = [
        json.loads(line) for line in semantic_results_text.splitlines() if line.strip()
    ]
    assert sha256_text(semantic_results_text) == saved_manifest["semantic_retrieval_sha256"]
    assert len(semantic_rows) == 6
    assert all(row["schema_version"] == "nslab.semantic_retrieval_result.v1" for row in semantic_rows)
    assert all(row["included_episode_ids"] == [] for row in semantic_rows)
    assert all(row["included_record_ids"] == [] for row in semantic_rows)
    assert all("record_retrieval_filters" in row for row in semantic_rows)
    assert saved_manifest["candidate_expansion_artifact"]
    assert saved_manifest["candidate_expansion_count"] == 4
    assert saved_manifest["candidate_expansion_summary"] == {
        "required_paths": [
            "SINGLE_EVENT",
            "THEME_FORMATION",
            "BENEFICIARY_DISCOVERY",
            "CONTINUATION",
        ],
        "path_counts": {
            "SINGLE_EVENT": 1,
            "THEME_FORMATION": 1,
            "BENEFICIARY_DISCOVERY": 1,
            "CONTINUATION": 1,
        },
        "finding_count": 4,
        "candidate_name_count": 4,
        "requires_web_company_discovery_count": 3,
        "continuation_d_minus_one_only_verified": True,
    }
    candidate_expansion_path = tmp_path / saved_manifest["candidate_expansion_artifact"]
    candidate_expansion_text = candidate_expansion_path.read_text(encoding="utf-8")
    candidate_expansion = json.loads(candidate_expansion_text)
    assert sha256_text(candidate_expansion_text) == saved_manifest["candidate_expansion_sha256"]
    assert candidate_expansion["schema_version"] == "nslab.candidate_expansion.v1"
    assert [finding["path"] for finding in candidate_expansion["findings"]] == [
        "SINGLE_EVENT",
        "THEME_FORMATION",
        "BENEFICIARY_DISCOVERY",
        "CONTINUATION",
    ]
    assert candidate_expansion["findings"][-1]["d_minus_one_market_data_only"] is True
    assert saved_manifest["token_counts"]["final_synthesis_prompt"] > 0
    traces = [read_json(path) for path in (tmp_path / "runs" / "traces").glob("TRACE-*.json")]
    prompt_hash_by_purpose = {
        trace["purpose"]: trace["input"]["prompt_sha256"]
        for trace in traces
        if "prompt_sha256" in trace["input"]
    }
    trace_model_config_by_purpose = {
        trace["purpose"]: trace["model_config"]
        for trace in traces
        if trace["purpose"]
        in {
            "open_world_first_analysis",
            "news_novelty_review",
            "semantic_retrieval_plan",
            "candidate_expansion",
            "daily_blind_analysis",
            "red_team_candidate_review",
            "final_synthesis",
        }
    }
    assert saved_manifest["prompt_hashes"]["open_world_first_analysis"] == prompt_hash_by_purpose[
        "open_world_first_analysis"
    ]
    assert saved_manifest["prompt_hashes"]["news_novelty_review"] == prompt_hash_by_purpose[
        "news_novelty_review"
    ]
    assert saved_manifest["prompt_hashes"]["semantic_retrieval_plan"] == prompt_hash_by_purpose[
        "semantic_retrieval_plan"
    ]
    assert saved_manifest["prompt_hashes"]["candidate_expansion"] == prompt_hash_by_purpose[
        "candidate_expansion"
    ]
    assert saved_manifest["prompt_hashes"]["blind_analysis"] == prompt_hash_by_purpose[
        "daily_blind_analysis"
    ]
    assert saved_manifest["prompt_hashes"]["red_team_candidate_review"] == prompt_hash_by_purpose[
        "red_team_candidate_review"
    ]
    assert saved_manifest["prompt_hashes"]["final_synthesis"] == prompt_hash_by_purpose[
        "final_synthesis"
    ]
    assert all(
        config == {key: value for key, value in saved_manifest["model_config"].items() if key != "analysis_mode"}
        for config in trace_model_config_by_purpose.values()
    )
    red_team_path = tmp_path / analysis.context_manifest.red_team_artifacts[0]
    red_team = read_json(red_team_path)
    assert red_team["schema_version"] == "nslab.red_team_artifact.v1"
    assert red_team["run_id"] == analysis.context_manifest.run_id
    assert red_team["candidate_count"] == len(analysis.blind_prediction.candidates)
    assert len(red_team["candidate_findings"]) == len(analysis.blind_prediction.candidates)
    assert red_team["required_attack_checks"] == [
        "good_company_news_not_limit_up_language",
        "novelty_not_recycled",
        "economic_amount_attributable_to_listed_company",
        "weak_stage_mou_planned_prototype",
        "already_pre_absorbed",
        "market_cap_float_liquidity_drag",
        "dilution_or_financing_risk",
        "forced_indirect_relation",
        "market_memory_relation_currently_broken",
        "purer_same_theme_leader_exists",
    ]
    assert saved_manifest["red_team_summary"] == {
        "candidate_count": len(analysis.blind_prediction.candidates),
        "required_attack_checks": red_team["required_attack_checks"],
        "required_attack_check_count": len(red_team["required_attack_checks"]),
        "finding_count": len(red_team["candidate_findings"]),
        "all_findings_passed_to_synthesis": True,
    }
    assert all(finding["passed_to_synthesis"] for finding in red_team["candidate_findings"])
    assert all(
        [check["name"] for check in finding["attack_checks"]]
        == red_team["required_attack_checks"]
        for finding in red_team["candidate_findings"]
    )
    assert all(
        check["passed_to_synthesis"]
        for finding in red_team["candidate_findings"]
        for check in finding["attack_checks"]
    )
    assert audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))["passed"]
    assert audit_provenance(tmp_path)["passed"]


@pytest.mark.asyncio
async def test_blind_web_search_keeps_only_cutoff_safe_sources(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","FutureGuardCo, catalyst",'
        '"Only cutoff-safe web evidence may be used."\n',
        encoding="utf-8",
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    web_provider = MixedTemporalWebProvider()

    analysis = await DailyAnalyzer(settings, web_provider=web_provider).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=True,
    )

    expected_candidate_web_subjects = (
        len(analysis.blind_prediction.candidates)
        + analysis.context_manifest.candidate_expansion_count
    )
    expected_web_search_calls = (
        len(analysis.context_manifest.web_queries) + expected_candidate_web_subjects
    )
    assert len(web_provider.search_calls) == expected_web_search_calls
    assert analysis.context_manifest.blind_context_mode == "CUTOFF_SAFE_WEB_BLIND"
    assert analysis.context_manifest.blind_web_search_call_count == expected_web_search_calls
    assert analysis.context_manifest.web_sources
    assert all(
        source_id.startswith("WEB-SAFE-")
        for source_id in analysis.context_manifest.web_sources
    )
    assert analysis.context_manifest.excluded_web_source_ids
    assert all(
        source_id.startswith("WEB-FUTURE-")
        for source_id in analysis.context_manifest.excluded_web_source_ids
    )
    assert web_provider.open_calls
    assert all(call == "mock://safe-pipeline" for call in web_provider.open_calls)
    manifest_path = tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json"
    saved_manifest = read_json(manifest_path)
    assert saved_manifest["blind_context_mode"] == "CUTOFF_SAFE_WEB_BLIND"
    assert saved_manifest["blind_web_search_call_count"] == expected_web_search_calls
    assert saved_manifest["candidate_web_check_summary"]["subject_count"] == (
        expected_candidate_web_subjects
    )
    assert saved_manifest["candidate_web_check_summary"][
        "final_candidate_subject_count"
    ] == len(analysis.blind_prediction.candidates)
    assert saved_manifest["candidate_web_check_summary"][
        "candidate_expansion_subject_count"
    ] == analysis.context_manifest.candidate_expansion_count
    assert saved_manifest["candidate_web_check_summary"]["verification_focus"] == [
        "listed_security_and_exact_ticker",
        "business_location_customer_supply_chain_relation",
        "prior_market_narratives_and_theme_memory",
        "current_news_relation_vs_name_similarity",
        "recent_disclosures_and_news",
        "market_cap_and_shares_outstanding",
        "D_minus_one_trading_value_turnover_limit_up",
        "multi_day_pre_absorption",
        "liquidity_and_competing_leaders",
    ]
    assert saved_manifest["web_sources"] == analysis.context_manifest.web_sources
    assert saved_manifest["excluded_web_source_ids"] == (
        analysis.context_manifest.excluded_web_source_ids
    )
    web_source_path = tmp_path / saved_manifest["web_source_artifact"]
    web_source_rows = [
        json.loads(line) for line in web_source_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["source_id"] for row in web_source_rows} == set(saved_manifest["web_sources"])
    assert all(row["source_url"] == row["url"] for row in web_source_rows)
    assert all(row["timestamp_precision"] is None for row in web_source_rows)
    assert all(row["available_before_cutoff"] is True for row in web_source_rows)
    assert all(
        "cutoff-safe opened verification text" in row["opened_text_excerpt"]
        for row in web_source_rows
    )
    candidate_check_path = tmp_path / saved_manifest["candidate_web_check_artifact"]
    candidate_check_rows = [
        json.loads(line)
        for line in candidate_check_path.read_text(encoding="utf-8").splitlines()
    ]
    assert saved_manifest["candidate_web_check_count"] == len(candidate_check_rows)
    assert saved_manifest["candidate_web_check_count"] == len(
        analysis.blind_prediction.candidates
    ) + analysis.context_manifest.candidate_expansion_count
    assert saved_manifest["candidate_web_check_count"] == (
        saved_manifest["candidate_web_check_summary"]["source_count"]
    )
    verification_path = tmp_path / saved_manifest["candidate_verification_artifact"]
    verification_text = verification_path.read_text(encoding="utf-8")
    verification = json.loads(verification_text)
    assert sha256_text(verification_text) == saved_manifest["candidate_verification_sha256"]
    assert verification["schema_version"] == "nslab.candidate_verification.v1"
    assert saved_manifest["candidate_verification_count"] == len(verification["findings"])
    assert saved_manifest["candidate_verification_count"] == expected_candidate_web_subjects
    assert saved_manifest["candidate_verification_summary"]["finding_count"] == (
        expected_candidate_web_subjects
    )
    assert saved_manifest["candidate_verification_summary"][
        "candidate_expansion_subject_count"
    ] == analysis.context_manifest.candidate_expansion_count
    assert saved_manifest["candidate_verification_summary"][
        "subjects_without_cutoff_safe_sources"
    ] == 0
    assert saved_manifest["candidate_verification_summary"]["d_minus_one_snapshot_count"] == 0
    assert saved_manifest["candidate_verification_summary"][
        "d_minus_one_snapshot_unavailable_count"
    ] == expected_candidate_web_subjects
    expected_source_collected_dimensions = (
        len(analysis.blind_prediction.candidates) * 9
        + analysis.context_manifest.candidate_expansion_count * 8
    )
    assert saved_manifest["candidate_verification_summary"]["status_counts"] == {
        "source_collected": expected_source_collected_dimensions,
        "needs_company_discovery": analysis.context_manifest.candidate_expansion_count,
    }
    assert verification["required_dimensions"] == (
        saved_manifest["candidate_web_check_summary"]["verification_focus"]
    )
    assert all(
        len(finding["verification_dimensions"]) == len(verification["required_dimensions"])
        for finding in verification["findings"]
    )
    assert all(
        finding["blind_safe_market_snapshot"]["status"] == "unavailable"
        for finding in verification["findings"]
    )
    assert {
        finding["blind_safe_market_snapshot"]["reason"]
        for finding in verification["findings"]
    } >= {
        "ticker_not_verified",
        "ticker_not_resolved_for_candidate_discovery",
    }
    expansion_verification_findings = [
        finding
        for finding in verification["findings"]
        if finding["subject_type"] == "candidate_expansion"
    ]
    assert len(expansion_verification_findings) == (
        analysis.context_manifest.candidate_expansion_count
    )
    assert all(
        any(
            dimension["name"] == "listed_security_and_exact_ticker"
            and dimension["status"] == "needs_company_discovery"
            for dimension in finding["verification_dimensions"]
        )
        for finding in expansion_verification_findings
    )
    assert {row["source_id"] for row in candidate_check_rows} == set(
        saved_manifest["candidate_web_source_ids"]
    )
    assert all(row["source_url"] == row["url"] for row in candidate_check_rows)
    final_candidate_check_rows = [
        row for row in candidate_check_rows if row["candidate_subject_type"] == "final_candidate"
    ]
    expansion_candidate_check_rows = [
        row
        for row in candidate_check_rows
        if row["candidate_subject_type"] == "candidate_expansion"
    ]
    assert {row["candidate_rank"] for row in final_candidate_check_rows} == {
        candidate.rank for candidate in analysis.blind_prediction.candidates
    }
    assert len(expansion_candidate_check_rows) == (
        analysis.context_manifest.candidate_expansion_count
    )
    assert {row["candidate_expansion_path"] for row in expansion_candidate_check_rows} == {
        "SINGLE_EVENT",
        "THEME_FORMATION",
        "BENEFICIARY_DISCOVERY",
        "CONTINUATION",
    }
    assert all(row["candidate_rank"] == 0 for row in expansion_candidate_check_rows)
    assert all(
        "market_cap_and_shares_outstanding" in row["verification_focus"]
        and "D_minus_one_trading_value_turnover_limit_up" in row["verification_focus"]
        and "current_news_relation_vs_name_similarity" in row["verification_focus"]
        for row in candidate_check_rows
    )
    assert all(row["available_before_cutoff"] is True for row in candidate_check_rows)
    assert all(row["time_verified"] is True for row in candidate_check_rows)
    assert all(row["timestamp_precision"] is None for row in candidate_check_rows)
    assert all("opened_text" not in row for row in candidate_check_rows)
    excluded_web_source_path = tmp_path / saved_manifest["excluded_web_source_artifact"]
    excluded_web_source_rows = [
        json.loads(line)
        for line in excluded_web_source_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["source_id"] for row in excluded_web_source_rows} == set(
        saved_manifest["excluded_web_source_ids"]
    )
    assert all(row["source_url"] == row["url"] for row in excluded_web_source_rows)
    assert saved_manifest["excluded_web_source_count"] == len(excluded_web_source_rows)
    assert all(
        row["exclusion_reason"] == "published_after_cutoff"
        for row in excluded_web_source_rows
    )
    assert all(row["available_before_cutoff"] is False for row in excluded_web_source_rows)
    assert all(row["time_verified"] is False for row in excluded_web_source_rows)
    assert all(row["timestamp_precision"] is None for row in excluded_web_source_rows)
    excluded_candidate_check_path = (
        tmp_path / saved_manifest["excluded_candidate_web_check_artifact"]
    )
    excluded_candidate_check_rows = [
        json.loads(line)
        for line in excluded_candidate_check_path.read_text(encoding="utf-8").splitlines()
    ]
    assert saved_manifest["excluded_candidate_web_check_count"] == len(
        excluded_candidate_check_rows
    )
    assert saved_manifest["excluded_candidate_web_check_count"] == len(
        analysis.blind_prediction.candidates
    ) + analysis.context_manifest.candidate_expansion_count
    assert saved_manifest["excluded_candidate_web_check_count"] == (
        saved_manifest["candidate_web_check_summary"]["excluded_source_count"]
    )
    assert {row["source_id"] for row in excluded_candidate_check_rows} == set(
        saved_manifest["excluded_candidate_web_source_ids"]
    )
    assert all(
        row["source_url"] == row["url"] for row in excluded_candidate_check_rows
    )
    assert all(
        row["exclusion_reason"] == "published_after_cutoff"
        for row in excluded_candidate_check_rows
    )
    assert all(row["timestamp_precision"] is None for row in excluded_candidate_check_rows)
    source_ledger = (
        tmp_path / saved_manifest["source_ledger_artifact"]
    ).read_text(encoding="utf-8")
    source_ledger_rows = [json.loads(line) for line in source_ledger.splitlines()]
    assert saved_manifest["source_ledger_entry_count"] == len(source_ledger_rows)
    assert any(row["source_type"] == "web_search_result" for row in source_ledger_rows)
    web_ledger_rows = [
        row for row in source_ledger_rows if row["source_type"] == "web_search_result"
    ]
    assert all(row["timestamp_precision"] is None for row in web_ledger_rows)
    candidate_ledger_rows = [
        row for row in source_ledger_rows if row["source_type"] == "candidate_web_check"
    ]
    assert len(candidate_ledger_rows) == saved_manifest["candidate_web_check_count"]
    assert {row["source_id"] for row in candidate_ledger_rows} == set(
        saved_manifest["candidate_web_source_ids"]
    )
    assert all(row["source_url"] == row["url"] for row in candidate_ledger_rows)
    assert all(row["usage_phase"] == "BLIND" for row in candidate_ledger_rows)
    assert all(row["available_before_cutoff"] is True for row in candidate_ledger_rows)
    assert all(row["timestamp_precision"] is None for row in candidate_ledger_rows)
    assert all("content" not in row for row in candidate_ledger_rows)
    assert audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))["passed"]


@pytest.mark.asyncio
async def test_analyze_uses_structured_llm_provider_for_blind_prediction(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","ProviderCo, catalyst","Structured LLM provider should see this."\n',
        encoding="utf-8",
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    company_dir = tmp_path / "memory" / "company_memory"
    company_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        company_dir / "CM-safe-provider.json",
        {
            "ticker": "100001",
            "company_name": "SafeMemoryCo",
            "aliases": ["SafeMemoryCo"],
            "business_descriptions": ["Known before cutoff and available to synthesis."],
            "locations": [],
            "customers": [],
            "supply_chain_roles": ["existing safe company memory"],
            "prior_market_narratives": ["safe pre-cutoff memory"],
            "prior_leader_occurrences": [],
            "contradictory_relations": [],
            "known_at": "2030-01-10T08:00:00+09:00",
            "provenance": [],
        },
    )
    write_json(
        company_dir / "CM-future-provider.json",
        {
            "ticker": "100002",
            "company_name": "FutureMemoryCo",
            "aliases": ["FutureMemoryCo"],
            "business_descriptions": ["Known after cutoff and unsafe for synthesis."],
            "locations": [],
            "customers": [],
            "supply_chain_roles": ["future company memory"],
            "prior_market_narratives": ["future post-cutoff memory"],
            "prior_leader_occurrences": [],
            "contradictory_relations": [],
            "known_at": "2030-01-10T09:30:00+09:00",
            "provenance": [],
        },
    )
    market_dir = tmp_path / "memory" / "market_memory"
    market_dir.mkdir(parents=True, exist_ok=True)
    (market_dir / "claims.jsonl").write_text(
        '{"claim_id":"M-safe","available_from":"2030-01-10T08:00:00+09:00",'
        '"statement":"safe market context reaches final synthesis"}\n'
        '{"claim_id":"M-future","available_from":"2030-01-10T09:30:00+09:00",'
        '"statement":"future market context must not reach synthesis"}\n'
        '{"claim_id":"M-unscoped","statement":"unscoped market context must not reach synthesis"}\n',
        encoding="utf-8",
    )
    llm = RecordingBlindLLM(
        expected_final_prompt_substring="cutoff-safe opened verification text",
        forbidden_final_prompt_substrings=[
            "FutureMemoryCo",
            "future market context must not reach synthesis",
            "unscoped market context must not reach synthesis",
        ],
    )
    web_provider = MixedTemporalWebProvider()

    analysis = await DailyAnalyzer(settings, llm=llm, web_provider=web_provider).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=True,
    )

    assert [call["purpose"] for call in llm.calls] == [
        "open_world_first_analysis",
        "news_novelty_review",
        "semantic_retrieval_plan",
        "candidate_expansion",
        "daily_blind_analysis",
        "red_team_candidate_review",
        "final_synthesis",
    ]
    blind_call = _llm_call(llm, "daily_blind_analysis")
    final_call = _llm_call(llm, "final_synthesis")
    assert "ProviderCo" in str(blind_call["prompt"])
    assert "news_novelty_review" in str(final_call["prompt"])
    assert "additional_semantic_retrieval" in str(final_call["prompt"])
    assert "open_world_candidate_expansion" in str(final_call["prompt"])
    assert "candidate_verification" in str(final_call["prompt"])
    assert "required_attack_checks" in str(final_call["prompt"])
    assert "red_team_output" in str(final_call["prompt"])
    assert analysis.blind_prediction.trade_date == date(2030, 1, 10)
    assert analysis.blind_prediction.cutoff_at == datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    assert analysis.blind_prediction.candidates[0].rank == 1
    assert analysis.blind_prediction.candidates[0].event_ids
    assert analysis.blind_prediction.blind_analysis.provenance
    assert analysis.blind_prediction.dominant_sectors[0].provenance
    assert analysis.blind_prediction.candidates[0].provenance
    assert analysis.blind_prediction.blind_analysis.summary == "Provider final synthesis."
    assert "provider red-team objection" in analysis.blind_prediction.candidates[0].counterarguments
    final_prompt = str(final_call["prompt"])
    assert "company_memory" in final_prompt
    assert "SafeMemoryCo" in final_prompt
    assert "FutureMemoryCo" not in final_prompt
    assert "market_memory" in final_prompt
    assert "safe market context reaches final synthesis" in final_prompt
    assert "future market context must not reach synthesis" not in final_prompt
    assert "unscoped market context must not reach synthesis" not in final_prompt
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json")
    assert saved_manifest["included_company_memory_files"] == [
        "memory/company_memory/CM-safe-provider.json"
    ]
    assert saved_manifest["omitted_company_memory_files"] == [
        {
            "path": "memory/company_memory/CM-future-provider.json",
            "reason": "company_memory_known_after_cutoff",
            "known_at": "2030-01-10T09:30:00+09:00",
        }
    ]
    assert saved_manifest["included_market_context_files"] == [
        "memory/market_memory/claims.jsonl#L1"
    ]
    assert saved_manifest["omitted_market_context_files"] == [
        {
            "path": "memory/market_memory/claims.jsonl#L2",
            "reason": "available_from_after_cutoff",
            "available_at": "2030-01-10T09:30:00+09:00",
        },
        {
            "path": "memory/market_memory/claims.jsonl#L3",
            "reason": "missing_temporal_scope",
        },
    ]
    assert audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))["passed"]
    assert analysis.blind_prediction.blind_artifact_sha256
    assert analysis.context_manifest.red_team_artifacts
    assert analysis.context_manifest.prompt_hashes["open_world_first_analysis"]
    assert analysis.context_manifest.prompt_hashes["news_novelty_review"]
    assert analysis.context_manifest.prompt_hashes["final_synthesis"]
    traces = [read_json(path) for path in (tmp_path / "runs" / "traces").glob("TRACE-*.json")]
    assert any(trace["purpose"] == "open_world_first_analysis" for trace in traces)
    assert any(trace["purpose"] == "news_novelty_review" for trace in traces)
    assert any(trace["purpose"] == "semantic_retrieval_plan" for trace in traces)
    assert any(trace["purpose"] == "candidate_expansion" for trace in traces)
    assert any(trace["purpose"] == "daily_blind_analysis" for trace in traces)
    assert any(trace["purpose"] == "red_team_candidate_review" for trace in traces)
    assert any(trace["purpose"] == "final_synthesis" for trace in traces)
    prompt_versions = {trace["purpose"]: trace["prompt_version"] for trace in traces}
    assert prompt_versions["open_world_first_analysis"] == "open_world_first_analysis.v1"
    assert prompt_versions["news_novelty_review"] == "news_novelty_review.v1"
    assert prompt_versions["semantic_retrieval_plan"] == "semantic_retrieval_plan.v1"
    assert prompt_versions["candidate_expansion"] == "candidate_expansion.v1"
    assert prompt_versions["daily_blind_analysis"] == "daily_blind_analysis.v1"
    assert prompt_versions["red_team_candidate_review"] == "red_team.candidate_attack.v2"
    assert prompt_versions["final_synthesis"] == "synthesis.final.v1"


@pytest.mark.asyncio
async def test_run_id_changes_when_llm_model_config_changes(tmp_path) -> None:
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","ProviderCo, catalyst","Run ids include model config."\n',
        encoding="utf-8",
    )
    settings_a = Settings(project_root=tmp_path)
    settings_a.llm_provider = "mock-a"
    ensure_project_dirs(settings_a)
    BrainCompiler(tmp_path).rebuild(mode="full")

    first = await DailyAnalyzer(settings_a, llm=RecordingBlindLLM()).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    settings_b = Settings(project_root=tmp_path)
    settings_b.llm_provider = "mock-b"
    second = await DailyAnalyzer(settings_b, llm=RecordingBlindLLM()).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    assert first.run_id != second.run_id
    first_manifest = read_json(tmp_path / "runs" / "manifests" / f"{first.run_id}.json")
    second_manifest = read_json(tmp_path / "runs" / "manifests" / f"{second.run_id}.json")
    assert first_manifest["model_config"]["configured_provider"] == "mock-a"
    assert second_manifest["model_config"]["configured_provider"] == "mock-b"


@pytest.mark.asyncio
async def test_final_synthesis_receives_counterexample_context(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","CounterCo, catalyst","Counterexample context should be visible."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    episode = _episode_with_counterexample()
    store.save_episode(episode)
    store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    llm = RecordingBlindLLM()

    analysis = await DailyAnalyzer(settings, llm=llm).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    final_prompt = str(_llm_call(llm, "final_synthesis")["prompt"])
    assert analysis.context_manifest.counterexample_episode_ids == ["EP-counterexample"]
    assert '"negative_cases":["EP-counterexample"]' in final_prompt
    assert "Same-looking catalyst failed" in final_prompt
    assert analysis.blind_prediction.candidates[0].prior_negative_cases == [
        "EP-counterexample"
    ]
    assert "EP-counterexample" in analysis.blind_prediction.candidates[0].memory_episode_ids
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json")
    assert saved_manifest["counterexample_episode_ids"] == ["EP-counterexample"]


@pytest.mark.asyncio
async def test_counterexample_cases_reach_candidates_sectors_and_report(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","CounterCo, catalyst","Counterexample context should be visible."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    episode = _episode_with_counterexample()
    store.save_episode(episode)
    store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")

    analysis = await DailyAnalyzer(
        settings,
        retrieval=LocalRetrievalStore(tmp_path, force_empty=True),
    ).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    assert analysis.context_manifest.counterexample_episode_ids == ["EP-counterexample"]
    assert analysis.blind_prediction.candidates
    assert all(
        candidate.prior_negative_cases == ["EP-counterexample"]
        for candidate in analysis.blind_prediction.candidates
    )
    assert all(
        "EP-counterexample" in candidate.memory_episode_ids
        for candidate in analysis.blind_prediction.candidates
    )
    assert analysis.blind_prediction.dominant_sectors
    assert all(
        sector.contradicting_cases == ["EP-counterexample"]
        for sector in analysis.blind_prediction.dominant_sectors
    )
    saved_prediction = read_json(tmp_path / analysis.prediction_path)
    assert saved_prediction["candidates"][0]["prior_negative_cases"] == ["EP-counterexample"]
    assert saved_prediction["dominant_sectors"][0]["contradicting_cases"] == ["EP-counterexample"]
    report = (tmp_path / analysis.report_path).read_text(encoding="utf-8")
    assert "Prior negative cases: EP-counterexample" in report


@pytest.mark.asyncio
async def test_retrieved_raw_episodes_are_filtered_by_available_from(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","ProviderCo, catalyst","Retrieved raw episode context should be visible."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    available = _retrieval_episode(
        "EP-retrieved",
        summary="ProviderCo retrieved raw summary available before the run.",
        available_day=date(2030, 1, 10),
    )
    future = _retrieval_episode(
        "EP-future-retrieved",
        summary="ProviderCo future unavailable summary must not enter blind context.",
        available_day=date(2030, 1, 10),
        available_time=time(9, 30, 0),
    )
    for episode in (available, future):
        store.save_episode(episode)
        store.accept(episode.episode_id)
    llm = RecordingBlindLLM()

    analysis = await DailyAnalyzer(settings, llm=llm).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    final_prompt = str(_llm_call(llm, "final_synthesis")["prompt"])
    assert analysis.context_manifest.retrieved_episode_ids == ["EP-retrieved"]
    assert analysis.context_manifest.excluded_retrieved_episode_ids == ["EP-future-retrieved"]
    assert "EP-retrieved" in analysis.context_manifest.semantic_retrieval_episode_ids
    assert (
        "EP-future-retrieved"
        in analysis.context_manifest.excluded_semantic_retrieval_episode_ids
    )
    assert "ProviderCo retrieved raw summary available before the run" in final_prompt
    assert "ProviderCo future unavailable summary must not enter blind context" not in final_prompt
    assert "additional_semantic_retrieval" in final_prompt
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json")
    assert saved_manifest["retrieved_episode_ids"] == ["EP-retrieved"]
    assert saved_manifest["excluded_retrieved_episode_ids"] == ["EP-future-retrieved"]
    assert "EP-retrieved" in saved_manifest["semantic_retrieval_episode_ids"]
    assert "EP-future-retrieved" in saved_manifest[
        "excluded_semantic_retrieval_episode_ids"
    ]


@pytest.mark.asyncio
async def test_analyze_uses_as_of_brain_when_current_brain_contains_future_episode(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","ProviderCo, catalyst","Future brain context must stop the run."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    available = _retrieval_episode(
        "EP-available-brain",
        summary="ProviderCo available brain summary.",
        available_day=date(2030, 1, 10),
    )
    available = available.model_copy(
        update={
            "lessons": [
                MemoryClaim(
                    claim_id="CL-available-asof-lesson",
                    statement="Available imported lesson must enter as-of brain context.",
                    mechanism="available imported lesson -> as-of brain",
                    scope="as-of context fixture",
                    support_episode_ids=[],
                    status=ClaimStatus.TENTATIVE,
                    confidence_label=ConfidenceLabel.MEDIUM,
                    available_from=available.available_from,
                )
            ]
        }
    )
    future = _retrieval_episode(
        "EP-future-brain",
        summary="ProviderCo future brain summary must stop analysis.",
        available_day=date(2030, 1, 10),
        available_time=time(9, 30, 0),
    )
    for episode in (available, future):
        store.save_episode(episode)
        store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    llm = RecordingBlindLLM()

    analysis = await DailyAnalyzer(settings, llm=llm).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    assert manifest.brain_version is not None
    assert manifest.brain_version.startswith("brain-asof-")
    assert manifest.accepted_episode_count == 1
    assert manifest.total_accepted_episode_count == 2
    assert manifest.available_episode_count == 1
    assert manifest.unavailable_episode_count == 1
    assert manifest.unavailable_episode_ids == ["EP-future-brain"]
    assert manifest.swept_episode_ids == ["EP-available-brain"]
    assert manifest.errors == []
    assert all(
        path.startswith(f"runs/checkpoints/brain_context/{manifest.run_id}/brain/")
        for path in manifest.brain_files
    )
    assert all(
        path.startswith(f"runs/checkpoints/brain_context/{manifest.run_id}/shards/")
        for path in manifest.shard_brain_files
    )
    context_text = "\n".join(
        (tmp_path / path).read_text(encoding="utf-8")
        for path in [*manifest.brain_files, *manifest.shard_brain_files]
    )
    assert "EP-available-brain" in context_text
    assert "EP-future-brain" not in context_text
    assert "CL-available-asof-lesson" in context_text
    assert "Available imported lesson must enter as-of brain context." in context_text
    final_prompt = str(_llm_call(llm, "final_synthesis")["prompt"])
    assert "ProviderCo available brain summary" in final_prompt
    assert "Available imported lesson must enter as-of brain context." in final_prompt
    assert "ProviderCo future brain summary must stop analysis" not in final_prompt
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json")
    assert saved_manifest["brain_version"].startswith("brain-asof-")
    assert saved_manifest["total_accepted_episode_count"] == 2
    assert saved_manifest["available_episode_count"] == 1
    assert saved_manifest["unavailable_episode_count"] == 1
    assert saved_manifest["unavailable_episode_ids"] == ["EP-future-brain"]
    assert audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))["passed"]
    assert (tmp_path / "predictions" / "2030-01-10.json").exists()


@pytest.mark.asyncio
async def test_manifest_brain_context_remains_immutable_after_later_brain_update(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","ProviderCo, new catalyst","Immutable context test."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    available = _retrieval_episode(
        "EP-before-cutoff",
        summary="ProviderCo available lesson.",
        available_day=date(2030, 1, 10),
    )
    store.save_episode(available)
    store.accept(available.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    assert manifest.brain_version is not None
    assert manifest.brain_version.startswith("brain-")
    assert all(
        path.startswith(f"runs/checkpoints/brain_context/{manifest.run_id}/brain/")
        for path in manifest.brain_files
    )
    future = _retrieval_episode(
        "EP-later-postmortem",
        summary="ProviderCo future postmortem must not rewrite prior context.",
        available_day=date(2030, 1, 10),
        available_time=time(9, 30, 0),
    )
    store.save_episode(future)
    store.accept(future.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")

    checkpoint_text = "\n".join(
        (tmp_path / path).read_text(encoding="utf-8")
        for path in [*manifest.brain_files, *manifest.shard_brain_files]
    )
    assert "EP-before-cutoff" in checkpoint_text
    assert "EP-later-postmortem" not in checkpoint_text
    assert audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))["passed"]


@pytest.mark.asyncio
async def test_new_company_candidate_creates_company_memory_candidate(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","NovelCo, new facility",'
        '"New company appears without pre-existing company memory."\n',
        encoding="utf-8",
    )
    BrainCompiler(tmp_path).rebuild(mode="full")

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    assert analysis.blind_prediction.candidates[0].company_name == "NovelCo"
    memory_paths = list((tmp_path / "memory" / "company_memory").glob("*.json"))
    assert len(memory_paths) == 1
    memory = read_json(memory_paths[0])
    assert memory["company_name"] == "NovelCo"
    assert memory["ticker"] == "UNKNOWN"
    assert memory["known_at"] == "2030-01-10T08:59:59+09:00"
    assert memory["provenance"][0]["source_type"] == "blind_analysis_company_memory_candidate"
    assert memory["provenance"][0]["uri"] == analysis.prediction_path
    await DailyAnalyzer(settings).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )
    memory_after_rerun = read_json(memory_paths[0])
    provenance_source_ids = [
        item["source_id"] for item in memory_after_rerun["provenance"]
    ]
    assert provenance_source_ids == list(dict.fromkeys(provenance_source_ids))


@pytest.mark.asyncio
async def test_exhaustive_analyze_fails_when_memory_sweep_is_incomplete(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","CoverageCo, catalyst","Coverage failure should stop output."\n',
        encoding="utf-8",
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    monkeypatch.setattr(analyzer_module, "MemorySweeper", BrokenMemorySweeper)

    with pytest.raises(ExhaustiveCoverageError, match="exhaustive memory coverage failed"):
        await DailyAnalyzer(settings).analyze(
            news_csv=csv_path,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            mode="exhaustive",
            web_search=False,
        )

    assert not (tmp_path / "predictions" / "2030-01-10.json").exists()
    manifests = list((tmp_path / "runs" / "manifests").glob("RUN-*.json"))
    assert len(manifests) == 1
    manifest = read_json(manifests[0])
    assert manifest["accepted_episode_count"] == 1
    assert manifest["swept_episode_count"] == 0
    assert "memory sweep missing accepted episodes: EP-missing" in manifest["errors"]
    assert "exhaustive mode requires swept_episode_count == accepted_episode_count" in manifest["errors"]


@pytest.mark.asyncio
async def test_blind_analyze_does_not_request_d_day_outcomes(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","SampleCo, preopen event","Outcome access is forbidden."\n',
        encoding="utf-8",
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    price_source = OutcomeTrapPriceSource()

    analysis = await DailyAnalyzer(settings, price_source=price_source).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    assert price_source.outcome_calls == []
    assert price_source.snapshot_calls == []
    assert analysis.context_manifest.blind_price_repository_access_count == 0
    assert analysis.context_manifest.blind_current_price_access_count == 0
    assert analysis.context_manifest.no_d_outcome_exposed is True
    assert analysis.context_manifest.price_snapshot.source_name == "outcome-trap"
    assert analysis.context_manifest.price_snapshot.as_of == datetime(
        2030, 1, 10, 8, 59, 59, tzinfo=KST
    )
    assert analysis.context_manifest.price_snapshot.allowed_through == date(2030, 1, 9)


def test_d_minus_one_market_data_uses_blind_price_guard(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    price_source = OutcomeTrapPriceSource()
    analyzer = DailyAnalyzer(settings, price_source=price_source)
    trade_day = date(2030, 1, 10)
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    manifest = ContextManifest(
        run_id="RUN-price-guard",
        mode="exhaustive",
        trade_date=trade_day,
        cutoff_at=cutoff,
        as_of=cutoff,
        accepted_episode_count=0,
        swept_episode_count=0,
        price_snapshot=PriceSnapshot(
            source_name="outcome-trap",
            as_of=cutoff,
            allowed_through=date(2030, 1, 9),
        ),
    )
    candidates = [
        Candidate(
            rank=1,
            ticker="123456",
            company_name="DMinusOneCo",
            path_type=PathType.CONTINUATION,
            thesis="Continuation candidate.",
            why_now="D-1 snapshot should be allowed.",
            causal_chain=["D-1 market data", "continuation check"],
            confidence_label=ConfidenceLabel.LOW,
        )
    ]

    market_data = analyzer._collect_d_minus_one_market_data(
        candidates=candidates,
        manifest=manifest,
    )

    assert price_source.outcome_calls == []
    assert price_source.snapshot_calls == [("123456", date(2030, 1, 9))]
    assert manifest.blind_context_mode == "D_MINUS_ONE_PRICE_BLIND"
    assert manifest.blind_price_repository_access_count == 1
    assert manifest.blind_current_price_access_count == 0
    assert market_data["status"] == "D_MINUS_ONE_PRICE_SNAPSHOTS"
    assert market_data["blind_context_mode"] == "D_MINUS_ONE_PRICE_BLIND"
    assert market_data["blind_price_repository_access_count"] == 1
    assert market_data["blind_current_price_access_count"] == 0
    assert market_data["snapshots"] == [
        {
            "ticker": "123456",
            "trade_date": "2030-01-09",
            "open": None,
            "high": None,
            "low": None,
            "close": 100.0,
            "volume": None,
            "amount": None,
            "market_cap": None,
            "listed_shares": None,
        }
    ]


def test_daily_analyzer_uses_configured_stock_web_price_source(tmp_path) -> None:
    stock_web_path = tmp_path / "stock-web"
    _write_minimal_stock_web_atlas(stock_web_path)
    settings = Settings(
        project_root=tmp_path,
        price_provider="stock-web",
        stock_web_path=stock_web_path,
    )

    analyzer = DailyAnalyzer(settings)

    assert analyzer.price_source is not None
    assert analyzer.price_source.source_name == "stock-web"


@pytest.mark.asyncio
async def test_exhaustive_analyze_persists_all_memory_sweep_shards(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.shard_episode_count = 1
    ensure_project_dirs(settings)
    importer = ResearchImporter(tmp_path)
    store = ResearchStore(tmp_path)
    for day in (8, 9):
        source = tmp_path / f"research_203001{day}.md"
        source.write_text(
            f"# Research 2030-01-{day}\n\nMechanism notes and counterexamples.",
            encoding="utf-8",
        )
        episode = await importer.import_path_async(source, mode="semantic")
        store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")

    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-11","08:00:00","SampleCo, new facility","Needs open-world review."\n',
        encoding="utf-8",
    )
    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 11),
        cutoff_at=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    assert manifest.accepted_episode_count == 2
    assert manifest.swept_episode_count == 2
    assert len(manifest.shard_brain_files) == 2
    shard_brain_text = "\n".join(
        (tmp_path / shard_path).read_text(encoding="utf-8")
        for shard_path in manifest.shard_brain_files
    )
    assert all(episode_id in shard_brain_text for episode_id in manifest.swept_episode_ids)
    assert manifest.memory_sweep_shard_count == 2
    assert manifest.memory_sweep_cache_hits == 0
    assert len(manifest.memory_sweep_artifacts) == 2
    assert set(manifest.memory_sweep_artifact_hashes) == set(
        manifest.memory_sweep_artifacts
    )
    swept_from_artifacts: set[str] = set()
    for artifact in manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        assert payload["cache_key"]
        assert payload["episode_shard_sha256"]
        source_hashes = payload["episode_shard_source_hashes"]
        assert sorted(source_hashes) == sorted(payload["episode_ids"])
        assert payload["episode_shard_sha256"] == sha256_text(
            canonical_json(
                [
                    {
                        "episode_id": episode_id,
                        "source_sha256": source_hash,
                    }
                    for episode_id, source_hash in sorted(source_hashes.items())
                ]
            )
        )
        for episode_id in payload["episode_ids"]:
            assert source_hashes[episode_id] == file_sha256(
                tmp_path / "research" / "accepted" / f"{episode_id}.json"
            )
        assert payload["from_cache"] is False
        swept_from_artifacts.update(payload["episode_ids"])
    assert swept_from_artifacts == set(manifest.swept_episode_ids)

    repeated = await DailyAnalyzer(settings).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 11),
        cutoff_at=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    repeated_manifest = repeated.context_manifest
    assert repeated_manifest.run_id == manifest.run_id
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{manifest.run_id}.json")
    assert saved_manifest["shard_brain_files"] == manifest.shard_brain_files
    assert set(saved_manifest["shard_brain_file_hashes"]) == set(manifest.shard_brain_files)
    assert set(saved_manifest["memory_sweep_artifact_hashes"]) == set(
        manifest.memory_sweep_artifacts
    )
    assert repeated_manifest.memory_sweep_shard_count == 2
    assert repeated_manifest.memory_sweep_cache_hits == 2
    for artifact in repeated_manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        assert payload["from_cache"] is True

    changed_settings = Settings(project_root=tmp_path)
    changed_settings.limits.shard_episode_count = 1
    changed_settings.llm_provider = "mock-cache-other"
    changed_model = await DailyAnalyzer(
        changed_settings,
        llm=RecordingBlindLLM(),
    ).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 11),
        cutoff_at=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    changed_manifest = changed_model.context_manifest
    assert changed_manifest.run_id != manifest.run_id
    assert changed_manifest.memory_sweep_shard_count == 2
    assert changed_manifest.memory_sweep_cache_hits == 0
    for artifact in changed_manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        assert payload["from_cache"] is False
        assert payload["prompt_version"] == "memory_sweep.shard_analysis.v1"
        assert payload["model_config_sha256"]


@pytest.mark.asyncio
async def test_brain_mode_loads_shard_brains_and_sweeps_available_episodes(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.shard_episode_count = 1
    ensure_project_dirs(settings)
    store = ResearchStore(tmp_path)
    for index in range(2):
        episode = _retrieval_episode(
            f"EP-brain-mode-{index}",
            summary=f"Brain mode lesson {index} should reach shard context.",
            available_day=date(2030, 1, 10),
        )
        store.save_episode(episode)
        store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    csv_path = tmp_path / "brain_mode_news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","BrainModeCo, catalyst",'
        '"Brain mode must carry shard summaries and swept memory."\n',
        encoding="utf-8",
    )
    llm = RecordingBlindLLM(
        expected_final_prompt_substring="Brain mode lesson 0 should reach shard context."
    )

    analysis = await DailyAnalyzer(settings, llm=llm).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="brain",
        web_search=False,
    )

    manifest = analysis.context_manifest
    expected_ids = {"EP-brain-mode-0", "EP-brain-mode-1"}
    assert manifest.mode == "brain"
    assert manifest.accepted_episode_count == 2
    assert manifest.swept_episode_count == 2
    assert set(manifest.swept_episode_ids) == expected_ids
    assert manifest.errors == []
    assert manifest.memory_sweep_shard_count == 2
    assert len(manifest.memory_sweep_artifacts) == 2
    assert set(manifest.memory_sweep_artifact_hashes) == set(
        manifest.memory_sweep_artifacts
    )
    shard_brain_text = "\n".join(
        (tmp_path / path).read_text(encoding="utf-8")
        for path in manifest.shard_brain_files
    )
    assert expected_ids <= {
        episode_id for episode_id in expected_ids if episode_id in shard_brain_text
    }
    swept_from_artifacts: set[str] = set()
    for artifact in manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        assert payload["mode"] == "brain"
        swept_from_artifacts.update(payload["episode_ids"])
    assert swept_from_artifacts == expected_ids


@pytest.mark.asyncio
async def test_exhaustive_analyze_sweeps_one_hundred_accepted_episodes(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.shard_episode_count = 10
    ensure_project_dirs(settings)
    store = ResearchStore(tmp_path)
    for index in range(100):
        episode = ResearchEpisode(
            episode_id=f"EP-scale-{index:03d}",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
            research_version="scale-coverage-test-v1",
            price_source_snapshot={"source": "scale-test"},
            blind_analysis=BlindAnalysis(
                summary=f"Scale coverage lesson {index:03d}.",
                open_world_mechanisms=[
                    f"EP-scale-{index:03d} -> exhaustive sweep coverage path"
                ],
            ),
            available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
        )
        store.save_episode(episode)
        store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")

    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-12","08:00:00","ScaleCo, catalyst",'
        '"Every accepted episode must be swept in exhaustive mode."\n',
        encoding="utf-8",
    )
    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 12),
        cutoff_at=datetime(2030, 1, 12, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    expected_ids = {f"EP-scale-{index:03d}" for index in range(100)}
    assert manifest.accepted_episode_count == 100
    assert manifest.swept_episode_count == 100
    assert set(manifest.swept_episode_ids) == expected_ids
    assert manifest.memory_sweep_shard_count == 10
    assert len(manifest.memory_sweep_artifacts) == 10
    swept_from_artifacts: set[str] = set()
    for artifact in manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        assert payload["episode_count"] == 10
        swept_from_artifacts.update(payload["episode_ids"])
    assert swept_from_artifacts == expected_ids
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{manifest.run_id}.json")
    assert saved_manifest["accepted_episode_count"] == 100
    assert saved_manifest["swept_episode_count"] == 100
