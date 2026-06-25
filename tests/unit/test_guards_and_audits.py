from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from news_scalping_lab.audits.hardcoding import audit_hardcoding
from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.prices.base import BlindPriceAccessError, BlindPriceGuard
from news_scalping_lab.prices.mock import MockPriceSource
from news_scalping_lab.utils import KST, canonical_json, sha256_text, write_json
from news_scalping_lab.web.provider import TemporalWebGuard, WebSearchResult


def _provenance(source_type: str = "test") -> list[dict[str, str]]:
    return [
        {
            "source_id": f"SRC-{source_type}",
            "source_type": source_type,
            "uri": f"test://{source_type}",
            "content_sha256": "a" * 64,
        }
    ]


def _candidate_with_provenance() -> dict[str, object]:
    return {
        "company_name": "CandidateCo",
        "event_ids": ["EVT-1"],
        "provenance": _provenance("test_candidate"),
    }


def _sector_with_provenance() -> dict[str, object]:
    return {
        "name": "SectorCo catalyst cluster",
        "triggering_events": ["EVT-1"],
        "formation_mechanism": "current catalyst -> open-world sector hypothesis",
        "expected_breadth": "narrow",
        "provenance": _provenance("test_dominant_sector"),
    }


def _blind_analysis_with_provenance() -> dict[str, object]:
    return {
        "summary": "Test blind analysis.",
        "provenance": _provenance("test_blind_analysis"),
    }


def _trace_payload(*, prompt_sha256: str = "blind-hash") -> dict[str, object]:
    trace_input = {
        "prompt_sha256": prompt_sha256,
        "prompt_chars": 100,
        "response_model": "BlindPrediction",
    }
    output = {"prediction_id": "PRED-linked"}
    return {
        "trace_id": "TRACE-linked",
        "operation": "generate_structured",
        "purpose": "daily_blind_analysis",
        "status": "ok",
        "provider": "DeterministicMockLLMProvider",
        "model_config": {"provider": "mock"},
        "input": trace_input,
        "input_sha256": sha256_text(canonical_json(trace_input)),
        "output": output,
        "output_sha256": sha256_text(canonical_json(output)),
        "checkpoint_id": "LLMCKPT-linked",
        "tool_calls": [],
        "retries": 0,
        "token_usage": {"prompt_tokens_estimate": 25, "completion_tokens_estimate": 10},
        "started_at": "2030-01-10T08:59:00+09:00",
        "finished_at": "2030-01-10T08:59:01+09:00",
        "prompt_version": "daily_blind_analysis.v1",
    }


def test_blind_price_guard_blocks_d_day() -> None:
    trade_day = date(2030, 1, 10)
    guard = BlindPriceGuard(MockPriceSource(), trade_date=trade_day)
    with pytest.raises(BlindPriceAccessError):
        guard.get_snapshot("UNKNOWN", as_of=trade_day)
    with pytest.raises(BlindPriceAccessError):
        guard.get_history("UNKNOWN", through=trade_day)
    with pytest.raises(BlindPriceAccessError):
        guard.get_outcome("UNKNOWN", trade_date=trade_day)
    assert guard.get_snapshot("UNKNOWN", as_of=date(2030, 1, 9)) is not None
    assert guard.get_history("UNKNOWN", through=date(2030, 1, 9))


class FutureOnlyProvider:
    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                source_id="WEB-FUTURE",
                title=query,
                url="mock://future",
                snippet="future-only",
                published_at=cutoff_at + timedelta(seconds=1),
            )
        ]

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        return url

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        return result.published_at is None or result.published_at <= cutoff_at


class ProviderTimestampRejectsUnknown:
    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                source_id="WEB-UNVERIFIED",
                title=query,
                url="mock://unknown",
                snippet="timestamp cannot be verified",
                published_at=None,
            )
        ]

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        return url

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        return False


@pytest.mark.asyncio
async def test_temporal_web_guard_excludes_cutoff_after_sources() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    guard = TemporalWebGuard(FutureOnlyProvider())
    assert await guard.search("query", cutoff_at=cutoff) == []
    assert guard.excluded_source_ids == ["WEB-FUTURE"]


@pytest.mark.asyncio
async def test_temporal_web_guard_uses_provider_timestamp_verification() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    guard = TemporalWebGuard(ProviderTimestampRejectsUnknown())
    assert await guard.search("query", cutoff_at=cutoff) == []
    assert guard.excluded_source_ids == ["WEB-UNVERIFIED"]


