"""Price source selection."""

from __future__ import annotations

from news_scalping_lab.config import Settings
from news_scalping_lab.prices.base import PriceSource
from news_scalping_lab.prices.mock import MockPriceSource
from news_scalping_lab.prices.stock_web import StockWebPriceSource, ensure_stock_web_cache


def create_price_source(settings: Settings) -> PriceSource:
    provider = settings.price_provider.strip().lower()
    if provider == "mock":
        return MockPriceSource()
    if provider not in {"stock-web", "stock_web", "stockweb"}:
        raise ValueError(f"unsupported price provider: {settings.price_provider}")
    if settings.stock_web_path is not None:
        stock_web_path = settings.path(settings.stock_web_path)
        if stock_web_path.exists():
            return StockWebPriceSource(stock_web_path)
    if settings.stock_web_cache_enabled:
        cache_path = ensure_stock_web_cache(
            settings.path(settings.stock_web_cache_path),
            remote_url=settings.stock_web_remote_url,
        )
        if cache_path.exists():
            return StockWebPriceSource(cache_path)
    details = "set NSLAB_STOCK_WEB_PATH or enable NSLAB_STOCK_WEB_CACHE=1"
    raise ValueError(f"stock-web price provider is configured but unavailable; {details}")
