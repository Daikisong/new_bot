from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

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
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    stable_id,
)


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


def _write_record_training_fixture(root: Path) -> list[BrainRecordEnvelope]:
    records = [
        _brain_record(
            "BRAIN-ISSUER",
            "supervised_issuer_day_case",
            training_target="issuer_day_price_response",
            training_eligible=True,
            payload={
                "issuer_day_case_id": "ISSUER-1",
                "ticker": "111111",
                "company_name": "WinnerCo",
                "sample_weight": 1.0,
                "response_class": "upper_limit",
                "D_outcome": {"label_quality": "verified"},
                "event_level_weights": {"EVT-1": 1.0},
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
    records_dir = root / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        records_dir / "EP-record-training.jsonl",
        [record.model_dump(mode="json") for record in records],
    )
    return records


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
    assert set(sft_manifest["phase_outputs"]) == {
        "AUDIT_ONLY",
        "BLIND",
        "POSTMORTEM",
    }
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
    assert sft_manifest["phase_outputs"]["AUDIT_ONLY"]["output_file"] == (
        "training_exports/sft/audit_only_sft.jsonl"
    )
    assert sft_manifest["phase_outputs"]["AUDIT_ONLY"]["row_count"] == 0
    assert sft_manifest["phase_outputs"]["AUDIT_ONLY"]["source_phase"] == "AUDIT_ONLY"
    assert sft_manifest["phase_outputs"]["AUDIT_ONLY"]["audit_only"] is True
    assert (
        sft_manifest["phase_outputs"]["AUDIT_ONLY"]["hindsight_safe_for_blind_sft"]
        is False
    )
    assert (
        _jsonl(tmp_path / sft_manifest["phase_outputs"]["BLIND"]["output_file"])
        == blind_rows
    )
    assert _jsonl(
        tmp_path / sft_manifest["phase_outputs"]["POSTMORTEM"]["output_file"]
    ) == failure_rows
    assert (
        _jsonl(tmp_path / sft_manifest["phase_outputs"]["AUDIT_ONLY"]["output_file"])
        == []
    )
    assert sft_manifest["phase_outputs"]["BLIND"]["output_sha256"]
    assert sft_manifest["phase_outputs"]["POSTMORTEM"]["output_sha256"]
    assert sft_manifest["phase_outputs"]["AUDIT_ONLY"]["output_sha256"]
    assert (
        "The combined output_file is for audit and compatibility; use phase_outputs.BLIND "
        "for blind-only SFT."
    ) in sft_manifest["notes"]
    assert "Do not train postmortem labels as if they were blind answers." in sft_manifest["notes"]
    assert (
        "Skipped brain records are written to phase_outputs.AUDIT_ONLY for auditability "
        "and must not be used as training rows."
    ) in sft_manifest["notes"]
    preference_manifest = read_json(preference.manifest_path)
    evals_manifest = read_json(evals.manifest_path)
    assert preference_manifest["output_file"] == "training_exports/preference/preference.jsonl"
    assert evals_manifest["output_file"] == "training_exports/evals/evals.jsonl"
    assert preference_manifest["phase_outputs"]["BLIND"]["row_count"] == 0
    assert preference_manifest["phase_outputs"]["POSTMORTEM"]["row_count"] == 1
    assert preference_manifest["phase_outputs"]["AUDIT_ONLY"]["row_count"] == 0
    assert evals_manifest["phase_outputs"]["BLIND"]["row_count"] == 0
    assert evals_manifest["phase_outputs"]["POSTMORTEM"]["row_count"] == 3
    assert evals_manifest["phase_outputs"]["AUDIT_ONLY"]["row_count"] == 0
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
    assert training_report["source_phase_counts"] == {"BLIND": 4, "POSTMORTEM": 5}
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


def test_blind_postmortem_exports_separated(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode()
    store.save_episode(episode)
    store.accept(episode.episode_id)

    sft = export_training(tmp_path, kind="sft")
    manifest = read_json(sft.manifest_path)
    blind_rows = _jsonl(tmp_path / manifest["phase_outputs"]["BLIND"]["output_file"])
    postmortem_rows = _jsonl(
        tmp_path / manifest["phase_outputs"]["POSTMORTEM"]["output_file"]
    )

    assert manifest["phase_outputs"]["BLIND"]["source_phase"] == "BLIND"
    assert manifest["phase_outputs"]["POSTMORTEM"]["source_phase"] == "POSTMORTEM"
    assert blind_rows
    assert postmortem_rows
    assert all(row["hindsight_safe_for_blind_sft"] is True for row in blind_rows)
    assert all(row["source_phase"] == "BLIND" for row in blind_rows)
    assert all(row["hindsight_safe_for_blind_sft"] is False for row in postmortem_rows)
    assert all(row["source_phase"] == "POSTMORTEM" for row in postmortem_rows)


def test_training_export_uses_explicit_brain_records(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode()
    store.save_episode(episode)
    store.accept(episode.episode_id)
    _write_record_training_fixture(tmp_path)

    sft = export_training(tmp_path, kind="sft")
    rows = _jsonl(sft.path)
    manifest = read_json(sft.manifest_path)

    assert manifest["source_mode"] == "brain_records"
    assert manifest["source_record_ids"] == [
        "BRAIN-ISSUER",
        "BRAIN-MEMORY",
        "BRAIN-PAIR",
    ]
    assert manifest["exported_record_ids"] == ["BRAIN-ISSUER"]
    assert {row["record_id"] for row in rows if not row.get("audit_only")} == {
        "BRAIN-ISSUER"
    }
    assert all(row["episode_id"] == "EP-record-training" for row in rows)


def test_record_backed_training_export_ignores_unreadable_legacy_episode(
    tmp_path,
) -> None:
    _write_record_training_fixture(tmp_path)
    store = ResearchStore(tmp_path)
    accepted_path = store.accepted_dir / "EP-record-training.json"
    accepted_path.write_text("{not valid json", encoding="utf-8")

    sft = export_training(tmp_path, kind="sft")
    preference = export_training(tmp_path, kind="preference")
    evals = export_training(tmp_path, kind="evals")

    sft_manifest = read_json(sft.manifest_path)
    preference_manifest = read_json(preference.manifest_path)
    evals_manifest = read_json(evals.manifest_path)
    for manifest in (sft_manifest, preference_manifest, evals_manifest):
        assert manifest["source_mode"] == "brain_records"
        assert manifest["source_episode_count"] == 1
        assert manifest["episode_ids"] == ["EP-record-training"]
        assert manifest["source_hashes"] == {
            "EP-record-training": file_sha256(accepted_path)
        }
    assert sft.row_count == 1
    assert preference.row_count == 1
    assert evals.row_count == 1


def test_preference_export_uses_sealed_pairs_only(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode()
    store.save_episode(episode)
    store.accept(episode.episode_id)
    _write_record_training_fixture(tmp_path)

    preference = export_training(tmp_path, kind="preference")
    rows = _jsonl(preference.path)
    manifest = read_json(preference.manifest_path)
    export_rows = [row for row in rows if not row.get("audit_only")]

    assert manifest["source_mode"] == "brain_records"
    assert manifest["eligible_record_ids"] == ["BRAIN-PAIR"]
    assert manifest["exported_record_ids"] == ["BRAIN-PAIR"]
    assert [row["record_id"] for row in export_rows] == ["BRAIN-PAIR"]
    assert export_rows[0]["input"]["blind_preferred_ticker"] == "111111"
    assert export_rows[0]["input"]["blind_rejected_ticker"] == "222222"
    assert export_rows[0]["output"]["outcome_winner_ticker"] == "111111"
    assert "WinnerCo" not in json.dumps(export_rows[0], ensure_ascii=False)


def test_ineligible_records_not_exported(tmp_path) -> None:
    _write_record_training_fixture(tmp_path)

    sft = export_training(tmp_path, kind="sft")
    preference = export_training(tmp_path, kind="preference")
    evals = export_training(tmp_path, kind="evals")

    for export in (sft, preference, evals):
        rows = _jsonl(export.path)
        manifest = read_json(export.manifest_path)
        audit_rows = _jsonl(
            tmp_path / manifest["phase_outputs"]["AUDIT_ONLY"]["output_file"]
        )
        assert "BRAIN-MEMORY" not in {
            row["record_id"] for row in rows if not row.get("audit_only")
        }
        assert any(row["record_id"] == "BRAIN-MEMORY" for row in audit_rows)


def test_issuer_day_unique_and_weight_one(tmp_path) -> None:
    _write_record_training_fixture(tmp_path)

    sft = export_training(tmp_path, kind="sft")
    manifest = read_json(sft.manifest_path)

    assert manifest["duplicate_issuer_day_count"] == 0
    assert manifest["duplicate_issuer_day_keys"] == []
    assert manifest["issuer_day_weight_sum_mismatch_count"] == 0
    assert manifest["issuer_day_weight_sum_mismatches"] == {}
    assert manifest["weight_validation_status"] == "passed"


def test_event_weights_sum_to_one(tmp_path) -> None:
    _write_record_training_fixture(tmp_path)

    sft = export_training(tmp_path, kind="sft")
    manifest = read_json(sft.manifest_path)

    assert manifest["direct_event_weight_sum_mismatch_count"] == 0
    assert manifest["direct_event_weight_sum_mismatches"] == {}
    assert manifest["weight_validation"]["direct_event_weight_sum_mismatches"] == {}
    assert manifest["weight_validation_status"] == "passed"


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

    sft = export_training(tmp_path, kind="sft")
    preference = export_training(tmp_path, kind="preference")
    evals = export_training(tmp_path, kind="evals")
    assert audit_training_exports(tmp_path)["passed"] is True

    sft_manifest = read_json(sft.manifest_path)
    preference_manifest = read_json(preference.manifest_path)
    evals_manifest = read_json(evals.manifest_path)
    for manifest in (sft_manifest, preference_manifest, evals_manifest):
        assert manifest["weight_validation_status"] == "passed"
        assert manifest["source_record_ids"] == [
            "BRAIN-ISSUER",
            "BRAIN-MEMORY",
            "BRAIN-PAIR",
        ]
        assert manifest["duplicate_issuer_day_count"] == 0
        assert manifest["duplicate_issuer_day_keys"] == []
        assert manifest["issuer_day_weight_sum_mismatch_count"] == 0
        assert manifest["issuer_day_weight_sum_mismatches"] == {}
        assert manifest["direct_event_weight_sum_mismatch_count"] == 0
        assert manifest["direct_event_weight_sum_mismatches"] == {}
        assert manifest["phase_outputs"]["AUDIT_ONLY"]["audit_only"] is True
        assert (
            manifest["phase_outputs"]["AUDIT_ONLY"]["hindsight_safe_for_blind_sft"]
            is False
        )
    assert sft_manifest["eligible_record_ids"] == ["BRAIN-ISSUER"]
    assert sft_manifest["exported_record_ids"] == ["BRAIN-ISSUER"]
    assert sft_manifest["skipped_record_ids"] == ["BRAIN-MEMORY", "BRAIN-PAIR"]
    assert preference_manifest["eligible_record_ids"] == ["BRAIN-PAIR"]
    assert preference_manifest["exported_record_ids"] == ["BRAIN-PAIR"]
    assert preference_manifest["skipped_record_ids"] == [
        "BRAIN-ISSUER",
        "BRAIN-MEMORY",
    ]
    assert evals_manifest["eligible_record_ids"] == ["BRAIN-ISSUER"]
    assert evals_manifest["exported_record_ids"] == ["BRAIN-ISSUER"]
    assert evals_manifest["skipped_record_ids"] == ["BRAIN-MEMORY", "BRAIN-PAIR"]

    sft_audit_only = _jsonl(
        tmp_path / sft_manifest["phase_outputs"]["AUDIT_ONLY"]["output_file"]
    )
    preference_audit_only = _jsonl(
        tmp_path / preference_manifest["phase_outputs"]["AUDIT_ONLY"]["output_file"]
    )
    evals_audit_only = _jsonl(
        tmp_path / evals_manifest["phase_outputs"]["AUDIT_ONLY"]["output_file"]
    )
    assert {row["record_id"] for row in sft_audit_only} == {
        "BRAIN-MEMORY",
        "BRAIN-PAIR",
    }
    assert {row["record_id"] for row in preference_audit_only} == {
        "BRAIN-ISSUER",
        "BRAIN-MEMORY",
    }
    assert {row["record_id"] for row in evals_audit_only} == {
        "BRAIN-MEMORY",
        "BRAIN-PAIR",
    }
    assert all(row["source_phase"] == "AUDIT_ONLY" for row in sft_audit_only)
    assert all(row["audit_only"] is True for row in preference_audit_only)
    assert all(row["kind"] == "evals" for row in evals_audit_only)
    assert "record_type_not_selected_for_export_kind" in {
        reason
        for row in sft_audit_only
        for reason in row["skip_reasons"]
    }

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
    assert training_report["source_phase_counts"] == {"POSTMORTEM": 3}
    assert training_report["source_record_hash_count"] == 3
    assert training_report["unique_source_record_ids"] == [
        "BRAIN-ISSUER",
        "BRAIN-MEMORY",
        "BRAIN-PAIR",
    ]
    assert training_report["unique_training_eligible_record_ids"] == [
        "BRAIN-ISSUER",
        "BRAIN-PAIR",
    ]
    assert sorted(training_report["source_record_hashes"]) == [
        "BRAIN-ISSUER",
        "BRAIN-MEMORY",
        "BRAIN-PAIR",
    ]
    assert training_report["unique_exported_record_ids"] == [
        "BRAIN-ISSUER",
        "BRAIN-PAIR",
    ]
    assert training_report["unique_skipped_record_ids"] == ["BRAIN-MEMORY"]
    assert training_report["weight_validation_statuses"] == {
        "evals": "passed",
        "preference": "passed",
        "sft": "passed",
    }
    assert training_report["duplicate_issuer_day_count"] == 0
    assert training_report["duplicate_issuer_day_keys"] == []
    assert training_report["issuer_day_weight_sum_mismatch_count"] == 0
    assert training_report["issuer_day_weight_sum_mismatches"] == {}
    assert training_report["direct_event_weight_sum_mismatch_count"] == 0
    assert training_report["direct_event_weight_sum_mismatches"] == {}

    sft_manifest["exported_record_ids"] = ["BRAIN-MEMORY"]
    sft.manifest_path.write_text(
        json.dumps(sft_manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    failed = audit_training_exports(tmp_path)
    assert not failed["passed"]
    assert "sft: exported_record_ids does not match manifest records" in failed[
        "findings"
    ]


def test_training_audit_rejects_invalid_skipped_record_reasons(tmp_path) -> None:
    _write_record_training_fixture(tmp_path)
    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    manifest = read_json(sft.manifest_path)
    manifest["skipped_records"][0]["skip_reasons"] = [
        "record_type_not_selected_for_export_kind",
        7,
    ]
    sft.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert "sft: skipped_records:1 skip_reasons is invalid" in audit["findings"]


def test_training_manifest_surfaces_duplicate_issuer_day_weight_validation(
    tmp_path,
) -> None:
    records = [
        _brain_record(
            "BRAIN-ISSUER-A",
            "supervised_issuer_day_case",
            training_target="issuer_day_price_response",
            training_eligible=True,
            payload={
                "issuer_day_case_id": "ISSUER-A",
                "ticker": "111111",
                "sample_weight": 0.5,
                "response_class": "upper_limit",
                "D_outcome": {"label_quality": "verified"},
            },
        ),
        _brain_record(
            "BRAIN-ISSUER-B",
            "supervised_issuer_day_case",
            training_target="issuer_day_price_response",
            training_eligible=True,
            payload={
                "issuer_day_case_id": "ISSUER-B",
                "ticker": "111111",
                "sample_weight": 0.5,
                "response_class": "failed_follow_through",
                "D_outcome": {"label_quality": "verified"},
            },
        ),
    ]
    records_dir = tmp_path / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        records_dir / "EP-record-training.jsonl",
        [record.model_dump(mode="json") for record in records],
    )

    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")

    manifest = read_json(sft.manifest_path)
    audit = audit_training_exports(tmp_path)
    training_report = read_json(tmp_path / "diagnostics" / "training_export_report.json")

    assert manifest["weight_validation_status"] == "failed"
    assert manifest["duplicate_issuer_day_count"] == 1
    assert manifest["duplicate_issuer_day_keys"] == ["2030-01-10|111111"]
    assert manifest["issuer_day_weight_sum_mismatch_count"] == 0
    assert manifest["issuer_day_weight_sum_mismatches"] == {}
    assert manifest["direct_event_weight_sum_mismatch_count"] == 0
    assert manifest["direct_event_weight_sum_mismatches"] == {}
    assert manifest["weight_validation"]["duplicate_issuer_day_count"] == 1
    assert audit["passed"] is False
    assert "sft: record weight validation failed" in audit["findings"]
    assert training_report["duplicate_issuer_day_count"] == 1
    assert training_report["duplicate_issuer_day_keys"] == ["2030-01-10|111111"]
    assert training_report["issuer_day_weight_sum_mismatch_count"] == 0
    assert training_report["direct_event_weight_sum_mismatch_count"] == 0


def test_training_manifest_surfaces_direct_event_weight_validation(tmp_path) -> None:
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
            "BRAIN-DIRECT-A",
            "supervised_direct_event_case",
            training_target="direct_event_response",
            training_eligible=True,
            payload={
                "case_id": "DIRECT-A",
                "issuer_day_case_id": "ISSUER-1",
                "ticker": "111111",
                "event_id": "EVT-A",
                "sample_weight": 0.4,
                "response_class": "upper_limit",
                "D_outcome": {"label_quality": "verified"},
            },
        ),
        _brain_record(
            "BRAIN-DIRECT-B",
            "supervised_direct_event_case",
            training_target="direct_event_response",
            training_eligible=True,
            payload={
                "case_id": "DIRECT-B",
                "issuer_day_case_id": "ISSUER-1",
                "ticker": "111111",
                "event_id": "EVT-B",
                "sample_weight": 0.4,
                "response_class": "upper_limit",
                "D_outcome": {"label_quality": "verified"},
            },
        ),
    ]
    records_dir = tmp_path / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        records_dir / "EP-record-training.jsonl",
        [record.model_dump(mode="json") for record in records],
    )

    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    manifest = read_json(sft.manifest_path)

    assert manifest["weight_validation_status"] == "failed"
    assert manifest["duplicate_issuer_day_count"] == 0
    assert manifest["issuer_day_weight_sum_mismatch_count"] == 0
    assert manifest["issuer_day_weight_sum_mismatches"] == {}
    assert manifest["direct_event_weight_sum_mismatch_count"] == 1
    assert manifest["direct_event_weight_sum_mismatches"] == {"ISSUER-1": 0.8}
    assert manifest["weight_validation"]["direct_event_weight_sum_mismatches"] == {
        "ISSUER-1": 0.8
    }

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert "sft: record weight validation failed" in audit["findings"]
    training_report = read_json(tmp_path / "diagnostics" / "training_export_report.json")
    assert training_report["weight_validation_statuses"] == {
        "evals": "failed",
        "preference": "failed",
        "sft": "failed",
    }
    assert training_report["direct_event_weight_sum_mismatch_count"] == 1
    assert training_report["direct_event_weight_sum_mismatches"] == {"ISSUER-1": 0.8}


def test_training_audit_requires_brain_record_source_hashes(tmp_path) -> None:
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
        )
    ]
    records_dir = tmp_path / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        records_dir / "EP-record-training.jsonl",
        [record.model_dump(mode="json") for record in records],
    )
    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    manifest = read_json(sft.manifest_path)
    manifest.pop("source_record_hashes")
    sft.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert "sft: source_record_hashes are missing" in audit["findings"]
    training_report = read_json(tmp_path / "diagnostics" / "training_export_report.json")
    assert training_report["passed"] is False
    assert "sft: source_record_hashes are missing" in training_report["findings"]


