"""Web research provider factory."""

from __future__ import annotations

import os

from news_scalping_lab.config import Settings
from news_scalping_lab.web.provider import (
    BraveSearchWebResearchProvider,
    MockWebResearchProvider,
    WebResearchProvider,
)


def create_web_provider(settings: Settings) -> WebResearchProvider:
    provider = settings.web_provider.strip().lower()
    if provider == "mock":
        return MockWebResearchProvider()
    if provider in {"brave", "brave-search", "brave-news"}:
        api_key = os.getenv(settings.brave_search_api_key_env)
        if not api_key:
            raise ValueError(
                f"{settings.brave_search_api_key_env} must be set for "
                f"NSLAB_WEB_PROVIDER={settings.web_provider}"
            )
        return BraveSearchWebResearchProvider(
            api_key=api_key,
            count=settings.brave_search_count,
            country=settings.brave_search_country,
            search_lang=settings.brave_search_lang,
            ui_lang=settings.brave_search_ui_lang,
            freshness_days=settings.brave_search_freshness_days,
        )
    raise ValueError(f"unsupported web provider: {settings.web_provider}")
