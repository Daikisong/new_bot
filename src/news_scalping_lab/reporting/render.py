"""Markdown report rendering."""

from __future__ import annotations

from collections.abc import Sequence

from news_scalping_lab.contracts.models import BlindPrediction, Candidate, ContextManifest, PathType


def render_preopen_report(prediction: BlindPrediction, manifest: ContextManifest) -> str:
    candidates = sorted(prediction.candidates, key=lambda candidate: candidate.rank)

    def list_text(values: list[str]) -> str:
        return ", ".join(values) if values else "none"

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
                    f"- Causal chain: {list_text(candidate.causal_chain)}",
                    f"- Direct evidence: {list_text(candidate.direct_evidence)}",
                    f"- Inferred evidence: {list_text(candidate.inferred_evidence)}",
                    f"- Market-memory evidence: {list_text(candidate.market_memory_evidence)}",
                    f"- Prior positive cases: {list_text(candidate.prior_positive_cases)}",
                    f"- Prior negative cases: {list_text(candidate.prior_negative_cases)}",
                    f"- Counterarguments: {'; '.join(candidate.counterarguments) or 'none recorded'}",
                    f"- Disconfirming conditions: {list_text(candidate.disconfirming_conditions)}",
                    f"- Memory episodes: {list_text(candidate.memory_episode_ids)}",
                    f"- Source URLs: {list_text(candidate.source_urls)}",
                    f"- Provenance sources: {list_text([item.source_id for item in candidate.provenance])}",
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
                f"- Provenance sources: {list_text([item.source_id for item in sector.provenance])}",
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
