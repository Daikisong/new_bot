from __future__ import annotations

import json
from datetime import date, datetime

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import ContextManifest, NewsItem, PriceSnapshot
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
