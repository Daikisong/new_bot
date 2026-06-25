from __future__ import annotations

from datetime import date

from news_scalping_lab.ingest.news import import_news_csv, load_news_csv


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
