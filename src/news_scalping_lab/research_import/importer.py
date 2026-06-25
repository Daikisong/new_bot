"""Strict and semantic research import."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, time
from pathlib import Path

from news_scalping_lab.contracts.models import BlindAnalysis, Provenance, ResearchEpisode
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
                "output_field_source_ids": {
                    field_name: [provenance.source_id]
                    for field_name in SEMANTIC_IMPORT_REQUIRED_OUTPUT_FIELDS
                },
            }
        }
        available_from = draft.available_from or datetime.combine(
            next_trading_day(draft.trade_date), time(0, 0, 0), tzinfo=KST
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
            blind_predictions=[],
            observed_events=[],
            event_ticker_edges=[],
            lessons=[],
            counterexamples=[],
            misses=[],
            provenance=[provenance],
            available_from=available_from,
        )


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
