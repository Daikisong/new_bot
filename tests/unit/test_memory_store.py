from __future__ import annotations

from datetime import date, datetime, time

from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.retrieval.embedding import DeterministicHashEmbeddingProvider
from news_scalping_lab.retrieval.store import LocalRetrievalStore, inspect_vector_index
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST


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
