"""Project diagnostics for the doctor command."""

from __future__ import annotations

import json
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from news_scalping_lab.audits.coverage import audit_coverage
from news_scalping_lab.brain.compiler import current_brain_version
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.schemas import SCHEMA_MODELS
from news_scalping_lab.prices.stock_web import StockWebPriceSource
from news_scalping_lab.retrieval.embedding import VECTOR_EMBEDDING_METHOD
from news_scalping_lab.retrieval.store import inspect_vector_index
from news_scalping_lab.storage import ResearchStore

ENV_KEYS = [
    "NSLAB_LLM_PROVIDER",
    "NSLAB_WEB_PROVIDER",
    "NSLAB_PRICE_PROVIDER",
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


def production_readiness_report(
    report: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    findings: list[str] = []
    if settings.llm_provider.strip().lower() == "mock":
        findings.append("llm: mock provider cannot compile production brain")
    if settings.llm.provider.strip().lower() == "mock":
        findings.append("llm_model: mock model profile cannot compile production brain")
    openai_status = _nested_dict(report, "api_connections", "openai").get("status")
    if settings.llm_provider.strip().lower() in OPENAI_PROVIDER_ALIASES and openai_status != "configured_not_called":
        findings.append("openai: production llm-full requires configured OpenAI SDK and API key")
    brain = report.get("brain")
    if isinstance(brain, dict):
        brain_audit = _nested_dict(brain, "audit")
        if brain_audit and brain_audit.get("passed") is not True:
            findings.append("brain: latest brain audit failed")
        coverage = brain.get("coverage")
        if isinstance(coverage, dict) and coverage.get("status") not in {"complete", "missing"}:
            findings.append("brain: accepted episodes are not fully covered")
    brain_manifest = _read_optional_json(settings.project_root / "brain" / "current" / "brain_manifest.json")
    build_mode = brain_manifest.get("build_mode") if isinstance(brain_manifest, dict) else None
    if build_mode != "llm-full":
        observed_mode = build_mode if isinstance(build_mode, str) and build_mode else "missing"
        findings.append(f"brain: current manifest build_mode is {observed_mode}, not llm-full")
    record_coverage = _read_optional_json(
        settings.project_root / "brain" / "current" / "record_coverage_manifest.json"
    )
    if isinstance(record_coverage, dict) and record_coverage.get("coverage_complete") is not True:
        findings.append("records: record coverage is incomplete")
    vector_index = report.get("vector_index")
    if isinstance(vector_index, dict):
        embedding_method = vector_index.get("embedding_method")
        if embedding_method == VECTOR_EMBEDDING_METHOD:
            findings.append(
                "embedding: deterministic mock vector index cannot be production semantic index"
            )
    return {
        "schema_version": "nslab.production_readiness.v1",
        "passed": not findings,
        "status": "ready" if not findings else "attention",
        "finding_count": len(findings),
        "findings": findings,
    }


def build_doctor_report(settings: Settings) -> dict[str, Any]:
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
                "required": _openai_required(settings),
                "configured": bool(settings.env_value("OPENAI_API_KEY")),
                "sdk": _openai_sdk_status(),
                "status": _openai_status(settings),
            },
            "brave_search": {
                "required": _brave_search_required(settings),
                "configured": bool(settings.env_value(settings.brave_search_api_key_env)),
                "status": _brave_search_status(settings),
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


def _brain_audit_status(coverage_audit: dict[str, object]) -> dict[str, Any]:
    findings = _string_list(coverage_audit.get("brain_audit_findings"))
    return {
        "passed": coverage_audit.get("brain_audit_passed") is True,
        "brain_build_mode": coverage_audit.get("brain_build_mode"),
        "record_coverage_complete": coverage_audit.get("record_coverage_complete"),
        "deterministic_rebuild_verified": coverage_audit.get(
            "deterministic_rebuild_verified"
        ),
        "finding_count": len(findings),
        "findings": findings,
    }


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


def _openai_status(settings: Settings) -> str:
    if not _openai_required(settings):
        return "not_required"
    if not settings.env_value("OPENAI_API_KEY"):
        return "missing_api_key"
    sdk_status = _openai_sdk_status()
    if sdk_status.get("available") is not True:
        return "missing_sdk" if sdk_status.get("error") is None else "sdk_import_error"
    if sdk_status.get("async_client_available") is not True:
        return "sdk_missing_async_client"
    return "configured_not_called"


def _openai_required(settings: Settings) -> bool:
    return settings.llm_provider.strip().lower() in OPENAI_PROVIDER_ALIASES


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


def _brave_search_required(settings: Settings) -> bool:
    return settings.web_provider.strip().lower() in {"brave", "brave-search", "brave-news"}


def _brave_search_status(settings: Settings) -> str:
    if not _brave_search_required(settings):
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


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


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
