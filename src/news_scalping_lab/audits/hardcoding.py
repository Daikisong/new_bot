"""Production source scan for domain hardcoding risks."""

from __future__ import annotations

import re
from pathlib import Path


def audit_hardcoding(root: Path) -> dict[str, object]:
    src = root / "src" / "news_scalping_lab"
    findings: list[dict[str, object]] = []
    theme_tokens = [
        "_".join(parts)
        for parts in (
            ("THEME", "MAP"),
            ("TICKER", "MAP"),
            ("SECTOR", "TO", "TICKER"),
            ("REGION", "TO", "THEME"),
        )
    ]
    score_parts = (("KEYWORD", "SCORE"), ("FIXED", "SCORE"), ("STATIC", "SCORE"))
    score_tokens = ["_".join(parts) for parts in score_parts]
    patterns = {
        "quoted_six_digit_ticker": re.compile(r"[\"'][0-9]{6}[\"']"),
        "theme_map_name": re.compile(r"\b(" + "|".join(theme_tokens) + r")\b"),
        "score_table_name": re.compile(r"\b(" + "|".join(score_tokens) + r")\b"),
    }
    for path in sorted(src.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for name, pattern in patterns.items():
                if pattern.search(line):
                    findings.append(
                        {
                            "file": path.relative_to(root).as_posix(),
                            "line": line_number,
                            "rule": name,
                            "text": line.strip(),
                        }
                    )
    return {"passed": not findings, "findings": findings}
