"""Small deterministic helpers used across the project."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed


def as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def is_available_as_of(available_from: datetime, cutoff_at: datetime) -> bool:
    return as_kst(available_from) <= as_kst(cutoff_at)


def combine_kst(day: date, value: str) -> datetime:
    hour, minute, second = (int(part) for part in value.split(":"))
    return datetime.combine(day, time(hour, minute, second), tzinfo=KST)


def next_calendar_day(day: date) -> date:
    return day + timedelta(days=1)


def next_trading_day(day: date) -> date:
    next_day = next_calendar_day(day)
    while next_day.weekday() >= 5:
        next_day = next_calendar_day(next_day)
    return next_day


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def slug(value: str, max_length: int = 80) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9가-힣_-]+", "-", lowered)
    lowered = re.sub(r"-+", "-", lowered).strip("-")
    return lowered[:max_length] or "item"


def stable_id(prefix: str, *parts: object, length: int = 12) -> str:
    joined = "|".join(str(part) for part in parts)
    return f"{prefix}-{sha256_text(joined)[:length]}"


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