def test_training_audit_requires_brain_record_source_mode_when_records_exist(
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
        )
    ]
    records_dir = tmp_path / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        records_dir / "EP-record-training.jsonl",
        [record.model_dump(mode="json") for record in records],
    )
    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    manifest = read_json(sft.manifest_path)
    manifest["source_mode"] = "legacy_research_episodes"
    sft.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert (
        "sft: brain record store exists but export source_mode is not brain_records"
        in audit["findings"]
    )
    training_report = read_json(tmp_path / "diagnostics" / "training_export_report.json")
    assert training_report["brain_record_source_required"] is True
    assert training_report["record_store_source_record_count"] == 1


def test_training_audit_rejects_stale_brain_record_source_hashes(tmp_path) -> None:
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
    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    manifest = read_json(sft.manifest_path)
    manifest["source_record_count"] = 99
    manifest["source_record_hashes"].pop("BRAIN-MEMORY")
    manifest["source_record_hashes"]["BRAIN-ISSUER"] = "0" * 64
    manifest["source_record_hashes"]["BRAIN-EXTRA"] = "1" * 64
    sft.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert (
        "sft: source_record_count does not match current brain record store"
        in audit["findings"]
    )
    assert (
        "sft: source_record_hashes missing current brain records: BRAIN-MEMORY"
        in audit["findings"]
    )
    assert (
        "sft: source_record_hashes contain records outside current store: BRAIN-EXTRA"
        in audit["findings"]
    )
    assert (
        "sft: source_record_hashes mismatch current brain records: BRAIN-ISSUER"
        in audit["findings"]
    )
    training_report = read_json(tmp_path / "diagnostics" / "training_export_report.json")
    assert training_report["brain_record_source_required"] is True
    assert training_report["record_store_source_record_count"] == 2


