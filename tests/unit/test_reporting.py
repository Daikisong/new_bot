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
                direct_beneficiaries=["DirectReportCo"],
                indirect_beneficiaries=["IndirectReportCo"],
                narrative_beneficiaries=["NarrativeReportCo"],
                possible_leaders=["LeaderReportCo"],
                failure_conditions=["theme fails without breadth"],
                supporting_cases=["EP-sector-positive"],
                contradicting_cases=["EP-sector-negative"],
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
            ),
            Candidate(
                rank=2,
                ticker="UNKNOWN",
                company_name="HybridReportCo",
                path_type=PathType.HYBRID,
                event_ids=["EVT-report"],
                thesis="Hybrid report candidate.",
                why_now="Current catalyst may create both direct and beneficiary paths.",
                causal_chain=["current catalyst", "direct path", "beneficiary path"],
                direct_evidence=["direct path still needs confirmation"],
                inferred_evidence=["beneficiary channel requires verification"],
                market_memory_evidence=["hybrid prior market narrative"],
                prior_positive_cases=["EP-hybrid-positive"],
                prior_negative_cases=["EP-hybrid-negative"],
                novel_reasoning="Hybrid path should not lose detailed report evidence.",
                counterarguments=["hybrid relation may be too diffuse"],
                disconfirming_conditions=["no direct or indirect tie"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
                source_urls=["news://EVT-report-hybrid"],
                memory_episode_ids=["EP-hybrid-positive", "EP-hybrid-negative"],
                provenance=[
                    Provenance(
                        source_id="SRC-report-hybrid",
                        source_type="test",
                        uri="test://hybrid-candidate",
                    )
                ],
            ),
        ],
    )
    manifest = ContextManifest(
        run_id="RUN-report",
        mode="exhaustive",
        trade_date=trade_day,
        cutoff_at=cutoff,
        as_of=cutoff,
        news_window_start_at=datetime(2030, 1, 9, 15, 30, 0, tzinfo=KST),
        news_window_end_at=cutoff,
        brain_version="brain-report",
        accepted_episode_count=2,
        swept_episode_count=2,
        swept_episode_ids=["EP-positive", "EP-negative"],
        retrieved_episode_ids=["EP-positive"],
        counterexample_episode_ids=["EP-negative"],
        row_disposition_artifact="runs/checkpoints/row_disposition/RUN-report/row_disposition.jsonl",
        row_disposition_sha256="row-sha",
        row_disposition_coverage_ratio=0.5,
        row_disposition_summary={
            "total_rows": 2,
            "included_in_news_window": 1,
            "included_before_cutoff": 1,
            "excluded_before_window": 0,
            "excluded_after_cutoff": 1,
            "missing_collected_at": 1,
            "coverage_ratio": 0.5,
        },
        source_ledger_artifact="runs/checkpoints/source_ledger/RUN-report/source_ledger.jsonl",
        source_ledger_sha256="ledger-sha",
        source_ledger_entry_count=3,
        web_sources=["mock://source"],
        candidate_web_check_artifact=(
            "runs/checkpoints/candidate_web_checks/RUN-report/candidate_web_checks.jsonl"
        ),
        candidate_web_check_sha256="candidate-web-sha",
        candidate_web_check_count=2,
        candidate_web_source_ids=["WEB-CANDIDATE-1", "WEB-CANDIDATE-2"],
        excluded_candidate_web_check_artifact=(
            "runs/checkpoints/candidate_web_checks/RUN-report/"
            "excluded_candidate_web_checks.jsonl"
        ),
        excluded_candidate_web_check_sha256="excluded-candidate-web-sha",
        excluded_candidate_web_check_count=1,
        excluded_candidate_web_source_ids=["WEB-CANDIDATE-EXCLUDED"],
        price_snapshot=PriceSnapshot(
            source_name="mock",
            as_of=cutoff,
            allowed_through=date(2030, 1, 9),
        ),
    )

    report = render_preopen_report(prediction, manifest)

    assert "- Causal chain: current catalyst, listed entity verification" in report
    assert "- Path type: `SINGLE_EVENT`" in report
    assert "- Event IDs: EVT-report" in report
    assert "- Direct evidence: direct company mention" in report
    assert "- Inferred evidence: economic attribution check" in report
    assert "- Market-memory evidence: D-1 absorption check" in report
    assert "- Prior positive cases: EP-positive" in report
    assert "- Prior negative cases: EP-negative" in report
    assert "- Disconfirming conditions: not listed" in report
    assert "- Source URLs: news://EVT-report" in report
    assert "- Provenance sources: SRC-report-candidate" in report
    assert "HybridReportCo" in report
    assert "- Path type: `HYBRID`" in report
    assert "- Thesis: Hybrid report candidate." in report
    assert "- Novel reasoning: Hybrid path should not lose detailed report evidence." in report
    assert "- Direct evidence: direct path still needs confirmation" in report
    assert "- Inferred evidence: beneficiary channel requires verification" in report
    assert "- Market-memory evidence: hybrid prior market narrative" in report
    assert "- Counterarguments: hybrid relation may be too diffuse" in report
    assert "- Disconfirming conditions: no direct or indirect tie" in report
    assert "- Memory episodes: EP-hybrid-positive, EP-hybrid-negative" in report
    assert "- Source URLs: news://EVT-report-hybrid" in report
    assert "- Provenance sources: SRC-report-hybrid" in report
    assert "- Triggering events: EVT-report" in report
    assert "- Direct beneficiaries: DirectReportCo" in report
    assert "- Indirect beneficiaries: IndirectReportCo" in report
    assert "- Narrative beneficiaries: NarrativeReportCo" in report
    assert "- Possible leaders: LeaderReportCo" in report
    assert "- Failure conditions: theme fails without breadth" in report
    assert "- Supporting cases: EP-sector-positive" in report
    assert "- Contradicting cases: EP-sector-negative" in report
    assert "- Provenance sources: SRC-report-sector" in report
    assert "- Total input rows: 2" in report
    assert "- News window start: 2030-01-09T15:30:00+09:00" in report
    assert "- News window end: 2030-01-10T08:59:59+09:00" in report
    assert "- Included news-window rows: 1" in report
    assert "- Excluded before-window rows: 0" in report
    assert "- Excluded after-cutoff rows: 1" in report
    assert "- Rows missing collected_at: 1" in report
    assert "- Row coverage ratio: 0.5" in report
    assert (
        "- Row disposition artifact: runs/checkpoints/row_disposition/RUN-report/row_disposition.jsonl"
        in report
    )
    assert "- Row disposition SHA256: row-sha" in report
    assert "Source ledger:" in report
    assert "- Artifact: runs/checkpoints/source_ledger/RUN-report/source_ledger.jsonl" in report
    assert "- SHA256: ledger-sha" in report
    assert "- Entries: 3" in report
    assert "Candidate web verification:" in report
    assert (
        "- Artifact: runs/checkpoints/candidate_web_checks/RUN-report/"
        "candidate_web_checks.jsonl"
        in report
    )
    assert "- SHA256: candidate-web-sha" in report
    assert "- Accepted sources: 2" in report
    assert "- Accepted source ids: WEB-CANDIDATE-1, WEB-CANDIDATE-2" in report
    assert (
        "- Excluded artifact: runs/checkpoints/candidate_web_checks/RUN-report/"
        "excluded_candidate_web_checks.jsonl"
        in report
    )
    assert "- Excluded SHA256: excluded-candidate-web-sha" in report
    assert "- Excluded sources: 1" in report
    assert "- Excluded source ids: WEB-CANDIDATE-EXCLUDED" in report
    assert "Counterexample episode ids:" in report
    assert "Prior positive cases referenced by candidates:" in report
    assert "Prior negative cases referenced by candidates:" in report
