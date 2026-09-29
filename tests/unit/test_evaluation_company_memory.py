from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.memory.company import (
    CompanyMemoryDeltaApplyResult,
    CompanyMemoryStore,
)
from news_scalping_lab.records.hashing import brain_record_envelope_sha256
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
)

_EPISODE_ID = "EP-evaluation-company-memory"
_TICKER = "347700"
_SOURCE_ID = "SRC-company-identity"


def test_evaluation_company_delta_uses_snapshot_identity_records(
    tmp_path: Path,
) -> None:
    target = _record(
        "REC-company-delta",
        record_type="company_memory_delta",
        company_name=None,
        known_at="2031-01-10T08:30:00+09:00",
    )
    identity = _record(
        "REC-company-identity",
        record_type="supervised_issuer_day_case",
        company_name="라이프시맨틱스",
    )
    replay_available_from = datetime(2030, 1, 10, 8, 0, tzinfo=KST)
    analyzer = _evaluation_analyzer(
        tmp_path,
        source_records=[target, identity],
        snapshot_rows=[
            (target, replay_available_from, None),
            (identity, replay_available_from, None),
        ],
    )

    deltas, identities = analyzer._evaluation_company_memory_record_sets()
    result = analyzer._apply_company_memory_record_deltas(
        CompanyMemoryStore(tmp_path, directory=tmp_path / "derived"),
        as_of=datetime(2031, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    assert [record.record_id for record in deltas] == [target.record_id]
    assert [record.record_id for record in identities] == [
        target.record_id,
        identity.record_id,
    ]
    assert all(record.available_from == replay_available_from for record in identities)
    assert result.skipped_invalid_record_ids == []
    assert result.skipped_future_record_ids == []
    assert result.written_count == 1
    assert read_json(result.written_paths[0])["company_name"] == "라이프시맨틱스"


def test_evaluation_company_identity_preserves_source_known_at(
    tmp_path: Path,
) -> None:
    target = _record(
        "REC-company-future-delta",
        record_type="company_memory_delta",
        company_name=None,
        known_at="2031-01-10T08:30:00+09:00",
    )
    identity = _record(
        "REC-company-future-identity",
        record_type="supervised_direct_event_case",
        company_name="라이프시맨틱스",
    )
    replay_available_from = datetime(2030, 1, 10, 8, 0, tzinfo=KST)
    analyzer = _evaluation_analyzer(
        tmp_path,
        source_records=[target, identity],
        snapshot_rows=[
            (target, replay_available_from, None),
            (identity, replay_available_from, None),
        ],
    )

    result = analyzer._apply_company_memory_record_deltas(
        CompanyMemoryStore(tmp_path, directory=tmp_path / "derived"),
        as_of=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    assert result.skipped_invalid_record_ids == []
    assert result.skipped_future_record_ids == [target.record_id]
    assert result.written_count == 0


def test_evaluation_company_identity_cannot_escape_snapshot(
    tmp_path: Path,
) -> None:
    target = _record(
        "REC-company-isolated-delta",
        record_type="company_memory_delta",
        company_name=None,
        known_at="2030-01-10T08:30:00+09:00",
    )
    outside_snapshot = _record(
        "REC-company-outside-snapshot",
        record_type="supervised_issuer_day_case",
        company_name="Forbidden Future Identity",
    )
    analyzer = _evaluation_analyzer(
        tmp_path,
        source_records=[target, outside_snapshot],
        snapshot_rows=[
            (target, datetime(2030, 1, 10, 8, 0, tzinfo=KST), None),
        ],
    )

    _deltas, identities = analyzer._evaluation_company_memory_record_sets()
    assert [record.record_id for record in identities] == [target.record_id]
    with pytest.raises(
        ValueError,
        match="evaluation snapshot company-memory identity closure failed",
    ):
        analyzer._apply_company_memory_record_deltas(
            CompanyMemoryStore(tmp_path, directory=tmp_path / "derived"),
            as_of=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        )


def test_evaluation_company_identity_rejects_later_replay_availability(
    tmp_path: Path,
) -> None:
    target = _record(
        "REC-company-earlier-delta",
        record_type="company_memory_delta",
        company_name=None,
        known_at="2031-01-10T08:30:00+09:00",
    )
    later_identity = _record(
        "REC-company-later-identity",
        record_type="supervised_issuer_day_case",
        company_name="Later Identity",
    )
    analyzer = _evaluation_analyzer(
        tmp_path,
        source_records=[target, later_identity],
        snapshot_rows=[
            (target, datetime(2030, 1, 10, 8, 0, tzinfo=KST), None),
            (later_identity, datetime(2030, 1, 10, 9, 0, tzinfo=KST), None),
        ],
    )

    with pytest.raises(
        ValueError,
        match="evaluation snapshot company-memory identity closure failed",
    ):
        analyzer._apply_company_memory_record_deltas(
            CompanyMemoryStore(tmp_path, directory=tmp_path / "derived"),
            as_of=datetime(2032, 1, 10, 8, 59, 59, tzinfo=KST),
        )


def test_evaluation_company_projection_rejects_source_hash_drift(
    tmp_path: Path,
) -> None:
    target = _record(
        "REC-company-hash-delta",
        record_type="company_memory_delta",
        company_name="Hash Bound Co",
        known_at="2030-01-10T08:30:00+09:00",
    )
    analyzer = _evaluation_analyzer(
        tmp_path,
        source_records=[target],
        snapshot_rows=[
            (
                target,
                datetime(2030, 1, 10, 8, 0, tzinfo=KST),
                "0" * 64,
            ),
        ],
    )

    with pytest.raises(
        ValueError,
        match=f"evaluation company-memory source hash mismatch: {target.record_id}",
    ):
        analyzer._evaluation_company_memory_record_sets()


def test_production_company_memory_application_path_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = DailyAnalyzer(Settings(project_root=tmp_path))
    store = CompanyMemoryStore(tmp_path, directory=tmp_path / "derived")
    expected = CompanyMemoryDeltaApplyResult(
        processed_record_count=0,
        written_count=0,
        written_paths=[],
        skipped_future_record_ids=[],
        skipped_invalid_record_ids=[],
    )
    called: list[datetime] = []

    def apply_record_deltas(*, as_of: datetime | None = None) -> CompanyMemoryDeltaApplyResult:
        assert as_of is not None
        called.append(as_of)
        return expected

    monkeypatch.setattr(store, "apply_record_deltas", apply_record_deltas)
    monkeypatch.setattr(
        analyzer,
        "_evaluation_company_memory_record_sets",
        lambda: (_ for _ in ()).throw(
            AssertionError("production path queried the evaluation snapshot")
        ),
    )
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)

    observed = analyzer._apply_company_memory_record_deltas(
        store,
        as_of=cutoff,
    )

    assert observed is expected
    assert called == [cutoff]


def _evaluation_analyzer(
    root: Path,
    *,
    source_records: list[BrainRecordEnvelope],
    snapshot_rows: list[tuple[BrainRecordEnvelope, datetime, str | None]],
) -> DailyAnalyzer:
    records_dir = root / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / f"{_EPISODE_ID}.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in source_records),
        encoding="utf-8",
    )
    database_path = root / "memory" / "evaluation-memory.duckdb"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(
            """
            CREATE TABLE records (
                record_id VARCHAR,
                episode_id VARCHAR,
                record_type VARCHAR,
                available_from VARCHAR,
                source_sha256 VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?)",
            [
                (
                    record.record_id,
                    record.episode_id,
                    record.record_type,
                    replay_available_from.isoformat(),
                    source_sha256 or brain_record_envelope_sha256(record),
                )
                for record, replay_available_from, source_sha256 in snapshot_rows
            ],
        )
    finally:
        connection.close()
    analyzer = DailyAnalyzer(Settings(project_root=root))
    analyzer._evaluation_memory_snapshot = SimpleNamespace(  # type: ignore[assignment]
        snapshot_id="MEMIDX-evaluation-company-test",
        database=SimpleNamespace(
            artifact_path=database_path.relative_to(root).as_posix(),
            sha256=file_sha256(database_path),
        ),
        evaluation_only=True,
    )
    analyzer._evaluation_company_record_cache = None
    return analyzer


def _record(
    record_id: str,
    *,
    record_type: str,
    company_name: str | None,
    known_at: str | None = None,
) -> BrainRecordEnvelope:
    source_available_from = datetime(2031, 1, 10, 9, 0, tzinfo=KST)
    payload: dict[str, object] = {
        "record_id": record_id,
        "record_type": record_type,
        "episode_id": _EPISODE_ID,
        "trade_date": "2029-01-10",
        "available_from": source_available_from.isoformat(),
        "ticker": _TICKER,
        "source_ids": [_SOURCE_ID],
        "summary": f"{record_id} fixture",
    }
    if company_name is not None:
        payload["company_name"] = company_name
    if known_at is not None:
        payload["known_at"] = known_at
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id=_EPISODE_ID,
        trade_date=date(2029, 1, 10),
        available_from=source_available_from,
        training_target="company_memory",
        evidence_phase="POSTMORTEM_SYNTHESIS",
        training_eligible=True,
        eligibility_reason="unit test",
        status="supported",
        confidence_label="medium",
        provenance_source_ids=[_SOURCE_ID],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )
