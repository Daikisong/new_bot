from __future__ import annotations

from datetime import date, datetime, time

import pytest
from typer.testing import CliRunner

from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.cli import app
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.session_pack import SessionPackBudgetExceededError, export_session_pack
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.records.models import (
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, canonical_json, read_json, sha256_text, write_json

RUNNER = CliRunner()


def _episode(
    episode_id: str,
    *,
    summary: str,
    available_day: date,
    available_time: time = time(0, 0, 0),
) -> ResearchEpisode:
    trade_day = date(2030, 1, 9)
    return ResearchEpisode(
        episode_id=episode_id,
        trade_date=trade_day,
        cutoff_at=datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST),
        created_at=datetime.combine(trade_day, time(16, 0, 0), tzinfo=KST),
        research_version="test-v1",
        input_news_files=[],
        input_news_hashes=[],
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(
            summary=summary,
            open_world_mechanisms=["current evidence -> open-world path"],
        ),
        available_from=datetime.combine(available_day, available_time, tzinfo=KST),
    )


def _record(
    record_id: str,
    *,
    available_from: datetime,
) -> BrainRecordEnvelope:
    trade_day = date(2030, 1, 9)
    payload = {
        "record_id": record_id,
        "record_type": "supervised_direct_event_case",
        "episode_id": "NSLAB-20300110-SESSION-RECORDS",
        "trade_date": trade_day.isoformat(),
        "available_from": available_from.isoformat(),
        "training_target": "direct_event_response",
        "evidence_phase": "BLIND_SAFE",
        "ticker": "100001",
        "company_name": "SessionRecordCo",
        "path_type": "single_event",
        "response_class": "positive_high10",
        "training_eligible": True,
        "provenance_source_ids": ["SRC-SESSION-RECORD"],
    }
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="supervised_direct_event_case",
        episode_id="NSLAB-20300110-SESSION-RECORDS",
        trade_date=trade_day,
        available_from=available_from,
        training_target="direct_event_response",
        evidence_phase="BLIND_SAFE",
        training_eligible=True,
        eligibility_reason="unit test record",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=["SRC-SESSION-RECORD"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )


def _store_records(tmp_path, records: list[BrainRecordEnvelope]) -> None:
    episode_id = records[0].episode_id
    source_path = tmp_path / "session_pack_records.md"
    raw_payload = "\n".join(record.model_dump_json() for record in records)
    raw_sha = sha256_text(raw_payload)
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(tmp_path).store_bundle(
        source_path=source_path,
        envelope=ResearchBundleEnvelope(
            bundle_schema_version="nslab.research_bundle.v11",
            manifest_schema_version="nslab.bundle_manifest.v11",
            episode_schema_version="nslab.research_episode.v11",
            episode_id=episode_id,
            trade_date=records[0].trade_date,
            cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
            available_from=min(record.available_from for record in records),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_bundle_sha256=raw_sha,
            raw_block_hashes={"brain_delta.jsonl": raw_sha},
            raw_block_counts={"brain_delta.jsonl": len(records)},
            provenance_closure_status="closed",
            adapter_name="unit-test",
            import_status="imported",
        ),
        index=NormalizedEpisodeIndex(
            episode_id=episode_id,
            trade_date=records[0].trade_date,
            cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
            available_from=min(record.available_from for record in records),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_block_names=["brain_delta.jsonl"],
            record_ids=[record.record_id for record in records],
            record_count_by_type={"supervised_direct_event_case": len(records)},
            training_eligible_record_count=len(records),
            source_ids=["SRC-SESSION-RECORD"],
        ),
        records=records,
        raw_blocks={"brain_delta.jsonl": raw_payload},
        validation_report={"passed": True},
    )


