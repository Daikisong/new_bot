from __future__ import annotations

import json
from datetime import date, datetime

from typer.testing import CliRunner

from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.cli import app
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.contracts.schemas import SCHEMA_MODELS, export_json_schemas
from news_scalping_lab.diagnostics import build_doctor_report
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, write_json
from news_scalping_lab.warehouse import WarehouseStore

RUNNER = CliRunner()


def test_doctor_report_includes_environment_api_schema_vector_and_warehouse(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        llm_provider="openai",
        web_provider="brave",
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
    monkeypatch.setenv("NSLAB_WEB_PROVIDER", "brave")
    monkeypatch.setenv("NSLAB_LLM_REASONING_EFFORT", "medium")
    monkeypatch.setenv("NSLAB_LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-secret")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics._openai_sdk_status",
        lambda: {
            "available": True,
            "version": "fake-openai",
            "async_client_available": True,
            "error": None,
        },
    )

    report = build_doctor_report(settings)

    assert report["readiness"] == {
        "passed": True,
        "status": "ready",
        "finding_count": 0,
        "findings": [],
    }
    assert report["providers"]["llm"] == "openai"
    assert report["providers"]["web"] == "brave"
    assert report["llm_model"] == {
        "provider": "openai",
        "model": "gpt-diagnostic",
        "embedding_model": "embed-diagnostic",
        "reasoning_effort": "medium",
        "max_output_tokens": 8192,
        "max_retries": 0,
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
    assert report["environment"]["NSLAB_LLM_MAX_RETRIES"] == {
        "set": True,
        "value": "2",
    }
    assert report["environment"]["BRAVE_SEARCH_API_KEY"] == {
        "set": True,
        "value": "***",
    }
    assert report["api_connections"]["openai"] == {
        "required": True,
        "configured": True,
        "sdk": {
            "available": True,
            "version": "fake-openai",
            "async_client_available": True,
            "error": None,
        },
        "status": "configured_not_called",
    }
    assert report["api_connections"]["brave_search"] == {
        "required": True,
        "configured": True,
        "status": "configured_not_called",
    }
    assert report["stock_web"]["path_exists"] is True
    assert report["stock_web"]["effective_path"] == (tmp_path / "stock-web").as_posix()
    assert report["stock_web"]["effective_path_exists"] is True
    assert report["stock_web"]["effective_path_source"] == "path"
    assert report["stock_web"]["schema"]["source_name"] == "stock-web-test"
    assert report["warehouse"]["status"] == "ok"
    assert "research_episodes.parquet" in report["warehouse"]["counts"]
    assert report["database"]["engine"] == "duckdb"
    assert report["database"]["available"] is True
    assert isinstance(report["database"]["version"], str)
    assert report["database"]["connection"] == "ok"
    assert report["database"]["warehouse_path"] == (tmp_path / "warehouse").as_posix()
    assert report["database"]["warehouse_path_exists"] is True
    assert report["database"]["warehouse_counts_readable"] is True
    assert report["database"]["status"] == "ok"
    assert report["brain"]["accepted_episode_count"] == 0
    assert report["brain"]["coverage"] == {
        "manifest_exists": False,
        "coverage_complete": False,
        "covered_episode_count": 0,
        "missing_episode_ids": [],
        "status": "missing",
    }
    assert report["vector_index"]["exists"] is True
    assert report["vector_index"]["status"] == "current"
    assert report["vector_index"]["record_count"] == 0
    assert report["vector_index"]["embedding_method"] == "deterministic_hashing_v1"
    assert report["schemas"]["file_count"] >= 12
    assert report["schemas"]["versions"]["research_episode"] == "nslab.research_episode.v1"
    assert (
        report["schemas"]["versions"]["semantic_research_draft"]
        == "nslab.semantic_research_draft.v1"
    )
    assert set(report["schemas"]["versions"]).issuperset(
        {
            "blind_prediction",
            "brain_manifest",
            "context_manifest",
            "daily_analysis",
            "news_novelty_review",
            "semantic_retrieval_plan",
            "semantic_research_draft",
        }
    )
    assert report["schemas"]["files"]["status"] == "ok"
    assert report["schemas"]["files"]["expected_file_count"] == len(SCHEMA_MODELS)
    assert report["schemas"]["files"]["missing_files"] == []
    assert report["schemas"]["files"]["stale_files"] == []
    research_schema = report["schemas"]["files"]["files"]["research_episode.schema.json"]
    assert research_schema == {
        "exists": True,
        "expected_schema_version": "nslab.research_episode.v1",
        "schema_version": "nslab.research_episode.v1",
        "status": "ok",
    }


def test_doctor_report_includes_brain_coverage_status(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    episode = ResearchEpisode(
        episode_id="EP-doctor-coverage",
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
        research_version="doctor-test-v1",
        price_source_snapshot={"source": "doctor-test"},
        blind_analysis=BlindAnalysis(
            summary="Doctor coverage status lesson.",
            open_world_mechanisms=["accepted episode -> coverage manifest"],
        ),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)
    manifest = BrainCompiler(tmp_path).rebuild(mode="full")

    report = build_doctor_report(settings)

    assert report["readiness"]["passed"] is True
    assert report["brain"]["head"] == manifest.brain_version
    assert report["brain"]["accepted_episode_count"] == 1
    assert report["brain"]["coverage"] == {
        "manifest_exists": True,
        "brain_version": manifest.brain_version,
        "coverage_complete": True,
        "covered_episode_count": 1,
        "missing_episode_ids": [],
        "status": "complete",
    }


def test_doctor_report_readiness_flags_unsynced_warehouse(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    episode = ResearchEpisode(
        episode_id="EP-doctor-warehouse",
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
        research_version="doctor-test-v1",
        price_source_snapshot={"source": "doctor-test"},
        blind_analysis=BlindAnalysis(
            summary="Doctor warehouse status lesson.",
            open_world_mechanisms=["accepted episode -> warehouse projection"],
        ),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    (tmp_path / "warehouse" / "research_episodes.parquet").unlink()

    report = build_doctor_report(settings)

    assert report["warehouse"]["status"] == "attention"
    assert report["warehouse"]["missing_files"] == ["research_episodes.parquet"]
    assert report["warehouse"]["synced"] is False
    assert report["readiness"] == {
        "passed": False,
        "status": "attention",
        "finding_count": 1,
        "findings": ["warehouse: required projections are missing, unreadable, or unsynced"],
    }


def test_doctor_report_flags_missing_and_stale_schema_files(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    (tmp_path / "schemas" / "blind_prediction.schema.json").unlink()
    write_json(
        tmp_path / "schemas" / "research_episode.schema.json",
        {
            "properties": {
                "schema_version": {"default": "nslab.research_episode.v0"}
            }
        },
    )

    report = build_doctor_report(settings)

    assert report["readiness"] == {
        "passed": False,
        "status": "attention",
        "finding_count": 1,
        "findings": ["schemas: missing, invalid, or stale schema files"],
    }
    schema_status = report["schemas"]["files"]
    assert schema_status["status"] == "attention"
    assert schema_status["missing_files"] == ["blind_prediction.schema.json"]
    assert schema_status["stale_files"] == ["research_episode.schema.json"]
    assert schema_status["files"]["blind_prediction.schema.json"]["status"] == "missing"
    assert schema_status["files"]["research_episode.schema.json"] == {
        "exists": True,
        "expected_schema_version": "nslab.research_episode.v1",
        "schema_version": "nslab.research_episode.v0",
        "status": "stale",
    }


def test_doctor_report_inspects_stock_web_cache_when_no_explicit_path(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        stock_web_path=None,
        stock_web_cache_enabled=True,
        stock_web_cache_path=tmp_path / "cache" / "stock-web",
    )
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    atlas = tmp_path / "cache" / "stock-web" / "atlas"
    atlas.mkdir(parents=True)
    write_json(
        atlas / "manifest.json",
        {
            "source_name": "stock-web-cache-test",
            "source_repo_url": "https://example.test/cache-stock-web",
        },
    )
    write_json(atlas / "schema.json", {"tradable_shard_columns": {"d": "date"}})

    report = build_doctor_report(settings)

    assert report["readiness"]["passed"] is True
    assert report["stock_web"]["path"] is None
    assert report["stock_web"]["path_exists"] is False
    assert report["stock_web"]["cache_enabled"] is True
    assert report["stock_web"]["cache_path"] == (tmp_path / "cache" / "stock-web").as_posix()
    assert report["stock_web"]["cache_path_exists"] is True
    assert report["stock_web"]["effective_path"] == (
        tmp_path / "cache" / "stock-web"
    ).as_posix()
    assert report["stock_web"]["effective_path_exists"] is True
    assert report["stock_web"]["effective_path_source"] == "cache"
    assert report["stock_web"]["schema"]["source_name"] == "stock-web-cache-test"


def test_doctor_report_readiness_flags_missing_required_api_keys(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")

    report = build_doctor_report(settings)

    assert report["readiness"] == {
        "passed": False,
        "status": "attention",
        "finding_count": 2,
        "findings": [
            "brave_search: required API key is missing",
            "openai: required API key is missing",
        ],
    }


def test_doctor_report_readiness_flags_missing_openai_sdk_when_provider_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    settings = Settings(project_root=tmp_path, llm_provider="responses")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics._openai_sdk_status",
        lambda: {
            "available": False,
            "version": None,
            "async_client_available": False,
            "error": None,
        },
    )

    report = build_doctor_report(settings)

    assert report["api_connections"]["openai"] == {
        "required": True,
        "configured": True,
        "sdk": {
            "available": False,
            "version": None,
            "async_client_available": False,
            "error": None,
        },
        "status": "missing_sdk",
    }
    assert report["readiness"] == {
        "passed": False,
        "status": "attention",
        "finding_count": 1,
        "findings": ["openai: required SDK extra is not installed"],
    }


def test_doctor_report_readiness_flags_openai_sdk_without_async_client(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    settings = Settings(project_root=tmp_path, llm_provider="openai")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics._openai_sdk_status",
        lambda: {
            "available": True,
            "version": "legacy-fake",
            "async_client_available": False,
            "error": None,
        },
    )

    report = build_doctor_report(settings)

    assert report["api_connections"]["openai"]["status"] == "sdk_missing_async_client"
    assert report["readiness"]["findings"] == ["openai: SDK does not expose AsyncOpenAI"]


def test_doctor_report_readiness_requires_stock_web_path_when_price_provider_enabled(
    tmp_path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        price_provider="stock-web",
        stock_web_path=tmp_path / "missing-stock-web",
    )
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")

    report = build_doctor_report(settings)

    assert report["readiness"] == {
        "passed": False,
        "status": "attention",
        "finding_count": 1,
        "findings": ["stock_web: configured price provider has no readable path"],
    }


def test_doctor_report_readiness_flags_incomplete_stock_web_atlas(
    tmp_path,
) -> None:
    stock_web_path = tmp_path / "stock-web"
    atlas = stock_web_path / "atlas"
    atlas.mkdir(parents=True)
    write_json(
        atlas / "manifest.json",
        {
            "source_name": "stock-web-incomplete-test",
            "calibration_shard_root": "atlas/ohlcv_tradable_by_symbol_year",
        },
    )
    write_json(
        atlas / "schema.json",
        {
            "tradable_shard_columns": {
                "d": "date",
                "o": "open",
                "c": "close",
            }
        },
    )
    settings = Settings(
        project_root=tmp_path,
        price_provider="stock-web",
        stock_web_path=stock_web_path,
    )
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")

    report = build_doctor_report(settings)

    assert report["stock_web"]["schema_status"]["status"] == "attention"
    assert report["stock_web"]["schema_status"]["missing_required_fields"] == [
        "high",
        "low",
    ]
    assert report["stock_web"]["schema_status"]["has_readable_shard_root"] is False
    assert report["readiness"] == {
        "passed": False,
        "status": "attention",
        "finding_count": 1,
        "findings": ["stock_web: atlas manifest/schema or shard roots are incomplete"],
    }


def test_doctor_report_readiness_flags_unsupported_price_provider(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, price_provider="unknown-price")
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")

    report = build_doctor_report(settings)

    assert report["providers"]["price"] == "unknown-price"
    assert report["readiness"] == {
        "passed": False,
        "status": "attention",
        "finding_count": 1,
        "findings": ["price: unsupported provider unknown-price"],
    }


def test_doctor_strict_exits_nonzero_when_readiness_has_findings(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "openai")
    monkeypatch.setenv("NSLAB_WEB_PROVIDER", "mock")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    initialized = RUNNER.invoke(app, ["init"])
    non_strict = RUNNER.invoke(app, ["doctor"])
    strict = RUNNER.invoke(app, ["doctor", "--strict"])

    assert initialized.exit_code == 0, initialized.output
    assert non_strict.exit_code == 0, non_strict.output
    assert strict.exit_code == 1, strict.output
    payload = json.loads(strict.output)
    assert payload["readiness"]["passed"] is False
    assert "openai: required API key is missing" in payload["readiness"]["findings"]
