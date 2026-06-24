"""Lookahead leak audits."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from news_scalping_lab.utils import read_json


def audit_lookahead(root: Path, *, trade_date: date | None = None) -> dict[str, object]:
    findings: list[str] = []
    manifest_paths = sorted((root / "runs" / "manifests").glob("*.json"))
    for path in manifest_paths:
        manifest = read_json(path)
        price_snapshot = manifest.get("price_snapshot", {})
        allowed = price_snapshot.get("allowed_through")
        if (
            trade_date is not None
            and allowed is not None
            and str(allowed) >= trade_date.isoformat()
        ):
            findings.append(f"{path.name}: price allowed_through is not before trade date")
        if (
            manifest.get("mode") == "exhaustive"
            and manifest.get("accepted_episode_count") != manifest.get("swept_episode_count")
        ):
            findings.append(f"{path.name}: exhaustive coverage mismatch")
    return {
        "passed": not findings,
        "findings": findings,
        "checked_manifests": len(manifest_paths),
    }
