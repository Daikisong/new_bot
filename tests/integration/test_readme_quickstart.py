from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from news_scalping_lab.cli import app
from news_scalping_lab.research_import.bundle import parse_bundle
from news_scalping_lab.utils import file_sha256, read_json

RUNNER = CliRunner()


def test_readme_quick_start_block_matches_exercised_commands() -> None:
    commands = _readme_code_block("## Quick Start")

    assert commands == [
        'python -m pip install -e ".[dev]"',
        "python -m news_scalping_lab.cli init",
        "python -m news_scalping_lab.cli doctor",
        "python -m news_scalping_lab.cli news inspect docs/csv/news_20260624.csv",
        "python -m news_scalping_lab.cli brain rebuild --mode full",
        "python -m news_scalping_lab.cli warehouse rebuild",
        (
            "python -m news_scalping_lab.cli analyze --news docs/csv/news_20260624.csv "
            "--trade-date 2026-06-24 --cutoff 2026-06-24T08:59:59+09:00 "
            "--mode exhaustive"
        ),
    ]


def test_readme_quick_start_commands_produce_demo_outputs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "mock")
    monkeypatch.setenv("NSLAB_WEB_PROVIDER", "mock")
    monkeypatch.delenv("NSLAB_STOCK_WEB_PATH", raising=False)
    monkeypatch.setenv("NSLAB_STOCK_WEB_CACHE", "false")
    monkeypatch.delenv("NSLAB_STOCK_WEB_CACHE_PATH", raising=False)
    monkeypatch.delenv("NSLAB_STOCK_WEB_REMOTE_URL", raising=False)
    csv_path = tmp_path / "docs" / "csv" / "news_20260624.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2026-06-24","08:30:00","FictionalPrecisionCo, asset sale complete",'
        '"Pre-cutoff balance sheet event requiring directness and novelty review."\n',
        encoding="utf-8",
    )

    init = RUNNER.invoke(app, ["init"])
    doctor = RUNNER.invoke(app, ["doctor"])
    inspected = RUNNER.invoke(app, ["news", "inspect", "docs/csv/news_20260624.csv"])
    rebuilt = RUNNER.invoke(app, ["brain", "rebuild", "--mode", "full"])
    warehouse = RUNNER.invoke(app, ["warehouse", "rebuild"])
    analyzed = RUNNER.invoke(
        app,
        [
            "analyze",
            "--news",
            "docs/csv/news_20260624.csv",
            "--trade-date",
            "2026-06-24",
            "--cutoff",
            "2026-06-24T08:59:59+09:00",
            "--mode",
            "exhaustive",
        ],
    )

    assert init.exit_code == 0, init.output
    assert doctor.exit_code == 0, doctor.output
    assert inspected.exit_code == 0, inspected.output
    assert rebuilt.exit_code == 0, rebuilt.output
    assert warehouse.exit_code == 0, warehouse.output
    assert analyzed.exit_code == 0, analyzed.output

    doctor_payload = json.loads(doctor.output)
    assert doctor_payload["vector_index"]["status"] in {"current", "missing"}
    inspect_payload = json.loads(inspected.output)
    assert inspect_payload["trade_date"] == "2026-06-24"
    assert inspect_payload["row_count"] == 1
    assert inspect_payload["default_news_window_start_at"] == "2026-06-23T15:30:00+09:00"
    assert inspect_payload["missing_collected_at"] == 1
    brain_manifest = json.loads(rebuilt.output)
    assert brain_manifest["coverage_complete"] is True
    assert read_json(tmp_path / "memory" / "vector_index" / "manifest.json")[
        "schema_version"
    ] == "nslab.local_vector_index.v1"

    warehouse_counts = json.loads(warehouse.output)
    assert "research_episodes" in warehouse_counts
    analysis = json.loads(analyzed.output)
    run_id = analysis["run_id"]

    assert (tmp_path / "predictions" / "2026-06-24.json").exists()
    assert (tmp_path / "reports" / "2026-06-24_preopen.md").exists()
    assert (tmp_path / "runs" / "manifests" / f"{run_id}.json").exists()
    saved_manifest = read_json(tmp_path / "runs" / "manifests" / f"{run_id}.json")
    assert saved_manifest["mode"] == "exhaustive"
    assert saved_manifest["accepted_episode_count"] == saved_manifest["swept_episode_count"]

    inspected_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    session_pack = RUNNER.invoke(
        app,
        [
            "context",
            "export-session-pack",
            "--news",
            "docs/csv/news_20260624.csv",
            "--trade-date",
            "2026-06-24",
            "--cutoff",
            "2026-06-24T08:59:59+09:00",
            "--mode",
            "brain",
        ],
    )
    analysis_bundle = RUNNER.invoke(
        app,
        ["context", "export-analysis-bundle", "--run-id", run_id],
    )

    assert inspected_context.exit_code == 0, inspected_context.output
    assert session_pack.exit_code == 0, session_pack.output
    assert analysis_bundle.exit_code == 0, analysis_bundle.output

    context_payload = json.loads(inspected_context.output)
    assert context_payload["run_id"] == run_id
    inspection = context_payload["inspection"]
    assert inspection["reproducibility_checks_passed"] is True
    news_input = inspection["news_input"]
    assert news_input["hash_verified"] is True
    assert news_input["expected_sha256"] == file_sha256(csv_path)
    assert news_input["observed_row_count"] == 1
    assert news_input["row_count_verified"] is True
    assert news_input["row_count_partition_verified"] is True
    assert news_input["observed_included_row_count"] == 1
    assert news_input["observed_excluded_row_count"] == 0
    assert news_input["included_row_count_verified"] is True
    assert news_input["excluded_row_count_verified"] is True
    assert news_input["observed_missing_collected_at"] == 1
    assert news_input["missing_collected_at_verified"] is True
    assert news_input["default_news_window_start_at"] == "2026-06-23T15:30:00+09:00"
    assert news_input["news_window_start_verified"] is True
    assert news_input["news_window_end_verified"] is True
    assert news_input["news_window_counts_verified"] is True
    assert inspection["context_files"]["brain"]["hashes_verified"] is True
    assert inspection["context_files"]["brain"]["file_count"] >= 12
    assert inspection["context_files"]["shard_brain"]["hashes_verified"] is True
    supporting = inspection["supporting_artifacts"]
    assert supporting["row_disposition"]["hash_verified"] is True
    assert supporting["event_cluster"]["hash_verified"] is True
    assert supporting["open_world_first_analysis"]["hash_verified"] is True
    assert supporting["open_world_first_analysis"]["schema_version_verified"] is True
    assert supporting["open_world_first_analysis"]["summary_verified"] is True
    assert supporting["news_novelty_review"]["hash_verified"] is True
    assert supporting["semantic_retrieval_plan"]["hash_verified"] is True
    assert supporting["semantic_retrieval"]["hash_verified"] is True
    assert supporting["candidate_expansion"]["hash_verified"] is True
    assert supporting["final_synthesis_context"]["hash_verified"] is True
    assert supporting["final_synthesis_context"]["schema_version_verified"] is True
    assert supporting["final_synthesis_context"]["payload_hash_verified"] is True
    assert supporting["final_synthesis_context"]["input_summary_verified"] is True
    assert supporting["final_synthesis_context"]["manifest_summary_verified"] is True
    assert supporting["source_ledger"]["hash_verified"] is True
    assert supporting["blind_seal_receipt"]["hash_verified"] is True
    assert supporting["phase_state"]["hash_verified"] is True
    assert supporting["red_team"]["metadata_verified"] is True
    memory_sweep = inspection["memory_sweep"]
    assert memory_sweep["passed"] is True
    assert memory_sweep["hashes_verified"] is True
    assert memory_sweep["metadata_verified"] is True
    assert memory_sweep["shard_count_verified"] is True
    assert memory_sweep["cache_hits_verified"] is True
    assert memory_sweep["swept_episode_ids_verified"] is True
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
    pack_payload = json.loads(session_pack.output)
    pack_dir = tmp_path / pack_payload["session_pack"]
    assert read_json(pack_dir / "manifest.json")["blocked"] is False
    bundle_payload = json.loads(analysis_bundle.output)
    bundle_path = tmp_path / bundle_payload["bundle"]
    parsed_bundle = parse_bundle(bundle_path)
    assert parsed_bundle.validation["blind_hash_verified"]
    assert parsed_bundle.validation["manifest_validation_self_consistent_verified"]

    evaluated = RUNNER.invoke(app, ["evaluate", "--trade-date", "2026-06-24"])
    assert evaluated.exit_code == 0, evaluated.output
    evaluation_payload = json.loads(evaluated.output)
    assert evaluation_payload["outcome_coverage_status"] == "PREDICTED_CANDIDATES_ONLY"
    assert evaluation_payload["performance_metrics"]["candidate_count"] > 0
    assert evaluation_payload["eligibility_matrix"]["forecast_evaluation_eligible"] is True
    postmortem_path = tmp_path / evaluation_payload["postmortem"]
    episode_id = evaluation_payload["research_episode_id"]
    assert postmortem_path.exists()
    assert (tmp_path / evaluation_payload["research_episode_path"]).exists()

    updated = RUNNER.invoke(
        app,
        ["brain", "update", "--episode", "2026-06-24"],
    )
    assert updated.exit_code == 0, updated.output
    update_manifest = json.loads(updated.output)
    assert update_manifest["coverage_complete"] is True
    assert episode_id in update_manifest["covered_episode_ids"]
    assert read_json(tmp_path / "brain" / "current" / "coverage_manifest.json")[
        "coverage_complete"
    ] is True


def _readme_code_block(section_heading: str) -> list[str]:
    repo_root = Path(__file__).resolve().parents[2]
    lines = (repo_root / "README.md").read_text(encoding="utf-8").splitlines()
    start = lines.index(section_heading)
    fence_start = next(
        index for index in range(start + 1, len(lines)) if lines[index].startswith("```")
    )
    fence_end = next(
        index for index in range(fence_start + 1, len(lines)) if lines[index].startswith("```")
    )
    return [line for line in lines[fence_start + 1 : fence_end] if line]
