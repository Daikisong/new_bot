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
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.training import export_training
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    sha256_text,
    write_json,
)
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
    assert report["brain"]["audit"]["brain_category_file_count"] == 9
    assert isinstance(
        report["brain"]["audit"]["brain_category_source_record_types"],
        dict,
    )
    assert report["brain"]["audit"]["brain_category_source_population_mismatches"] == []
    assert report["brain"]["audit"]["brain_empty_category_complete_files"] == []
    assert isinstance(report["brain"]["audit"]["finding_count"], int)


def test_production_readiness_rejects_missing_latest_brain_diversity_summary(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    episode = ResearchEpisode(
        episode_id="EP-doctor-brain-diversity-summary",
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
        research_version="doctor-test-v1",
        price_source_snapshot={"source": "doctor-test"},
        blind_analysis=BlindAnalysis(
            summary="Doctor production readiness must inspect brain diversity status.",
            open_world_mechanisms=["brain diversity audit -> production readiness"],
        ),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    report = build_doctor_report(settings)
    report["brain"]["audit"].pop("brain_category_source_record_types")

    production = production_readiness_report(report, settings)

    assert production["passed"] is False
    assert (
        "brain: latest brain audit diversity summary is missing"
        in production["findings"]
    )


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
        "NSLAB_REAL_BUNDLE_PATH": "<path-to-real-v11-ACCEPT_FULL-bundle>",
    }
    assert production["remediation_commands"] == [
        "python -m news_scalping_lab.cli research smoke-bundle --path %NSLAB_REAL_BUNDLE_PATH% --require-valid",
        "python -m news_scalping_lab.cli brain rebuild --mode llm-full",
        "python -m news_scalping_lab.cli memory rebuild-index --production",
        "python -m news_scalping_lab.cli warehouse rebuild",
        "python -m news_scalping_lab.cli warehouse verify",
        "python -m news_scalping_lab.cli brain audit --deep",
        "python -m news_scalping_lab.cli training export-sft",
        "python -m news_scalping_lab.cli training export-preference",
        "python -m news_scalping_lab.cli training export-evals",
        "python -m news_scalping_lab.cli training audit",
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

    def fake_audit_coverage(root: Path, *, deep: bool = False) -> dict[str, object]:
        assert deep is False
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
    assert normal_report["brain"]["audit"]["deep"] is False
    assert production_report["api_connections"]["openai"]["required"] is True
    assert production_report["api_connections"]["openai"]["configured"] is False
    assert production_report["api_connections"]["openai"]["status"] == "missing_api_key"
    assert production_report["api_connections"]["brave_search"]["required"] is True
    assert production_report["api_connections"]["brave_search"]["configured"] is False
    assert production_report["brain"]["audit"]["deep"] is True
    compile_report = json.loads(
        (tmp_path / "diagnostics" / "brain_compile_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert compile_report["latest_brain_audit"]["deep"] is True
    assert (
        production_report["api_connections"]["brave_search"]["status"]
        == "missing_api_key"
    )
    assert production_report["readiness"]["passed"] is False
    assert production_report["readiness"]["findings"] == [
        "brave_search: required API key is missing",
        "openai: required API key is missing",
    ]


def test_production_readiness_requires_deep_latest_brain_audit(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-production"
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "brain": {
            "audit": {
                "passed": True,
                "deep": False,
                "brain_category_source_record_types": {},
                "brain_category_source_population_mismatches": [],
                "brain_empty_category_complete_files": [],
                "brain_category_files_identical": [],
                "brain_category_bodies_identical": [],
            }
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["passed"] is False
    assert (
        "brain: latest brain audit was not run with --deep"
        in production["findings"]
    )


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


def test_production_readiness_rejects_on_disk_mock_embedding_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai")
    settings.llm.provider = "openai"
    settings.llm.embedding_model = "text-embedding-3-small"
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
    vector_index_dir = tmp_path / "memory" / "vector_index"
    vector_index_dir.mkdir(parents=True)
    write_json(
        vector_index_dir / "manifest.json",
        {
            "schema_version": "nslab.local_vector_index.v1",
            "embedding_method": "deterministic_hashing_v1",
            "brain_record_count": 2,
            "brain_record_hashes": {"BRAIN-1": "hash-1", "BRAIN-2": "hash-2"},
        },
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": {
            "status": "current",
            "manifest_exists": True,
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
            "brain_records_exists": True,
            "source_brain_record_count": 2,
            "brain_record_count": 2,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["checked"] is True
    assert production["semantic_index"]["manifest"]["passed"] is False
    assert (
        "embedding: on-disk deterministic mock vector index cannot be production semantic index"
        in production["findings"]
    )
    assert (
        "embedding: semantic index report does not match on-disk embedding method"
        in production["findings"]
    )


def test_production_readiness_accepts_semantic_index_record_evidence(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai")
    settings.llm.provider = "openai"
    settings.llm.embedding_model = "text-embedding-3-small"
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
    assert production["semantic_index"]["embedding_model"] == "text-embedding-3-small"
    assert (
        production["semantic_index"]["configured_embedding_model"]
        == "text-embedding-3-small"
    )
    assert not any(
        finding.startswith("embedding:") for finding in production["findings"]
    )


def test_production_readiness_uses_openai_default_embedding_model_when_unset(
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
            "brain_record_count": 2,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["passed"] is True
    assert (
        production["semantic_index"]["configured_embedding_model"]
        == "text-embedding-3-small"
    )
    assert not any(
        finding.startswith("embedding:") for finding in production["findings"]
    )


def test_production_readiness_accepts_matching_on_disk_semantic_index_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai")
    settings.llm.provider = "openai"
    settings.llm.embedding_model = "text-embedding-3-small"
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
    vector_index_dir = tmp_path / "memory" / "vector_index"
    vector_index_dir.mkdir(parents=True)
    write_json(
        vector_index_dir / "manifest.json",
        {
            "schema_version": "nslab.local_vector_index.v1",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
            "brain_record_count": 2,
            "brain_record_hashes": {"BRAIN-1": "hash-1", "BRAIN-2": "hash-2"},
        },
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": {
            "status": "current",
            "manifest_exists": True,
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
            "brain_records_exists": True,
            "source_brain_record_count": 2,
            "brain_record_count": 2,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["checked"] is True
    assert production["semantic_index"]["manifest"]["passed"] is True
    assert production["semantic_index"]["manifest"]["embedding_model"] == (
        "text-embedding-3-small"
    )
    assert not any(
        finding.startswith("embedding:") for finding in production["findings"]
    )


def test_production_readiness_accepts_complete_record_coverage_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(current / "record_coverage_manifest.json", _complete_record_coverage())
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
            "brain_records_exists": True,
            "source_brain_record_count": 2,
            "brain_record_count": 2,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["record_coverage"]["passed"] is True
    assert production["record_coverage"]["status"] == "ready"
    assert production["record_coverage"]["accepted_record_count"] == 2
    assert production["record_coverage"]["swept_record_count"] == 2
    assert production["record_coverage"]["unswept_record_ids"] == []
    assert not any(finding.startswith("records:") for finding in production["findings"])


def test_production_readiness_rejects_incomplete_record_coverage_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    coverage = _complete_record_coverage()
    coverage.update(
        {
            "training_eligible_available_record_count": 3,
            "compiled_record_count": 1,
            "swept_record_count": 1,
            "swept_record_ids": ["BRAIN-1", "BRAIN-1"],
            "unswept_record_ids": ["BRAIN-2"],
            "record_counts_by_type": {"supervised_issuer_day_case": 1},
            "record_counts_by_evidence_phase": {},
            "record_counts_by_training_target": {},
            "ineligible_record_count": 3,
            "audit_only_record_count": 3,
        }
    )
    write_json(current / "record_coverage_manifest.json", coverage)
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
            "brain_records_exists": True,
            "source_brain_record_count": 2,
            "brain_record_count": 2,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["record_coverage"]["passed"] is False
    assert production["record_coverage"]["swept_record_count"] == 1
    assert production["record_coverage"]["swept_record_id_count"] == 2
    assert production["record_coverage"]["unswept_record_ids"] == ["BRAIN-2"]
    assert (
        "records: record coverage manifest has unswept records"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest compiled count does not match accepted count"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest swept count does not match swept IDs"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest swept count does not match available count"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest is marked complete despite production findings"
        in production["findings"]
    )


def test_production_readiness_rejects_missing_training_exports_when_records_exist(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["source_record_count"] == 2
    assert production["training_exports"]["missing_manifest_kinds"] == [
        "evals",
        "preference",
        "sft",
    ]
    assert (
        "training: training export manifests are missing: evals, preference, sft"
        in production["findings"]
    )


def test_production_readiness_accepts_record_backed_training_exports(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is True
    assert production["training_exports"]["status"] == "ready"
    assert production["training_exports"]["source_record_count"] == 2
    assert production["training_exports"]["record_store_source_record_count"] == 2
    assert production["training_exports"]["unique_source_record_count"] == 2
    assert production["training_exports"]["unique_training_eligible_record_count"] == 2
    assert production["training_exports"]["unique_exported_record_count"] == 2
    assert production["training_exports"]["source_record_hash_count"] == 2
    assert production["training_exports"]["weight_validation_statuses"] == {
        "evals": "passed",
        "preference": "passed",
        "sft": "passed",
    }
    assert not any(
        finding.startswith("training:") for finding in production["findings"]
    )


def test_production_readiness_rejects_semantic_index_model_mismatch(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai")
    settings.llm.provider = "openai"
    settings.llm.embedding_model = "text-embedding-3-small"
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
            "embedding_method": "llm_embedding:openai:stale-embedding-model",
            "brain_records_exists": True,
            "source_brain_record_count": 2,
            "brain_record_count": 2,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["passed"] is False
    assert production["semantic_index"]["embedding_model"] == "stale-embedding-model"
    assert (
        production["semantic_index"]["configured_embedding_model"]
        == "text-embedding-3-small"
    )
    assert (
        "embedding: semantic index embedding model does not match configured model"
        in production["findings"]
    )


def test_production_readiness_rejects_implicit_default_semantic_index_model_mismatch(
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
    vector_index_dir = tmp_path / "memory" / "vector_index"
    vector_index_dir.mkdir(parents=True)
    write_json(
        vector_index_dir / "manifest.json",
        {
            "schema_version": "nslab.local_vector_index.v1",
            "embedding_method": "llm_embedding:openai:stale-embedding-model",
            "brain_record_count": 2,
            "brain_record_hashes": {"BRAIN-1": "hash-1", "BRAIN-2": "hash-2"},
        },
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": {
            "status": "current",
            "manifest_exists": True,
            "embedding_method": "llm_embedding:openai:stale-embedding-model",
            "brain_records_exists": True,
            "source_brain_record_count": 2,
            "brain_record_count": 2,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["passed"] is False
    assert (
        production["semantic_index"]["configured_embedding_model"]
        == "text-embedding-3-small"
    )
    assert (
        "embedding: semantic index embedding model does not match configured model"
        in production["findings"]
    )
    assert (
        "embedding: semantic index manifest embedding model does not match configured model"
        in production["findings"]
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


def test_production_readiness_rejects_mock_llm_context_manifests(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-mock-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-mock-llm",
            "model_config": {
                "configured_provider": "mock",
                "provider_class": "DeterministicMockLLMProvider",
                "model": "deterministic-mock",
            },
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

    assert production["llm_evidence"]["passed"] is False
    assert production["llm_evidence"]["mock_model_config_manifest_count"] == 1
    assert production["llm_evidence"]["mock_model_config_manifests"] == [
        {
            "path": "runs/manifests/RUN-mock-llm.json",
            "run_id": "RUN-mock-llm",
            "mock_values": [
                "configured_provider=mock",
                "provider_class=DeterministicMockLLMProvider",
                "model=deterministic-mock",
            ],
        }
    ]
    assert (
        "llm_evidence: mock LLM model_config present in "
        "runs/manifests/RUN-mock-llm.json: configured_provider=mock, "
        "provider_class=DeterministicMockLLMProvider, model=deterministic-mock"
        in production["findings"]
    )


def test_production_readiness_accepts_live_llm_context_manifests(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    trace_dir = tmp_path / "runs" / "traces"
    checkpoint_dir = tmp_path / "runs" / "checkpoints" / "llm"
    manifest_dir.mkdir(parents=True)
    trace_dir.mkdir(parents=True)
    checkpoint_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-live-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-live-llm",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {"daily_blind_analysis": "live-trace-hash"},
        },
    )
    write_json(
        trace_dir / "TRACE-live.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-live",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "prompt_version": "daily_blind_analysis.v1",
            "checkpoint_id": "LLMCKPT-live",
            "input": {"prompt_sha256": "live-trace-hash"},
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
        },
    )
    write_json(
        trace_dir / "TRACE-stale-mock.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-stale-mock",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "DeterministicMockLLMProvider",
            "checkpoint_id": "LLMCKPT-stale-mock",
            "input": {"prompt_sha256": "stale-mock-trace-hash"},
            "model_config": {
                "configured_provider": "mock",
                "provider_class": "DeterministicMockLLMProvider",
                "model": "deterministic-mock",
            },
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-live",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-stale-mock.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-stale-mock",
            "purpose": "daily_blind_analysis",
            "provider": "DeterministicMockLLMProvider",
            "model_config": {
                "configured_provider": "mock",
                "provider_class": "DeterministicMockLLMProvider",
                "model": "deterministic-mock",
            },
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

    assert production["llm_evidence"]["passed"] is True
    assert production["llm_evidence"]["checked_manifest_count"] == 1
    assert production["llm_evidence"]["mock_model_config_manifest_count"] == 0
    assert production["llm_evidence"]["referenced_prompt_hash_count"] == 1
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["missing_trace_prompt_hash_count"] == 0
    assert production["llm_evidence"]["mock_trace_count"] == 0
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert production["llm_evidence"]["mock_checkpoint_count"] == 0
    assert not any(
        finding.startswith("llm_evidence:") for finding in production["findings"]
    )


def test_production_readiness_rejects_mock_llm_trace_and_checkpoint(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    trace_dir = tmp_path / "runs" / "traces"
    checkpoint_dir = tmp_path / "runs" / "checkpoints" / "llm"
    manifest_dir.mkdir(parents=True)
    trace_dir.mkdir(parents=True)
    checkpoint_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-live-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-live-llm",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {"daily_blind_analysis": "mock-trace-hash"},
        },
    )
    write_json(
        trace_dir / "TRACE-mock.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-mock",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "DeterministicMockLLMProvider",
            "prompt_version": "daily_blind_analysis.v1",
            "checkpoint_id": "LLMCKPT-mock",
            "input": {"prompt_sha256": "mock-trace-hash"},
            "model_config": {
                "configured_provider": "mock",
                "provider_class": "DeterministicMockLLMProvider",
                "model": "deterministic-mock",
            },
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-mock.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-mock",
            "purpose": "daily_blind_analysis",
            "provider": "DeterministicMockLLMProvider",
            "model_config": {
                "configured_provider": "mock",
                "provider_class": "DeterministicMockLLMProvider",
                "model": "deterministic-mock",
            },
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

    assert production["llm_evidence"]["passed"] is False
    assert production["llm_evidence"]["checked_manifest_count"] == 1
    assert production["llm_evidence"]["mock_model_config_manifest_count"] == 0
    assert production["llm_evidence"]["referenced_prompt_hash_count"] == 1
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["missing_trace_prompt_hash_count"] == 0
    assert production["llm_evidence"]["mock_trace_count"] == 1
    assert production["llm_evidence"]["mock_traces"] == [
        {
            "path": "runs/traces/TRACE-mock.json",
            "trace_id": "TRACE-mock",
            "purpose": "daily_blind_analysis",
            "prompt_sha256": "mock-trace-hash",
            "mock_values": [
                "provider=DeterministicMockLLMProvider",
                "configured_provider=mock",
                "provider_class=DeterministicMockLLMProvider",
                "model=deterministic-mock",
            ],
        }
    ]
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert production["llm_evidence"]["mock_checkpoint_count"] == 1
    assert production["llm_evidence"]["mock_checkpoints"] == [
        {
            "path": "runs/checkpoints/llm/LLMCKPT-mock.json",
            "checkpoint_id": "LLMCKPT-mock",
            "purpose": "daily_blind_analysis",
            "mock_values": [
                "provider=DeterministicMockLLMProvider",
                "configured_provider=mock",
                "provider_class=DeterministicMockLLMProvider",
                "model=deterministic-mock",
            ],
        }
    ]
    assert (
        "llm_evidence: mock LLM trace present in "
        "runs/traces/TRACE-mock.json: provider=DeterministicMockLLMProvider, "
        "configured_provider=mock, provider_class=DeterministicMockLLMProvider, "
        "model=deterministic-mock"
        in production["findings"]
    )
    assert (
        "llm_evidence: mock LLM checkpoint present in "
        "runs/checkpoints/llm/LLMCKPT-mock.json: "
        "provider=DeterministicMockLLMProvider, configured_provider=mock, "
        "provider_class=DeterministicMockLLMProvider, model=deterministic-mock"
        in production["findings"]
    )


def test_production_readiness_rejects_missing_llm_trace_for_prompt_hash(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    trace_dir = tmp_path / "runs" / "traces"
    manifest_dir.mkdir(parents=True)
    trace_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-live-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-live-llm",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {"daily_blind_analysis": "missing-trace-hash"},
        },
    )
    write_json(
        trace_dir / "TRACE-unrelated.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-unrelated",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": {"prompt_sha256": "unrelated-trace-hash"},
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
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

    assert production["llm_evidence"]["passed"] is False
    assert production["llm_evidence"]["referenced_prompt_hash_count"] == 1
    assert production["llm_evidence"]["checked_trace_count"] == 0
    assert production["llm_evidence"]["missing_trace_prompt_hash_count"] == 1
    assert production["llm_evidence"]["missing_trace_prompt_hashes"] == [
        "missing-trace-hash"
    ]
    assert (
        "llm_evidence: referenced LLM prompt hash has no matching trace: "
        "missing-trace-hash"
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


def test_production_readiness_rejects_mock_web_evidence_artifacts(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    web_source_path = (
        tmp_path / "runs" / "checkpoints" / "web_sources" / "RUN-web" / "web_sources.jsonl"
    )
    manifest_dir.mkdir(parents=True)
    web_source_path.parent.mkdir(parents=True)
    web_source_path.write_text(
        json.dumps(
            {
                "source_id": "WEB-mock",
                "url": "mock://web/WEB-mock",
                "source_url": "mock://web/WEB-mock",
                "title": "mock web evidence",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "run_id": "RUN-web",
            "web_sources": ["WEB-mock"],
            "web_source_artifact": web_source_path.relative_to(tmp_path).as_posix(),
            "web_source_sha256": file_sha256(web_source_path),
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

    assert production["web_evidence"]["passed"] is False
    assert production["web_evidence"]["mock_web_artifact_count"] == 1
    assert production["web_evidence"]["mock_web_url_count"] == 2
    assert production["web_evidence"]["mock_web_artifacts"] == [
        {
            "path": "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl",
            "mock_url_count": 2,
            "sample_values": ["mock://web/WEB-mock"],
        }
    ]
    assert (
        "web_evidence: mock web source URLs present in "
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl (2)"
        in production["findings"]
    )


def test_production_readiness_accepts_live_web_evidence_artifacts(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    web_source_path = (
        tmp_path / "runs" / "checkpoints" / "web_sources" / "RUN-web" / "web_sources.jsonl"
    )
    manifest_dir.mkdir(parents=True)
    web_source_path.parent.mkdir(parents=True)
    web_source_path.write_text(
        json.dumps(
            {
                "source_id": "WEB-live",
                "url": "https://example.test/news",
                "source_url": "https://example.test/news",
                "title": "live web evidence",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "run_id": "RUN-web",
            "web_sources": ["WEB-live"],
            "web_source_artifact": web_source_path.relative_to(tmp_path).as_posix(),
            "web_source_sha256": file_sha256(web_source_path),
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

    assert production["web_evidence"]["passed"] is True
    assert production["web_evidence"]["checked_manifest_count"] == 1
    assert production["web_evidence"]["checked_artifact_reference_count"] == 1
    assert production["web_evidence"]["checked_artifact_count"] == 1
    assert production["web_evidence"]["missing_artifact_hash_count"] == 0
    assert production["web_evidence"]["artifact_sha256_mismatch_count"] == 0
    assert production["web_evidence"]["mock_web_artifact_count"] == 0
    assert not any(
        finding.startswith("web_evidence:") for finding in production["findings"]
    )


def test_production_readiness_rejects_web_evidence_artifact_sha_mismatch(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    web_source_path = (
        tmp_path / "runs" / "checkpoints" / "web_sources" / "RUN-web" / "web_sources.jsonl"
    )
    manifest_dir.mkdir(parents=True)
    web_source_path.parent.mkdir(parents=True)
    web_source_path.write_text(
        json.dumps(
            {
                "source_id": "WEB-live",
                "url": "https://example.test/news",
                "source_url": "https://example.test/news",
                "title": "live web evidence",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "run_id": "RUN-web",
            "web_sources": ["WEB-live"],
            "web_source_artifact": web_source_path.relative_to(tmp_path).as_posix(),
            "web_source_sha256": "0" * 64,
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

    assert production["web_evidence"]["passed"] is False
    assert production["web_evidence"]["checked_manifest_count"] == 1
    assert production["web_evidence"]["checked_artifact_reference_count"] == 1
    assert production["web_evidence"]["checked_artifact_count"] == 1
    assert production["web_evidence"]["artifact_sha256_mismatch_count"] == 1
    assert production["web_evidence"]["artifact_sha256_mismatches"] == [
        {
            "manifest": "runs/manifests/RUN-web.json",
            "artifact_field": "web_source_artifact",
            "sha_field": "web_source_sha256",
            "artifact": "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl",
            "expected_sha256": "0" * 64,
            "observed_sha256": file_sha256(web_source_path),
        }
    ]
    assert (
        "web_evidence: web evidence artifact sha256 mismatch: "
        "runs/manifests/RUN-web.json web_source_sha256 for "
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl"
        in production["findings"]
    )


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
    assert production["llm_full_brain"]["status"] == "not_applicable"
    assert production["llm_full_brain"]["catalog_only"] is True
    assert production["llm_full_brain"]["findings"] == [
        "current manifest is catalog_only",
        "current manifest build_mode is catalog, not llm-full",
        "llm-full compile manifest is missing",
        "compiled claims JSONL is missing",
    ]
    assert "brain: current manifest is catalog_only" in production["findings"]
    assert (
        "brain: current manifest build_mode is catalog, not llm-full"
        in production["findings"]
    )
    assert "brain: llm-full compile manifest is missing" in production["findings"]
    assert "brain: compiled claims JSONL is missing" in production["findings"]


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
    assert report["first_production_failure_reasons"] == []
    assert report["selected"]["source"] == "data_inbox"
    assert report["selected"]["inspection"]["raw_record_count"] == 327
    assert report["selected"]["inspection"]["missing_payload_reference_count"] == 0
    assert report["selected"]["inspection"]["record_id_set_matches_raw"] is True
    assert report["selected"]["inspection"]["record_type_counts_match_raw"] is True
    assert (
        report["selected"]["inspection"]["training_eligible_count_matches_raw"]
        is True
    )
    assert report["selected"]["inspection"]["raw_payload_hashes_match"] is True
    assert report["selected"]["inspection"]["import_loss_audit_passed"] is True


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
    assert report["first_production_failure_reasons"] == [
        "validation_passed=False expected True",
        "hash_mismatch_count=1 expected 0",
    ]
    production_inspection = next(
        item for item in report["inspections"] if item["production_source"] is True
    )
    assert production_inspection["inspection"]["failure_reasons"] == [
        "validation_passed=False expected True",
        "hash_mismatch_count=1 expected 0",
    ]
    assert report["production_failed_inspection_count"] == 1
    assert report["synthetic_valid_smoke_count"] == 1


def test_production_readiness_reports_real_bundle_failure_reasons(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    bundle = tmp_path / "data" / "inbox" / "research" / "real_bundle.md"
    bundle.write_text("real bundle", encoding="utf-8")
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _invalid_v11_bundle_inspection(path),
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

    assert production["real_bundle_smoke"]["status"] == "failed"
    assert production["real_bundle_smoke"]["first_production_failure_reasons"] == [
        "validation_passed=False expected True",
        "hash_mismatch_count=1 expected 0",
    ]
    assert "real_bundle: v11 ACCEPT_FULL smoke failed" in production["findings"]
    assert (
        "real_bundle: validation_passed=False expected True"
        in production["findings"]
    )
    assert "real_bundle: hash_mismatch_count=1 expected 0" in production["findings"]


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


def test_real_bundle_smoke_rejects_invalid_typed_payload(
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
        inspection["typed_payload_valid"] = False
        inspection["invalid_typed_payload_record_count"] = 1
        inspection["validation"] = {
            **dict(inspection["validation"]),
            "passed": False,
            "typed_payload_valid": False,
            "invalid_typed_payload_record_ids": ["BRAIN-invalid-typed"],
        }
        return inspection

    monkeypatch.setattr("news_scalping_lab.diagnostics.inspect_versioned_bundle", inspect)

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "failed"
    assert report["passed"] is False
    assert report["first_production_status"] == "failed"
    assert report["production_failed_inspection_count"] == 1
    assert report["inspections"][0]["inspection"]["typed_payload_valid"] is False
    assert report["inspections"][0]["inspection"]["invalid_typed_payload_record_count"] == 1


def test_real_bundle_smoke_rejects_import_loss_parity_gap(
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
        inspection["record_id_set_matches_raw"] = False
        inspection["missing_normalized_record_ids"] = ["BRAIN-missing"]
        inspection["import_loss_audit_passed"] = False
        inspection["validation"] = {
            **dict(inspection["validation"]),
            "import_loss_audit_passed": False,
        }
        return inspection

    monkeypatch.setattr("news_scalping_lab.diagnostics.inspect_versioned_bundle", inspect)

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "failed"
    assert report["passed"] is False
    assert report["first_production_status"] == "failed"
    assert report["production_failed_inspection_count"] == 1
    assert report["inspections"][0]["inspection"]["record_id_set_matches_raw"] is False
    assert report["inspections"][0]["inspection"]["import_loss_audit_passed"] is False
    assert report["inspections"][0]["inspection"]["missing_normalized_record_ids"] == [
        "BRAIN-missing"
    ]


def test_real_bundle_smoke_rejects_missing_quarantine_counts(
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
        del inspection["quarantined_bundle_count"]
        del inspection["quarantined_raw_record_count"]
        return inspection

    monkeypatch.setattr("news_scalping_lab.diagnostics.inspect_versioned_bundle", inspect)

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "failed"
    assert report["passed"] is False
    assert report["valid_smoke_count"] == 0
    assert report["real_valid_smoke_count"] == 0
    assert report["first_production_status"] == "failed"
    assert report["first_production_failure_reasons"] == [
        "quarantined_bundle_count=None expected 0",
        "quarantined_raw_record_count=None expected 0",
    ]
    assert (
        report["inspections"][0]["inspection"]["v11_accept_full_smoke_passed"]
        is False
    )


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
            "source_ids": [_real_smoke_source_id(episode_id)],
        },
    )
    _write_real_smoke_validation_report(tmp_path, episode_id, inspection)
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
    assert production["real_bundle_import"]["expected_record_id_count"] == 327
    assert production["real_bundle_import"]["expected_record_ids"] == record_ids
    assert production["real_bundle_import"]["quarantined_bundle_count"] == 0
    assert production["real_bundle_import"]["quarantined_raw_record_count"] == 0
    assert production["real_bundle_import"]["quarantined_record_count"] == 0
    assert (
        production["real_bundle_import"][
            "validation_report_raw_normalized_record_count_matches"
        ]
        is True
    )
    assert (
        production["real_bundle_import"][
            "validation_report_missing_normalized_record_count"
        ]
        == 0
    )
    assert (
        production["real_bundle_import"][
            "validation_report_extra_normalized_record_count"
        ]
        == 0
    )
    assert production["real_bundle_import"][
        "observed_training_eligible_record_count"
    ] == 325
    assert not any(
        finding.startswith("real_bundle_import:")
        for finding in production["findings"]
    )


def test_production_readiness_rejects_failed_real_import_validation_report(
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
            "source_ids": [_real_smoke_source_id(episode_id)],
        },
    )
    _write_real_smoke_validation_report(
        tmp_path,
        episode_id,
        inspection,
        overrides={
            "passed": False,
            "import_loss_audit_passed": False,
            "record_id_set_matches_raw": False,
            "raw_normalized_record_count_matches": False,
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
    assert production["real_bundle_import"]["validation_report_exists"] is True
    assert (
        production["real_bundle_import"]["validation_report_import_loss_audit_passed"]
        is False
    )
    assert (
        production["real_bundle_import"][
            "validation_report_raw_normalized_record_count_matches"
        ]
        is False
    )
    assert (
        "real_bundle_import: validation report import loss audit did not pass"
        in production["findings"]
    )
    assert (
        "real_bundle_import: validation report raw record ID parity failed"
        in production["findings"]
    )
    assert (
        "real_bundle_import: validation report raw/normalized record count parity failed"
        in production["findings"]
    )


def test_production_readiness_rejects_real_import_validation_report_id_gap(
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
            "source_ids": [_real_smoke_source_id(episode_id)],
        },
    )
    wrong_record_id = "wrong-validation-record-id"
    missing_record_id = record_ids[-1]
    _write_real_smoke_validation_report(
        tmp_path,
        episode_id,
        inspection,
        overrides={
            "normalized_record_ids": [*record_ids[:-1], wrong_record_id],
            "missing_normalized_record_ids": [missing_record_id],
            "missing_normalized_record_count": 1,
            "extra_normalized_record_ids": [wrong_record_id],
            "extra_normalized_record_count": 1,
            "raw_payload_hash_mismatch_record_ids": [record_ids[0]],
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
        production["real_bundle_import"]["validation_report_normalized_record_id_count"]
        == 327
    )
    assert (
        production["real_bundle_import"][
            "validation_report_missing_normalized_record_count"
        ]
        == 1
    )
    assert (
        production["real_bundle_import"][
            "validation_report_extra_normalized_record_count"
        ]
        == 1
    )
    assert production["real_bundle_import"][
        "validation_report_missing_normalized_record_ids"
    ] == [missing_record_id]
    assert production["real_bundle_import"][
        "validation_report_extra_normalized_record_ids"
    ] == [wrong_record_id]
    assert production["real_bundle_import"][
        "validation_report_raw_payload_hash_mismatch_record_ids"
    ] == [record_ids[0]]
    assert (
        "real_bundle_import: validation report normalized record IDs do not match real smoke"
        in production["findings"]
    )
    assert (
        "real_bundle_import: validation report has missing normalized record IDs"
        in production["findings"]
    )
    assert (
        "real_bundle_import: validation report has extra normalized record IDs"
        in production["findings"]
    )
    assert (
        "real_bundle_import: validation report has raw payload hash mismatches"
        in production["findings"]
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
            "source_ids": [_real_smoke_source_id(episode_id)],
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
    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["status"] == "attention"
    assert any(
        finding.startswith(
            "training: training export source record store is unreadable"
        )
        for finding in production["findings"]
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
            "source_ids": [_real_smoke_source_id(episode_id)],
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
            "source_ids": [_real_smoke_source_id(episode_id)],
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


def test_production_readiness_rejects_failed_deep_record_store_audit(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"

    def failed_record_store_audit(root: Path, *, deep: bool = False) -> dict[str, object]:
        assert root == tmp_path
        assert deep is True
        return {
            "schema_version": "nslab.record_store_audit.v1",
            "passed": False,
            "deep": True,
            "record_count": 2,
            "all_record_count": 2,
            "staged_record_count": 0,
            "episode_count": 1,
            "training_eligible_record_count": 2,
            "brain_delta_record_id_mismatch_episode_ids": ["EP-import-loss"],
            "records_with_raw_payload_hash_mismatch": ["BRAIN-import-loss-1"],
            "findings": [
                "brain_delta raw record IDs do not match normalized records",
                "record raw payload hashes do not match source lines",
            ],
        }

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_record_store",
        failed_record_store_audit,
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

    assert production["record_store"]["passed"] is False
    assert production["record_store"]["deep"] is True
    assert production["record_store"]["raw_record_count"] == 0
    assert production["record_store"]["normalized_record_count"] == 2
    assert production["record_store"]["raw_normalized_record_count_matches"] is False
    assert production["record_store"]["raw_record_counts_by_episode"] == {}
    assert production["record_store"]["dropped_record_count"] == 0
    assert production["record_store"]["extra_normalized_record_count"] == 2
    assert production["record_store"]["quarantined_record_count"] == 0
    assert production["record_store"]["brain_delta_record_id_mismatch_episode_ids"] == [
        "EP-import-loss"
    ]
    assert production["record_store"]["records_with_raw_payload_hash_mismatch"] == [
        "BRAIN-import-loss-1"
    ]
    assert (
        "records: brain_delta raw record IDs do not match normalized records"
        in production["findings"]
    )
    assert (
        "records: record raw payload hashes do not match source lines"
        in production["findings"]
    )
    assert (
        "records: extra_normalized_record_count=2 expected 0"
        in production["findings"]
    )


def test_production_readiness_rejects_record_store_raw_normalized_count_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    episode_dir = tmp_path / "research" / "episodes" / "EP-import-loss"
    episode_dir.mkdir(parents=True)
    write_json(
        episode_dir / "bundle_envelope.json",
        {
            "episode_id": "EP-import-loss",
            "raw_block_counts": {"brain_delta.jsonl": 3},
        },
    )

    def passed_record_store_audit(root: Path, *, deep: bool = False) -> dict[str, object]:
        assert root == tmp_path
        assert deep is True
        return {
            "schema_version": "nslab.record_store_audit.v1",
            "passed": True,
            "deep": True,
            "record_count": 2,
            "all_record_count": 2,
            "staged_record_count": 0,
            "episode_count": 1,
            "training_eligible_record_count": 2,
            "findings": [],
        }

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_record_store",
        passed_record_store_audit,
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

    assert production["record_store"]["passed"] is False
    assert production["record_store"]["raw_record_count"] == 3
    assert production["record_store"]["normalized_record_count"] == 2
    assert production["record_store"]["raw_normalized_record_count_matches"] is False
    assert production["record_store"]["dropped_record_count"] == 1
    assert production["record_store"]["extra_normalized_record_count"] == 0
    assert (
        "records: raw/normalized record count mismatch: "
        "raw_record_count=3 normalized_record_count=2"
        in production["findings"]
    )
    assert "records: dropped_record_count=1 expected 0" in production["findings"]


def test_production_readiness_rejects_record_store_loss_or_quarantine_counts(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"

    def passed_record_store_audit(root: Path, *, deep: bool = False) -> dict[str, object]:
        assert root == tmp_path
        assert deep is True
        return {
            "schema_version": "nslab.record_store_audit.v1",
            "passed": True,
            "deep": True,
            "record_count": 2,
            "all_record_count": 2,
            "staged_record_count": 0,
            "episode_count": 1,
            "training_eligible_record_count": 2,
            "findings": [],
        }

    def lossy_record_store_report(
        root: Path,
        audit_result: dict[str, object],
    ) -> dict[str, object]:
        assert root == tmp_path
        assert audit_result["passed"] is True
        return {
            "schema_version": "nslab.brain_record_store_report.v1",
            "record_count": 2,
            "raw_record_count": 2,
            "normalized_record_count": 2,
            "raw_record_counts_by_episode": {"EP-loss": 2},
            "raw_normalized_record_count_matches": True,
            "dropped_record_count": 1,
            "extra_normalized_record_count": 0,
            "quarantined_bundle_count": 1,
            "quarantined_raw_record_count": 1,
            "quarantined_record_count": 1,
        }

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_record_store",
        passed_record_store_audit,
    )
    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.record_store_report_payload",
        lossy_record_store_report,
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

    assert production["record_store"]["passed"] is False
    assert production["record_store"]["dropped_record_count"] == 1
    assert production["record_store"]["extra_normalized_record_count"] == 0
    assert production["record_store"]["quarantined_bundle_count"] == 1
    assert production["record_store"]["quarantined_raw_record_count"] == 1
    assert production["record_store"]["quarantined_record_count"] == 1
    assert "records: dropped_record_count=1 expected 0" in production["findings"]
    assert "records: quarantined_record_count=1 expected 0" in production["findings"]
    assert "records: quarantined_bundle_count=1 expected 0" in production["findings"]


def test_production_readiness_requires_deep_record_store_audit(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"

    def shallow_record_store_audit(root: Path, *, deep: bool = False) -> dict[str, object]:
        assert root == tmp_path
        assert deep is True
        return {
            "schema_version": "nslab.record_store_audit.v1",
            "passed": True,
            "deep": False,
            "record_count": 0,
            "all_record_count": 0,
            "staged_record_count": 0,
            "episode_count": 0,
            "training_eligible_record_count": 0,
            "findings": [],
        }

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_record_store",
        shallow_record_store_audit,
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

    assert production["record_store"]["passed"] is False
    assert production["record_store"]["status"] == "attention"
    assert (
        "records: deep record-store audit was not run"
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
        "NSLAB_REAL_BUNDLE_PATH": "<path-to-real-v11-ACCEPT_FULL-bundle>",
    }
    assert (
        "real_bundle: no readable v11 ACCEPT_FULL bundle candidate; real smoke pending"
        in production["findings"]
    )
    assert production["remediation_commands"] == [
        "python -m news_scalping_lab.cli research smoke-bundle --path %NSLAB_REAL_BUNDLE_PATH% --require-valid",
        "python -m news_scalping_lab.cli brain rebuild --mode llm-full",
        "python -m news_scalping_lab.cli memory rebuild-index --production",
        "python -m news_scalping_lab.cli warehouse rebuild",
        "python -m news_scalping_lab.cli warehouse verify",
        "python -m news_scalping_lab.cli brain audit --deep",
        "python -m news_scalping_lab.cli training export-sft",
        "python -m news_scalping_lab.cli training export-preference",
        "python -m news_scalping_lab.cli training export-evals",
        "python -m news_scalping_lab.cli training audit",
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
    episode_dir = root / "research" / "episodes" / episode_id
    episode_dir.mkdir(parents=True, exist_ok=True)
    record_path = root / "memory" / "records" / f"{episode_id}.jsonl"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_counts_by_type = inspection["record_counts_by_type"]
    assert isinstance(record_counts_by_type, dict)
    training_eligible_count = inspection["training_eligible_record_count"]
    assert isinstance(training_eligible_count, int)
    record_ids = _real_smoke_record_ids(inspection)
    source_id = _real_smoke_source_id(episode_id)
    trade_day = date.fromisoformat(str(inspection["trade_date"]))
    available_from = datetime(2026, 6, 23, 0, 0, 0, tzinfo=KST)
    raw_rows: list[dict[str, object]] = []
    records: list[BrainRecordEnvelope] = []
    record_index = 0
    for record_type, count in sorted(record_counts_by_type.items()):
        assert isinstance(record_type, str)
        assert isinstance(count, int)
        for _index in range(count):
            training_eligible = len(raw_rows) < training_eligible_count
            raw_payload = {
                "record_id": record_ids[record_index],
                "record_type": record_type,
                "episode_id": episode_id,
                "trade_date": trade_day.isoformat(),
                "available_from": available_from.isoformat(),
                "training_eligible": training_eligible,
                "provenance_source_ids": [source_id],
            }
            raw_payload_sha = sha256_text(
                json.dumps(raw_payload, ensure_ascii=False, sort_keys=True)
            )
            records.append(
                BrainRecordEnvelope(
                    record_id=record_ids[record_index],
                    record_type=record_type,
                    episode_id=episode_id,
                    trade_date=trade_day,
                    available_from=available_from,
                    training_target="real_smoke_fixture",
                    evidence_phase="POSTMORTEM",
                    training_eligible=training_eligible,
                    eligibility_reason="real smoke fixture",
                    status="tentative",
                    confidence_label="low",
                    provenance_source_ids=[source_id],
                    raw_payload_sha256=raw_payload_sha,
                    normalized_payload_sha256=sha256_text(
                        canonical_json(raw_payload)
                    ),
                    typed_payload_status="KNOWN_TYPED_PAYLOAD",
                    source_block="brain_delta.jsonl",
                    source_line=len(raw_rows) + 1,
                    payload=raw_payload,
                )
            )
            raw_rows.append(raw_payload)
            record_index += 1
    brain_delta_path = episode_dir / "brain_delta.jsonl"
    source_ledger_path = episode_dir / "source_ledger.jsonl"
    brain_delta_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in raw_rows),
        encoding="utf-8",
    )
    source_ledger_path.write_text(
        json.dumps({"source_id": source_id}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record_path.write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )
    envelope_path = episode_dir / "bundle_envelope.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    assert isinstance(envelope, dict)
    envelope.update(
        {
            "raw_block_paths": {
                "brain_delta.jsonl": brain_delta_path.relative_to(root).as_posix(),
                "source_ledger.jsonl": source_ledger_path.relative_to(root).as_posix(),
            },
            "raw_block_hashes": {
                "brain_delta.jsonl": sha256_text(
                    brain_delta_path.read_text(encoding="utf-8")
                ),
                "source_ledger.jsonl": sha256_text(
                    source_ledger_path.read_text(encoding="utf-8")
                ),
            },
            "raw_block_counts": {
                "brain_delta.jsonl": len(raw_rows),
                "source_ledger.jsonl": 1,
            },
        }
    )
    write_json(envelope_path, envelope)
    return record_path


def _write_real_smoke_validation_report(
    root: Path,
    episode_id: str,
    inspection: dict[str, object],
    *,
    overrides: dict[str, object] | None = None,
) -> None:
    record_ids = _real_smoke_record_ids(inspection)
    report = {
        "schema_version": "nslab.versioned_bundle_validation.v1",
        "passed": True,
        "record_count": inspection["normalized_record_count"],
        "training_eligible_record_count": inspection[
            "training_eligible_record_count"
        ],
        "record_count_matches_manifest": True,
        "training_eligible_count_matches_manifest": True,
        "typed_payload_valid": True,
        "import_loss_audit_passed": True,
        "record_id_set_matches_raw": True,
        "record_type_counts_match_raw": True,
        "training_eligible_count_matches_raw": True,
        "raw_payload_hashes_match": True,
        "raw_normalized_record_count_matches": True,
        "raw_record_counts_by_type": inspection["record_counts_by_type"],
        "raw_record_ids": record_ids,
        "normalized_record_ids": record_ids,
        "missing_normalized_record_ids": [],
        "missing_normalized_record_count": 0,
        "extra_normalized_record_ids": [],
        "extra_normalized_record_count": 0,
        "raw_payload_hash_mismatch_record_ids": [],
    }
    if overrides:
        report.update(overrides)
    write_json(
        root / "research" / "episodes" / episode_id / "validation_report.json",
        report,
    )


def _production_base_report() -> dict[str, object]:
    return {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }


def _write_training_record_store(root: Path) -> None:
    episode_id = "EP-training-production"
    records = [
        _training_record(
            record_id="BRAIN-TRAIN-ISSUER",
            record_type="supervised_issuer_day_case",
            training_target="issuer_day_price_response",
            payload={
                "record_id": "BRAIN-TRAIN-ISSUER",
                "record_type": "supervised_issuer_day_case",
                "episode_id": episode_id,
                "trade_date": "2030-01-10",
                "ticker": "TRAIN",
                "safe_D1_features": {"market_cap": "known before cutoff"},
                "blind_fact_ids": ["FACT-TRAIN"],
                "blind_inference_ids": ["INF-TRAIN"],
                "event_ids": ["EVT-TRAIN"],
                "response_class": "winner",
                "D_outcome": {"label_quality": "verified"},
                "sample_weight": 1.0,
                "attribution_status": "attributed",
            },
        ),
        _training_record(
            record_id="BRAIN-TRAIN-PAIR",
            record_type="blind_leader_preference_pair",
            training_target="outcome_preferred_candidate",
            payload={
                "record_id": "BRAIN-TRAIN-PAIR",
                "record_type": "blind_leader_preference_pair",
                "episode_id": episode_id,
                "trade_date": "2030-01-10",
                "blind_pair_id": "PAIR-TRAIN",
                "blind_preferred_candidate_id": "CAND-WIN",
                "blind_rejected_candidate_id": "CAND-LOSE",
                "blind_preferred_ticker": "WIN",
                "blind_rejected_ticker": "LOSE",
                "outcome_preferred_candidate_id": "CAND-WIN",
                "outcome_rejected_candidate_id": "CAND-LOSE",
                "outcome_winner_ticker": "WIN",
                "blind_preference_correct": True,
                "safe_D1_features": {"relative_strength": "known before cutoff"},
            },
        ),
    ]
    records_dir = root / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / f"{episode_id}.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )
    manifest_dir = root / "memory" / "record_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        manifest_dir / f"{episode_id}.json",
        {
            "schema_version": "nslab.record_manifest.v1",
            "episode_id": episode_id,
            "accepted": True,
            "acceptance_status": "accepted",
            "record_count": len(records),
            "training_eligible_record_count": len(records),
            "record_counts_by_type": {
                "blind_leader_preference_pair": 1,
                "supervised_issuer_day_case": 1,
            },
        },
    )


def _training_record(
    *,
    record_id: str,
    record_type: str,
    training_target: str,
    payload: dict[str, object],
) -> BrainRecordEnvelope:
    available_from = datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST)
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id="EP-training-production",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target=training_target,
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        eligibility_reason="production training fixture",
        status="supported",
        confidence_label="medium",
        provenance_source_ids=[f"SRC-{record_id}"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )


def _complete_record_coverage() -> dict[str, object]:
    return {
        "schema_version": "nslab.record_coverage_manifest.v1",
        "accepted_record_count": 2,
        "available_record_count": 2,
        "available_record_count_as_of": 2,
        "training_eligible_available_record_count": 1,
        "training_eligible_record_count_as_of": 1,
        "compiled_record_count": 2,
        "swept_record_count": 2,
        "swept_record_ids": ["BRAIN-1", "BRAIN-2"],
        "unswept_record_ids": [],
        "record_counts_by_type": {
            "counterexample": 1,
            "supervised_issuer_day_case": 1,
        },
        "record_counts_by_evidence_phase": {
            "AUDIT": 1,
            "POSTMORTEM": 1,
        },
        "record_counts_by_training_target": {
            "audit_only": 1,
            "issuer_day_price_response": 1,
        },
        "ineligible_record_count": 1,
        "audit_only_record_count": 1,
        "coverage_complete": True,
    }


def _real_smoke_source_id(episode_id: str) -> str:
    return f"{episode_id}:source"


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
    record_counts_by_type = {
        "supervised_issuer_day_case": 150,
        "supervised_direct_event_case": 171,
        "supervised_theme_formation_case": 3,
        "blind_leader_preference_pair": 3,
    }
    record_ids: list[str] = []
    for record_type, count in sorted(record_counts_by_type.items()):
        record_ids.extend(f"{record_type}-{index}" for index in range(count))
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
        "raw_normalized_record_count_matches": True,
        "training_eligible_record_count": 325,
        "dropped_record_count": 0,
        "missing_normalized_record_count": 0,
        "extra_normalized_record_count": 0,
        "quarantined_bundle_count": 0,
        "quarantined_raw_record_count": 0,
        "quarantined_record_count": 0,
        "record_counts_by_type": record_counts_by_type,
        "raw_record_ids": record_ids,
        "normalized_record_ids": record_ids,
        "raw_record_without_id_count": 0,
        "record_id_set_comparable": True,
        "record_id_set_matches_raw": True,
        "missing_normalized_record_ids": [],
        "extra_normalized_record_ids": [],
        "raw_record_counts_by_type": record_counts_by_type,
        "record_type_counts_match_raw": True,
        "raw_training_eligible_record_count": 325,
        "training_eligible_count_matches_raw": True,
        "raw_payload_hashes_match": True,
        "raw_payload_hash_mismatch_record_ids": [],
        "import_loss_audit_passed": True,
        "typed_payload_valid": True,
        "invalid_typed_payload_record_count": 0,
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
            "typed_payload_valid": True,
            "invalid_typed_payload_record_ids": [],
            "import_loss_audit_passed": True,
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
