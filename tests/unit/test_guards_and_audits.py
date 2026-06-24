from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from news_scalping_lab.audits.hardcoding import audit_hardcoding
from news_scalping_lab.prices.base import BlindPriceAccessError, BlindPriceGuard
from news_scalping_lab.prices.mock import MockPriceSource
from news_scalping_lab.utils import KST
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
