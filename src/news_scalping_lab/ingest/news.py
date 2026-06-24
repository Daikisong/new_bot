"""CSV news ingestion with cutoff-aware parsing."""

from __future__ import annotations

import csv
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from news_scalping_lab.contracts.models import NewsItem, Provenance
from news_scalping_lab.utils import KST, combine_kst, file_sha256, stable_id


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


def _detect_trade_date(rows: list[dict[str, str]]) -> date:
    counts: Counter[date] = Counter()
    for row in rows:
        value = row.get("date")
        if value:
            counts[date.fromisoformat(value.strip().strip('"'))] += 1
    if not counts:
        raise ValueError("CSV has no date column values")
    return counts.most_common(1)[0][0]


def load_news_csv(path: Path, trade_date: date | None = None) -> NewsBatch:
    resolved = path.resolve()
    with resolved.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]

    detected_trade_date = trade_date or _detect_trade_date(rows)
    content_hash = file_sha256(resolved)
    items: list[NewsItem] = []
    for index, row in enumerate(rows, start=1):
        row_date = date.fromisoformat(row.get("date", str(detected_trade_date)))
        row_time = row.get("time", "00:00:00")
        published_at = combine_kst(row_date, row_time)
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
                title=row.get("title", "").strip(),
                body=row.get("body", "").strip(),
                source_id=source_id,
                provenance=[provenance],
            )
        )
    return NewsBatch(
        path=resolved, sha256=content_hash, trade_date=detected_trade_date, items=items
    )


def import_news_csv(path: Path, raw_news_dir: Path, trade_date: date | None = None) -> NewsBatch:
    batch = load_news_csv(path, trade_date=trade_date)
    target = raw_news_dir / f"{batch.trade_date.isoformat()}_{batch.sha256[:8]}_{path.name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(path, target)
    return load_news_csv(target, trade_date=batch.trade_date)
