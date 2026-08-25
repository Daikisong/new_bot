from datetime import datetime
from pathlib import Path

from news_scalping_lab.research_import.repair_source_evidence import (
    NEWS_TIMESTAMP_REPAIR_RULE,
    audit_rehydrated_news_source_timestamps,
    rehydrate_news_source_timestamps,
)
from news_scalping_lab.utils import KST, canonical_json, sha256_bytes, sha256_text


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
            "source_row_id": "SRC-1",
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
        cutoff_at=datetime(2020, 3, 27, 8, 59, 59, tzinfo=KST),
    )

    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert repaired[0]["published_at"] == "2018-01-03T08:58:41+09:00"
    assert repaired[0]["timestamp_repair_provenance"]["rule_id"] == NEWS_TIMESTAMP_REPAIR_RULE
    assert repaired[0]["timestamp_repair_provenance"]["input_hash_mode"] == "CRLF_TO_LF"
    assert repaired[0]["timestamp_repair_provenance"]["evidence_file"] == "news.csv"
    assert repaired[0]["timestamp_repair_provenance"]["evidence_resolution"] == (
        "DECLARED_FILENAME"
    )
    assert audit["timestamp_repair_verified_count"] == 1
    assert audit["timestamp_repair_failure_count"] == 0
    assert overrides == {"SRC-1": "2018-01-03T08:58:41+09:00"}


def test_verifies_csv_ordinal_without_treating_page_row_as_global_index(
    tmp_path: Path,
) -> None:
    csv_bytes = (
        b"\xef\xbb\xbfpage,row,date,time,title,body\r\n"
        b"128,6,2023-11-28,08:57:00,Earlier,Earlier body\r\n"
        b"129,7,2023-11-28,08:58:41,Alpha event,Complete body   \r\n"
    )
    (tmp_path / "news_20231128.csv").write_bytes(csv_bytes)
    input_sha = sha256_bytes(csv_bytes.replace(b"\r\n", b"\n"))
    source = [
        {
            "source_id": "SRC-000001",
            "source_type": "news_csv_row",
            "input_file": "news_20231128.csv",
            "input_sha256": input_sha,
            "csv_ordinal": 2,
            "page": 129,
            "source_row_number": 7,
            "title": "Alpha event",
            "body": "Complete body",
            "content_sha256": sha256_text("Alpha event\0Complete body"),
            "published_at": "2023-11-28T08:58:41+09:00",
        }
    ]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2023, 11, 28, 8, 59, 59, tzinfo=KST),
    )
    audit, overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2023, 11, 28, 8, 59, 59, tzinfo=KST),
    )

    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert repaired[0]["time_verified"] is True
    assert repaired[0]["timestamp_repair_provenance"]["row_index"] == 2
    assert repaired[0]["timestamp_repair_provenance"]["content_sha256"] == (
        source[0]["content_sha256"]
    )
    assert audit["timestamp_repair_failure_count"] == 0
    assert overrides == {"SRC-000001": "2023-11-28T08:58:41+09:00"}


def test_verifies_csv_row_number_before_page_local_source_row(tmp_path: Path) -> None:
    csv_bytes = (
        b"date,time,title,body,page,row\n"
        b"2024-03-20,08:59:43,First,First body,287,1\n"
        b"2024-03-20,08:57:14,Second,Second body,288,1\n"
    )
    csv_path = tmp_path / "news_20240320.csv"
    csv_path.write_bytes(csv_bytes)
    source = [
        {
            "source_id": "SRC-NEWS-000002",
            "source_type": "news_csv_row",
            "input_file": csv_path.name,
            "input_sha256": sha256_bytes(csv_bytes),
            "csv_row_number": 2,
            "source_row": "1",
            "page": "288",
            "title": "Second",
            "body": "Second body",
            "published_at_kst": "2024-03-20T08:57:14+09:00",
        }
    ]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=None,
    )

    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert repaired[0]["timestamp_repair_provenance"]["row_index"] == 2


