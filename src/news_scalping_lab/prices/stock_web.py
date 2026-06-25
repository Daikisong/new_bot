"""Adapter seam for a local stock-web checkout.

This implementation inspects local tabular files conservatively. It does not assume
the upstream repository layout; if no compatible files are found, callers can use
the mock source while keeping the same interface.
"""

from __future__ import annotations

import csv
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from news_scalping_lab.contracts.models import OutcomeLabels
from news_scalping_lab.outcomes.labels import build_outcome_labels, unavailable_outcome
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.utils import read_json

PRIMARY_STOCK_WEB_REMOTE_URL = "https://github.com/Songdaiki/stock-web.git"


class GitRunner(Protocol):
    def __call__(self, args: list[str], cwd: Path | None) -> None: ...


class StockWebPriceSource:
    source_name = "stock-web"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.atlas_root = root / "atlas" if (root / "atlas").exists() else root

    def inspect_atlas_schema(self) -> dict[str, Any]:
        manifest_path = self.atlas_root / "manifest.json"
        schema_path = self.atlas_root / "schema.json"
        manifest = read_json(manifest_path) if manifest_path.exists() else {}
        schema = read_json(schema_path) if schema_path.exists() else {}
        return {
            "manifest_path": manifest_path.as_posix() if manifest_path.exists() else None,
            "schema_path": schema_path.as_posix() if schema_path.exists() else None,
            "source_name": manifest.get("source_name"),
            "source_repo_url": manifest.get("source_repo_url"),
            "price_adjustment_status": manifest.get("price_adjustment_status"),
            "calibration_shard_root": manifest.get("calibration_shard_root")
            or manifest.get("ohlcv_shard_root")
            or "atlas/ohlcv_tradable_by_symbol_year",
            "raw_shard_root": manifest.get("raw_shard_root") or "atlas/ohlcv_raw_by_symbol_year",
            "compat_shard_root": manifest.get("deprecated_or_compat_shard_root")
            or "atlas/ohlcv_min_by_symbol_year",
            "tradable_shard_columns": schema.get("tradable_shard_columns", {}),
            "raw_shard_columns": schema.get("raw_shard_columns", {}),
            "max_date": manifest.get("max_date"),
        }

    def inspect_schema(self) -> dict[str, list[str]]:
        schemas: dict[str, list[str]] = {}
        atlas_schema = self.inspect_atlas_schema()
        if atlas_schema["tradable_shard_columns"]:
            schemas[str(atlas_schema["calibration_shard_root"])] = list(
                atlas_schema["tradable_shard_columns"].keys()
            )
        if atlas_schema["raw_shard_columns"]:
            schemas[str(atlas_schema["raw_shard_root"])] = list(
                atlas_schema["raw_shard_columns"].keys()
            )
        for path in sorted(self.root.rglob("*.csv"))[:200]:
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.reader(handle)
                    header = next(reader, [])
                lowered = [column.strip().lower() for column in header]
                short_schema = {"d", "o", "h", "l", "c"} & set(lowered)
                long_schema = {
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                } & set(lowered)
                if short_schema or long_schema:
                    schemas[path.relative_to(self.root).as_posix()] = header
            except UnicodeDecodeError:
                continue
        return schemas

    def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
        records = [record for record in self._iter_records(ticker) if record.trade_date <= through]
        return sorted(records, key=lambda record: record.trade_date)

    def get_snapshot(self, ticker: str, *, as_of: date) -> PriceRecord | None:
        history = self.get_history(ticker, through=as_of)
        return history[-1] if history else None

    def get_outcome(self, ticker: str, *, trade_date: date) -> OutcomeLabels:
        all_records = sorted(self._iter_records(ticker), key=lambda record: record.trade_date)
        snapshot = next((record for record in all_records if record.trade_date == trade_date), None)
        previous_candidates = [record for record in all_records if record.trade_date < trade_date]
        previous = previous_candidates[-1] if previous_candidates else None
        if snapshot is None or previous is None or previous.close in (None, 0):
            return unavailable_outcome()
        return build_outcome_labels(
            previous_close=previous.close,
            open_price=snapshot.open,
            high=snapshot.high,
            low=snapshot.low,
            close=snapshot.close,
            volume=snapshot.volume,
            amount=snapshot.amount,
            listed_shares=snapshot.listed_shares,
            market_cap_previous_close=previous.market_cap,
        )

    def get_outcome_universe(self, *, trade_date: date) -> dict[str, OutcomeLabels]:
        universe: dict[str, OutcomeLabels] = {}
        for ticker in self._known_tickers():
            outcome = self.get_outcome(ticker, trade_date=trade_date)
            if outcome.flags == ["PRICE_UNAVAILABLE"]:
                continue
            universe[ticker] = outcome
        return universe

    def _iter_records(self, ticker: str) -> list[PriceRecord]:
        records: list[PriceRecord] = []
        paths = self._ticker_shard_paths(ticker)
        if not paths:
            paths = sorted(self.root.rglob(f"*{ticker}*.csv"))
        for path in paths:
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        parsed = self._row_to_record(ticker, row)
                        if parsed is not None:
                            records.append(parsed)
            except UnicodeDecodeError:
                continue
        return records

    def _row_to_record(self, ticker: str, row: dict[str, str]) -> PriceRecord | None:
        lowered = {_normalize_column_name(key): value for key, value in row.items()}
        aliases = self._column_aliases()
        date_value = _first_value(lowered, aliases["date"])
        if not date_value:
            return None
        try:
            trade_day = date.fromisoformat(date_value[:10])
        except ValueError:
            return None

        def number(field: str) -> float | None:
            keys = aliases[field]
            for key in keys:
                raw = lowered.get(key)
                if raw in (None, ""):
                    continue
                try:
                    value = str(raw).replace(",", "")
                    return float(value)
                except ValueError:
                    return None
            return None

        return PriceRecord(
            ticker=ticker,
            trade_date=trade_day,
            open=number("open"),
            high=number("high"),
            low=number("low"),
            close=number("close"),
            volume=number("volume"),
            amount=number("amount"),
            market_cap=number("market_cap"),
            listed_shares=number("listed_shares"),
        )

    def _ticker_shard_paths(self, ticker: str) -> list[Path]:
        normalized = _normalize_ticker(ticker)
        if normalized is None:
            return []
        years = self._available_years(normalized)
        prefix = normalized[:3]
        paths: list[Path] = []
        for root in self._preferred_shard_roots():
            for year in years:
                path = root / prefix / normalized / f"{year}.csv"
                if path.exists():
                    paths.append(path)
            if paths:
                return sorted(paths)
        return []

    def _known_tickers(self) -> list[str]:
        tickers: set[str] = set()
        profile_root = self.atlas_root / "symbol_profiles"
        if profile_root.exists():
            for path in profile_root.glob("*/*.json"):
                normalized = _normalize_ticker(path.stem)
                if normalized is not None:
                    tickers.add(normalized)
        for root in self._preferred_shard_roots():
            if not root.exists():
                continue
            for prefix_dir in root.iterdir():
                if not prefix_dir.is_dir():
                    continue
                for ticker_dir in prefix_dir.iterdir():
                    if not ticker_dir.is_dir():
                        continue
                    normalized = _normalize_ticker(ticker_dir.name)
                    if normalized is not None:
                        tickers.add(normalized)
        return sorted(tickers)

    def _available_years(self, ticker: str) -> list[int]:
        profile_path = self.atlas_root / "symbol_profiles" / ticker[:3] / f"{ticker}.json"
        if profile_path.exists():
            profile = read_json(profile_path)
            profile_years = profile.get("available_years", [])
            if isinstance(profile_years, list):
                return sorted(int(year) for year in profile_years)
        discovered_years: set[int] = set()
        for root in self._preferred_shard_roots():
            symbol_dir = root / ticker[:3] / ticker
            if not symbol_dir.exists():
                continue
            for path in symbol_dir.glob("*.csv"):
                try:
                    discovered_years.add(int(path.stem))
                except ValueError:
                    continue
        return sorted(discovered_years)

    def _preferred_shard_roots(self) -> list[Path]:
        schema = self.inspect_atlas_schema()
        relative_roots = [
            schema["calibration_shard_root"],
            schema["compat_shard_root"],
            schema["raw_shard_root"],
        ]
        roots: list[Path] = []
        for value in relative_roots:
            candidate = self.root / str(value)
            if not candidate.exists() and str(value).startswith("atlas/"):
                candidate = self.atlas_root / str(value).removeprefix("atlas/")
            if candidate.exists() and candidate not in roots:
                roots.append(candidate)
        return roots

    def _column_aliases(self) -> dict[str, list[str]]:
        aliases = {
            field: {_normalize_column_name(value) for value in values}
            for field, values in DEFAULT_COLUMN_ALIASES.items()
        }
        atlas_schema = self.inspect_atlas_schema()
        for key in ("tradable_shard_columns", "raw_shard_columns"):
            column_map = atlas_schema.get(key)
            if not isinstance(column_map, dict):
                continue
            for raw_key, raw_value in column_map.items():
                canonical = _canonical_price_field(raw_key, raw_value)
                if canonical is None:
                    continue
                aliases[canonical].add(_normalize_column_name(str(raw_key)))
                aliases[canonical].add(_normalize_column_name(str(raw_value)))
        return {field: sorted(values) for field, values in aliases.items()}


