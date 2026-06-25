from __future__ import annotations

from datetime import date

from news_scalping_lab.utils import next_trading_day, slug


def test_slug_preserves_korean_letters_and_normalizes_separators() -> None:
    assert slug(" 가상회사 신규 시설 검토! ") == "가상회사-신규-시설-검토"
    assert slug("Sample_Co / 신규-사업") == "sample_co-신규-사업"


def test_next_trading_day_skips_weekends() -> None:
    assert next_trading_day(date(2030, 1, 10)) == date(2030, 1, 11)
    assert next_trading_day(date(2030, 1, 11)) == date(2030, 1, 14)
