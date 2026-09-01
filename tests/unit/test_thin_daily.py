from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.offline_brain import (
    CurrentDayInterpretation,
    CurrentEventCapsule,
    DailyBrainContext,
    ExactWitness,
    SemanticMemoryCapsule,
    SynthesizedMechanismClaim,
)
from news_scalping_lab.inference.thin_daily import ThinDailyAnalyzer
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.utils import KST, canonical_json, read_json, sha256_text


class CountingMockLLM(DeterministicMockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.prompts: dict[str, str] = {}

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[Any],
        purpose: str,
    ) -> Any:
        self.calls.append(purpose)
        self.prompts[purpose] = prompt
        return await super().generate_structured(
            prompt=prompt,
            response_model=response_model,
            purpose=purpose,
        )


class FixtureBrainContextProvider:
    def __init__(self, *, record_count: int = 2) -> None:
        self.calls = 0
        self.record_count = record_count

    async def retrieve(
        self,
        *,
        interpretation: CurrentDayInterpretation,
        current_event_capsules: list[CurrentEventCapsule],
        cutoff_at: datetime,
        max_exact_witnesses: int,
    ) -> DailyBrainContext:
        self.calls += 1
        assert current_event_capsules
        assert max_exact_witnesses == 24
        supporting_id = "REC-support"
        contradicting_id = "REC-contradict"
        available_from = datetime(2025, 12, 31, 18, 0, tzinfo=KST)
        capsule = SemanticMemoryCapsule(
            capsule_id="CAP-fixture",
            category="single_event",
            semantic_unit_id="UNIT-fixture",
            member_record_count=self.record_count,
            member_independent_unit_count=2,
            member_record_root="1" * 64,
            record_type_distribution={"fixture": self.record_count},
            polarity_distribution={"positive": 1, "negative": 1},
            label_quality_distribution={"verified": 2},
            time_distribution={"2025": 2},
            regime_distribution={"unknown": 2},
            event_or_mechanism_summary="A bounded fixture mechanism.",
            economic_transmission=["event -> economic exposure -> market response"],
            applicable_conditions=["novel and economically attributable"],
            failure_conditions=["already absorbed"],
            boundary_conditions=["weak attribution"],
            supporting_record_ids=[supporting_id],
            contradicting_record_ids=[contradicting_id],
            representative_exact_witnesses=[
                ExactWitness(
                    record_id=supporting_id,
                    excerpt="fixture supporting witness",
                    available_from=available_from,
                    provenance_root="2" * 64,
                ),
                ExactWitness(
                    record_id=contradicting_id,
                    excerpt="fixture contradicting witness",
                    available_from=available_from,
                    provenance_root="3" * 64,
                ),
            ],
            available_from=available_from,
            provenance_root="4" * 64,
        )
        claim = SynthesizedMechanismClaim(
            claim_id="MCLAIM-fixture",
            category="single_event",
            statement="Novel attributable events can form a direct candidate path.",
            mechanism="novel event -> attributable exposure -> candidate review",
            conditions=["cutoff-safe"],
            boundary_conditions=["weak relation"],
            failure_modes=["already absorbed"],
            supporting_capsule_ids=[capsule.capsule_id],
            supporting_record_ids=[supporting_id],
            contradicting_record_ids=[contradicting_id],
            source_node_ids=["NODE-fixture"],
            available_from=available_from,
            confidence="medium",
            status="supported",
        )
        return DailyBrainContext(
            brain_version="brain-v2-fixture",
            brain_package_root="5" * 64,
            interpretation_sha256=sha256_text(
                canonical_json(interpretation.model_dump(mode="json"))
            ),
            selected_semantic_capsules=[capsule],
            selected_mechanism_claims=[claim],
            population_statistics=[
                {
                    "population_root": "6" * 64,
                    "member_count": 2,
                    "positive_count": 1,
                    "negative_count": 1,
                }
            ],
            current_vs_history_differences=["fixture current event is newer"],
            unresolved_contradictions=["fixture counterexample remains"],
            exact_witnesses=capsule.representative_exact_witnesses,
            retrieval_query_count=len(interpretation.retrieval_queries),
            index_query_count=2,
            online_full_corpus_scan_count=0,
            future_record_count=0,
        )


