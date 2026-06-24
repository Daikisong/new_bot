"""Output provenance audits."""

from __future__ import annotations

from pathlib import Path

from news_scalping_lab.utils import read_json


def audit_provenance(root: Path) -> dict[str, object]:
    findings: list[str] = []
    for path in sorted((root / "predictions").glob("*.json")):
        prediction = read_json(path)
        if not prediction.get("blind_artifact_sha256"):
            findings.append(f"{path.name}: missing blind_artifact_sha256")
        for candidate in prediction.get("candidates", []):
            has_anchor = (
                candidate.get("event_ids")
                or candidate.get("memory_episode_ids")
                or candidate.get("source_urls")
            )
            if not has_anchor:
                findings.append(
                    f"{path.name}: candidate lacks provenance anchors: {candidate.get('company_name')}"
                )
    return {"passed": not findings, "findings": findings}
