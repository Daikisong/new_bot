"""GPT Web session pack export."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from news_scalping_lab.brain.compiler import current_brain_file_hashes, current_brain_version
from news_scalping_lab.config import Settings
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import file_sha256, write_json


def export_session_pack(settings: Settings, *, news_csv: Path, trade_date: date, mode: str) -> Path:
    output_dir = settings.path(settings.output_dirs.session_packs) / trade_date.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    batch = load_news_csv(news_csv, trade_date=trade_date)
    store = ResearchStore(settings.project_root)
    accepted = store.list_accepted()
    brain_texts: list[str] = []
    for path in sorted((settings.project_root / "brain" / "current").glob("*.md")):
        brain_texts.append(f"\n<!-- {path.name} -->\n{path.read_text(encoding='utf-8')}")

    (output_dir / "system_instructions.md").write_text(
        "Use open-world reasoning. Do not treat retrieval misses as candidate blockers. "
        "Do not use cutoff-after evidence for blind prediction.\n",
        encoding="utf-8",
    )
    (output_dir / "research_brain.md").write_text("\n".join(brain_texts), encoding="utf-8")
    (output_dir / "memory_cases.md").write_text(
        "\n".join(
            f"- {episode.episode_id}: {episode.blind_analysis.summary}" for episode in accepted
        ),
        encoding="utf-8",
    )
    (output_dir / "current_news.md").write_text(
        "\n\n".join(f"## {item.event_id}\n{item.title}\n\n{item.body}" for item in batch.items),
        encoding="utf-8",
    )
    (output_dir / "company_memory.md").write_text(
        "Company memory is data-driven and may be empty. New entities must be investigated.\n",
        encoding="utf-8",
    )
    (output_dir / "market_context.md").write_text(
        "Use D-1 and earlier market context only during blind analysis.\n",
        encoding="utf-8",
    )
    manifest: dict[str, object] = {
        "schema_version": "nslab.session_pack_manifest.v1",
        "trade_date": trade_date.isoformat(),
        "mode": mode,
        "brain_version": current_brain_version(settings.project_root),
        "brain_file_hashes": current_brain_file_hashes(settings.project_root),
        "news_file": news_csv.as_posix(),
        "news_sha256": file_sha256(news_csv),
        "accepted_episode_count": len(accepted),
        "included_episode_count": len(accepted),
        "truncations": [],
    }
    write_json(output_dir / "manifest.json", manifest)
    return output_dir
