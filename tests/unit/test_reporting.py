from __future__ import annotations

from datetime import date, datetime

from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    ConfidenceLabel,
    ContextManifest,
    DominantSectorHypothesis,
    PathType,
    PriceSnapshot,
    Provenance,
)
from news_scalping_lab.reporting.render import render_preopen_report
from news_scalping_lab.utils import KST


def test_preopen_report_surfaces_candidate_evidence_and_past_cases() -> None:
    trade_day = date(2030, 1, 10)
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    prediction = BlindPrediction(
        prediction_id="PRED-report",
        trade_date=trade_day,
        cutoff_at=cutoff,
        created_at=cutoff,
        blind_analysis=BlindAnalysis(
            summary="Report evidence summary.",
            initial_uncertainties=["directness may fail"],
        ),
        dominant_sectors=[
            DominantSectorHypothesis(
                name="Report sector",
                triggering_events=["EVT-report"],
                formation_mechanism="current catalyst -> sector hypothesis",
                expected_breadth="narrow",
                provenance=[
                    Provenance(
                        source_id="SRC-report-sector",
                        source_type="test",
                        uri="test://sector",
                    )
                ],
            )
        ],
        candidates=[
            Candidate(
                rank=1,
                ticker="UNKNOWN",
                company_name="ReportCo",
                path_type=PathType.SINGLE_EVENT,
                event_ids=["EVT-report"],
                thesis="Direct report candidate.",
                why_now="Current catalyst is pre-cutoff.",
                causal_chain=["current catalyst", "listed entity verification"],
                direct_evidence=["direct company mention"],
                inferred_evidence=["economic attribution check"],
                market_memory_evidence=["D-1 absorption check"],
                prior_positive_cases=["EP-positive"],
                prior_negative_cases=["EP-negative"],
                counterarguments=["could be already reflected"],
                disconfirming_conditions=["not listed"],
                confidence_label=ConfidenceLabel.LOW,
                evidence_quality=ConfidenceLabel.MEDIUM,
                source_urls=["news://EVT-report"],
                memory_episode_ids=["EP-positive", "EP-negative"],
                provenance=[
                    Provenance(
                        source_id="SRC-report-candidate",
                        source_type="test",
                        uri="test://candidate",
                    )
                ],
            )
        ],
    )
    manifest = ContextManifest(
        run_id="RUN-report",
        mode="exhaustive",
        trade_date=trade_day,
        cutoff_at=cutoff,
        brain_version="brain-report",
        accepted_episode_count=2,
        swept_episode_count=2,
        swept_episode_ids=["EP-positive", "EP-negative"],
        retrieved_episode_ids=["EP-positive"],
        counterexample_episode_ids=["EP-negative"],
        web_sources=["mock://source"],
        price_snapshot=PriceSnapshot(source_name="mock", allowed_through=date(2030, 1, 9)),
    )

    report = render_preopen_report(prediction, manifest)

    assert "- Causal chain: current catalyst, listed entity verification" in report
    assert "- Direct evidence: direct company mention" in report
    assert "- Inferred evidence: economic attribution check" in report
    assert "- Market-memory evidence: D-1 absorption check" in report
    assert "- Prior positive cases: EP-positive" in report
    assert "- Prior negative cases: EP-negative" in report
    assert "- Disconfirming conditions: not listed" in report
    assert "- Source URLs: news://EVT-report" in report
    assert "- Provenance sources: SRC-report-candidate" in report
    assert "- Provenance sources: SRC-report-sector" in report
    assert "Counterexample episode ids:" in report
    assert "Prior positive cases referenced by candidates:" in report
    assert "Prior negative cases referenced by candidates:" in report
