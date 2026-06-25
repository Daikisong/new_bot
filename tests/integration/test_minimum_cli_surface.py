from __future__ import annotations

import json
from datetime import date, datetime, time

from typer.testing import CliRunner

from news_scalping_lab.cli import app
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.utils import KST, read_json

RUNNER = CliRunner()


def test_goal_minimum_cli_commands_run_as_documented(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
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
    _assert_ok("news inspect", RUNNER.invoke(app, ["news", "inspect", str(news_csv)]))
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
    warehouse = RUNNER.invoke(app, ["warehouse", "inspect"])
    _assert_ok("warehouse inspect", warehouse)
    assert json.loads(warehouse.output)["research_episodes.parquet"] == 2


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
