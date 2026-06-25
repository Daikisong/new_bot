"""Web research interfaces with cutoff filtering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from news_scalping_lab.utils import KST, as_kst, now_kst, parse_datetime, sha256_text

BRAVE_NEWS_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/news/search"
DEFAULT_USER_AGENT = "news-scalping-lab/0.1 (+https://github.com/Daikisong/new_bot)"


@dataclass(frozen=True)
class WebSearchResult:
    source_id: str
    title: str
    url: str
    snippet: str
    published_at: datetime | None
    timestamp_precision: str | None = None


@dataclass(frozen=True)
class ParsedWebTimestamp:
    published_at: datetime | None
    precision: str | None


@dataclass(frozen=True)
class WebSearchExclusion:
    result: WebSearchResult
    reason: str


class WebResearchProvider(Protocol):
    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        """Return search results for the guard to timestamp-filter."""

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        """Open a cutoff-safe source."""

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        """Return whether a result is safe to use for a blind run."""


class HTTPResponse(Protocol):
    @property
    def text(self) -> str:
        """Response body text."""

    def json(self) -> Any:
        """JSON response body."""

    def raise_for_status(self) -> None:
        """Raise when the HTTP response is not successful."""


class AsyncHTTPClient(Protocol):
    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool | None = None,
        timeout: float | None = None,
    ) -> HTTPResponse:
        """GET a URL."""


class MockWebResearchProvider:
    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        source_id = f"WEB-{sha256_text(query)[:12]}"
        return [
            WebSearchResult(
                source_id=source_id,
                title=f"mock verification for {query[:60]}",
                url=f"mock://web/{source_id}",
                snippet="Mock source generated before cutoff for deterministic tests.",
                published_at=cutoff_at,
            )
        ]

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        return f"Mock page {url} opened at {now_kst().isoformat()} with cutoff {cutoff_at.isoformat()}."

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        return result.published_at is not None and result.published_at <= cutoff_at


class BraveSearchWebResearchProvider:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = BRAVE_NEWS_SEARCH_ENDPOINT,
        count: int = 10,
        country: str = "KR",
        search_lang: str = "ko",
        ui_lang: str = "ko-KR",
        freshness_days: int = 7,
        timeout: float = 15.0,
        client: AsyncHTTPClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Brave Search API key is required")
        self.api_key = api_key
        self.endpoint = endpoint
        self.count = max(1, min(count, 50))
        self.country = country
        self.search_lang = search_lang
        self.ui_lang = ui_lang
        self.freshness_days = max(0, freshness_days)
        self.timeout = timeout
        self.client = client

    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        params: dict[str, str | int] = {
            "q": query[:400],
            "count": self.count,
            "country": self.country,
            "search_lang": self.search_lang,
            "ui_lang": self.ui_lang,
            "safesearch": "moderate",
            "freshness": self._freshness(cutoff_at),
        }
        response = await self._get_json(self.endpoint, params=params)
        results: list[WebSearchResult] = []
        for index, item in enumerate(_brave_result_items(response), start=1):
            url = _string_value(item, "url") or _string_value(item, "link")
            if not url:
                continue
            title = _string_value(item, "title") or _string_value(item, "name") or url
            snippet = (
                _string_value(item, "description")
                or _string_value(item, "snippet")
                or _string_value(item, "content")
                or ""
            )
            timestamp = _published_at_from_result(item)
            source_id = stable_web_source_id("BRAVE", query, url, str(index))
            results.append(
                WebSearchResult(
                    source_id=source_id,
                    title=title,
                    url=url,
                    snippet=snippet,
                    published_at=timestamp.published_at,
                    timestamp_precision=timestamp.precision,
                )
            )
        return results

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        if not url.startswith(("http://", "https://")):
            return f"Unsupported URL scheme for live web open: {url}"
        text = await self._get_text(url)
        return text[:20_000]

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        return result.published_at is not None and result.published_at <= cutoff_at

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Cache-Control": "no-cache",
            "User-Agent": DEFAULT_USER_AGENT,
            "X-Subscription-Token": self.api_key,
        }

    def _freshness(self, cutoff_at: datetime) -> str:
        if self.freshness_days <= 0:
            return ""
        cutoff_day = as_kst(cutoff_at).date()
        start_day = cutoff_day - timedelta(days=self.freshness_days)
        return f"{start_day.isoformat()}to{cutoff_day.isoformat()}"

    async def _get_json(self, url: str, *, params: Mapping[str, str | int]) -> Any:
        if self.client is not None:
            client_response = await self.client.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            client_response.raise_for_status()
            return client_response.json()
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout) as client:
            http_response = await client.get(url, params=dict(params), headers=self._headers())
            http_response.raise_for_status()
            return http_response.json()

    async def _get_text(self, url: str) -> str:
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html,*/*"}
        if self.client is not None:
            client_response = await self.client.get(
                url,
                headers=headers,
                follow_redirects=True,
                timeout=self.timeout,
            )
            client_response.raise_for_status()
            return client_response.text
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout) as client:
            http_response = await client.get(url, headers=headers)
            http_response.raise_for_status()
            return http_response.text


class TemporalWebGuard:
    def __init__(self, provider: WebResearchProvider) -> None:
        self.provider = provider
        self.excluded_source_ids: list[str] = []
        self.excluded_sources: list[WebSearchExclusion] = []
        self._accepted_results_by_url: dict[str, WebSearchResult] = {}

    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        results = await self.provider.search(query, cutoff_at=cutoff_at)
        kept: list[WebSearchResult] = []
        for result in results:
            exclusion_reason = await self._exclusion_reason(result, cutoff_at=cutoff_at)
            if exclusion_reason is not None:
                self.excluded_source_ids.append(result.source_id)
                self.excluded_sources.append(
                    WebSearchExclusion(result=result, reason=exclusion_reason)
                )
                continue
            self._accepted_results_by_url[result.url] = result
            kept.append(result)
        return kept

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        result = self._accepted_results_by_url.get(url)
        if result is None:
            raise ValueError(
                f"Cannot open unverified web source before cutoff search acceptance: {url}"
            )
        exclusion_reason = await self._exclusion_reason(result, cutoff_at=cutoff_at)
        if exclusion_reason is not None:
            raise ValueError(
                f"Cannot open cutoff-unsafe web source {result.source_id}: {exclusion_reason}"
            )
        return await self.provider.open(url, cutoff_at=cutoff_at)

    async def _exclusion_reason(
        self,
        result: WebSearchResult,
        *,
        cutoff_at: datetime,
    ) -> str | None:
        if result.published_at is None:
            return "missing_published_at"
        if await self.provider.verify_timestamp(result, cutoff_at=cutoff_at):
            return None
        if result.published_at > cutoff_at:
            return "published_after_cutoff"
        return "timestamp_verification_failed"


def stable_web_source_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{sha256_text('|'.join(parts))[:16]}"


def _brave_result_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    direct_results = payload.get("results")
    if isinstance(direct_results, list):
        return [item for item in direct_results if isinstance(item, dict)]
    items: list[dict[str, Any]] = []
    for container_name in ("news", "web"):
        container = payload.get(container_name)
        if not isinstance(container, dict):
            continue
        nested = container.get("results")
        if isinstance(nested, list):
            items.extend(item for item in nested if isinstance(item, dict))
    return items


def _string_value(item: Mapping[str, Any], key: str) -> str | None:
    value = item.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _published_at_from_result(item: Mapping[str, Any]) -> ParsedWebTimestamp:
    for key in (
        "published_at",
        "published",
        "published_time",
        "date_published",
        "datePublished",
        "date",
        "page_age",
    ):
        value = _string_value(item, key)
        if value is None:
            continue
        parsed = _parse_web_datetime(value)
        if parsed.published_at is not None:
            return parsed
    age = _string_value(item, "age")
    if age is None:
        return ParsedWebTimestamp(published_at=None, precision=None)
    return _parse_web_datetime(age)


def _parse_web_datetime(value: str) -> ParsedWebTimestamp:
    cleaned = value.strip()
    if not cleaned:
        return ParsedWebTimestamp(published_at=None, precision=None)
    date_only = _parse_date_only(cleaned)
    if date_only is not None:
        return ParsedWebTimestamp(published_at=date_only, precision="date_only_end_of_day")
    iso_candidate = cleaned.replace("Z", "+00:00")
    try:
        parsed_iso = parse_datetime(iso_candidate)
        return ParsedWebTimestamp(published_at=as_kst(parsed_iso), precision="datetime")
    except ValueError:
        pass
    try:
        parsed_email = parsedate_to_datetime(cleaned)
        return ParsedWebTimestamp(published_at=as_kst(parsed_email), precision="datetime")
    except (TypeError, ValueError, IndexError):
        pass
    # Relative ages like "2 hours ago" are not cutoff-safe without a provider retrieval
    # timestamp from the same response, so leave them unverifiable.
    return ParsedWebTimestamp(published_at=None, precision=None)


def _parse_date_only(value: str) -> datetime | None:
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, pattern).replace(
                hour=23,
                minute=59,
                second=59,
                tzinfo=KST,
            )
        except ValueError:
            continue
    return None
