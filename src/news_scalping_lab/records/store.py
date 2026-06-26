"""Immutable file-backed brain record store."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.records.models import (
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.utils import canonical_json, file_sha256, read_json, sha256_text, write_json


@dataclass(frozen=True)
class StoredBundleResult:
    envelope_path: Path
    index_path: Path
    record_path: Path
    manifest_path: Path
    record_count: int
    training_eligible_record_count: int


class BrainRecordStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw_research_dir = root / "data" / "raw" / "research"
        self.research_episodes_dir = root / "research" / "episodes"
        self.records_dir = root / "memory" / "records"
        self.record_manifests_dir = root / "memory" / "record_manifests"
        self.record_index_dir = root / "memory" / "record_index"
        self.quarantine_dir = root / "data" / "quarantine" / "research_bundles"
        for directory in (
            self.raw_research_dir,
            self.research_episodes_dir,
            self.records_dir,
            self.record_manifests_dir,
            self.record_index_dir,
            self.quarantine_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def store_bundle(
        self,
        *,
        source_path: Path,
        envelope: ResearchBundleEnvelope,
        index: NormalizedEpisodeIndex,
        records: list[BrainRecordEnvelope],
        raw_blocks: dict[str, str],
        validation_report: dict[str, Any],
        accepted: bool = True,
    ) -> StoredBundleResult:
        source_hash = file_sha256(source_path)
        episode_dir = self.research_episodes_dir / envelope.episode_id
        original_bundle = episode_dir / "original_bundle.md"
        if original_bundle.exists() and file_sha256(original_bundle) != source_hash:
            quarantine = self.quarantine_conflict(
                source_path=source_path,
                reason="EPISODE_HASH_CONFLICT",
                episode_id=envelope.episode_id,
                source_hash=source_hash,
            )
            raise ValueError(
                "episode already exists with different bundle hash; "
                f"quarantined at {quarantine.as_posix()}"
            )

        existing_ids = _record_id_index(self.list_records(accepted_only=False))
        for record in records:
            existing = existing_ids.get(record.record_id)
            if existing is None:
                continue
            if (
                existing.get("episode_id") != record.episode_id
                or existing.get("normalized_payload_sha256")
                != record.normalized_payload_sha256
            ):
                quarantine = self.quarantine_conflict(
                    source_path=source_path,
                    reason="RECORD_ID_CONFLICT",
                    episode_id=envelope.episode_id,
                    source_hash=source_hash,
                )
                raise ValueError(
                    "record_id already exists with different payload; "
                    f"quarantined at {quarantine.as_posix()}"
                )

        episode_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, original_bundle)
        shutil.copy2(source_path, self.raw_research_dir / f"{source_hash}.md")
        raw_blocks_dir = episode_dir / "raw_blocks"
        if raw_blocks_dir.exists():
            shutil.rmtree(raw_blocks_dir)
        raw_blocks_dir.mkdir(parents=True, exist_ok=True)
        raw_block_paths: dict[str, str] = {}
        for name, payload in sorted(raw_blocks.items()):
            block_path = raw_blocks_dir / _safe_block_filename(name)
            block_path.write_text(payload, encoding="utf-8")
            raw_block_paths[name] = block_path.relative_to(self.root).as_posix()

        record_path = self.records_dir / f"{envelope.episode_id}.jsonl"
        record_payload = "".join(
            record.model_dump_json() + "\n"
            for record in sorted(records, key=lambda item: item.record_id)
        )
        record_path.write_text(record_payload, encoding="utf-8")
        record_counts = Counter(record.record_type for record in records)
        eligible_count = sum(1 for record in records if record.training_eligible)
        index = index.model_copy(
            update={
                "record_ids": [record.record_id for record in records],
                "record_count_by_type": dict(sorted(record_counts.items())),
                "training_eligible_record_count": eligible_count,
            }
        )
        index_path = episode_dir / "normalized_episode_index.json"
        write_json(index_path, index.model_dump(mode="json"))
        manifest_path = self.record_manifests_dir / f"{envelope.episode_id}.json"
        record_manifest = {
            "schema_version": "nslab.record_manifest.v1",
            "episode_id": envelope.episode_id,
            "accepted": accepted,
            "acceptance_status": "accepted" if accepted else "staged",
            "record_count": len(records),
            "training_eligible_record_count": eligible_count,
            "record_counts_by_type": dict(sorted(record_counts.items())),
            "record_ids": [record.record_id for record in records],
            "records_file": record_path.relative_to(self.root).as_posix(),
            "records_sha256": sha256_text(record_payload),
        }
        write_json(manifest_path, record_manifest)
        envelope = envelope.model_copy(
            update={
                "raw_block_paths": raw_block_paths,
                "normalized_episode_index_path": index_path.relative_to(self.root).as_posix(),
                "record_manifest_path": manifest_path.relative_to(self.root).as_posix(),
            }
        )
        envelope_path = episode_dir / "bundle_envelope.json"
        write_json(envelope_path, envelope.model_dump(mode="json"))
        write_json(episode_dir / "validation_report.json", validation_report)
        self.rebuild_indexes()
        return StoredBundleResult(
            envelope_path=envelope_path,
            index_path=index_path,
            record_path=record_path,
            manifest_path=manifest_path,
            record_count=len(records),
            training_eligible_record_count=eligible_count,
        )

    def quarantine_conflict(
        self,
        *,
        source_path: Path,
        reason: str,
        episode_id: str,
        source_hash: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        target_dir = self.quarantine_dir / f"{episode_id}-{source_hash[:12]}"
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_dir / "original_bundle.md")
        write_json(
            target_dir / "quarantine.json",
            {
                "schema_version": "nslab.bundle_quarantine.v1",
                "reason": reason,
                "episode_id": episode_id,
                "source_path": source_path.as_posix(),
                "source_sha256": source_hash,
                "metadata": metadata or {},
            },
        )
        return target_dir

    def list_records(self, *, accepted_only: bool = True) -> list[BrainRecordEnvelope]:
        records: list[BrainRecordEnvelope] = []
        for path in sorted(self.records_dir.glob("*.jsonl")):
            if accepted_only and not self.episode_records_accepted(path.stem):
                continue
            records.extend(self.read_episode_records(path.stem))
        return sorted(records, key=lambda record: (record.trade_date, record.record_id))

    def episode_records_accepted(self, episode_id: str) -> bool:
        manifest_path = self.record_manifests_dir / f"{episode_id}.json"
        if not manifest_path.exists():
            return True
        payload = read_json(manifest_path)
        if not isinstance(payload, dict):
            return True
        return payload.get("accepted") is not False

    def accept_episode_records(self, episode_id: str) -> Path:
        records_path = self.records_dir / f"{episode_id}.jsonl"
        if not records_path.exists():
            raise FileNotFoundError(f"records not found for episode: {episode_id}")
        manifest_path = self.record_manifests_dir / f"{episode_id}.json"
        payload = read_json(manifest_path) if manifest_path.exists() else {}
        if not isinstance(payload, dict):
            payload = {}
        payload.update(
            {
                "schema_version": payload.get("schema_version", "nslab.record_manifest.v1"),
                "episode_id": episode_id,
                "accepted": True,
                "acceptance_status": "accepted",
            }
        )
        write_json(manifest_path, payload)
        self.rebuild_indexes()
        return manifest_path

    def read_episode_records(self, episode_id: str) -> list[BrainRecordEnvelope]:
        path = self.records_dir / f"{episode_id}.jsonl"
        if not path.exists():
            return []
        records: list[BrainRecordEnvelope] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(BrainRecordEnvelope.model_validate_json(line))
        return records

    def get_record(self, record_id: str) -> BrainRecordEnvelope:
        for record in self.list_records():
            if record.record_id == record_id:
                return record
        raise FileNotFoundError(f"record not found: {record_id}")

    def list_indexes(self) -> list[NormalizedEpisodeIndex]:
        indexes: list[NormalizedEpisodeIndex] = []
        for path in sorted(self.research_episodes_dir.glob("*/normalized_episode_index.json")):
            indexes.append(NormalizedEpisodeIndex.model_validate(read_json(path)))
        return sorted(indexes, key=lambda item: (item.trade_date, item.episode_id))

    def record_id_index(self) -> dict[str, dict[str, str]]:
        path = self.record_index_dir / "by_record_id.json"
        if not path.exists():
            return {}
        payload = read_json(path)
        if not isinstance(payload, dict):
            return {}
        return {
            str(key): {
                str(nested_key): str(nested_value)
                for nested_key, nested_value in value.items()
                if isinstance(nested_key, str) and isinstance(nested_value, str)
            }
            for key, value in payload.items()
            if isinstance(value, dict)
        }

    def rebuild_indexes(self) -> dict[str, Any]:
        records = self.list_records()
        by_record_id = _record_id_index(records)
        counts_by_type = Counter(record.record_type for record in records)
        counts_by_phase = Counter(record.evidence_phase for record in records)
        counts_by_target = Counter(
            record.training_target or "UNKNOWN" for record in records
        )
        manifest = {
            "schema_version": "nslab.record_index_manifest.v1",
            "record_count": len(records),
            "episode_count": len({record.episode_id for record in records}),
            "training_eligible_record_count": sum(
                1 for record in records if record.training_eligible
            ),
            "record_counts_by_type": dict(sorted(counts_by_type.items())),
            "record_counts_by_evidence_phase": dict(sorted(counts_by_phase.items())),
            "record_counts_by_training_target": dict(sorted(counts_by_target.items())),
            "records_sha256": sha256_text(
                "\n".join(record.normalized_payload_sha256 for record in records)
            ),
        }
        write_json(self.record_index_dir / "by_record_id.json", by_record_id)
        write_json(self.record_index_dir / "manifest.json", manifest)
        return manifest

    def stats(self, *, as_of: datetime | None = None) -> dict[str, Any]:
        records = self.list_records()
        if as_of is not None:
            records = [
                record for record in records if record.available_from <= as_of
            ]
        return _record_stats(records)


def audit_record_store(root: Path, *, deep: bool = False) -> dict[str, Any]:
    store = BrainRecordStore(root)
    records = store.list_records()
    records_by_episode = _records_by_episode(records)
    ids = [record.record_id for record in records]
    duplicate_ids = sorted(
        record_id for record_id, count in Counter(ids).items() if count > 1
    )
    unknown_training_enabled = sorted(
        record.record_id
        for record in records
        if record.typed_payload_status == "UNKNOWN_TYPED_PAYLOAD"
        and record.training_eligible
    )
    missing_payload_hashes = sorted(
        record.record_id
        for record in records
        if sha256_text(canonical_json(record.payload)) != record.normalized_payload_sha256
    )
    missing_provenance = sorted(
        record.record_id
        for record in records
        if record.training_eligible and not record.provenance_source_ids
    )
    deep_result = _audit_deep_record_store(root, store, records_by_episode) if deep else {}
    findings = []
    if duplicate_ids:
        findings.append("record_id values are not globally unique")
    if unknown_training_enabled:
        findings.append("unknown record types are marked training_eligible")
    if missing_payload_hashes:
        findings.append("record payload hashes do not match normalized payloads")
    if deep and missing_provenance:
        findings.append("eligible records are missing provenance_source_ids")
    if deep:
        findings.extend(deep_result["findings"])
    return {
        "schema_version": "nslab.record_store_audit.v1",
        "passed": not findings,
        "deep": deep,
        "record_count": len(records),
        "episode_count": len({record.episode_id for record in records}),
        "training_eligible_record_count": sum(
            1 for record in records if record.training_eligible
        ),
        "duplicate_record_ids": duplicate_ids,
        "unknown_training_enabled_record_ids": unknown_training_enabled,
        "payload_hash_mismatch_record_ids": missing_payload_hashes,
        "eligible_records_without_provenance": missing_provenance,
        **deep_result,
        "findings": findings,
        "stats": _record_stats(records),
    }


def record_store_report_payload(
    root: Path,
    audit_result: dict[str, Any],
    *,
    warehouse_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    stats_value = audit_result.get("stats")
    stats = stats_value if isinstance(stats_value, dict) else {}
    effective_warehouse_counts = (
        warehouse_counts
        if warehouse_counts is not None
        else _existing_report_warehouse_counts(root)
    )
    return {
        "schema_version": "nslab.brain_record_store_report.v1",
        "record_count": audit_result.get("record_count", 0),
        "training_eligible_record_count": audit_result.get(
            "training_eligible_record_count",
            0,
        ),
        "record_counts_by_type": stats.get("record_counts_by_type", {}),
        "warehouse_counts": effective_warehouse_counts,
        "dropped_record_count": 0,
        "quarantined_record_count": quarantined_bundle_count(root),
        "audit_passed": audit_result.get("passed") is True,
        "record_store_audit": audit_result,
    }


def quarantined_bundle_count(root: Path) -> int:
    quarantine_dir = root / "data" / "quarantine" / "research_bundles"
    if not quarantine_dir.exists():
        return 0
    return sum(1 for path in quarantine_dir.iterdir() if path.is_dir())


def _existing_report_warehouse_counts(root: Path) -> dict[str, int]:
    report_path = root / "diagnostics" / "brain_record_store_report.json"
    if not report_path.exists():
        return {}
    try:
        payload = read_json(report_path)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    counts = payload.get("warehouse_counts")
    if not isinstance(counts, dict):
        return {}
    return {
        str(key): value
        for key, value in counts.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }


def _audit_deep_record_store(
    root: Path,
    store: BrainRecordStore,
    records_by_episode: dict[str, list[BrainRecordEnvelope]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "missing_record_manifest_episode_ids": [],
        "manifest_count_mismatch_episode_ids": [],
        "manifest_record_id_mismatch_episode_ids": [],
        "manifest_training_eligible_mismatch_episode_ids": [],
        "manifest_type_count_mismatch_episode_ids": [],
        "manifest_hash_mismatch_episode_ids": [],
        "missing_normalized_index_episode_ids": [],
        "index_record_id_mismatch_episode_ids": [],
        "index_training_eligible_mismatch_episode_ids": [],
        "index_type_count_mismatch_episode_ids": [],
        "missing_bundle_envelope_episode_ids": [],
        "raw_block_hash_mismatch_episode_ids": [],
        "brain_delta_count_mismatch_episode_ids": [],
        "brain_delta_record_id_mismatch_episode_ids": [],
        "records_missing_source_block": [],
        "records_missing_source_line": [],
        "records_with_invalid_source_line": [],
        "records_with_raw_payload_hash_mismatch": [],
        "eligible_records_with_unknown_provenance_sources": [],
        "records_with_naive_available_from": [],
        "findings": [],
    }
    for episode_id, episode_records in sorted(records_by_episode.items()):
        manifest_path = store.record_manifests_dir / f"{episode_id}.json"
        manifest = _read_json_dict(manifest_path)
        if manifest is None:
            result["missing_record_manifest_episode_ids"].append(episode_id)
        else:
            _audit_record_manifest(
                root=root,
                episode_id=episode_id,
                records=episode_records,
                manifest=manifest,
                result=result,
            )

        index_path = store.research_episodes_dir / episode_id / "normalized_episode_index.json"
        index = _read_json_dict(index_path)
        if index is None:
            result["missing_normalized_index_episode_ids"].append(episode_id)
            source_ids: set[str] = set()
        else:
            _audit_normalized_index(
                episode_id=episode_id,
                records=episode_records,
                index=index,
                result=result,
            )
            source_ids = _string_set(index.get("source_ids"))
        _audit_provenance_source_closure(
            records=episode_records,
            source_ids=source_ids,
            result=result,
        )

        envelope_path = store.research_episodes_dir / episode_id / "bundle_envelope.json"
        envelope = _read_json_dict(envelope_path)
        if envelope is None:
            result["missing_bundle_envelope_episode_ids"].append(episode_id)
            raw_block_paths: dict[str, str] = {}
            allow_block_only_trace = False
        else:
            raw_block_paths = _string_dict(envelope.get("raw_block_paths"))
            allow_block_only_trace = _is_catalog_only_envelope(envelope)
            _audit_raw_block_hashes(
                root,
                episode_id,
                envelope,
                result,
                skip_catalog_only=allow_block_only_trace,
            )
            _audit_brain_delta_count(
                root=root,
                episode_id=episode_id,
                records=episode_records,
                envelope=envelope,
                result=result,
            )
            _audit_brain_delta_record_ids(
                root=root,
                episode_id=episode_id,
                records=episode_records,
                envelope=envelope,
                result=result,
            )
        _audit_record_source_lines(
            root=root,
            records=episode_records,
            raw_block_paths=raw_block_paths,
            allow_block_only_trace=allow_block_only_trace,
            result=result,
        )
    _append_deep_findings(result)
    return result


def _audit_record_manifest(
    *,
    root: Path,
    episode_id: str,
    records: list[BrainRecordEnvelope],
    manifest: dict[str, Any],
    result: dict[str, Any],
) -> None:
    record_ids = sorted(record.record_id for record in records)
    if manifest.get("record_count") != len(records):
        result["manifest_count_mismatch_episode_ids"].append(episode_id)
    if sorted(_string_list(manifest.get("record_ids"))) != record_ids:
        result["manifest_record_id_mismatch_episode_ids"].append(episode_id)
    if manifest.get("training_eligible_record_count") != sum(
        1 for record in records if record.training_eligible
    ):
        result["manifest_training_eligible_mismatch_episode_ids"].append(episode_id)
    if _int_dict(manifest.get("record_counts_by_type")) != _type_counts(records):
        result["manifest_type_count_mismatch_episode_ids"].append(episode_id)
    records_file = manifest.get("records_file")
    record_path = root / records_file if isinstance(records_file, str) else None
    expected_hash = manifest.get("records_sha256")
    if (
        not isinstance(expected_hash, str)
        or record_path is None
        or not record_path.exists()
        or sha256_text(record_path.read_text(encoding="utf-8")) != expected_hash
    ):
        result["manifest_hash_mismatch_episode_ids"].append(episode_id)


def _audit_normalized_index(
    *,
    episode_id: str,
    records: list[BrainRecordEnvelope],
    index: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if sorted(_string_list(index.get("record_ids"))) != sorted(
        record.record_id for record in records
    ):
        result["index_record_id_mismatch_episode_ids"].append(episode_id)
    if index.get("training_eligible_record_count") != sum(
        1 for record in records if record.training_eligible
    ):
        result["index_training_eligible_mismatch_episode_ids"].append(episode_id)
    if _int_dict(index.get("record_count_by_type")) != _type_counts(records):
        result["index_type_count_mismatch_episode_ids"].append(episode_id)


def _audit_provenance_source_closure(
    *,
    records: list[BrainRecordEnvelope],
    source_ids: set[str],
    result: dict[str, Any],
) -> None:
    for record in records:
        if not record.training_eligible:
            continue
        missing = sorted(set(record.provenance_source_ids) - source_ids)
        if missing:
            result["eligible_records_with_unknown_provenance_sources"].append(
                record.record_id
            )


def _audit_raw_block_hashes(
    root: Path,
    episode_id: str,
    envelope: dict[str, Any],
    result: dict[str, Any],
    *,
    skip_catalog_only: bool,
) -> None:
    if skip_catalog_only:
        return
    raw_block_paths = _string_dict(envelope.get("raw_block_paths"))
    raw_block_hashes = _string_dict(envelope.get("raw_block_hashes"))
    for block_name, expected_hash in raw_block_hashes.items():
        relative_path = raw_block_paths.get(block_name)
        if relative_path is None:
            result["raw_block_hash_mismatch_episode_ids"].append(episode_id)
            return
        path = root / relative_path
        if not path.exists() or sha256_text(path.read_text(encoding="utf-8")) != expected_hash:
            result["raw_block_hash_mismatch_episode_ids"].append(episode_id)
            return


def _audit_brain_delta_count(
    *,
    root: Path,
    episode_id: str,
    records: list[BrainRecordEnvelope],
    envelope: dict[str, Any],
    result: dict[str, Any],
) -> None:
    raw_block_paths = _string_dict(envelope.get("raw_block_paths"))
    brain_delta_path = raw_block_paths.get("brain_delta.jsonl")
    if brain_delta_path is None:
        return
    path = root / brain_delta_path
    if not path.exists():
        result["brain_delta_count_mismatch_episode_ids"].append(episode_id)
        return
    raw_count = len(_nonempty_lines(path.read_text(encoding="utf-8")))
    source_count = sum(
        1 for record in records if record.source_block == "brain_delta.jsonl"
    )
    expected_counts = _int_dict(envelope.get("raw_block_counts"))
    if (
        raw_count != source_count
        or expected_counts.get("brain_delta.jsonl") != raw_count
    ):
        result["brain_delta_count_mismatch_episode_ids"].append(episode_id)


def _audit_brain_delta_record_ids(
    *,
    root: Path,
    episode_id: str,
    records: list[BrainRecordEnvelope],
    envelope: dict[str, Any],
    result: dict[str, Any],
) -> None:
    raw_block_paths = _string_dict(envelope.get("raw_block_paths"))
    brain_delta_path = raw_block_paths.get("brain_delta.jsonl")
    if brain_delta_path is None:
        return
    path = root / brain_delta_path
    if not path.exists():
        return
    raw_ids: list[str] = []
    for line in _nonempty_lines(path.read_text(encoding="utf-8")):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        raw_id = _explicit_brain_record_id(payload)
        if raw_id is None:
            return
        raw_ids.append(raw_id)
    normalized_ids = sorted(
        record.record_id
        for record in records
        if record.source_block == "brain_delta.jsonl"
    )
    if sorted(raw_ids) != normalized_ids:
        result["brain_delta_record_id_mismatch_episode_ids"].append(episode_id)


def _audit_record_source_lines(
    *,
    root: Path,
    records: list[BrainRecordEnvelope],
    raw_block_paths: dict[str, str],
    allow_block_only_trace: bool,
    result: dict[str, Any],
) -> None:
    raw_block_cache: dict[str, list[str]] = {}
    raw_text_cache: dict[str, str] = {}
    for record in records:
        if record.available_from.tzinfo is None:
            result["records_with_naive_available_from"].append(record.record_id)
        relative_path = raw_block_paths.get(record.source_block)
        if relative_path is None:
            result["records_missing_source_block"].append(record.record_id)
            continue
        path = root / relative_path
        if not path.exists():
            result["records_missing_source_block"].append(record.record_id)
            continue
        if record.source_line is None:
            if allow_block_only_trace:
                continue
            raw_text = raw_text_cache.setdefault(
                record.source_block,
                path.read_text(encoding="utf-8"),
            )
            if sha256_text(raw_text) != record.raw_payload_sha256:
                result["records_missing_source_line"].append(record.record_id)
            continue
        if record.source_line <= 0:
            result["records_with_invalid_source_line"].append(record.record_id)
            continue
        lines = raw_block_cache.setdefault(
            record.source_block,
            _nonempty_lines(path.read_text(encoding="utf-8")),
        )
        line_index = record.source_line - 1
        if line_index >= len(lines):
            result["records_with_invalid_source_line"].append(record.record_id)
            continue
        try:
            raw_payload = json.loads(lines[line_index])
        except json.JSONDecodeError:
            result["records_with_invalid_source_line"].append(record.record_id)
            continue
        if not isinstance(raw_payload, dict):
            result["records_with_invalid_source_line"].append(record.record_id)
            continue
        raw_hash = sha256_text(json.dumps(raw_payload, ensure_ascii=False, sort_keys=True))
        if raw_hash != record.raw_payload_sha256:
            result["records_with_raw_payload_hash_mismatch"].append(record.record_id)


def _append_deep_findings(result: dict[str, Any]) -> None:
    finding_labels = {
        "missing_record_manifest_episode_ids": "record manifest is missing",
        "manifest_count_mismatch_episode_ids": "record manifest count does not match records",
        "manifest_record_id_mismatch_episode_ids": "record manifest IDs do not match records",
        "manifest_training_eligible_mismatch_episode_ids": (
            "record manifest training eligible count does not match records"
        ),
        "manifest_type_count_mismatch_episode_ids": (
            "record manifest type counts do not match records"
        ),
        "manifest_hash_mismatch_episode_ids": "record manifest records_sha256 mismatch",
        "missing_normalized_index_episode_ids": "normalized episode index is missing",
        "index_record_id_mismatch_episode_ids": "normalized episode index IDs do not match records",
        "index_training_eligible_mismatch_episode_ids": (
            "normalized episode index training eligible count does not match records"
        ),
        "index_type_count_mismatch_episode_ids": (
            "normalized episode index type counts do not match records"
        ),
        "missing_bundle_envelope_episode_ids": "bundle envelope is missing",
        "raw_block_hash_mismatch_episode_ids": "raw block hashes do not match bundle envelope",
        "brain_delta_count_mismatch_episode_ids": (
            "brain_delta raw count does not match normalized records"
        ),
        "brain_delta_record_id_mismatch_episode_ids": (
            "brain_delta raw record IDs do not match normalized records"
        ),
        "records_missing_source_block": "records reference missing source blocks",
        "records_missing_source_line": "records are missing source_line traceability",
        "records_with_invalid_source_line": "records have invalid source_line references",
        "records_with_raw_payload_hash_mismatch": "record raw payload hashes do not match source lines",
        "eligible_records_with_unknown_provenance_sources": (
            "eligible record provenance_source_ids are not closed by source ledger"
        ),
        "records_with_naive_available_from": "record available_from values are timezone-naive",
    }
    findings = result["findings"]
    for key, label in finding_labels.items():
        if result[key]:
            findings.append(label)


def _record_stats(records: list[BrainRecordEnvelope]) -> dict[str, Any]:
    return {
        "record_count": len(records),
        "episode_count": len({record.episode_id for record in records}),
        "training_eligible_record_count": sum(
            1 for record in records if record.training_eligible
        ),
        "record_counts_by_type": dict(
            sorted(Counter(record.record_type for record in records).items())
        ),
        "record_counts_by_evidence_phase": dict(
            sorted(Counter(record.evidence_phase for record in records).items())
        ),
        "record_counts_by_training_target": dict(
            sorted(Counter(record.training_target or "UNKNOWN" for record in records).items())
        ),
    }


def _records_by_episode(
    records: list[BrainRecordEnvelope],
) -> dict[str, list[BrainRecordEnvelope]]:
    grouped: dict[str, list[BrainRecordEnvelope]] = {}
    for record in records:
        grouped.setdefault(record.episode_id, []).append(record)
    return {
        episode_id: sorted(items, key=lambda record: record.record_id)
        for episode_id, items in grouped.items()
    }


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = read_json(path)
    return payload if isinstance(payload, dict) else None


def _type_counts(records: list[BrainRecordEnvelope]) -> dict[str, int]:
    return dict(sorted(Counter(record.record_type for record in records).items()))


def _explicit_brain_record_id(payload: dict[str, Any]) -> str | None:
    for field_name in (
        "record_id",
        "issuer_day_case_id",
        "case_id",
        "blind_pair_id",
        "claim_id",
        "mechanism_id",
        "counterexample_id",
        "edge_id",
        "question_id",
        "error_id",
    ):
        value = payload.get(field_name)
        if isinstance(value, str) and value:
            return value
    return None


def _int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_set(value: object) -> set[str]:
    return set(_string_list(value))


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _is_catalog_only_envelope(envelope: dict[str, Any]) -> bool:
    import_status = envelope.get("import_status")
    bundle_status = envelope.get("bundle_status")
    return import_status == "catalog_only" or bundle_status == "LEGACY_ACCEPTED"


def _nonempty_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _record_id_index(records: list[BrainRecordEnvelope]) -> dict[str, dict[str, str]]:
    return {
        record.record_id: {
            "episode_id": record.episode_id,
            "record_type": record.record_type,
            "trade_date": record.trade_date.isoformat(),
            "available_from": record.available_from.isoformat(),
            "training_eligible": str(record.training_eligible).lower(),
            "normalized_payload_sha256": record.normalized_payload_sha256,
        }
        for record in records
    }


def _safe_block_filename(name: str) -> str:
    return name.replace("/", "__").replace("\\", "__")
