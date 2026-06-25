"""Parser for single-file NSLAB Markdown research bundles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from news_scalping_lab.contracts.models import Provenance, ResearchEpisode
from news_scalping_lab.utils import canonical_json, file_sha256, now_kst, sha256_text, stable_id

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
    if not parsed.validation["phase_state_hash_verified"]:
        raise BundleImportError("phase_state.json hash does not match bundle_manifest.json")
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

    _validate_jsonl_contracts(jsonl_blocks)
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
        "phase_state_hash_verified": _verify_payload_hash(
            json_blocks,
            payload_blocks,
            block_name="phase_state.json",
            manifest_field="phase_state_sha256",
        ),
        "phase_state_receipt_link_verified": _verify_phase_state_receipt_link(
            json_blocks,
        ),
        "id_reference_integrity_verified": _verify_id_reference_integrity(
            json_blocks,
            jsonl_blocks,
        ),
    }
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


def _validate_jsonl_contracts(jsonl_blocks: dict[str, list[dict[str, Any]]]) -> None:
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
        input_row_ids = row.get("input_row_ids")
        if (
            not isinstance(input_row_ids, list)
            or not input_row_ids
            or any(not isinstance(row_id, int) for row_id in input_row_ids)
        ):
            raise BundleImportError(
                f"source_ledger.jsonl:{index} input_row_ids must be non-empty integers"
            )
    if len(source_ids) != len(set(source_ids)):
        raise BundleImportError("source_ledger.jsonl duplicate source_id")


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
    guard_fields = {
        "blind_web_search_call_count": 0,
        "blind_price_repository_access_count": 0,
        "blind_current_price_access_count": 0,
    }
    if mode == "NEWS_ONLY_STRICT":
        for field_name, expected in guard_fields.items():
            if manifest.get(field_name) != expected:
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
    for field_name, expected in guard_fields.items():
        if blind_integrity.get(field_name) != expected:
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
        input_row_ids = source.get("input_row_ids")
        if not isinstance(input_row_ids, list) or not input_row_ids:
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


def _row_string_values(rows: list[dict[str, Any]], field_name: str) -> set[str]:
    return {value for row in rows if isinstance((value := row.get(field_name)), str) and value}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
