from __future__ import annotations

from datetime import datetime

from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import NewsItem
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.utils import KST


def test_web_queries_include_required_semantic_research_perspectives(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    analyzer = DailyAnalyzer(settings)
    item = NewsItem(
        event_id="EVT-query",
        row_number=1,
        published_at=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
        title="SampleCo announces new facility catalyst",
        body="The query builder should generate semantic research perspectives.",
        source_id="news.csv:1",
    )

    queries = analyzer._build_web_queries([item])

    assert len(queries) == len(set(queries))
    joined = "\n".join(queries)
    assert "verify listing ticker novelty direct relation SampleCo" in joined
    assert "beneficiary supply chain infrastructure relationship SampleCo" in joined
    assert "D-1 absorption continuation leader review SampleCo" in joined
    assert "causal mechanism analogs" in joined
    assert "market narrative propagation analogs" in joined
    assert "direct company news versus policy-derived beneficiary cases" in joined
    assert "successful analog cases" in joined
    assert "failed analog cases" in joined
    assert "near misses candidates not selected as leaders" in joined
    assert "counterexamples superficially similar opposite outcome" in joined
    assert "unexpected leader selection in first-seen policy or industry event" in joined
    assert "theme formation failures" in joined


def test_web_queries_keep_open_world_fallback_without_news_items(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    analyzer = DailyAnalyzer(settings)

    queries = analyzer._build_web_queries([])

    assert queries[0] == "open-world market catalyst company discovery"
    assert "counterexamples superficially similar opposite outcome" in queries
