"""Project diagnostics for the doctor command."""

from __future__ import annotations

import json
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from news_scalping_lab.audits.coverage import audit_coverage
from news_scalping_lab.brain.compiler import BRAIN_FILES, current_brain_version
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.schemas import SCHEMA_MODELS
from news_scalping_lab.prices.stock_web import StockWebPriceSource
from news_scalping_lab.records.store import audit_record_store
from news_scalping_lab.research_import.versioned_bundle import inspect_versioned_bundle
from news_scalping_lab.retrieval.embedding import VECTOR_EMBEDDING_METHOD
from news_scalping_lab.retrieval.store import inspect_vector_index
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import file_sha256, relative_to_root

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
REAL_BUNDLE_ENV_KEY = "NSLAB_REAL_BUNDLE_PATH"
REAL_BUNDLE_SEARCH_DIRS = (
    ("data_inbox", Path("data/inbox/research")),
    ("tests_fixture", Path("tests/fixtures/research_bundles")),
)
REAL_BUNDLE_PRODUCTION_SOURCES = {"cli", "env", "data_inbox"}
REAL_BUNDLE_SEARCH_ORDER = ["data_inbox", "tests_fixture", "env", "cli"]


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
    brain = report.get("brain")
    if isinstance(brain, dict):
        brain_audit = _nested_dict(brain, "audit")
        if brain_audit and brain_audit.get("passed") is not True:
            findings.append("brain: latest brain audit failed")
        if brain_audit and not _brain_audit_diversity_summary_present(brain_audit):
            findings.append("brain: latest brain audit diversity summary is missing")
        coverage = brain.get("coverage")
        if isinstance(coverage, dict) and coverage.get("status") not in {"complete", "missing"}:
            findings.append("brain: accepted episodes are not fully covered")
    brain_manifest = _read_optional_json(settings.project_root / "brain" / "current" / "brain_manifest.json")
    build_mode = brain_manifest.get("build_mode") if isinstance(brain_manifest, dict) else None
    catalog_only = _brain_manifest_catalog_only(brain_manifest)
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
    llm_full_brain = _llm_full_brain_status(
        settings,
        build_mode=build_mode,
        catalog_only=catalog_only,
        current_brain_version=current_brain_version_value,
        expected_source_record_count=expected_source_record_count,
    )
    if catalog_only is True:
        findings.append("brain: current manifest is catalog_only")
    if build_mode != "llm-full":
        observed_mode = build_mode if isinstance(build_mode, str) and build_mode else "missing"
        findings.append(f"brain: current manifest build_mode is {observed_mode}, not llm-full")
    elif llm_full_brain["passed"] is not True:
        findings.extend(f"brain: {finding}" for finding in llm_full_brain["findings"])
    if not isinstance(record_coverage, dict):
        findings.append("records: record coverage manifest is missing")
    elif record_coverage.get("coverage_complete") is not True:
        findings.append("records: record coverage is incomplete")
    record_store = _production_record_store_status(settings)
    if record_store["passed"] is not True:
        findings.extend(f"records: {finding}" for finding in record_store["findings"])
    warehouse = _production_warehouse_status(report.get("warehouse"))
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
    return {
        "schema_version": "nslab.production_readiness.v1",
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "real_bundle_smoke": real_bundle_smoke,
        "real_bundle_import": real_bundle_import,
        "llm_full_brain": llm_full_brain,
        "record_store": record_store,
        "warehouse": warehouse,
        "semantic_index": semantic_index,
        "required_environment": remediation["required_environment"],
        "remediation_commands": remediation["commands"],
    }


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
        summary = _real_bundle_inspection_summary(inspection)
        inspections.append(
            {
                **candidate,
                "status": summary["status"],
                "inspectable": True,
                "production_source": _is_production_bundle_candidate(
                    settings,
                    candidate,
                ),
                "inspection": summary,
            }
        )

    valid_inspections = [
        item
        for item in inspections
        if isinstance(item.get("inspection"), dict)
        and item["inspection"].get("v11_accept_full_smoke_passed") is True
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
    first_production_inspection = (
        production_inspections[0] if production_inspections else None
    )
    first_production_passed = (
        isinstance(first_production_inspection, dict)
        and isinstance(first_production_inspection.get("inspection"), dict)
        and first_production_inspection["inspection"].get(
            "v11_accept_full_smoke_passed"
        )
        is True
    )
    failed_inspection_count = sum(
        1
        for item in inspections
        if item.get("inspectable") is True
        and isinstance(item.get("inspection"), dict)
        and item["inspection"].get("v11_accept_full_smoke_passed") is not True
    )
    production_failed_inspection_count = sum(
        1
        for item in inspections
        if item.get("production_source") is True
        and (
            item.get("inspectable") is not True
            or not isinstance(item.get("inspection"), dict)
            or item["inspection"].get("v11_accept_full_smoke_passed") is not True
        )
    )
    if first_production_inspection is not None:
        status = "passed" if first_production_passed else "failed"
    elif synthetic_valid_inspections:
        status = "synthetic_only"
    elif production_failed_inspection_count:
        status = "failed"
    elif inspections:
        status = "failed" if failed_inspection_count else "pending"
    else:
        status = "pending"
    selected = first_production_inspection if first_production_passed else None
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
    coverage_audit = audit_coverage(settings.project_root)
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
    }
    if settings.brave_search_api_key_env != "BRAVE_SEARCH_API_KEY":
        required_environment["NSLAB_BRAVE_SEARCH_API_KEY_ENV"] = (
            settings.brave_search_api_key_env
        )
    return {
        "required_environment": required_environment,
        "commands": [
            f"{python_command} research smoke-bundle --path %NSLAB_REAL_BUNDLE_PATH% --require-valid",
            f"{python_command} brain rebuild --mode llm-full",
            f"{python_command} warehouse rebuild",
            f"{python_command} brain audit --deep",
            f"{python_command} doctor --production",
        ],
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
    findings: list[str] = []
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
    return {
        "schema_version": "nslab.production_semantic_index.v1",
        "status": "ready" if not findings else "attention",
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings,
        "vector_index_status": status,
        "embedding_method": embedding_method,
        "expected_source_record_count": expected_source_record_count,
        "source_brain_record_count": source_brain_record_count,
        "indexed_brain_record_count": indexed_brain_record_count,
    }


def _production_warehouse_status(warehouse: object) -> dict[str, Any]:
    if not isinstance(warehouse, dict):
        return {
            "schema_version": "nslab.production_warehouse.v1",
            "applicable": False,
            "passed": True,
            "status": "not_applicable",
            "finding_count": 0,
            "findings": [],
        }
    findings: list[str] = []
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
    ):
        value = warehouse.get(field_name)
        if isinstance(value, dict) and value:
            findings.append(finding)
    return {
        "schema_version": "nslab.production_warehouse.v1",
        "applicable": True,
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
        "required_files_present": warehouse.get("required_files_present"),
        "synced": warehouse.get("synced"),
        "projection_synced": warehouse.get("projection_synced"),
        "count_mismatches": warehouse.get("count_mismatches", {}),
        "identity_mismatches": warehouse.get("identity_mismatches", {}),
        "duplicate_identities": warehouse.get("duplicate_identities", {}),
        "weight_mismatches": warehouse.get("weight_mismatches", {}),
    }


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
            "all_record_count": None,
            "staged_record_count": None,
            "episode_count": None,
            "training_eligible_record_count": None,
            "duplicate_record_ids": [],
            "unknown_training_enabled_record_ids": [],
            "payload_hash_mismatch_record_ids": [],
            "eligible_records_without_provenance": [],
            "brain_delta_count_mismatch_episode_ids": [],
            "brain_delta_record_id_mismatch_episode_ids": [],
            "brain_delta_training_eligible_mismatch_episode_ids": [],
            "brain_delta_type_count_mismatch_episode_ids": [],
            "records_with_raw_payload_hash_mismatch": [],
            "raw_block_hash_mismatch_episode_ids": [],
        }
    findings = _string_list(audit.get("findings"))
    if audit.get("deep") is not True:
        findings.append("deep record-store audit was not run")
    passed = audit.get("passed") is True and audit.get("deep") is True
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
        "all_record_count": audit.get("all_record_count"),
        "staged_record_count": audit.get("staged_record_count"),
        "episode_count": audit.get("episode_count"),
        "training_eligible_record_count": audit.get("training_eligible_record_count"),
        "duplicate_record_ids": audit.get("duplicate_record_ids", []),
        "unknown_training_enabled_record_ids": audit.get(
            "unknown_training_enabled_record_ids",
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
    }


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
    record_manifest_path = (
        settings.project_root / "memory" / "record_manifests" / f"{episode_id}.json"
    )
    envelope = _read_optional_json(envelope_path)
    normalized_index = _read_optional_json(normalized_index_path)
    record_manifest = _read_optional_json(record_manifest_path)
    record_path = settings.project_root / "memory" / "records" / f"{episode_id}.jsonl"
    if isinstance(record_manifest, dict):
        records_file = record_manifest.get("records_file")
        if isinstance(records_file, str) and records_file:
            record_path = settings.path(Path(records_file))
    record_file_stats = _record_file_stats(record_path)
    if envelope is None:
        findings.append("selected real bundle has not been imported into record store")
    else:
        if envelope.get("bundle_schema_version") != "nslab.research_bundle.v11":
            findings.append("imported envelope is not v11")
        if envelope.get("bundle_status") != "ACCEPT_FULL":
            findings.append("imported envelope is not ACCEPT_FULL")
        if envelope.get("blind_valid") is not True:
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
            and record_file_stats["sha256"] != manifest_records_sha
        ):
            findings.append("record JSONL sha does not match record manifest")

    if record_file_stats["exists"] is not True:
        findings.append("record JSONL for selected real bundle is missing")
    elif record_file_stats["invalid_line_count"] != 0:
        findings.append("record JSONL for selected real bundle has invalid rows")
    else:
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
        "record_manifest_path": relative_to_root(
            record_manifest_path,
            settings.project_root,
        ),
        "record_manifest_exists": record_manifest is not None,
        "record_path": relative_to_root(record_path, settings.project_root),
        "record_file_exists": record_file_stats["exists"],
        "record_file_sha256": record_file_stats["sha256"],
        "observed_record_count": record_file_stats["record_count"],
        "observed_training_eligible_record_count": record_file_stats[
            "training_eligible_record_count"
        ],
        "observed_record_counts_by_type": record_file_stats["record_counts_by_type"],
        "observed_record_ids": record_file_stats["record_ids"],
        "duplicate_record_ids": record_file_stats["duplicate_record_ids"],
        "record_file_invalid_line_count": record_file_stats["invalid_line_count"],
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
    compiled_claim_count = _jsonl_line_count(compiled_claims_path)
    findings: list[str] = []
    status = {
        "schema_version": "nslab.production_llm_full_brain.v1",
        "build_mode": build_mode if isinstance(build_mode, str) else None,
        "catalog_only": catalog_only if isinstance(catalog_only, bool) else None,
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
        "compile_report_path": relative_to_root(
            compile_report_path,
            settings.project_root,
        ),
        "compile_report_exists": compile_report is not None,
        "compile_run_present": compile_run is not None,
        "provider": compile_manifest.get("provider")
        if isinstance(compile_manifest, dict)
        else None,
        "model": compile_manifest.get("model")
        if isinstance(compile_manifest, dict)
        else None,
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
    }
    if build_mode != "llm-full":
        return {
            **status,
            "passed": False,
            "status": "not_applicable",
            "finding_count": 0,
            "findings": [],
        }
    if compile_manifest is None:
        findings.append("llm-full compile manifest is missing")
    else:
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
        elif manifest_claim_count != compiled_claim_count:
            findings.append("llm-full compiled claim count does not match JSONL file")
        record_shard_count = status["record_shard_count"]
        if not isinstance(record_shard_count, int) or record_shard_count <= 0:
            findings.append("llm-full record shard accounting is missing")
        category_count = status["category_count"]
        if category_count != len(BRAIN_FILES):
            findings.append("llm-full category count does not match brain category files")
        manifest_generation_count = status["llm_generation_count"]
        if not isinstance(manifest_generation_count, int) or manifest_generation_count <= 0:
            findings.append("llm-full LLM generation accounting is missing")
        if compile_run is None:
            findings.append("llm-full compile run diagnostics are missing")
        else:
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
    if not compiled_claims_path.exists():
        findings.append("compiled claims JSONL is missing")
    elif compiled_claim_count <= 0:
        findings.append("compiled claims JSONL is empty")
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
    is_v11 = adapter == "v11" and bundle_version == "nslab.research_bundle.v11"
    smoke_passed = (
        is_v11
        and inspection.get("validation_passed") is True
        and validation.get("bundle_status_accept_full") is True
        and validation.get("blind_valid") is True
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
    )
    return {
        "status": "passed" if smoke_passed else "failed",
        "v11_accept_full_smoke_passed": smoke_passed,
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
        "dropped_record_count": inspection.get("dropped_record_count"),
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
    }


