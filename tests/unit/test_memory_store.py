from __future__ import annotations

from datetime import date, datetime, time

from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.records.models import (
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import DeterministicHashEmbeddingProvider
from news_scalping_lab.retrieval.store import LocalRetrievalStore, inspect_vector_index
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, canonical_json, sha256_text


def _episode(
    episode_id: str,
    *,
    summary: str,
    mechanism: str,
    available_at: datetime,
) -> ResearchEpisode:
    trade_day = date(2030, 1, 9)
    return ResearchEpisode(
        episode_id=episode_id,
        trade_date=trade_day,
        cutoff_at=datetime.combine(trade_day, time(8, 59, 59), tzinfo=KST),
        created_at=datetime.combine(trade_day, time(16, 0, 0), tzinfo=KST),
        research_version="test-v1",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(
            summary=summary,
            open_world_mechanisms=[mechanism],
        ),
        available_from=available_at,
    )


def _store_retrieval_records(tmp_path) -> None:
    records = [
        _retrieval_record(
            "BRAIN-REC-DIRECT",
            record_type="supervised_direct_event_case",
            ticker="000001",
            theme_id="theme-direct",
            response_class="positive_high10",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-COUNTER",
            record_type="counterexample",
            ticker="000002",
            theme_id="theme-counter",
            response_class="negative_control",
            available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-GEN-ERROR",
            record_type="candidate_generation_error_case",
            ticker="000003",
            theme_id="theme-error",
            response_class="candidate_missed",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-RANK-ERROR",
            record_type="candidate_ranking_error_case",
            ticker="000004",
            theme_id="theme-error",
            response_class="leader_missed",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-ROW-ERROR",
            record_type="row_disposition_error_case",
            ticker="000005",
            theme_id="theme-error",
            response_class="row_misclassified",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
        _retrieval_record(
            "BRAIN-REC-ENTITY-ERROR",
            record_type="entity_resolution_error_case",
            ticker="000006",
            theme_id="theme-error",
            response_class="entity_misresolved",
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        ),
    ]
    raw_payload = "\n".join(record.model_dump_json() for record in records)
    raw_sha = sha256_text(raw_payload)
    source_path = tmp_path / "record_retrieval_bundle.md"
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(tmp_path).store_bundle(
        source_path=source_path,
        envelope=ResearchBundleEnvelope(
            bundle_schema_version="nslab.research_bundle.v11",
            manifest_schema_version="nslab.bundle_manifest.v11",
            episode_schema_version="nslab.research_episode.v11",
            episode_id="NSLAB-20300110-RETRIEVAL",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_bundle_sha256=raw_sha,
            raw_block_hashes={"brain_delta.jsonl": raw_sha},
            raw_block_counts={"brain_delta.jsonl": len(records)},
            provenance_closure_status="closed",
            adapter_name="unit-test",
            import_status="imported",
        ),
        index=NormalizedEpisodeIndex(
            episode_id="NSLAB-20300110-RETRIEVAL",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_block_names=["brain_delta.jsonl"],
            record_ids=[record.record_id for record in records],
            record_count_by_type={
                "supervised_direct_event_case": 1,
                "counterexample": 1,
                "candidate_generation_error_case": 1,
                "candidate_ranking_error_case": 1,
                "row_disposition_error_case": 1,
                "entity_resolution_error_case": 1,
            },
            training_eligible_record_count=5,
            source_ids=["SRC-RETRIEVAL"],
        ),
        records=records,
        raw_blocks={"brain_delta.jsonl": raw_payload},
        validation_report={"passed": True},
    )


def _retrieval_record(
    record_id: str,
    *,
    record_type: str,
    ticker: str,
    theme_id: str,
    response_class: str,
    available_from: datetime,
) -> BrainRecordEnvelope:
    payload = {
        "record_id": record_id,
        "record_type": record_type,
        "episode_id": "NSLAB-20300110-RETRIEVAL",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "training_target": "direct_event_response",
        "evidence_phase": "BLIND_SAFE",
        "ticker": ticker,
        "company_name": f"{ticker} Test Co",
        "theme_id": theme_id,
        "path_type": "single_event",
        "response_class": response_class,
    }
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id="NSLAB-20300110-RETRIEVAL",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target="direct_event_response",
        evidence_phase="BLIND_SAFE",
        training_eligible=record_type != "counterexample",
        eligibility_reason="unit test retrieval record",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=["SRC-RETRIEVAL"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )


def test_local_memory_store_adds_and_lists_accepted_episode(tmp_path) -> None:
    memory = LocalRetrievalStore(tmp_path)
    episode = _episode(
        "EP-memory",
        summary="Accepted memory summary.",
        mechanism="current event -> open-world path",
        available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
    )

    memory.add_episode(episode)

    assert [item.episode_id for item in memory.list_all_episodes()] == ["EP-memory"]
    assert (tmp_path / "research" / "accepted" / "EP-memory.json").exists()
    assert (tmp_path / "memory" / "vector_index" / "manifest.json").exists()
    assert (tmp_path / "memory" / "vector_index" / "episodes.jsonl").exists()
    index = memory.inspect_index()
    assert index["status"] == "current"
    assert index["record_count"] == 1


def test_local_memory_store_filters_available_as_of_cutoff(tmp_path) -> None:
    memory = LocalRetrievalStore(tmp_path)
    available = _episode(
        "EP-available",
        summary="Available before cutoff.",
        mechanism="available mechanism",
        available_at=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
    )
    future = _episode(
        "EP-future",
        summary="Unavailable after cutoff.",
        mechanism="future mechanism",
        available_at=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
    )
    memory.add_episode(available)
    memory.add_episode(future)

    as_of = memory.get_available_as_of(datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST))

    assert [episode.episode_id for episode in as_of] == ["EP-available"]


def test_semantic_search_keeps_research_available_without_exact_keyword_gate(tmp_path) -> None:
    memory = LocalRetrievalStore(tmp_path)
    memory.add_episode(
        _episode(
            "EP-abstract",
            summary="Past case about indirect supply-chain beneficiary discovery.",
            mechanism="new catalyst -> infer adjacent infrastructure demand",
            available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
    )

    assert memory.search_semantic("unseen wording with no shared tokens", limit=5) == [
        "EP-abstract"
    ]
    assert LocalRetrievalStore(tmp_path, force_empty=True).search_semantic(
        "unseen wording with no shared tokens", limit=5
    ) == []


def test_record_retrieval_supports_structural_filters(tmp_path) -> None:
    _store_retrieval_records(tmp_path)
    memory = LocalRetrievalStore(tmp_path)
    memory.rebuild_index()

    assert memory.search_records(
        "unseen wording",
        record_type="supervised_direct_event_case",
        training_target="direct_event_response",
        trade_date_from="2030-01-10",
        trade_date_to="2030-01-10",
        ticker="000001",
        company_name="000001 Test Co",
        theme_id="theme-direct",
        path_type="single_event",
        response_class="positive_high10",
        evidence_phase="BLIND_SAFE",
        confidence_label="low",
        training_eligible=True,
        available_from=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    ) == ["BRAIN-REC-DIRECT"]
    assert memory.search_records(
        "unseen wording",
        record_type="counterexample",
        training_eligible=False,
        available_from=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    ) == []
    assert memory.search_records(
        "unseen wording",
        record_type="counterexample",
        training_eligible=False,
        available_from=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
    ) == ["BRAIN-REC-COUNTER"]
    assert memory.search_records(
        "unseen wording",
        record_type="supervised_direct_event_case",
        trade_date_from="2030-01-11",
        trade_date_to="2030-01-11",
        training_eligible=True,
        available_from=datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST),
    ) == []
    assert set(
        memory.search_records(
            "unseen wording",
            record_type=(
                "candidate_generation_error_case",
                "candidate_ranking_error_case",
                "row_disposition_error_case",
                "entity_resolution_error_case",
            ),
            theme_id="theme-error",
            available_from=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            limit=10,
        )
    ) == {
        "BRAIN-REC-GEN-ERROR",
        "BRAIN-REC-RANK-ERROR",
        "BRAIN-REC-ROW-ERROR",
        "BRAIN-REC-ENTITY-ERROR",
    }


def test_vector_index_marks_stale_when_accepted_episode_changes_without_rebuild(tmp_path) -> None:
    memory = LocalRetrievalStore(tmp_path)
    memory.add_episode(
        _episode(
            "EP-indexed",
            summary="Indexed memory summary.",
            mechanism="indexed mechanism",
            available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
    )
    store = ResearchStore(tmp_path)
    second = _episode(
        "EP-new",
        summary="New accepted memory summary.",
        mechanism="new mechanism",
        available_at=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    store.save_episode(second)
    store.accept(second.episode_id)

    stale = inspect_vector_index(tmp_path)
    rebuilt = memory.rebuild_index()

    assert stale["status"] == "stale"
    assert rebuilt["record_count"] == 2
    assert inspect_vector_index(tmp_path)["status"] == "current"


def test_local_retrieval_store_uses_injected_embedding_provider(tmp_path) -> None:
    class RecordingEmbeddingProvider(DeterministicHashEmbeddingProvider):
        embedding_method = "recording_hashing_v1"

        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(list(texts))
            return super().embed_texts(texts)

    provider = RecordingEmbeddingProvider()
    memory = LocalRetrievalStore(tmp_path, embedding_provider=provider)
    memory.add_episode(
        _episode(
            "EP-provider",
            summary="Provider-backed memory summary.",
            mechanism="provider mechanism",
            available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
    )

    result = memory.search_semantic("provider query", limit=5)
    index = memory.inspect_index()

    assert result == ["EP-provider"]
    assert index["embedding_method"] == "recording_hashing_v1"
    assert provider.calls[0][0].startswith("EP-provider")
    assert provider.calls[-1] == ["provider query"]


def test_vector_index_supports_provider_specific_dimensions(tmp_path) -> None:
    class TwoDimensionalEmbeddingProvider:
        embedding_method = "two_dimensional_provider_v1"
        dimensions = 2

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(text)), 1.0] for text in texts]

        async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
            return self.embed_texts(texts)

    memory = LocalRetrievalStore(
        tmp_path,
        embedding_provider=TwoDimensionalEmbeddingProvider(),
    )
    memory.add_episode(
        _episode(
            "EP-two-dim",
            summary="Two dimensional provider summary.",
            mechanism="provider-specific embedding dimensions",
            available_at=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
    )

    index = memory.inspect_index()

    assert index["status"] == "current"
    assert index["embedding_method"] == "two_dimensional_provider_v1"
    assert index["dimensions"] == 2
    assert memory.search_semantic("two dimensional query", limit=1) == ["EP-two-dim"]
