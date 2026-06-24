from __future__ import annotations

import json
from datetime import date, datetime, time

import pytest

from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    Candidate,
    OutcomeLabels,
    PathType,
    Postmortem,
    ResearchEpisode,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.training import export_training
from news_scalping_lab.utils import KST, read_json


def _accepted_episode() -> ResearchEpisode:
    trade_day = date(2030, 1, 10)
    candidates = [
        Candidate(
            rank=1,
            ticker="111111",
            company_name="WinnerCo",
            path_type=PathType.SINGLE_EVENT,
            thesis="Winner blind thesis.",
            why_now="Observed before cutoff.",
            causal_chain=["news", "direct verification"],
        ),
        Candidate(
            rank=2,
            ticker="222222",
            company_name="LoserCo",
            path_type=PathType.THEME_BENEFICIARY,
            thesis="Loser blind thesis.",
            why_now="Possible indirect beneficiary.",
            causal_chain=["news", "indirect verification"],
        ),
    ]
    return ResearchEpisode(
        episode_id="EP-training",
        trade_date=trade_day,
        cutoff_at=datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST),
        created_at=datetime.combine(trade_day, time(16, 0, 0), tzinfo=KST),
        research_version="training-test-v1",
        input_news_files=["news.csv"],
        input_news_hashes=["a" * 64],
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(
            summary="Blind reasoning without outcome knowledge.",
            open_world_mechanisms=["current catalyst -> direct and indirect paths"],
            initial_uncertainties=["leader selection"],
        ),
        blind_predictions=candidates,
        outcome_labels={
            "1:111111:WinnerCo": OutcomeLabels(upper_limit_touched=True, upper_limit_closed=True),
            "2:222222:LoserCo": OutcomeLabels(upper_limit_touched=False, upper_limit_closed=False),
        },
        postmortem=Postmortem(
            summary="Winner hit and loser failed.",
            hits=["WinnerCo"],
            false_positives=["LoserCo"],
            failure_codes=["DIRECTNESS_ERROR"],
            lessons=["prefer verified directness over loose theme breadth"],
        ),
        available_from=datetime.combine(date(2030, 1, 11), time(0, 0, 0), tzinfo=KST),
    )


def _jsonl(path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_training_exports_separate_blind_postmortem_preference_and_evals(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode()
    store.save_episode(episode)
    store.accept(episode.episode_id)

    sft = export_training(tmp_path, kind="sft")
    preference = export_training(tmp_path, kind="preference")
    evals = export_training(tmp_path, kind="evals")

    sft_rows = _jsonl(sft.path)
    preference_rows = _jsonl(preference.path)
    eval_rows = _jsonl(evals.path)

    assert sft.row_count == 4
    assert {row["task"] for row in sft_rows} == {
        "blind_reasoning",
        "theme_formation",
        "beneficiary_discovery",
        "failure_correction",
    }
    blind_rows = [row for row in sft_rows if row["hindsight_safe_for_blind_sft"]]
    assert {row["task"] for row in blind_rows} == {
        "blind_reasoning",
        "theme_formation",
        "beneficiary_discovery",
    }
    blind_row_text = json.dumps(blind_rows, ensure_ascii=False, sort_keys=True)
    assert "prefer verified directness over loose theme breadth" not in blind_row_text
    assert "Winner hit and loser failed." not in blind_row_text
    assert "DIRECTNESS_ERROR" not in blind_row_text
    theme_row = next(row for row in blind_rows if row["task"] == "theme_formation")
    assert theme_row["output"]["failure_conditions"] == ["leader selection"]
    failure_rows = [row for row in sft_rows if row["task"] == "failure_correction"]
    assert failure_rows[0]["hindsight_safe_for_blind_sft"] is False
    assert "failure_codes" in failure_rows[0]["output"]

    assert preference.row_count == 1
    assert preference_rows[0]["task"] == "positive_vs_negative_candidate_preference"
    assert preference_rows[0]["output"]["chosen"] == "WinnerCo"
    assert preference_rows[0]["output"]["rejected"] == "LoserCo"
    assert preference_rows[0]["hindsight_safe_for_blind_sft"] is False

    assert evals.row_count == 3
    assert {row["task"] for row in eval_rows} == {
        "candidate_outcome_eval",
        "failure_code_eval",
    }
    assert all(row["hindsight_safe_for_blind_sft"] is False for row in eval_rows)

    manifest = read_json(sft.manifest_path)
    assert manifest["row_count"] == sft.row_count
    assert manifest["task_counts"]["blind_reasoning"] == 1
    assert manifest["output_sha256"]
    assert "Do not train postmortem labels as if they were blind answers." in manifest["notes"]


def test_training_export_rejects_unknown_kind(tmp_path) -> None:
    with pytest.raises(ValueError, match="kind must be sft, preference, or evals"):
        export_training(tmp_path, kind="unknown")
