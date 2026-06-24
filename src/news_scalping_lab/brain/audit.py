"""Brain coverage audit."""

from __future__ import annotations

from pathlib import Path

from news_scalping_lab.brain.compiler import current_brain_version
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import read_json


def audit_brain(root: Path) -> dict[str, object]:
    store = ResearchStore(root)
    accepted = store.list_accepted()
    coverage_path = root / "brain" / "current" / "coverage_manifest.json"
    manifest = read_json(coverage_path) if coverage_path.exists() else {}
    covered = set(manifest.get("covered_episode_ids", []))
    accepted_ids = {episode.episode_id for episode in accepted}
    missing = sorted(accepted_ids - covered)
    extra = sorted(covered - accepted_ids)
    source_less_claims: list[str] = []
    claims_path = root / "brain" / "current" / "claims.jsonl"
    if claims_path.exists():
        for line in claims_path.read_text(encoding="utf-8").splitlines():
            if '"support_episode_ids":[]' in line:
                source_less_claims.append(line[:120])
    return {
        "accepted_episode_count": len(accepted),
        "brain_covered_episode_count": len(covered),
        "missing_episode_ids": missing,
        "extra_episode_ids": extra,
        "claims_without_support": source_less_claims,
        "coverage_complete": not missing and not extra and len(covered) == len(accepted),
        "brain_version": current_brain_version(root),
        "last_full_rebuild": manifest.get("created_at"),
    }
