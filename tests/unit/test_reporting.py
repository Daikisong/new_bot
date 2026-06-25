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
from news_scalping_lab.reporting.sections import inspect_preopen_report_sections
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
        event_cluster_artifact=(
            "runs/checkpoints/event_clusters/RUN-report/event_clusters.jsonl"
        ),
        event_cluster_sha256="event-cluster-sha",
        event_cluster_count=1,
        event_cluster_summary={"exact_duplicate_count": 0},
        news_novelty_review_artifact=(
            "runs/checkpoints/news_novelty_reviews/RUN-report/news_novelty_review.json"
        ),
        news_novelty_review_sha256="novelty-review-sha",
        news_novelty_review_count=1,
        news_novelty_review_summary={
            "novelty_counts": {"unclear": 1},
            "time_verified_count": 1,
        },
        semantic_retrieval_plan_artifact=(
            "runs/checkpoints/semantic_retrieval/RUN-report/semantic_retrieval_plan.json"
        ),
        semantic_retrieval_plan_sha256="semantic-plan-sha",
        semantic_retrieval_query_count=6,
        semantic_retrieval_artifact=(
            "runs/checkpoints/semantic_retrieval/RUN-report/semantic_retrieval.jsonl"
        ),
        semantic_retrieval_sha256="semantic-result-sha",
        semantic_retrieval_episode_ids=["EP-semantic-positive"],
        excluded_semantic_retrieval_episode_ids=["EP-semantic-future"],
        candidate_expansion_artifact=(
            "runs/checkpoints/candidate_expansion/RUN-report/candidate_expansion.json"
        ),
        candidate_expansion_sha256="candidate-expansion-sha",
        candidate_expansion_count=4,
        candidate_expansion_summary={
            "path_counts": {
                "SINGLE_EVENT": 1,
                "THEME_FORMATION": 1,
                "BENEFICIARY_DISCOVERY": 1,
                "CONTINUATION": 1,
            },
            "continuation_d_minus_one_only_verified": True,
        },
        source_ledger_artifact="runs/checkpoints/source_ledger/RUN-report/source_ledger.jsonl",
        source_ledger_sha256="ledger-sha",
        source_ledger_entry_count=3,
        red_team_artifacts=["runs/checkpoints/red_team/RUN-report.json"],
        red_team_summary={
            "required_attack_check_count": 10,
            "all_findings_passed_to_synthesis": True,
        },
        web_sources=["mock://source"],
        candidate_web_check_artifact=(
            "runs/checkpoints/candidate_web_checks/RUN-report/candidate_web_checks.jsonl"
        ),
        candidate_web_check_sha256="candidate-web-sha",
        candidate_web_check_count=2,
        candidate_web_check_summary={
            "subject_count": 6,
            "candidate_expansion_subject_count": 4,
        },
        candidate_web_source_ids=["WEB-CANDIDATE-1", "WEB-CANDIDATE-2"],
        candidate_verification_artifact=(
            "runs/checkpoints/candidate_verifications/RUN-report/"
            "candidate_verification.json"
        ),
        candidate_verification_sha256="candidate-verification-sha",
        candidate_verification_count=6,
        candidate_verification_summary={
            "status_counts": {
                "source_collected": 12,
                "needs_company_discovery": 4,
            },
        },
        final_synthesis_context_artifact=(
            "runs/checkpoints/final_synthesis_context/RUN-report/"
            "final_synthesis_context.json"
        ),
        final_synthesis_context_sha256="final-synthesis-context-sha",
        final_synthesis_context_summary={
            "required_input_count": 20,
            "current_news_count": 1,
            "candidate_count": 2,
            "red_team_finding_count": 2,
        },
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

    assert inspect_preopen_report_sections(report) == {
        "required_count": 13,
        "present_count": 13,
        "missing": [],
        "ordered": True,
        "passed": True,
    }
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
    assert "Red-team artifacts: runs/checkpoints/red_team/RUN-report.json" in report
    assert "- Required attack checks: 10" in report
    assert "- All passed to synthesis: True" in report
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
    assert "News event clusters:" in report
    assert (
        "- Artifact: runs/checkpoints/event_clusters/RUN-report/event_clusters.jsonl"
        in report
    )
    assert "- SHA256: event-cluster-sha" in report
    assert "- Clusters: 1" in report
    assert "- Exact duplicates: 0" in report
    assert "News novelty review:" in report
    assert (
        "- Artifact: runs/checkpoints/news_novelty_reviews/RUN-report/"
        "news_novelty_review.json"
        in report
    )
    assert "- SHA256: novelty-review-sha" in report
    assert "- Reviewed clusters: 1" in report
    assert "- Novelty counts: {'unclear': 1}" in report
    assert "- Time-verified findings: 1" in report
    assert "Semantic retrieval:" in report
    assert (
        "- Plan artifact: runs/checkpoints/semantic_retrieval/RUN-report/"
        "semantic_retrieval_plan.json"
        in report
    )
    assert "- Plan SHA256: semantic-plan-sha" in report
    assert (
        "- Result artifact: runs/checkpoints/semantic_retrieval/RUN-report/"
        "semantic_retrieval.jsonl"
        in report
    )
    assert "- Result SHA256: semantic-result-sha" in report
    assert "- Queries: 6" in report
    assert "- Retrieved episodes: EP-semantic-positive" in report
    assert "- Excluded semantic episodes: EP-semantic-future" in report
    assert "Candidate expansion:" in report
    assert (
        "- Artifact: runs/checkpoints/candidate_expansion/RUN-report/"
        "candidate_expansion.json"
        in report
    )
    assert "- SHA256: candidate-expansion-sha" in report
    assert "- Findings: 4" in report
    assert (
        "- Path counts: {'SINGLE_EVENT': 1, 'THEME_FORMATION': 1, "
        "'BENEFICIARY_DISCOVERY': 1, 'CONTINUATION': 1}"
        in report
    )
    assert "- Continuation D-1 only: True" in report
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
    assert "- Subjects: 6" in report
    assert "- Expansion subjects: 4" in report
    assert "- Accepted source ids: WEB-CANDIDATE-1, WEB-CANDIDATE-2" in report
    assert (
        "- Excluded artifact: runs/checkpoints/candidate_web_checks/RUN-report/"
        "excluded_candidate_web_checks.jsonl"
        in report
    )
    assert "- Excluded SHA256: excluded-candidate-web-sha" in report
    assert "- Excluded sources: 1" in report
    assert "- Excluded source ids: WEB-CANDIDATE-EXCLUDED" in report
    assert (
        "- Verification artifact: runs/checkpoints/candidate_verifications/RUN-report/"
        "candidate_verification.json"
        in report
    )
    assert "- Verification SHA256: candidate-verification-sha" in report
    assert "- Verification subjects: 6" in report
    assert (
        "- Verification status counts: {'source_collected': 12, "
        "'needs_company_discovery': 4}"
        in report
    )
    assert "Final synthesis context:" in report
    assert (
        "- Artifact: runs/checkpoints/final_synthesis_context/RUN-report/"
        "final_synthesis_context.json"
        in report
    )
    assert "- SHA256: final-synthesis-context-sha" in report
    assert (
        "- Summary: {'required_input_count': 20, 'current_news_count': 1, "
        "'candidate_count': 2, 'red_team_finding_count': 2}"
        in report
    )
    assert "Counterexample episode ids:" in report
    assert "Prior positive cases referenced by candidates:" in report
    assert "Prior negative cases referenced by candidates:" in report
