"""Price source protocol and blind access guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from news_scalping_lab.contracts.models import OutcomeLabels


class BlindPriceAccessError(RuntimeError):
    """Raised when blind inference attempts to read D-day price data."""


@dataclass(frozen=True)
class PriceRecord:
    ticker: str
    trade_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    amount: float | None = None
    market_cap: float | None = None
    listed_shares: float | None = None


class PriceSource(Protocol):
    source_name: str

    def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
        """Return history up to and including ``through``."""

    def get_snapshot(self, ticker: str, *, as_of: date) -> PriceRecord | None:
        """Return a snapshot up to ``as_of``."""

    def get_outcome(self, ticker: str, *, trade_date: date) -> OutcomeLabels:
        """Return D-day outcome labels for evaluation only."""


@runtime_checkable
class OutcomeUniversePriceSource(Protocol):
    def get_outcome_universe(self, *, trade_date: date) -> dict[str, OutcomeLabels]:
        """Return D-day outcome labels for the full tradable universe when available."""


class BlindPriceGuard:
    def __init__(self, source: PriceSource, *, trade_date: date) -> None:
        self.source = source
        self.trade_date = trade_date

    @property
    def source_name(self) -> str:
        return self.source.source_name

    def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
        if through >= self.trade_date:
            raise BlindPriceAccessError(
                "blind inference cannot access D-day or later price history"
            )
        return self.source.get_history(ticker, through=through)

    def get_snapshot(self, ticker: str, *, as_of: date) -> PriceRecord | None:
        if as_of >= self.trade_date:
            raise BlindPriceAccessError(
                "blind inference cannot access D-day or later price snapshot"
            )
        return self.source.get_snapshot(ticker, as_of=as_of)

    def get_outcome(self, ticker: str, *, trade_date: date) -> OutcomeLabels:
        raise BlindPriceAccessError("blind inference cannot access outcome labels")
