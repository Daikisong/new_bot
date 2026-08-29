from datetime import datetime

import pytest

from news_scalping_lab.contracts.models import NewsItem
from news_scalping_lab.inference.event_clustering import (
    cluster_news_events,
    event_clustering_from_payload,
    event_clustering_payload,
    open_world_cluster_inputs,
)
from news_scalping_lab.utils import KST


class ControlledEmbeddingProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        assert purpose == "daily_event_clustering"
        self.calls.append(texts)
        if self.fail:
            raise RuntimeError("embedding unavailable")
        return [[1.0, 0.0] for _text in texts]


@pytest.mark.asyncio
async def test_semantic_clusters_cover_every_row_without_merging_different_numbers() -> None:
    items = [
        _item(1, "가상기업, 100억원 공급계약 체결", "공급계약을 확정했다.", "08:00:00"),
        _item(2, "가상기업, 100억원 공급계약 체결", "공급계약을 확정했다.", "08:01:00"),
        _item(3, "가상기업 100억원 규모 공급계약 확정", "계약 체결을 발표했다.", "08:02:00"),
        _item(4, "가상기업, 200억원 공급계약 체결", "다른 규모 계약이다.", "08:03:00"),
        _item(5, "마감 뒤 공개된 뉴스", "cutoff 이후 행", "09:10:00"),
    ]
    provider = ControlledEmbeddingProvider()

    result = await cluster_news_events(
        items,
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=provider,
        embedding_batch_size=2,
        similarity_threshold=0.9,
    )

    assert result.input_row_count == 5
    assert result.cutoff_safe_row_count == 4
    assert result.audit_only_row_count == 1
    assert result.exact_duplicate_count == 1
    assert result.semantic_duplicate_count == 1
    assert len(result.material_clusters) == 2
    assert [item.row_number for item in result.material_clusters[0].members] == [1, 2, 3]
    assert [item.row_number for item in result.material_clusters[1].members] == [4]
    assert result.clusters[-1].disposition == "AUDIT_ONLY"
    assert [len(call) for call in provider.calls] == [2, 1]
    assert sum(len(cluster.members) for cluster in result.clusters) == len(items)
    open_world_inputs = open_world_cluster_inputs(result)
    assert len(open_world_inputs) == 2
    assert len(open_world_inputs[0].member_news) == 2
    assert any("계약 체결을 발표했다" in text for text in open_world_inputs[0].member_news)
    restored = event_clustering_from_payload(event_clustering_payload(result))
    assert event_clustering_payload(restored) == event_clustering_payload(result)
    assert [
        item.row_number for cluster in restored.clusters for item in cluster.members
    ] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_without_dropping_rows() -> None:
    result = await cluster_news_events(
        [
            _item(1, "첫 뉴스", "서로 다른 첫 내용", "08:00:00"),
            _item(2, "둘째 뉴스", "서로 다른 둘째 내용", "08:01:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(fail=True),
        embedding_batch_size=10,
        similarity_threshold=0.99,
    )

    assert result.embedding_status == "DETERMINISTIC_FALLBACK"
    assert result.warnings == ("semantic_embedding_fallback:RuntimeError",)
    assert sum(len(cluster.members) for cluster in result.clusters) == 2
    assert all(cluster.disposition == "MATERIAL_FULL_RETRIEVAL" for cluster in result.clusters)


@pytest.mark.asyncio
async def test_semantic_clustering_keeps_different_issuers_and_predicates_separate() -> None:
    provider = ControlledEmbeddingProvider()
    result = await cluster_news_events(
        [
            _item(1, "가상기업 공급계약 확정", "새 고객과 계약했다.", "08:00:00"),
            _item(2, "가상기업 대표이사 사임", "대표이사가 물러났다.", "08:01:00"),
            _item(3, "다른기업 공급계약 확정", "새 고객과 계약했다.", "08:02:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=provider,
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert len(result.material_clusters) == 3
    assert result.semantic_duplicate_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("속보 가상기업 100억원 공급계약 체결", "속보 다른기업 100억원 공급계약 체결"),
        ("ABC Bio supply contract signed", "ABC Tech supply contract signed"),
        ("Korea Alpha 100억원 계약 체결", "Korea Beta 100억원 계약 체결"),
        ("주식회사 가상기업 공급계약 체결", "주식회사 다른기업 공급계약 체결"),
    ],
)
async def test_semantic_clustering_keeps_multi_token_issuers_separate(
    left: str,
    right: str,
) -> None:
    result = await cluster_news_events(
        [
            _item(1, left, "동일한 사건 설명", "08:00:00"),
            _item(2, right, "동일한 사건 설명", "08:01:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(),
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert len(result.material_clusters) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("affirming", "reversing"),
    [
        ("가상기업 공급계약 체결", "가상기업 공급계약 해지"),
        ("가상기업 유상증자 결정", "가상기업 유상증자 철회"),
        ("가상기업 사업 추진", "가상기업 사업 무산"),
        ("가상기업 허가 신청", "가상기업 허가 반려"),
    ],
)
async def test_semantic_clustering_never_merges_reversed_event_states(
    affirming: str,
    reversing: str,
) -> None:
    result = await cluster_news_events(
        [
            _item(1, affirming, "같은 회사의 같은 사건 설명", "08:00:00"),
            _item(2, reversing, "같은 회사의 같은 사건 설명", "08:01:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(),
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert len(result.material_clusters) == 2


@pytest.mark.asyncio
async def test_semantic_clustering_normalizes_equivalent_korean_money_units() -> None:
    result = await cluster_news_events(
        [
            _item(1, "가상기업 1조원 공급계약 체결", "계약 확정", "08:00:00"),
            _item(2, "가상기업 10000억원 공급계약 확정", "계약 체결", "08:01:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(),
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert len(result.material_clusters) == 1


@pytest.mark.asyncio
async def test_shared_year_never_masks_conflicting_contract_amounts() -> None:
    result = await cluster_news_events(
        [
            _item(1, "가상기업 2026년 100억원 공급계약 체결", "계약 확정", "08:00:00"),
            _item(2, "가상기업 2026년 200억원 공급계약 체결", "계약 확정", "08:01:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(),
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert len(result.material_clusters) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("left_body", "right_body"),
    [
        ("A사에 장비 공급", "B사에 장비 공급"),
        ("A사 대상 장비 공급", "B사 대상 장비 공급"),
        ("equipment supplied to Alpha", "equipment supplied to Beta"),
    ],
)
async def test_semantic_clustering_keeps_different_counterparties_separate(
    left_body: str,
    right_body: str,
) -> None:
    result = await cluster_news_events(
        [
            _item(1, "가상기업 100억원 공급계약 체결", left_body, "08:00:00"),
            _item(2, "가상기업 100억원 공급계약 체결", right_body, "08:01:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(),
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert len(result.material_clusters) == 2


@pytest.mark.asyncio
async def test_semantic_cluster_member_context_is_bounded_without_dropping_rows() -> None:
    items = [
        _item(
            row,
            "가상기업 공급계약 체결 보도",
            "같은 사건의 서로 다른 기사 본문 " + ("추가" * row),
            "08:00:00",
        )
        for row in range(1, 66)
    ]
    result = await cluster_news_events(
        items,
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(),
        embedding_batch_size=128,
        similarity_threshold=0.9,
        max_semantic_variants=16,
    )

    assert sum(len(cluster.members) for cluster in result.material_clusters) == 65
    assert all(
        len(cluster.member_news) <= 16
        for cluster in open_world_cluster_inputs(result)
    )


@pytest.mark.asyncio
async def test_exact_duplicate_fingerprint_includes_body_tail_after_four_kilobytes() -> None:
    common = "공통 본문 " * 600
    result = await cluster_news_events(
        [
            _item(1, "가상기업 계약 공시", common + "A사 대상", "08:00:00"),
            _item(2, "가상기업 계약 공시", common + "B사 대상", "08:01:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(),
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert result.exact_duplicate_count == 0
    assert len(result.material_clusters) == 2


@pytest.mark.asyncio
async def test_exact_whitespace_variants_use_one_prompt_member_but_keep_all_rows() -> None:
    result = await cluster_news_events(
        [
            _item(1, "가상기업 계약 체결", "같은 본문", "08:00:00"),
            _item(2, " 가상기업  계약 체결 ", "같은   본문", "08:01:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(),
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert len(result.material_clusters[0].members) == 2
    assert len(open_world_cluster_inputs(result)[0].member_news) == 1


@pytest.mark.asyncio
async def test_breaking_correction_and_wrap_up_merge_only_when_numbers_agree() -> None:
    result = await cluster_news_events(
        [
            _item(1, "[속보] 가상기업 100억 계약", "계약 체결", "08:00:00"),
            _item(2, "가상기업 100억원 계약 확정", "종합 기사", "08:01:00"),
            _item(3, "[정정] 가상기업 120억원 계약 확정", "금액 정정", "08:02:00"),
        ],
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=ControlledEmbeddingProvider(),
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert len(result.material_clusters) == 2
    assert [item.row_number for item in result.material_clusters[0].members] == [1, 2]
    assert [item.row_number for item in result.material_clusters[1].members] == [3]


@pytest.mark.asyncio
async def test_more_than_one_thousand_rows_are_embedded_in_bounded_batches() -> None:
    provider = ControlledEmbeddingProvider()
    items = [
        _item(
            row,
            f"가상기업{row:04d} 고유 사건 발표",
            f"서로 다른 사건 본문 {row:04d}",
            "08:00:00",
        )
        for row in range(1, 1002)
    ]

    result = await cluster_news_events(
        items,
        window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=provider,
        embedding_batch_size=128,
        similarity_threshold=0.9,
    )

    assert result.input_row_count == 1001
    assert sum(len(cluster.members) for cluster in result.clusters) == 1001
    assert [len(call) for call in provider.calls] == [128] * 7 + [105]


@pytest.mark.asyncio
async def test_all_out_of_window_rows_are_audit_only_without_embedding_calls() -> None:
    provider = ControlledEmbeddingProvider()
    result = await cluster_news_events(
        [
            _item(1, "이전 뉴스", "윈도우 이전", "06:00:00"),
            _item(2, "마감 뒤 뉴스", "cutoff 이후", "09:30:00"),
        ],
        window_start_at=datetime(2030, 1, 10, 7, 0, tzinfo=KST),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        embedding_provider=provider,
        embedding_batch_size=10,
        similarity_threshold=0.9,
    )

    assert not result.material_clusters
    assert result.audit_only_row_count == 2
    assert provider.calls == []


def _item(row: int, title: str, body: str, time_value: str) -> NewsItem:
    hour, minute, second = (int(part) for part in time_value.split(":"))
    return NewsItem(
        event_id=f"EV-{row}",
        row_number=row,
        published_at=datetime(2030, 1, 10, hour, minute, second, tzinfo=KST),
        title=title,
        body=body,
        source_id=f"SRC-{row}",
    )
