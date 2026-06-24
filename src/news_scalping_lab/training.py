"""Training data exports.

Exports are derived artifacts from accepted research episodes. They separate blind
reasoning from postmortem labels so hindsight never becomes a fake blind answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.models import Candidate, OutcomeLabels, ResearchEpisode
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import file_sha256, now_kst, stable_id, write_json

VALID_KINDS = {"sft", "preference", "evals"}


@dataclass(frozen=True)
class TrainingExportResult:
    path: Path
    manifest_path: Path
    row_count: int


def export_training(root: Path, *, kind: str) -> TrainingExportResult:
    if kind not in VALID_KINDS:
        raise ValueError("kind must be sft, preference, or evals")
    target_dir = root / "training_exports" / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    store = ResearchStore(root)
    episodes = store.list_accepted()
    rows = _rows_for_kind(kind, episodes)
    path = target_dir / f"{kind}.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    manifest_path = target_dir / "manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": "nslab.training_export_manifest.v1",
            "kind": kind,
            "created_at": now_kst().isoformat(),
            "row_count": len(rows),
            "episode_count": len(episodes),
            "episode_ids": [episode.episode_id for episode in episodes],
            "source_hashes": store.accepted_hashes(),
            "output_file": path.as_posix(),
            "output_sha256": file_sha256(path),
            "task_counts": _task_counts(rows),
            "notes": [
                "SFT rows use only blind inputs and blind outputs.",
                "Preference and eval rows may include postmortem/outcome labels.",
                "Do not train postmortem labels as if they were blind answers.",
            ],
        },
    )
    return TrainingExportResult(path=path, manifest_path=manifest_path, row_count=len(rows))


def _rows_for_kind(kind: str, episodes: list[ResearchEpisode]) -> list[dict[str, Any]]:
    if kind == "sft":
        return [row for episode in episodes for row in _sft_rows(episode)]
    if kind == "preference":
        return [row for episode in episodes for row in _preference_rows(episode)]
    return [row for episode in episodes for row in _eval_rows(episode)]


def _sft_rows(episode: ResearchEpisode) -> list[dict[str, Any]]:
    rows = [
        _training_row(
            task="blind_reasoning",
            episode=episode,
            input_payload={
                "trade_date": episode.trade_date.isoformat(),
                "cutoff_at": episode.cutoff_at.isoformat(),
                "observed_events": [
                    item.model_dump(mode="json") for item in episode.observed_events
                ],
                "input_news_files": episode.input_news_files,
            },
            output_payload={
                "summary": episode.blind_analysis.summary,
                "open_world_mechanisms": episode.blind_analysis.open_world_mechanisms,
                "initial_uncertainties": episode.blind_analysis.initial_uncertainties,
                "candidates": [candidate.model_dump(mode="json") for candidate in episode.blind_predictions],
            },
            split="sft",
            hindsight_safe=True,
        ),
        _training_row(
            task="theme_formation",
            episode=episode,
            input_payload={"blind_summary": episode.blind_analysis.summary},
            output_payload={
                "mechanisms": episode.blind_analysis.open_world_mechanisms,
                "failure_conditions": episode.blind_analysis.initial_uncertainties,
            },
            split="sft",
            hindsight_safe=True,
        ),
        _training_row(
            task="beneficiary_discovery",
            episode=episode,
            input_payload={
                "blind_summary": episode.blind_analysis.summary,
                "candidate_count": len(episode.blind_predictions),
            },
            output_payload={
                "candidate_paths": [
                    {
                        "company_name": candidate.company_name,
                        "path_type": str(candidate.path_type),
                        "causal_chain": candidate.causal_chain,
                        "counterarguments": candidate.counterarguments,
                    }
                    for candidate in episode.blind_predictions
                ],
                "event_ticker_edges": [
                    edge.model_dump(mode="json") for edge in episode.event_ticker_edges
                ],
            },
            split="sft",
            hindsight_safe=True,
        ),
    ]
    if episode.postmortem is not None:
        rows.append(
            _training_row(
                task="failure_correction",
                episode=episode,
                input_payload={
                    "blind_candidates": [
                        candidate.model_dump(mode="json") for candidate in episode.blind_predictions
                    ],
                    "postmortem_summary": episode.postmortem.summary,
                    "misses": episode.misses,
                },
                output_payload={
                    "failure_codes": episode.postmortem.failure_codes,
                    "lessons": episode.postmortem.lessons,
                    "false_positives": episode.postmortem.false_positives,
                },
                split="sft_postmortem",
                hindsight_safe=False,
            )
        )
    return rows


def _preference_rows(episode: ResearchEpisode) -> list[dict[str, Any]]:
    positives: list[Candidate] = []
    negatives: list[Candidate] = []
    for candidate in episode.blind_predictions:
        outcome = _outcome_for_candidate(episode, candidate)
        if outcome is not None and outcome.upper_limit_touched:
            positives.append(candidate)
        elif outcome is not None:
            negatives.append(candidate)

    rows: list[dict[str, Any]] = []
    for positive in positives:
        for negative in negatives:
            rows.append(
                _training_row(
                    task="positive_vs_negative_candidate_preference",
                    episode=episode,
                    input_payload={
                        "blind_summary": episode.blind_analysis.summary,
                        "chosen_candidate": positive.model_dump(mode="json"),
                        "rejected_candidate": negative.model_dump(mode="json"),
                    },
                    output_payload={
                        "chosen": positive.company_name,
                        "rejected": negative.company_name,
                        "rationale": (
                            "Postmortem outcome labels favored the chosen candidate over the rejected candidate."
                        ),
                    },
                    split="preference",
                    hindsight_safe=False,
                )
            )
    if not rows and episode.postmortem is not None:
        rows.append(
            _training_row(
                task="postmortem_preference_summary",
                episode=episode,
                input_payload={
                    "hits": episode.postmortem.hits,
                    "false_positives": episode.postmortem.false_positives,
                    "misses": episode.postmortem.misses,
                },
                output_payload={"lessons": episode.postmortem.lessons},
                split="preference",
                hindsight_safe=False,
            )
        )
    return rows


def _eval_rows(episode: ResearchEpisode) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in episode.blind_predictions:
        outcome = _outcome_for_candidate(episode, candidate)
        rows.append(
            _training_row(
                task="candidate_outcome_eval",
                episode=episode,
                input_payload={
                    "candidate": candidate.model_dump(mode="json"),
                    "blind_summary": episode.blind_analysis.summary,
                },
                output_payload={
                    "outcome": outcome.model_dump(mode="json") if outcome is not None else None,
                    "expected_labels": {
                        "upper_limit_touched": outcome.upper_limit_touched if outcome else None,
                        "upper_limit_closed": outcome.upper_limit_closed if outcome else None,
                    },
                },
                split="evals",
                hindsight_safe=False,
            )
        )
    if episode.postmortem is not None:
        rows.append(
            _training_row(
                task="failure_code_eval",
                episode=episode,
                input_payload={
                    "blind_summary": episode.blind_analysis.summary,
                    "postmortem_summary": episode.postmortem.summary,
                },
                output_payload={
                    "failure_codes": episode.postmortem.failure_codes,
                    "misses": episode.postmortem.misses,
                    "false_positives": episode.postmortem.false_positives,
                },
                split="evals",
                hindsight_safe=False,
            )
        )
    return rows


def _training_row(
    *,
    task: str,
    episode: ResearchEpisode,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
    split: str,
    hindsight_safe: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "nslab.training_example.v1",
        "example_id": stable_id("TRN", split, task, episode.episode_id, input_payload),
        "task": task,
        "split": split,
        "episode_id": episode.episode_id,
        "trade_date": episode.trade_date.isoformat(),
        "available_from": episode.available_from.isoformat(),
        "hindsight_safe_for_blind_sft": hindsight_safe,
        "input": input_payload,
        "output": output_payload,
        "provenance": [item.model_dump(mode="json") for item in episode.provenance],
    }


def _outcome_for_candidate(
    episode: ResearchEpisode,
    candidate: Candidate,
) -> OutcomeLabels | None:
    direct_key = f"{candidate.rank}:{candidate.ticker}:{candidate.company_name}"
    if direct_key in episode.outcome_labels:
        return episode.outcome_labels[direct_key]
    for key, outcome in episode.outcome_labels.items():
        if candidate.company_name in key or candidate.ticker in key:
            return outcome
    return None

def _task_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        task = str(row["task"])
        counts[task] = counts.get(task, 0) + 1
    return counts
