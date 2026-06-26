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
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.training import audit_training_exports, export_training
from news_scalping_lab.utils import KST, canonical_json, read_json, sha256_text, stable_id


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


def _write_jsonl(path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _brain_record(
    record_id: str,
    record_type: str,
    *,
    training_target: str,
    training_eligible: bool,
    payload: dict[str, object] | None = None,
) -> BrainRecordEnvelope:
    available_from = datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST)
    body = {
        "record_id": record_id,
        "record_type": record_type,
        "episode_id": "EP-record-training",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "training_target": training_target,
        "training_eligible": training_eligible,
        **(payload or {}),
    }
    payload_hash = sha256_text(canonical_json(body))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id="EP-record-training",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target=training_target,
        evidence_phase="POSTMORTEM",
        training_eligible=training_eligible,
        eligibility_reason="record training fixture"
        if training_eligible
        else "audit-only fixture",
        status="tentative",
        confidence_label="medium",
        provenance_source_ids=[f"SRC-{record_id}"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=body,
    )


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
    sft_manifest = read_json(sft.manifest_path)
    accepted_provenance = {
        "source_id": "EP-training:accepted_episode",
        "source_type": "accepted_research_episode",
        "uri": "research/accepted/EP-training.json",
        "content_sha256": sft_manifest["source_hashes"]["EP-training"],
    }

    assert sft.row_count == 5
    assert {row["split"] for row in sft_rows} == {"sft", "sft_postmortem"}
    assert {row["split"] for row in preference_rows} == {"preference"}
    assert {row["split"] for row in eval_rows} == {"evals"}
    assert all(
        row["example_id"]
        == stable_id(
            "TRN",
            row["split"],
            row["task"],
            row["episode_id"],
            canonical_json(row["input"]),
        )
        for row in [*sft_rows, *preference_rows, *eval_rows]
    )
    assert all(accepted_provenance in row["provenance"] for row in sft_rows)
    assert all(accepted_provenance in row["provenance"] for row in preference_rows)
    assert all(accepted_provenance in row["provenance"] for row in eval_rows)
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

    assert sft_manifest["row_count"] == sft.row_count
    assert sft_manifest["task_counts"]["blind_reasoning"] == 1
    assert sft_manifest["task_counts"]["leader_selection_comparison"] == 1
    assert sft_manifest["required_training_categories"] == [
        "blind_reasoning_examples",
        "theme_formation_examples",
        "beneficiary_discovery_examples",
        "leader_selection_comparisons",
        "positive_vs_negative_candidate_preferences",
        "failure_correction_examples",
    ]
    assert sft_manifest["training_categories"] == [
        "blind_reasoning_examples",
        "theme_formation_examples",
        "beneficiary_discovery_examples",
        "leader_selection_comparisons",
        "failure_correction_examples",
    ]
    assert sft_manifest["category_counts"] == {
        "blind_reasoning_examples": 1,
        "theme_formation_examples": 1,
        "beneficiary_discovery_examples": 1,
        "leader_selection_comparisons": 1,
        "failure_correction_examples": 1,
    }
    assert sft_manifest["missing_training_categories"] == []
    assert sft_manifest["blind_safe_row_count"] == 4
    assert sft_manifest["hindsight_row_count"] == 1
    assert sft_manifest["eligible_episode_count"] == 1
    assert sft_manifest["skipped_episode_count"] == 0
    assert sft_manifest["skipped_episodes"] == []
    assert sft_manifest["source_phase_counts"] == {"BLIND": 4, "POSTMORTEM": 1}
    assert sft_manifest["output_file"] == "training_exports/sft/sft.jsonl"
    assert sft_manifest["output_sha256"]
    assert set(sft_manifest["phase_outputs"]) == {"BLIND", "POSTMORTEM"}
    assert sft_manifest["phase_outputs"]["BLIND"]["output_file"] == (
        "training_exports/sft/blind_sft.jsonl"
    )
    assert sft_manifest["phase_outputs"]["BLIND"]["row_count"] == 4
    assert sft_manifest["phase_outputs"]["BLIND"]["source_phase"] == "BLIND"
    assert (
        sft_manifest["phase_outputs"]["BLIND"]["hindsight_safe_for_blind_sft"] is True
    )
    assert sft_manifest["phase_outputs"]["POSTMORTEM"]["output_file"] == (
        "training_exports/sft/postmortem_sft.jsonl"
    )
    assert sft_manifest["phase_outputs"]["POSTMORTEM"]["row_count"] == 1
    assert sft_manifest["phase_outputs"]["POSTMORTEM"]["source_phase"] == "POSTMORTEM"
    assert (
        sft_manifest["phase_outputs"]["POSTMORTEM"]["hindsight_safe_for_blind_sft"]
        is False
    )
    assert (
        _jsonl(tmp_path / sft_manifest["phase_outputs"]["BLIND"]["output_file"])
        == blind_rows
    )
    assert _jsonl(
        tmp_path / sft_manifest["phase_outputs"]["POSTMORTEM"]["output_file"]
    ) == failure_rows
    assert sft_manifest["phase_outputs"]["BLIND"]["output_sha256"]
    assert sft_manifest["phase_outputs"]["POSTMORTEM"]["output_sha256"]
    assert (
        "The combined output_file is for audit and compatibility; use phase_outputs.BLIND "
        "for blind-only SFT."
    ) in sft_manifest["notes"]
    assert "Do not train postmortem labels as if they were blind answers." in sft_manifest["notes"]
    preference_manifest = read_json(preference.manifest_path)
    evals_manifest = read_json(evals.manifest_path)
    assert preference_manifest["output_file"] == "training_exports/preference/preference.jsonl"
    assert evals_manifest["output_file"] == "training_exports/evals/evals.jsonl"
    assert preference_manifest["phase_outputs"]["BLIND"]["row_count"] == 0
    assert preference_manifest["phase_outputs"]["POSTMORTEM"]["row_count"] == 1
    assert evals_manifest["phase_outputs"]["BLIND"]["row_count"] == 0
    assert evals_manifest["phase_outputs"]["POSTMORTEM"]["row_count"] == 3
    assert preference_manifest["category_counts"] == {
        "positive_vs_negative_candidate_preferences": 1
    }
    assert preference_manifest["missing_training_categories"] == []
    assert evals_manifest["category_counts"] == {"evaluation_examples": 3}
    assert evals_manifest["missing_training_categories"] == []
    assert audit_training_exports(tmp_path)["passed"] is True
    training_report = read_json(tmp_path / "diagnostics" / "training_export_report.json")
    assert training_report["schema_version"] == "nslab.training_export_diagnostics.v1"
    assert training_report["passed"] is True
    assert training_report["available_manifest_kinds"] == ["evals", "preference", "sft"]
    assert training_report["missing_manifest_kinds"] == []
    assert training_report["source_episode_count"] == 1
    assert training_report["row_count"] == 9
    assert training_report["blind_safe_row_count"] == 4
    assert training_report["hindsight_row_count"] == 5
    assert training_report["exports"]["sft"]["row_count"] == 5
    assert training_report["exports"]["preference"]["row_count"] == 1
    assert training_report["exports"]["evals"]["row_count"] == 3
    assert training_report["exports"]["sft"]["category_counts"] == {
        "blind_reasoning_examples": 1,
        "theme_formation_examples": 1,
        "beneficiary_discovery_examples": 1,
        "leader_selection_comparisons": 1,
        "failure_correction_examples": 1,
    }


