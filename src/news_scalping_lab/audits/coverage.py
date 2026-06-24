"""Coverage audit wrapper."""

from __future__ import annotations

from pathlib import Path

from news_scalping_lab.brain.audit import audit_brain


def audit_coverage(root: Path) -> dict[str, object]:
    return audit_brain(root)
