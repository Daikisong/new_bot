from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.context.final_synthesis import (
    FINAL_SYNTHESIS_REQUIRED_INPUTS,
    final_synthesis_input_summary,
)
from news_scalping_lab.contracts.models import BlindAnalysis, BlindPrediction
from news_scalping_lab.reporting.bundle import (
    _blind_execution_guard_verified,
    export_analysis_bundle,
)
from news_scalping_lab.research_import.bundle import parse_bundle
from news_scalping_lab.utils import KST, canonical_json, file_sha256, sha256_text, write_json


def _candidate_web_context_row(row: dict[str, object]) -> dict[str, object]:
    context_row = {
        "candidate_rank": row.get("candidate_rank"),
        "candidate_ticker": row.get("candidate_ticker"),
        "candidate_company_name": row.get("candidate_company_name"),
        "candidate_path_type": row.get("candidate_path_type"),
        "candidate_subject_type": row.get("candidate_subject_type"),
        "candidate_expansion_path": row.get("candidate_expansion_path"),
        "candidate_expansion_hypothesis": row.get("candidate_expansion_hypothesis"),
        "candidate_investigation_questions": row.get(
            "candidate_investigation_questions"
        ),
        "verification_focus": row.get("verification_focus"),
        "source_id": row.get("source_id"),
        "query": row.get("query"),
        "title": row.get("title"),
        "url": row.get("url"),
        "snippet": row.get("snippet"),
        "published_at": row.get("published_at"),
        "time_verified": row.get("time_verified"),
        "content_sha256": row.get("content_sha256"),
        "opened_text_excerpt": row.get("opened_text_excerpt"),
    }
    if "timestamp_precision" in row:
        context_row["timestamp_precision"] = row.get("timestamp_precision")
    return context_row


def test_blind_execution_guard_allows_d_minus_one_price_access_only() -> None:
    assert _blind_execution_guard_verified(
        {
            "blind_context_mode": "D_MINUS_ONE_PRICE_BLIND",
            "blind_web_search_call_count": 0,
            "blind_price_repository_access_count": 3,
            "blind_current_price_access_count": 0,
            "no_d_outcome_exposed": True,
        }
    )
    assert _blind_execution_guard_verified(
        {
            "blind_context_mode": "CUTOFF_SAFE_WEB_AND_D_MINUS_ONE_PRICE_BLIND",
            "blind_web_search_call_count": 2,
            "blind_price_repository_access_count": 3,
            "blind_current_price_access_count": 0,
            "no_d_outcome_exposed": True,
        }
    )
    assert not _blind_execution_guard_verified(
        {
            "blind_context_mode": "D_MINUS_ONE_PRICE_BLIND",
            "blind_web_search_call_count": 1,
            "blind_price_repository_access_count": 3,
            "blind_current_price_access_count": 0,
            "no_d_outcome_exposed": True,
        }
    )
    assert not _blind_execution_guard_verified(
        {
            "blind_context_mode": "D_MINUS_ONE_PRICE_BLIND",
            "blind_web_search_call_count": 0,
            "blind_price_repository_access_count": 3,
            "blind_current_price_access_count": 1,
            "no_d_outcome_exposed": True,
        }
    )


