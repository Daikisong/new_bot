"""GPT Web session pack export."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from news_scalping_lab.brain.compiler import current_brain_file_hashes, current_brain_version
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import ResearchEpisode
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import file_sha256, sha256_text, write_json


def export_session_pack(settings: Settings, *, news_csv: Path, trade_date: date, mode: str) -> Path:
    output_dir = settings.path(settings.output_dirs.session_packs) / trade_date.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    batch = load_news_csv(news_csv, trade_date=trade_date)
    store = ResearchStore(settings.project_root)
    all_accepted = store.list_accepted()
    available = [
        episode for episode in all_accepted if episode.available_from.date() <= trade_date
    ]
    unavailable = [
        episode for episode in all_accepted if episode.available_from.date() > trade_date
    ]
    brain_texts: list[str] = []
    for path in sorted((settings.project_root / "brain" / "current").glob("*.md")):
        brain_texts.append(f"\n<!-- {path.name} -->\n{path.read_text(encoding='utf-8')}")
    shard_brain_text, shard_brain_files, shard_brain_hashes = _read_shard_brains(
        settings.project_root / "memory" / "shard_brains" / "current",
        root=settings.project_root,
    )
    brain_text = "\n".join(brain_texts)
    brain_text = (
        f"{brain_text.rstrip()}\n\n# Shard Brain Summaries\n\n{shard_brain_text}".strip()
        + "\n"
    )
    news_text = "\n\n".join(
        f"## {item.event_id}\n{item.title}\n\n{item.body}" for item in batch.items
    )
    company_memory_text = _read_memory_dir(settings.project_root / "memory" / "company_memory")
    market_context_text = _read_memory_dir(settings.project_root / "memory" / "market_memory")
    if not company_memory_text:
        company_memory_text = (
            "Company memory is data-driven and may be empty. New entities must be investigated.\n"
        )
    if not market_context_text:
        market_context_text = "Use D-1 and earlier market context only during blind analysis.\n"

    token_budget = max(1, settings.limits.session_pack_token_budget)
    fixed_texts = {
        "system_instructions.md": (
            "Use open-world reasoning. Do not treat retrieval misses as candidate blockers. "
            "Do not use cutoff-after evidence for blind prediction.\n"
        ),
        "research_brain.md": brain_text,
        "current_news.md": news_text,
        "company_memory.md": company_memory_text,
        "market_context.md": market_context_text,
    }
    fixed_tokens = sum(_estimate_tokens(text) for text in fixed_texts.values())
    memory_budget = max(0, token_budget - fixed_tokens)
    memory_text, included, omitted_budget = _build_memory_cases(available, memory_budget)
    omitted_ids = [episode.episode_id for episode in omitted_budget]
    unavailable_ids = [episode.episode_id for episode in unavailable]
    truncations: list[dict[str, Any]] = []
    if omitted_ids:
        truncations.append(
            {
                "artifact": "memory_cases.md",
                "reason": "session_pack_token_budget_exceeded",
                "omitted_episode_ids": omitted_ids,
            }
        )
    if unavailable_ids:
        truncations.append(
            {
                "artifact": "memory_cases.md",
                "reason": "episode_available_from_after_trade_date",
                "omitted_episode_ids": unavailable_ids,
            }
        )
    errors: list[str] = []
    if omitted_ids:
        errors.append("session pack omitted available episodes due to token budget")
    if unavailable_ids:
        errors.append("session pack excluded future-unavailable episodes")

    (output_dir / "system_instructions.md").write_text(
        fixed_texts["system_instructions.md"],
        encoding="utf-8",
    )
    (output_dir / "research_brain.md").write_text(brain_text, encoding="utf-8")
    (output_dir / "memory_cases.md").write_text(memory_text, encoding="utf-8")
    (output_dir / "current_news.md").write_text(fixed_texts["current_news.md"], encoding="utf-8")
    (output_dir / "company_memory.md").write_text(fixed_texts["company_memory.md"], encoding="utf-8")
    (output_dir / "market_context.md").write_text(fixed_texts["market_context.md"], encoding="utf-8")
    pack_files = [
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "current_news.md",
        "company_memory.md",
        "market_context.md",
    ]
    manifest: dict[str, object] = {
        "schema_version": "nslab.session_pack_manifest.v1",
        "trade_date": trade_date.isoformat(),
        "mode": mode,
        "brain_version": current_brain_version(settings.project_root),
        "brain_file_hashes": current_brain_file_hashes(settings.project_root),
        "shard_brain_files": shard_brain_files,
        "shard_brain_file_hashes": shard_brain_hashes,
        "shard_brain_count": len(shard_brain_files),
        "news_file": news_csv.as_posix(),
        "news_sha256": file_sha256(news_csv),
        "accepted_episode_count": len(all_accepted),
        "available_episode_count": len(available),
        "included_episode_count": len(included),
        "included_episode_ids": [episode.episode_id for episode in included],
        "omitted_episode_ids": [*omitted_ids, *unavailable_ids],
        "unavailable_episode_ids": unavailable_ids,
        "token_budget": token_budget,
        "token_counts": {
            file_name: _estimate_tokens((output_dir / file_name).read_text(encoding="utf-8"))
            for file_name in pack_files
        },
        "pack_file_hashes": {
            file_name: file_sha256(output_dir / file_name) for file_name in pack_files
        },
        "pack_sha256": sha256_text(
            "\n".join(file_sha256(output_dir / file_name) for file_name in pack_files)
        ),
        "truncations": truncations,
        "errors": errors,
    }
    write_json(output_dir / "manifest.json", manifest)
    return output_dir


def _build_memory_cases(
    episodes: list[ResearchEpisode],
    token_budget: int,
) -> tuple[str, list[ResearchEpisode], list[ResearchEpisode]]:
    included: list[ResearchEpisode] = []
    omitted: list[ResearchEpisode] = []
    parts: list[str] = []
    used_tokens = 0
    for episode in episodes:
        block = _episode_block(episode)
        block_tokens = _estimate_tokens(block)
        if used_tokens + block_tokens > token_budget:
            omitted.append(episode)
            continue
        included.append(episode)
        parts.append(block)
        used_tokens += block_tokens
    if not parts:
        return "No episode memory cases fit within the session pack budget.\n", included, omitted
    return "\n\n".join(parts) + "\n", included, omitted


def _episode_block(episode: ResearchEpisode) -> str:
    mechanisms = "\n".join(
        f"- {mechanism}" for mechanism in episode.blind_analysis.open_world_mechanisms
    )
    postmortem = (
        f"\nPostmortem: {episode.postmortem.summary}\n"
        if episode.postmortem is not None
        else ""
    )
    return "\n".join(
        [
            f"## {episode.episode_id}",
            f"- Trade date: {episode.trade_date.isoformat()}",
            f"- Available from: {episode.available_from.isoformat()}",
            f"- Research version: {episode.research_version}",
            "",
            episode.blind_analysis.summary,
            "",
            "Mechanisms:",
            mechanisms or "- none",
            postmortem.rstrip(),
        ]
    ).strip()


def _read_memory_dir(path: Path) -> str:
    if not path.exists():
        return ""
    chunks: list[str] = []
    for file_path in sorted(path.glob("*")):
        if file_path.is_file() and file_path.suffix.lower() in {".md", ".txt", ".json", ".jsonl"}:
            chunks.append(f"\n<!-- {file_path.name} -->\n{file_path.read_text(encoding='utf-8')}")
    return "\n".join(chunks).strip() + ("\n" if chunks else "")


def _read_shard_brains(path: Path, *, root: Path) -> tuple[str, list[str], dict[str, str]]:
    if not path.exists():
        return "No shard brain summaries are available. Run `nslab brain rebuild --mode full`.\n", [], {}
    chunks: list[str] = []
    files: list[str] = []
    hashes: dict[str, str] = {}
    for file_path in sorted(path.glob("*.md")):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(root).as_posix()
        files.append(relative_path)
        hashes[relative_path] = file_sha256(file_path)
        chunks.append(f"\n<!-- {relative_path} -->\n{file_path.read_text(encoding='utf-8')}")
    if not chunks:
        return "No shard brain summaries are available. Run `nslab brain rebuild --mode full`.\n", [], {}
    return "\n".join(chunks).strip() + "\n", files, hashes


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0
