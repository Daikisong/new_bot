from __future__ import annotations

import json
from datetime import date, datetime, time

from typer.testing import CliRunner

from news_scalping_lab.cli import app
from news_scalping_lab.context.final_synthesis import final_synthesis_input_summary
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    write_json,
)

RUNNER = CliRunner()


def test_goal_minimum_cli_commands_run_as_documented(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "mock")
    monkeypatch.setenv("NSLAB_WEB_PROVIDER", "mock")
    monkeypatch.delenv("NSLAB_STOCK_WEB_PATH", raising=False)
    monkeypatch.setenv("NSLAB_STOCK_WEB_CACHE", "false")
    monkeypatch.delenv("NSLAB_STOCK_WEB_CACHE_PATH", raising=False)
    monkeypatch.delenv("NSLAB_STOCK_WEB_REMOTE_URL", raising=False)
    news_csv = tmp_path / "data" / "inbox" / "news" / "minimum_cli_news.csv"
    news_csv.parent.mkdir(parents=True)
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-12","08:00:00","MinimumCliCo, contract catalyst",'
        '"Pre-cutoff event used to exercise the documented CLI surface."\n',
        encoding="utf-8",
    )

    assert RUNNER.invoke(app, ["init"]).exit_code == 0
    _assert_ok("doctor", RUNNER.invoke(app, ["doctor"]))
    doctor_strict = RUNNER.invoke(app, ["doctor", "--strict"])
    _assert_ok("doctor --strict", doctor_strict)
    assert json.loads(doctor_strict.output)["readiness"]["passed"] is True
    inspected_news = RUNNER.invoke(app, ["news", "inspect", str(news_csv)])
    _assert_ok("news inspect", inspected_news)
    inspected_news_payload = json.loads(inspected_news.output)
    assert inspected_news_payload["default_news_window_start_at"] == "2030-01-11T15:30:00+09:00"
    assert inspected_news_payload["missing_collected_at"] == 1
    imported_news = RUNNER.invoke(app, ["news", "import", str(news_csv)])
    _assert_ok("news import", imported_news)
    assert json.loads(imported_news.output)["imported"] is True

    rejected_path = tmp_path / "rejected_episode.json"
    rejected_path.write_text(
        _episode("EP-cli-reject", "Rejected CLI staging lesson.").model_dump_json(),
        encoding="utf-8",
    )
    imported_reject = RUNNER.invoke(
        app,
        ["research", "import", str(rejected_path), "--mode", "strict"],
    )
    _assert_ok("research import reject fixture", imported_reject)
    _assert_ok("research validate", RUNNER.invoke(app, ["research", "validate", "EP-cli-reject"]))
    rejected = RUNNER.invoke(app, ["research", "reject", "EP-cli-reject"])
    _assert_ok("research reject", rejected)
    assert "research/rejected/EP-cli-reject.json" in rejected.output

    accepted_path = tmp_path / "accepted_episode.json"
    accepted_path.write_text(
        _episode("EP-cli-one", "Accepted CLI update lesson.").model_dump_json(),
        encoding="utf-8",
    )
    imported_accept = RUNNER.invoke(
        app,
        ["research", "import", str(accepted_path), "--mode", "strict"],
    )
    _assert_ok("research import accept fixture", imported_accept)
    accepted = RUNNER.invoke(app, ["research", "accept", "EP-cli-one"])
    _assert_ok("research accept", accepted)
    assert "research/accepted/EP-cli-one.json" in accepted.output

    first_update = RUNNER.invoke(
        app,
        [
            "brain",
            "update",
            "--episode",
            "EP-cli-one",
            "--mode",
            "catalog",
            "--allow-catalog",
        ],
    )
    _assert_ok("brain update", first_update)
    first_version = json.loads(first_update.output)["brain_version"]

    batch_dir = tmp_path / "data" / "inbox" / "research"
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "batch_episode.json").write_text(
        _episode("EP-cli-two", "Batch CLI import lesson.").model_dump_json(),
        encoding="utf-8",
    )
    imported_batch = RUNNER.invoke(
        app,
        ["research", "import-batch", str(batch_dir), "--mode", "strict"],
    )
    _assert_ok("research import-batch", imported_batch)
    batch_payload = json.loads(imported_batch.output)
    assert batch_payload["accepted_episode_ids"] == ["EP-cli-two"]

    rebuilt = RUNNER.invoke(
        app,
        ["brain", "rebuild", "--mode", "catalog", "--allow-catalog"],
    )
    _assert_ok("brain rebuild", rebuilt)
    rebuilt_payload = json.loads(rebuilt.output)
    second_version = rebuilt_payload["brain_version"]
    assert rebuilt_payload["covered_episode_ids"] == ["EP-cli-one", "EP-cli-two"]
    _assert_ok("brain audit", RUNNER.invoke(app, ["brain", "audit"]))
    diffed = RUNNER.invoke(app, ["brain", "diff", first_version, second_version])
    _assert_ok("brain diff", diffed)
    diff_payload = json.loads(diffed.output)
    assert diff_payload["changed"] is True
    diff_path = tmp_path / diff_payload["markdown_path"]
    assert diff_path.exists()
    diff_text = diff_path.read_text(encoding="utf-8")
    assert first_version in diff_text
    assert second_version in diff_text

    analyzed = RUNNER.invoke(
        app,
        [
            "analyze",
            "--news",
            str(news_csv),
            "--trade-date",
            "2030-01-12",
            "--cutoff",
            "2030-01-12T08:59:59+09:00",
            "--mode",
            "exhaustive",
        ],
    )
    _assert_ok("analyze", analyzed)
    run_id = json.loads(analyzed.output)["run_id"]
    assert read_json(tmp_path / "runs" / "manifests" / f"{run_id}.json")[
        "blind_context_mode"
    ] == "NEWS_ONLY_STRICT"
    inspected_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect", inspected_context)
    context_payload = json.loads(inspected_context.output)
    assert context_payload["run_id"] == run_id
    inspection = context_payload["inspection"]
    assert inspection["reproducibility_checks_passed"] is True
    inspected_context_strict = RUNNER.invoke(
        app,
        ["context", "inspect", run_id, "--strict"],
    )
    _assert_ok("context inspect --strict", inspected_context_strict)
    strict_context_payload = json.loads(inspected_context_strict.output)
    assert strict_context_payload["run_id"] == run_id
    assert strict_context_payload["inspection"]["reproducibility_checks_passed"] is True
    news_input = inspection["news_input"]
    assert news_input["hash_verified"] is True
    assert news_input["expected_sha256"] == file_sha256(news_csv)
    assert news_input["observed_row_count"] == 1
    assert news_input["row_count_verified"] is True
    assert news_input["row_count_partition_verified"] is True
    assert news_input["observed_included_row_count"] == 1
    assert news_input["observed_excluded_row_count"] == 0
    assert news_input["included_row_count_verified"] is True
    assert news_input["excluded_row_count_verified"] is True
    assert news_input["observed_missing_collected_at"] == 1
    assert news_input["missing_collected_at_verified"] is True
    assert news_input["default_news_window_start_at"] == "2030-01-11T15:30:00+09:00"
    assert news_input["news_window_start_verified"] is True
    assert news_input["news_window_end_verified"] is True
    assert news_input["news_window_counts_verified"] is True
    brain_context = inspection["context_files"]["brain"]
    assert brain_context["hashes_verified"] is True
    assert brain_context["file_count"] >= 12
    shard_context = inspection["context_files"]["shard_brain"]
    assert shard_context["hashes_verified"] is True
    assert shard_context["file_count"] >= 1
    supporting = inspection["supporting_artifacts"]
    assert supporting["row_disposition"]["hash_verified"] is True
    assert supporting["row_disposition"]["schema_version_verified"] is True
    assert supporting["row_disposition"]["run_id_verified"] is True
    assert supporting["row_disposition"]["row_count_verified"] is True
    assert supporting["row_disposition"]["summary_verified"] is True
    assert supporting["row_disposition"]["coverage_ratio_verified"] is True
    assert supporting["row_disposition"]["duplicate_row_numbers_absent"] is True
    assert supporting["row_disposition"]["raw_content_absent_verified"] is True
    assert supporting["row_disposition"]["news_window_contract_verified"] is True
    assert supporting["event_cluster"]["hash_verified"] is True
    assert supporting["event_cluster"]["schema_version_verified"] is True
    assert supporting["event_cluster"]["run_id_verified"] is True
    assert supporting["event_cluster"]["row_count_verified"] is True
    assert supporting["event_cluster"]["summary_cluster_count_verified"] is True
    assert supporting["event_cluster"]["summary_source_row_count_verified"] is True
    assert (
        supporting["event_cluster"]["summary_exact_duplicate_count_verified"]
        is True
    )
    assert (
        supporting["event_cluster"][
            "summary_exact_duplicate_cluster_count_verified"
        ]
        is True
    )
    assert (
        supporting["event_cluster"][
            "summary_semantic_duplicate_cluster_count_verified"
        ]
        is True
    )
    assert supporting["event_cluster"]["summary_cluster_method_verified"] is True
    assert (
        supporting["event_cluster"]["summary_novelty_review_required_verified"]
        is True
    )
    assert supporting["event_cluster"]["row_membership_counts_verified"] is True
    assert supporting["open_world_first_analysis"]["hash_verified"] is True
    assert supporting["open_world_first_analysis"]["schema_version_verified"] is True
    assert supporting["open_world_first_analysis"]["run_id_verified"] is True
    assert supporting["open_world_first_analysis"]["prompt_hash_verified"] is True
    assert supporting["open_world_first_analysis"]["required_fields_present"] is True
    assert supporting["open_world_first_analysis"]["summary_verified"] is True
    assert supporting["news_novelty_review"]["hash_verified"] is True
    assert supporting["news_novelty_review"]["schema_version_verified"] is True
    assert supporting["news_novelty_review"]["run_id_verified"] is True
    assert supporting["news_novelty_review"]["prompt_hash_verified"] is True
    assert supporting["news_novelty_review"]["manifest_count_verified"] is True
    assert (
        supporting["news_novelty_review"]["payload_cluster_count_verified"]
        is True
    )
    assert (
        supporting["news_novelty_review"][
            "payload_reviewed_cluster_count_verified"
        ]
        is True
    )
    assert (
        supporting["news_novelty_review"]["summary_cluster_count_verified"]
        is True
    )
    assert (
        supporting["news_novelty_review"][
            "summary_reviewed_cluster_count_verified"
        ]
        is True
    )
    assert supporting["news_novelty_review"]["summary_review_mode_verified"] is True
    assert (
        supporting["news_novelty_review"]["summary_novelty_counts_verified"]
        is True
    )
    assert (
        supporting["news_novelty_review"][
            "summary_time_verified_count_verified"
        ]
        is True
    )
    assert (
        supporting["news_novelty_review"][
            "summary_excluded_after_cutoff_source_count_verified"
        ]
        is True
    )
    assert supporting["semantic_retrieval_plan"]["hash_verified"] is True
    assert supporting["semantic_retrieval_plan"]["schema_version_verified"] is True
    assert supporting["semantic_retrieval_plan"]["run_id_verified"] is True
    assert supporting["semantic_retrieval_plan"]["prompt_hash_verified"] is True
    assert supporting["semantic_retrieval_plan"]["required_categories_verified"] is True
    assert supporting["semantic_retrieval_plan"]["query_count_verified"] is True
    assert supporting["semantic_retrieval_plan"]["category_coverage_verified"] is True
    assert supporting["semantic_retrieval"]["hash_verified"] is True
    assert supporting["semantic_retrieval"]["schema_version_verified"] is True
    assert supporting["semantic_retrieval"]["run_id_verified"] is True
    assert supporting["semantic_retrieval"]["query_count_verified"] is True
    assert supporting["semantic_retrieval"]["category_counts_verified"] is True
    assert supporting["semantic_retrieval"]["included_episode_ids_verified"] is True
    assert supporting["semantic_retrieval"]["excluded_episode_ids_verified"] is True
    assert supporting["semantic_retrieval"]["included_record_ids_verified"] is True
    assert supporting["semantic_retrieval"]["excluded_record_ids_verified"] is True
    assert supporting["semantic_retrieval"]["summary_verified"] is True
    assert supporting["semantic_retrieval"]["retrieval_zero_is_valid"] is True
    assert supporting["semantic_retrieval"]["record_retrieval_zero_is_valid"] is True
    assert supporting["candidate_expansion"]["hash_verified"] is True
    assert supporting["candidate_expansion"]["schema_version_verified"] is True
    assert supporting["candidate_expansion"]["run_id_verified"] is True
    assert supporting["candidate_expansion"]["prompt_hash_verified"] is True
    assert supporting["candidate_expansion"]["required_paths_verified"] is True
    assert supporting["candidate_expansion"]["finding_count_verified"] is True
    assert supporting["candidate_expansion"]["path_coverage_verified"] is True
    assert supporting["candidate_expansion"]["path_counts_verified"] is True
    assert supporting["candidate_expansion"]["manifest_count_verified"] is True
    assert supporting["candidate_expansion"]["continuation_d_minus_one_verified"] is True
    for label in (
        "web_source",
        "excluded_web_source",
        "candidate_web_check",
        "excluded_candidate_web_check",
    ):
        assert supporting[label]["configured"] is False
        assert supporting[label]["passed"] is True
        assert supporting[label]["errors"] == []
    candidate_verification = supporting["candidate_verification"]
    assert candidate_verification["configured"] is True
    assert candidate_verification["passed"] is True
    assert candidate_verification["hash_verified"] is True
    assert candidate_verification["finding_count_verified"] is True
    assert candidate_verification["source_counts_verified"] is True
    assert candidate_verification["errors"] == []
    assert supporting["final_synthesis_context"]["hash_verified"] is True
    assert supporting["final_synthesis_context"]["schema_version_verified"] is True
    assert supporting["final_synthesis_context"]["run_id_verified"] is True
    assert supporting["final_synthesis_context"]["payload_hash_verified"] is True
    assert supporting["final_synthesis_context"]["required_inputs_verified"] is True
    assert supporting["final_synthesis_context"]["required_input_set_verified"] is True
    assert supporting["final_synthesis_context"]["payload_keys_verified"] is True
    assert supporting["final_synthesis_context"]["input_summary_verified"] is True
    assert supporting["final_synthesis_context"]["manifest_summary_verified"] is True
    assert supporting["final_synthesis_context"]["manifest_counts_verified"] is True
    assert supporting["final_synthesis_context"]["event_clusters_verified"] is True
    assert (
        supporting["final_synthesis_context"][
            "semantic_retrieval_plan_artifact_verified"
        ]
        is True
    )
    assert (
        supporting["final_synthesis_context"]["semantic_retrieval_artifact_verified"]
        is True
    )
    assert (
        supporting["final_synthesis_context"]["semantic_retrieval_summary_verified"]
        is True
    )
    assert (
        supporting["final_synthesis_context"]["semantic_retrieval_rows_verified"]
        is True
    )
    assert (
        supporting["final_synthesis_context"][
            "semantic_retrieval_included_ids_verified"
        ]
        is True
    )
    assert (
        supporting["final_synthesis_context"][
            "semantic_retrieval_excluded_ids_verified"
        ]
        is True
    )
    assert (
        supporting["final_synthesis_context"][
            "semantic_retrieval_included_record_ids_verified"
        ]
        is True
    )
    assert (
        supporting["final_synthesis_context"][
            "semantic_retrieval_excluded_record_ids_verified"
        ]
        is True
    )
    assert (
        supporting["final_synthesis_context"]["semantic_retrieval_records_verified"]
        is True
    )
    assert (
        supporting["final_synthesis_context"]["semantic_retrieval_context_verified"]
        is True
    )
    assert supporting["final_synthesis_context"]["web_research_queries_verified"] is True
    assert supporting["final_synthesis_context"]["web_research_source_ids_verified"] is True
    assert supporting["final_synthesis_context"]["web_research_sources_verified"] is True
    assert (
        supporting["final_synthesis_context"]["web_research_excluded_ids_verified"]
        is True
    )
    assert supporting["final_synthesis_context"]["web_research_verified"] is True
    assert (
        supporting["final_synthesis_context"][
            "candidate_verification_context_verified"
        ]
        is True
    )
    assert (
        supporting["final_synthesis_context"][
            "candidate_web_checks_context_verified"
        ]
        is True
    )
    assert (
        supporting["final_synthesis_context"][
            "news_novelty_review_context_verified"
        ]
        is True
    )
    assert (
        supporting["final_synthesis_context"]["candidate_expansion_context_verified"]
        is True
    )
    assert (
        supporting["final_synthesis_context"]["red_team_output_context_verified"]
        is True
    )
    assert supporting["source_ledger"]["hash_verified"] is True
    assert supporting["source_ledger"]["schema_version_verified"] is True
    assert supporting["source_ledger"]["entry_count_verified"] is True
    assert supporting["source_ledger"]["required_fields_verified"] is True
    assert supporting["source_ledger"]["source_ids_verified"] is True
    assert supporting["source_ledger"]["duplicate_source_ids_absent"] is True
    assert supporting["source_ledger"]["summary_verified"] is True
    assert supporting["source_ledger"]["web_sources_covered_verified"] is True
    assert (
        supporting["source_ledger"]["candidate_web_sources_covered_verified"]
        is True
    )
    assert supporting["source_ledger"]["excluded_sources_absent_verified"] is True
    assert supporting["source_ledger"]["source_url_verified"] is True
    assert supporting["source_ledger"]["raw_content_absent_verified"] is True
    assert supporting["source_ledger"]["usage_phase_verified"] is True
    assert supporting["source_ledger"]["blind_cutoff_verified"] is True
    assert supporting["source_ledger"]["timestamp_precision_verified"] is True
    assert supporting["blind_seal_receipt"]["hash_verified"] is True
    assert supporting["blind_seal_receipt"]["schema_version_verified"] is True
    assert supporting["blind_seal_receipt"]["run_id_verified"] is True
    assert supporting["blind_seal_receipt"]["phase_verified"] is True
    assert supporting["blind_seal_receipt"]["blind_artifact_hash_verified"] is True
    assert supporting["blind_seal_receipt"]["prediction_path_verified"] is True
    assert supporting["blind_seal_receipt"]["row_disposition_hash_verified"] is True
    assert supporting["blind_seal_receipt"]["source_ledger_hash_verified"] is True
    assert supporting["blind_seal_receipt"]["no_d_outcome_verified"] is True
    assert supporting["blind_seal_receipt"]["validation_counts_verified"] is True
    assert supporting["phase_state"]["hash_verified"] is True
    assert supporting["phase_state"]["schema_version_verified"] is True
    assert supporting["phase_state"]["run_id_verified"] is True
    assert supporting["phase_state"]["phase_verified"] is True
    assert supporting["phase_state"]["completed_phase_verified"] is True
    assert supporting["phase_state"]["receipt_link_verified"] is True
    assert supporting["phase_state"]["trade_date_verified"] is True
    assert supporting["phase_state"]["cutoff_at_verified"] is True
    assert supporting["red_team"]["metadata_verified"] is True
    assert supporting["red_team"]["candidate_count_verified"] is True
    assert supporting["red_team"]["finding_count_verified"] is True
    assert supporting["red_team"]["required_attack_checks_verified"] is True
    assert supporting["red_team"]["attack_check_coverage_verified"] is True
    assert supporting["red_team"]["passed_to_synthesis_verified"] is True
    assert supporting["red_team"]["summary_verified"] is True
    memory_coverage = inspection["memory_coverage"]
    assert memory_coverage["passed"] is True
    assert memory_coverage["manifest_hash_verified"] is True
    assert memory_coverage["contract_verified"] is True
    assert memory_coverage["references_verified"] is True
    assert memory_coverage["record_sets_verified"] is True
    assert memory_coverage["cutoff_verified"] is True
    llm_traces = inspection["llm_traces"]
    assert llm_traces["passed"] is True
    assert llm_traces["matched_prompt_count"] == 7
    for purpose in (
        "open_world_first_analysis",
        "news_novelty_review",
        "semantic_retrieval_plan",
        "candidate_expansion",
        "daily_blind_analysis",
        "red_team_candidate_review",
        "final_synthesis",
    ):
        purpose_trace = llm_traces["purposes"][purpose]
        assert purpose_trace["matching_trace_count"] >= 1
        assert purpose_trace["trace_payloads_valid"] is True
        assert purpose_trace["model_config_verified"] is True
        assert purpose_trace["token_counts_verified"] is True
    assert inspection["output_artifacts"]["prediction"]["hash_verified"] is True
    assert (
        inspection["output_artifacts"]["prediction"]["context_manifest_id_verified"]
        is True
    )
    assert (
        inspection["output_artifacts"]["prediction"]["schema_version_verified"] is True
    )
    assert inspection["output_artifacts"]["prediction"]["sealed_at_verified"] is True
    assert (
        inspection["output_artifacts"]["prediction"]["blind_artifact_hash_verified"]
        is True
    )
    assert (
        inspection["output_artifacts"]["prediction"]["manifest_blind_hash_verified"]
        is True
    )
    assert inspection["output_artifacts"]["report"]["hash_verified"] is True
    assert inspection["output_artifacts"]["report"]["contains_run_id"] is True
    assert inspection["output_artifacts"]["report"]["required_sections"]["passed"] is True
    analysis_bundle = RUNNER.invoke(
        app,
        ["context", "export-analysis-bundle", "--run-id", run_id],
    )
    _assert_ok("context export-analysis-bundle", analysis_bundle)
    analysis_bundle_payload = json.loads(analysis_bundle.output)
    assert analysis_bundle_payload["run_id"] == run_id
    analysis_bundle_file = tmp_path / analysis_bundle_payload["bundle"]
    assert analysis_bundle_file.exists()
    analysis_bundle_text = analysis_bundle_file.read_text(encoding="utf-8")
    assert "schema_version: nslab.research_bundle.v1" in analysis_bundle_text
    assert run_id in analysis_bundle_text
    manifest_file = tmp_path / "runs" / "manifests" / f"{run_id}.json"
    prediction_file = tmp_path / context_payload["prediction_artifact"]
    original_prediction = read_json(prediction_file)
    original_manifest_for_prediction = read_json(manifest_file)
    tampered_prediction = {
        **original_prediction,
        "blind_artifact_sha256": "0" * 64,
    }
    write_json(prediction_file, tampered_prediction)
    write_json(
        manifest_file,
        {
            **original_manifest_for_prediction,
            "prediction_sha256": file_sha256(prediction_file),
        },
    )
    tampered_prediction_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered prediction", tampered_prediction_context)
    tampered_prediction_context_strict = RUNNER.invoke(
        app,
        ["context", "inspect", run_id, "--strict"],
    )
    assert tampered_prediction_context_strict.exit_code == 1, (
        tampered_prediction_context_strict.output
    )
    assert (
        json.loads(tampered_prediction_context_strict.output)["inspection"][
            "reproducibility_checks_passed"
        ]
        is False
    )
    tampered_prediction_inspection = json.loads(
        tampered_prediction_context.output
    )["inspection"]
    assert tampered_prediction_inspection["reproducibility_checks_passed"] is False
    tampered_prediction_status = tampered_prediction_inspection["output_artifacts"][
        "prediction"
    ]
    assert tampered_prediction_status["hash_verified"] is True
    assert tampered_prediction_status["blind_artifact_hash_verified"] is False
    assert tampered_prediction_status["manifest_blind_hash_verified"] is False
    assert "prediction_artifact_blind_hash_mismatch" in (
        tampered_prediction_status["errors"]
    )
    strict_tampered_prediction_context = RUNNER.invoke(
        app, ["context", "inspect", run_id, "--strict"]
    )
    assert strict_tampered_prediction_context.exit_code == 1
    strict_tampered_prediction_inspection = json.loads(
        strict_tampered_prediction_context.output
    )["inspection"]
    assert strict_tampered_prediction_inspection["reproducibility_checks_passed"] is False
    write_json(prediction_file, original_prediction)
    write_json(manifest_file, original_manifest_for_prediction)

    original_manifest_for_row_disposition_summary = read_json(manifest_file)
    tampered_row_disposition_summary = {
        **original_manifest_for_row_disposition_summary["row_disposition_summary"],
        "included_in_news_window": (
            original_manifest_for_row_disposition_summary["row_disposition_summary"][
                "included_in_news_window"
            ]
            + 1
        ),
    }
    write_json(
        manifest_file,
        {
            **original_manifest_for_row_disposition_summary,
            "row_disposition_summary": tampered_row_disposition_summary,
        },
    )
    tampered_row_disposition_summary_context = RUNNER.invoke(
        app, ["context", "inspect", run_id]
    )
    _assert_ok(
        "context inspect tampered row disposition summary",
        tampered_row_disposition_summary_context,
    )
    tampered_row_disposition_summary_inspection = json.loads(
        tampered_row_disposition_summary_context.output
    )["inspection"]
    assert (
        tampered_row_disposition_summary_inspection["reproducibility_checks_passed"]
        is False
    )
    tampered_row_disposition_status = tampered_row_disposition_summary_inspection[
        "supporting_artifacts"
    ]["row_disposition"]
    assert tampered_row_disposition_status["hash_verified"] is True
    assert tampered_row_disposition_status["summary_verified"] is False
    assert "row_disposition_summary_mismatch" in (
        tampered_row_disposition_status["errors"]
    )
    write_json(manifest_file, original_manifest_for_row_disposition_summary)

    original_manifest_for_event_cluster_summary = read_json(manifest_file)
    tampered_event_cluster_summary = {
        **original_manifest_for_event_cluster_summary["event_cluster_summary"],
        "cluster_count": (
            original_manifest_for_event_cluster_summary["event_cluster_summary"][
                "cluster_count"
            ]
            + 1
        ),
    }
    write_json(
        manifest_file,
        {
            **original_manifest_for_event_cluster_summary,
            "event_cluster_summary": tampered_event_cluster_summary,
        },
    )
    tampered_event_cluster_summary_context = RUNNER.invoke(
        app, ["context", "inspect", run_id]
    )
    _assert_ok(
        "context inspect tampered event cluster summary",
        tampered_event_cluster_summary_context,
    )
    tampered_event_cluster_summary_inspection = json.loads(
        tampered_event_cluster_summary_context.output
    )["inspection"]
    assert (
        tampered_event_cluster_summary_inspection["reproducibility_checks_passed"]
        is False
    )
    tampered_event_cluster_status = tampered_event_cluster_summary_inspection[
        "supporting_artifacts"
    ]["event_cluster"]
    assert tampered_event_cluster_status["hash_verified"] is True
    assert tampered_event_cluster_status["summary_cluster_count_verified"] is False
    assert "event_cluster_summary_cluster_count_mismatch" in (
        tampered_event_cluster_status["errors"]
    )
    write_json(manifest_file, original_manifest_for_event_cluster_summary)

    original_manifest_for_news_novelty_summary = read_json(manifest_file)
    tampered_news_novelty_summary = {
        **original_manifest_for_news_novelty_summary[
            "news_novelty_review_summary"
        ],
        "time_verified_count": (
            original_manifest_for_news_novelty_summary[
                "news_novelty_review_summary"
            ]["time_verified_count"]
            + 1
        ),
    }
    write_json(
        manifest_file,
        {
            **original_manifest_for_news_novelty_summary,
            "news_novelty_review_summary": tampered_news_novelty_summary,
        },
    )
    tampered_news_novelty_summary_context = RUNNER.invoke(
        app, ["context", "inspect", run_id]
    )
    _assert_ok(
        "context inspect tampered news novelty summary",
        tampered_news_novelty_summary_context,
    )
    tampered_news_novelty_summary_inspection = json.loads(
        tampered_news_novelty_summary_context.output
    )["inspection"]
    assert (
        tampered_news_novelty_summary_inspection["reproducibility_checks_passed"]
        is False
    )
    tampered_news_novelty_status = tampered_news_novelty_summary_inspection[
        "supporting_artifacts"
    ]["news_novelty_review"]
    assert tampered_news_novelty_status["hash_verified"] is True
    assert (
        tampered_news_novelty_status["summary_time_verified_count_verified"]
        is False
    )
    assert "news_novelty_review_summary_time_verified_count_mismatch" in (
        tampered_news_novelty_status["errors"]
    )
    write_json(manifest_file, original_manifest_for_news_novelty_summary)

    # Web-artifact tamper cases live in policy-specific regression tests.
    red_team_file = tmp_path / context_payload["red_team_artifacts"][0]
    original_red_team = read_json(red_team_file)
    tampered_red_team = {
        **original_red_team,
        "candidate_findings": [
            (
                {
                    **finding,
                    "attack_checks": [
                        (
                            {**check, "passed_to_synthesis": False}
                            if check_index == 0
                            else check
                        )
                        for check_index, check in enumerate(finding["attack_checks"])
                    ],
                }
                if finding_index == 0
                else finding
            )
            for finding_index, finding in enumerate(
                original_red_team["candidate_findings"]
            )
        ],
    }
    write_json(red_team_file, tampered_red_team)
    tampered_red_team_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered red team", tampered_red_team_context)
    tampered_red_team_inspection = json.loads(tampered_red_team_context.output)[
        "inspection"
    ]
    assert tampered_red_team_inspection["reproducibility_checks_passed"] is False
    tampered_red_team_status = tampered_red_team_inspection["supporting_artifacts"][
        "red_team"
    ]
    assert tampered_red_team_status["metadata_verified"] is True
    assert tampered_red_team_status["passed_to_synthesis_verified"] is False
    assert "red_team_artifact_not_passed_to_synthesis" in (
        tampered_red_team_status["errors"]
    )
    write_json(red_team_file, original_red_team)
    semantic_retrieval_file = tmp_path / context_payload["semantic_retrieval_artifact"]
    original_semantic_retrieval = semantic_retrieval_file.read_text(encoding="utf-8")
    original_manifest_for_semantic = read_json(manifest_file)
    tampered_semantic_rows = [
        json.loads(line)
        for line in original_semantic_retrieval.splitlines()
        if line.strip()
    ]
    tampered_semantic_rows[0]["schema_version"] = "tampered.semantic_retrieval"
    semantic_retrieval_file.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in tampered_semantic_rows
        ),
        encoding="utf-8",
    )
    write_json(
        manifest_file,
        {
            **original_manifest_for_semantic,
            "semantic_retrieval_sha256": sha256_text(
                semantic_retrieval_file.read_text(encoding="utf-8")
            ),
        },
    )
    tampered_semantic_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered semantic retrieval", tampered_semantic_context)
    tampered_semantic_inspection = json.loads(tampered_semantic_context.output)["inspection"]
    assert tampered_semantic_inspection["reproducibility_checks_passed"] is False
    tampered_semantic_status = tampered_semantic_inspection["supporting_artifacts"][
        "semantic_retrieval"
    ]
    assert tampered_semantic_status["hash_verified"] is True
    assert tampered_semantic_status["schema_version_verified"] is False
    assert "semantic_retrieval_schema_version_mismatch" in tampered_semantic_status["errors"]
    semantic_retrieval_file.write_text(original_semantic_retrieval, encoding="utf-8")
    write_json(manifest_file, original_manifest_for_semantic)
    manifest_reproducibility = inspection["manifest_reproducibility"]
    assert manifest_reproducibility["configured"] is True
    assert manifest_reproducibility["model_config_valid"] is True
    assert manifest_reproducibility["token_counts_valid"] is True
    assert manifest_reproducibility["truncations_valid"] is True
    assert manifest_reproducibility["web_queries_valid"] is True
    assert manifest_reproducibility["web_sources_valid"] is True
    assert manifest_reproducibility["episode_scope_valid"] is True
    assert manifest_reproducibility["price_snapshot_valid"] is True
    assert manifest_reproducibility["price_snapshot"]["source_name_valid"] is True
    assert manifest_reproducibility["price_snapshot"]["source_ref_valid"] is True
    assert (
        manifest_reproducibility["price_snapshot"][
            "source_ref_mock_or_placeholder"
        ]
        is True
    )
    assert (
        manifest_reproducibility["price_snapshot"][
            "allowed_through_before_trade_date"
        ]
        is True
    )
    assert (
        manifest_reproducibility["price_snapshot"]["as_of_not_after_cutoff"]
        is True
    )
    assert manifest_reproducibility["episode_scope"][
        "accepted_episode_count_verified"
    ] is True
    assert manifest_reproducibility["episode_scope"][
        "total_accepted_episode_count_verified"
    ] is True
    assert manifest_reproducibility["episode_scope"][
        "available_episode_count_verified"
    ] is True
    assert manifest_reproducibility["episode_scope"][
        "unavailable_episode_ids_verified"
    ] is True
    original_manifest_for_reproducibility = read_json(manifest_file)
    tampered_manifest_reproducibility = {
        **original_manifest_for_reproducibility,
        "token_counts": {"current_news": -1},
        "web_queries": "tampered",
        "available_episode_count": (
            original_manifest_for_reproducibility["available_episode_count"] + 1
        ),
    }
    write_json(manifest_file, tampered_manifest_reproducibility)
    tampered_manifest_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered manifest reproducibility", tampered_manifest_context)
    tampered_manifest_inspection = json.loads(
        tampered_manifest_context.output
    )["inspection"]
    assert tampered_manifest_inspection["reproducibility_checks_passed"] is False
    tampered_manifest_status = tampered_manifest_inspection["manifest_reproducibility"]
    assert tampered_manifest_status["token_counts_valid"] is False
    assert tampered_manifest_status["web_queries_valid"] is False
    assert tampered_manifest_status["episode_scope_valid"] is False
    assert "token_counts_missing_or_invalid" in tampered_manifest_status["errors"]
    assert "web_queries_missing_or_invalid" in tampered_manifest_status["errors"]
    assert "available_episode_count_mismatch" in tampered_manifest_status[
        "episode_scope"
    ]["errors"]
    write_json(manifest_file, original_manifest_for_reproducibility)
    original_manifest_for_price_snapshot = read_json(manifest_file)
    write_json(
        manifest_file,
        {
            **original_manifest_for_price_snapshot,
            "price_snapshot": {
                **original_manifest_for_price_snapshot["price_snapshot"],
                "source_ref": "",
                "allowed_through": original_manifest_for_price_snapshot["trade_date"],
                "as_of": "2030-01-12T09:00:00+09:00",
            },
        },
    )
    tampered_price_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered price snapshot", tampered_price_context)
    tampered_price_inspection = json.loads(tampered_price_context.output)["inspection"]
    assert tampered_price_inspection["reproducibility_checks_passed"] is False
    tampered_price_status = tampered_price_inspection["manifest_reproducibility"]
    assert tampered_price_status["price_snapshot_valid"] is False
    assert "price_snapshot_invalid" in tampered_price_status["errors"]
    tampered_price_snapshot = tampered_price_status["price_snapshot"]
    assert tampered_price_snapshot["allowed_through_valid"] is True
    assert tampered_price_snapshot["source_ref_valid"] is False
    assert tampered_price_snapshot["allowed_through_before_trade_date"] is False
    assert tampered_price_snapshot["as_of_not_after_cutoff"] is False
    assert "price_snapshot_allowed_through_not_before_trade_date" in (
        tampered_price_snapshot["errors"]
    )
    assert "price_snapshot_source_ref_missing_or_invalid" in (
        tampered_price_snapshot["errors"]
    )
    assert "price_snapshot_as_of_after_cutoff_at" in tampered_price_snapshot["errors"]
    strict_tampered_price_context = RUNNER.invoke(
        app, ["context", "inspect", run_id, "--strict"]
    )
    assert strict_tampered_price_context.exit_code == 1
    write_json(manifest_file, original_manifest_for_price_snapshot)
    brain_context_file = tmp_path / context_payload["brain_files"][0]
    original_brain_context = brain_context_file.read_text(encoding="utf-8")
    brain_context_file.write_text(
        original_brain_context + "\nTampered context checkpoint.\n",
        encoding="utf-8",
    )
    tampered_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered brain file", tampered_context)
    tampered_inspection = json.loads(tampered_context.output)["inspection"]
    assert tampered_inspection["reproducibility_checks_passed"] is False
    assert tampered_inspection["context_files"]["brain"]["hash_mismatches"] == [
        context_payload["brain_files"][0]
    ]
    brain_context_file.write_text(original_brain_context, encoding="utf-8")
    source_ledger_file = tmp_path / context_payload["source_ledger_artifact"]
    original_source_ledger = source_ledger_file.read_text(encoding="utf-8")
    source_ledger_file.write_text(
        original_source_ledger + "\nTampered source ledger.\n",
        encoding="utf-8",
    )
    tampered_ledger_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered source ledger", tampered_ledger_context)
    tampered_ledger_inspection = json.loads(tampered_ledger_context.output)["inspection"]
    assert tampered_ledger_inspection["reproducibility_checks_passed"] is False
    assert (
        tampered_ledger_inspection["supporting_artifacts"]["source_ledger"][
            "hash_verified"
        ]
        is False
    )
    source_ledger_file.write_text(original_source_ledger, encoding="utf-8")

    original_manifest_for_source_ledger_contract = read_json(manifest_file)
    source_ledger_rows = [
        json.loads(line) for line in original_source_ledger.splitlines() if line.strip()
    ]
    source_ledger_rows[0] = {
        **source_ledger_rows[0],
        "timestamp_precision": "date_only_end_of_day",
        "published_at": "2030-01-12T08:00:00+09:00",
    }
    tampered_source_ledger_payload = "".join(
        canonical_json(row) + "\n" for row in source_ledger_rows
    )
    source_ledger_file.write_text(tampered_source_ledger_payload, encoding="utf-8")
    write_json(
        manifest_file,
        {
            **original_manifest_for_source_ledger_contract,
            "source_ledger_sha256": sha256_text(tampered_source_ledger_payload),
        },
    )
    tampered_ledger_contract_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok(
        "context inspect tampered source ledger contract",
        tampered_ledger_contract_context,
    )
    tampered_ledger_contract_inspection = json.loads(
        tampered_ledger_contract_context.output
    )["inspection"]
    assert tampered_ledger_contract_inspection["reproducibility_checks_passed"] is False
    tampered_ledger_contract_status = tampered_ledger_contract_inspection[
        "supporting_artifacts"
    ]["source_ledger"]
    assert tampered_ledger_contract_status["hash_verified"] is True
    assert tampered_ledger_contract_status["timestamp_precision_verified"] is False
    assert "source_ledger_timestamp_precision_invalid" in (
        tampered_ledger_contract_status["errors"]
    )
    source_ledger_file.write_text(original_source_ledger, encoding="utf-8")
    write_json(manifest_file, original_manifest_for_source_ledger_contract)

    blind_receipt_file = tmp_path / context_payload["blind_seal_receipt_artifact"]
    phase_state_file = tmp_path / context_payload["phase_state_artifact"]
    original_blind_receipt = read_json(blind_receipt_file)
    original_phase_state = read_json(phase_state_file)
    original_manifest_for_seal = read_json(manifest_file)
    tampered_blind_receipt = {
        **original_blind_receipt,
        "no_d_outcome_exposed": False,
    }
    write_json(blind_receipt_file, tampered_blind_receipt)
    tampered_receipt_sha = sha256_text(
        blind_receipt_file.read_text(encoding="utf-8")
    )
    tampered_phase_state = {
        **original_phase_state,
        "blind_seal_receipt_sha256": tampered_receipt_sha,
    }
    write_json(phase_state_file, tampered_phase_state)
    write_json(
        manifest_file,
        {
            **original_manifest_for_seal,
            "blind_seal_receipt_sha256": tampered_receipt_sha,
            "phase_state_sha256": sha256_text(
                phase_state_file.read_text(encoding="utf-8")
            ),
        },
    )
    tampered_seal_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered blind seal", tampered_seal_context)
    tampered_seal_inspection = json.loads(tampered_seal_context.output)["inspection"]
    assert tampered_seal_inspection["reproducibility_checks_passed"] is False
    tampered_receipt_status = tampered_seal_inspection["supporting_artifacts"][
        "blind_seal_receipt"
    ]
    assert tampered_receipt_status["hash_verified"] is True
    assert tampered_receipt_status["no_d_outcome_verified"] is False
    assert "blind_seal_receipt_no_d_outcome_mismatch" in (
        tampered_receipt_status["errors"]
    )
    assert (
        tampered_seal_inspection["supporting_artifacts"]["phase_state"][
            "receipt_link_verified"
        ]
        is True
    )
    write_json(blind_receipt_file, original_blind_receipt)
    write_json(phase_state_file, original_phase_state)
    write_json(manifest_file, original_manifest_for_seal)
    original_manifest_for_phase = read_json(manifest_file)
    tampered_phase_state = {**original_phase_state, "phase": "OPEN"}
    write_json(phase_state_file, tampered_phase_state)
    write_json(
        manifest_file,
        {
            **original_manifest_for_phase,
            "phase_state_sha256": sha256_text(
                phase_state_file.read_text(encoding="utf-8")
            ),
        },
    )
    tampered_phase_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered phase state", tampered_phase_context)
    tampered_phase_inspection = json.loads(tampered_phase_context.output)["inspection"]
    assert tampered_phase_inspection["reproducibility_checks_passed"] is False
    tampered_phase_status = tampered_phase_inspection["supporting_artifacts"][
        "phase_state"
    ]
    assert tampered_phase_status["hash_verified"] is True
    assert tampered_phase_status["phase_verified"] is False
    assert "phase_state_phase_mismatch" in tampered_phase_status["errors"]
    write_json(phase_state_file, original_phase_state)
    write_json(manifest_file, original_manifest_for_phase)
    final_context_file = tmp_path / context_payload["final_synthesis_context_artifact"]
    original_final_context = read_json(final_context_file)
    original_manifest = read_json(manifest_file)
    missing_required_inputs = [
        item
        for item in original_final_context["required_inputs"]
        if item != "d_minus_one_market_data"
    ]
    tampered_final_payload = {
        **original_final_context["payload"],
        "required_inputs": missing_required_inputs,
    }
    tampered_final_summary = final_synthesis_input_summary(tampered_final_payload)
    tampered_final_required_inputs = {
        **original_final_context,
        "required_inputs": missing_required_inputs,
        "payload_sha256": sha256_text(canonical_json(tampered_final_payload)),
        "input_summary": tampered_final_summary,
        "payload": tampered_final_payload,
    }
    write_json(final_context_file, tampered_final_required_inputs)
    tampered_manifest_required_inputs = {
        **original_manifest,
        "final_synthesis_context_sha256": sha256_text(
            final_context_file.read_text(encoding="utf-8")
        ),
        "final_synthesis_context_summary": tampered_final_summary,
    }
    write_json(manifest_file, tampered_manifest_required_inputs)
    tampered_final_inputs_result = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok(
        "context inspect tampered final synthesis required inputs",
        tampered_final_inputs_result,
    )
    tampered_final_inputs_inspection = json.loads(
        tampered_final_inputs_result.output
    )["inspection"]
    assert tampered_final_inputs_inspection["reproducibility_checks_passed"] is False
    tampered_final_inputs_status = tampered_final_inputs_inspection[
        "supporting_artifacts"
    ]["final_synthesis_context"]
    assert tampered_final_inputs_status["hash_verified"] is True
    assert tampered_final_inputs_status["payload_hash_verified"] is True
    assert tampered_final_inputs_status["input_summary_verified"] is True
    assert tampered_final_inputs_status["manifest_summary_verified"] is True
    assert tampered_final_inputs_status["required_input_set_verified"] is False
    assert "final_synthesis_context_required_input_set_mismatch" in (
        tampered_final_inputs_status["errors"]
    )
    write_json(final_context_file, original_final_context)
    write_json(manifest_file, original_manifest)
    original_event_clusters = original_final_context["payload"]["event_clusters"]
    original_semantic_rows = original_final_context["payload"][
        "additional_semantic_retrieval"
    ]["rows"]
    assert original_event_clusters
    assert original_semantic_rows
    tampered_event_clusters = [
        (
            {**cluster, "cluster_id": f"{cluster['cluster_id']}-tampered"}
            if index == 0
            else cluster
        )
        for index, cluster in enumerate(original_event_clusters)
    ]
    tampered_semantic_rows = [
        (
            {**row, "category": f"{row['category']}-tampered"}
            if index == 0
            else row
        )
        for index, row in enumerate(original_semantic_rows)
    ]
    tampered_event_semantic_payload = {
        **original_final_context["payload"],
        "event_clusters": tampered_event_clusters,
        "additional_semantic_retrieval": {
            **original_final_context["payload"]["additional_semantic_retrieval"],
            "rows": tampered_semantic_rows,
        },
    }
    tampered_event_semantic_summary = final_synthesis_input_summary(
        tampered_event_semantic_payload
    )
    tampered_event_semantic_context = {
        **original_final_context,
        "payload_sha256": sha256_text(canonical_json(tampered_event_semantic_payload)),
        "input_summary": tampered_event_semantic_summary,
        "payload": tampered_event_semantic_payload,
    }
    write_json(final_context_file, tampered_event_semantic_context)
    write_json(
        manifest_file,
        {
            **original_manifest,
            "final_synthesis_context_sha256": sha256_text(
                final_context_file.read_text(encoding="utf-8")
            ),
            "final_synthesis_context_summary": tampered_event_semantic_summary,
        },
    )
    tampered_event_semantic_result = RUNNER.invoke(
        app, ["context", "inspect", run_id]
    )
    _assert_ok(
        "context inspect tampered final synthesis event semantic context",
        tampered_event_semantic_result,
    )
    tampered_event_semantic_inspection = json.loads(
        tampered_event_semantic_result.output
    )["inspection"]
    assert (
        tampered_event_semantic_inspection["reproducibility_checks_passed"]
        is False
    )
    tampered_event_semantic_status = tampered_event_semantic_inspection[
        "supporting_artifacts"
    ]["final_synthesis_context"]
    assert tampered_event_semantic_status["hash_verified"] is True
    assert tampered_event_semantic_status["payload_hash_verified"] is True
    assert tampered_event_semantic_status["input_summary_verified"] is True
    assert tampered_event_semantic_status["manifest_summary_verified"] is True
    assert tampered_event_semantic_status["manifest_counts_verified"] is True
    assert tampered_event_semantic_status["event_clusters_verified"] is False
    assert (
        tampered_event_semantic_status["semantic_retrieval_rows_verified"]
        is False
    )
    assert (
        tampered_event_semantic_status["semantic_retrieval_context_verified"]
        is False
    )
    assert "final_synthesis_context_event_clusters_mismatch" in (
        tampered_event_semantic_status["errors"]
    )
    assert "final_synthesis_context_semantic_retrieval_rows_mismatch" in (
        tampered_event_semantic_status["errors"]
    )
    write_json(final_context_file, original_final_context)
    write_json(manifest_file, original_manifest)
    # Embedded web-context tamper cases are not part of CSV-only BLIND.
    tampered_final_context = {
        **original_final_context,
        "input_summary": {"current_news_count": 999},
    }
    write_json(final_context_file, tampered_final_context)
    tampered_manifest = {
        **original_manifest,
        "final_synthesis_context_sha256": sha256_text(
            final_context_file.read_text(encoding="utf-8")
        ),
    }
    write_json(manifest_file, tampered_manifest)
    tampered_final_context_result = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered final synthesis context", tampered_final_context_result)
    tampered_final_context_inspection = json.loads(
        tampered_final_context_result.output
    )["inspection"]
    assert tampered_final_context_inspection["reproducibility_checks_passed"] is False
    tampered_final_context_status = tampered_final_context_inspection[
        "supporting_artifacts"
    ]["final_synthesis_context"]
    assert tampered_final_context_status["hash_verified"] is True
    assert tampered_final_context_status["input_summary_verified"] is False
    assert tampered_final_context_status["manifest_summary_verified"] is False
    assert "final_synthesis_context_input_summary_mismatch" in (
        tampered_final_context_status["errors"]
    )
    write_json(final_context_file, original_final_context)
    write_json(manifest_file, original_manifest)
    memory_coverage_file = (
        tmp_path / context_payload["memory_coverage_manifest_artifact"]
    )
    original_memory_coverage_bytes = memory_coverage_file.read_bytes()
    original_memory_coverage = original_memory_coverage_bytes.decode("utf-8")
    memory_coverage_file.write_text(
        original_memory_coverage.replace(
            "nslab.memory_coverage_manifest.v2",
            "tampered.memory_coverage",
            1,
        ),
        encoding="utf-8",
    )
    tampered_coverage_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered memory coverage", tampered_coverage_context)
    tampered_coverage_inspection = json.loads(tampered_coverage_context.output)[
        "inspection"
    ]
    assert tampered_coverage_inspection["reproducibility_checks_passed"] is False
    assert tampered_coverage_inspection["memory_coverage"][
        "manifest_hash_verified"
    ] is False
    assert tampered_coverage_inspection["memory_coverage"][
        "contract_verified"
    ] is False
    memory_coverage_file.write_bytes(original_memory_coverage_bytes)
    daily_trace_file = (
        tmp_path
        / llm_traces["purposes"]["daily_blind_analysis"]["matching_trace_paths"][0]
    )
    original_daily_trace = read_json(daily_trace_file)
    tampered_daily_trace = {
        **original_daily_trace,
        "model_config": {
            **original_daily_trace["model_config"],
            "configured_provider": "tampered-provider",
        },
    }
    write_json(daily_trace_file, tampered_daily_trace)
    tampered_trace_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered llm trace", tampered_trace_context)
    tampered_trace_inspection = json.loads(tampered_trace_context.output)["inspection"]
    assert tampered_trace_inspection["reproducibility_checks_passed"] is False
    tampered_trace_status = tampered_trace_inspection["llm_traces"]["purposes"][
        "daily_blind_analysis"
    ]
    assert tampered_trace_status["model_config_verified"] is False
    assert tampered_trace_status["model_config_mismatches"][0]["keys"] == [
        "configured_provider"
    ]
    write_json(daily_trace_file, original_daily_trace)

    original_manifest_for_trace_tokens = read_json(manifest_file)
    tampered_trace_token_counts = {
        **original_manifest_for_trace_tokens["token_counts"],
        "blind_analysis_prompt": (
            original_manifest_for_trace_tokens["token_counts"]["blind_analysis_prompt"]
            + 1
        ),
    }
    write_json(
        manifest_file,
        {
            **original_manifest_for_trace_tokens,
            "token_counts": tampered_trace_token_counts,
        },
    )
    tampered_trace_tokens_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok(
        "context inspect tampered llm trace token counts",
        tampered_trace_tokens_context,
    )
    tampered_trace_tokens_inspection = json.loads(
        tampered_trace_tokens_context.output
    )["inspection"]
    assert tampered_trace_tokens_inspection["reproducibility_checks_passed"] is False
    tampered_trace_tokens_status = tampered_trace_tokens_inspection["llm_traces"][
        "purposes"
    ]["daily_blind_analysis"]
    assert tampered_trace_tokens_status["token_counts_verified"] is False
    assert tampered_trace_tokens_status["token_count_mismatches"][0][
        "manifest_token_count_key"
    ] == "blind_analysis_prompt"
    assert "matching_trace_token_count_mismatch" in (
        tampered_trace_tokens_status["errors"]
    )
    write_json(manifest_file, original_manifest_for_trace_tokens)

    session_pack = RUNNER.invoke(
        app,
        [
            "context",
            "export-session-pack",
            "--news",
            str(news_csv),
            "--trade-date",
            "2030-01-12",
            "--cutoff",
            "2030-01-12T08:59:59+09:00",
            "--mode",
            "brain",
        ],
    )
    _assert_ok("context export-session-pack", session_pack)
    session_pack_dir = tmp_path / json.loads(session_pack.output)["session_pack"]
    assert read_json(session_pack_dir / "manifest.json")["blocked"] is False

    _assert_ok("audit hardcoding", RUNNER.invoke(app, ["audit", "hardcoding"]))
    _assert_ok(
        "audit lookahead",
        RUNNER.invoke(app, ["audit", "lookahead", "--trade-date", "2030-01-12"]),
    )
    provenance = RUNNER.invoke(app, ["audit", "provenance"])
    _assert_ok("audit provenance", provenance)
    provenance_payload = json.loads(provenance.output)
    assert provenance_payload["checked_analysis_bundles"] >= 1
    assert provenance_payload["checked_session_pack_manifests"] >= 1
    _assert_ok("audit coverage", RUNNER.invoke(app, ["audit", "coverage"]))

    _assert_ok("training export-sft", RUNNER.invoke(app, ["training", "export-sft"]))
    _assert_ok(
        "training export-preference",
        RUNNER.invoke(app, ["training", "export-preference"]),
    )
    _assert_ok("training export-evals", RUNNER.invoke(app, ["training", "export-evals"]))
    evaluated = RUNNER.invoke(app, ["evaluate", "--trade-date", "2030-01-12"])
    _assert_ok("evaluate", evaluated)
    evaluation_payload = json.loads(evaluated.output)
    assert evaluation_payload["outcome_coverage_status"] == "PREDICTED_CANDIDATES_ONLY"
    assert evaluation_payload["performance_metrics"]["candidate_count"] > 0
    assert evaluation_payload["eligibility_matrix"]["forecast_evaluation_eligible"] is True
    evaluation_episode_id = evaluation_payload["research_episode_id"]
    assert (tmp_path / evaluation_payload["postmortem"]).exists()
    assert (tmp_path / evaluation_payload["research_episode_path"]).exists()
    post_eval_provenance = RUNNER.invoke(app, ["audit", "provenance"])
    _assert_ok("audit provenance after evaluate", post_eval_provenance)
    assert json.loads(post_eval_provenance.output)["checked_evaluation_episode_files"] >= 1

    postmortem_update = RUNNER.invoke(
        app,
        [
            "brain",
            "update",
            "--episode",
            "2030-01-12",
            "--mode",
            "catalog",
            "--allow-catalog",
        ],
    )
    _assert_ok("brain update postmortem", postmortem_update)
    assert evaluation_episode_id in json.loads(postmortem_update.output)["covered_episode_ids"]

    warehouse = RUNNER.invoke(app, ["warehouse", "inspect"])
    _assert_ok("warehouse inspect", warehouse)
    assert json.loads(warehouse.output)["research_episodes.parquet"] == 3


def _episode(episode_id: str, summary: str) -> ResearchEpisode:
    trade_day = date(2030, 1, 10)
    return ResearchEpisode(
        episode_id=episode_id,
        trade_date=trade_day,
        cutoff_at=datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST),
        created_at=datetime.combine(trade_day, time(16, 0, 0), tzinfo=KST),
        research_version="minimum-cli-test-v1",
        price_source_snapshot={"source": "minimum-cli"},
        blind_analysis=BlindAnalysis(
            summary=summary,
            open_world_mechanisms=[f"{episode_id} -> documented CLI command coverage"],
        ),
        available_from=datetime.combine(date(2030, 1, 11), time(0, 0, 0), tzinfo=KST),
    )


def _assert_ok(label: str, result) -> None:
    assert result.exit_code == 0, f"{label} failed:\n{result.output}"
