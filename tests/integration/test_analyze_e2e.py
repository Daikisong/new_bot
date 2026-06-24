from __future__ import annotations

from datetime import date, datetime
from typing import TypeVar

import pytest
from pydantic import BaseModel

from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import BlindAnalysis, BlindPrediction, Candidate, OutcomeLabels, PathType
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, read_json

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
    assert (tmp_path / analysis.report_path).exists()
    assert (tmp_path / analysis.prediction_path).exists()
    assert audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))["passed"]
    assert audit_provenance(tmp_path)["passed"]


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
    assert len(manifest.memory_sweep_artifacts) == 2
    swept_from_artifacts: set[str] = set()
    for artifact in manifest.memory_sweep_artifacts:
        payload = read_json(tmp_path / artifact)
        swept_from_artifacts.update(payload["episode_ids"])
    assert swept_from_artifacts == set(manifest.swept_episode_ids)
