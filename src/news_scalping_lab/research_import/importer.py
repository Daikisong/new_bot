"""Strict and semantic research import."""

from __future__ import annotations

import re
import shutil
from datetime import date, datetime, time
from pathlib import Path

from news_scalping_lab.contracts.models import BlindAnalysis, Provenance, ResearchEpisode
from news_scalping_lab.research_import.bundle import (
    import_bundle_episode,
    looks_like_bundle,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    KST,
    file_sha256,
    next_calendar_day,
    now_kst,
    read_json,
    stable_id,
)


class ResearchImporter:
    def __init__(self, root: Path, store: ResearchStore | None = None) -> None:
        self.root = root
        self.store = store or ResearchStore(root)
        self.raw_dir = root / "data" / "raw" / "research"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def import_path(self, path: Path, *, mode: str = "auto") -> ResearchEpisode:
        if mode not in {"auto", "strict", "semantic", "bundle"}:
            raise ValueError("mode must be auto, strict, semantic, or bundle")
        resolved = path.resolve()
        preserved = self._preserve_raw(resolved)
        if mode == "bundle" or (mode == "auto" and looks_like_bundle(preserved)):
            episode = import_bundle_episode(preserved)
        elif mode == "strict" or (mode == "auto" and resolved.suffix.lower() == ".json"):
            episode = self._strict_import(preserved)
        else:
            episode = self._semantic_import(preserved)
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

    def _semantic_import(self, path: Path) -> ResearchEpisode:
        text = path.read_text(encoding="utf-8", errors="replace")
        trade_day = self._infer_trade_date(path, text)
        cutoff_at = datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST)
        source_hash = file_sha256(path)
        episode_id = stable_id("EP", trade_day.isoformat(), source_hash)
        provenance = Provenance(
            source_id=stable_id("SRC", path.as_posix(), source_hash),
            source_type="semantic_research_source",
            uri=path.as_posix(),
            content_sha256=source_hash,
            excerpt=text[:500],
            observed_at=now_kst(),
        )
        summary = (
            self._extract_section(text)
            or "Semantic import preserved source and created a canonical shell."
        )
        available_from = datetime.combine(next_calendar_day(trade_day), time(0, 0, 0), tzinfo=KST)
        return ResearchEpisode(
            episode_id=episode_id,
            trade_date=trade_day,
            cutoff_at=cutoff_at,
            created_at=now_kst(),
            research_version="semantic-mock-v1",
            input_news_files=[],
            input_news_hashes=[],
            price_source_snapshot={"source": "unknown"},
            blind_analysis=BlindAnalysis(
                summary=summary,
                open_world_mechanisms=[
                    "imported research source -> abstract mechanism extraction required",
                    "postmortem evidence -> available only after its trade date",
                ],
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

    def _infer_trade_date(self, path: Path, text: str) -> date:
        haystack = f"{path.name}\n{text[:2000]}"
        match = re.search(r"(20[0-9]{2})[-_./]?(0[1-9]|1[0-2])[-_./]?([0-2][0-9]|3[01])", haystack)
        if match:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return now_kst().date()

    def _extract_section(self, text: str) -> str:
        stripped = re.sub(r"\s+", " ", text).strip()
        return stripped[:1000]
