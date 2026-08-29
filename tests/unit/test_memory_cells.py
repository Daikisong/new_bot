from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

import news_scalping_lab.memory.index as memory_index_module
from news_scalping_lab.contracts.memory_context import MemoryCellMembership
from news_scalping_lab.memory.cells import (
    RecordMemoryDocumentResolver,
    _secondary_cells,
    build_memory_cells,
    build_record_memory_documents,
    record_independent_unit_id,
    record_memory_document,
)
from news_scalping_lab.memory.index import (
    ProductionMemoryIndex,
    _stable_ann_probe_rows,
    inspect_current_memory_index,
    inspect_memory_snapshot,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import record_routing_metadata
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import AsyncEmbeddingProviderAdapter, DeterministicHashEmbeddingProvider
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    write_json,
)


class _TestAsyncEmbeddingProvider:
    def __init__(self) -> None:
        self.embedded_text_count = 0

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        self.embedded_text_count += len(texts)
        return DeterministicHashEmbeddingProvider().embed_texts(texts)


def test_ann_probe_rows_use_stable_cell_id_ties_at_limit_boundary() -> None:
    tied_rows = [
        ("CELL-Z", 0.5, 1, 1),
        ("CELL-B", 0.9, 1, 1),
        ("CELL-A", 0.5, 1, 1),
    ]

    selected, tie_distance = _stable_ann_probe_rows(tied_rows, limit=2)

    assert [str(row[0]) for row in selected] == ["CELL-B", "CELL-A"]
    assert tie_distance == pytest.approx(0.5)

    untied_rows = [
        ("CELL-Z", 0.5, 1, 1),
        ("CELL-B", 0.9, 1, 1),
        ("CELL-A", 0.6, 1, 1),
    ]
    selected, tie_distance = _stable_ann_probe_rows(untied_rows, limit=2)
    assert [str(row[0]) for row in selected] == ["CELL-B", "CELL-A"]
    assert tie_distance is None


class _RealLikeEmbeddingProvider(AsyncEmbeddingProviderAdapter):
    def __init__(self, *, embedding_method: str = "llm_embedding:test:embed-v1") -> None:
        self.backend = _TestAsyncEmbeddingProvider()
        super().__init__(
            self.backend,
            embedding_method=embedding_method,
            production_capability_attested=True,
        )

    @property
    def embedded_text_count(self) -> int:
        return self.backend.embedded_text_count


class _ChangedEmbeddingProvider(_RealLikeEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(embedding_method="llm_embedding:test:embed-v2")


class _SpoofedEmbeddingProvider:
    dimensions = 32
    embedding_method = "llm_embedding:openai:text-embedding-3-small"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return DeterministicHashEmbeddingProvider().embed_texts(texts)


def _record(
    record_id: str,
    *,
    available_from: datetime,
    ticker: str,
    response_class: str,
    high_return_pct: float,
    company_name: str | None = None,
) -> BrainRecordEnvelope:
    payload = {
        "record_type": "supervised_direct_event_case",
        "training_eligible": True,
        "ticker": ticker,
        "company_name": company_name or f"Company {ticker}",
        "title": company_name or "supply contract signed",
        "event_id": f"EVENT-{record_id}",
        "response_class": response_class,
        "high_return_pct": high_return_pct,
        "label_quality": "verified",
    }
    digest = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="supervised_direct_event_case",
        episode_id="NSLAB-20300110-CELLS",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target="direct_event_response",
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        status="supported",
        confidence_label="high",
        provenance_source_ids=[f"SRC-{record_id}"],
        raw_payload_sha256=digest,
        normalized_payload_sha256=digest,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=payload,
    )


def test_memory_cell_membership_rejects_primary_secondary_overlap() -> None:
    with pytest.raises(ValidationError):
        MemoryCellMembership(
            record_id="REC-1",
            primary_cell_id="CELL-1",
            secondary_cell_ids=["CELL-1"],
            independent_unit_id="ISSUER_DAY:2030-01-10:000001",
            membership_score=1.0,
            membership_rule="rule",
            membership_rule_version="v1",
            available_from=datetime(2030, 1, 10, tzinfo=KST),
            routing_disposition="REASONING",
        )


def test_production_index_rejects_provider_name_spoofing(tmp_path) -> None:
    with pytest.raises(ValueError, match="attested real embedding provider"):
        ProductionMemoryIndex(
            tmp_path,
            embedding_provider=_SpoofedEmbeddingProvider(),
            production=True,
        )


def test_unsupported_reasoning_record_blocks_production_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, tzinfo=KST)
    record = _record(
        "REC-UNSUPPORTED-UNIT",
        available_from=cutoff,
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=12.0,
    )
    payload = dict(record.payload)
    payload.pop("event_id")
    payload.pop("ticker")
    payload.pop("company_name")
    digest = sha256_text(canonical_json(payload))
    record = record.model_copy(
        update={
            "payload": payload,
            "raw_payload_sha256": digest,
            "normalized_payload_sha256": digest,
        }
    )
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: [record])
    BrainRecordStore(tmp_path).rebuild_indexes()
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=DeterministicHashEmbeddingProvider(),
        production=False,
    )

    manifest = index.build(as_of=cutoff)

    assert manifest.unsupported_reasoning_record_count == 1
    assert manifest.unsupported_reasoning_record_ids_sha256 == sha256_text(
        canonical_json([record.record_id])
    )
    assert manifest.production_ready is False
    inspection = inspect_memory_snapshot(tmp_path, manifest.snapshot_id)
    assert inspection["passed"] is True, inspection["errors"]


