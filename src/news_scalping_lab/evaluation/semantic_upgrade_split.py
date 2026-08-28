"""Chronological BUILD/CALIBRATION/HOLDOUT selection for the semantic upgrade."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from news_scalping_lab.records.models import NormalizedEpisodeIndex
from news_scalping_lab.utils import KST, file_sha256, read_json, sha256_text, write_json

SEMANTIC_UPGRADE_SPLIT_VERSION = "nslab.semantic_upgrade_split_selection.v1"
SEMANTIC_UPGRADE_SPLIT_SEED = "NSLAB-SEMANTIC-UPGRADE-SPLIT-20260828-v1"
SEMANTIC_UPGRADE_SPLIT_ROOT = Path(
    "runs/semantic_brain_upgrade/shadow_split"
)


@dataclass(frozen=True)
class SemanticUpgradeCase:
    episode_id: str
    trade_date: date
    next_trade_date: date
    index_path: Path
    source_ledger_path: Path
    prediction_path: Path
    outcome_path: Path


@dataclass(frozen=True)
class SemanticUpgradeSplitResult:
    build_cases: tuple[SemanticUpgradeCase, ...]
    calibration_cases: tuple[SemanticUpgradeCase, ...]
    holdout_cases: tuple[SemanticUpgradeCase, ...]
    plan_path: Path
    selection_path: Path


def build_semantic_upgrade_split(
    root: Path,
    *,
    calibration_count: int = 40,
    holdout_count: int = 40,
    seed: str = SEMANTIC_UPGRADE_SPLIT_SEED,
) -> SemanticUpgradeSplitResult:
    root = root.resolve()
    if calibration_count < 20 or holdout_count < 20 or not seed.strip():
        raise ValueError("semantic upgrade split requires at least 20 cases per gate")
    cases = _complete_gold_cases(root, seed=seed)
    required = calibration_count + holdout_count + 1
    if len(cases) < required:
        raise ValueError(
            f"semantic upgrade split requires {required} complete dates; found {len(cases)}"
        )
    calibration_start_index = len(cases) - calibration_count - holdout_count
    build_cases = tuple(cases[:calibration_start_index])
    calibration_cases = tuple(
        cases[
            calibration_start_index : calibration_start_index + calibration_count
        ]
    )
    holdout_cases = tuple(cases[-holdout_count:])
    if not build_cases:
        raise ValueError("semantic upgrade split requires a non-empty BUILD partition")
    if not (
        build_cases[-1].trade_date < calibration_cases[0].trade_date
        and calibration_cases[-1].trade_date < holdout_cases[0].trade_date
    ):
        raise ValueError("semantic upgrade split must be strictly chronological")
    output_dir = root / SEMANTIC_UPGRADE_SPLIT_ROOT
    plan_path = output_dir / "shadow_split_plan.json"
    plan = {
        "schema_version": "nslab.shadow_dataset_split_plan.v1",
        "build_start": build_cases[0].trade_date.isoformat(),
        "build_end": build_cases[-1].trade_date.isoformat(),
        "calibration_start": calibration_cases[0].trade_date.isoformat(),
        "calibration_end": calibration_cases[-1].trade_date.isoformat(),
        "holdout_start": holdout_cases[0].trade_date.isoformat(),
        "holdout_end": holdout_cases[-1].trade_date.isoformat(),
        "calibration_dates": [
            item.trade_date.isoformat() for item in calibration_cases
        ],
        "holdout_dates": [item.trade_date.isoformat() for item in holdout_cases],
    }
    write_json(plan_path, plan)
    selection_path = output_dir / "shadow_case_selection.json"
    selection = {
        "schema_version": SEMANTIC_UPGRADE_SPLIT_VERSION,
        "seed": seed,
        "seed_sha256": sha256_text(seed),
        "selection_policy": (
            "ONE_COMPLETE_GOLD_CASE_PER_DATE_THEN_LATEST_40_HOLDOUT_"
            "PRECEDING_40_CALIBRATION"
        ),
        "build_case_count": len(build_cases),
        "calibration_case_count": len(calibration_cases),
        "holdout_case_count": len(holdout_cases),
        "plan_sha256": file_sha256(plan_path),
        "cases": [
            _case_payload(root, item, split="BUILD")
            for item in build_cases
        ]
        + [
            _case_payload(root, item, split="CALIBRATION")
            for item in calibration_cases
        ]
        + [
            _case_payload(root, item, split="HOLDOUT")
            for item in holdout_cases
        ],
        "production_activation_status": "NOT_PRODUCTION_ACTIVATED",
    }
    write_json(selection_path, selection)
    return SemanticUpgradeSplitResult(
        build_cases=build_cases,
        calibration_cases=calibration_cases,
        holdout_cases=holdout_cases,
        plan_path=plan_path,
        selection_path=selection_path,
    )


def split_record_ids(root: Path, cases: tuple[SemanticUpgradeCase, ...]) -> set[str]:
    records_dir = root.resolve() / "memory" / "records"
    record_ids: set[str] = set()
    for case in cases:
        path = records_dir / f"{case.episode_id}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"split record shard is missing: {case.episode_id}")
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = read_json_line(line)
                record_id = payload.get("record_id")
                if not isinstance(record_id, str) or not record_id:
                    raise ValueError("split record shard contains an invalid record ID")
                record_ids.add(record_id)
    return record_ids


def read_json_line(line: str) -> dict[str, Any]:
    import json

    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("split JSONL row must be an object")
    return value


def _complete_gold_cases(root: Path, *, seed: str) -> list[SemanticUpgradeCase]:
    by_date: dict[date, list[SemanticUpgradeCase]] = {}
    for episode_dir in sorted((root / "research" / "episodes").iterdir()):
        if not episode_dir.is_dir():
            continue
        index_path = episode_dir / "normalized_episode_index.json"
        source_path = episode_dir / "raw_blocks" / "source_ledger.jsonl"
        prediction_path = episode_dir / "raw_blocks" / "blind_prediction.json"
        outcome_path = episode_dir / "raw_blocks" / "outcome_ledger.jsonl"
        if not all(
            path.exists()
            for path in (index_path, source_path, prediction_path, outcome_path)
        ):
            continue
        if source_path.stat().st_size == 0 or outcome_path.stat().st_size == 0:
            continue
        try:
            index = NormalizedEpisodeIndex.model_validate(read_json(index_path))
        except (OSError, ValueError):
            continue
        if index.blind_valid is False or index.next_trade_date is None:
            continue
        by_date.setdefault(index.trade_date, []).append(
            SemanticUpgradeCase(
                episode_id=index.episode_id,
                trade_date=index.trade_date,
                next_trade_date=index.next_trade_date,
                index_path=index_path,
                source_ledger_path=source_path,
                prediction_path=prediction_path,
                outcome_path=outcome_path,
            )
        )
    selected = [
        min(
            rows,
            key=lambda item: sha256_text(f"{seed}|{item.episode_id}"),
        )
        for _trade_date, rows in sorted(by_date.items())
    ]
    return sorted(selected, key=lambda item: (item.trade_date, item.episode_id))


def _case_payload(
    root: Path,
    case: SemanticUpgradeCase,
    *,
    split: str,
) -> dict[str, Any]:
    def reference(path: Path) -> dict[str, str]:
        try:
            artifact_path = path.relative_to(root).as_posix()
        except ValueError:
            try:
                artifact_path = path.resolve().relative_to(root).as_posix()
            except ValueError:
                artifact_path = path.resolve().as_posix()
        return {"artifact_path": artifact_path, "sha256": file_sha256(path)}

    return {
        "episode_id": case.episode_id,
        "trade_date": case.trade_date.isoformat(),
        "next_trade_date": case.next_trade_date.isoformat(),
        "replay_available_from": datetime.combine(
            case.next_trade_date,
            time(0, 0),
            tzinfo=KST,
        ).isoformat(),
        "split": split,
        "normalized_index": reference(case.index_path),
        "source_ledger": reference(case.source_ledger_path),
        "blind_prediction": reference(case.prediction_path),
        "outcome_ledger": reference(case.outcome_path),
    }
