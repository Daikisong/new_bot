from __future__ import annotations

from datetime import date, datetime

import pytest

from news_scalping_lab.ingest.news import import_news_csv, load_news_csv
from news_scalping_lab.utils import KST, default_news_window_start


def test_load_news_csv_detects_trade_date_as_latest_date_in_preopen_window(tmp_path) -> None:
    csv_path = tmp_path / "mixed_preopen.csv"
    csv_path.write_text(
        "\n".join(
            [
                "page,row,date,time,title,body",
                '1,1,"2030-01-09","15:30:00","Previous session close","older news"',
                '1,2,"2030-01-09","16:00:00","After close update","older news"',
                '1,3,"2030-01-10","08:59:00","Pre-open catalyst","current news"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    batch = load_news_csv(csv_path)

    assert batch.trade_date == date(2030, 1, 10)
    assert [item.published_at.date() for item in batch.items] == [
        date(2030, 1, 9),
        date(2030, 1, 9),
        date(2030, 1, 10),
    ]


def test_load_news_csv_parses_collected_at_and_filters_default_news_window(tmp_path) -> None:
    csv_path = tmp_path / "collected.csv"
    csv_path.write_text(
        "\n".join(
            [
                "page,row,date,time,collected_at,title,body",
                '1,1,"2030-01-09","14:59:00","2030-01-09T15:00:00+09:00","Too old","before window"',
                '1,2,"2030-01-09","15:30:00","2030-01-09T15:30:05+09:00","Window start","included"',
                '1,3,"2030-01-10","09:00:00","2030-01-10T09:00:05+09:00","After cutoff","excluded"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)

    batch = load_news_csv(csv_path, trade_date=date(2030, 1, 10))
    windowed = batch.within_window(default_news_window_start(batch.trade_date), cutoff)

    assert batch.items[0].collected_at == datetime(2030, 1, 9, 15, 0, 0, tzinfo=KST)
    assert [item.row_number for item in windowed.items] == [2]


def test_load_news_csv_parses_split_collected_date_time(tmp_path) -> None:
    csv_path = tmp_path / "split_collected.csv"
    csv_path.write_text(
        "\n".join(
            [
                "page,row,date,time,collected_date,collected_time,title,body",
                '1,1,"2030-01-10","08:30:00","2030-01-10","08:31:02","Split collected","body"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    batch = load_news_csv(csv_path)

    assert batch.items[0].collected_at == datetime(2030, 1, 10, 8, 31, 2, tzinfo=KST)


def test_news_import_uses_detected_latest_trade_date_in_raw_filename(tmp_path) -> None:
    csv_path = tmp_path / "mixed_preopen.csv"
    csv_path.write_text(
        "\n".join(
            [
                "page,row,date,time,title,body",
                '1,1,"2030-01-09","15:30:00","Previous session close","older news"',
                '1,2,"2030-01-10","08:59:00","Pre-open catalyst","current news"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    batch = import_news_csv(csv_path, tmp_path / "raw_news")

    assert batch.trade_date == date(2030, 1, 10)
    assert batch.path.name.startswith("2030-01-10_")


def test_load_news_csv_accepts_cp949_korean_input(tmp_path) -> None:
    csv_path = tmp_path / "korean_cp949.csv"
    text = "\n".join(
        [
            "page,row,date,time,title,body",
            '1,1,"2030-01-10","08:30:00","가상회사, 신규 시설 검토","한국어 본문이 깨지지 않아야 한다"',
        ]
    )
    csv_path.write_bytes(text.encode("cp949"))

    batch = load_news_csv(csv_path)

    assert batch.trade_date == date(2030, 1, 10)
    assert batch.items[0].title == "가상회사, 신규 시설 검토"
    assert batch.items[0].body == "한국어 본문이 깨지지 않아야 한다"


def test_load_news_csv_rejects_missing_required_columns(tmp_path) -> None:
    csv_path = tmp_path / "missing_required.csv"
    csv_path.write_text(
        "\n".join(
            [
                "page,row,date,body",
                '1,1,"2030-01-10","body without time and title"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="CSV missing required columns: time, title"):
        load_news_csv(csv_path)


def test_load_news_csv_rejects_blank_required_values_with_row_number(tmp_path) -> None:
    csv_path = tmp_path / "blank_required.csv"
    csv_path.write_text(
        "\n".join(
            [
                "page,row,date,time,title,body",
                '1,1,"2030-01-10","08:30:00","Valid title","body"',
                '1,2,"2030-01-10","08:31:00","","blank title"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="CSV row 2 missing required title"):
        load_news_csv(csv_path)


def test_load_news_csv_allows_missing_optional_body_column(tmp_path) -> None:
    csv_path = tmp_path / "title_only.csv"
    csv_path.write_text(
        "\n".join(
            [
                "page,row,date,time,title",
                '1,1,"2030-01-10","08:30:00","Title-only catalyst"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    batch = load_news_csv(csv_path)

    assert batch.trade_date == date(2030, 1, 10)
    assert batch.items[0].title == "Title-only catalyst"
    assert batch.items[0].body == ""
