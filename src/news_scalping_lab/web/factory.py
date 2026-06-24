"""Web research provider factory."""

from __future__ import annotations

from news_scalping_lab.config import Settings
from news_scalping_lab.web.provider import MockWebResearchProvider, WebResearchProvider


def create_web_provider(settings: Settings) -> WebResearchProvider:
    if settings.web_provider == "mock":
        return MockWebResearchProvider()
    raise ValueError(f"unsupported web provider: {settings.web_provider}")
