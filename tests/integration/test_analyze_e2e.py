from __future__ import annotations

from datetime import date, datetime, timedelta
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
    OutcomeLabels,
    PathType,
)
from news_scalping_lab.inference.analyzer import DailyAnalyzer, ExhaustiveCoverageError
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, read_json
from news_scalping_lab.web.provider import WebSearchResult

T = TypeVar("T", bound=BaseModel)


class OutcomeTrapPriceSource:
    source_name = "outcome-trap"

    def __init__(self) -> None:
        self.outcome_calls: list[tuple[str, date]] = []

    def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
        return [
            PriceRecord(
                ticker=ticker,
                trade_date=through,
                close=100.0,
            )
        ]

    def get_snapshot(self, ticker: str, *, as_of: date) -> PriceRecord | None:
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
        assert response_model is BlindPrediction
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


class FutureOnlyWebProvider:
    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                source_id="WEB-FUTURE-PIPELINE",
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


@pytest.mark.asyncio
async def test_analyze_retrieval_miss_still_outputs_candidates(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","가상회사, 신규 사업 검토","상장 여부와 직접성을 검증해야 한다."\n',
        encoding="utf-8",
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    analyzer = DailyAnalyzer(settings, retrieval=LocalRetrievalStore(tmp_path, force_empty=True))
    analysis = await analyzer.analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=True,
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
    assert audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))["passed"]
    assert audit_provenance(tmp_path)["passed"]


@pytest.mark.asyncio
async def test_analyze_excludes_cutoff_after_web_sources_from_manifest_and_prediction(
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

    analysis = await DailyAnalyzer(settings, web_provider=FutureOnlyWebProvider()).analyze(
        news_csv=csv_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=True,
    )

    assert analysis.context_manifest.web_sources == []
    assert analysis.context_manifest.excluded_web_source_ids == ["WEB-FUTURE-PIPELINE"]
    assert analysis.blind_prediction.blind_analysis.excluded_after_cutoff_source_ids == [
        "WEB-FUTURE-PIPELINE"
    ]
    manifest_path = tmp_path / "runs" / "manifests" / f"{analysis.run_id}.json"
    saved_manifest = read_json(manifest_path)
    assert saved_manifest["web_sources"] == []
    assert saved_manifest["excluded_web_source_ids"] == ["WEB-FUTURE-PIPELINE"]


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

    assert len(llm.calls) == 1
    assert llm.calls[0]["purpose"] == "daily_blind_analysis"
    assert "ProviderCo" in str(llm.calls[0]["prompt"])
    assert analysis.blind_prediction.trade_date == date(2030, 1, 10)
    assert analysis.blind_prediction.cutoff_at == datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    assert analysis.blind_prediction.candidates[0].rank == 1
    assert analysis.blind_prediction.candidates[0].event_ids
    assert analysis.blind_prediction.blind_artifact_sha256
    traces = [read_json(path) for path in (tmp_path / "runs" / "traces").glob("TRACE-*.json")]
    assert any(trace["purpose"] == "daily_blind_analysis" for trace in traces)


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
    assert repeated_manifest.memory_sweep_shard_count == 2
    assert repeated_manifest.memory_sweep_cache_hits == 2
    for artifact in repeated_manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        assert payload["from_cache"] is True
