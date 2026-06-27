from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from typer.testing import CliRunner

from news_scalping_lab.audits.coverage import audit_coverage
from news_scalping_lab.brain.compiler import (
    BRAIN_FILES,
    LLM_FULL_COMPILER_VERSION,
    BrainCompiler,
    _brain_category,
)
from news_scalping_lab.cli import app
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import BlindAnalysis, ResearchEpisode
from news_scalping_lab.contracts.schemas import SCHEMA_MODELS, export_json_schemas
from news_scalping_lab.diagnostics import (
    build_doctor_report,
    production_readiness_report,
    real_bundle_smoke_report,
)
from news_scalping_lab.records.models import BrainRecordEnvelope, CompiledBrainClaim
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.training import export_training
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
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
        "covered_episode_ids": [],
        "expected_covered_episode_ids": [],
        "missing_covered_episode_ids": [],
        "unexpected_covered_episode_ids": [],
        "duplicate_covered_episode_ids": [],
        "missing_episode_ids": [],
        "expected_missing_episode_ids": [],
        "unknown_missing_episode_ids": [],
        "missing_missing_episode_ids": [],
        "unexpected_missing_episode_ids": [],
        "accepted_episode_store_readable": True,
        "accepted_episode_store_error": None,
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
        "expected_brain_version": manifest.brain_version,
        "created_at": manifest.created_at.isoformat(),
        "expected_created_at": manifest.created_at.isoformat(),
        "build_mode": "full",
        "expected_build_mode": "full",
        "catalog_only": True,
        "expected_catalog_only": True,
        "coverage_complete": True,
        "covered_episode_count": 1,
        "manifest_covered_episode_count": 1,
        "covered_episode_ids": ["EP-doctor-coverage"],
        "expected_covered_episode_ids": ["EP-doctor-coverage"],
        "missing_covered_episode_ids": [],
        "unexpected_covered_episode_ids": [],
        "duplicate_covered_episode_ids": [],
        "missing_episode_ids": [],
        "expected_missing_episode_ids": [],
        "unknown_missing_episode_ids": [],
        "missing_missing_episode_ids": [],
        "unexpected_missing_episode_ids": [],
        "accepted_episode_store_readable": True,
        "accepted_episode_store_error": None,
        "finding_count": 0,
        "findings": [],
        "status": "complete",
    }
    assert report["brain"]["audit"]["brain_build_mode"] == "full"
    assert report["brain"]["audit"]["brain_category_file_count"] == 9
    assert isinstance(
        report["brain"]["audit"]["brain_category_source_record_types"],
        dict,
    )
    assert report["brain"]["audit"]["llm_compile_category_schema_mismatches"] == []
    assert report["brain"]["audit"]["brain_category_source_population_mismatches"] == []
    assert report["brain"]["audit"]["brain_empty_category_complete_files"] == []
    assert isinstance(report["brain"]["audit"]["finding_count"], int)