def _brain_audit_status(coverage_audit: dict[str, object]) -> dict[str, Any]:
    findings = _string_list(coverage_audit.get("brain_audit_findings"))
    return {
        "passed": coverage_audit.get("brain_audit_passed") is True,
        "brain_build_mode": coverage_audit.get("brain_build_mode"),
        "catalog_only": coverage_audit.get("catalog_only"),
        "record_coverage_complete": coverage_audit.get("record_coverage_complete"),
        "deterministic_rebuild_verified": coverage_audit.get(
            "deterministic_rebuild_verified"
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


def _record_file_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "sha256": None,
            "record_count": 0,
            "training_eligible_record_count": 0,
            "record_counts_by_type": {},
            "record_ids": [],
            "duplicate_record_ids": [],
            "invalid_line_count": 0,
        }
    record_count = 0
    training_eligible_count = 0
    record_counts_by_type: dict[str, int] = {}
    record_ids: list[str] = []
    invalid_line_count = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
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
        "record_count": record_count,
        "training_eligible_record_count": training_eligible_count,
        "record_counts_by_type": dict(sorted(record_counts_by_type.items())),
        "record_ids": record_ids,
        "duplicate_record_ids": _duplicate_strings(record_ids),
        "invalid_line_count": invalid_line_count,
    }


def _int_from_mapping(source: object, key: str) -> int | None:
    if not isinstance(source, dict):
        return None
    value = source.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


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