def test_bundle_csv_identity_and_input_row_rehydrate_legacy_source(
    tmp_path: Path,
) -> None:
    csv_bytes = (
        b"date,time,title,body,page,row\n"
        b"2024-05-09,08:59:40,First event,First body,171,1\n"
        b"2024-05-09,08:56:57,Issuer event,Exact body,172,1\n"
    )
    evidence_path = tmp_path / "news_20240509.csv"
    evidence_path.write_bytes(csv_bytes)
    input_sha = sha256_bytes(csv_bytes)
    source = [
        {
            "source_id": "SRC-NEWS-000002",
            "source_type": "news_csv_row",
            "row_id": "NEWS-000002",
            "input_row": "1",
            "page": "172",
            "title": "Issuer event",
            "body": "Exact body",
            "published_at": "2024-05-09T08:56:57+09:00",
        }
    ]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2024, 5, 9, 8, 59, 59, tzinfo=KST),
        declared_input_file="acquired_news_20240509.csv",
        declared_input_sha256=input_sha,
    )
    audit, overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2024, 5, 9, 8, 59, 59, tzinfo=KST),
        declared_input_file="acquired_news_20240509.csv",
        declared_input_sha256=input_sha,
    )

    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert repaired[0]["timestamp_repair_provenance"]["row_index"] == 2
    assert repaired[0]["timestamp_repair_provenance"]["evidence_resolution"] == (
        "CONTENT_SHA256"
    )
    assert audit["timestamp_repair_failure_count"] == 0
    assert overrides == {"SRC-NEWS-000002": "2024-05-09T08:56:57+09:00"}


def test_csv_ordinal_join_rejects_identity_anchor_tampering(tmp_path: Path) -> None:
    csv_bytes = (
        b"page,row,date,time,title,body\n"
        b"129,7,2023-11-28,08:58:41,Alpha event,Complete body\n"
    )
    (tmp_path / "news.csv").write_bytes(csv_bytes)
    base_source = {
        "source_id": "SRC-1",
        "source_type": "news_csv_row",
        "input_file": "news.csv",
        "input_sha256": sha256_bytes(csv_bytes),
        "csv_ordinal": 1,
        "page": 129,
        "source_row_number": 7,
        "title": "Alpha event",
        "body": "Complete body",
        "content_sha256": sha256_text("Alpha event\0Complete body"),
        "published_at": "2023-11-28T08:58:41+09:00",
    }

    for field, tampered_value in (
        ("csv_ordinal", 2),
        ("page", 130),
        ("source_row_number", 8),
        ("content_sha256", "0" * 64),
        ("published_at", "2023-11-28T08:58:42+09:00"),
    ):
        source = [{**base_source, field: tampered_value}]
        repaired, summary = rehydrate_news_source_timestamps(
            source,
            news_csv_root=tmp_path,
            cutoff_at=datetime(2023, 11, 28, 8, 59, 59, tzinfo=KST),
        )

        assert summary["timestamp_repair_verified_count"] == 0, field
        assert summary["timestamp_repair_failure_count"] == 1, field
        assert repaired == source


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


def test_verifies_existing_timestamp_by_content_addressed_csv(
    tmp_path: Path,
) -> None:
    csv_bytes = (
        b"page,row,date,time,title,body\n"
        b"390,1,2020-03-27,08:59:35,Alpha event,Body text\n"
    )
    local_csv = tmp_path / "news_20200327.csv"
    local_csv.write_bytes(csv_bytes)
    csv_row = {
        "page": "390",
        "row": "1",
        "date": "2020-03-27",
        "time": "08:59:35",
        "title": "Alpha event",
        "body": "Body text",
    }
    source = [
        {
            "source_id": "SRC-NEWS-000001",
            "source_type": "NEWS_ROW",
            "input_file": "news_20200327_generated_name.csv",
            "input_sha256": sha256_bytes(csv_bytes),
            "row_index": 1,
            "page": "390",
            "page_row": "1",
            "title": "Alpha event",
            "published_at": "2020-03-27T08:59:35+09:00",
            "raw_row_sha256": sha256_text(canonical_json(csv_row)),
        }
    ]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2020, 3, 27, 8, 59, 59, tzinfo=KST),
    )
    audit, overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
    )

    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert repaired[0]["published_at"] == source[0]["published_at"]
    assert repaired[0]["time_verified"] is True
    assert repaired[0]["available_before_cutoff"] is True
    provenance = repaired[0]["timestamp_repair_provenance"]
    assert provenance["evidence_file"] == "news_20200327.csv"
    assert provenance["evidence_resolution"] == "CONTENT_SHA256"
    assert provenance["input_hash_mode"] == "RAW_BYTES"
    assert audit["timestamp_repair_changed_count"] == 1
    assert audit["timestamp_repair_verified_count"] == 1
    assert audit["timestamp_repair_failure_count"] == 0
    assert overrides == {"SRC-NEWS-000001": "2020-03-27T08:59:35+09:00"}

    repaired[0]["available_before_cutoff"] = False
    tampered_audit, tampered_overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2020, 3, 27, 8, 59, 59, tzinfo=KST),
    )
    assert tampered_audit["timestamp_repair_failure_count"] == 1
    assert tampered_overrides == {}


