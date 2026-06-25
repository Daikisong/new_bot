"""CSV news ingestion with cutoff-aware parsing."""

from __future__ import annotations

import csv
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.contracts.models import NewsItem, Provenance
from news_scalping_lab.utils import KST, combine_kst, file_sha256, parse_datetime, stable_id

NEWS_CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")
REQUIRED_NEWS_COLUMNS = ("date", "time", "title")


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
    for row_number, row in enumerate(rows, start=1):
        value = _required_cell(row, "date", row_number)
        observed_dates.append(_parse_row_date(value, row_number=row_number, column="date"))
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
        raw_date = _required_cell(row, "date", index)
        row_date = _parse_row_date(raw_date, row_number=index, column="date")
        row_time = _required_cell(row, "time", index)
        published_at = _parse_row_datetime(row_date, row_time, row_number=index)
        title = _required_cell(row, "title", index)
        body = _optional_cell(row, "body")
        collected_at = _parse_collected_at(row)
        source_id = stable_id("SRC", resolved.as_posix(), index, content_hash)
        event_id = stable_id(
            "EVT", title, body, published_at.isoformat()
        )
        provenance = Provenance(
            source_id=source_id,
            source_type="news_csv_row",
            uri=f"{resolved.as_posix()}#row={index}",
            content_sha256=content_hash,
            excerpt=title,
            observed_at=datetime.now(tz=KST),
        )
        items.append(
            NewsItem(
                event_id=event_id,
                row_number=index,
                published_at=published_at,
                collected_at=collected_at,
                title=title,
                body=body,
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
                _validate_news_csv_columns(reader.fieldnames)
                return [dict(row) for row in reader]
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise ValueError(
            "could not decode news CSV with supported encodings: "
            + ", ".join(NEWS_CSV_ENCODINGS)
        ) from last_error
    return []


def _validate_news_csv_columns(fieldnames: Sequence[str] | None) -> None:
    observed = set(fieldnames or [])
    missing = [column for column in REQUIRED_NEWS_COLUMNS if column not in observed]
    if missing:
        raise ValueError("CSV missing required columns: " + ", ".join(missing))


def _required_cell(row: dict[str, str], column: str, row_number: int) -> str:
    value = row.get(column)
    cleaned = value.strip().strip('"') if value is not None else ""
    if not cleaned:
        raise ValueError(f"CSV row {row_number} missing required {column}")
    return cleaned


def _optional_cell(row: dict[str, str], column: str) -> str:
    value = row.get(column)
    return value.strip().strip('"') if value is not None else ""


def _parse_row_date(value: str, *, row_number: int, column: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"CSV row {row_number} has invalid {column}: {value}") from exc


def _parse_row_datetime(row_date: date, row_time: str, *, row_number: int) -> datetime:
    try:
        return combine_kst(row_date, row_time)
    except ValueError as exc:
        raise ValueError(f"CSV row {row_number} has invalid time: {row_time}") from exc


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