def test_hardcoding_audit_passes_current_source() -> None:
    root = Path(__file__).resolve().parents[2]
    result = audit_hardcoding(root)
    assert result["passed"], result["findings"]


def test_hardcoding_audit_flags_domain_maps_and_ticker_lists(tmp_path: Path) -> None:
    source_dir = tmp_path / "src" / "news_scalping_lab"
    source_dir.mkdir(parents=True)
    (source_dir / "domain_rules.py").write_text(
        """
THEME_MAP = {
    "new_policy": ["111111", "FictionalBuilder"],
}

BENEFICIARY_WHITELIST = ["222222", "FictionalInfra"]
""".strip(),
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    rules = {finding["rule"] for finding in findings}
    assert "domain_hardcoding_collection" in rules
    assert "quoted_six_digit_ticker" in rules


def test_hardcoding_audit_flags_fixed_news_expression_scores(tmp_path: Path) -> None:
    source_dir = tmp_path / "src" / "news_scalping_lab"
    source_dir.mkdir(parents=True)
    (source_dir / "ranking.py").write_text(
        """
def rank(title: str) -> int:
    score = 0
    if "special-region plant" in title:
        score = 5
    return score
""".strip(),
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    rules = {finding["rule"] for finding in findings}
    assert "fixed_expression_score" in rules


def test_provenance_audit_requires_prediction_context_manifest(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-missing`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "2030-01-10.json: missing context_manifest_id" in result["findings"]


def test_provenance_audit_accepts_manifest_and_report_links(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "dominant_sectors": [_sector_with_provenance()],
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]


def test_provenance_audit_requires_blind_sector_and_candidate_provenance(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": {"summary": "No provenance."},
            "dominant_sectors": [
                {
                    "name": "SectorWithoutProvenance",
                    "formation_mechanism": "missing source",
                    "expected_breadth": "unknown",
                }
            ],
            "candidates": [{"company_name": "CandidateCo", "event_ids": ["EVT-1"]}],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "2030-01-10.json: blind_analysis missing provenance" in result["findings"]
    assert (
        "2030-01-10.json: dominant sector missing provenance: SectorWithoutProvenance"
    ) in result["findings"]
    assert (
        "2030-01-10.json: dominant sector lacks provenance anchors: SectorWithoutProvenance"
    ) in result["findings"]
    assert (
        "2030-01-10.json: candidate missing provenance: CandidateCo"
    ) in result["findings"]


def test_provenance_audit_flags_prompt_hash_without_matching_trace(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "manifest-prompt-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    write_json(
        tmp_path / "runs" / "traces" / "TRACE-daily.json",
        _trace_payload(prompt_sha256="different-trace-hash"),
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: prompt hash has no matching trace for daily_blind_analysis"
    ) in result["findings"]


def test_provenance_audit_flags_incomplete_llm_trace_metadata(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "blind-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    incomplete_trace = _trace_payload(prompt_sha256="blind-hash")
    incomplete_trace.pop("model_config")
    incomplete_trace.pop("prompt_version")
    incomplete_trace["input_sha256"] = "wrong"
    incomplete_trace["token_usage"] = {}
    write_json(tmp_path / "runs" / "traces" / "TRACE-daily.json", incomplete_trace)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "TRACE-daily.json: trace missing model_config" in result["findings"]
    assert "TRACE-daily.json: trace missing prompt_version" in result["findings"]
    assert "TRACE-daily.json: trace input_sha256 mismatch" in result["findings"]
    assert "TRACE-daily.json: trace missing prompt token estimate" in result["findings"]


def test_provenance_audit_accepts_red_team_artifact_links(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "prediction_id": "PRED-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "prompt_hashes": {
                "blind_analysis": "blind-hash",
                "red_team_candidate_review": "red-team-hash",
                "final_synthesis": "final-hash",
            },
            "token_counts": {"final_synthesis_prompt": 10},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "red_team_artifacts": ["runs/checkpoints/red_team/RUN-linked.json"],
        },
    )
    write_json(
        tmp_path / "runs" / "checkpoints" / "red_team" / "RUN-linked.json",
        {
            "schema_version": "nslab.red_team_artifact.v1",
            "run_id": "RUN-linked",
            "source_prediction_id": "PRED-linked",
            "prompt_version": "red_team.candidate_attack.v1",
            "prompt_sha256": "red-team-hash",
            "created_at": "2030-01-10T08:59:59+09:00",
            "candidate_count": 1,
            "candidate_findings": [{"candidate_rank": 1, "passed_to_synthesis": True}],
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]


def test_provenance_audit_flags_red_team_artifact_mismatch(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "prediction_id": "PRED-linked",
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {
                "blind_analysis": "blind-hash",
                "red_team_candidate_review": "red-team-hash",
                "final_synthesis": "final-hash",
            },
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "red_team_artifacts": ["runs/checkpoints/red_team/RUN-linked.json"],
        },
    )
    write_json(
        tmp_path / "runs" / "checkpoints" / "red_team" / "RUN-linked.json",
        {
            "schema_version": "nslab.red_team_artifact.v1",
            "run_id": "RUN-other",
            "source_prediction_id": "",
            "prompt_version": "red_team.candidate_attack.v1",
            "prompt_sha256": "wrong-hash",
            "created_at": "2030-01-10T08:59:59+09:00",
            "candidate_count": 0,
            "candidate_findings": [],
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: red-team artifact run_id mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in result["findings"]
    assert (
        "2030-01-10.json: red-team artifact missing source_prediction_id"
    ) in result["findings"]
    assert (
        "2030-01-10.json: red-team artifact prompt hash mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in result["findings"]
    assert (
        "2030-01-10.json: red-team artifact candidate_count mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in result["findings"]


def test_lookahead_audit_flags_future_retrieved_episode_and_context_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "research" / "accepted").mkdir(parents=True)
    (tmp_path / "brain" / "current").mkdir(parents=True)
    (tmp_path / "memory" / "shard_brains" / "current").mkdir(parents=True)
    (tmp_path / "runs" / "checkpoints" / "memory_sweep" / "RUN-lookahead").mkdir(
        parents=True
    )
    write_json(
        tmp_path / "research" / "accepted" / "EP-future.json",
        {
            "episode_id": "EP-future",
            "available_from": "2030-01-10T09:30:00+09:00",
        },
    )
    (tmp_path / "brain" / "current" / "00_world_model.md").write_text(
        "future context leak EP-future",
        encoding="utf-8",
    )
    (tmp_path / "memory" / "shard_brains" / "current" / "shard_0001.md").write_text(
        "future shard leak EP-future",
        encoding="utf-8",
    )
    write_json(
        tmp_path
        / "runs"
        / "checkpoints"
        / "memory_sweep"
        / "RUN-lookahead"
        / "shard_0001.json",
        {"episode_ids": ["EP-future"], "related_lessons": ["future leak EP-future"]},
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-lookahead.json",
        {
            "run_id": "RUN-lookahead",
            "mode": "brain",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "retrieved_episode_ids": ["EP-future"],
            "excluded_retrieved_episode_ids": [],
            "brain_files": ["brain/current/00_world_model.md"],
            "shard_brain_files": ["memory/shard_brains/current/shard_0001.md"],
            "memory_sweep_artifacts": [
                "runs/checkpoints/memory_sweep/RUN-lookahead/shard_0001.json"
            ],
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-lookahead.json: future retrieved episode not excluded: EP-future" in findings
    assert (
        "RUN-lookahead.json: context file contains future episode EP-future: "
        "brain/current/00_world_model.md"
    ) in findings
    assert (
        "RUN-lookahead.json: context file contains future episode EP-future: "
        "memory/shard_brains/current/shard_0001.md"
    ) in findings
    assert (
        "RUN-lookahead.json: context file contains future episode EP-future: "
        "runs/checkpoints/memory_sweep/RUN-lookahead/shard_0001.json"
    ) in findings


def test_lookahead_audit_flags_missing_manifest_time_fields(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-missing-time.json",
        {
            "run_id": "RUN-missing-time",
            "mode": "brain",
            "price_snapshot": {"allowed_through": "2030-01-09"},
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert "RUN-missing-time.json: missing trade_date" in result["findings"]
    assert "RUN-missing-time.json: missing cutoff_at" in result["findings"]


def test_lookahead_audit_flags_news_only_blind_protocol_violations(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-news-only-violation.json",
        {
            "run_id": "RUN-news-only-violation",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_context_mode": "NEWS_ONLY_STRICT",
            "blind_web_search_call_count": 1,
            "blind_price_repository_access_count": 1,
            "blind_current_price_access_count": 1,
            "no_d_outcome_exposed": False,
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert (
        "RUN-news-only-violation.json: blind_web_search_call_count must be 0 in NEWS_ONLY_STRICT"
    ) in findings
    assert (
        "RUN-news-only-violation.json: blind_price_repository_access_count must be 0 in NEWS_ONLY_STRICT"
    ) in findings
    assert (
        "RUN-news-only-violation.json: blind_current_price_access_count must be 0 in NEWS_ONLY_STRICT"
    ) in findings
    assert "RUN-news-only-violation.json: no_d_outcome_exposed must be true" in findings


def test_lookahead_audit_flags_cutoff_safe_web_blind_artifact_leaks(
    tmp_path: Path,
) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    source_dir = tmp_path / "runs" / "checkpoints" / "source_ledger" / "RUN-web"
    web_dir = tmp_path / "runs" / "checkpoints" / "web_sources" / "RUN-web"
    source_dir.mkdir(parents=True)
    web_dir.mkdir(parents=True)
    web_artifact = web_dir / "web_sources.jsonl"
    web_payload = (
        canonical_json(
            {
                "schema_version": "nslab.web_source.v1",
                "source_id": "WEB-FUTURE",
                "query": "future leak",
                "title": "future source",
                "url": "https://example.test/future",
                "snippet": "future",
                "published_at": "2030-01-10T09:30:00+09:00",
                "retrieved_at": "2030-01-10T09:31:00+09:00",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "time_verified": False,
                "available_before_cutoff": False,
                "content_sha256": "abc",
                "opened_text_sha256": "def",
            }
        )
        + "\n"
    )
    web_artifact.write_text(web_payload, encoding="utf-8")
    source_artifact = source_dir / "source_ledger.jsonl"
    source_artifact.write_text("", encoding="utf-8")
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-web.json",
        {
            "run_id": "RUN-web",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_context_mode": "CUTOFF_SAFE_WEB_BLIND",
            "blind_web_search_call_count": 1,
            "blind_price_repository_access_count": 0,
            "blind_current_price_access_count": 0,
            "no_d_outcome_exposed": True,
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "source_ledger_artifact": source_artifact.relative_to(tmp_path).as_posix(),
            "source_ledger_sha256": sha256_text(""),
            "source_ledger_entry_count": 0,
            "web_sources": ["WEB-FUTURE"],
            "excluded_web_source_ids": ["WEB-FUTURE"],
            "web_source_artifact": web_artifact.relative_to(tmp_path).as_posix(),
            "web_source_sha256": sha256_text(web_payload),
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-web.json: web source is both included and excluded" in findings
    assert "RUN-web.json: web_source:1 is not cutoff verified" in findings
    assert "RUN-web.json: web_source:1 after cutoff" in findings


def test_lookahead_audit_flags_invalid_row_disposition_artifact(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    artifact_dir = tmp_path / "runs" / "checkpoints" / "row_disposition" / "RUN-row"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "row_disposition.jsonl"
    artifact.write_text(
        '{"row_number":1,"title":"raw title must not be duplicated"}\n'
        '{"row_number":1,"disposition":"INCLUDED_BEFORE_CUTOFF"}\n',
        encoding="utf-8",
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-row.json",
        {
            "run_id": "RUN-row",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "row_disposition_artifact": artifact.relative_to(tmp_path).as_posix(),
            "row_disposition_sha256": "wrong",
            "row_disposition_coverage_ratio": 0.5,
            "row_disposition_summary": {"total_rows": 3},
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-row.json: row_disposition_sha256 mismatch" in findings
    assert "RUN-row.json: row_disposition:1 must not duplicate title/body" in findings
    assert "RUN-row.json: row_disposition duplicate row_number" in findings
    assert "RUN-row.json: row_disposition total_rows mismatch" in findings
    assert "RUN-row.json: row_disposition coverage ratio must be 1.0" in findings


def test_lookahead_audit_flags_invalid_source_ledger_artifact(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    artifact_dir = tmp_path / "runs" / "checkpoints" / "source_ledger" / "RUN-ledger"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "source_ledger.jsonl"
    artifact.write_text(
        canonical_json(
            {
                "source_id": "SRC-1",
                "source_type": "news_csv_row",
                "title": "source one",
                "publisher": None,
                "url": "news://one",
                "published_at": "2030-01-10T09:30:00+09:00",
                "retrieved_at": "2030-01-10T10:00:00+09:00",
                "time_verified": True,
                "available_before_cutoff": False,
                "usage_phase": "BLIND",
                "input_row_ids": [1],
                "content_sha256": "abc",
                "notes": "bad",
                "body": "must not be duplicated",
            }
        )
        + "\n"
        + canonical_json({"source_id": "SRC-1", "usage_phase": "NOPE"})
        + "\n",
        encoding="utf-8",
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-ledger.json",
        {
            "run_id": "RUN-ledger",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "source_ledger_artifact": artifact.relative_to(tmp_path).as_posix(),
            "source_ledger_sha256": "wrong",
            "source_ledger_entry_count": 3,
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-ledger.json: source_ledger_sha256 mismatch" in findings
    assert "RUN-ledger.json: source_ledger:1 must not duplicate body/content" in findings
    assert "RUN-ledger.json: source_ledger:1 BLIND source after cutoff" in findings
    assert any(
        finding.startswith("RUN-ledger.json: source_ledger:2 missing fields:")
        for finding in findings
    )
    assert "RUN-ledger.json: source_ledger:2 invalid usage_phase" in findings
    assert "RUN-ledger.json: source_ledger duplicate source_id" in findings
    assert "RUN-ledger.json: source_ledger entry_count mismatch" in findings


def test_lookahead_audit_flags_invalid_blind_seal_artifacts(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    receipt_dir = tmp_path / "runs" / "checkpoints" / "blind_seal" / "RUN-seal"
    phase_dir = tmp_path / "runs" / "checkpoints" / "phase_state" / "RUN-seal"
    receipt_dir.mkdir(parents=True)
    phase_dir.mkdir(parents=True)
    receipt = receipt_dir / "blind_seal_receipt.json"
    phase_state = phase_dir / "phase_state.json"
    write_json(
        receipt,
        {
            "schema_version": "nslab.blind_seal_receipt.v1",
            "run_id": "RUN-seal",
            "phase": "OPEN",
            "blind_artifact_sha256": "other",
            "no_d_outcome_exposed": False,
        },
    )
    write_json(
        phase_state,
        {
            "schema_version": "nslab.phase_state.v1",
            "run_id": "RUN-seal",
            "phase": "OPEN",
            "blind_seal_receipt_sha256": "wrong",
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-seal.json",
        {
            "run_id": "RUN-seal",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "blind_artifact_sha256": "abc",
            "blind_seal_receipt_artifact": receipt.relative_to(tmp_path).as_posix(),
            "blind_seal_receipt_sha256": "bad",
            "phase_state_artifact": phase_state.relative_to(tmp_path).as_posix(),
            "phase_state_sha256": "bad",
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-seal.json: blind_seal_receipt_sha256 mismatch" in findings
    assert "RUN-seal.json: phase_state_sha256 mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt phase must be BLIND_SEALED" in findings
    assert "RUN-seal.json: blind_seal_receipt blind hash mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt no_d_outcome_exposed must be true" in findings
    assert "RUN-seal.json: phase_state phase must be BLIND_SEALED" in findings
    assert "RUN-seal.json: phase_state receipt sha mismatch" in findings


def test_lookahead_audit_checks_session_pack_context_files(tmp_path: Path) -> None:
    (tmp_path / "session_packs" / "2030-01-10").mkdir(parents=True)
    (tmp_path / "research" / "accepted").mkdir(parents=True)
    (tmp_path / "brain" / "current").mkdir(parents=True)
    write_json(
        tmp_path / "research" / "accepted" / "EP-after-cutoff.json",
        {
            "episode_id": "EP-after-cutoff",
            "available_from": "2030-01-10T09:30:00+09:00",
        },
    )
    (tmp_path / "brain" / "current" / "00_world_model.md").write_text(
        "session pack leak EP-after-cutoff",
        encoding="utf-8",
    )
    write_json(
        tmp_path / "session_packs" / "2030-01-10" / "manifest.json",
        {
            "schema_version": "nslab.session_pack_manifest.v1",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "mode": "brain",
            "brain_files": ["brain/current/00_world_model.md"],
            "shard_brain_files": [],
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert (
        "session_packs/2030-01-10/manifest.json: context file contains future episode "
        "EP-after-cutoff: brain/current/00_world_model.md"
    ) in result["findings"]
