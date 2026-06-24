from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.prices.stock_web import StockWebPriceSource, ensure_stock_web_cache


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
        stock_web_cache_enabled=True,
        stock_web_cache_path=Path("cache/stock-web"),
        stock_web_remote_url="https://example.test/stock-web.git",
    )

    source = create_price_source(settings)

    assert isinstance(source, StockWebPriceSource)
    assert calls == [(tmp_path / "cache" / "stock-web", "https://example.test/stock-web.git")]
