"""Deterministic mock price source."""

from __future__ import annotations

from datetime import date, timedelta

from news_scalping_lab.contracts.models import OutcomeLabels
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.utils import sha256_text


class MockPriceSource:
    source_name = "mock-price"

    def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
        records: list[PriceRecord] = []
        base = 1000 + int(sha256_text(ticker)[:2], 16)
        for offset in range(5, 0, -1):
            day = through - timedelta(days=offset - 1)
            records.append(
                PriceRecord(
                    ticker=ticker,
                    trade_date=day,
                    open=float(base + offset),
                    high=float(base + offset + 10),
                    low=float(base + offset - 5),
                    close=float(base + offset + 3),
                    volume=float(base * offset),
                    amount=float(base * offset * 100),
                    market_cap=float(base * 1000),
                    listed_shares=float(base * 10),
                )
            )
        return records

    def get_snapshot(self, ticker: str, *, as_of: date) -> PriceRecord | None:
        history = self.get_history(ticker, through=as_of)
        return history[-1] if history else None

    def get_outcome(self, ticker: str, *, trade_date: date) -> OutcomeLabels:
        bucket = int(sha256_text(f"{ticker}|{trade_date}")[:2], 16)
        high_return = float(bucket % 30)
        return OutcomeLabels(
            open_gap_pct=float(bucket % 7),
            intraday_high_return_pct=high_return,
            close_return_pct=float(bucket % 15),
            upper_limit_touched=high_return >= 29,
            upper_limit_closed=high_return >= 29 and bucket % 2 == 0,
            upper_limit_released=high_return >= 29 and bucket % 2 == 1,
            one_price_upper_limit=False,
            volume=float(bucket * 1000),
            amount=float(bucket * 10000),
            turnover_ratio=float(bucket % 20),
            market_cap_previous_close=float(bucket * 1000000),
            intraday_fields_unavailable=[
                "first_upper_limit_touch_time",
                "first_one_minute_return",
                "volatility_interruptions",
            ],
        )
