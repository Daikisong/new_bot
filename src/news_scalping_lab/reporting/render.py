"""Markdown report rendering."""

from __future__ import annotations

from collections.abc import Sequence

from news_scalping_lab.contracts.models import BlindPrediction, Candidate, ContextManifest, PathType


def render_preopen_report(prediction: BlindPrediction, manifest: ContextManifest) -> str:
    candidates = sorted(prediction.candidates, key=lambda candidate: candidate.rank)

    def list_text(values: list[str]) -> str:
        return ", ".join(values) if values else "none"

    def candidate_lines(candidate: Candidate) -> list[str]:
        return [
            f"### {candidate.rank}. {candidate.company_name} ({candidate.ticker})",
            "",
            f"- Path type: `{candidate.path_type}`",
            f"- Event IDs: {list_text(candidate.event_ids)}",
            f"- Confidence: `{candidate.confidence_label}`",
            f"- Evidence quality: `{candidate.evidence_quality}`",
            f"- Thesis: {candidate.thesis}",
            f"- Why now: {candidate.why_now}",
            f"- Causal chain: {list_text(candidate.causal_chain)}",
            f"- Direct evidence: {list_text(candidate.direct_evidence)}",
            f"- Inferred evidence: {list_text(candidate.inferred_evidence)}",
            f"- Market-memory evidence: {list_text(candidate.market_memory_evidence)}",
            f"- Prior positive cases: {list_text(candidate.prior_positive_cases)}",
            f"- Prior negative cases: {list_text(candidate.prior_negative_cases)}",
            f"- Novel reasoning: {candidate.novel_reasoning or 'none recorded'}",
            f"- Counterarguments: {'; '.join(candidate.counterarguments) or 'none recorded'}",
            f"- Disconfirming conditions: {list_text(candidate.disconfirming_conditions)}",
            f"- Memory episodes: {list_text(candidate.memory_episode_ids)}",
            f"- Source URLs: {list_text(candidate.source_urls)}",
            f"- Provenance sources: {list_text([item.source_id for item in candidate.provenance])}",
            "",
        ]

    def candidate_section(rows: list[Candidate]) -> str:
        if not rows:
            return "No candidates in this path.\n"
        lines: list[str] = []
        for candidate in rows:
            lines.extend(candidate_lines(candidate))
        return "\n".join(lines)

    def section_for(path_type: PathType) -> str:
        rows = [candidate for candidate in candidates if candidate.path_type == path_type]
        return candidate_section(rows)

    sector_lines: list[str] = []
    for sector in prediction.dominant_sectors:
        sector_lines.extend(
            [
                f"### {sector.name}",
                "",
                f"- Triggering events: {list_text(sector.triggering_events)}",
                f"- Mechanism: {sector.formation_mechanism}",
                f"- Expected breadth: {sector.expected_breadth}",
                f"- Direct beneficiaries: {list_text(sector.direct_beneficiaries)}",
                f"- Indirect beneficiaries: {list_text(sector.indirect_beneficiaries)}",
                f"- Narrative beneficiaries: {list_text(sector.narrative_beneficiaries)}",
                f"- Possible leaders: {list_text(sector.possible_leaders)}",
                f"- Failure conditions: {'; '.join(sector.failure_conditions) or 'none recorded'}",
                f"- Supporting cases: {list_text(sector.supporting_cases)}",
                f"- Contradicting cases: {list_text(sector.contradicting_cases)}",
                f"- Provenance sources: {list_text([item.source_id for item in sector.provenance])}",
                "",
            ]
        )
    row_summary = manifest.row_disposition_summary

    return "\n".join(
        [
            f"# Pre-Open Research Report: {prediction.trade_date.isoformat()}",
            "",
            "## 1. Execution Info",
            "",
            f"- Run ID: `{manifest.run_id}`",
            f"- Mode: `{manifest.mode}`",
            f"- Cutoff: `{prediction.cutoff_at.isoformat()}`",
            "",
            "## 2. Research Brain Version",
            "",
            f"- Brain version: `{manifest.brain_version or 'none'}`",
            "",
            "## 3. News Range And Cutoff",
            "",
            "Only pre-cutoff news rows are eligible for blind evidence.",
            f"- Total input rows: {row_summary.get('total_rows', 'unknown')}",
            f"- Included pre-cutoff rows: {row_summary.get('included_before_cutoff', 'unknown')}",
            f"- Excluded after-cutoff rows: {row_summary.get('excluded_after_cutoff', 'unknown')}",
            f"- Row coverage ratio: {row_summary.get('coverage_ratio', 'unknown')}",
            f"- Row disposition artifact: {manifest.row_disposition_artifact or 'none'}",
            f"- Row disposition SHA256: {manifest.row_disposition_sha256 or 'none'}",
            "",
            "## 4. Dominant Sector Hypotheses",
            "",
            "\n".join(sector_lines) or "No sector hypotheses.",
            "",
            "## 5. Single-News Upper-Limit Candidates",
            "",
            section_for(PathType.SINGLE_EVENT),
            "## 6. Theme Beneficiary Upper-Limit Candidates",
            "",
            section_for(PathType.THEME_BENEFICIARY),
            "## 7. Prior-Leader Continuation Candidates",
            "",
            section_for(PathType.CONTINUATION),
            "## 8. All Pre-Open Watchlist Candidates",
            "",
            candidate_section(candidates),
            "",
            "## 9. Excluded But Watch",
            "",
            "No automatic exclusions. Red-team objections are retained with each candidate.",
            f"Red-team artifacts: {', '.join(manifest.red_team_artifacts) or 'none'}",
            "",
            "## 10. Key Counterexamples And Uncertainty",
            "",
            "\n".join(f"- {item}" for item in prediction.blind_analysis.initial_uncertainties),
            "",
            "Counterexample episode ids:",
            "",
            "\n".join(f"- {episode_id}" for episode_id in manifest.counterexample_episode_ids)
            or "- none",
            "",
            "Red-team objections passed to synthesis:",
            "",
            "\n".join(f"- {path}" for path in manifest.red_team_artifacts) or "- none",
            "",
            "## 11. Used Past Research Cases",
            "",
            "\n".join(f"- {episode_id}" for episode_id in manifest.swept_episode_ids) or "- none",
            "",
            "Retrieved raw episode ids:",
            "",
            "\n".join(f"- {episode_id}" for episode_id in manifest.retrieved_episode_ids)
            or "- none",
            "",
            "Prior positive cases referenced by candidates:",
            "",
            "\n".join(f"- {case}" for case in _candidate_case_refs(candidates, "prior_positive_cases"))
            or "- none",
            "",
            "Prior negative cases referenced by candidates:",
            "",
            "\n".join(f"- {case}" for case in _candidate_case_refs(candidates, "prior_negative_cases"))
            or "- none",
            "",
            "## 12. Additional Web Sources",
            "",
            "\n".join(f"- {source}" for source in manifest.web_sources)
            or "- mock/no external web sources",
            "",
            "Source ledger:",
            "",
            f"- Artifact: {manifest.source_ledger_artifact or 'none'}",
            f"- SHA256: {manifest.source_ledger_sha256 or 'none'}",
            f"- Entries: {manifest.source_ledger_entry_count}",
            "",
            "Candidate web verification:",
            "",
            f"- Artifact: {manifest.candidate_web_check_artifact or 'none'}",
            f"- SHA256: {manifest.candidate_web_check_sha256 or 'none'}",
            f"- Accepted sources: {manifest.candidate_web_check_count}",
            f"- Accepted source ids: {list_text(manifest.candidate_web_source_ids)}",
            f"- Excluded artifact: {manifest.excluded_candidate_web_check_artifact or 'none'}",
            f"- Excluded SHA256: {manifest.excluded_candidate_web_check_sha256 or 'none'}",
            f"- Excluded sources: {manifest.excluded_candidate_web_check_count}",
            f"- Excluded source ids: {list_text(manifest.excluded_candidate_web_source_ids)}",
            "",
            "Excluded after-cutoff web source ids:",
            "",
            "\n".join(f"- {source_id}" for source_id in manifest.excluded_web_source_ids)
            or "- none",
            "",
            "## 13. Memory Coverage",
            "",
            f"- Accepted episodes: {manifest.accepted_episode_count}",
            f"- Swept episodes: {manifest.swept_episode_count}",
            f"- Memory sweep shards: {manifest.memory_sweep_shard_count}",
            f"- Memory sweep artifacts: {', '.join(manifest.memory_sweep_artifacts) or 'none'}",
            f"- Coverage errors: {'; '.join(manifest.errors) or 'none'}",
            "",
        ]
    )


def _candidate_case_refs(candidates: Sequence[Candidate], field_name: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = getattr(candidate, field_name, None)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, str) or not item or item in seen:
                continue
            seen.add(item)
            refs.append(item)
    return refs
