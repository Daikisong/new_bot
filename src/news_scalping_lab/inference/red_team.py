"""Candidate red-team review pass."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from news_scalping_lab.contracts.models import (
    BlindPrediction,
    Candidate,
    ContextManifest,
    RedTeamArtifact,
    RedTeamFinding,
)
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.utils import canonical_json, now_kst, relative_to_root, sha256_text, write_json

PROMPT_VERSION = "red_team.candidate_attack.v1"


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
    return artifact.model_copy(
        update={
            "run_id": manifest.run_id,
            "source_prediction_id": prediction.prediction_id,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": prompt_sha256,
            "created_at": now_kst(),
            "candidate_count": len(prediction.candidates),
        }
    )


def _fallback_finding(candidate: Candidate) -> RedTeamFinding:
    objections = _unique(
        [
            *candidate.counterarguments,
            *_candidate_specific_objections(candidate),
        ]
    )
    contrary_evidence = [f"negative memory case: {case}" for case in candidate.prior_negative_cases]
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
        objections=objections,
        contrary_evidence=contrary_evidence,
        disconfirming_conditions=disconfirming_conditions,
        verification_questions=verification_questions,
        passed_to_synthesis=True,
    )


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
    prompt_text: str,
    prediction: BlindPrediction,
    manifest: ContextManifest,
) -> str:
    payload = {
        "schema": "nslab.red_team_artifact.v1",
        "prompt_version": PROMPT_VERSION,
        "run_id": manifest.run_id,
        "source_prediction_id": prediction.prediction_id,
        "trade_date": prediction.trade_date.isoformat(),
        "cutoff_at": prediction.cutoff_at.isoformat(),
        "brain_version": manifest.brain_version,
        "swept_episode_ids": manifest.swept_episode_ids,
        "memory_sweep_artifacts": manifest.memory_sweep_artifacts,
        "web_sources": manifest.web_sources,
        "excluded_web_source_ids": manifest.excluded_web_source_ids,
        "blind_uncertainties": prediction.blind_analysis.initial_uncertainties,
        "candidates": [
            candidate.model_dump(mode="json")
            for candidate in sorted(prediction.candidates, key=lambda item: item.rank)
        ],
    }
    return (
        "Review each candidate as an adversarial red-team pass. "
        "Return one RedTeamFinding per candidate. Keep candidates, but pass objections "
        "and contrary evidence forward to final synthesis.\n"
        f"{prompt_text.strip()}\n"
        "---RED_TEAM_PAYLOAD---\n"
        f"{canonical_json(payload)}"
    )


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
