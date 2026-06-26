"""Helpers for record-level fact/inference/event reference closure."""

from __future__ import annotations

from typing import Any

REFERENCE_TYPES = ("event", "fact", "inference")
REFERENCE_PREFIXES = {
    "event": "EVT-",
    "fact": "FACT-",
    "inference": "INF-",
}


def known_reference_ids_from_blocks(
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> dict[str, set[str]]:
    known = _empty_reference_sets()
    for block_name, rows in jsonl_blocks.items():
        lower_name = block_name.lower()
        for row in rows:
            if "row_disposition" in lower_name or "event_ledger" in lower_name:
                _add_string(known["event"], row.get("event_id"))
                _add_strings(known["event"], row.get("event_ids"))
            if "source_ledger" in lower_name:
                _add_strings(known["event"], row.get("event_ids"))
            if "fact_ledger" in lower_name:
                _add_string(known["fact"], row.get("fact_id"))
                _add_strings(known["fact"], row.get("fact_ids"))
            if "inference_ledger" in lower_name:
                _add_string(known["inference"], row.get("inference_id"))
                _add_strings(known["inference"], row.get("inference_ids"))
    for block_name, payload in json_blocks.items():
        lower_name = block_name.lower()
        if "event_ledger" in lower_name or "row_disposition" in lower_name:
            _collect_definition_ids(payload, "event", known["event"])
        if "fact_ledger" in lower_name:
            _collect_definition_ids(payload, "fact", known["fact"])
        if "inference_ledger" in lower_name:
            _collect_definition_ids(payload, "inference", known["inference"])
    return known


def payload_reference_audit(
    record_payloads: list[tuple[str, dict[str, Any]]],
    known_ids: dict[str, set[str]],
) -> dict[str, Any]:
    missing_by_reference: dict[tuple[str, str], set[str]] = {}
    reference_count = 0
    for record_id, payload in record_payloads:
        references = payload_reference_ids(payload)
        reference_count += sum(len(ids) for ids in references.values())
        for reference_type, ids in references.items():
            allowed = known_ids.get(reference_type, set())
            for reference_id in ids:
                if reference_id not in allowed:
                    missing_by_reference.setdefault(
                        (reference_type, reference_id),
                        set(),
                    ).add(record_id)
    return {
        "reference_count": reference_count,
        "missing_references": [
            {
                "reference_type": reference_type,
                "reference_id": reference_id,
                "record_ids": sorted(record_ids),
            }
            for (reference_type, reference_id), record_ids in sorted(
                missing_by_reference.items()
            )
        ],
    }


def payload_reference_ids(payload: dict[str, Any]) -> dict[str, set[str]]:
    references = _empty_reference_sets()
    _collect_payload_references(payload, references)
    return references


def _collect_definition_ids(value: Any, reference_type: str, target: set[str]) -> None:
    if isinstance(value, dict):
        _add_string(target, value.get(f"{reference_type}_id"))
        _add_strings(target, value.get(f"{reference_type}_ids"))
        for item in value.values():
            _collect_definition_ids(item, reference_type, target)
    elif isinstance(value, list):
        for item in value:
            _collect_definition_ids(item, reference_type, target)


def _collect_payload_references(
    value: Any,
    references: dict[str, set[str]],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _add_reference_value(key, item, references)
            _collect_payload_references(item, references)
    elif isinstance(value, list):
        for item in value:
            _collect_payload_references(item, references)


def _add_reference_value(
    key: str,
    value: Any,
    references: dict[str, set[str]],
) -> None:
    lower_key = key.lower()
    if "fact_or_inference_id" in lower_key:
        _add_prefixed_values(value, references)
        return
    for reference_type in REFERENCE_TYPES:
        if _is_reference_key(lower_key, reference_type):
            if lower_key.endswith("_ids") or lower_key == f"{reference_type}_ids":
                _add_strings(references[reference_type], value)
            else:
                _add_string(references[reference_type], value)


def _is_reference_key(key: str, reference_type: str) -> bool:
    return key == f"{reference_type}_id" or key.endswith(
        f"_{reference_type}_id"
    ) or key == f"{reference_type}_ids" or key.endswith(f"_{reference_type}_ids")


def _add_prefixed_values(value: Any, references: dict[str, set[str]]) -> None:
    values: list[str]
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [item for item in value if isinstance(item, str)]
    else:
        return
    for item in values:
        for reference_type, prefix in REFERENCE_PREFIXES.items():
            if item.startswith(prefix):
                references[reference_type].add(item)


def _add_string(target: set[str], value: Any) -> None:
    if isinstance(value, str) and value:
        target.add(value)


def _add_strings(target: set[str], value: Any) -> None:
    if isinstance(value, list):
        target.update(item for item in value if isinstance(item, str) and item)


def _empty_reference_sets() -> dict[str, set[str]]:
    return {reference_type: set() for reference_type in REFERENCE_TYPES}
