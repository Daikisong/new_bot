"""Post-close evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from news_scalping_lab.config import Settings, load_settings
from news_scalping_lab.contracts.models import (
    BlindPrediction,
    Candidate,
    ClaimStatus,
    ConfidenceLabel,
    EligibilityMatrix,
    EvaluationMetrics,
    FailureCode,
    MemoryClaim,
    OutcomeLabels,
    PathType,
    Postmortem,
    Provenance,
    ResearchEpisode,
)
from news_scalping_lab.prices.base import OutcomeUniversePriceSource, PriceSource
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    KST,
    file_sha256,
    next_trading_day,
    now_kst,
    read_json,
    relative_to_root,
    stable_id,
    write_json,
)
from news_scalping_lab.warehouse import WarehouseStore

EVALUATION_PROTOCOL_VERSION = "nslab.exhaustive_news_blind_full_market.v5"


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
        ranked_outcomes: list[tuple[Candidate, OutcomeLabels]] = []
        hits: list[str] = []
        false_positives: list[str] = []
        for candidate in sorted(prediction.candidates, key=lambda item: item.rank):
            ticker = candidate.ticker
            company = candidate.company_name
            outcome = self.price_source.get_outcome(ticker, trade_date=trade_date)
            outcome_key = f"{candidate.rank}:{ticker}:{company}"
            outcomes[company] = outcome.model_dump(mode="json")
            outcome_labels[outcome_key] = outcome
            ranked_outcomes.append((candidate, outcome))
            if outcome.upper_limit_touched:
                hits.append(company)
            else:
                false_positives.append(company)
        outcome_universe = _load_outcome_universe(self.price_source, trade_date=trade_date)
        metrics = _build_metrics(ranked_outcomes, outcome_universe=outcome_universe)
        outcome_coverage_status = _outcome_coverage_status(outcome_universe)
        misses = _upper_limit_misses(prediction.candidates, outcome_universe)
        postmortem = Postmortem(
            summary="Mock postmortem generated from sealed blind prediction and evaluation-only outcomes.",
            hits=hits,
            misses=misses,
            false_positives=false_positives,
            failure_codes=_failure_codes(false_positives=false_positives, misses=misses),
            lessons=[
                "Use postmortem lessons only from the next trading day forward.",
                "Do not rewrite sealed blind reasoning after outcomes are known.",
            ],
        )
        eligibility_matrix = _build_eligibility_matrix(
            prediction=prediction,
            ranked_outcomes=ranked_outcomes,
            outcome_universe=outcome_universe,
            metrics=metrics,
            postmortem=postmortem,
            trade_date=trade_date,
        )
        output = {
            "schema_version": "nslab.evaluation.v1",
            "execution_protocol_version": EVALUATION_PROTOCOL_VERSION,
            "trade_date": trade_date.isoformat(),
            "created_at": now_kst().isoformat(),
            "blind_prediction_id": prediction.prediction_id,
            "outcome_coverage_status": outcome_coverage_status,
            "outcomes": outcomes,
            "performance_metrics": metrics.model_dump(mode="json"),
            "postmortem": postmortem.model_dump(mode="json"),
            "eligibility_matrix": eligibility_matrix.model_dump(mode="json"),
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
            eligibility_matrix=eligibility_matrix,
            outcome_coverage_status=outcome_coverage_status,
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
        eligibility_matrix: EligibilityMatrix,
        outcome_coverage_status: str,
    ) -> ResearchEpisode:
        episode_id = stable_id(
            "EP",
            "evaluation",
            trade_date.isoformat(),
            prediction.prediction_id,
        )
        prediction_snapshot_path, postmortem_snapshot_path = (
            self._write_evaluation_source_snapshots(
                episode_id=episode_id,
                prediction_path=prediction_path,
                postmortem_path=postmortem_path,
            )
        )
        prediction_hash = file_sha256(prediction_snapshot_path)
        postmortem_hash = file_sha256(postmortem_snapshot_path)
        prediction_uri = relative_to_root(prediction_snapshot_path, self.root)
        postmortem_uri = relative_to_root(postmortem_snapshot_path, self.root)
        evaluation_provenance = Provenance(
            source_id=stable_id("SRC", postmortem_uri, postmortem_hash),
            source_type="evaluation_postmortem",
            uri=postmortem_uri,
            content_sha256=postmortem_hash,
            observed_at=now_kst(),
        )
        prediction_provenance = Provenance(
            source_id=stable_id("SRC", prediction_uri, prediction_hash),
            source_type="sealed_blind_prediction",
            uri=prediction_uri,
            content_sha256=prediction_hash,
            observed_at=prediction.sealed_at or prediction.created_at,
        )
        available_from = datetime.combine(next_trading_day(trade_date), time(0, 0, 0), tzinfo=KST)
        postmortem_with_provenance = postmortem.model_copy(
            update={"provenance": [*postmortem.provenance, evaluation_provenance]}
        )
        lesson_claims = _postmortem_lesson_claims(
            episode_id=episode_id,
            trade_date=trade_date,
            available_from=available_from,
            postmortem=postmortem_with_provenance,
            provenance=evaluation_provenance,
        )
        return ResearchEpisode(
            episode_id=episode_id,
            trade_date=trade_date,
            cutoff_at=prediction.cutoff_at,
            created_at=now_kst(),
            execution_protocol_version=EVALUATION_PROTOCOL_VERSION,
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
            postmortem=postmortem_with_provenance,
            observed_events=[],
            event_ticker_edges=[],
            lessons=lesson_claims,
            counterexamples=[],
            misses=postmortem.misses,
            eligibility_matrix=eligibility_matrix,
            outcome_coverage_status=outcome_coverage_status,
            provenance=[prediction_provenance, evaluation_provenance],
            available_from=available_from,
        )

    def _write_evaluation_source_snapshots(
        self,
        *,
        episode_id: str,
        prediction_path: Path,
        postmortem_path: Path,
    ) -> tuple[Path, Path]:
        snapshot_dir = self.root / "runs" / "checkpoints" / "evaluations" / episode_id
        prediction_snapshot_path = snapshot_dir / "sealed_blind_prediction.json"
        postmortem_snapshot_path = snapshot_dir / "postmortem_report.json"
        write_json(prediction_snapshot_path, read_json(prediction_path))
        write_json(postmortem_snapshot_path, read_json(postmortem_path))
        return prediction_snapshot_path, postmortem_snapshot_path


def _build_metrics(
    ranked_outcomes: list[tuple[Candidate, OutcomeLabels]],
    *,
    outcome_universe: dict[str, OutcomeLabels] | None = None,
) -> EvaluationMetrics:
    candidate_count = len(ranked_outcomes)
    upper_limit_hits_at_5 = _upper_limit_hits_at(ranked_outcomes, 5)
    upper_limit_hits_at_10 = _upper_limit_hits_at(ranked_outcomes, 10)
    upper_limit_hits_at_20 = _upper_limit_hits_at(ranked_outcomes, 20)
    recall_unavailable_reason = None
    if outcome_universe is None:
        recall_unavailable_reason = (
            "Daily market outcome universe is unavailable; recall requires all "
            "upper-limit and high-return symbols, not only predicted candidates."
        )
    elif not _upper_limit_universe_tickers(outcome_universe):
        recall_unavailable_reason = "Daily market outcome universe has no upper-limit symbols."
    return EvaluationMetrics(
        candidate_count=candidate_count,
        upper_limit_hits_at_5=upper_limit_hits_at_5,
        upper_limit_hits_at_10=upper_limit_hits_at_10,
        upper_limit_hits_at_20=upper_limit_hits_at_20,
        upper_limit_recall_at_5=_upper_limit_recall_at(
            ranked_outcomes, outcome_universe, 5
        ),
        upper_limit_recall_at_10=_upper_limit_recall_at(
            ranked_outcomes, outcome_universe, 10
        ),
        upper_limit_recall_at_20=_upper_limit_recall_at(
            ranked_outcomes, outcome_universe, 20
        ),
        recall_unavailable_reason=recall_unavailable_reason,
        precision_at_5=_rate(upper_limit_hits_at_5, min(5, candidate_count)),
        precision_at_10=_rate(upper_limit_hits_at_10, min(10, candidate_count)),
        theme_recall=_path_recall(
            ranked_outcomes,
            outcome_universe,
            path_types={PathType.THEME_BENEFICIARY, PathType.HYBRID},
        ),
        single_event_recall=_path_recall(
            ranked_outcomes,
            outcome_universe,
            path_types={PathType.SINGLE_EVENT, PathType.HYBRID},
        ),
        beneficiary_recall=_path_recall(
            ranked_outcomes,
            outcome_universe,
            path_types={PathType.THEME_BENEFICIARY, PathType.HYBRID},
        ),
        continuation_recall=_path_recall(
            ranked_outcomes,
            outcome_universe,
            path_types={PathType.CONTINUATION, PathType.HYBRID},
        ),
        average_max_return_top_5=_average_high_return_at(ranked_outcomes, 5),
        average_max_return_top_10=_average_high_return_at(ranked_outcomes, 10),
        average_max_return_top_20=_average_high_return_at(ranked_outcomes, 20),
        gap_up_hit_rate=_gap_up_hit_rate(ranked_outcomes),
        false_positive_rate=_rate(
            sum(1 for _, outcome in ranked_outcomes if not outcome.upper_limit_touched),
            candidate_count,
        ),
        high_return_5pct_hit_rate=_high_return_hit_rate(ranked_outcomes, 5.0),
        high_return_10pct_hit_rate=_high_return_hit_rate(ranked_outcomes, 10.0),
        high_return_15pct_hit_rate=_high_return_hit_rate(ranked_outcomes, 15.0),
        high_return_20pct_hit_rate=_high_return_hit_rate(ranked_outcomes, 20.0),
        upper_limit_touched_count=sum(
            1 for _, outcome in ranked_outcomes if outcome.upper_limit_touched
        ),
        upper_limit_closed_count=sum(
            1 for _, outcome in ranked_outcomes if outcome.upper_limit_closed
        ),
    )


def _postmortem_lesson_claims(
    *,
    episode_id: str,
    trade_date: date,
    available_from: datetime,
    postmortem: Postmortem,
    provenance: Provenance,
) -> list[MemoryClaim]:
    claims: list[MemoryClaim] = []
    for index, lesson in enumerate(postmortem.lessons, start=1):
        claims.append(
            MemoryClaim(
                claim_id=stable_id("CL", "evaluation", episode_id, str(index), lesson),
                statement=lesson,
                mechanism="postmortem learning from sealed blind prediction and evaluation-only outcomes",
                scope="postmortem evaluation learning",
                conditions=[
                    "available only after the evaluated trade date",
                    "compare against the sealed blind prediction before reuse",
                ],
                failure_modes=[str(code) for code in postmortem.failure_codes]
                or ["none recorded"],
                support_episode_ids=[episode_id],
                contradiction_episode_ids=[],
                near_miss_episode_ids=postmortem.misses,
                status=ClaimStatus.TENTATIVE,
                confidence_label=ConfidenceLabel.MEDIUM,
                first_observed_at=trade_date,
                last_updated_at=provenance.observed_at or available_from,
                available_from=available_from,
                provenance=[provenance],
            )
        )
    return claims


def _build_eligibility_matrix(
    *,
    prediction: BlindPrediction,
    ranked_outcomes: list[tuple[Candidate, OutcomeLabels]],
    outcome_universe: dict[str, OutcomeLabels] | None,
    metrics: EvaluationMetrics,
    postmortem: Postmortem,
    trade_date: date,
) -> EligibilityMatrix:
    sealed_blind = prediction.sealed_at is not None and bool(prediction.blind_artifact_sha256)
    candidate_outcomes_available = bool(ranked_outcomes) and all(
        not _outcome_unavailable(outcome) for _candidate, outcome in ranked_outcomes
    )
    resolved_candidate_pool = [
        candidate
        for candidate, _outcome in ranked_outcomes
        if candidate.ticker.strip() and candidate.ticker.strip().upper() != "UNKNOWN"
    ]
    full_market_complete = outcome_universe is not None
    has_theme_hypothesis = bool(prediction.dominant_sectors)
    forecast_evaluation_eligible = sealed_blind
    direct_supervised_cases_eligible = (
        sealed_blind and candidate_outcomes_available and bool(resolved_candidate_pool)
    )
    theme_supervised_cases_eligible = (
        sealed_blind and full_market_complete and has_theme_hypothesis
    )
    leader_pair_training_eligible = (
        sealed_blind and candidate_outcomes_available and len(resolved_candidate_pool) >= 2
    )
    retrospective_memory_eligible = (
        sealed_blind
        and bool(postmortem.lessons)
        and candidate_outcomes_available
        and prediction.trade_date == trade_date
    )
    brain_eligible = retrospective_memory_eligible
    reasons: dict[str, str] = {}
    if not forecast_evaluation_eligible:
        reasons["forecast_evaluation_eligible"] = "sealed blind prediction is missing"
    if not direct_supervised_cases_eligible:
        reasons["direct_supervised_cases_eligible"] = (
            "resolved candidate D-day outcomes are unavailable or blind prediction is unsealed"
        )
    if not theme_supervised_cases_eligible:
        if not full_market_complete:
            reasons["theme_supervised_cases_eligible"] = (
                metrics.recall_unavailable_reason
                or "full-market outcome universe is unavailable"
            )
        elif not has_theme_hypothesis:
            reasons["theme_supervised_cases_eligible"] = (
                "blind prediction has no dominant sector hypothesis"
            )
        else:
            reasons["theme_supervised_cases_eligible"] = "sealed blind prediction is missing"
    if not leader_pair_training_eligible:
        reasons["leader_pair_training_eligible"] = (
            "need at least two resolved blind candidates with D-day outcomes"
        )
    if not retrospective_memory_eligible:
        reasons["retrospective_memory_eligible"] = (
            "postmortem lessons require sealed blind prediction and candidate outcomes"
        )
    if not brain_eligible:
        reasons["brain_eligible"] = "retrospective memory is not eligible for future brain use"
    return EligibilityMatrix(
        forecast_evaluation_eligible=forecast_evaluation_eligible,
        direct_supervised_cases_eligible=direct_supervised_cases_eligible,
        theme_supervised_cases_eligible=theme_supervised_cases_eligible,
        leader_pair_training_eligible=leader_pair_training_eligible,
        retrospective_memory_eligible=retrospective_memory_eligible,
        brain_eligible=brain_eligible,
        reasons=reasons,
    )


def _outcome_coverage_status(outcome_universe: dict[str, OutcomeLabels] | None) -> str:
    return "FULL_MARKET_COMPLETE" if outcome_universe is not None else "PREDICTED_CANDIDATES_ONLY"


def _outcome_unavailable(outcome: OutcomeLabels) -> bool:
    return "PRICE_UNAVAILABLE" in outcome.flags


def _upper_limit_hits_at(
    ranked_outcomes: list[tuple[Candidate, OutcomeLabels]], limit: int
) -> int:
    return sum(1 for _, outcome in ranked_outcomes[:limit] if outcome.upper_limit_touched)


def _load_outcome_universe(
    price_source: PriceSource,
    *,
    trade_date: date,
) -> dict[str, OutcomeLabels] | None:
    if not isinstance(price_source, OutcomeUniversePriceSource):
        return None
    return price_source.get_outcome_universe(trade_date=trade_date)


def _upper_limit_recall_at(
    ranked_outcomes: list[tuple[Candidate, OutcomeLabels]],
    outcome_universe: dict[str, OutcomeLabels] | None,
    limit: int,
) -> float | None:
    if outcome_universe is None:
        return None
    universe_tickers = _upper_limit_universe_tickers(outcome_universe)
    if not universe_tickers:
        return None
    predicted_tickers = {
        candidate.ticker
        for candidate, outcome in ranked_outcomes[:limit]
        if outcome.upper_limit_touched and candidate.ticker in universe_tickers
    }
    return len(predicted_tickers) / len(universe_tickers)


def _upper_limit_universe_tickers(outcome_universe: dict[str, OutcomeLabels]) -> set[str]:
    return {
        ticker
        for ticker, outcome in outcome_universe.items()
        if outcome.upper_limit_touched is True
    }


def _path_recall(
    ranked_outcomes: list[tuple[Candidate, OutcomeLabels]],
    outcome_universe: dict[str, OutcomeLabels] | None,
    *,
    path_types: set[PathType],
) -> float | None:
    if outcome_universe is None:
        return None
    universe_tickers = _upper_limit_universe_tickers(outcome_universe)
    if not universe_tickers:
        return None
    predicted_tickers = {
        candidate.ticker
        for candidate, outcome in ranked_outcomes
        if candidate.path_type in path_types
        and outcome.upper_limit_touched
        and candidate.ticker in universe_tickers
    }
    return len(predicted_tickers) / len(universe_tickers)


def _upper_limit_misses(
    candidates: list[Candidate],
    outcome_universe: dict[str, OutcomeLabels] | None,
) -> list[str]:
    if outcome_universe is None:
        return []
    predicted_tickers = {candidate.ticker for candidate in candidates}
    return sorted(_upper_limit_universe_tickers(outcome_universe) - predicted_tickers)


def _failure_codes(*, false_positives: list[str], misses: list[str]) -> list[FailureCode]:
    codes: list[FailureCode] = []
    if false_positives:
        codes.append(FailureCode.UNKNOWN)
    if misses:
        codes.append(FailureCode.RANKING_MISS)
    return codes


def _average_high_return_at(
    ranked_outcomes: list[tuple[Candidate, OutcomeLabels]], limit: int
) -> float | None:
    values = [
        outcome.intraday_high_return_pct
        for _, outcome in ranked_outcomes[:limit]
        if outcome.intraday_high_return_pct is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _gap_up_hit_rate(ranked_outcomes: list[tuple[Candidate, OutcomeLabels]]) -> float | None:
    values = [
        outcome.open_gap_pct
        for _, outcome in ranked_outcomes
        if outcome.open_gap_pct is not None
    ]
    if not values:
        return None
    return sum(1 for value in values if value > 0) / len(values)


def _high_return_hit_rate(
    ranked_outcomes: list[tuple[Candidate, OutcomeLabels]], threshold: float
) -> float | None:
    values = [
        outcome.intraday_high_return_pct
        for _, outcome in ranked_outcomes
        if outcome.intraday_high_return_pct is not None
    ]
    if not values:
        return None
    return sum(1 for value in values if value >= threshold) / len(values)


def _rate(count: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return count / denominator
