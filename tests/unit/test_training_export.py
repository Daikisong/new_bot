from __future__ import annotations

import json
from datetime import date, datetime, time

import pytest

from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    Candidate,
    EligibilityMatrix,
    EventTickerEdge,
    OutcomeLabels,
    PathType,
    Postmortem,
    RelationClass,
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
        blind_artifact_sha256="a" * 64,
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
        event_ticker_edges=[
            EventTickerEdge(
                edge_id="EDGE-postmortem",
                episode_id="EP-training",
                event_id="EVT-postmortem",
                ticker="111111",
                company_name="WinnerCo",
                relation_class=RelationClass.DIRECT,
                relation_explanation="postmortem-only edge must not enter blind-safe rows",
                directly_mentioned=True,
                temporal_validity="validated after outcome",
            )
        ],
        eligibility_matrix=EligibilityMatrix(
            forecast_evaluation_eligible=True,
            direct_supervised_cases_eligible=True,
            theme_supervised_cases_eligible=True,
            leader_pair_training_eligible=True,
            retrospective_memory_eligible=True,
            brain_eligible=True,
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

    assert sft.row_count == 5
    assert {row["training_category"] for row in sft_rows} == {
        "blind_reasoning_examples",
        "theme_formation_examples",
        "beneficiary_discovery_examples",
        "leader_selection_comparisons",
        "failure_correction_examples",
    }
    assert {row["task"] for row in sft_rows} == {
        "blind_reasoning",
        "theme_formation",
        "beneficiary_discovery",
        "leader_selection_comparison",
        "failure_correction",
    }
    blind_rows = [row for row in sft_rows if row["hindsight_safe_for_blind_sft"]]
    assert {row["task"] for row in blind_rows} == {
        "blind_reasoning",
        "theme_formation",
        "beneficiary_discovery",
        "leader_selection_comparison",
    }
    blind_row_text = json.dumps(blind_rows, ensure_ascii=False, sort_keys=True)
    assert "prefer verified directness over loose theme breadth" not in blind_row_text
    assert "Winner hit and loser failed." not in blind_row_text
    assert "DIRECTNESS_ERROR" not in blind_row_text
    assert "postmortem-only edge must not enter blind-safe rows" not in blind_row_text
    assert all(row["source_phase"] == "BLIND" for row in blind_rows)
    assert all(row["eligibility_basis"]["satisfied"] is True for row in blind_rows)
    assert all(
        row["eligibility_basis"]["required_fields"] == ["forecast_evaluation_eligible"]
        for row in blind_rows
    )
    theme_row = next(row for row in blind_rows if row["task"] == "theme_formation")
    assert theme_row["output"]["failure_conditions"] == ["leader selection"]
    beneficiary_row = next(row for row in blind_rows if row["task"] == "beneficiary_discovery")
    assert "event_ticker_edges" not in beneficiary_row["output"]
    leader_row = next(row for row in blind_rows if row["task"] == "leader_selection_comparison")
    assert leader_row["output"]["preferred_order"][0]["company_name"] == "WinnerCo"
    assert leader_row["output"]["preferred_order"][1]["company_name"] == "LoserCo"
    assert leader_row["output"]["comparison_basis"] == [
        "sealed blind rank",
        "pre-cutoff causal chain",
        "confidence label",
        "evidence quality",
        "counterarguments and disconfirming conditions",
    ]
    failure_rows = [row for row in sft_rows if row["task"] == "failure_correction"]
    assert failure_rows[0]["hindsight_safe_for_blind_sft"] is False
    assert failure_rows[0]["source_phase"] == "POSTMORTEM"
    assert failure_rows[0]["eligibility_basis"]["required_fields"] == [
        "retrospective_memory_eligible"
    ]
    assert "failure_codes" in failure_rows[0]["output"]

    assert preference.row_count == 1
    assert preference_rows[0]["task"] == "positive_vs_negative_candidate_preference"
    assert (
        preference_rows[0]["training_category"]
        == "positive_vs_negative_candidate_preferences"
    )
    assert preference_rows[0]["output"]["chosen"] == "WinnerCo"
    assert preference_rows[0]["output"]["rejected"] == "LoserCo"
    assert preference_rows[0]["hindsight_safe_for_blind_sft"] is False
    assert preference_rows[0]["source_phase"] == "POSTMORTEM"
    assert preference_rows[0]["eligibility_basis"]["required_fields"] == [
        "leader_pair_training_eligible"
    ]

    assert evals.row_count == 3
    assert {row["training_category"] for row in eval_rows} == {"evaluation_examples"}
    assert {row["task"] for row in eval_rows} == {
        "candidate_outcome_eval",
        "failure_code_eval",
    }
    assert all(row["hindsight_safe_for_blind_sft"] is False for row in eval_rows)
    assert all(row["source_phase"] == "POSTMORTEM" for row in eval_rows)
    candidate_eval_rows = [
        row for row in eval_rows if row["task"] == "candidate_outcome_eval"
    ]
    failure_eval_row = next(row for row in eval_rows if row["task"] == "failure_code_eval")
    assert all(
        row["eligibility_basis"]["required_fields"] == [
            "direct_supervised_cases_eligible"
        ]
        for row in candidate_eval_rows
    )
    assert failure_eval_row["eligibility_basis"]["required_fields"] == [
        "retrospective_memory_eligible"
    ]

    manifest = read_json(sft.manifest_path)
    assert manifest["row_count"] == sft.row_count
    assert manifest["task_counts"]["blind_reasoning"] == 1
    assert manifest["task_counts"]["leader_selection_comparison"] == 1
    assert manifest["required_training_categories"] == [
        "blind_reasoning_examples",
        "theme_formation_examples",
        "beneficiary_discovery_examples",
        "leader_selection_comparisons",
        "positive_vs_negative_candidate_preferences",
        "failure_correction_examples",
    ]
    assert manifest["training_categories"] == [
        "blind_reasoning_examples",
        "theme_formation_examples",
        "beneficiary_discovery_examples",
        "leader_selection_comparisons",
        "failure_correction_examples",
    ]
    assert manifest["category_counts"] == {
        "blind_reasoning_examples": 1,
        "theme_formation_examples": 1,
        "beneficiary_discovery_examples": 1,
        "leader_selection_comparisons": 1,
        "failure_correction_examples": 1,
    }
    assert manifest["missing_training_categories"] == []
    assert manifest["blind_safe_row_count"] == 4
    assert manifest["hindsight_row_count"] == 1
    assert manifest["eligible_episode_count"] == 1
    assert manifest["skipped_episode_count"] == 0
    assert manifest["skipped_episodes"] == []
    assert manifest["source_phase_counts"] == {"BLIND": 4, "POSTMORTEM": 1}
    assert manifest["output_sha256"]
    assert "Do not train postmortem labels as if they were blind answers." in manifest["notes"]
    preference_manifest = read_json(preference.manifest_path)
    evals_manifest = read_json(evals.manifest_path)
    assert preference_manifest["category_counts"] == {
        "positive_vs_negative_candidate_preferences": 1
    }
    assert preference_manifest["missing_training_categories"] == []
    assert evals_manifest["category_counts"] == {"evaluation_examples": 3}
    assert evals_manifest["missing_training_categories"] == []


def test_training_export_skips_ineligible_accepted_episodes(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode().model_copy(
        update={
            "episode_id": "EP-ineligible",
            "eligibility_matrix": EligibilityMatrix(
                forecast_evaluation_eligible=False,
                direct_supervised_cases_eligible=False,
                theme_supervised_cases_eligible=False,
                leader_pair_training_eligible=False,
                retrospective_memory_eligible=False,
                brain_eligible=False,
                reasons={
                    "forecast_evaluation_eligible": "sealed blind prediction is missing",
                    "leader_pair_training_eligible": "candidate outcomes are incomplete",
                    "direct_supervised_cases_eligible": "candidate outcomes are incomplete",
                },
            ),
        }
    )
    store.save_episode(episode)
    store.accept(episode.episode_id)

    sft = export_training(tmp_path, kind="sft")
    preference = export_training(tmp_path, kind="preference")
    evals = export_training(tmp_path, kind="evals")

    assert sft.row_count == 0
    assert preference.row_count == 0
    assert evals.row_count == 0
    sft_manifest = read_json(sft.manifest_path)
    preference_manifest = read_json(preference.manifest_path)
    evals_manifest = read_json(evals.manifest_path)
    assert sft_manifest["skipped_episode_count"] == 1
    assert sft_manifest["category_counts"] == {
        "blind_reasoning_examples": 0,
        "theme_formation_examples": 0,
        "beneficiary_discovery_examples": 0,
        "leader_selection_comparisons": 0,
        "failure_correction_examples": 0,
    }
    assert sft_manifest["missing_training_categories"] == [
        "blind_reasoning_examples",
        "theme_formation_examples",
        "beneficiary_discovery_examples",
        "leader_selection_comparisons",
        "failure_correction_examples",
    ]
    assert sft_manifest["skipped_episodes"][0]["missing_eligibility"] == [
        "forecast_evaluation_eligible"
    ]
    assert preference_manifest["category_counts"] == {
        "positive_vs_negative_candidate_preferences": 0
    }
    assert preference_manifest["missing_training_categories"] == [
        "positive_vs_negative_candidate_preferences"
    ]
    assert preference_manifest["skipped_episodes"][0]["missing_eligibility"] == [
        "leader_pair_training_eligible"
    ]
    assert evals_manifest["category_counts"] == {"evaluation_examples": 0}
    assert evals_manifest["missing_training_categories"] == ["evaluation_examples"]
    assert evals_manifest["skipped_episodes"][0]["missing_eligibility"] == [
        "direct_supervised_cases_eligible"
    ]


def test_training_export_rejects_unknown_kind(tmp_path) -> None:
    with pytest.raises(ValueError, match="kind must be sft, preference, or evals"):
        export_training(tmp_path, kind="unknown")
