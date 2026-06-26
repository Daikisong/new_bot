"""Immutable file-backed brain record store."""

from __future__ import annotations

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
    findings = []
    if duplicate_ids:
        findings.append("record_id values are not globally unique")
    if unknown_training_enabled:
        findings.append("unknown record types are marked training_eligible")
    if missing_payload_hashes:
        findings.append("record payload hashes do not match normalized payloads")
    if deep and missing_provenance:
        findings.append("eligible records are missing provenance_source_ids")
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
        "findings": findings,
        "stats": _record_stats(records),
    }


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
