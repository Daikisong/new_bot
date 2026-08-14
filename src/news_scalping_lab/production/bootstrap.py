"""Local production bootstrap without persisting OAuth credentials."""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from news_scalping_lab.config import Settings, load_settings
from news_scalping_lab.inference.event_clustering import cluster_news_events
from news_scalping_lab.llm.codex_oauth_provider import (
    CodexOAuthProvider,
    codex_login_status,
    probe_codex_embedding_capability,
)
from news_scalping_lab.policies import EvidencePolicy
from news_scalping_lab.prices.stock_web import StockWebPriceSource
from news_scalping_lab.retrieval.production_embedding import (
    ProductionEmbeddingUnavailableError,
    load_local_production_embedding,
    prepare_local_production_embedding,
)
from news_scalping_lab.utils import KST, now_kst, read_json, sha256_text, write_json
from news_scalping_lab.web.factory import create_web_provider
from news_scalping_lab.web.provider import UnexpectedWebAccessError

HMAC_ENV_KEYS = (
    "NSLAB_PHASE7_TRANSPORT_HMAC_KEY",
    "NSLAB_SHADOW_EVALUATION_HMAC_KEY",
    "NSLAB_SHADOW_RUNNER_HMAC_KEY",
    "NSLAB_SHADOW_TRUTH_HMAC_KEY",
    "NSLAB_PRODUCTION_PROMOTION_HMAC_KEY",
)
HMAC_MIN_CHARACTER_LENGTH = 48
BOOTSTRAP_REPORT_JSON = Path("diagnostics/local_production_bootstrap_report.json")
BOOTSTRAP_REPORT_MD = Path("diagnostics/local_production_bootstrap_report.md")
PROVIDER_IDENTITY_RECEIPT = Path("diagnostics/provider_identity_receipt.json")


class _BootstrapOAuthSmoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    provider: str


