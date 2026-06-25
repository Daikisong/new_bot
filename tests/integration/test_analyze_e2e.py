from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import TypeVar

import pytest
from pydantic import BaseModel

import news_scalping_lab.inference.analyzer as analyzer_module
from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.sweep import SweepResult
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    ClaimStatus,
    ConfidenceLabel,
    MemoryClaim,
    OutcomeLabels,
    PathType,
    RedTeamArtifact,
    RedTeamFinding,
    ResearchEpisode,
)
from news_scalping_lab.inference.analyzer import (
    DailyAnalyzer,
    ExhaustiveCoverageError,
)
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, read_json, sha256_text
from news_scalping_lab.web.provider import WebSearchResult

T = TypeVar("T", bound=BaseModel)


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
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        raise AssertionError("daily analyzer should request structured output")

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        self.calls.append(
            {"prompt": prompt, "response_model": response_model, "purpose": purpose}
        )
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
            assert "NEWS_ONLY_STRICT_NO_PRICE_ACCESS" in prompt
            assert "retrieved_raw_episodes" in prompt
            assert "all_shard_brains" in prompt
            assert "all_shard_contributions" in prompt
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


class BrokenMemorySweeper:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def sweep(self, *args: object, **kwargs: object) -> SweepResult:
        return SweepResult(
            accepted_episode_count=1,
            swept_episode_ids=[],
            artifact_paths=[],
            shard_count=0,
            cache_hits=0,
            token_counts={"memory_sweep": 0},
            errors=["memory sweep missing accepted episodes: EP-missing"],
        )


