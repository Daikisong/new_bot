from __future__ import annotations

import json

from typer.testing import CliRunner

from news_scalping_lab.cli import app
from news_scalping_lab.research_import.bundle import parse_bundle
from news_scalping_lab.utils import file_sha256, read_json

RUNNER = CliRunner()


def test_readme_quick_start_commands_produce_demo_outputs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
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
    assert inspection["context_files"]["brain"]["hashes_verified"] is True
    assert inspection["context_files"]["brain"]["file_count"] >= 12
    assert inspection["context_files"]["shard_brain"]["hashes_verified"] is True
    supporting = inspection["supporting_artifacts"]
    assert supporting["row_disposition"]["hash_verified"] is True
    assert supporting["source_ledger"]["hash_verified"] is True
    assert supporting["blind_seal_receipt"]["hash_verified"] is True
    assert supporting["phase_state"]["hash_verified"] is True
    assert supporting["red_team"]["metadata_verified"] is True
    assert inspection["output_artifacts"]["prediction"]["hash_verified"] is True
    assert (
        inspection["output_artifacts"]["prediction"]["context_manifest_id_verified"]
        is True
    )
    assert inspection["output_artifacts"]["report"]["hash_verified"] is True
    assert inspection["output_artifacts"]["report"]["contains_run_id"] is True
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
