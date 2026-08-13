"""Run repair-only research bundle processing one source at a time.

This command intentionally performs isolated imports only. It never writes the
production research, memory, warehouse, or brain stores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.records.store import audit_record_store
from news_scalping_lab.research_import.repair_census import census_source
from news_scalping_lab.research_import.repair_models import RepairTaskState
from news_scalping_lab.research_import.repair_quality import evaluate_bundle_quality
from news_scalping_lab.research_import.repair_routing import classify_repair_source
from news_scalping_lab.research_import.versioned_bundle import (
    VersionedBundleImportError,
    import_versioned_bundle,
)
from news_scalping_lab.tools.repair_research_bundle import repair_bundle
from news_scalping_lab.utils import canonical_json, sha256_text

ROOT = Path(__file__).resolve().parents[3]
RAW_ROOT = ROOT / "research/inbox/bundles/raw"
REPAIRED_ROOT = ROOT / "research/inbox/bundles/repaired"
WORK_ROOT = ROOT / "research/inbox/bundles/.work"
MANIFEST_PATH = REPAIRED_ROOT / "sequential_repair_manifest.v2.jsonl"
CSV_ROOT = ROOT / "docs/csv"
STORE_PATHS = (
    ROOT / "data/raw/research",
    ROOT / "research/episodes",
    ROOT / "memory/records",
    ROOT / "memory/record_manifests",
    ROOT / "memory/record_index",
    ROOT / "warehouse",
    ROOT / "brain/current",
)
_TERMINAL_PRESERVED_STATUSES = {
    "DEFERRED_NON_TRADING",
    "PARTIAL_PRICE_SOURCE_MISSING",
    "PRESERVED_SOURCE_PAYLOAD_ABSENT",
    "PRESERVED_PARTIAL_NOT_CURRENT_GOLD",
}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def build_engine_manifest() -> dict[str, Any]:
    paths = [ROOT / "pyproject.toml"]
    for lock_name in ("uv.lock", "poetry.lock", "pdm.lock", "requirements.lock"):
        lock_path = ROOT / lock_name
        if lock_path.exists():
            paths.append(lock_path)
    paths.extend(sorted((ROOT / "src/news_scalping_lab").rglob("*.py")))
    files = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": sha256_bytes(path.read_bytes()),
            "byte_size": path.stat().st_size,
        }
        for path in paths
    ]
    manifest: dict[str, Any] = {
        "schema_version": "nslab.repair_engine_manifest.v1",
        "files": files,
        "python_major_minor": [str(sys.version_info.major), str(sys.version_info.minor)],
    }
    manifest["engine_digest"] = sha256_text(canonical_json(manifest))
    return manifest


def tree_snapshot() -> dict[str, Any]:
    rows: list[tuple[str, int, str]] = []
    for base in STORE_PATHS:
        if not base.exists():
            continue
        for path in sorted(candidate for candidate in base.rglob("*") if candidate.is_file()):
            payload = path.read_bytes()
            rows.append((path.relative_to(ROOT).as_posix(), len(payload), sha256_bytes(payload)))
    digest_payload = "".join(
        f"{path}\0{size}\0{digest}\n" for path, size, digest in rows
    ).encode()
    return {
        "file_count": len(rows),
        "byte_size": sum(size for _, size, _ in rows),
        "sha256": sha256_bytes(digest_payload),
    }


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = source.read_bytes()
    digest = sha256_bytes(payload)
    if destination.exists():
        if sha256_bytes(destination.read_bytes()) == digest:
            return
        raise RuntimeError(f"repaired output conflict: {destination}")
    partial = destination.with_name(
        f".{destination.name}.{os.getpid()}.partial"
    )
    try:
        partial.write_bytes(payload)
        if sha256_bytes(partial.read_bytes()) != digest:
            raise RuntimeError("repaired partial hash mismatch")
        os.replace(partial, destination)
    finally:
        if partial.exists():
            partial.unlink()


def _source_date(path: Path) -> str | None:
    valid_years = {
        "2018",
        "2019",
        "2020",
        "2021",
        "2022",
        "2023",
        "2024",
        "2025",
        "2026",
    }
    for token in path.name.split("_"):
        if len(token) >= 8 and token[:8].isdigit() and token[:4] in valid_years:
            return token[:8]
        if len(token) == 10 and token[4] == "-" and token[7] == "-":
            compact = token.replace("-", "")
            if compact.isdigit() and compact[:4] in valid_years:
                return compact
    return None


def _final_path(source: Path, source_sha256: str, engine_digest: str) -> Path:
    date_token = _source_date(source) or "unknown"
    year = date_token[:4] if date_token[:4].isdigit() else "unknown"
    return (
        REPAIRED_ROOT
        / year
        / f"{source.stem}.{source_sha256[:12]}.{engine_digest[:12]}.repaired.md"
    )


def _quarantine_existing_repaired_artifacts(
    source: Path,
    source_sha256: str,
) -> list[str]:
    """Move stale promotable outputs out of the normal ingest directory."""

    date_token = _source_date(source) or "unknown"
    year = date_token[:4] if date_token[:4].isdigit() else "unknown"
    year_root = REPAIRED_ROOT / year
    if not year_root.exists():
        return []
    quarantine_root = REPAIRED_ROOT / "quarantined" / year
    moved: list[str] = []
    for candidate in sorted(year_root.glob(f"*{source_sha256[:12]}*.repaired.md")):
        destination = quarantine_root / candidate.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate, destination)
        moved.append(str(destination))
    return moved


def _isolated_validation(
    repaired_path: Path,
    *,
    production_before: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    isolated_root: Path | None = None
    with tempfile.TemporaryDirectory(prefix="nslab-sequential-mechanical-") as name:
        isolated_root = Path(name)
        try:
            result = import_versioned_bundle(
                repaired_path,
                root=isolated_root,
                validate=True,
                accepted=True,
                allow_external_quality_pending_for_isolated_validation=True,
            )
            deep = audit_record_store(isolated_root, deep=True)
            diagnostic_path = isolated_root / "diagnostics/bundle_import_report.json"
            diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            result_payload = jsonable(asdict(result))
        except VersionedBundleImportError as error:
            # Validation rejection is a source-level partial result, not a
            # runner crash. Keep the exact importer message in the sidecar so
            # the same source can be targeted after a generic repair fix.
            message = str(error)
            result_payload = {
                "status": "validation_failed",
                "accepted": False,
                "error_type": type(error).__name__,
                "error": message,
            }
            deep = {
                "passed": False,
                "findings": [
                    {
                        "type": "versioned_bundle_import_error",
                        "message": message,
                    }
                ],
            }
            diagnostic = {
                "status": "validation_failed",
                "error_type": type(error).__name__,
                "error": message,
            }
    production_after = tree_snapshot()
    isolated_removed = isolated_root is not None and not isolated_root.exists()
    passed = (
        result_payload.get("status") == "imported"
        and result_payload.get("accepted") is True
        and deep.get("passed") is True
        and not deep.get("findings")
        and isolated_removed
        and production_before == production_after
    )
    return (
        {
            "schema_version": "nslab.mechanical_isolated_import_audit.v1",
            "passed": passed,
            "import_result": result_payload,
            "import_diagnostic": diagnostic,
            "deep_audit": deep,
            "production_store_before": production_before,
            "production_store_after": production_after,
            "real_store_unchanged": production_before == production_after,
            "isolated_root_removed": isolated_removed,
        },
        {
            "passed": passed,
            "import_status": result_payload.get("status"),
            "accepted": result_payload.get("accepted"),
            "record_count": result_payload.get("record_count"),
            "training_eligible_record_count": result_payload.get(
                "training_eligible_record_count"
            ),
            "deep_audit_passed": deep.get("passed"),
            "real_store_unchanged": production_before == production_after,
        },
    )


def process_source(source: Path, engine: dict[str, Any]) -> dict[str, Any]:
    source = source.resolve()
    payload = source.read_bytes()
    source_sha256 = sha256_bytes(payload)
    work = WORK_ROOT / source_sha256
    work.mkdir(parents=True, exist_ok=True)
    census = census_source(source)
    state, reason = classify_repair_source(source, census=census)
    inventory = {
        "schema_version": "nslab.repair_source_inventory.v1",
        "source_path": str(source),
        "source_sha256": source_sha256,
        "byte_size": len(payload),
        "source_mtime_ns": source.stat().st_mtime_ns,
        "filename_date": _source_date(source),
        "classification": state.value,
        "classification_reason": reason,
    }
    write_json(work / "source_census.json", census.model_dump(mode="json"))
    write_json(work / "source_inventory.json", inventory)
    base_result: dict[str, Any] = {
        **inventory,
        "started_at": datetime.now().astimezone().isoformat(),
        "engine_digest": engine["engine_digest"],
    }
    if state is not RepairTaskState.DISCOVERED:
        quarantined_paths: list[str] = []
        if reason.startswith("source_declares_"):
            quarantined_paths = _quarantine_existing_repaired_artifacts(
                source,
                source_sha256,
            )
        base_result.update(
            {
                "final_status": state.value,
                "ready_for_import": False,
                "production_import_performed": False,
                "brain_ingest_blocked": state
                in {
                    RepairTaskState.PRESERVED_PARTIAL_NOT_CURRENT_GOLD,
                    RepairTaskState.PRESERVED_SOURCE_PAYLOAD_ABSENT,
                }
                and reason.startswith("source_declares_"),
                "quarantined_repaired_paths": quarantined_paths,
            }
        )
        write_json(work / "sequential_result.json", base_result)
        return base_result

    repaired_a = work / "candidate-a.repaired.md"
    repaired_b = work / "candidate-b.repaired.md"
    try:
        summary_a = repair_bundle(source, repaired_a, news_csv_root=CSV_ROOT)
        summary_b = repair_bundle(source, repaired_b, news_csv_root=CSV_ROOT)
    except (VersionedBundleImportError, ValueError) as error:
        # A malformed or newly observed machine wrapper must become an
        # auditable stop for this source, not an unrecorded runner crash.
        base_result.update(
            {
                "final_status": RepairTaskState.ADAPTER_REQUIRED.value,
                "ready_for_import": False,
                "production_import_performed": False,
                "blockers": [
                    f"REPAIR_PARSE_ERROR:{type(error).__name__}:{error}"
                ],
                "error_type": type(error).__name__,
                "error": str(error),
            }
        )
        write_json(work / "sequential_result.json", base_result)
        return base_result
    payload_a = repaired_a.read_bytes()
    payload_b = repaired_b.read_bytes()
    deterministic = {
        "matches": payload_a == payload_b,
        "first_sha256": sha256_bytes(payload_a),
        "second_sha256": sha256_bytes(payload_b),
    }
    if not deterministic["matches"]:
        raise RuntimeError(f"deterministic repair outputs differ: {source}")
    write_json(work / "engine_manifest.json", engine)
    write_json(work / "repair_summary_a.json", summary_a)
    write_json(work / "repair_summary_b.json", summary_b)
    production_before = tree_snapshot()
    isolated, ephemeral = _isolated_validation(
        repaired_a,
        production_before=production_before,
    )
    write_json(work / "isolated_import_audit.json", isolated)
    gate, lineage, auxiliary = evaluate_bundle_quality(
        source,
        repaired_a,
        engine_digest=str(engine["engine_digest"]),
        deterministic=deterministic,
        ephemeral_store=ephemeral,
        news_csv_root=CSV_ROOT,
    )
    write_json(work / "mechanical_quality_gate.json", gate.model_dump(mode="json"))
    (work / "mechanical_lineage.jsonl").write_text(
        "".join(
            json.dumps(row.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
            for row in lineage
        ),
        encoding="utf-8",
    )
    write_json(work / "mechanical_auxiliary.json", auxiliary)
    final_path: Path | None = None
    if gate.ready_for_import_pass:
        final_path = _final_path(
            source,
            source_sha256,
            str(engine["engine_digest"]),
        )
        _atomic_copy(repaired_a, final_path)
    base_result.update(
        {
            "repaired_sha256": deterministic["first_sha256"],
            "repaired_byte_size": len(payload_a),
            "deterministic": deterministic["matches"],
            "record_count": summary_a.get("record_count"),
            "training_eligible_record_count": summary_a.get("training_eligible_record_count"),
            "semantic_excluded_record_count": summary_a.get("semantic_excluded_record_count", 0),
            "isolated_import_passed": isolated["passed"],
            "deep_audit_passed": isolated["deep_audit"].get("passed"),
            "production_store_unchanged": isolated["real_store_unchanged"],
            "final_status": gate.final_status,
            "ready_for_import": gate.ready_for_import_pass,
            "blockers": gate.blockers,
            "warnings": gate.warnings,
            "quality_gate_path": str(work / "mechanical_quality_gate.json"),
            "lineage_path": str(work / "mechanical_lineage.jsonl"),
            "repaired_path": str(final_path) if final_path is not None else None,
            "production_import_performed": False,
        }
    )
    write_json(work / "sequential_result.json", base_result)
    return base_result


def _read_manifest() -> list[dict[str, Any]]:
    if not MANIFEST_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_manifest(rows: list[dict[str, Any]]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    partial = MANIFEST_PATH.with_name(f".{MANIFEST_PATH.name}.{os.getpid()}.partial")
    try:
        with partial.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, MANIFEST_PATH)
    finally:
        if partial.exists():
            partial.unlink()


def _append_manifest(row: dict[str, Any]) -> None:
    rows = _read_manifest()
    source_sha256 = row.get("source_sha256")
    rows = [
        existing
        for existing in rows
        if existing.get("source_sha256") != source_sha256
    ]
    rows.append(row)
    rows.sort(
        key=lambda value: (
            str(value.get("filename_date") or "99999999"),
            str(value.get("source_path") or ""),
        )
    )
    _write_manifest(rows)


def _compact_manifest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_source: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_sha256 = row.get("source_sha256")
        if isinstance(source_sha256, str):
            latest_by_source[source_sha256] = row
    compacted = list(latest_by_source.values())
    compacted.sort(
        key=lambda value: (
            str(value.get("filename_date") or "99999999"),
            str(value.get("source_path") or ""),
        )
    )
    return compacted


def _source_files() -> list[Path]:
    return sorted(
        RAW_ROOT.rglob("*.md"),
        key=lambda path: (_source_date(path) or "99999999", path.as_posix()),
    )


def _is_resumable(row: dict[str, Any]) -> bool:
    status = str(row.get("final_status") or "")
    if status in {"ADAPTER_REQUIRED", "FATAL_INPUT_FAILURE"}:
        return False
    if row.get("ready_for_import") is True:
        path = row.get("repaired_path")
        return isinstance(path, str) and Path(path).exists()
    return status in {
        "DEFERRED_NON_TRADING",
        "PARTIAL_PRICE_SOURCE_MISSING",
        "PRESERVED_SOURCE_PAYLOAD_ABSENT",
        "PRESERVED_PARTIAL_NOT_CURRENT_GOLD",
    }


def _matches_source_filter(source: Path, *, source_date: str | None, source_path: str | None) -> bool:
    if source_date is not None and _source_date(source) != source_date:
        return False
    if source_path is not None:
        try:
            return source.resolve() == Path(source_path).expanduser().resolve()
        except OSError:
            return False
    return True


def run(
    *,
    max_files: int | None,
    resume: bool,
    stop_on_blocker: bool,
    source_date: str | None = None,
    source_path: str | None = None,
    retry_existing: bool = False,
) -> int:
    engine = build_engine_manifest()
    previous = _compact_manifest(_read_manifest())
    if MANIFEST_PATH.exists():
        _write_manifest(previous)
    previous_by_sha = {
        str(row.get("source_sha256")): row
        for row in previous
        if isinstance(row.get("source_sha256"), str)
    }
    processed = 0
    for source in _source_files():
        if not _matches_source_filter(
            source,
            source_date=source_date,
            source_path=source_path,
        ):
            continue
        if max_files is not None and processed >= max_files:
            break
        source_sha256 = sha256_bytes(source.read_bytes())
        old = previous_by_sha.get(source_sha256)
        if resume and old is not None and not retry_existing:
            if _is_resumable(old):
                continue
            # A prior partial/adapter result is a deliberate stop point. Do
            # not silently march past it; the caller must either repair this
            # source with --retry-existing/--source-date or explicitly opt
            # into a continuation scan.
            if stop_on_blocker:
                print(
                    json.dumps(
                        {
                            "source_path": str(source),
                            "source_sha256": source_sha256,
                            "existing_status": old.get("final_status"),
                            "action": "RETRY_EXISTING_SOURCE_REQUIRED",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return 2
            continue
        result = process_source(source, engine)
        _append_manifest(result)
        previous_by_sha[source_sha256] = result
        processed += 1
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        status = str(result.get("final_status") or "")
        if stop_on_blocker and status in {"ADAPTER_REQUIRED", "FATAL_INPUT_FAILURE"}:
            return 2
        if (
            stop_on_blocker
            and result.get("ready_for_import") is False
            and status not in _TERMINAL_PRESERVED_STATUSES
        ):
            return 2
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequential repair-only bundle runner")
    parser.add_argument(
        "--max-files",
        type=int,
        default=1,
        help="Must be 1: sequential repair closes one source before the next",
    )
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--continue-after-blocker", action="store_true")
    parser.add_argument("--source-date", help="Process only the YYYYMMDD source date")
    parser.add_argument("--source-path", help="Process only this exact source path")
    parser.add_argument(
        "--retry-existing",
        action="store_true",
        help="Re-run an existing source instead of stopping at its prior result",
    )
    args = parser.parse_args()
    if args.max_files != 1:
        parser.error(
            "sequential repair accepts exactly one source per invocation; "
            "use --max-files 1"
        )
    raise SystemExit(
        run(
            max_files=args.max_files,
            resume=not args.no_resume,
            stop_on_blocker=not args.continue_after_blocker,
            source_date=args.source_date,
            source_path=args.source_path,
            retry_existing=args.retry_existing,
        )
    )


if __name__ == "__main__":
    main()
