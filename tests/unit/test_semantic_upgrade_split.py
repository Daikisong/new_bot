from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from news_scalping_lab.evaluation.semantic_upgrade_split import (
    SemanticUpgradeCase,
    _complete_gold_cases,
    _source_has_cutoff_safe_news,
    build_semantic_upgrade_split,
)
from news_scalping_lab.utils import read_json, write_json


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


def test_semantic_upgrade_split_excludes_cases_without_cutoff_safe_news(
    tmp_path: Path,
) -> None:
    start = date(2025, 1, 2)
    for index in range(45):
        episode_id = f"EP-{index:03d}"
        episode = tmp_path / "research/episodes" / episode_id
        blocks = episode / "raw_blocks"
        blocks.mkdir(parents=True)
        trade_date = start + timedelta(days=index)
        write_json(episode / "normalized_episode_index.json", {
            "episode_id": episode_id,
            "trade_date": trade_date.isoformat(),
            "next_trade_date": (trade_date + timedelta(days=1)).isoformat(),
            "available_from": f"{trade_date.isoformat()}T16:00:00+09:00",
            "blind_valid": True,
        })
        (blocks / "source_ledger.jsonl").write_text(
            '{"available_before_cutoff": false}\n' if index == 44 else '{"available_before_cutoff": true}\n',
            encoding="utf-8",
        )
        (blocks / "blind_prediction.json").write_text("{}", encoding="utf-8")
        (blocks / "outcome_ledger.jsonl").write_text("{}\n", encoding="utf-8")

    assert len(_complete_gold_cases(tmp_path, seed="test")) == 44

    result = build_semantic_upgrade_split(
        tmp_path,
        calibration_count=20,
        holdout_count=20,
    )

    assert len(result.holdout_cases) == 20
    assert result.holdout_cases[-1].episode_id == "EP-043"


@pytest.mark.parametrize("contents", ["", "{}\n", "[]\n", "invalid\n", '{"available_before_cutoff": "true"}\n'])
def test_cutoff_safe_news_requires_an_explicit_boolean(tmp_path: Path, contents: str) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text(contents, encoding="utf-8")
    assert _source_has_cutoff_safe_news(source) is False
