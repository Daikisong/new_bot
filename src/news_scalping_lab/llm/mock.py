"""Deterministic test provider.

The mock is intentionally generic. It does not translate domains, policies, regions,
or themes into stock lists. It creates open-world mechanisms and direct candidate
placeholders from the input text so tests can exercise the pipeline without API keys.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import TypeVar

from pydantic import BaseModel

from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    ConfidenceLabel,
    DominantSectorHypothesis,
    PathType,
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
        if response_model is BlindPrediction:
            prediction = self._blind_prediction(prompt)
            return prediction  # type: ignore[return-value]
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
        today = date.today()
        created_at = now_kst()
        mechanisms = self.infer_mechanisms(prompt)
        mentions = self.extract_company_mentions(prompt.split("\n---NEWS---\n"))
        candidates: list[Candidate] = []
        for rank, company in enumerate(mentions[:5], start=1):
            candidates.append(
                Candidate(
                    rank=rank,
                    ticker="UNKNOWN",
                    company_name=company,
                    path_type=PathType.SINGLE_EVENT,
                    event_ids=[],
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
                )
            )

        sector = DominantSectorHypothesis(
            name="open-world catalyst cluster",
            triggering_events=[],
            formation_mechanism=mechanisms[0],
            expected_breadth="unknown until web and memory evidence are compared",
            direct_beneficiaries=[candidate.company_name for candidate in candidates[:3]],
            indirect_beneficiaries=[],
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
            cutoff_at=created_at,
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
