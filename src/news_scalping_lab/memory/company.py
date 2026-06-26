"""File-backed company memory candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.models import Candidate, CompanyMemory, Provenance
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.utils import (
    as_kst,
    file_sha256,
    is_available_as_of,
    parse_datetime,
    read_json,
    stable_id,
    write_json,
)

GENERIC_COMPANY_NAMES = {
    "BENEFICIARY_DISCOVERY_REQUIRED",
    "D_MINUS_ONE_LEADER_REVIEW",
    "UNVERIFIED_ENTITY",
}


@dataclass(frozen=True)
class CompanyMemoryDeltaApplyResult:
    processed_record_count: int
    written_count: int
    written_paths: list[Path]
    skipped_future_record_ids: list[str]
    skipped_invalid_record_ids: list[str]


class CompanyMemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / "memory" / "company_memory"
        self.dir.mkdir(parents=True, exist_ok=True)

    def upsert_from_candidates(
        self,
        candidates: list[Candidate],
        *,
        prediction_path: Path,
        known_at: datetime,
    ) -> list[Path]:
        written: list[Path] = []
        prediction_uri = _relative_uri(prediction_path, self.root)
        prediction_hash = file_sha256(prediction_path)
        for candidate in candidates:
            if not _is_company_memory_candidate(candidate):
                continue
            memory = self._memory_from_candidate(
                candidate,
                prediction_uri=prediction_uri,
                prediction_hash=prediction_hash,
                known_at=known_at,
            )
            path = self._path_for_candidate_record(
                memory,
                prediction_uri=prediction_uri,
                prediction_hash=prediction_hash,
            )
            existing = self._read_existing(path)
            merged = _merge_company_memory(existing, memory) if existing else memory
            write_json(path, merged.model_dump(mode="json"))
            written.append(path)
        return written

    def apply_record_deltas(
        self,
        *,
        as_of: datetime | None = None,
    ) -> CompanyMemoryDeltaApplyResult:
        records = [
            record
            for record in BrainRecordStore(self.root).list_records()
            if record.record_type == "company_memory_delta"
        ]
        return self.apply_record_delta_records(records, as_of=as_of)

    def apply_record_delta_records(
        self,
        records: list[BrainRecordEnvelope],
        *,
        as_of: datetime | None = None,
    ) -> CompanyMemoryDeltaApplyResult:
        written: list[Path] = []
        skipped_future: list[str] = []
        skipped_invalid: list[str] = []
        cutoff = as_kst(as_of) if as_of is not None else None
        for record in sorted(records, key=lambda item: item.record_id):
            if record.record_type != "company_memory_delta":
                continue
            memory = self._memory_from_delta_record(record)
            if memory is None:
                skipped_invalid.append(record.record_id)
                continue
            if cutoff is not None and (
                not is_available_as_of(record.available_from, cutoff)
                or not is_available_as_of(memory.known_at, cutoff)
            ):
                skipped_future.append(record.record_id)
                continue
            path = self._path_for_delta_record(record)
            write_json(path, memory.model_dump(mode="json"))
            written.append(path)
        return CompanyMemoryDeltaApplyResult(
            processed_record_count=len(records),
            written_count=len(written),
            written_paths=written,
            skipped_future_record_ids=skipped_future,
            skipped_invalid_record_ids=skipped_invalid,
        )

    def _memory_from_candidate(
        self,
        candidate: Candidate,
        *,
        prediction_uri: str,
        prediction_hash: str,
        known_at: datetime,
    ) -> CompanyMemory:
        provenance = Provenance(
            source_id=stable_id("SRC", prediction_uri, candidate.company_name),
            source_type="blind_analysis_company_memory_candidate",
            uri=prediction_uri,
            content_sha256=prediction_hash,
            excerpt=candidate.thesis,
            observed_at=known_at,
        )
        return CompanyMemory(
            ticker=candidate.ticker,
            company_name=candidate.company_name,
            aliases=[candidate.company_name],
            business_descriptions=[
                "Candidate generated from pre-cutoff news; verify listing, ownership, business, and relation."
            ],
            supply_chain_roles=candidate.causal_chain,
            prior_market_narratives=[candidate.thesis, candidate.why_now],
            contradictory_relations=candidate.counterarguments,
            known_at=known_at,
            provenance=[provenance],
        )

    def _memory_from_delta_record(
        self,
        record: BrainRecordEnvelope,
    ) -> CompanyMemory | None:
        payload = record.payload
        ticker = _first_string(payload, "ticker", "ticker_symbol", "symbol")
        company_name = _first_string(payload, "company_name", "issuer_name", "name")
        if not ticker or not company_name:
            return None
        try:
            known_at = _effective_delta_known_at(record)
        except ValueError:
            return None
        records_path = self.root / "memory" / "records" / f"{record.episode_id}.jsonl"
        uri = _relative_uri(records_path, self.root)
        content_sha256 = file_sha256(records_path) if records_path.exists() else None
        provenance = Provenance(
            source_id=stable_id("SRC", "company_memory_delta", record.record_id),
            source_type="company_memory_delta_record",
            uri=uri,
            content_sha256=content_sha256,
            excerpt=_first_string(payload, "statement", "summary", "description"),
            observed_at=known_at,
        )
        return CompanyMemory(
            ticker=ticker,
            company_name=company_name,
            aliases=_string_list(payload.get("aliases")),
            business_descriptions=_string_list(
                payload.get("business_descriptions")
                or payload.get("business_description")
                or payload.get("business_lines")
            ),
            locations=_string_list(payload.get("locations")),
            customers=_string_list(payload.get("customers")),
            supply_chain_roles=_string_list(
                payload.get("supply_chain_roles")
                or payload.get("relation_roles")
                or payload.get("roles")
            ),
            prior_market_narratives=_string_list(
                payload.get("prior_market_narratives")
                or payload.get("market_narratives")
                or payload.get("narratives")
            ),
            prior_leader_occurrences=_string_list(
                payload.get("prior_leader_occurrences")
                or payload.get("leader_occurrences")
            ),
            contradictory_relations=_string_list(
                payload.get("contradictory_relations")
                or payload.get("conflicting_relations")
                or payload.get("contradictions")
            ),
            known_at=known_at,
            provenance=[provenance],
        )

    def _path_for_candidate_record(
        self,
        memory: CompanyMemory,
        *,
        prediction_uri: str,
        prediction_hash: str,
    ) -> Path:
        key = stable_id(
            "CM",
            "blind_analysis_company_memory_candidate",
            memory.ticker,
            memory.company_name,
            prediction_uri,
            prediction_hash,
            memory.known_at.isoformat(),
            length=16,
        )
        return self.dir / f"{key}.json"

    def _path_for_delta_record(self, record: BrainRecordEnvelope) -> Path:
        key = stable_id(
            "CM",
            "company_memory_delta",
            record.record_id,
            record.normalized_payload_sha256,
            length=16,
        )
        return self.dir / f"{key}.json"

    def _read_existing(self, path: Path) -> CompanyMemory | None:
        if not path.exists():
            return None
        return CompanyMemory.model_validate(read_json(path))


def _is_company_memory_candidate(candidate: Candidate) -> bool:
    company_name = candidate.company_name.strip()
    if not company_name:
        return False
    return company_name not in GENERIC_COMPANY_NAMES


def _merge_company_memory(existing: CompanyMemory, incoming: CompanyMemory) -> CompanyMemory:
    return existing.model_copy(
        update={
            "aliases": _merged(existing.aliases, incoming.aliases),
            "business_descriptions": _merged(
                existing.business_descriptions, incoming.business_descriptions
            ),
            "locations": _merged(existing.locations, incoming.locations),
            "customers": _merged(existing.customers, incoming.customers),
            "supply_chain_roles": _merged(existing.supply_chain_roles, incoming.supply_chain_roles),
            "prior_market_narratives": _merged(
                existing.prior_market_narratives, incoming.prior_market_narratives
            ),
            "prior_leader_occurrences": _merged(
                existing.prior_leader_occurrences, incoming.prior_leader_occurrences
            ),
            "contradictory_relations": _merged(
                existing.contradictory_relations, incoming.contradictory_relations
            ),
            "known_at": min(existing.known_at, incoming.known_at),
            "provenance": _merged_provenance(existing.provenance, incoming.provenance),
        }
    )


def _merged_provenance(first: list[Provenance], second: list[Provenance]) -> list[Provenance]:
    by_source: dict[str, Provenance] = {}
    order: list[str] = []
    for item in [*first, *second]:
        key = item.source_id
        if key not in by_source:
            order.append(key)
        by_source[key] = item
    return [by_source[key] for key in order]


def _merged(first: list[str], second: list[str]) -> list[str]:
    values: list[str] = []
    for value in [*first, *second]:
        if value and value not in values:
            values.append(value)
    return values


def _effective_delta_known_at(record: BrainRecordEnvelope) -> datetime:
    raw_known_at = record.payload.get("known_at")
    known_at = record.available_from
    if isinstance(raw_known_at, str) and raw_known_at.strip():
        known_at = parse_datetime(raw_known_at)
    return max(as_kst(known_at), as_kst(record.available_from))


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip() and item.strip() not in values:
                values.append(item.strip())
        return values
    return []


def _relative_uri(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
