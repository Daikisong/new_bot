"""Web research interfaces with cutoff filtering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from news_scalping_lab.utils import now_kst, sha256_text


@dataclass(frozen=True)
class WebSearchResult:
    source_id: str
    title: str
    url: str
    snippet: str
    published_at: datetime | None


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


class TemporalWebGuard:
    def __init__(self, provider: WebResearchProvider) -> None:
        self.provider = provider
        self.excluded_source_ids: list[str] = []
        self.excluded_sources: list[WebSearchExclusion] = []

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
            kept.append(result)
        return kept

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
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