DEFAULT_COLUMN_ALIASES: dict[str, set[str]] = {
    "date": {"d", "date", "trade_date", "tradedate"},
    "open": {"o", "open", "open_price", "openprice"},
    "high": {"h", "high", "high_price", "highprice"},
    "low": {"l", "low", "low_price", "lowprice"},
    "close": {"c", "close", "close_price", "closeprice"},
    "volume": {"v", "volume", "vol"},
    "amount": {"a", "amount", "value", "trading_value"},
    "market_cap": {"mc", "market_cap", "marketcap", "marcap"},
    "listed_shares": {"s", "listed_shares", "listedshares", "stocks", "shares"},
}


def _normalize_column_name(value: str | None) -> str:
    if value is None:
        return ""
    return "".join(character for character in value.strip().lower() if character.isalnum())


def _canonical_price_field(raw_key: object, raw_value: object) -> str | None:
    candidates = [
        _normalize_column_name(str(raw_key)),
        _normalize_column_name(str(raw_value)),
    ]
    for field, aliases in DEFAULT_COLUMN_ALIASES.items():
        normalized_aliases = {_normalize_column_name(alias) for alias in aliases}
        if any(candidate in normalized_aliases for candidate in candidates):
            return field
    return None


def _first_value(row: dict[str, str], aliases: list[str]) -> str | None:
    for alias in aliases:
        value = row.get(alias)
        if value not in (None, ""):
            return value
    return None


def _normalize_ticker(ticker: str) -> str | None:
    digits = "".join(character for character in ticker if character.isdigit())
    if len(digits) != 6:
        return None
    return digits


def ensure_stock_web_cache(
    cache_path: Path,
    *,
    remote_url: str = PRIMARY_STOCK_WEB_REMOTE_URL,
    refresh: bool = False,
    runner: GitRunner | None = None,
) -> Path:
    runner = runner or _run_git
    if cache_path.exists() and any(cache_path.iterdir()) and not (cache_path / ".git").exists():
        return cache_path
    if (cache_path / ".git").exists():
        if refresh:
            runner(["git", "fetch", "--all", "--tags", "--prune"], cache_path)
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    runner(["git", "clone", "--depth", "1", remote_url, cache_path.as_posix()], None)
    return cache_path


def _run_git(args: list[str], cwd: Path | None) -> None:
    subprocess.run(args, cwd=cwd, check=True)
