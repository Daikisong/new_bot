"""CSV news ingestion with cutoff-aware parsing."""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.contracts.models import NewsItem, Provenance
from news_scalping_lab.utils import KST, combine_kst, file_sha256, parse_datetime, stable_id

NEWS_CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")


@dataclass(frozen=True)
class NewsBatch:
    path: Path
    sha256: str
    trade_date: date
    items: list[NewsItem]

    @property
    def row_count(self) -> int:
        return len(self.items)

    def before_or_at(self, cutoff_at: datetime) -> NewsBatch:
        kept = [item for item in self.items if item.published_at <= cutoff_at]
        return NewsBatch(path=self.path, sha256=self.sha256, trade_date=self.trade_date, items=kept)

    def within_window(self, start_at: datetime, end_at: datetime) -> NewsBatch:
        kept = [item for item in self.items if start_at <= item.published_at <= end_at]
        return NewsBatch(path=self.path, sha256=self.sha256, trade_date=self.trade_date, items=kept)


def _detect_trade_date(rows: list[dict[str, str]]) -> date:
    observed_dates: list[date] = []
    for row in rows:
        value = row.get("date")
        if value:
            observed_dates.append(date.fromisoformat(value.strip().strip('"')))
    if not observed_dates:
        raise ValueError("CSV has no date column values")
    return max(observed_dates)


def load_news_csv(path: Path, trade_date: date | None = None) -> NewsBatch:
    resolved = path.resolve()
    rows = _read_news_csv_rows(resolved)

    detected_trade_date = trade_date or _detect_trade_date(rows)
    content_hash = file_sha256(resolved)
    items: list[NewsItem] = []
    for index, row in enumerate(rows, start=1):
        row_date = date.fromisoformat(row.get("date", str(detected_trade_date)))
        row_time = row.get("time", "00:00:00")
        published_at = combine_kst(row_date, row_time)
        collected_at = _parse_collected_at(row)
        source_id = stable_id("SRC", resolved.as_posix(), index, content_hash)
        event_id = stable_id(
            "EVT", row.get("title", ""), row.get("body", ""), published_at.isoformat()
        )
        provenance = Provenance(
            source_id=source_id,
            source_type="news_csv_row",
            uri=f"{resolved.as_posix()}#row={index}",
            content_sha256=content_hash,
            excerpt=row.get("title", ""),
            observed_at=datetime.now(tz=KST),
        )
        items.append(
            NewsItem(
                event_id=event_id,
                row_number=index,
                published_at=published_at,
                collected_at=collected_at,
                title=row.get("title", "").strip(),
                body=row.get("body", "").strip(),
                source_id=source_id,
                provenance=[provenance],
            )
        )
    return NewsBatch(
        path=resolved, sha256=content_hash, trade_date=detected_trade_date, items=items
    )


def _read_news_csv_rows(path: Path) -> list[dict[str, str]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in NEWS_CSV_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                return [dict(row) for row in reader]
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise ValueError(
            "could not decode news CSV with supported encodings: "
            + ", ".join(NEWS_CSV_ENCODINGS)
        ) from last_error
    return []


def _parse_collected_at(row: dict[str, str]) -> datetime | None:
    raw = _first_present(
        row,
        "collected_at",
        "collected_datetime",
        "retrieved_at",
        "crawl_at",
        "crawled_at",
        "scraped_at",
    )
    if raw is not None:
        return parse_datetime(raw)
    collected_date = _first_present(row, "collected_date", "retrieved_date", "crawl_date")
    if collected_date is None:
        return None
    collected_time = _first_present(row, "collected_time", "retrieved_time", "crawl_time")
    return combine_kst(date.fromisoformat(collected_date), collected_time or "00:00:00")


def _first_present(row: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        cleaned = value.strip().strip('"')
        if cleaned:
            return cleaned
    return None


def import_news_csv(path: Path, raw_news_dir: Path, trade_date: date | None = None) -> NewsBatch:
    batch = load_news_csv(path, trade_date=trade_date)
    target = raw_news_dir / f"{batch.trade_date.isoformat()}_{batch.sha256[:8]}_{path.name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(path, target)
    return load_news_csv(target, trade_date=batch.trade_date)
