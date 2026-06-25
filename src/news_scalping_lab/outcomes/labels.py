"""Outcome label construction from evaluation-phase market data."""

from __future__ import annotations

from news_scalping_lab.contracts.models import OutcomeLabels

UPPER_LIMIT_RETURN_THRESHOLD_PCT = 29.0


def unavailable_outcome(flag: str = "PRICE_UNAVAILABLE") -> OutcomeLabels:
    return OutcomeLabels(flags=[flag])


def build_outcome_labels(
    *,
    previous_close: float | None,
    open_price: float | None,
    high: float | None,
    low: float | None,
    close: float | None,
    volume: float | None = None,
    amount: float | None = None,
    listed_shares: float | None = None,
    market_cap_previous_close: float | None = None,
) -> OutcomeLabels:
    if previous_close is None or previous_close == 0:
        return unavailable_outcome()
    base_close = previous_close
    high_return = _pct_return(high, base_close)
    close_return = _pct_return(close, base_close)
    open_gap = _pct_return(open_price, base_close)
    low_return = _pct_return(low, base_close)
    touched = high_return is not None and high_return >= UPPER_LIMIT_RETURN_THRESHOLD_PCT
    closed = close_return is not None and close_return >= UPPER_LIMIT_RETURN_THRESHOLD_PCT
    one_price = all(
        value is not None and value >= UPPER_LIMIT_RETURN_THRESHOLD_PCT
        for value in (open_gap, high_return, low_return, close_return)
    )
    turnover_ratio = (
        volume / listed_shares * 100
        if volume is not None and listed_shares is not None and listed_shares > 0
        else None
    )
    return OutcomeLabels(
        open_gap_pct=open_gap,
        intraday_high_return_pct=high_return,
        close_return_pct=close_return,
        upper_limit_touched=touched,
        upper_limit_closed=closed,
        upper_limit_released=touched and not closed,
        one_price_upper_limit=one_price,
        volume=volume,
        amount=amount,
        turnover_ratio=turnover_ratio,
        market_cap_previous_close=market_cap_previous_close,
        intraday_fields_unavailable=[
            "first_upper_limit_touch_time",
            "first_one_minute_return",
            "volatility_interruptions",
        ],
    )


def _pct_return(value: float | None, previous_close: float) -> float | None:
    return None if value is None else (value / previous_close - 1) * 100
