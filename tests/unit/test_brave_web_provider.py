from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.utils import KST
from news_scalping_lab.web.factory import create_web_provider
from news_scalping_lab.web.provider import (
    BRAVE_NEWS_SEARCH_ENDPOINT,
    BraveSearchWebResearchProvider,
    TemporalWebGuard,
)


@dataclass
class FakeHTTPResponse:
    payload: Any
    body: str = ""

    @property
    def text(self) -> str:
        return self.body

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class FakeHTTPClient:
    def __init__(self, payload: Any, body: str = "<html>opened source</html>") -> None:
        self.payload = payload
        self.body = body
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool | None = None,
        timeout: float | None = None,
    ) -> FakeHTTPResponse:
        self.calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "follow_redirects": follow_redirects,
                "timeout": timeout,
            }
        )
        if params is None:
            return FakeHTTPResponse({}, body=self.body)
        return FakeHTTPResponse(self.payload)


@pytest.mark.asyncio
async def test_brave_search_provider_maps_results_and_temporal_guard_excludes_unverified() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    client = FakeHTTPClient(
        {
            "results": [
                {
                    "title": "cutoff-safe source",
                    "url": "https://example.test/safe",
                    "description": "Published before cutoff.",
                    "published_at": "2030-01-10T08:30:00+09:00",
                },
                {
                    "title": "missing timestamp",
                    "url": "https://example.test/missing",
                    "description": "No publication time should be excluded.",
                },
            ]
        }
    )
    provider = BraveSearchWebResearchProvider(
        api_key="brave-key",
        count=12,
        country="KR",
        search_lang="ko",
        ui_lang="ko-KR",
        freshness_days=3,
        client=client,
    )
    guard = TemporalWebGuard(provider)

    kept = await guard.search("market catalyst", cutoff_at=cutoff)
    opened = await guard.open(kept[0].url, cutoff_at=cutoff)

    assert [result.title for result in kept] == ["cutoff-safe source"]
    assert guard.excluded_source_ids
    assert guard.excluded_sources[0].reason == "missing_published_at"
    assert opened == "<html>opened source</html>"
    search_call = client.calls[0]
    assert search_call["url"] == BRAVE_NEWS_SEARCH_ENDPOINT
    assert search_call["headers"]["X-Subscription-Token"] == "brave-key"
    assert search_call["params"] == {
        "q": "market catalyst",
        "count": 12,
        "country": "KR",
        "search_lang": "ko",
        "ui_lang": "ko-KR",
        "safesearch": "moderate",
        "freshness": "2030-01-07to2030-01-10",
    }


@pytest.mark.asyncio
async def test_brave_search_provider_reads_nested_web_results() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    client = FakeHTTPClient(
        {
            "web": {
                "results": [
                    {
                        "name": "nested web source",
                        "url": "https://example.test/web",
                        "snippet": "Nested response format.",
                        "date": "2030-01-09",
                    }
                ]
            }
        }
    )
    provider = BraveSearchWebResearchProvider(api_key="brave-key", client=client)

    results = await provider.search("nested", cutoff_at=cutoff)

    assert len(results) == 1
    assert results[0].title == "nested web source"
    assert results[0].published_at == datetime(2030, 1, 9, 0, 0, 0, tzinfo=KST)


def test_create_web_provider_selects_brave_when_api_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(web_provider="brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret")

    provider = create_web_provider(settings)

    assert isinstance(provider, BraveSearchWebResearchProvider)


def test_create_web_provider_rejects_brave_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(web_provider="brave")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    with pytest.raises(ValueError, match="BRAVE_SEARCH_API_KEY must be set"):
        create_web_provider(settings)
