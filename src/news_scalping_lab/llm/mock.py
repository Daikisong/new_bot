"""Deterministic test provider.

The mock is intentionally generic. It does not translate domains, policies, regions,
or themes into stock lists. It creates open-world mechanisms and direct candidate
placeholders from the input text so tests can exercise the pipeline without API keys.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from typing import Any, TypeVar

from pydantic import BaseModel

from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    ConfidenceLabel,
    DominantSectorHypothesis,
    PathType,
    RedTeamArtifact,
    RedTeamFinding,
)
from news_scalping_lab.research_import.semantic import SemanticResearchDraft
from news_scalping_lab.utils import KST, now_kst, sha256_text, stable_id

T = TypeVar("T", bound=BaseModel)


class DeterministicMockLLMProvider:
    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        digest = sha256_text(f"{purpose}|{prompt}")[:12]
        return (
            f"mock:{purpose}:{digest}\n"
            "Open-world reasoning should begin from current evidence, then compare memory "
            "support and counterexamples without using retrieval as a gate."
        )

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        if response_model is BlindPrediction and purpose == "final_synthesis":
            prediction = self._final_synthesis_prediction(prompt)
            return prediction  # type: ignore[return-value]
        if response_model is BlindPrediction:
            prediction = self._blind_prediction(prompt)
            return prediction  # type: ignore[return-value]
        if response_model is RedTeamArtifact:
            artifact = self._red_team_artifact(prompt)
            return artifact  # type: ignore[return-value]
        if response_model is SemanticResearchDraft:
            draft = self._semantic_research_draft(prompt)
            return draft  # type: ignore[return-value]
        raise NotImplementedError(f"mock structured output not registered for {response_model}")

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = sha256_text(f"{purpose}|{text}")
            values = [int(digest[index : index + 2], 16) / 255 for index in range(0, 24, 2)]
            vectors.append(values)
        return vectors

    def infer_mechanisms(self, text: str) -> list[str]:
        # Mechanism templates are market-structure abstractions, not ticker or theme maps.
        length_bucket = "broad" if len(text) > 300 else "narrow"
        return [
            f"{length_bucket} catalyst -> direct beneficiary check -> indirect capacity path",
            "current evidence -> market narrative expansion -> leader selection uncertainty",
            "first-order company event -> balance-sheet or contract relevance -> red-team review",
        ]

    def extract_company_mentions(self, texts: list[str], limit: int = 8) -> list[str]:
        mentions: list[str] = []
        for text in texts:
            title = text.splitlines()[0] if text else ""
            title = re.sub(r"^[\\[【].*?[\\]】]\\s*", "", title)
            for separator in (",", "，", "ㆍ", "·", " "):
                if separator in title:
                    title = title.split(separator)[0]
                    break
            cleaned = re.sub(r"[^0-9A-Za-z가-힣&()._-]+", "", title).strip()
            if len(cleaned) >= 2 and cleaned not in mentions:
                mentions.append(cleaned)
            if len(mentions) >= limit:
                break
        return mentions

    def _blind_prediction(self, prompt: str) -> BlindPrediction:
        payload = self._blind_payload(prompt)
        today = self._payload_date(payload, "trade_date") or date.today()
        created_at = now_kst()
        cutoff_at = self._payload_datetime(payload, "cutoff_at") or created_at
        current_news = self._payload_string_list(payload, "current_news")
        event_ids = self._payload_string_list(payload, "event_ids")
        mechanisms = self._payload_string_list(payload, "first_pass_mechanisms")
        if not mechanisms:
            mechanisms = self.infer_mechanisms("\n---NEWS---\n".join(current_news) or prompt)
        mentions = self.extract_company_mentions(current_news or [prompt])
        event_anchor = f"news://{event_ids[0]}" if event_ids else "news://current-batch"
        candidates: list[Candidate] = []
        for rank, company in enumerate(mentions[:5], start=1):
            candidates.append(
                Candidate(
                    rank=rank,
                    ticker="UNKNOWN",
                    company_name=company,
                    path_type=PathType.SINGLE_EVENT,
                    event_ids=event_ids[:1],
                    thesis=(
                        "Directly mentioned entity requires verification of listing status, "
                        "economic ownership, novelty, and D-1 market absorption."
                    ),
                    why_now="The entity appears in the current pre-cutoff news batch.",
                    causal_chain=[
                        "news event observed before cutoff",
                        "direct entity relevance investigated",
                        "counterarguments retained for final synthesis",
                    ],
                    direct_evidence=[f"current-news mention: {company}"],
                    inferred_evidence=["mock provider requests web and memory verification"],
                    market_memory_evidence=[],
                    novel_reasoning="Candidate was generated from current evidence, not from a static list.",
                    counterarguments=[
                        "listing status may be unverified",
                        "event may be stale or economically indirect",
                    ],
                    disconfirming_conditions=[
                        "not listed",
                        "cutoff-after source only",
                        "D-1 price action already fully reflected the event",
                    ],
                    confidence_label=ConfidenceLabel.SPECULATIVE,
                    evidence_quality=ConfidenceLabel.LOW,
                    source_urls=[event_anchor],
                )
            )
        if not candidates:
            candidates.append(
                Candidate(
                    rank=1,
                    ticker="UNKNOWN",
                    company_name="UNVERIFIED_ENTITY",
                    path_type=PathType.THEME_BENEFICIARY,
                    event_ids=[],
                    thesis="No direct entity could be extracted, so a web/company discovery pass is required.",
                    why_now="The news batch still contains pre-cutoff catalysts requiring open-world analysis.",
                    causal_chain=["news catalyst", "mechanism inference", "company discovery"],
                    direct_evidence=[],
                    inferred_evidence=["retrieval miss does not block candidate discovery"],
                    confidence_label=ConfidenceLabel.SPECULATIVE,
                    evidence_quality=ConfidenceLabel.LOW,
                    source_urls=[event_anchor],
                )
            )
        discovery_rank = len(candidates) + 1
        candidates.append(
            Candidate(
                rank=discovery_rank,
                ticker="UNKNOWN",
                company_name="BENEFICIARY_DISCOVERY_REQUIRED",
                path_type=PathType.THEME_BENEFICIARY,
                event_ids=event_ids[:3],
                thesis="Policy, industry, or supply-chain beneficiaries require web/company discovery.",
                why_now="Open-world mechanisms indicate indirect paths before any retrieval gate.",
                causal_chain=["current catalyst", "beneficiary path discovery", "company verification"],
                inferred_evidence=mechanisms[:2],
                novel_reasoning="A new beneficiary can be investigated even when memory has no exact precedent.",
                counterarguments=["theme breadth may fail", "indirect relation may be too weak"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
                source_urls=[event_anchor],
            )
        )
        candidates.append(
            Candidate(
                rank=discovery_rank + 1,
                ticker="UNKNOWN",
                company_name="D_MINUS_ONE_LEADER_REVIEW",
                path_type=PathType.CONTINUATION,
                event_ids=[],
                thesis="Recent leaders must be checked using only D-1 and earlier market data.",
                why_now="Continuation is evaluated separately from current-news directness.",
                causal_chain=["D-1 market memory", "current catalyst overlap", "continuation red-team"],
                inferred_evidence=["requires blind-safe price provider"],
                market_memory_evidence=["D-day prices are blocked during blind analysis"],
                counterarguments=["already exhausted", "no current catalyst overlap"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
                evidence_quality=ConfidenceLabel.LOW,
                source_urls=["price://blind-safe-d-minus-one"],
            )
        )

        sector = DominantSectorHypothesis(
            name="open-world catalyst cluster",
            triggering_events=event_ids[:5],
            formation_mechanism=mechanisms[0],
            expected_breadth="unknown until web and memory evidence are compared",
            direct_beneficiaries=[
                candidate.company_name for candidate in candidates if candidate.path_type == PathType.SINGLE_EVENT
            ][:3],
            indirect_beneficiaries=["BENEFICIARY_DISCOVERY_REQUIRED"],
            narrative_beneficiaries=[],
            possible_leaders=[candidate.company_name for candidate in candidates[:3]],
            failure_conditions=[
                "no listed entity relation",
                "event was already known before the window",
                "memory counterexamples dominate supporting cases",
            ],
        )
        return BlindPrediction(
            prediction_id=stable_id("PRED", prompt, created_at.isoformat()),
            trade_date=today,
            cutoff_at=cutoff_at,
            created_at=created_at,
            blind_analysis=BlindAnalysis(
                summary="Mock open-world blind analysis produced from current evidence only.",
                open_world_mechanisms=mechanisms,
                initial_uncertainties=[
                    "ticker verification",
                    "cutoff-safe web evidence",
                    "D-1 price absorption",
                ],
            ),
            dominant_sectors=[sector],
            candidates=candidates,
        )

    def _blind_payload(self, prompt: str) -> dict[str, Any]:
        marker = "---BLIND_ANALYSIS_PAYLOAD---"
        if marker not in prompt:
            return {}
        payload_text = prompt.split(marker, maxsplit=1)[-1].strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _red_team_artifact(self, prompt: str) -> RedTeamArtifact:
        payload = self._red_team_payload(prompt)
        candidates = payload.get("candidates", [])
        findings: list[RedTeamFinding] = []
        if isinstance(candidates, list):
            for raw_candidate in candidates:
                if not isinstance(raw_candidate, dict):
                    continue
                rank = raw_candidate.get("rank")
                candidate_rank = rank if isinstance(rank, int) else len(findings) + 1
                ticker = str(raw_candidate.get("ticker") or "UNKNOWN")
                company_name = str(raw_candidate.get("company_name") or "UNVERIFIED_ENTITY")
                path_type = str(raw_candidate.get("path_type") or "HYBRID")
                try:
                    parsed_path_type = PathType(path_type)
                except ValueError:
                    parsed_path_type = PathType.HYBRID
                counterarguments = [
                    item for item in raw_candidate.get("counterarguments", []) if isinstance(item, str)
                ]
                disconfirming = [
                    item
                    for item in raw_candidate.get("disconfirming_conditions", [])
                    if isinstance(item, str)
                ]
                prior_negative_cases = [
                    item for item in raw_candidate.get("prior_negative_cases", []) if isinstance(item, str)
                ]
                direct_evidence = [
                    item for item in raw_candidate.get("direct_evidence", []) if isinstance(item, str)
                ]
                objections = list(counterarguments)
                if ticker.upper() in {"UNKNOWN", "UNVERIFIED"}:
                    objections.append("ticker or listing status is unverified")
                if not direct_evidence:
                    objections.append("direct evidence is weak or absent")
                if path_type != "SINGLE_EVENT":
                    objections.append("candidate depends on indirect path reasoning")
                findings.append(
                    RedTeamFinding(
                        candidate_rank=candidate_rank,
                        ticker=ticker,
                        company_name=company_name,
                        path_type=parsed_path_type,
                        attack_summary=(
                            "Mock red-team review retained the candidate and passed objections forward."
                        ),
                        objections=self._dedupe_strings(objections),
                        contrary_evidence=[
                            f"negative memory case: {case}" for case in prior_negative_cases
                        ],
                        disconfirming_conditions=self._dedupe_strings(
                            [
                                *disconfirming,
                                "only cutoff-after evidence is available",
                                "D-1 and earlier market action already reflected the catalyst",
                            ]
                        ),
                        verification_questions=[
                            "Is the candidate a listed security with a current tradable ticker?",
                            "Is the catalyst economically attributable to this listed entity?",
                            "Was the catalyst already absorbed by D-1 and earlier market data?",
                        ],
                    )
                )
        return RedTeamArtifact(
            run_id=str(payload.get("run_id") or "RUN-mock-red-team"),
            source_prediction_id=str(payload.get("source_prediction_id") or "PRED-mock"),
            prompt_version="red_team.candidate_attack.v1",
            prompt_sha256=sha256_text(prompt),
            created_at=now_kst(),
            candidate_count=len(findings),
            candidate_findings=findings,
            notes=["Mock structured red-team pass."],
        )

    def _red_team_payload(self, prompt: str) -> dict[str, Any]:
        marker = "---RED_TEAM_PAYLOAD---"
        if marker not in prompt:
            return {}
        payload_text = prompt.split(marker, maxsplit=1)[-1].strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _final_synthesis_prediction(self, prompt: str) -> BlindPrediction:
        payload = self._final_synthesis_payload(prompt)
        draft = payload.get("candidate_research")
        if isinstance(draft, dict):
            prediction = BlindPrediction.model_validate(draft)
        else:
            prediction = self._blind_prediction(prompt)
        red_team = payload.get("red_team_output", {})
        red_team_notes = []
        if isinstance(red_team, dict):
            raw_findings = red_team.get("candidate_findings", [])
            if isinstance(raw_findings, list):
                red_team_notes = [
                    str(finding.get("attack_summary"))
                    for finding in raw_findings
                    if isinstance(finding, dict) and finding.get("attack_summary")
                ]
        analysis = prediction.blind_analysis.model_copy(
            update={
                "summary": (
                    "Mock final synthesis reviewed current news, memory sweep, web context, "
                    "red-team output, and blind-safe D-1 market data."
                ),
                "initial_uncertainties": self._dedupe_strings(
                    [
                        *prediction.blind_analysis.initial_uncertainties,
                        *red_team_notes[:3],
                    ]
                ),
            }
        )
        return prediction.model_copy(update={"blind_analysis": analysis})

    def _final_synthesis_payload(self, prompt: str) -> dict[str, Any]:
        marker = "---FINAL_SYNTHESIS_PAYLOAD---"
        if marker not in prompt:
            return {}
        payload_text = prompt.split(marker, maxsplit=1)[-1].strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            stripped = value.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            deduped.append(stripped)
        return deduped

    def _payload_string_list(self, payload: dict[str, Any], key: str) -> list[str]:
        value = payload.get(key)
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    def _payload_date(self, payload: dict[str, Any], key: str) -> date | None:
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def _payload_datetime(self, payload: dict[str, Any], key: str) -> datetime | None:
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _semantic_research_draft(self, prompt: str) -> SemanticResearchDraft:
        trade_day = self._infer_generic_date(prompt) or date.today()
        cutoff_at = datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST)
        source_text = prompt.split("---SOURCE_TEXT---", maxsplit=1)[-1].strip()
        compact = re.sub(r"\s+", " ", source_text).strip()
        summary = compact[:1000] or "Structured semantic import created from an empty source."
        mechanisms = self.infer_mechanisms(source_text or prompt)
        return SemanticResearchDraft(
            trade_date=trade_day,
            cutoff_at=cutoff_at,
            summary=summary,
            open_world_mechanisms=mechanisms,
            initial_uncertainties=[
                "semantic conversion should be reviewed before acceptance",
                "raw source remains immutable for provenance",
            ],
            price_source_snapshot={"source": "semantic_import_unknown"},
        )

    def _infer_generic_date(self, text: str) -> date | None:
        match = re.search(r"(20[0-9]{2})[-_./](0[1-9]|1[0-2])[-_./]([0-2][0-9]|3[01])", text)
        if match:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        compact_match = re.search(r"(20[0-9]{2})(0[1-9]|1[0-2])([0-2][0-9]|3[01])", text)
        if compact_match:
            return date(
                int(compact_match.group(1)),
                int(compact_match.group(2)),
                int(compact_match.group(3)),
            )
        return None
