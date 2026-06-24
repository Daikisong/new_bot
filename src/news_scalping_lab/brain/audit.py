"""Brain coverage audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from news_scalping_lab.brain.compiler import current_brain_version
from news_scalping_lab.contracts.models import MemoryClaim, ResearchEpisode
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import is_available_as_of, read_json


def audit_brain(root: Path) -> dict[str, object]:
    store = ResearchStore(root)
    accepted = store.list_accepted()
    coverage_path = root / "brain" / "current" / "coverage_manifest.json"
    manifest = read_json(coverage_path) if coverage_path.exists() else {}
    covered = set(_string_list(manifest.get("covered_episode_ids", [])))
    accepted_ids = {episode.episode_id for episode in accepted}
    missing = sorted(accepted_ids - covered)
    extra = sorted(covered - accepted_ids)
    claim_audit = _audit_claims(root, accepted)
    hard_findings = [
        *claim_audit["invalid_claim_lines"],
        *claim_audit["claims_without_support"],
        *claim_audit["claims_with_unknown_support"],
        *claim_audit["claim_temporal_leaks"],
        *claim_audit["claims_without_provenance"],
    ]
    coverage_complete = not missing and not extra and len(covered) == len(accepted)
    return {
        "accepted_episode_count": len(accepted),
        "brain_covered_episode_count": len(covered),
        "missing_episode_ids": missing,
        "extra_episode_ids": extra,
        **claim_audit,
        "coverage_complete": coverage_complete,
        "passed": coverage_complete and not hard_findings,
        "brain_version": current_brain_version(root),
        "last_full_rebuild": manifest.get("created_at"),
    }


def _audit_claims(root: Path, accepted: list[ResearchEpisode]) -> dict[str, list[str]]:
    accepted_by_id = {episode.episode_id: episode for episode in accepted}
    claims_path = root / "brain" / "current" / "claims.jsonl"
    invalid_claim_lines: list[str] = []
    claims_without_support: list[str] = []
    claims_with_unknown_support: list[str] = []
    claims_without_provenance: list[str] = []
    claim_temporal_leaks: list[str] = []
    single_support_claims_without_contradictions: list[str] = []
    if not claims_path.exists():
        return {
            "invalid_claim_lines": invalid_claim_lines,
            "claims_without_support": claims_without_support,
            "claims_with_unknown_support": claims_with_unknown_support,
            "claims_without_provenance": claims_without_provenance,
            "claim_temporal_leaks": claim_temporal_leaks,
            "single_support_claims_without_contradictions": (
                single_support_claims_without_contradictions
            ),
        }
    for line_number, line in enumerate(claims_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            claim = MemoryClaim.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            invalid_claim_lines.append(f"claims.jsonl:{line_number}: {exc}")
            continue
        if not claim.support_episode_ids:
            claims_without_support.append(claim.claim_id)
        unknown_support = [
            episode_id
            for episode_id in claim.support_episode_ids
            if episode_id not in accepted_by_id
        ]
        if unknown_support:
            claims_with_unknown_support.append(
                f"{claim.claim_id}: {', '.join(sorted(unknown_support))}"
            )
        if not claim.provenance:
            claims_without_provenance.append(claim.claim_id)
        for episode_id in claim.support_episode_ids:
            episode = accepted_by_id.get(episode_id)
            if episode is None:
                continue
            if not is_available_as_of(episode.available_from, claim.available_from):
                claim_temporal_leaks.append(
                    f"{claim.claim_id}: available_from precedes support {episode_id}"
                )
        if (
            len(claim.support_episode_ids) == 1
            and claim.support_episode_ids[0] in accepted_by_id
            and not claim.contradiction_episode_ids
            and not claim.near_miss_episode_ids
        ):
            single_support_claims_without_contradictions.append(claim.claim_id)
    return {
        "invalid_claim_lines": invalid_claim_lines,
        "claims_without_support": claims_without_support,
        "claims_with_unknown_support": claims_with_unknown_support,
        "claims_without_provenance": claims_without_provenance,
        "claim_temporal_leaks": claim_temporal_leaks,
        "single_support_claims_without_contradictions": (
            single_support_claims_without_contradictions
        ),
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