def test_current_memory_pointer_rejects_relocated_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, tzinfo=KST)
    record = _record(
        "REC-CANONICAL-POINTER",
        available_from=cutoff,
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=12.0,
    )
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: [record])
    BrainRecordStore(tmp_path).rebuild_indexes()
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    manifest = index.build(as_of=cutoff)
    canonical_manifest = (
        tmp_path
        / "memory"
        / "retrieval_index"
        / "snapshots"
        / manifest.snapshot_id
        / "manifest.json"
    )
    relocated_manifest = (
        tmp_path / "memory" / "retrieval_index" / "relocated" / "manifest.json"
    )
    relocated_manifest.parent.mkdir(parents=True)
    relocated_manifest.write_bytes(canonical_manifest.read_bytes())
    pointer_path = tmp_path / "memory" / "retrieval_index" / "current.json"
    pointer = read_json(pointer_path)
    pointer["manifest_path"] = relocated_manifest.relative_to(tmp_path).as_posix()
    pointer["manifest_sha256"] = file_sha256(relocated_manifest)
    write_json(pointer_path, pointer)

    inspection = inspect_current_memory_index(tmp_path)

    assert inspection["passed"] is False
    assert inspection["status"] == "invalid"
    assert inspection["pointer_path_verified"] is False


def test_fact_id_is_not_promoted_to_an_independent_event_unit() -> None:
    cutoff = datetime(2030, 1, 10, tzinfo=KST)
    record = _record(
        "REC-FACT-NOT-EVENT",
        available_from=cutoff,
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=12.0,
    )
    payload = dict(record.payload)
    payload.pop("event_id")
    payload["fact_id"] = "FACT-1"
    digest = sha256_text(canonical_json(payload))
    record = record.model_copy(
        update={
            "payload": payload,
            "raw_payload_sha256": digest,
            "normalized_payload_sha256": digest,
        }
    )

    unit_id = record_independent_unit_id(record)
    assert unit_id.startswith("ISSUER_DAY:")
    assert "FACT-1" not in unit_id


def test_v1_snapshot_manifest_is_reported_as_legacy_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, tzinfo=KST)
    record = _record(
        "REC-LEGACY-SNAPSHOT",
        available_from=cutoff,
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=12.0,
    )
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: [record])
    BrainRecordStore(tmp_path).rebuild_indexes()
    manifest = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=DeterministicHashEmbeddingProvider(),
        production=False,
    ).build(as_of=cutoff)
    manifest_path = tmp_path / "memory" / "retrieval_index" / "snapshots" / (
        manifest.snapshot_id
    ) / "manifest.json"
    payload = read_json(manifest_path)
    payload["schema_version"] = "nslab.memory_cell_snapshot_manifest.v1"
    write_json(manifest_path, payload)

    inspection = inspect_memory_snapshot(tmp_path, manifest.snapshot_id)

    assert inspection["status"] == "stale"
    assert inspection["errors"] == ["snapshot_schema_legacy"]
    assert inspection["legacy_read_compatible"] is True


def test_memory_document_ignores_identity_routing_and_outcome_but_keeps_structure() -> None:
    base = _record(
        "REC-BASE",
        available_from=datetime(2030, 1, 10, tzinfo=KST),
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=20.0,
        company_name="virtual issuer supply agreement",
    )
    changed_payload = {
        **base.payload,
        "record_type": "negative_control_case",
        "training_eligible": False,
        "response_class": "NEGATIVE",
        "high_return_pct": -20.0,
        "postmortem_summary": "price outcome changed",
        "payload_sha256": "f" * 64,
    }
    changed = base.model_copy(
        update={
            "record_id": "REC-CHANGED",
            "record_type": "negative_control_case",
            "training_eligible": False,
            "confidence_label": "low",
            "provenance_source_ids": ["SRC-OTHER"],
            "payload": changed_payload,
        }
    )
    different = base.model_copy(
        update={"payload": {**base.payload, "title": "different issuer merger approved"}}
    )

    assert record_memory_document(base) == record_memory_document(changed)
    assert record_memory_document(base) != record_memory_document(different)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("error_type", "CANDIDATE_GENERATION_MISS"),
        ("discovery_mode", "POSTMORTEM_SOURCE_BACKED_NOT_BLIND_HIT"),
    ],
)
def test_memory_document_keeps_generic_error_and_discovery_structure(
    field: str,
    value: str,
) -> None:
    record = _record(
        f"REC-{field.upper()}",
        available_from=datetime(2030, 1, 10, tzinfo=KST),
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=20.0,
        company_name="excluded issuer identity",
    )
    payload = {
        "ticker": "000001",
        "company_name": "excluded issuer identity",
        "high_return_pct": 20.0,
        "D_outcome": {"description": "excluded outcome description"},
        field: value,
    }
    record = record.model_copy(update={"payload": payload})

    document = record_memory_document(record)

    assert "unavailable_in_record_payload" not in document
    assert field in document
    assert value in document
    assert "000001" not in document
    assert "excluded issuer identity" not in document
    assert "excluded outcome description" not in document


