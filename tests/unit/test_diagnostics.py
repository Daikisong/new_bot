from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from typer.testing import CliRunner

from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.cli import app
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.contracts.schemas import SCHEMA_MODELS, export_json_schemas
from news_scalping_lab.diagnostics import (
    build_doctor_report,
    production_readiness_report,
    real_bundle_smoke_report,
)
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, file_sha256, write_json
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
    assert report["warehouse"]["duplicate_identities"] == {}
    assert report["warehouse"]["weight_mismatches"] == {}
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
    assert report["brain"]["audit"]["brain_build_mode"] == "full"
    assert isinstance(report["brain"]["audit"]["finding_count"], int)


def test_production_readiness_rejects_failed_latest_brain_audit(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics._openai_sdk_status",
        lambda: {
            "available": True,
            "version": "fake-openai",
            "async_client_available": True,
            "error": None,
        },
    )
    settings = Settings(project_root=tmp_path, llm_provider="openai")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    episode = ResearchEpisode(
        episode_id="EP-doctor-brain-audit",
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
        research_version="doctor-test-v1",
        price_source_snapshot={"source": "doctor-test"},
        blind_analysis=BlindAnalysis(
            summary="Doctor production readiness must inspect brain audit status.",
            open_world_mechanisms=["brain audit failure -> production readiness failure"],
        ),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    brain_file = tmp_path / "brain" / "current" / "00_world_model.md"
    brain_file.write_text(
        brain_file.read_text(encoding="utf-8") + "\nTampered production audit fixture.\n",
        encoding="utf-8",
    )

    report = build_doctor_report(settings)
    production = production_readiness_report(report, settings)

    assert report["brain"]["audit"]["passed"] is False
    assert "brain immutable snapshot does not match current brain files" in report[
        "brain"
    ]["audit"]["findings"]
    assert production["passed"] is False
    assert "brain: latest brain audit failed" in production["findings"]
    assert production["required_environment"] == {
        "NSLAB_LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "<required>",
        "NSLAB_WEB_PROVIDER": "brave",
        "BRAVE_SEARCH_API_KEY": "<required>",
    }
    assert production["remediation_commands"] == [
        "python -m news_scalping_lab.cli research smoke-bundle --path %NSLAB_REAL_BUNDLE_PATH% --require-valid",
        "python -m news_scalping_lab.cli brain rebuild --mode llm-full",
        "python -m news_scalping_lab.cli warehouse rebuild",
        "python -m news_scalping_lab.cli brain audit --deep",
        "python -m news_scalping_lab.cli doctor --production",
    ]


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


def test_doctor_report_exposes_warehouse_duplicate_and_weight_details(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")

    def fake_audit_coverage(root: Path) -> dict[str, object]:
        return {
            "brain_audit_passed": True,
            "brain_audit_findings": [],
            "warehouse_counts": {"issuer_day_cases.parquet": 2},
            "warehouse_required_files": ["issuer_day_cases.parquet"],
            "warehouse_missing_files": [],
            "warehouse_unreadable_files": [],
            "warehouse_required_files_present": True,
            "warehouse_synced": True,
            "warehouse_projection_synced": False,
            "warehouse_count_mismatches": {},
            "warehouse_identity_mismatches": {},
            "warehouse_duplicate_identities": {
                "issuer_day_cases.parquet": ["2030-01-10|000001"]
            },
            "warehouse_weight_mismatches": {
                "issuer_day_cases.parquet": {"2030-01-10|000001": 0.5}
            },
            "warehouse_expected_source_counts": {
                "issuer_day_cases.parquet": {
                    "expected": 1,
                    "source_label": "issuer-day brain records",
                }
            },
        }

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_coverage",
        fake_audit_coverage,
    )

    report = build_doctor_report(settings)

    assert report["warehouse"]["status"] == "attention"
    assert report["warehouse"]["duplicate_identities"] == {
        "issuer_day_cases.parquet": ["2030-01-10|000001"]
    }
    assert report["warehouse"]["weight_mismatches"] == {
        "issuer_day_cases.parquet": {"2030-01-10|000001": 0.5}
    }
    assert report["readiness"]["passed"] is False
    assert (
        "warehouse: required projections are missing, unreadable, or unsynced"
        in report["readiness"]["findings"]
    )


def test_production_readiness_requires_synced_warehouse_projection(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "warehouse": {
            "status": "attention",
            "required_files_present": True,
            "synced": True,
            "projection_synced": False,
            "count_mismatches": {},
            "identity_mismatches": {},
            "duplicate_identities": {
                "issuer_day_cases.parquet": ["2030-01-10|000001"]
            },
            "weight_mismatches": {
                "issuer_day_cases.parquet": {"2030-01-10|000001": 0.5}
            },
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["warehouse"]["passed"] is False
    assert production["warehouse"]["duplicate_identities"] == {
        "issuer_day_cases.parquet": ["2030-01-10|000001"]
    }
    assert production["warehouse"]["weight_mismatches"] == {
        "issuer_day_cases.parquet": {"2030-01-10|000001": 0.5}
    }
    assert "warehouse: warehouse status is not ok" in production["findings"]
    assert "warehouse: record-level projections are not synced" in production["findings"]
    assert (
        "warehouse: projection duplicate identities detected"
        in production["findings"]
    )
    assert (
        "warehouse: projection sample weight sums are invalid"
        in production["findings"]
    )


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


def test_doctor_production_report_requires_real_api_connections_for_mock_defaults(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")

    normal_report = build_doctor_report(settings)
    production_report = build_doctor_report(settings, production=True)

    assert normal_report["api_connections"]["openai"]["required"] is False
    assert normal_report["api_connections"]["openai"]["status"] == "not_required"
    assert normal_report["api_connections"]["brave_search"]["required"] is False
    assert normal_report["api_connections"]["brave_search"]["status"] == "not_required"
    assert production_report["api_connections"]["openai"]["required"] is True
    assert production_report["api_connections"]["openai"]["configured"] is False
    assert production_report["api_connections"]["openai"]["status"] == "missing_api_key"
    assert production_report["api_connections"]["brave_search"]["required"] is True
    assert production_report["api_connections"]["brave_search"]["configured"] is False
    assert (
        production_report["api_connections"]["brave_search"]["status"]
        == "missing_api_key"
    )
    assert production_report["readiness"]["passed"] is False
    assert production_report["readiness"]["findings"] == [
        "brave_search: required API key is missing",
        "openai: required API key is missing",
    ]


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


def test_production_readiness_rejects_deterministic_embedding_index(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai")
    settings.llm.provider = "openai"
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": {
            "status": "current",
            "embedding_method": "deterministic_hashing_v1",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["passed"] is False
    assert (
        "embedding: deterministic mock vector index cannot be production semantic index"
        in production["findings"]
    )
    assert production["required_environment"]["OPENAI_API_KEY"] == "<required>"
    assert any(
        command.endswith("brain rebuild --mode llm-full")
        for command in production["remediation_commands"]
    )


def test_production_readiness_accepts_semantic_index_record_evidence(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "record_coverage_manifest.json",
        {
            "schema_version": "nslab.record_coverage_manifest.v1",
            "accepted_record_count": 2,
            "coverage_complete": True,
        },
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
            "brain_records_exists": True,
            "source_brain_record_count": 2,
            "brain_record_count": 2,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["passed"] is True
    assert production["semantic_index"]["expected_source_record_count"] == 2
    assert not any(
        finding.startswith("embedding:") for finding in production["findings"]
    )


def test_production_readiness_rejects_semantic_index_record_count_gap(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "record_coverage_manifest.json",
        {
            "schema_version": "nslab.record_coverage_manifest.v1",
            "accepted_record_count": 2,
            "coverage_complete": True,
        },
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
            "brain_records_exists": True,
            "source_brain_record_count": 2,
            "brain_record_count": 1,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["passed"] is False
    assert production["semantic_index"]["indexed_brain_record_count"] == 1
    assert (
        "embedding: semantic index record count does not match coverage"
        in production["findings"]
    )


def test_production_readiness_rejects_mock_web_provider(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="mock")
    settings.llm.provider = "openai"
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
    }

    production = production_readiness_report(report, settings)

    assert production["passed"] is False
    assert "web: mock provider cannot supply production evidence" in production["findings"]
    assert production["required_environment"]["NSLAB_WEB_PROVIDER"] == "brave"
    assert production["required_environment"]["BRAVE_SEARCH_API_KEY"] == "<required>"


def test_production_readiness_requires_brave_api_key_for_live_web(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "missing_api_key"},
        },
    }

    production = production_readiness_report(report, settings)

    assert production["passed"] is False
    assert (
        "brave_search: production web research requires configured Brave Search API key"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_manifest_without_compile_evidence(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-production"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "brain_manifest.json",
        {"brain_version": "brain-production", "build_mode": "llm-full"},
    )
    write_json(
        current / "record_coverage_manifest.json",
        {
            "schema_version": "nslab.record_coverage_manifest.v1",
            "accepted_record_count": 1,
            "coverage_complete": True,
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["passed"] is False
    assert production["llm_full_brain"]["status"] == "attention"
    assert production["llm_full_brain"]["compile_manifest_exists"] is False
    assert "brain: llm-full compile manifest is missing" in production["findings"]
    assert "brain: compiled claims JSONL is missing" in production["findings"]


def test_production_readiness_rejects_catalog_only_brain_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-production"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "brain_manifest.json",
        {
            "brain_version": "brain-catalog",
            "build_mode": "catalog",
            "catalog_only": True,
        },
    )
    write_json(
        current / "record_coverage_manifest.json",
        {
            "schema_version": "nslab.record_coverage_manifest.v1",
            "accepted_record_count": 0,
            "coverage_complete": True,
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["passed"] is False
    assert production["llm_full_brain"]["catalog_only"] is True
    assert "brain: current manifest is catalog_only" in production["findings"]
    assert (
        "brain: current manifest build_mode is catalog, not llm-full"
        in production["findings"]
    )


def test_production_readiness_accepts_llm_full_compile_evidence(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-production"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "brain_manifest.json",
        {"brain_version": "brain-production", "build_mode": "llm-full"},
    )
    write_json(
        current / "record_coverage_manifest.json",
        {
            "schema_version": "nslab.record_coverage_manifest.v1",
            "accepted_record_count": 1,
            "coverage_complete": True,
        },
    )
    write_json(
        current / "llm_compile_manifest.json",
        {
            "schema_version": "nslab.llm_full_brain_compile_manifest.v1",
            "brain_version": "brain-production",
            "provider": "openai",
            "model": "gpt-production",
            "source_record_count": 1,
            "compiled_claim_count": 1,
            "record_shard_count": 1,
            "category_count": 9,
            "llm_generation_count": 19,
        },
    )
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": {
                "schema_version": "nslab.llm_full_brain_compile_run.v1",
                "brain_version": "brain-production",
                "llm_generation_count": 19,
                "llm_live_call_count": 19,
                "llm_cache_hit_count": 0,
                "all_outputs_from_cache": False,
            },
        },
    )
    (current / "compiled_claims.jsonl").write_text(
        json.dumps({"claim_id": "CC-production"}) + "\n",
        encoding="utf-8",
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["llm_full_brain"]["passed"] is True
    assert production["llm_full_brain"]["expected_source_record_count"] == 1
    assert production["llm_full_brain"]["run_llm_live_call_count"] == 19
    assert not any(
        finding.startswith("brain: llm-full") or finding.startswith("brain: compiled claims")
        for finding in production["findings"]
    )


def test_production_readiness_rejects_all_cached_llm_full_compile(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-production"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "brain_manifest.json",
        {"brain_version": "brain-production", "build_mode": "llm-full"},
    )
    write_json(
        current / "llm_compile_manifest.json",
        {
            "schema_version": "nslab.llm_full_brain_compile_manifest.v1",
            "brain_version": "brain-production",
            "provider": "openai",
            "model": "gpt-production",
            "source_record_count": 1,
            "compiled_claim_count": 1,
            "record_shard_count": 1,
            "category_count": 9,
            "llm_generation_count": 19,
        },
    )
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": {
                "schema_version": "nslab.llm_full_brain_compile_run.v1",
                "brain_version": "brain-production",
                "llm_generation_count": 19,
                "llm_live_call_count": 0,
                "llm_cache_hit_count": 19,
                "all_outputs_from_cache": True,
            },
        },
    )
    (current / "compiled_claims.jsonl").write_text(
        json.dumps({"claim_id": "CC-production"}) + "\n",
        encoding="utf-8",
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["llm_full_brain"]["passed"] is False
    assert production["llm_full_brain"]["run_llm_live_call_count"] == 0
    assert production["llm_full_brain"]["run_all_outputs_from_cache"] is True
    assert (
        "brain: llm-full production compile has no live LLM calls"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_model_mismatch(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-production"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "brain_manifest.json",
        {"brain_version": "brain-production", "build_mode": "llm-full"},
    )
    write_json(
        current / "record_coverage_manifest.json",
        {
            "schema_version": "nslab.record_coverage_manifest.v1",
            "accepted_record_count": 1,
            "coverage_complete": True,
        },
    )
    write_json(
        current / "llm_compile_manifest.json",
        {
            "schema_version": "nslab.llm_full_brain_compile_manifest.v1",
            "brain_version": "brain-production",
            "provider": "openai",
            "model": "gpt-stale",
            "source_record_count": 1,
            "compiled_claim_count": 1,
            "record_shard_count": 1,
            "category_count": 9,
            "llm_generation_count": 19,
        },
    )
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": {
                "schema_version": "nslab.llm_full_brain_compile_run.v1",
                "brain_version": "brain-production",
                "llm_generation_count": 19,
                "llm_live_call_count": 19,
                "llm_cache_hit_count": 0,
                "all_outputs_from_cache": False,
            },
        },
    )
    (current / "compiled_claims.jsonl").write_text(
        json.dumps({"claim_id": "CC-production"}) + "\n",
        encoding="utf-8",
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["llm_full_brain"]["passed"] is False
    assert production["llm_full_brain"]["model"] == "gpt-stale"
    assert production["llm_full_brain"]["configured_model"] == "gpt-production"
    assert (
        "brain: llm-full compile model does not match configured model"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_source_count_gap(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-production"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "brain_manifest.json",
        {"brain_version": "brain-production", "build_mode": "llm-full"},
    )
    write_json(
        current / "record_coverage_manifest.json",
        {
            "schema_version": "nslab.record_coverage_manifest.v1",
            "accepted_record_count": 2,
            "coverage_complete": True,
        },
    )
    write_json(
        current / "llm_compile_manifest.json",
        {
            "schema_version": "nslab.llm_full_brain_compile_manifest.v1",
            "brain_version": "brain-production",
            "provider": "openai",
            "model": "gpt-production",
            "source_record_count": 1,
            "compiled_claim_count": 1,
            "record_shard_count": 1,
            "category_count": 9,
            "llm_generation_count": 19,
        },
    )
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": {
                "schema_version": "nslab.llm_full_brain_compile_run.v1",
                "brain_version": "brain-production",
                "llm_generation_count": 19,
                "llm_live_call_count": 19,
                "llm_cache_hit_count": 0,
                "all_outputs_from_cache": False,
            },
        },
    )
    (current / "compiled_claims.jsonl").write_text(
        json.dumps({"claim_id": "CC-production"}) + "\n",
        encoding="utf-8",
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["llm_full_brain"]["passed"] is False
    assert production["llm_full_brain"]["expected_source_record_count"] == 2
    assert production["llm_full_brain"]["source_record_count"] == 1
    assert (
        "brain: llm-full compile source record count does not match coverage"
        in production["findings"]
    )


def test_production_readiness_rejects_stale_llm_full_compile_evidence(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-production"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "brain_manifest.json",
        {"brain_version": "brain-current", "build_mode": "llm-full"},
    )
    write_json(
        current / "llm_compile_manifest.json",
        {
            "schema_version": "nslab.llm_full_brain_compile_manifest.v1",
            "brain_version": "brain-stale",
            "provider": "openai",
            "model": "gpt-production",
            "source_record_count": 1,
            "compiled_claim_count": 1,
            "record_shard_count": 1,
            "category_count": 9,
            "llm_generation_count": 19,
        },
    )
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-stale",
            "llm_compile_run": {
                "schema_version": "nslab.llm_full_brain_compile_run.v1",
                "brain_version": "brain-stale",
                "llm_generation_count": 19,
                "llm_live_call_count": 19,
                "llm_cache_hit_count": 0,
                "all_outputs_from_cache": False,
            },
        },
    )
    (current / "compiled_claims.jsonl").write_text(
        json.dumps({"claim_id": "CC-production"}) + "\n",
        encoding="utf-8",
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["llm_full_brain"]["passed"] is False
    assert production["llm_full_brain"]["current_brain_version"] == "brain-current"
    assert (
        "brain: llm-full compile manifest does not match current brain"
        in production["findings"]
    )
    assert (
        "brain: llm-full compile run is stale for current brain"
        in production["findings"]
    )
    assert (
        "brain: llm-full compile report is stale for current brain"
        in production["findings"]
    )


def test_real_bundle_smoke_reports_pending_when_no_candidate_exists(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)

    report = real_bundle_smoke_report(settings)

    assert report["schema_version"] == "nslab.real_bundle_smoke.v1"
    assert report["status"] == "pending"
    assert report["passed"] is False
    assert report["real_smoke_pending"] is True
    assert report["candidate_count"] == 0
    assert report["search_order"] == ["data_inbox", "tests_fixture", "env", "cli"]


def test_real_bundle_smoke_passes_only_for_production_source_bundle(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "data" / "inbox" / "research" / "real_bundle.md"
    bundle.write_text("real bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v11_bundle_inspection(path),
    )

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "passed"
    assert report["passed"] is True
    assert report["real_valid_smoke_count"] == 1
    assert report["synthetic_valid_smoke_count"] == 0
    assert report["first_production_source"] == "data_inbox"
    assert report["first_production_status"] == "passed"
    assert report["selected"]["source"] == "data_inbox"
    assert report["selected"]["inspection"]["raw_record_count"] == 327
    assert report["selected"]["inspection"]["missing_payload_reference_count"] == 0


def test_real_bundle_smoke_keeps_fixture_success_synthetic_only(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    fixture_dir = tmp_path / "tests" / "fixtures" / "research_bundles"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "synthetic_bundle.md").write_text("synthetic bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v11_bundle_inspection(path),
    )

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "synthetic_only"
    assert report["passed"] is False
    assert report["real_smoke_pending"] is True
    assert report["real_valid_smoke_count"] == 0
    assert report["synthetic_valid_smoke_count"] == 1
    assert report["first_production_source"] is None
    assert report["first_production_status"] is None


def test_real_bundle_smoke_keeps_explicit_fixture_path_synthetic_only(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    fixture_dir = tmp_path / "tests" / "fixtures" / "research_bundles"
    fixture_dir.mkdir(parents=True)
    fixture = fixture_dir / "synthetic_bundle.md"
    fixture.write_text("synthetic bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v11_bundle_inspection(path),
    )

    report = real_bundle_smoke_report(settings, explicit_path=fixture)

    assert report["status"] == "synthetic_only"
    assert report["passed"] is False
    assert report["selected"] is None
    assert report["real_smoke_pending"] is True
    assert report["real_valid_smoke_count"] == 0
    assert report["synthetic_valid_smoke_count"] == 1
    assert report["inspections"][0]["source"] == "tests_fixture"
    assert report["inspections"][0]["production_source"] is False


def test_real_bundle_smoke_keeps_explicit_example_path_synthetic_only(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    example = docs_dir / "20260622_nslab_episode_bundle.example.md"
    example.write_text("example bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v11_bundle_inspection(path),
    )

    report = real_bundle_smoke_report(settings, explicit_path=example)

    assert report["status"] == "synthetic_only"
    assert report["passed"] is False
    assert report["selected"] is None
    assert report["real_valid_smoke_count"] == 0
    assert report["synthetic_valid_smoke_count"] == 1
    assert report["inspections"][0]["source"] == "cli"
    assert report["inspections"][0]["production_source"] is False


def test_real_bundle_smoke_prioritizes_failed_production_candidate(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    fixture_dir = tmp_path / "tests" / "fixtures" / "research_bundles"
    fixture_dir.mkdir(parents=True)
    fixture = fixture_dir / "synthetic_bundle.md"
    fixture.write_text("synthetic bundle", encoding="utf-8")
    real_candidate = tmp_path / "real_bundle.md"
    real_candidate.write_text("real bundle", encoding="utf-8")

    def inspect(path: Path) -> dict[str, object]:
        if path == real_candidate:
            return _invalid_v11_bundle_inspection(path)
        return _valid_v11_bundle_inspection(path)

    monkeypatch.setattr("news_scalping_lab.diagnostics.inspect_versioned_bundle", inspect)

    report = real_bundle_smoke_report(settings, explicit_path=real_candidate)

    assert report["status"] == "failed"
    assert report["passed"] is False
    assert report["selected"] is None
    assert report["first_production_source"] == "cli"
    assert report["first_production_status"] == "failed"
    assert report["production_failed_inspection_count"] == 1
    assert report["synthetic_valid_smoke_count"] == 1


def test_real_bundle_smoke_rejects_missing_payload_references(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    bundle = tmp_path / "data" / "inbox" / "research" / "real_bundle.md"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text("real bundle", encoding="utf-8")

    def inspect(path: Path) -> dict[str, object]:
        inspection = _valid_v11_bundle_inspection(path)
        inspection["missing_payload_reference_count"] = 1
        return inspection

    monkeypatch.setattr("news_scalping_lab.diagnostics.inspect_versioned_bundle", inspect)

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "failed"
    assert report["passed"] is False
    assert report["first_production_source"] == "data_inbox"
    assert report["first_production_status"] == "failed"
    assert report["production_failed_inspection_count"] == 1
    assert report["inspections"][0]["inspection"]["missing_payload_reference_count"] == 1


def test_real_bundle_smoke_does_not_skip_failed_earlier_production_candidate(
    tmp_path,
    monkeypatch,
) -> None:
    env_candidate = tmp_path / "external" / "real_bundle.md"
    env_candidate.parent.mkdir()
    env_candidate.write_text("later valid real bundle", encoding="utf-8")
    monkeypatch.setenv("NSLAB_REAL_BUNDLE_PATH", env_candidate.as_posix())
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    inbox_candidate = tmp_path / "data" / "inbox" / "research" / "real_bundle.md"
    inbox_candidate.write_text("earlier invalid real bundle", encoding="utf-8")

    def inspect(path: Path) -> dict[str, object]:
        if path.resolve() == inbox_candidate.resolve():
            return _invalid_v11_bundle_inspection(path)
        return _valid_v11_bundle_inspection(path)

    monkeypatch.setattr("news_scalping_lab.diagnostics.inspect_versioned_bundle", inspect)

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "failed"
    assert report["passed"] is False
    assert report["selected"] is None
    assert report["first_production_source"] == "data_inbox"
    assert report["first_production_status"] == "failed"
    assert report["production_failed_inspection_count"] == 1
    assert report["real_valid_smoke_count"] == 1
    assert [
        item["source"]
        for item in report["inspections"]
        if item["production_source"] is True
    ] == ["data_inbox", "env"]


def test_production_readiness_rejects_real_smoke_without_import(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    bundle = tmp_path / "data" / "inbox" / "research" / "real_bundle.md"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("real bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v11_bundle_inspection(path),
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["real_bundle_smoke"]["status"] == "passed"
    assert production["real_bundle_import"]["passed"] is False
    assert production["real_bundle_import"]["envelope_exists"] is False
    assert (
        "real_bundle_import: selected real bundle has not been imported into record store"
        in production["findings"]
    )


def test_production_readiness_accepts_real_smoke_import_link(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    bundle = tmp_path / "data" / "inbox" / "research" / "real_bundle.md"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("real bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v11_bundle_inspection(path),
    )
    inspection = _valid_v11_bundle_inspection(bundle)
    episode_id = str(inspection["episode_id"])
    episode_dir = tmp_path / "research" / "episodes" / episode_id
    episode_dir.mkdir(parents=True)
    write_json(
        episode_dir / "bundle_envelope.json",
        {
            "bundle_schema_version": "nslab.research_bundle.v11",
            "bundle_status": "ACCEPT_FULL",
            "blind_valid": True,
            "raw_bundle_sha256": inspection["raw_bundle_sha256"],
        },
    )
    (episode_dir / "original_bundle.md").write_text(
        bundle.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    record_path = _write_real_smoke_records(tmp_path, episode_id, inspection)
    record_ids = _real_smoke_record_ids(inspection)
    write_json(
        episode_dir / "normalized_episode_index.json",
        {
            "record_ids": record_ids,
            "record_count_by_type": inspection["record_counts_by_type"],
            "training_eligible_record_count": inspection[
                "training_eligible_record_count"
            ],
        },
    )
    write_json(
        tmp_path / "memory" / "record_manifests" / f"{episode_id}.json",
        {
            "accepted": True,
            "record_count": inspection["normalized_record_count"],
            "training_eligible_record_count": inspection[
                "training_eligible_record_count"
            ],
            "record_counts_by_type": inspection["record_counts_by_type"],
            "record_ids": record_ids,
            "records_file": record_path.relative_to(tmp_path).as_posix(),
            "records_sha256": file_sha256(record_path),
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["real_bundle_smoke"]["status"] == "passed"
    assert production["real_bundle_import"]["passed"] is True
    assert production["real_bundle_import"]["normalized_index_exists"] is True
    assert production["real_bundle_import"]["record_manifest_exists"] is True
    assert production["real_bundle_import"]["record_file_exists"] is True
    assert production["real_bundle_import"]["observed_record_count"] == 327
    assert production["real_bundle_import"][
        "observed_training_eligible_record_count"
    ] == 325
    assert not any(
        finding.startswith("real_bundle_import:")
        for finding in production["findings"]
    )


def test_production_readiness_rejects_tampered_real_import_record_jsonl(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    bundle = tmp_path / "data" / "inbox" / "research" / "real_bundle.md"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("real bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v11_bundle_inspection(path),
    )
    inspection = _valid_v11_bundle_inspection(bundle)
    episode_id = str(inspection["episode_id"])
    episode_dir = tmp_path / "research" / "episodes" / episode_id
    episode_dir.mkdir(parents=True)
    write_json(
        episode_dir / "bundle_envelope.json",
        {
            "bundle_schema_version": "nslab.research_bundle.v11",
            "bundle_status": "ACCEPT_FULL",
            "blind_valid": True,
            "raw_bundle_sha256": inspection["raw_bundle_sha256"],
        },
    )
    (episode_dir / "original_bundle.md").write_text(
        bundle.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    record_path = _write_real_smoke_records(tmp_path, episode_id, inspection)
    record_ids = _real_smoke_record_ids(inspection)
    write_json(
        episode_dir / "normalized_episode_index.json",
        {
            "record_ids": record_ids,
            "record_count_by_type": inspection["record_counts_by_type"],
            "training_eligible_record_count": inspection[
                "training_eligible_record_count"
            ],
        },
    )
    record_path.write_text(
        json.dumps(
            {
                "record_id": "tampered-1",
                "record_type": "supervised_issuer_day_case",
                "training_eligible": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        tmp_path / "memory" / "record_manifests" / f"{episode_id}.json",
        {
            "accepted": True,
            "record_count": inspection["normalized_record_count"],
            "training_eligible_record_count": inspection[
                "training_eligible_record_count"
            ],
            "record_counts_by_type": inspection["record_counts_by_type"],
            "record_ids": record_ids,
            "records_file": record_path.relative_to(tmp_path).as_posix(),
            "records_sha256": "0" * 64,
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["real_bundle_import"]["passed"] is False
    assert production["real_bundle_import"]["observed_record_count"] == 1
    assert (
        "real_bundle_import: record JSONL sha does not match record manifest"
        in production["findings"]
    )
    assert (
        "real_bundle_import: record JSONL count does not match real smoke"
        in production["findings"]
    )
    assert (
        "real_bundle_import: record JSONL training eligible count does not match real smoke"
        in production["findings"]
    )
    assert (
        "real_bundle_import: record JSONL type counts do not match real smoke"
        in production["findings"]
    )


def test_production_readiness_rejects_real_import_index_id_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    bundle = tmp_path / "data" / "inbox" / "research" / "real_bundle.md"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("real bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v11_bundle_inspection(path),
    )
    inspection = _valid_v11_bundle_inspection(bundle)
    episode_id = str(inspection["episode_id"])
    episode_dir = tmp_path / "research" / "episodes" / episode_id
    episode_dir.mkdir(parents=True)
    write_json(
        episode_dir / "bundle_envelope.json",
        {
            "bundle_schema_version": "nslab.research_bundle.v11",
            "bundle_status": "ACCEPT_FULL",
            "blind_valid": True,
            "raw_bundle_sha256": inspection["raw_bundle_sha256"],
        },
    )
    (episode_dir / "original_bundle.md").write_text(
        bundle.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    record_path = _write_real_smoke_records(tmp_path, episode_id, inspection)
    record_ids = _real_smoke_record_ids(inspection)
    write_json(
        episode_dir / "normalized_episode_index.json",
        {
            "record_ids": [*record_ids[:-1], "wrong-record-id"],
            "record_count_by_type": inspection["record_counts_by_type"],
            "training_eligible_record_count": inspection[
                "training_eligible_record_count"
            ],
        },
    )
    write_json(
        tmp_path / "memory" / "record_manifests" / f"{episode_id}.json",
        {
            "accepted": True,
            "record_count": inspection["normalized_record_count"],
            "training_eligible_record_count": inspection[
                "training_eligible_record_count"
            ],
            "record_counts_by_type": inspection["record_counts_by_type"],
            "record_ids": record_ids,
            "records_file": record_path.relative_to(tmp_path).as_posix(),
            "records_sha256": file_sha256(record_path),
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["real_bundle_import"]["passed"] is False
    assert (
        "real_bundle_import: normalized episode index IDs do not match record JSONL"
        in production["findings"]
    )


def test_production_readiness_rejects_real_import_duplicate_record_ids(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    bundle = tmp_path / "data" / "inbox" / "research" / "real_bundle.md"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("real bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v11_bundle_inspection(path),
    )
    inspection = _valid_v11_bundle_inspection(bundle)
    episode_id = str(inspection["episode_id"])
    episode_dir = tmp_path / "research" / "episodes" / episode_id
    episode_dir.mkdir(parents=True)
    write_json(
        episode_dir / "bundle_envelope.json",
        {
            "bundle_schema_version": "nslab.research_bundle.v11",
            "bundle_status": "ACCEPT_FULL",
            "blind_valid": True,
            "raw_bundle_sha256": inspection["raw_bundle_sha256"],
        },
    )
    (episode_dir / "original_bundle.md").write_text(
        bundle.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    record_path = _write_real_smoke_records(tmp_path, episode_id, inspection)
    rows = [
        json.loads(line)
        for line in record_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows[-1]["record_id"] = rows[0]["record_id"]
    duplicate_record_ids = [
        row["record_id"] for row in rows if isinstance(row.get("record_id"), str)
    ]
    record_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    write_json(
        episode_dir / "normalized_episode_index.json",
        {
            "record_ids": duplicate_record_ids,
            "record_count_by_type": inspection["record_counts_by_type"],
            "training_eligible_record_count": inspection[
                "training_eligible_record_count"
            ],
        },
    )
    write_json(
        tmp_path / "memory" / "record_manifests" / f"{episode_id}.json",
        {
            "accepted": True,
            "record_count": inspection["normalized_record_count"],
            "training_eligible_record_count": inspection[
                "training_eligible_record_count"
            ],
            "record_counts_by_type": inspection["record_counts_by_type"],
            "record_ids": duplicate_record_ids,
            "records_file": record_path.relative_to(tmp_path).as_posix(),
            "records_sha256": file_sha256(record_path),
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["real_bundle_import"]["passed"] is False
    assert production["real_bundle_import"]["duplicate_record_ids"] == [
        rows[0]["record_id"]
    ]
    assert (
        "real_bundle_import: normalized episode index has duplicate record IDs"
        in production["findings"]
    )
    assert (
        "real_bundle_import: record manifest has duplicate record IDs"
        in production["findings"]
    )
    assert (
        "real_bundle_import: record JSONL has duplicate record IDs"
        in production["findings"]
    )


def test_production_readiness_reports_exact_commands_for_mock_defaults(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    report: dict[str, object] = {}

    production = production_readiness_report(report, settings)

    assert production["passed"] is False
    assert production["required_environment"] == {
        "NSLAB_LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "<required>",
        "NSLAB_WEB_PROVIDER": "brave",
        "BRAVE_SEARCH_API_KEY": "<required>",
    }
    assert (
        "real_bundle: no readable v11 ACCEPT_FULL bundle candidate; real smoke pending"
        in production["findings"]
    )
    assert production["remediation_commands"] == [
        "python -m news_scalping_lab.cli research smoke-bundle --path %NSLAB_REAL_BUNDLE_PATH% --require-valid",
        "python -m news_scalping_lab.cli brain rebuild --mode llm-full",
        "python -m news_scalping_lab.cli warehouse rebuild",
        "python -m news_scalping_lab.cli brain audit --deep",
        "python -m news_scalping_lab.cli doctor --production",
    ]


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


def _write_real_smoke_records(
    root: Path,
    episode_id: str,
    inspection: dict[str, object],
) -> Path:
    record_path = root / "memory" / "records" / f"{episode_id}.jsonl"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_counts_by_type = inspection["record_counts_by_type"]
    assert isinstance(record_counts_by_type, dict)
    training_eligible_count = inspection["training_eligible_record_count"]
    assert isinstance(training_eligible_count, int)
    record_ids = _real_smoke_record_ids(inspection)
    rows: list[dict[str, object]] = []
    record_index = 0
    for record_type, count in sorted(record_counts_by_type.items()):
        assert isinstance(record_type, str)
        assert isinstance(count, int)
        for _index in range(count):
            rows.append(
                {
                    "record_id": record_ids[record_index],
                    "record_type": record_type,
                    "training_eligible": len(rows) < training_eligible_count,
                }
            )
            record_index += 1
    record_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return record_path


def _real_smoke_record_ids(inspection: dict[str, object]) -> list[str]:
    record_counts_by_type = inspection["record_counts_by_type"]
    assert isinstance(record_counts_by_type, dict)
    record_ids: list[str] = []
    for record_type, count in sorted(record_counts_by_type.items()):
        assert isinstance(record_type, str)
        assert isinstance(count, int)
        record_ids.extend(f"{record_type}-{index}" for index in range(count))
    return record_ids


def _valid_v11_bundle_inspection(path: Path) -> dict[str, object]:
    return {
        "path": path.as_posix(),
        "raw_bundle_sha256": file_sha256(path),
        "bundle_schema_version": "nslab.research_bundle.v11",
        "manifest_schema_version": "nslab.bundle_manifest.v11",
        "episode_schema_version": "nslab.research_episode.v11",
        "adapter": "v11",
        "supported": True,
        "episode_id": "NSLAB-20260622-REAL",
        "trade_date": "2026-06-22",
        "raw_record_count": 327,
        "normalized_record_count": 327,
        "training_eligible_record_count": 325,
        "dropped_record_count": 0,
        "quarantined_record_count": 0,
        "record_counts_by_type": {
            "supervised_issuer_day_case": 150,
            "supervised_direct_event_case": 171,
            "supervised_theme_formation_case": 3,
            "blind_leader_preference_pair": 3,
        },
        "validation_passed": True,
        "record_count_matches_manifest": True,
        "training_eligible_count_matches_manifest": True,
        "available_from_valid": True,
        "invalid_available_from_record_count": 0,
        "outcome_label_quality_valid": True,
        "invalid_outcome_label_quality_record_count": 0,
        "hash_mismatch_count": 0,
        "hash_expectation_conflict_count": 0,
        "missing_source_reference_count": 0,
        "missing_payload_reference_count": 0,
        "validation": {
            "passed": True,
            "bundle_status_accept_full": True,
            "blind_valid": True,
            "validator_exit_code_zero": True,
            "critical_error_count_zero": True,
        },
    }


def _invalid_v11_bundle_inspection(path: Path) -> dict[str, object]:
    inspection = _valid_v11_bundle_inspection(path)
    inspection["validation_passed"] = False
    inspection["hash_mismatch_count"] = 1
    inspection["validation"] = {
        **dict(inspection["validation"]),
        "passed": False,
    }
    return inspection
