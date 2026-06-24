"""Context manifest assembly."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.brain.compiler import current_brain_file_hashes, current_brain_version
from news_scalping_lab.contracts.models import ContextManifest, PriceSnapshot
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import file_sha256, stable_id


class ContextAssembler:
    def __init__(self, root: Path, store: ResearchStore | None = None) -> None:
        self.root = root
        self.store = store or ResearchStore(root)

    def assemble(
        self,
        *,
        mode: str,
        trade_date: date,
        cutoff_at: datetime,
        run_seed: str,
        retrieved_episode_ids: list[str] | None = None,
        web_queries: list[str] | None = None,
    ) -> ContextManifest:
        accepted = [
            episode
            for episode in self.store.list_accepted()
            if episode.available_from.date() <= trade_date
        ]
        accepted_ids = [episode.episode_id for episode in accepted]
        counterexample_ids = [
            episode.episode_id for episode in accepted if episode.counterexamples
        ]
        swept_ids = accepted_ids if mode in {"exhaustive", "brain"} else []
        errors: list[str] = []
        if mode == "exhaustive" and len(swept_ids) != len(accepted_ids):
            errors.append("exhaustive coverage mismatch")
        manifest = ContextManifest(
            run_id=stable_id("RUN", trade_date.isoformat(), mode, run_seed),
            mode=mode,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            brain_version=current_brain_version(self.root),
            brain_files=list(current_brain_file_hashes(self.root).keys()),
            brain_file_hashes=current_brain_file_hashes(self.root),
            shard_brain_files=list(current_shard_brain_file_hashes(self.root).keys()),
            shard_brain_file_hashes=current_shard_brain_file_hashes(self.root),
            accepted_episode_count=len(accepted_ids),
            swept_episode_count=len(swept_ids),
            swept_episode_ids=swept_ids,
            retrieved_episode_ids=retrieved_episode_ids or [],
            counterexample_episode_ids=counterexample_ids,
            token_counts={},
            truncations=[],
            web_queries=web_queries or [],
            web_sources=[],
            price_snapshot=PriceSnapshot(
                source_name="blind-guarded",
                allowed_through=date.fromordinal(trade_date.toordinal() - 1),
            ),
            llm_model_config={"provider": "mock-compatible", "mode": mode},
            prompt_hashes={},
            errors=errors,
        )
        if mode == "exhaustive" and manifest.swept_episode_count != manifest.accepted_episode_count:
            manifest.errors.append("swept_episode_count must equal accepted_episode_count")
        return manifest


def current_shard_brain_file_hashes(root: Path) -> dict[str, str]:
    shard_dir = root / "memory" / "shard_brains" / "current"
    if not shard_dir.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(shard_dir.glob("*.md"))
        if path.is_file()
    }
