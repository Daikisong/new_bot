from __future__ import annotations

from datetime import date, datetime

from news_scalping_lab.utils import (
    KST,
    default_news_window_start,
    is_krx_trading_day,
    next_trading_day,
    slug,
)


def test_slug_preserves_korean_letters_and_normalizes_separators() -> None:
    assert slug(" 가상회사 신규 시설 검토! ") == "가상회사-신규-시설-검토"
    assert slug("Sample_Co / 신규-사업") == "sample_co-신규-사업"


def test_next_trading_day_skips_weekends() -> None:
    assert next_trading_day(date(2030, 1, 10)) == date(2030, 1, 11)
    assert next_trading_day(date(2030, 1, 11)) == date(2030, 1, 14)


def test_next_trading_day_skips_krx_holidays() -> None:
    assert next_trading_day(date(2026, 10, 2)) == date(2026, 10, 6)
    assert next_trading_day(date(2026, 10, 5)) == date(2026, 10, 6)


def test_news_window_starts_at_previous_krx_session_close() -> None:
    assert default_news_window_start(date(2026, 9, 28)) == datetime(
        2026, 9, 23, 15, 30, tzinfo=KST
    )
    assert default_news_window_start(date(2026, 10, 6)) == datetime(
        2026, 10, 2, 15, 30, tzinfo=KST
    )
    assert is_krx_trading_day(date(2026, 9, 28))
    assert not is_krx_trading_day(date(2026, 9, 25))
    assert not is_krx_trading_day(date(2026, 10, 5))