def test_doctor_report_rejects_stale_brain_coverage_manifest(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    episode = ResearchEpisode(
        episode_id="EP-doctor-stale-coverage",
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
        research_version="doctor-test-v1",
        price_source_snapshot={"source": "doctor-test"},
        blind_analysis=BlindAnalysis(
            summary="Doctor stale coverage status lesson.",
            open_world_mechanisms=["accepted episode -> stale coverage manifest"],
        ),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)
    manifest = BrainCompiler(tmp_path).rebuild(mode="full")
    coverage_path = tmp_path / "brain" / "current" / "coverage_manifest.json"
    coverage_manifest = read_json(coverage_path)
    coverage_manifest.update(
        {
            "brain_version": "brain-stale",
            "created_at": manifest.created_at.replace(
                year=manifest.created_at.year + 1
            ).isoformat(),
            "build_mode": "llm-full",
            "catalog_only": False,
        }
    )
    write_json(coverage_path, coverage_manifest)

    report = build_doctor_report(settings)

    assert report["readiness"]["passed"] is False
    assert report["brain"]["coverage"]["coverage_complete"] is False
    assert report["brain"]["coverage"]["status"] == "incomplete"
    assert report["brain"]["coverage"]["brain_version"] == "brain-stale"
    assert report["brain"]["coverage"]["expected_brain_version"] == manifest.brain_version
    assert (
        "coverage manifest brain_version does not match current brain manifest"
        in report["brain"]["coverage"]["findings"]
    )
    assert (
        "coverage manifest created_at does not match current brain manifest"
        in report["brain"]["coverage"]["findings"]
    )
    assert (
        "coverage manifest build_mode does not match current brain manifest"
        in report["brain"]["coverage"]["findings"]
    )
    assert (
        "coverage manifest catalog_only does not match current brain manifest"
        in report["brain"]["coverage"]["findings"]
    )
    assert "brain: accepted episodes are not fully covered" in report["readiness"][
        "findings"
    ]


def test_doctor_report_rejects_wrong_brain_coverage_episode_ids(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    episode = ResearchEpisode(
        episode_id="EP-doctor-coverage-id",
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
        research_version="doctor-test-v1",
        price_source_snapshot={"source": "doctor-test"},
        blind_analysis=BlindAnalysis(
            summary="Doctor coverage ID status lesson.",
            open_world_mechanisms=["accepted episode -> tampered coverage IDs"],
        ),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    coverage_path = tmp_path / "brain" / "current" / "coverage_manifest.json"
    coverage_manifest = read_json(coverage_path)
    coverage_manifest.update(
        {
            "covered_episode_ids": ["EP-unknown-covered"],
            "missing_episode_ids": [],
            "covered_episode_count": 1,
            "coverage_complete": True,
        }
    )
    write_json(coverage_path, coverage_manifest)

    report = build_doctor_report(settings)

    assert report["readiness"]["passed"] is False
    coverage = report["brain"]["coverage"]
    assert coverage["coverage_complete"] is False
    assert coverage["status"] == "incomplete"
    assert coverage["covered_episode_ids"] == ["EP-unknown-covered"]
    assert coverage["expected_covered_episode_ids"] == ["EP-doctor-coverage-id"]
    assert coverage["missing_covered_episode_ids"] == ["EP-doctor-coverage-id"]
    assert coverage["unexpected_covered_episode_ids"] == ["EP-unknown-covered"]
    assert coverage["expected_missing_episode_ids"] == ["EP-doctor-coverage-id"]
    assert coverage["missing_missing_episode_ids"] == ["EP-doctor-coverage-id"]
    assert coverage["unexpected_missing_episode_ids"] == []
    assert (
        "coverage manifest includes unknown covered episodes"
        in coverage["findings"]
    )
    assert (
        "coverage manifest does not cover accepted episodes"
        in coverage["findings"]
    )
    assert (
        "coverage manifest missing IDs do not match accepted episodes"
        in coverage["findings"]
    )
    assert "brain: accepted episodes are not fully covered" in report["readiness"][
        "findings"
    ]


def test_doctor_report_rejects_stale_empty_project_coverage_manifest(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        llm_provider="openai",
        web_provider="brave",
    )
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    WarehouseStore(tmp_path).rebuild_all()
    LocalRetrievalStore(tmp_path).rebuild_index()
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
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
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True, exist_ok=True)
    write_json(
        current / "coverage_manifest.json",
        {
            "accepted_episode_count": 0,
            "covered_episode_count": 1,
            "covered_episode_ids": ["EP-ghost"],
            "missing_episode_ids": [],
            "coverage_complete": True,
        },
    )

    report = build_doctor_report(settings)

    assert report["brain"]["accepted_episode_count"] == 0
    assert report["brain"]["coverage"]["status"] == "incomplete"
    assert report["brain"]["coverage"]["covered_episode_ids"] == ["EP-ghost"]
    assert report["brain"]["coverage"]["unexpected_covered_episode_ids"] == [
        "EP-ghost"
    ]
    assert (
        "coverage manifest includes unknown covered episodes"
        in report["brain"]["coverage"]["findings"]
    )
    assert report["readiness"]["passed"] is False
    assert report["readiness"]["findings"] == [
        "brain: coverage manifest is invalid or stale"
    ]


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


def test_production_readiness_rejects_missing_latest_brain_audit(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    report = _production_base_report()
    report["brain"] = {"coverage": {"status": "complete"}}

    production = production_readiness_report(report, settings)

    assert production["passed"] is False
    assert "brain: latest brain audit is missing" in production["findings"]


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
        "NSLAB_PRICE_PROVIDER": "stock-web",
        "NSLAB_STOCK_WEB_PATH": "<path-to-stock-web-checkout-or-cache>",
        "NSLAB_REAL_BUNDLE_PATH": "<path-to-real-v11-ACCEPT_FULL-bundle>",
    }
    assert production["remediation_commands"] == [
        "python -m news_scalping_lab.cli research smoke-bundle --path %NSLAB_REAL_BUNDLE_PATH% --require-valid",
        "python -m news_scalping_lab.cli research import-bundle %NSLAB_REAL_BUNDLE_PATH% --validate --accept",
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


def test_doctor_report_flags_unreadable_accepted_store(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    export_json_schemas(tmp_path / "schemas")
    episode = ResearchEpisode(
        episode_id="EP-doctor-accepted-unreadable",
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
        research_version="doctor-test-v1",
        price_source_snapshot={"source": "doctor-test"},
        blind_analysis=BlindAnalysis(
            summary="Doctor accepted store unreadable test.",
            open_world_mechanisms=["accepted episode -> diagnostics"],
        ),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)
    BrainCompiler(tmp_path).rebuild(mode="full")
    accepted_path = tmp_path / "research" / "accepted" / f"{episode.episode_id}.json"
    accepted_path.write_text("{not valid json", encoding="utf-8")

    coverage = audit_coverage(tmp_path)
    report = build_doctor_report(settings)

    assert coverage["accepted_episode_store_findings"] == [
        "accepted episode store is unreadable"
    ]
    assert "brain: accepted episode store is unreadable" in coverage["findings"]
    assert report["brain"]["accepted_episode_count"] == 0
    assert report["brain"]["accepted_episode_store_findings"] == [
        "accepted episode store is unreadable"
    ]
    assert report["readiness"]["passed"] is False
    assert "brain: accepted episode store is unreadable" in report["readiness"][
        "findings"
    ]


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
            "counts": {"issuer_day_cases.parquet": 2},
            "required_files": ["issuer_day_cases.parquet"],
            "missing_files": [],
            "unreadable_files": [],
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
            "expected_source_counts": {
                "issuer_day_cases.parquet": {
                    "expected": 1,
                    "source_label": "issuer-day brain records",
                }
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
    assert production["warehouse"]["counts"] == {"issuer_day_cases.parquet": 2}
    assert production["warehouse"]["expected_source_counts"] == {
        "issuer_day_cases.parquet": {
            "expected": 1,
            "source_label": "issuer-day brain records",
        }
    }
    assert production["warehouse"]["required_files"] == ["issuer_day_cases.parquet"]
    assert production["warehouse"]["missing_files"] == []
    assert production["warehouse"]["unreadable_files"] == []
    assert production["warehouse"]["report_counts"] == {
        "issuer_day_cases.parquet": 2
    }
    assert production["warehouse"]["report_expected_source_counts"] == {
        "issuer_day_cases.parquet": {
            "expected": 1,
            "source_label": "issuer-day brain records",
        }
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


def test_production_readiness_audits_current_warehouse_when_report_missing(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    _write_record_coverage_store(tmp_path)

    production = production_readiness_report(_production_base_report(), settings)

    assert production["warehouse"]["passed"] is False
    assert production["warehouse"]["report_present"] is False
    assert production["warehouse"]["required_files_present"] is False
    assert "brain_records.parquet" in production["warehouse"]["required_files"]
    assert "brain_records.parquet" in production["warehouse"]["missing_files"]
    assert production["warehouse"]["counts"] == {}
    assert production["warehouse"]["expected_source_counts"][
        "brain_records.parquet"
    ] == {
        "expected": 2,
        "source_label": "normalized brain records",
    }
    assert (
        "warehouse: required warehouse projections are missing or unreadable"
        in production["findings"]
    )
    assert (
        "warehouse: record-level projections are not synced"
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
                "llm_compile_category_schema_mismatches": [],
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


def test_production_readiness_rejects_latest_brain_category_schema_mismatches(
    tmp_path,
) -> None:
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
                "deep": True,
                "brain_category_source_record_types": {},
                "llm_compile_category_schema_mismatches": [
                    "categories: expected 9, got 8"
                ],
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
        "brain: latest brain audit llm compile category schema mismatches"
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


def test_production_readiness_rejects_mock_price_provider(tmp_path) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
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

    assert production["price_data"] == {
        "schema_version": "nslab.production_price_data.v1",
        "passed": False,
        "status": "attention",
        "finding_count": 1,
        "findings": ["mock provider cannot supply production D-1 price evidence"],
        "provider": "mock",
        "stock_web_effective_path": None,
        "stock_web_effective_path_exists": None,
        "stock_web_schema_status": {},
    }
    assert (
        "price: mock provider cannot supply production D-1 price evidence"
        in production["findings"]
    )


def test_production_readiness_accepts_stock_web_price_provider(tmp_path) -> None:
    stock_web_path = tmp_path / "stock-web"
    atlas = stock_web_path / "atlas"
    shard_root = atlas / "ohlcv_tradable_by_symbol_year"
    shard_root.mkdir(parents=True)
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
        {
            "tradable_shard_columns": {
                "d": "date",
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
            }
        },
    )
    settings = Settings(
        project_root=tmp_path,
        llm_provider="openai",
        web_provider="brave",
        price_provider="stock-web",
        stock_web_path=stock_web_path,
    )
    settings.llm.provider = "openai"
    report = build_doctor_report(settings)
    report["api_connections"]["openai"]["status"] = "configured_not_called"
    report["api_connections"]["brave_search"]["status"] = "configured_not_called"
    report["vector_index"] = {
        "status": "current",
        "embedding_method": "llm_embedding:openai:text-embedding-3-small",
    }

    production = production_readiness_report(report, settings)

    assert production["price_data"]["passed"] is True
    assert production["price_data"]["provider"] == "stock-web"
    assert production["price_data"]["stock_web_effective_path"] == (
        stock_web_path.as_posix()
    )
    assert not any(finding.startswith("price:") for finding in production["findings"])


def test_production_readiness_rejects_mock_price_snapshot_evidence(
    tmp_path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        llm_provider="openai",
        web_provider="brave",
        price_provider="stock-web",
    )
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-price.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-price",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {"blind_analysis": "price-hash"},
            "price_snapshot": {
                "source_name": "mock-price",
                "source_ref": "mock://prices/news-only",
                "allowed_through": "2030-01-09",
                "as_of": "2030-01-10T08:30:00+09:00",
            },
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "stock_web": {
            "effective_path": (tmp_path / "stock-web").as_posix(),
            "effective_path_exists": True,
            "schema": {"source_name": "stock-web-test"},
            "schema_status": {"status": "ok"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["price_evidence"]["passed"] is False
    assert production["price_evidence"]["mock_price_snapshot_count"] == 1
    assert production["price_evidence"]["mock_source_ref_count"] == 1
    assert production["price_evidence"]["mock_price_snapshots"] == [
        {
            "path": "runs/manifests/RUN-price.json",
            "run_id": "RUN-price",
            "source_name": "mock-price",
        }
    ]
    assert (
        "price_evidence: mock price_snapshot present in "
        "runs/manifests/RUN-price.json: source_name=mock-price"
        in production["findings"]
    )


def test_production_readiness_accepts_stock_web_price_snapshot_evidence(
    tmp_path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        llm_provider="openai",
        web_provider="brave",
        price_provider="stock-web",
    )
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-price.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-price",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {"blind_analysis": "price-hash"},
            "price_snapshot": {
                "source_name": "stock-web",
                "source_ref": "stock-web://atlas",
                "allowed_through": "2030-01-09",
                "as_of": "2030-01-10T08:30:00+09:00",
            },
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "stock_web": {
            "effective_path": (tmp_path / "stock-web").as_posix(),
            "effective_path_exists": True,
            "schema": {"source_name": "stock-web-test"},
            "schema_status": {"status": "ok"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["price_evidence"]["passed"] is True
    assert production["price_evidence"]["checked_manifest_count"] == 1
    assert production["price_evidence"]["mock_price_snapshot_count"] == 0
    assert production["price_evidence"]["missing_source_ref_count"] == 0
    assert production["price_evidence"]["mock_source_ref_count"] == 0
    assert production["price_evidence"]["unsafe_allowed_through_count"] == 0
    assert production["price_evidence"]["as_of_after_cutoff_count"] == 0
    assert not any(
        finding.startswith("price_evidence:") for finding in production["findings"]
    )


def test_production_readiness_rejects_final_context_price_after_allowed_through(
    tmp_path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        llm_provider="openai",
        web_provider="brave",
        price_provider="stock-web",
    )
    settings.llm.provider = "openai"
    context_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "final_synthesis_context"
        / "RUN-price"
        / "final_synthesis_context.json"
    )
    write_json(
        context_path,
        {
            "schema_version": "nslab.final_synthesis_context.v1",
            "payload": {
                "d_minus_one_market_data": {
                    "status": "D_MINUS_ONE_PRICE_SNAPSHOTS",
                    "source_name": "stock-web",
                    "source_ref": "stock-web://atlas",
                    "allowed_through": "2030-01-08",
                    "snapshots": [
                        {
                            "ticker": "005930",
                            "trade_date": "2030-01-09",
                            "close": 100.0,
                        }
                    ],
                }
            },
        },
    )
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-price.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-price",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {"blind_analysis": "price-hash"},
            "final_synthesis_context_artifact": context_path.relative_to(
                tmp_path
            ).as_posix(),
            "price_snapshot": {
                "source_name": "stock-web",
                "source_ref": "stock-web://atlas",
                "allowed_through": "2030-01-08",
                "as_of": "2030-01-10T08:30:00+09:00",
            },
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "stock_web": {
            "effective_path": (tmp_path / "stock-web").as_posix(),
            "effective_path_exists": True,
            "schema": {"source_name": "stock-web-test"},
            "schema_status": {"status": "ok"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["price_evidence"]["passed"] is False
    assert production["price_evidence"]["checked_final_context_reference_count"] == 1
    assert production["price_evidence"]["unsafe_final_context_price_row_count"] == 1
    assert production["price_evidence"]["unsafe_final_context_price_rows"] == [
        {
            "manifest": "runs/manifests/RUN-price.json",
            "artifact": (
                "runs/checkpoints/final_synthesis_context/RUN-price/"
                "final_synthesis_context.json"
            ),
            "row_index": 0,
            "ticker": "005930",
            "trade_date": "2030-01-09",
            "allowed_through": "2030-01-08",
            "manifest_trade_date": "2030-01-10",
            "reasons": ["after_allowed_through"],
        }
    ]
    assert (
        "price_evidence: final synthesis price snapshot row violates D-1 "
        "cutoff in runs/checkpoints/final_synthesis_context/RUN-price/"
        "final_synthesis_context.json: row=0 trade_date=2030-01-09 "
        "reasons=after_allowed_through"
        in production["findings"]
    )


def test_production_readiness_rejects_final_context_price_source_ref_mismatch(
    tmp_path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        llm_provider="openai",
        web_provider="brave",
        price_provider="stock-web",
    )
    settings.llm.provider = "openai"
    context_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "final_synthesis_context"
        / "RUN-price"
        / "final_synthesis_context.json"
    )
    write_json(
        context_path,
        {
            "schema_version": "nslab.final_synthesis_context.v1",
            "payload": {
                "d_minus_one_market_data": {
                    "status": "D_MINUS_ONE_PRICE_SNAPSHOTS",
                    "source_name": "stock-web",
                    "source_ref": "stock-web://wrong-source",
                    "allowed_through": "2030-01-09",
                    "snapshots": [],
                }
            },
        },
    )
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-price.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-price",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {"blind_analysis": "price-hash"},
            "final_synthesis_context_artifact": context_path.relative_to(
                tmp_path
            ).as_posix(),
            "price_snapshot": {
                "source_name": "stock-web",
                "source_ref": "stock-web://atlas",
                "allowed_through": "2030-01-09",
                "as_of": "2030-01-10T08:30:00+09:00",
            },
        },
    )
    report = {
        "api_connections": {
            "openai": {"status": "configured_not_called"},
            "brave_search": {"status": "configured_not_called"},
        },
        "stock_web": {
            "effective_path": (tmp_path / "stock-web").as_posix(),
            "effective_path_exists": True,
            "schema": {"source_name": "stock-web-test"},
            "schema_status": {"status": "ok"},
        },
        "vector_index": {
            "status": "current",
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
        },
    }

    production = production_readiness_report(report, settings)

    assert production["price_evidence"]["passed"] is False
    assert production["price_evidence"]["final_context_source_ref_mismatch_count"] == 1
    assert production["price_evidence"]["final_context_source_ref_mismatches"] == [
        {
            "manifest": "runs/manifests/RUN-price.json",
            "artifact": (
                "runs/checkpoints/final_synthesis_context/RUN-price/"
                "final_synthesis_context.json"
            ),
            "manifest_source_ref": "stock-web://atlas",
            "context_source_ref": "stock-web://wrong-source",
        }
    ]
    assert (
        "price_evidence: final synthesis price source_ref does not match manifest "
        "in runs/checkpoints/final_synthesis_context/RUN-price/"
        "final_synthesis_context.json: stock-web://wrong-source != stock-web://atlas"
        in production["findings"]
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


def test_production_readiness_rejects_semantic_index_without_disk_manifest(
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
            "embedding_method": "llm_embedding:openai:text-embedding-3-small",
            "brain_records_exists": True,
            "source_brain_record_count": 2,
            "brain_record_count": 2,
        },
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["passed"] is False
    assert production["semantic_index"]["manifest"]["exists"] is False
    assert (
        "embedding: semantic index manifest is missing on disk"
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["checked"] is True
    assert production["semantic_index"]["manifest"]["passed"] is True
    assert production["semantic_index"]["manifest"]["embedding_model"] == (
        "text-embedding-3-small"
    )
    assert production["semantic_index"]["manifest"]["accepted_hashes_match"] is True
    assert not any(
        finding.startswith("embedding:") for finding in production["findings"]
    )


def test_production_readiness_rejects_stale_semantic_index_accepted_hashes(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    accepted_dir = tmp_path / "research" / "accepted"
    accepted_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        accepted_dir / "EP-semantic-index.json",
        {
            "episode_id": "EP-semantic-index",
            "trade_date": "2030-01-01",
            "fixture": "accepted hash mismatch",
        },
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["passed"] is False
    assert (
        production["semantic_index"]["manifest"]["accepted_episode_count"] == 0
    )
    assert (
        production["semantic_index"]["manifest"]["expected_accepted_hash_count"] == 1
    )
    assert (
        production["semantic_index"]["manifest"]["accepted_hashes_match"] is False
    )
    assert (
        "embedding: semantic index accepted episode count does not match accepted episodes"
        in production["findings"]
    )
    assert (
        "embedding: semantic index accepted_hashes do not match accepted episodes"
        in production["findings"]
    )


def test_production_readiness_rejects_invalid_semantic_index_accepted_hashes(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    manifest_path = tmp_path / "memory" / "vector_index" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["accepted_hashes"] = {"EP-invalid": 123}
    write_json(manifest_path, manifest)
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["passed"] is False
    assert (
        production["semantic_index"]["manifest"]["accepted_hash_invalid_count"] == 1
    )
    assert (
        "embedding: semantic index accepted_hashes has invalid hashes"
        in production["findings"]
    )


def test_production_readiness_rejects_absolute_semantic_index_records_file(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    manifest_path = tmp_path / "memory" / "vector_index" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["records_file"] = (
        tmp_path / "memory" / "vector_index" / "records.jsonl"
    ).resolve().as_posix()
    write_json(manifest_path, manifest)
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["passed"] is False
    assert (
        production["semantic_index"]["manifest"]["records_file_path_valid"] is False
    )
    assert production["semantic_index"]["manifest"]["records_file_is_absolute"] is True
    assert (
        "embedding: semantic index records_file must be vector-index relative"
        in production["findings"]
    )


def test_production_readiness_rejects_escaping_semantic_index_records_file(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    vector_index_dir = tmp_path / "memory" / "vector_index"
    records_payload = (vector_index_dir / "records.jsonl").read_text(
        encoding="utf-8"
    )
    (tmp_path / "memory" / "external_records.jsonl").write_text(
        records_payload,
        encoding="utf-8",
    )
    manifest_path = vector_index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["records_file"] = "../external_records.jsonl"
    manifest["records_sha256"] = sha256_text(records_payload)
    write_json(manifest_path, manifest)
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["passed"] is False
    assert (
        production["semantic_index"]["manifest"]["records_file_path_valid"] is False
    )
    assert (
        production["semantic_index"]["manifest"]["records_file_escapes_index"] is True
    )
    assert (
        "embedding: semantic index records_file escapes vector index directory"
        in production["findings"]
    )


def test_production_readiness_rejects_invalid_semantic_index_records_file_rows(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    vector_index_dir = tmp_path / "memory" / "vector_index"
    records_payload = "".join(
        [
            json.dumps(
                {
                    "episode_id": "EP-semantic-index",
                    "terms": ["brain-1"],
                    "embedding": [0.1, 0.2],
                },
                sort_keys=True,
            )
            + "\n",
            json.dumps(
                {
                    "episode_id": "EP-semantic-index",
                    "terms": ["brain-2"],
                    "embedding": [0.1],
                },
                sort_keys=True,
            )
            + "\n",
        ]
    )
    (vector_index_dir / "records.jsonl").write_text(
        records_payload,
        encoding="utf-8",
    )
    manifest_path = vector_index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["records_sha256"] = sha256_text(records_payload)
    write_json(manifest_path, manifest)
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["passed"] is False
    assert production["semantic_index"]["manifest"]["records_row_count"] == 2
    assert production["semantic_index"]["manifest"]["records_invalid_line_count"] == 1
    assert (
        "embedding: semantic index records file has invalid rows"
        in production["findings"]
    )


def test_production_readiness_rejects_semantic_index_records_file_count_mismatch(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    vector_index_dir = tmp_path / "memory" / "vector_index"
    records_payload = (vector_index_dir / "records.jsonl").read_text(
        encoding="utf-8"
    )
    records_payload += (
        json.dumps(
            {
                "episode_id": "EP-extra",
                "terms": ["extra"],
                "embedding": [0.1, 0.2],
            },
            sort_keys=True,
        )
        + "\n"
    )
    (vector_index_dir / "records.jsonl").write_text(
        records_payload,
        encoding="utf-8",
    )
    manifest_path = vector_index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["records_sha256"] = sha256_text(records_payload)
    write_json(manifest_path, manifest)
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["passed"] is False
    assert production["semantic_index"]["manifest"]["record_count"] == 2
    assert production["semantic_index"]["manifest"]["records_row_count"] == 3
    assert (
        "embedding: semantic index records row count does not match manifest"
        in production["findings"]
    )


def test_production_readiness_rejects_absolute_semantic_index_brain_records_file(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    manifest_path = tmp_path / "memory" / "vector_index" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["brain_records_file"] = (
        tmp_path / "memory" / "vector_index" / "brain_records.jsonl"
    ).resolve().as_posix()
    write_json(manifest_path, manifest)
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["passed"] is False
    assert (
        production["semantic_index"]["manifest"]["brain_records_file_path_valid"]
        is False
    )
    assert (
        production["semantic_index"]["manifest"]["brain_records_file_is_absolute"]
        is True
    )
    assert (
        "embedding: semantic index brain_records_file must be vector-index relative"
        in production["findings"]
    )


def test_production_readiness_rejects_escaping_semantic_index_brain_records_file(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    vector_index_dir = tmp_path / "memory" / "vector_index"
    brain_records_payload = (vector_index_dir / "brain_records.jsonl").read_text(
        encoding="utf-8"
    )
    (tmp_path / "memory" / "external_brain_records.jsonl").write_text(
        brain_records_payload,
        encoding="utf-8",
    )
    manifest_path = vector_index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["brain_records_file"] = "../external_brain_records.jsonl"
    manifest["brain_records_sha256"] = sha256_text(brain_records_payload)
    write_json(manifest_path, manifest)
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["manifest"]["passed"] is False
    assert (
        production["semantic_index"]["manifest"]["brain_records_file_path_valid"]
        is False
    )
    assert (
        production["semantic_index"]["manifest"]["brain_records_file_escapes_index"]
        is True
    )
    assert (
        "embedding: semantic index brain_records_file escapes vector index directory"
        in production["findings"]
    )


def test_production_readiness_rejects_semantic_index_record_store_id_gaps(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    vector_index_dir = tmp_path / "memory" / "vector_index"
    brain_records_payload = "".join(
        json.dumps(
            {
                "record_id": record_id,
                "record_type": "memory_claim",
                "terms": [record_id.lower()],
                "embedding": [0.1, 0.2],
            },
            sort_keys=True,
        )
        + "\n"
        for record_id in ["BRAIN-1", "BRAIN-missing"]
    )
    (vector_index_dir / "brain_records.jsonl").write_text(
        brain_records_payload,
        encoding="utf-8",
    )
    manifest_path = vector_index_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["brain_record_hashes"] = {
        "BRAIN-1": "hash-1",
        "BRAIN-missing": "hash-missing",
    }
    manifest["brain_records_sha256"] = sha256_text(brain_records_payload)
    write_json(manifest_path, manifest)
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["passed"] is False
    assert production["semantic_index"]["manifest"]["unknown_brain_record_ids"] == [
        "BRAIN-missing"
    ]
    assert production["semantic_index"]["manifest"]["missing_brain_record_ids"] == [
        "BRAIN-2"
    ]
    assert (
        "embedding: semantic index references unknown brain record IDs"
        in production["findings"]
    )
    assert (
        "embedding: semantic index does not cover record store IDs"
        in production["findings"]
    )


def test_production_readiness_rejects_semantic_index_record_store_hash_mismatch(
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:text-embedding-3-small",
    )
    manifest_path = tmp_path / "memory" / "vector_index" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    brain_record_hashes = manifest["brain_record_hashes"]
    assert isinstance(brain_record_hashes, dict)
    brain_record_hashes["BRAIN-1"] = "stale-hash"
    write_json(manifest_path, manifest)
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
    }

    production = production_readiness_report(report, settings)

    assert production["semantic_index"]["passed"] is False
    assert production["semantic_index"]["manifest"]["brain_record_hash_mismatches"] == [
        "BRAIN-1"
    ]
    assert (
        "embedding: semantic index brain record hashes do not match record store"
        in production["findings"]
    )


def test_production_readiness_accepts_complete_record_coverage_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    _write_record_coverage_store(tmp_path)
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
    assert (
        production["record_coverage"]["record_coverage_as_of"]
        == "2030-01-02T00:00:00+09:00"
    )
    assert production["record_coverage"]["record_coverage_accepted_episode_count"] == 0
    assert production["record_coverage"]["expected_accepted_episode_count"] == 0
    assert production["record_coverage"]["accepted_record_count"] == 2
    assert production["record_coverage"]["record_store_record_count"] == 2
    assert production["record_coverage"]["swept_record_count"] == 2
    assert production["record_coverage"]["swept_record_ids"] == [
        "BRAIN-1",
        "BRAIN-2",
    ]
    assert production["record_coverage"]["expected_swept_record_ids"] == [
        "BRAIN-1",
        "BRAIN-2",
    ]
    assert production["record_coverage"]["duplicate_swept_record_ids"] == []
    assert production["record_coverage"]["unexpected_swept_record_ids"] == []
    assert production["record_coverage"]["unswept_record_ids"] == []
    assert production["record_coverage"]["expected_unswept_record_ids"] == []
    assert production["record_coverage"]["unknown_unswept_record_ids"] == []
    assert production["record_coverage"]["missing_unswept_record_ids"] == []
    assert production["record_coverage"]["unexpected_unswept_record_ids"] == []
    assert production["record_coverage"]["findings"] == []


def test_production_readiness_reports_unreadable_record_coverage_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    (current / "record_coverage_manifest.json").write_text(
        "{not valid json",
        encoding="utf-8",
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["record_coverage"]["passed"] is False
    assert production["record_coverage"]["status"] == "unreadable"
    assert production["record_coverage"]["findings"] == [
        "record coverage manifest is unreadable"
    ]
    assert production["record_coverage"]["manifest_read_findings"] == [
        "record coverage manifest is unreadable"
    ]
    assert "records: record coverage manifest is unreadable" in production["findings"]
    assert "records: record coverage manifest is missing" not in production["findings"]


def test_production_readiness_rejects_record_coverage_store_mismatch(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    _write_record_coverage_store(tmp_path)
    coverage = _complete_record_coverage()
    coverage.update(
        {
            "accepted_record_count": 2,
            "available_record_count": 2,
            "compiled_record_count": 2,
            "swept_record_count": 3,
            "swept_record_ids": ["BRAIN-1", "BRAIN-missing", "BRAIN-missing"],
            "record_counts_by_type": {
                "counterexample": 2,
            },
            "record_counts_by_evidence_phase": {
                "AUDIT": 2,
            },
            "record_counts_by_training_target": {
                "audit_only": 2,
            },
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
    assert production["record_coverage"]["unknown_swept_record_ids"] == [
        "BRAIN-missing"
    ]
    assert production["record_coverage"]["unexpected_swept_record_ids"] == [
        "BRAIN-missing"
    ]
    assert production["record_coverage"]["expected_swept_record_ids"] == [
        "BRAIN-1",
        "BRAIN-2",
    ]
    assert production["record_coverage"]["duplicate_swept_record_ids"] == [
        "BRAIN-missing"
    ]
    assert production["record_coverage"]["missing_swept_record_ids"] == ["BRAIN-2"]
    assert (
        "records: record coverage manifest swept IDs reference unknown records"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest swept IDs do not cover record store"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest record_counts_by_type does not match record store"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest record_counts_by_evidence_phase does not match record store"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest record_counts_by_training_target does not match record store"
        in production["findings"]
    )


def test_production_readiness_rejects_invalid_record_coverage_as_of(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    _write_record_coverage_store(tmp_path)
    coverage = _complete_record_coverage()
    coverage["record_coverage_as_of"] = "not-a-datetime"
    write_json(current / "record_coverage_manifest.json", coverage)

    production = production_readiness_report(
        {
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
        },
        settings,
    )

    assert production["record_coverage"]["passed"] is False
    assert production["record_coverage"]["record_coverage_as_of"] == "not-a-datetime"
    assert (
        "records: record coverage manifest record_coverage_as_of is missing or invalid"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest is marked complete despite production findings"
        in production["findings"]
    )


def test_production_readiness_rejects_record_coverage_episode_count_mismatch(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    _write_record_coverage_store(tmp_path)
    coverage = _complete_record_coverage()
    coverage["accepted_episode_count"] = 1
    write_json(current / "record_coverage_manifest.json", coverage)

    production = production_readiness_report(
        {
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
        },
        settings,
    )

    assert production["record_coverage"]["passed"] is False
    assert production["record_coverage"]["record_coverage_accepted_episode_count"] == 1
    assert production["record_coverage"]["expected_accepted_episode_count"] == 0
    assert (
        "records: record coverage manifest accepted_episode_count does not match accepted episodes"
        in production["findings"]
    )


def test_production_readiness_rejects_stale_episode_coverage_brain_metadata(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    write_json(
        current / "brain_manifest.json",
        {
            "brain_version": "brain-current",
            "created_at": "2030-01-03T00:00:00+09:00",
            "build_mode": "llm-full",
            "catalog_only": False,
        },
    )
    write_json(
        current / "coverage_manifest.json",
        {
            "brain_version": "brain-stale",
            "created_at": "2030-01-02T00:00:00+09:00",
            "build_mode": "catalog",
            "catalog_only": True,
            "accepted_episode_count": 0,
            "covered_episode_count": 0,
            "covered_episode_ids": [],
            "missing_episode_ids": [],
            "coverage_complete": True,
        },
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["episode_coverage"]["passed"] is False
    assert (
        production["episode_coverage"]["episode_coverage_brain_version"]
        == "brain-stale"
    )
    assert production["episode_coverage"]["expected_brain_version"] == "brain-current"
    assert (
        production["episode_coverage"]["episode_coverage_created_at"]
        == "2030-01-02T00:00:00+09:00"
    )
    assert (
        production["episode_coverage"]["expected_created_at"]
        == "2030-01-03T00:00:00+09:00"
    )
    assert production["episode_coverage"]["episode_coverage_build_mode"] == "catalog"
    assert production["episode_coverage"]["expected_build_mode"] == "llm-full"
    assert production["episode_coverage"]["episode_coverage_catalog_only"] is True
    assert production["episode_coverage"]["expected_catalog_only"] is False
    assert (
        "brain: coverage manifest brain_version does not match current brain manifest"
        in production["findings"]
    )
    assert (
        "brain: coverage manifest created_at does not match current brain manifest"
        in production["findings"]
    )
    assert (
        "brain: coverage manifest build_mode does not match current brain manifest"
        in production["findings"]
    )
    assert (
        "brain: coverage manifest catalog_only does not match current brain manifest"
        in production["findings"]
    )
    assert (
        "brain: coverage manifest is marked complete despite production findings"
        in production["findings"]
    )


def test_production_readiness_reports_unreadable_episode_coverage_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    (current / "coverage_manifest.json").mkdir()

    production = production_readiness_report(_production_base_report(), settings)

    assert production["episode_coverage"]["passed"] is False
    assert production["episode_coverage"]["status"] == "invalid"
    assert production["episode_coverage"]["findings"] == [
        "coverage manifest is unreadable"
    ]
    assert "unreadable JSON:" in production["episode_coverage"]["error"]
    assert "brain: coverage manifest is unreadable" in production["findings"]


def test_production_readiness_reports_unreadable_brain_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    (current / "brain_manifest.json").write_text("{not valid json", encoding="utf-8")
    write_json(
        current / "coverage_manifest.json",
        {
            "brain_version": "brain-current",
            "created_at": "2030-01-03T00:00:00+09:00",
            "build_mode": "llm-full",
            "catalog_only": False,
            "accepted_episode_count": 0,
            "covered_episode_count": 0,
            "covered_episode_ids": [],
            "missing_episode_ids": [],
            "coverage_complete": True,
        },
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["brain_manifest_read_findings"] == [
        "brain manifest is unreadable"
    ]
    assert "brain: brain manifest is unreadable" in production["findings"]
    assert production["episode_coverage"]["expected_brain_version"] is None
    assert production["llm_full_brain"]["current_brain_version"] is None


def test_production_readiness_rejects_stale_record_coverage_brain_metadata(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    _write_record_coverage_store(tmp_path)
    write_json(
        current / "brain_manifest.json",
        {
            "brain_version": "brain-current",
            "created_at": "2030-01-03T00:00:00+09:00",
            "build_mode": "llm-full",
            "catalog_only": False,
        },
    )
    coverage = _complete_record_coverage()
    coverage.update(
        {
            "brain_version": "brain-stale",
            "build_mode": "catalog",
            "catalog_only": True,
        }
    )
    write_json(current / "record_coverage_manifest.json", coverage)

    production = production_readiness_report(
        {
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
        },
        settings,
    )

    assert production["record_coverage"]["passed"] is False
    assert (
        production["record_coverage"]["record_coverage_as_of"]
        == "2030-01-02T00:00:00+09:00"
    )
    assert (
        production["record_coverage"]["expected_record_coverage_as_of"]
        == "2030-01-03T00:00:00+09:00"
    )
    assert production["record_coverage"]["record_coverage_brain_version"] == "brain-stale"
    assert production["record_coverage"]["expected_brain_version"] == "brain-current"
    assert production["record_coverage"]["record_coverage_build_mode"] == "catalog"
    assert production["record_coverage"]["expected_build_mode"] == "llm-full"
    assert production["record_coverage"]["record_coverage_catalog_only"] is True
    assert production["record_coverage"]["expected_catalog_only"] is False
    assert (
        "records: record coverage manifest record_coverage_as_of does not match current brain manifest"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest brain_version does not match current brain manifest"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest build_mode does not match current brain manifest"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest catalog_only does not match current brain manifest"
        in production["findings"]
    )


def test_production_readiness_rejects_record_coverage_unswept_id_mismatch(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    _write_record_coverage_store(tmp_path)
    coverage = _complete_record_coverage()
    coverage.update(
        {
            "swept_record_count": 1,
            "swept_record_ids": ["BRAIN-1"],
            "unswept_record_ids": ["BRAIN-1", "BRAIN-missing"],
        }
    )
    write_json(current / "record_coverage_manifest.json", coverage)

    production = production_readiness_report(
        {
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
        },
        settings,
    )

    assert production["record_coverage"]["passed"] is False
    assert production["record_coverage"]["expected_unswept_record_ids"] == ["BRAIN-2"]
    assert production["record_coverage"]["unknown_unswept_record_ids"] == [
        "BRAIN-missing"
    ]
    assert production["record_coverage"]["missing_unswept_record_ids"] == ["BRAIN-2"]
    assert production["record_coverage"]["unexpected_unswept_record_ids"] == [
        "BRAIN-1",
        "BRAIN-missing",
    ]
    assert (
        "records: record coverage manifest unswept IDs reference unknown records"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest unswept IDs do not match record store"
        in production["findings"]
    )


def test_production_readiness_rejects_non_string_record_coverage_ids(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    current = tmp_path / "brain" / "current"
    current.mkdir(parents=True)
    _write_record_coverage_store(tmp_path)
    coverage = _complete_record_coverage()
    coverage.update(
        {
            "swept_record_count": 2,
            "swept_record_ids": ["BRAIN-1", 7],
            "unswept_record_ids": [False],
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
    assert (
        "records: record coverage manifest swept_record_ids is missing or invalid"
        in production["findings"]
    )
    assert (
        "records: record coverage manifest unswept_record_ids is missing or invalid"
        in production["findings"]
    )


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


def test_production_readiness_marks_training_exports_not_applicable_without_records(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is True
    assert production["training_exports"]["status"] == "not_applicable"
    assert production["training_exports"]["source_record_count"] == 0
    assert production["training_exports"]["expected_weight_validation_statuses"] == {}
    assert production["training_exports"]["weight_validation_status_mismatches"] == {}
    assert production["training_exports"]["expected_skipped_record_reason_fields"] == {}
    assert production["training_exports"]["skipped_record_reason_mismatches"] == []
    assert production["training_exports"]["expected_source_record_hashes"] == {}
    assert production["training_exports"]["source_record_hash_manifest_mismatch_ids"] == []
    assert production["training_exports"]["expected_count_maps"] == {}
    assert production["training_exports"]["count_map_mismatches"] == []
    assert production["training_exports"]["weight_diagnostic_count_mismatches"] == []


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
    training_report = json.loads(
        (tmp_path / "diagnostics" / "training_export_report.json").read_text(
            encoding="utf-8"
        )
    )

    assert production["training_exports"]["passed"] is True
    assert production["training_exports"]["status"] == "ready"
    assert production["training_exports"]["diagnostic_report_passed"] is True
    assert production["training_exports"]["diagnostic_report_findings"] == []
    assert production["training_exports"]["invalid_diagnostic_report_fields"] == []
    assert production["training_exports"]["source_record_count"] == 2
    assert production["training_exports"]["record_store_source_record_count"] == 2
    assert production["training_exports"]["diagnostic_source_record_count"] == (
        training_report["source_record_count"]
    )
    assert production["training_exports"]["diagnostic_eligible_record_count"] == (
        training_report["eligible_record_count"]
    )
    assert production["training_exports"]["diagnostic_exported_record_count"] == (
        training_report["exported_record_count"]
    )
    assert production["training_exports"]["diagnostic_row_count"] == (
        training_report["row_count"]
    )
    assert production["training_exports"]["diagnostic_skipped_record_count"] == (
        training_report["skipped_record_count"]
    )
    assert production["training_exports"]["expected_aggregate_counts"] == {
        "source_record_count": training_report["source_record_count"],
        "eligible_record_count": training_report["eligible_record_count"],
        "exported_record_count": training_report["exported_record_count"],
        "row_count": training_report["row_count"],
        "skipped_record_count": training_report["skipped_record_count"],
    }
    assert production["training_exports"]["missing_aggregate_count_fields"] == []
    assert production["training_exports"]["invalid_aggregate_count_fields"] == []
    assert production["training_exports"]["export_kinds"] == [
        "evals",
        "preference",
        "sft",
    ]
    assert production["training_exports"]["missing_export_kinds"] == []
    assert production["training_exports"]["unexpected_export_kinds"] == []
    assert production["training_exports"]["available_manifest_kinds"] == [
        "evals",
        "preference",
        "sft",
    ]
    assert production["training_exports"]["missing_manifest_kinds"] == []
    assert production["training_exports"]["invalid_manifest_kind_fields"] == []
    assert production["training_exports"]["missing_available_manifest_kinds"] == []
    assert production["training_exports"]["unexpected_available_manifest_kinds"] == []
    assert production["training_exports"]["unexpected_missing_manifest_kinds"] == []
    assert production["training_exports"]["unique_source_record_count"] == 2
    assert production["training_exports"]["unique_training_eligible_record_count"] == 2
    assert production["training_exports"]["unique_exported_record_count"] == 2
    assert production["training_exports"]["unique_skipped_record_count"] == 0
    assert production["training_exports"]["record_store_source_record_ids"] == [
        "BRAIN-TRAIN-ISSUER",
        "BRAIN-TRAIN-PAIR",
    ]
    assert production["training_exports"][
        "record_store_training_eligible_record_ids"
    ] == [
        "BRAIN-TRAIN-ISSUER",
        "BRAIN-TRAIN-PAIR",
    ]
    assert production["training_exports"]["unique_source_record_ids"] == [
        "BRAIN-TRAIN-ISSUER",
        "BRAIN-TRAIN-PAIR",
    ]
    assert production["training_exports"]["unique_training_eligible_record_ids"] == [
        "BRAIN-TRAIN-ISSUER",
        "BRAIN-TRAIN-PAIR",
    ]
    assert production["training_exports"]["unique_exported_record_ids"] == [
        "BRAIN-TRAIN-ISSUER",
        "BRAIN-TRAIN-PAIR",
    ]
    assert production["training_exports"]["unique_skipped_record_ids"] == []
    assert (
        production["training_exports"]["missing_current_training_eligible_record_ids"]
        == []
    )
    assert (
        production["training_exports"][
            "unsealed_training_eligible_preference_record_ids"
        ]
        == []
    )
    assert (
        production["training_exports"][
            "expected_unsealed_training_eligible_preference_record_ids"
        ]
        == []
    )
    assert (
        production["training_exports"][
            "invalid_unsealed_preference_record_id_fields"
        ]
        == []
    )
    assert production["training_exports"]["expected_unique_record_ids"] == {
        "unique_exported_record_ids": [
            "BRAIN-TRAIN-ISSUER",
            "BRAIN-TRAIN-PAIR",
        ],
        "unique_skipped_record_ids": [],
        "unique_source_record_ids": [
            "BRAIN-TRAIN-ISSUER",
            "BRAIN-TRAIN-PAIR",
        ],
        "unique_training_eligible_record_ids": [
            "BRAIN-TRAIN-ISSUER",
            "BRAIN-TRAIN-PAIR",
        ],
    }
    assert production["training_exports"]["unique_record_id_mismatches"] == []
    assert training_report["skipped_record_reason_counts"] == {
        "record_type_not_selected_for_export_kind": (
            training_report["per_export_skipped_record_count"]
        )
    }
    assert training_report["skipped_record_reasons_by_record_id"] == {
        "BRAIN-TRAIN-ISSUER": ["record_type_not_selected_for_export_kind"],
        "BRAIN-TRAIN-PAIR": ["record_type_not_selected_for_export_kind"],
    }
    assert training_report["unique_skipped_record_reasons_by_record_id"] == {}
    assert production["training_exports"]["skipped_record_reason_counts"] == (
        training_report["skipped_record_reason_counts"]
    )
    assert production["training_exports"]["skipped_record_reasons_by_record_id"] == (
        training_report["skipped_record_reasons_by_record_id"]
    )
    assert production["training_exports"][
        "unique_skipped_record_reasons_by_record_id"
    ] == {}
    assert production["training_exports"]["expected_skipped_record_reason_fields"] == {
        "skipped_record_reason_counts": training_report["skipped_record_reason_counts"],
        "skipped_record_reasons_by_record_id": (
            training_report["skipped_record_reasons_by_record_id"]
        ),
        "unique_skipped_record_reasons_by_record_id": {},
    }
    assert production["training_exports"]["skipped_record_reason_mismatches"] == []
    assert production["training_exports"]["per_export_eligible_record_count"] == (
        training_report["per_export_eligible_record_count"]
    )
    assert production["training_exports"]["per_export_exported_record_count"] == (
        training_report["per_export_exported_record_count"]
    )
    assert production["training_exports"]["per_export_skipped_record_count"] == (
        training_report["per_export_skipped_record_count"]
    )
    assert production["training_exports"]["expected_per_export_counts"] == {
        "per_export_eligible_record_count": (
            training_report["per_export_eligible_record_count"]
        ),
        "per_export_exported_record_count": (
            training_report["per_export_exported_record_count"]
        ),
        "per_export_skipped_record_count": (
            training_report["per_export_skipped_record_count"]
        ),
    }
    assert production["training_exports"]["missing_per_export_count_fields"] == []
    assert production["training_exports"]["invalid_per_export_count_fields"] == []
    assert production["training_exports"]["source_record_hash_count"] == 2
    assert set(production["training_exports"]["source_record_hashes"]) == {
        "BRAIN-TRAIN-ISSUER",
        "BRAIN-TRAIN-PAIR",
    }
    assert production["training_exports"]["source_record_hashes"] == production[
        "training_exports"
    ]["record_store_source_record_hashes"]
    assert production["training_exports"]["expected_source_record_hashes"] == (
        production["training_exports"]["source_record_hashes"]
    )
    assert production["training_exports"]["source_record_hash_manifest_mismatch_ids"] == []
    assert production["training_exports"]["blind_safe_row_count"] == training_report[
        "blind_safe_row_count"
    ]
    assert production["training_exports"]["hindsight_row_count"] == training_report[
        "hindsight_row_count"
    ]
    assert production["training_exports"]["missing_phase_row_count_fields"] == []
    assert production["training_exports"]["invalid_phase_row_count_fields"] == []
    assert production["training_exports"]["source_phase_counts"] == training_report[
        "source_phase_counts"
    ]
    assert production["training_exports"]["source_phase_row_count"] == (
        training_report["blind_safe_row_count"] + training_report["hindsight_row_count"]
    )
    assert production["training_exports"]["invalid_source_phase_labels"] == []
    assert production["training_exports"]["counts_by_record_type"] == {
        "blind_leader_preference_pair": 1,
        "supervised_issuer_day_case": 1,
    }
    assert production["training_exports"]["counts_by_training_target"] == {
        "issuer_day_price_response": 1,
        "outcome_preferred_candidate": 1,
    }
    assert production["training_exports"]["expected_count_maps"] == {
        "counts_by_record_type": {
            "blind_leader_preference_pair": 1,
            "supervised_issuer_day_case": 1,
        },
        "counts_by_training_target": {
            "issuer_day_price_response": 1,
            "outcome_preferred_candidate": 1,
        },
        "source_phase_counts": training_report["source_phase_counts"],
    }
    assert production["training_exports"]["count_map_mismatches"] == []
    assert production["training_exports"]["weight_validation_statuses"] == {
        "evals": "passed",
        "preference": "passed",
        "sft": "passed",
    }
    assert production["training_exports"]["expected_weight_validation_statuses"] == {
        "evals": "passed",
        "preference": "passed",
        "sft": "passed",
    }
    assert production["training_exports"]["missing_weight_validation_kinds"] == []
    assert production["training_exports"]["unexpected_weight_validation_kinds"] == []
    assert production["training_exports"]["invalid_weight_validation_entries"] == []
    assert production["training_exports"]["weight_validation_status_mismatches"] == {}
    assert production["training_exports"]["missing_weight_diagnostic_fields"] == []
    assert production["training_exports"]["invalid_weight_diagnostic_fields"] == []
    assert production["training_exports"]["weight_diagnostic_count_mismatches"] == []
    assert not any(
        finding.startswith("training:") for finding in production["findings"]
    )


def test_production_readiness_rejects_failed_training_export_diagnostics_report(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["passed"] = False
    tampered_report["findings"] = ["diagnostic self-reported failure"]

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["diagnostic_report_passed"] is False
    assert production["training_exports"]["diagnostic_report_findings"] == [
        "diagnostic self-reported failure",
    ]
    assert production["training_exports"]["invalid_diagnostic_report_fields"] == []
    assert (
        "training export diagnostics report did not pass"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics report has findings: diagnostic self-reported failure"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics report did not pass"
        in production["findings"]
    )


def test_production_readiness_rejects_failed_training_export_audit_status(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    existing_report = json.loads(report_path.read_text(encoding="utf-8"))
    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", existing_report)
        return {"passed": False, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["audit_passed"] is False
    assert production["training_exports"]["diagnostic_report_passed"] is True
    assert (
        "training export audit did not pass"
        in production["training_exports"]["findings"]
    )
    assert "training: training export audit did not pass" in production["findings"]


def test_production_readiness_rejects_invalid_training_export_manifest_kinds(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["export_kinds"] = ["sft", "debug", ""]
    tampered_report["available_manifest_kinds"] = ["sft", "debug", 7]
    tampered_report["missing_manifest_kinds"] = ["ghost"]

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["export_kinds"] == [
        "sft",
        "debug",
        "",
    ]
    assert production["training_exports"]["missing_export_kinds"] == [
        "evals",
        "preference",
    ]
    assert production["training_exports"]["unexpected_export_kinds"] == [
        "",
        "debug",
    ]
    assert production["training_exports"]["available_manifest_kinds"] == [
        "sft",
        "debug",
    ]
    assert production["training_exports"]["missing_manifest_kinds"] == ["ghost"]
    assert production["training_exports"]["invalid_manifest_kind_fields"] == [
        "export_kinds",
        "available_manifest_kinds",
    ]
    assert production["training_exports"]["missing_available_manifest_kinds"] == [
        "evals",
        "preference",
    ]
    assert production["training_exports"]["unexpected_available_manifest_kinds"] == [
        "debug",
    ]
    assert production["training_exports"]["unexpected_missing_manifest_kinds"] == [
        "ghost",
    ]
    assert (
        "training export diagnostics export_kinds is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export export_kinds are missing required kinds: evals, preference"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export export_kinds include unexpected kinds: , debug"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics available_manifest_kinds is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export available manifests are missing required kinds: evals, preference"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export available manifests include unexpected kinds: debug"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export missing manifests include unexpected kinds: ghost"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export available manifests are missing required kinds: "
        "evals, preference"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_unique_record_id_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["unique_source_record_ids"] = [
        "BRAIN-TRAIN-ISSUER",
        "BRAIN-TRAIN-BOGUS",
    ]
    tampered_report["unique_training_eligible_record_ids"] = [
        "BRAIN-TRAIN-ISSUER",
        "BRAIN-TRAIN-BOGUS",
    ]

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert (
        "training export unique source record IDs do not match current records"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export unique training-eligible record IDs include IDs not in current records"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export unique source record IDs do not match current records"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_missing_current_eligible_ids(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["unique_training_eligible_record_count"] = 1
    tampered_report["unique_training_eligible_record_ids"] = ["BRAIN-TRAIN-ISSUER"]
    tampered_report["unique_exported_record_count"] = 1
    tampered_report["unique_exported_record_ids"] = ["BRAIN-TRAIN-ISSUER"]
    tampered_report["unique_skipped_record_count"] = 1
    tampered_report["unique_skipped_record_ids"] = ["BRAIN-TRAIN-PAIR"]
    tampered_report["unique_skipped_record_reasons_by_record_id"] = {
        "BRAIN-TRAIN-PAIR": ["tampered_missing_eligible_id"],
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"][
        "missing_current_training_eligible_record_ids"
    ] == ["BRAIN-TRAIN-PAIR"]
    assert (
        "training export unique training-eligible record IDs are missing "
        "current eligible records: BRAIN-TRAIN-PAIR"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export unique training-eligible record IDs are missing "
        "current eligible records: BRAIN-TRAIN-PAIR"
        in production["findings"]
    )


def test_production_readiness_rejects_unsealed_training_eligible_preference_records(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path, include_unsealed_preference_pair=True)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"][
        "unsealed_training_eligible_preference_record_ids"
    ] == ["BRAIN-TRAIN-UNSEALED-PAIR"]
    assert production["training_exports"][
        "expected_unsealed_training_eligible_preference_record_ids"
    ] == ["BRAIN-TRAIN-UNSEALED-PAIR"]
    assert (
        "training export has unsealed training-eligible preference records: "
        "BRAIN-TRAIN-UNSEALED-PAIR"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export has unsealed training-eligible preference "
        "records: BRAIN-TRAIN-UNSEALED-PAIR"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_unique_ids_manifest_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["unique_exported_record_count"] = 1
    tampered_report["unique_skipped_record_count"] = 1
    tampered_report["unique_exported_record_ids"] = ["BRAIN-TRAIN-ISSUER"]
    tampered_report["unique_skipped_record_ids"] = ["BRAIN-TRAIN-PAIR"]
    tampered_report["unique_skipped_record_reasons_by_record_id"] = {
        "BRAIN-TRAIN-PAIR": ["record_type_not_selected_for_export_kind"],
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["unique_record_id_mismatches"] == [
        "unique_exported_record_ids",
        "unique_skipped_record_ids",
    ]
    assert (
        "training export diagnostics unique_exported_record_ids does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics unique_skipped_record_ids does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics unique_exported_record_ids does not match manifests"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_aggregate_count_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["source_record_count"] = 1
    tampered_report["eligible_record_count"] = True
    tampered_report["exported_record_count"] = 0
    tampered_report["row_count"] = 0
    tampered_report.pop("skipped_record_count")

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    expected_counts = {
        "source_record_count": max(
            manifest["source_record_count"] for manifest in manifests.values()
        ),
        "eligible_record_count": sum(
            manifest["eligible_record_count"] for manifest in manifests.values()
        ),
        "exported_record_count": sum(
            manifest["exported_record_count"] for manifest in manifests.values()
        ),
        "row_count": sum(manifest["row_count"] for manifest in manifests.values()),
        "skipped_record_count": sum(
            manifest["skipped_record_count"] for manifest in manifests.values()
        ),
    }
    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["expected_aggregate_counts"] == expected_counts
    assert production["training_exports"]["missing_aggregate_count_fields"] == [
        "skipped_record_count",
    ]
    assert production["training_exports"]["invalid_aggregate_count_fields"] == [
        "eligible_record_count",
    ]
    assert (
        "training export diagnostics skipped_record_count is missing"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics eligible_record_count is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics source_record_count does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics exported_record_count does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics source_record_count does not match manifests"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_per_export_count_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["per_export_eligible_record_count"] = True
    tampered_report["per_export_exported_record_count"] = 0
    tampered_report.pop("per_export_skipped_record_count")

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    expected_counts = {
        "per_export_eligible_record_count": sum(
            manifest["eligible_record_count"] for manifest in manifests.values()
        ),
        "per_export_exported_record_count": sum(
            manifest["exported_record_count"] for manifest in manifests.values()
        ),
        "per_export_skipped_record_count": sum(
            manifest["skipped_record_count"] for manifest in manifests.values()
        ),
    }
    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["expected_per_export_counts"] == expected_counts
    assert production["training_exports"]["missing_per_export_count_fields"] == [
        "per_export_skipped_record_count",
    ]
    assert production["training_exports"]["invalid_per_export_count_fields"] == [
        "per_export_eligible_record_count",
    ]
    assert (
        "training export diagnostics per_export_skipped_record_count is missing"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics per_export_eligible_record_count is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics per_export_exported_record_count does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics per_export_exported_record_count "
        "does not match manifests"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_source_hash_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["source_record_hash_count"] = 2
    tampered_report["source_record_hashes"] = {
        "BRAIN-TRAIN-BOGUS": "1" * 64,
        "BRAIN-TRAIN-ISSUER": "0" * 64,
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["missing_hash_fields"] == []
    assert production["training_exports"]["invalid_hash_fields"] == []
    assert production["training_exports"]["source_record_hashes"] == {
        "BRAIN-TRAIN-BOGUS": "1" * 64,
        "BRAIN-TRAIN-ISSUER": "0" * 64,
    }
    assert set(production["training_exports"]["record_store_source_record_hashes"]) == {
        "BRAIN-TRAIN-ISSUER",
        "BRAIN-TRAIN-PAIR",
    }
    assert (
        "training export source_record_hashes IDs do not match current records"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export source_record_hashes mismatch current records: "
        "BRAIN-TRAIN-ISSUER"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export source_record_hashes IDs do not match current records"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_source_hash_manifest_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    manifests["sft"]["source_record_hashes"]["BRAIN-TRAIN-ISSUER"] = "0" * 64
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    existing_report = json.loads(report_path.read_text(encoding="utf-8"))

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", existing_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["source_record_hash_manifest_mismatch_ids"] == [
        "BRAIN-TRAIN-ISSUER",
    ]
    assert (
        "training export source_record_hashes do not match manifests: "
        "BRAIN-TRAIN-ISSUER"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export source_record_hashes do not match manifests: "
        "BRAIN-TRAIN-ISSUER"
        in production["findings"]
    )


def test_production_readiness_rejects_non_string_training_export_record_ids(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["unique_exported_record_ids"] = ["BRAIN-TRAIN-ISSUER", 7]
    tampered_report["unique_skipped_record_ids"] = [False]

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["invalid_record_id_fields"] == [
        "unique_exported_record_ids",
        "unique_skipped_record_ids",
    ]
    assert (
        "training export diagnostics unique_exported_record_ids is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics unique_skipped_record_ids is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics unique_exported_record_ids is invalid"
        in production["findings"]
    )


def test_production_readiness_rejects_invalid_training_export_skip_reasons(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["skipped_record_reasons_by_record_id"] = {
        "BRAIN-TRAIN-ISSUER": ["record_type_not_selected_for_export_kind", 7],
    }
    tampered_report["unique_skipped_record_reasons_by_record_id"] = {
        "BRAIN-TRAIN-PAIR": [],
    }
    tampered_report["skipped_record_reason_counts"] = {
        "record_type_not_selected_for_export_kind": True,
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["invalid_reason_fields"] == [
        "skipped_record_reasons_by_record_id",
        "unique_skipped_record_reasons_by_record_id",
        "skipped_record_reason_counts",
    ]
    assert (
        "training export diagnostics skipped_record_reasons_by_record_id is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics unique_skipped_record_reasons_by_record_id is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics skipped_record_reason_counts is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics skipped_record_reasons_by_record_id is invalid"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_skip_reason_manifest_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["skipped_record_reasons_by_record_id"] = {
        "BRAIN-TRAIN-ISSUER": ["record_type_not_selected_for_export_kind"],
    }
    tampered_report["skipped_record_reason_counts"] = {
        "record_type_not_selected_for_export_kind": 1,
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["skipped_record_reason_mismatches"] == [
        "skipped_record_reasons_by_record_id",
        "skipped_record_reason_counts",
    ]
    assert (
        "training export diagnostics skipped_record_reasons_by_record_id does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics skipped_record_reason_counts does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics skipped_record_reason_counts does not match manifests"
        in production["findings"]
    )


def test_production_readiness_rejects_invalid_training_export_count_maps(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["source_phase_counts"] = {"BLIND": -1}
    tampered_report["counts_by_record_type"] = {
        "supervised_issuer_day_case": 2,
    }
    tampered_report["counts_by_training_target"] = {
        "issuer_day_price_response": True,
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["record_store_counts_by_record_type"] == {
        "blind_leader_preference_pair": 1,
        "supervised_issuer_day_case": 1,
    }
    assert production["training_exports"]["record_store_counts_by_training_target"] == {
        "issuer_day_price_response": 1,
        "outcome_preferred_candidate": 1,
    }
    assert production["training_exports"]["invalid_count_fields"] == [
        "source_phase_counts",
        "counts_by_training_target",
    ]
    assert (
        "training export diagnostics source_phase_counts is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics counts_by_training_target is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export counts_by_record_type does not match current records"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export counts_by_record_type does not match current records"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_count_map_manifest_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    manifests["sft"]["source_phase_counts"]["BLIND"] = (
        manifests["sft"]["source_phase_counts"].get("BLIND", 0) + 1
    )
    manifests["sft"]["counts_by_record_type"]["supervised_issuer_day_case"] = 2
    manifests["sft"]["counts_by_training_target"]["issuer_day_price_response"] = 2
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    existing_report = json.loads(report_path.read_text(encoding="utf-8"))

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", existing_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["count_map_mismatches"] == [
        "source_phase_counts",
        "counts_by_record_type",
        "counts_by_training_target",
    ]
    assert (
        "training export diagnostics source_phase_counts does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics counts_by_record_type does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics counts_by_training_target does not match manifests"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics counts_by_training_target does not match manifests"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_phase_count_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["blind_safe_row_count"] = 1
    tampered_report["hindsight_row_count"] = 1
    tampered_report["source_phase_counts"] = {
        "AUDIT_ONLY": 1,
        "BLIND": 2,
        "POSTMORTEM": tampered_report["hindsight_row_count"],
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["invalid_source_phase_labels"] == [
        "AUDIT_ONLY",
    ]
    assert production["training_exports"]["source_phase_row_count"] == (
        tampered_report["blind_safe_row_count"]
        + tampered_report["hindsight_row_count"]
        + 2
    )
    assert (
        "training export source_phase_counts include invalid phases: AUDIT_ONLY"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export source phase row count does not match blind/hindsight row counts"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export BLIND phase count does not match blind-safe row count"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export source_phase_counts include invalid phases: AUDIT_ONLY"
        in production["findings"]
    )


def test_production_readiness_rejects_missing_training_export_phase_row_counts(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report.pop("blind_safe_row_count")
    tampered_report["hindsight_row_count"] = True

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["missing_phase_row_count_fields"] == [
        "blind_safe_row_count",
    ]
    assert production["training_exports"]["invalid_phase_row_count_fields"] == [
        "hindsight_row_count",
    ]
    assert (
        "training export diagnostics blind_safe_row_count is missing"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics hindsight_row_count is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics blind_safe_row_count is missing"
        in production["findings"]
    )


def test_production_readiness_rejects_incomplete_training_export_weight_statuses(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["weight_validation_statuses"] = {
        "debug": "passed",
        "evals": True,
        "sft": "passed",
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": {}}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["weight_validation_statuses"] == {
        "debug": "passed",
        "sft": "passed",
    }
    assert production["training_exports"]["invalid_weight_validation_entries"] == [
        "evals",
    ]
    assert production["training_exports"]["missing_weight_validation_kinds"] == [
        "evals",
        "preference",
    ]
    assert production["training_exports"]["unexpected_weight_validation_kinds"] == [
        "debug",
    ]
    assert (
        "training export weight validation statuses contain invalid entries: evals"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export weight validation statuses are missing kinds: evals, preference"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export weight validation statuses include unexpected kinds: debug"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export weight validation statuses are missing kinds: "
        "evals, preference"
        in production["findings"]
    )


def test_production_readiness_rejects_training_export_weight_status_manifest_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    manifests["sft"]["weight_validation_status"] = "failed"
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["weight_validation_statuses"] = {
        "evals": "passed",
        "preference": "passed",
        "sft": "passed",
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["expected_weight_validation_statuses"] == {
        "evals": "passed",
        "preference": "passed",
        "sft": "failed",
    }
    assert production["training_exports"]["weight_validation_status_mismatches"] == {
        "sft": {"expected": "failed", "observed": "passed"},
    }
    assert (
        "training export weight validation statuses do not match manifests: sft"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export weight validation statuses do not match manifests: sft"
        in production["findings"]
    )


def test_production_readiness_rejects_invalid_training_export_weight_diagnostics(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["duplicate_issuer_day_count"] = True
    tampered_report["duplicate_issuer_day_keys"] = ["2030-01-10|TRAIN", 7]
    tampered_report["issuer_day_weight_sum_mismatches"] = {"ISSUER-1": True}
    tampered_report.pop("direct_event_weight_sum_mismatch_count")

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["missing_weight_diagnostic_fields"] == [
        "direct_event_weight_sum_mismatch_count",
    ]
    assert production["training_exports"]["invalid_weight_diagnostic_fields"] == [
        "duplicate_issuer_day_count",
        "duplicate_issuer_day_keys",
        "issuer_day_weight_sum_mismatches",
    ]
    assert (
        "training export diagnostics direct_event_weight_sum_mismatch_count is missing"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics duplicate_issuer_day_count is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics duplicate_issuer_day_keys is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export diagnostics issuer_day_weight_sum_mismatches is invalid"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export diagnostics duplicate_issuer_day_count is invalid"
        in production["findings"]
    )


def test_production_readiness_rejects_duplicate_issuer_day_count_key_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["duplicate_issuer_day_count"] = 2
    tampered_report["duplicate_issuer_day_keys"] = ["2030-01-10|TRAIN"]

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["duplicate_issuer_day_count"] == 2
    assert production["training_exports"]["duplicate_issuer_day_keys"] == [
        "2030-01-10|TRAIN",
    ]
    assert (
        "training export duplicate issuer-day count does not match keys"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export duplicate issuer-day count does not match keys"
        in production["findings"]
    )


def test_production_readiness_rejects_weight_mismatch_count_detail_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(tmp_path)
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)
    production_readiness_report(_production_base_report(), settings)

    manifests = {
        kind: json.loads(
            (tmp_path / "training_exports" / kind / "manifest.json").read_text(
                encoding="utf-8",
            )
        )
        for kind in ("sft", "preference", "evals")
    }
    report_path = tmp_path / "diagnostics" / "training_export_report.json"
    tampered_report = json.loads(report_path.read_text(encoding="utf-8"))
    tampered_report["issuer_day_weight_sum_mismatch_count"] = 0
    tampered_report["issuer_day_weight_sum_mismatches"] = {
        "2030-01-10|TRAIN": 0.8,
    }
    tampered_report["direct_event_weight_sum_mismatch_count"] = 2
    tampered_report["direct_event_weight_sum_mismatches"] = {
        "ISSUER-1": 0.8,
    }

    def fake_audit_training_exports(root: Path) -> dict[str, object]:
        write_json(root / "diagnostics" / "training_export_report.json", tampered_report)
        return {"passed": True, "findings": [], "manifests": manifests}

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.audit_training_exports",
        fake_audit_training_exports,
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["weight_diagnostic_count_mismatches"] == [
        "issuer_day_weight_sum_mismatch_count",
        "direct_event_weight_sum_mismatch_count",
    ]
    assert (
        "training export issuer-day weight mismatch count does not match details"
        in production["training_exports"]["findings"]
    )
    assert (
        "training export direct-event weight mismatch count does not match details"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export issuer-day weight mismatch count does not match details"
        in production["findings"]
    )


def test_production_readiness_rejects_direct_event_weight_mismatch(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(
        tmp_path,
        include_direct_event_weight_mismatch=True,
    )
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["status"] == "attention"
    assert production["training_exports"]["weight_validation_statuses"] == {
        "evals": "failed",
        "preference": "failed",
        "sft": "failed",
    }
    assert production["training_exports"]["direct_event_weight_sum_mismatch_count"] == 1
    assert production["training_exports"]["direct_event_weight_sum_mismatches"] == {
        "ISSUER-1": 0.8
    }
    assert (
        "training export has direct-event weight sum mismatches"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export has direct-event weight sum mismatches"
        in production["findings"]
    )


def test_production_readiness_reports_duplicate_issuer_day_keys(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    ensure_project_dirs(settings)
    _write_training_record_store(
        tmp_path,
        include_duplicate_issuer_day=True,
    )
    for kind in ("sft", "preference", "evals"):
        export_training(tmp_path, kind=kind)

    production = production_readiness_report(_production_base_report(), settings)

    assert production["training_exports"]["passed"] is False
    assert production["training_exports"]["duplicate_issuer_day_count"] == 1
    assert production["training_exports"]["duplicate_issuer_day_keys"] == [
        "2030-01-10|TRAIN"
    ]
    assert production["training_exports"]["issuer_day_weight_sum_mismatch_count"] == 0
    assert production["training_exports"]["issuer_day_weight_sum_mismatches"] == {}
    assert (
        "training export has duplicate issuer-day samples"
        in production["training_exports"]["findings"]
    )
    assert (
        "training: training export has duplicate issuer-day samples"
        in production["findings"]
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
    vector_index = _write_semantic_index_fixture(
        tmp_path,
        embedding_method="llm_embedding:openai:stale-embedding-model",
    )
    report = {
        "api_connections": {"openai": {"status": "configured_not_called"}},
        "vector_index": vector_index,
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


def test_production_readiness_rejects_missing_llm_context_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
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
    assert production["llm_evidence"]["checked_manifest_count"] == 0
    assert production["llm_evidence"]["finding_count"] == 1
    assert production["llm_evidence"]["findings"] == [
        "production LLM context manifest is missing"
    ]
    assert (
        "llm_evidence: production LLM context manifest is missing"
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


def test_production_readiness_rejects_llm_context_manifest_without_prompt_hashes(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-no-prompt-hashes.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-no-prompt-hashes",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {},
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
    assert production["llm_evidence"]["missing_prompt_hash_manifest_count"] == 1
    assert production["llm_evidence"]["missing_prompt_hash_manifests"] == [
        "runs/manifests/RUN-no-prompt-hashes.json"
    ]
    assert production["llm_evidence"]["referenced_prompt_hash_count"] == 0
    assert production["llm_evidence"]["checked_trace_count"] == 0
    assert (
        "llm_evidence: context manifest prompt_hashes missing or empty: "
        "runs/manifests/RUN-no-prompt-hashes.json"
        in production["findings"]
    )


def test_production_readiness_rejects_invalid_llm_context_prompt_hash_entries(
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
        manifest_dir / "RUN-invalid-prompt-hashes.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-invalid-prompt-hashes",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {
                "daily_blind_analysis": "live-trace-hash",
                "empty_prompt": "",
                "numeric_prompt": 123,
            },
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
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-live",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": {"prompt_sha256": "live-trace-hash"},
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
    assert production["llm_evidence"]["missing_prompt_hash_manifest_count"] == 0
    assert production["llm_evidence"]["invalid_prompt_hash_manifest_count"] == 1
    assert production["llm_evidence"]["invalid_prompt_hash_entry_count"] == 2
    assert production["llm_evidence"]["invalid_prompt_hash_manifests"] == [
        {
            "path": "runs/manifests/RUN-invalid-prompt-hashes.json",
            "run_id": "RUN-invalid-prompt-hashes",
            "invalid_fields": ["empty_prompt", "numeric_prompt"],
        }
    ]
    assert production["llm_evidence"]["referenced_prompt_hash_count"] == 1
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert (
        "llm_evidence: context manifest prompt_hashes contains invalid entries: "
        "runs/manifests/RUN-invalid-prompt-hashes.json (2)"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_trace_purpose_mismatch(
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
        manifest_dir / "RUN-purpose-mismatch.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-purpose-mismatch",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {"daily_blind_analysis": "live-trace-hash"},
        },
    )
    write_json(
        trace_dir / "TRACE-purpose-mismatch.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-purpose-mismatch",
            "operation": "generate_structured",
            "purpose": "postmortem_analysis",
            "provider": "OpenAIResponsesProvider",
            "checkpoint_id": "LLMCKPT-purpose-mismatch",
            "input": {"prompt_sha256": "live-trace-hash"},
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-purpose-mismatch.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-purpose-mismatch",
            "operation": "generate_structured",
            "purpose": "postmortem_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": {"prompt_sha256": "live-trace-hash"},
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
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["missing_trace_prompt_hash_count"] == 0
    assert production["llm_evidence"]["prompt_hash_purpose_mismatch_count"] == 1
    assert production["llm_evidence"]["prompt_hash_purpose_mismatches"] == [
        {
            "path": "runs/traces/TRACE-purpose-mismatch.json",
            "trace_id": "TRACE-purpose-mismatch",
            "prompt_sha256": "live-trace-hash",
            "expected_purposes": ["daily_blind_analysis"],
            "observed_purpose": "postmortem_analysis",
        }
    ]
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 0
    assert (
        "llm_evidence: referenced LLM trace purpose does not match "
        "manifest prompt_hashes: runs/traces/TRACE-purpose-mismatch.json"
        in production["findings"]
    )


def test_production_readiness_accepts_blind_analysis_prompt_alias(
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
    trace_input = {"prompt_sha256": "live-trace-hash"}
    trace_output = {"prediction_id": "PRED-live"}
    input_sha256 = sha256_text(canonical_json(trace_input))
    output_sha256 = sha256_text(canonical_json(trace_output))
    model_config = {
        "configured_provider": "openai",
        "provider_class": "OpenAIResponsesProvider",
        "model": "gpt-production",
    }
    write_json(
        manifest_dir / "RUN-live-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-live-llm",
            "model_config": model_config,
            "prompt_hashes": {"blind_analysis": "live-trace-hash"},
        },
    )
    write_json(
        trace_dir / "TRACE-live.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-live",
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "checkpoint_id": "LLMCKPT-live",
            "input": trace_input,
            "input_sha256": input_sha256,
            "output": trace_output,
            "output_sha256": output_sha256,
            "token_usage": {
                "prompt_tokens_estimate": 25,
                "completion_tokens_estimate": 10,
            },
            "model_config": model_config,
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-live",
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": trace_input,
            "input_sha256": input_sha256,
            "output": trace_output,
            "output_sha256": output_sha256,
            "token_usage": {
                "prompt_tokens_estimate": 25,
                "completion_tokens_estimate": 10,
            },
            "model_config": model_config,
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
    assert production["llm_evidence"]["prompt_hash_purpose_mismatch_count"] == 0
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 0


def test_production_readiness_rejects_duplicate_llm_context_prompt_hashes(
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
        manifest_dir / "RUN-duplicate-prompt-hashes.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-duplicate-prompt-hashes",
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
            "prompt_hashes": {
                "daily_blind_analysis": "shared-trace-hash",
                "postmortem_analysis": "shared-trace-hash",
            },
        },
    )
    write_json(
        trace_dir / "TRACE-shared.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-shared",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "checkpoint_id": "LLMCKPT-shared",
            "input": {"prompt_sha256": "shared-trace-hash"},
            "model_config": {
                "configured_provider": "openai",
                "provider_class": "OpenAIResponsesProvider",
                "model": "gpt-production",
            },
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-shared.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-shared",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": {"prompt_sha256": "shared-trace-hash"},
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
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert production["llm_evidence"]["duplicate_prompt_hash_manifest_count"] == 1
    assert production["llm_evidence"]["duplicate_prompt_hash_count"] == 1
    assert production["llm_evidence"]["duplicate_prompt_hash_manifests"] == [
        {
            "path": "runs/manifests/RUN-duplicate-prompt-hashes.json",
            "run_id": "RUN-duplicate-prompt-hashes",
            "duplicate_hashes": {
                "shared-trace-hash": [
                    "daily_blind_analysis",
                    "postmortem_analysis",
                ],
            },
        }
    ]
    assert (
        "llm_evidence: context manifest prompt_hashes contains duplicate hashes: "
        "runs/manifests/RUN-duplicate-prompt-hashes.json (1)"
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
    live_trace_input = {"prompt_sha256": "live-trace-hash"}
    live_trace_output = {"prediction_id": "PRED-live"}
    live_token_usage = {
        "prompt_tokens_estimate": 25,
        "completion_tokens_estimate": 10,
    }
    write_json(
        trace_dir / "TRACE-live.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-live",
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "prompt_version": "daily_blind_analysis.v1",
            "checkpoint_id": "LLMCKPT-live",
            "input": live_trace_input,
            "input_sha256": sha256_text(canonical_json(live_trace_input)),
            "output": live_trace_output,
            "output_sha256": sha256_text(canonical_json(live_trace_output)),
            "token_usage": live_token_usage,
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
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": live_trace_input,
            "input_sha256": sha256_text(canonical_json(live_trace_input)),
            "output": live_trace_output,
            "output_sha256": sha256_text(canonical_json(live_trace_output)),
            "token_usage": live_token_usage,
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
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "DeterministicMockLLMProvider",
            "input": {"prompt_sha256": "stale-mock-trace-hash"},
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
    assert production["llm_evidence"]["invalid_manifest_schema_count"] == 0
    assert production["llm_evidence"]["mock_model_config_manifest_count"] == 0
    assert production["llm_evidence"]["invalid_prompt_hash_manifest_count"] == 0
    assert production["llm_evidence"]["invalid_prompt_hash_entry_count"] == 0
    assert production["llm_evidence"]["invalid_prompt_hash_manifests"] == []
    assert production["llm_evidence"]["duplicate_prompt_hash_manifest_count"] == 0
    assert production["llm_evidence"]["duplicate_prompt_hash_count"] == 0
    assert production["llm_evidence"]["duplicate_prompt_hash_manifests"] == []
    assert production["llm_evidence"]["referenced_prompt_hash_count"] == 1
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["invalid_trace_schema_count"] == 0
    assert production["llm_evidence"]["invalid_trace_payload_count"] == 0
    assert production["llm_evidence"]["invalid_trace_payloads"] == []
    assert production["llm_evidence"]["missing_trace_prompt_hash_count"] == 0
    assert production["llm_evidence"]["prompt_hash_purpose_mismatch_count"] == 0
    assert production["llm_evidence"]["prompt_hash_purpose_mismatches"] == []
    assert production["llm_evidence"]["missing_trace_checkpoint_id_count"] == 0
    assert production["llm_evidence"]["mock_trace_count"] == 0
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert production["llm_evidence"]["invalid_checkpoint_schema_count"] == 0
    assert production["llm_evidence"]["checkpoint_id_mismatch_count"] == 0
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 0
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
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "DeterministicMockLLMProvider",
            "input": {"prompt_sha256": "mock-trace-hash"},
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
    assert production["llm_evidence"]["missing_trace_checkpoint_id_count"] == 0
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
    assert production["llm_evidence"]["checkpoint_id_mismatch_count"] == 0
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 0
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


def test_production_readiness_rejects_invalid_llm_evidence_schema_versions(
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
            "schema_version": "nslab.context_manifest.v0",
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
            "schema_version": "nslab.llm_trace.v0",
            "trace_id": "TRACE-live",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
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
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v0",
            "checkpoint_id": "LLMCKPT-live",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": {"prompt_sha256": "live-trace-hash"},
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
    assert production["llm_evidence"]["invalid_manifest_schema_count"] == 1
    assert production["llm_evidence"]["invalid_manifest_schemas"] == [
        {
            "path": "runs/manifests/RUN-live-llm.json",
            "run_id": "RUN-live-llm",
            "schema_version": "nslab.context_manifest.v0",
        }
    ]
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["invalid_trace_schema_count"] == 1
    assert production["llm_evidence"]["invalid_trace_schemas"] == [
        {
            "path": "runs/traces/TRACE-live.json",
            "trace_id": "TRACE-live",
            "purpose": "daily_blind_analysis",
            "prompt_sha256": "live-trace-hash",
            "schema_version": "nslab.llm_trace.v0",
        }
    ]
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert production["llm_evidence"]["invalid_checkpoint_schema_count"] == 1
    assert production["llm_evidence"]["invalid_checkpoint_schemas"] == [
        {
            "path": "runs/checkpoints/llm/LLMCKPT-live.json",
            "checkpoint_id": "LLMCKPT-live",
            "schema_version": "nslab.llm_checkpoint.v0",
        }
    ]
    assert (
        "llm_evidence: context manifest schema_version is invalid in "
        "runs/manifests/RUN-live-llm.json: nslab.context_manifest.v0"
        in production["findings"]
    )
    assert (
        "llm_evidence: referenced LLM trace schema_version is invalid in "
        "runs/traces/TRACE-live.json: nslab.llm_trace.v0"
        in production["findings"]
    )
    assert (
        "llm_evidence: referenced LLM checkpoint schema_version is invalid in "
        "runs/checkpoints/llm/LLMCKPT-live.json: nslab.llm_checkpoint.v0"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_trace_without_checkpoint_id(
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
            "input": {"prompt_sha256": "live-trace-hash"},
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
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["missing_trace_checkpoint_id_count"] == 1
    assert production["llm_evidence"]["missing_trace_checkpoint_id_traces"] == [
        {
            "path": "runs/traces/TRACE-live.json",
            "trace_id": "TRACE-live",
            "purpose": "daily_blind_analysis",
            "prompt_sha256": "live-trace-hash",
        }
    ]
    assert production["llm_evidence"]["checked_checkpoint_count"] == 0
    assert (
        "llm_evidence: referenced LLM trace missing checkpoint_id: "
        "runs/traces/TRACE-live.json"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_checkpoint_id_mismatch(
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
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-other",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": {"prompt_sha256": "live-trace-hash"},
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
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert production["llm_evidence"]["checkpoint_id_mismatch_count"] == 1
    assert production["llm_evidence"]["checkpoint_id_mismatches"] == [
        {
            "path": "runs/checkpoints/llm/LLMCKPT-live.json",
            "expected_checkpoint_id": "LLMCKPT-live",
            "observed_checkpoint_id": "LLMCKPT-other",
        }
    ]
    assert (
        "llm_evidence: referenced LLM checkpoint_id mismatch in "
        "runs/checkpoints/llm/LLMCKPT-live.json: expected LLMCKPT-live "
        "observed LLMCKPT-other"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_checkpoint_trace_payload_mismatch(
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
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-live",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "UnexpectedProvider",
            "input": {"prompt_sha256": "live-trace-hash"},
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
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert production["llm_evidence"]["checkpoint_id_mismatch_count"] == 0
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 1
    assert production["llm_evidence"]["checkpoint_trace_mismatches"] == [
        {
            "path": "runs/checkpoints/llm/LLMCKPT-live.json",
            "checkpoint_id": "LLMCKPT-live",
            "trace": "runs/traces/TRACE-live.json",
            "trace_id": "TRACE-live",
            "field": "provider",
            "trace_value": "OpenAIResponsesProvider",
            "checkpoint_value": "UnexpectedProvider",
        }
    ]
    assert (
        "llm_evidence: referenced LLM checkpoint trace mismatch in "
        "runs/checkpoints/llm/LLMCKPT-live.json: provider differs from "
        "runs/traces/TRACE-live.json"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_checkpoint_output_hash_mismatch(
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
    trace_input = {"prompt_sha256": "live-trace-hash"}
    trace_output = {"prediction_id": "PRED-live"}
    output_sha256 = sha256_text(canonical_json(trace_output))
    model_config = {
        "configured_provider": "openai",
        "provider_class": "OpenAIResponsesProvider",
        "model": "gpt-production",
    }
    write_json(
        manifest_dir / "RUN-live-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-live-llm",
            "model_config": model_config,
            "prompt_hashes": {"daily_blind_analysis": "live-trace-hash"},
        },
    )
    write_json(
        trace_dir / "TRACE-live.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-live",
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "checkpoint_id": "LLMCKPT-live",
            "input": trace_input,
            "input_sha256": sha256_text(canonical_json(trace_input)),
            "output": trace_output,
            "output_sha256": output_sha256,
            "token_usage": {
                "prompt_tokens_estimate": 25,
                "completion_tokens_estimate": 10,
            },
            "model_config": model_config,
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-live",
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": trace_input,
            "input_sha256": sha256_text(canonical_json(trace_input)),
            "output": trace_output,
            "output_sha256": "tampered-output-hash",
            "token_usage": {
                "prompt_tokens_estimate": 25,
                "completion_tokens_estimate": 10,
            },
            "model_config": model_config,
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
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["checked_checkpoint_count"] == 1
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 1
    assert production["llm_evidence"]["checkpoint_trace_mismatches"] == [
        {
            "path": "runs/checkpoints/llm/LLMCKPT-live.json",
            "checkpoint_id": "LLMCKPT-live",
            "trace": "runs/traces/TRACE-live.json",
            "trace_id": "TRACE-live",
            "field": "output_sha256",
            "trace_value": output_sha256,
            "checkpoint_value": "tampered-output-hash",
        }
    ]
    assert (
        "llm_evidence: referenced LLM checkpoint trace mismatch in "
        "runs/checkpoints/llm/LLMCKPT-live.json: output_sha256 differs from "
        "runs/traces/TRACE-live.json"
        in production["findings"]
    )


def test_production_readiness_accepts_checkpoint_hit_trace_runtime_fields(
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
    trace_input = {"prompt_sha256": "live-trace-hash"}
    trace_output = {"prediction_id": "PRED-live"}
    input_sha256 = sha256_text(canonical_json(trace_input))
    output_sha256 = sha256_text(canonical_json(trace_output))
    model_config = {
        "configured_provider": "openai",
        "provider_class": "OpenAIResponsesProvider",
        "model": "gpt-production",
    }
    write_json(
        manifest_dir / "RUN-live-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-live-llm",
            "model_config": model_config,
            "prompt_hashes": {"daily_blind_analysis": "live-trace-hash"},
        },
    )
    write_json(
        trace_dir / "TRACE-live.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-live",
            "operation": "generate_structured",
            "status": "checkpoint_hit",
            "purpose": "daily_blind_analysis",
            "prompt_version": "daily_blind_analysis.v1",
            "provider": "OpenAIResponsesProvider",
            "checkpoint_id": "LLMCKPT-live",
            "input": trace_input,
            "input_sha256": input_sha256,
            "output": trace_output,
            "output_sha256": output_sha256,
            "token_usage": {
                "prompt_tokens_estimate": 30,
                "completion_tokens_estimate": 12,
            },
            "retries": 0,
            "retry_errors": [],
            "model_config": model_config,
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-live",
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "metadata": {"prompt_version": "daily_blind_analysis.v1"},
            "input": trace_input,
            "input_sha256": input_sha256,
            "output": trace_output,
            "output_sha256": output_sha256,
            "model_config": model_config,
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
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 0
    assert production["llm_evidence"]["checkpoint_trace_mismatches"] == []


def test_production_readiness_rejects_llm_trace_output_hash_mismatch(
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
    trace_input = {"prompt_sha256": "live-trace-hash"}
    trace_output = {"prediction_id": "PRED-live"}
    model_config = {
        "configured_provider": "openai",
        "provider_class": "OpenAIResponsesProvider",
        "model": "gpt-production",
    }
    write_json(
        manifest_dir / "RUN-live-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-live-llm",
            "model_config": model_config,
            "prompt_hashes": {"daily_blind_analysis": "live-trace-hash"},
        },
    )
    trace_payload = {
        "schema_version": "nslab.llm_trace.v1",
        "trace_id": "TRACE-live",
        "operation": "generate_structured",
        "status": "ok",
        "purpose": "daily_blind_analysis",
        "provider": "OpenAIResponsesProvider",
        "checkpoint_id": "LLMCKPT-live",
        "input": trace_input,
        "input_sha256": sha256_text(canonical_json(trace_input)),
        "output": trace_output,
        "output_sha256": "tampered-output-hash",
        "token_usage": {
            "prompt_tokens_estimate": 25,
            "completion_tokens_estimate": 10,
        },
        "model_config": model_config,
    }
    write_json(trace_dir / "TRACE-live.json", trace_payload)
    write_json(
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-live",
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": trace_input,
            "input_sha256": trace_payload["input_sha256"],
            "output": trace_output,
            "output_sha256": "tampered-output-hash",
            "token_usage": trace_payload["token_usage"],
            "model_config": model_config,
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
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["invalid_trace_payload_count"] == 1
    assert production["llm_evidence"]["invalid_trace_payloads"] == [
        {
            "path": "runs/traces/TRACE-live.json",
            "trace_id": "TRACE-live",
            "purpose": "daily_blind_analysis",
            "prompt_sha256": "live-trace-hash",
            "mismatched_fields": ["output_sha256"],
        }
    ]
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 0
    assert (
        "llm_evidence: referenced LLM trace payload contract mismatch in "
        "runs/traces/TRACE-live.json: output_sha256"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_trace_missing_input_hash(
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
    trace_input = {"prompt_sha256": "live-trace-hash"}
    model_config = {
        "configured_provider": "openai",
        "provider_class": "OpenAIResponsesProvider",
        "model": "gpt-production",
    }
    write_json(
        manifest_dir / "RUN-live-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-live-llm",
            "model_config": model_config,
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
            "checkpoint_id": "LLMCKPT-live",
            "input": trace_input,
            "model_config": model_config,
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-live",
            "operation": "generate_structured",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": trace_input,
            "model_config": model_config,
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
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["invalid_trace_payload_count"] == 1
    assert production["llm_evidence"]["invalid_trace_payloads"] == [
        {
            "path": "runs/traces/TRACE-live.json",
            "trace_id": "TRACE-live",
            "purpose": "daily_blind_analysis",
            "prompt_sha256": "live-trace-hash",
            "mismatched_fields": ["input_sha256_missing"],
        }
    ]
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 0
    assert (
        "llm_evidence: referenced LLM trace payload contract mismatch in "
        "runs/traces/TRACE-live.json: input_sha256_missing"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_trace_missing_token_usage(
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
    trace_input = {"prompt_sha256": "live-trace-hash"}
    trace_output = {"prediction_id": "PRED-live"}
    input_sha256 = sha256_text(canonical_json(trace_input))
    output_sha256 = sha256_text(canonical_json(trace_output))
    model_config = {
        "configured_provider": "openai",
        "provider_class": "OpenAIResponsesProvider",
        "model": "gpt-production",
    }
    write_json(
        manifest_dir / "RUN-live-llm.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-live-llm",
            "model_config": model_config,
            "prompt_hashes": {"daily_blind_analysis": "live-trace-hash"},
        },
    )
    write_json(
        trace_dir / "TRACE-live.json",
        {
            "schema_version": "nslab.llm_trace.v1",
            "trace_id": "TRACE-live",
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "checkpoint_id": "LLMCKPT-live",
            "input": trace_input,
            "input_sha256": input_sha256,
            "output": trace_output,
            "output_sha256": output_sha256,
            "model_config": model_config,
        },
    )
    write_json(
        checkpoint_dir / "LLMCKPT-live.json",
        {
            "schema_version": "nslab.llm_checkpoint.v1",
            "checkpoint_id": "LLMCKPT-live",
            "operation": "generate_structured",
            "status": "ok",
            "purpose": "daily_blind_analysis",
            "provider": "OpenAIResponsesProvider",
            "input": trace_input,
            "input_sha256": input_sha256,
            "output": trace_output,
            "output_sha256": output_sha256,
            "model_config": model_config,
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
    assert production["llm_evidence"]["checked_trace_count"] == 1
    assert production["llm_evidence"]["invalid_trace_payload_count"] == 1
    assert production["llm_evidence"]["invalid_trace_payloads"] == [
        {
            "path": "runs/traces/TRACE-live.json",
            "trace_id": "TRACE-live",
            "purpose": "daily_blind_analysis",
            "prompt_sha256": "live-trace-hash",
            "mismatched_fields": ["token_usage_missing"],
        }
    ]
    assert production["llm_evidence"]["checkpoint_trace_mismatch_count"] == 0
    assert (
        "llm_evidence: referenced LLM trace payload contract mismatch in "
        "runs/traces/TRACE-live.json: token_usage_missing"
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


def test_production_readiness_rejects_missing_web_context_manifest(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
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
    assert production["web_evidence"]["checked_manifest_count"] == 0
    assert production["web_evidence"]["checked_artifact_reference_count"] == 0
    assert production["web_evidence"]["findings"] == [
        "production web context manifest is missing"
    ]
    assert (
        "web_evidence: production web context manifest is missing"
        in production["findings"]
    )


def test_production_readiness_rejects_missing_web_evidence_artifact_refs(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    write_json(
        manifest_dir / "RUN-no-web-artifacts.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-no-web-artifacts",
            "web_sources": ["WEB-live"],
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
    assert production["web_evidence"]["checked_artifact_reference_count"] == 0
    assert production["web_evidence"]["findings"] == [
        "production web evidence artifact reference is missing"
    ]
    assert (
        "web_evidence: production web evidence artifact reference is missing"
        in production["findings"]
    )


def test_production_readiness_rejects_empty_web_evidence_artifact(
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
    web_source_path.write_text("", encoding="utf-8")
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
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

    assert production["web_evidence"]["passed"] is False
    assert production["web_evidence"]["checked_artifact_count"] == 1
    assert production["web_evidence"]["checked_artifact_record_count"] == 0
    assert production["web_evidence"]["empty_artifact_count"] == 1
    assert production["web_evidence"]["empty_artifacts"] == [
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl"
    ]
    assert production["web_evidence"]["artifact_record_counts"] == {
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl": 0
    }
    assert (
        "web_evidence: web evidence artifact has no evidence rows: "
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl"
        in production["findings"]
    )


def test_production_readiness_rejects_web_evidence_source_id_mismatch(
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
                "schema_version": "nslab.web_source.v1",
                "source_id": "WEB-other",
                "url": "https://www.reuters.com/markets/companies/live-web-evidence",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
                "title": "live web evidence with wrong id",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
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

    assert production["web_evidence"]["passed"] is False
    assert production["web_evidence"]["artifact_source_id_mismatch_count"] == 1
    assert production["web_evidence"]["artifact_source_id_mismatches"] == [
        {
            "manifest": "runs/manifests/RUN-web.json",
            "artifact_field": "web_source_artifact",
            "source_field": "web_sources",
            "artifact": "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl",
            "expected_source_ids": ["WEB-live"],
            "observed_source_ids": ["WEB-other"],
        }
    ]
    assert (
        "web_evidence: web evidence artifact source IDs do not match manifest: "
        "runs/manifests/RUN-web.json web_source_artifact -> web_sources"
        in production["findings"]
    )


def test_production_readiness_uses_text_hash_for_web_evidence_artifacts(
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
    line = (
        json.dumps(
            {
                "schema_version": "nslab.web_source.v1",
                "source_id": "WEB-live",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
                "time_verified": True,
                "title": "live web evidence",
            },
            sort_keys=True,
        )
        + "\n"
    )
    web_source_path.write_bytes(line.replace("\n", "\r\n").encode("utf-8"))
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-web",
            "web_sources": ["WEB-live"],
            "web_source_artifact": web_source_path.relative_to(tmp_path).as_posix(),
            "web_source_sha256": sha256_text(line),
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
    assert production["web_evidence"]["artifact_sha256_mismatch_count"] == 0
    assert production["web_evidence"]["artifact_sha256_mismatches"] == []


def test_production_readiness_rejects_web_evidence_rows_without_source_id(
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
                "schema_version": "nslab.web_source.v1",
                "url": "https://www.reuters.com/markets/companies/live-web-evidence",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
                "title": "source id missing web evidence",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-web",
            "web_sources": [],
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
    assert production["web_evidence"]["artifact_source_id_mismatch_count"] == 0
    assert production["web_evidence"]["artifact_missing_source_id_count"] == 1
    assert production["web_evidence"]["artifact_missing_source_id_artifacts"] == [
        {
            "manifest": "runs/manifests/RUN-web.json",
            "artifact_field": "web_source_artifact",
            "source_field": "web_sources",
            "artifact": "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl",
            "row_count": 1,
            "missing_source_id_count": 1,
        }
    ]
    assert (
        "web_evidence: web evidence artifact has rows without source IDs: "
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl (1)"
        in production["findings"]
    )


def test_production_readiness_rejects_nested_only_web_evidence_source_id(
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
                "schema_version": "nslab.web_source.v1",
                "url": "https://www.reuters.com/markets/companies/live-web-evidence",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
                "metadata": {"source_id": "WEB-nested"},
                "title": "nested source id web evidence",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-web",
            "web_sources": ["WEB-nested"],
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
    assert production["web_evidence"]["artifact_source_id_mismatch_count"] == 1
    assert production["web_evidence"]["artifact_source_id_mismatches"] == [
        {
            "manifest": "runs/manifests/RUN-web.json",
            "artifact_field": "web_source_artifact",
            "source_field": "web_sources",
            "artifact": "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl",
            "expected_source_ids": ["WEB-nested"],
            "observed_source_ids": [],
        }
    ]
    assert production["web_evidence"]["artifact_missing_source_id_count"] == 1
    assert production["web_evidence"]["artifact_missing_source_id_artifacts"] == [
        {
            "manifest": "runs/manifests/RUN-web.json",
            "artifact_field": "web_source_artifact",
            "source_field": "web_sources",
            "artifact": "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl",
            "row_count": 1,
            "missing_source_id_count": 1,
        }
    ]
    assert (
        "web_evidence: web evidence artifact source IDs do not match manifest: "
        "runs/manifests/RUN-web.json web_source_artifact -> web_sources"
        in production["findings"]
    )
    assert (
        "web_evidence: web evidence artifact has rows without source IDs: "
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl (1)"
        in production["findings"]
    )


def test_production_readiness_rejects_invalid_web_evidence_json_artifact(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    context_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "final_synthesis_context"
        / "RUN-web"
        / "context.json"
    )
    manifest_dir.mkdir(parents=True)
    context_path.parent.mkdir(parents=True)
    context_path.write_text('{"broken": ', encoding="utf-8")
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-web",
            "final_synthesis_context_artifact": context_path.relative_to(
                tmp_path
            ).as_posix(),
            "final_synthesis_context_sha256": file_sha256(context_path),
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
    assert production["web_evidence"]["checked_artifact_count"] == 1
    assert production["web_evidence"]["checked_artifact_record_count"] == 1
    assert production["web_evidence"]["invalid_artifact_json_count"] == 1
    assert production["web_evidence"]["invalid_artifact_json_artifacts"] == [
        {
            "path": (
                "runs/checkpoints/final_synthesis_context/RUN-web/context.json"
            ),
            "invalid_json_count": 1,
        }
    ]
    assert (
        "web_evidence: web evidence artifact contains invalid JSON: "
        "runs/checkpoints/final_synthesis_context/RUN-web/context.json (1)"
        in production["findings"]
    )


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
            "schema_version": "nslab.context_manifest.v1",
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
    assert production["web_evidence"]["mock_web_metadata_count"] == 0
    assert production["web_evidence"]["mock_web_evidence_count"] == 2
    assert production["web_evidence"]["mock_web_artifacts"] == [
        {
            "path": "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl",
            "mock_url_count": 2,
            "mock_metadata_count": 0,
            "sample_values": ["mock://web/WEB-mock"],
        }
    ]
    assert (
        "web_evidence: mock web source URLs present in "
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl (2)"
        in production["findings"]
    )


def test_production_readiness_rejects_mock_web_provider_metadata(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path, llm_provider="openai", web_provider="brave")
    settings.llm.provider = "openai"
    manifest_dir = tmp_path / "runs" / "manifests"
    candidate_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_web_checks"
        / "RUN-web"
        / "candidate_web_check.jsonl"
    )
    manifest_dir.mkdir(parents=True)
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_text(
        json.dumps(
            {
                "source_id": "WEB-provider-mock",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
                "title": "provider metadata mock evidence",
                "provider": "mock",
                "source_provider": "DeterministicMockWebProvider",
                "source_type": "web",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-web",
            "candidate_web_source_ids": ["WEB-provider-mock"],
            "candidate_web_check_artifact": candidate_path.relative_to(
                tmp_path
            ).as_posix(),
            "candidate_web_check_sha256": file_sha256(candidate_path),
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
    assert production["web_evidence"]["mock_web_url_count"] == 0
    assert production["web_evidence"]["mock_web_metadata_count"] == 2
    assert production["web_evidence"]["mock_web_evidence_count"] == 2
    assert production["web_evidence"]["mock_web_artifacts"] == [
        {
            "path": (
                "runs/checkpoints/candidate_web_checks/RUN-web/"
                "candidate_web_check.jsonl"
            ),
            "mock_url_count": 0,
            "mock_metadata_count": 2,
            "sample_values": [
                "provider=mock",
                "source_provider=DeterministicMockWebProvider",
            ],
        }
    ]
    assert (
        "web_evidence: mock web provider metadata present in "
        "runs/checkpoints/candidate_web_checks/RUN-web/candidate_web_check.jsonl (2)"
        in production["findings"]
    )


def test_production_readiness_rejects_placeholder_web_evidence_urls(
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
                "schema_version": "nslab.web_source.v1",
                "source_id": "WEB-placeholder",
                "url": "https://example.test/news",
                "source_url": "https://example.test/news",
                "title": "placeholder web evidence",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-web",
            "web_sources": ["WEB-placeholder"],
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
    assert production["web_evidence"]["placeholder_web_artifact_count"] == 1
    assert production["web_evidence"]["placeholder_web_url_count"] == 2
    assert production["web_evidence"]["placeholder_web_artifacts"] == [
        {
            "path": "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl",
            "placeholder_url_count": 2,
            "sample_values": ["https://example.test/news"],
        }
    ]
    assert (
        "web_evidence: placeholder web source URLs present in "
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
    web_source_text = (
        json.dumps(
            {
                "available_before_cutoff": True,
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "published_at": "2030-01-10T08:30:00+09:00",
                "source_id": "WEB-live",
                "time_verified": True,
                "timestamp_precision": "datetime",
                "url": "https://www.reuters.com/markets/companies/live-web-evidence",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
                "title": "live web evidence",
            },
            sort_keys=True,
        )
        + "\n"
    )
    web_source_path.write_text(web_source_text, encoding="utf-8")
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-web",
            "web_sources": ["WEB-live"],
            "web_source_artifact": web_source_path.relative_to(tmp_path).as_posix(),
            "web_source_sha256": sha256_text(web_source_text),
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
    assert production["web_evidence"]["checked_artifact_record_count"] == 1
    assert production["web_evidence"]["empty_artifact_count"] == 0
    assert production["web_evidence"]["invalid_artifact_json_count"] == 0
    assert production["web_evidence"]["artifact_source_id_mismatch_count"] == 0
    assert production["web_evidence"]["artifact_missing_source_id_count"] == 0
    assert production["web_evidence"]["artifact_missing_source_id_artifacts"] == []
    assert production["web_evidence"]["artifact_cutoff_missing_count"] == 0
    assert production["web_evidence"]["artifact_cutoff_failed_count"] == 0
    assert production["web_evidence"]["artifact_cutoff_after_count"] == 0
    assert production["web_evidence"]["artifact_cutoff_invalid_timestamp_count"] == 0
    assert production["web_evidence"]["missing_artifact_hash_count"] == 0
    assert production["web_evidence"]["artifact_sha256_mismatch_count"] == 0
    assert production["web_evidence"]["mock_web_artifact_count"] == 0
    assert production["web_evidence"]["mock_web_url_count"] == 0
    assert production["web_evidence"]["mock_web_metadata_count"] == 0
    assert production["web_evidence"]["mock_web_evidence_count"] == 0
    assert production["web_evidence"]["placeholder_web_artifact_count"] == 0
    assert production["web_evidence"]["placeholder_web_url_count"] == 0
    assert production["web_evidence"]["placeholder_web_artifacts"] == []
    assert not any(
        finding.startswith("web_evidence:") for finding in production["findings"]
    )


def test_production_readiness_rejects_web_evidence_without_cutoff_verification(
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
                "url": "https://www.reuters.com/markets/companies/live-web-evidence",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
                "title": "live web evidence without cutoff verification",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
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

    assert production["web_evidence"]["passed"] is False
    assert production["web_evidence"]["artifact_cutoff_missing_count"] == 1
    assert production["web_evidence"]["artifact_cutoff_missing_artifacts"] == [
        {
            "manifest": "runs/manifests/RUN-web.json",
            "artifact_field": "web_source_artifact",
            "artifact": "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl",
            "checked_row_count": 1,
            "missing_verification_count": 1,
        }
    ]
    assert (
        "web_evidence: web evidence artifact has rows without cutoff verification: "
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl (1)"
        in production["findings"]
    )


def test_production_readiness_rejects_cutoff_failed_web_evidence(
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
                "available_before_cutoff": False,
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "published_at": "2030-01-10T09:30:00+09:00",
                "source_id": "WEB-future",
                "source_url": (
                    "https://www.reuters.com/markets/companies/future-web-evidence"
                ),
                "time_verified": False,
                "title": "future web evidence",
                "url": "https://www.reuters.com/markets/companies/future-web-evidence",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-web",
            "web_sources": ["WEB-future"],
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
    assert production["web_evidence"]["artifact_cutoff_failed_count"] == 1
    assert production["web_evidence"]["artifact_cutoff_after_count"] == 1
    assert (
        "web_evidence: web evidence artifact has cutoff verification failures: "
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl (1)"
        in production["findings"]
    )
    assert (
        "web_evidence: web evidence artifact has rows after cutoff: "
        "runs/checkpoints/web_sources/RUN-web/web_sources.jsonl (1)"
        in production["findings"]
    )


def test_production_readiness_rejects_web_context_manifest_schema_mismatch(
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
                "url": "https://www.reuters.com/markets/companies/live-web-evidence",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
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
            "schema_version": "nslab.context_manifest.v0",
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

    assert production["web_evidence"]["passed"] is False
    assert production["web_evidence"]["checked_manifest_count"] == 1
    assert production["web_evidence"]["invalid_manifest_schema_count"] == 1
    assert production["web_evidence"]["invalid_manifest_schemas"] == [
        {
            "path": "runs/manifests/RUN-web.json",
            "run_id": "RUN-web",
            "schema_version": "nslab.context_manifest.v0",
        }
    ]
    assert production["web_evidence"]["checked_artifact_reference_count"] == 1
    assert production["web_evidence"]["checked_artifact_count"] == 1
    assert (
        "web_evidence: context manifest schema_version is invalid in "
        "runs/manifests/RUN-web.json: nslab.context_manifest.v0"
        in production["findings"]
    )


def test_production_readiness_rejects_absolute_web_evidence_artifact_refs(
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
                "url": "https://www.reuters.com/markets/companies/live-web-evidence",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
                "title": "live web evidence",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    absolute_ref = str(web_source_path.resolve())
    write_json(
        manifest_dir / "RUN-web.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-web",
            "web_sources": ["WEB-live"],
            "web_source_artifact": absolute_ref,
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
    assert production["web_evidence"]["invalid_artifact_ref_count"] == 1
    assert production["web_evidence"]["invalid_artifact_refs"] == [
        {
            "manifest": "runs/manifests/RUN-web.json",
            "artifact_field": "web_source_artifact",
            "artifact": absolute_ref,
        }
    ]
    assert production["web_evidence"]["checked_artifact_count"] == 0
    assert (
        "web_evidence: web evidence artifact reference is invalid: "
        f"runs/manifests/RUN-web.json web_source_artifact={absolute_ref}"
        in production["findings"]
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
                "url": "https://www.reuters.com/markets/companies/live-web-evidence",
                "source_url": (
                    "https://www.reuters.com/markets/companies/live-web-evidence"
                ),
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
            "schema_version": "nslab.context_manifest.v1",
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
            "observed_sha256": sha256_text(
                web_source_path.read_text(encoding="utf-8")
            ),
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
    assert production["llm_full_brain"]["status"] == "attention"
    assert production["llm_full_brain"]["catalog_only"] is True
    assert production["llm_full_brain"]["catalog_mode_reason"] == "explicit_catalog_mode"
    assert production["llm_full_brain"]["deprecated_mode_alias"] is False
    assert production["llm_full_brain"]["production_eligible"] is False
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
        _llm_compile_manifest_v4_fixture(),
    )
    compile_run = _llm_compile_run_v4_fixture()
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": compile_run,
        },
    )
    _write_llm_compile_trace_evidence_fixture(tmp_path, compile_run)
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"]["catalog_mode_reason"] is None
    assert production["llm_full_brain"]["deprecated_mode_alias"] is False
    assert production["llm_full_brain"]["production_eligible"] is True
    assert production["llm_full_brain"]["compiler_version"] == LLM_FULL_COMPILER_VERSION
    assert production["llm_full_brain"]["expected_source_record_count"] == 1
    assert production["llm_full_brain"]["run_llm_live_call_count"] == 19
    assert not any(
        finding.startswith("brain: llm-full") or finding.startswith("brain: compiled claims")
        for finding in production["findings"]
    )


def test_production_readiness_rejects_llm_full_manifest_not_production_eligible(
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
            "brain_version": "brain-production",
            "build_mode": "llm-full",
            "catalog_only": False,
            "production_eligible": False,
        },
    )
    write_json(
        current / "record_coverage_manifest.json",
        {
            "schema_version": "nslab.record_coverage_manifest.v1",
            "accepted_record_count": 1,
            "coverage_complete": True,
        },
    )
    write_json(current / "llm_compile_manifest.json", _llm_compile_manifest_v4_fixture())
    compile_run = _llm_compile_run_v4_fixture()
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": compile_run,
        },
    )
    _write_llm_compile_trace_evidence_fixture(tmp_path, compile_run)
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"]["passed"] is False
    assert production["llm_full_brain"]["production_eligible"] is False
    assert "current manifest is not production_eligible" in production["llm_full_brain"][
        "findings"
    ]
    assert "brain: current manifest is not production_eligible" in production["findings"]


def test_production_readiness_accepts_llm_full_v4_trace_evidence(
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
    compile_manifest = _llm_compile_manifest_v4_fixture()
    compile_run = _llm_compile_run_v4_fixture()
    write_json(current / "llm_compile_manifest.json", compile_manifest)
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": compile_run,
        },
    )
    _write_llm_compile_trace_evidence_fixture(tmp_path, compile_run)
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"]["compiler_version"] == LLM_FULL_COMPILER_VERSION
    assert production["llm_full_brain"]["run_llm_prompt_hash_count"] == 19
    assert production["llm_full_brain"]["run_llm_trace_evidence"][
        "checked_trace_count"
    ] == 19
    assert production["llm_full_brain"]["run_llm_trace_evidence"][
        "checked_checkpoint_count"
    ] == 19
    assert not any(
        finding.startswith("brain: llm-full compile run")
        for finding in production["findings"]
    )


def test_production_readiness_rejects_llm_full_invalid_manifest_schema_version(
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
    compile_run = _llm_compile_run_v4_fixture()
    write_json(
        current / "llm_compile_manifest.json",
        _llm_compile_manifest_v4_fixture(
            schema_version="bad.llm_full_brain_compile_manifest.v1"
        ),
    )
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": compile_run,
        },
    )
    _write_llm_compile_trace_evidence_fixture(tmp_path, compile_run)
    _write_compiled_claim_fixture(tmp_path)
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
    assert (
        production["llm_full_brain"]["compile_manifest_schema_version"]
        == "bad.llm_full_brain_compile_manifest.v1"
    )
    assert (
        production["llm_full_brain"]["expected_compile_manifest_schema_version"]
        == "nslab.llm_full_brain_compile_manifest.v1"
    )
    assert (
        "brain: llm-full compile manifest schema_version is "
        "bad.llm_full_brain_compile_manifest.v1, not "
        "nslab.llm_full_brain_compile_manifest.v1"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_invalid_run_schema_version(
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
    compile_run = _llm_compile_run_v4_fixture(
        schema_version="bad.llm_full_brain_compile_run.v1"
    )
    write_json(current / "llm_compile_manifest.json", _llm_compile_manifest_v4_fixture())
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": compile_run,
        },
    )
    _write_llm_compile_trace_evidence_fixture(tmp_path, compile_run)
    _write_compiled_claim_fixture(tmp_path)
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
    assert (
        production["llm_full_brain"]["compile_run_schema_version"]
        == "bad.llm_full_brain_compile_run.v1"
    )
    assert (
        production["llm_full_brain"]["expected_compile_run_schema_version"]
        == "nslab.llm_full_brain_compile_run.v1"
    )
    assert (
        "brain: llm-full compile run schema_version is "
        "bad.llm_full_brain_compile_run.v1, not "
        "nslab.llm_full_brain_compile_run.v1"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_invalid_compile_report_schema_version(
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
    compile_run = _llm_compile_run_v4_fixture()
    write_json(current / "llm_compile_manifest.json", _llm_compile_manifest_v4_fixture())
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "bad.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": compile_run,
        },
    )
    _write_llm_compile_trace_evidence_fixture(tmp_path, compile_run)
    _write_compiled_claim_fixture(tmp_path)
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
    assert (
        production["llm_full_brain"]["compile_report_schema_version"]
        == "bad.brain_compile_diagnostics.v1"
    )
    assert (
        production["llm_full_brain"]["expected_compile_report_schema_version"]
        == "nslab.brain_compile_diagnostics.v1"
    )
    assert (
        "brain: llm-full compile report schema_version is "
        "bad.brain_compile_diagnostics.v1, not "
        "nslab.brain_compile_diagnostics.v1"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_missing_compiler_version(
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
        _llm_compile_manifest_fixture(),
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
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"]["compiler_version"] is None
    assert (
        "brain: llm-full compile compiler_version is missing, not "
        f"{LLM_FULL_COMPILER_VERSION}"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_v4_without_trace_evidence(
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
    compile_run = _llm_compile_run_v4_fixture()
    write_json(
        current / "llm_compile_manifest.json",
        _llm_compile_manifest_v4_fixture(),
    )
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    write_json(
        diagnostics_dir / "brain_compile_report.json",
        {
            "schema_version": "nslab.brain_compile_diagnostics.v1",
            "brain_version": "brain-production",
            "llm_compile_run": compile_run,
        },
    )
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"]["run_llm_prompt_hash_count"] == 19
    assert production["llm_full_brain"]["run_llm_trace_evidence"][
        "missing_trace_prompt_hash_count"
    ] == 19
    assert (
        "brain: llm-full compile run referenced LLM prompt hash has no "
        "matching trace: brain-compile-shard-0001-hash"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_v4_missing_prompt_hash_accounting(
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
        _llm_compile_manifest_v4_fixture(),
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
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"]["run_llm_prompt_hash_count"] == 0
    assert (
        "brain: llm-full compile run prompt hash accounting does not match "
        "generation count"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_category_manifest_mismatch(
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
    manifest = _llm_compile_manifest_fixture()
    categories = manifest["categories"]
    assert isinstance(categories, list)
    manifest["categories"] = categories[:-1]
    write_json(current / "llm_compile_manifest.json", manifest)
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
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"]["category_manifest_observed_count"] == 8
    assert "categories: expected 9, got 8" in production["llm_full_brain"][
        "category_manifest_schema_mismatches"
    ]
    assert (
        "brain: llm-full compile manifest categories do not match canonical brain files"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_record_shard_manifest_mismatch(
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
    manifest = _llm_compile_manifest_fixture()
    manifest["record_shards"] = [
        {
            "shard_index": 1,
            "record_count": 1,
            "record_ids": ["BRAIN-production", "BRAIN-production"],
            "cache_key": "",
        }
    ]
    write_json(current / "llm_compile_manifest.json", manifest)
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
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"]["record_shard_manifest_record_id_count"] == 2
    assert production["llm_full_brain"]["record_shard_manifest_unique_record_id_count"] == 1
    assert production["llm_full_brain"]["record_shard_manifest_schema_mismatches"] == [
        "record_shards[1]: missing cache_key"
    ]
    assert production["llm_full_brain"]["record_shard_manifest_count_mismatches"] == [
        "record_shards[1]"
    ]
    assert production["llm_full_brain"]["record_shard_manifest_duplicate_record_ids"] == [
        "BRAIN-production"
    ]
    assert (
        "brain: llm-full compile manifest record shards are invalid"
        in production["findings"]
    )
    assert (
        "brain: llm-full compile manifest record shard counts are inconsistent"
        in production["findings"]
    )
    assert (
        "brain: llm-full compile manifest record shards contain duplicate record IDs"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_category_source_unknown_record_ids(
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
    manifest = _llm_compile_manifest_fixture()
    categories = manifest["categories"]
    assert isinstance(categories, list)
    category = categories[0]
    assert isinstance(category, dict)
    category["source_record_ids"] = ["BRAIN-missing"]
    category["source_record_count"] = 1
    write_json(current / "llm_compile_manifest.json", manifest)
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
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"][
        "category_manifest_unknown_source_record_ids"
    ] == ["BRAIN-missing"]
    assert (
        "brain: llm-full compile manifest categories reference unknown source record IDs"
        in production["findings"]
    )


def test_production_readiness_rejects_llm_full_record_shard_unknown_or_missing_record_ids(
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
    manifest = _llm_compile_manifest_fixture()
    manifest["record_shards"] = [
        {
            "shard_index": 1,
            "record_count": 1,
            "record_ids": ["BRAIN-missing"],
            "cache_key": "LLMBRAIN-missing-shard",
        }
    ]
    write_json(current / "llm_compile_manifest.json", manifest)
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
    _write_compiled_claim_fixture(tmp_path)
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
    assert production["llm_full_brain"]["record_shard_manifest_unknown_record_ids"] == [
        "BRAIN-missing"
    ]
    assert production["llm_full_brain"]["record_shard_manifest_missing_record_ids"] == [
        "BRAIN-production"
    ]
    assert (
        "brain: llm-full compile manifest record shards reference unknown record IDs"
        in production["findings"]
    )
    assert (
        "brain: llm-full compile manifest record shards do not cover record store IDs"
        in production["findings"]
    )


def test_production_readiness_rejects_missing_llm_full_category_files(
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
        _llm_compile_manifest_fixture(),
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
    _write_compiled_claim_fixture(tmp_path, write_category_files=False)
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
    assert production["llm_full_brain"]["brain_category_existing_file_count"] == 0
    assert production["llm_full_brain"]["brain_category_missing_files"] == BRAIN_FILES
    assert (
        "brain: llm-full brain category files are missing"
        in production["findings"]
    )


def test_production_readiness_rejects_invalid_compiled_claim_rows(
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
        _llm_compile_manifest_fixture(),
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
    _write_brain_category_file_fixture(tmp_path)
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
    assert production["llm_full_brain"]["compiled_claim_jsonl_count"] == 1
    assert production["llm_full_brain"]["compiled_claim_valid_count"] == 0
    assert production["llm_full_brain"]["compiled_claim_invalid_line_count"] == 1
    assert (
        "brain: llm-full compiled claim count does not match valid JSONL claims"
        in production["findings"]
    )
    assert (
        "brain: compiled claims JSONL has invalid compiled claim rows"
        in production["findings"]
    )


def test_production_readiness_rejects_compiled_claim_unknown_record_refs(
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
        _llm_compile_manifest_fixture(),
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
    _write_brain_category_file_fixture(tmp_path)
    _write_production_brain_record_fixture(tmp_path)
    claim = CompiledBrainClaim(
        claim_id="CC-production",
        category="world_model",
        statement="Production claim must close over record IDs.",
        mechanism="production readiness fixture",
        scope="diagnostic fixture",
        supporting_record_ids=["BRAIN-missing"],
        contradicting_record_ids=["BRAIN-missing-contra"],
        supporting_episode_ids=["EP-production"],
        positive_case_count=1,
        confidence_label="medium",
        status="supported",
        available_from=datetime(2030, 1, 2, 0, 0, 0, tzinfo=KST),
        provenance={"fixture": "production_readiness"},
    )
    (current / "compiled_claims.jsonl").write_text(
        claim.model_dump_json() + "\n",
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
    assert production["llm_full_brain"]["record_store_readable_for_compiled_claims"] is True
    assert production["llm_full_brain"]["record_store_record_count_for_compiled_claims"] == 1
    assert production["llm_full_brain"]["compiled_claim_supporting_record_id_count"] == 1
    assert production["llm_full_brain"][
        "compiled_claims_with_unknown_supporting_records"
    ] == ["CC-production: BRAIN-missing"]
    assert production["llm_full_brain"][
        "compiled_claims_with_unknown_contradicting_records"
    ] == ["CC-production: BRAIN-missing-contra"]
    assert (
        "brain: compiled claims reference unknown supporting record IDs"
        in production["findings"]
    )
    assert (
        "brain: compiled claims reference unknown contradicting record IDs"
        in production["findings"]
    )


def test_production_readiness_rejects_compiled_claim_episode_and_time_gaps(
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
        _llm_compile_manifest_fixture(),
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
    _write_brain_category_file_fixture(tmp_path)
    _write_production_brain_record_fixture(tmp_path)
    claim = CompiledBrainClaim(
        claim_id="CC-production",
        category="world_model",
        statement="Production claim provenance must match referenced records.",
        mechanism="production readiness fixture",
        scope="diagnostic fixture",
        supporting_record_ids=["BRAIN-production"],
        contradicting_record_ids=["BRAIN-production"],
        supporting_episode_ids=["EP-wrong"],
        contradicting_episode_ids=[],
        positive_case_count=1,
        negative_case_count=1,
        confidence_label="medium",
        status="supported",
        available_from=datetime(2030, 1, 1, 0, 0, 0, tzinfo=KST),
        provenance={"fixture": "production_readiness"},
    )
    (current / "compiled_claims.jsonl").write_text(
        claim.model_dump_json() + "\n",
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
    assert production["llm_full_brain"][
        "compiled_claims_with_unknown_supporting_episodes"
    ] == ["CC-production: EP-wrong"]
    assert production["llm_full_brain"]["compiled_claim_episode_record_mismatches"] == [
        "CC-production: contradicting BRAIN-production->EP-production",
        "CC-production: supporting BRAIN-production->EP-production",
    ]
    assert production["llm_full_brain"]["compiled_claim_temporal_leaks"] == [
        "CC-production: available_from precedes contradicting record BRAIN-production",
        "CC-production: available_from precedes supporting record BRAIN-production",
    ]
    assert (
        "brain: compiled claims reference unknown supporting episode IDs"
        in production["findings"]
    )
    assert (
        "brain: compiled claims episode IDs do not match referenced records"
        in production["findings"]
    )
    assert (
        "brain: compiled claims expose future record evidence"
        in production["findings"]
    )


def test_production_readiness_rejects_single_episode_validated_compiled_claim(
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
        _llm_compile_manifest_fixture(),
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
    _write_brain_category_file_fixture(tmp_path)
    _write_production_brain_record_fixture(tmp_path)
    claim = CompiledBrainClaim(
        claim_id="CC-production",
        category="world_model",
        statement="Single episode support cannot validate a production claim.",
        mechanism="production readiness fixture",
        scope="diagnostic fixture",
        supporting_record_ids=["BRAIN-production"],
        supporting_episode_ids=["EP-production"],
        positive_case_count=1,
        confidence_label="medium",
        status="validated",
        available_from=datetime(2030, 1, 2, 0, 0, 0, tzinfo=KST),
        provenance={"fixture": "production_readiness"},
    )
    (current / "compiled_claims.jsonl").write_text(
        claim.model_dump_json() + "\n",
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
    assert production["llm_full_brain"][
        "validated_compiled_claims_without_contradictions"
    ] == ["CC-production"]
    assert production["llm_full_brain"][
        "validated_compiled_claims_with_single_episode"
    ] == ["CC-production"]
    assert (
        "brain: validated compiled claims are missing contradiction evidence"
        in production["findings"]
    )
    assert (
        "brain: validated compiled claims rely on one or zero supporting episodes"
        in production["findings"]
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
        _llm_compile_manifest_fixture(),
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
    _write_compiled_claim_fixture(tmp_path)
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
        _llm_compile_manifest_fixture(model="gpt-stale"),
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
    _write_compiled_claim_fixture(tmp_path)
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
        _llm_compile_manifest_fixture(),
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
    _write_compiled_claim_fixture(tmp_path)
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
        _llm_compile_manifest_fixture(brain_version="brain-stale"),
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
    _write_compiled_claim_fixture(tmp_path)
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
    assert report["search_order"] == [
        "data_inbox",
        "imported_episodes",
        "tests_fixture",
        "env",
        "cli",
    ]


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


def test_real_bundle_smoke_rejects_production_bundle_without_direct_ingest_contract(
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
        for key in (
            "direct_ingest_contract_present",
            "direct_ingest_contract_schema_version",
            "direct_brain_ingest_ready",
            "brain_eligible",
            "requires_human_semantic_review",
            "direct_ingest_fatal_blocker_count",
            "direct_ingest_contract_validation_parity_verified",
            "direct_ingest_contract_count_hash_parity_verified",
            "final_semantic_audit_present",
            "final_semantic_audit_count",
            "final_semantic_audit_fail_count",
        ):
            inspection.pop(key)
        return inspection

    monkeypatch.setattr("news_scalping_lab.diagnostics.inspect_versioned_bundle", inspect)

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "failed"
    assert report["passed"] is False
    assert report["selected"] is None
    assert report["first_production_status"] == "failed"
    assert report["production_failed_inspection_count"] == 1
    inspection = report["inspections"][0]["inspection"]
    assert inspection["v11_accept_full_smoke_passed"] is True
    assert inspection["direct_ingest_smoke_passed"] is False
    assert (
        "direct_ingest_contract_present=None expected True"
        in report["first_production_failure_reasons"]
    )
    assert (
        "direct_brain_ingest_ready=None expected True"
        in report["first_production_failure_reasons"]
    )
    assert (
        "final_semantic_audit_present=None expected True"
        in report["first_production_failure_reasons"]
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


def test_real_bundle_smoke_accepts_imported_v23_direct_ingest_original_bundle(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    episode_id = "NSLAB-20241204-REAL"
    bundle = tmp_path / "research" / "episodes" / episode_id / "original_bundle.md"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("imported real v23 direct ingest bundle", encoding="utf-8")

    monkeypatch.setattr(
        "news_scalping_lab.diagnostics.inspect_versioned_bundle",
        lambda path: _valid_v23_direct_ingest_bundle_inspection(path, episode_id),
    )

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "passed"
    assert report["passed"] is True
    assert report["first_production_source"] == "imported_episodes"
    assert report["selected"]["path"] == (
        "research/episodes/NSLAB-20241204-REAL/original_bundle.md"
    )
    inspection = report["selected"]["inspection"]
    assert inspection["adapter"] == "v23-direct-ingest"
    assert inspection["v11_accept_full_smoke_passed"] is True
    assert inspection["direct_ingest_smoke_passed"] is True
    assert inspection["blind_valid"] is None


def test_real_bundle_smoke_accepts_later_valid_imported_episode(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    legacy_bundle = tmp_path / "research" / "episodes" / "EP-legacy" / "original_bundle.md"
    valid_episode_id = "NSLAB-20241204-REAL"
    valid_bundle = (
        tmp_path / "research" / "episodes" / valid_episode_id / "original_bundle.md"
    )
    legacy_bundle.parent.mkdir(parents=True)
    valid_bundle.parent.mkdir(parents=True)
    legacy_bundle.write_text("legacy invalid imported bundle", encoding="utf-8")
    valid_bundle.write_text("imported real v23 direct ingest bundle", encoding="utf-8")

    def inspect(path: Path) -> dict[str, object]:
        if path.resolve() == legacy_bundle.resolve():
            return _invalid_v11_bundle_inspection(path)
        return _valid_v23_direct_ingest_bundle_inspection(path, valid_episode_id)

    monkeypatch.setattr("news_scalping_lab.diagnostics.inspect_versioned_bundle", inspect)

    report = real_bundle_smoke_report(settings)

    assert report["status"] == "passed"
    assert report["passed"] is True
    assert report["first_production_source"] == "imported_episodes"
    assert report["first_production_status"] == "failed"
    assert report["production_failed_inspection_count"] == 1
    assert report["real_valid_smoke_count"] == 1
    assert report["selected"]["path"] == (
        "research/episodes/NSLAB-20241204-REAL/original_bundle.md"
    )
    assert report["selected"]["inspection"]["adapter"] == "v23-direct-ingest"


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
            "records_sha256": sha256_text(record_path.read_text(encoding="utf-8")),
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
            "direct_ingest_contract_raw_block_path_listed"
        ]
        is True
    )
    assert (
        production["real_bundle_import"]["direct_ingest_contract_raw_block_exists"]
        is True
    )
    assert (
        production["real_bundle_import"][
            "direct_ingest_contract_raw_block_hash_matches"
        ]
        is True
    )
    assert (
        production["real_bundle_import"][
            "direct_ingest_contract_raw_block_valid_json"
        ]
        is True
    )
    assert (
        production["real_bundle_import"]["direct_ingest_contract_schema_version"]
        == "nslab.direct_ingest_contract.v1"
    )
    assert production["real_bundle_import"]["direct_brain_ingest_ready"] is True
    assert production["real_bundle_import"]["brain_eligible"] is True
    assert (
        production["real_bundle_import"]["requires_human_semantic_review"]
        is False
    )
    assert production["real_bundle_import"]["direct_ingest_fatal_blocker_count"] == 0
    assert (
        production["real_bundle_import"][
            "direct_ingest_contract_validation_parity_verified"
        ]
        is True
    )
    assert (
        production["real_bundle_import"][
            "direct_ingest_contract_count_hash_parity_verified"
        ]
        is True
    )
    assert (
        production["real_bundle_import"]["final_semantic_audit_raw_block_path_listed"]
        is True
    )
    assert (
        production["real_bundle_import"]["final_semantic_audit_raw_block_exists"]
        is True
    )
    assert (
        production["real_bundle_import"][
            "final_semantic_audit_raw_block_hash_matches"
        ]
        is True
    )
    assert production["real_bundle_import"]["final_semantic_audit_count"] == 7
    assert production["real_bundle_import"]["final_semantic_audit_fail_count"] == 0
    assert (
        production["real_bundle_import"]["final_semantic_audit_invalid_line_count"]
        == 0
    )
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


def test_production_readiness_rejects_real_import_record_manifest_path_escape(
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
            "records_file": "../outside-records.jsonl",
            "records_sha256": sha256_text(record_path.read_text(encoding="utf-8")),
        },
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["real_bundle_import"]["passed"] is False
    assert (
        production["real_bundle_import"]["record_manifest_records_file"]
        == "../outside-records.jsonl"
    )
    assert (
        production["real_bundle_import"]["record_manifest_records_file_resolved"]
        is None
    )
    assert (
        "real_bundle_import: record manifest records_file escapes project root"
        in production["findings"]
    )


def test_production_readiness_rejects_real_import_missing_direct_ingest_raw_blocks(
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
    record_path = _write_real_smoke_records(
        tmp_path,
        episode_id,
        inspection,
        include_direct_ingest_raw_blocks=False,
    )
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
            "records_sha256": sha256_text(record_path.read_text(encoding="utf-8")),
        },
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["real_bundle_smoke"]["status"] == "passed"
    assert production["real_bundle_import"]["passed"] is False
    assert (
        production["real_bundle_import"][
            "direct_ingest_contract_raw_block_path_listed"
        ]
        is False
    )
    assert (
        production["real_bundle_import"]["direct_ingest_contract_raw_block_exists"]
        is False
    )
    assert (
        production["real_bundle_import"]["final_semantic_audit_raw_block_path_listed"]
        is False
    )
    assert (
        production["real_bundle_import"]["final_semantic_audit_raw_block_exists"]
        is False
    )
    assert (
        "real_bundle_import: direct ingest contract raw block path missing from imported envelope"
        in production["findings"]
    )
    assert (
        "real_bundle_import: direct ingest contract raw block is missing"
        in production["findings"]
    )
    assert (
        "real_bundle_import: final semantic audit raw block path missing from imported envelope"
        in production["findings"]
    )
    assert (
        "real_bundle_import: final semantic audit raw block is missing"
        in production["findings"]
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
            "records_sha256": sha256_text(record_path.read_text(encoding="utf-8")),
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
            "records_sha256": sha256_text(record_path.read_text(encoding="utf-8")),
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
    assert production["training_exports"]["expected_weight_validation_statuses"] == {}
    assert production["training_exports"]["weight_validation_status_mismatches"] == {}
    assert production["training_exports"]["weight_diagnostic_count_mismatches"] == []
    assert any(
        finding.startswith(
            "training: training export source record store is unreadable"
        )
        for finding in production["findings"]
    )


def test_production_readiness_rejects_real_import_invalid_record_envelopes(
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
    for row in rows:
        row.pop("available_from", None)
    record_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
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
            "records_sha256": sha256_text(record_path.read_text(encoding="utf-8")),
        },
    )

    production = production_readiness_report(_production_base_report(), settings)

    assert production["real_bundle_import"]["passed"] is False
    assert production["real_bundle_import"]["record_file_invalid_line_count"] == 0
    assert (
        production["real_bundle_import"]["record_file_invalid_envelope_count"]
        == inspection["normalized_record_count"]
    )
    assert (
        "real_bundle_import: record JSONL for selected real bundle has invalid rows"
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
            "records_sha256": sha256_text(record_path.read_text(encoding="utf-8")),
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
            "records_sha256": sha256_text(record_path.read_text(encoding="utf-8")),
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
            "brain_delta_duplicate_record_ids": ["BRAIN-import-loss-1"],
            "records_with_raw_payload_hash_mismatch": ["BRAIN-import-loss-1"],
            "findings": [
                "brain_delta raw record IDs do not match normalized records",
                "brain_delta raw record IDs are duplicated",
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
    assert production["record_store"]["brain_delta_duplicate_record_ids"] == [
        "BRAIN-import-loss-1"
    ]
    assert production["record_store"]["records_with_raw_payload_hash_mismatch"] == [
        "BRAIN-import-loss-1"
    ]
    assert (
        "records: brain_delta raw record IDs do not match normalized records"
        in production["findings"]
    )
    assert (
        "records: brain_delta raw record IDs are duplicated"
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
            "unknown_typed_payload_count": 1,
            "raw_only_record_count": 1,
            "all_unknown_typed_payload_count": 1,
            "all_raw_only_record_count": 1,
            "staged_unknown_typed_payload_count": 0,
            "staged_raw_only_record_count": 0,
            "unknown_typed_payload_record_ids": ["BRAIN-RAW-ONLY"],
            "raw_only_record_ids": ["BRAIN-RAW-ONLY"],
            "all_unknown_typed_payload_record_ids": ["BRAIN-RAW-ONLY"],
            "all_raw_only_record_ids": ["BRAIN-RAW-ONLY"],
            "staged_unknown_typed_payload_record_ids": [],
            "staged_raw_only_record_ids": [],
            "dropped_record_count": 1,
            "extra_normalized_record_count": 0,
            "quarantined_bundle_count": 1,
            "quarantined_raw_record_count": 1,
            "quarantined_normalized_record_count": 2,
            "quarantined_record_count": 1,
            "quarantine_reasons": {"BUNDLE_VALIDATION_FAILED": 1},
            "quarantine_normalization_skipped_reasons": {
                "BUNDLE_VALIDATION_FAILED": 1
            },
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
    assert production["record_store"]["unknown_typed_payload_count"] == 1
    assert production["record_store"]["raw_only_record_count"] == 1
    assert production["record_store"]["all_unknown_typed_payload_count"] == 1
    assert production["record_store"]["all_raw_only_record_count"] == 1
    assert production["record_store"]["staged_unknown_typed_payload_count"] == 0
    assert production["record_store"]["staged_raw_only_record_count"] == 0
    assert production["record_store"]["unknown_typed_payload_record_ids"] == [
        "BRAIN-RAW-ONLY"
    ]
    assert production["record_store"]["raw_only_record_ids"] == ["BRAIN-RAW-ONLY"]
    assert production["record_store"]["all_unknown_typed_payload_record_ids"] == [
        "BRAIN-RAW-ONLY"
    ]
    assert production["record_store"]["all_raw_only_record_ids"] == [
        "BRAIN-RAW-ONLY"
    ]
    assert production["record_store"]["staged_unknown_typed_payload_record_ids"] == []
    assert production["record_store"]["staged_raw_only_record_ids"] == []
    assert production["record_store"]["quarantined_bundle_count"] == 1
    assert production["record_store"]["quarantined_raw_record_count"] == 1
    assert production["record_store"]["quarantined_normalized_record_count"] == 2
    assert production["record_store"]["quarantined_record_count"] == 1
    assert production["record_store"]["quarantine_reasons"] == {
        "BUNDLE_VALIDATION_FAILED": 1
    }
    assert production["record_store"]["quarantine_normalization_skipped_reasons"] == {
        "BUNDLE_VALIDATION_FAILED": 1
    }
    assert "records: dropped_record_count=1 expected 0" in production["findings"]
    assert (
        "records: raw_only_record_count=1 expected 0 for accepted records"
        in production["findings"]
    )
    assert "records: quarantined_record_count=1 expected 0" in production["findings"]
    assert (
        "records: quarantined_normalized_record_count=2 expected 0"
        in production["findings"]
    )
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
        "NSLAB_PRICE_PROVIDER": "stock-web",
        "NSLAB_STOCK_WEB_PATH": "<path-to-stock-web-checkout-or-cache>",
        "NSLAB_REAL_BUNDLE_PATH": "<path-to-real-v11-ACCEPT_FULL-bundle>",
    }
    assert (
        "real_bundle: no readable v11 ACCEPT_FULL bundle candidate; real smoke pending"
        in production["findings"]
    )
    assert production["findings_by_category"]["real_bundle"] == [
        "real_bundle: no readable v11 ACCEPT_FULL bundle candidate; real smoke pending"
    ]
    assert production["finding_counts_by_category"]["llm"] == 1
    assert production["finding_counts_by_category"]["llm_model"] == 1
    assert production["finding_counts_by_category"]["price"] == 1
    assert production["finding_counts_by_category"]["web"] == 1
    blocker_summary = {
        item["category"]: item for item in production["blocker_summary"]
    }
    assert blocker_summary["real_bundle"] == {
        "category": "real_bundle",
        "finding_count": 1,
        "first_finding": "real_bundle: no readable v11 ACCEPT_FULL bundle candidate; real smoke pending",
    }
    assert production["remediation_commands"] == [
        "python -m news_scalping_lab.cli research smoke-bundle --path %NSLAB_REAL_BUNDLE_PATH% --require-valid",
        "python -m news_scalping_lab.cli research import-bundle %NSLAB_REAL_BUNDLE_PATH% --validate --accept",
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
    *,
    include_direct_ingest_raw_blocks: bool = True,
) -> Path:
    episode_dir = root / "research" / "episodes" / episode_id
    episode_dir.mkdir(parents=True, exist_ok=True)
    raw_blocks_dir = episode_dir / "raw_blocks"
    raw_blocks_dir.mkdir(parents=True, exist_ok=True)
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
    brain_delta_path = raw_blocks_dir / "brain_delta.jsonl"
    source_ledger_path = raw_blocks_dir / "source_ledger.jsonl"
    brain_delta_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in raw_rows),
        encoding="utf-8",
    )
    source_ledger_path.write_text(
        json.dumps({"source_id": source_id}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    raw_block_paths = {
        "brain_delta.jsonl": brain_delta_path,
        "source_ledger.jsonl": source_ledger_path,
    }
    if include_direct_ingest_raw_blocks:
        fatal_blocker_count = inspection["direct_ingest_fatal_blocker_count"]
        final_semantic_audit_count = inspection["final_semantic_audit_count"]
        final_semantic_audit_fail_count = inspection["final_semantic_audit_fail_count"]
        assert isinstance(fatal_blocker_count, int)
        assert isinstance(final_semantic_audit_count, int)
        assert isinstance(final_semantic_audit_fail_count, int)
        direct_ingest_contract_path = raw_blocks_dir / "direct_ingest_contract.json"
        final_semantic_audit_path = raw_blocks_dir / "final_semantic_audit.jsonl"
        write_json(
            direct_ingest_contract_path,
            {
                "schema_version": inspection[
                    "direct_ingest_contract_schema_version"
                ],
                "direct_brain_ingest_ready": inspection[
                    "direct_brain_ingest_ready"
                ],
                "brain_eligible": inspection["brain_eligible"],
                "requires_human_semantic_review": inspection[
                    "requires_human_semantic_review"
                ],
                "fatal_blockers": [
                    f"fixture-blocker-{index}"
                    for index in range(fatal_blocker_count)
                ],
                "hard_gate_summary": {
                    "direct_ingest_contract_validation_parity_verified": inspection[
                        "direct_ingest_contract_validation_parity_verified"
                    ],
                    "direct_ingest_contract_count_hash_parity_verified": inspection[
                        "direct_ingest_contract_count_hash_parity_verified"
                    ],
                },
            },
        )
        final_semantic_audit_path.write_text(
            "".join(
                json.dumps(
                    {
                        "candidate_id": f"CAND-{index}",
                        "semantic_verdict": (
                            "FAIL"
                            if index < final_semantic_audit_fail_count
                            else "PASS"
                        ),
                    },
                    sort_keys=True,
                )
                + "\n"
                for index in range(final_semantic_audit_count)
            ),
            encoding="utf-8",
        )
        raw_block_paths["direct_ingest_contract.json"] = direct_ingest_contract_path
        raw_block_paths["final_semantic_audit.jsonl"] = final_semantic_audit_path
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
                name: path.relative_to(root).as_posix()
                for name, path in sorted(raw_block_paths.items())
            },
            "raw_block_hashes": {
                name: sha256_text(path.read_text(encoding="utf-8"))
                for name, path in sorted(raw_block_paths.items())
            },
            "raw_block_counts": {
                name: (
                    sum(
                        1
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    )
                    if name.endswith(".jsonl")
                    else 1
                )
                for name, path in sorted(raw_block_paths.items())
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


def _write_semantic_index_fixture(
    root: Path,
    *,
    embedding_method: str,
    record_ids: list[str] | None = None,
) -> dict[str, object]:
    record_ids = record_ids or ["BRAIN-1", "BRAIN-2"]
    vector_index_dir = root / "memory" / "vector_index"
    vector_index_dir.mkdir(parents=True, exist_ok=True)
    records_dir = root / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    store_records: list[BrainRecordEnvelope] = []
    for index, record_id in enumerate(record_ids, start=1):
        payload = {
            "record_id": record_id,
            "record_type": "memory_claim",
            "episode_id": "EP-semantic-index",
            "trade_date": "2030-01-01",
            "available_from": datetime(2030, 1, 2, 0, 0, 0, tzinfo=KST).isoformat(),
            "training_target": "semantic_index_fixture",
            "training_eligible": True,
        }
        store_records.append(
            BrainRecordEnvelope(
                record_id=record_id,
                record_type="memory_claim",
                episode_id="EP-semantic-index",
                trade_date=date(2030, 1, 1),
                available_from=datetime(2030, 1, 2, 0, 0, 0, tzinfo=KST),
                training_target="semantic_index_fixture",
                evidence_phase="POSTMORTEM",
                training_eligible=True,
                eligibility_reason="semantic index fixture",
                status="supported",
                confidence_label="medium",
                provenance_source_ids=[f"SRC-{index}"],
                raw_payload_sha256=f"hash-{index}",
                normalized_payload_sha256=f"hash-{index}",
                typed_payload_status="KNOWN_TYPED_PAYLOAD",
                source_block="brain_delta.jsonl",
                source_line=index,
                payload=payload,
            )
        )
    (records_dir / "EP-semantic-index.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in store_records),
        encoding="utf-8",
    )
    records_payload = "".join(
        json.dumps(
            {
                "episode_id": "EP-semantic-index",
                "record_id": record_id,
                "terms": [record_id.lower()],
                "embedding": [0.1, 0.2],
            },
            sort_keys=True,
        )
        + "\n"
        for record_id in record_ids
    )
    (vector_index_dir / "records.jsonl").write_text(
        records_payload,
        encoding="utf-8",
    )
    brain_records_payload = "".join(
        json.dumps(
            {
                "record_id": record_id,
                "record_type": "memory_claim",
                "terms": [record_id.lower()],
                "embedding": [0.1, 0.2],
            },
            sort_keys=True,
        )
        + "\n"
        for record_id in record_ids
    )
    (vector_index_dir / "brain_records.jsonl").write_text(
        brain_records_payload,
        encoding="utf-8",
    )
    write_json(
        vector_index_dir / "manifest.json",
        {
            "schema_version": "nslab.local_vector_index.v1",
            "embedding_method": embedding_method,
            "dimensions": 2,
            "record_count": len(record_ids),
            "accepted_episode_count": 0,
            "accepted_hashes": {},
            "brain_record_count": len(record_ids),
            "brain_record_hashes": {
                record_id: f"hash-{index}"
                for index, record_id in enumerate(record_ids, start=1)
            },
            "records_file": "records.jsonl",
            "records_sha256": sha256_text(records_payload),
            "brain_records_file": "brain_records.jsonl",
            "brain_records_sha256": sha256_text(brain_records_payload),
        },
    )
    return {
        "status": "current",
        "manifest_exists": True,
        "embedding_method": embedding_method,
        "brain_records_exists": True,
        "source_brain_record_count": len(record_ids),
        "brain_record_count": len(record_ids),
    }


def _write_compiled_claim_fixture(
    root: Path,
    *,
    claim_id: str = "CC-production",
    write_category_files: bool = True,
    write_support_record: bool = True,
) -> None:
    current = root / "brain" / "current"
    current.mkdir(parents=True, exist_ok=True)
    if write_category_files:
        _write_brain_category_file_fixture(root)
    if write_support_record:
        _write_production_brain_record_fixture(root)
    claim = CompiledBrainClaim(
        claim_id=claim_id,
        category="world_model",
        statement="Production brain claim fixture with record-level support.",
        mechanism="production readiness fixture",
        scope="diagnostic fixture",
        supporting_record_ids=["BRAIN-production"],
        supporting_episode_ids=["EP-production"],
        positive_case_count=1,
        confidence_label="medium",
        status="supported",
        available_from=datetime(2030, 1, 2, 0, 0, 0, tzinfo=KST),
        provenance={"fixture": "production_readiness"},
    )
    (current / "compiled_claims.jsonl").write_text(
        claim.model_dump_json() + "\n",
        encoding="utf-8",
    )


def _write_production_brain_record_fixture(root: Path) -> None:
    available_from = datetime(2030, 1, 2, 0, 0, 0, tzinfo=KST)
    payload = {
        "record_id": "BRAIN-production",
        "record_type": "memory_claim",
        "episode_id": "EP-production",
        "trade_date": "2030-01-01",
        "available_from": available_from.isoformat(),
        "training_target": "production_readiness_fixture",
        "training_eligible": True,
    }
    payload_hash = sha256_text(canonical_json(payload))
    record = BrainRecordEnvelope(
        record_id="BRAIN-production",
        record_type="memory_claim",
        episode_id="EP-production",
        trade_date=date(2030, 1, 1),
        available_from=available_from,
        training_target="production_readiness_fixture",
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        eligibility_reason="production readiness fixture",
        status="supported",
        confidence_label="medium",
        provenance_source_ids=["SRC-production"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )
    records_dir = root / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / "EP-production.jsonl").write_text(
        record.model_dump_json() + "\n",
        encoding="utf-8",
    )


def _llm_compile_manifest_fixture(**overrides: object) -> dict[str, object]:
    categories: list[dict[str, object]] = []
    for file_name in BRAIN_FILES:
        category = _brain_category(file_name)
        is_world_model = category == "world_model"
        categories.append(
            {
                "category": category,
                "file_name": file_name,
                "source_record_count": 1 if is_world_model else 0,
                "source_record_ids": ["BRAIN-production"] if is_world_model else [],
                "compiled_claim_count": 1 if is_world_model else 0,
                "compiled_claim_ids": ["CC-production"] if is_world_model else [],
            }
        )
    manifest: dict[str, object] = {
        "schema_version": "nslab.llm_full_brain_compile_manifest.v1",
        "brain_version": "brain-production",
        "provider": "openai",
        "model": "gpt-production",
        "source_record_count": 1,
        "compiled_claim_count": 1,
        "record_shard_count": 1,
        "record_shards": [
            {
                "shard_index": 1,
                "record_count": 1,
                "record_ids": ["BRAIN-production"],
                "cache_key": "LLMBRAIN-production-shard",
            }
        ],
        "category_count": len(BRAIN_FILES),
        "categories": categories,
        "llm_generation_count": 19,
    }
    manifest.update(overrides)
    return manifest


def _llm_compile_manifest_v4_fixture(**overrides: object) -> dict[str, object]:
    manifest = _llm_compile_manifest_fixture(
        compiler_version=LLM_FULL_COMPILER_VERSION,
    )
    record_shards = manifest["record_shards"]
    assert isinstance(record_shards, list)
    for index, shard in enumerate(record_shards, start=1):
        assert isinstance(shard, dict)
        shard["prompt_sha256"] = f"brain-compile-shard-{index:04d}-hash"
    categories = manifest["categories"]
    assert isinstance(categories, list)
    for category_entry in categories:
        assert isinstance(category_entry, dict)
        category = category_entry["category"]
        assert isinstance(category, str)
        category_entry["synthesis_prompt_sha256"] = (
            f"brain-compile-synthesis-{category}-hash"
        )
        category_entry["review_prompt_sha256"] = (
            f"brain-compile-review-{category}-hash"
        )
    manifest.update(overrides)
    return manifest


def _llm_compile_run_v4_fixture(**overrides: object) -> dict[str, object]:
    categories: list[dict[str, object]] = []
    for file_name in BRAIN_FILES:
        category = _brain_category(file_name)
        is_world_model = category == "world_model"
        categories.append(
            {
                "category": category,
                "file_name": file_name,
                "source_record_count": 1 if is_world_model else 0,
                "synthesis_cache_key": f"LLMBRAIN-production-synthesis-{category}",
                "synthesis_prompt_sha256": (
                    f"brain-compile-synthesis-{category}-hash"
                ),
                "synthesis_cache_hit": False,
                "review_cache_key": f"LLMBRAIN-production-review-{category}",
                "review_prompt_sha256": f"brain-compile-review-{category}-hash",
                "review_cache_hit": False,
            }
        )
    run: dict[str, object] = {
        "schema_version": "nslab.llm_full_brain_compile_run.v1",
        "brain_version": "brain-production",
        "provider": "openai",
        "model": "gpt-production",
        "llm_generation_count": 19,
        "llm_live_call_count": 19,
        "llm_cache_hit_count": 0,
        "llm_cache_miss_count": 19,
        "all_outputs_from_cache": False,
        "record_shards": [
            {
                "shard_index": 1,
                "cache_key": "LLMBRAIN-production-shard",
                "prompt_sha256": "brain-compile-shard-0001-hash",
                "record_count": 1,
                "cache_hit": False,
            }
        ],
        "categories": categories,
    }
    run.update(overrides)
    return run


def _write_llm_compile_trace_evidence_fixture(
    root: Path,
    compile_run: dict[str, object],
) -> None:
    trace_dir = root / "runs" / "traces"
    checkpoint_dir = root / "runs" / "checkpoints" / "llm"
    trace_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    for index, prompt_hash in enumerate(
        _llm_compile_run_prompt_hashes(compile_run),
        start=1,
    ):
        trace_id = f"TRACE-brain-compile-{index:04d}"
        checkpoint_id = f"LLMCKPT-brain-compile-{index:04d}"
        purpose = f"brain_compile:fixture:{index:04d}"
        model_config = {
            "configured_provider": "openai",
            "provider_class": "OpenAIResponsesProvider",
            "model": "gpt-production",
            "compiler_version": LLM_FULL_COMPILER_VERSION,
        }
        input_payload = {"prompt_sha256": prompt_hash}
        input_sha256 = sha256_text(canonical_json(input_payload))
        token_usage = {
            "prompt_tokens_estimate": 25,
            "completion_tokens_estimate": 10,
        }
        write_json(
            trace_dir / f"{trace_id}.json",
            {
                "schema_version": "nslab.llm_trace.v1",
                "trace_id": trace_id,
                "operation": "generate_text",
                "purpose": purpose,
                "status": "ok",
                "provider": "OpenAIResponsesProvider",
                "checkpoint_id": checkpoint_id,
                "input": input_payload,
                "input_sha256": input_sha256,
                "token_usage": token_usage,
                "model_config": model_config,
            },
        )
        write_json(
            checkpoint_dir / f"{checkpoint_id}.json",
            {
                "schema_version": "nslab.llm_checkpoint.v1",
                "checkpoint_id": checkpoint_id,
                "operation": "generate_text",
                "purpose": purpose,
                "status": "ok",
                "provider": "OpenAIResponsesProvider",
                "input": input_payload,
                "input_sha256": input_sha256,
                "token_usage": token_usage,
                "model_config": model_config,
            },
        )


def _llm_compile_run_prompt_hashes(compile_run: dict[str, object]) -> list[str]:
    prompt_hashes: list[str] = []
    record_shards = compile_run["record_shards"]
    assert isinstance(record_shards, list)
    for shard in record_shards:
        assert isinstance(shard, dict)
        prompt_hash = shard["prompt_sha256"]
        assert isinstance(prompt_hash, str)
        prompt_hashes.append(prompt_hash)
    categories = compile_run["categories"]
    assert isinstance(categories, list)
    for category in categories:
        assert isinstance(category, dict)
        synthesis_prompt_hash = category["synthesis_prompt_sha256"]
        review_prompt_hash = category["review_prompt_sha256"]
        assert isinstance(synthesis_prompt_hash, str)
        assert isinstance(review_prompt_hash, str)
        prompt_hashes.extend([synthesis_prompt_hash, review_prompt_hash])
    return sorted(prompt_hashes)


def _write_brain_category_file_fixture(root: Path) -> None:
    current = root / "brain" / "current"
    current.mkdir(parents=True, exist_ok=True)
    for file_name in BRAIN_FILES:
        title = file_name.removesuffix(".md").replace("_", " ").title()
        (current / file_name).write_text(
            f"# {title}\n\nProduction llm-full category fixture for {file_name}.\n",
            encoding="utf-8",
        )


def _write_training_record_store(
    root: Path,
    *,
    include_direct_event_weight_mismatch: bool = False,
    include_duplicate_issuer_day: bool = False,
    include_unsealed_preference_pair: bool = False,
) -> None:
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
    if include_duplicate_issuer_day:
        records.append(
            _training_record(
                record_id="BRAIN-TRAIN-ISSUER-DUP",
                record_type="supervised_issuer_day_case",
                training_target="issuer_day_price_response",
                payload={
                    "record_id": "BRAIN-TRAIN-ISSUER-DUP",
                    "record_type": "supervised_issuer_day_case",
                    "episode_id": episode_id,
                    "trade_date": "2030-01-10",
                    "issuer_day_case_id": "ISSUER-DUP",
                    "ticker": "TRAIN",
                    "safe_D1_features": {"market_cap": "known before cutoff"},
                    "blind_fact_ids": ["FACT-TRAIN-DUP"],
                    "blind_inference_ids": ["INF-TRAIN-DUP"],
                    "event_ids": ["EVT-TRAIN-DUP"],
                    "response_class": "winner",
                    "D_outcome": {"label_quality": "verified"},
                    "sample_weight": 0.0,
                    "attribution_status": "attributed",
                },
            )
        )
    if include_direct_event_weight_mismatch:
        records.extend(
            [
                _training_record(
                    record_id="BRAIN-TRAIN-DIRECT-A",
                    record_type="supervised_direct_event_case",
                    training_target="direct_event_response",
                    payload={
                        "record_id": "BRAIN-TRAIN-DIRECT-A",
                        "record_type": "supervised_direct_event_case",
                        "episode_id": episode_id,
                        "trade_date": "2030-01-10",
                        "case_id": "DIRECT-A",
                        "issuer_day_case_id": "ISSUER-1",
                        "ticker": "TRAIN",
                        "event_id": "EVT-A",
                        "response_class": "winner",
                        "D_outcome": {"label_quality": "verified"},
                        "sample_weight": 0.4,
                    },
                ),
                _training_record(
                    record_id="BRAIN-TRAIN-DIRECT-B",
                    record_type="supervised_direct_event_case",
                    training_target="direct_event_response",
                    payload={
                        "record_id": "BRAIN-TRAIN-DIRECT-B",
                        "record_type": "supervised_direct_event_case",
                        "episode_id": episode_id,
                        "trade_date": "2030-01-10",
                        "case_id": "DIRECT-B",
                        "issuer_day_case_id": "ISSUER-1",
                        "ticker": "TRAIN",
                        "event_id": "EVT-B",
                        "response_class": "winner",
                        "D_outcome": {"label_quality": "verified"},
                        "sample_weight": 0.4,
                    },
                ),
            ]
        )
    if include_unsealed_preference_pair:
        records.append(
            _training_record(
                record_id="BRAIN-TRAIN-UNSEALED-PAIR",
                record_type="blind_leader_preference_pair",
                training_target="outcome_preferred_candidate",
                payload={
                    "record_id": "BRAIN-TRAIN-UNSEALED-PAIR",
                    "record_type": "blind_leader_preference_pair",
                    "episode_id": episode_id,
                    "trade_date": "2030-01-10",
                    "blind_pair_id": "PAIR-UNSEALED",
                    "outcome_winner_ticker": "WIN",
                    "blind_preference_correct": True,
                },
            )
        )
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
            "record_counts_by_type": _training_record_type_counts(records),
        },
    )


def _training_record_type_counts(
    records: list[BrainRecordEnvelope],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.record_type] = counts.get(record.record_type, 0) + 1
    return dict(sorted(counts.items()))


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


def _write_record_coverage_store(root: Path) -> None:
    available_from = datetime(2030, 1, 2, 0, 0, 0, tzinfo=KST)
    rows: list[BrainRecordEnvelope] = []
    for index, (record_id, record_type, target, phase, eligible) in enumerate(
        (
            (
                "BRAIN-1",
                "supervised_issuer_day_case",
                "issuer_day_price_response",
                "POSTMORTEM",
                True,
            ),
            ("BRAIN-2", "counterexample", "audit_only", "AUDIT", False),
        ),
        start=1,
    ):
        payload = {
            "record_id": record_id,
            "record_type": record_type,
            "episode_id": "EP-coverage",
            "trade_date": "2030-01-01",
            "available_from": available_from.isoformat(),
            "training_target": target,
            "training_eligible": eligible,
        }
        payload_hash = sha256_text(canonical_json(payload))
        rows.append(
            BrainRecordEnvelope(
                record_id=record_id,
                record_type=record_type,
                episode_id="EP-coverage",
                trade_date=date(2030, 1, 1),
                available_from=available_from,
                training_target=target,
                evidence_phase=phase,
                training_eligible=eligible,
                eligibility_reason="record coverage fixture",
                status="supported",
                confidence_label="medium",
                provenance_source_ids=[f"SRC-coverage-{index}"],
                raw_payload_sha256=payload_hash,
                normalized_payload_sha256=payload_hash,
                typed_payload_status="KNOWN_TYPED_PAYLOAD",
                source_block="brain_delta.jsonl",
                source_line=index,
                payload=payload,
            )
        )
    records_dir = root / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / "EP-coverage.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in rows),
        encoding="utf-8",
    )


def _complete_record_coverage() -> dict[str, object]:
    return {
        "schema_version": "nslab.record_coverage_manifest.v1",
        "accepted_episode_count": 0,
        "accepted_record_count": 2,
        "available_record_count": 2,
        "record_coverage_as_of": "2030-01-02T00:00:00+09:00",
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
        "direct_ingest_contract_present": True,
        "direct_ingest_contract_schema_version": "nslab.direct_ingest_contract.v1",
        "direct_brain_ingest_ready": True,
        "brain_eligible": True,
        "requires_human_semantic_review": False,
        "direct_ingest_fatal_blocker_count": 0,
        "direct_ingest_contract_validation_parity_verified": True,
        "direct_ingest_contract_count_hash_parity_verified": True,
        "final_semantic_audit_present": True,
        "final_semantic_audit_count": 7,
        "final_semantic_audit_fail_count": 0,
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


def _valid_v23_direct_ingest_bundle_inspection(
    path: Path,
    episode_id: str,
) -> dict[str, object]:
    inspection = _valid_v11_bundle_inspection(path)
    validation = dict(inspection["validation"])
    validation.pop("blind_valid", None)
    validation.update(
        {
            "adapter": "v23-direct-ingest",
            "brain_eligible": True,
            "direct_ingest_schema_contract_verified": True,
            "direct_ingest_record_count_hash_parity_ready": True,
        }
    )
    inspection.update(
        {
            "adapter": "v23-direct-ingest",
            "manifest_schema_version": "nslab.bundle_manifest.v23",
            "episode_schema_version": None,
            "episode_id": episode_id,
            "brain_eligible": None,
            "direct_ingest_contract_validation_parity_verified": None,
            "direct_ingest_contract_count_hash_parity_verified": None,
            "validation": validation,
        }
    )
    return inspection


def _invalid_v11_bundle_inspection(path: Path) -> dict[str, object]:
    inspection = _valid_v11_bundle_inspection(path)
    inspection["validation_passed"] = False
    inspection["hash_mismatch_count"] = 1
    inspection["validation"] = {
        **dict(inspection["validation"]),
        "passed": False,
    }
    return inspection
