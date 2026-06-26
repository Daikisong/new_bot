"""Candidate red-team review pass."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from news_scalping_lab.contracts.models import (
    BlindPrediction,
    Candidate,
    ContextManifest,
    RedTeamArtifact,
    RedTeamAttackCheck,
    RedTeamFinding,
)
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.utils import (
    canonical_json,
    now_kst,
    read_json,
    relative_to_root,
    sha256_text,
    write_json,
)

PROMPT_VERSION = "red_team.candidate_attack.v2"
REQUIRED_ATTACK_CHECKS = (
    "good_company_news_not_limit_up_language",
    "novelty_not_recycled",
    "economic_amount_attributable_to_listed_company",
    "weak_stage_mou_planned_prototype",
    "already_pre_absorbed",
    "market_cap_float_liquidity_drag",
    "dilution_or_financing_risk",
    "forced_indirect_relation",
    "market_memory_relation_currently_broken",
    "purer_same_theme_leader_exists",
)


@dataclass(frozen=True)
class RedTeamPassResult:
    artifact: RedTeamArtifact
    artifact_path: str
    prompt_token_estimate: int
    used_fallback: bool


async def run_red_team_pass(
    *,
    root: Path,
    llm: LLMProvider,
    prediction: BlindPrediction,
    manifest: ContextManifest,
) -> RedTeamPassResult:
    prompt_text = _load_prompt(root)
    prompt = _build_prompt(
        root=root,
        prompt_text=prompt_text,
        prediction=prediction,
        manifest=manifest,
    )
    prompt_sha256 = sha256_text(prompt)
    used_fallback = False
    try:
        artifact = await llm.generate_structured(
            prompt=prompt,
            response_model=RedTeamArtifact,
            purpose="red_team_candidate_review",
        )
        artifact = _normalize_artifact(
            artifact,
            prediction=prediction,
            manifest=manifest,
            prompt_sha256=prompt_sha256,
        )
    except NotImplementedError:
        used_fallback = True
        artifact = build_deterministic_red_team_artifact(
            prediction=prediction,
            manifest=manifest,
            prompt_sha256=prompt_sha256,
            notes=["LLM provider does not implement RedTeamArtifact; deterministic fallback used."],
        )

    if len(artifact.candidate_findings) != len(prediction.candidates):
        used_fallback = True
        artifact = build_deterministic_red_team_artifact(
            prediction=prediction,
            manifest=manifest,
            prompt_sha256=prompt_sha256,
            notes=["LLM red-team output did not cover every candidate; deterministic fallback used."],
        )

    artifact_dir = root / "runs" / "checkpoints" / "red_team"
    artifact_path = artifact_dir / f"{manifest.run_id}.json"
    write_json(artifact_path, artifact.model_dump(mode="json"))
    return RedTeamPassResult(
        artifact=artifact,
        artifact_path=relative_to_root(artifact_path, root),
        prompt_token_estimate=max(1, len(prompt) // 4),
        used_fallback=used_fallback,
    )


def apply_red_team_findings(
    prediction: BlindPrediction,
    artifact: RedTeamArtifact,
) -> BlindPrediction:
    findings_by_rank = {finding.candidate_rank: finding for finding in artifact.candidate_findings}
    candidates: list[Candidate] = []
    for candidate in prediction.candidates:
        finding = findings_by_rank.get(candidate.rank)
        if finding is None:
            candidates.append(candidate)
            continue
        candidates.append(
            candidate.model_copy(
                update={
                    "counterarguments": _unique(
                        [
                            *candidate.counterarguments,
                            *finding.objections,
                            *finding.contrary_evidence,
                        ]
                    ),
                    "disconfirming_conditions": _unique(
                        [
                            *candidate.disconfirming_conditions,
                            *finding.disconfirming_conditions,
                        ]
                    ),
                }
            )
        )
    return prediction.model_copy(update={"candidates": candidates})


def build_deterministic_red_team_artifact(
    *,
    prediction: BlindPrediction,
    manifest: ContextManifest,
    prompt_sha256: str,
    notes: list[str] | None = None,
) -> RedTeamArtifact:
    findings = [_fallback_finding(candidate) for candidate in prediction.candidates]
    return RedTeamArtifact(
        run_id=manifest.run_id,
        source_prediction_id=prediction.prediction_id,
        prompt_version=PROMPT_VERSION,
        prompt_sha256=prompt_sha256,
        created_at=now_kst(),
        candidate_count=len(prediction.candidates),
        required_attack_checks=list(REQUIRED_ATTACK_CHECKS),
        candidate_findings=findings,
        notes=notes
        or [
            "Candidates are retained; red-team objections are passed forward for final reporting.",
        ],
    )


def _normalize_artifact(
    artifact: RedTeamArtifact,
    *,
    prediction: BlindPrediction,
    manifest: ContextManifest,
    prompt_sha256: str,
) -> RedTeamArtifact:
    candidates_by_rank = {candidate.rank: candidate for candidate in prediction.candidates}
    candidate_findings = [
        _normalize_finding(
            finding,
            candidate=candidates_by_rank.get(finding.candidate_rank),
        )
        for finding in artifact.candidate_findings
    ]
    return artifact.model_copy(
        update={
            "run_id": manifest.run_id,
            "source_prediction_id": prediction.prediction_id,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": prompt_sha256,
            "created_at": now_kst(),
            "candidate_count": len(prediction.candidates),
            "required_attack_checks": list(REQUIRED_ATTACK_CHECKS),
            "candidate_findings": candidate_findings,
        }
    )


def _fallback_finding(candidate: Candidate) -> RedTeamFinding:
    objections = _unique(
        [
            *candidate.counterarguments,
            *_candidate_specific_objections(candidate),
        ]
    )
    contrary_evidence = [
        f"negative memory case: {case}" for case in candidate.prior_negative_cases
    ] + [
        f"negative memory record: {record_id}"
        for record_id in candidate.prior_negative_record_ids
    ]
    disconfirming_conditions = _unique(
        [
            *candidate.disconfirming_conditions,
            "only cutoff-after evidence is available",
            "D-1 and earlier market action already reflected the catalyst",
        ]
    )
    verification_questions = [
        "Is the candidate a listed security with a current tradable ticker?",
        "Is the catalyst economically attributable to this listed entity?",
        "Is the evidence direct enough compared with other same-theme candidates?",
        "Was the catalyst already absorbed by D-1 and earlier market data?",
    ]
    return RedTeamFinding(
        candidate_rank=candidate.rank,
        ticker=candidate.ticker,
        company_name=candidate.company_name,
        path_type=candidate.path_type,
        attack_summary=(
            f"Rank {candidate.rank} remains eligible, but synthesis must weigh "
            f"{len(objections)} objections before treating it as actionable."
        ),
        attack_checks=[
            _fallback_attack_check(candidate, check_name)
            for check_name in REQUIRED_ATTACK_CHECKS
        ],
        objections=objections,
        contrary_evidence=contrary_evidence,
        disconfirming_conditions=disconfirming_conditions,
        verification_questions=verification_questions,
        passed_to_synthesis=True,
    )


def _normalize_finding(
    finding: RedTeamFinding,
    *,
    candidate: Candidate | None,
) -> RedTeamFinding:
    checks_by_name = {check.name: check for check in finding.attack_checks}
    normalized_checks: list[RedTeamAttackCheck] = []
    for check_name in REQUIRED_ATTACK_CHECKS:
        existing = checks_by_name.get(check_name)
        if existing is not None:
            normalized_checks.append(
                existing.model_copy(update={"passed_to_synthesis": True})
            )
            continue
        if candidate is not None:
            normalized_checks.append(_fallback_attack_check(candidate, check_name))
        else:
            normalized_checks.append(
                RedTeamAttackCheck(
                    name=check_name,
                    status="needs_review",
                    objection="LLM output omitted this required attack check.",
                    passed_to_synthesis=True,
                )
            )
    update: dict[str, object] = {
        "attack_checks": normalized_checks,
        "passed_to_synthesis": True,
    }
    if candidate is not None:
        update.update(
            {
                "ticker": candidate.ticker,
                "company_name": candidate.company_name,
                "path_type": candidate.path_type,
            }
        )
    return finding.model_copy(update=update)


def _fallback_attack_check(candidate: Candidate, check_name: str) -> RedTeamAttackCheck:
    objection = _attack_check_objection(candidate, check_name)
    return RedTeamAttackCheck(
        name=check_name,
        status="needs_synthesis_review",
        objection=objection,
        evidence_source_ids=_unique(
            [
                *candidate.source_urls,
                *candidate.memory_episode_ids,
                *candidate.memory_record_ids,
                *candidate.prior_negative_cases,
                *candidate.prior_negative_record_ids,
            ]
        )[:10],
        passed_to_synthesis=True,
    )


def _attack_check_objection(candidate: Candidate, check_name: str) -> str:
    objections = {
        "good_company_news_not_limit_up_language": (
            "Good company news may lack market language strong enough for a limit-up move."
        ),
        "novelty_not_recycled": (
            "The catalyst may be recycled or already known before the blind window."
        ),
        "economic_amount_attributable_to_listed_company": (
            "Headline amount or benefit may not be economically attributable to this listed entity."
        ),
        "weak_stage_mou_planned_prototype": (
            "The event may be an MOU, plan, agreement, or prototype rather than realized revenue."
        ),
        "already_pre_absorbed": (
            "D-1 and earlier market action may already have absorbed the catalyst."
        ),
        "market_cap_float_liquidity_drag": (
            "Market cap, free float, turnover, or liquidity may be too heavy for limit-up behavior."
        ),
        "dilution_or_financing_risk": (
            "Dilution, financing, conversion, or supply overhang could offset the catalyst."
        ),
        "forced_indirect_relation": (
            "The candidate may be an overextended indirect beneficiary rather than a direct winner."
        ),
        "market_memory_relation_currently_broken": (
            "Prior market memory may exist while the current business relation is stale or broken."
        ),
        "purer_same_theme_leader_exists": (
            "A cleaner same-theme leader may have stronger directness or market memory."
        ),
    }
    base = objections[check_name]
    if check_name == "forced_indirect_relation" and str(candidate.path_type) == "SINGLE_EVENT":
        return f"{base} Direct-path evidence still needs verification."
    if check_name == "market_cap_float_liquidity_drag" and candidate.ticker.upper() in {
        "UNKNOWN",
        "UNVERIFIED",
    }:
        return f"{base} Exact listing and tradable float remain unresolved."
    return base


def _candidate_specific_objections(candidate: Candidate) -> list[str]:
    objections: list[str] = []
    if candidate.ticker.upper() in {"UNKNOWN", "UNVERIFIED"}:
        objections.append("ticker or listing status is unverified")
    if not candidate.direct_evidence:
        objections.append("direct evidence is weak or absent")
    if str(candidate.path_type) != "SINGLE_EVENT":
        objections.append("candidate depends on indirect path reasoning")
    if str(candidate.evidence_quality) in {"low", "speculative"}:
        objections.append("evidence quality is below actionable confirmation")
    return objections


def _load_prompt(root: Path) -> str:
    path = root / "prompts" / "red_team" / "candidate_attack.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "Attack directness, novelty, dilution, pre-reflection, economic ownership, "
        "and weak narrative links. Do not delete candidates automatically."
    )


def _build_prompt(
    *,
    root: Path,
    prompt_text: str,
    prediction: BlindPrediction,
    manifest: ContextManifest,
) -> str:
    payload = {
        "schema": "nslab.red_team_artifact.v1",
        "prompt_version": PROMPT_VERSION,
        "required_attack_checks": list(REQUIRED_ATTACK_CHECKS),
        "run_id": manifest.run_id,
        "source_prediction_id": prediction.prediction_id,
        "trade_date": prediction.trade_date.isoformat(),
        "cutoff_at": prediction.cutoff_at.isoformat(),
        "brain_version": manifest.brain_version,
        "swept_episode_ids": manifest.swept_episode_ids,
        "memory_sweep_artifacts": manifest.memory_sweep_artifacts,
        "web_sources": manifest.web_sources,
        "excluded_web_source_ids": manifest.excluded_web_source_ids,
        "candidate_verification": _read_candidate_verification_context(
            root=root,
            manifest=manifest,
        ),
        "blind_uncertainties": prediction.blind_analysis.initial_uncertainties,
        "candidates": [
            candidate.model_dump(mode="json")
            for candidate in sorted(prediction.candidates, key=lambda item: item.rank)
        ],
    }
    return (
        "Review each candidate as an adversarial red-team pass. "
        "Return one RedTeamFinding per candidate and cover every required_attack_checks "
        "entry in attack_checks. Keep candidates, but pass objections and contrary "
        "evidence forward to final synthesis.\n"
        f"{prompt_text.strip()}\n"
        "---RED_TEAM_PAYLOAD---\n"
        f"{canonical_json(payload)}"
    )


def _read_candidate_verification_context(
    *,
    root: Path,
    manifest: ContextManifest,
) -> dict[str, object]:
    if not manifest.candidate_verification_artifact:
        return {}
    path = root / manifest.candidate_verification_artifact
    if not path.exists() or not path.is_file():
        return {"path": manifest.candidate_verification_artifact, "missing": True}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        stripped = item.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        unique_items.append(stripped)
    return unique_items