def _write_news_csv(path: Path, *, row_count: int) -> Path:
    rows = ["date,time,title,body"]
    for index in range(row_count):
        minute = index % 60
        hour = 6 + (index // 60) % 2
        rows.append(
            f'2026-01-02,{hour:02d}:{minute:02d}:00,"Issuer{index} signed contract {index + 1} KRW",'
            '"opaque filler qwerty zxcvbn"'
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_daily_normal_call_count_is_two_and_uses_brain(tmp_path: Path) -> None:
    news_path = _write_news_csv(tmp_path / "news.csv", row_count=10)
    llm = CountingMockLLM()
    brain = FixtureBrainContextProvider()
    analysis = await ThinDailyAnalyzer(
        Settings(project_root=tmp_path),
        llm=llm,
        brain_context_provider=brain,
    ).analyze(
        news_csv=news_path,
        trade_date=date(2026, 1, 2),
        cutoff_at=datetime(2026, 1, 2, 8, 0, tzinfo=KST),
    )

    assert llm.calls == ["current_day_interpretation", "final_market_decision"]
    assert brain.calls == 1
    assert analysis.context_manifest.logical_llm_call_count == 2
    assert analysis.context_manifest.historical_raw_daily_map_call_count == 0
    assert analysis.context_manifest.daily_import_call_count == 0
    assert analysis.context_manifest.daily_brain_rebuild_call_count == 0
    assert analysis.context_manifest.blind_web_search_call_count == 0
    assert analysis.context_manifest.online_full_corpus_scan_count == 0
    assert analysis.context_manifest.brain_version == "brain-v2-fixture"
    assert any(
        "CAP-fixture" in candidate.semantic_capsule_ids
        for candidate in analysis.blind_prediction.candidates
    )
    assert any(
        "MCLAIM-fixture" in candidate.mechanism_claim_ids
        for candidate in analysis.blind_prediction.candidates
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("row_count", [10, 300])
async def test_daily_llm_call_count_is_independent_of_cluster_count(
    tmp_path: Path,
    row_count: int,
) -> None:
    news_path = _write_news_csv(tmp_path / f"news-{row_count}.csv", row_count=row_count)
    llm = CountingMockLLM()
    analysis = await ThinDailyAnalyzer(
        Settings(project_root=tmp_path),
        llm=llm,
        brain_context_provider=FixtureBrainContextProvider(),
    ).analyze(
        news_csv=news_path,
        trade_date=date(2026, 1, 2),
        cutoff_at=datetime(2026, 1, 2, 8, 0, tzinfo=KST),
    )

    assert len(llm.calls) == 2
    assert analysis.context_manifest.logical_llm_call_count == 2
    assert analysis.context_manifest.material_event_cluster_count >= 1


@pytest.mark.asyncio
async def test_daily_max_call_count_with_repairs_is_four(tmp_path: Path) -> None:
    news_path = _write_news_csv(tmp_path / "news.csv", row_count=2)
    settings = Settings(project_root=tmp_path)
    settings.llm.max_retries = 1
    analysis = await ThinDailyAnalyzer(
        settings,
        llm=CountingMockLLM(),
        brain_context_provider=FixtureBrainContextProvider(),
    ).analyze(
        news_csv=news_path,
        trade_date=date(2026, 1, 2),
        cutoff_at=datetime(2026, 1, 2, 8, 0, tzinfo=KST),
    )

    assert analysis.context_manifest.maximum_live_agent_call_count == 4


@pytest.mark.asyncio
async def test_all_news_rows_have_disposition_and_bodies_are_not_repeated(
    tmp_path: Path,
) -> None:
    news_path = _write_news_csv(tmp_path / "news.csv", row_count=8)
    llm = CountingMockLLM()
    analysis = await ThinDailyAnalyzer(
        Settings(project_root=tmp_path),
        llm=llm,
        brain_context_provider=FixtureBrainContextProvider(),
    ).analyze(
        news_csv=news_path,
        trade_date=date(2026, 1, 2),
        cutoff_at=datetime(2026, 1, 2, 8, 0, tzinfo=KST),
    )

    manifest = analysis.context_manifest
    dispositions = read_json(tmp_path / manifest.row_disposition_artifact)
    assert dispositions["row_count"] == 8
    assert len(dispositions["rows"]) == 8
    assert all(row["cluster_id"] for row in dispositions["rows"])
    assert "opaque filler qwerty zxcvbn" not in llm.prompts[
        "current_day_interpretation"
    ]
    assert "opaque filler qwerty zxcvbn" not in llm.prompts["final_market_decision"]


def test_daily_rejects_more_than_one_repair_retry(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    settings.llm.max_retries = 2
    with pytest.raises(ValueError, match="at most one structured repair"):
        ThinDailyAnalyzer(
            settings,
            llm=CountingMockLLM(),
            brain_context_provider=FixtureBrainContextProvider(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("record_count", [10_000, 823_279])
async def test_daily_llm_call_count_is_independent_of_record_count(
    tmp_path: Path,
    record_count: int,
) -> None:
    llm = CountingMockLLM()
    analysis = await ThinDailyAnalyzer(
        Settings(project_root=tmp_path),
        llm=llm,
        brain_context_provider=FixtureBrainContextProvider(record_count=record_count),
    ).analyze(
        news_csv=_write_news_csv(tmp_path / "news.csv", row_count=3),
        trade_date=date(2026, 1, 2),
        cutoff_at=datetime(2026, 1, 2, 8, 0, tzinfo=KST),
    )

    assert llm.calls == ["current_day_interpretation", "final_market_decision"]
    assert analysis.context_manifest.logical_llm_call_count == 2


def test_no_daily_llm_call_inside_historical_record_or_memory_loop() -> None:
    source = textwrap.dedent(inspect.getsource(ThinDailyAnalyzer.analyze))
    tree = ast.parse(source)
    loop_calls = [
        node
        for loop in ast.walk(tree)
        if isinstance(loop, (ast.For, ast.AsyncFor, ast.While))
        for node in ast.walk(loop)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"generate_structured", "generate_text"}
    ]
    assert loop_calls == []
    assert source.count("self.llm.generate_structured") == 2
    assert "build_runtime_evidence_memos" not in source
