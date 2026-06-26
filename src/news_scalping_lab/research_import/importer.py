"""Strict and semantic research import."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, time
from pathlib import Path
from typing import Literal, Protocol, Self

from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    MemoryClaim,
    Provenance,
    ResearchEpisode,
)
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.research_import.bundle import (
    import_bundle_episode,
    looks_like_bundle,
)
from news_scalping_lab.research_import.semantic import (
    SEMANTIC_IMPORT_PROMPT_VERSION,
    SEMANTIC_IMPORT_REQUIRED_OUTPUT_FIELDS,
    SemanticResearchDraft,
    build_semantic_import_prompt,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    next_trading_day,
    now_kst,
    read_json,
    sha256_text,
    stable_id,
)


class ProvenanceModel(Protocol):
    provenance: list[Provenance]

    def model_copy(
        self,
        *,
        update: dict[str, object] | None = None,
        deep: bool = False,
    ) -> Self: ...


class ResearchImporter:
    def __init__(
        self,
        root: Path,
        store: ResearchStore | None = None,
        llm: LLMProvider | None = None,
        llm_max_retries: int = 0,
    ) -> None:
        self.root = root
        self.store = store or ResearchStore(root)
        self.raw_dir = root / "data" / "raw" / "research"
        self.trace_dir = root / "runs" / "traces"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.llm = TracingLLMProvider(
            llm or DeterministicMockLLMProvider(),
            trace_dir=self.trace_dir,
            default_metadata={"prompt_version": SEMANTIC_IMPORT_PROMPT_VERSION},
            max_retries=llm_max_retries,
        )

    def import_path(self, path: Path, *, mode: str = "auto") -> ResearchEpisode:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.import_path_async(path, mode=mode))
        raise RuntimeError("import_path cannot run inside an active event loop; use import_path_async")

    async def import_path_async(self, path: Path, *, mode: str = "auto") -> ResearchEpisode:
        if mode not in {"auto", "strict", "semantic", "bundle"}:
            raise ValueError("mode must be auto, strict, semantic, or bundle")
        resolved = path.resolve()
        preserved = self._preserve_raw(resolved)
        if mode == "bundle" or (mode == "auto" and looks_like_bundle(preserved)):
            episode = import_bundle_episode(preserved)
        elif mode == "strict" or (mode == "auto" and resolved.suffix.lower() == ".json"):
            episode = self._strict_import(preserved)
        else:
            episode = await self._semantic_import(preserved)
        self.store.save_episode(episode)
        return episode

    def _preserve_raw(self, path: Path) -> Path:
        digest = file_sha256(path)
        target = self.raw_dir / f"{digest[:12]}_{path.name}"
        if not target.exists():
            shutil.copy2(path, target)
        return target

    def _strict_import(self, path: Path) -> ResearchEpisode:
        data = read_json(path)
        episode = ResearchEpisode.model_validate(data)
        source_hash = file_sha256(path)
        source_text = path.read_text(encoding="utf-8", errors="replace")
        provenance = Provenance(
            source_id=stable_id("SRC", path.as_posix(), source_hash),
            source_type="strict_research_json",
            uri=path.as_posix(),
            content_sha256=source_hash,
            observed_at=now_kst(),
        )
        input_audit = {
            **episode.input_audit,
            "strict_import": {
                "source_path": path.as_posix(),
                "source_sha256": source_hash,
                "source_text_sha256": sha256_text(source_text),
                "source_json_sha256": sha256_text(canonical_json(data)),
                "source_schema_version": data.get("schema_version"),
                "imported_episode_id": episode.episode_id,
                "source_id": provenance.source_id,
            },
        }
        blind_analysis = episode.blind_analysis
        if not blind_analysis.provenance:
            blind_analysis = blind_analysis.model_copy(update={"provenance": [provenance]})
        blind_predictions = [
            candidate
            if candidate.provenance
            else candidate.model_copy(update={"provenance": [provenance]})
            for candidate in episode.blind_predictions
        ]
        postmortem = episode.postmortem
        if postmortem is not None and not postmortem.provenance:
            postmortem = postmortem.model_copy(update={"provenance": [provenance]})
        observed_events = [
            item if item.provenance else item.model_copy(update={"provenance": [provenance]})
            for item in episode.observed_events
        ]
        event_ticker_edges = [
            edge if edge.provenance else edge.model_copy(update={"provenance": [provenance]})
            for edge in episode.event_ticker_edges
        ]
        lessons = [
            claim if claim.provenance else claim.model_copy(update={"provenance": [provenance]})
            for claim in episode.lessons
        ]
        counterexamples = [
            claim if claim.provenance else claim.model_copy(update={"provenance": [provenance]})
            for claim in episode.counterexamples
        ]
        return episode.model_copy(
            update={
                "input_audit": input_audit,
                "provenance": [*episode.provenance, provenance],
                "blind_analysis": blind_analysis,
                "blind_predictions": blind_predictions,
                "postmortem": postmortem,
                "observed_events": observed_events,
                "event_ticker_edges": event_ticker_edges,
                "lessons": lessons,
                "counterexamples": counterexamples,
            }
        )

    async def _semantic_import(self, path: Path) -> ResearchEpisode:
        text = path.read_text(encoding="utf-8", errors="replace")
        source_hash = file_sha256(path)
        prompt = build_semantic_import_prompt(
            root=self.root,
            source_path=path,
            source_sha256=source_hash,
            text=text,
        )
        draft = await self.llm.generate_structured(
            prompt=prompt,
            response_model=SemanticResearchDraft,
            purpose="research_import.semantic",
        )

        episode_id = stable_id("EP", draft.trade_date.isoformat(), source_hash)
        provenance = Provenance(
            source_id=stable_id("SRC", path.as_posix(), source_hash),
            source_type="semantic_llm_structured_import",
            uri=path.as_posix(),
            content_sha256=source_hash,
            excerpt=text[:500],
            observed_at=now_kst(),
        )
        source_segments = _source_segments(text)
        output_text_provenance = _output_text_provenance(
            draft,
            source_id=provenance.source_id,
            source_segments=source_segments,
        )
        output_field_source_ids = {
            field_name: [provenance.source_id]
            for field_name in SEMANTIC_IMPORT_REQUIRED_OUTPUT_FIELDS
        }
        for record in output_text_provenance:
            field_name = record.get("field_name")
            if isinstance(field_name, str) and field_name:
                output_field_source_ids.setdefault(field_name, [provenance.source_id])
        input_audit = {
            "semantic_import": {
                "prompt_version": SEMANTIC_IMPORT_PROMPT_VERSION,
                "prompt_sha256": sha256_text(prompt),
                "source_path": path.as_posix(),
                "source_sha256": source_hash,
                "source_text_sha256": sha256_text(text),
                "source_segment_count": len(source_segments),
                "source_segments_sha256": sha256_text(canonical_json(source_segments)),
                "source_segments": source_segments,
                "output_text_provenance_count": len(output_text_provenance),
                "output_text_provenance_sha256": sha256_text(
                    canonical_json(output_text_provenance)
                ),
                "output_text_provenance": output_text_provenance,
                "output_field_source_ids": output_field_source_ids,
            }
        }
        available_from = draft.available_from or datetime.combine(
            next_trading_day(draft.trade_date), time(0, 0, 0), tzinfo=KST
        )
        blind_predictions = _with_import_provenance(draft.blind_predictions, provenance)
        observed_events = _with_import_provenance(draft.observed_events, provenance)
        event_ticker_edges = _with_import_provenance(
            [
                edge.model_copy(update={"episode_id": episode_id})
                for edge in draft.event_ticker_edges
            ],
            provenance,
        )
        lessons = _semantic_claims_with_episode_support(
            draft.lessons,
            provenance=provenance,
            episode_id=episode_id,
            support_field="support_episode_ids",
        )
        counterexamples = _semantic_claims_with_episode_support(
            draft.counterexamples,
            provenance=provenance,
            episode_id=episode_id,
            support_field="contradiction_episode_ids",
        )
        return ResearchEpisode(
            episode_id=episode_id,
            trade_date=draft.trade_date,
            cutoff_at=draft.cutoff_at,
            created_at=now_kst(),
            research_version=draft.research_version,
            input_news_files=draft.input_news_files,
            input_news_hashes=draft.input_news_hashes,
            input_audit=input_audit,
            price_source_snapshot=draft.price_source_snapshot or {"source": "unknown"},
            blind_analysis=BlindAnalysis(
                summary=draft.summary,
                open_world_mechanisms=draft.open_world_mechanisms,
                initial_uncertainties=draft.initial_uncertainties,
                provenance=[provenance],
            ),
            blind_predictions=blind_predictions,
            observed_events=observed_events,
            event_ticker_edges=event_ticker_edges,
            lessons=lessons,
            counterexamples=counterexamples,
            misses=draft.misses,
            provenance=[provenance],
            available_from=available_from,
        )


def _with_import_provenance[TProvenanceModel: ProvenanceModel](
    items: list[TProvenanceModel],
    provenance: Provenance,
) -> list[TProvenanceModel]:
    updated: list[TProvenanceModel] = []
    for item in items:
        if item.provenance:
            updated.append(item)
        else:
            updated.append(item.model_copy(update={"provenance": [provenance]}))
    return updated


def _semantic_claims_with_episode_support(
    claims: list[MemoryClaim],
    *,
    provenance: Provenance,
    episode_id: str,
    support_field: Literal["support_episode_ids", "contradiction_episode_ids"],
) -> list[MemoryClaim]:
    updated_claims: list[MemoryClaim] = []
    for claim in claims:
        support_ids = (
            claim.support_episode_ids
            if support_field == "support_episode_ids"
            else claim.contradiction_episode_ids
        )
        updates: dict[str, object] = {}
        if isinstance(support_ids, list) and not support_ids:
            updates[support_field] = [episode_id]
        if not claim.provenance:
            updates["provenance"] = [provenance]
        if updates:
            updated_claims.append(claim.model_copy(update=updates))
        else:
            updated_claims.append(claim)
    return updated_claims


def _output_text_provenance(
    draft: SemanticResearchDraft,
    *,
    source_id: str,
    source_segments: list[dict[str, object]],
) -> list[dict[str, object]]:
    source_segment_indices: list[int] = []
    for segment in source_segments:
        index = segment.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            source_segment_indices.append(index)
    records: list[dict[str, object]] = []
    records.extend(
        _output_text_field_provenance(
            field_name="blind_analysis.summary",
            text=draft.summary,
            source_id=source_id,
            source_segment_indices=source_segment_indices,
        )
    )
    for item_index, mechanism in enumerate(draft.open_world_mechanisms, start=1):
        records.extend(
            _output_text_field_provenance(
                field_name="blind_analysis.open_world_mechanisms",
                text=mechanism,
                source_id=source_id,
                source_segment_indices=source_segment_indices,
                item_index=item_index,
            )
        )
    for item_index, uncertainty in enumerate(draft.initial_uncertainties, start=1):
        records.extend(
            _output_text_field_provenance(
                field_name="blind_analysis.initial_uncertainties",
                text=uncertainty,
                source_id=source_id,
                source_segment_indices=source_segment_indices,
                item_index=item_index,
            )
        )
    for item_index, candidate in enumerate(draft.blind_predictions, start=1):
        for field_name, text in (
            ("blind_predictions.thesis", candidate.thesis),
            ("blind_predictions.why_now", candidate.why_now),
            ("blind_predictions.novel_reasoning", candidate.novel_reasoning),
        ):
            records.extend(
                _output_text_field_provenance(
                    field_name=field_name,
                    text=text,
                    source_id=source_id,
                    source_segment_indices=source_segment_indices,
                    item_index=item_index,
                )
            )
        for field_name, values in (
            ("blind_predictions.causal_chain", candidate.causal_chain),
            ("blind_predictions.direct_evidence", candidate.direct_evidence),
            ("blind_predictions.inferred_evidence", candidate.inferred_evidence),
            (
                "blind_predictions.market_memory_evidence",
                candidate.market_memory_evidence,
            ),
            ("blind_predictions.prior_positive_cases", candidate.prior_positive_cases),
            ("blind_predictions.prior_negative_cases", candidate.prior_negative_cases),
            ("blind_predictions.counterarguments", candidate.counterarguments),
            (
                "blind_predictions.disconfirming_conditions",
                candidate.disconfirming_conditions,
            ),
        ):
            records.extend(
                _output_text_sequence_provenance(
                    field_name=field_name,
                    values=values,
                    source_id=source_id,
                    source_segment_indices=source_segment_indices,
                    item_index=item_index,
                )
            )
    for item_index, event in enumerate(draft.observed_events, start=1):
        for field_name, text in (
            ("observed_events.title", event.title),
            ("observed_events.body", event.body),
        ):
            records.extend(
                _output_text_field_provenance(
                    field_name=field_name,
                    text=text,
                    source_id=source_id,
                    source_segment_indices=source_segment_indices,
                    item_index=item_index,
                )
            )
    for item_index, edge in enumerate(draft.event_ticker_edges, start=1):
        for field_name, text in (
            ("event_ticker_edges.relation_explanation", edge.relation_explanation),
            ("event_ticker_edges.temporal_validity", edge.temporal_validity),
        ):
            records.extend(
                _output_text_field_provenance(
                    field_name=field_name,
                    text=text,
                    source_id=source_id,
                    source_segment_indices=source_segment_indices,
                    item_index=item_index,
                )
            )
        for field_name, values in (
            ("event_ticker_edges.fundamental_evidence", edge.fundamental_evidence),
            ("event_ticker_edges.narrative_evidence", edge.narrative_evidence),
            ("event_ticker_edges.market_memory_evidence", edge.market_memory_evidence),
        ):
            records.extend(
                _output_text_sequence_provenance(
                    field_name=field_name,
                    values=values,
                    source_id=source_id,
                    source_segment_indices=source_segment_indices,
                    item_index=item_index,
                )
            )
    for field_name, claims in (
        ("lessons", draft.lessons),
        ("counterexamples", draft.counterexamples),
    ):
        for item_index, claim in enumerate(claims, start=1):
            for claim_field_name, text in (
                (f"{field_name}.statement", claim.statement),
                (f"{field_name}.mechanism", claim.mechanism),
                (f"{field_name}.scope", claim.scope),
            ):
                records.extend(
                    _output_text_field_provenance(
                        field_name=claim_field_name,
                        text=text,
                        source_id=source_id,
                        source_segment_indices=source_segment_indices,
                        item_index=item_index,
                    )
                )
            for claim_field_name, values in (
                (f"{field_name}.conditions", claim.conditions),
                (f"{field_name}.failure_modes", claim.failure_modes),
            ):
                records.extend(
                    _output_text_sequence_provenance(
                        field_name=claim_field_name,
                        values=values,
                        source_id=source_id,
                        source_segment_indices=source_segment_indices,
                        item_index=item_index,
                    )
                )
    records.extend(
        _output_text_sequence_provenance(
            field_name="misses",
            values=draft.misses,
            source_id=source_id,
            source_segment_indices=source_segment_indices,
        )
    )
    return records


def _output_text_sequence_provenance(
    *,
    field_name: str,
    values: list[str],
    source_id: str,
    source_segment_indices: list[int],
    item_index: int | None = None,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for subitem_index, value in enumerate(values, start=1):
        records.extend(
            _output_text_field_provenance(
                field_name=f"{field_name}[{subitem_index}]",
                text=value,
                source_id=source_id,
                source_segment_indices=source_segment_indices,
                item_index=item_index,
            )
        )
    return records


def _output_text_field_provenance(
    *,
    field_name: str,
    text: str,
    source_id: str,
    source_segment_indices: list[int],
    item_index: int | None = None,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for sentence_index, segment in enumerate(_source_segments(text), start=1):
        record: dict[str, object] = {
            "field_name": field_name,
            "sentence_index": sentence_index,
            "text_sha256": segment["text_sha256"],
            "excerpt": segment["excerpt"],
            "source_ids": [source_id],
            "source_segment_indices": source_segment_indices,
        }
        if item_index is not None:
            record["item_index"] = item_index
        records.append(record)
    return records


def _source_segments(text: str) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    start = 0
    index = 1
    sentence_endings = {".", "?", "!", "。", "？", "！"}
    for position, character in enumerate(text):
        if character not in sentence_endings and character not in {"\n", "\r"}:
            continue
        next_position = position + 1
        raw_segment = text[start:next_position]
        stripped_start, stripped_end, segment = _trimmed_span(raw_segment, start)
        if segment:
            segments.append(_source_segment(index, segment, stripped_start, stripped_end))
            index += 1
        start = next_position
    stripped_start, stripped_end, tail = _trimmed_span(text[start:], start)
    if tail:
        segments.append(_source_segment(index, tail, stripped_start, stripped_end))
    return segments


def _source_segment(
    index: int,
    text: str,
    start: int,
    end: int,
) -> dict[str, object]:
    return {
        "index": index,
        "char_start": start,
        "char_end": end,
        "text_sha256": sha256_text(text),
        "excerpt": text[:240],
    }


def _trimmed_span(raw_segment: str, absolute_start: int) -> tuple[int, int, str]:
    leading = len(raw_segment) - len(raw_segment.lstrip())
    trailing = len(raw_segment.rstrip())
    start = absolute_start + leading
    end = absolute_start + trailing
    return start, end, raw_segment.strip()
