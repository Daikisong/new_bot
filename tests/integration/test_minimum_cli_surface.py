from __future__ import annotations

import json
from datetime import date, datetime, time

from typer.testing import CliRunner

from news_scalping_lab.cli import app
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.utils import KST, file_sha256, read_json, write_json

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

    first_update = RUNNER.invoke(app, ["brain", "update", "--episode", "EP-cli-one"])
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

    rebuilt = RUNNER.invoke(app, ["brain", "rebuild", "--mode", "full"])
    _assert_ok("brain rebuild", rebuilt)
    rebuilt_payload = json.loads(rebuilt.output)
    second_version = rebuilt_payload["brain_version"]
    assert rebuilt_payload["covered_episode_ids"] == ["EP-cli-one", "EP-cli-two"]
    _assert_ok("brain audit", RUNNER.invoke(app, ["brain", "audit"]))
    diffed = RUNNER.invoke(app, ["brain", "diff", first_version, second_version])
    _assert_ok("brain diff", diffed)
    assert json.loads(diffed.output)["changed"] is True

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
            "--web-search",
        ],
    )
    _assert_ok("analyze --web-search", analyzed)
    run_id = json.loads(analyzed.output)["run_id"]
    assert read_json(tmp_path / "runs" / "manifests" / f"{run_id}.json")[
        "blind_context_mode"
    ] == "CUTOFF_SAFE_WEB_BLIND"
    inspected_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect", inspected_context)
    context_payload = json.loads(inspected_context.output)
    assert context_payload["run_id"] == run_id
    inspection = context_payload["inspection"]
    assert inspection["reproducibility_checks_passed"] is True
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
    assert supporting["event_cluster"]["hash_verified"] is True
    assert supporting["news_novelty_review"]["hash_verified"] is True
    assert supporting["semantic_retrieval_plan"]["hash_verified"] is True
    assert supporting["semantic_retrieval"]["hash_verified"] is True
    assert supporting["candidate_expansion"]["hash_verified"] is True
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
    assert memory_sweep["artifact_count"] >= 1
    llm_traces = inspection["llm_traces"]
    assert llm_traces["passed"] is True
    assert llm_traces["matched_prompt_count"] == 6
    for purpose in (
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
    assert inspection["output_artifacts"]["report"]["hash_verified"] is True
    assert inspection["output_artifacts"]["report"]["contains_run_id"] is True
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
    memory_sweep_file = tmp_path / context_payload["memory_sweep_artifacts"][0]
    original_memory_sweep = memory_sweep_file.read_text(encoding="utf-8")
    memory_sweep_file.write_text(
        original_memory_sweep.replace(
            "nslab.memory_sweep_contribution.v1",
            "tampered.memory_sweep",
            1,
        ),
        encoding="utf-8",
    )
    tampered_sweep_context = RUNNER.invoke(app, ["context", "inspect", run_id])
    _assert_ok("context inspect tampered memory sweep", tampered_sweep_context)
    tampered_sweep_inspection = json.loads(tampered_sweep_context.output)["inspection"]
    assert tampered_sweep_inspection["reproducibility_checks_passed"] is False
    assert tampered_sweep_inspection["memory_sweep"]["hashes_verified"] is False
    assert tampered_sweep_inspection["memory_sweep"]["metadata_verified"] is False
    memory_sweep_file.write_text(original_memory_sweep, encoding="utf-8")
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
    _assert_ok("audit provenance", RUNNER.invoke(app, ["audit", "provenance"]))
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

    postmortem_update = RUNNER.invoke(app, ["brain", "update", "--episode", "2030-01-12"])
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
