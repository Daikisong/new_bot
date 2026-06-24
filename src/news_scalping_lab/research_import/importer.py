"""Strict and semantic research import."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, time
from pathlib import Path

from news_scalping_lab.contracts.models import BlindAnalysis, Provenance, ResearchEpisode
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.research_import.bundle import (
    import_bundle_episode,
    looks_like_bundle,
)
from news_scalping_lab.research_import.semantic import (
    SEMANTIC_IMPORT_PROMPT_VERSION,
    SemanticResearchDraft,
    build_semantic_import_prompt,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    next_calendar_day,
    now_kst,
    read_json,
    sha256_text,
    stable_id,
    write_json,
)


class ResearchImporter:
    def __init__(
        self,
        root: Path,
        store: ResearchStore | None = None,
        llm: LLMProvider | None = None,
    ) -> None:
        self.root = root
        self.store = store or ResearchStore(root)
        self.llm = llm or DeterministicMockLLMProvider()
        self.raw_dir = root / "data" / "raw" / "research"
        self.trace_dir = root / "runs" / "traces"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

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
        provenance = Provenance(
            source_id=stable_id("SRC", path.as_posix(), file_sha256(path)),
            source_type="strict_research_json",
            uri=path.as_posix(),
            content_sha256=file_sha256(path),
            observed_at=now_kst(),
        )
        return episode.model_copy(update={"provenance": [*episode.provenance, provenance]})

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
        self._write_semantic_trace(path=path, source_hash=source_hash, prompt=prompt, draft=draft)

        episode_id = stable_id("EP", draft.trade_date.isoformat(), source_hash)
        provenance = Provenance(
            source_id=stable_id("SRC", path.as_posix(), source_hash),
            source_type="semantic_llm_structured_import",
            uri=path.as_posix(),
            content_sha256=source_hash,
            excerpt=text[:500],
            observed_at=now_kst(),
        )
        available_from = draft.available_from or datetime.combine(
            next_calendar_day(draft.trade_date), time(0, 0, 0), tzinfo=KST
        )
        return ResearchEpisode(
            episode_id=episode_id,
            trade_date=draft.trade_date,
            cutoff_at=draft.cutoff_at,
            created_at=now_kst(),
            research_version=draft.research_version,
            input_news_files=draft.input_news_files,
            input_news_hashes=draft.input_news_hashes,
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

    def _write_semantic_trace(
        self,
        *,
        path: Path,
        source_hash: str,
        prompt: str,
        draft: SemanticResearchDraft,
    ) -> None:
        trace_id = stable_id(
            "TRACE",
            "research_import.semantic",
            source_hash,
            canonical_json(draft.model_dump(mode="json")),
        )
        write_json(
            self.trace_dir / f"{trace_id}.json",
            {
                "trace_id": trace_id,
                "purpose": "research_import.semantic",
                "prompt_version": SEMANTIC_IMPORT_PROMPT_VERSION,
                "source_path": path.as_posix(),
                "source_sha256": source_hash,
                "prompt_sha256": sha256_text(prompt),
                "response_model": "SemanticResearchDraft",
                "output": draft.model_dump(mode="json"),
                "created_at": now_kst().isoformat(),
            },
        )