def test_training_export_report_separates_unique_and_per_export_record_counts(
    tmp_path,
) -> None:
    records = [
        _brain_record(
            "BRAIN-ISSUER",
            "supervised_issuer_day_case",
            training_target="issuer_day_price_response",
            training_eligible=True,
            payload={
                "issuer_day_case_id": "ISSUER-1",
                "ticker": "111111",
                "sample_weight": 1.0,
                "response_class": "upper_limit",
                "D_outcome": {"label_quality": "verified"},
            },
        ),
        _brain_record(
            "BRAIN-PAIR",
            "blind_leader_preference_pair",
            training_target="outcome_preferred_candidate",
            training_eligible=True,
            payload={
                "blind_pair_id": "PAIR-1",
                "blind_preferred_ticker": "111111",
                "blind_rejected_ticker": "222222",
                "outcome_winner_ticker": "111111",
                "blind_preference_correct": True,
            },
        ),
        _brain_record(
            "BRAIN-MEMORY",
            "memory_claim",
            training_target="legacy_catalog_only",
            training_eligible=False,
        ),
    ]
    records_dir = tmp_path / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        records_dir / "EP-record-training.jsonl",
        [record.model_dump(mode="json") for record in records],
    )

    export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    assert audit_training_exports(tmp_path)["passed"] is True

    training_report = read_json(tmp_path / "diagnostics" / "training_export_report.json")
    assert training_report["source_record_count"] == 3
    assert training_report["eligible_record_count"] == 3
    assert training_report["exported_record_count"] == 3
    assert training_report["skipped_record_count"] == 6
    assert training_report["per_export_eligible_record_count"] == 3
    assert training_report["per_export_exported_record_count"] == 3
    assert training_report["per_export_skipped_record_count"] == 6
    assert training_report["unique_source_record_count"] == 3
    assert training_report["unique_training_eligible_record_count"] == 2
    assert training_report["unique_exported_record_count"] == 2
    assert training_report["unique_skipped_record_count"] == 1
    assert training_report["unique_exported_record_ids"] == [
        "BRAIN-ISSUER",
        "BRAIN-PAIR",
    ]
    assert training_report["unique_skipped_record_ids"] == ["BRAIN-MEMORY"]


