"""GPT Web session pack export."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.config import Settings
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.contracts.models import CompanyMemory, ResearchEpisode
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import (
    canonical_json,
    combine_kst,
    default_news_window_start,
    file_sha256,
    is_available_as_of,
    read_json,
    sha256_text,
    stable_id,
    write_json,
)


@dataclass(frozen=True)
class TemporalMemoryContext:
    text: str
    included_paths: list[str]
    omitted: list[dict[str, str]]
    errors: list[str]


class SessionPackFutureContextError(RuntimeError):
    def __init__(self, output_dir: Path, errors: list[str]) -> None:
        self.output_dir = output_dir
        self.errors = errors
        super().__init__("session pack brain context contains future-unavailable research")


class SessionPackBudgetExceededError(RuntimeError):
    def __init__(self, output_dir: Path, errors: list[str]) -> None:
        self.output_dir = output_dir
        self.errors = errors
        super().__init__("session pack omitted available research because the token budget is too small")


def export_session_pack(
    settings: Settings,
    *,
    news_csv: Path,
    trade_date: date,
    mode: str,
    cutoff_at: datetime | None = None,
) -> Path:
    mode = normalize_analysis_mode(mode)
    cutoff_at = cutoff_at or combine_kst(trade_date, "08:59:59")
    output_dir = settings.path(settings.output_dirs.session_packs) / trade_date.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    full_batch = load_news_csv(news_csv, trade_date=trade_date)
    news_window_start_at = default_news_window_start(trade_date)
    batch = full_batch.within_window(news_window_start_at, cutoff_at)
    included_news_event_ids = [item.event_id for item in batch.items]
    included_news_id_set = set(included_news_event_ids)
    excluded_news_event_ids = [
        item.event_id for item in full_batch.items if item.event_id not in included_news_id_set
    ]
    news_sha256 = file_sha256(news_csv)
    store = ResearchStore(settings.project_root)
    all_accepted = store.list_accepted()
    available = [
        episode for episode in all_accepted if is_available_as_of(episode.available_from, cutoff_at)
    ]
    unavailable = [
        episode for episode in all_accepted if not is_available_as_of(episode.available_from, cutoff_at)
    ]
    context_run_id = stable_id(
        "SESSION",
        trade_date.isoformat(),
        cutoff_at.isoformat(),
        mode,
        news_sha256,
    )
    brain_context = ContextAssembler(
        settings.project_root,
        store=store,
        shard_episode_count=settings.limits.shard_episode_count,
    )._brain_context_files(
        run_id=context_run_id,
        cutoff_at=cutoff_at,
        accepted=available,
    )
    brain_files = list(brain_context.brain_file_hashes)
    brain_file_hashes = brain_context.brain_file_hashes
    shard_brain_files = list(brain_context.shard_brain_file_hashes)
    shard_brain_hashes = brain_context.shard_brain_file_hashes
    brain_text = _read_context_files(settings.project_root, brain_files, suffixes={".md"})
    shard_brain_text = _read_context_files(
        settings.project_root,
        shard_brain_files,
        suffixes={".md"},
        empty_message="No shard brain summaries are available. Run `nslab brain rebuild --mode full`.\n",
    )
    brain_text = (
        f"{brain_text.rstrip()}\n\n# Shard Brain Summaries\n\n{shard_brain_text}".strip()
        + "\n"
    )
    news_text = _render_current_news(batch)
    company_memory = _read_company_memory_as_of(settings.project_root, cutoff_at)
    market_context = _read_temporal_memory_dir_as_of(
        settings.project_root,
        settings.project_root / "memory" / "market_memory",
        cutoff_at,
        label="market_context",
    )
    company_memory_text = company_memory.text
    market_context_text = market_context.text
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
                "reason": "episode_available_from_after_cutoff",
                "omitted_episode_ids": unavailable_ids,
            }
        )
    if excluded_news_event_ids:
        truncations.append(
            {
                "artifact": "current_news.md",
                "reason": "news_outside_blind_window",
                "omitted_event_ids": excluded_news_event_ids,
            }
        )
    if company_memory.omitted:
        truncations.append(
            {
                "artifact": "company_memory.md",
                "reason": "temporal_company_memory_omitted",
                "omitted": company_memory.omitted,
            }
        )
    if market_context.omitted:
        truncations.append(
            {
                "artifact": "market_context.md",
                "reason": "temporal_market_context_omitted",
                "omitted": market_context.omitted,
            }
        )
    errors: list[str] = []
    if omitted_ids:
        errors.append("session pack omitted available episodes due to token budget")
    if unavailable_ids:
        errors.append("session pack excluded future-unavailable episodes")
    errors.extend(company_memory.errors)
    errors.extend(market_context.errors)
    context_leak_errors = _future_context_leak_errors(
        root=settings.project_root,
        relative_paths=[*brain_files, *shard_brain_files],
        unavailable=unavailable,
    )
    errors.extend(context_leak_errors)
    if context_leak_errors:
        write_json(
            output_dir / "manifest.json",
            {
                "schema_version": "nslab.session_pack_manifest.v1",
                "blocked": True,
                "trade_date": trade_date.isoformat(),
                "cutoff_at": cutoff_at.isoformat(),
                "as_of": cutoff_at.isoformat(),
                "mode": mode,
                "brain_version": brain_context.brain_version,
                "brain_files": brain_files,
                "brain_file_hashes": brain_file_hashes,
                "shard_brain_files": shard_brain_files,
                "shard_brain_file_hashes": shard_brain_hashes,
                "shard_brain_count": len(shard_brain_files),
                "news_file": _relative_to_root(full_batch.path, settings.project_root),
                "news_sha256": news_sha256,
                "news_window_start_at": news_window_start_at.isoformat(),
                "news_window_end_at": cutoff_at.isoformat(),
                "news_row_count": full_batch.row_count,
                "included_news_row_count": batch.row_count,
                "excluded_news_row_count": full_batch.row_count - batch.row_count,
                "current_news_event_ids": included_news_event_ids,
                "excluded_news_event_ids": excluded_news_event_ids,
                "accepted_episode_count": len(all_accepted),
                "available_episode_count": len(available),
                "unavailable_episode_ids": unavailable_ids,
                "included_company_memory_files": company_memory.included_paths,
                "omitted_company_memory_files": company_memory.omitted,
                "included_market_context_files": market_context.included_paths,
                "omitted_market_context_files": market_context.omitted,
                "truncations": truncations,
                "errors": errors,
            },
        )
        raise SessionPackFutureContextError(output_dir, context_leak_errors)

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
    token_counts = {
        file_name: _estimate_tokens((output_dir / file_name).read_text(encoding="utf-8"))
        for file_name in pack_files
    }
    manifest: dict[str, object] = {
        "schema_version": "nslab.session_pack_manifest.v1",
        "blocked": bool(omitted_ids),
        "trade_date": trade_date.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "as_of": cutoff_at.isoformat(),
        "mode": mode,
        "brain_version": brain_context.brain_version,
        "brain_files": brain_files,
        "brain_file_hashes": brain_file_hashes,
        "shard_brain_files": shard_brain_files,
        "shard_brain_file_hashes": shard_brain_hashes,
        "shard_brain_count": len(shard_brain_files),
        "news_file": _relative_to_root(full_batch.path, settings.project_root),
        "news_sha256": news_sha256,
        "news_window_start_at": news_window_start_at.isoformat(),
        "news_window_end_at": cutoff_at.isoformat(),
        "news_row_count": full_batch.row_count,
        "included_news_row_count": batch.row_count,
        "excluded_news_row_count": full_batch.row_count - batch.row_count,
        "current_news_event_ids": included_news_event_ids,
        "excluded_news_event_ids": excluded_news_event_ids,
        "accepted_episode_count": len(all_accepted),
        "available_episode_count": len(available),
        "included_episode_count": len(included),
        "included_episode_ids": [episode.episode_id for episode in included],
        "omitted_episode_ids": [*omitted_ids, *unavailable_ids],
        "unavailable_episode_ids": unavailable_ids,
        "included_company_memory_files": company_memory.included_paths,
        "omitted_company_memory_files": company_memory.omitted,
        "included_market_context_files": market_context.included_paths,
        "omitted_market_context_files": market_context.omitted,
        "token_budget": token_budget,
        "token_counts": token_counts,
        "token_count_total": sum(token_counts.values()),
        "pack_files": pack_files,
        "pack_file_count": len(pack_files),
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
    if omitted_ids:
        raise SessionPackBudgetExceededError(output_dir, errors)
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


def _read_context_files(
    root: Path,
    relative_paths: list[str],
    *,
    suffixes: set[str],
    empty_message: str = "",
) -> str:
    chunks: list[str] = []
    for relative_path in sorted(relative_paths):
        file_path = root / relative_path
        if not file_path.is_file() or file_path.suffix.lower() not in suffixes:
            continue
        chunks.append(f"\n<!-- {relative_path} -->\n{file_path.read_text(encoding='utf-8')}")
    if not chunks:
        return empty_message
    return "\n".join(chunks).strip() + "\n"


def _read_memory_dir(path: Path) -> str:
    if not path.exists():
        return ""
    chunks: list[str] = []
    for file_path in sorted(path.glob("*")):
        if file_path.is_file() and file_path.suffix.lower() in {".md", ".txt", ".json", ".jsonl"}:
            chunks.append(f"\n<!-- {file_path.name} -->\n{file_path.read_text(encoding='utf-8')}")
    return "\n".join(chunks).strip() + ("\n" if chunks else "")


def _read_company_memory_as_of(root: Path, cutoff_at: datetime) -> TemporalMemoryContext:
    directory = root / "memory" / "company_memory"
    if not directory.exists():
        return TemporalMemoryContext(text="", included_paths=[], omitted=[], errors=[])
    chunks: list[str] = []
    included_paths: list[str] = []
    omitted: list[dict[str, str]] = []
    errors: list[str] = []
    for file_path in sorted(directory.glob("*.json")):
        relative_path = _relative_to_root(file_path, root)
        try:
            memory = CompanyMemory.model_validate(read_json(file_path))
        except Exception:
            omitted.append({"path": relative_path, "reason": "invalid_company_memory_schema"})
            errors.append(f"session pack omitted invalid company memory: {relative_path}")
            continue
        if not is_available_as_of(memory.known_at, cutoff_at):
            omitted.append(
                {
                    "path": relative_path,
                    "reason": "company_memory_known_after_cutoff",
                    "known_at": memory.known_at.isoformat(),
                }
            )
            errors.append(f"session pack excluded future company memory: {relative_path}")
            continue
        included_paths.append(relative_path)
        chunks.append(f"\n<!-- {relative_path} -->\n{file_path.read_text(encoding='utf-8')}")
    return TemporalMemoryContext(
        text="\n".join(chunks).strip() + ("\n" if chunks else ""),
        included_paths=included_paths,
        omitted=omitted,
        errors=errors,
    )


def _read_temporal_memory_dir_as_of(
    root: Path,
    directory: Path,
    cutoff_at: datetime,
    *,
    label: str,
) -> TemporalMemoryContext:
    if not directory.exists():
        return TemporalMemoryContext(text="", included_paths=[], omitted=[], errors=[])
    chunks: list[str] = []
    included_paths: list[str] = []
    omitted: list[dict[str, str]] = []
    errors: list[str] = []
    for file_path in sorted(directory.glob("*")):
        if not file_path.is_file():
            continue
        relative_path = _relative_to_root(file_path, root)
        if file_path.suffix.lower() == ".jsonl":
            included_lines, line_included_paths, line_omitted, line_errors = (
                _read_temporal_jsonl_as_of(
                    file_path,
                    relative_path=relative_path,
                    cutoff_at=cutoff_at,
                    label=label,
                )
            )
            included_paths.extend(line_included_paths)
            omitted.extend(line_omitted)
            errors.extend(line_errors)
            if included_lines:
                chunks.append(f"\n<!-- {relative_path} -->\n" + "\n".join(included_lines))
            continue
        if file_path.suffix.lower() == ".json":
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                omitted.append({"path": relative_path, "reason": "invalid_json"})
                errors.append(f"session pack omitted invalid {label} memory: {relative_path}")
                continue
            included_payload, payload_omitted, payload_errors = _filter_temporal_json_payload(
                payload,
                relative_path=relative_path,
                cutoff_at=cutoff_at,
                label=label,
            )
            omitted.extend(payload_omitted)
            errors.extend(payload_errors)
            if included_payload is not None:
                included_paths.append(relative_path)
                chunks.append(
                    f"\n<!-- {relative_path} -->\n"
                    + json.dumps(
                        included_payload,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
            continue
        if file_path.suffix.lower() in {".md", ".txt"}:
            omitted.append({"path": relative_path, "reason": "missing_temporal_scope"})
            errors.append(f"session pack omitted unscoped {label} memory: {relative_path}")
    return TemporalMemoryContext(
        text="\n".join(chunks).strip() + ("\n" if chunks else ""),
        included_paths=included_paths,
        omitted=omitted,
        errors=errors,
    )


def _read_temporal_jsonl_as_of(
    path: Path,
    *,
    relative_path: str,
    cutoff_at: datetime,
    label: str,
) -> tuple[list[str], list[str], list[dict[str, str]], list[str]]:
    included_lines: list[str] = []
    included_paths: list[str] = []
    omitted: list[dict[str, str]] = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        entry_path = f"{relative_path}#L{line_number}"
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            omitted.append({"path": entry_path, "reason": "invalid_jsonl"})
            errors.append(f"session pack omitted invalid {label} memory: {entry_path}")
            continue
        if not isinstance(payload, dict):
            omitted.append({"path": entry_path, "reason": "non_object_jsonl"})
            errors.append(f"session pack omitted non-object {label} memory: {entry_path}")
            continue
        timestamp, reason = _payload_temporal_scope(payload)
        if timestamp is None:
            omitted.append({"path": entry_path, "reason": reason})
            errors.append(f"session pack omitted unscoped {label} memory: {entry_path}")
            continue
        if not is_available_as_of(timestamp, cutoff_at):
            omitted.append(
                {
                    "path": entry_path,
                    "reason": f"{reason}_after_cutoff",
                    "available_at": timestamp.isoformat(),
                }
            )
            errors.append(f"session pack excluded future {label} memory: {entry_path}")
            continue
        included_lines.append(canonical_json(payload))
        included_paths.append(entry_path)
    return included_lines, included_paths, omitted, errors


def _filter_temporal_json_payload(
    payload: object,
    *,
    relative_path: str,
    cutoff_at: datetime,
    label: str,
) -> tuple[object | None, list[dict[str, str]], list[str]]:
    if isinstance(payload, dict):
        timestamp, reason = _payload_temporal_scope(payload)
        if timestamp is None:
            return (
                None,
                [{"path": relative_path, "reason": reason}],
                [f"session pack omitted unscoped {label} memory: {relative_path}"],
            )
        if not is_available_as_of(timestamp, cutoff_at):
            return (
                None,
                [
                    {
                        "path": relative_path,
                        "reason": f"{reason}_after_cutoff",
                        "available_at": timestamp.isoformat(),
                    }
                ],
                [f"session pack excluded future {label} memory: {relative_path}"],
            )
        return payload, [], []
    if isinstance(payload, list):
        included: list[object] = []
        omitted: list[dict[str, str]] = []
        errors: list[str] = []
        for index, item in enumerate(payload):
            entry_path = f"{relative_path}#{index}"
            if not isinstance(item, dict):
                omitted.append({"path": entry_path, "reason": "non_object_json"})
                errors.append(f"session pack omitted non-object {label} memory: {entry_path}")
                continue
            timestamp, reason = _payload_temporal_scope(item)
            if timestamp is None:
                omitted.append({"path": entry_path, "reason": reason})
                errors.append(f"session pack omitted unscoped {label} memory: {entry_path}")
                continue
            if not is_available_as_of(timestamp, cutoff_at):
                omitted.append(
                    {
                        "path": entry_path,
                        "reason": f"{reason}_after_cutoff",
                        "available_at": timestamp.isoformat(),
                    }
                )
                errors.append(f"session pack excluded future {label} memory: {entry_path}")
                continue
            included.append(item)
        return (included if included else None), omitted, errors
    return (
        None,
        [{"path": relative_path, "reason": "unsupported_json_payload"}],
        [f"session pack omitted unsupported {label} memory: {relative_path}"],
    )


def _payload_temporal_scope(payload: dict[str, object]) -> tuple[datetime | None, str]:
    for field in ("available_from", "known_at"):
        raw_value = payload.get(field)
        if not isinstance(raw_value, str):
            continue
        try:
            return datetime.fromisoformat(raw_value), field
        except ValueError:
            return None, f"invalid_{field}"
    return None, "missing_temporal_scope"


def _read_brain_files(path: Path, *, root: Path) -> tuple[str, list[str]]:
    if not path.exists():
        return "", []
    chunks: list[str] = []
    files: list[str] = []
    for file_path in sorted(path.glob("*.md")):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(root).as_posix()
        files.append(relative_path)
        chunks.append(f"\n<!-- {relative_path} -->\n{file_path.read_text(encoding='utf-8')}")
    return "\n".join(chunks).strip() + ("\n" if chunks else ""), files


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


def _render_current_news(batch: Any) -> str:
    return "\n\n".join(
        f"## {item.event_id}\n{item.title}\n\n{item.body}" for item in batch.items
    )


def _future_context_leak_errors(
    *,
    root: Path,
    relative_paths: list[str],
    unavailable: list[ResearchEpisode],
) -> list[str]:
    if not unavailable:
        return []
    errors: list[str] = []
    unavailable_ids = [episode.episode_id for episode in unavailable]
    for relative_path in relative_paths:
        path = root / relative_path
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for episode_id in unavailable_ids:
            if episode_id in text:
                errors.append(
                    f"session pack context file contains future episode {episode_id}: "
                    f"{relative_path}"
                )
    return errors


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