def test_training_audit_rejects_skipped_brain_record_output_rows(tmp_path) -> None:
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
    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    rows = _jsonl(sft.path)
    rows[0]["record_id"] = "BRAIN-MEMORY"
    _write_jsonl(sft.path, rows)

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert "sft: exported skipped brain record IDs: BRAIN-MEMORY" in audit["findings"]
    assert (
        "sft: source brain records are neither exported nor skipped: BRAIN-ISSUER"
        in audit["findings"]
    )
    training_report = read_json(tmp_path / "diagnostics" / "training_export_report.json")
    assert "sft: exported skipped brain record IDs: BRAIN-MEMORY" in training_report[
        "findings"
    ]
    assert training_report["unique_training_eligible_record_ids"] == ["BRAIN-ISSUER"]
    assert training_report["unique_exported_record_ids"] == [
        "BRAIN-ISSUER",
        "BRAIN-MEMORY",
    ]


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


def test_training_audit_rejects_absolute_output_file(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode()
    store.save_episode(episode)
    store.accept(episode.episode_id)
    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    manifest = read_json(sft.manifest_path)
    manifest["output_file"] = sft.path.resolve().as_posix()
    sft.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert "sft: output_file must be project-relative" in audit["findings"]


def test_training_audit_rejects_escaping_phase_output_file(tmp_path) -> None:
    store = ResearchStore(tmp_path)
    episode = _accepted_episode()
    store.save_episode(episode)
    store.accept(episode.episode_id)
    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    manifest = read_json(sft.manifest_path)
    blind_meta = manifest["phase_outputs"]["BLIND"]
    blind_path = tmp_path / blind_meta["output_file"]
    outside_path = tmp_path.parent / f"{tmp_path.name}_outside_blind_sft.jsonl"
    outside_path.write_text(blind_path.read_text(encoding="utf-8"), encoding="utf-8")
    blind_meta["output_file"] = f"../{outside_path.name}"
    blind_meta["output_sha256"] = sha256_text(outside_path.read_text(encoding="utf-8"))
    sft.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert (
        "sft: phase_outputs.BLIND output_file escapes project root"
        in audit["findings"]
    )


def test_training_audit_rejects_tampered_audit_only_phase_output(tmp_path) -> None:
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
    sft = export_training(tmp_path, kind="sft")
    export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    manifest = read_json(sft.manifest_path)
    audit_only_path = tmp_path / manifest["phase_outputs"]["AUDIT_ONLY"]["output_file"]
    audit_only_path.write_text("", encoding="utf-8")

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert (
        "sft: phase_outputs.AUDIT_ONLY row_count mismatch expected 1, got 0"
        in audit["findings"]
    )
    assert "sft: phase_outputs.AUDIT_ONLY sha256 mismatch" in audit["findings"]
    assert "sft: phase_outputs.AUDIT_ONLY rows mismatch" in audit["findings"]


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


def test_training_audit_rejects_malformed_sealed_preference_fields(tmp_path) -> None:
    records = [
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
        )
    ]
    records_dir = tmp_path / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        records_dir / "EP-record-training.jsonl",
        [record.model_dump(mode="json") for record in records],
    )
    export_training(tmp_path, kind="sft")
    preference = export_training(tmp_path, kind="preference")
    export_training(tmp_path, kind="evals")
    rows = _jsonl(preference.path)
    rows[0]["input"]["blind_preferred_ticker"] = ""
    rows[0]["output"]["outcome_winner_ticker"] = None
    rows[0]["output"]["blind_preference_correct"] = "yes"
    rows[0]["output"]["training_mode"] = "cross_product"
    row_id = rows[0]["example_id"]
    _write_jsonl(preference.path, rows)

    audit = audit_training_exports(tmp_path)

    assert audit["passed"] is False
    assert f"preference: preference row blind_preferred_ticker is missing {row_id}" in audit[
        "findings"
    ]
    assert f"preference: preference row outcome_winner_ticker is missing {row_id}" in audit[
        "findings"
    ]
    assert (
        f"preference: preference row blind_preference_correct is invalid {row_id}"
        in audit["findings"]
    )
    assert f"preference: preference row training_mode is invalid {row_id}" in audit[
        "findings"
    ]


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
