from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from news_scalping_lab.audits.hardcoding import audit_hardcoding
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


@pytest.mark.asyncio
async def test_temporal_web_guard_excludes_cutoff_after_sources() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    guard = TemporalWebGuard(FutureOnlyProvider())
    assert await guard.search("query", cutoff_at=cutoff) == []
    assert guard.excluded_source_ids == ["WEB-FUTURE"]


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
