from datetime import datetime
from pathlib import Path

from news_scalping_lab.research_import.repair_source_evidence import (
    NEWS_TIMESTAMP_REPAIR_RULE,
    audit_rehydrated_news_source_timestamps,
    rehydrate_news_source_timestamps,
)
from news_scalping_lab.utils import KST, sha256_bytes, sha256_text


def test_rehydrates_and_independently_audits_crlf_csv_timestamp(
    tmp_path: Path,
) -> None:
    csv_bytes = (
        b"\xef\xbb\xbfpage,row,date,time,title,body\r\n"
        b"120,1,2018-01-03,08:58:41,Alpha   event,Body   text\r\n"
    )
    csv_path = tmp_path / "news.csv"
    csv_path.write_bytes(csv_bytes)
    expected_input_sha = sha256_bytes(csv_bytes.replace(b"\r\n", b"\n"))
    content_sha = sha256_text("Alpha event\nBody text")
    source = [
        {
            "source_id": "SRC-1",
            "source_type": "news_csv_row",
            "input_file": "news.csv",
            "input_sha256": expected_input_sha,
            "row_index": 1,
            "page": "120",
            "source_row_number": "1",
            "title": "Alpha event",
            "content_sha256": content_sha,
            "published_at": None,
            "time_verified": True,
            "available_before_cutoff": True,
        }
    ]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2018, 1, 3, 8, 59, 59, tzinfo=KST),
    )
    audit, overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
    )

    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert repaired[0]["published_at"] == "2018-01-03T08:58:41+09:00"
    assert repaired[0]["timestamp_repair_provenance"]["rule_id"] == NEWS_TIMESTAMP_REPAIR_RULE
    assert repaired[0]["timestamp_repair_provenance"]["input_hash_mode"] == "CRLF_TO_LF"
    assert audit["timestamp_repair_verified_count"] == 1
    assert audit["timestamp_repair_failure_count"] == 0
    assert overrides == {"SRC-1": "2018-01-03T08:58:41+09:00"}


def test_timestamp_audit_rejects_attestation_tampering(tmp_path: Path) -> None:
    csv_bytes = b"page,row,date,time,title,body\n1,1,2018-01-03,08:00:00,A,B\n"
    (tmp_path / "news.csv").write_bytes(csv_bytes)
    source = [
        {
            "source_id": "SRC-1",
            "source_type": "news_csv_row",
            "input_file": "news.csv",
            "input_sha256": sha256_bytes(csv_bytes),
            "row_index": 1,
            "title": "A",
            "content_sha256": sha256_text("A\nB"),
            "published_at": None,
        }
    ]
    repaired, _ = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2018, 1, 3, 8, 59, 59, tzinfo=KST),
    )
    repaired[0]["published_at"] = "2018-01-03T08:30:00+09:00"

    audit, overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
    )

    assert audit["timestamp_repair_failure_count"] == 1
    assert overrides == {}