class MixedTemporalWebProvider:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, datetime]] = []

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
        return url

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
    assert saved_manifest["model_config"] == {
        "analysis_mode": "exhaustive",
        "configured_provider": "mock",
        "max_concurrency": 4,
        "provider_class": "DeterministicMockLLMProvider",
        "shard_episode_count": 20,
    }
    assert saved_manifest["red_team_artifacts"] == analysis.context_manifest.red_team_artifacts
    assert saved_manifest["prompt_hashes"]["red_team_candidate_review"]
    assert saved_manifest["prompt_hashes"]["final_synthesis"]
    assert saved_manifest["row_disposition_artifact"]
    assert saved_manifest["row_disposition_coverage_ratio"] == 1.0
    assert saved_manifest["row_disposition_summary"] == {
        "coverage_ratio": 1.0,
        "excluded_after_cutoff": 1,
        "included_before_cutoff": 1,
        "total_rows": 2,
    }
    row_disposition_path = tmp_path / saved_manifest["row_disposition_artifact"]
    row_disposition_text = row_disposition_path.read_text(encoding="utf-8")
    row_dispositions = [
        json.loads(line) for line in row_disposition_text.splitlines() if line.strip()
    ]
    assert sha256_text(row_disposition_text) == saved_manifest["row_disposition_sha256"]
    assert [row["row_number"] for row in row_dispositions] == [1, 2]
    assert [row["disposition"] for row in row_dispositions] == [
        "INCLUDED_BEFORE_CUTOFF",
        "EXCLUDED_AFTER_CUTOFF",
    ]
    assert "title" not in row_dispositions[0]
    assert "body" not in row_dispositions[0]
    assert row_dispositions[0]["title_sha256"]
    assert row_dispositions[0]["body_sha256"]
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
    assert source_ledger_rows[0]["input_row_ids"] == [1]
    assert "body" not in source_ledger_rows[0]
    assert saved_manifest["blind_artifact_sha256"] == saved_prediction["blind_artifact_sha256"]
    receipt_path = tmp_path / saved_manifest["blind_seal_receipt_artifact"]
    receipt_text = receipt_path.read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)
    assert sha256_text(receipt_text) == saved_manifest["blind_seal_receipt_sha256"]
    assert receipt["phase"] == "BLIND_SEALED"
    assert receipt["blind_artifact_sha256"] == saved_prediction["blind_artifact_sha256"]
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
        in {"daily_blind_analysis", "red_team_candidate_review", "final_synthesis"}
    }
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
    assert all(finding["passed_to_synthesis"] for finding in red_team["candidate_findings"])
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

    assert len(web_provider.search_calls) == len(analysis.context_manifest.web_queries)
    assert analysis.context_manifest.blind_context_mode == "CUTOFF_SAFE_WEB_BLIND"
    assert analysis.context_manifest.blind_web_search_call_count == len(
        analysis.context_manifest.web_queries
    )
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
    manifest_path = tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json"
    saved_manifest = read_json(manifest_path)
    assert saved_manifest["blind_context_mode"] == "CUTOFF_SAFE_WEB_BLIND"
    assert saved_manifest["blind_web_search_call_count"] == len(saved_manifest["web_queries"])
    assert saved_manifest["web_sources"] == analysis.context_manifest.web_sources
    assert saved_manifest["excluded_web_source_ids"] == (
        analysis.context_manifest.excluded_web_source_ids
    )
    web_source_path = tmp_path / saved_manifest["web_source_artifact"]
    web_source_rows = [
        json.loads(line) for line in web_source_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["source_id"] for row in web_source_rows} == set(saved_manifest["web_sources"])
    assert all(row["available_before_cutoff"] is True for row in web_source_rows)
    source_ledger = (
        tmp_path / saved_manifest["source_ledger_artifact"]
    ).read_text(encoding="utf-8")
    source_ledger_rows = [json.loads(line) for line in source_ledger.splitlines()]
    assert any(row["source_type"] == "web_search_result" for row in source_ledger_rows)
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
    llm = RecordingBlindLLM()

    analysis = await DailyAnalyzer(settings, llm=llm).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
    )

    assert len(llm.calls) == 3
    assert llm.calls[0]["purpose"] == "daily_blind_analysis"
    assert llm.calls[1]["purpose"] == "red_team_candidate_review"
    assert llm.calls[2]["purpose"] == "final_synthesis"
    assert "ProviderCo" in str(llm.calls[0]["prompt"])
    assert "red_team_output" in str(llm.calls[2]["prompt"])
    assert analysis.blind_prediction.trade_date == date(2030, 1, 10)
    assert analysis.blind_prediction.cutoff_at == datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    assert analysis.blind_prediction.candidates[0].rank == 1
    assert analysis.blind_prediction.candidates[0].event_ids
    assert analysis.blind_prediction.blind_analysis.provenance
    assert analysis.blind_prediction.dominant_sectors[0].provenance
    assert analysis.blind_prediction.candidates[0].provenance
    assert analysis.blind_prediction.blind_analysis.summary == "Provider final synthesis."
    assert "provider red-team objection" in analysis.blind_prediction.candidates[0].counterarguments
    assert analysis.blind_prediction.blind_artifact_sha256
    assert analysis.context_manifest.red_team_artifacts
    assert analysis.context_manifest.prompt_hashes["final_synthesis"]
    traces = [read_json(path) for path in (tmp_path / "runs" / "traces").glob("TRACE-*.json")]
    assert any(trace["purpose"] == "daily_blind_analysis" for trace in traces)
    assert any(trace["purpose"] == "red_team_candidate_review" for trace in traces)
    assert any(trace["purpose"] == "final_synthesis" for trace in traces)
    prompt_versions = {trace["purpose"]: trace["prompt_version"] for trace in traces}
    assert prompt_versions["daily_blind_analysis"] == "daily_blind_analysis.v1"
    assert prompt_versions["red_team_candidate_review"] == "red_team.candidate_attack.v1"
    assert prompt_versions["final_synthesis"] == "synthesis.final.v1"


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

    final_prompt = str(llm.calls[2]["prompt"])
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

    final_prompt = str(llm.calls[2]["prompt"])
    assert analysis.context_manifest.retrieved_episode_ids == ["EP-retrieved"]
    assert analysis.context_manifest.excluded_retrieved_episode_ids == ["EP-future-retrieved"]
    assert "ProviderCo retrieved raw summary available before the run" in final_prompt
    assert "ProviderCo future unavailable summary must not enter blind context" not in final_prompt
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json")
    assert saved_manifest["retrieved_episode_ids"] == ["EP-retrieved"]
    assert saved_manifest["excluded_retrieved_episode_ids"] == ["EP-future-retrieved"]


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
    final_prompt = str(llm.calls[2]["prompt"])
    assert "ProviderCo available brain summary" in final_prompt
    assert "Available imported lesson must enter as-of brain context." in final_prompt
    assert "ProviderCo future brain summary must stop analysis" not in final_prompt
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json")
    assert saved_manifest["brain_version"].startswith("brain-asof-")
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
    assert analysis.context_manifest.price_snapshot.allowed_through == date(2030, 1, 9)


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
    assert len(manifest.shard_brain_files) == 1
    shard_brain_text = (tmp_path / manifest.shard_brain_files[0]).read_text(encoding="utf-8")
    assert all(episode_id in shard_brain_text for episode_id in manifest.swept_episode_ids)
    assert manifest.memory_sweep_shard_count == 2
    assert manifest.memory_sweep_cache_hits == 0
    assert len(manifest.memory_sweep_artifacts) == 2
    swept_from_artifacts: set[str] = set()
    for artifact in manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        assert payload["cache_key"]
        assert payload["episode_shard_sha256"]
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
    assert repeated_manifest.memory_sweep_shard_count == 2
    assert repeated_manifest.memory_sweep_cache_hits == 2
    for artifact in repeated_manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        assert payload["from_cache"] is True
