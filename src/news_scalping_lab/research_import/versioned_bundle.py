"""Version-aware research bundle import.

This importer preserves newer bundle records without forcing them into the
legacy ``ResearchEpisode`` schema.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Protocol

from news_scalping_lab.diagnostic_reports import write_diagnostic_report
from news_scalping_lab.records.models import (
    KNOWN_RECORD_PAYLOAD_MODELS,
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.records.store import BrainRecordStore, StoredBundleResult
from news_scalping_lab.utils import KST, canonical_json, file_sha256, parse_datetime, sha256_text, stable_id


class VersionedBundleImportError(ValueError):
    """Raised when a versioned bundle cannot be normalized safely."""


@dataclass(frozen=True)
class GenericParsedBundle:
    path: Path
    text: str
    front_matter: dict[str, str]
    blocks: dict[str, str]
    payload_blocks: dict[str, str]
    json_blocks: dict[str, Any]
    jsonl_blocks: dict[str, list[dict[str, Any]]]


@dataclass(frozen=True)
class BundleImportResult:
    status: str
    adapter_name: str
    episode_id: str
    bundle_schema_version: str
    accepted: bool
    record_count: int
    training_eligible_record_count: int
    envelope_path: Path | None
    record_path: Path | None
    manifest_path: Path | None
    validation: dict[str, Any]


class BundleVersionAdapter(Protocol):
    name: str

    def supports(self, parsed: GenericParsedBundle) -> bool: ...

    def validate(self, parsed: GenericParsedBundle) -> dict[str, Any]: ...

    def normalize_episode_index(self, parsed: GenericParsedBundle) -> NormalizedEpisodeIndex: ...

    def normalize_brain_records(self, parsed: GenericParsedBundle) -> list[BrainRecordEnvelope]: ...

    def envelope(self, parsed: GenericParsedBundle) -> ResearchBundleEnvelope: ...


class BaseBundleAdapter:
    name = "base"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        return False

    def validate(self, parsed: GenericParsedBundle) -> dict[str, Any]:
        manifest = _manifest(parsed)
        validation_report = _validation_report(parsed)
        block_hashes = _block_hashes(parsed)
        hash_mismatches = _hash_mismatches(parsed, manifest, validation_report)
        records = parsed.jsonl_blocks.get("brain_delta.jsonl", [])
        source_ids = _source_ids(parsed)
        missing_source_refs = _missing_source_references(records, source_ids)
        expected_record_count = _int_field(
            manifest,
            "brain_delta_record_count",
            _nested_int(validation_report, "computed_counts", "brain_delta_record_count"),
        )
        expected_training_count = _int_field(
            manifest,
            "training_eligible_record_count",
            _nested_int(
                validation_report,
                "computed_counts",
                "training_eligible_record_count",
            ),
        )
        actual_training_count = sum(
            1 for record in records if record.get("training_eligible") is True
        )
        validation = {
            "schema_version": "nslab.versioned_bundle_validation.v1",
            "adapter": self.name,
            "bundle_schema_version": bundle_schema_version(parsed),
            "manifest_schema_version": _optional_string(manifest.get("schema_version")),
            "record_count": len(records),
            "training_eligible_record_count": actual_training_count,
            "expected_record_count": expected_record_count,
            "expected_training_eligible_record_count": expected_training_count,
            "record_count_matches_manifest": (
                expected_record_count is None or expected_record_count == len(records)
            ),
            "training_eligible_count_matches_manifest": (
                expected_training_count is None
                or expected_training_count == actual_training_count
            ),
            "block_hashes": block_hashes,
            "hash_mismatches": hash_mismatches,
            "source_reference_count": sum(
                len(_string_list(record.get("provenance_source_ids")))
                for record in records
            ),
            "missing_source_references": missing_source_refs,
            "provenance_closure_status": "closed" if not missing_source_refs else "missing_refs",
            "validator_exit_code": _int_field(manifest, "validator_exit_code"),
            "critical_error_count": _int_field(
                manifest,
                "critical_error_count",
                _nested_int(validation_report, "critical_error_count"),
            ),
        }
        validation["passed"] = (
            validation["record_count_matches_manifest"] is True
            and validation["training_eligible_count_matches_manifest"] is True
            and not hash_mismatches
            and not missing_source_refs
        )
        return validation

    def normalize_episode_index(self, parsed: GenericParsedBundle) -> NormalizedEpisodeIndex:
        manifest = _manifest(parsed)
        episode = _episode(parsed)
        records = self.normalize_brain_records(parsed)
        source_ids = sorted(_source_ids(parsed))
        available_from = _available_from(parsed, records)
        return NormalizedEpisodeIndex(
            episode_id=_episode_id(parsed),
            trade_date=_trade_date(parsed),
            previous_trade_date=_optional_date(_field(parsed, "previous_trade_date")),
            next_trade_date=_optional_date(_field(parsed, "next_trade_date")),
            window_start=_optional_datetime(_field(parsed, "window_start")),
            cutoff_at=_optional_datetime(_field(parsed, "cutoff_at")),
            available_from=available_from,
            bundle_status=_optional_string(manifest.get("bundle_status") or _field(parsed, "bundle_status")),
            blind_valid=_optional_bool(manifest.get("blind_valid") or _field(parsed, "blind_valid")),
            research_daily_source=_optional_string(manifest.get("research_daily_source")),
            entity_quality_summary=_dict_field(episode, "entity_quality_summary"),
            fact_quality_summary=_dict_field(episode, "fact_quality_summary"),
            candidate_screening_summary=_dict_field(episode, "candidate_screening_summary"),
            entity_resolution_summary=_dict_field(episode, "entity_resolution_summary"),
            winner_census=_dict_field(episode, "winner_census"),
            raw_block_names=sorted(parsed.blocks),
            record_ids=[record.record_id for record in records],
            record_count_by_type=dict(Counter(record.record_type for record in records)),
            training_eligible_record_count=sum(
                1 for record in records if record.training_eligible
            ),
            source_ids=source_ids,
        )

    def normalize_brain_records(self, parsed: GenericParsedBundle) -> list[BrainRecordEnvelope]:
        episode_id = _episode_id(parsed)
        trade_day = _trade_date(parsed)
        default_available_from = _default_available_from(parsed)
        records: list[BrainRecordEnvelope] = []
        for line_number, payload in enumerate(
            parsed.jsonl_blocks.get("brain_delta.jsonl", []),
            start=1,
        ):
            records.append(
                _record_envelope(
                    payload=payload,
                    episode_id=episode_id,
                    trade_date=trade_day,
                    default_available_from=default_available_from,
                    source_line=line_number,
                )
            )
        return records

    def envelope(self, parsed: GenericParsedBundle) -> ResearchBundleEnvelope:
        manifest = _manifest(parsed)
        records = self.normalize_brain_records(parsed)
        validation = self.validate(parsed)
        return ResearchBundleEnvelope(
            bundle_schema_version=bundle_schema_version(parsed),
            manifest_schema_version=_optional_string(manifest.get("schema_version")),
            episode_schema_version=_optional_string(_episode(parsed).get("schema_version")),
            episode_id=_episode_id(parsed),
            trade_date=_trade_date(parsed),
            cutoff_at=_optional_datetime(_field(parsed, "cutoff_at")),
            available_from=_available_from(parsed, records),
            bundle_status=_optional_string(manifest.get("bundle_status") or _field(parsed, "bundle_status")),
            blind_valid=_optional_bool(manifest.get("blind_valid") or _field(parsed, "blind_valid")),
            raw_bundle_sha256=file_sha256(parsed.path),
            raw_block_hashes=_block_hashes(parsed),
            raw_block_counts=_block_counts(parsed),
            provenance_closure_status=str(validation["provenance_closure_status"]),
            adapter_name=self.name,
        )


class LegacyV1Adapter(BaseBundleAdapter):
    name = "legacy-v1"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        return (
            bundle_schema_version(parsed) in {"nslab.bundle_manifest.v1", "nslab.research_bundle.v1"}
            or _optional_string(_manifest(parsed).get("schema_version"))
            == "nslab.bundle_manifest.v1"
            or _optional_string(_episode(parsed).get("schema_version"))
            == "nslab.research_episode.v1"
        )


class V10Adapter(BaseBundleAdapter):
    name = "v10"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        version = bundle_schema_version(parsed)
        if _declares_research_bundle_version(version) and not version.endswith(".v10"):
            return False
        manifest_version = _optional_string(_manifest(parsed).get("schema_version"))
        episode_version = _optional_string(_episode(parsed).get("schema_version"))
        versions = {
            version.split(".")[-1],
            (manifest_version or "").split(".")[-1],
            (episode_version or "").split(".")[-1],
        }
        return "v10" in versions


class V11Adapter(BaseBundleAdapter):
    name = "v11"

    def supports(self, parsed: GenericParsedBundle) -> bool:
        version = bundle_schema_version(parsed)
        if _declares_research_bundle_version(version) and not version.endswith(".v11"):
            return False
        manifest_version = _optional_string(_manifest(parsed).get("schema_version"))
        episode_version = _optional_string(_episode(parsed).get("schema_version"))
        versions = {
            version.split(".")[-1],
            (manifest_version or "").split(".")[-1],
            (episode_version or "").split(".")[-1],
        }
        return "v11" in versions

    def validate(self, parsed: GenericParsedBundle) -> dict[str, Any]:
        validation = super().validate(parsed)
        manifest = _manifest(parsed)
        validation.update(
            {
                "bundle_status_accept_full": manifest.get("bundle_status") == "ACCEPT_FULL",
                "blind_valid": manifest.get("blind_valid") is True,
                "validator_exit_code_zero": manifest.get("validator_exit_code") == 0,
                "critical_error_count_zero": _int_field(
                    manifest,
                    "critical_error_count",
                    _nested_int(_validation_report(parsed), "critical_error_count"),
                )
                == 0,
            }
        )
        validation["passed"] = (
            validation["passed"] is True
            and validation["bundle_status_accept_full"] is True
            and validation["blind_valid"] is True
            and validation["validator_exit_code_zero"] is True
            and validation["critical_error_count_zero"] is True
        )
        return validation


ADAPTERS: tuple[BundleVersionAdapter, ...] = (
    V11Adapter(),
    V10Adapter(),
    LegacyV1Adapter(),
)


def parse_generic_bundle(path: Path) -> GenericParsedBundle:
    text = path.read_text(encoding="utf-8", errors="replace")
    front_matter = _extract_front_matter(text)
    blocks = _extract_blocks(text)
    payload_blocks: dict[str, str] = {}
    json_blocks: dict[str, Any] = {}
    jsonl_blocks: dict[str, list[dict[str, Any]]] = {}
    for name, block in blocks.items():
        payload = _strip_optional_fence(block)
        payload_blocks[name] = payload
        if name.endswith(".json"):
            json_blocks[name] = _parse_json(name, payload)
        elif name.endswith(".jsonl"):
            jsonl_blocks[name] = _parse_jsonl(name, payload)
    return GenericParsedBundle(
        path=path,
        text=text,
        front_matter=front_matter,
        blocks=blocks,
        payload_blocks=payload_blocks,
        json_blocks=json_blocks,
        jsonl_blocks=jsonl_blocks,
    )


def inspect_versioned_bundle(path: Path) -> dict[str, Any]:
    parsed = parse_generic_bundle(path)
    adapter = select_adapter(parsed)
    records = (
        adapter.normalize_brain_records(parsed)
        if adapter is not None
        else []
    )
    validation = adapter.validate(parsed) if adapter is not None else {}
    return {
        "path": path.as_posix(),
        "bundle_schema_version": bundle_schema_version(parsed),
        "manifest_schema_version": _optional_string(_manifest(parsed).get("schema_version")),
        "episode_schema_version": _optional_string(_episode(parsed).get("schema_version")),
        "adapter": adapter.name if adapter is not None else None,
        "supported": adapter is not None,
        "episode_id": _field(parsed, "episode_id"),
        "trade_date": _field(parsed, "trade_date"),
        "block_count": len(parsed.blocks),
        "blocks": sorted(parsed.blocks),
        "record_count": len(records),
        "training_eligible_record_count": sum(
            1 for record in records if record.training_eligible
        ),
        "record_counts_by_type": dict(
            sorted(Counter(record.record_type for record in records).items())
        ),
        "validation": validation,
    }


def import_versioned_bundle(
    path: Path,
    *,
    root: Path,
    validate: bool = True,
    accepted: bool = True,
) -> BundleImportResult:
    parsed = parse_generic_bundle(path)
    adapter = select_adapter(parsed)
    source_hash = file_sha256(path)
    if adapter is None:
        episode_id = _optional_string(_field(parsed, "episode_id")) or stable_id(
            "UNSUPPORTED",
            source_hash,
        )
        quarantine = BrainRecordStore(root).quarantine_conflict(
            source_path=path,
            reason="UNSUPPORTED_BUNDLE_VERSION",
            episode_id=episode_id,
            source_hash=source_hash,
            metadata={
                "bundle_schema_version": bundle_schema_version(parsed),
                "manifest_schema_version": _optional_string(_manifest(parsed).get("schema_version")),
            },
        )
        write_diagnostic_report(
            root,
            "bundle_import_report",
            {
                "status": "UNSUPPORTED_BUNDLE_VERSION",
                "episode_id": episode_id,
                "bundle_version": bundle_schema_version(parsed),
                "dropped_record_count": 0,
                "quarantined_record_count": 1,
                "quarantine": quarantine.as_posix(),
            },
        )
        return BundleImportResult(
            status="UNSUPPORTED_BUNDLE_VERSION",
            adapter_name="unsupported",
            episode_id=episode_id,
            bundle_schema_version=bundle_schema_version(parsed),
            accepted=False,
            record_count=0,
            training_eligible_record_count=0,
            envelope_path=quarantine / "quarantine.json",
            record_path=None,
            manifest_path=None,
            validation={"passed": False, "quarantine": quarantine.as_posix()},
        )
    validation = adapter.validate(parsed)
    records = adapter.normalize_brain_records(parsed)
    if validation.get("passed") is not True and (validate or accepted):
        quarantine = _quarantine_validation_failure(
            root=root,
            path=path,
            parsed=parsed,
            adapter_name=adapter.name,
            source_hash=source_hash,
            validation=validation,
            records=records,
        )
        raise VersionedBundleImportError(
            "bundle validation failed; "
            f"quarantined at {quarantine.as_posix()}: "
            + json.dumps(validation, ensure_ascii=False, sort_keys=True)
        )
    stored: StoredBundleResult = BrainRecordStore(root).store_bundle(
        source_path=path,
        envelope=adapter.envelope(parsed),
        index=adapter.normalize_episode_index(parsed),
        records=records,
        raw_blocks=parsed.payload_blocks,
        validation_report=validation,
        accepted=accepted,
    )
    diagnostics_payload = {
        "status": "imported",
        "adapter": adapter.name,
        "bundle_version": bundle_schema_version(parsed),
        "episode_id": _episode_id(parsed),
        "accepted": accepted,
        "acceptance_status": "accepted" if accepted else "staged",
        "raw_record_count": len(parsed.jsonl_blocks.get("brain_delta.jsonl", [])),
        "normalized_record_count": stored.record_count,
        "training_eligible_record_count": stored.training_eligible_record_count,
        "dropped_record_count": 0,
        "quarantined_record_count": 0,
        "record_counts_by_type": dict(
            sorted(Counter(record.record_type for record in records).items())
        ),
        "validation": validation,
    }
    write_diagnostic_report(root, "bundle_import_report", diagnostics_payload)
    return BundleImportResult(
        status="imported",
        adapter_name=adapter.name,
        episode_id=_episode_id(parsed),
        bundle_schema_version=bundle_schema_version(parsed),
        accepted=accepted,
        record_count=stored.record_count,
        training_eligible_record_count=stored.training_eligible_record_count,
        envelope_path=stored.envelope_path,
        record_path=stored.record_path,
        manifest_path=stored.manifest_path,
        validation=validation,
    )


def _quarantine_validation_failure(
    *,
    root: Path,
    path: Path,
    parsed: GenericParsedBundle,
    adapter_name: str,
    source_hash: str,
    validation: dict[str, Any],
    records: list[BrainRecordEnvelope],
) -> Path:
    episode_id = _episode_id(parsed)
    quarantine = BrainRecordStore(root).quarantine_conflict(
        source_path=path,
        reason="BUNDLE_VALIDATION_FAILED",
        episode_id=episode_id,
        source_hash=source_hash,
        metadata={
            "adapter": adapter_name,
            "bundle_schema_version": bundle_schema_version(parsed),
            "validation": validation,
        },
    )
    write_diagnostic_report(
        root,
        "bundle_import_report",
        {
            "status": "BUNDLE_VALIDATION_FAILED",
            "adapter": adapter_name,
            "bundle_version": bundle_schema_version(parsed),
            "episode_id": episode_id,
            "raw_record_count": len(parsed.jsonl_blocks.get("brain_delta.jsonl", [])),
            "normalized_record_count": len(records),
            "training_eligible_record_count": sum(
                1 for record in records if record.training_eligible
            ),
            "dropped_record_count": 0,
            "quarantined_record_count": 1,
            "quarantine": quarantine.as_posix(),
            "validation": validation,
        },
    )
    return quarantine


def select_adapter(parsed: GenericParsedBundle) -> BundleVersionAdapter | None:
    for adapter in ADAPTERS:
        if adapter.supports(parsed):
            return adapter
    return None


def bundle_schema_version(parsed: GenericParsedBundle) -> str:
    value = parsed.front_matter.get("schema_version")
    if value:
        return value
    manifest_version = _optional_string(_manifest(parsed).get("schema_version"))
    if manifest_version:
        return manifest_version
    episode_version = _optional_string(_episode(parsed).get("schema_version"))
    return episode_version or "UNKNOWN_BUNDLE_VERSION"


def _declares_research_bundle_version(version: str) -> bool:
    return version.startswith("nslab.research_bundle.")


def _record_envelope(
    *,
    payload: dict[str, Any],
    episode_id: str,
    trade_date: date,
    default_available_from: datetime,
    source_line: int,
) -> BrainRecordEnvelope:
    record_type = str(payload.get("record_type") or "unknown")
    payload_model = KNOWN_RECORD_PAYLOAD_MODELS.get(record_type)
    typed_payload_status = (
        "KNOWN_TYPED_PAYLOAD" if payload_model is not None else "UNKNOWN_TYPED_PAYLOAD"
    )
    normalized_payload = dict(payload)
    training_eligible = bool(payload.get("training_eligible") is True)
    eligibility_reason = _optional_string(payload.get("eligibility_reason"))
    if payload_model is not None:
        payload_model.model_validate(payload)
    else:
        training_eligible = False
        eligibility_reason = (
            (eligibility_reason + "; " if eligibility_reason else "")
            + "unknown record_type preserved as raw payload"
        )
        normalized_payload["training_eligible"] = False
    raw_payload_sha = sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    normalized_payload_sha = sha256_text(canonical_json(normalized_payload))
    record_id = _record_id(payload, episode_id, record_type, normalized_payload_sha)
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id=_optional_string(payload.get("episode_id")) or episode_id,
        trade_date=_optional_date(payload.get("trade_date")) or trade_date,
        available_from=_optional_datetime(payload.get("available_from"))
        or default_available_from,
        training_target=_optional_string(payload.get("training_target")),
        evidence_phase=_evidence_phase(record_type, payload),
        training_eligible=training_eligible,
        eligibility_reason=eligibility_reason,
        status=_optional_string(payload.get("status")) or "tentative",
        confidence_label=_optional_string(payload.get("confidence_label")) or "low",
        provenance_source_ids=_string_list(payload.get("provenance_source_ids")),
        raw_payload_sha256=raw_payload_sha,
        normalized_payload_sha256=normalized_payload_sha,
        typed_payload_status=typed_payload_status,
        source_line=source_line,
        payload=normalized_payload,
    )


def _record_id(
    payload: dict[str, Any],
    episode_id: str,
    record_type: str,
    normalized_payload_sha: str,
) -> str:
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
    return stable_id("BRAIN", episode_id, record_type, normalized_payload_sha)


def _evidence_phase(record_type: str, payload: dict[str, Any]) -> str:
    value = _optional_string(payload.get("evidence_phase"))
    if value is not None:
        return value
    if record_type in {
        "memory_claim",
        "mechanism_memory",
        "counterexample",
        "research_question",
    }:
        return "AUDIT"
    if record_type == "beneficiary_discovery_case":
        return "BLIND"
    return "POSTMORTEM"


def _extract_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("<!-- NSLAB:BEGIN ") and stripped.endswith(" -->"):
            name = stripped.removeprefix("<!-- NSLAB:BEGIN ").removesuffix(" -->").strip()
            if name in blocks:
                raise VersionedBundleImportError(f"duplicate BEGIN block: {name}")
            end_marker = f"<!-- NSLAB:END {name} -->"
            index += 1
            content: list[str] = []
            while index < len(lines) and lines[index].strip() != end_marker:
                content.append(lines[index])
                index += 1
            if index >= len(lines):
                raise VersionedBundleImportError(f"missing END marker for block: {name}")
            blocks[name] = "\n".join(content).strip()
        index += 1
    return blocks


def _extract_front_matter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    front_matter: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return front_matter
        if not stripped:
            continue
        key, separator, value = stripped.partition(":")
        if separator and key.strip():
            front_matter[key.strip()] = value.strip()
    return front_matter


def _strip_optional_fence(block: str) -> str:
    lines = block.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return block.strip()


def _parse_json(name: str, payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise VersionedBundleImportError(f"{name} is not valid JSON: {exc}") from exc


def _parse_jsonl(name: str, payload: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VersionedBundleImportError(
                f"{name}:{line_number} is not valid JSONL: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise VersionedBundleImportError(f"{name}:{line_number} must be a JSON object")
        rows.append(parsed)
    return rows


def _manifest(parsed: GenericParsedBundle) -> dict[str, Any]:
    value = parsed.json_blocks.get("bundle_manifest.json")
    return value if isinstance(value, dict) else {}


def _validation_report(parsed: GenericParsedBundle) -> dict[str, Any]:
    value = parsed.json_blocks.get("validation_report.json")
    return value if isinstance(value, dict) else {}


def _episode(parsed: GenericParsedBundle) -> dict[str, Any]:
    value = parsed.json_blocks.get("research_episode.json")
    return value if isinstance(value, dict) else {}


def _field(parsed: GenericParsedBundle, key: str) -> object:
    if key in parsed.front_matter:
        return parsed.front_matter[key]
    manifest = _manifest(parsed)
    if key in manifest:
        return manifest[key]
    episode = _episode(parsed)
    return episode.get(key)


def _episode_id(parsed: GenericParsedBundle) -> str:
    value = _optional_string(_field(parsed, "episode_id"))
    if value is None:
        raise VersionedBundleImportError("bundle is missing episode_id")
    return value


def _trade_date(parsed: GenericParsedBundle) -> date:
    value = _optional_date(_field(parsed, "trade_date"))
    if value is None:
        raise VersionedBundleImportError("bundle is missing trade_date")
    return value


def _available_from(
    parsed: GenericParsedBundle,
    records: list[BrainRecordEnvelope],
) -> datetime:
    explicit = _optional_datetime(_field(parsed, "available_from"))
    if explicit is not None:
        return explicit
    if records:
        return min(record.available_from for record in records)
    return _default_available_from(parsed)


def _default_available_from(parsed: GenericParsedBundle) -> datetime:
    next_day = _optional_date(_field(parsed, "next_trade_date"))
    if next_day is not None:
        return datetime.combine(next_day, time(0, 0, 0), tzinfo=KST)
    trade_day = _trade_date(parsed)
    return datetime.combine(date.fromordinal(trade_day.toordinal() + 1), time(0, 0, 0), tzinfo=KST)


def _optional_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    if len(value) == 10:
        parsed_date = _optional_date(value)
        if parsed_date is None:
            return None
        return datetime.combine(parsed_date, time(0, 0, 0), tzinfo=KST)
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _int_field(*sources: object) -> int | None:
    index = 0
    while index < len(sources):
        source = sources[index]
        if (
            isinstance(source, dict)
            and index + 1 < len(sources)
            and isinstance(sources[index + 1], str)
        ):
            value = source.get(sources[index + 1])
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            index += 2
            continue
        if isinstance(source, int) and not isinstance(source, bool):
            return source
        index += 1
    return None


def _nested_int(source: dict[str, Any], *keys: str) -> int | None:
    current: object = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, int) and not isinstance(current, bool) else None


def _dict_field(source: dict[str, Any], key: str) -> dict[str, Any]:
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _source_ids(parsed: GenericParsedBundle) -> set[str]:
    source_ids: set[str] = set()
    for row in parsed.jsonl_blocks.get("source_ledger.jsonl", []):
        source_id = row.get("source_id")
        if isinstance(source_id, str) and source_id:
            source_ids.add(source_id)
    return source_ids


def _missing_source_references(
    records: list[dict[str, Any]],
    source_ids: set[str],
) -> list[str]:
    missing: set[str] = set()
    for record in records:
        for source_id in _string_list(record.get("provenance_source_ids")):
            if source_id not in source_ids:
                missing.add(source_id)
    return sorted(missing)


def _block_hashes(parsed: GenericParsedBundle) -> dict[str, str]:
    return {
        name: sha256_text(payload)
        for name, payload in sorted(parsed.payload_blocks.items())
    }


def _block_counts(parsed: GenericParsedBundle) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, rows in parsed.jsonl_blocks.items():
        counts[name] = len(rows)
    for name, payload in parsed.json_blocks.items():
        counts[name] = len(payload) if isinstance(payload, list | dict) else 1
    return counts


def _hash_mismatches(
    parsed: GenericParsedBundle,
    manifest: dict[str, Any],
    validation_report: dict[str, Any],
) -> dict[str, dict[str, str]]:
    expected_hashes: dict[str, str] = {}
    embedded = manifest.get("embedded_blocks")
    if isinstance(embedded, dict):
        for block_name, block_meta in embedded.items():
            if not isinstance(block_name, str) or not isinstance(block_meta, dict):
                continue
            sha = block_meta.get("sha256")
            if isinstance(sha, str) and sha:
                expected_hashes[block_name] = sha
    checked_hashes = validation_report.get("checked_artifact_hashes")
    if isinstance(checked_hashes, dict):
        for key, value in checked_hashes.items():
            if isinstance(key, str) and isinstance(value, str) and key in parsed.payload_blocks:
                expected_hashes.setdefault(key, value)
    legacy_fields = {
        "blind_prediction.json": "prediction_sha256",
        "research_report.md": "research_report_sha256",
        "research_episode.json": "research_episode_sha256",
        "row_disposition.jsonl": "row_disposition_sha256",
        "brain_delta.jsonl": "brain_delta_sha256",
        "source_ledger.jsonl": "source_ledger_sha256",
        "phase_state.json": "phase_state_sha256",
    }
    for block_name, field_name in legacy_fields.items():
        value = manifest.get(field_name)
        if isinstance(value, str) and value:
            expected_hashes.setdefault(block_name, value)
    actual_hashes = _block_hashes(parsed)
    return {
        block_name: {
            "expected": expected,
            "actual": actual_hashes.get(block_name, ""),
        }
        for block_name, expected in sorted(expected_hashes.items())
        if block_name in actual_hashes and actual_hashes[block_name] != expected
    }
