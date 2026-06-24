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
    PathType,
)
from news_scalping_lab.evaluation.evaluator import Evaluator
from news_scalping_lab.prices.mock import MockPriceSource
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, canonical_json, sha256_text, write_json


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
    assert episode.available_from.date() == date(2030, 1, 11)
    assert episode.postmortem is not None
    assert episode.blind_predictions[0].company_name == "EvaluationCandidate"
    assert len(episode.outcome_labels) == 1
    assert {item.source_type for item in episode.provenance} == {
        "sealed_blind_prediction",
        "evaluation_postmortem",
    }

    store.accept(episode.episode_id)
    manifest = BrainCompiler(tmp_path).rebuild(mode="full")
    assert manifest.accepted_episode_count == 1
    assert episode.episode_id in manifest.covered_episode_ids


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
