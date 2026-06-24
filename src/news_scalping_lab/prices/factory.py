"""Price source selection."""

from __future__ import annotations

from news_scalping_lab.config import Settings
from news_scalping_lab.prices.base import PriceSource
from news_scalping_lab.prices.mock import MockPriceSource
from news_scalping_lab.prices.stock_web import StockWebPriceSource


def create_price_source(settings: Settings) -> PriceSource:
    if settings.stock_web_path is not None and settings.stock_web_path.exists():
        return StockWebPriceSource(settings.stock_web_path)
    return MockPriceSource()