def test_export_analysis_bundle_writes_single_markdown_bundle(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    run_id = "RUN-bundle"
    trade_date = date(2030, 1, 10)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    prediction = BlindPrediction(
        prediction_id="PRED-bundle",
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        created_at=cutoff_at,
        sealed_at=cutoff_at,
        blind_analysis=BlindAnalysis(summary="blind summary"),
        context_manifest_id=run_id,
    )
    blind_hash = sha256_text(canonical_json(prediction.model_dump(mode="json")))
    prediction = prediction.model_copy(update={"blind_artifact_sha256": blind_hash})
    prediction_path = tmp_path / "predictions" / f"{trade_date.isoformat()}.json"
    write_json(prediction_path, prediction.model_dump(mode="json"))
    report_path = tmp_path / "reports" / f"{trade_date.isoformat()}_preopen.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# preopen report\n", encoding="utf-8")
    row_disposition = canonical_json(
        {
            "row_number": 1,
            "event_id": "EVT-1",
            "source_id": "SRC-1",
            "provenance_source_ids": ["SRC-1"],
            "disposition": "INCLUDED_BEFORE_CUTOFF",
        }
    ) + "\n"
    row_path = tmp_path / "runs" / "checkpoints" / "row_disposition" / run_id / "row_disposition.jsonl"
    row_path.parent.mkdir(parents=True)
    row_path.write_text(row_disposition, encoding="utf-8")
    source_ledger = canonical_json(
        {
            "source_id": "SRC-1",
            "source_type": "news_csv_row",
            "title": "source title",
            "publisher": None,
            "url": "news://EVT-1",
            "source_url": "news://EVT-1",
            "published_at": cutoff_at.isoformat(),
            "retrieved_at": cutoff_at.isoformat(),
            "time_verified": True,
            "available_before_cutoff": True,
            "usage_phase": "BLIND",
            "input_row_ids": [1],
            "event_ids": ["EVT-1"],
            "content_sha256": "abc",
            "notes": "test source",
        }
    ) + "\n"
    source_path = tmp_path / "runs" / "checkpoints" / "source_ledger" / run_id / "source_ledger.jsonl"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source_ledger, encoding="utf-8")
    candidate_web_check_payload = {
        "schema_version": "nslab.candidate_web_check.v1",
        "run_id": run_id,
        "candidate_rank": 1,
        "candidate_ticker": "UNKNOWN",
        "candidate_company_name": "BundleCandidateCo",
        "candidate_path_type": "SINGLE_EVENT",
        "verification_focus": ["listed_security_and_exact_ticker"],
        "source_id": "WEB-CANDIDATE-1",
        "query": "candidate verification",
        "title": "candidate source",
        "url": "https://example.test/candidate",
        "source_url": "https://example.test/candidate",
        "snippet": "candidate",
        "published_at": "2030-01-10T08:30:00+09:00",
        "timestamp_precision": "datetime",
        "retrieved_at": "2030-01-10T08:31:00+09:00",
        "cutoff_at": cutoff_at.isoformat(),
        "time_verified": True,
        "available_before_cutoff": True,
        "content_sha256": "candidate-hash",
    }
    candidate_web_checks = canonical_json(candidate_web_check_payload) + "\n"
    candidate_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_web_checks"
        / run_id
        / "candidate_web_checks.jsonl"
    )
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_text(candidate_web_checks, encoding="utf-8")
    candidate_verification_payload = {
        "schema_version": "nslab.candidate_verification.v1",
        "run_id": run_id,
        "created_at": cutoff_at.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "required_dimensions": ["listed_security_and_exact_ticker"],
        "subject_count": 1,
        "findings": [
            {
                "subject_type": "final_candidate",
                "candidate_rank": 1,
                "candidate_ticker": "UNKNOWN",
                "candidate_company_name": "BundleCandidateCo",
                "candidate_path_type": "SINGLE_EVENT",
                "query": "candidate verification",
                "source_count": 1,
                "excluded_source_count": 1,
                "accepted_source_ids": ["WEB-CANDIDATE-1"],
                "excluded_source_ids": ["WEB-CANDIDATE-EXCLUDED"],
                "verification_dimensions": [
                    {
                        "name": "listed_security_and_exact_ticker",
                        "status": "source_collected",
                        "evidence_source_ids": ["WEB-CANDIDATE-1"],
                        "notes": ["cutoff-safe source collected"],
                    }
                ],
                "d_minus_one_market_data_only": False,
                "uncertainties": [],
            }
        ],
        "notes": ["test verification"],
    }
    candidate_verification = canonical_json(candidate_verification_payload)
    candidate_verification_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_verifications"
        / run_id
        / "candidate_verification.json"
    )
    candidate_verification_path.parent.mkdir(parents=True)
    candidate_verification_path.write_text(candidate_verification, encoding="utf-8")
    excluded_candidate_web_checks = canonical_json(
        {
            "schema_version": "nslab.excluded_candidate_web_check.v1",
            "run_id": run_id,
            "candidate_rank": 1,
            "candidate_ticker": "UNKNOWN",
            "candidate_company_name": "BundleCandidateCo",
            "candidate_path_type": "SINGLE_EVENT",
            "source_id": "WEB-CANDIDATE-EXCLUDED",
            "query": "candidate verification",
            "title": "excluded candidate source",
            "url": "https://example.test/excluded",
            "source_url": "https://example.test/excluded",
            "snippet": "excluded",
            "published_at": "2030-01-10T09:30:00+09:00",
            "retrieved_at": "2030-01-10T09:31:00+09:00",
            "cutoff_at": cutoff_at.isoformat(),
            "exclusion_reason": "after_cutoff",
            "time_verified": True,
            "available_before_cutoff": False,
        }
    ) + "\n"
    excluded_candidate_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_web_checks"
        / run_id
        / "excluded_candidate_web_checks.jsonl"
    )
    excluded_candidate_path.write_text(excluded_candidate_web_checks, encoding="utf-8")
    final_context_payload = {
        "required_inputs": list(FINAL_SYNTHESIS_REQUIRED_INPUTS),
        "current_news": ["bundle news"],
        "open_world_first_analysis": [],
        "news_novelty_review": {"findings": []},
        "additional_semantic_retrieval": {"rows": [], "episodes": []},
        "open_world_candidate_expansion": {"findings": []},
        "web_research": {"sources": []},
        "global_brain": [],
        "all_shard_brains": [],
        "all_shard_contributions": [],
        "retrieved_raw_episodes": [],
        "positive_cases": [],
        "negative_cases": [],
        "counterexamples": [],
        "candidate_research": {"candidates": []},
        "candidate_web_checks": [
            _candidate_web_context_row(candidate_web_check_payload)
        ],
        "candidate_verification": candidate_verification_payload,
        "red_team_output": {"candidate_findings": []},
        "d_minus_one_market_data": {"snapshots": []},
        "company_memory": [],
        "market_memory": [],
    }
    final_context_summary = final_synthesis_input_summary(final_context_payload)
    final_synthesis_context = json.dumps(
        {
            "schema_version": "nslab.final_synthesis_context.v1",
            "run_id": run_id,
            "prompt_version": "synthesis.final.v1",
            "required_inputs": list(FINAL_SYNTHESIS_REQUIRED_INPUTS),
            "payload_sha256": sha256_text(canonical_json(final_context_payload)),
            "input_summary": final_context_summary,
            "payload": final_context_payload,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    final_context_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "final_synthesis_context"
        / run_id
        / "final_synthesis_context.json"
    )
    final_context_path.parent.mkdir(parents=True)
    final_context_path.write_text(final_synthesis_context, encoding="utf-8")
    receipt = {
        "schema_version": "nslab.blind_seal_receipt.v1",
        "run_id": run_id,
        "prediction_id": prediction.prediction_id,
        "trade_date": trade_date.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "sealed_at": cutoff_at.isoformat(),
        "phase": "BLIND_SEALED",
        "blind_context_mode": "NEWS_ONLY_STRICT",
        "blind_artifact_sha256": blind_hash,
        "blind_prediction_path": prediction_path.relative_to(tmp_path).as_posix(),
        "row_disposition_sha256": sha256_text(row_disposition),
        "row_disposition_coverage_ratio": 1.0,
        "source_ledger_sha256": sha256_text(source_ledger),
        "no_d_outcome_exposed": True,
        "validation": {
            "blind_web_search_call_count": 0,
            "blind_price_repository_access_count": 0,
            "blind_current_price_access_count": 0,
            "canonical_blind_hash_verified": True,
        },
    }
    receipt_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "blind_seal"
        / run_id
        / "blind_seal_receipt.json"
    )
    write_json(receipt_path, receipt)
    phase_state = {
        "schema_version": "nslab.phase_state.v1",
        "run_id": run_id,
        "phase": "BLIND_SEALED",
        "completed_phases": ["PHASE_A_NEWS_ONLY_BLIND"],
        "trade_date": trade_date.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "sealed_at": cutoff_at.isoformat(),
        "blind_seal_receipt_sha256": sha256_text(receipt_path.read_text(encoding="utf-8")),
    }
    phase_path = tmp_path / "runs" / "checkpoints" / "phase_state" / run_id / "phase_state.json"
    write_json(phase_path, phase_state)
    manifest_payload = {
        "run_id": run_id,
        "trade_date": trade_date.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "blind_context_mode": "NEWS_ONLY_STRICT",
        "blind_web_search_call_count": 0,
        "blind_price_repository_access_count": 0,
        "blind_current_price_access_count": 0,
        "no_d_outcome_exposed": True,
        "blind_artifact_sha256": blind_hash,
        "prediction_artifact": prediction_path.relative_to(tmp_path).as_posix(),
        "prediction_sha256": file_sha256(prediction_path),
        "report_artifact": report_path.relative_to(tmp_path).as_posix(),
        "report_sha256": sha256_text(report_path.read_text(encoding="utf-8")),
        "blind_seal_receipt_artifact": receipt_path.relative_to(tmp_path).as_posix(),
        "blind_seal_receipt_sha256": sha256_text(
            receipt_path.read_text(encoding="utf-8")
        ),
        "phase_state_artifact": phase_path.relative_to(tmp_path).as_posix(),
        "phase_state_sha256": sha256_text(phase_path.read_text(encoding="utf-8")),
        "price_snapshot": {"source_name": "mock", "allowed_through": "2030-01-09"},
        "row_disposition_artifact": row_path.relative_to(tmp_path).as_posix(),
        "row_disposition_sha256": sha256_text(row_disposition),
        "row_disposition_coverage_ratio": 1.0,
        "source_ledger_artifact": source_path.relative_to(tmp_path).as_posix(),
        "source_ledger_sha256": sha256_text(source_ledger),
        "source_ledger_entry_count": 1,
        "candidate_web_check_artifact": candidate_path.relative_to(tmp_path).as_posix(),
        "candidate_web_check_sha256": sha256_text(candidate_web_checks),
        "candidate_web_check_count": 1,
        "candidate_verification_artifact": candidate_verification_path.relative_to(
            tmp_path
        ).as_posix(),
        "candidate_verification_sha256": sha256_text(candidate_verification),
        "candidate_verification_count": 1,
        "final_synthesis_context_artifact": final_context_path.relative_to(
            tmp_path
        ).as_posix(),
        "final_synthesis_context_sha256": sha256_text(final_synthesis_context),
        "final_synthesis_context_summary": final_context_summary,
        "excluded_candidate_web_check_artifact": excluded_candidate_path.relative_to(
            tmp_path
        ).as_posix(),
        "excluded_candidate_web_check_sha256": sha256_text(
            excluded_candidate_web_checks
        ),
        "excluded_candidate_web_check_count": 1,
    }
    write_json(tmp_path / "runs" / "manifests" / f"{run_id}.json", manifest_payload)

    output = export_analysis_bundle(settings, run_id=run_id)
    parsed = parse_bundle(output)

    assert output.name == "20300110_nslab_episode_bundle.md"
    assert set(parsed.blocks) == {
        "research_report.md",
        "blind_prediction.json",
        "research_episode.json",
        "row_disposition.jsonl",
        "brain_delta.jsonl",
        "source_ledger.jsonl",
        "candidate_web_checks.jsonl",
        "candidate_verification.json",
        "final_synthesis_context.json",
        "excluded_candidate_web_checks.jsonl",
        "phase_state.json",
        "bundle_manifest.json",
    }
    assert parsed.validation["blind_hash_verified"]
    assert parsed.validation["front_matter_identity_verified"]
    assert parsed.validation["prediction_file_hash_verified"]
    assert parsed.validation["research_report_hash_verified"]
    assert parsed.validation["blind_execution_guard_verified"]
    assert parsed.validation["row_disposition_hash_verified"]
    assert parsed.validation["row_disposition_coverage_verified"]
    assert parsed.validation["source_ledger_hash_verified"]
    assert parsed.validation["source_ledger_entry_count_verified"]
    assert parsed.validation["candidate_web_check_hash_verified"]
    assert parsed.validation["candidate_web_check_count_verified"]
    assert parsed.validation["candidate_verification_hash_verified"]
    assert parsed.validation["candidate_verification_count_verified"]
    assert parsed.validation["candidate_verification_contract_verified"]
    assert parsed.validation["final_synthesis_context_hash_verified"]
    assert parsed.validation["final_synthesis_context_contract_verified"]
    assert parsed.validation["final_synthesis_context_candidate_web_checks_verified"]
    assert parsed.validation[
        "final_synthesis_context_candidate_verification_verified"
    ]
    assert parsed.validation["excluded_candidate_web_check_hash_verified"]
    assert parsed.validation["excluded_candidate_web_check_count_verified"]
    assert parsed.validation["research_episode_hash_verified"]
    assert parsed.validation["brain_delta_hash_verified"]
    assert parsed.validation["blind_seal_receipt_hash_verified"]
    assert parsed.validation["blind_seal_receipt_contract_verified"]
    assert parsed.validation["phase_state_hash_verified"]
    assert parsed.validation["phase_state_contract_verified"]
    assert parsed.validation["phase_state_receipt_link_verified"]
    assert parsed.validation["id_reference_integrity_verified"]
    assert parsed.validation["manifest_validation_self_consistent_verified"]
    manifest = parsed.json_blocks["bundle_manifest.json"]
    assert isinstance(manifest, dict)
    assert manifest["bundle_incomplete"] is True
    assert manifest["blind_seal_receipt_sha256"]
    assert manifest["validation"]["research_episode_hash_verified"] is True
    assert manifest["validation"]["blind_execution_guard_verified"] is True
    assert manifest["validation"]["front_matter_identity_verified"] is True
    assert manifest["validation"]["prediction_file_hash_verified"] is True
    assert manifest["validation"]["research_report_hash_verified"] is True
    assert manifest["validation"]["brain_delta_hash_verified"] is True
    assert manifest["validation"]["row_disposition_coverage_verified"] is True
    assert manifest["validation"]["source_ledger_entry_count_verified"] is True
    assert manifest["validation"]["blind_seal_receipt_contract_verified"] is True
    assert manifest["validation"]["candidate_web_check_hash_verified"] is True
    assert manifest["validation"]["candidate_web_check_count_verified"] is True
    assert manifest["validation"]["candidate_verification_hash_verified"] is True
    assert manifest["validation"]["candidate_verification_count_verified"] is True
    assert manifest["validation"]["candidate_verification_contract_verified"] is True
    assert manifest["validation"]["final_synthesis_context_hash_verified"] is True
    assert manifest["validation"]["final_synthesis_context_contract_verified"] is True
    assert (
        manifest["validation"]["final_synthesis_context_candidate_web_checks_verified"]
        is True
    )
    assert (
        manifest["validation"][
            "final_synthesis_context_candidate_verification_verified"
        ]
        is True
    )
    assert manifest["validation"]["excluded_candidate_web_check_hash_verified"] is True
    assert manifest["validation"]["excluded_candidate_web_check_count_verified"] is True
    assert manifest["validation"]["phase_state_hash_verified"] is True
    assert manifest["validation"]["phase_state_contract_verified"] is True
    assert manifest["validation"]["phase_state_receipt_link_verified"] is True
    assert manifest["validation"]["id_reference_integrity_verified"] is True
    assert manifest["validation"]["manifest_validation_self_consistent_verified"] is True
    episode = parsed.json_blocks["research_episode.json"]
    assert isinstance(episode, dict)
    assert episode["blind_seal_receipt"]["phase"] == "BLIND_SEALED"
    assert parsed.json_blocks["phase_state.json"]["phase"] == "BLIND_SEALED"
    assert parsed.jsonl_blocks["brain_delta.jsonl"][0]["record_type"] == "bundle_incomplete"
    assert parsed.jsonl_blocks["candidate_web_checks.jsonl"][0]["source_id"] == (
        "WEB-CANDIDATE-1"
    )
    assert parsed.json_blocks["candidate_verification.json"]["findings"][0][
        "accepted_source_ids"
    ] == ["WEB-CANDIDATE-1"]
    assert parsed.json_blocks["final_synthesis_context.json"]["input_summary"][
        "current_news_count"
    ] == 1
    assert parsed.jsonl_blocks["excluded_candidate_web_checks.jsonl"][0]["source_id"] == (
        "WEB-CANDIDATE-EXCLUDED"
    )
    assert json.loads(parsed.blocks["row_disposition.jsonl"].splitlines()[1])["row_number"] == 1

    output_text = output.read_text(encoding="utf-8")
    tampered_prediction_path = tmp_path / "reports" / "tampered_prediction_bundle.md"
    tampered_prediction_path.write_text(
        output_text.replace('"prediction_id": "PRED-bundle"', '"prediction_id": "PRED-tampered"', 1),
        encoding="utf-8",
    )
    tampered_prediction = parse_bundle(tampered_prediction_path)
    assert not tampered_prediction.validation["prediction_file_hash_verified"]
    assert not tampered_prediction.validation["manifest_validation_self_consistent_verified"]

    tampered_report_path = tmp_path / "reports" / "tampered_report_bundle.md"
    tampered_report_path.write_text(
        output_text.replace("# preopen report", "# tampered report", 1),
        encoding="utf-8",
    )
    tampered_report = parse_bundle(tampered_report_path)
    assert not tampered_report.validation["research_report_hash_verified"]
    assert not tampered_report.validation["manifest_validation_self_consistent_verified"]

    tampered_front_matter_path = tmp_path / "reports" / "tampered_front_matter_bundle.md"
    tampered_front_matter_path.write_text(
        output_text.replace("run_id: RUN-bundle", "run_id: RUN-tampered", 1),
        encoding="utf-8",
    )
    tampered_front_matter = parse_bundle(tampered_front_matter_path)
    assert not tampered_front_matter.validation["front_matter_identity_verified"]
    assert not tampered_front_matter.validation["manifest_validation_self_consistent_verified"]

    bad_run_id = "RUN-other"
    write_json(
        tmp_path / "runs" / "manifests" / f"{bad_run_id}.json",
        {**manifest_payload, "run_id": bad_run_id},
    )
    with pytest.raises(ValueError, match=f"belongs to {run_id}, not {bad_run_id}"):
        export_analysis_bundle(settings, run_id=bad_run_id)