def test_training_audit_rejects_ineligible_and_phase_mixed_rows(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode()
    store.save_episode(episode)
    store.accept(episode.episode_id)
    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")

    assert audit_training_exports(tmp_path)["passed"] is True

    rows = _jsonl(sft.path)
    rows[0]["eligibility_basis"]["satisfied"] = False
    _write_jsonl(sft.path, rows)
    manifest = read_json(sft.manifest_path)
    blind_path = tmp_path / manifest["phase_outputs"]["BLIND"]["output_file"]
    blind_rows = _jsonl(blind_path)
    blind_rows[0]["source_phase"] = "POSTMORTEM"
    _write_jsonl(blind_path, blind_rows)

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert any(finding.startswith("sft: exported ineligible row") for finding in audit["findings"])
    assert any(finding == "sft: output_file sha256 mismatch" for finding in audit["findings"])
    assert any(
        finding.startswith("sft: phase_outputs.BLIND contains POSTMORTEM row")
        for finding in audit["findings"]
    )
    training_report = read_json(tmp_path / "diagnostics" / "training_export_report.json")
    assert training_report["passed"] is False
    assert training_report["findings"] == audit["findings"]


def test_training_audit_rejects_non_sealed_preference_record_rows(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode()
    store.save_episode(episode)
    store.accept(episode.episode_id)
    export_training(tmp_path, kind="sft")
    preference = export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    manifest = read_json(preference.manifest_path)
    manifest["source_mode"] = "brain_records"
    preference.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert any(
        finding.startswith(
            "preference: brain record preference row is not a sealed leader pair"
        )
        for finding in audit["findings"]
    )


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


def test_training_export_does_not_skip_episode_with_evals_rows(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode().model_copy(
        update={
            "episode_id": "EP-evals-retrospective-only",
            "eligibility_matrix": EligibilityMatrix(
                forecast_evaluation_eligible=False,
                direct_supervised_cases_eligible=False,
                theme_supervised_cases_eligible=False,
                leader_pair_training_eligible=False,
                retrospective_memory_eligible=True,
                brain_eligible=True,
                reasons={
                    "direct_supervised_cases_eligible": (
                        "resolved candidate D-day outcomes are unavailable"
                    )
                },
            ),
        }
    )
    store.save_episode(episode)
    store.accept(episode.episode_id)

    evals = export_training(tmp_path, kind="evals")

    rows = _jsonl(evals.path)
    manifest = read_json(evals.manifest_path)
    assert evals.row_count == 1
    assert rows[0]["task"] == "failure_code_eval"
    assert manifest["eligible_episode_count"] == 1
    assert manifest["skipped_episode_count"] == 0
    assert manifest["skipped_episodes"] == []


def test_training_export_rejects_unknown_kind(tmp_path) -> None:
    with pytest.raises(ValueError, match="kind must be sft, preference, or evals"):
        export_training(tmp_path, kind="unknown")
