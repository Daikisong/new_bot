from __future__ import annotations

from datetime import date

from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.warehouse import WarehouseStore


def test_warehouse_writes_empty_projection_as_zero_rows(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    counts = WarehouseStore(tmp_path).rebuild_all()
    inspected = WarehouseStore(tmp_path).counts()

    assert counts["research_episodes"] == 0
    assert inspected["research_episodes.parquet"] == 0
    assert inspected["events.parquet"] == 0


def test_previous_trade_day_is_calendar_previous_day() -> None:
    from news_scalping_lab.warehouse import previous_trade_day

    assert previous_trade_day(date(2030, 1, 10)) == date(2030, 1, 9)
