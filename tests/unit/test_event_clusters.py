from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import (
    ContextManifest,
    NewsItem,
    NewsNoveltyFinding,
    NewsNoveltyReview,
    PriceSnapshot,
)
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.utils import KST, sha256_text


def test_event_cluster_artifact_groups_exact_normalized_duplicates(tmp_path) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    manifest = ContextManifest(
        run_id="RUN-event-cluster",
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff,
        as_of=cutoff,
        news_window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        news_window_end_at=cutoff,
        accepted_episode_count=0,
        swept_episode_count=0,
        price_snapshot=PriceSnapshot(
            source_name="mock",
            as_of=cutoff,
            allowed_through=date(2030, 1, 9),
        ),
    )
    items = [
        _news_item(
            row_number=2,
            published_at=datetime(2030, 1, 10, 8, 0, tzinfo=KST),
            title="Current  Catalyst",
            body="Repeated update",
        ),
        _news_item(
            row_number=3,
            published_at=datetime(2030, 1, 10, 8, 1, tzinfo=KST),
            title=" current catalyst ",
            body="Repeated     update",
        ),
        _news_item(
            row_number=4,
            published_at=datetime(2030, 1, 10, 8, 2, tzinfo=KST),
            title="Another catalyst",
            body="Different update",
        ),
    ]

    analyzer = DailyAnalyzer(Settings(project_root=tmp_path))
    analyzer._write_event_cluster_artifact(
        news_items=items,
        cutoff_at=cutoff,
        manifest=manifest,
    )

    assert manifest.event_cluster_artifact is not None
    assert manifest.event_cluster_count == 2
    assert manifest.event_cluster_summary == {
        "source_row_count": 3,
        "cluster_count": 2,
        "exact_duplicate_count": 1,
        "exact_duplicate_cluster_count": 1,
        "semantic_duplicate_cluster_count": 0,
        "cluster_method": "exact_normalized_title_body_v1",
        "novelty_review_required": True,
    }
    event_cluster_path = tmp_path / manifest.event_cluster_artifact
    event_cluster_text = event_cluster_path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in event_cluster_text.splitlines() if line.strip()]

    assert sha256_text(event_cluster_text) == manifest.event_cluster_sha256
    assert [row["row_numbers"] for row in rows] == [[2, 3], [4]]
    assert rows[0]["cluster_id"].startswith("EVCL-")
    assert rows[0]["row_count"] == 2
    assert rows[0]["exact_duplicate_count"] == 1
    assert rows[0]["last_published_at_before_cutoff"] == "2030-01-10T08:01:00+09:00"
    assert rows[0]["time_verified"] is True
    assert rows[0]["novelty"] == "unclear"
    assert rows[0]["requires_llm_novelty_review"] is True
    assert "title" not in rows[0]
    assert "body" not in rows[0]


def test_news_novelty_review_rejects_cutoff_after_evidence_time(tmp_path) -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    manifest = _manifest(cutoff)
    analyzer = DailyAnalyzer(Settings(project_root=tmp_path))
    analyzer._write_event_cluster_artifact(
        news_items=[
            _news_item(
                row_number=2,
                published_at=datetime(2030, 1, 10, 8, 0, tzinfo=KST),
                title="Current catalyst",
                body="Pre-cutoff update",
            )
        ],
        cutoff_at=cutoff,
        manifest=manifest,
    )
    cluster = json.loads(
        (tmp_path / manifest.event_cluster_artifact).read_text(encoding="utf-8").splitlines()[0]
    )
    review = NewsNoveltyReview(
        run_id=manifest.run_id,
        prompt_version="test",
        prompt_sha256="test",
        created_at=datetime(2030, 1, 10, 8, 1, tzinfo=KST),
        cutoff_at=cutoff,
        review_mode="NEWS_ONLY_STRICT",
        cluster_count=1,
        reviewed_cluster_count=1,
        findings=[
            NewsNoveltyFinding(
                cluster_id=cluster["cluster_id"],
                cluster_index=1,
                first_public_evidence_at=datetime(2030, 1, 10, 9, 1, tzinfo=KST),
                evidence_source_ids=cluster["source_ids"],
            )
        ],
    )

    with pytest.raises(ValueError, match="cutoff-after first_public_evidence_at"):
        analyzer._normalize_news_novelty_review(
            review,
            manifest=manifest,
            cutoff_at=cutoff,
            prompt_sha256="test",
        )


def _manifest(cutoff: datetime) -> ContextManifest:
    return ContextManifest(
        run_id="RUN-event-cluster",
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff,
        as_of=cutoff,
        news_window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        news_window_end_at=cutoff,
        accepted_episode_count=0,
        swept_episode_count=0,
        price_snapshot=PriceSnapshot(
            source_name="mock",
            as_of=cutoff,
            allowed_through=date(2030, 1, 9),
        ),
    )


def _news_item(
    *,
    row_number: int,
    published_at: datetime,
    title: str,
    body: str,
) -> NewsItem:
    return NewsItem(
        event_id=f"EVT-{row_number}",
        row_number=row_number,
        published_at=published_at,
        collected_at=published_at,
        title=title,
        body=body,
        source_id=f"SRC-{row_number}",
    )
