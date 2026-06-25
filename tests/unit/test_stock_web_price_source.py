from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.prices.mock import MockPriceSource
from news_scalping_lab.prices.stock_web import StockWebPriceSource, ensure_stock_web_cache


def test_stock_web_reads_manifest_schema_and_symbol_year_shards(tmp_path) -> None:
    atlas = tmp_path / "atlas"
    (atlas / "ohlcv_tradable_by_symbol_year" / "005" / "005930").mkdir(parents=True)
    (atlas / "ohlcv_tradable_by_symbol_year" / "123" / "123456").mkdir(parents=True)
    (atlas / "ohlcv_tradable_by_symbol_year" / "999" / "999999").mkdir(parents=True)
    (atlas / "ohlcv_raw_by_symbol_year").mkdir(parents=True)
    (atlas / "ohlcv_min_by_symbol_year").mkdir(parents=True)
    (atlas / "symbol_profiles" / "005").mkdir(parents=True)
    (atlas / "symbol_profiles" / "123").mkdir(parents=True)
    (atlas / "symbol_profiles" / "999").mkdir(parents=True)
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
    (atlas / "symbol_profiles" / "123" / "123456.json").write_text(
        '{"code":"123456","available_years":[2030]}',
        encoding="utf-8",
    )
    (atlas / "symbol_profiles" / "999" / "999999.json").write_text(
        '{"code":"999999","available_years":[2030]}',
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
    (atlas / "ohlcv_tradable_by_symbol_year" / "123" / "123456" / "2030.csv").write_text(
        "\n".join(
            [
                "d,o,h,l,c,v,a,mc,s,m",
                "2030-01-08,100,110,95,100,1000,100000,1000000,10000,KOSPI",
                "2030-01-10,101,105,98,103,1500,150000,1030000,10000,KOSPI",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (atlas / "ohlcv_tradable_by_symbol_year" / "999" / "999999" / "2030.csv").write_text(
        "\n".join(
            [
                "d,o,h,l,c,v,a,mc,s,m",
                "2030-01-08,100,100,100,100,1000,100000,1000000,10000,KOSPI",
                "2030-01-10,130,130,130,130,3000,390000,1300000,10000,KOSPI",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    source = StockWebPriceSource(tmp_path)
    schema = source.inspect_atlas_schema()
    history = source.get_history("005930", through=date(2030, 1, 10))
    outcome = source.get_outcome("005930", trade_date=date(2030, 1, 10))
    one_price_outcome = source.get_outcome("999999", trade_date=date(2030, 1, 10))
    universe = source.get_outcome_universe(trade_date=date(2030, 1, 10))

    assert schema["calibration_shard_root"] == "atlas/ohlcv_tradable_by_symbol_year"
    assert [record.trade_date for record in history] == [date(2030, 1, 8), date(2030, 1, 10)]
    assert outcome.open_gap_pct == pytest.approx(5.0)
    assert outcome.intraday_high_return_pct == pytest.approx(30.0)
    assert outcome.close_return_pct == pytest.approx(29.0)
    assert outcome.upper_limit_touched is True
    assert outcome.upper_limit_closed is True
    assert outcome.one_price_upper_limit is False
    assert outcome.turnover_ratio == pytest.approx(20.0)
    assert one_price_outcome.one_price_upper_limit is True
    assert one_price_outcome.turnover_ratio == pytest.approx(30.0)
    assert sorted(universe) == ["005930", "123456", "999999"]
    assert universe["005930"].upper_limit_touched is True
    assert universe["123456"].upper_limit_touched is False
    assert universe["999999"].one_price_upper_limit is True


def test_stock_web_uses_schema_column_aliases_when_csv_headers_are_not_short_codes(
    tmp_path,
) -> None:
    atlas = tmp_path / "atlas"
    shard_dir = atlas / "custom_tradable" / "111" / "111111"
    shard_dir.mkdir(parents=True)
    (atlas / "symbol_profiles" / "111").mkdir(parents=True)
    (atlas / "manifest.json").write_text(
        """
{
  "source_name": "stock-web-custom-schema",
  "calibration_shard_root": "atlas/custom_tradable"
}
""".strip(),
        encoding="utf-8",
    )
    (atlas / "schema.json").write_text(
        """
{
  "tradable_shard_columns": {
    "d": "trade_day",
    "o": "open_px",
    "h": "high_px",
    "l": "low_px",
    "c": "close_px",
    "v": "trade_volume",
    "a": "trade_amount",
    "mc": "market_value",
    "s": "shares_listed"
  }
}
""".strip(),
        encoding="utf-8",
    )
    (atlas / "symbol_profiles" / "111" / "111111.json").write_text(
        '{"code":"111111","available_years":[2030]}',
        encoding="utf-8",
    )
    (shard_dir / "2030.csv").write_text(
        "\n".join(
            [
                "trade_day,open_px,high_px,low_px,close_px,trade_volume,trade_amount,market_value,shares_listed",
                "2030-01-08,100,100,100,100,1,100,1000000,1000",
                "2030-01-10,105,130,100,129,10,1290,1290000,1000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    source = StockWebPriceSource(tmp_path)
    history = source.get_history("111111", through=date(2030, 1, 10))
    outcome = source.get_outcome("111111", trade_date=date(2030, 1, 10))

    assert [record.open for record in history] == [100.0, 105.0]
    assert history[-1].amount == 1290.0
    assert history[-1].market_cap == 1290000.0
    assert history[-1].listed_shares == 1000.0
    assert outcome.intraday_high_return_pct == pytest.approx(30.0)
    assert outcome.upper_limit_touched is True


def test_stock_web_unions_profile_years_with_discovered_shard_years(tmp_path) -> None:
    atlas = tmp_path / "atlas"
    shard_dir = atlas / "ohlcv_tradable_by_symbol_year" / "222" / "222222"
    shard_dir.mkdir(parents=True)
    (atlas / "symbol_profiles" / "222").mkdir(parents=True)
    (atlas / "manifest.json").write_text(
        """
{
  "calibration_shard_root": "atlas/ohlcv_tradable_by_symbol_year"
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
    "c": "close"
  }
}
""".strip(),
        encoding="utf-8",
    )
    (atlas / "symbol_profiles" / "222" / "222222.json").write_text(
        '{"code":"222222","available_years":[2030,"bad-year"]}',
        encoding="utf-8",
    )
    (shard_dir / "2030.csv").write_text(
        "\n".join(
            [
                "d,o,h,l,c",
                "2030-01-08,100,101,99,100",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (shard_dir / "2031.csv").write_text(
        "\n".join(
            [
                "d,o,h,l,c",
                "2031-01-08,110,111,109,110",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    history = StockWebPriceSource(tmp_path).get_history("222222", through=date(2031, 1, 8))

    assert [record.trade_date for record in history] == [
        date(2030, 1, 8),
        date(2031, 1, 8),
    ]


def test_stock_web_cache_clones_remote_when_cache_is_missing(tmp_path) -> None:
    commands: list[tuple[list[str], Path | None]] = []

    def runner(args: list[str], cwd: Path | None) -> None:
        commands.append((args, cwd))

    cache_path = tmp_path / "cache" / "stock-web"

    result = ensure_stock_web_cache(
        cache_path,
        remote_url="https://example.test/stock-web.git",
        runner=runner,
    )

    assert result == cache_path
    assert commands == [
        (
            [
                "git",
                "clone",
                "--depth",
                "1",
                "https://example.test/stock-web.git",
                cache_path.as_posix(),
            ],
            None,
        )
    ]


def test_stock_web_cache_fetches_existing_git_checkout_when_refresh_requested(
    tmp_path,
) -> None:
    commands: list[tuple[list[str], Path | None]] = []

    def runner(args: list[str], cwd: Path | None) -> None:
        commands.append((args, cwd))

    cache_path = tmp_path / "stock-web"
    (cache_path / ".git").mkdir(parents=True)

    result = ensure_stock_web_cache(cache_path, refresh=True, runner=runner)

    assert result == cache_path
    assert commands == [(["git", "fetch", "--all", "--tags", "--prune"], cache_path)]


def test_stock_web_cache_accepts_prepopulated_non_git_cache(tmp_path) -> None:
    commands: list[tuple[list[str], Path | None]] = []

    def runner(args: list[str], cwd: Path | None) -> None:
        commands.append((args, cwd))

    cache_path = tmp_path / "stock-web"
    (cache_path / "atlas").mkdir(parents=True)

    result = ensure_stock_web_cache(cache_path, runner=runner)

    assert result == cache_path
    assert commands == []


def test_price_factory_can_prepare_stock_web_cache(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, str]] = []

    def fake_ensure_stock_web_cache(cache_path: Path, *, remote_url: str) -> Path:
        calls.append((cache_path, remote_url))
        (cache_path / "atlas").mkdir(parents=True)
        return cache_path

    monkeypatch.setattr(
        "news_scalping_lab.prices.factory.ensure_stock_web_cache",
        fake_ensure_stock_web_cache,
    )
    settings = Settings(
        project_root=tmp_path,
        price_provider="stock-web",
        stock_web_cache_enabled=True,
        stock_web_cache_path=Path("cache/stock-web"),
        stock_web_remote_url="https://example.test/stock-web.git",
    )

    source = create_price_source(settings)

    assert isinstance(source, StockWebPriceSource)
    assert calls == [(tmp_path / "cache" / "stock-web", "https://example.test/stock-web.git")]


def test_price_factory_uses_mock_when_provider_is_mock_even_with_stock_web_path(
    tmp_path,
) -> None:
    stock_web_path = tmp_path / "stock-web"
    (stock_web_path / "atlas").mkdir(parents=True)
    settings = Settings(
        project_root=tmp_path,
        price_provider="mock",
        stock_web_path=stock_web_path,
    )

    source = create_price_source(settings)

    assert isinstance(source, MockPriceSource)


def test_price_factory_fails_when_stock_web_provider_has_no_source(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, price_provider="stock-web")

    with pytest.raises(ValueError, match="stock-web price provider is configured"):
        create_price_source(settings)