def test_existing_timestamp_verification_rejects_row_hash_mismatch(
    tmp_path: Path,
) -> None:
    csv_bytes = b"page,row,date,time,title,body\n1,1,2020-03-27,08:00:00,A,B\n"
    (tmp_path / "local.csv").write_bytes(csv_bytes)
    source = [
        {
            "source_id": "SRC-1",
            "source_type": "NEWS_ROW",
            "input_file": "missing-generated-name.csv",
            "input_sha256": sha256_bytes(csv_bytes),
            "row_index": 1,
            "title": "A",
            "published_at": "2020-03-27T08:00:00+09:00",
            "raw_row_sha256": "0" * 64,
        }
    ]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2020, 3, 27, 8, 59, 59, tzinfo=KST),
    )

    assert summary["timestamp_repair_verified_count"] == 0
    assert summary["timestamp_repair_failure_count"] == 1
    assert repaired == source


def test_verifies_legacy_rows_from_bundle_csv_declaration_and_full_body(
    tmp_path: Path,
) -> None:
    csv_bytes = (
        b"page,row,date,time,title,body\r\n"
        b"285,1,2020-04-10,08:59:46,Alpha event,Complete body\r\n"
    )
    (tmp_path / "news_20200410.csv").write_bytes(csv_bytes)
    expected_input_sha = sha256_bytes(csv_bytes.replace(b"\r\n", b"\n"))
    source = [
        {
            "source_id": "SRC-NEWS-CSV",
            "source_type": "news_csv",
            "path": "/mnt/data/generated_news_20200410.csv",
            "sha256": expected_input_sha,
        },
        {
            "source_id": "SRC-NEWS-000001",
            "source_type": "news_row",
            "input_sha256": expected_input_sha,
            "csv_position": 1,
            "source_row": "1",
            "page": "285",
            "title": "Alpha event",
            "body": "Complete body",
            "published_at": "2020-04-10T08:59:46+09:00",
        },
    ]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2020, 4, 10, 8, 59, 59, tzinfo=KST),
    )
    audit, overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2020, 4, 10, 8, 59, 59, tzinfo=KST),
    )

    news_row = repaired[1]
    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert news_row["time_verified"] is True
    assert news_row["available_before_cutoff"] is True
    assert news_row["timestamp_repair_provenance"]["input_file"] == (
        "generated_news_20200410.csv"
    )
    assert news_row["timestamp_repair_provenance"]["evidence_file"] == (
        "news_20200410.csv"
    )
    assert news_row["timestamp_repair_provenance"]["evidence_resolution"] == (
        "CONTENT_SHA256"
    )
    assert audit["timestamp_repair_failure_count"] == 0
    assert overrides == {"SRC-NEWS-000001": "2020-04-10T08:59:46+09:00"}

    tampered = [dict(row) for row in source]
    tampered[1]["body"] = "Different body"
    rejected, rejected_summary = rehydrate_news_source_timestamps(
        tampered,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2020, 4, 10, 8, 59, 59, tzinfo=KST),
    )
    assert rejected_summary["timestamp_repair_verified_count"] == 0
    assert rejected_summary["timestamp_repair_failure_count"] == 1
    assert rejected == tampered


def test_verifies_typeless_legacy_row_with_csv_and_row_hash_aliases(
    tmp_path: Path,
) -> None:
    csv_bytes = (
        b"page,row,date,time,title,body\r\n"
        b"147,1,2023-07-31,08:58:33,Alpha event,Complete body\r\n"
    )
    (tmp_path / "news_20230731.csv").write_bytes(csv_bytes)
    input_sha = sha256_bytes(csv_bytes.replace(b"\r\n", b"\n"))
    csv_row = {
        "page": "147",
        "row": "1",
        "date": "2023-07-31",
        "time": "08:58:33",
        "title": "Alpha event",
        "body": "Complete body",
    }
    source = [
        {
            "source_row_id": "SRC-1",
            "input_file": "generated_news_20230731.csv",
            "input_sha256": input_sha,
            "csv_row_index": 1,
            "page": "147",
            "page_row": "1",
            "title": "Alpha event",
            "body": "Complete body",
            "published_at_kst": "2023-07-31T08:58:33+09:00",
            "row_sha256": sha256_text(canonical_json(csv_row)),
        }
    ]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2023, 7, 31, 8, 59, 59, tzinfo=KST),
    )
    repaired[0]["source_id"] = "SRC-1"
    audit, overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2023, 7, 31, 8, 59, 59, tzinfo=KST),
    )

    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert repaired[0]["time_verified"] is True
    assert repaired[0]["available_before_cutoff"] is True
    assert audit["timestamp_repair_verified_count"] == 1
    assert audit["timestamp_repair_failure_count"] == 0
    assert overrides == {"SRC-1": "2023-07-31T08:58:33+09:00"}