def bootstrap_local_environment(
    root: Path,
    *,
    evidence_policy: str = "csv-memory-only-strict",
    llm_provider: str = "codex-oauth",
    embedding_provider: str = "auto",
    stock_web_path: Path | None = None,
    rotate_secrets: bool = False,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    env_path = resolved_root / ".env"
    values = _read_env_values(env_path)
    backup_path: Path | None = None
    if env_path.is_file():
        timestamp = now_kst().strftime("%Y%m%dT%H%M%S%f%z")
        backup_path = env_path.with_name(f".env.backup.{timestamp}")
        shutil.copy2(env_path, backup_path)
        _restrict_secret_file(backup_path)
    secret_results: list[dict[str, str]] = []
    generated_values: set[str] = set()
    for key in HMAC_ENV_KEYS:
        existing = values.get(key, "")
        if existing and not rotate_secrets:
            status = "preserved"
            selected = existing
        else:
            selected = _distinct_secret(generated_values)
            values[key] = selected
            status = "rotated" if existing else "created"
        generated_values.add(selected)
        secret_results.append(
            {
                "key": key,
                "status": status,
                "fingerprint": sha256_text(selected)[:12],
                "strength": (
                    "strong"
                    if len(selected) >= HMAC_MIN_CHARACTER_LENGTH
                    else "weak_existing_value"
                ),
            }
        )
    values.update(
        {
            "NSLAB_EVIDENCE_POLICY": EvidencePolicy.parse(
                evidence_policy
            ).value,
            "NSLAB_LLM_PROVIDER": llm_provider,
            "NSLAB_EMBEDDING_PROVIDER": embedding_provider,
            "NSLAB_EVENT_CLUSTER_FALLBACK_POLICY": "fail-closed",
            "NSLAB_WEB_PROVIDER": "disabled",
            "NSLAB_PRICE_PROVIDER": "stock-web",
        }
    )
    if stock_web_path is not None:
        values["NSLAB_STOCK_WEB_PATH"] = str(stock_web_path.resolve())
    _write_env_atomic(env_path, values)
    permission = _restrict_secret_file(env_path)
    return {
        "schema_version": "nslab.local_env_bootstrap.v1",
        "env_path": env_path.as_posix(),
        "backup_path": backup_path.as_posix() if backup_path else None,
        "secrets": secret_results,
        "distinct_secret_count": len(
            {values[key] for key in HMAC_ENV_KEYS if values.get(key)}
        ),
        "strong_secret_count": sum(
            len(values.get(key, "")) >= HMAC_MIN_CHARACTER_LENGTH
            for key in HMAC_ENV_KEYS
        ),
        "permission_status": permission,
        "gitignored": _env_is_gitignored(resolved_root),
        "oauth_credentials_written": False,
        "openai_api_key_status": _unused_optional_key_status(
            values.get("OPENAI_API_KEY")
        ),
        "brave_api_key_status": _unused_optional_key_status(
            values.get("BRAVE_SEARCH_API_KEY")
        ),
        "environment_scope": (
            "ephemeral-cloud"
            if _is_ephemeral_cloud_environment()
            else "local-working-tree"
        ),
        "local_persistence_claimed": not _is_ephemeral_cloud_environment(),
    }


def prepare_local_production(
    root: Path,
    *,
    rotate_secrets: bool = False,
    stock_web_path: Path | None = None,
    live_oauth_smoke: bool = True,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    started_at = now_kst()
    bootstrap = bootstrap_local_environment(
        resolved_root,
        rotate_secrets=rotate_secrets,
        stock_web_path=stock_web_path,
    )
    settings = load_settings(resolved_root, resolve_production=False)
    checks: dict[str, bool] = {}
    findings: list[str] = []
    checks["env_gitignored"] = bootstrap["gitignored"] is True
    checks["five_distinct_hmac_secrets"] = (
        bootstrap["distinct_secret_count"] == len(HMAC_ENV_KEYS)
        and bootstrap["strong_secret_count"] == len(HMAC_ENV_KEYS)
    )
    checks["local_persistence"] = bootstrap["local_persistence_claimed"] is True
    checks["codex_cli_installed"] = shutil.which(settings.codex_command) is not None
    login = _codex_login_status(settings.codex_command)
    checks["codex_oauth_health"] = login["logged_in"] is True
    if not checks["codex_cli_installed"]:
        findings.append("CODEX_CLI_NOT_INSTALLED")
    if not checks["codex_oauth_health"]:
        findings.append("CODEX_OAUTH_INTERACTIVE_LOGIN_REQUIRED")
    embedding_probe = _codex_embedding_probe(settings.codex_command)
    embedding_selection = (
        "codex-oauth"
        if embedding_probe["supported"] is True
        else "local-production"
    )
    embedding_identity: dict[str, Any] | None = None
    try:
        if embedding_selection == "local-production":
            embedding_identity = prepare_local_production_embedding(settings)
        else:
            raise ProductionEmbeddingUnavailableError(
                "official Codex embedding adapter is not implemented"
            )
    except (OSError, ValueError, ProductionEmbeddingUnavailableError) as exc:
        findings.append(f"embedding_prepare:{type(exc).__name__}:{exc}")
    checks["embedding_ready"] = embedding_identity is not None
    stock_web = _stock_web_status(settings)
    checks["stock_web_ready"] = stock_web["ready"] is True
    if not checks["stock_web_ready"]:
        findings.append(str(stock_web["finding"]))
    checks["evidence_policy"] = (
        settings.evidence_policy is EvidencePolicy.CSV_MEMORY_ONLY_STRICT
    )
    checks["web_disabled"] = settings.web_provider.strip().lower() == "disabled"
    checks["web_guard"] = _disabled_web_guard_smoke(settings)
    checks["event_clustering_fail_closed"] = (
        _event_clustering_fail_closed_smoke(settings)
        if embedding_identity is not None
        else False
    )
    oauth_smoke: dict[str, Any] = {
        "attempted": False,
        "passed": False,
        "reason": "login unavailable or live smoke disabled",
    }
    if live_oauth_smoke and checks["codex_oauth_health"]:
        oauth_smoke = _live_codex_oauth_smoke(settings)
    checks["codex_oauth_live_smoke"] = oauth_smoke["passed"] is True
    if live_oauth_smoke and not checks["codex_oauth_live_smoke"]:
        findings.append(f"codex_oauth_smoke:{oauth_smoke['reason']}")
    provider_receipt = {
        "schema_version": "nslab.production_provider_identity.v1",
        "created_at": now_kst().isoformat(),
        "evidence_policy": settings.evidence_policy.value,
        "web_provider": settings.web_provider,
        "web_required": False,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm.model,
        "codex_login": login,
        "codex_embedding_capability": embedding_probe,
        "selected_embedding_provider": embedding_selection,
        "embedding_identity": embedding_identity,
        "stock_web": stock_web,
        "oauth_credentials_read": False,
    }
    write_json(resolved_root / PROVIDER_IDENTITY_RECEIPT, provider_receipt)
    report = {
        "schema_version": "nslab.local_production_bootstrap_report.v1",
        "started_at": started_at.isoformat(),
        "completed_at": now_kst().isoformat(),
        "passed": all(checks.values()),
        "checks": checks,
        "finding_count": len(findings),
        "findings": findings,
        "env_bootstrap": bootstrap,
        "codex_login": login,
        "oauth_smoke": oauth_smoke,
        "codex_embedding_capability": embedding_probe,
        "embedding_selection": embedding_selection,
        "embedding_identity": embedding_identity,
        "stock_web": stock_web,
        "provider_identity_receipt": PROVIDER_IDENTITY_RECEIPT.as_posix(),
        "large_import_executed": False,
        "shadow_gate_bypassed": False,
        "production_pointer_changed": False,
    }
    write_json(resolved_root / BOOTSTRAP_REPORT_JSON, report)
    (resolved_root / BOOTSTRAP_REPORT_MD).write_text(
        _bootstrap_markdown(report),
        encoding="utf-8",
        newline="\n",
    )
    return report


def production_preflight_report(settings: Settings) -> dict[str, Any]:
    report_path = settings.path(BOOTSTRAP_REPORT_JSON)
    try:
        bootstrap = read_bootstrap_report(report_path)
    except (OSError, ValueError):
        bootstrap = {}
    checks = {
        "evidence_policy_csv_memory_only": (
            settings.evidence_policy is EvidencePolicy.CSV_MEMORY_ONLY_STRICT
        ),
        "web_disabled": settings.web_provider.strip().lower() == "disabled",
        "embedding_fail_closed": (
            settings.event_cluster_fallback_policy.value == "fail-closed"
        ),
        "codex_oauth_selected": (
            settings.llm_provider.strip().lower() == "codex-oauth"
        ),
        "bootstrap_report_passed": bootstrap.get("passed") is True,
        "five_distinct_hmac_secrets": _strong_distinct_hmac_secrets(settings),
    }
    blockers = sorted(key for key, passed in checks.items() if not passed)
    return {
        "schema_version": "nslab.production_preflight.v1",
        "passed": not blockers,
        "checks": checks,
        "blockers": blockers,
        "full_phase9_activation_checked": False,
    }


def read_bootstrap_report(path: Path) -> dict[str, Any]:
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("bootstrap report must be an object")
    return payload


def _write_env_atomic(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".env.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _restrict_secret_file(temporary_path)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def _restrict_secret_file(path: Path) -> str:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        return "WARNING_PERMISSION_RESTRICTION_FAILED"
    if os.name != "nt":
        return "POSIX_0600"
    username = os.environ.get("USERNAME")
    if not username:
        return "WINDOWS_OWNER_RW_CHMOD"
    completed = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{username}:(F)",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return "WINDOWS_ACL_OWNER_ONLY" if completed.returncode == 0 else "WINDOWS_ACL_WARNING"


def _distinct_secret(existing: set[str]) -> str:
    while True:
        value = secrets.token_urlsafe(48)
        if value not in existing:
            return value


def _strong_distinct_hmac_secrets(settings: Settings) -> bool:
    values = [settings.env_value(key) or "" for key in HMAC_ENV_KEYS]
    return (
        all(len(value) >= HMAC_MIN_CHARACTER_LENGTH for value in values)
        and len(set(values)) == len(HMAC_ENV_KEYS)
    )


def _env_is_gitignored(root: Path) -> bool:
    completed = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],
        cwd=root,
        check=False,
    )
    return completed.returncode == 0


def _unused_optional_key_status(value: str | None) -> str:
    return "PRESENT_UNUSED_BY_POLICY" if value else "ABSENT_NOT_REQUIRED"


def _is_ephemeral_cloud_environment() -> bool:
    return any(
        os.environ.get(key)
        for key in ("CI", "CODEX_CLOUD_TASK_ID", "CODEX_CLOUD_ENVIRONMENT")
    )


def _stock_web_status(settings: Settings) -> dict[str, Any]:
    path = settings.stock_web_path
    if path is None:
        return {
            "ready": False,
            "path": None,
            "finding": "NSLAB_STOCK_WEB_PATH is missing",
        }
    resolved = settings.path(path)
    try:
        atlas = StockWebPriceSource(resolved).inspect_atlas_status()
    except (OSError, ValueError) as exc:
        return {
            "ready": False,
            "path": resolved.as_posix(),
            "finding": f"stock-web atlas inspection failed: {type(exc).__name__}",
        }
    research_daily_root = StockWebPriceSource(resolved).atlas_root / "research_daily"
    research_manifest_path = research_daily_root / "manifest.json"
    research_schema_path = research_daily_root / "schema.json"
    research_calendar_path = research_daily_root / "trading_calendar.csv"
    try:
        research_manifest = read_json(research_manifest_path)
        research_schema = read_json(research_schema_path)
    except (OSError, ValueError):
        research_manifest = {}
        research_schema = {}
    research_daily = {
        "root": research_daily_root.as_posix(),
        "manifest_exists": research_manifest_path.is_file(),
        "schema_exists": research_schema_path.is_file(),
        "calendar_exists": research_calendar_path.is_file(),
        "access_root_exists": (research_daily_root / "access").is_dir(),
        "snapshot_root_exists": (research_daily_root / "snapshots").is_dir(),
        "schema_version": (
            research_schema.get("schema_version")
            if isinstance(research_schema, dict)
            else None
        ),
        "max_trade_date": (
            research_manifest.get("max_trade_date")
            if isinstance(research_manifest, dict)
            else None
        ),
        "full_backfill_complete": (
            research_manifest.get("full_backfill_complete") is True
            if isinstance(research_manifest, dict)
            else False
        ),
        "validation_passed": (
            research_manifest.get("validation_passed") is True
            if isinstance(research_manifest, dict)
            else False
        ),
    }
    research_daily_ready = all(
        value is True
        for key, value in research_daily.items()
        if key.endswith("_exists")
        or key in {"full_backfill_complete", "validation_passed"}
    ) and research_daily["schema_version"] == "stock_web.research_daily_snapshot.v1"
    research_daily["ready"] = research_daily_ready
    ready = atlas.get("status") == "ok" and research_daily_ready
    return {
        "ready": ready,
        "path": resolved.as_posix(),
        "atlas": atlas,
        "research_daily": research_daily,
        "finding": (
            "stock-web ready"
            if ready
            else "stock-web atlas or research_daily contract is incomplete"
        ),
    }


def _codex_login_status(command: str) -> dict[str, Any]:
    try:
        return codex_login_status(command)
    except OSError as exc:
        return {
            "logged_in": False,
            "login_method": None,
            "status": "CLI_UNAVAILABLE",
            "error_type": type(exc).__name__,
        }


def _codex_embedding_probe(command: str) -> dict[str, Any]:
    try:
        return probe_codex_embedding_capability(command)
    except OSError as exc:
        return {
            "supported": False,
            "probe": "codex exec --help",
            "credential_accessed": False,
            "reason": f"Codex CLI unavailable: {type(exc).__name__}",
        }


def _disabled_web_guard_smoke(settings: Settings) -> bool:
    provider = create_web_provider(settings)

    async def run() -> bool:
        try:
            await provider.search("must fail", cutoff_at=now_kst())
        except UnexpectedWebAccessError:
            return True
        return False

    return asyncio.run(run())


def _event_clustering_fail_closed_smoke(settings: Settings) -> bool:
    from news_scalping_lab.contracts.models import NewsItem

    provider = load_local_production_embedding(settings)
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    item = NewsItem(
        row_number=1,
        source_id="SRC-preflight",
        event_id="EV-preflight",
        published_at=cutoff,
        title="상장사 공급 계약",
        body="Cutoff-safe CSV event.",
    )
    result = asyncio.run(
        cluster_news_events(
            [item],
            window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
            cutoff_at=cutoff,
            embedding_provider=provider,
            embedding_batch_size=8,
            similarity_threshold=0.9,
            fallback_policy="fail-closed",
            production_runtime_identity=provider.embedding_method,
        )
    )
    return (
        result.embedding_status == "PROVIDER"
        and result.deterministic_fallback_used is False
        and result.embedding_dimensions == provider.dimensions
    )


def _live_codex_oauth_smoke(settings: Settings) -> dict[str, Any]:
    provider = CodexOAuthProvider(
        command=settings.codex_command,
        model=settings.llm.model,
        reasoning_effort=(
            settings.llm.reasoning_effort or settings.codex_reasoning_effort
        ),
        max_output_tokens=128,
        structured_repair_retries=0,
    )
    try:
        result = asyncio.run(
            provider.generate_structured(
                prompt=(
                    "Return status=ok and provider=codex-oauth as JSON matching "
                    "the supplied schema. Do not use tools."
                ),
                response_model=_BootstrapOAuthSmoke,
                purpose="production_prepare_oauth_smoke",
            )
        )
    except Exception as exc:
        return {
            "attempted": True,
            "passed": False,
            "reason": f"{type(exc).__name__}:{exc}",
        }
    return {
        "attempted": True,
        "passed": result.status == "ok" and result.provider == "codex-oauth",
        "reason": "PASS",
        "identity": provider.identity(),
    }


def _bootstrap_markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks", {})
    lines = [
        "# Local Production Bootstrap Report",
        "",
        f"- passed: `{report.get('passed')}`",
        f"- embedding_selection: `{report.get('embedding_selection')}`",
        f"- finding_count: `{report.get('finding_count')}`",
        "",
        "## Checks",
        "",
    ]
    if isinstance(checks, dict):
        lines.extend(
            f"- {key}: `{value}`" for key, value in sorted(checks.items())
        )
    lines.extend(
        [
            "",
            "No secret or OAuth credential value is included in this report.",
            "",
        ]
    )
    return "\n".join(lines)
