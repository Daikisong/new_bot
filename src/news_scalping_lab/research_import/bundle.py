"""Parser for single-file NSLAB Markdown research bundles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from news_scalping_lab.context.final_synthesis import (
    FINAL_SYNTHESIS_REQUIRED_INPUTS,
    final_synthesis_input_summary,
)
from news_scalping_lab.contracts.models import Provenance, ResearchEpisode
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    is_available_as_of,
    now_kst,
    parse_datetime,
    sha256_text,
    stable_id,
)

REQUIRED_BUNDLE_BLOCKS = {
    "research_report.md",
    "blind_prediction.json",
    "research_episode.json",
    "row_disposition.jsonl",
    "brain_delta.jsonl",
    "source_ledger.jsonl",
    "phase_state.json",
    "bundle_manifest.json",
}

SOURCE_LEDGER_REQUIRED_FIELDS = {
    "source_id",
    "source_type",
    "title",
    "publisher",
    "url",
    "source_url",
    "published_at",
    "retrieved_at",
    "time_verified",
    "available_before_cutoff",
    "usage_phase",
    "input_row_ids",
    "content_sha256",
    "notes",
}
SOURCE_LEDGER_USAGE_PHASES = {"BLIND", "OUTCOME", "POSTMORTEM"}
CANDIDATE_WEB_CHECK_REQUIRED_FIELDS = {
    "schema_version",
    "run_id",
    "candidate_rank",
    "candidate_ticker",
    "candidate_company_name",
    "candidate_path_type",
    "source_id",
    "query",
    "title",
    "url",
    "source_url",
    "published_at",
    "retrieved_at",
    "cutoff_at",
    "time_verified",
    "available_before_cutoff",
    "content_sha256",
}
EXCLUDED_CANDIDATE_WEB_CHECK_REQUIRED_FIELDS = {
    "schema_version",
    "run_id",
    "candidate_rank",
    "candidate_ticker",
    "candidate_company_name",
    "candidate_path_type",
    "source_id",
    "query",
    "title",
    "url",
    "source_url",
    "published_at",
    "retrieved_at",
    "cutoff_at",
    "exclusion_reason",
}
LEGACY_OPTIONAL_VALIDATION_KEYS = {
    "final_synthesis_context_candidate_web_checks_verified",
    "final_synthesis_context_candidate_verification_verified",
}


class BundleImportError(ValueError):
    """Raised when a Markdown bundle is present but structurally invalid."""


@dataclass(frozen=True)
class BundleParseResult:
    blocks: dict[str, str]
    json_blocks: dict[str, Any]
    jsonl_blocks: dict[str, list[dict[str, Any]]]
    validation: dict[str, bool]


def looks_like_bundle(path: Path) -> bool:
    if path.suffix.lower() not in {".md", ".markdown"}:
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return "<!-- NSLAB:BEGIN " in text and "<!-- NSLAB:END " in text


def import_bundle_episode(path: Path) -> ResearchEpisode:
    parsed = parse_bundle(path)
    if not parsed.validation["blind_hash_verified"]:
        raise BundleImportError(
            "blind_prediction.json hash does not match bundle_manifest.json"
        )
    if not parsed.validation["blind_execution_guard_verified"]:
        raise BundleImportError("bundle blind execution guard check failed")
    if not parsed.validation["row_disposition_hash_verified"]:
        raise BundleImportError(
            "row_disposition.jsonl hash does not match bundle_manifest.json"
        )
    if not parsed.validation["row_disposition_coverage_verified"]:
        raise BundleImportError("row_disposition coverage ratio must be 1.0")
    if not parsed.validation["source_ledger_hash_verified"]:
        raise BundleImportError("source_ledger.jsonl hash does not match bundle_manifest.json")
    if not parsed.validation["source_ledger_entry_count_verified"]:
        raise BundleImportError(
            "source_ledger.jsonl entry count does not match bundle_manifest.json"
        )
    if not parsed.validation.get("candidate_web_check_hash_verified", True):
        raise BundleImportError(
            "candidate_web_checks.jsonl hash does not match bundle_manifest.json"
        )
    if not parsed.validation.get("candidate_web_check_count_verified", True):
        raise BundleImportError(
            "candidate_web_checks.jsonl entry count does not match bundle_manifest.json"
        )
    if not parsed.validation.get("candidate_verification_hash_verified", True):
        raise BundleImportError(
            "candidate_verification.json hash does not match bundle_manifest.json"
        )
    if not parsed.validation.get("candidate_verification_count_verified", True):
        raise BundleImportError(
            "candidate_verification.json finding count does not match bundle_manifest.json"
        )
    if not parsed.validation.get("candidate_verification_contract_verified", True):
        raise BundleImportError(
            "candidate_verification.json content does not match candidate web checks"
        )
    if not parsed.validation.get("final_synthesis_context_hash_verified", True):
        raise BundleImportError(
            "final_synthesis_context.json hash does not match bundle_manifest.json"
        )
    if not parsed.validation.get("final_synthesis_context_contract_verified", True):
        raise BundleImportError(
            "final_synthesis_context.json content does not match bundle_manifest.json"
        )
    if not parsed.validation.get(
        "final_synthesis_context_candidate_web_checks_verified",
        True,
    ):
        raise BundleImportError(
            "final_synthesis_context.json candidate_web_checks do not match "
            "candidate_web_checks.jsonl"
        )
    if not parsed.validation.get(
        "final_synthesis_context_candidate_verification_verified",
        True,
    ):
        raise BundleImportError(
            "final_synthesis_context.json candidate_verification does not match "
            "candidate_verification.json"
        )
    if not parsed.validation.get("excluded_candidate_web_check_hash_verified", True):
        raise BundleImportError(
            "excluded_candidate_web_checks.jsonl hash does not match bundle_manifest.json"
        )
    if not parsed.validation.get("excluded_candidate_web_check_count_verified", True):
        raise BundleImportError(
            "excluded_candidate_web_checks.jsonl entry count does not match "
            "bundle_manifest.json"
        )
    if not parsed.validation["research_episode_hash_verified"]:
        raise BundleImportError(
            "research_episode.json hash does not match bundle_manifest.json"
        )
    if not parsed.validation["brain_delta_hash_verified"]:
        raise BundleImportError("brain_delta.jsonl hash does not match bundle_manifest.json")
    if not parsed.validation["blind_seal_receipt_hash_verified"]:
        raise BundleImportError(
            "blind_seal_receipt hash does not match bundle_manifest.json"
        )
    if not parsed.validation["blind_seal_receipt_contract_verified"]:
        raise BundleImportError(
            "blind_seal_receipt content does not match bundle_manifest.json"
        )
    if not parsed.validation["phase_state_hash_verified"]:
        raise BundleImportError("phase_state.json hash does not match bundle_manifest.json")
    if not parsed.validation["phase_state_contract_verified"]:
        raise BundleImportError("phase_state.json content does not match bundle_manifest.json")
    if not parsed.validation["phase_state_receipt_link_verified"]:
        raise BundleImportError("phase_state.json is not linked to blind_seal_receipt")
    if not parsed.validation["id_reference_integrity_verified"]:
        raise BundleImportError("bundle ID reference integrity check failed")
    if not parsed.validation["manifest_validation_self_consistent_verified"]:
        raise BundleImportError(
            "bundle_manifest.json validation does not match recomputed validation"
        )
    if "research_episode.json" not in parsed.json_blocks:
        raise BundleImportError("bundle is missing research_episode.json")
    try:
        episode = ResearchEpisode.model_validate(parsed.json_blocks["research_episode.json"])
    except ValidationError as exc:
        raise BundleImportError(f"research_episode.json failed schema validation: {exc}") from exc

    source_hash = file_sha256(path)
    provenance = Provenance(
        source_id=stable_id("SRC", path.as_posix(), source_hash),
        source_type="nslab_markdown_bundle",
        uri=path.as_posix(),
        content_sha256=source_hash,
        observed_at=now_kst(),
    )
    return episode.model_copy(update={"provenance": [*episode.provenance, provenance]})


def parse_bundle(path: Path) -> BundleParseResult:
    text = path.read_text(encoding="utf-8", errors="replace")
    _validate_required_marker_counts(text)
    blocks = _extract_blocks(text)
    missing = sorted(REQUIRED_BUNDLE_BLOCKS - set(blocks))
    if missing:
        raise BundleImportError(f"bundle missing required blocks: {', '.join(missing)}")

    json_blocks: dict[str, Any] = {}
    jsonl_blocks: dict[str, list[dict[str, Any]]] = {}
    payload_blocks: dict[str, str] = {}
    for name, block in blocks.items():
        payload = _strip_optional_fence(block)
        payload_blocks[name] = payload
        if name.endswith(".json"):
            json_blocks[name] = _parse_json(name, payload)
        elif name.endswith(".jsonl"):
            jsonl_blocks[name] = _parse_jsonl(name, payload)

    _validate_bundle_identity_contract(json_blocks)
    _validate_jsonl_contracts(json_blocks, jsonl_blocks)
    validation = {
        "markers_complete": True,
        "json_valid": True,
        "jsonl_valid": True,
        "blind_hash_verified": _verify_blind_hash(json_blocks),
        "blind_execution_guard_verified": _verify_blind_execution_guard(json_blocks),
        "row_disposition_hash_verified": _verify_payload_hash(
            json_blocks,
            payload_blocks,
            block_name="row_disposition.jsonl",
            manifest_field="row_disposition_sha256",
        ),
        "row_disposition_coverage_verified": _verify_row_disposition_coverage(
            json_blocks,
            jsonl_blocks,
        ),
        "source_ledger_hash_verified": _verify_payload_hash(
            json_blocks,
            payload_blocks,
            block_name="source_ledger.jsonl",
            manifest_field="source_ledger_sha256",
        ),
        "source_ledger_entry_count_verified": _verify_jsonl_entry_count(
            json_blocks,
            jsonl_blocks,
            block_name="source_ledger.jsonl",
            manifest_field="source_ledger_entry_count",
        ),
        "research_episode_hash_verified": _verify_canonical_json_hash(
            json_blocks,
            block_name="research_episode.json",
            manifest_field="research_episode_sha256",
        ),
        "brain_delta_hash_verified": _verify_payload_hash(
            json_blocks,
            payload_blocks,
            block_name="brain_delta.jsonl",
            manifest_field="brain_delta_sha256",
        ),
        "blind_seal_receipt_hash_verified": _verify_embedded_write_json_hash(
            json_blocks,
            block_name="research_episode.json",
            embedded_field="blind_seal_receipt",
            manifest_field="blind_seal_receipt_sha256",
        ),
        "blind_seal_receipt_contract_verified": _verify_blind_seal_receipt_contract(
            json_blocks
        ),
        "phase_state_hash_verified": _verify_payload_hash(
            json_blocks,
            payload_blocks,
            block_name="phase_state.json",
            manifest_field="phase_state_sha256",
        ),
        "phase_state_contract_verified": _verify_phase_state_contract(json_blocks),
        "phase_state_receipt_link_verified": _verify_phase_state_receipt_link(
            json_blocks,
        ),
        "id_reference_integrity_verified": _verify_id_reference_integrity(
            json_blocks,
            jsonl_blocks,
        ),
    }
    _add_optional_jsonl_validation(
        validation,
        json_blocks,
        jsonl_blocks,
        payload_blocks,
        block_name="candidate_web_checks.jsonl",
        hash_field="candidate_web_check_sha256",
        count_field="candidate_web_check_count",
        hash_key="candidate_web_check_hash_verified",
        count_key="candidate_web_check_count_verified",
    )
    _add_optional_json_validation(
        validation,
        json_blocks,
        payload_blocks,
        block_name="candidate_verification.json",
        hash_field="candidate_verification_sha256",
        count_field="candidate_verification_count",
        hash_key="candidate_verification_hash_verified",
        count_key="candidate_verification_count_verified",
    )
    if "candidate_verification.json" in json_blocks:
        validation["candidate_verification_contract_verified"] = (
            _verify_candidate_verification_contract(json_blocks, jsonl_blocks)
        )
    if "final_synthesis_context.json" in payload_blocks:
        validation["final_synthesis_context_hash_verified"] = _verify_payload_hash(
            json_blocks,
            payload_blocks,
            block_name="final_synthesis_context.json",
            manifest_field="final_synthesis_context_sha256",
        )
        validation["final_synthesis_context_contract_verified"] = (
            _verify_final_synthesis_context_contract(json_blocks)
        )
        validation["final_synthesis_context_candidate_web_checks_verified"] = (
            _verify_final_synthesis_candidate_web_checks_context(
                json_blocks,
                jsonl_blocks,
            )
        )
        validation["final_synthesis_context_candidate_verification_verified"] = (
            _verify_final_synthesis_candidate_verification_context(json_blocks)
        )
    _add_optional_jsonl_validation(
        validation,
        json_blocks,
        jsonl_blocks,
        payload_blocks,
        block_name="excluded_candidate_web_checks.jsonl",
        hash_field="excluded_candidate_web_check_sha256",
        count_field="excluded_candidate_web_check_count",
        hash_key="excluded_candidate_web_check_hash_verified",
        count_key="excluded_candidate_web_check_count_verified",
    )
    validation["manifest_validation_self_consistent_verified"] = (
        _verify_manifest_validation_self_consistency(json_blocks, validation)
    )
    return BundleParseResult(
        blocks=blocks,
        json_blocks=json_blocks,
        jsonl_blocks=jsonl_blocks,
        validation=validation,
    )


def _validate_required_marker_counts(text: str) -> None:
    for name in sorted(REQUIRED_BUNDLE_BLOCKS):
        begin_marker = f"<!-- NSLAB:BEGIN {name} -->"
        end_marker = f"<!-- NSLAB:END {name} -->"
        begin_count = text.count(begin_marker)
        end_count = text.count(end_marker)
        if begin_count != 1 or end_count != 1:
            raise BundleImportError(
                f"bundle block {name} must have exactly one BEGIN and one END marker"
            )


def _extract_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("<!-- NSLAB:BEGIN ") and stripped.endswith(" -->"):
            name = stripped.removeprefix("<!-- NSLAB:BEGIN ").removesuffix(" -->").strip()
            if name in blocks:
                raise BundleImportError(f"duplicate BEGIN block: {name}")
            end_marker = f"<!-- NSLAB:END {name} -->"
            index += 1
            content: list[str] = []
            found_end = False
            while index < len(lines):
                if lines[index].strip() == end_marker:
                    found_end = True
                    break
                content.append(lines[index])
                index += 1
            if not found_end:
                raise BundleImportError(f"missing END marker for block: {name}")
            blocks[name] = "\n".join(content).strip()
        index += 1
    return blocks


def _strip_optional_fence(block: str) -> str:
    lines = block.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return block.strip()


def _parse_json(name: str, payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BundleImportError(f"{name} is not valid JSON: {exc}") from exc


def _parse_jsonl(name: str, payload: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BundleImportError(f"{name}:{line_number} is not valid JSONL: {exc}") from exc
        if not isinstance(parsed, dict):
            raise BundleImportError(f"{name}:{line_number} must be a JSON object")
        rows.append(parsed)
    return rows


def _validate_jsonl_contracts(
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> None:
    bundle_cutoff_at = _bundle_manifest_cutoff_at(json_blocks)
    bundle_run_id = _bundle_manifest_string(json_blocks, "run_id")
    bundle_cutoff_at_raw = _bundle_manifest_string(json_blocks, "cutoff_at")
    for index, row in enumerate(jsonl_blocks.get("row_disposition.jsonl", []), start=1):
        if "row_number" not in row:
            raise BundleImportError(f"row_disposition.jsonl:{index} missing row_number")
        if "title" in row or "body" in row:
            raise BundleImportError(
                f"row_disposition.jsonl:{index} must not duplicate title/body"
            )
    for index, row in enumerate(jsonl_blocks.get("brain_delta.jsonl", []), start=1):
        if "record_type" not in row:
            raise BundleImportError(f"brain_delta.jsonl:{index} missing record_type")
    source_ids: list[str] = []
    for index, row in enumerate(jsonl_blocks.get("source_ledger.jsonl", []), start=1):
        missing = sorted(SOURCE_LEDGER_REQUIRED_FIELDS - set(row))
        if missing:
            raise BundleImportError(
                f"source_ledger.jsonl:{index} missing fields: {', '.join(missing)}"
            )
        if "body" in row or "content" in row:
            raise BundleImportError(
                f"source_ledger.jsonl:{index} must not duplicate body/content"
            )
        _validate_source_url(block_name="source_ledger.jsonl", index=index, row=row)
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise BundleImportError(f"source_ledger.jsonl:{index} invalid source_id")
        source_ids.append(source_id)
        usage_phase = row.get("usage_phase")
        if usage_phase not in SOURCE_LEDGER_USAGE_PHASES:
            raise BundleImportError(f"source_ledger.jsonl:{index} invalid usage_phase")
        if not isinstance(row.get("available_before_cutoff"), bool):
            raise BundleImportError(
                f"source_ledger.jsonl:{index} invalid available_before_cutoff"
            )
        if not isinstance(row.get("time_verified"), bool):
            raise BundleImportError(f"source_ledger.jsonl:{index} invalid time_verified")
        if usage_phase == "BLIND" and row.get("available_before_cutoff") is not True:
            raise BundleImportError(f"source_ledger.jsonl:{index} BLIND source after cutoff")
        if usage_phase == "BLIND" and row.get("time_verified") is not True:
            raise BundleImportError(
                f"source_ledger.jsonl:{index} BLIND source without verified time"
            )
        if usage_phase == "BLIND" and bundle_cutoff_at is not None:
            _validate_blind_published_at_before_cutoff(
                "source_ledger.jsonl",
                index,
                row,
                bundle_cutoff_at,
            )
        input_row_ids = row.get("input_row_ids")
        source_type = row.get("source_type")
        input_row_ids_valid = isinstance(input_row_ids, list) and not any(
            not isinstance(row_id, int) for row_id in input_row_ids
        )
        if not input_row_ids_valid or (
            source_type == "news_csv_row" and not input_row_ids
        ):
            raise BundleImportError(
                f"source_ledger.jsonl:{index} input_row_ids invalid for source_type"
            )
    if len(source_ids) != len(set(source_ids)):
        raise BundleImportError("source_ledger.jsonl duplicate source_id")

    for index, row in enumerate(jsonl_blocks.get("candidate_web_checks.jsonl", []), start=1):
        _validate_candidate_web_check_row(
            block_name="candidate_web_checks.jsonl",
            index=index,
            row=row,
            required_fields=CANDIDATE_WEB_CHECK_REQUIRED_FIELDS,
            expected_schema_version="nslab.candidate_web_check.v1",
            expected_run_id=bundle_run_id,
            expected_cutoff_at=bundle_cutoff_at_raw,
        )
        if row.get("time_verified") is not True:
            raise BundleImportError(
                f"candidate_web_checks.jsonl:{index} must be cutoff verified"
            )
        if row.get("available_before_cutoff") is not True:
            raise BundleImportError(
                f"candidate_web_checks.jsonl:{index} must be before cutoff"
            )
        if bundle_cutoff_at is not None:
            _validate_blind_published_at_before_cutoff(
                "candidate_web_checks.jsonl",
                index,
                row,
                bundle_cutoff_at,
            )
    for index, row in enumerate(
        jsonl_blocks.get("excluded_candidate_web_checks.jsonl", []),
        start=1,
    ):
        _validate_candidate_web_check_row(
            block_name="excluded_candidate_web_checks.jsonl",
            index=index,
            row=row,
            required_fields=EXCLUDED_CANDIDATE_WEB_CHECK_REQUIRED_FIELDS,
            expected_schema_version="nslab.excluded_candidate_web_check.v1",
            expected_run_id=bundle_run_id,
            expected_cutoff_at=bundle_cutoff_at_raw,
        )
    candidate_verification = json_blocks.get("candidate_verification.json")
    if isinstance(candidate_verification, dict):
        if candidate_verification.get("schema_version") != "nslab.candidate_verification.v1":
            raise BundleImportError("candidate_verification.json invalid schema_version")
        findings = candidate_verification.get("findings")
        if not isinstance(findings, list):
            raise BundleImportError("candidate_verification.json findings must be a list")
    final_synthesis_context = json_blocks.get("final_synthesis_context.json")
    if isinstance(final_synthesis_context, dict):
        if (
            final_synthesis_context.get("schema_version")
            != "nslab.final_synthesis_context.v1"
        ):
            raise BundleImportError("final_synthesis_context.json invalid schema_version")
        if not isinstance(final_synthesis_context.get("payload"), dict):
            raise BundleImportError("final_synthesis_context.json payload must be an object")
        required_inputs = final_synthesis_context.get("required_inputs")
        if not isinstance(required_inputs, list) or not all(
            isinstance(item, str) for item in required_inputs
        ):
            raise BundleImportError(
                "final_synthesis_context.json required_inputs must be a string list"
            )


def _validate_bundle_identity_contract(json_blocks: dict[str, Any]) -> None:
    manifest = json_blocks.get("bundle_manifest.json")
    if not isinstance(manifest, dict):
        raise BundleImportError("bundle_manifest.json must be an object")
    if manifest.get("schema_version") != "nslab.bundle_manifest.v1":
        raise BundleImportError("bundle_manifest.json invalid schema_version")
    for field_name in ("run_id", "trade_date", "cutoff_at"):
        if not isinstance(manifest.get(field_name), str) or not manifest.get(field_name):
            raise BundleImportError(f"bundle_manifest.json missing {field_name}")
    try:
        date.fromisoformat(str(manifest["trade_date"]))
    except ValueError as exc:
        raise BundleImportError("bundle_manifest.json invalid trade_date") from exc
    try:
        parse_datetime(str(manifest["cutoff_at"]))
    except ValueError as exc:
        raise BundleImportError("bundle_manifest.json invalid cutoff_at") from exc

    for block_name in ("blind_prediction.json", "research_episode.json"):
        payload = json_blocks.get(block_name)
        if not isinstance(payload, dict):
            raise BundleImportError(f"{block_name} must be an object")
        for field_name in ("trade_date", "cutoff_at"):
            if payload.get(field_name) != manifest[field_name]:
                raise BundleImportError(f"{block_name} {field_name} mismatch")


def _bundle_manifest_cutoff_at(json_blocks: dict[str, Any]) -> datetime | None:
    manifest = json_blocks.get("bundle_manifest.json")
    if not isinstance(manifest, dict):
        return None
    raw_cutoff = manifest.get("cutoff_at")
    if not isinstance(raw_cutoff, str):
        return None
    try:
        return parse_datetime(raw_cutoff)
    except ValueError:
        return None


def _bundle_manifest_string(json_blocks: dict[str, Any], field_name: str) -> str | None:
    manifest = json_blocks.get("bundle_manifest.json")
    if not isinstance(manifest, dict):
        return None
    value = manifest.get(field_name)
    return value if isinstance(value, str) and value else None


def _validate_blind_published_at_before_cutoff(
    block_name: str,
    index: int,
    row: dict[str, Any],
    cutoff_at: datetime,
) -> None:
    raw_published_at = row.get("published_at")
    if not isinstance(raw_published_at, str):
        raise BundleImportError(f"{block_name}:{index} missing published_at")
    try:
        published_at = parse_datetime(raw_published_at)
    except ValueError as exc:
        raise BundleImportError(f"{block_name}:{index} invalid published_at") from exc
    if not is_available_as_of(published_at, cutoff_at):
        raise BundleImportError(f"{block_name}:{index} BLIND source after cutoff")


def _verify_blind_hash(json_blocks: dict[str, Any]) -> bool:
    blind = json_blocks.get("blind_prediction.json")
    manifest = json_blocks.get("bundle_manifest.json", {})
    if not isinstance(blind, dict) or not isinstance(manifest, dict):
        return False
    expected = manifest.get("blind_artifact_sha256")
    if not isinstance(expected, str) or not expected:
        return False
    candidate = dict(blind)
    observed = candidate.get("blind_artifact_sha256")
    if observed is not None and observed != expected:
        return False
    candidate["blind_artifact_sha256"] = None
    return sha256_text(canonical_json(candidate)) == expected


def _verify_blind_execution_guard(json_blocks: dict[str, Any]) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    episode = json_blocks.get("research_episode.json", {})
    if not isinstance(manifest, dict) or not isinstance(episode, dict):
        return False

    mode = manifest.get("blind_context_mode")
    if mode not in {"NEWS_ONLY_STRICT", "CUTOFF_SAFE_WEB_BLIND"}:
        return False
    price_guard_fields = {
        "blind_price_repository_access_count": 0,
        "blind_current_price_access_count": 0,
    }
    for field_name, expected in price_guard_fields.items():
        if manifest.get(field_name) != expected:
            return False
    manifest_web_calls = manifest.get("blind_web_search_call_count")
    if mode == "NEWS_ONLY_STRICT" and manifest_web_calls != 0:
        return False
    if mode == "CUTOFF_SAFE_WEB_BLIND" and not isinstance(manifest_web_calls, int):
        return False
    if manifest.get("no_d_outcome_exposed") is not True:
        return False

    blind_integrity = episode.get("blind_integrity")
    if not isinstance(blind_integrity, dict):
        return False
    if blind_integrity.get("blind_context_mode") != mode:
        return False
    if blind_integrity.get("no_d_outcome_exposed") is not True:
        return False
    for field_name, expected in price_guard_fields.items():
        if blind_integrity.get(field_name) != expected:
            return False
    if blind_integrity.get("blind_web_search_call_count") != manifest_web_calls:
        return False

    receipt = episode.get("blind_seal_receipt")
    if not isinstance(receipt, dict):
        return False
    if receipt.get("phase") != "BLIND_SEALED":
        return False
    return receipt.get("no_d_outcome_exposed") is True


def _verify_payload_hash(
    json_blocks: dict[str, Any],
    payload_blocks: dict[str, str],
    *,
    block_name: str,
    manifest_field: str,
) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    if not isinstance(manifest, dict):
        return False
    expected = manifest.get(manifest_field)
    payload = payload_blocks.get(block_name)
    if not isinstance(expected, str) or not expected or payload is None:
        return False
    return sha256_text(payload) == expected or sha256_text(f"{payload}\n") == expected


def _verify_row_disposition_coverage(
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    if not isinstance(manifest, dict):
        return False
    coverage_ratio = manifest.get("row_disposition_coverage_ratio")
    if not isinstance(coverage_ratio, (int, float)) or float(coverage_ratio) != 1.0:
        return False

    rows = jsonl_blocks.get("row_disposition.jsonl", [])
    episode = json_blocks.get("research_episode.json", {})
    if isinstance(episode, dict):
        input_audit = episode.get("input_audit")
        if isinstance(input_audit, dict):
            episode_ratio = input_audit.get("row_disposition_coverage_ratio")
            if isinstance(episode_ratio, (int, float)) and float(episode_ratio) != 1.0:
                return False
        summary = episode.get("row_disposition_summary")
        if isinstance(summary, dict):
            total_rows = summary.get("total_rows")
            if isinstance(total_rows, int) and total_rows != len(rows):
                return False
            summary_ratio = summary.get("coverage_ratio")
            if isinstance(summary_ratio, (int, float)) and float(summary_ratio) != 1.0:
                return False
    return True


def _verify_jsonl_entry_count(
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
    *,
    block_name: str,
    manifest_field: str,
) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    if not isinstance(manifest, dict):
        return False
    expected = manifest.get(manifest_field)
    rows = jsonl_blocks.get(block_name)
    if not isinstance(expected, int) or rows is None:
        return False
    return expected == len(rows)


def _verify_json_finding_count(
    json_blocks: dict[str, Any],
    *,
    block_name: str,
    manifest_field: str,
) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    payload = json_blocks.get(block_name)
    if not isinstance(manifest, dict) or not isinstance(payload, dict):
        return False
    expected = manifest.get(manifest_field)
    findings = payload.get("findings")
    if not isinstance(expected, int) or not isinstance(findings, list):
        return False
    return expected == len(findings)


def _add_optional_jsonl_validation(
    validation: dict[str, bool],
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
    payload_blocks: dict[str, str],
    *,
    block_name: str,
    hash_field: str,
    count_field: str,
    hash_key: str,
    count_key: str,
) -> None:
    manifest = json_blocks.get("bundle_manifest.json", {})
    if not isinstance(manifest, dict):
        return
    has_manifest_fields = hash_field in manifest or count_field in manifest
    has_block = block_name in payload_blocks or block_name in jsonl_blocks
    if not has_manifest_fields and not has_block:
        return
    validation[hash_key] = _verify_payload_hash(
        json_blocks,
        payload_blocks,
        block_name=block_name,
        manifest_field=hash_field,
    )
    validation[count_key] = _verify_jsonl_entry_count(
        json_blocks,
        jsonl_blocks,
        block_name=block_name,
        manifest_field=count_field,
    )


def _add_optional_json_validation(
    validation: dict[str, bool],
    json_blocks: dict[str, Any],
    payload_blocks: dict[str, str],
    *,
    block_name: str,
    hash_field: str,
    count_field: str,
    hash_key: str,
    count_key: str,
) -> None:
    manifest = json_blocks.get("bundle_manifest.json", {})
    if not isinstance(manifest, dict):
        return
    has_manifest_fields = hash_field in manifest or count_field in manifest
    has_block = block_name in payload_blocks or block_name in json_blocks
    if not has_manifest_fields and not has_block:
        return
    validation[hash_key] = _verify_payload_hash(
        json_blocks,
        payload_blocks,
        block_name=block_name,
        manifest_field=hash_field,
    )
    validation[count_key] = _verify_json_finding_count(
        json_blocks,
        block_name=block_name,
        manifest_field=count_field,
    )


def _validate_candidate_web_check_row(
    *,
    block_name: str,
    index: int,
    row: dict[str, Any],
    required_fields: set[str],
    expected_schema_version: str,
    expected_run_id: str | None = None,
    expected_cutoff_at: str | None = None,
) -> None:
    missing = sorted(required_fields - set(row))
    if missing:
        raise BundleImportError(
            f"{block_name}:{index} missing fields: {', '.join(missing)}"
        )
    _validate_source_url(block_name=block_name, index=index, row=row)
    if row.get("schema_version") != expected_schema_version:
        raise BundleImportError(f"{block_name}:{index} invalid schema_version")
    if expected_run_id is not None and row.get("run_id") != expected_run_id:
        raise BundleImportError(f"{block_name}:{index} run_id mismatch")
    if expected_cutoff_at is not None and row.get("cutoff_at") != expected_cutoff_at:
        raise BundleImportError(f"{block_name}:{index} cutoff_at mismatch")
    if "opened_text" in row or "body" in row or "content" in row:
        raise BundleImportError(
            f"{block_name}:{index} must not duplicate opened/body/content"
        )
    if not isinstance(row.get("source_id"), str) or not row.get("source_id"):
        raise BundleImportError(f"{block_name}:{index} invalid source_id")
    if not isinstance(row.get("candidate_rank"), int):
        raise BundleImportError(f"{block_name}:{index} invalid candidate_rank")


def _validate_source_url(*, block_name: str, index: int, row: dict[str, Any]) -> None:
    source_url = row.get("source_url")
    if not isinstance(source_url, str) or not source_url:
        raise BundleImportError(f"{block_name}:{index} invalid source_url")
    url = row.get("url")
    if isinstance(url, str) and url and source_url != url:
        raise BundleImportError(f"{block_name}:{index} source_url mismatch")


def _verify_canonical_json_hash(
    json_blocks: dict[str, Any],
    *,
    block_name: str,
    manifest_field: str,
) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    payload = json_blocks.get(block_name)
    if not isinstance(manifest, dict):
        return False
    expected = manifest.get(manifest_field)
    if not isinstance(expected, str) or not expected or payload is None:
        return False
    return sha256_text(canonical_json(payload)) == expected


def _verify_embedded_write_json_hash(
    json_blocks: dict[str, Any],
    *,
    block_name: str,
    embedded_field: str,
    manifest_field: str,
) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    payload = json_blocks.get(block_name)
    if not isinstance(manifest, dict) or not isinstance(payload, dict):
        return False
    expected = manifest.get(manifest_field)
    embedded = payload.get(embedded_field)
    if not isinstance(expected, str) or not expected or embedded is None:
        return False
    write_json_text = json.dumps(
        embedded,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    return sha256_text(write_json_text) == expected


def _verify_phase_state_receipt_link(json_blocks: dict[str, Any]) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    phase_state = json_blocks.get("phase_state.json")
    if not isinstance(manifest, dict) or not isinstance(phase_state, dict):
        return False
    if phase_state.get("phase") != "BLIND_SEALED":
        return False
    receipt_sha = manifest.get("blind_seal_receipt_sha256")
    if not isinstance(receipt_sha, str) or not receipt_sha:
        return False
    if phase_state.get("blind_seal_receipt_sha256") != receipt_sha:
        return False
    manifest_run_id = manifest.get("run_id")
    if isinstance(manifest_run_id, str) and phase_state.get("run_id") != manifest_run_id:
        return False
    manifest_trade_date = manifest.get("trade_date")
    return not (
        isinstance(manifest_trade_date, str)
        and phase_state.get("trade_date") != manifest_trade_date
    )


def _verify_phase_state_contract(json_blocks: dict[str, Any]) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    phase_state = json_blocks.get("phase_state.json")
    if not isinstance(manifest, dict) or not isinstance(phase_state, dict):
        return False
    if phase_state.get("schema_version") != "nslab.phase_state.v1":
        return False
    if phase_state.get("phase") != "BLIND_SEALED":
        return False
    manifest_run_id = manifest.get("run_id")
    if isinstance(manifest_run_id, str) and phase_state.get("run_id") != manifest_run_id:
        return False
    for field_name in ("trade_date", "cutoff_at"):
        manifest_value = manifest.get(field_name)
        if isinstance(manifest_value, str) and phase_state.get(field_name) != manifest_value:
            return False
    sealed_at = phase_state.get("sealed_at")
    if not isinstance(sealed_at, str) or not sealed_at:
        return False
    blind_context_mode = manifest.get("blind_context_mode")
    if isinstance(blind_context_mode, str):
        completed_phases = set(_string_list(phase_state.get("completed_phases")))
        if completed_phases.isdisjoint(_phase_a_names(blind_context_mode)):
            return False
    return True


def _verify_final_synthesis_context_contract(json_blocks: dict[str, Any]) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    context = json_blocks.get("final_synthesis_context.json")
    if not isinstance(manifest, dict) or not isinstance(context, dict):
        return False
    if context.get("schema_version") != "nslab.final_synthesis_context.v1":
        return False
    manifest_run_id = manifest.get("run_id")
    if isinstance(manifest_run_id, str) and context.get("run_id") != manifest_run_id:
        return False
    payload = context.get("payload")
    if not isinstance(payload, dict):
        return False
    if context.get("payload_sha256") != sha256_text(canonical_json(payload)):
        return False
    required_inputs = payload.get("required_inputs")
    if not isinstance(required_inputs, list) or not all(
        isinstance(item, str) for item in required_inputs
    ):
        return False
    if context.get("required_inputs") != required_inputs:
        return False
    if required_inputs != list(FINAL_SYNTHESIS_REQUIRED_INPUTS):
        return False
    if any(key not in payload for key in required_inputs):
        return False
    expected_summary = final_synthesis_input_summary(payload)
    if context.get("input_summary") != expected_summary:
        return False
    manifest_summary = manifest.get("final_synthesis_context_summary")
    return manifest_summary is None or manifest_summary == expected_summary


def _verify_final_synthesis_candidate_web_checks_context(
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> bool:
    payload = _final_synthesis_context_payload(json_blocks)
    if payload is None:
        return False
    expected_rows = [
        _candidate_web_check_context_row(row)
        for row in jsonl_blocks.get("candidate_web_checks.jsonl", [])
    ]
    return payload.get("candidate_web_checks") == expected_rows


def _verify_final_synthesis_candidate_verification_context(
    json_blocks: dict[str, Any],
) -> bool:
    payload = _final_synthesis_context_payload(json_blocks)
    if payload is None:
        return False
    verification = json_blocks.get("candidate_verification.json")
    expected = verification if isinstance(verification, dict) else {}
    return payload.get("candidate_verification") == expected


def _final_synthesis_context_payload(
    json_blocks: dict[str, Any],
) -> dict[str, Any] | None:
    context = json_blocks.get("final_synthesis_context.json")
    if not isinstance(context, dict):
        return None
    payload = context.get("payload")
    return payload if isinstance(payload, dict) else None


def _candidate_web_check_context_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_rank": row.get("candidate_rank"),
        "candidate_ticker": row.get("candidate_ticker"),
        "candidate_company_name": row.get("candidate_company_name"),
        "candidate_path_type": row.get("candidate_path_type"),
        "candidate_subject_type": row.get("candidate_subject_type"),
        "candidate_expansion_path": row.get("candidate_expansion_path"),
        "candidate_expansion_hypothesis": row.get("candidate_expansion_hypothesis"),
        "candidate_investigation_questions": row.get(
            "candidate_investigation_questions"
        ),
        "verification_focus": row.get("verification_focus"),
        "source_id": row.get("source_id"),
        "query": row.get("query"),
        "title": row.get("title"),
        "url": row.get("url"),
        "snippet": row.get("snippet"),
        "published_at": row.get("published_at"),
        "time_verified": row.get("time_verified"),
        "content_sha256": row.get("content_sha256"),
        "opened_text_excerpt": row.get("opened_text_excerpt"),
    }


def _verify_candidate_verification_contract(
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    verification = json_blocks.get("candidate_verification.json")
    if not isinstance(manifest, dict) or not isinstance(verification, dict):
        return False
    if verification.get("schema_version") != "nslab.candidate_verification.v1":
        return False
    manifest_run_id = manifest.get("run_id")
    if isinstance(manifest_run_id, str) and verification.get("run_id") != manifest_run_id:
        return False
    manifest_cutoff_at = manifest.get("cutoff_at")
    if (
        isinstance(manifest_cutoff_at, str)
        and verification.get("cutoff_at") != manifest_cutoff_at
    ):
        return False
    findings = verification.get("findings")
    if not isinstance(findings, list) or not all(
        isinstance(finding, dict) for finding in findings
    ):
        return False
    if verification.get("subject_count") != len(findings):
        return False
    expected_count = manifest.get("candidate_verification_count")
    if isinstance(expected_count, int) and expected_count != len(findings):
        return False
    required_dimensions = _string_list(verification.get("required_dimensions"))
    if not required_dimensions:
        return False
    accepted_rows = jsonl_blocks.get("candidate_web_checks.jsonl", [])
    excluded_rows = jsonl_blocks.get("excluded_candidate_web_checks.jsonl", [])
    accepted_source_ids = _row_string_values(accepted_rows, "source_id")
    excluded_source_ids = _row_string_values(excluded_rows, "source_id")
    finding_accepted_ids: set[str] = set()
    finding_excluded_ids: set[str] = set()
    total_source_count = 0
    total_excluded_source_count = 0
    for finding in findings:
        if _verification_dimension_names(finding) != required_dimensions:
            return False
        accepted_ids = set(_string_list(finding.get("accepted_source_ids")))
        excluded_ids = set(_string_list(finding.get("excluded_source_ids")))
        if not isinstance(finding.get("source_count"), int):
            return False
        if not isinstance(finding.get("excluded_source_count"), int):
            return False
        if finding["source_count"] != len(accepted_ids):
            return False
        if finding["excluded_source_count"] != len(excluded_ids):
            return False
        finding_accepted_ids.update(accepted_ids)
        finding_excluded_ids.update(excluded_ids)
        total_source_count += finding["source_count"]
        total_excluded_source_count += finding["excluded_source_count"]
    if finding_accepted_ids != accepted_source_ids:
        return False
    if finding_excluded_ids != excluded_source_ids:
        return False
    if isinstance(manifest.get("candidate_web_check_count"), int) and (
        total_source_count != manifest["candidate_web_check_count"]
    ):
        return False
    expected_excluded_count = manifest.get("excluded_candidate_web_check_count")
    return not isinstance(expected_excluded_count, int) or (
        total_excluded_source_count == expected_excluded_count
    )


def _verify_blind_seal_receipt_contract(json_blocks: dict[str, Any]) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    episode = json_blocks.get("research_episode.json", {})
    blind = json_blocks.get("blind_prediction.json", {})
    if (
        not isinstance(manifest, dict)
        or not isinstance(episode, dict)
        or not isinstance(blind, dict)
    ):
        return False
    receipt = episode.get("blind_seal_receipt")
    if not isinstance(receipt, dict):
        return False
    if receipt.get("schema_version") != "nslab.blind_seal_receipt.v1":
        return False
    if receipt.get("phase") != "BLIND_SEALED":
        return False
    manifest_run_id = manifest.get("run_id")
    if isinstance(manifest_run_id, str) and receipt.get("run_id") != manifest_run_id:
        return False
    for field_name in ("trade_date", "cutoff_at", "blind_context_mode"):
        manifest_value = manifest.get(field_name)
        if isinstance(manifest_value, str) and receipt.get(field_name) != manifest_value:
            return False
    expected_blind_hash = manifest.get("blind_artifact_sha256")
    if not isinstance(expected_blind_hash, str) or not expected_blind_hash:
        return False
    if receipt.get("blind_artifact_sha256") != expected_blind_hash:
        return False
    if blind.get("blind_artifact_sha256") != expected_blind_hash:
        return False
    if episode.get("blind_artifact_sha256") != expected_blind_hash:
        return False
    for field_name in ("row_disposition_sha256", "source_ledger_sha256"):
        manifest_hash = manifest.get(field_name)
        if isinstance(manifest_hash, str) and receipt.get(field_name) != manifest_hash:
            return False
    if receipt.get("no_d_outcome_exposed") is not True:
        return False
    if manifest.get("no_d_outcome_exposed") is not True:
        return False
    validation = receipt.get("validation")
    if not isinstance(validation, dict):
        return False
    if validation.get("canonical_blind_hash_verified") is not True:
        return False
    for field_name in (
        "blind_web_search_call_count",
        "blind_price_repository_access_count",
        "blind_current_price_access_count",
    ):
        expected = manifest.get(field_name)
        if isinstance(expected, int) and validation.get(field_name) != expected:
            return False
    return True


def _verify_manifest_validation_self_consistency(
    json_blocks: dict[str, Any],
    recomputed_validation: dict[str, bool],
) -> bool:
    manifest = json_blocks.get("bundle_manifest.json", {})
    if not isinstance(manifest, dict):
        return False
    manifest_validation = manifest.get("validation")
    if not isinstance(manifest_validation, dict):
        return False
    for key, recomputed in recomputed_validation.items():
        if key not in manifest_validation and key in LEGACY_OPTIONAL_VALIDATION_KEYS:
            continue
        if manifest_validation.get(key) is not recomputed:
            return False
    self_key = "manifest_validation_self_consistent_verified"
    return not (
        self_key in manifest_validation and manifest_validation[self_key] is not True
    )


def _verify_id_reference_integrity(
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> bool:
    row_dispositions = jsonl_blocks.get("row_disposition.jsonl", [])
    source_ledger = jsonl_blocks.get("source_ledger.jsonl", [])
    row_numbers = {row.get("row_number") for row in row_dispositions}
    if len(row_numbers) != len(row_dispositions):
        return False
    event_ids = _row_string_values(row_dispositions, "event_id")
    row_source_ids = _row_string_values(row_dispositions, "source_id")
    for row in row_dispositions:
        row_source_ids.update(_string_list(row.get("provenance_source_ids")))
    if not row_numbers or not event_ids:
        return False

    for source in source_ledger:
        source_type = source.get("source_type")
        input_row_ids = source.get("input_row_ids")
        if not isinstance(input_row_ids, list):
            return False
        if source_type != "news_csv_row" and not input_row_ids:
            continue
        if not input_row_ids:
            return False
        if any(row_id not in row_numbers for row_id in input_row_ids):
            return False
        ledger_event_ids = _string_list(source.get("event_ids"))
        if ledger_event_ids and any(event_id not in event_ids for event_id in ledger_event_ids):
            return False
        source_id = source.get("source_id")
        if isinstance(source_id, str) and source_id and row_source_ids and source_id not in row_source_ids:
            return False

    referenced_event_ids = _prediction_event_ids(json_blocks.get("blind_prediction.json"))
    referenced_event_ids.update(
        _prediction_event_ids(json_blocks.get("research_episode.json"), field_name="blind_predictions")
    )
    return not any(event_id not in event_ids for event_id in referenced_event_ids)


def _prediction_event_ids(payload: Any, *, field_name: str = "candidates") -> set[str]:
    if not isinstance(payload, dict):
        return set()
    event_ids: set[str] = set()
    for sector in payload.get("dominant_sectors", []):
        if isinstance(sector, dict):
            event_ids.update(_string_list(sector.get("triggering_events")))
    for candidate in payload.get(field_name, []):
        if not isinstance(candidate, dict):
            continue
        event_ids.update(_string_list(candidate.get("event_ids")))
        for source_url in _string_list(candidate.get("source_urls")):
            if source_url.startswith("news://"):
                event_ids.add(source_url.removeprefix("news://"))
    return event_ids


def _verification_dimension_names(finding: dict[str, Any]) -> list[str]:
    dimensions = finding.get("verification_dimensions")
    if not isinstance(dimensions, list):
        return []
    return [
        str(dimension["name"])
        for dimension in dimensions
        if isinstance(dimension, dict) and isinstance(dimension.get("name"), str)
    ]


def _row_string_values(rows: list[dict[str, Any]], field_name: str) -> set[str]:
    return {value for row in rows if isinstance((value := row.get(field_name)), str) and value}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _phase_a_names(blind_context_mode: str) -> set[str]:
    names = {f"PHASE_A_{blind_context_mode}"}
    if blind_context_mode == "NEWS_ONLY_STRICT":
        names.add("PHASE_A_NEWS_ONLY_BLIND")
    return names
