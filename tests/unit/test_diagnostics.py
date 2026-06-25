from __future__ import annotations

from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.schemas import export_json_schemas
from news_scalping_lab.diagnostics import build_doctor_report
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.utils import write_json
from news_scalping_lab.warehouse import WarehouseStore


def test_doctor_report_includes_environment_api_schema_vector_and_warehouse(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        llm_provider="openai",
        stock_web_path=tmp_path / "stock-web",
        stock_web_cache_enabled=True,
    )
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-diagnostic"
    settings.llm.embedding_model = "embed-diagnostic"
    settings.llm.reasoning_effort = "medium"
    settings.llm.max_output_tokens = 8192
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    LocalRetrievalStore(tmp_path).rebuild_index()
    atlas = tmp_path / "stock-web" / "atlas"
    atlas.mkdir(parents=True)
    write_json(
        atlas / "manifest.json",
        {
            "source_name": "stock-web-test",
            "source_repo_url": "https://example.test/stock-web",
            "calibration_shard_root": "atlas/ohlcv_tradable_by_symbol_year",
        },
    )
    write_json(
        atlas / "schema.json",
        {"tradable_shard_columns": {"d": "date", "o": "open", "c": "close"}},
    )
    WarehouseStore(tmp_path).rebuild_all()
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "openai")
    monkeypatch.setenv("NSLAB_LLM_REASONING_EFFORT", "medium")

    report = build_doctor_report(settings)

    assert report["providers"]["llm"] == "openai"
    assert report["llm_model"] == {
        "provider": "openai",
        "model": "gpt-diagnostic",
        "embedding_model": "embed-diagnostic",
        "reasoning_effort": "medium",
        "max_output_tokens": 8192,
    }
    assert report["environment"]["OPENAI_API_KEY"] == {"set": True, "value": "***"}
    assert report["environment"]["NSLAB_LLM_PROVIDER"] == {
        "set": True,
        "value": "openai",
    }
    assert report["environment"]["NSLAB_LLM_REASONING_EFFORT"] == {
        "set": True,
        "value": "medium",
    }
    assert report["api_connections"]["openai"] == {
        "required": True,
        "configured": True,
        "status": "configured_not_called",
    }
    assert report["stock_web"]["path_exists"] is True
    assert report["stock_web"]["schema"]["source_name"] == "stock-web-test"
    assert report["warehouse"]["status"] == "ok"
    assert "research_episodes.parquet" in report["warehouse"]["counts"]
    assert report["vector_index"]["exists"] is True
    assert report["vector_index"]["status"] == "current"
    assert report["vector_index"]["record_count"] == 0
    assert report["vector_index"]["embedding_method"] == "deterministic_hashing_v1"
    assert report["schemas"]["file_count"] >= 12
    assert report["schemas"]["versions"]["research_episode"] == "nslab.research_episode.v1"
