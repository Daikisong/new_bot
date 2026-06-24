"""Post-close evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from news_scalping_lab.config import Settings, load_settings
from news_scalping_lab.contracts.models import (
    BlindPrediction,
    FailureCode,
    OutcomeLabels,
    Postmortem,
    Provenance,
    ResearchEpisode,
)
from news_scalping_lab.prices.base import PriceSource
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    KST,
    file_sha256,
    next_calendar_day,
    now_kst,
    read_json,
    stable_id,
    write_json,
)
from news_scalping_lab.warehouse import WarehouseStore


@dataclass(frozen=True)
class EvaluationResult:
    report_path: Path
    episode_id: str
    episode_path: Path


class Evaluator:
    def __init__(self, root: Path, price_source: PriceSource | None = None) -> None:
        self.root = root
        settings = load_settings(root)
        self.price_source = price_source or create_price_source(
            settings if settings.project_root == root.resolve() else Settings(project_root=root)
        )

    def evaluate(self, *, trade_date: date) -> EvaluationResult:
        prediction_path = self.root / "predictions" / f"{trade_date.isoformat()}.json"
        if not prediction_path.exists():
            raise FileNotFoundError(f"blind prediction not found: {prediction_path}")
        prediction_data = read_json(prediction_path)
        prediction = BlindPrediction.model_validate(prediction_data)
        if prediction.sealed_at is None or not prediction.blind_artifact_sha256:
            raise ValueError("evaluation requires a sealed blind prediction")
        outcomes: dict[str, object] = {}
        outcome_labels: dict[str, OutcomeLabels] = {}
        hits: list[str] = []
        false_positives: list[str] = []
        for candidate in prediction.candidates:
            ticker = candidate.ticker
            company = candidate.company_name
            outcome = self.price_source.get_outcome(ticker, trade_date=trade_date)
            outcome_key = f"{candidate.rank}:{ticker}:{company}"
            outcomes[company] = outcome.model_dump(mode="json")
            outcome_labels[outcome_key] = outcome
            if outcome.upper_limit_touched:
                hits.append(company)
            else:
                false_positives.append(company)
        postmortem = Postmortem(
            summary="Mock postmortem generated from sealed blind prediction and evaluation-only outcomes.",
            hits=hits,
            misses=[],
            false_positives=false_positives,
            failure_codes=[FailureCode.UNKNOWN] if false_positives else [],
            lessons=[
                "Use postmortem lessons only from the next trading day forward.",
                "Do not rewrite sealed blind reasoning after outcomes are known.",
            ],
        )
        output = {
            "schema_version": "nslab.evaluation.v1",
            "trade_date": trade_date.isoformat(),
            "created_at": now_kst().isoformat(),
            "blind_prediction_id": prediction.prediction_id,
            "outcomes": outcomes,
            "postmortem": postmortem.model_dump(mode="json"),
        }
        target = self.root / "reports" / f"{trade_date.isoformat()}_postmortem.json"
        write_json(target, output)
        episode = self._build_research_episode(
            trade_date=trade_date,
            prediction=prediction,
            prediction_path=prediction_path,
            postmortem_path=target,
            postmortem=postmortem,
            outcome_labels=outcome_labels,
        )
        episode_path = ResearchStore(self.root).save_episode(episode)
        WarehouseStore(self.root).write_daily_outcomes_from_files()
        return EvaluationResult(
            report_path=target,
            episode_id=episode.episode_id,
            episode_path=episode_path,
        )

    def _build_research_episode(
        self,
        *,
        trade_date: date,
        prediction: BlindPrediction,
        prediction_path: Path,
        postmortem_path: Path,
        postmortem: Postmortem,
        outcome_labels: dict[str, OutcomeLabels],
    ) -> ResearchEpisode:
        prediction_hash = file_sha256(prediction_path)
        postmortem_hash = file_sha256(postmortem_path)
        episode_id = stable_id(
            "EP",
            "evaluation",
            trade_date.isoformat(),
            prediction.prediction_id,
            postmortem_hash,
        )
        evaluation_provenance = Provenance(
            source_id=stable_id("SRC", postmortem_path.as_posix(), postmortem_hash),
            source_type="evaluation_postmortem",
            uri=postmortem_path.as_posix(),
            content_sha256=postmortem_hash,
            observed_at=now_kst(),
        )
        prediction_provenance = Provenance(
            source_id=stable_id("SRC", prediction_path.as_posix(), prediction_hash),
            source_type="sealed_blind_prediction",
            uri=prediction_path.as_posix(),
            content_sha256=prediction_hash,
            observed_at=prediction.sealed_at or prediction.created_at,
        )
        available_from = datetime.combine(next_calendar_day(trade_date), time(0, 0, 0), tzinfo=KST)
        return ResearchEpisode(
            episode_id=episode_id,
            trade_date=trade_date,
            cutoff_at=prediction.cutoff_at,
            created_at=now_kst(),
            research_version="evaluation-postmortem-v1",
            input_news_files=[],
            input_news_hashes=[],
            price_source_snapshot={
                "source": self.price_source.source_name,
                "outcome_trade_date": trade_date.isoformat(),
            },
            blind_analysis=prediction.blind_analysis.model_copy(
                update={
                    "provenance": [
                        *prediction.blind_analysis.provenance,
                        prediction_provenance,
                    ]
                }
            ),
            blind_predictions=prediction.candidates,
            outcome_labels=outcome_labels,
            postmortem=postmortem.model_copy(
                update={"provenance": [*postmortem.provenance, evaluation_provenance]}
            ),
            observed_events=[],
            event_ticker_edges=[],
            lessons=[],
            counterexamples=[],
            misses=postmortem.misses,
            provenance=[prediction_provenance, evaluation_provenance],
            available_from=available_from,
        )