def test_actual_reasoning_documents_resolve_structural_evidence() -> None:
    records = BrainRecordStore(Path.cwd()).list_records()
    documents = build_record_memory_documents(Path.cwd(), records)
    reasoning_ids = {
        record.record_id
        for record in records
        if record_routing_metadata(record).routing_disposition == "REASONING"
    }

    assert all(
        "unavailable_in_record_payload" not in documents[record_id]
        for record_id in reasoning_ids
    )


def test_fallback_evidence_rejects_postmortem_and_cutoff_after_rows(
    tmp_path: Path,
) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, tzinfo=KST)
    record = _record(
        "REC-FALLBACK",
        available_from=datetime(2031, 1, 1, tzinfo=KST),
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=12.0,
    )
    payload = {
        "training_eligible": True,
        "ticker": "000001",
        "event_id": "EVENT-REC-EVIDENCE-GENERATION",
        "response_class": "POSITIVE",
        "high_return_pct": 12.0,
        "label_quality": "verified",
        "fact_id": "FACT-BLIND",
        "error_type": "CANDIDATE_GENERATION_MISS",
        "postmortem_fact_id": "FACT-POST",
        "future_fact_id": "FACT-FUTURE",
    }
    digest = sha256_text(canonical_json(payload))
    record = record.model_copy(
        update={
            "payload": payload,
            "raw_payload_sha256": digest,
            "normalized_payload_sha256": digest,
            "provenance_source_ids": [
                "FACT-BLIND",
                "FACT-POST",
                "FACT-FUTURE",
                "FACT-FLAGGED",
            ],
        }
    )
    raw_blocks = (
        tmp_path
        / "research"
        / "episodes"
        / record.episode_id
        / "raw_blocks"
    )
    raw_blocks.mkdir(parents=True)
    rows = [
        {
            "fact_id": "FACT-BLIND",
            "source_phase": "BLIND",
            "available_from": cutoff.isoformat(),
            "statement": "cutoff safe supply mechanism",
        },
        {
            "fact_id": "FACT-POST",
            "metadata": {"source_phase": "POSTMORTEM"},
            "available_from": cutoff.isoformat(),
            "statement": "postmortem outcome leak",
        },
        {
            "fact_id": "FACT-FUTURE",
            "source_phase": "BLIND",
            "available_from": datetime(2030, 1, 10, 9, 1, tzinfo=KST).isoformat(),
            "statement": "future cutoff leak",
        },
        {
            "fact_id": "FACT-FLAGGED",
            "source_phase": "BLIND",
            "available_from": cutoff.isoformat(),
            "available_before_cutoff": False,
            "statement": "explicit unsafe flag leak",
        },
    ]
    (raw_blocks / "fact_ledger.jsonl").write_text(
        "".join(canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    document = RecordMemoryDocumentResolver(tmp_path).document(record)

    assert "CANDIDATE_GENERATION_MISS" in document
    assert "cutoff safe supply mechanism" in document
    assert "postmortem outcome leak" not in document
    assert "future cutoff leak" not in document
    assert "explicit unsafe flag leak" not in document


def test_fallback_evidence_change_invalidates_runtime_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, tzinfo=KST)
    record = _record(
        "REC-EVIDENCE-GENERATION",
        available_from=cutoff,
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=12.0,
    )
    payload = {
        "training_eligible": True,
        "ticker": "000001",
        "event_id": "EVENT-REC-EVIDENCE-GENERATION",
        "response_class": "POSITIVE",
        "high_return_pct": 12.0,
        "label_quality": "verified",
        "fact_id": "FACT-1",
    }
    digest = sha256_text(canonical_json(payload))
    record = record.model_copy(
        update={
            "payload": payload,
            "raw_payload_sha256": digest,
            "normalized_payload_sha256": digest,
            "provenance_source_ids": ["FACT-1"],
        }
    )
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: [record])
    raw_blocks = (
        tmp_path
        / "research"
        / "episodes"
        / record.episode_id
        / "raw_blocks"
    )
    raw_blocks.mkdir(parents=True)
    fact_path = raw_blocks / "fact_ledger.jsonl"
    base_row = {
        "fact_id": "FACT-1",
        "source_phase": "BLIND",
        "available_from": cutoff.isoformat(),
        "statement": "original mechanism",
    }
    fact_path.write_text(canonical_json(base_row) + "\n", encoding="utf-8")
    provider = _RealLikeEmbeddingProvider()
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=provider,
        production=True,
    )
    BrainRecordStore(tmp_path).rebuild_indexes()
    first = index.build(as_of=cutoff)
    assert provider.embedded_text_count == 1

    fact_path.write_text(
        canonical_json({**base_row, "statement": "changed mechanism"}) + "\n",
        encoding="utf-8",
    )
    BrainRecordStore(tmp_path).rebuild_indexes()

    with pytest.raises(ValueError, match="stale relative to the record store"):
        index.search_cells("mechanism", cutoff_at=cutoff)
    second = index.build(as_of=cutoff)
    assert second.snapshot_id != first.snapshot_id
    assert provider.embedded_text_count == 2


