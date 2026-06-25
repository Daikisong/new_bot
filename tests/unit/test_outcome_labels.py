from __future__ import annotations

import pytest

from news_scalping_lab.outcomes.labels import (
    DAILY_ONLY_INTRADAY_FIELDS_UNAVAILABLE,
    build_outcome_labels,
    unavailable_outcome,
)


def test_build_outcome_labels_marks_upper_limit_touch_close_release_and_turnover() -> None:
    outcome = build_outcome_labels(
        previous_close=100.0,
        open_price=105.0,
        high=130.0,
        low=99.0,
        close=128.0,
        volume=2_000.0,
        amount=256_000.0,
        listed_shares=10_000.0,
        market_cap_previous_close=1_000_000.0,
    )

    assert outcome.open_gap_pct == pytest.approx(5.0)
    assert outcome.intraday_high_return_pct == pytest.approx(30.0)
    assert outcome.close_return_pct == pytest.approx(28.0)
    assert outcome.upper_limit_touched is True
    assert outcome.upper_limit_closed is False
    assert outcome.upper_limit_released is True
    assert outcome.one_price_upper_limit is False
    assert outcome.turnover_ratio == pytest.approx(20.0)
    assert outcome.market_cap_previous_close == 1_000_000.0
    assert outcome.intraday_fields_unavailable == DAILY_ONLY_INTRADAY_FIELDS_UNAVAILABLE


def test_build_outcome_labels_detects_one_price_upper_limit() -> None:
    outcome = build_outcome_labels(
        previous_close=100.0,
        open_price=130.0,
        high=130.0,
        low=130.0,
        close=130.0,
    )

    assert outcome.upper_limit_touched is True
    assert outcome.upper_limit_closed is True
    assert outcome.upper_limit_released is False
    assert outcome.one_price_upper_limit is True


def test_build_outcome_labels_returns_unavailable_without_previous_close() -> None:
    assert build_outcome_labels(
        previous_close=None,
        open_price=130.0,
        high=130.0,
        low=130.0,
        close=130.0,
    ) == unavailable_outcome()
    assert build_outcome_labels(
        previous_close=0.0,
        open_price=130.0,
        high=130.0,
        low=130.0,
        close=130.0,
    ).flags == ["PRICE_UNAVAILABLE"]
