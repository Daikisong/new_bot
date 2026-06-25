from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    ConfidenceLabel,
    DominantSectorHypothesis,
    OutcomeLabels,
    PathType,
)
from news_scalping_lab.evaluation.evaluator import Evaluator
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.prices.mock import MockPriceSource
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, canonical_json, read_json, sha256_text, write_json


def _sealed_prediction(trade_day: date) -> BlindPrediction:
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    created_at = datetime(2030, 1, 10, 8, 58, 0, tzinfo=KST)
    prediction = BlindPrediction(
        prediction_id="PRED-evaluation-test",
        trade_date=trade_day,
        cutoff_at=cutoff_at,
        created_at=created_at,
        blind_analysis=BlindAnalysis(
            summary="Sealed blind analysis before outcome access.",
            open_world_mechanisms=["current news -> blind candidate -> postmortem later"],
        ),
        candidates=[
            Candidate(
                rank=1,
                ticker="UNKNOWN",
                company_name="EvaluationCandidate",
                path_type=PathType.SINGLE_EVENT,
                thesis="A blind candidate preserved before evaluation.",
                why_now="It appeared before the cutoff.",
                causal_chain=["news", "blind reasoning", "evaluation later"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
            )
        ],
    )
    sealed = prediction.model_copy(update={"sealed_at": created_at, "blind_artifact_sha256": None})
    digest = sha256_text(canonical_json(sealed.model_dump(mode="json")))
    return sealed.model_copy(update={"blind_artifact_sha256": digest})


class MetricsPriceSource:
    source_name = "metrics-test"

    def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
        return []

    def get_snapshot(self, ticker: str, *, as_of: date) -> PriceRecord | None:
        return None

    def get_outcome(self, ticker: str, *, trade_date: date) -> OutcomeLabels:
        outcomes = {
            "T1": OutcomeLabels(
                open_gap_pct=2.0,
                intraday_high_return_pct=29.0,
                upper_limit_touched=True,
                upper_limit_closed=True,
            ),
            "T2": OutcomeLabels(
                open_gap_pct=0.0,
                intraday_high_return_pct=12.0,
                upper_limit_touched=False,
                upper_limit_closed=False,
            ),
            "T3": OutcomeLabels(
                open_gap_pct=None,
                intraday_high_return_pct=4.0,
                upper_limit_touched=False,
                upper_limit_closed=False,
            ),
        }
        return outcomes[ticker]


class UniverseMetricsPriceSource(MetricsPriceSource):
    def get_outcome_universe(self, *, trade_date: date) -> dict[str, OutcomeLabels]:
        return {
            "T1": OutcomeLabels(upper_limit_touched=True, upper_limit_closed=True),
            "T4": OutcomeLabels(upper_limit_touched=True, upper_limit_closed=False),
            "T5": OutcomeLabels(upper_limit_touched=False, upper_limit_closed=False),
        }


def test_evaluate_writes_postmortem_research_episode_available_next_day(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    trade_day = date(2030, 1, 10)
    prediction = _sealed_prediction(trade_day)
    write_json(
        tmp_path / "predictions" / f"{trade_day.isoformat()}.json",
        prediction.model_dump(mode="json"),
    )

    result = Evaluator(tmp_path, price_source=MockPriceSource()).evaluate(trade_date=trade_day)
    store = ResearchStore(tmp_path)
    episodes = store.list_episodes()

    assert result.report_path.exists()
    assert result.episode_path.exists()
    assert len(episodes) == 1
    episode = episodes[0]
    assert result.episode_id == episode.episode_id
    assert episode.trade_date == trade_day
    assert episode.execution_protocol_version == "nslab.exhaustive_news_blind_full_market.v5"
    assert episode.outcome_coverage_status == "PREDICTED_CANDIDATES_ONLY"
    assert episode.eligibility_matrix.forecast_evaluation_eligible is True
    assert episode.eligibility_matrix.direct_supervised_cases_eligible is False
    assert episode.eligibility_matrix.retrospective_memory_eligible is True
    assert episode.eligibility_matrix.brain_eligible is True
    assert "direct_supervised_cases_eligible" in episode.eligibility_matrix.reasons
    assert episode.available_from.date() == date(2030, 1, 11)
    assert episode.postmortem is not None
    assert episode.blind_predictions[0].company_name == "EvaluationCandidate"
    assert len(episode.outcome_labels) == 1
    assert episode.lessons
    assert episode.lessons[0].statement == (
        "Use postmortem lessons only from the next trading day forward."
    )
    assert episode.lessons[0].support_episode_ids == [episode.episode_id]
    assert episode.lessons[0].available_from.date() == date(2030, 1, 11)
    assert episode.lessons[0].provenance[0].source_type == "evaluation_postmortem"
    assert {item.source_type for item in episode.provenance} == {
        "sealed_blind_prediction",
        "evaluation_postmortem",
    }

    store.accept(episode.episode_id)
    manifest = BrainCompiler(tmp_path).rebuild(mode="full")
    claims_text = (tmp_path / "brain" / "current" / "claims.jsonl").read_text(
        encoding="utf-8"
    )
    assert manifest.accepted_episode_count == 1
    assert episode.episode_id in manifest.covered_episode_ids
    assert episode.lessons[0].claim_id in manifest.claim_ids
    assert "Use postmortem lessons only from the next trading day forward." in claims_text


def test_evaluate_defers_friday_postmortem_to_next_weekday(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    trade_day = date(2030, 1, 11)
    assert trade_day.weekday() == 4
    prediction = _sealed_prediction(trade_day)
    write_json(
        tmp_path / "predictions" / f"{trade_day.isoformat()}.json",
        prediction.model_dump(mode="json"),
    )

    result = Evaluator(tmp_path, price_source=MockPriceSource()).evaluate(trade_date=trade_day)
    episode = ResearchStore(tmp_path).get_episode(result.episode_id)

    assert episode.available_from.date() == date(2030, 1, 14)
    assert episode.lessons[0].available_from.date() == date(2030, 1, 14)


def test_evaluate_uses_stable_episode_id_for_same_sealed_prediction(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    trade_day = date(2030, 1, 10)
    prediction = _sealed_prediction(trade_day)
    write_json(
        tmp_path / "predictions" / f"{trade_day.isoformat()}.json",
        prediction.model_dump(mode="json"),
    )

    evaluator = Evaluator(tmp_path, price_source=MockPriceSource())
    first = evaluator.evaluate(trade_date=trade_day)
    second = evaluator.evaluate(trade_date=trade_day)

    assert second.episode_id == first.episode_id
    assert second.episode_path == first.episode_path
    assert [episode.episode_id for episode in ResearchStore(tmp_path).list_episodes()] == [
        first.episode_id
    ]


def test_brain_update_accepts_evaluation_episode_by_trade_date(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    trade_day = date(2030, 1, 10)
    prediction = _sealed_prediction(trade_day)
    write_json(
        tmp_path / "predictions" / f"{trade_day.isoformat()}.json",
        prediction.model_dump(mode="json"),
    )

    result = Evaluator(tmp_path, price_source=MockPriceSource()).evaluate(trade_date=trade_day)
    manifest = BrainCompiler(tmp_path).update(episode_id=trade_day.isoformat())

    assert result.episode_id in manifest.covered_episode_ids
    assert [episode.episode_id for episode in ResearchStore(tmp_path).list_accepted()] == [
        result.episode_id
    ]
    claims_text = (tmp_path / "brain" / "current" / "claims.jsonl").read_text(
        encoding="utf-8"
    )
    assert "Use postmortem lessons only from the next trading day forward." in claims_text


def test_evaluate_writes_performance_metrics_without_faking_recall(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    trade_day = date(2030, 1, 10)
    prediction = _sealed_prediction(trade_day)
    prediction = prediction.model_copy(
        update={
            "candidates": [
                Candidate(
                    rank=1,
                    ticker="T1",
                    company_name="HitCandidate",
                    path_type=PathType.SINGLE_EVENT,
                    thesis="Candidate expected to touch upper limit.",
                    why_now="Pre-cutoff catalyst.",
                    causal_chain=["news", "direct relevance"],
                ),
                Candidate(
                    rank=2,
                    ticker="T2",
                    company_name="HighReturnCandidate",
                    path_type=PathType.THEME_BENEFICIARY,
                    thesis="Candidate expected to move but not upper-limit.",
                    why_now="Pre-cutoff catalyst.",
                    causal_chain=["news", "beneficiary path"],
                ),
                Candidate(
                    rank=3,
                    ticker="T3",
                    company_name="FalsePositiveCandidate",
                    path_type=PathType.CONTINUATION,
                    thesis="Candidate expected to continue.",
                    why_now="D-1 continuation review.",
                    causal_chain=["market memory", "continuation"],
                ),
            ]
        }
    )
    resealed = prediction.model_copy(update={"blind_artifact_sha256": None})
    prediction = resealed.model_copy(
        update={"blind_artifact_sha256": sha256_text(canonical_json(resealed.model_dump(mode="json")))}
    )
    write_json(
        tmp_path / "predictions" / f"{trade_day.isoformat()}.json",
        prediction.model_dump(mode="json"),
    )

    result = Evaluator(tmp_path, price_source=MetricsPriceSource()).evaluate(
        trade_date=trade_day
    )

    metrics = read_json(result.report_path)["performance_metrics"]
    eligibility = read_json(result.report_path)["eligibility_matrix"]
    assert metrics["candidate_count"] == 3
    assert metrics["upper_limit_hits_at_5"] == 1
    assert metrics["upper_limit_hits_at_10"] == 1
    assert metrics["upper_limit_hits_at_20"] == 1
    assert metrics["precision_at_5"] == pytest.approx(1 / 3)
    assert metrics["precision_at_10"] == pytest.approx(1 / 3)
    assert metrics["average_max_return_top_5"] == pytest.approx(15.0)
    assert metrics["gap_up_hit_rate"] == pytest.approx(0.5)
    assert metrics["false_positive_rate"] == pytest.approx(2 / 3)
    assert metrics["high_return_5pct_hit_rate"] == pytest.approx(2 / 3)
    assert metrics["high_return_10pct_hit_rate"] == pytest.approx(2 / 3)
    assert metrics["high_return_15pct_hit_rate"] == pytest.approx(1 / 3)
    assert metrics["high_return_20pct_hit_rate"] == pytest.approx(1 / 3)
    assert metrics["upper_limit_touched_count"] == 1
    assert metrics["upper_limit_closed_count"] == 1
    assert metrics["upper_limit_recall_at_5"] is None
    assert metrics["theme_recall"] is None
    assert metrics["single_event_recall"] is None
    assert metrics["beneficiary_recall"] is None
    assert metrics["continuation_recall"] is None
    assert "universe is unavailable" in metrics["recall_unavailable_reason"]
    assert eligibility["forecast_evaluation_eligible"] is True
    assert eligibility["direct_supervised_cases_eligible"] is True
    assert eligibility["theme_supervised_cases_eligible"] is False
    assert eligibility["leader_pair_training_eligible"] is True
    assert eligibility["retrospective_memory_eligible"] is True
    assert eligibility["brain_eligible"] is True


def test_evaluate_calculates_upper_limit_recall_when_universe_is_available(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    trade_day = date(2030, 1, 10)
    prediction = _sealed_prediction(trade_day)
    prediction = prediction.model_copy(
        update={
            "dominant_sectors": [
                DominantSectorHypothesis(
                    name="Fake direct catalyst group",
                    formation_mechanism="Direct news candidates can form a narrow theme.",
                    expected_breadth="limited",
                )
            ],
            "candidates": [
                Candidate(
                    rank=1,
                    ticker="T1",
                    company_name="HitCandidate",
                    path_type=PathType.SINGLE_EVENT,
                    thesis="Candidate expected to touch upper limit.",
                    why_now="Pre-cutoff catalyst.",
                    causal_chain=["news", "direct relevance"],
                ),
                Candidate(
                    rank=2,
                    ticker="T2",
                    company_name="HighReturnCandidate",
                    path_type=PathType.THEME_BENEFICIARY,
                    thesis="Candidate expected to move but not upper-limit.",
                    why_now="Pre-cutoff catalyst.",
                    causal_chain=["news", "beneficiary path"],
                ),
            ]
        }
    )
    resealed = prediction.model_copy(update={"blind_artifact_sha256": None})
    prediction = resealed.model_copy(
        update={"blind_artifact_sha256": sha256_text(canonical_json(resealed.model_dump(mode="json")))}
    )
    write_json(
        tmp_path / "predictions" / f"{trade_day.isoformat()}.json",
        prediction.model_dump(mode="json"),
    )

    result = Evaluator(tmp_path, price_source=UniverseMetricsPriceSource()).evaluate(
        trade_date=trade_day
    )

    metrics = read_json(result.report_path)["performance_metrics"]
    eligibility = read_json(result.report_path)["eligibility_matrix"]
    postmortem = read_json(result.report_path)["postmortem"]
    assert metrics["upper_limit_recall_at_5"] == pytest.approx(0.5)
    assert metrics["upper_limit_recall_at_10"] == pytest.approx(0.5)
    assert metrics["upper_limit_recall_at_20"] == pytest.approx(0.5)
    assert metrics["single_event_recall"] == pytest.approx(0.5)
    assert metrics["theme_recall"] == pytest.approx(0.0)
    assert metrics["beneficiary_recall"] == pytest.approx(0.0)
    assert metrics["continuation_recall"] == pytest.approx(0.0)
    assert metrics["recall_unavailable_reason"] is None
    assert postmortem["misses"] == ["T4"]
    assert postmortem["failure_codes"] == ["UNKNOWN", "RANKING_MISS"]
    episode = ResearchStore(tmp_path).get_episode(result.episode_id)
    assert episode.outcome_coverage_status == "FULL_MARKET_COMPLETE"
    assert episode.eligibility_matrix.theme_supervised_cases_eligible is True
    assert episode.eligibility_matrix.leader_pair_training_eligible is True
    assert eligibility["theme_supervised_cases_eligible"] is True
    assert episode.misses == ["T4"]
    assert episode.postmortem is not None
    assert episode.postmortem.misses == ["T4"]


def test_evaluate_rejects_unsealed_prediction(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    trade_day = date(2030, 1, 10)
    unsealed = _sealed_prediction(trade_day).model_copy(
        update={"sealed_at": None, "blind_artifact_sha256": None}
    )
    write_json(
        tmp_path / "predictions" / f"{trade_day.isoformat()}.json",
        unsealed.model_dump(mode="json"),
    )

    with pytest.raises(ValueError, match="sealed blind prediction"):
        Evaluator(tmp_path, price_source=MockPriceSource()).evaluate(trade_date=trade_day)
