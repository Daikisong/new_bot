"""Project diagnostics for the doctor command."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from news_scalping_lab.audits.coverage import audit_coverage
from news_scalping_lab.brain.compiler import (
    BRAIN_FILES,
    LLM_FULL_COMPILER_VERSION,
    _brain_category,
    current_brain_version,
)
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.schemas import SCHEMA_MODELS
from news_scalping_lab.llm.openai_provider import DEFAULT_OPENAI_EMBEDDING_MODEL
from news_scalping_lab.prices.stock_web import StockWebPriceSource
from news_scalping_lab.records.models import BrainRecordEnvelope, CompiledBrainClaim
from news_scalping_lab.records.store import (
    BrainRecordStore,
    audit_record_store,
    record_store_report_payload,
)
from news_scalping_lab.research_import.versioned_bundle import inspect_versioned_bundle
from news_scalping_lab.retrieval.embedding import VECTOR_EMBEDDING_METHOD
from news_scalping_lab.retrieval.store import inspect_vector_index
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.training import audit_training_exports
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    is_available_as_of,
    parse_datetime,
    relative_to_root,
    sha256_text,
)

ENV_KEYS = [
    "NSLAB_LLM_PROVIDER",
    "NSLAB_WEB_PROVIDER",
    "NSLAB_PRICE_PROVIDER",
    "NSLAB_REAL_BUNDLE_PATH",
    "NSLAB_BRAVE_SEARCH_API_KEY_ENV",
    "NSLAB_BRAVE_SEARCH_COUNT",
    "NSLAB_BRAVE_SEARCH_COUNTRY",
    "NSLAB_BRAVE_SEARCH_LANG",
    "NSLAB_BRAVE_SEARCH_UI_LANG",
    "NSLAB_BRAVE_SEARCH_FRESHNESS_DAYS",
    "NSLAB_STOCK_WEB_PATH",
    "NSLAB_STOCK_WEB_CACHE",
    "NSLAB_STOCK_WEB_CACHE_PATH",
    "NSLAB_STOCK_WEB_REMOTE_URL",
    "NSLAB_MAX_CONCURRENCY",
    "NSLAB_LLM_MODEL",
    "NSLAB_LLM_REASONING_EFFORT",
    "NSLAB_LLM_MAX_OUTPUT_TOKENS",
    "NSLAB_LLM_MAX_RETRIES",
    "NSLAB_OPENAI_MODEL",
    "NSLAB_OPENAI_EMBEDDING_MODEL",
    "OPENAI_API_KEY",
    "BRAVE_SEARCH_API_KEY",
]
OPENAI_PROVIDER_ALIASES = {"openai", "responses", "openai-responses"}
PRODUCTION_WEB_PROVIDER_ALIASES = {"brave", "brave-search", "brave-news"}
PRODUCTION_PRICE_PROVIDER_ALIASES = {"stock-web", "stock_web", "stockweb"}
BRAIN_COMPILE_DIAGNOSTICS_SCHEMA_VERSION = "nslab.brain_compile_diagnostics.v1"
LLM_FULL_COMPILE_MANIFEST_SCHEMA_VERSION = "nslab.llm_full_brain_compile_manifest.v1"
LLM_FULL_COMPILE_RUN_SCHEMA_VERSION = "nslab.llm_full_brain_compile_run.v1"
REAL_BUNDLE_ENV_KEY = "NSLAB_REAL_BUNDLE_PATH"
REAL_BUNDLE_SEARCH_DIRS = (
    ("data_inbox", Path("data/inbox/research")),
    ("tests_fixture", Path("tests/fixtures/research_bundles")),
)
REAL_BUNDLE_PRODUCTION_SOURCES = {"cli", "env", "data_inbox", "imported_episodes"}
REAL_BUNDLE_SEARCH_ORDER = [
    "data_inbox",
    "imported_episodes",
    "tests_fixture",
    "env",
    "cli",
]
PRODUCTION_WEB_EVIDENCE_ARTIFACT_FIELDS = (
    "web_source_artifact",
    "excluded_web_source_artifact",
    "candidate_web_check_artifact",
    "excluded_candidate_web_check_artifact",
    "source_ledger_artifact",
    "final_synthesis_context_artifact",
)
PRODUCTION_WEB_EVIDENCE_CUTOFF_SAFE_ARTIFACT_FIELDS = {
    "web_source_artifact",
    "candidate_web_check_artifact",
    "source_ledger_artifact",
    "final_synthesis_context_artifact",
}
PRODUCTION_WEB_EVIDENCE_ARTIFACT_SHA_FIELDS = {
    field: field.removesuffix("_artifact") + "_sha256"
    for field in PRODUCTION_WEB_EVIDENCE_ARTIFACT_FIELDS
}
PRODUCTION_WEB_EVIDENCE_ARTIFACT_SOURCE_FIELDS = {
    "web_source_artifact": "web_sources",
    "excluded_web_source_artifact": "excluded_web_source_ids",
    "candidate_web_check_artifact": "candidate_web_source_ids",
    "excluded_candidate_web_check_artifact": "excluded_candidate_web_source_ids",
}
PRODUCTION_LLM_PROMPT_PURPOSE_ALIASES = {
    "blind_analysis": "daily_blind_analysis",
}
LLM_TRACE_CORE_FIELDS = {
    "schema_version",
    "trace_id",
    "operation",
    "purpose",
    "status",
    "provider",
    "model_config",
    "metadata",
    "input",
    "input_sha256",
    "output",
    "output_sha256",
    "checkpoint_id",
    "tool_calls",
    "retries",
    "retry_errors",
    "token_usage",
    "started_at",
    "finished_at",
    "error",
}
PLACEHOLDER_WEB_EVIDENCE_HOSTS = {
    "0.0.0.0",
    "127.0.0.1",
    "::1",
    "example.com",
    "example.net",
    "example.org",
    "example.test",
    "localhost",
}
PLACEHOLDER_WEB_EVIDENCE_HOST_SUFFIXES = (
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".localhost",
    ".test",
)


@dataclass(frozen=True)
class WebEvidenceArtifactCounts:
    row_count: int
    invalid_json_count: int
    mock_url_count: int
    mock_metadata_count: int
    placeholder_url_count: int
    mock_sample_values: list[str]
    placeholder_sample_values: list[str]


@dataclass(frozen=True)
class WebEvidenceSourceIdStatus:
    source_ids: list[str]
    row_count: int
    missing_source_id_count: int


@dataclass(frozen=True)
class WebEvidenceCutoffStatus:
    checked_row_count: int
    missing_verification_count: int
    failed_verification_count: int
    after_cutoff_count: int
    invalid_timestamp_count: int


@dataclass(frozen=True)
class ManifestPromptHashStatus:
    values: set[str]
    invalid_fields: list[str]
    fields_by_hash: dict[str, set[str]]
    duplicate_hashes: dict[str, list[str]]


def production_readiness_report(
    report: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    findings: list[str] = []
    remediation = _production_remediation(settings)
    real_bundle_smoke = real_bundle_smoke_report(settings)
    if real_bundle_smoke["status"] == "pending":
        findings.append("real_bundle: no readable v11 ACCEPT_FULL bundle candidate; real smoke pending")
    elif real_bundle_smoke["status"] == "synthetic_only":
        findings.append("real_bundle: only synthetic fixture smoke passed; real v11 ACCEPT_FULL smoke pending")
    elif real_bundle_smoke["status"] != "passed":
        findings.append("real_bundle: v11 ACCEPT_FULL smoke failed")
        findings.extend(
            f"real_bundle: {reason}"
            for reason in _string_list(
                real_bundle_smoke.get("first_production_failure_reasons")
            )
        )
    real_bundle_import = _real_bundle_import_status(settings, real_bundle_smoke)
    if (
        real_bundle_smoke["status"] == "passed"
        and real_bundle_import["passed"] is not True
    ):
        findings.extend(
            f"real_bundle_import: {finding}"
            for finding in real_bundle_import["findings"]
        )
    if settings.llm_provider.strip().lower() == "mock":
        findings.append("llm: mock provider cannot compile production brain")
    if settings.llm.provider.strip().lower() == "mock":
        findings.append("llm_model: mock model profile cannot compile production brain")
    llm_evidence = _production_llm_evidence_status(settings.project_root)
    if llm_evidence["passed"] is not True:
        findings.extend(
            f"llm_evidence: {finding}"
            for finding in llm_evidence["findings"]
        )
    openai_status = _nested_dict(report, "api_connections", "openai").get("status")
    if settings.llm_provider.strip().lower() in OPENAI_PROVIDER_ALIASES and openai_status != "configured_not_called":
        findings.append("openai: production llm-full requires configured OpenAI SDK and API key")
    web_provider = settings.web_provider.strip().lower()
    brave_status = _nested_dict(report, "api_connections", "brave_search").get("status")
    if web_provider == "mock":
        findings.append("web: mock provider cannot supply production evidence")
    elif web_provider not in PRODUCTION_WEB_PROVIDER_ALIASES:
        findings.append(f"web: unsupported production provider {settings.web_provider}")
    elif brave_status != "configured_not_called":
        findings.append(
            "brave_search: production web research requires configured Brave Search API key"
        )
    price_data = _production_price_data_status(report, settings)
    if price_data["passed"] is not True:
        findings.extend(f"price: {finding}" for finding in price_data["findings"])
    price_evidence = _production_price_evidence_status(settings.project_root)
    if price_evidence["passed"] is not True:
        findings.extend(
            f"price_evidence: {finding}"
            for finding in price_evidence["findings"]
        )
    web_evidence = _production_web_evidence_status(settings.project_root)
    if web_evidence["passed"] is not True:
        findings.extend(
            f"web_evidence: {finding}"
            for finding in web_evidence["findings"]
        )
    brain = report.get("brain")
    if isinstance(brain, dict):
        brain_audit = _nested_dict(brain, "audit")
        if not brain_audit:
            findings.append("brain: latest brain audit is missing")
        elif brain_audit.get("passed") is not True:
            findings.append("brain: latest brain audit failed")
        if brain_audit and brain_audit.get("deep") is not True:
            findings.append("brain: latest brain audit was not run with --deep")
        if brain_audit and not _brain_audit_diversity_summary_present(brain_audit):
            findings.append("brain: latest brain audit diversity summary is missing")
        if brain_audit and _string_list(
            brain_audit.get("llm_compile_category_schema_mismatches")
        ):
            findings.append(
                "brain: latest brain audit llm compile category schema mismatches"
            )
        coverage = brain.get("coverage")
        if isinstance(coverage, dict) and coverage.get("status") not in {"complete", "missing"}:
            findings.append("brain: accepted episodes are not fully covered")
    else:
        findings.append("brain: latest brain audit is missing")
    brain_manifest = _read_optional_json(settings.project_root / "brain" / "current" / "brain_manifest.json")
    build_mode = brain_manifest.get("build_mode") if isinstance(brain_manifest, dict) else None
    catalog_only = _brain_manifest_catalog_only(brain_manifest)
    catalog_mode_reason = _brain_manifest_catalog_mode_reason(brain_manifest)
    deprecated_mode_alias = _brain_manifest_deprecated_mode_alias(brain_manifest)
    production_eligible = _brain_manifest_production_eligible(brain_manifest)
    current_brain_version_value = (
        brain_manifest.get("brain_version") if isinstance(brain_manifest, dict) else None
    )
    record_coverage = _read_optional_json(
        settings.project_root / "brain" / "current" / "record_coverage_manifest.json"
    )
    expected_source_record_count = _int_from_mapping(
        record_coverage,
        "accepted_record_count",
    )
    record_coverage_status = _production_record_coverage_status(
        record_coverage,
        root=settings.project_root,
    )
    llm_full_brain = _llm_full_brain_status(
        settings,
        build_mode=build_mode,
        catalog_only=catalog_only,
        catalog_mode_reason=catalog_mode_reason,
        deprecated_mode_alias=deprecated_mode_alias,
        production_eligible=production_eligible,
        current_brain_version=current_brain_version_value,
        expected_source_record_count=expected_source_record_count,
    )
    if catalog_only is True:
        findings.append("brain: current manifest is catalog_only")
    if build_mode != "llm-full":
        observed_mode = build_mode if isinstance(build_mode, str) and build_mode else "missing"
        findings.append(f"brain: current manifest build_mode is {observed_mode}, not llm-full")
    if llm_full_brain["passed"] is not True:
        for finding in llm_full_brain["findings"]:
            formatted_finding = f"brain: {finding}"
            if formatted_finding not in findings:
                findings.append(formatted_finding)
    if record_coverage_status["passed"] is not True:
        findings.extend(
            f"records: {finding}" for finding in record_coverage_status["findings"]
        )
    record_store = _production_record_store_status(settings)
    if record_store["passed"] is not True:
        findings.extend(f"records: {finding}" for finding in record_store["findings"])
    warehouse = _production_warehouse_status(
        report.get("warehouse"),
        root=settings.project_root,
    )
    if warehouse["passed"] is not True:
        findings.extend(f"warehouse: {finding}" for finding in warehouse["findings"])
    vector_index = report.get("vector_index")
    semantic_index = _production_semantic_index_status(
        vector_index,
        settings=settings,
        expected_source_record_count=expected_source_record_count,
    )
    if semantic_index["passed"] is not True:
        findings.extend(
            f"embedding: {finding}" for finding in semantic_index["findings"]
        )
    training_exports = _production_training_export_status(settings)
    if training_exports["passed"] is not True:
        findings.extend(
            f"training: {finding}" for finding in training_exports["findings"]
        )
    findings_by_category = _findings_by_category(findings)
    finding_counts_by_category = {
        category: len(category_findings)
        for category, category_findings in findings_by_category.items()
    }
    return {
        "schema_version": "nslab.production_readiness.v1",
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "finding_counts_by_category": finding_counts_by_category,
        "findings_by_category": findings_by_category,
        "blocker_summary": _production_blocker_summary(findings_by_category),
        "real_bundle_smoke": real_bundle_smoke,
        "real_bundle_import": real_bundle_import,
        "llm_evidence": llm_evidence,
        "llm_full_brain": llm_full_brain,
        "record_coverage": record_coverage_status,
        "record_store": record_store,
        "warehouse": warehouse,
        "semantic_index": semantic_index,
        "training_exports": training_exports,
        "web_evidence": web_evidence,
        "price_data": price_data,
        "price_evidence": price_evidence,
        "required_environment": remediation["required_environment"],
        "remediation_commands": remediation["commands"],
    }


def _findings_by_category(findings: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for finding in findings:
        category, separator, _ = finding.partition(":")
        normalized_category = category.strip() if separator and category.strip() else "general"
        grouped.setdefault(normalized_category, []).append(finding)
    return {category: grouped[category] for category in sorted(grouped)}


def _production_blocker_summary(
    findings_by_category: dict[str, list[str]],
) -> list[dict[str, Any]]:
    return [
        {
            "category": category,
            "finding_count": len(category_findings),
            "first_finding": category_findings[0] if category_findings else None,
        }
        for category, category_findings in findings_by_category.items()
    ]


def _production_price_data_status(
    report: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    findings: list[str] = []
    provider = settings.price_provider.strip().lower()
    stock_web = report.get("stock_web")
    if provider == "mock":
        findings.append("mock provider cannot supply production D-1 price evidence")
    elif provider not in PRODUCTION_PRICE_PROVIDER_ALIASES:
        findings.append(f"unsupported production provider {settings.price_provider}")
    elif not isinstance(stock_web, dict) or stock_web.get("effective_path_exists") is not True:
        findings.append("stock-web provider has no readable path")
    elif not isinstance(stock_web.get("schema"), dict):
        findings.append("stock-web atlas schema is missing or unreadable")
    elif not isinstance(stock_web.get("schema_status"), dict) or _nested_dict(
        stock_web, "schema_status"
    ).get("status") != "ok":
        findings.append("stock-web atlas manifest/schema or shard roots are incomplete")
    return {
        "schema_version": "nslab.production_price_data.v1",
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "provider": settings.price_provider,
        "stock_web_effective_path": (
            stock_web.get("effective_path") if isinstance(stock_web, dict) else None
        ),
        "stock_web_effective_path_exists": (
            stock_web.get("effective_path_exists")
            if isinstance(stock_web, dict)
            else None
        ),
        "stock_web_schema_status": (
            _nested_dict(stock_web, "schema_status")
            if isinstance(stock_web, dict)
            else {}
        ),
    }


def _production_price_evidence_status(root: Path) -> dict[str, Any]:
    manifest_dir = root / "runs" / "manifests"
    manifest_paths = sorted(manifest_dir.glob("*.json")) if manifest_dir.exists() else []
    unreadable_manifests: list[str] = []
    invalid_manifest_schemas: list[dict[str, Any]] = []
    missing_price_snapshots: list[str] = []
    missing_source_names: list[str] = []
    missing_source_refs: list[str] = []
    mock_source_refs: list[dict[str, Any]] = []
    mock_price_snapshots: list[dict[str, Any]] = []
    invalid_allowed_through: list[dict[str, Any]] = []
    unsafe_allowed_through: list[dict[str, Any]] = []
    invalid_as_of: list[dict[str, Any]] = []
    as_of_after_cutoff: list[dict[str, Any]] = []
    checked_final_context_refs = 0
    invalid_final_context_refs: list[dict[str, Any]] = []
    missing_final_context_artifacts: list[dict[str, Any]] = []
    unreadable_final_context_artifacts: list[dict[str, Any]] = []
    missing_final_context_price_data: list[dict[str, Any]] = []
    final_context_mock_price_data: list[dict[str, Any]] = []
    final_context_source_mismatches: list[dict[str, Any]] = []
    final_context_source_ref_mismatches: list[dict[str, Any]] = []
    final_context_allowed_mismatches: list[dict[str, Any]] = []
    invalid_final_context_price_rows: list[dict[str, Any]] = []
    unsafe_final_context_price_rows: list[dict[str, Any]] = []
    for manifest_path in manifest_paths:
        relative_path = relative_to_root(manifest_path, root)
        try:
            manifest = _read_json_object(manifest_path)
        except ValueError:
            unreadable_manifests.append(relative_path)
            continue
        if manifest.get("schema_version") != "nslab.context_manifest.v1":
            invalid_manifest_schemas.append(
                {
                    "path": relative_path,
                    "run_id": manifest.get("run_id"),
                    "schema_version": manifest.get("schema_version"),
                }
            )
        price_snapshot = manifest.get("price_snapshot")
        if not isinstance(price_snapshot, dict):
            missing_price_snapshots.append(relative_path)
            continue
        source_name = price_snapshot.get("source_name")
        if not isinstance(source_name, str) or not source_name.strip():
            missing_source_names.append(relative_path)
        elif "mock" in source_name.strip().lower():
            mock_price_snapshots.append(
                {
                    "path": relative_path,
                    "run_id": manifest.get("run_id"),
                    "source_name": source_name,
                }
            )
        source_ref = price_snapshot.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref.strip():
            missing_source_refs.append(relative_path)
        elif _is_mock_or_placeholder_price_ref(source_ref):
            mock_source_refs.append(
                {
                    "path": relative_path,
                    "run_id": manifest.get("run_id"),
                    "source_ref": source_ref,
                }
            )
        raw_allowed_through = price_snapshot.get("allowed_through")
        trade_date = _manifest_trade_date(manifest)
        allowed_through: date | None = None
        if not isinstance(raw_allowed_through, str) or not raw_allowed_through:
            invalid_allowed_through.append(
                {
                    "path": relative_path,
                    "run_id": manifest.get("run_id"),
                    "allowed_through": raw_allowed_through,
                }
            )
        else:
            try:
                allowed_through = date.fromisoformat(raw_allowed_through)
            except ValueError:
                invalid_allowed_through.append(
                    {
                        "path": relative_path,
                        "run_id": manifest.get("run_id"),
                        "allowed_through": raw_allowed_through,
                    }
                )
            else:
                if trade_date is not None and allowed_through >= trade_date:
                    unsafe_allowed_through.append(
                        {
                            "path": relative_path,
                            "run_id": manifest.get("run_id"),
                            "allowed_through": raw_allowed_through,
                            "trade_date": trade_date.isoformat(),
                        }
                    )
        raw_as_of = price_snapshot.get("as_of")
        raw_cutoff_at = manifest.get("cutoff_at")
        if isinstance(raw_as_of, str) and isinstance(raw_cutoff_at, str):
            try:
                as_of = parse_datetime(raw_as_of)
                cutoff_at = parse_datetime(raw_cutoff_at)
            except ValueError:
                invalid_as_of.append(
                    {
                        "path": relative_path,
                        "run_id": manifest.get("run_id"),
                        "as_of": raw_as_of,
                        "cutoff_at": raw_cutoff_at,
                    }
                )
            else:
                if not is_available_as_of(as_of, cutoff_at):
                    as_of_after_cutoff.append(
                        {
                            "path": relative_path,
                            "run_id": manifest.get("run_id"),
                            "as_of": raw_as_of,
                            "cutoff_at": raw_cutoff_at,
                        }
                    )
        final_context_ref = manifest.get("final_synthesis_context_artifact")
        if isinstance(final_context_ref, str) and final_context_ref:
            checked_final_context_refs += 1
            final_context_path = _project_relative_artifact_path(root, final_context_ref)
            if final_context_path is None:
                invalid_final_context_refs.append(
                    {
                        "manifest": relative_path,
                        "artifact": final_context_ref,
                    }
                )
                continue
            relative_artifact_path = relative_to_root(final_context_path, root)
            if not final_context_path.exists() or not final_context_path.is_file():
                missing_final_context_artifacts.append(
                    {
                        "manifest": relative_path,
                        "artifact": relative_artifact_path,
                    }
                )
                continue
            try:
                final_context = _read_json_object(final_context_path)
            except ValueError:
                unreadable_final_context_artifacts.append(
                    {
                        "manifest": relative_path,
                        "artifact": relative_artifact_path,
                    }
                )
                continue
            price_context = _final_context_price_data(final_context)
            if price_context is None:
                missing_final_context_price_data.append(
                    {
                        "manifest": relative_path,
                        "artifact": relative_artifact_path,
                    }
                )
                continue
            context_source_name = price_context.get("source_name")
            if isinstance(context_source_name, str):
                if "mock" in context_source_name.strip().lower():
                    final_context_mock_price_data.append(
                        {
                            "manifest": relative_path,
                            "artifact": relative_artifact_path,
                            "source_name": context_source_name,
                        }
                    )
                if (
                    isinstance(source_name, str)
                    and source_name.strip()
                    and context_source_name != source_name
                ):
                    final_context_source_mismatches.append(
                        {
                            "manifest": relative_path,
                            "artifact": relative_artifact_path,
                            "manifest_source_name": source_name,
                            "context_source_name": context_source_name,
                        }
                    )
            elif isinstance(source_name, str) and source_name.strip():
                final_context_source_mismatches.append(
                    {
                        "manifest": relative_path,
                        "artifact": relative_artifact_path,
                        "manifest_source_name": source_name,
                        "context_source_name": context_source_name,
                    }
                )
            context_source_ref = price_context.get("source_ref")
            if (
                isinstance(source_ref, str)
                and source_ref.strip()
                and context_source_ref != source_ref
            ):
                final_context_source_ref_mismatches.append(
                    {
                        "manifest": relative_path,
                        "artifact": relative_artifact_path,
                        "manifest_source_ref": source_ref,
                        "context_source_ref": context_source_ref,
                    }
                )
            context_allowed_through = price_context.get("allowed_through")
            if (
                isinstance(raw_allowed_through, str)
                and raw_allowed_through
                and context_allowed_through != raw_allowed_through
            ):
                final_context_allowed_mismatches.append(
                    {
                        "manifest": relative_path,
                        "artifact": relative_artifact_path,
                        "manifest_allowed_through": raw_allowed_through,
                        "context_allowed_through": context_allowed_through,
                    }
                )
            snapshots = price_context.get("snapshots")
            if snapshots is None:
                continue
            if not isinstance(snapshots, list):
                invalid_final_context_price_rows.append(
                    {
                        "manifest": relative_path,
                        "artifact": relative_artifact_path,
                        "row_index": None,
                        "reason": "snapshots_not_list",
                        "trade_date": None,
                    }
                )
                continue
            for row_index, row in enumerate(snapshots):
                if not isinstance(row, dict):
                    invalid_final_context_price_rows.append(
                        {
                            "manifest": relative_path,
                            "artifact": relative_artifact_path,
                            "row_index": row_index,
                            "reason": "snapshot_not_object",
                            "trade_date": None,
                        }
                    )
                    continue
                raw_row_trade_date = row.get("trade_date")
                if not isinstance(raw_row_trade_date, str):
                    invalid_final_context_price_rows.append(
                        {
                            "manifest": relative_path,
                            "artifact": relative_artifact_path,
                            "row_index": row_index,
                            "reason": "trade_date_missing_or_invalid",
                            "trade_date": raw_row_trade_date,
                        }
                    )
                    continue
                try:
                    row_trade_date = date.fromisoformat(raw_row_trade_date)
                except ValueError:
                    invalid_final_context_price_rows.append(
                        {
                            "manifest": relative_path,
                            "artifact": relative_artifact_path,
                            "row_index": row_index,
                            "reason": "trade_date_invalid",
                            "trade_date": raw_row_trade_date,
                        }
                    )
                    continue
                reasons: list[str] = []
                if allowed_through is not None and row_trade_date > allowed_through:
                    reasons.append("after_allowed_through")
                if trade_date is not None and row_trade_date >= trade_date:
                    reasons.append("not_before_trade_date")
                if reasons:
                    unsafe_final_context_price_rows.append(
                        {
                            "manifest": relative_path,
                            "artifact": relative_artifact_path,
                            "row_index": row_index,
                            "ticker": row.get("ticker"),
                            "trade_date": raw_row_trade_date,
                            "allowed_through": (
                                allowed_through.isoformat()
                                if allowed_through is not None
                                else raw_allowed_through
                            ),
                            "manifest_trade_date": (
                                trade_date.isoformat()
                                if trade_date is not None
                                else manifest.get("trade_date")
                            ),
                            "reasons": reasons,
                        }
                    )

    findings: list[str] = []
    if not manifest_paths:
        findings.append("production price context manifest is missing")
    for path in unreadable_manifests:
        findings.append(f"context manifest is unreadable: {path}")
    for manifest in invalid_manifest_schemas:
        findings.append(
            f"context manifest schema_version is invalid in {manifest['path']}: "
            f"{manifest['schema_version']}"
        )
    for path in missing_price_snapshots:
        findings.append(f"context manifest price_snapshot is missing: {path}")
    for path in missing_source_names:
        findings.append(f"context manifest price_snapshot source_name is missing: {path}")
    for path in missing_source_refs:
        findings.append(f"context manifest price_snapshot source_ref is missing: {path}")
    for snapshot in mock_source_refs:
        findings.append(
            f"mock or placeholder price_snapshot source_ref present in "
            f"{snapshot['path']}: source_ref={snapshot['source_ref']}"
        )
    for snapshot in mock_price_snapshots:
        findings.append(
            f"mock price_snapshot present in {snapshot['path']}: "
            f"source_name={snapshot['source_name']}"
        )
    for snapshot in invalid_allowed_through:
        findings.append(
            f"context manifest price_snapshot allowed_through is invalid in "
            f"{snapshot['path']}: {snapshot['allowed_through']}"
        )
    for snapshot in unsafe_allowed_through:
        findings.append(
            f"context manifest price_snapshot allowed_through is not before "
            f"trade_date in {snapshot['path']}: {snapshot['allowed_through']}"
        )
    for snapshot in invalid_as_of:
        findings.append(
            f"context manifest price_snapshot as_of is invalid in "
            f"{snapshot['path']}: {snapshot['as_of']}"
        )
    for snapshot in as_of_after_cutoff:
        findings.append(
            f"context manifest price_snapshot as_of is after cutoff_at in "
            f"{snapshot['path']}: {snapshot['as_of']}"
        )
    for artifact in invalid_final_context_refs:
        findings.append(
            "final synthesis price context artifact reference is invalid: "
            f"{artifact['manifest']} final_synthesis_context_artifact="
            f"{artifact['artifact']}"
        )
    for artifact in missing_final_context_artifacts:
        findings.append(
            f"final synthesis price context artifact is missing: "
            f"{artifact['manifest']} {artifact['artifact']}"
        )
    for artifact in unreadable_final_context_artifacts:
        findings.append(
            f"final synthesis price context artifact is unreadable: "
            f"{artifact['artifact']}"
        )
    for artifact in missing_final_context_price_data:
        findings.append(
            f"final synthesis context d_minus_one_market_data is missing: "
            f"{artifact['artifact']}"
        )
    for artifact in final_context_mock_price_data:
        findings.append(
            f"mock final synthesis price data present in {artifact['artifact']}: "
            f"source_name={artifact['source_name']}"
        )
    for mismatch in final_context_source_mismatches:
        findings.append(
            f"final synthesis price source_name does not match manifest in "
            f"{mismatch['artifact']}: {mismatch['context_source_name']} != "
            f"{mismatch['manifest_source_name']}"
        )
    for mismatch in final_context_source_ref_mismatches:
        findings.append(
            f"final synthesis price source_ref does not match manifest in "
            f"{mismatch['artifact']}: {mismatch['context_source_ref']} != "
            f"{mismatch['manifest_source_ref']}"
        )
    for mismatch in final_context_allowed_mismatches:
        findings.append(
            f"final synthesis price allowed_through does not match manifest in "
            f"{mismatch['artifact']}: {mismatch['context_allowed_through']} != "
            f"{mismatch['manifest_allowed_through']}"
        )
    for row in invalid_final_context_price_rows:
        findings.append(
            f"final synthesis price snapshot row is invalid in {row['artifact']}: "
            f"row={row['row_index']} reason={row['reason']}"
        )
    for row in unsafe_final_context_price_rows:
        findings.append(
            f"final synthesis price snapshot row violates D-1 cutoff in "
            f"{row['artifact']}: row={row['row_index']} "
            f"trade_date={row['trade_date']} reasons={','.join(row['reasons'])}"
        )

    return {
        "schema_version": "nslab.production_price_evidence.v1",
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "checked_manifest_count": len(manifest_paths),
        "unreadable_manifest_count": len(unreadable_manifests),
        "unreadable_manifests": unreadable_manifests,
        "invalid_manifest_schema_count": len(invalid_manifest_schemas),
        "invalid_manifest_schemas": invalid_manifest_schemas,
        "missing_price_snapshot_count": len(missing_price_snapshots),
        "missing_price_snapshot_manifests": missing_price_snapshots,
        "missing_source_name_count": len(missing_source_names),
        "missing_source_name_manifests": missing_source_names,
        "missing_source_ref_count": len(missing_source_refs),
        "missing_source_ref_manifests": missing_source_refs,
        "mock_source_ref_count": len(mock_source_refs),
        "mock_source_refs": mock_source_refs,
        "mock_price_snapshot_count": len(mock_price_snapshots),
        "mock_price_snapshots": mock_price_snapshots,
        "invalid_allowed_through_count": len(invalid_allowed_through),
        "invalid_allowed_through_manifests": invalid_allowed_through,
        "unsafe_allowed_through_count": len(unsafe_allowed_through),
        "unsafe_allowed_through_manifests": unsafe_allowed_through,
        "invalid_as_of_count": len(invalid_as_of),
        "invalid_as_of_manifests": invalid_as_of,
        "as_of_after_cutoff_count": len(as_of_after_cutoff),
        "as_of_after_cutoff_manifests": as_of_after_cutoff,
        "checked_final_context_reference_count": checked_final_context_refs,
        "invalid_final_context_ref_count": len(invalid_final_context_refs),
        "invalid_final_context_refs": invalid_final_context_refs,
        "missing_final_context_artifact_count": len(missing_final_context_artifacts),
        "missing_final_context_artifacts": missing_final_context_artifacts,
        "unreadable_final_context_artifact_count": len(
            unreadable_final_context_artifacts
        ),
        "unreadable_final_context_artifacts": unreadable_final_context_artifacts,
        "missing_final_context_price_data_count": len(
            missing_final_context_price_data
        ),
        "missing_final_context_price_data": missing_final_context_price_data,
        "final_context_mock_price_data_count": len(final_context_mock_price_data),
        "final_context_mock_price_data": final_context_mock_price_data,
        "final_context_source_mismatch_count": len(final_context_source_mismatches),
        "final_context_source_mismatches": final_context_source_mismatches,
        "final_context_source_ref_mismatch_count": len(
            final_context_source_ref_mismatches
        ),
        "final_context_source_ref_mismatches": final_context_source_ref_mismatches,
        "final_context_allowed_mismatch_count": len(final_context_allowed_mismatches),
        "final_context_allowed_mismatches": final_context_allowed_mismatches,
        "invalid_final_context_price_row_count": len(invalid_final_context_price_rows),
        "invalid_final_context_price_rows": invalid_final_context_price_rows,
        "unsafe_final_context_price_row_count": len(unsafe_final_context_price_rows),
        "unsafe_final_context_price_rows": unsafe_final_context_price_rows,
    }


def _manifest_trade_date(manifest: dict[str, Any]) -> date | None:
    raw_trade_date = manifest.get("trade_date")
    if not isinstance(raw_trade_date, str):
        return None
    try:
        return date.fromisoformat(raw_trade_date)
    except ValueError:
        return None


def _is_mock_or_placeholder_price_ref(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.startswith(("mock://", "placeholder://")) or normalized in {
        "mock",
        "placeholder",
    }


def _final_context_price_data(payload: dict[str, Any]) -> dict[str, Any] | None:
    nested_payload = payload.get("payload")
    if isinstance(nested_payload, dict):
        market_data = nested_payload.get("d_minus_one_market_data")
        if isinstance(market_data, dict):
            return market_data
    market_data = payload.get("d_minus_one_market_data")
    return market_data if isinstance(market_data, dict) else None


def real_bundle_smoke_report(
    settings: Settings,
    *,
    explicit_path: Path | None = None,
) -> dict[str, Any]:
    search_locations, candidates = _real_bundle_candidates(
        settings,
        explicit_path=explicit_path,
    )
    inspections: list[dict[str, Any]] = []
    for candidate in candidates:
        path = Path(str(candidate["absolute_path"]))
        if not path.exists() or not path.is_file():
            inspections.append(
                {
                    **candidate,
                    "status": "missing",
                    "inspectable": False,
                    "production_source": _is_production_bundle_candidate(
                        settings,
                        candidate,
                    ),
                }
            )
            continue
        try:
            inspection = inspect_versioned_bundle(path)
        except (OSError, ValueError) as exc:
            inspections.append(
                {
                    **candidate,
                    "status": "inspection_failed",
                    "inspectable": False,
                    "production_source": _is_production_bundle_candidate(
                        settings,
                        candidate,
                    ),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        production_source = _is_production_bundle_candidate(
            settings,
            candidate,
        )
        summary = _real_bundle_inspection_summary(inspection)
        smoke_passed = summary["v11_accept_full_smoke_passed"] is True
        if production_source:
            smoke_passed = (
                smoke_passed and summary["direct_ingest_smoke_passed"] is True
            )
        inspections.append(
            {
                **candidate,
                "status": "passed" if smoke_passed else "failed",
                "inspectable": True,
                "production_source": production_source,
                "inspection": summary,
            }
        )

    valid_inspections = [
        item
        for item in inspections
        if isinstance(item.get("inspection"), dict)
        and item.get("status") == "passed"
    ]
    real_valid_inspections = [
        item for item in valid_inspections if item.get("production_source") is True
    ]
    synthetic_valid_inspections = [
        item for item in valid_inspections if item.get("production_source") is not True
    ]
    production_inspections = [
        item for item in inspections if item.get("production_source") is True
    ]
    passed_production_inspections = [
        item
        for item in production_inspections
        if isinstance(item.get("inspection"), dict)
        and item.get("status") == "passed"
    ]
    first_production_inspection = (
        production_inspections[0] if production_inspections else None
    )
    first_production_passed = (
        isinstance(first_production_inspection, dict)
        and isinstance(first_production_inspection.get("inspection"), dict)
        and first_production_inspection.get("status") == "passed"
    )
    first_production_source = (
        first_production_inspection.get("source")
        if isinstance(first_production_inspection, dict)
        else None
    )
    selected = first_production_inspection if first_production_passed else None
    if selected is None and first_production_source == "imported_episodes":
        selected = next(
            (
                item
                for item in passed_production_inspections
                if item.get("source") == first_production_source
            ),
            None,
        )
    failed_inspection_count = sum(
        1
        for item in inspections
        if item.get("inspectable") is True
        and isinstance(item.get("inspection"), dict)
        and item.get("status") != "passed"
    )
    production_failed_inspection_count = sum(
        1
        for item in inspections
        if item.get("production_source") is True
        and (
            item.get("inspectable") is not True
            or not isinstance(item.get("inspection"), dict)
            or item.get("status") != "passed"
        )
    )
    if selected is not None:
        status = "passed"
    elif first_production_inspection is not None:
        status = "failed"
    elif synthetic_valid_inspections:
        status = "synthetic_only"
    elif production_failed_inspection_count:
        status = "failed"
    elif inspections:
        status = "failed" if failed_inspection_count else "pending"
    else:
        status = "pending"
    return {
        "schema_version": "nslab.real_bundle_smoke.v1",
        "status": status,
        "passed": status == "passed",
        "real_smoke_pending": status != "passed",
        "search_order": REAL_BUNDLE_SEARCH_ORDER,
        "environment_key": REAL_BUNDLE_ENV_KEY,
        "search_locations": search_locations,
        "candidate_count": len(candidates),
        "inspected_count": len(
            [item for item in inspections if item.get("inspectable") is True]
        ),
        "valid_smoke_count": len(valid_inspections),
        "real_valid_smoke_count": len(real_valid_inspections),
        "synthetic_valid_smoke_count": len(synthetic_valid_inspections),
        "failed_inspection_count": failed_inspection_count,
        "production_failed_inspection_count": production_failed_inspection_count,
        "first_production_source": (
            first_production_inspection.get("source")
            if first_production_inspection is not None
            else None
        ),
        "first_production_status": (
            first_production_inspection.get("status")
            if first_production_inspection is not None
            else None
        ),
        "first_production_path": (
            first_production_inspection.get("path")
            if first_production_inspection is not None
            else None
        ),
        "first_production_failure_reasons": (
            _real_bundle_production_failure_reasons(
                first_production_inspection["inspection"]
            )
            if isinstance(first_production_inspection, dict)
            and isinstance(first_production_inspection.get("inspection"), dict)
            else []
        ),
        "selected": selected,
        "inspections": inspections,
    }


def build_doctor_report(
    settings: Settings,
    *,
    production: bool = False,
) -> dict[str, Any]:
    store = ResearchStore(settings.project_root)
    accepted_episode_count = len(store.list_accepted())
    stock_web_path = _resolved_optional_path(settings, settings.stock_web_path)
    stock_web_cache_path = settings.path(settings.stock_web_cache_path)
    stock_web_effective_path, stock_web_effective_source = _stock_web_effective_path(
        settings,
        configured_path=stock_web_path,
        cache_path=stock_web_cache_path,
    )
    stock_web_schema: dict[str, Any] | None = None
    stock_web_schema_status: dict[str, Any] | None = None
    if stock_web_effective_path is not None and stock_web_effective_path.exists():
        stock_web_source = StockWebPriceSource(stock_web_effective_path)
        stock_web_schema = stock_web_source.inspect_atlas_schema()
        stock_web_schema_status = stock_web_source.inspect_atlas_status()
    schema_dir = settings.path("schemas")
    coverage_audit = audit_coverage(settings.project_root, deep=production)
    warehouse_status = _warehouse_status(
        coverage_audit,
        accepted_episode_count=accepted_episode_count,
    )
    database_status = _database_status(settings, coverage_audit)
    report = {
        "project_root": settings.project_root.as_posix(),
        "providers": {
            "llm": settings.llm_provider,
            "web": settings.web_provider,
            "price": settings.price_provider,
        },
        "llm_model": settings.llm.model_dump(exclude_none=True),
        "environment": _environment_status(settings),
        "api_connections": {
            "openai": {
                "required": _openai_required(settings, production=production),
                "configured": bool(settings.env_value("OPENAI_API_KEY")),
                "sdk": _openai_sdk_status(),
                "status": _openai_status(settings, production=production),
            },
            "brave_search": {
                "required": _brave_search_required(settings, production=production),
                "configured": bool(settings.env_value(settings.brave_search_api_key_env)),
                "status": _brave_search_status(settings, production=production),
            },
        },
        "stock_web": {
            "path": stock_web_path.as_posix() if stock_web_path is not None else None,
            "path_exists": bool(stock_web_path is not None and stock_web_path.exists()),
            "cache_enabled": settings.stock_web_cache_enabled,
            "cache_path": stock_web_cache_path.as_posix(),
            "cache_path_exists": stock_web_cache_path.exists(),
            "effective_path": (
                stock_web_effective_path.as_posix()
                if stock_web_effective_path is not None
                else None
            ),
            "effective_path_exists": bool(
                stock_web_effective_path is not None and stock_web_effective_path.exists()
            ),
            "effective_path_source": stock_web_effective_source,
            "remote_url": settings.stock_web_remote_url,
            "schema": stock_web_schema,
            "schema_status": stock_web_schema_status,
        },
        "warehouse": {
            "status": warehouse_status,
            "counts": coverage_audit.get("warehouse_counts", {}),
            "required_files": coverage_audit.get("warehouse_required_files", []),
            "missing_files": coverage_audit.get("warehouse_missing_files", []),
            "unreadable_files": coverage_audit.get("warehouse_unreadable_files", []),
            "required_files_present": coverage_audit.get(
                "warehouse_required_files_present",
                False,
            ),
            "synced": coverage_audit.get("warehouse_synced", False),
            "projection_synced": coverage_audit.get("warehouse_projection_synced", False),
            "count_mismatches": coverage_audit.get("warehouse_count_mismatches", {}),
            "identity_mismatches": coverage_audit.get("warehouse_identity_mismatches", {}),
            "duplicate_identities": coverage_audit.get(
                "warehouse_duplicate_identities",
                {},
            ),
            "weight_mismatches": coverage_audit.get(
                "warehouse_weight_mismatches",
                {},
            ),
            "missing_columns": coverage_audit.get("warehouse_missing_columns", {}),
            "expected_source_counts": coverage_audit.get(
                "warehouse_expected_source_counts",
                {},
            ),
        },
        "database": database_status,
        "brain": {
            "head": current_brain_version(settings.project_root),
            "accepted_episode_count": accepted_episode_count,
            "coverage": _brain_coverage_status(settings.project_root, accepted_episode_count),
            "audit": _brain_audit_status(coverage_audit),
        },
        "vector_index": inspect_vector_index(settings.project_root),
        "schemas": {
            "path": schema_dir.as_posix(),
            "exists": schema_dir.exists(),
            "file_count": _file_count(schema_dir, suffix=".json"),
            "versions": _schema_versions(),
            "files": _schema_file_status(schema_dir),
        },
    }
    report["readiness"] = _doctor_readiness(
        report,
        settings=settings,
        accepted_episode_count=accepted_episode_count,
    )
    return report


def _doctor_readiness(
    report: dict[str, Any],
    *,
    settings: Settings,
    accepted_episode_count: int,
) -> dict[str, Any]:
    findings: list[str] = []
    price_provider = settings.price_provider.strip().lower()
    if price_provider not in {"mock", "stock-web", "stock_web", "stockweb"}:
        findings.append(f"price: unsupported provider {settings.price_provider}")
    api_connections = report.get("api_connections")
    if isinstance(api_connections, dict):
        for name, api_status in sorted(api_connections.items()):
            if not isinstance(api_status, dict):
                continue
            status = api_status.get("status")
            if status == "missing_api_key":
                findings.append(f"{name}: required API key is missing")
            elif status == "missing_sdk":
                findings.append(f"{name}: required SDK extra is not installed")
            elif status == "sdk_missing_async_client":
                findings.append(f"{name}: SDK does not expose AsyncOpenAI")
            elif status == "sdk_import_error":
                findings.append(f"{name}: SDK could not be imported")

    schema_files = _nested_dict(report, "schemas", "files")
    if schema_files.get("status") != "ok":
        findings.append("schemas: missing, invalid, or stale schema files")

    stock_web = report.get("stock_web")
    if settings.price_provider == "stock-web":
        if not isinstance(stock_web, dict) or stock_web.get("effective_path_exists") is not True:
            findings.append("stock_web: configured price provider has no readable path")
        elif not isinstance(stock_web.get("schema"), dict):
            findings.append("stock_web: atlas schema is missing or unreadable")
        elif not isinstance(stock_web.get("schema_status"), dict) or _nested_dict(
            stock_web, "schema_status"
        ).get("status") != "ok":
            findings.append("stock_web: atlas manifest/schema or shard roots are incomplete")

    warehouse = report.get("warehouse")
    if not isinstance(warehouse, dict) or warehouse.get("status") != "ok":
        findings.append("warehouse: required projections are missing, unreadable, or unsynced")
    else:
        counts = warehouse.get("counts")
        if not isinstance(counts, dict) or any(
            not isinstance(count, int) or isinstance(count, bool)
            for count in counts.values()
        ):
            findings.append("warehouse: one or more projections are missing or unreadable")

    database = report.get("database")
    if not isinstance(database, dict) or database.get("status") != "ok":
        findings.append(
            "database: DuckDB engine is unavailable or cannot query warehouse projections"
        )

    brain_coverage = _nested_dict(report, "brain", "coverage")
    if accepted_episode_count > 0 and brain_coverage.get("status") != "complete":
        findings.append("brain: accepted episodes are not fully covered")

    vector_index = report.get("vector_index")
    vector_status = vector_index.get("status") if isinstance(vector_index, dict) else None
    if accepted_episode_count > 0 and vector_status != "current":
        findings.append("vector_index: accepted episodes are not indexed by a current index")
    elif accepted_episode_count == 0 and vector_status not in {"current", "missing"}:
        findings.append("vector_index: empty project index is invalid or stale")

    return {
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
    }


def _production_remediation(settings: Settings) -> dict[str, object]:
    python_command = "python -m news_scalping_lab.cli"
    llm_provider = settings.llm_provider.strip().lower()
    if llm_provider not in OPENAI_PROVIDER_ALIASES:
        llm_provider = "openai"
    web_provider = settings.web_provider.strip().lower()
    if web_provider not in PRODUCTION_WEB_PROVIDER_ALIASES:
        web_provider = "brave"
    required_environment = {
        "NSLAB_LLM_PROVIDER": llm_provider,
        "OPENAI_API_KEY": "<required>",
        "NSLAB_WEB_PROVIDER": web_provider,
        settings.brave_search_api_key_env: "<required>",
        "NSLAB_PRICE_PROVIDER": "stock-web",
        "NSLAB_STOCK_WEB_PATH": "<path-to-stock-web-checkout-or-cache>",
        REAL_BUNDLE_ENV_KEY: "<path-to-real-v11-ACCEPT_FULL-bundle>",
    }
    if settings.brave_search_api_key_env != "BRAVE_SEARCH_API_KEY":
        required_environment["NSLAB_BRAVE_SEARCH_API_KEY_ENV"] = (
            settings.brave_search_api_key_env
        )
    return {
        "required_environment": required_environment,
        "commands": [
            f"{python_command} research smoke-bundle --path %NSLAB_REAL_BUNDLE_PATH% --require-valid",
            f"{python_command} research import-bundle %NSLAB_REAL_BUNDLE_PATH% --validate --accept",
            f"{python_command} brain rebuild --mode llm-full",
            f"{python_command} memory rebuild-index --production",
            f"{python_command} warehouse rebuild",
            f"{python_command} warehouse verify",
            f"{python_command} brain audit --deep",
            f"{python_command} training export-sft",
            f"{python_command} training export-preference",
            f"{python_command} training export-evals",
            f"{python_command} training audit",
            f"{python_command} doctor --production",
        ],
    }


def _production_llm_evidence_status(root: Path) -> dict[str, Any]:
    manifest_dir = root / "runs" / "manifests"
    manifest_paths = sorted(manifest_dir.glob("*.json")) if manifest_dir.exists() else []
    unreadable_manifests: list[str] = []
    invalid_manifest_schemas: list[dict[str, Any]] = []
    missing_model_config: list[str] = []
    mock_manifests: list[dict[str, Any]] = []
    missing_prompt_hash_manifests: list[str] = []
    invalid_prompt_hash_manifests: list[dict[str, Any]] = []
    duplicate_prompt_hash_manifests: list[dict[str, Any]] = []
    manifest_prompt_hashes: set[str] = set()
    manifest_prompt_hash_fields: dict[str, set[str]] = {}
    for manifest_path in manifest_paths:
        relative_path = relative_to_root(manifest_path, root)
        try:
            manifest = _read_json_object(manifest_path)
        except ValueError:
            unreadable_manifests.append(relative_path)
            continue
        if manifest.get("schema_version") != "nslab.context_manifest.v1":
            invalid_manifest_schemas.append(
                {
                    "path": relative_path,
                    "run_id": manifest.get("run_id"),
                    "schema_version": manifest.get("schema_version"),
                }
            )
        model_config = manifest.get("model_config")
        if not isinstance(model_config, dict) or not model_config:
            missing_model_config.append(relative_path)
            continue
        mock_values = _mock_model_config_values(model_config)
        if mock_values:
            mock_manifests.append(
                {
                    "path": relative_path,
                    "run_id": manifest.get("run_id"),
                    "mock_values": mock_values,
                }
            )
        prompt_hash_status = _manifest_prompt_hash_status(manifest)
        if prompt_hash_status.values:
            manifest_prompt_hashes.update(prompt_hash_status.values)
            for prompt_hash, fields in prompt_hash_status.fields_by_hash.items():
                manifest_prompt_hash_fields.setdefault(prompt_hash, set()).update(fields)
        else:
            missing_prompt_hash_manifests.append(relative_path)
        if prompt_hash_status.invalid_fields:
            invalid_prompt_hash_manifests.append(
                {
                    "path": relative_path,
                    "run_id": manifest.get("run_id"),
                    "invalid_fields": prompt_hash_status.invalid_fields,
                }
            )
        if prompt_hash_status.duplicate_hashes:
            duplicate_prompt_hash_manifests.append(
                {
                    "path": relative_path,
                    "run_id": manifest.get("run_id"),
                    "duplicate_hashes": prompt_hash_status.duplicate_hashes,
                }
            )

    trace_evidence = _production_llm_trace_evidence_status(
        root,
        manifest_prompt_hashes=manifest_prompt_hashes,
        manifest_prompt_hash_fields=manifest_prompt_hash_fields,
    )

    findings: list[str] = []
    if not manifest_paths:
        findings.append("production LLM context manifest is missing")
    for path in unreadable_manifests:
        findings.append(f"context manifest is unreadable: {path}")
    for manifest in invalid_manifest_schemas:
        findings.append(
            f"context manifest schema_version is invalid in {manifest['path']}: "
            f"{manifest['schema_version']}"
        )
    for path in missing_model_config:
        findings.append(f"context manifest model_config is missing: {path}")
    for manifest in mock_manifests:
        findings.append(
            f"mock LLM model_config present in {manifest['path']}: "
            f"{', '.join(manifest['mock_values'])}"
        )
    for path in missing_prompt_hash_manifests:
        findings.append(f"context manifest prompt_hashes missing or empty: {path}")
    for manifest in invalid_prompt_hash_manifests:
        findings.append(
            f"context manifest prompt_hashes contains invalid entries: "
            f"{manifest['path']} ({len(manifest['invalid_fields'])})"
        )
    for manifest in duplicate_prompt_hash_manifests:
        findings.append(
            f"context manifest prompt_hashes contains duplicate hashes: "
            f"{manifest['path']} ({len(manifest['duplicate_hashes'])})"
        )
    findings.extend(trace_evidence["findings"])

    return {
        "schema_version": "nslab.production_llm_evidence.v1",
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "checked_manifest_count": len(manifest_paths),
        "unreadable_manifest_count": len(unreadable_manifests),
        "unreadable_manifests": unreadable_manifests,
        "invalid_manifest_schema_count": len(invalid_manifest_schemas),
        "invalid_manifest_schemas": invalid_manifest_schemas,
        "missing_model_config_count": len(missing_model_config),
        "missing_model_config_manifests": missing_model_config,
        "mock_model_config_manifest_count": len(mock_manifests),
        "mock_model_config_manifests": mock_manifests,
        "missing_prompt_hash_manifest_count": len(missing_prompt_hash_manifests),
        "missing_prompt_hash_manifests": missing_prompt_hash_manifests,
        "invalid_prompt_hash_manifest_count": len(invalid_prompt_hash_manifests),
        "invalid_prompt_hash_entry_count": sum(
            len(manifest["invalid_fields"])
            for manifest in invalid_prompt_hash_manifests
        ),
        "invalid_prompt_hash_manifests": invalid_prompt_hash_manifests,
        "duplicate_prompt_hash_manifest_count": len(
            duplicate_prompt_hash_manifests
        ),
        "duplicate_prompt_hash_count": sum(
            len(manifest["duplicate_hashes"])
            for manifest in duplicate_prompt_hash_manifests
        ),
        "duplicate_prompt_hash_manifests": duplicate_prompt_hash_manifests,
        "referenced_prompt_hash_count": len(manifest_prompt_hashes),
        "checked_trace_count": trace_evidence["checked_trace_count"],
        "unreadable_trace_count": trace_evidence["unreadable_trace_count"],
        "unreadable_traces": trace_evidence["unreadable_traces"],
        "invalid_trace_schema_count": trace_evidence["invalid_trace_schema_count"],
        "invalid_trace_schemas": trace_evidence["invalid_trace_schemas"],
        "invalid_trace_payload_count": trace_evidence["invalid_trace_payload_count"],
        "invalid_trace_payloads": trace_evidence["invalid_trace_payloads"],
        "missing_trace_prompt_hash_count": trace_evidence[
            "missing_trace_prompt_hash_count"
        ],
        "missing_trace_prompt_hashes": trace_evidence["missing_trace_prompt_hashes"],
        "prompt_hash_purpose_mismatch_count": trace_evidence[
            "prompt_hash_purpose_mismatch_count"
        ],
        "prompt_hash_purpose_mismatches": trace_evidence[
            "prompt_hash_purpose_mismatches"
        ],
        "missing_trace_checkpoint_id_count": trace_evidence[
            "missing_trace_checkpoint_id_count"
        ],
        "missing_trace_checkpoint_id_traces": trace_evidence[
            "missing_trace_checkpoint_id_traces"
        ],
        "mock_trace_count": trace_evidence["mock_trace_count"],
        "mock_traces": trace_evidence["mock_traces"],
        "checked_checkpoint_count": trace_evidence["checked_checkpoint_count"],
        "unreadable_checkpoint_count": trace_evidence["unreadable_checkpoint_count"],
        "unreadable_checkpoints": trace_evidence["unreadable_checkpoints"],
        "invalid_checkpoint_schema_count": trace_evidence[
            "invalid_checkpoint_schema_count"
        ],
        "invalid_checkpoint_schemas": trace_evidence[
            "invalid_checkpoint_schemas"
        ],
        "checkpoint_id_mismatch_count": trace_evidence["checkpoint_id_mismatch_count"],
        "checkpoint_id_mismatches": trace_evidence["checkpoint_id_mismatches"],
        "checkpoint_trace_mismatch_count": trace_evidence[
            "checkpoint_trace_mismatch_count"
        ],
        "checkpoint_trace_mismatches": trace_evidence["checkpoint_trace_mismatches"],
        "mock_checkpoint_count": trace_evidence["mock_checkpoint_count"],
        "mock_checkpoints": trace_evidence["mock_checkpoints"],
    }


def _mock_model_config_values(model_config: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("configured_provider", "provider", "provider_class", "model"):
        value = model_config.get(key)
        if isinstance(value, str) and "mock" in value.strip().lower():
            values.append(f"{key}={value}")
    return values


def _manifest_prompt_hash_status(manifest: dict[str, Any]) -> ManifestPromptHashStatus:
    prompt_hashes = manifest.get("prompt_hashes")
    if not isinstance(prompt_hashes, dict):
        return ManifestPromptHashStatus(
            values=set(),
            invalid_fields=[],
            fields_by_hash={},
            duplicate_hashes={},
        )
    values: set[str] = set()
    invalid_fields: list[str] = []
    fields_by_hash: dict[str, set[str]] = {}
    for key, value in prompt_hashes.items():
        field = str(key)
        if isinstance(value, str) and value:
            values.add(value)
            fields_by_hash.setdefault(value, set()).add(field)
        else:
            invalid_fields.append(field)
    return ManifestPromptHashStatus(
        values=values,
        invalid_fields=sorted(invalid_fields),
        fields_by_hash=fields_by_hash,
        duplicate_hashes={
            prompt_hash: sorted(fields)
            for prompt_hash, fields in fields_by_hash.items()
            if len(fields) > 1
        },
    )


def _production_llm_trace_evidence_status(
    root: Path,
    *,
    manifest_prompt_hashes: set[str],
    manifest_prompt_hash_fields: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    if not manifest_prompt_hashes:
        return {
            "findings": [],
            "checked_trace_count": 0,
            "unreadable_trace_count": 0,
            "unreadable_traces": [],
            "invalid_trace_schema_count": 0,
            "invalid_trace_schemas": [],
            "invalid_trace_payload_count": 0,
            "invalid_trace_payloads": [],
            "missing_trace_prompt_hash_count": 0,
            "missing_trace_prompt_hashes": [],
            "prompt_hash_purpose_mismatch_count": 0,
            "prompt_hash_purpose_mismatches": [],
            "missing_trace_checkpoint_id_count": 0,
            "missing_trace_checkpoint_id_traces": [],
            "mock_trace_count": 0,
            "mock_traces": [],
            "checked_checkpoint_count": 0,
            "unreadable_checkpoint_count": 0,
            "unreadable_checkpoints": [],
            "invalid_checkpoint_schema_count": 0,
            "invalid_checkpoint_schemas": [],
            "checkpoint_id_mismatch_count": 0,
            "checkpoint_id_mismatches": [],
            "checkpoint_trace_mismatch_count": 0,
            "checkpoint_trace_mismatches": [],
            "mock_checkpoint_count": 0,
            "mock_checkpoints": [],
        }

    trace_dir = root / "runs" / "traces"
    trace_paths = sorted(trace_dir.glob("*.json")) if trace_dir.exists() else []
    checked_traces: list[Path] = []
    unreadable_traces: list[str] = []
    invalid_trace_schemas: list[dict[str, Any]] = []
    invalid_trace_payloads: list[dict[str, Any]] = []
    matched_prompt_hashes: set[str] = set()
    prompt_hash_purpose_mismatches: list[dict[str, Any]] = []
    missing_trace_checkpoint_id_traces: list[dict[str, Any]] = []
    mock_traces: list[dict[str, Any]] = []
    checkpoint_ids: set[str] = set()
    checkpoint_trace_refs: dict[str, list[dict[str, Any]]] = {}
    for trace_path in trace_paths:
        try:
            trace = _read_json_object(trace_path)
        except ValueError:
            unreadable_traces.append(relative_to_root(trace_path, root))
            continue
        prompt_hash = _trace_prompt_hash(trace)
        if prompt_hash not in manifest_prompt_hashes:
            continue
        matched_prompt_hashes.add(prompt_hash)
        checked_traces.append(trace_path)
        expected_purposes = _expected_llm_trace_purposes(
            manifest_prompt_hash_fields.get(prompt_hash, set())
            if manifest_prompt_hash_fields is not None
            else set()
        )
        trace_purpose = trace.get("purpose")
        if expected_purposes and trace_purpose not in expected_purposes:
            prompt_hash_purpose_mismatches.append(
                {
                    "path": relative_to_root(trace_path, root),
                    "trace_id": trace.get("trace_id"),
                    "prompt_sha256": prompt_hash,
                    "expected_purposes": sorted(expected_purposes),
                    "observed_purpose": trace_purpose,
                }
            )
        if trace.get("schema_version") != "nslab.llm_trace.v1":
            invalid_trace_schemas.append(
                {
                    "path": relative_to_root(trace_path, root),
                    "trace_id": trace.get("trace_id"),
                    "purpose": trace.get("purpose"),
                    "prompt_sha256": prompt_hash,
                    "schema_version": trace.get("schema_version"),
                }
            )
        trace_payload_mismatches = _llm_trace_payload_mismatches(trace)
        if trace_payload_mismatches:
            invalid_trace_payloads.append(
                {
                    "path": relative_to_root(trace_path, root),
                    "trace_id": trace.get("trace_id"),
                    "purpose": trace.get("purpose"),
                    "prompt_sha256": prompt_hash,
                    "mismatched_fields": trace_payload_mismatches,
                }
            )
        checkpoint_id = trace.get("checkpoint_id")
        if isinstance(checkpoint_id, str) and checkpoint_id:
            checkpoint_ids.add(checkpoint_id)
            checkpoint_trace_refs.setdefault(checkpoint_id, []).append(
                {
                    "path": relative_to_root(trace_path, root),
                    "trace_id": trace.get("trace_id"),
                    "status": trace.get("status"),
                    "purpose": trace.get("purpose"),
                    "prompt_sha256": prompt_hash,
                    "operation": trace.get("operation"),
                    "provider": trace.get("provider"),
                    "model_config": trace.get("model_config"),
                    "metadata": trace.get("metadata"),
                    "top_level_metadata": _trace_top_level_metadata(trace),
                    "input": trace.get("input"),
                    "input_sha256": trace.get("input_sha256"),
                    "output": trace.get("output"),
                    "output_sha256": trace.get("output_sha256"),
                    "token_usage": trace.get("token_usage"),
                    "retries": trace.get("retries"),
                    "retry_errors": trace.get("retry_errors"),
                    "error": trace.get("error"),
                }
            )
        else:
            missing_trace_checkpoint_id_traces.append(
                {
                    "path": relative_to_root(trace_path, root),
                    "trace_id": trace.get("trace_id"),
                    "purpose": trace.get("purpose"),
                    "prompt_sha256": prompt_hash,
                }
            )
        mock_values = _mock_llm_artifact_values(trace)
        if mock_values:
            mock_traces.append(
                {
                    "path": relative_to_root(trace_path, root),
                    "trace_id": trace.get("trace_id"),
                    "purpose": trace.get("purpose"),
                    "prompt_sha256": prompt_hash,
                    "mock_values": mock_values,
                }
            )

    missing_trace_prompt_hashes = sorted(manifest_prompt_hashes - matched_prompt_hashes)
    checkpoint_root = root / "runs" / "checkpoints" / "llm"
    checked_checkpoints: list[Path] = []
    unreadable_checkpoints: list[str] = []
    invalid_checkpoint_schemas: list[dict[str, Any]] = []
    checkpoint_id_mismatches: list[dict[str, Any]] = []
    checkpoint_trace_mismatches: list[dict[str, Any]] = []
    mock_checkpoints: list[dict[str, Any]] = []
    for checkpoint_id in sorted(checkpoint_ids):
        if checkpoint_id != Path(checkpoint_id).name:
            unreadable_checkpoints.append(checkpoint_id)
            continue
        checkpoint_path = checkpoint_root / f"{checkpoint_id}.json"
        if not checkpoint_path.exists():
            unreadable_checkpoints.append(relative_to_root(checkpoint_path, root))
            continue
        try:
            checkpoint = _read_json_object(checkpoint_path)
        except ValueError:
            unreadable_checkpoints.append(relative_to_root(checkpoint_path, root))
            continue
        checked_checkpoints.append(checkpoint_path)
        if checkpoint.get("schema_version") != "nslab.llm_checkpoint.v1":
            invalid_checkpoint_schemas.append(
                {
                    "path": relative_to_root(checkpoint_path, root),
                    "checkpoint_id": checkpoint.get("checkpoint_id"),
                    "schema_version": checkpoint.get("schema_version"),
                }
            )
        observed_checkpoint_id = checkpoint.get("checkpoint_id")
        if observed_checkpoint_id != checkpoint_id:
            checkpoint_id_mismatches.append(
                {
                    "path": relative_to_root(checkpoint_path, root),
                    "expected_checkpoint_id": checkpoint_id,
                    "observed_checkpoint_id": observed_checkpoint_id,
                }
            )
        for trace_ref in checkpoint_trace_refs.get(checkpoint_id, []):
            for field in (
                "operation",
                "purpose",
                "status",
                "provider",
                "model_config",
                "metadata",
                "input",
                "input_sha256",
                "output",
                "output_sha256",
                "token_usage",
                "retries",
                "retry_errors",
                "error",
            ):
                if _checkpoint_trace_field_compatible(checkpoint, trace_ref, field):
                    continue
                checkpoint_trace_mismatches.append(
                    {
                        "path": relative_to_root(checkpoint_path, root),
                        "checkpoint_id": checkpoint_id,
                        "trace": trace_ref["path"],
                        "trace_id": trace_ref.get("trace_id"),
                        "field": field,
                        "trace_value": _expected_checkpoint_trace_value(
                            trace_ref, field
                        ),
                        "checkpoint_value": checkpoint.get(field),
                    }
                )
        mock_values = _mock_llm_artifact_values(checkpoint)
        if mock_values:
            mock_checkpoints.append(
                {
                    "path": relative_to_root(checkpoint_path, root),
                    "checkpoint_id": checkpoint.get("checkpoint_id"),
                    "purpose": checkpoint.get("purpose"),
                    "mock_values": mock_values,
                }
            )

    findings: list[str] = []
    for path in unreadable_traces:
        findings.append(f"referenced LLM trace is unreadable: {path}")
    for trace in invalid_trace_schemas:
        findings.append(
            f"referenced LLM trace schema_version is invalid in {trace['path']}: "
            f"{trace['schema_version']}"
        )
    for trace in invalid_trace_payloads:
        findings.append(
            f"referenced LLM trace payload contract mismatch in {trace['path']}: "
            f"{', '.join(trace['mismatched_fields'])}"
        )
    for prompt_hash in missing_trace_prompt_hashes:
        findings.append(f"referenced LLM prompt hash has no matching trace: {prompt_hash}")
    for mismatch in prompt_hash_purpose_mismatches:
        findings.append(
            f"referenced LLM trace purpose does not match manifest prompt_hashes: "
            f"{mismatch['path']}"
        )
    for trace in missing_trace_checkpoint_id_traces:
        findings.append(f"referenced LLM trace missing checkpoint_id: {trace['path']}")
    for trace in mock_traces:
        findings.append(
            f"mock LLM trace present in {trace['path']}: "
            f"{', '.join(trace['mock_values'])}"
        )
    for path in unreadable_checkpoints:
        findings.append(f"referenced LLM checkpoint is unreadable: {path}")
    for checkpoint in invalid_checkpoint_schemas:
        findings.append(
            f"referenced LLM checkpoint schema_version is invalid in "
            f"{checkpoint['path']}: {checkpoint['schema_version']}"
        )
    for mismatch in checkpoint_id_mismatches:
        findings.append(
            f"referenced LLM checkpoint_id mismatch in {mismatch['path']}: "
            f"expected {mismatch['expected_checkpoint_id']} observed "
            f"{mismatch['observed_checkpoint_id']}"
        )
    for mismatch in checkpoint_trace_mismatches:
        findings.append(
            f"referenced LLM checkpoint trace mismatch in {mismatch['path']}: "
            f"{mismatch['field']} differs from {mismatch['trace']}"
        )
    for checkpoint in mock_checkpoints:
        findings.append(
            f"mock LLM checkpoint present in {checkpoint['path']}: "
            f"{', '.join(checkpoint['mock_values'])}"
        )

    return {
        "findings": findings,
        "checked_trace_count": len(checked_traces),
        "unreadable_trace_count": len(unreadable_traces),
        "unreadable_traces": unreadable_traces,
        "invalid_trace_schema_count": len(invalid_trace_schemas),
        "invalid_trace_schemas": invalid_trace_schemas,
        "invalid_trace_payload_count": len(invalid_trace_payloads),
        "invalid_trace_payloads": invalid_trace_payloads,
        "missing_trace_prompt_hash_count": len(missing_trace_prompt_hashes),
        "missing_trace_prompt_hashes": missing_trace_prompt_hashes,
        "prompt_hash_purpose_mismatch_count": len(prompt_hash_purpose_mismatches),
        "prompt_hash_purpose_mismatches": prompt_hash_purpose_mismatches,
        "missing_trace_checkpoint_id_count": len(missing_trace_checkpoint_id_traces),
        "missing_trace_checkpoint_id_traces": missing_trace_checkpoint_id_traces,
        "mock_trace_count": len(mock_traces),
        "mock_traces": mock_traces,
        "checked_checkpoint_count": len(checked_checkpoints),
        "unreadable_checkpoint_count": len(unreadable_checkpoints),
        "unreadable_checkpoints": unreadable_checkpoints,
        "invalid_checkpoint_schema_count": len(invalid_checkpoint_schemas),
        "invalid_checkpoint_schemas": invalid_checkpoint_schemas,
        "checkpoint_id_mismatch_count": len(checkpoint_id_mismatches),
        "checkpoint_id_mismatches": checkpoint_id_mismatches,
        "checkpoint_trace_mismatch_count": len(checkpoint_trace_mismatches),
        "checkpoint_trace_mismatches": checkpoint_trace_mismatches,
        "mock_checkpoint_count": len(mock_checkpoints),
        "mock_checkpoints": mock_checkpoints,
    }


def _llm_trace_payload_mismatches(trace: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    trace_input = trace.get("input")
    if isinstance(trace_input, dict) and "input_sha256" not in trace:
        mismatches.append("input_sha256_missing")
    elif isinstance(trace_input, dict):
        expected_input_hash = sha256_text(canonical_json(trace_input))
        if trace.get("input_sha256") != expected_input_hash:
            mismatches.append("input_sha256")
    if "output" in trace and "output_sha256" not in trace:
        mismatches.append("output_sha256_missing")
    elif "output_sha256" in trace:
        trace_output = trace.get("output")
        expected_output_hash = (
            sha256_text(canonical_json(trace_output))
            if trace_output is not None
            else None
        )
        if trace.get("output_sha256") != expected_output_hash:
            mismatches.append("output_sha256")
    status = trace.get("status")
    token_usage = trace.get("token_usage")
    if status in {"ok", "checkpoint_hit"}:
        if not isinstance(token_usage, dict):
            mismatches.append("token_usage_missing")
        else:
            prompt_tokens = token_usage.get("prompt_tokens_estimate")
            if not isinstance(prompt_tokens, int) or isinstance(prompt_tokens, bool):
                mismatches.append("prompt_tokens_estimate_missing")
            operation = trace.get("operation")
            completion_tokens = token_usage.get("completion_tokens_estimate")
            if operation in {"generate_text", "generate_structured"} and (
                not isinstance(completion_tokens, int)
                or isinstance(completion_tokens, bool)
            ):
                mismatches.append("completion_tokens_estimate_missing")
    return mismatches


def _expected_checkpoint_trace_value(trace_ref: dict[str, Any], field: str) -> Any:
    value = trace_ref.get(field)
    if field == "status" and value == "checkpoint_hit":
        return "ok"
    return value


def _expected_llm_trace_purposes(fields: set[str]) -> set[str]:
    expected: set[str] = set()
    for field in fields:
        expected.add(field)
        alias = PRODUCTION_LLM_PROMPT_PURPOSE_ALIASES.get(field)
        if alias:
            expected.add(alias)
    return expected


def _trace_top_level_metadata(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in trace.items()
        if key not in LLM_TRACE_CORE_FIELDS
    }


def _checkpoint_trace_field_compatible(
    checkpoint: dict[str, Any],
    trace_ref: dict[str, Any],
    field: str,
) -> bool:
    trace_status = trace_ref.get("status")
    if field == "metadata":
        return _checkpoint_metadata_compatible(checkpoint, trace_ref)
    if trace_status == "checkpoint_hit" and field in {
        "token_usage",
        "retries",
        "retry_errors",
    }:
        return True
    matches = checkpoint.get(field) == _expected_checkpoint_trace_value(trace_ref, field)
    return bool(matches)


def _checkpoint_metadata_compatible(
    checkpoint: dict[str, Any],
    trace_ref: dict[str, Any],
) -> bool:
    checkpoint_metadata = checkpoint.get("metadata")
    trace_metadata = trace_ref.get("metadata")
    if isinstance(trace_metadata, dict):
        return checkpoint_metadata == trace_metadata
    if not isinstance(checkpoint_metadata, dict):
        return checkpoint_metadata in (None, {})
    top_level_metadata = trace_ref.get("top_level_metadata")
    if not isinstance(top_level_metadata, dict):
        return False
    return all(
        top_level_metadata.get(key) == value
        for key, value in checkpoint_metadata.items()
    )


def _trace_prompt_hash(trace: dict[str, Any]) -> str | None:
    trace_input = trace.get("input")
    if not isinstance(trace_input, dict):
        return None
    prompt_hash = trace_input.get("prompt_sha256")
    return prompt_hash if isinstance(prompt_hash, str) and prompt_hash else None


def _mock_llm_artifact_values(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    provider = payload.get("provider")
    if isinstance(provider, str) and "mock" in provider.strip().lower():
        values.append(f"provider={provider}")
    model_config = payload.get("model_config")
    if isinstance(model_config, dict):
        values.extend(_mock_model_config_values(model_config))
    return _unique_preserving_order(values)


def _production_web_evidence_status(root: Path) -> dict[str, Any]:
    manifest_dir = root / "runs" / "manifests"
    manifest_paths = sorted(manifest_dir.glob("*.json")) if manifest_dir.exists() else []
    artifact_paths: set[Path] = set()
    unreadable_manifests: list[str] = []
    invalid_manifest_schemas: list[dict[str, Any]] = []
    invalid_artifact_refs: list[dict[str, Any]] = []
    missing_artifacts: list[dict[str, Any]] = []
    missing_artifact_hashes: list[dict[str, Any]] = []
    artifact_hash_mismatches: list[dict[str, Any]] = []
    artifact_source_id_mismatches: list[dict[str, Any]] = []
    artifact_missing_source_ids: list[dict[str, Any]] = []
    artifact_cutoff_missing: list[dict[str, Any]] = []
    artifact_cutoff_failed: list[dict[str, Any]] = []
    artifact_cutoff_after: list[dict[str, Any]] = []
    artifact_cutoff_invalid_timestamps: list[dict[str, Any]] = []
    checked_artifact_refs = 0
    for manifest_path in manifest_paths:
        manifest_relative_path = relative_to_root(manifest_path, root)
        try:
            manifest = _read_json_object(manifest_path)
        except ValueError:
            unreadable_manifests.append(manifest_relative_path)
            continue
        if manifest.get("schema_version") != "nslab.context_manifest.v1":
            invalid_manifest_schemas.append(
                {
                    "path": manifest_relative_path,
                    "run_id": manifest.get("run_id"),
                    "schema_version": manifest.get("schema_version"),
                }
            )
        for field in PRODUCTION_WEB_EVIDENCE_ARTIFACT_FIELDS:
            artifact_ref = manifest.get(field)
            if not isinstance(artifact_ref, str) or not artifact_ref:
                continue
            checked_artifact_refs += 1
            artifact_path = _project_relative_artifact_path(root, artifact_ref)
            if artifact_path is None:
                invalid_artifact_refs.append(
                    {
                        "manifest": manifest_relative_path,
                        "artifact_field": field,
                        "artifact": artifact_ref,
                    }
                )
                continue
            relative_artifact_path = relative_to_root(artifact_path, root)
            if not artifact_path.exists() or not artifact_path.is_file():
                missing_artifacts.append(
                    {
                        "manifest": manifest_relative_path,
                        "artifact_field": field,
                        "artifact": relative_artifact_path,
                    }
                )
                continue
            source_field = PRODUCTION_WEB_EVIDENCE_ARTIFACT_SOURCE_FIELDS.get(field)
            if source_field is not None:
                expected_source_ids = _string_list(manifest.get(source_field))
                try:
                    source_id_status = _web_evidence_artifact_source_id_status(
                        artifact_path
                    )
                except OSError:
                    source_id_status = WebEvidenceSourceIdStatus(
                        source_ids=[],
                        row_count=0,
                        missing_source_id_count=0,
                    )
                observed_source_ids = source_id_status.source_ids
                if source_id_status.missing_source_id_count:
                    artifact_missing_source_ids.append(
                        {
                            "manifest": manifest_relative_path,
                            "artifact_field": field,
                            "source_field": source_field,
                            "artifact": relative_artifact_path,
                            "row_count": source_id_status.row_count,
                            "missing_source_id_count": (
                                source_id_status.missing_source_id_count
                            ),
                        }
                    )
                if (
                    set(observed_source_ids) != set(expected_source_ids)
                    or len(observed_source_ids) != len(set(observed_source_ids))
                    or len(expected_source_ids) != len(set(expected_source_ids))
                ):
                    artifact_source_id_mismatches.append(
                        {
                            "manifest": manifest_relative_path,
                            "artifact_field": field,
                            "source_field": source_field,
                            "artifact": relative_artifact_path,
                            "expected_source_ids": expected_source_ids,
                            "observed_source_ids": observed_source_ids,
                        }
                    )
            artifact_paths.add(artifact_path)
            if field in PRODUCTION_WEB_EVIDENCE_CUTOFF_SAFE_ARTIFACT_FIELDS:
                try:
                    cutoff_status = _web_evidence_artifact_cutoff_status(artifact_path)
                except OSError:
                    cutoff_status = WebEvidenceCutoffStatus(
                        checked_row_count=0,
                        missing_verification_count=0,
                        failed_verification_count=0,
                        after_cutoff_count=0,
                        invalid_timestamp_count=0,
                    )
                if cutoff_status.missing_verification_count:
                    artifact_cutoff_missing.append(
                        {
                            "manifest": manifest_relative_path,
                            "artifact_field": field,
                            "artifact": relative_artifact_path,
                            "checked_row_count": cutoff_status.checked_row_count,
                            "missing_verification_count": (
                                cutoff_status.missing_verification_count
                            ),
                        }
                    )
                if cutoff_status.failed_verification_count:
                    artifact_cutoff_failed.append(
                        {
                            "manifest": manifest_relative_path,
                            "artifact_field": field,
                            "artifact": relative_artifact_path,
                            "checked_row_count": cutoff_status.checked_row_count,
                            "failed_verification_count": (
                                cutoff_status.failed_verification_count
                            ),
                        }
                    )
                if cutoff_status.after_cutoff_count:
                    artifact_cutoff_after.append(
                        {
                            "manifest": manifest_relative_path,
                            "artifact_field": field,
                            "artifact": relative_artifact_path,
                            "checked_row_count": cutoff_status.checked_row_count,
                            "after_cutoff_count": cutoff_status.after_cutoff_count,
                        }
                    )
                if cutoff_status.invalid_timestamp_count:
                    artifact_cutoff_invalid_timestamps.append(
                        {
                            "manifest": manifest_relative_path,
                            "artifact_field": field,
                            "artifact": relative_artifact_path,
                            "checked_row_count": cutoff_status.checked_row_count,
                            "invalid_timestamp_count": (
                                cutoff_status.invalid_timestamp_count
                            ),
                        }
                    )
            sha_field = PRODUCTION_WEB_EVIDENCE_ARTIFACT_SHA_FIELDS[field]
            expected_sha = manifest.get(sha_field)
            if not isinstance(expected_sha, str) or not expected_sha:
                missing_artifact_hashes.append(
                    {
                        "manifest": manifest_relative_path,
                        "artifact_field": field,
                        "sha_field": sha_field,
                        "artifact": relative_artifact_path,
                    }
                )
                continue
            observed_sha = _text_artifact_sha256(artifact_path)
            if observed_sha != expected_sha:
                artifact_hash_mismatches.append(
                    {
                        "manifest": manifest_relative_path,
                        "artifact_field": field,
                        "sha_field": sha_field,
                        "artifact": relative_artifact_path,
                        "expected_sha256": expected_sha,
                        "observed_sha256": observed_sha,
                    }
                )

    mock_artifacts: list[dict[str, Any]] = []
    placeholder_artifacts: list[dict[str, Any]] = []
    empty_artifacts: list[str] = []
    invalid_json_artifacts: list[dict[str, Any]] = []
    unreadable_artifacts: list[str] = []
    artifact_record_counts: dict[str, int] = {}
    for artifact_path in sorted(artifact_paths):
        relative_artifact_path = relative_to_root(artifact_path, root)
        try:
            counts = _web_evidence_artifact_counts(artifact_path)
        except OSError:
            unreadable_artifacts.append(relative_artifact_path)
            continue
        artifact_record_counts[relative_artifact_path] = counts.row_count
        if counts.row_count == 0:
            empty_artifacts.append(relative_artifact_path)
        if counts.invalid_json_count:
            invalid_json_artifacts.append(
                {
                    "path": relative_artifact_path,
                    "invalid_json_count": counts.invalid_json_count,
                }
            )
        if counts.mock_url_count or counts.mock_metadata_count:
            mock_artifacts.append(
                {
                    "path": relative_artifact_path,
                    "mock_url_count": counts.mock_url_count,
                    "mock_metadata_count": counts.mock_metadata_count,
                    "sample_values": counts.mock_sample_values[:5],
                }
            )
        if counts.placeholder_url_count:
            placeholder_artifacts.append(
                {
                    "path": relative_artifact_path,
                    "placeholder_url_count": counts.placeholder_url_count,
                    "sample_values": counts.placeholder_sample_values[:5],
                }
            )

    findings: list[str] = []
    if not manifest_paths:
        findings.append("production web context manifest is missing")
    elif checked_artifact_refs == 0:
        findings.append("production web evidence artifact reference is missing")
    for path in unreadable_manifests:
        findings.append(f"context manifest is unreadable: {path}")
    for manifest in invalid_manifest_schemas:
        findings.append(
            f"context manifest schema_version is invalid in {manifest['path']}: "
            f"{manifest['schema_version']}"
        )
    for ref in invalid_artifact_refs:
        findings.append(
            "web evidence artifact reference is invalid: "
            f"{ref['manifest']} {ref['artifact_field']}={ref['artifact']}"
        )
    for artifact in missing_artifacts:
        findings.append(
            f"web evidence artifact is missing: {artifact['manifest']} "
            f"{artifact['artifact_field']}={artifact['artifact']}"
        )
    for artifact in missing_artifact_hashes:
        findings.append(
            f"web evidence artifact hash is missing: {artifact['manifest']} "
            f"{artifact['sha_field']} for {artifact['artifact']}"
        )
    for artifact in artifact_hash_mismatches:
        findings.append(
            f"web evidence artifact sha256 mismatch: {artifact['manifest']} "
            f"{artifact['sha_field']} for {artifact['artifact']}"
        )
    for artifact in artifact_source_id_mismatches:
        findings.append(
            f"web evidence artifact source IDs do not match manifest: "
            f"{artifact['manifest']} {artifact['artifact_field']} -> "
            f"{artifact['source_field']}"
        )
    for artifact in artifact_missing_source_ids:
        findings.append(
            f"web evidence artifact has rows without source IDs: "
            f"{artifact['artifact']} ({artifact['missing_source_id_count']})"
        )
    for artifact in artifact_cutoff_missing:
        findings.append(
            f"web evidence artifact has rows without cutoff verification: "
            f"{artifact['artifact']} ({artifact['missing_verification_count']})"
        )
    for artifact in artifact_cutoff_failed:
        findings.append(
            f"web evidence artifact has cutoff verification failures: "
            f"{artifact['artifact']} ({artifact['failed_verification_count']})"
        )
    for artifact in artifact_cutoff_after:
        findings.append(
            f"web evidence artifact has rows after cutoff: "
            f"{artifact['artifact']} ({artifact['after_cutoff_count']})"
        )
    for artifact in artifact_cutoff_invalid_timestamps:
        findings.append(
            f"web evidence artifact has invalid cutoff timestamps: "
            f"{artifact['artifact']} ({artifact['invalid_timestamp_count']})"
        )
    for path in unreadable_artifacts:
        findings.append(f"web evidence artifact is unreadable: {path}")
    for artifact in invalid_json_artifacts:
        findings.append(
            f"web evidence artifact contains invalid JSON: {artifact['path']} "
            f"({artifact['invalid_json_count']})"
        )
    for path in empty_artifacts:
        findings.append(f"web evidence artifact has no evidence rows: {path}")
    for artifact in mock_artifacts:
        mock_url_count = int(artifact["mock_url_count"])
        mock_metadata_count = int(artifact["mock_metadata_count"])
        if mock_url_count:
            findings.append(
                f"mock web source URLs present in {artifact['path']} "
                f"({mock_url_count})"
            )
        if mock_metadata_count:
            findings.append(
                f"mock web provider metadata present in {artifact['path']} "
                f"({mock_metadata_count})"
            )
    for artifact in placeholder_artifacts:
        placeholder_url_count = int(artifact["placeholder_url_count"])
        findings.append(
            f"placeholder web source URLs present in {artifact['path']} "
            f"({placeholder_url_count})"
        )

    return {
        "schema_version": "nslab.production_web_evidence.v1",
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "checked_manifest_count": len(manifest_paths),
        "checked_artifact_reference_count": checked_artifact_refs,
        "checked_artifact_count": len(artifact_paths),
        "unreadable_manifest_count": len(unreadable_manifests),
        "unreadable_manifests": unreadable_manifests,
        "invalid_manifest_schema_count": len(invalid_manifest_schemas),
        "invalid_manifest_schemas": invalid_manifest_schemas,
        "invalid_artifact_ref_count": len(invalid_artifact_refs),
        "invalid_artifact_refs": invalid_artifact_refs,
        "missing_artifact_count": len(missing_artifacts),
        "missing_artifacts": missing_artifacts,
        "missing_artifact_hash_count": len(missing_artifact_hashes),
        "missing_artifact_hashes": missing_artifact_hashes,
        "artifact_sha256_mismatch_count": len(artifact_hash_mismatches),
        "artifact_sha256_mismatches": artifact_hash_mismatches,
        "artifact_source_id_mismatch_count": len(artifact_source_id_mismatches),
        "artifact_source_id_mismatches": artifact_source_id_mismatches,
        "artifact_missing_source_id_count": sum(
            int(artifact["missing_source_id_count"])
            for artifact in artifact_missing_source_ids
        ),
        "artifact_missing_source_id_artifacts": artifact_missing_source_ids,
        "artifact_cutoff_missing_count": sum(
            int(artifact["missing_verification_count"])
            for artifact in artifact_cutoff_missing
        ),
        "artifact_cutoff_missing_artifacts": artifact_cutoff_missing,
        "artifact_cutoff_failed_count": sum(
            int(artifact["failed_verification_count"])
            for artifact in artifact_cutoff_failed
        ),
        "artifact_cutoff_failed_artifacts": artifact_cutoff_failed,
        "artifact_cutoff_after_count": sum(
            int(artifact["after_cutoff_count"]) for artifact in artifact_cutoff_after
        ),
        "artifact_cutoff_after_artifacts": artifact_cutoff_after,
        "artifact_cutoff_invalid_timestamp_count": sum(
            int(artifact["invalid_timestamp_count"])
            for artifact in artifact_cutoff_invalid_timestamps
        ),
        "artifact_cutoff_invalid_timestamp_artifacts": (
            artifact_cutoff_invalid_timestamps
        ),
        "unreadable_artifact_count": len(unreadable_artifacts),
        "unreadable_artifacts": unreadable_artifacts,
        "invalid_artifact_json_count": sum(
            int(artifact["invalid_json_count"]) for artifact in invalid_json_artifacts
        ),
        "invalid_artifact_json_artifacts": invalid_json_artifacts,
        "empty_artifact_count": len(empty_artifacts),
        "empty_artifacts": empty_artifacts,
        "checked_artifact_record_count": sum(artifact_record_counts.values()),
        "artifact_record_counts": artifact_record_counts,
        "mock_web_artifact_count": len(mock_artifacts),
        "mock_web_url_count": sum(
            int(artifact["mock_url_count"]) for artifact in mock_artifacts
        ),
        "mock_web_metadata_count": sum(
            int(artifact["mock_metadata_count"]) for artifact in mock_artifacts
        ),
        "mock_web_evidence_count": sum(
            int(artifact["mock_url_count"]) + int(artifact["mock_metadata_count"])
            for artifact in mock_artifacts
        ),
        "mock_web_artifacts": mock_artifacts,
        "placeholder_web_artifact_count": len(placeholder_artifacts),
        "placeholder_web_url_count": sum(
            int(artifact["placeholder_url_count"])
            for artifact in placeholder_artifacts
        ),
        "placeholder_web_artifacts": placeholder_artifacts,
    }


def _text_artifact_sha256(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8", errors="replace"))


def _project_relative_artifact_path(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return None
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _web_evidence_artifact_counts(
    path: Path,
) -> WebEvidenceArtifactCounts:
    if path.suffix == ".jsonl":
        row_count = 0
        invalid_json_count = 0
        mock_url_count = 0
        metadata_count = 0
        placeholder_url_count = 0
        mock_samples: list[str] = []
        placeholder_samples: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row_count += 1
            try:
                payload: object = json.loads(line)
            except json.JSONDecodeError:
                invalid_json_count += 1
                payload = line
            url_values = _mock_url_values(payload)
            metadata_values = _mock_provider_metadata_values(payload)
            placeholder_values = _placeholder_web_url_values(payload)
            mock_url_count += len(url_values)
            metadata_count += len(metadata_values)
            placeholder_url_count += len(placeholder_values)
            mock_samples.extend(url_values)
            mock_samples.extend(metadata_values)
            placeholder_samples.extend(placeholder_values)
        return WebEvidenceArtifactCounts(
            row_count=row_count,
            invalid_json_count=invalid_json_count,
            mock_url_count=mock_url_count,
            mock_metadata_count=metadata_count,
            placeholder_url_count=placeholder_url_count,
            mock_sample_values=_unique_preserving_order(mock_samples),
            placeholder_sample_values=_unique_preserving_order(placeholder_samples),
        )
    if path.suffix == ".json":
        text = path.read_text(encoding="utf-8-sig")
        row_count = 1 if text.strip() else 0
        invalid_json_count = 0
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            invalid_json_count = 1 if text.strip() else 0
            payload = text
        url_values = _mock_url_values(payload)
        metadata_values = _mock_provider_metadata_values(payload)
        placeholder_values = _placeholder_web_url_values(payload)
        return WebEvidenceArtifactCounts(
            row_count=row_count,
            invalid_json_count=invalid_json_count,
            mock_url_count=len(url_values),
            mock_metadata_count=len(metadata_values),
            placeholder_url_count=len(placeholder_values),
            mock_sample_values=_unique_preserving_order([*url_values, *metadata_values]),
            placeholder_sample_values=_unique_preserving_order(placeholder_values),
        )
    text = path.read_text(encoding="utf-8")
    values = text.split()
    mock_values = [value for value in values if "mock://" in value]
    placeholder_values = [
        value for value in values if _is_placeholder_web_url(value)
    ]
    row_count = 1 if text.strip() else 0
    return WebEvidenceArtifactCounts(
        row_count=row_count,
        invalid_json_count=0,
        mock_url_count=len(mock_values),
        mock_metadata_count=0,
        placeholder_url_count=len(placeholder_values),
        mock_sample_values=_unique_preserving_order(mock_values),
        placeholder_sample_values=_unique_preserving_order(placeholder_values),
    )


def _web_evidence_artifact_source_id_status(path: Path) -> WebEvidenceSourceIdStatus:
    if path.suffix == ".jsonl":
        source_ids: list[str] = []
        row_count = 0
        missing_source_id_count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row_count += 1
            try:
                payload: object = json.loads(line)
            except json.JSONDecodeError:
                missing_source_id_count += 1
                continue
            source_id = _top_level_source_id(payload)
            if source_id is not None:
                source_ids.append(source_id)
            else:
                missing_source_id_count += 1
        return WebEvidenceSourceIdStatus(
            source_ids=source_ids,
            row_count=row_count,
            missing_source_id_count=missing_source_id_count,
        )
    if path.suffix == ".json":
        text = path.read_text(encoding="utf-8-sig")
        row_count = 1 if text.strip() else 0
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return WebEvidenceSourceIdStatus(
                source_ids=[],
                row_count=row_count,
                missing_source_id_count=row_count,
            )
        source_ids = _source_ids_from_payload(payload)
        return WebEvidenceSourceIdStatus(
            source_ids=source_ids,
            row_count=row_count,
            missing_source_id_count=0 if source_ids or row_count == 0 else 1,
        )
    return WebEvidenceSourceIdStatus(
        source_ids=[],
        row_count=0,
        missing_source_id_count=0,
    )


def _web_evidence_artifact_cutoff_status(path: Path) -> WebEvidenceCutoffStatus:
    checked_row_count = 0
    missing_verification_count = 0
    failed_verification_count = 0
    after_cutoff_count = 0
    invalid_timestamp_count = 0
    for payload in _web_evidence_artifact_payloads(path):
        for row in _web_evidence_rows(payload):
            checked_row_count += 1
            has_time_verified = "time_verified" in row
            has_available_before_cutoff = "available_before_cutoff" in row
            if not has_time_verified and not has_available_before_cutoff:
                missing_verification_count += 1
            elif (
                (has_time_verified and row.get("time_verified") is not True)
                or (
                    has_available_before_cutoff
                    and row.get("available_before_cutoff") is not True
                )
            ):
                failed_verification_count += 1
            timestamp_status = _web_evidence_row_timestamp_status(row)
            if timestamp_status == "after_cutoff":
                after_cutoff_count += 1
            elif timestamp_status == "invalid":
                invalid_timestamp_count += 1
    return WebEvidenceCutoffStatus(
        checked_row_count=checked_row_count,
        missing_verification_count=missing_verification_count,
        failed_verification_count=failed_verification_count,
        after_cutoff_count=after_cutoff_count,
        invalid_timestamp_count=invalid_timestamp_count,
    )


def _web_evidence_artifact_payloads(path: Path) -> list[object]:
    if path.suffix == ".jsonl":
        payloads: list[object] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return payloads
    if path.suffix == ".json":
        text = path.read_text(encoding="utf-8-sig")
        if not text.strip():
            return []
        try:
            return [json.loads(text)]
        except json.JSONDecodeError:
            return []
    return []


def _web_evidence_rows(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows: list[dict[str, Any]] = []
        if _is_web_evidence_row(value):
            rows.append(value)
        for item in value.values():
            rows.extend(_web_evidence_rows(item))
        return rows
    if isinstance(value, list):
        rows = []
        for item in value:
            rows.extend(_web_evidence_rows(item))
        return rows
    return []


def _is_web_evidence_row(value: dict[str, Any]) -> bool:
    return isinstance(value.get("source_id"), str) and any(
        key in value
        for key in (
            "source_url",
            "url",
            "published_at",
            "time_verified",
            "available_before_cutoff",
        )
    )


def _web_evidence_row_timestamp_status(row: dict[str, Any]) -> str:
    raw_published_at = row.get("published_at")
    raw_cutoff_at = row.get("cutoff_at")
    if raw_published_at is None and raw_cutoff_at is None:
        return "not_applicable"
    if not isinstance(raw_published_at, str) or not isinstance(raw_cutoff_at, str):
        return "not_applicable"
    try:
        published_at = parse_datetime(raw_published_at)
        cutoff_at = parse_datetime(raw_cutoff_at)
    except ValueError:
        return "invalid"
    return "ok" if is_available_as_of(published_at, cutoff_at) else "after_cutoff"


def _top_level_source_id(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    source_id = value.get("source_id")
    return source_id if isinstance(source_id, str) and source_id else None


def _source_ids_from_payload(value: object) -> list[str]:
    if isinstance(value, dict):
        source_ids: list[str] = []
        source_id = value.get("source_id")
        if isinstance(source_id, str) and source_id:
            source_ids.append(source_id)
        for item in value.values():
            source_ids.extend(_source_ids_from_payload(item))
        return _unique_preserving_order(source_ids)
    if isinstance(value, list):
        list_source_ids: list[str] = []
        for item in value:
            list_source_ids.extend(_source_ids_from_payload(item))
        return _unique_preserving_order(list_source_ids)
    return []


def _mock_url_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if "mock://" in value else []
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_mock_url_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_mock_url_values(item))
        return values
    return []


def _placeholder_web_url_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if _is_placeholder_web_url(value) else []
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_placeholder_web_url_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_placeholder_web_url_values(item))
        return values
    return []


def _is_placeholder_web_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    host = parsed.hostname
    if not host:
        return False
    normalized_host = host.rstrip(".").lower()
    if normalized_host in PLACEHOLDER_WEB_EVIDENCE_HOSTS:
        return True
    return any(
        normalized_host.endswith(suffix)
        for suffix in PLACEHOLDER_WEB_EVIDENCE_HOST_SUFFIXES
    )


def _mock_provider_metadata_values(value: object) -> list[str]:
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_mock_provider_metadata_values(item))
        return values
    if not isinstance(value, dict):
        return []
    values = []
    for raw_key, item in value.items():
        key = str(raw_key)
        key_lower = key.lower()
        if (
            isinstance(item, str)
            and "mock" in item.strip().lower()
            and ("provider" in key_lower or key_lower == "source_type")
        ):
            values.append(f"{key}={item}")
        values.extend(_mock_provider_metadata_values(item))
    return values


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _production_record_coverage_status(
    record_coverage: object,
    *,
    root: Path,
) -> dict[str, Any]:
    if not isinstance(record_coverage, dict):
        return {
            "schema_version": "nslab.production_record_coverage.v1",
            "passed": False,
            "status": "missing",
            "finding_count": 1,
            "findings": ["record coverage manifest is missing"],
            "accepted_record_count": None,
            "available_record_count": None,
            "compiled_record_count": None,
            "swept_record_count": None,
            "swept_record_ids": [],
            "duplicate_swept_record_ids": [],
            "unswept_record_ids": None,
            "record_store_readable": None,
            "record_store_record_count": None,
            "record_store_error": None,
            "unknown_swept_record_ids": [],
            "missing_swept_record_ids": [],
        }

    findings: list[str] = []
    try:
        store_records = BrainRecordStore(root).list_records()
        record_store_readable = True
        record_store_error = None
    except Exception as exc:
        store_records = []
        record_store_readable = False
        record_store_error = type(exc).__name__
        findings.append("record coverage source record store is unreadable")
    store_record_ids = [record.record_id for record in store_records]
    store_record_id_set = set(store_record_ids)
    store_record_count = len(store_record_id_set)
    store_training_eligible_count = sum(
        1 for record in store_records if record.training_eligible
    )
    store_ineligible_count = sum(
        1 for record in store_records if not record.training_eligible
    )
    store_audit_only_count = sum(
        1 for record in store_records if record.evidence_phase == "AUDIT"
    )
    store_counts_by_type = dict(
        sorted(Counter(record.record_type for record in store_records).items())
    )
    store_counts_by_phase = dict(
        sorted(Counter(record.evidence_phase for record in store_records).items())
    )
    store_counts_by_target = dict(
        sorted(
            Counter(record.training_target or "UNKNOWN" for record in store_records).items()
        )
    )
    coverage_as_of = _record_coverage_as_of(record_coverage)
    store_available_as_of_count: int | None = None
    store_training_eligible_as_of_count: int | None = None
    if coverage_as_of is not None:
        available_as_of = [
            record
            for record in store_records
            if is_available_as_of(record.available_from, coverage_as_of)
        ]
        store_available_as_of_count = len(available_as_of)
        store_training_eligible_as_of_count = sum(
            1 for record in available_as_of if record.training_eligible
        )
    if record_coverage.get("schema_version") != "nslab.record_coverage_manifest.v1":
        findings.append("record coverage manifest schema_version is invalid")

    count_keys = (
        "accepted_record_count",
        "available_record_count",
        "available_record_count_as_of",
        "training_eligible_available_record_count",
        "training_eligible_record_count_as_of",
        "compiled_record_count",
        "swept_record_count",
        "ineligible_record_count",
        "audit_only_record_count",
    )
    counts = {
        key: _int_from_mapping(record_coverage, key)
        for key in count_keys
    }
    for key, value in counts.items():
        if value is None:
            findings.append(f"record coverage manifest {key} is missing")

    swept_record_ids = _string_list(record_coverage.get("swept_record_ids"))
    unswept_record_ids = _string_list(record_coverage.get("unswept_record_ids"))
    if not isinstance(record_coverage.get("swept_record_ids"), list):
        findings.append("record coverage manifest swept_record_ids is missing")
    if not isinstance(record_coverage.get("unswept_record_ids"), list):
        findings.append("record coverage manifest unswept_record_ids is missing")
    if _duplicate_strings(swept_record_ids):
        findings.append("record coverage manifest has duplicate swept records")
    if unswept_record_ids:
        findings.append("record coverage manifest has unswept records")
    unknown_swept_record_ids = sorted(set(swept_record_ids) - store_record_id_set)
    missing_swept_record_ids = sorted(store_record_id_set - set(swept_record_ids))
    if record_store_readable is True and unknown_swept_record_ids:
        findings.append("record coverage manifest swept IDs reference unknown records")
    if record_store_readable is True and missing_swept_record_ids:
        findings.append("record coverage manifest swept IDs do not cover record store")

    count_maps = {
        "record_counts_by_type": _int_dict(
            record_coverage.get("record_counts_by_type")
        ),
        "record_counts_by_evidence_phase": _int_dict(
            record_coverage.get("record_counts_by_evidence_phase")
        ),
        "record_counts_by_training_target": _int_dict(
            record_coverage.get("record_counts_by_training_target")
        ),
    }
    for key, values in count_maps.items():
        raw_value = record_coverage.get(key)
        if not isinstance(raw_value, dict):
            findings.append(f"record coverage manifest {key} is missing")
        elif len(values) != len(raw_value):
            findings.append(
                f"record coverage manifest {key} contains non-integer values"
            )

    accepted_count = counts["accepted_record_count"]
    available_count = counts["available_record_count"]
    available_count_as_of = counts["available_record_count_as_of"]
    training_eligible_count = counts["training_eligible_available_record_count"]
    training_eligible_count_as_of = counts["training_eligible_record_count_as_of"]
    compiled_count = counts["compiled_record_count"]
    swept_count = counts["swept_record_count"]
    ineligible_count = counts["ineligible_record_count"]
    audit_only_count = counts["audit_only_record_count"]

    if accepted_count is not None:
        for key, values in count_maps.items():
            if isinstance(record_coverage.get(key), dict) and sum(values.values()) != accepted_count:
                findings.append(
                    f"record coverage manifest {key} does not sum to accepted records"
                )
    if (
        accepted_count is not None
        and available_count is not None
        and available_count > accepted_count
    ):
        findings.append("record coverage manifest available count exceeds accepted count")
    if (
        record_store_readable is True
        and accepted_count is not None
        and accepted_count != store_record_count
    ):
        findings.append(
            "record coverage manifest accepted count does not match record store"
        )
    if (
        record_store_readable is True
        and available_count is not None
        and available_count != store_record_count
    ):
        findings.append(
            "record coverage manifest available count does not match record store"
        )
    if (
        available_count is not None
        and available_count_as_of is not None
        and available_count_as_of > available_count
    ):
        findings.append("record coverage manifest as-of available count exceeds available count")
    if (
        store_available_as_of_count is not None
        and available_count_as_of is not None
        and available_count_as_of != store_available_as_of_count
    ):
        findings.append(
            "record coverage manifest as-of available count does not match record store"
        )
    if (
        accepted_count is not None
        and compiled_count is not None
        and compiled_count != accepted_count
    ):
        findings.append(
            "record coverage manifest compiled count does not match accepted count"
        )
    if (
        record_store_readable is True
        and compiled_count is not None
        and compiled_count != store_record_count
    ):
        findings.append(
            "record coverage manifest compiled count does not match record store"
        )
    if swept_count is not None and swept_count != len(swept_record_ids):
        findings.append("record coverage manifest swept count does not match swept IDs")
    if (
        available_count is not None
        and swept_count is not None
        and swept_count != available_count
    ):
        findings.append(
            "record coverage manifest swept count does not match available count"
        )
    if (
        training_eligible_count is not None
        and available_count is not None
        and training_eligible_count > available_count
    ):
        findings.append(
            "record coverage manifest training eligible count exceeds available count"
        )
    if (
        record_store_readable is True
        and training_eligible_count is not None
        and training_eligible_count != store_training_eligible_count
    ):
        findings.append(
            "record coverage manifest training eligible count does not match record store"
        )
    if (
        training_eligible_count_as_of is not None
        and available_count_as_of is not None
        and training_eligible_count_as_of > available_count_as_of
    ):
        findings.append(
            "record coverage manifest as-of training eligible count exceeds as-of available count"
        )
    if (
        store_training_eligible_as_of_count is not None
        and training_eligible_count_as_of is not None
        and training_eligible_count_as_of != store_training_eligible_as_of_count
    ):
        findings.append(
            "record coverage manifest as-of training eligible count does not match record store"
        )
    if (
        ineligible_count is not None
        and accepted_count is not None
        and ineligible_count > accepted_count
    ):
        findings.append("record coverage manifest ineligible count exceeds accepted count")
    if (
        record_store_readable is True
        and ineligible_count is not None
        and ineligible_count != store_ineligible_count
    ):
        findings.append(
            "record coverage manifest ineligible count does not match record store"
        )
    if (
        audit_only_count is not None
        and accepted_count is not None
        and audit_only_count > accepted_count
    ):
        findings.append("record coverage manifest audit-only count exceeds accepted count")
    if (
        record_store_readable is True
        and audit_only_count is not None
        and audit_only_count != store_audit_only_count
    ):
        findings.append(
            "record coverage manifest audit-only count does not match record store"
        )
    if (
        record_store_readable is True
        and isinstance(record_coverage.get("record_counts_by_type"), dict)
        and count_maps["record_counts_by_type"] != store_counts_by_type
    ):
        findings.append(
            "record coverage manifest record_counts_by_type does not match record store"
        )
    if (
        record_store_readable is True
        and isinstance(record_coverage.get("record_counts_by_evidence_phase"), dict)
        and count_maps["record_counts_by_evidence_phase"] != store_counts_by_phase
    ):
        findings.append(
            "record coverage manifest record_counts_by_evidence_phase does not match record store"
        )
    if (
        record_store_readable is True
        and isinstance(record_coverage.get("record_counts_by_training_target"), dict)
        and count_maps["record_counts_by_training_target"] != store_counts_by_target
    ):
        findings.append(
            "record coverage manifest record_counts_by_training_target does not match record store"
        )

    has_findings_before_complete_check = bool(findings)
    if record_coverage.get("coverage_complete") is not True:
        findings.append("record coverage manifest is not marked complete")
    elif has_findings_before_complete_check:
        findings.append(
            "record coverage manifest is marked complete despite production findings"
        )

    return {
        "schema_version": "nslab.production_record_coverage.v1",
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "accepted_record_count": accepted_count,
        "available_record_count": available_count,
        "available_record_count_as_of": available_count_as_of,
        "training_eligible_available_record_count": training_eligible_count,
        "training_eligible_record_count_as_of": training_eligible_count_as_of,
        "compiled_record_count": compiled_count,
        "swept_record_count": swept_count,
        "swept_record_id_count": len(swept_record_ids),
        "swept_record_ids": swept_record_ids,
        "duplicate_swept_record_ids": _duplicate_strings(swept_record_ids),
        "unswept_record_ids": unswept_record_ids,
        "record_counts_by_type": count_maps["record_counts_by_type"],
        "record_counts_by_evidence_phase": count_maps[
            "record_counts_by_evidence_phase"
        ],
        "record_counts_by_training_target": count_maps[
            "record_counts_by_training_target"
        ],
        "ineligible_record_count": ineligible_count,
        "audit_only_record_count": audit_only_count,
        "coverage_complete": record_coverage.get("coverage_complete"),
        "record_store_readable": record_store_readable,
        "record_store_record_count": store_record_count if record_store_readable else None,
        "record_store_error": record_store_error,
        "record_store_counts_by_type": store_counts_by_type,
        "record_store_counts_by_evidence_phase": store_counts_by_phase,
        "record_store_counts_by_training_target": store_counts_by_target,
        "record_store_training_eligible_record_count": (
            store_training_eligible_count if record_store_readable else None
        ),
        "record_store_ineligible_record_count": (
            store_ineligible_count if record_store_readable else None
        ),
        "record_store_audit_only_record_count": (
            store_audit_only_count if record_store_readable else None
        ),
        "record_store_available_record_count_as_of": store_available_as_of_count,
        "record_store_training_eligible_record_count_as_of": (
            store_training_eligible_as_of_count
        ),
        "unknown_swept_record_ids": unknown_swept_record_ids,
        "missing_swept_record_ids": missing_swept_record_ids,
    }


def _production_semantic_index_status(
    vector_index: object,
    *,
    settings: Settings,
    expected_source_record_count: int | None,
) -> dict[str, Any]:
    status = vector_index.get("status") if isinstance(vector_index, dict) else None
    embedding_method = (
        vector_index.get("embedding_method") if isinstance(vector_index, dict) else None
    )
    source_brain_record_count = _int_from_mapping(
        vector_index,
        "source_brain_record_count",
    )
    indexed_brain_record_count = _int_from_mapping(vector_index, "brain_record_count")
    embedding_model = (
        _llm_embedding_model_from_method(embedding_method)
        if isinstance(embedding_method, str)
        else None
    )
    configured_embedding_model = _production_configured_embedding_model(settings)
    findings: list[str] = []
    manifest_status = _production_semantic_index_manifest_status(
        settings.project_root,
        vector_index=vector_index,
        expected_source_record_count=expected_source_record_count,
        configured_provider=settings.llm_provider,
        configured_embedding_model=configured_embedding_model,
    )
    if not isinstance(vector_index, dict):
        findings.append("vector index status is missing")
    else:
        if status != "current":
            findings.append("vector index is not current")
        if vector_index.get("brain_records_exists") is not True:
            findings.append("brain record vector index is missing")
        if not isinstance(embedding_method, str) or not embedding_method:
            findings.append("semantic index embedding method is missing")
        elif embedding_method == VECTOR_EMBEDDING_METHOD:
            findings.append("deterministic mock vector index cannot be production semantic index")
        else:
            expected_prefix = f"llm_embedding:{settings.llm_provider.strip().lower()}:"
            if not embedding_method.strip().lower().startswith(expected_prefix):
                findings.append("semantic index provider does not match configured LLM provider")
            expected_model = configured_embedding_model
            if not isinstance(embedding_model, str) or not embedding_model:
                findings.append("semantic index embedding model is missing")
            elif expected_model and embedding_model.strip() != expected_model.strip():
                findings.append("semantic index embedding model does not match configured model")
        if (
            isinstance(expected_source_record_count, int)
            and source_brain_record_count != expected_source_record_count
        ):
            findings.append("semantic index source record count does not match coverage")
        if (
            isinstance(expected_source_record_count, int)
            and indexed_brain_record_count != expected_source_record_count
        ):
            findings.append("semantic index record count does not match coverage")
    findings.extend(manifest_status["findings"])
    return {
        "schema_version": "nslab.production_semantic_index.v1",
        "status": "ready" if not findings else "attention",
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings,
        "vector_index_status": status,
        "embedding_method": embedding_method,
        "embedding_model": embedding_model,
        "configured_embedding_model": configured_embedding_model,
        "expected_source_record_count": expected_source_record_count,
        "source_brain_record_count": source_brain_record_count,
        "indexed_brain_record_count": indexed_brain_record_count,
        "manifest": manifest_status,
    }


def _production_configured_embedding_model(settings: Settings) -> str | None:
    if settings.llm.embedding_model and settings.llm.embedding_model.strip():
        return settings.llm.embedding_model.strip()
    if settings.llm_provider.strip().lower() in OPENAI_PROVIDER_ALIASES:
        return DEFAULT_OPENAI_EMBEDDING_MODEL
    return None


def _production_semantic_index_manifest_status(
    root: Path,
    *,
    vector_index: object,
    expected_source_record_count: int | None,
    configured_provider: str,
    configured_embedding_model: str | None,
) -> dict[str, Any]:
    manifest_path = root / "memory" / "vector_index" / "manifest.json"
    report_embedding_method = (
        vector_index.get("embedding_method") if isinstance(vector_index, dict) else None
    )
    findings: list[str] = []
    if not manifest_path.exists():
        findings.append("semantic index manifest is missing on disk")
        return {
            "schema_version": "nslab.production_semantic_index_manifest.v1",
            "path": relative_to_root(manifest_path, root),
            "exists": False,
            "checked": False,
            "passed": not findings,
            "finding_count": len(findings),
            "findings": findings,
            "embedding_method": None,
            "embedding_model": None,
            "dimensions": None,
            "record_count": None,
            "accepted_episode_count": None,
            "accepted_hash_count": None,
            "accepted_hash_invalid_count": None,
            "expected_accepted_hash_count": None,
            "accepted_hashes_match": None,
            "brain_record_count": None,
            "brain_record_hash_count": None,
            "records_file": None,
            "records_file_exists": False,
            "records_file_path_valid": None,
            "records_file_is_absolute": None,
            "records_file_escapes_index": None,
            "records_sha256_matches": None,
            "records_row_count": None,
            "records_invalid_line_count": None,
            "brain_records_file": None,
            "brain_records_file_exists": False,
            "brain_records_file_path_valid": None,
            "brain_records_file_is_absolute": None,
            "brain_records_file_escapes_index": None,
            "brain_records_sha256_matches": None,
            "brain_records_row_count": None,
            "brain_records_invalid_line_count": None,
            "record_store_readable": None,
            "record_store_record_count": None,
            "record_store_error": None,
            "unknown_brain_record_ids": [],
            "missing_brain_record_ids": [],
            "brain_record_hash_mismatches": [],
        }
    try:
        manifest = _read_json_object(manifest_path)
    except ValueError:
        findings.append("semantic index manifest is unreadable")
        return {
            "schema_version": "nslab.production_semantic_index_manifest.v1",
            "path": relative_to_root(manifest_path, root),
            "exists": True,
            "checked": False,
            "passed": False,
            "finding_count": len(findings),
            "findings": findings,
            "embedding_method": None,
            "embedding_model": None,
            "dimensions": None,
            "record_count": None,
            "accepted_episode_count": None,
            "accepted_hash_count": None,
            "accepted_hash_invalid_count": None,
            "expected_accepted_hash_count": None,
            "accepted_hashes_match": None,
            "brain_record_count": None,
            "brain_record_hash_count": None,
            "records_file": None,
            "records_file_exists": False,
            "records_file_path_valid": None,
            "records_file_is_absolute": None,
            "records_file_escapes_index": None,
            "records_sha256_matches": None,
            "records_row_count": None,
            "records_invalid_line_count": None,
            "brain_records_file": None,
            "brain_records_file_exists": False,
            "brain_records_file_path_valid": None,
            "brain_records_file_is_absolute": None,
            "brain_records_file_escapes_index": None,
            "brain_records_sha256_matches": None,
            "brain_records_row_count": None,
            "brain_records_invalid_line_count": None,
            "record_store_readable": None,
            "record_store_record_count": None,
            "record_store_error": None,
            "unknown_brain_record_ids": [],
            "missing_brain_record_ids": [],
            "brain_record_hash_mismatches": [],
        }

    embedding_method = manifest.get("embedding_method")
    embedding_model = (
        _llm_embedding_model_from_method(embedding_method)
        if isinstance(embedding_method, str)
        else None
    )
    dimensions = _int_from_mapping(manifest, "dimensions")
    dimensions = dimensions if isinstance(dimensions, int) and dimensions > 0 else None
    record_count = _int_from_mapping(manifest, "record_count")
    accepted_episode_count = _int_from_mapping(manifest, "accepted_episode_count")
    expected_accepted_hashes = ResearchStore(root).accepted_hashes()
    accepted_hashes = manifest.get("accepted_hashes")
    accepted_hashes_valid: dict[str, str] | None = None
    accepted_hash_invalid_count: int | None = None
    if isinstance(accepted_hashes, dict):
        accepted_hashes_valid = {
            key: value
            for key, value in accepted_hashes.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        accepted_hash_invalid_count = sum(
            1
            for key, value in accepted_hashes.items()
            if not isinstance(key, str) or not isinstance(value, str)
        )
    accepted_hash_count = (
        len(accepted_hashes_valid) if accepted_hashes_valid is not None else None
    )
    expected_accepted_hash_count = len(expected_accepted_hashes)
    accepted_hashes_match = accepted_hashes_valid == expected_accepted_hashes
    brain_record_count = _int_from_mapping(manifest, "brain_record_count")
    brain_record_hashes = manifest.get("brain_record_hashes")
    brain_record_hash_count = (
        len(brain_record_hashes) if isinstance(brain_record_hashes, dict) else None
    )
    records_file = manifest.get("records_file")
    records_file = (
        records_file if isinstance(records_file, str) and records_file else "records.jsonl"
    )
    records_path = manifest_path.parent / "records.jsonl"
    records_file_is_absolute = False
    records_file_escapes_index = False
    records_file_path_valid = True
    records_ref = Path(records_file)
    if records_ref.is_absolute():
        records_file_is_absolute = True
        records_file_path_valid = False
        records_path = records_ref
    else:
        resolved_records_path = (manifest_path.parent / records_ref).resolve()
        try:
            resolved_records_path.relative_to(manifest_path.parent.resolve())
        except ValueError:
            records_file_escapes_index = True
            records_file_path_valid = False
            records_path = resolved_records_path
        else:
            records_path = resolved_records_path
    records_exists = records_path.exists() if records_file_path_valid else False
    records_sha256_matches: bool | None = None
    records_row_count: int | None = None
    records_invalid_line_count: int | None = None
    if records_exists:
        try:
            records_payload = records_path.read_text(encoding="utf-8")
        except OSError:
            records_payload = ""
            records_invalid_line_count = 1
        declared_records_sha = manifest.get("records_sha256")
        if isinstance(declared_records_sha, str):
            records_sha256_matches = sha256_text(records_payload) == declared_records_sha
        rows = [line for line in records_payload.splitlines() if line.strip()]
        records_row_count = len(rows)
        invalid_line_count = records_invalid_line_count or 0
        for line in rows:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                invalid_line_count += 1
                continue
            if not _valid_semantic_index_episode_row(payload, dimensions=dimensions):
                invalid_line_count += 1
        records_invalid_line_count = invalid_line_count

    brain_records_file = manifest.get("brain_records_file")
    brain_records_file = (
        brain_records_file
        if isinstance(brain_records_file, str) and brain_records_file
        else "brain_records.jsonl"
    )
    brain_records_path = manifest_path.parent / "brain_records.jsonl"
    brain_records_file_is_absolute = False
    brain_records_file_escapes_index = False
    brain_records_file_path_valid = True
    brain_records_ref = Path(brain_records_file)
    if brain_records_ref.is_absolute():
        brain_records_file_is_absolute = True
        brain_records_file_path_valid = False
        brain_records_path = brain_records_ref
    else:
        resolved_brain_records_path = (manifest_path.parent / brain_records_ref).resolve()
        try:
            resolved_brain_records_path.relative_to(manifest_path.parent.resolve())
        except ValueError:
            brain_records_file_escapes_index = True
            brain_records_file_path_valid = False
            brain_records_path = resolved_brain_records_path
        else:
            brain_records_path = resolved_brain_records_path
    brain_records_exists = (
        brain_records_path.exists() if brain_records_file_path_valid else False
    )
    brain_records_sha256_matches: bool | None = None
    brain_records_row_count: int | None = None
    brain_records_invalid_line_count: int | None = None
    brain_records_ids: list[str] = []
    if brain_records_exists:
        try:
            brain_records_payload = brain_records_path.read_text(encoding="utf-8")
        except OSError:
            brain_records_payload = ""
            brain_records_invalid_line_count = 1
        declared_brain_records_sha = manifest.get("brain_records_sha256")
        if isinstance(declared_brain_records_sha, str):
            brain_records_sha256_matches = (
                sha256_text(brain_records_payload) == declared_brain_records_sha
            )
        rows = [line for line in brain_records_payload.splitlines() if line.strip()]
        brain_records_row_count = len(rows)
        invalid_line_count = brain_records_invalid_line_count or 0
        for line in rows:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                invalid_line_count += 1
                continue
            if not isinstance(payload, dict):
                invalid_line_count += 1
                continue
            record_id = payload.get("record_id")
            if isinstance(record_id, str) and record_id:
                brain_records_ids.append(record_id)
            else:
                invalid_line_count += 1
                continue
            if not _valid_semantic_index_brain_record_row(
                payload,
                dimensions=dimensions,
            ):
                invalid_line_count += 1
        brain_records_invalid_line_count = invalid_line_count
    record_store_stats = _brain_record_store_id_stats(root)
    record_store_records = (
        record_store_stats["records_by_id"]
        if record_store_stats["readable"] is True
        else None
    )
    unknown_brain_record_ids: list[str] = []
    missing_brain_record_ids: list[str] = []
    brain_record_hash_mismatches: list[str] = []
    if isinstance(record_store_records, dict):
        record_store_ids = set(record_store_records)
        indexed_ids = set(brain_records_ids)
        unknown_brain_record_ids = sorted(indexed_ids - record_store_ids)
        missing_brain_record_ids = sorted(record_store_ids - indexed_ids)
        if isinstance(brain_record_hashes, dict):
            manifest_hashes = {
                key: value
                for key, value in brain_record_hashes.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            invalid_hash_ids = [
                key
                for key, value in brain_record_hashes.items()
                if isinstance(key, str) and not isinstance(value, str)
            ]
            mismatched_hash_ids = [
                record_id
                for record_id in sorted(set(manifest_hashes) & record_store_ids)
                if (
                    manifest_hashes[record_id]
                    != record_store_records[record_id].normalized_payload_sha256
                )
            ]
            brain_record_hash_mismatches = sorted(
                {*invalid_hash_ids, *mismatched_hash_ids}
            )
    if manifest.get("schema_version") != "nslab.local_vector_index.v1":
        findings.append("semantic index manifest schema version is invalid")
    if not isinstance(embedding_method, str) or not embedding_method:
        findings.append("semantic index manifest embedding method is missing")
    elif embedding_method == VECTOR_EMBEDDING_METHOD:
        findings.append("on-disk deterministic mock vector index cannot be production semantic index")
    else:
        expected_prefix = f"llm_embedding:{configured_provider.strip().lower()}:"
        if not embedding_method.strip().lower().startswith(expected_prefix):
            findings.append("semantic index manifest provider does not match configured LLM provider")
        if not isinstance(embedding_model, str) or not embedding_model:
            findings.append("semantic index manifest embedding model is missing")
        elif (
            configured_embedding_model
            and embedding_model.strip() != configured_embedding_model.strip()
        ):
            findings.append("semantic index manifest embedding model does not match configured model")
    if isinstance(report_embedding_method, str) and embedding_method != report_embedding_method:
        findings.append("semantic index report does not match on-disk embedding method")
    if accepted_hashes_valid is None:
        findings.append("semantic index accepted_hashes field is invalid")
    elif accepted_hash_invalid_count:
        findings.append("semantic index accepted_hashes has invalid hashes")
    if accepted_episode_count != expected_accepted_hash_count:
        findings.append(
            "semantic index accepted episode count does not match accepted episodes"
        )
    if accepted_hashes_match is not True:
        findings.append("semantic index accepted_hashes do not match accepted episodes")
    if (
        isinstance(expected_source_record_count, int)
        and brain_record_count != expected_source_record_count
    ):
        findings.append("semantic index manifest record count does not match coverage")
    if (
        isinstance(expected_source_record_count, int)
        and brain_record_hash_count != expected_source_record_count
    ):
        findings.append("semantic index manifest record hash count does not match coverage")
    if records_file_is_absolute:
        findings.append("semantic index records_file must be vector-index relative")
    if records_file_escapes_index:
        findings.append("semantic index records_file escapes vector index directory")
    if not records_exists:
        findings.append("semantic index records file is missing on disk")
    elif records_invalid_line_count != 0:
        findings.append("semantic index records file has invalid rows")
    if records_exists and records_sha256_matches is not True:
        findings.append("semantic index records file hash mismatch")
    if (
        records_row_count is not None
        and record_count is not None
        and records_row_count != record_count
    ):
        findings.append("semantic index records row count does not match manifest")
    if brain_records_file_is_absolute:
        findings.append("semantic index brain_records_file must be vector-index relative")
    if brain_records_file_escapes_index:
        findings.append("semantic index brain_records_file escapes vector index directory")
    if not brain_records_exists:
        findings.append("semantic index brain record file is missing on disk")
    elif brain_records_invalid_line_count != 0:
        findings.append("semantic index brain record file has invalid rows")
    if brain_records_exists and brain_records_sha256_matches is not True:
        findings.append("semantic index brain record file hash mismatch")
    if (
        brain_records_row_count is not None
        and brain_record_count is not None
        and brain_records_row_count != brain_record_count
    ):
        findings.append("semantic index brain record row count does not match manifest")
    if isinstance(brain_record_hashes, dict) and sorted(brain_records_ids) != sorted(
        key for key in brain_record_hashes if isinstance(key, str)
    ):
        findings.append("semantic index brain record IDs do not match manifest hashes")
    if record_store_stats["readable"] is not True:
        findings.append("semantic index record store is unreadable")
    if unknown_brain_record_ids:
        findings.append("semantic index references unknown brain record IDs")
    if missing_brain_record_ids:
        findings.append("semantic index does not cover record store IDs")
    if brain_record_hash_mismatches:
        findings.append("semantic index brain record hashes do not match record store")
    return {
        "schema_version": "nslab.production_semantic_index_manifest.v1",
        "path": relative_to_root(manifest_path, root),
        "exists": True,
        "checked": True,
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings,
        "embedding_method": embedding_method,
        "embedding_model": embedding_model,
        "dimensions": dimensions,
        "record_count": record_count,
        "accepted_episode_count": accepted_episode_count,
        "accepted_hash_count": accepted_hash_count,
        "accepted_hash_invalid_count": accepted_hash_invalid_count,
        "expected_accepted_hash_count": expected_accepted_hash_count,
        "accepted_hashes_match": accepted_hashes_match,
        "brain_record_count": brain_record_count,
        "brain_record_hash_count": brain_record_hash_count,
        "records_file": relative_to_root(records_path, root),
        "records_file_exists": records_exists,
        "records_file_path_valid": records_file_path_valid,
        "records_file_is_absolute": records_file_is_absolute,
        "records_file_escapes_index": records_file_escapes_index,
        "records_sha256_matches": records_sha256_matches,
        "records_row_count": records_row_count,
        "records_invalid_line_count": records_invalid_line_count,
        "brain_records_file": relative_to_root(brain_records_path, root),
        "brain_records_file_exists": brain_records_exists,
        "brain_records_file_path_valid": brain_records_file_path_valid,
        "brain_records_file_is_absolute": brain_records_file_is_absolute,
        "brain_records_file_escapes_index": brain_records_file_escapes_index,
        "brain_records_sha256_matches": brain_records_sha256_matches,
        "brain_records_row_count": brain_records_row_count,
        "brain_records_invalid_line_count": brain_records_invalid_line_count,
        "record_store_readable": record_store_stats["readable"],
        "record_store_record_count": record_store_stats["record_count"],
        "record_store_error": record_store_stats["error"],
        "unknown_brain_record_ids": unknown_brain_record_ids,
        "missing_brain_record_ids": missing_brain_record_ids,
        "brain_record_hash_mismatches": brain_record_hash_mismatches,
    }


def _valid_semantic_index_episode_row(
    value: object,
    *,
    dimensions: int | None,
) -> bool:
    if dimensions is None or not isinstance(value, dict):
        return False
    if not isinstance(value.get("episode_id"), str):
        return False
    return _valid_semantic_index_vector_payload(value, dimensions=dimensions)


def _valid_semantic_index_brain_record_row(
    value: object,
    *,
    dimensions: int | None,
) -> bool:
    if dimensions is None or not isinstance(value, dict):
        return False
    if not isinstance(value.get("record_id"), str):
        return False
    if not isinstance(value.get("record_type"), str):
        return False
    return _valid_semantic_index_vector_payload(value, dimensions=dimensions)


def _valid_semantic_index_vector_payload(
    value: dict[str, Any],
    *,
    dimensions: int,
) -> bool:
    terms = value.get("terms")
    embedding = value.get("embedding")
    return (
        isinstance(terms, list)
        and all(isinstance(term, str) for term in terms)
        and isinstance(embedding, list)
        and len(embedding) == dimensions
        and all(isinstance(item, int | float) and not isinstance(item, bool) for item in embedding)
    )


def _production_training_export_status(settings: Settings) -> dict[str, Any]:
    root = settings.project_root
    try:
        source_records = BrainRecordStore(root).list_records()
    except (OSError, ValueError) as exc:
        finding = (
            "training export source record store is unreadable: "
            f"{type(exc).__name__}: {exc}"
        )
        return {
            "schema_version": "nslab.production_training_exports.v1",
            "passed": False,
            "status": "attention",
            "finding_count": 1,
            "findings": [finding],
            "source_record_count": None,
            "per_export_eligible_record_count": None,
            "per_export_exported_record_count": None,
            "per_export_skipped_record_count": None,
            "unique_source_record_count": None,
            "unique_training_eligible_record_count": None,
            "unique_exported_record_count": None,
            "unique_skipped_record_count": None,
            "record_store_source_record_ids": [],
            "record_store_training_eligible_record_ids": [],
            "unique_source_record_ids": [],
            "unique_training_eligible_record_ids": [],
            "unique_exported_record_ids": [],
            "unique_skipped_record_ids": [],
            "skipped_record_reasons_by_record_id": {},
            "unique_skipped_record_reasons_by_record_id": {},
            "skipped_record_reason_counts": {},
            "blind_safe_row_count": None,
            "hindsight_row_count": None,
            "source_phase_counts": {},
            "counts_by_record_type": {},
            "counts_by_training_target": {},
            "available_manifest_kinds": [],
            "missing_manifest_kinds": [],
        }
    source_record_ids = sorted(record.record_id for record in source_records)
    training_eligible_record_ids = sorted(
        record.record_id for record in source_records if record.training_eligible
    )
    source_record_count = len(source_record_ids)
    if source_record_count == 0:
        return {
            "schema_version": "nslab.production_training_exports.v1",
            "passed": True,
            "status": "not_applicable",
            "finding_count": 0,
            "findings": [],
            "source_record_count": 0,
            "per_export_eligible_record_count": 0,
            "per_export_exported_record_count": 0,
            "per_export_skipped_record_count": 0,
            "unique_source_record_count": 0,
            "unique_training_eligible_record_count": 0,
            "unique_exported_record_count": 0,
            "unique_skipped_record_count": 0,
            "record_store_source_record_ids": [],
            "record_store_training_eligible_record_ids": [],
            "unique_source_record_ids": [],
            "unique_training_eligible_record_ids": [],
            "unique_exported_record_ids": [],
            "unique_skipped_record_ids": [],
            "skipped_record_reasons_by_record_id": {},
            "unique_skipped_record_reasons_by_record_id": {},
            "skipped_record_reason_counts": {},
            "blind_safe_row_count": 0,
            "hindsight_row_count": 0,
            "source_phase_counts": {},
            "counts_by_record_type": {},
            "counts_by_training_target": {},
            "available_manifest_kinds": [],
            "missing_manifest_kinds": [],
        }

    findings: list[str] = []
    try:
        audit = audit_training_exports(root)
    except (OSError, ValueError) as exc:
        audit = {
            "passed": False,
            "findings": [f"training export audit failed: {type(exc).__name__}: {exc}"],
        }
    findings.extend(_string_list(audit.get("findings")))

    report = _read_optional_json(root / "diagnostics" / "training_export_report.json")
    diagnostics = report if isinstance(report, dict) else {}
    if not diagnostics:
        findings.append("training export diagnostics report is missing")
    elif diagnostics.get("schema_version") != "nslab.training_export_diagnostics.v1":
        findings.append("training export diagnostics schema_version is invalid")

    missing_manifest_kinds = _string_list(diagnostics.get("missing_manifest_kinds"))
    if missing_manifest_kinds:
        findings.append(
            "training export manifests are missing: "
            + ", ".join(missing_manifest_kinds)
        )
    if diagnostics.get("brain_record_source_required") is not True:
        findings.append("training export diagnostics do not require brain records")

    record_store_source_count = _int_from_mapping(
        diagnostics,
        "record_store_source_record_count",
    )
    unique_source_record_count = _int_from_mapping(
        diagnostics,
        "unique_source_record_count",
    )
    per_export_eligible_count = _int_from_mapping(
        diagnostics,
        "per_export_eligible_record_count",
    )
    per_export_exported_count = _int_from_mapping(
        diagnostics,
        "per_export_exported_record_count",
    )
    per_export_skipped_count = _int_from_mapping(
        diagnostics,
        "per_export_skipped_record_count",
    )
    source_record_hash_count = _int_from_mapping(
        diagnostics,
        "source_record_hash_count",
    )
    unique_training_eligible_count = _int_from_mapping(
        diagnostics,
        "unique_training_eligible_record_count",
    )
    unique_exported_count = _int_from_mapping(
        diagnostics,
        "unique_exported_record_count",
    )
    unique_skipped_count = _int_from_mapping(
        diagnostics,
        "unique_skipped_record_count",
    )
    unique_source_ids = _string_list(
        diagnostics.get("unique_source_record_ids")
    )
    unique_training_eligible_ids = _string_list(
        diagnostics.get("unique_training_eligible_record_ids")
    )
    unique_exported_ids = _string_list(
        diagnostics.get("unique_exported_record_ids")
    )
    unique_skipped_ids = _string_list(
        diagnostics.get("unique_skipped_record_ids")
    )
    skipped_record_reasons_by_record_id = _string_list_dict(
        diagnostics.get("skipped_record_reasons_by_record_id")
    )
    unique_skipped_record_reasons_by_record_id = _string_list_dict(
        diagnostics.get("unique_skipped_record_reasons_by_record_id")
    )
    skipped_record_reason_counts = _int_dict(
        diagnostics.get("skipped_record_reason_counts")
    )
    blind_safe_row_count = _int_from_mapping(diagnostics, "blind_safe_row_count")
    hindsight_row_count = _int_from_mapping(diagnostics, "hindsight_row_count")
    source_phase_counts = _int_dict(diagnostics.get("source_phase_counts"))
    counts_by_record_type = _int_dict(diagnostics.get("counts_by_record_type"))
    counts_by_training_target = _int_dict(
        diagnostics.get("counts_by_training_target")
    )

    if record_store_source_count != source_record_count:
        findings.append(
            "training export record-store source count does not match current records"
        )
    if unique_source_record_count != source_record_count:
        findings.append(
            "training export unique source record count does not match current records"
        )
    if set(unique_source_ids) != set(source_record_ids):
        findings.append(
            "training export unique source record IDs do not match current records"
        )
    if set(unique_training_eligible_ids) - set(training_eligible_record_ids):
        findings.append(
            "training export unique training-eligible record IDs include IDs "
            "not in current records"
        )
    if set(unique_exported_ids) - set(training_eligible_record_ids):
        findings.append(
            "training export exported record IDs include ineligible current records"
        )
    if source_record_hash_count != source_record_count:
        findings.append(
            "training export source record hash count does not match current records"
        )
    if unique_source_record_count is not None:
        if (
            unique_exported_count is not None
            and unique_skipped_count is not None
            and unique_source_record_count != unique_exported_count + unique_skipped_count
        ):
            findings.append(
                "training export unique source/export/skipped counts are inconsistent"
            )
        if unique_source_record_count != len(set(unique_exported_ids) | set(unique_skipped_ids)):
            findings.append(
                "training export unique source IDs do not match exported and skipped IDs"
            )
    if (
        unique_source_record_count is not None
        and unique_source_record_count != len(unique_source_ids)
    ):
        findings.append("training export unique source record ID count mismatch")
    if (
        unique_training_eligible_count is not None
        and unique_training_eligible_count != len(unique_training_eligible_ids)
    ):
        findings.append(
            "training export unique training-eligible record ID count mismatch"
        )
    if set(unique_training_eligible_ids) - set(unique_source_ids):
        findings.append(
            "training export training-eligible record IDs are not a subset of "
            "source record IDs"
        )
    if unique_exported_count is not None and unique_exported_count != len(unique_exported_ids):
        findings.append("training export unique exported record ID count mismatch")
    if unique_skipped_count is not None and unique_skipped_count != len(unique_skipped_ids):
        findings.append("training export unique skipped record ID count mismatch")
    if set(unique_exported_ids) & set(unique_skipped_ids):
        findings.append("training export exported and skipped record IDs overlap")
    if unique_skipped_ids and (
        set(unique_skipped_ids) - set(unique_skipped_record_reasons_by_record_id)
    ):
        findings.append("training export skipped record reasons are missing")
    if per_export_skipped_count and not skipped_record_reason_counts:
        findings.append("training export skipped record reason counts are missing")
    if (
        unique_exported_count is not None
        and unique_training_eligible_count is not None
        and unique_exported_count > unique_training_eligible_count
    ):
        findings.append("training export includes more records than eligible records")

    weight_statuses = diagnostics.get("weight_validation_statuses")
    if not isinstance(weight_statuses, dict):
        findings.append("training export weight validation statuses are missing")
    else:
        failed_weights = [
            str(kind)
            for kind, status in sorted(weight_statuses.items())
            if status != "passed"
        ]
        if failed_weights:
            findings.append(
                "training export weight validation failed: "
                + ", ".join(failed_weights)
            )

    duplicate_issuer_day_count = _int_from_mapping(
        diagnostics,
        "duplicate_issuer_day_count",
    )
    duplicate_issuer_day_keys = _string_list(
        diagnostics.get("duplicate_issuer_day_keys")
    )
    if duplicate_issuer_day_count is not None and duplicate_issuer_day_count != 0:
        findings.append("training export has duplicate issuer-day samples")
    issuer_weight_mismatch_count = _int_from_mapping(
        diagnostics,
        "issuer_day_weight_sum_mismatch_count",
    )
    issuer_weight_mismatches = _numeric_map(
        diagnostics.get("issuer_day_weight_sum_mismatches")
    )
    if (
        issuer_weight_mismatch_count is not None
        and issuer_weight_mismatch_count != 0
    ):
        findings.append("training export has issuer-day weight sum mismatches")
    direct_weight_mismatch_count = _int_from_mapping(
        diagnostics,
        "direct_event_weight_sum_mismatch_count",
    )
    direct_weight_mismatches = _numeric_map(
        diagnostics.get("direct_event_weight_sum_mismatches")
    )
    if direct_weight_mismatch_count is not None and direct_weight_mismatch_count != 0:
        findings.append("training export has direct-event weight sum mismatches")

    return {
        "schema_version": "nslab.production_training_exports.v1",
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "source_record_count": source_record_count,
        "record_store_source_record_count": record_store_source_count,
        "per_export_eligible_record_count": per_export_eligible_count,
        "per_export_exported_record_count": per_export_exported_count,
        "per_export_skipped_record_count": per_export_skipped_count,
        "unique_source_record_count": unique_source_record_count,
        "unique_training_eligible_record_count": unique_training_eligible_count,
        "unique_exported_record_count": unique_exported_count,
        "unique_skipped_record_count": unique_skipped_count,
        "record_store_source_record_ids": source_record_ids,
        "record_store_training_eligible_record_ids": training_eligible_record_ids,
        "unique_source_record_ids": unique_source_ids,
        "unique_training_eligible_record_ids": unique_training_eligible_ids,
        "unique_exported_record_ids": unique_exported_ids,
        "unique_skipped_record_ids": unique_skipped_ids,
        "skipped_record_reasons_by_record_id": skipped_record_reasons_by_record_id,
        "unique_skipped_record_reasons_by_record_id": (
            unique_skipped_record_reasons_by_record_id
        ),
        "skipped_record_reason_counts": skipped_record_reason_counts,
        "source_record_hash_count": source_record_hash_count,
        "blind_safe_row_count": blind_safe_row_count,
        "hindsight_row_count": hindsight_row_count,
        "source_phase_counts": source_phase_counts,
        "counts_by_record_type": counts_by_record_type,
        "counts_by_training_target": counts_by_training_target,
        "available_manifest_kinds": _string_list(
            diagnostics.get("available_manifest_kinds")
        ),
        "missing_manifest_kinds": missing_manifest_kinds,
        "weight_validation_statuses": weight_statuses
        if isinstance(weight_statuses, dict)
        else {},
        "duplicate_issuer_day_count": duplicate_issuer_day_count,
        "duplicate_issuer_day_keys": duplicate_issuer_day_keys,
        "issuer_day_weight_sum_mismatch_count": issuer_weight_mismatch_count,
        "issuer_day_weight_sum_mismatches": issuer_weight_mismatches,
        "direct_event_weight_sum_mismatch_count": direct_weight_mismatch_count,
        "direct_event_weight_sum_mismatches": direct_weight_mismatches,
        "audit_passed": audit.get("passed") is True,
    }


def _production_warehouse_status(
    warehouse: object,
    *,
    root: Path,
) -> dict[str, Any]:
    actual_warehouse = _current_production_warehouse_snapshot(root)
    report_warehouse = warehouse if isinstance(warehouse, dict) else None
    findings: list[str] = []
    if report_warehouse is not None:
        findings.extend(_warehouse_readiness_findings(report_warehouse))
    if actual_warehouse["applicable"] is True or report_warehouse is not None:
        findings.extend(_warehouse_readiness_findings(actual_warehouse))
    findings = _unique_preserving_order(findings)
    actual_first = actual_warehouse["applicable"] is True

    def merged_mapping(actual: object, reported: object) -> dict[str, Any]:
        first, second = (actual, reported) if actual_first else (reported, actual)
        if isinstance(first, dict):
            return first
        if isinstance(second, dict):
            return second
        return {}

    def merged_list(actual: object, reported: object) -> list[Any]:
        first, second = (actual, reported) if actual_first else (reported, actual)
        if isinstance(first, list):
            return first
        if isinstance(second, list):
            return second
        return []

    count_mismatches = _first_non_empty_mapping(
        actual_warehouse.get("count_mismatches"),
        report_warehouse.get("count_mismatches") if report_warehouse is not None else None,
    )
    identity_mismatches = _first_non_empty_mapping(
        actual_warehouse.get("identity_mismatches"),
        report_warehouse.get("identity_mismatches") if report_warehouse is not None else None,
    )
    duplicate_identities = _first_non_empty_mapping(
        actual_warehouse.get("duplicate_identities"),
        report_warehouse.get("duplicate_identities") if report_warehouse is not None else None,
    )
    weight_mismatches = _first_non_empty_mapping(
        actual_warehouse.get("weight_mismatches"),
        report_warehouse.get("weight_mismatches") if report_warehouse is not None else None,
    )
    missing_columns = _first_non_empty_mapping(
        actual_warehouse.get("missing_columns"),
        report_warehouse.get("missing_columns") if report_warehouse is not None else None,
    )
    counts = merged_mapping(
        actual_warehouse.get("counts"),
        report_warehouse.get("counts") if report_warehouse is not None else None,
    )
    expected_source_counts = merged_mapping(
        actual_warehouse.get("expected_source_counts"),
        (
            report_warehouse.get("expected_source_counts")
            if report_warehouse is not None
            else None
        ),
    )
    required_files = merged_list(
        actual_warehouse.get("required_files"),
        report_warehouse.get("required_files") if report_warehouse is not None else None,
    )
    missing_files = merged_list(
        actual_warehouse.get("missing_files"),
        report_warehouse.get("missing_files") if report_warehouse is not None else None,
    )
    unreadable_files = merged_list(
        actual_warehouse.get("unreadable_files"),
        (
            report_warehouse.get("unreadable_files")
            if report_warehouse is not None
            else None
        ),
    )
    return {
        "schema_version": "nslab.production_warehouse.v1",
        "applicable": actual_warehouse["applicable"] or report_warehouse is not None,
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "report_present": report_warehouse is not None,
        "audit_error": actual_warehouse.get("audit_error"),
        "required_files_present": actual_warehouse.get("required_files_present"),
        "synced": actual_warehouse.get("synced"),
        "projection_synced": actual_warehouse.get("projection_synced"),
        "count_mismatches": count_mismatches,
        "identity_mismatches": identity_mismatches,
        "duplicate_identities": duplicate_identities,
        "weight_mismatches": weight_mismatches,
        "missing_columns": missing_columns,
        "counts": counts,
        "expected_source_counts": expected_source_counts,
        "required_files": required_files,
        "missing_files": missing_files,
        "unreadable_files": unreadable_files,
        "report_required_files_present": (
            report_warehouse.get("required_files_present")
            if report_warehouse is not None
            else None
        ),
        "report_synced": (
            report_warehouse.get("synced") if report_warehouse is not None else None
        ),
        "report_projection_synced": (
            report_warehouse.get("projection_synced")
            if report_warehouse is not None
            else None
        ),
        "report_count_mismatches": (
            report_warehouse.get("count_mismatches", {})
            if report_warehouse is not None
            else {}
        ),
        "report_identity_mismatches": (
            report_warehouse.get("identity_mismatches", {})
            if report_warehouse is not None
            else {}
        ),
        "report_duplicate_identities": (
            report_warehouse.get("duplicate_identities", {})
            if report_warehouse is not None
            else {}
        ),
        "report_weight_mismatches": (
            report_warehouse.get("weight_mismatches", {})
            if report_warehouse is not None
            else {}
        ),
        "report_missing_columns": (
            report_warehouse.get("missing_columns", {})
            if report_warehouse is not None
            else {}
        ),
        "report_counts": (
            report_warehouse.get("counts", {}) if report_warehouse is not None else {}
        ),
        "report_expected_source_counts": (
            report_warehouse.get("expected_source_counts", {})
            if report_warehouse is not None
            else {}
        ),
        "report_required_files": (
            report_warehouse.get("required_files", [])
            if report_warehouse is not None
            else []
        ),
        "report_missing_files": (
            report_warehouse.get("missing_files", [])
            if report_warehouse is not None
            else []
        ),
        "report_unreadable_files": (
            report_warehouse.get("unreadable_files", [])
            if report_warehouse is not None
            else []
        ),
    }


def _current_production_warehouse_snapshot(root: Path) -> dict[str, Any]:
    try:
        coverage_audit = audit_coverage(root, deep=True)
        accepted_episode_count = len(ResearchStore(root).list_accepted())
        status = _warehouse_status(
            coverage_audit,
            accepted_episode_count=accepted_episode_count,
        )
        applicable = accepted_episode_count > 0 or _has_expected_warehouse_sources(
            coverage_audit.get("warehouse_expected_source_counts")
        )
        return {
            "status": status,
            "applicable": applicable,
            "required_files_present": coverage_audit.get(
                "warehouse_required_files_present"
            ),
            "synced": coverage_audit.get("warehouse_synced"),
            "projection_synced": coverage_audit.get("warehouse_projection_synced"),
            "counts": coverage_audit.get("warehouse_counts", {}),
            "required_files": coverage_audit.get("warehouse_required_files", []),
            "missing_files": coverage_audit.get("warehouse_missing_files", []),
            "unreadable_files": coverage_audit.get("warehouse_unreadable_files", []),
            "count_mismatches": coverage_audit.get("warehouse_count_mismatches", {}),
            "identity_mismatches": coverage_audit.get(
                "warehouse_identity_mismatches",
                {},
            ),
            "duplicate_identities": coverage_audit.get(
                "warehouse_duplicate_identities",
                {},
            ),
            "weight_mismatches": coverage_audit.get("warehouse_weight_mismatches", {}),
            "missing_columns": coverage_audit.get("warehouse_missing_columns", {}),
            "expected_source_counts": coverage_audit.get(
                "warehouse_expected_source_counts",
                {},
            ),
            "audit_error": None,
        }
    except Exception as exc:
        return {
            "status": "attention",
            "applicable": True,
            "required_files_present": False,
            "synced": False,
            "projection_synced": False,
            "counts": {},
            "required_files": [],
            "missing_files": [],
            "unreadable_files": [],
            "count_mismatches": {},
            "identity_mismatches": {},
            "duplicate_identities": {},
            "weight_mismatches": {},
            "missing_columns": {},
            "expected_source_counts": {},
            "audit_error": type(exc).__name__,
        }


def _warehouse_readiness_findings(warehouse: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    audit_error = warehouse.get("audit_error")
    if isinstance(audit_error, str) and audit_error:
        findings.append(f"warehouse audit failed: {audit_error}")
    if warehouse.get("status") != "ok":
        findings.append("warehouse status is not ok")
    if warehouse.get("required_files_present") is not True:
        findings.append("required warehouse projections are missing or unreadable")
    if warehouse.get("synced") is not True:
        findings.append("research episode projection is not synced")
    if warehouse.get("projection_synced") is not True:
        findings.append("record-level projections are not synced")
    for field_name, finding in (
        ("count_mismatches", "projection counts mismatch source data"),
        ("identity_mismatches", "projection identities mismatch source data"),
        ("duplicate_identities", "projection duplicate identities detected"),
        ("weight_mismatches", "projection sample weight sums are invalid"),
        ("missing_columns", "projection required columns are missing"),
    ):
        value = warehouse.get(field_name)
        if isinstance(value, dict) and value:
            findings.append(finding)
    return findings


def _first_non_empty_mapping(*values: object) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return {}


def _first_not_none(*values: object) -> object:
    for value in values:
        if value is not None:
            return value
    return None


def _llm_embedding_model_from_method(embedding_method: str) -> str | None:
    parts = embedding_method.strip().split(":", 2)
    if len(parts) != 3 or parts[0] != "llm_embedding":
        return None
    model = parts[2].strip()
    return model or None


def _production_record_store_status(settings: Settings) -> dict[str, Any]:
    try:
        audit = audit_record_store(settings.project_root, deep=True)
    except Exception as exc:
        finding = f"deep record-store audit failed: {type(exc).__name__}"
        return {
            "schema_version": "nslab.production_record_store.v1",
            "passed": False,
            "status": "attention",
            "finding_count": 1,
            "findings": [finding],
            "deep": True,
            "record_count": None,
            "raw_record_count": None,
            "normalized_record_count": None,
            "raw_normalized_record_count_matches": None,
            "raw_record_counts_by_episode": {},
            "dropped_record_count": None,
            "extra_normalized_record_count": None,
            "quarantined_bundle_count": None,
            "quarantined_raw_record_count": None,
            "quarantined_normalized_record_count": None,
            "quarantined_record_count": None,
            "quarantine_reasons": {},
            "quarantine_normalization_skipped_reasons": {},
            "all_record_count": None,
            "staged_record_count": None,
            "episode_count": None,
            "training_eligible_record_count": None,
            "unknown_typed_payload_count": None,
            "raw_only_record_count": None,
            "all_unknown_typed_payload_count": None,
            "all_raw_only_record_count": None,
            "staged_unknown_typed_payload_count": None,
            "staged_raw_only_record_count": None,
            "duplicate_record_ids": [],
            "unknown_training_enabled_record_ids": [],
            "unknown_typed_payload_record_ids": [],
            "raw_only_record_ids": [],
            "all_unknown_typed_payload_record_ids": [],
            "all_raw_only_record_ids": [],
            "staged_unknown_typed_payload_record_ids": [],
            "staged_raw_only_record_ids": [],
            "payload_hash_mismatch_record_ids": [],
            "eligible_records_without_provenance": [],
            "brain_delta_count_mismatch_episode_ids": [],
            "brain_delta_record_id_mismatch_episode_ids": [],
            "brain_delta_training_eligible_mismatch_episode_ids": [],
            "brain_delta_type_count_mismatch_episode_ids": [],
            "records_with_raw_payload_hash_mismatch": [],
            "raw_block_hash_mismatch_episode_ids": [],
            "invalid_event_ticker_edge_path_type_record_ids": [],
            "event_ticker_edge_cutoff_provenance_violation_record_ids": [],
            "event_ticker_edge_source_ledger_cutoff_violation_record_ids": [],
            "invalid_company_memory_delta_known_at_record_ids": [],
            "backdated_company_memory_delta_known_at_record_ids": [],
            "issuer_day_event_level_weight_mismatch_record_ids": [],
        }
    findings = _string_list(audit.get("findings"))
    report_payload = record_store_report_payload(settings.project_root, audit)
    report_findings = _production_record_store_report_findings(report_payload)
    findings.extend(report_findings)
    if audit.get("deep") is not True:
        findings.append("deep record-store audit was not run")
    passed = (
        audit.get("passed") is True
        and audit.get("deep") is True
        and not report_findings
    )
    if not passed and not findings:
        findings.append("deep record-store audit failed")
    return {
        "schema_version": "nslab.production_record_store.v1",
        "passed": passed,
        "status": "ready" if passed else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "deep": audit.get("deep"),
        "record_count": audit.get("record_count"),
        "raw_record_count": report_payload.get("raw_record_count"),
        "normalized_record_count": report_payload.get("normalized_record_count"),
        "raw_normalized_record_count_matches": report_payload.get(
            "raw_normalized_record_count_matches",
        ),
        "raw_record_counts_by_episode": report_payload.get(
            "raw_record_counts_by_episode",
            {},
        ),
        "dropped_record_count": report_payload.get("dropped_record_count"),
        "extra_normalized_record_count": report_payload.get(
            "extra_normalized_record_count",
        ),
        "quarantined_bundle_count": report_payload.get("quarantined_bundle_count"),
        "quarantined_raw_record_count": report_payload.get(
            "quarantined_raw_record_count",
        ),
        "quarantined_normalized_record_count": report_payload.get(
            "quarantined_normalized_record_count",
        ),
        "quarantined_record_count": report_payload.get("quarantined_record_count"),
        "quarantine_reasons": report_payload.get("quarantine_reasons", {}),
        "quarantine_normalization_skipped_reasons": report_payload.get(
            "quarantine_normalization_skipped_reasons",
            {},
        ),
        "all_record_count": audit.get("all_record_count"),
        "staged_record_count": audit.get("staged_record_count"),
        "episode_count": audit.get("episode_count"),
        "training_eligible_record_count": audit.get("training_eligible_record_count"),
        "unknown_typed_payload_count": report_payload.get(
            "unknown_typed_payload_count",
        ),
        "raw_only_record_count": report_payload.get("raw_only_record_count"),
        "all_unknown_typed_payload_count": report_payload.get(
            "all_unknown_typed_payload_count",
        ),
        "all_raw_only_record_count": report_payload.get("all_raw_only_record_count"),
        "staged_unknown_typed_payload_count": report_payload.get(
            "staged_unknown_typed_payload_count",
        ),
        "staged_raw_only_record_count": report_payload.get(
            "staged_raw_only_record_count",
        ),
        "duplicate_record_ids": audit.get("duplicate_record_ids", []),
        "unknown_training_enabled_record_ids": audit.get(
            "unknown_training_enabled_record_ids",
            [],
        ),
        "unknown_typed_payload_record_ids": report_payload.get(
            "unknown_typed_payload_record_ids",
            [],
        ),
        "raw_only_record_ids": report_payload.get("raw_only_record_ids", []),
        "all_unknown_typed_payload_record_ids": report_payload.get(
            "all_unknown_typed_payload_record_ids",
            [],
        ),
        "all_raw_only_record_ids": report_payload.get(
            "all_raw_only_record_ids",
            [],
        ),
        "staged_unknown_typed_payload_record_ids": report_payload.get(
            "staged_unknown_typed_payload_record_ids",
            [],
        ),
        "staged_raw_only_record_ids": report_payload.get(
            "staged_raw_only_record_ids",
            [],
        ),
        "payload_hash_mismatch_record_ids": audit.get(
            "payload_hash_mismatch_record_ids",
            [],
        ),
        "eligible_records_without_provenance": audit.get(
            "eligible_records_without_provenance",
            [],
        ),
        "brain_delta_count_mismatch_episode_ids": audit.get(
            "brain_delta_count_mismatch_episode_ids",
            [],
        ),
        "brain_delta_record_id_mismatch_episode_ids": audit.get(
            "brain_delta_record_id_mismatch_episode_ids",
            [],
        ),
        "brain_delta_training_eligible_mismatch_episode_ids": audit.get(
            "brain_delta_training_eligible_mismatch_episode_ids",
            [],
        ),
        "brain_delta_type_count_mismatch_episode_ids": audit.get(
            "brain_delta_type_count_mismatch_episode_ids",
            [],
        ),
        "records_with_raw_payload_hash_mismatch": audit.get(
            "records_with_raw_payload_hash_mismatch",
            [],
        ),
        "raw_block_hash_mismatch_episode_ids": audit.get(
            "raw_block_hash_mismatch_episode_ids",
            [],
        ),
        "invalid_event_ticker_edge_path_type_record_ids": audit.get(
            "invalid_event_ticker_edge_path_type_record_ids",
            [],
        ),
        "event_ticker_edge_cutoff_provenance_violation_record_ids": audit.get(
            "event_ticker_edge_cutoff_provenance_violation_record_ids",
            [],
        ),
        "event_ticker_edge_source_ledger_cutoff_violation_record_ids": audit.get(
            "event_ticker_edge_source_ledger_cutoff_violation_record_ids",
            [],
        ),
        "invalid_company_memory_delta_known_at_record_ids": audit.get(
            "invalid_company_memory_delta_known_at_record_ids",
            [],
        ),
        "backdated_company_memory_delta_known_at_record_ids": audit.get(
            "backdated_company_memory_delta_known_at_record_ids",
            [],
        ),
        "issuer_day_event_level_weight_mismatch_record_ids": audit.get(
            "issuer_day_event_level_weight_mismatch_record_ids",
            [],
        ),
    }


def _production_record_store_report_findings(
    report_payload: dict[str, Any],
) -> list[str]:
    findings: list[str] = []
    raw_record_count = _int_from_mapping(report_payload, "raw_record_count")
    normalized_record_count = _int_from_mapping(
        report_payload,
        "normalized_record_count",
    )
    if (
        raw_record_count is not None
        and normalized_record_count is not None
        and raw_record_count != normalized_record_count
    ):
        findings.append(
            "raw/normalized record count mismatch: "
            f"raw_record_count={raw_record_count} "
            f"normalized_record_count={normalized_record_count}"
        )
    dropped_record_count = _int_from_mapping(report_payload, "dropped_record_count")
    if dropped_record_count is not None and dropped_record_count != 0:
        findings.append(f"dropped_record_count={dropped_record_count} expected 0")
    extra_normalized_record_count = _int_from_mapping(
        report_payload,
        "extra_normalized_record_count",
    )
    if (
        extra_normalized_record_count is not None
        and extra_normalized_record_count != 0
    ):
        findings.append(
            "extra_normalized_record_count="
            f"{extra_normalized_record_count} expected 0"
        )
    raw_only_record_count = _int_from_mapping(report_payload, "raw_only_record_count")
    if raw_only_record_count is not None and raw_only_record_count != 0:
        findings.append(
            f"raw_only_record_count={raw_only_record_count} expected 0 "
            "for accepted records"
        )
    quarantined_record_count = _int_from_mapping(
        report_payload,
        "quarantined_record_count",
    )
    if quarantined_record_count is not None and quarantined_record_count != 0:
        findings.append(
            f"quarantined_record_count={quarantined_record_count} expected 0"
        )
    quarantined_normalized_record_count = _int_from_mapping(
        report_payload,
        "quarantined_normalized_record_count",
    )
    if (
        quarantined_normalized_record_count is not None
        and quarantined_normalized_record_count != 0
    ):
        findings.append(
            "quarantined_normalized_record_count="
            f"{quarantined_normalized_record_count} expected 0"
        )
    quarantined_bundle_count = _int_from_mapping(
        report_payload,
        "quarantined_bundle_count",
    )
    if quarantined_bundle_count is not None and quarantined_bundle_count != 0:
        findings.append(
            f"quarantined_bundle_count={quarantined_bundle_count} expected 0"
        )
    return findings


def _safe_raw_block_filename(name: str) -> str:
    return name.replace("/", "__").replace("\\", "__")


def _imported_raw_block_status(
    *,
    root: Path,
    episode_dir: Path,
    envelope: dict[str, Any] | None,
    block_name: str,
) -> dict[str, Any]:
    raw_block_paths = envelope.get("raw_block_paths") if isinstance(envelope, dict) else None
    raw_block_paths = raw_block_paths if isinstance(raw_block_paths, dict) else {}
    raw_block_hashes = (
        envelope.get("raw_block_hashes") if isinstance(envelope, dict) else None
    )
    raw_block_hashes = raw_block_hashes if isinstance(raw_block_hashes, dict) else {}
    raw_block_counts = (
        envelope.get("raw_block_counts") if isinstance(envelope, dict) else None
    )
    raw_block_counts = raw_block_counts if isinstance(raw_block_counts, dict) else {}
    path_value = raw_block_paths.get(block_name)
    path_text = path_value if isinstance(path_value, str) and path_value else None
    path_listed = path_text is not None
    path = (
        root / path_text
        if path_text is not None
        else episode_dir / "raw_blocks" / _safe_raw_block_filename(block_name)
    )
    declared_sha256 = raw_block_hashes.get(block_name)
    declared_sha256 = declared_sha256 if isinstance(declared_sha256, str) else None
    declared_count = raw_block_counts.get(block_name)
    declared_count = (
        declared_count
        if isinstance(declared_count, int) and not isinstance(declared_count, bool)
        else None
    )
    exists = path.exists()
    try:
        observed_sha256 = sha256_text(path.read_text(encoding="utf-8")) if exists else None
    except OSError:
        observed_sha256 = None
    return {
        "path": relative_to_root(path, root),
        "path_listed": path_listed,
        "exists": exists,
        "declared_sha256": declared_sha256,
        "observed_sha256": observed_sha256,
        "hash_matches": (
            observed_sha256 == declared_sha256
            if observed_sha256 is not None and declared_sha256 is not None
            else None
        ),
        "declared_count": declared_count,
    }


def _direct_ingest_contract_raw_status(
    *,
    root: Path,
    episode_dir: Path,
    envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_status = _imported_raw_block_status(
        root=root,
        episode_dir=episode_dir,
        envelope=envelope,
        block_name="direct_ingest_contract.json",
    )
    contract: dict[str, Any] | None = None
    valid_json: bool | None = None
    if raw_status["exists"] is True:
        try:
            contract = _read_json_object(root / raw_status["path"])
            valid_json = True
        except ValueError:
            valid_json = False
    hard_gate_summary = (
        contract.get("hard_gate_summary") if isinstance(contract, dict) else None
    )
    hard_gate_summary = (
        hard_gate_summary if isinstance(hard_gate_summary, dict) else {}
    )
    fatal_blockers = contract.get("fatal_blockers") if isinstance(contract, dict) else None
    fatal_blocker_count = len(fatal_blockers) if isinstance(fatal_blockers, list) else None
    validation_parity_verified = hard_gate_summary.get(
        "direct_ingest_contract_validation_parity_verified"
    )
    if validation_parity_verified is None:
        validation_parity_verified = hard_gate_summary.get("schema_contract_verified")
    count_hash_parity_verified = hard_gate_summary.get(
        "direct_ingest_contract_count_hash_parity_verified"
    )
    if count_hash_parity_verified is None:
        count_hash_parity_verified = hard_gate_summary.get(
            "record_count_hash_parity_ready"
        )
    return {
        **raw_status,
        "valid_json": valid_json,
        "schema_version": contract.get("schema_version")
        if isinstance(contract, dict)
        else None,
        "direct_brain_ingest_ready": contract.get("direct_brain_ingest_ready")
        if isinstance(contract, dict)
        else None,
        "brain_eligible": contract.get("brain_eligible")
        if isinstance(contract, dict)
        else None,
        "requires_human_semantic_review": contract.get(
            "requires_human_semantic_review"
        )
        if isinstance(contract, dict)
        else None,
        "fatal_blocker_count": fatal_blocker_count,
        "validation_parity_verified": validation_parity_verified,
        "count_hash_parity_verified": count_hash_parity_verified,
    }


def _final_semantic_audit_raw_status(
    *,
    root: Path,
    episode_dir: Path,
    envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_status = _imported_raw_block_status(
        root=root,
        episode_dir=episode_dir,
        envelope=envelope,
        block_name="final_semantic_audit.jsonl",
    )
    row_count = 0
    invalid_line_count = 0
    fail_count = 0
    if raw_status["exists"] is True:
        try:
            lines = (root / raw_status["path"]).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
            invalid_line_count = 1
        for line in lines:
            if not line.strip():
                continue
            row_count += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                invalid_line_count += 1
                continue
            if not isinstance(payload, dict):
                invalid_line_count += 1
                continue
            if not _semantic_audit_row_passed(payload):
                fail_count += 1
    return {
        **raw_status,
        "row_count": row_count if raw_status["exists"] is True else None,
        "invalid_line_count": invalid_line_count
        if raw_status["exists"] is True
        else None,
        "fail_count": fail_count if raw_status["exists"] is True else None,
    }


def _semantic_audit_row_passed(payload: dict[str, Any]) -> bool:
    for field_name in ("semantic_verdict", "semantic_audit_status", "status"):
        value = payload.get(field_name)
        if isinstance(value, str) and value.upper() == "PASS":
            return True
    return False


def _imported_direct_ingest_raw_findings(
    *,
    inspection: dict[str, Any] | None,
    contract_status: dict[str, Any],
    final_audit_status: dict[str, Any],
) -> list[str]:
    if not isinstance(inspection, dict):
        return []
    findings: list[str] = []
    if inspection.get("direct_ingest_contract_present") is True:
        if contract_status["path_listed"] is not True:
            findings.append(
                "direct ingest contract raw block path missing from imported envelope"
            )
        if contract_status["exists"] is not True:
            findings.append("direct ingest contract raw block is missing")
        elif contract_status["valid_json"] is not True:
            findings.append("direct ingest contract raw block is invalid JSON")
        if contract_status["declared_sha256"] is None:
            findings.append(
                "direct ingest contract raw block hash missing from imported envelope"
            )
        elif contract_status["hash_matches"] is not True:
            findings.append("direct ingest contract raw block hash mismatch")
        checks = (
            (
                "direct ingest contract schema_version",
                contract_status.get("schema_version"),
                inspection.get("direct_ingest_contract_schema_version"),
            ),
            (
                "direct ingest contract direct_brain_ingest_ready",
                contract_status.get("direct_brain_ingest_ready"),
                inspection.get("direct_brain_ingest_ready"),
            ),
            (
                "direct ingest contract brain_eligible",
                contract_status.get("brain_eligible"),
                inspection.get("brain_eligible"),
            ),
            (
                "direct ingest contract requires_human_semantic_review",
                contract_status.get("requires_human_semantic_review"),
                inspection.get("requires_human_semantic_review"),
            ),
            (
                "direct ingest contract fatal blocker count",
                contract_status.get("fatal_blocker_count"),
                inspection.get("direct_ingest_fatal_blocker_count"),
            ),
            (
                "direct ingest contract validation parity",
                contract_status.get("validation_parity_verified"),
                inspection.get("direct_ingest_contract_validation_parity_verified"),
            ),
            (
                "direct ingest contract count/hash parity",
                contract_status.get("count_hash_parity_verified"),
                inspection.get("direct_ingest_contract_count_hash_parity_verified"),
            ),
        )
        for label, observed, expected in checks:
            if label == "direct ingest contract brain_eligible" and observed is None:
                continue
            if observed != expected:
                findings.append(f"{label}={observed!r} expected {expected!r}")
    if inspection.get("final_semantic_audit_present") is True:
        if final_audit_status["path_listed"] is not True:
            findings.append(
                "final semantic audit raw block path missing from imported envelope"
            )
        if final_audit_status["exists"] is not True:
            findings.append("final semantic audit raw block is missing")
        if final_audit_status["declared_sha256"] is None:
            findings.append(
                "final semantic audit raw block hash missing from imported envelope"
            )
        elif final_audit_status["hash_matches"] is not True:
            findings.append("final semantic audit raw block hash mismatch")
        expected_count = inspection.get("final_semantic_audit_count")
        if (
            isinstance(expected_count, int)
            and not isinstance(expected_count, bool)
            and final_audit_status.get("row_count") != expected_count
        ):
            findings.append(
                "final semantic audit raw block count does not match real smoke"
            )
        expected_fail_count = inspection.get("final_semantic_audit_fail_count")
        if (
            isinstance(expected_fail_count, int)
            and not isinstance(expected_fail_count, bool)
            and final_audit_status.get("fail_count") != expected_fail_count
        ):
            findings.append(
                "final semantic audit raw block fail count does not match real smoke"
            )
        if final_audit_status.get("invalid_line_count") not in {None, 0}:
            findings.append("final semantic audit raw block has invalid rows")
    return findings


def _real_bundle_import_status(
    settings: Settings,
    real_bundle_smoke: dict[str, Any],
) -> dict[str, Any]:
    selected = real_bundle_smoke.get("selected")
    selected = selected if isinstance(selected, dict) else None
    inspection = selected.get("inspection") if selected is not None else None
    inspection = inspection if isinstance(inspection, dict) else None
    episode_id = inspection.get("episode_id") if inspection is not None else None
    raw_bundle_sha256 = (
        inspection.get("raw_bundle_sha256") if inspection is not None else None
    )
    expected_record_count = _int_from_mapping(inspection, "normalized_record_count")
    expected_training_count = _int_from_mapping(
        inspection,
        "training_eligible_record_count",
    )
    expected_record_counts_by_type = (
        inspection.get("record_counts_by_type") if inspection is not None else None
    )
    expected_record_ids = (
        _string_list(inspection.get("normalized_record_ids"))
        if inspection is not None
        else []
    )
    base = {
        "schema_version": "nslab.real_bundle_import_status.v1",
        "applicable": real_bundle_smoke.get("status") == "passed",
        "selected_path": selected.get("path") if selected is not None else None,
        "episode_id": episode_id if isinstance(episode_id, str) else None,
        "raw_bundle_sha256": raw_bundle_sha256
        if isinstance(raw_bundle_sha256, str)
        else None,
        "expected_record_count": expected_record_count,
        "expected_training_eligible_record_count": expected_training_count,
        "expected_record_counts_by_type": expected_record_counts_by_type
        if isinstance(expected_record_counts_by_type, dict)
        else None,
        "expected_record_id_count": len(expected_record_ids)
        if expected_record_ids
        else None,
        "expected_record_ids": expected_record_ids or None,
        "quarantined_bundle_count": inspection.get("quarantined_bundle_count")
        if inspection is not None
        else None,
        "quarantined_raw_record_count": inspection.get("quarantined_raw_record_count")
        if inspection is not None
        else None,
        "quarantined_record_count": inspection.get("quarantined_record_count")
        if inspection is not None
        else None,
    }
    if real_bundle_smoke.get("status") != "passed":
        return {
            **base,
            "passed": False,
            "status": "not_applicable",
            "finding_count": 0,
            "findings": [],
        }

    findings: list[str] = []
    if inspection is None:
        findings.append("selected smoke bundle has no inspection")
        return {
            **base,
            "passed": False,
            "status": "attention",
            "finding_count": len(findings),
            "findings": findings,
        }
    if not isinstance(episode_id, str) or not episode_id:
        findings.append("selected smoke bundle has no episode_id")
        return {
            **base,
            "passed": False,
            "status": "attention",
            "finding_count": len(findings),
            "findings": findings,
        }
    if not isinstance(raw_bundle_sha256, str) or not raw_bundle_sha256:
        findings.append("selected smoke bundle has no raw_bundle_sha256")

    episode_dir = settings.project_root / "research" / "episodes" / episode_id
    envelope_path = episode_dir / "bundle_envelope.json"
    normalized_index_path = episode_dir / "normalized_episode_index.json"
    original_bundle_path = episode_dir / "original_bundle.md"
    validation_report_path = episode_dir / "validation_report.json"
    record_manifest_path = (
        settings.project_root / "memory" / "record_manifests" / f"{episode_id}.json"
    )
    envelope = _read_optional_json(envelope_path)
    normalized_index = _read_optional_json(normalized_index_path)
    validation_report = _read_optional_json(validation_report_path)
    record_manifest = _read_optional_json(record_manifest_path)
    record_path = settings.project_root / "memory" / "records" / f"{episode_id}.jsonl"
    record_manifest_records_file: object = None
    record_manifest_records_file_resolved: str | None = None
    if isinstance(record_manifest, dict):
        records_file = record_manifest.get("records_file")
        record_manifest_records_file = records_file
        if isinstance(records_file, str) and records_file:
            if Path(records_file).is_absolute():
                findings.append("record manifest records_file must be project-relative")
            else:
                resolved_record_path = _project_relative_artifact_path(
                    settings.project_root,
                    records_file,
                )
                if resolved_record_path is None:
                    findings.append("record manifest records_file escapes project root")
                else:
                    record_path = resolved_record_path
                    record_manifest_records_file_resolved = relative_to_root(
                        resolved_record_path,
                        settings.project_root,
                    )
        else:
            findings.append("record manifest records_file is missing")
    record_file_stats = _record_file_stats(record_path)
    validation_raw_record_ids: list[str] = []
    validation_normalized_record_ids: list[str] = []
    validation_raw_normalized_record_count_matches: object = None
    validation_missing_normalized_record_count: int | None = None
    validation_extra_normalized_record_count: int | None = None
    validation_missing_normalized_record_ids: list[str] = []
    validation_extra_normalized_record_ids: list[str] = []
    validation_raw_payload_hash_mismatch_record_ids: list[str] = []
    if envelope is None:
        findings.append("selected real bundle has not been imported into record store")
    else:
        adapter = inspection.get("adapter")
        if envelope.get("bundle_schema_version") != "nslab.research_bundle.v11":
            findings.append("imported envelope is not v11")
        if envelope.get("bundle_status") != "ACCEPT_FULL":
            findings.append("imported envelope is not ACCEPT_FULL")
        if adapter != "v23-direct-ingest" and envelope.get("blind_valid") is not True:
            findings.append("imported envelope is not blind_valid")
        if isinstance(raw_bundle_sha256, str) and (
            envelope.get("raw_bundle_sha256") != raw_bundle_sha256
        ):
            findings.append("imported envelope raw bundle sha does not match real smoke")
    if original_bundle_path.exists():
        if isinstance(raw_bundle_sha256, str) and (
            file_sha256(original_bundle_path) != raw_bundle_sha256
        ):
            findings.append("stored original bundle sha does not match real smoke")
    else:
        findings.append("stored original bundle is missing")
    if normalized_index is None:
        findings.append("normalized episode index for selected real bundle is missing")
    else:
        if _duplicate_strings(_string_list(normalized_index.get("record_ids"))):
            findings.append("normalized episode index has duplicate record IDs")
        if (
            isinstance(expected_record_count, int)
            and len(_string_list(normalized_index.get("record_ids")))
            != expected_record_count
        ):
            findings.append("normalized episode index count does not match real smoke")
        if expected_record_ids and (
            sorted(_string_list(normalized_index.get("record_ids")))
            != sorted(expected_record_ids)
        ):
            findings.append("normalized episode index IDs do not match real smoke")
        if (
            isinstance(expected_training_count, int)
            and normalized_index.get("training_eligible_record_count")
            != expected_training_count
        ):
            findings.append(
                "normalized episode index training eligible count does not match real smoke"
            )
        if isinstance(expected_record_counts_by_type, dict) and (
            normalized_index.get("record_count_by_type")
            != expected_record_counts_by_type
        ):
            findings.append("normalized episode index type counts do not match real smoke")
    if validation_report is None:
        findings.append("validation report for selected real bundle is missing")
    else:
        validation_raw_record_ids = _string_list(validation_report.get("raw_record_ids"))
        validation_normalized_record_ids = _string_list(
            validation_report.get("normalized_record_ids")
        )
        validation_raw_normalized_record_count_matches = validation_report.get(
            "raw_normalized_record_count_matches"
        )
        validation_missing_normalized_record_ids = _string_list(
            validation_report.get("missing_normalized_record_ids")
        )
        validation_missing_normalized_record_count = _int_from_mapping(
            validation_report,
            "missing_normalized_record_count",
        )
        if (
            validation_missing_normalized_record_count is None
            and validation_missing_normalized_record_ids
        ):
            validation_missing_normalized_record_count = len(
                validation_missing_normalized_record_ids
            )
        validation_extra_normalized_record_ids = _string_list(
            validation_report.get("extra_normalized_record_ids")
        )
        validation_extra_normalized_record_count = _int_from_mapping(
            validation_report,
            "extra_normalized_record_count",
        )
        if (
            validation_extra_normalized_record_count is None
            and validation_extra_normalized_record_ids
        ):
            validation_extra_normalized_record_count = len(
                validation_extra_normalized_record_ids
            )
        validation_raw_payload_hash_mismatch_record_ids = _string_list(
            validation_report.get("raw_payload_hash_mismatch_record_ids")
        )
        if validation_report.get("passed") is not True:
            findings.append("validation report did not pass")
        if validation_report.get("import_loss_audit_passed") is not True:
            findings.append("validation report import loss audit did not pass")
        if validation_report.get("typed_payload_valid") is not True:
            findings.append("validation report typed payload validation did not pass")
        if validation_report.get("record_count_matches_manifest") is not True:
            findings.append("validation report record count manifest parity failed")
        if validation_report.get("training_eligible_count_matches_manifest") is not True:
            findings.append(
                "validation report training eligible manifest parity failed"
            )
        if validation_report.get("record_id_set_matches_raw") is not True:
            findings.append("validation report raw record ID parity failed")
        if (
            validation_raw_normalized_record_count_matches is not None
            and validation_raw_normalized_record_count_matches is not True
        ):
            findings.append(
                "validation report raw/normalized record count parity failed"
            )
        if validation_report.get("record_type_counts_match_raw") is not True:
            findings.append("validation report raw record type parity failed")
        if validation_report.get("training_eligible_count_matches_raw") is not True:
            findings.append(
                "validation report raw training eligible parity failed"
            )
        if validation_report.get("raw_payload_hashes_match") is not True:
            findings.append("validation report raw payload hash parity failed")
        if _duplicate_strings(validation_raw_record_ids):
            findings.append("validation report raw record IDs contain duplicates")
        if _duplicate_strings(validation_normalized_record_ids):
            findings.append("validation report normalized record IDs contain duplicates")
        if expected_record_ids and validation_raw_record_ids and (
            sorted(validation_raw_record_ids) != sorted(expected_record_ids)
        ):
            findings.append("validation report raw record IDs do not match real smoke")
        if expected_record_ids and validation_normalized_record_ids and (
            sorted(validation_normalized_record_ids) != sorted(expected_record_ids)
        ):
            findings.append(
                "validation report normalized record IDs do not match real smoke"
            )
        if validation_missing_normalized_record_ids or (
            validation_missing_normalized_record_count is not None
            and validation_missing_normalized_record_count != 0
        ):
            findings.append("validation report has missing normalized record IDs")
        if validation_extra_normalized_record_ids or (
            validation_extra_normalized_record_count is not None
            and validation_extra_normalized_record_count != 0
        ):
            findings.append("validation report has extra normalized record IDs")
        if validation_raw_payload_hash_mismatch_record_ids:
            findings.append("validation report has raw payload hash mismatches")
        if (
            isinstance(expected_record_count, int)
            and validation_report.get("record_count") != expected_record_count
        ):
            findings.append("validation report count does not match real smoke")
        if (
            isinstance(expected_training_count, int)
            and validation_report.get("training_eligible_record_count")
            != expected_training_count
        ):
            findings.append(
                "validation report training eligible count does not match real smoke"
            )
        if isinstance(expected_record_counts_by_type, dict) and (
            validation_report.get("raw_record_counts_by_type")
            != expected_record_counts_by_type
        ):
            findings.append("validation report raw type counts do not match real smoke")
    if record_manifest is None:
        findings.append("record manifest for selected real bundle is missing")
    else:
        if _duplicate_strings(_string_list(record_manifest.get("record_ids"))):
            findings.append("record manifest has duplicate record IDs")
        if record_manifest.get("accepted") is not True:
            findings.append("record manifest for selected real bundle is not accepted")
        if (
            isinstance(expected_record_count, int)
            and record_manifest.get("record_count") != expected_record_count
        ):
            findings.append("record manifest count does not match real smoke")
        if expected_record_ids and (
            sorted(_string_list(record_manifest.get("record_ids")))
            != sorted(expected_record_ids)
        ):
            findings.append("record manifest IDs do not match real smoke")
        if (
            isinstance(expected_training_count, int)
            and record_manifest.get("training_eligible_record_count")
            != expected_training_count
        ):
            findings.append(
                "record manifest training eligible count does not match real smoke"
            )
        if isinstance(expected_record_counts_by_type, dict) and (
            record_manifest.get("record_counts_by_type")
            != expected_record_counts_by_type
        ):
            findings.append("record manifest type counts do not match real smoke")
        manifest_records_sha = record_manifest.get("records_sha256")
        if (
            isinstance(manifest_records_sha, str)
            and record_file_stats["text_sha256"] != manifest_records_sha
        ):
            findings.append("record JSONL sha does not match record manifest")

    if record_file_stats["exists"] is not True:
        findings.append("record JSONL for selected real bundle is missing")
    else:
        if (
            record_file_stats["invalid_line_count"] != 0
            or record_file_stats["invalid_envelope_count"] != 0
        ):
            findings.append("record JSONL for selected real bundle has invalid rows")
        if record_file_stats["duplicate_record_ids"]:
            findings.append("record JSONL has duplicate record IDs")
        if (
            isinstance(expected_record_count, int)
            and record_file_stats["record_count"] != expected_record_count
        ):
            findings.append("record JSONL count does not match real smoke")
        if expected_record_ids and (
            sorted(_string_list(record_file_stats["record_ids"]))
            != sorted(expected_record_ids)
        ):
            findings.append("record JSONL IDs do not match real smoke")
        if (
            isinstance(expected_training_count, int)
            and record_file_stats["training_eligible_record_count"]
            != expected_training_count
        ):
            findings.append(
                "record JSONL training eligible count does not match real smoke"
            )
        if isinstance(expected_record_counts_by_type, dict) and (
            record_file_stats["record_counts_by_type"] != expected_record_counts_by_type
        ):
            findings.append("record JSONL type counts do not match real smoke")
    direct_contract_raw_status = _direct_ingest_contract_raw_status(
        root=settings.project_root,
        episode_dir=episode_dir,
        envelope=envelope,
    )
    final_semantic_audit_raw_status = _final_semantic_audit_raw_status(
        root=settings.project_root,
        episode_dir=episode_dir,
        envelope=envelope,
    )
    findings.extend(
        _imported_direct_ingest_raw_findings(
            inspection=inspection,
            contract_status=direct_contract_raw_status,
            final_audit_status=final_semantic_audit_raw_status,
        )
    )
    if (
        normalized_index is not None
        and record_manifest is not None
        and record_file_stats["exists"] is True
        and record_file_stats["invalid_line_count"] == 0
    ):
        record_ids = sorted(_string_list(record_file_stats["record_ids"]))
        if sorted(_string_list(record_manifest.get("record_ids"))) != record_ids:
            findings.append("record manifest IDs do not match record JSONL")
        if sorted(_string_list(normalized_index.get("record_ids"))) != record_ids:
            findings.append("normalized episode index IDs do not match record JSONL")

    return {
        **base,
        "envelope_path": relative_to_root(envelope_path, settings.project_root),
        "envelope_exists": envelope is not None,
        "normalized_index_path": relative_to_root(
            normalized_index_path,
            settings.project_root,
        ),
        "normalized_index_exists": normalized_index is not None,
        "original_bundle_path": relative_to_root(
            original_bundle_path,
            settings.project_root,
        ),
        "original_bundle_exists": original_bundle_path.exists(),
        "validation_report_path": relative_to_root(
            validation_report_path,
            settings.project_root,
        ),
        "validation_report_exists": validation_report is not None,
        "validation_report_passed": (
            validation_report.get("passed") if isinstance(validation_report, dict) else None
        ),
        "validation_report_import_loss_audit_passed": (
            validation_report.get("import_loss_audit_passed")
            if isinstance(validation_report, dict)
            else None
        ),
        "validation_report_raw_record_id_count": len(validation_raw_record_ids)
        if validation_raw_record_ids
        else None,
        "validation_report_normalized_record_id_count": len(
            validation_normalized_record_ids
        )
        if validation_normalized_record_ids
        else None,
        "validation_report_raw_normalized_record_count_matches": (
            validation_raw_normalized_record_count_matches
        ),
        "validation_report_missing_normalized_record_count": (
            validation_missing_normalized_record_count
        ),
        "validation_report_extra_normalized_record_count": (
            validation_extra_normalized_record_count
        ),
        "validation_report_missing_normalized_record_ids": (
            validation_missing_normalized_record_ids or None
        ),
        "validation_report_extra_normalized_record_ids": (
            validation_extra_normalized_record_ids or None
        ),
        "validation_report_raw_payload_hash_mismatch_record_ids": (
            validation_raw_payload_hash_mismatch_record_ids or None
        ),
        "record_manifest_path": relative_to_root(
            record_manifest_path,
            settings.project_root,
        ),
        "record_manifest_exists": record_manifest is not None,
        "record_manifest_records_file": record_manifest_records_file,
        "record_manifest_records_file_resolved": record_manifest_records_file_resolved,
        "record_path": relative_to_root(record_path, settings.project_root),
        "record_file_exists": record_file_stats["exists"],
        "record_file_sha256": record_file_stats["sha256"],
        "record_file_text_sha256": record_file_stats["text_sha256"],
        "observed_record_count": record_file_stats["record_count"],
        "observed_training_eligible_record_count": record_file_stats[
            "training_eligible_record_count"
        ],
        "observed_record_counts_by_type": record_file_stats["record_counts_by_type"],
        "observed_record_ids": record_file_stats["record_ids"],
        "duplicate_record_ids": record_file_stats["duplicate_record_ids"],
        "record_file_invalid_line_count": record_file_stats["invalid_line_count"],
        "record_file_invalid_envelope_count": record_file_stats[
            "invalid_envelope_count"
        ],
        "direct_ingest_contract_raw_block_path": direct_contract_raw_status["path"],
        "direct_ingest_contract_raw_block_path_listed": direct_contract_raw_status[
            "path_listed"
        ],
        "direct_ingest_contract_raw_block_exists": direct_contract_raw_status[
            "exists"
        ],
        "direct_ingest_contract_raw_block_hash_matches": direct_contract_raw_status[
            "hash_matches"
        ],
        "direct_ingest_contract_raw_block_valid_json": direct_contract_raw_status[
            "valid_json"
        ],
        "direct_ingest_contract_schema_version": direct_contract_raw_status[
            "schema_version"
        ],
        "direct_brain_ingest_ready": direct_contract_raw_status[
            "direct_brain_ingest_ready"
        ],
        "brain_eligible": direct_contract_raw_status["brain_eligible"],
        "requires_human_semantic_review": direct_contract_raw_status[
            "requires_human_semantic_review"
        ],
        "direct_ingest_fatal_blocker_count": direct_contract_raw_status[
            "fatal_blocker_count"
        ],
        "direct_ingest_contract_validation_parity_verified": (
            direct_contract_raw_status["validation_parity_verified"]
        ),
        "direct_ingest_contract_count_hash_parity_verified": (
            direct_contract_raw_status["count_hash_parity_verified"]
        ),
        "final_semantic_audit_raw_block_path": final_semantic_audit_raw_status[
            "path"
        ],
        "final_semantic_audit_raw_block_path_listed": (
            final_semantic_audit_raw_status["path_listed"]
        ),
        "final_semantic_audit_raw_block_exists": final_semantic_audit_raw_status[
            "exists"
        ],
        "final_semantic_audit_raw_block_hash_matches": (
            final_semantic_audit_raw_status["hash_matches"]
        ),
        "final_semantic_audit_count": final_semantic_audit_raw_status["row_count"],
        "final_semantic_audit_fail_count": final_semantic_audit_raw_status[
            "fail_count"
        ],
        "final_semantic_audit_invalid_line_count": (
            final_semantic_audit_raw_status["invalid_line_count"]
        ),
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
    }


def _llm_full_brain_status(
    settings: Settings,
    *,
    build_mode: object,
    catalog_only: object,
    catalog_mode_reason: object,
    deprecated_mode_alias: object,
    production_eligible: object,
    current_brain_version: object,
    expected_source_record_count: int | None,
) -> dict[str, Any]:
    current_dir = settings.project_root / "brain" / "current"
    compile_manifest_path = current_dir / "llm_compile_manifest.json"
    compiled_claims_path = current_dir / "compiled_claims.jsonl"
    compile_report_path = settings.project_root / "diagnostics" / "brain_compile_report.json"
    compile_manifest = _read_optional_json(compile_manifest_path)
    compile_report = _read_optional_json(compile_report_path)
    compile_run = (
        compile_report.get("llm_compile_run")
        if isinstance(compile_report, dict)
        else None
    )
    compile_run = compile_run if isinstance(compile_run, dict) else None
    record_id_stats = _brain_record_store_id_stats(settings.project_root)
    known_record_ids = (
        record_id_stats["record_ids"]
        if record_id_stats["readable"] is True
        else None
    )
    known_records_by_id = (
        record_id_stats["records_by_id"]
        if record_id_stats["readable"] is True
        else None
    )
    compiled_claim_stats = _compiled_claim_file_stats(
        compiled_claims_path,
        known_record_ids=known_record_ids,
        known_records_by_id=known_records_by_id,
    )
    category_file_stats = _brain_category_file_stats(current_dir)
    category_manifest_stats = _llm_compile_category_manifest_stats(
        compile_manifest,
        compiled_claim_stats["claim_ids"],
        known_record_ids=known_record_ids,
    )
    record_shard_manifest_stats = _llm_compile_record_shard_manifest_stats(
        compile_manifest,
        known_record_ids=known_record_ids,
    )
    manifest_prompt_hash_stats = _llm_full_compile_prompt_hash_stats(
        compile_manifest,
    )
    run_prompt_hash_stats = _llm_full_compile_prompt_hash_stats(
        compile_run,
    )
    run_trace_evidence = _production_llm_trace_evidence_status(
        settings.project_root,
        manifest_prompt_hashes=set(run_prompt_hash_stats["prompt_hashes"]),
    )
    compiled_claim_count = compiled_claim_stats["line_count"]
    valid_compiled_claim_count = compiled_claim_stats["valid_claim_count"]
    findings: list[str] = []
    status = {
        "schema_version": "nslab.production_llm_full_brain.v1",
        "build_mode": build_mode if isinstance(build_mode, str) else None,
        "catalog_only": catalog_only if isinstance(catalog_only, bool) else None,
        "catalog_mode_reason": (
            catalog_mode_reason if isinstance(catalog_mode_reason, str) else None
        ),
        "deprecated_mode_alias": (
            deprecated_mode_alias
            if isinstance(deprecated_mode_alias, bool)
            else None
        ),
        "production_eligible": (
            production_eligible if isinstance(production_eligible, bool) else None
        ),
        "current_brain_version": current_brain_version
        if isinstance(current_brain_version, str)
        else None,
        "expected_source_record_count": expected_source_record_count,
        "applicable": build_mode == "llm-full",
        "compile_manifest_path": relative_to_root(
            compile_manifest_path,
            settings.project_root,
        ),
        "compile_manifest_exists": compile_manifest is not None,
        "compiled_claims_path": relative_to_root(
            compiled_claims_path,
            settings.project_root,
        ),
        "compiled_claims_exists": compiled_claims_path.exists(),
        "compiled_claim_jsonl_count": compiled_claim_count,
        "compiled_claim_valid_count": valid_compiled_claim_count,
        "compiled_claim_invalid_line_count": compiled_claim_stats[
            "invalid_line_count"
        ],
        "duplicate_compiled_claim_ids": compiled_claim_stats["duplicate_claim_ids"],
        "compiled_claim_invalid_categories": compiled_claim_stats[
            "invalid_categories"
        ],
        "compiled_claims_without_supporting_records": compiled_claim_stats[
            "claims_without_supporting_records"
        ],
        "compiled_claims_with_unknown_supporting_records": compiled_claim_stats[
            "claims_with_unknown_supporting_records"
        ],
        "compiled_claims_with_unknown_contradicting_records": compiled_claim_stats[
            "claims_with_unknown_contradicting_records"
        ],
        "compiled_claims_without_supporting_episodes": compiled_claim_stats[
            "claims_without_supporting_episodes"
        ],
        "compiled_claims_with_unknown_supporting_episodes": compiled_claim_stats[
            "claims_with_unknown_supporting_episodes"
        ],
        "compiled_claims_with_unknown_contradicting_episodes": compiled_claim_stats[
            "claims_with_unknown_contradicting_episodes"
        ],
        "compiled_claim_episode_record_mismatches": compiled_claim_stats[
            "episode_record_mismatches"
        ],
        "compiled_claim_temporal_leaks": compiled_claim_stats["temporal_leaks"],
        "validated_compiled_claims_without_contradictions": compiled_claim_stats[
            "validated_without_contradictions"
        ],
        "validated_compiled_claims_with_single_episode": compiled_claim_stats[
            "validated_single_episode"
        ],
        "compiled_claim_supporting_record_id_count": compiled_claim_stats[
            "supporting_record_id_count"
        ],
        "compiled_claim_contradicting_record_id_count": compiled_claim_stats[
            "contradicting_record_id_count"
        ],
        "record_store_readable_for_compiled_claims": record_id_stats["readable"],
        "record_store_record_count_for_compiled_claims": record_id_stats["record_count"],
        "record_store_error_for_compiled_claims": record_id_stats["error"],
        "brain_category_expected_file_count": len(BRAIN_FILES),
        "brain_category_existing_file_count": category_file_stats["existing_count"],
        "brain_category_missing_files": category_file_stats["missing_files"],
        "brain_category_empty_files": category_file_stats["empty_files"],
        "brain_category_unreadable_files": category_file_stats["unreadable_files"],
        "category_manifest_expected_count": len(BRAIN_FILES),
        "category_manifest_observed_count": category_manifest_stats["observed_count"],
        "category_manifest_schema_mismatches": category_manifest_stats[
            "schema_mismatches"
        ],
        "category_manifest_source_count_mismatches": category_manifest_stats[
            "source_count_mismatches"
        ],
        "category_manifest_compiled_claim_count_mismatches": (
            category_manifest_stats["compiled_claim_count_mismatches"]
        ),
        "category_manifest_unknown_compiled_claim_ids": category_manifest_stats[
            "unknown_compiled_claim_ids"
        ],
        "category_manifest_unknown_source_record_ids": category_manifest_stats[
            "unknown_source_record_ids"
        ],
        "record_shard_manifest_observed_count": record_shard_manifest_stats[
            "observed_count"
        ],
        "record_shard_manifest_record_id_count": record_shard_manifest_stats[
            "record_id_count"
        ],
        "record_shard_manifest_unique_record_id_count": record_shard_manifest_stats[
            "unique_record_id_count"
        ],
        "record_shard_manifest_schema_mismatches": record_shard_manifest_stats[
            "schema_mismatches"
        ],
        "record_shard_manifest_count_mismatches": record_shard_manifest_stats[
            "count_mismatches"
        ],
        "record_shard_manifest_duplicate_record_ids": record_shard_manifest_stats[
            "duplicate_record_ids"
        ],
        "record_shard_manifest_unknown_record_ids": record_shard_manifest_stats[
            "unknown_record_ids"
        ],
        "record_shard_manifest_missing_record_ids": record_shard_manifest_stats[
            "missing_record_ids"
        ],
        "compile_report_path": relative_to_root(
            compile_report_path,
            settings.project_root,
        ),
        "compile_report_exists": compile_report is not None,
        "compile_report_schema_version": compile_report.get("schema_version")
        if isinstance(compile_report, dict)
        else None,
        "expected_compile_report_schema_version": (
            BRAIN_COMPILE_DIAGNOSTICS_SCHEMA_VERSION
        ),
        "compile_run_present": compile_run is not None,
        "compile_run_schema_version": compile_run.get("schema_version")
        if isinstance(compile_run, dict)
        else None,
        "expected_compile_run_schema_version": LLM_FULL_COMPILE_RUN_SCHEMA_VERSION,
        "provider": compile_manifest.get("provider")
        if isinstance(compile_manifest, dict)
        else None,
        "model": compile_manifest.get("model")
        if isinstance(compile_manifest, dict)
        else None,
        "compile_manifest_schema_version": compile_manifest.get("schema_version")
        if isinstance(compile_manifest, dict)
        else None,
        "expected_compile_manifest_schema_version": (
            LLM_FULL_COMPILE_MANIFEST_SCHEMA_VERSION
        ),
        "compiler_version": compile_manifest.get("compiler_version")
        if isinstance(compile_manifest, dict)
        else None,
        "expected_compiler_version": LLM_FULL_COMPILER_VERSION,
        "configured_model": settings.llm.model,
        "brain_version": compile_manifest.get("brain_version")
        if isinstance(compile_manifest, dict)
        else None,
        "source_record_count": _int_from_mapping(compile_manifest, "source_record_count"),
        "compiled_claim_count": _int_from_mapping(compile_manifest, "compiled_claim_count"),
        "record_shard_count": _int_from_mapping(compile_manifest, "record_shard_count"),
        "category_count": _int_from_mapping(compile_manifest, "category_count"),
        "llm_generation_count": _int_from_mapping(compile_manifest, "llm_generation_count"),
        "run_brain_version": compile_run.get("brain_version")
        if isinstance(compile_run, dict)
        else None,
        "compile_report_brain_version": compile_report.get("brain_version")
        if isinstance(compile_report, dict)
        else None,
        "run_llm_generation_count": _int_from_mapping(compile_run, "llm_generation_count"),
        "run_llm_live_call_count": _int_from_mapping(compile_run, "llm_live_call_count"),
        "run_llm_cache_hit_count": _int_from_mapping(compile_run, "llm_cache_hit_count"),
        "run_all_outputs_from_cache": compile_run.get("all_outputs_from_cache")
        if isinstance(compile_run, dict)
        else None,
        "manifest_llm_prompt_hash_count": manifest_prompt_hash_stats["prompt_hash_count"],
        "manifest_llm_unique_prompt_hash_count": manifest_prompt_hash_stats[
            "unique_prompt_hash_count"
        ],
        "manifest_llm_missing_prompt_hash_fields": manifest_prompt_hash_stats[
            "missing_fields"
        ],
        "manifest_llm_duplicate_prompt_hashes": manifest_prompt_hash_stats[
            "duplicate_prompt_hashes"
        ],
        "run_llm_prompt_hash_count": run_prompt_hash_stats["prompt_hash_count"],
        "run_llm_unique_prompt_hash_count": run_prompt_hash_stats[
            "unique_prompt_hash_count"
        ],
        "run_llm_prompt_hashes": run_prompt_hash_stats["prompt_hashes"],
        "run_llm_missing_prompt_hash_fields": run_prompt_hash_stats["missing_fields"],
        "run_llm_duplicate_prompt_hashes": run_prompt_hash_stats[
            "duplicate_prompt_hashes"
        ],
        "run_llm_trace_evidence": run_trace_evidence,
    }
    if catalog_only is True:
        findings.append("current manifest is catalog_only")
    if build_mode != "llm-full":
        observed_mode = build_mode if isinstance(build_mode, str) and build_mode else "missing"
        findings.append(f"current manifest build_mode is {observed_mode}, not llm-full")
        if compile_manifest is None:
            findings.append("llm-full compile manifest is missing")
        if not compiled_claims_path.exists():
            findings.append("compiled claims JSONL is missing")
        return {
            **status,
            "passed": False,
            "status": "attention",
            "finding_count": len(findings),
            "findings": findings,
        }
    if production_eligible is False:
        findings.append("current manifest is not production_eligible")
    if compile_manifest is None:
        findings.append("llm-full compile manifest is missing")
    else:
        manifest_schema_version = status["compile_manifest_schema_version"]
        if manifest_schema_version != LLM_FULL_COMPILE_MANIFEST_SCHEMA_VERSION:
            observed_schema = (
                manifest_schema_version
                if isinstance(manifest_schema_version, str) and manifest_schema_version
                else "missing"
            )
            findings.append(
                "llm-full compile manifest schema_version is "
                f"{observed_schema}, not {LLM_FULL_COMPILE_MANIFEST_SCHEMA_VERSION}"
            )
        provider = status["provider"]
        model = status["model"]
        if not isinstance(provider, str) or not provider or provider.lower() == "mock":
            findings.append("llm-full compile provider is missing or mock")
        elif provider.strip().lower() != settings.llm_provider.strip().lower():
            findings.append("llm-full compile provider does not match configured provider")
        if not isinstance(model, str) or not model or "mock" in model.lower():
            findings.append("llm-full compile model is missing or mock")
        elif settings.llm.model and model.strip() != settings.llm.model.strip():
            findings.append("llm-full compile model does not match configured model")
        compiler_version = status["compiler_version"]
        if compiler_version != LLM_FULL_COMPILER_VERSION:
            observed_version = (
                compiler_version
                if isinstance(compiler_version, str) and compiler_version
                else "missing"
            )
            findings.append(
                "llm-full compile compiler_version is "
                f"{observed_version}, not {LLM_FULL_COMPILER_VERSION}"
            )
        manifest_brain_version = status["brain_version"]
        if (
            isinstance(current_brain_version, str)
            and current_brain_version
            and manifest_brain_version != current_brain_version
        ):
            findings.append("llm-full compile manifest does not match current brain")
        source_record_count = status["source_record_count"]
        if not isinstance(source_record_count, int) or source_record_count <= 0:
            findings.append("llm-full compile source records are missing")
        elif (
            isinstance(expected_source_record_count, int)
            and source_record_count != expected_source_record_count
        ):
            findings.append("llm-full compile source record count does not match coverage")
        manifest_claim_count = status["compiled_claim_count"]
        if not isinstance(manifest_claim_count, int) or manifest_claim_count <= 0:
            findings.append("llm-full compile manifest has no compiled claims")
        elif manifest_claim_count != valid_compiled_claim_count:
            findings.append(
                "llm-full compiled claim count does not match valid JSONL claims"
            )
        record_shard_count = status["record_shard_count"]
        if not isinstance(record_shard_count, int) or record_shard_count <= 0:
            findings.append("llm-full record shard accounting is missing")
        if record_shard_manifest_stats["schema_mismatches"]:
            findings.append("llm-full compile manifest record shards are invalid")
        if record_shard_manifest_stats["count_mismatches"]:
            findings.append(
                "llm-full compile manifest record shard counts are inconsistent"
            )
        if record_shard_manifest_stats["duplicate_record_ids"]:
            findings.append(
                "llm-full compile manifest record shards contain duplicate record IDs"
            )
        if record_shard_manifest_stats["unknown_record_ids"]:
            findings.append(
                "llm-full compile manifest record shards reference unknown record IDs"
            )
        if record_shard_manifest_stats["missing_record_ids"]:
            findings.append(
                "llm-full compile manifest record shards do not cover record store IDs"
            )
        category_count = status["category_count"]
        if category_count != len(BRAIN_FILES):
            findings.append("llm-full category count does not match brain category files")
        if category_manifest_stats["schema_mismatches"]:
            findings.append(
                "llm-full compile manifest categories do not match canonical brain files"
            )
        if category_manifest_stats["source_count_mismatches"]:
            findings.append(
                "llm-full compile manifest category source counts are inconsistent"
            )
        if category_manifest_stats["compiled_claim_count_mismatches"]:
            findings.append(
                "llm-full compile manifest category compiled claim counts are inconsistent"
            )
        if category_manifest_stats["unknown_compiled_claim_ids"]:
            findings.append(
                "llm-full compile manifest references unknown compiled claim IDs"
            )
        if category_manifest_stats["unknown_source_record_ids"]:
            findings.append(
                "llm-full compile manifest categories reference unknown source record IDs"
            )
        if category_file_stats["missing_files"]:
            findings.append("llm-full brain category files are missing")
        if category_file_stats["empty_files"]:
            findings.append("llm-full brain category files are empty")
        if category_file_stats["unreadable_files"]:
            findings.append("llm-full brain category files are unreadable")
        manifest_generation_count = status["llm_generation_count"]
        if not isinstance(manifest_generation_count, int) or manifest_generation_count <= 0:
            findings.append("llm-full LLM generation accounting is missing")
        if compile_run is None:
            findings.append("llm-full compile run diagnostics are missing")
        else:
            report_schema_version = status["compile_report_schema_version"]
            if report_schema_version != BRAIN_COMPILE_DIAGNOSTICS_SCHEMA_VERSION:
                observed_schema = (
                    report_schema_version
                    if isinstance(report_schema_version, str) and report_schema_version
                    else "missing"
                )
                findings.append(
                    "llm-full compile report schema_version is "
                    f"{observed_schema}, not {BRAIN_COMPILE_DIAGNOSTICS_SCHEMA_VERSION}"
                )
            run_schema_version = status["compile_run_schema_version"]
            if run_schema_version != LLM_FULL_COMPILE_RUN_SCHEMA_VERSION:
                observed_schema = (
                    run_schema_version
                    if isinstance(run_schema_version, str) and run_schema_version
                    else "missing"
                )
                findings.append(
                    "llm-full compile run schema_version is "
                    f"{observed_schema}, not {LLM_FULL_COMPILE_RUN_SCHEMA_VERSION}"
                )
            brain_version = status["brain_version"]
            run_brain_version = status["run_brain_version"]
            if (
                isinstance(brain_version, str)
                and brain_version
                and run_brain_version != brain_version
            ):
                findings.append("llm-full compile run diagnostics do not match current brain")
            if (
                isinstance(current_brain_version, str)
                and current_brain_version
                and run_brain_version != current_brain_version
            ):
                findings.append("llm-full compile run is stale for current brain")
            report_brain_version = status["compile_report_brain_version"]
            if (
                isinstance(current_brain_version, str)
                and current_brain_version
                and report_brain_version != current_brain_version
            ):
                findings.append("llm-full compile report is stale for current brain")
            generation_count = status["run_llm_generation_count"]
            live_call_count = status["run_llm_live_call_count"]
            cache_hit_count = status["run_llm_cache_hit_count"]
            if (
                not isinstance(generation_count, int)
                or not isinstance(live_call_count, int)
                or not isinstance(cache_hit_count, int)
                or generation_count <= 0
                or live_call_count + cache_hit_count != generation_count
            ):
                findings.append("llm-full LLM generation cache/live-call accounting is invalid")
            elif live_call_count <= 0 or status["run_all_outputs_from_cache"] is True:
                findings.append("llm-full production compile has no live LLM calls")
            if status["compiler_version"] == LLM_FULL_COMPILER_VERSION:
                if run_prompt_hash_stats["missing_fields"]:
                    findings.append("llm-full compile run prompt hashes are missing")
                if (
                    isinstance(generation_count, int)
                    and run_prompt_hash_stats["prompt_hash_count"] != generation_count
                ):
                    findings.append(
                        "llm-full compile run prompt hash accounting does not match "
                        "generation count"
                    )
                manifest_hashes = set(manifest_prompt_hash_stats["prompt_hashes"])
                run_hashes = set(run_prompt_hash_stats["prompt_hashes"])
                if manifest_hashes and run_hashes and manifest_hashes != run_hashes:
                    findings.append(
                        "llm-full compile manifest and run prompt hashes differ"
                    )
                for trace_finding in run_trace_evidence["findings"]:
                    findings.append(f"llm-full compile run {trace_finding}")
    if not compiled_claims_path.exists():
        findings.append("compiled claims JSONL is missing")
    elif compiled_claim_count <= 0:
        findings.append("compiled claims JSONL is empty")
    elif compiled_claim_stats["invalid_line_count"] != 0:
        findings.append("compiled claims JSONL has invalid compiled claim rows")
    elif compiled_claim_stats["duplicate_claim_ids"]:
        findings.append("compiled claims JSONL has duplicate claim IDs")
    if record_id_stats["readable"] is not True:
        findings.append("compiled claim support record store is unreadable")
    if compiled_claim_stats["invalid_categories"]:
        findings.append("compiled claims use unknown categories")
    if compiled_claim_stats["claims_without_supporting_records"]:
        findings.append("compiled claims are missing supporting_record_ids")
    if compiled_claim_stats["claims_with_unknown_supporting_records"]:
        findings.append("compiled claims reference unknown supporting record IDs")
    if compiled_claim_stats["claims_with_unknown_contradicting_records"]:
        findings.append("compiled claims reference unknown contradicting record IDs")
    if compiled_claim_stats["claims_without_supporting_episodes"]:
        findings.append("compiled claims are missing supporting_episode_ids")
    if compiled_claim_stats["claims_with_unknown_supporting_episodes"]:
        findings.append("compiled claims reference unknown supporting episode IDs")
    if compiled_claim_stats["claims_with_unknown_contradicting_episodes"]:
        findings.append("compiled claims reference unknown contradicting episode IDs")
    if compiled_claim_stats["episode_record_mismatches"]:
        findings.append("compiled claims episode IDs do not match referenced records")
    if compiled_claim_stats["temporal_leaks"]:
        findings.append("compiled claims expose future record evidence")
    if compiled_claim_stats["validated_without_contradictions"]:
        findings.append("validated compiled claims are missing contradiction evidence")
    if compiled_claim_stats["validated_single_episode"]:
        findings.append(
            "validated compiled claims rely on one or zero supporting episodes"
        )
    return {
        **status,
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
    }


def _real_bundle_candidates(
    settings: Settings,
    *,
    explicit_path: Path | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    search_locations: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen: set[Path] = set()

    def add_location(source: str, path: Path, *, configured: bool) -> None:
        resolved = settings.path(path)
        search_locations.append(
            {
                "source": source,
                "path": relative_to_root(resolved, settings.project_root),
                "exists": resolved.exists(),
                "is_file": resolved.is_file(),
                "is_dir": resolved.is_dir(),
                "configured": configured,
            }
        )
        if resolved.is_file():
            add_candidate(source, resolved)
        elif resolved.is_dir():
            for item in sorted(resolved.rglob("*.md")):
                if item.is_file():
                    add_candidate(source, item)

    def add_candidate(source: str, path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        candidates.append(
            {
                "source": source,
                "path": relative_to_root(path, settings.project_root),
                "absolute_path": resolved.as_posix(),
            }
        )

    for source, relative in REAL_BUNDLE_SEARCH_DIRS:
        add_location(source, relative, configured=False)
    imported_root = settings.path(Path("research/episodes"))
    search_locations.append(
        {
            "source": "imported_episodes",
            "path": relative_to_root(imported_root, settings.project_root),
            "exists": imported_root.exists(),
            "is_file": imported_root.is_file(),
            "is_dir": imported_root.is_dir(),
            "configured": False,
        }
    )
    if imported_root.is_dir():
        for item in sorted(imported_root.glob("*/original_bundle.md")):
            if item.is_file():
                add_candidate("imported_episodes", item)
    env_path = settings.env_value(REAL_BUNDLE_ENV_KEY)
    if env_path:
        add_location("env", Path(env_path), configured=True)
    if explicit_path is not None:
        add_location("cli", explicit_path, configured=True)
    return search_locations, candidates


def _is_production_bundle_candidate(
    settings: Settings,
    candidate: dict[str, Any],
) -> bool:
    source = candidate.get("source")
    if source not in REAL_BUNDLE_PRODUCTION_SOURCES:
        return False
    absolute_path = candidate.get("absolute_path")
    if not isinstance(absolute_path, str) or not absolute_path:
        return False
    try:
        resolved = Path(absolute_path).resolve()
    except OSError:
        return False
    if ".example" in resolved.name:
        return False
    fixture_root = settings.path(Path("tests/fixtures/research_bundles")).resolve()
    try:
        resolved.relative_to(fixture_root)
    except ValueError:
        return True
    return False


def _real_bundle_inspection_summary(inspection: dict[str, Any]) -> dict[str, Any]:
    validation = inspection.get("validation")
    validation = validation if isinstance(validation, dict) else {}
    bundle_version = inspection.get("bundle_schema_version")
    adapter = inspection.get("adapter")
    is_direct_ingest = adapter == "v23-direct-ingest"
    is_v11 = (
        bundle_version == "nslab.research_bundle.v11"
        and adapter in {"v11", "v23-direct-ingest"}
    )
    requires_blind_valid = not is_direct_ingest
    brain_eligible = _first_not_none(
        inspection.get("brain_eligible"),
        validation.get("brain_eligible"),
    )
    direct_ingest_contract_validation_parity_verified = _first_not_none(
        inspection.get("direct_ingest_contract_validation_parity_verified"),
        validation.get("direct_ingest_contract_validation_parity_verified"),
        validation.get("direct_ingest_schema_contract_verified"),
    )
    direct_ingest_contract_count_hash_parity_verified = _first_not_none(
        inspection.get("direct_ingest_contract_count_hash_parity_verified"),
        validation.get("direct_ingest_contract_count_hash_parity_verified"),
        validation.get("direct_ingest_record_count_hash_parity_ready"),
    )
    structural_checks_passed = (
        is_v11
        and inspection.get("validation_passed") is True
        and validation.get("bundle_status_accept_full") is True
        and (not requires_blind_valid or validation.get("blind_valid") is True)
        and validation.get("validator_exit_code_zero") is True
        and validation.get("critical_error_count_zero") is True
        and inspection.get("record_count_matches_manifest") is True
        and inspection.get("training_eligible_count_matches_manifest") is True
        and inspection.get("dropped_record_count") == 0
        and inspection.get("available_from_valid") is True
        and inspection.get("outcome_label_quality_valid") is True
        and inspection.get("hash_mismatch_count") == 0
        and inspection.get("hash_expectation_conflict_count") == 0
        and inspection.get("missing_source_reference_count") == 0
        and inspection.get("missing_payload_reference_count") == 0
        and inspection.get("raw_record_without_id_count") == 0
        and inspection.get("record_id_set_comparable") is True
        and inspection.get("record_id_set_matches_raw") is True
        and inspection.get("record_type_counts_match_raw") is True
        and inspection.get("training_eligible_count_matches_raw") is True
        and inspection.get("raw_payload_hashes_match") is True
        and inspection.get("import_loss_audit_passed") is True
        and inspection.get("typed_payload_valid") is True
    )
    failure_reasons = _real_bundle_failure_reasons(
        inspection,
        validation=validation,
        is_v11=is_v11,
        requires_blind_valid=requires_blind_valid,
    )
    direct_ingest_failure_reasons = _real_bundle_direct_ingest_failure_reasons(
        inspection,
        validation=validation,
    )
    smoke_passed = structural_checks_passed and not failure_reasons
    return {
        "status": "passed" if smoke_passed else "failed",
        "v11_accept_full_smoke_passed": smoke_passed,
        "direct_ingest_smoke_passed": not direct_ingest_failure_reasons,
        "failure_reason_count": len(failure_reasons),
        "failure_reasons": failure_reasons,
        "direct_ingest_failure_reason_count": len(direct_ingest_failure_reasons),
        "direct_ingest_failure_reasons": direct_ingest_failure_reasons,
        "raw_bundle_sha256": inspection.get("raw_bundle_sha256"),
        "bundle_version": bundle_version,
        "manifest_schema_version": inspection.get("manifest_schema_version"),
        "episode_schema_version": inspection.get("episode_schema_version"),
        "adapter": adapter,
        "supported": inspection.get("supported"),
        "episode_id": inspection.get("episode_id"),
        "trade_date": inspection.get("trade_date"),
        "raw_record_count": inspection.get("raw_record_count"),
        "normalized_record_count": inspection.get("normalized_record_count"),
        "training_eligible_record_count": inspection.get(
            "training_eligible_record_count"
        ),
        "raw_record_ids": inspection.get("raw_record_ids"),
        "normalized_record_ids": inspection.get("normalized_record_ids"),
        "raw_record_without_id_count": inspection.get("raw_record_without_id_count"),
        "record_id_set_comparable": inspection.get("record_id_set_comparable"),
        "record_id_set_matches_raw": inspection.get("record_id_set_matches_raw"),
        "missing_normalized_record_ids": inspection.get(
            "missing_normalized_record_ids"
        ),
        "extra_normalized_record_ids": inspection.get("extra_normalized_record_ids"),
        "raw_record_counts_by_type": inspection.get("raw_record_counts_by_type"),
        "record_type_counts_match_raw": inspection.get(
            "record_type_counts_match_raw"
        ),
        "raw_training_eligible_record_count": inspection.get(
            "raw_training_eligible_record_count"
        ),
        "training_eligible_count_matches_raw": inspection.get(
            "training_eligible_count_matches_raw"
        ),
        "raw_payload_hashes_match": inspection.get("raw_payload_hashes_match"),
        "raw_payload_hash_mismatch_record_ids": inspection.get(
            "raw_payload_hash_mismatch_record_ids"
        ),
        "import_loss_audit_passed": inspection.get("import_loss_audit_passed"),
        "typed_payload_valid": inspection.get("typed_payload_valid"),
        "invalid_typed_payload_record_count": inspection.get(
            "invalid_typed_payload_record_count"
        ),
        "dropped_record_count": inspection.get("dropped_record_count"),
        "quarantined_bundle_count": inspection.get("quarantined_bundle_count"),
        "quarantined_raw_record_count": inspection.get(
            "quarantined_raw_record_count"
        ),
        "quarantined_record_count": inspection.get("quarantined_record_count"),
        "record_counts_by_type": inspection.get("record_counts_by_type"),
        "validation_passed": inspection.get("validation_passed"),
        "bundle_status_accept_full": validation.get("bundle_status_accept_full"),
        "blind_valid": validation.get("blind_valid"),
        "validator_exit_code_zero": validation.get("validator_exit_code_zero"),
        "critical_error_count_zero": validation.get("critical_error_count_zero"),
        "record_count_matches_manifest": inspection.get("record_count_matches_manifest"),
        "training_eligible_count_matches_manifest": inspection.get(
            "training_eligible_count_matches_manifest"
        ),
        "available_from_valid": inspection.get("available_from_valid"),
        "invalid_available_from_record_count": inspection.get(
            "invalid_available_from_record_count"
        ),
        "outcome_label_quality_valid": inspection.get("outcome_label_quality_valid"),
        "invalid_outcome_label_quality_record_count": inspection.get(
            "invalid_outcome_label_quality_record_count"
        ),
        "hash_mismatch_count": inspection.get("hash_mismatch_count"),
        "hash_expectation_conflict_count": inspection.get(
            "hash_expectation_conflict_count"
        ),
        "missing_source_reference_count": inspection.get(
            "missing_source_reference_count"
        ),
        "missing_payload_reference_count": inspection.get(
            "missing_payload_reference_count"
        ),
        "direct_ingest_contract_present": inspection.get(
            "direct_ingest_contract_present"
        ),
        "direct_ingest_contract_schema_version": inspection.get(
            "direct_ingest_contract_schema_version"
        ),
        "direct_brain_ingest_ready": inspection.get("direct_brain_ingest_ready"),
        "brain_eligible": brain_eligible,
        "requires_human_semantic_review": inspection.get(
            "requires_human_semantic_review"
        ),
        "direct_ingest_fatal_blocker_count": inspection.get(
            "direct_ingest_fatal_blocker_count"
        ),
        "direct_ingest_contract_validation_parity_verified": (
            direct_ingest_contract_validation_parity_verified
        ),
        "direct_ingest_contract_count_hash_parity_verified": (
            direct_ingest_contract_count_hash_parity_verified
        ),
        "final_semantic_audit_present": inspection.get(
            "final_semantic_audit_present"
        ),
        "final_semantic_audit_count": inspection.get("final_semantic_audit_count"),
        "final_semantic_audit_fail_count": inspection.get(
            "final_semantic_audit_fail_count"
        ),
    }


def _real_bundle_failure_reasons(
    inspection: dict[str, Any],
    *,
    validation: dict[str, Any],
    is_v11: bool,
    requires_blind_valid: bool = True,
) -> list[str]:
    reasons: list[str] = []
    if not is_v11:
        reasons.append("bundle is not supported v11 research bundle")
    checks: list[tuple[str, object, object]] = [
        ("validation_passed", inspection.get("validation_passed"), True),
        ("bundle_status_accept_full", validation.get("bundle_status_accept_full"), True),
        ("validator_exit_code_zero", validation.get("validator_exit_code_zero"), True),
        ("critical_error_count_zero", validation.get("critical_error_count_zero"), True),
        ("record_count_matches_manifest", inspection.get("record_count_matches_manifest"), True),
        (
            "training_eligible_count_matches_manifest",
            inspection.get("training_eligible_count_matches_manifest"),
            True,
        ),
        ("dropped_record_count", inspection.get("dropped_record_count"), 0),
        ("available_from_valid", inspection.get("available_from_valid"), True),
        ("outcome_label_quality_valid", inspection.get("outcome_label_quality_valid"), True),
        ("hash_mismatch_count", inspection.get("hash_mismatch_count"), 0),
        (
            "hash_expectation_conflict_count",
            inspection.get("hash_expectation_conflict_count"),
            0,
        ),
        ("missing_source_reference_count", inspection.get("missing_source_reference_count"), 0),
        ("missing_payload_reference_count", inspection.get("missing_payload_reference_count"), 0),
        ("raw_record_without_id_count", inspection.get("raw_record_without_id_count"), 0),
        ("record_id_set_comparable", inspection.get("record_id_set_comparable"), True),
        ("record_id_set_matches_raw", inspection.get("record_id_set_matches_raw"), True),
        ("record_type_counts_match_raw", inspection.get("record_type_counts_match_raw"), True),
        (
            "training_eligible_count_matches_raw",
            inspection.get("training_eligible_count_matches_raw"),
            True,
        ),
        ("raw_payload_hashes_match", inspection.get("raw_payload_hashes_match"), True),
        ("import_loss_audit_passed", inspection.get("import_loss_audit_passed"), True),
        ("typed_payload_valid", inspection.get("typed_payload_valid"), True),
        ("quarantined_bundle_count", inspection.get("quarantined_bundle_count"), 0),
        (
            "quarantined_raw_record_count",
            inspection.get("quarantined_raw_record_count"),
            0,
        ),
        ("quarantined_record_count", inspection.get("quarantined_record_count"), 0),
    ]
    if requires_blind_valid:
        checks.insert(2, ("blind_valid", validation.get("blind_valid"), True))
    for name, observed, expected in checks:
        if observed != expected:
            reasons.append(f"{name}={observed!r} expected {expected!r}")
    return reasons


def _real_bundle_direct_ingest_failure_reasons(
    inspection: dict[str, Any],
    *,
    validation: dict[str, Any] | None = None,
) -> list[str]:
    validation = validation if isinstance(validation, dict) else {}
    brain_eligible = _first_not_none(
        inspection.get("brain_eligible"),
        validation.get("brain_eligible"),
    )
    validation_parity_verified = _first_not_none(
        inspection.get("direct_ingest_contract_validation_parity_verified"),
        validation.get("direct_ingest_contract_validation_parity_verified"),
        validation.get("direct_ingest_schema_contract_verified"),
    )
    count_hash_parity_verified = _first_not_none(
        inspection.get("direct_ingest_contract_count_hash_parity_verified"),
        validation.get("direct_ingest_contract_count_hash_parity_verified"),
        validation.get("direct_ingest_record_count_hash_parity_ready"),
    )
    checks = (
        ("direct_ingest_contract_present", inspection.get("direct_ingest_contract_present"), True),
        (
            "direct_ingest_contract_schema_version",
            inspection.get("direct_ingest_contract_schema_version"),
            "nslab.direct_ingest_contract.v1",
        ),
        ("direct_brain_ingest_ready", inspection.get("direct_brain_ingest_ready"), True),
        ("brain_eligible", brain_eligible, True),
        (
            "requires_human_semantic_review",
            inspection.get("requires_human_semantic_review"),
            False,
        ),
        (
            "direct_ingest_fatal_blocker_count",
            inspection.get("direct_ingest_fatal_blocker_count"),
            0,
        ),
        (
            "direct_ingest_contract_validation_parity_verified",
            validation_parity_verified,
            True,
        ),
        (
            "direct_ingest_contract_count_hash_parity_verified",
            count_hash_parity_verified,
            True,
        ),
        ("final_semantic_audit_present", inspection.get("final_semantic_audit_present"), True),
        ("final_semantic_audit_fail_count", inspection.get("final_semantic_audit_fail_count"), 0),
    )
    return [
        f"{name}={observed!r} expected {expected!r}"
        for name, observed, expected in checks
        if observed != expected
    ]


def _real_bundle_production_failure_reasons(
    inspection: dict[str, Any],
) -> list[str]:
    return _string_list(inspection.get("failure_reasons")) + _string_list(
        inspection.get("direct_ingest_failure_reasons")
    )


def _brain_audit_status(coverage_audit: dict[str, object]) -> dict[str, Any]:
    findings = _string_list(coverage_audit.get("brain_audit_findings"))
    return {
        "passed": coverage_audit.get("brain_audit_passed") is True,
        "deep": coverage_audit.get("brain_audit_deep"),
        "brain_build_mode": coverage_audit.get("brain_build_mode"),
        "catalog_only": coverage_audit.get("catalog_only"),
        "record_coverage_complete": coverage_audit.get("record_coverage_complete"),
        "deterministic_rebuild_verified": coverage_audit.get(
            "deterministic_rebuild_verified"
        ),
        "llm_compile_category_schema_mismatches": coverage_audit.get(
            "llm_compile_category_schema_mismatches"
        ),
        "brain_category_file_count": coverage_audit.get("brain_category_file_count"),
        "brain_category_missing_files": coverage_audit.get(
            "brain_category_missing_files"
        ),
        "brain_category_source_record_types": coverage_audit.get(
            "brain_category_source_record_types"
        ),
        "brain_category_source_population_mismatches": coverage_audit.get(
            "brain_category_source_population_mismatches"
        ),
        "brain_empty_category_complete_files": coverage_audit.get(
            "brain_empty_category_complete_files"
        ),
        "brain_category_files_identical": coverage_audit.get(
            "brain_category_files_identical"
        ),
        "brain_category_bodies_identical": coverage_audit.get(
            "brain_category_bodies_identical"
        ),
        "finding_count": len(findings),
        "findings": findings,
    }


def _brain_audit_diversity_summary_present(brain_audit: dict[str, Any]) -> bool:
    return (
        isinstance(brain_audit.get("brain_category_source_record_types"), dict)
        and isinstance(
            brain_audit.get("llm_compile_category_schema_mismatches"),
            list,
        )
        and isinstance(
            brain_audit.get("brain_category_source_population_mismatches"),
            list,
        )
        and isinstance(brain_audit.get("brain_empty_category_complete_files"), list)
        and isinstance(brain_audit.get("brain_category_files_identical"), list)
        and isinstance(brain_audit.get("brain_category_bodies_identical"), list)
    )


def _brain_manifest_catalog_only(manifest: dict[str, Any] | None) -> bool | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("catalog_only")
    if isinstance(value, bool):
        return value
    build_mode = manifest.get("build_mode")
    if build_mode in {"full", "catalog", "incremental"}:
        return True
    if build_mode in {"llm-full", "asof_context"}:
        return False
    return None


def _brain_manifest_catalog_mode_reason(manifest: dict[str, Any] | None) -> str | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("catalog_mode_reason")
    if isinstance(value, str) and value:
        return value
    build_mode = manifest.get("build_mode")
    if build_mode == "full":
        return "deprecated_full_alias"
    if build_mode == "catalog":
        return "explicit_catalog_mode"
    if build_mode == "incremental":
        return "catalog_incremental_update"
    return None


def _brain_manifest_deprecated_mode_alias(
    manifest: dict[str, Any] | None,
) -> bool | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("deprecated_mode_alias")
    if isinstance(value, bool):
        return value
    return manifest.get("build_mode") == "full"


def _brain_manifest_production_eligible(manifest: dict[str, Any] | None) -> bool | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("production_eligible")
    if isinstance(value, bool):
        return value
    build_mode = manifest.get("build_mode")
    catalog_only = _brain_manifest_catalog_only(manifest)
    if isinstance(build_mode, str) and isinstance(catalog_only, bool):
        return build_mode == "llm-full" and catalog_only is False
    return None


def _nested_dict(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: object = source
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _warehouse_status(
    coverage_audit: dict[str, object],
    *,
    accepted_episode_count: int,
) -> str:
    expected_counts = coverage_audit.get("warehouse_expected_source_counts")
    source_projection_required = accepted_episode_count > 0 or _has_expected_warehouse_sources(
        expected_counts,
    )
    unreadable = coverage_audit.get("warehouse_unreadable_files")
    if isinstance(unreadable, list) and unreadable:
        return "attention"
    if not source_projection_required:
        return "ok"
    return (
        "ok"
        if coverage_audit.get("warehouse_required_files_present") is True
        and coverage_audit.get("warehouse_synced") is True
        and coverage_audit.get("warehouse_projection_synced") is True
        else "attention"
    )


def _has_expected_warehouse_sources(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    for item in value.values():
        if not isinstance(item, dict):
            continue
        expected = item.get("expected")
        if isinstance(expected, int) and not isinstance(expected, bool) and expected > 0:
            return True
    return False


def _database_status(settings: Settings, coverage_audit: dict[str, object]) -> dict[str, Any]:
    warehouse_counts = coverage_audit.get("warehouse_counts")
    counts_readable = isinstance(warehouse_counts, dict) and not any(
        isinstance(value, str) for value in warehouse_counts.values()
    )
    warehouse_path = settings.path("warehouse")
    base: dict[str, Any] = {
        "engine": "duckdb",
        "available": False,
        "version": None,
        "connection": "unavailable",
        "warehouse_path": warehouse_path.as_posix(),
        "warehouse_path_exists": warehouse_path.exists(),
        "warehouse_counts_readable": counts_readable,
        "status": "attention",
    }
    try:
        module = import_module("duckdb")
    except Exception as exc:
        return {
            **base,
            "error": f"{type(exc).__name__}: {exc}",
        }
    connect = getattr(module, "connect", None)
    base.update(
        {
            "available": True,
            "version": getattr(module, "__version__", None),
        }
    )
    if not callable(connect):
        return {
            **base,
            "connection": "missing_connect",
        }
    try:
        with connect(database=":memory:") as connection:
            row = connection.execute("select 1").fetchone()
    except Exception as exc:
        return {
            **base,
            "connection": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    connection_ok = bool(row and row[0] == 1)
    return {
        **base,
        "connection": "ok" if connection_ok else "failed",
        "status": "ok" if connection_ok and counts_readable else "attention",
    }


def _resolved_optional_path(settings: Settings, path: Path | None) -> Path | None:
    if path is None:
        return None
    return settings.path(path)


def _stock_web_effective_path(
    settings: Settings,
    *,
    configured_path: Path | None,
    cache_path: Path,
) -> tuple[Path | None, str]:
    if configured_path is not None and configured_path.exists():
        return configured_path, "path"
    if settings.stock_web_cache_enabled and cache_path.exists():
        return cache_path, "cache"
    if configured_path is not None:
        return configured_path, "path"
    if settings.stock_web_cache_enabled:
        return cache_path, "cache"
    return None, "none"


def _environment_status(settings: Settings) -> dict[str, dict[str, object]]:
    status: dict[str, dict[str, object]] = {}
    for key in ENV_KEYS:
        value = settings.env_value(key)
        status[key] = {
            "set": value is not None,
            "value": "***" if key.endswith("KEY") and value else value,
        }
    return status


def _openai_status(settings: Settings, *, production: bool = False) -> str:
    if not _openai_required(settings, production=production):
        return "not_required"
    if not settings.env_value("OPENAI_API_KEY"):
        return "missing_api_key"
    sdk_status = _openai_sdk_status()
    if sdk_status.get("available") is not True:
        return "missing_sdk" if sdk_status.get("error") is None else "sdk_import_error"
    if sdk_status.get("async_client_available") is not True:
        return "sdk_missing_async_client"
    return "configured_not_called"


def _openai_required(settings: Settings, *, production: bool = False) -> bool:
    return production or settings.llm_provider.strip().lower() in OPENAI_PROVIDER_ALIASES


def _openai_sdk_status() -> dict[str, Any]:
    try:
        spec = find_spec("openai")
    except (ImportError, ValueError) as exc:
        return {
            "available": False,
            "version": None,
            "async_client_available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if spec is None:
        return {
            "available": False,
            "version": None,
            "async_client_available": False,
            "error": None,
        }
    try:
        module = import_module("openai")
    except Exception as exc:
        return {
            "available": False,
            "version": None,
            "async_client_available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "available": True,
        "version": getattr(module, "__version__", None),
        "async_client_available": hasattr(module, "AsyncOpenAI"),
        "error": None,
    }


def _brave_search_required(settings: Settings, *, production: bool = False) -> bool:
    return production or settings.web_provider.strip().lower() in {
        "brave",
        "brave-search",
        "brave-news",
    }


def _brave_search_status(settings: Settings, *, production: bool = False) -> str:
    if not _brave_search_required(settings, production=production):
        return "not_required"
    if settings.env_value(settings.brave_search_api_key_env):
        return "configured_not_called"
    return "missing_api_key"


def _brain_coverage_status(root: Path, accepted_episode_count: int) -> dict[str, Any]:
    coverage_path = root / "brain" / "current" / "coverage_manifest.json"
    if not coverage_path.exists():
        return {
            "manifest_exists": False,
            "coverage_complete": False,
            "covered_episode_count": 0,
            "missing_episode_ids": [],
            "status": "missing",
        }
    try:
        manifest = _read_json_object(coverage_path)
    except ValueError as exc:
        return {
            "manifest_exists": True,
            "coverage_complete": False,
            "covered_episode_count": 0,
            "missing_episode_ids": [],
            "status": "invalid",
            "error": str(exc),
        }
    covered_ids = _string_list(manifest.get("covered_episode_ids"))
    missing_ids = _string_list(manifest.get("missing_episode_ids"))
    coverage_complete = (
        manifest.get("coverage_complete") is True
        and len(covered_ids) == accepted_episode_count
        and not missing_ids
    )
    return {
        "manifest_exists": True,
        "brain_version": manifest.get("brain_version"),
        "coverage_complete": coverage_complete,
        "covered_episode_count": len(covered_ids),
        "missing_episode_ids": missing_ids,
        "status": "complete" if coverage_complete else "incomplete",
    }


def _file_count(path: Path, *, suffix: str | None = None) -> int:
    if not path.exists():
        return 0
    files = [item for item in path.rglob("*") if item.is_file()]
    if suffix is not None:
        files = [item for item in files if item.suffix == suffix]
    return len(files)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path.as_posix()}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path.as_posix()}")
    return payload


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = _read_json_object(path)
    except ValueError:
        return None
    return payload


def _jsonl_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _compiled_claim_file_stats(
    path: Path,
    *,
    known_record_ids: set[str] | None = None,
    known_records_by_id: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "line_count": 0,
            "valid_claim_count": 0,
            "invalid_line_count": 0,
            "claim_ids": [],
            "duplicate_claim_ids": [],
            "invalid_categories": [],
            "claims_without_supporting_records": [],
            "claims_with_unknown_supporting_records": [],
            "claims_with_unknown_contradicting_records": [],
            "claims_without_supporting_episodes": [],
            "claims_with_unknown_supporting_episodes": [],
            "claims_with_unknown_contradicting_episodes": [],
            "episode_record_mismatches": [],
            "temporal_leaks": [],
            "validated_without_contradictions": [],
            "validated_single_episode": [],
            "supporting_record_id_count": 0,
            "contradicting_record_id_count": 0,
        }
    line_count = 0
    invalid_line_count = 0
    claim_ids: list[str] = []
    invalid_categories: list[str] = []
    claims_without_supporting_records: list[str] = []
    claims_with_unknown_supporting_records: list[str] = []
    claims_with_unknown_contradicting_records: list[str] = []
    claims_without_supporting_episodes: list[str] = []
    claims_with_unknown_supporting_episodes: list[str] = []
    claims_with_unknown_contradicting_episodes: list[str] = []
    episode_record_mismatches: list[str] = []
    temporal_leaks: list[str] = []
    validated_without_contradictions: list[str] = []
    validated_single_episode: list[str] = []
    supporting_record_ids: list[str] = []
    contradicting_record_ids: list[str] = []
    valid_categories = {_brain_category(file_name) for file_name in BRAIN_FILES}
    known_episode_ids = (
        {record.episode_id for record in known_records_by_id.values()}
        if known_records_by_id is not None
        else None
    )
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
        invalid_line_count = 1
    for line in lines:
        if not line.strip():
            continue
        line_count += 1
        try:
            payload = json.loads(line)
            claim = CompiledBrainClaim.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            invalid_line_count += 1
            continue
        claim_ids.append(claim.claim_id)
        if claim.category not in valid_categories:
            invalid_categories.append(f"{claim.claim_id}: {claim.category}")
        if not claim.supporting_record_ids:
            claims_without_supporting_records.append(claim.claim_id)
        if not claim.supporting_episode_ids:
            claims_without_supporting_episodes.append(claim.claim_id)
        supporting_record_ids.extend(claim.supporting_record_ids)
        contradicting_record_ids.extend(claim.contradicting_record_ids)
        if known_record_ids is not None:
            unknown_supporting = sorted(set(claim.supporting_record_ids) - known_record_ids)
            if unknown_supporting:
                claims_with_unknown_supporting_records.append(
                    f"{claim.claim_id}: {', '.join(unknown_supporting)}"
                )
            unknown_contradicting = sorted(
                set(claim.contradicting_record_ids) - known_record_ids
            )
            if unknown_contradicting:
                claims_with_unknown_contradicting_records.append(
                    f"{claim.claim_id}: {', '.join(unknown_contradicting)}"
                )
        if known_episode_ids is not None:
            unknown_supporting_episodes = sorted(
                set(claim.supporting_episode_ids) - known_episode_ids
            )
            if unknown_supporting_episodes:
                claims_with_unknown_supporting_episodes.append(
                    f"{claim.claim_id}: {', '.join(unknown_supporting_episodes)}"
                )
            unknown_contradicting_episodes = sorted(
                set(claim.contradicting_episode_ids) - known_episode_ids
            )
            if unknown_contradicting_episodes:
                claims_with_unknown_contradicting_episodes.append(
                    f"{claim.claim_id}: {', '.join(unknown_contradicting_episodes)}"
                )
        if known_records_by_id is not None:
            for record_id in claim.supporting_record_ids:
                record = known_records_by_id.get(record_id)
                if record is None:
                    continue
                if record.episode_id not in claim.supporting_episode_ids:
                    episode_record_mismatches.append(
                        f"{claim.claim_id}: supporting {record_id}->{record.episode_id}"
                    )
                if not is_available_as_of(record.available_from, claim.available_from):
                    temporal_leaks.append(
                        f"{claim.claim_id}: available_from precedes supporting record {record_id}"
                    )
            for record_id in claim.contradicting_record_ids:
                record = known_records_by_id.get(record_id)
                if record is None:
                    continue
                if record.episode_id not in claim.contradicting_episode_ids:
                    episode_record_mismatches.append(
                        f"{claim.claim_id}: contradicting {record_id}->{record.episode_id}"
                    )
                if not is_available_as_of(record.available_from, claim.available_from):
                    temporal_leaks.append(
                        f"{claim.claim_id}: available_from precedes contradicting record {record_id}"
                    )
        if claim.status == "validated":
            if not claim.contradicting_record_ids and not claim.contradicting_episode_ids:
                validated_without_contradictions.append(claim.claim_id)
            if len(set(claim.supporting_episode_ids)) <= 1:
                validated_single_episode.append(claim.claim_id)
    return {
        "exists": True,
        "line_count": line_count,
        "valid_claim_count": len(claim_ids),
        "invalid_line_count": invalid_line_count,
        "claim_ids": claim_ids,
        "duplicate_claim_ids": _duplicate_strings(claim_ids),
        "invalid_categories": sorted(invalid_categories),
        "claims_without_supporting_records": sorted(
            claims_without_supporting_records
        ),
        "claims_with_unknown_supporting_records": sorted(
            claims_with_unknown_supporting_records
        ),
        "claims_with_unknown_contradicting_records": sorted(
            claims_with_unknown_contradicting_records
        ),
        "claims_without_supporting_episodes": sorted(
            claims_without_supporting_episodes
        ),
        "claims_with_unknown_supporting_episodes": sorted(
            claims_with_unknown_supporting_episodes
        ),
        "claims_with_unknown_contradicting_episodes": sorted(
            claims_with_unknown_contradicting_episodes
        ),
        "episode_record_mismatches": sorted(episode_record_mismatches),
        "temporal_leaks": sorted(temporal_leaks),
        "validated_without_contradictions": sorted(validated_without_contradictions),
        "validated_single_episode": sorted(validated_single_episode),
        "supporting_record_id_count": len(supporting_record_ids),
        "contradicting_record_id_count": len(contradicting_record_ids),
    }


def _brain_record_store_id_stats(root: Path) -> dict[str, Any]:
    try:
        records = BrainRecordStore(root).list_records()
    except Exception as exc:
        return {
            "readable": False,
            "record_ids": None,
            "record_count": None,
            "error": type(exc).__name__,
        }
    record_ids = {record.record_id for record in records}
    return {
        "readable": True,
        "record_ids": record_ids,
        "records_by_id": {record.record_id: record for record in records},
        "record_count": len(record_ids),
        "error": None,
    }


def _brain_category_file_stats(current_dir: Path) -> dict[str, Any]:
    missing_files: list[str] = []
    empty_files: list[str] = []
    unreadable_files: list[str] = []
    file_hashes: dict[str, str] = {}
    for file_name in BRAIN_FILES:
        path = current_dir / file_name
        if not path.exists():
            missing_files.append(file_name)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            unreadable_files.append(file_name)
            continue
        if not text.strip():
            empty_files.append(file_name)
        file_hashes[file_name] = file_sha256(path)
    return {
        "existing_count": len(file_hashes),
        "missing_files": missing_files,
        "empty_files": empty_files,
        "unreadable_files": unreadable_files,
        "file_hashes": file_hashes,
    }


def _llm_compile_category_manifest_stats(
    manifest: object,
    compiled_claim_ids: list[str],
    *,
    known_record_ids: set[str] | None = None,
) -> dict[str, Any]:
    expected_by_category = {
        _brain_category(file_name): file_name for file_name in BRAIN_FILES
    }
    schema_mismatches: list[str] = []
    source_count_mismatches: list[str] = []
    compiled_claim_count_mismatches: list[str] = []
    unknown_compiled_claim_ids: list[str] = []
    source_record_ids: list[str] = []
    categories = manifest.get("categories") if isinstance(manifest, dict) else None
    if not isinstance(categories, list):
        return {
            "observed_count": None,
            "schema_mismatches": ["categories: missing"],
            "source_count_mismatches": source_count_mismatches,
            "compiled_claim_count_mismatches": compiled_claim_count_mismatches,
            "unknown_compiled_claim_ids": unknown_compiled_claim_ids,
            "unknown_source_record_ids": [],
        }
    if len(categories) != len(BRAIN_FILES):
        schema_mismatches.append(
            f"categories: expected {len(BRAIN_FILES)}, got {len(categories)}"
        )
    observed_category_names: list[str] = []
    valid_claim_ids = set(compiled_claim_ids)
    for index, category in enumerate(categories, start=1):
        if not isinstance(category, dict):
            schema_mismatches.append(f"categories[{index}]: invalid entry")
            continue
        category_name = category.get("category")
        file_name = category.get("file_name")
        if not isinstance(category_name, str) or not category_name:
            schema_mismatches.append(f"categories[{index}]: missing category")
            category_label = f"categories[{index}]"
        else:
            observed_category_names.append(category_name)
            category_label = category_name
            expected_file_name = expected_by_category.get(category_name)
            if expected_file_name is None:
                schema_mismatches.append(
                    f"categories[{index}]: unexpected category {category_name}"
                )
            elif file_name != expected_file_name:
                observed_file = (
                    file_name
                    if isinstance(file_name, str) and file_name
                    else "missing"
                )
                schema_mismatches.append(
                    f"{category_name}: expected file {expected_file_name}, got {observed_file}"
                )
        source_ids = _string_list(category.get("source_record_ids"))
        if (
            not isinstance(category.get("source_record_ids"), list)
            or category.get("source_record_count") != len(source_ids)
        ):
            source_count_mismatches.append(category_label)
        source_record_ids.extend(source_ids)
        claim_ids = _string_list(category.get("compiled_claim_ids"))
        if (
            not isinstance(category.get("compiled_claim_ids"), list)
            or category.get("compiled_claim_count") != len(claim_ids)
        ):
            compiled_claim_count_mismatches.append(category_label)
        unknown_compiled_claim_ids.extend(
            claim_id for claim_id in claim_ids if claim_id not in valid_claim_ids
        )
    for category_name in _duplicate_strings(observed_category_names):
        schema_mismatches.append(f"{category_name}: duplicate category entry")
    missing_categories = sorted(set(expected_by_category) - set(observed_category_names))
    for category_name in missing_categories:
        schema_mismatches.append(f"{category_name}: missing category entry")
    unknown_source_record_ids: list[str] = []
    if known_record_ids is not None:
        unknown_source_record_ids = sorted(set(source_record_ids) - known_record_ids)
    return {
        "observed_count": len(categories),
        "schema_mismatches": sorted(schema_mismatches),
        "source_count_mismatches": sorted(source_count_mismatches),
        "compiled_claim_count_mismatches": sorted(compiled_claim_count_mismatches),
        "unknown_compiled_claim_ids": sorted(set(unknown_compiled_claim_ids)),
        "unknown_source_record_ids": unknown_source_record_ids,
    }


def _llm_compile_record_shard_manifest_stats(
    manifest: object,
    *,
    known_record_ids: set[str] | None = None,
) -> dict[str, Any]:
    schema_mismatches: list[str] = []
    count_mismatches: list[str] = []
    record_ids: list[str] = []
    record_shards = manifest.get("record_shards") if isinstance(manifest, dict) else None
    source_record_count = _int_from_mapping(manifest, "source_record_count")
    expected_shard_count = _int_from_mapping(manifest, "record_shard_count")
    if not isinstance(record_shards, list):
        return {
            "observed_count": None,
            "record_id_count": 0,
            "unique_record_id_count": 0,
            "schema_mismatches": ["record_shards: missing"],
            "count_mismatches": count_mismatches,
            "duplicate_record_ids": [],
            "unknown_record_ids": [],
            "missing_record_ids": [],
        }
    if expected_shard_count != len(record_shards):
        observed = (
            "missing"
            if expected_shard_count is None
            else str(expected_shard_count)
        )
        count_mismatches.append(
            f"record_shard_count: expected {len(record_shards)}, got {observed}"
        )
    for index, shard in enumerate(record_shards, start=1):
        if not isinstance(shard, dict):
            schema_mismatches.append(f"record_shards[{index}]: invalid entry")
            continue
        shard_record_ids = _string_list(shard.get("record_ids"))
        if not isinstance(shard.get("record_ids"), list):
            schema_mismatches.append(f"record_shards[{index}]: missing record_ids")
        if not isinstance(shard.get("cache_key"), str) or not shard.get("cache_key"):
            schema_mismatches.append(f"record_shards[{index}]: missing cache_key")
        if shard.get("record_count") != len(shard_record_ids):
            count_mismatches.append(f"record_shards[{index}]")
        record_ids.extend(shard_record_ids)
    if (
        isinstance(source_record_count, int)
        and source_record_count != len(set(record_ids))
    ):
        count_mismatches.append(
            "source_record_count: expected "
            f"{len(set(record_ids))}, got {source_record_count}"
        )
    unknown_record_ids: list[str] = []
    missing_record_ids: list[str] = []
    if known_record_ids is not None:
        observed_record_ids = set(record_ids)
        unknown_record_ids = sorted(observed_record_ids - known_record_ids)
        missing_record_ids = sorted(known_record_ids - observed_record_ids)
    return {
        "observed_count": len(record_shards),
        "record_id_count": len(record_ids),
        "unique_record_id_count": len(set(record_ids)),
        "schema_mismatches": sorted(schema_mismatches),
        "count_mismatches": sorted(count_mismatches),
        "duplicate_record_ids": _duplicate_strings(record_ids),
        "unknown_record_ids": unknown_record_ids,
        "missing_record_ids": missing_record_ids,
    }


def _llm_full_compile_prompt_hash_stats(source: object) -> dict[str, Any]:
    prompt_hashes: list[str] = []
    missing_fields: list[str] = []
    if not isinstance(source, dict):
        return {
            "prompt_hash_count": 0,
            "unique_prompt_hash_count": 0,
            "prompt_hashes": [],
            "missing_fields": [],
            "duplicate_prompt_hashes": [],
        }
    record_shards = source.get("record_shards")
    if isinstance(record_shards, list):
        for index, shard in enumerate(record_shards, start=1):
            if isinstance(shard, dict):
                _append_prompt_hash(
                    shard,
                    "prompt_sha256",
                    f"record_shards[{index}].prompt_sha256",
                    prompt_hashes=prompt_hashes,
                    missing_fields=missing_fields,
                )
            else:
                missing_fields.append(f"record_shards[{index}]")
    elif record_shards is not None:
        missing_fields.append("record_shards")
    categories = source.get("categories")
    if isinstance(categories, list):
        for index, category in enumerate(categories, start=1):
            if not isinstance(category, dict):
                missing_fields.append(f"categories[{index}]")
                continue
            _append_prompt_hash(
                category,
                "synthesis_prompt_sha256",
                f"categories[{index}].synthesis_prompt_sha256",
                prompt_hashes=prompt_hashes,
                missing_fields=missing_fields,
            )
            _append_prompt_hash(
                category,
                "review_prompt_sha256",
                f"categories[{index}].review_prompt_sha256",
                prompt_hashes=prompt_hashes,
                missing_fields=missing_fields,
            )
    elif categories is not None:
        missing_fields.append("categories")
    return {
        "prompt_hash_count": len(prompt_hashes),
        "unique_prompt_hash_count": len(set(prompt_hashes)),
        "prompt_hashes": sorted(set(prompt_hashes)),
        "missing_fields": sorted(missing_fields),
        "duplicate_prompt_hashes": _duplicate_strings(prompt_hashes),
    }


def _append_prompt_hash(
    source: dict[str, Any],
    key: str,
    label: str,
    *,
    prompt_hashes: list[str],
    missing_fields: list[str],
) -> None:
    value = source.get(key)
    if isinstance(value, str) and value:
        prompt_hashes.append(value)
    else:
        missing_fields.append(label)


def _record_file_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "sha256": None,
            "text_sha256": None,
            "record_count": 0,
            "training_eligible_record_count": 0,
            "record_counts_by_type": {},
            "record_ids": [],
            "duplicate_record_ids": [],
            "invalid_line_count": 0,
            "invalid_envelope_count": 0,
        }
    record_count = 0
    training_eligible_count = 0
    record_counts_by_type: dict[str, int] = {}
    record_ids: list[str] = []
    invalid_line_count = 0
    invalid_envelope_count = 0
    try:
        payload_text = path.read_text(encoding="utf-8")
        lines = payload_text.splitlines()
    except OSError:
        payload_text = ""
        lines = []
        invalid_line_count = 1
    for line in lines:
        if not line.strip():
            continue
        record_count += 1
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            invalid_line_count += 1
            continue
        if not isinstance(payload, dict):
            invalid_line_count += 1
            continue
        try:
            BrainRecordEnvelope.model_validate(payload)
        except ValidationError:
            invalid_envelope_count += 1
        if payload.get("training_eligible") is True:
            training_eligible_count += 1
        record_id = payload.get("record_id")
        if isinstance(record_id, str) and record_id:
            record_ids.append(record_id)
        record_type = payload.get("record_type")
        if isinstance(record_type, str) and record_type:
            record_counts_by_type[record_type] = record_counts_by_type.get(record_type, 0) + 1
    return {
        "exists": True,
        "sha256": file_sha256(path),
        "text_sha256": sha256_text(payload_text),
        "record_count": record_count,
        "training_eligible_record_count": training_eligible_count,
        "record_counts_by_type": dict(sorted(record_counts_by_type.items())),
        "record_ids": record_ids,
        "duplicate_record_ids": _duplicate_strings(record_ids),
        "invalid_line_count": invalid_line_count,
        "invalid_envelope_count": invalid_envelope_count,
    }


def _int_from_mapping(source: object, key: str) -> int | None:
    if not isinstance(source, dict):
        return None
    value = source.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _record_coverage_as_of(record_coverage: dict[str, Any]) -> datetime | None:
    raw_value = record_coverage.get("record_coverage_as_of")
    if not isinstance(raw_value, str) or not raw_value:
        return None
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError:
        return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_list_dict(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {
        item_key: _string_list(item_value)
        for item_key, item_value in sorted(value.items())
        if isinstance(item_key, str)
    }


def _int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, int) and not isinstance(item, bool)
    }


def _numeric_map(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {
        key: float(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, int | float) and not isinstance(item, bool)
    }


def _duplicate_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _schema_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for filename, model in sorted(SCHEMA_MODELS.items()):
        version = _model_schema_version(model)
        if version is not None:
            versions[filename.removesuffix(".schema.json")] = version
    return versions


def _schema_file_status(schema_dir: Path) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    invalid: list[str] = []
    stale: list[str] = []
    for filename, model in sorted(SCHEMA_MODELS.items()):
        expected_version = _model_schema_version(model)
        path = schema_dir / filename
        status: dict[str, Any] = {
            "exists": path.exists(),
            "expected_schema_version": expected_version,
            "schema_version": None,
            "status": "missing",
        }
        if not path.exists():
            missing.append(filename)
            files[filename] = status
            continue
        try:
            payload = _read_json_object(path)
        except ValueError:
            invalid.append(filename)
            status["status"] = "invalid"
            files[filename] = status
            continue
        observed_version = _schema_version_default(payload)
        status["schema_version"] = observed_version
        if expected_version is not None and observed_version != expected_version:
            stale.append(filename)
            status["status"] = "stale"
        else:
            status["status"] = "ok"
        files[filename] = status
    return {
        "expected_file_count": len(SCHEMA_MODELS),
        "missing_files": missing,
        "invalid_files": invalid,
        "stale_files": stale,
        "status": "ok" if not missing and not invalid and not stale else "attention",
        "files": files,
    }


def _model_schema_version(model: type[Any]) -> str | None:
    field = getattr(model, "model_fields", {}).get("schema_version")
    if field is None:
        return None
    default = getattr(field, "default", None)
    return str(default) if default is not None else None


def _schema_version_default(payload: dict[str, Any]) -> str | None:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return None
    schema_version = properties.get("schema_version")
    if not isinstance(schema_version, dict):
        return None
    default = schema_version.get("default")
    return str(default) if default is not None else None