def test_session_pack_blocks_when_available_episode_exceeds_budget(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.session_pack_token_budget = 500
    ensure_project_dirs(settings)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","PackCo, catalyst","Session pack current news."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    small = _episode("EP-small", summary="Short useful lesson.", available_day=date(2030, 1, 10))
    large = _episode(
        "EP-large",
        summary="Long lesson. " * 300,
        available_day=date(2030, 1, 10),
    )
    future = _episode("EP-future", summary="Future postmortem.", available_day=date(2030, 1, 11))
    after_cutoff = _episode(
        "EP-after-cutoff",
        summary="Same-day after-cutoff postmortem.",
        available_day=date(2030, 1, 10),
        available_time=time(9, 30, 0),
    )
    for episode in (small, large, future, after_cutoff):
        store.save_episode(episode)
        store.accept(episode.episode_id)
    shard_dir = tmp_path / "memory" / "shard_brains" / "current"
    shard_dir.mkdir(parents=True)
    (shard_dir / "shard_0001.md").write_text(
        "# Shard Brain 0001\n\nEP-small\n",
        encoding="utf-8",
    )
    (shard_dir / "shard_0002.md").write_text(
        "# Shard Brain 0002\n\nEP-large\n",
        encoding="utf-8",
    )

    with pytest.raises(SessionPackBudgetExceededError) as exc_info:
        export_session_pack(
            settings,
            news_csv=news_csv,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            mode="brain",
        )

    output_dir = exc_info.value.output_dir
    manifest = read_json(output_dir / "manifest.json")
    memory_cases = (output_dir / "memory_cases.md").read_text(encoding="utf-8")
    research_brain = (output_dir / "research_brain.md").read_text(encoding="utf-8")
    omission_report = (output_dir / "omission_report.md").read_text(encoding="utf-8")

    assert manifest["blocked"] is True
    assert manifest["accepted_episode_count"] == 4
    assert manifest["cutoff_at"] == "2030-01-10T08:59:59+09:00"
    assert manifest["as_of"] == "2030-01-10T08:59:59+09:00"
    assert manifest["available_episode_count"] == 2
    assert manifest["available_episode_ids"] == ["EP-large", "EP-small"]
    assert manifest["unavailable_episode_count"] == 2
    assert manifest["included_episode_ids"] == []
    assert manifest["budget_omitted_episode_count"] == 2
    assert manifest["budget_omitted_episode_ids"] == ["EP-large", "EP-small"]
    assert manifest["available_coverage_complete"] is False
    assert manifest["brain_version"].startswith("brain-asof-")
    assert all(
        path.startswith("runs/checkpoints/brain_context/SESSION-")
        for path in manifest["brain_files"]
    )
    assert manifest["shard_brain_count"] == 1
    assert all(
        path.startswith("runs/checkpoints/brain_context/SESSION-")
        for path in manifest["shard_brain_files"]
    )
    assert set(manifest["brain_file_hashes"]) == set(manifest["brain_files"])
    assert set(manifest["shard_brain_file_hashes"]) == set(manifest["shard_brain_files"])
    assert set(manifest["omitted_episode_ids"]) == {
        "EP-small",
        "EP-large",
        "EP-future",
        "EP-after-cutoff",
    }
    assert set(manifest["unavailable_episode_ids"]) == {"EP-future", "EP-after-cutoff"}
    assert manifest["omission_report_file"] == "omission_report.md"
    assert manifest["omission_report_sha256"]
    assert "## Budget-Omitted Available Episodes" in omission_report
    assert "## Future-Unavailable Episodes" in omission_report
    assert "- EP-large" in omission_report
    assert "- EP-small" in omission_report
    assert "- EP-future" in omission_report
    assert "- EP-after-cutoff" in omission_report
    assert {item["reason"] for item in manifest["truncations"]} == {
        "session_pack_token_budget_exceeded",
        "episode_available_from_after_cutoff",
    }
    assert "session pack omitted available episodes due to token budget" in manifest["errors"]
    assert "session pack excluded future-unavailable episodes" in manifest["errors"]
    assert "EP-small" not in memory_cases
    assert "EP-large" not in memory_cases
    assert "EP-future" not in memory_cases
    assert "EP-after-cutoff" not in memory_cases
    assert "# Shard Brain Summaries" in research_brain
    assert "Shard Brain 0001" in research_brain
    assert "EP-small" in research_brain
    assert "EP-large" in research_brain
    assert set(manifest["pack_file_hashes"]) == {
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "record_memory_cases.md",
        "current_news.md",
        "company_memory.md",
        "market_context.md",
    }
    assert manifest["pack_files"] == [
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "record_memory_cases.md",
        "current_news.md",
        "company_memory.md",
        "market_context.md",
    ]
    assert manifest["pack_file_count"] == len(manifest["pack_files"])
    assert manifest["pack_sha256"]
    assert manifest["token_count_total"] == sum(manifest["token_counts"].values())
    assert manifest["token_counts"]["memory_cases.md"] > 0
    assert "session pack omitted available episodes due to token budget" in exc_info.value.errors


def test_session_pack_cli_exits_nonzero_when_available_episode_exceeds_budget(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.session_pack_token_budget = 500
    ensure_project_dirs(settings)
    (tmp_path / "configs" / "default.yaml").write_text(
        "limits:\n  session_pack_token_budget: 500\n",
        encoding="utf-8",
    )
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","PackCo, catalyst","Session pack current news."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    for episode in (
        _episode("EP-small", summary="Short useful lesson.", available_day=date(2030, 1, 10)),
        _episode("EP-large", summary="Long lesson. " * 300, available_day=date(2030, 1, 10)),
    ):
        store.save_episode(episode)
        store.accept(episode.episode_id)
    monkeypatch.chdir(tmp_path)

    result = RUNNER.invoke(
        app,
        [
            "context",
            "export-session-pack",
            "--news",
            str(news_csv),
            "--trade-date",
            "2030-01-10",
            "--cutoff",
            "2030-01-10T08:59:59+09:00",
            "--mode",
            "brain",
        ],
    )

    assert result.exit_code == 1
    assert "session pack omitted available episodes due to token budget" in result.output
    manifest = read_json(tmp_path / "session_packs" / "2030-01-10" / "manifest.json")
    assert manifest["blocked"] is True


def test_session_pack_blocks_when_required_context_exceeds_budget(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.session_pack_token_budget = 100
    ensure_project_dirs(settings)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","PackCo, catalyst","Session pack current news."\n',
        encoding="utf-8",
    )
    brain_dir = tmp_path / "brain" / "current"
    brain_dir.mkdir(parents=True, exist_ok=True)
    (brain_dir / "00_world_model.md").write_text(
        "# Required Brain\n\n" + ("Open-world context must stay present. " * 100),
        encoding="utf-8",
    )
    write_json(
        brain_dir / "coverage_manifest.json",
        {"covered_episode_ids": []},
    )
    shard_dir = tmp_path / "memory" / "shard_brains" / "current"
    shard_dir.mkdir(parents=True, exist_ok=True)
    (shard_dir / "shard_0001.md").write_text(
        "# Required Shard\n\n" + ("Shard summaries must stay present. " * 100),
        encoding="utf-8",
    )
    write_json(
        shard_dir / "manifest.json",
        {
            "schema_version": "nslab.shard_brain_manifest.v1",
            "brain_version": "brain-required-context",
            "shard_episode_count": settings.limits.shard_episode_count,
            "shard_count": 1,
            "shard_files": ["memory/shard_brains/current/shard_0001.md"],
            "covered_episode_ids": [],
        },
    )

    with pytest.raises(SessionPackBudgetExceededError) as exc_info:
        export_session_pack(
            settings,
            news_csv=news_csv,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            mode="brain",
        )

    manifest = read_json(exc_info.value.output_dir / "manifest.json")

    assert manifest["blocked"] is True
    assert manifest["included_episode_ids"] == []
    assert manifest["available_episode_ids"] == []
    assert manifest["budget_omitted_episode_ids"] == []
    assert manifest["budget_omitted_episode_count"] == 0
    assert manifest["unavailable_episode_count"] == 0
    assert manifest["omitted_episode_ids"] == []
    assert manifest["token_count_total"] > manifest["token_budget"]
    assert manifest["omission_report_file"] == "omission_report.md"
    assert "Required context over budget: true" in (
        exc_info.value.output_dir / "omission_report.md"
    ).read_text(encoding="utf-8")
    assert "session pack required context exceeds token budget" in manifest["errors"]
    assert "session pack required context exceeds token budget" in exc_info.value.errors
    assert {
        item["reason"]
        for item in manifest["truncations"]
        if item["artifact"] == "session_pack"
    } == {"session_pack_required_context_exceeds_token_budget"}
    assert audit_lookahead(tmp_path)["passed"]


def test_session_pack_exports_record_memory_cases_as_of_cutoff(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","RecordPackCo, catalyst",'
        '"Session pack should include record-level memory."\n',
        encoding="utf-8",
    )
    available_record = _record(
        "REC-SESSION-AVAILABLE",
        available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
    )
    future_record = _record(
        "REC-SESSION-FUTURE",
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
    )
    _store_records(
        tmp_path,
        [available_record, future_record],
    )

    output_dir = export_session_pack(
        settings,
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="brain",
    )

    manifest = read_json(output_dir / "manifest.json")
    record_memory = (output_dir / "record_memory_cases.md").read_text(encoding="utf-8")

    assert "REC-SESSION-AVAILABLE" in record_memory
    assert "REC-SESSION-FUTURE" not in record_memory
    assert "SessionRecordCo" in record_memory
    assert available_record.raw_payload_sha256 in record_memory
    assert available_record.normalized_payload_sha256 in record_memory
    assert manifest["accepted_record_count"] == 2
    assert manifest["available_record_count"] == 1
    assert manifest["available_record_ids"] == ["REC-SESSION-AVAILABLE"]
    assert manifest["included_record_ids"] == ["REC-SESSION-AVAILABLE"]
    assert manifest["budget_omitted_record_ids"] == []
    assert manifest["unavailable_record_ids"] == ["REC-SESSION-FUTURE"]
    assert manifest["available_record_coverage_complete"] is True
    assert "record_memory_cases.md" in manifest["pack_files"]
    assert manifest["token_counts"]["record_memory_cases.md"] > 0
    assert "session pack excluded future-unavailable records" in manifest["errors"]
    assert audit_lookahead(tmp_path)["passed"]


def test_session_pack_blocks_when_available_record_exceeds_budget(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.session_pack_token_budget = 100
    ensure_project_dirs(settings)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","RecordPackCo, catalyst",'
        '"Session pack should block record memory omissions."\n',
        encoding="utf-8",
    )
    available_record = _record(
        "REC-SESSION-BUDGET-OMITTED",
        available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
    )
    _store_records(tmp_path, [available_record])

    with pytest.raises(SessionPackBudgetExceededError) as exc_info:
        export_session_pack(
            settings,
            news_csv=news_csv,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            mode="brain",
        )

    manifest = read_json(exc_info.value.output_dir / "manifest.json")

    assert manifest["blocked"] is True
    assert manifest["available_record_ids"] == ["REC-SESSION-BUDGET-OMITTED"]
    assert manifest["included_record_ids"] == []
    assert manifest["budget_omitted_record_ids"] == [
        "REC-SESSION-BUDGET-OMITTED"
    ]
    assert manifest["available_record_coverage_complete"] is False
    assert (
        "session pack omitted available records due to token budget"
        in manifest["errors"]
    )
    assert (
        "session pack omitted available records due to token budget"
        in exc_info.value.errors
    )
    assert audit_lookahead(tmp_path)["passed"]


def test_session_pack_uses_as_of_brain_context_when_current_contains_future_episode(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","PackCo, catalyst","Session pack current news."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    available = _episode(
        "EP-available",
        summary="Available lesson.",
        available_day=date(2030, 1, 10),
    )
    future = _episode(
        "EP-after-cutoff",
        summary="Future after cutoff lesson.",
        available_day=date(2030, 1, 10),
        available_time=time(9, 30, 0),
    )
    for episode in (available, future):
        store.save_episode(episode)
        store.accept(episode.episode_id)
    brain_dir = tmp_path / "brain" / "current"
    brain_dir.mkdir(parents=True, exist_ok=True)
    (brain_dir / "00_world_model.md").write_text(
        "Unsafe future context EP-after-cutoff",
        encoding="utf-8",
    )

    output_dir = export_session_pack(
        settings,
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="brain",
    )

    manifest = read_json(output_dir / "manifest.json")
    research_brain = (output_dir / "research_brain.md").read_text(encoding="utf-8")
    assert manifest["brain_version"].startswith("brain-asof-")
    assert manifest["budget_omitted_episode_ids"] == []
    assert manifest["unavailable_episode_count"] == 1
    assert manifest["available_coverage_complete"] is True
    assert all(
        path.startswith("runs/checkpoints/brain_context/SESSION-")
        for path in manifest["brain_files"]
    )
    assert "session pack excluded future-unavailable episodes" in manifest["errors"]
    assert "EP-available" in research_brain
    assert "EP-after-cutoff" not in research_brain
    assert not any("context file contains future episode" in item for item in manifest["errors"])


def test_session_pack_filters_company_and_market_memory_by_cutoff(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","PackCo, catalyst","Session pack current news."\n',
        encoding="utf-8",
    )
    store = ResearchStore(tmp_path)
    episode = _episode(
        "EP-available",
        summary="Available lesson.",
        available_day=date(2030, 1, 10),
    )
    store.save_episode(episode)
    store.accept(episode.episode_id)
    company_dir = tmp_path / "memory" / "company_memory"
    company_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        company_dir / "CM-safe.json",
        {
            "ticker": "100001",
            "company_name": "SafeMemoryCo",
            "aliases": ["SafeMemoryCo"],
            "business_descriptions": ["Known before cutoff."],
            "locations": [],
            "customers": [],
            "supply_chain_roles": [],
            "prior_market_narratives": [],
            "prior_leader_occurrences": [],
            "contradictory_relations": [],
            "known_at": "2030-01-10T08:00:00+09:00",
            "provenance": [],
        },
    )
    write_json(
        company_dir / "CM-future.json",
        {
            "ticker": "100002",
            "company_name": "FutureMemoryCo",
            "aliases": ["FutureMemoryCo"],
            "business_descriptions": ["Known after cutoff."],
            "locations": [],
            "customers": [],
            "supply_chain_roles": [],
            "prior_market_narratives": [],
            "prior_leader_occurrences": [],
            "contradictory_relations": [],
            "known_at": "2030-01-10T09:30:00+09:00",
            "provenance": [],
        },
    )
    market_dir = tmp_path / "memory" / "market_memory"
    market_dir.mkdir(parents=True, exist_ok=True)
    (market_dir / "claims.jsonl").write_text(
        '{"claim_id":"M-safe","available_from":"2030-01-10T08:00:00+09:00",'
        '"statement":"safe market context"}\n'
        '{"claim_id":"M-future","available_from":"2030-01-10T09:30:00+09:00",'
        '"statement":"future market context"}\n'
        '{"claim_id":"M-unscoped","statement":"unscoped market context"}\n',
        encoding="utf-8",
    )

    output_dir = export_session_pack(
        settings,
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="brain",
    )

    manifest = read_json(output_dir / "manifest.json")
    company_memory = (output_dir / "company_memory.md").read_text(encoding="utf-8")
    market_context = (output_dir / "market_context.md").read_text(encoding="utf-8")

    assert "SafeMemoryCo" in company_memory
    assert "FutureMemoryCo" not in company_memory
    assert "safe market context" in market_context
    assert "future market context" not in market_context
    assert "unscoped market context" not in market_context
    assert manifest["included_company_memory_files"] == ["memory/company_memory/CM-safe.json"]
    assert manifest["included_market_context_files"] == ["memory/market_memory/claims.jsonl#L1"]
    assert {
        item["reason"] for item in manifest["omitted_company_memory_files"]
    } == {"company_memory_known_after_cutoff"}
    assert {item["reason"] for item in manifest["omitted_market_context_files"]} == {
        "available_from_after_cutoff",
        "missing_temporal_scope",
    }
    assert "session pack excluded future company memory" in "\n".join(manifest["errors"])
    assert "session pack omitted unscoped market_context memory" in "\n".join(manifest["errors"])