def test_bundle_cutoff_change_invalidates_fallback_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_cutoff = datetime(2030, 1, 10, 8, 59, tzinfo=KST)
    record = _record(
        "REC-CUTOFF-EVIDENCE",
        available_from=datetime(2031, 1, 1, tzinfo=KST),
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=12.0,
    )
    payload = {
        "training_eligible": True,
        "ticker": "000001",
        "event_id": "EVENT-REC-CUTOFF-EVIDENCE",
        "response_class": "POSITIVE",
        "high_return_pct": 12.0,
        "label_quality": "verified",
        "fact_id": "FACT-CUTOFF",
    }
    digest = sha256_text(canonical_json(payload))
    record = record.model_copy(
        update={
            "payload": payload,
            "raw_payload_sha256": digest,
            "normalized_payload_sha256": digest,
            "provenance_source_ids": ["FACT-CUTOFF"],
        }
    )
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: [record])
    episode_dir = tmp_path / "research" / "episodes" / record.episode_id
    raw_blocks = episode_dir / "raw_blocks"
    raw_blocks.mkdir(parents=True)
    write_json(episode_dir / "bundle_envelope.json", {"cutoff_at": first_cutoff.isoformat()})
    (raw_blocks / "fact_ledger.jsonl").write_text(
        canonical_json(
            {
                "fact_id": "FACT-CUTOFF",
                "source_phase": "BLIND",
                "available_from": first_cutoff.isoformat(),
                "statement": "cutoff-bound mechanism",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    provider = _RealLikeEmbeddingProvider()
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=provider,
        production=True,
    )
    store = BrainRecordStore(tmp_path)
    store.rebuild_indexes()
    manifest = index.build(as_of=record.available_from)

    write_json(
        episode_dir / "bundle_envelope.json",
        {"cutoff_at": datetime(2030, 1, 10, 8, 58, tzinfo=KST).isoformat()},
    )
    store.rebuild_indexes()

    with pytest.raises(ValueError, match="stale relative to the record store"):
        index.search_cells("mechanism", cutoff_at=manifest.as_of_cutoff)


def test_independent_unit_matches_record_semantics() -> None:
    base = _record(
        "REC-UNIT",
        available_from=datetime(2030, 1, 10, tzinfo=KST),
        ticker="000001",
        response_class="POSITIVE",
        high_return_pct=12.0,
    )
    direct_one = base.model_copy(
        update={"payload": {**base.payload, "event_id": "EVENT-1"}}
    )
    direct_two = base.model_copy(
        update={"payload": {**base.payload, "event_id": "EVENT-2"}}
    )
    issuer = base.model_copy(update={"record_type": "supervised_issuer_day_case"})
    theme = base.model_copy(
        update={
            "record_type": "theme_formation_case",
            "payload": {**base.payload, "theme_id": "THEME-1"},
        }
    )
    newsless = base.model_copy(
        update={"record_type": "newsless_or_unexplained_case"}
    )
    pair = base.model_copy(
        update={
            "record_type": "blind_leader_preference_pair",
            "payload": {
                **base.payload,
                "theme_id": "THEME-1",
                "blind_pair_id": "PAIR-1",
            },
        }
    )
    pair_without_theme = base.model_copy(
        update={
            "record_type": "blind_leader_preference_pair",
            "payload": {**base.payload, "blind_pair_id": "PAIR-2"},
        }
    )
    beneficiary_without_theme = base.model_copy(
        update={"record_type": "beneficiary_discovery_case"}
    )
    negative_control_without_issuer = base.model_copy(
        update={
            "record_type": "negative_control_case",
            "payload": {"body_table_audit_id": "BTA-1"},
        }
    )
    counterexample_without_issuer = base.model_copy(
        update={
            "record_type": "counterexample",
            "payload": {"candidate_screening_id": "CS-1"},
        }
    )
    source_bound_negative_control = base.model_copy(
        update={
            "record_type": "negative_control_case",
            "payload": {"negative_control_reason": "market-state-only control"},
        }
    )

    assert record_independent_unit_id(direct_one).startswith("EVENT_ISSUER_DAY:")
    assert record_independent_unit_id(direct_one) != record_independent_unit_id(
        direct_two
    )
    assert record_independent_unit_id(issuer).startswith("ISSUER_DAY:")
    assert record_independent_unit_id(theme).startswith("THEME_DAY:")
    assert record_independent_unit_id(newsless).startswith("TICKER_DAY:")
    assert record_independent_unit_id(pair).startswith("THEME_DAY_PAIR:")
    assert record_independent_unit_id(pair_without_theme).startswith("PAIR_DAY:")
    assert record_independent_unit_id(beneficiary_without_theme).startswith(
        "ISSUER_DAY:"
    )
    assert record_independent_unit_id(negative_control_without_issuer).startswith(
        "CASE_DAY:"
    )
    assert record_independent_unit_id(counterexample_without_issuer).startswith(
        "CASE_DAY:"
    )
    control_unit = record_independent_unit_id(source_bound_negative_control)
    assert control_unit.startswith("CONTROL_SOURCE_DAY:")
    assert source_bound_negative_control.provenance_source_ids[0] not in control_unit


def test_build_memory_cells_assigns_one_primary_and_bounded_secondary() -> None:
    records = [
        _record(
            "REC-1",
            available_from=datetime(2030, 1, 10, tzinfo=KST),
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        ),
        _record(
            "REC-2",
            available_from=datetime(2030, 1, 10, tzinfo=KST),
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-3.0,
        ),
    ]
    vectors = [[1.0, 0.1, -0.2], [1.0, -0.1, -0.2]]

    result = build_memory_cells(
        records,
        vectors,
        max_available_from=datetime(2030, 1, 10, tzinfo=KST),
    )

    assert len(result.memberships) == 2
    assert len({item.record_id for item in result.memberships}) == 2
    assert all(item.primary_cell_id for item in result.memberships)
    assert all(len(item.secondary_cell_ids) <= 2 for item in result.memberships)
    assert sum(cell.primary_member_count for cell in result.cells) == 2


def test_secondary_cell_membership_supports_multiple_adjacent_cells() -> None:
    assert _secondary_cells(
        "000",
        margins=[0.1, 0.2, 0.3],
        cell_ids_by_signature={
            "000": "CELL-PRIMARY",
            "100": "CELL-SECONDARY-1",
            "110": "CELL-SECONDARY-2",
        },
    ) == ["CELL-SECONDARY-1", "CELL-SECONDARY-2"]


def test_memory_index_snapshot_is_as_of_and_queries_cells_without_future_records(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    available = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    future = datetime(2030, 1, 11, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-SUPPLY-POSITIVE",
            available_from=available,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=15.0,
            company_name="가상기업 공급계약",
        ),
        _record(
            "REC-SUPPLY-NEGATIVE",
            available_from=available,
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-2.0,
        ),
        _record(
            "REC-FUTURE",
            available_from=future,
            ticker="000003",
            response_class="POSITIVE",
            high_return_pct=20.0,
        ),
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=DeterministicHashEmbeddingProvider(),
        production=False,
    )

    manifest = index.build(as_of=available)

    assert manifest.record_count == 2
    assert manifest.primary_membership_count == 2
    assert manifest.production_ready is False
    assert manifest.metadata_index_ready is True
    assert manifest.fts_index_ready is True
    assert manifest.hnsw_index_ready is True
    inspection = inspect_memory_snapshot(tmp_path, manifest.snapshot_id)
    assert inspection["status"] == "current_as_of", inspection["errors"]
    database_path = tmp_path / manifest.database.artifact_path
    connection = duckdb.connect(str(database_path), read_only=True)
    connection.execute("LOAD vss")
    vector = DeterministicHashEmbeddingProvider().embed_texts(["공급계약"])[0]
    plan = "\n".join(
        str(row[1])
        for row in connection.execute(
            """
            EXPLAIN SELECT cell_id
                FROM reasoning_cells
            ORDER BY array_cosine_distance(centroid, ?::FLOAT[32])
            LIMIT 4
            """,
            [vector],
        ).fetchall()
    )
    connection.close()
    assert "HNSW_INDEX_SCAN" in plan

    monkeypatch.setattr(
        BrainRecordStore,
        "list_records",
        lambda self: (_ for _ in ()).throw(AssertionError("online corpus scan")),
    )
    candidates = index.search_cells("공급계약", cutoff_at=available, limit=4)
    assert candidates
    assert any(candidate.fts_score is not None for candidate in candidates)
    query_text = "공급계약"
    index.search_cells(
        query_text,
        cutoff_at=available,
        limit=4,
        included_memory_lanes=("positive_analogs",),
    )
    index.search_cells(
        query_text,
        cutoff_at=available,
        limit=4,
        included_memory_lanes=("negative_controls",),
    )
    database_key = str(database_path.resolve())
    fts_caches = index._runtime_connection_state.fts_score_tables_by_database
    assert set(fts_caches) == {database_key}
    fts_cache = fts_caches[database_key]
    assert len(fts_cache) == 1
    runtime_connection = index._runtime_connection(database_path)
    score_table = next(iter(fts_cache.values()))
    set_based_scores = runtime_connection.execute(
        f"SELECT record_id, score FROM {score_table} ORDER BY record_id"
    ).fetchall()
    macro_scores = runtime_connection.execute(
        """
        SELECT record_id,
               fts_main_reasoning_records.match_bm25(record_id, ?) AS score
        FROM reasoning_records
        WHERE score IS NOT NULL
        ORDER BY record_id
        """,
        [query_text],
    ).fetchall()
    assert [row[0] for row in set_based_scores] == [row[0] for row in macro_scores]
    assert [row[1] for row in set_based_scores] == pytest.approx(
        [row[1] for row in macro_scores],
        abs=1e-12,
    )
    monkeypatch.setattr(
        memory_index_module,
        "MEMORY_INDEX_RUNTIME_FTS_CACHE_SIZE",
        2,
    )
    second_database_path = tmp_path / "second-memory.duckdb"
    shutil.copy2(database_path, second_database_path)
    second_connection = index._runtime_connection(second_database_path)
    second_shared_table = index._runtime_fts_score_table(
        second_connection,
        database_path=second_database_path,
        query=query_text,
    )
    index._runtime_fts_score_table(
        second_connection,
        database_path=second_database_path,
        query="negative outcome",
    )
    reused_second_table = index._runtime_fts_score_table(
        second_connection,
        database_path=second_database_path,
        query=query_text,
    )
    assert reused_second_table == second_shared_table
    assert second_connection.execute(
        f"SELECT COUNT(*) FROM {reused_second_table}"
    ).fetchone() is not None
    assert runtime_connection.execute(
        f"SELECT COUNT(*) FROM {score_table}"
    ).fetchone() is not None
    assert set(fts_caches) == {
        database_key,
        str(second_database_path.resolve()),
    }
    members = index.members_for_cells(
        [candidate.cell_id for candidate in candidates],
        cutoff_at=available,
    )
    member_ids = {member.record_id for member in members}
    assert "REC-FUTURE" not in member_ids
    assert member_ids == {"REC-SUPPLY-POSITIVE", "REC-SUPPLY-NEGATIVE"}
    assert {member.evidence_polarity for member in members} == {"POSITIVE", "NEGATIVE"}


def test_memory_index_incremental_snapshot_preserves_historical_replay(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    second_cutoff = datetime(2030, 1, 11, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-OLD",
            available_from=first_cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    provider = _RealLikeEmbeddingProvider()
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=provider,
        production=True,
    )
    first = index.build(as_of=first_cutoff)
    records.append(
        _record(
            "REC-NEW",
            available_from=second_cutoff,
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-1.0,
        )
    )
    BrainRecordStore(tmp_path).rebuild_indexes()
    with pytest.raises(ValueError, match="stale relative to the record store"):
        index.search_cells("supply contract", cutoff_at=second_cutoff)

    second = index.build(as_of=second_cutoff)

    assert second.snapshot_id != first.snapshot_id
    assert second.parent_snapshot_id == first.snapshot_id
    assert second.retained_record_count == 1
    assert second.added_record_count == 1
    assert provider.embedded_text_count == 2
    assert index.resolve_snapshot(cutoff_at=first_cutoff).snapshot_id == first.snapshot_id
    assert index.resolve_snapshot(cutoff_at=second_cutoff).snapshot_id == second.snapshot_id


def test_snapshot_expires_when_preexisting_future_record_becomes_available(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    next_available = datetime(2030, 1, 11, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-OLD",
            available_from=first_cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        ),
        _record(
            "REC-FUTURE-PREEXISTING",
            available_from=next_available,
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-2.0,
        ),
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    manifest = index.build(as_of=first_cutoff)

    assert manifest.next_available_from == next_available
    with pytest.raises(FileNotFoundError, match="no compatible memory snapshot"):
        index.resolve_snapshot(cutoff_at=next_available)


def test_streaming_audit_rejects_next_available_tamper(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    future = datetime(2030, 1, 11, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-SAFE",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        ),
        _record(
            "REC-FUTURE",
            available_from=future,
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-2.0,
        ),
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    manifest = index.build(as_of=cutoff)
    manifest_path = (
        tmp_path
        / "memory"
        / "retrieval_index"
        / "snapshots"
        / manifest.snapshot_id
        / "manifest.json"
    )
    payload = read_json(manifest_path)
    payload["next_available_from"] = datetime(
        2030, 1, 12, 12, 0, tzinfo=KST
    ).isoformat()
    write_json(manifest_path, payload)
    monkeypatch.setattr(memory_index_module, "MEMORY_INDEX_STREAMING_AUDIT_THRESHOLD", 0)

    inspection = inspect_memory_snapshot(tmp_path, manifest.snapshot_id)

    assert inspection["status"] == "stale"
    assert "next_available_from_stale" in inspection["errors"]


def test_streaming_audit_threshold_counts_future_partition(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-SAFE",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        ),
        _record(
            "REC-FUTURE",
            available_from=datetime(2030, 1, 11, 12, 0, tzinfo=KST),
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-2.0,
        ),
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    manifest = index.build(as_of=cutoff)
    monkeypatch.setattr(memory_index_module, "MEMORY_INDEX_STREAMING_AUDIT_THRESHOLD", 1)

    inspection = inspect_memory_snapshot(tmp_path, manifest.snapshot_id)

    assert inspection["passed"] is True
    assert inspection["streaming_audit"] is True


def test_reasoning_cell_search_is_not_reweighted_by_audit_fts_documents(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    reasoning_records = [
        _record(
            "REC-REASONING-1",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
            company_name="issuer supply contract",
        ),
        _record(
            "REC-REASONING-2",
            available_from=cutoff,
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-2.0,
            company_name="issuer merger approval",
        ),
    ]

    records = list(reasoning_records)
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    clean_index = ProductionMemoryIndex(
        tmp_path / "clean",
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    clean_index.build(as_of=cutoff)
    clean_candidates = clean_index.search_cells(
        "supply contract",
        cutoff_at=cutoff,
        limit=8,
    )

    audit_records = []
    for item in range(50):
        record = _record(
            f"REC-AUDIT-{item:03d}",
            available_from=cutoff,
            ticker=f"9{item:05d}",
            response_class="POSITIVE",
            high_return_pct=20.0,
            company_name="supply contract supply contract supply contract",
        )
        payload = {**record.payload, "training_eligible": False}
        digest = sha256_text(canonical_json(payload))
        audit_records.append(
            record.model_copy(
                update={
                    "training_eligible": False,
                    "payload": payload,
                    "raw_payload_sha256": digest,
                    "normalized_payload_sha256": digest,
                }
            )
        )
    records = [*reasoning_records, *audit_records]
    noisy_index = ProductionMemoryIndex(
        tmp_path / "noisy",
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    noisy_index.build(as_of=cutoff)
    noisy_candidates = noisy_index.search_cells(
        "supply contract",
        cutoff_at=cutoff,
        limit=8,
    )

    assert [item.cell_id for item in noisy_candidates] == [
        item.cell_id for item in clean_candidates
    ]
    for noisy, clean in zip(noisy_candidates, clean_candidates, strict=True):
        assert noisy.score == pytest.approx(clean.score)
        assert noisy.ann_score == pytest.approx(clean.ann_score)
        assert noisy.fts_score == pytest.approx(clean.fts_score)
        assert noisy.primary_member_count == clean.primary_member_count
        assert noisy.independent_unit_count == clean.independent_unit_count


def test_memory_index_provider_change_creates_incompatible_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-1",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    first_index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    second_index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_ChangedEmbeddingProvider(),
        production=True,
    )

    first = first_index.build(as_of=cutoff)
    second = second_index.build(as_of=cutoff)

    assert first.snapshot_id != second.snapshot_id
    assert first.embedding_model != second.embedding_model
    assert first_index.resolve_snapshot(cutoff_at=cutoff).snapshot_id == first.snapshot_id
    assert second_index.resolve_snapshot(cutoff_at=cutoff).snapshot_id == second.snapshot_id


def test_memory_index_inspection_detects_source_envelope_change(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-1",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    manifest = index.build(as_of=cutoff)
    records[0] = records[0].model_copy(update={"confidence_label": "low"})

    inspection = inspect_memory_snapshot(tmp_path, manifest.snapshot_id)

    assert inspection["status"] == "stale"
    assert "source_record_hashes_stale" in inspection["errors"]


def test_production_memory_index_rejects_deterministic_embeddings(tmp_path) -> None:
    with pytest.raises(ValueError, match="real embedding"):
        ProductionMemoryIndex(
            tmp_path,
            embedding_provider=DeterministicHashEmbeddingProvider(),
            production=True,
        )


def test_memory_index_inspection_recomputes_database_membership(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-1",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    manifest = index.build(as_of=cutoff)
    database_path = tmp_path / manifest.database.artifact_path
    connection = duckdb.connect(str(database_path))
    connection.execute("LOAD vss")
    connection.execute(
        "UPDATE memberships SET primary_cell_id = 'CELL-TAMPERED' WHERE record_id = 'REC-1'"
    )
    connection.execute("CHECKPOINT")
    connection.close()
    manifest_path = database_path.parent / "manifest.json"
    manifest_payload = read_json(manifest_path)
    manifest_payload["database"]["sha256"] = file_sha256(database_path)
    write_json(manifest_path, manifest_payload)

    inspection = inspect_memory_snapshot(tmp_path, manifest.snapshot_id)

    assert inspection["status"] == "invalid"
    assert "database_membership_sidecar_mismatch" in inspection["errors"]


def test_streaming_audit_detects_coherent_membership_database_tamper(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-1",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    monkeypatch.setattr(
        memory_index_module,
        "MEMORY_INDEX_STREAMING_AUDIT_THRESHOLD",
        0,
    )
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    manifest = index.build(as_of=cutoff, promote_current=False)
    database_path = tmp_path / manifest.database.artifact_path
    connection = duckdb.connect(str(database_path))
    connection.execute("LOAD vss")
    connection.execute(
        "UPDATE memberships SET independent_unit_id = 'TAMPERED' "
        "WHERE record_id = 'REC-1'"
    )
    connection.execute("CHECKPOINT")
    connection.close()
    manifest_path = database_path.parent / "manifest.json"
    payload = read_json(manifest_path)
    payload["database"]["sha256"] = file_sha256(database_path)
    write_json(manifest_path, payload)

    inspection = inspect_memory_snapshot(tmp_path, manifest.snapshot_id)

    assert inspection["status"] == "invalid"
    assert "database_memberships_recomputed_mismatch" in inspection["errors"]


def test_runtime_search_rejects_database_tamper(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-TAMPER",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    manifest = index.build(as_of=cutoff)
    database_path = tmp_path / manifest.database.artifact_path
    with database_path.open("ab") as stream:
        stream.write(b"tamper")

    with pytest.raises(ValueError, match="database hash mismatch"):
        index.search_cells("supply contract", cutoff_at=cutoff)


def test_record_generation_manifest_tracks_full_envelope_changes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    available = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-GENERATION",
            available_from=available,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    store = BrainRecordStore(tmp_path)
    first = store.rebuild_indexes()
    records[0] = records[0].model_copy(update={"confidence_label": "low"})
    second = store.rebuild_indexes()

    assert first["schema_version"] == "nslab.record_index_manifest.v2"
    assert first["full_envelope_root_sha256"] != second["full_envelope_root_sha256"]
    assert second["generation_history"] == {
        first["generation_root_sha256"]: available.isoformat()
    }


def test_streaming_build_is_batch_size_and_noop_stable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            f"REC-{index:03d}",
            available_from=cutoff,
            ticker=f"{index:06d}",
            response_class="POSITIVE" if index % 2 else "NEGATIVE",
            high_return_pct=12.0 if index % 2 else -2.0,
            company_name=f"issuer {index} supply contract",
        )
        for index in range(129)
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    first = ProductionMemoryIndex(
        tmp_path / "first",
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
        embedding_batch_size=1,
    ).build(as_of=cutoff, promote_current=False)
    second_index = ProductionMemoryIndex(
        tmp_path / "second",
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
        embedding_batch_size=127,
    )
    second = second_index.build(as_of=cutoff, promote_current=False)
    repeated = second_index.build(as_of=cutoff, promote_current=False)

    assert second.snapshot_id == first.snapshot_id
    assert second.cell_entries.sha256 == first.cell_entries.sha256
    assert second.memberships.sha256 == first.memberships.sha256
    assert repeated.snapshot_id == second.snapshot_id


def test_stage_only_identical_snapshot_is_reused_without_rebuilding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-STAGED-REUSE",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    first = index.build(as_of=cutoff, stage_only=True)

    monkeypatch.setattr(
        index,
        "_build_streaming_database",
        lambda *args, **kwargs: pytest.fail("identical snapshot was rebuilt"),
    )

    repeated = index.build(as_of=cutoff, stage_only=True)

    assert repeated == first
    assert not index.as_of_registry_path.exists()
    assert not index.current_pointer_path.exists()


def test_stage_only_reuse_fails_closed_for_a_corrupt_matching_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-STAGED-CORRUPT",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    first = index.build(as_of=cutoff, stage_only=True)
    database_path = tmp_path / first.database.artifact_path
    database_path.write_bytes(database_path.read_bytes() + b"corrupt")

    with pytest.raises(ValueError, match="matching reusable memory snapshot is invalid"):
        index.build(as_of=cutoff, stage_only=True)


def test_historical_cutoff_detects_late_backfill(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-EARLY",
            available_from=datetime(2030, 1, 5, 12, 0, tzinfo=KST),
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    first = index.build(as_of=cutoff)

    records.append(
        _record(
            "REC-BACKFILL",
            available_from=datetime(2030, 1, 8, 12, 0, tzinfo=KST),
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-2.0,
        )
    )
    BrainRecordStore(tmp_path).rebuild_indexes()
    stale = inspect_memory_snapshot(tmp_path, first.snapshot_id)
    second = index.build(as_of=cutoff)

    assert stale["status"] == "stale"
    assert "source_record_hashes_stale" in stale["errors"]
    assert second.snapshot_id != first.snapshot_id
    assert second.as_of_cutoff == cutoff
    assert index.resolve_snapshot(cutoff_at=cutoff).snapshot_id == second.snapshot_id


def test_staged_and_historical_snapshots_do_not_regress_active_pointer(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    current_cutoff = datetime(2030, 1, 11, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-OLD",
            available_from=old_cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        ),
        _record(
            "REC-CURRENT",
            available_from=current_cutoff,
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-2.0,
        ),
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    monkeypatch.setattr(memory_index_module, "now_kst", lambda: current_cutoff)
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    current = index.build()
    pointer_before = read_json(index.current_pointer_path)
    historical = index.build(as_of=old_cutoff)

    assert historical.snapshot_id != current.snapshot_id
    pointer_after = read_json(index.current_pointer_path)
    assert pointer_after["snapshot_id"] == pointer_before["snapshot_id"]
    assert pointer_after["manifest_sha256"] == pointer_before["manifest_sha256"]
    staged_root = tmp_path / "staged"
    staged_index = ProductionMemoryIndex(
        staged_root,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    staged_index.build(as_of=current_cutoff, stage_only=True)
    assert not staged_index.as_of_registry_path.exists()
    assert not staged_index.current_pointer_path.exists()


def test_activation_rejects_registry_cutoff_relabeling(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 10, 12, 0, tzinfo=KST)
    records = [
        _record(
            "REC-CUTOFF",
            available_from=cutoff,
            ticker="000001",
            response_class="POSITIVE",
            high_return_pct=12.0,
        )
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_RealLikeEmbeddingProvider(),
        production=True,
    )
    manifest = index.build(as_of=cutoff, promote_current=False)

    with pytest.raises(ValueError, match="requested cutoff does not match"):
        index.activate(
            manifest,
            requested_cutoff=datetime(2030, 1, 1, 12, 0, tzinfo=KST),
        )
