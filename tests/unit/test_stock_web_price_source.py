from __future__ import annotations

from datetime import date

import pytest

from news_scalping_lab.prices.stock_web import StockWebPriceSource


def test_stock_web_reads_manifest_schema_and_symbol_year_shards(tmp_path) -> None:
    atlas = tmp_path / "atlas"
    (atlas / "ohlcv_tradable_by_symbol_year" / "005" / "005930").mkdir(parents=True)
    (atlas / "ohlcv_raw_by_symbol_year").mkdir(parents=True)
    (atlas / "ohlcv_min_by_symbol_year").mkdir(parents=True)
    (atlas / "symbol_profiles" / "005").mkdir(parents=True)
    (atlas / "manifest.json").write_text(
        """
{
  "source_name": "FinanceData/marcap",
  "source_repo_url": "https://github.com/FinanceData/marcap",
  "price_adjustment_status": "raw_unadjusted_marcap",
  "calibration_shard_root": "atlas/ohlcv_tradable_by_symbol_year",
  "raw_shard_root": "atlas/ohlcv_raw_by_symbol_year",
  "deprecated_or_compat_shard_root": "atlas/ohlcv_min_by_symbol_year",
  "max_date": "2030-01-10"
}
""".strip(),
        encoding="utf-8",
    )
    (atlas / "schema.json").write_text(
        """
{
  "tradable_shard_columns": {
    "d": "date",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "a": "amount",
    "mc": "marcap",
    "s": "stocks",
    "m": "market"
  },
  "raw_shard_columns": {
    "d": "date",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "a": "amount",
    "mc": "marcap",
    "s": "stocks",
    "m": "market",
    "rs": "row_status"
  }
}
""".strip(),
        encoding="utf-8",
    )
    (atlas / "symbol_profiles" / "005" / "005930.json").write_text(
        '{"code":"005930","available_years":[2030]}',
        encoding="utf-8",
    )
    (atlas / "ohlcv_tradable_by_symbol_year" / "005" / "005930" / "2030.csv").write_text(
        "\n".join(
            [
                "d,o,h,l,c,v,a,mc,s,m",
                "2030-01-08,100,110,95,100,1000,100000,1000000,10000,KOSPI",
                "2030-01-10,105,130,100,129,2000,200000,1290000,10000,KOSPI",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    source = StockWebPriceSource(tmp_path)
    schema = source.inspect_atlas_schema()
    history = source.get_history("005930", through=date(2030, 1, 10))
    outcome = source.get_outcome("005930", trade_date=date(2030, 1, 10))

    assert schema["calibration_shard_root"] == "atlas/ohlcv_tradable_by_symbol_year"
    assert [record.trade_date for record in history] == [date(2030, 1, 8), date(2030, 1, 10)]
    assert outcome.open_gap_pct == pytest.approx(5.0)
    assert outcome.intraday_high_return_pct == pytest.approx(30.0)
    assert outcome.close_return_pct == pytest.approx(29.0)
    assert outcome.upper_limit_touched is True
    assert outcome.upper_limit_closed is True
