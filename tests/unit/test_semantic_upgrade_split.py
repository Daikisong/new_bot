from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from news_scalping_lab.evaluation.semantic_upgrade_split import (
    SemanticUpgradeCase,
    build_semantic_upgrade_split,
)
from news_scalping_lab.utils import read_json


def test_semantic_upgrade_split_is_strictly_chronological(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    start = date(2025, 1, 2)
    cases = [
        SemanticUpgradeCase(
            episode_id=f"EP-{index:03d}",
            trade_date=start + timedelta(days=index),
            next_trade_date=start + timedelta(days=index + 1),
            index_path=source,
            source_ledger_path=source,
            prediction_path=source,
            outcome_path=source,
        )
        for index in range(45)
    ]
    monkeypatch.setattr(
        "news_scalping_lab.evaluation.semantic_upgrade_split._complete_gold_cases",
        lambda _root, seed: cases,
    )

    result = build_semantic_upgrade_split(
        tmp_path,
        calibration_count=20,
        holdout_count=20,
    )
    plan = read_json(result.plan_path)

    assert len(result.build_cases) == 5
    assert len(result.calibration_cases) == 20
    assert len(result.holdout_cases) == 20
    assert (
        result.build_cases[-1].trade_date
        < result.calibration_cases[0].trade_date
        < result.holdout_cases[0].trade_date
    )
    assert plan["calibration_dates"][0] == (
        result.calibration_cases[0].trade_date.isoformat()
    )
    assert plan["holdout_dates"][-1] == result.holdout_cases[-1].trade_date.isoformat()
