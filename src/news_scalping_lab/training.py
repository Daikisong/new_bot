"""Training data exports."""

from __future__ import annotations

from pathlib import Path

from news_scalping_lab.storage import ResearchStore


def export_training(root: Path, *, kind: str) -> Path:
    if kind not in {"sft", "preference", "evals"}:
        raise ValueError("kind must be sft, preference, or evals")
    target_dir = root / "training_exports" / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    store = ResearchStore(root)
    path = target_dir / f"{kind}.jsonl"
    lines: list[str] = []
    for episode in store.list_accepted():
        if kind == "sft":
            lines.append(
                episode.model_dump_json(
                    include={"episode_id", "trade_date", "blind_analysis", "blind_predictions"}
                )
            )
        elif kind == "preference":
            lines.append(
                episode.model_dump_json(
                    include={"episode_id", "event_ticker_edges", "postmortem", "counterexamples"}
                )
            )
        else:
            lines.append(
                episode.model_dump_json(include={"episode_id", "outcome_labels", "misses"})
            )
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path