def test_verifies_recovered_v2_row_with_unit_separator_hash(
    tmp_path: Path,
) -> None:
    csv_bytes = (
        b"page,row,date,time,title,body\r\n"
        b"238,2,2023-12-04,08:59:55,Alpha event,Complete body\r\n"
    )
    (tmp_path / "news_20231204.csv").write_bytes(csv_bytes)
    csv_row = {
        "page": "238",
        "row": "2",
        "date": "2023-12-04",
        "time": "08:59:55",
        "title": "Alpha event",
        "body": "Complete body",
    }
    source_row = {
        "schema_version": "nslab.source_ledger.v2",
        "source_id": "SRC-20231204-000001",
        "input_file": "generated-news.csv",
        "input_sha256": sha256_bytes(csv_bytes.replace(b"\r\n", b"\n")),
        "source_row_index": 1,
        "page": "238",
        "provider_row": "2",
        "title": "Alpha event",
        "body": "Complete body",
        "published_at": "2023-12-04T08:59:55+09:00",
        "raw_row_sha256": sha256_text("\x1f".join(csv_row.values())),
        "recovery_reconstruction": True,
    }
    source = [source_row]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2023, 12, 4, 8, 59, 59, tzinfo=KST),
    )
    audit, overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2023, 12, 4, 8, 59, 59, tzinfo=KST),
    )

    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert repaired[0]["time_verified"] is True
    assert repaired[0]["available_before_cutoff"] is True
    assert repaired[0]["timestamp_repair_provenance"]["row_index"] == 1
    assert audit["timestamp_repair_failure_count"] == 0
    assert overrides == {
        "SRC-20231204-000001": "2023-12-04T08:59:55+09:00"
    }

    for field, value in (
        ("source_row_index", 2),
        ("provider_row", "3"),
        ("raw_row_sha256", "0" * 64),
    ):
        tampered = [{**source_row, field: value}]
        rejected, rejected_summary = rehydrate_news_source_timestamps(
            tampered,
            news_csv_root=tmp_path,
            cutoff_at=datetime(2023, 12, 4, 8, 59, 59, tzinfo=KST),
        )
        assert rejected_summary["timestamp_repair_verified_count"] == 0, field
        assert rejected_summary["timestamp_repair_failure_count"] == 1, field
        assert rejected == tampered


def test_verifies_v1_source_file_and_input_row_aliases(tmp_path: Path) -> None:
    csv_bytes = (
        b"page,row,date,time,title,body\r\n"
        b"114,5,2023-12-08,08:59:29,Alpha event,Complete body\r\n"
    )
    (tmp_path / "news_20231208.csv").write_bytes(csv_bytes)
    csv_row = {
        "page": "114",
        "row": "5",
        "date": "2023-12-08",
        "time": "08:59:29",
        "title": "Alpha event",
        "body": "Complete body",
    }
    source = [
        {
            "schema_version": "nslab.source_ledger.v1",
            "source_row_id": "SRC-20231208-0001",
            "source_file": "generated-news.csv",
            "source_sha256": sha256_bytes(csv_bytes.replace(b"\r\n", b"\n")),
            "input_row_number": 1,
            "page": "114",
            "source_row_number": "5",
            "title": "Alpha event",
            "body": "Complete body",
            "published_at_kst": "2023-12-08T08:59:29+09:00",
            "raw_row_sha256": sha256_text(canonical_json(csv_row)),
        }
    ]

    repaired, summary = rehydrate_news_source_timestamps(
        source,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2023, 12, 8, 8, 59, 59, tzinfo=KST),
    )
    repaired[0]["source_id"] = source[0]["source_row_id"]
    audit, overrides = audit_rehydrated_news_source_timestamps(
        source,
        repaired,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2023, 12, 8, 8, 59, 59, tzinfo=KST),
    )

    assert summary["timestamp_repair_verified_count"] == 1
    assert summary["timestamp_repair_failure_count"] == 0
    assert repaired[0]["time_verified"] is True
    assert repaired[0]["timestamp_repair_provenance"]["input_file"] == (
        "generated-news.csv"
    )
    assert audit["timestamp_repair_failure_count"] == 0
    assert overrides == {"SRC-20231208-0001": "2023-12-08T08:59:29+09:00"}

    tampered = [{**source[0], "source_row_number": "6"}]
    rejected, rejected_summary = rehydrate_news_source_timestamps(
        tampered,
        news_csv_root=tmp_path,
        cutoff_at=datetime(2023, 12, 8, 8, 59, 59, tzinfo=KST),
    )
    assert rejected_summary["timestamp_repair_verified_count"] == 0
    assert rejected_summary["timestamp_repair_failure_count"] == 1
    assert rejected == tampered
