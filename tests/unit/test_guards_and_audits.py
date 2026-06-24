from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from news_scalping_lab.audits.hardcoding import audit_hardcoding
from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.prices.base import BlindPriceAccessError, BlindPriceGuard
from news_scalping_lab.prices.mock import MockPriceSource
from news_scalping_lab.utils import KST, write_json
from news_scalping_lab.web.provider import TemporalWebGuard, WebSearchResult


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
            "candidates": [{"company_name": "CandidateCo", "event_ids": ["EVT-1"]}],
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

    assert result["passed"], result["findings"]


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
            "candidates": [{"company_name": "CandidateCo", "event_ids": ["EVT-1"]}],
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
            "candidates": [{"company_name": "CandidateCo", "event_ids": ["EVT-1"]}],
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