def test_session_pack_filters_current_news_by_default_blind_window(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-09","15:29:59","Before window","Must not be exported."\n'
        '1,2,"2030-01-09","15:30:00","Inside window","Must be exported."\n'
        '1,3,"2030-01-10","09:00:00","After cutoff","Must not be exported."\n',
        encoding="utf-8",
    )

    output_dir = export_session_pack(
        settings,
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="brain",
    )

    manifest = read_json(output_dir / "manifest.json")
    current_news = (output_dir / "current_news.md").read_text(encoding="utf-8")

    assert "Inside window" in current_news
    assert "Before window" not in current_news
    assert "After cutoff" not in current_news
    assert manifest["news_window_start_at"] == "2030-01-09T15:30:00+09:00"
    assert manifest["news_window_end_at"] == "2030-01-10T08:59:59+09:00"
    assert manifest["news_row_count"] == 3
    assert manifest["included_news_row_count"] == 1
    assert manifest["excluded_news_row_count"] == 2
    assert len(manifest["current_news_event_ids"]) == 1
    assert len(manifest["excluded_news_event_ids"]) == 2
    assert {
        item["reason"] for item in manifest["truncations"] if item["artifact"] == "current_news.md"
    } == {"news_outside_blind_window"}

    audit = audit_lookahead(tmp_path)

    assert audit["passed"], audit["findings"]
