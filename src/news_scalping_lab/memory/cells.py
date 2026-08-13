"""Deterministic, model-versioned semantic memory-cell membership."""

from __future__ import annotations

import json
import math
import struct
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from news_scalping_lab.contracts.memory_context import (
    MemoryCellEntry,
    MemoryCellMembership,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import record_routing_metadata
from news_scalping_lab.utils import as_kst, canonical_json, parse_datetime, sha256_text

MEMORY_CELL_CLUSTERING_VERSION = "semantic_sign_lsh.v1"
MEMORY_CELL_NORMALIZER_VERSION = "cutoff_safe_structural_projection.v4"
MEMORY_CELL_SCHEMA_VERSION = "memory_cell_snapshot.v2"
MEMORY_CELL_MEMBERSHIP_RULE = "semantic_sign_primary_adjacent_secondary"
MEMORY_CELL_MEMBERSHIP_RULE_VERSION = "v2"
MEMORY_CELL_SIGNATURE_BITS = 10
MEMORY_CELL_SECONDARY_LIMIT = 2
MEMORY_VECTOR_QUANTIZATION_SCALE = 10_000_000
MISSING_STRUCTURAL_CONTEXT = "unavailable_in_record_payload"

_STRUCTURAL_VALUE_KEYS = frozenset(
    {
        "title",
        "headline",
        "exact_quote",
        "event_quote",
        "source_quote",
        "mechanism",
        "mechanisms",
        "description",
        "relation_explanation",
        "why_now",
        "event_type",
        "direct_event_type",
        "event_family",
        "event_category",
        "modality",
        "materiality",
        "path_type",
        "theme_name",
        "fact_type",
        "fact_class",
        "counterparty",
        "counterparty_name",
        "business_role",
        "beneficiary_role",
        "benefit_layer",
        "product",
        "service",
        "policy_name",
        "contract_amount",
        "order_value",
        "investment_amount",
        "ownership_pct",
        "dilution_ratio",
        "buyback_ratio",
        "market_cap_ratio",
        "revenue_ratio",
        "correction",
        "rejection_reason",
        "rejection_or_exclusion_reason",
        "matched_quotes",
    }
)

_OUTCOME_CONTAINERS = frozenset(
    {
        "D_outcome",
        "outcome",
        "issuer_day_outcome",
        "safe_D1_features",
        "safe_D1_context",
    }
)
_NESTED_PAYLOAD_KEYS = (
    "payload",
    "D_outcome",
    "outcome",
    "issuer_day_outcome",
    "safe_D1_features",
    "safe_D1_context",
    "fields",
)
_NON_STRUCTURAL_KEYS = frozenset(
    {
        "record_type",
        "summary",
        "lesson",
        "legacy_record_type",
        "training_target",
        "training_eligible",
        "eligibility_reason",
        "evidence_phase",
        "source_phase",
        "status",
        "confidence_label",
        "label_quality",
        "response_class",
        "response_summary",
        "outcome_label",
        "outcome_summary",
        "postmortem_summary",
        "price_reaction_explanation",
        "classification",
        "screening_decision",
        "negative_control_reason",
        "audit_summary",
        "audit_explanation",
        "training_exclusion_reason",
        "label_leakage_policy",
        "sample_weight",
        "available_from",
        "trade_date",
        "ticker",
        "company_name",
        "name",
    }
)
_TICKER_KEYS = (
    "ticker",
    "code",
    "issuer_ticker",
    "candidate_ticker",
    "outcome_ticker",
)
_COMPANY_KEYS = (
    "company_name",
    "issuer_company_name",
    "candidate_company_name",
    "outcome_company_name",
)
_THEME_KEYS = ("theme_id", "candidate_theme_id", "theme_name")
_EVENT_KEYS = (
    "event_id",
    "source_event_id",
    "direct_event_case_id",
    "observation_id",
    "fact_id",
)
_UNIT_EVENT_KEYS = ("event_id", "source_event_id", "direct_event_case_id")
_PAIR_KEYS = ("blind_pair_id", "preference_pair_id", "pair_id")


@dataclass(frozen=True)
class MemoryCellBuild:
    memberships: list[MemoryCellMembership]
    cells: list[MemoryCellEntry]
    centroids: dict[str, list[float]]
    reasoning_centroids: dict[str, list[float]]
    documents: dict[str, str]


class RecordMemoryDocumentResolver:
    """Resolve cutoff-safe structural text while retaining one episode cache."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._evidence_key: tuple[str, str] | None = None
        self._evidence: dict[str, tuple[str, ...]] = {}

    def document(self, record: BrainRecordEnvelope) -> str:
        direct_structure = _dedupe_structure(_cutoff_safe_structure(record.payload))
        evidence_documents: tuple[str, ...] = ()
        if not direct_structure:
            blind_cutoff = _record_blind_cutoff(self.root, record)
            evidence_key = (record.episode_id, blind_cutoff.isoformat())
            if self._evidence_key != evidence_key:
                self._evidence_key = evidence_key
                self._evidence = _episode_blind_evidence(
                    self.root,
                    record.episode_id,
                    cutoff_at=blind_cutoff,
                )
            evidence_documents = _record_evidence_documents(record, self._evidence)
        return record_memory_document(record, evidence_documents=evidence_documents)


def record_memory_document(
    record: BrainRecordEnvelope,
    *,
    evidence_documents: tuple[str, ...] = (),
) -> str:
    """Canonical searchable text without adding domain knowledge in code."""

    structure = _dedupe_structure(_cutoff_safe_structure(record.payload))
    sections: list[dict[str, Any]] = []
    if structure:
        sections.append({"record_structure": structure})
    if evidence_documents:
        sections.append({"source_evidence": sorted(set(evidence_documents))})
    if not sections:
        sections = [{"structural_context": MISSING_STRUCTURAL_CONTEXT}]
    payload_text = json.dumps(
        sections,
        ensure_ascii=False,
        sort_keys=True,
    )
    return payload_text


def build_record_memory_documents(
    root: Path,
    records: list[BrainRecordEnvelope],
) -> dict[str, str]:
    """Resolve missing structural text from referenced blind evidence artifacts."""

    resolver = RecordMemoryDocumentResolver(root)
    documents: dict[str, str] = {}
    for record in records:
        documents[record.record_id] = resolver.document(record)
    return documents


def _cutoff_safe_structure(value: Any) -> Any:
    if isinstance(value, dict):
        projected: list[dict[str, Any]] = []
        for key, item in sorted(value.items()):
            if key in _OUTCOME_CONTAINERS:
                continue
            if _is_structural_key(key):
                normalized = _structural_value(item)
                if normalized is not None:
                    projected.append({"field": key, "value": normalized})
            nested = _cutoff_safe_structure(item)
            if nested:
                projected.extend(nested)
        return projected
    if isinstance(value, list):
        projected = []
        for item in value:
            nested = _cutoff_safe_structure(item)
            if nested:
                projected.extend(nested)
        return projected
    return []


def _is_structural_key(key: str) -> bool:
    normalized = key.strip()
    if not normalized or normalized in _NON_STRUCTURAL_KEYS:
        return False
    if normalized.endswith(("_id", "_ids", "_sha256", "_hash")):
        return False
    lowered = normalized.lower()
    if any(
        token in lowered
        for token in (
            "return_pct",
            "upper_limit",
            "outcome_",
            "blind_score",
            "final_rank",
            "amount_rank",
            "turnover_rank",
        )
    ):
        return False
    return normalized in _STRUCTURAL_VALUE_KEYS


def _dedupe_structure(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {canonical_json(item): item for item in items}
    return [unique[key] for key in sorted(unique)]


def _episode_blind_evidence(
    root: Path,
    episode_id: str,
    *,
    cutoff_at: datetime,
) -> dict[str, tuple[str, ...]]:
    raw_blocks = root / "research" / "episodes" / episode_id / "raw_blocks"
    evidence: dict[str, set[str]] = defaultdict(set)
    if not raw_blocks.exists():
        return {}
    for path in sorted(raw_blocks.iterdir()):
        lowered = path.name.lower()
        if path.suffix.lower() not in {".json", ".jsonl"}:
            continue
        if not any(token in lowered for token in ("source", "fact", "inference", "observation")):
            continue
        if any(token in lowered for token in ("outcome", "postmortem", "audit")):
            continue
        for row in _read_structured_rows(path):
            if not _row_is_blind_and_cutoff_safe(row, cutoff_at=cutoff_at):
                continue
            identifiers = _row_identifiers(row)
            texts = _source_evidence_texts(row)
            for identifier in identifiers:
                evidence[identifier].update(texts)
    return {key: tuple(sorted(values)) for key, values in evidence.items()}


def _row_is_blind_and_cutoff_safe(
    row: dict[str, Any],
    *,
    cutoff_at: datetime,
) -> bool:
    phase_keys = {"source_phase", "evidence_phase", "phase"}
    time_keys = {
        "available_from",
        "known_at",
        "observed_at",
        "published_at",
        "published_at_kst",
        "disclosed_at",
    }
    for mapping in _all_nested_mappings(row):
        for key, value in mapping.items():
            if key in phase_keys:
                if not isinstance(value, str) or not value.strip():
                    return False
                phase = value.strip().upper()
                if phase not in {"BLIND", "BLIND_SAFE", "PREOPEN"}:
                    return False
            if key in {
                "available_before_cutoff",
                "used_in_blind",
                "cutoff_safe",
                "time_verified",
            } and value is not True:
                return False
            if key not in time_keys:
                continue
            if not isinstance(value, str) or not value.strip():
                return False
            try:
                observed_at = parse_datetime(value)
            except ValueError:
                return False
            if as_kst(observed_at) > as_kst(cutoff_at):
                return False
    return True


def _record_blind_cutoff(root: Path, record: BrainRecordEnvelope) -> datetime:
    envelope_path = (
        root
        / "research"
        / "episodes"
        / record.episode_id
        / "bundle_envelope.json"
    )
    try:
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        envelope = None
    if isinstance(envelope, dict):
        cutoff_at = envelope.get("cutoff_at")
        if isinstance(cutoff_at, str) and cutoff_at.strip():
            try:
                return as_kst(parse_datetime(cutoff_at))
            except ValueError:
                pass
    return datetime.combine(record.trade_date, time(8, 59, 59), tzinfo=as_kst(record.available_from).tzinfo)


def _all_nested_mappings(value: Any) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        mappings.append(value)
        for item in value.values():
            mappings.extend(_all_nested_mappings(item))
    elif isinstance(value, list):
        for item in value:
            mappings.extend(_all_nested_mappings(item))
    return mappings


def _read_structured_rows(path: Path) -> list[dict[str, Any]]:
    try:
        if path.suffix.lower() == ".jsonl":
            return [
                value
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
                and isinstance((value := json.loads(line)), dict)
            ]
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _row_identifiers(row: dict[str, Any]) -> set[str]:
    identifiers = set()
    for key, value in row.items():
        if not (key == "id" or key.endswith("_id")):
            continue
        if isinstance(value, str) and value.strip():
            identifiers.add(value.strip())
    return identifiers


def _source_evidence_texts(row: dict[str, Any]) -> tuple[str, ...]:
    keys = {
        "title",
        "headline",
        "short_title",
        "body",
        "exact_quote",
        "semantic_witness",
        "statement",
        "mechanism",
        "notes",
        "fact_class",
        "fact_type",
        "event_type",
        "observation_type",
        "catalyst_type",
    }
    values = []
    for key, value in row.items():
        if key not in keys:
            continue
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return tuple(sorted(set(values)))


def _record_evidence_documents(
    record: BrainRecordEnvelope,
    episode_evidence: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    identifiers = set(record.provenance_source_ids)
    for mapping in _payload_mappings(record.payload):
        for key, value in mapping.items():
            if not key.endswith(("_id", "_ids")):
                continue
            if not any(token in key for token in ("source", "fact", "inference", "observation")):
                continue
            if isinstance(value, str) and value.strip():
                identifiers.add(value.strip())
            elif isinstance(value, list):
                identifiers.update(
                    item.strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                )
    return tuple(
        sorted(
            {
                text
                for identifier in identifiers
                for text in episode_evidence.get(identifier, ())
            }
        )
    )


def _structural_value(value: Any) -> Any | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(float(value)) else None
    if isinstance(value, list):
        normalized = [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
        return normalized or None
    return None


def build_memory_cells(
    records: list[BrainRecordEnvelope],
    vectors: list[list[float]],
    *,
    max_available_from: datetime,
    documents: dict[str, str] | None = None,
) -> MemoryCellBuild:
    """Build O(n*d) primary cells and bounded adjacent secondary memberships."""

    _validate_inputs(records, vectors, max_available_from=max_available_from)
    documents = documents or {
        record.record_id: record_memory_document(record) for record in records
    }
    if set(documents) != {record.record_id for record in records}:
        raise ValueError("memory documents must exactly cover indexed records")
    unresolved_reasoning = [
        record.record_id
        for record in records
        if record_routing_metadata(record).routing_disposition == "REASONING"
        and MISSING_STRUCTURAL_CONTEXT in documents[record.record_id]
    ]
    if unresolved_reasoning:
        raise ValueError(
            "reasoning records require structural evidence documents: "
            + ", ".join(unresolved_reasoning[:10])
        )
    if not records:
        return MemoryCellBuild([], [], {}, {}, documents)

    signatures_and_margins = vector_signatures_and_margins(vectors)
    signatures = [item[0] for item in signatures_and_margins]
    signature_margins = [item[1] for item in signatures_and_margins]
    cell_ids_by_signature = {
        signature: _cell_id(signature) for signature in sorted(set(signatures))
    }
    primary_vectors: dict[str, list[list[float]]] = defaultdict(list)
    primary_records: dict[str, list[BrainRecordEnvelope]] = defaultdict(list)
    reasoning_vectors: dict[str, list[list[float]]] = defaultdict(list)
    for record, vector, signature in zip(records, vectors, signatures, strict=True):
        cell_id = cell_ids_by_signature[signature]
        primary_vectors[cell_id].append(vector)
        primary_records[cell_id].append(record)
        if record_routing_metadata(record).routing_disposition == "REASONING":
            reasoning_vectors[cell_id].append(vector)

    centroids = {
        cell_id: _normalized_quantized_vectors(cell_vectors)
        for cell_id, cell_vectors in primary_vectors.items()
    }
    reasoning_centroids = {
        cell_id: _normalized_quantized_vectors(cell_vectors)
        for cell_id, cell_vectors in reasoning_vectors.items()
    }
    memberships: list[MemoryCellMembership] = []
    secondary_counts: dict[str, int] = defaultdict(int)
    for record, vector, signature, margins in zip(
        records,
        vectors,
        signatures,
        signature_margins,
        strict=True,
    ):
        primary_cell_id = cell_ids_by_signature[signature]
        secondary_cell_ids = _secondary_cells(
            signature,
            margins=margins,
            cell_ids_by_signature=cell_ids_by_signature,
        )
        for cell_id in secondary_cell_ids:
            secondary_counts[cell_id] += 1
        routing = record_routing_metadata(record)
        memberships.append(
            MemoryCellMembership(
                record_id=record.record_id,
                primary_cell_id=primary_cell_id,
                secondary_cell_ids=secondary_cell_ids,
                independent_unit_id=record_independent_unit_id(record),
                membership_score=max(
                    0.0,
                    min(1.0, _cosine_similarity(vector, centroids[primary_cell_id])),
                ),
                membership_rule=MEMORY_CELL_MEMBERSHIP_RULE,
                membership_rule_version=MEMORY_CELL_MEMBERSHIP_RULE_VERSION,
                available_from=record.available_from,
                routing_disposition=routing.routing_disposition,
            )
        )

    cells = []
    for signature, cell_id in sorted(cell_ids_by_signature.items()):
        cell_records = primary_records[cell_id]
        cells.append(
            MemoryCellEntry(
                cell_id=cell_id,
                signature=signature,
                primary_member_count=len(cell_records),
                reasoning_member_count=len(reasoning_vectors[cell_id]),
                secondary_member_count=secondary_counts[cell_id],
                independent_unit_count=len(
                    {record_independent_unit_id(record) for record in cell_records}
                ),
                centroid_sha256=sha256_text(canonical_json(centroids[cell_id])),
                reasoning_centroid_sha256=(
                    sha256_text(canonical_json(reasoning_centroids[cell_id]))
                    if cell_id in reasoning_centroids
                    else None
                ),
            )
        )
    return MemoryCellBuild(
        memberships=sorted(memberships, key=lambda item: item.record_id),
        cells=cells,
        centroids=centroids,
        reasoning_centroids=reasoning_centroids,
        documents=documents,
    )


def record_independent_unit_id(record: BrainRecordEnvelope) -> str:
    """Create a stable counting key from structural record fields only."""

    mappings = _payload_mappings(record.payload)
    ticker = _first_text(mappings, _TICKER_KEYS)
    company = _first_text(mappings, _COMPANY_KEYS)
    theme = _first_text(mappings, _THEME_KEYS)
    event = _first_text(mappings, _UNIT_EVENT_KEYS)
    pair = _first_text(mappings, _PAIR_KEYS)
    day = record.trade_date.isoformat()
    issuer = ticker.upper() if ticker else _normalized_key(company) if company else None
    record_type = record.record_type.lower()
    if "leader_preference_pair" in record_type:
        if theme and pair:
            return (
                f"THEME_DAY_PAIR:{day}:{_normalized_key(theme)}:"
                f"{_normalized_key(pair)}"
            )
        return f"UNSUPPORTED_RECORD:{record.record_id}"
    if "theme_formation" in record_type:
        if theme:
            return f"THEME_DAY:{day}:{_normalized_key(theme)}"
        return f"UNSUPPORTED_RECORD:{record.record_id}"
    if "beneficiary" in record_type:
        if theme and issuer:
            return f"THEME_DAY_TICKER_DAY:{day}:{_normalized_key(theme)}:{issuer}"
        return f"UNSUPPORTED_RECORD:{record.record_id}"
    if "newsless_or_unexplained" in record_type:
        if issuer:
            return f"TICKER_DAY:{day}:{issuer}"
        return f"UNSUPPORTED_RECORD:{record.record_id}"
    if "direct_event" in record_type:
        if event and issuer:
            return f"EVENT_ISSUER_DAY:{day}:{_normalized_key(event)}:{issuer}"
        return f"UNSUPPORTED_RECORD:{record.record_id}"
    if "issuer_day" in record_type:
        if issuer:
            return f"ISSUER_DAY:{day}:{issuer}"
        return f"UNSUPPORTED_RECORD:{record.record_id}"
    if theme:
        return f"THEME_DAY:{day}:{_normalized_key(theme)}"
    if event and issuer:
        return f"EVENT_ISSUER_DAY:{day}:{_normalized_key(event)}:{issuer}"
    if issuer:
        return f"ISSUER_DAY:{day}:{issuer}"
    if event:
        return f"EVENT_DAY:{day}:{_normalized_key(event)}"
    return f"UNSUPPORTED_RECORD:{record.record_id}"


def independent_unit_type(independent_unit_id: str) -> str | None:
    prefixes = {
        "EVENT_ISSUER_DAY": "event-issuer-day",
        "ISSUER_DAY": "issuer-day",
        "THEME_DAY_TICKER_DAY": "theme-day-ticker-day",
        "THEME_DAY_PAIR": "theme-day-pair",
        "THEME_DAY": "theme-day",
        "TICKER_DAY": "ticker-day",
    }
    for prefix, unit_type in prefixes.items():
        if independent_unit_id.startswith(f"{prefix}:"):
            return unit_type
    return None


def _validate_inputs(
    records: list[BrainRecordEnvelope],
    vectors: list[list[float]],
    *,
    max_available_from: datetime,
) -> None:
    if len(records) != len(vectors):
        raise ValueError("record and embedding counts must match")
    if len({record.record_id for record in records}) != len(records):
        raise ValueError("memory cell source record identifiers must be unique")
    if max_available_from.tzinfo is None:
        raise ValueError("memory cell snapshot cutoff must be timezone-aware")
    if any(
        as_kst(record.available_from) > as_kst(max_available_from)
        for record in records
    ):
        raise ValueError("memory cell source contains a future record")
    dimensions = {len(vector) for vector in vectors}
    if records and (len(dimensions) != 1 or 0 in dimensions):
        raise ValueError("memory cell embeddings require one non-zero dimension")
    if any(not math.isfinite(value) for vector in vectors for value in vector):
        raise ValueError("memory cell embeddings must be finite")


def _vector_signature_and_margins(vector: list[float]) -> tuple[str, list[float]]:
    return vector_signatures_and_margins([vector])[0]


def vector_signatures_and_margins(
    vectors: list[list[float]],
) -> list[tuple[str, list[float]]]:
    """Compute LSH projections in bounded float32 batches, not Python loops."""

    if not vectors:
        return []
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] < 1 or not np.isfinite(matrix).all():
        raise ValueError("memory cell embeddings must be finite non-empty vectors")
    weights = _projection_matrix(int(matrix.shape[1]))
    results = []
    for vector in matrix:
        margins = np.round(weights @ vector, decimals=6).astype(np.float32)
        row = [float(value) for value in margins]
        results.append(
            ("".join("1" if value >= 0.0 else "0" for value in row), row)
        )
    return results


@lru_cache(maxsize=8)
def _projection_matrix(dimensions: int) -> npt.NDArray[np.float32]:
    if dimensions < 1:
        raise ValueError("projection dimensions must be positive")
    return np.asarray(
        [
            [
                _projection_weight(bit_index, dimension_index)
                for dimension_index in range(dimensions)
            ]
            for bit_index in range(MEMORY_CELL_SIGNATURE_BITS)
        ],
        dtype=np.float32,
    )


def _projection_weight(bit_index: int, dimension_index: int) -> float:
    digest = sha256_text(
        f"{MEMORY_CELL_CLUSTERING_VERSION}|{bit_index}|{dimension_index}"
    )
    return 1.0 if int(digest[:2], 16) % 2 == 0 else -1.0


def _cell_id(signature: str) -> str:
    digest = sha256_text(f"{MEMORY_CELL_CLUSTERING_VERSION}|{signature}")[:16]
    return f"CELL-{digest}"


def _secondary_cells(
    signature: str,
    *,
    margins: list[float],
    cell_ids_by_signature: dict[str, str],
) -> list[str]:
    bit_count = len(signature)
    candidates: list[tuple[float, str]] = []
    for index in range(bit_count):
        neighbor = list(signature)
        neighbor[index] = "0" if neighbor[index] == "1" else "1"
        neighbor_signature = "".join(neighbor)
        cell_id = cell_ids_by_signature.get(neighbor_signature)
        if cell_id is not None:
            candidates.append((abs(margins[index]), cell_id))
    for first_index in range(bit_count):
        for second_index in range(first_index + 1, bit_count):
            neighbor = list(signature)
            for index in (first_index, second_index):
                neighbor[index] = "0" if neighbor[index] == "1" else "1"
            cell_id = cell_ids_by_signature.get("".join(neighbor))
            if cell_id is not None:
                candidates.append(
                    (
                        abs(margins[first_index]) + abs(margins[second_index]),
                        cell_id,
                    )
                )
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [cell_id for _margin, cell_id in candidates[:MEMORY_CELL_SECONDARY_LIMIT]]


def _normalized_quantized_vectors(vectors: list[list[float]]) -> list[float]:
    matrix = np.asarray(vectors, dtype=np.float32)
    total = np.rint(matrix * MEMORY_VECTOR_QUANTIZATION_SCALE).astype(
        np.int64
    ).sum(axis=0, dtype=np.int64)
    return normalized_quantized_sum(total, len(vectors))


def normalized_quantized_sum(
    total: npt.NDArray[np.int64],
    count: int,
) -> list[float]:
    mean = total.astype(np.float64) / (
        float(count) * MEMORY_VECTOR_QUANTIZATION_SCALE
    )
    magnitude = float(np.linalg.norm(mean))
    if magnitude:
        mean = mean / magnitude
    return [float(value) for value in mean.astype(np.float32)]


def _float32(value: float) -> float:
    return float(struct.unpack("!f", struct.pack("!f", value))[0])


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    left_magnitude = math.sqrt(sum(value * value for value in left))
    right_magnitude = math.sqrt(sum(value * value for value in right))
    if left_magnitude == 0.0 or right_magnitude == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_magnitude * right_magnitude
    )


def _payload_mappings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mappings = [payload]
    queue = [payload]
    seen = {id(payload)}
    while queue:
        current = queue.pop(0)
        for nested in current.values():
            candidates = (
                [nested]
                if isinstance(nested, dict)
                else [item for item in nested if isinstance(item, dict)]
                if isinstance(nested, list)
                else []
            )
            for candidate in candidates:
                if id(candidate) not in seen:
                    seen.add(id(candidate))
                    mappings.append(candidate)
                    queue.append(candidate)
    return mappings


def _first_text(mappings: list[dict[str, Any]], keys: tuple[str, ...]) -> str | None:
    for mapping in mappings:
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _normalized_key(value: str) -> str:
    return "_".join(value.strip().upper().split())
