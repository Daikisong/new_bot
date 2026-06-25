"""Shared pre-open report section checks."""

from __future__ import annotations

from typing import Any

PREOPEN_REPORT_SECTION_TITLES: tuple[str, ...] = (
    "1. Execution Info",
    "2. Research Brain Version",
    "3. News Range And Cutoff",
    "4. Dominant Sector Hypotheses",
    "5. Single-News Upper-Limit Candidates",
    "6. Theme Beneficiary Upper-Limit Candidates",
    "7. Prior-Leader Continuation Candidates",
    "8. All Pre-Open Watchlist Candidates",
    "9. Excluded But Watch",
    "10. Key Counterexamples And Uncertainty",
    "11. Used Past Research Cases",
    "12. Additional Web Sources",
    "13. Memory Coverage",
)

PREOPEN_REPORT_SECTION_HEADINGS: tuple[str, ...] = tuple(
    f"## {title}" for title in PREOPEN_REPORT_SECTION_TITLES
)


def inspect_preopen_report_sections(report_text: str) -> dict[str, Any]:
    positions = {
        heading: report_text.find(heading) for heading in PREOPEN_REPORT_SECTION_HEADINGS
    }
    missing = [heading for heading, position in positions.items() if position < 0]
    observed_positions = [
        position
        for heading in PREOPEN_REPORT_SECTION_HEADINGS
        if (position := positions[heading]) >= 0
    ]
    ordered = observed_positions == sorted(observed_positions)
    empty = [
        heading
        for heading in PREOPEN_REPORT_SECTION_HEADINGS
        if positions[heading] >= 0 and not _section_body(report_text, positions, heading)
    ]
    return {
        "required_count": len(PREOPEN_REPORT_SECTION_HEADINGS),
        "present_count": len(PREOPEN_REPORT_SECTION_HEADINGS) - len(missing),
        "missing": missing,
        "empty": empty,
        "ordered": ordered,
        "passed": not missing and not empty and ordered,
    }


def _section_body(
    report_text: str,
    positions: dict[str, int],
    heading: str,
) -> str:
    start = positions[heading] + len(heading)
    later_heading_positions = [
        position for position in positions.values() if position > positions[heading]
    ]
    end = min(later_heading_positions) if later_heading_positions else len(report_text)
    return report_text[start:end].strip()
