"""Markdown report rendering."""

from __future__ import annotations

from news_scalping_lab.contracts.models import BlindPrediction, ContextManifest, PathType


def render_preopen_report(prediction: BlindPrediction, manifest: ContextManifest) -> str:
    candidates = sorted(prediction.candidates, key=lambda candidate: candidate.rank)

    def section_for(path_type: PathType) -> str:
        rows = [candidate for candidate in candidates if candidate.path_type == path_type]
        if not rows:
            return "No candidates in this path.\n"
        lines: list[str] = []
        for candidate in rows:
            lines.extend(
                [
                    f"### {candidate.rank}. {candidate.company_name} ({candidate.ticker})",
                    "",
                    f"- Confidence: `{candidate.confidence_label}`",
                    f"- Evidence quality: `{candidate.evidence_quality}`",
                    f"- Thesis: {candidate.thesis}",
                    f"- Why now: {candidate.why_now}",
                    f"- Counterarguments: {'; '.join(candidate.counterarguments) or 'none recorded'}",
                    f"- Memory episodes: {', '.join(candidate.memory_episode_ids) or 'none'}",
                    "",
                ]
            )
        return "\n".join(lines)

    sector_lines: list[str] = []
    for sector in prediction.dominant_sectors:
        sector_lines.extend(
            [
                f"### {sector.name}",
                "",
                f"- Mechanism: {sector.formation_mechanism}",
                f"- Expected breadth: {sector.expected_breadth}",
                f"- Possible leaders: {', '.join(sector.possible_leaders) or 'unknown'}",
                f"- Failure conditions: {'; '.join(sector.failure_conditions) or 'none recorded'}",
                "",
            ]
        )

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
            "\n".join(
                f"- {candidate.rank}. {candidate.company_name} [{candidate.path_type}]"
                for candidate in candidates
            ),
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
            "Red-team objections passed to synthesis:",
            "",
            "\n".join(f"- {path}" for path in manifest.red_team_artifacts) or "- none",
            "",
            "## 11. Used Past Research Cases",
            "",
            "\n".join(f"- {episode_id}" for episode_id in manifest.swept_episode_ids) or "- none",
            "",
            "## 12. Additional Web Sources",
            "",
            "\n".join(f"- {source}" for source in manifest.web_sources)
            or "- mock/no external web sources",
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
