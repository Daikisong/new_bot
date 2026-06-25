"""Training data exports.

Exports are derived artifacts from accepted research episodes. They separate blind
reasoning from postmortem labels so hindsight never becomes a fake blind answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.models import (
    Candidate,
    EligibilityMatrix,
    OutcomeLabels,
    ResearchEpisode,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import file_sha256, now_kst, stable_id, write_json

VALID_KINDS = {"sft", "preference", "evals"}

TASK_TRAINING_CATEGORY = {
    "blind_reasoning": "blind_reasoning_examples",
    "theme_formation": "theme_formation_examples",
    "beneficiary_discovery": "beneficiary_discovery_examples",
    "leader_selection_comparison": "leader_selection_comparisons",
    "positive_vs_negative_candidate_preference": "positive_vs_negative_candidate_preferences",
    "postmortem_preference_summary": "positive_vs_negative_candidate_preferences",
    "failure_correction": "failure_correction_examples",
    "candidate_outcome_eval": "evaluation_examples",
    "failure_code_eval": "evaluation_examples",
}

REQUIRED_TRAINING_CATEGORIES = [
    "blind_reasoning_examples",
    "theme_formation_examples",
    "beneficiary_discovery_examples",
    "leader_selection_comparisons",
    "positive_vs_negative_candidate_preferences",
    "failure_correction_examples",
]

KIND_TRAINING_CATEGORIES = {
    "sft": [
        "blind_reasoning_examples",
        "theme_formation_examples",
        "beneficiary_discovery_examples",
        "leader_selection_comparisons",
        "failure_correction_examples",
    ],
    "preference": ["positive_vs_negative_candidate_preferences"],
    "evals": ["evaluation_examples"],
}


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
    skipped = _skipped_episodes(kind, episodes)
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
            "eligible_episode_count": len(episodes) - len(skipped),
            "skipped_episode_count": len(skipped),
            "skipped_episodes": skipped,
            "source_hashes": store.accepted_hashes(),
            "output_file": path.as_posix(),
            "output_sha256": file_sha256(path),
            "task_counts": _task_counts(rows),
            "required_training_categories": REQUIRED_TRAINING_CATEGORIES,
            "training_categories": KIND_TRAINING_CATEGORIES[kind],
            "category_counts": _category_counts(rows, kind=kind),
            "missing_training_categories": _missing_training_categories(rows, kind=kind),
            "blind_safe_row_count": sum(
                1 for row in rows if row["hindsight_safe_for_blind_sft"] is True
            ),
            "hindsight_row_count": sum(
                1 for row in rows if row["hindsight_safe_for_blind_sft"] is False
            ),
            "source_phase_counts": _source_phase_counts(rows),
            "notes": [
                "SFT rows use only blind inputs and blind outputs.",
                "Preference and eval rows may include postmortem/outcome labels.",
                "Do not train postmortem labels as if they were blind answers.",
                "Rows with source_phase=POSTMORTEM must not be mixed into blind SFT.",
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
    rows: list[dict[str, Any]] = []
    if episode.eligibility_matrix.forecast_evaluation_eligible:
        rows.extend(
            [
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
                        "candidates": [
                            candidate.model_dump(mode="json")
                            for candidate in episode.blind_predictions
                        ],
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
                    },
                    split="sft",
                    hindsight_safe=True,
                ),
                _training_row(
                    task="leader_selection_comparison",
                    episode=episode,
                    input_payload={
                        "blind_summary": episode.blind_analysis.summary,
                        "candidate_count": len(episode.blind_predictions),
                        "candidates": [
                            {
                                "rank": candidate.rank,
                                "company_name": candidate.company_name,
                                "ticker": candidate.ticker,
                                "path_type": str(candidate.path_type),
                                "why_now": candidate.why_now,
                                "causal_chain": candidate.causal_chain,
                                "counterarguments": candidate.counterarguments,
                                "confidence_label": str(candidate.confidence_label),
                                "evidence_quality": str(candidate.evidence_quality),
                            }
                            for candidate in episode.blind_predictions
                        ],
                    },
                    output_payload={
                        "preferred_order": [
                            {
                                "rank": candidate.rank,
                                "company_name": candidate.company_name,
                                "selection_reason": candidate.why_now,
                                "risk_checks": candidate.disconfirming_conditions,
                            }
                            for candidate in sorted(
                                episode.blind_predictions,
                                key=lambda item: item.rank,
                            )
                        ],
                        "comparison_basis": [
                            "sealed blind rank",
                            "pre-cutoff causal chain",
                            "confidence label",
                            "evidence quality",
                            "counterarguments and disconfirming conditions",
                        ],
                    },
                    split="sft",
                    hindsight_safe=True,
                ),
            ]
        )
    if episode.postmortem is not None and episode.eligibility_matrix.retrospective_memory_eligible:
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
    if not episode.eligibility_matrix.leader_pair_training_eligible:
        return []
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
    if episode.eligibility_matrix.direct_supervised_cases_eligible:
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
    if episode.postmortem is not None and episode.eligibility_matrix.retrospective_memory_eligible:
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
    eligibility_basis = _eligibility_basis_for_task(task, episode.eligibility_matrix)
    return {
        "schema_version": "nslab.training_example.v1",
        "example_id": stable_id("TRN", split, task, episode.episode_id, input_payload),
        "task": task,
        "training_category": _training_category_for_task(task),
        "split": split,
        "episode_id": episode.episode_id,
        "trade_date": episode.trade_date.isoformat(),
        "available_from": episode.available_from.isoformat(),
        "hindsight_safe_for_blind_sft": hindsight_safe,
        "source_phase": "BLIND" if hindsight_safe else "POSTMORTEM",
        "eligibility_basis": eligibility_basis,
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


def _skipped_episodes(kind: str, episodes: list[ResearchEpisode]) -> list[dict[str, Any]]:
    skipped: list[dict[str, Any]] = []
    for episode in episodes:
        missing = _missing_eligibility_for_kind(kind, episode.eligibility_matrix)
        if missing:
            skipped.append(
                {
                    "episode_id": episode.episode_id,
                    "missing_eligibility": missing,
                    "reasons": {
                        key: episode.eligibility_matrix.reasons.get(key, "not eligible")
                        for key in missing
                    },
                }
            )
    return skipped


def _missing_eligibility_for_kind(kind: str, eligibility: EligibilityMatrix) -> list[str]:
    if kind == "sft":
        required = [
            "forecast_evaluation_eligible",
        ]
        if eligibility.retrospective_memory_eligible:
            required.append("retrospective_memory_eligible")
    elif kind == "preference":
        required = ["leader_pair_training_eligible"]
    else:
        required = ["direct_supervised_cases_eligible"]
        if eligibility.retrospective_memory_eligible:
            required.append("retrospective_memory_eligible")
    return [field for field in required if not bool(getattr(eligibility, field))]


def _eligibility_basis_for_task(
    task: str,
    eligibility: EligibilityMatrix,
) -> dict[str, Any]:
    required_fields = _required_eligibility_for_task(task)
    return {
        "required_fields": required_fields,
        "satisfied": all(bool(getattr(eligibility, field)) for field in required_fields),
        "field_values": {
            field: bool(getattr(eligibility, field)) for field in required_fields
        },
        "reasons": {
            field: eligibility.reasons[field]
            for field in required_fields
            if field in eligibility.reasons
        },
    }


def _required_eligibility_for_task(task: str) -> list[str]:
    if task in {
        "blind_reasoning",
        "theme_formation",
        "beneficiary_discovery",
        "leader_selection_comparison",
    }:
        return ["forecast_evaluation_eligible"]
    if task in {
        "positive_vs_negative_candidate_preference",
        "postmortem_preference_summary",
    }:
        return ["leader_pair_training_eligible"]
    if task == "candidate_outcome_eval":
        return ["direct_supervised_cases_eligible"]
    if task in {"failure_correction", "failure_code_eval"}:
        return ["retrospective_memory_eligible"]
    return []


def _training_category_for_task(task: str) -> str:
    return TASK_TRAINING_CATEGORY.get(task, "other")


def _task_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        task = str(row["task"])
        counts[task] = counts.get(task, 0) + 1
    return counts


def _category_counts(rows: list[dict[str, Any]], *, kind: str) -> dict[str, int]:
    counts = dict.fromkeys(KIND_TRAINING_CATEGORIES[kind], 0)
    for row in rows:
        category = str(row["training_category"])
        counts[category] = counts.get(category, 0) + 1
    return counts


def _missing_training_categories(rows: list[dict[str, Any]], *, kind: str) -> list[str]:
    counts = _category_counts(rows, kind=kind)
    return [
        category
        for category in KIND_TRAINING_CATEGORIES[kind]
        if counts.get(category, 0) == 0
    ]


def _source_phase_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        phase = str(row["source_phase"])
        counts[phase] = counts.get(phase, 0) + 1
    return counts
