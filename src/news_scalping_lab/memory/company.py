"""File-backed company memory candidates."""

from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.models import Candidate, CompanyMemory, Provenance
from news_scalping_lab.contracts.production import ProductionCompanyMemoryAttestation
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    is_available_as_of,
    now_kst,
    parse_datetime,
    read_json,
    sha256_text,
    stable_id,
    write_json,
)

GENERIC_COMPANY_NAMES = {
    "BENEFICIARY_DISCOVERY_REQUIRED",
    "D_MINUS_ONE_LEADER_REVIEW",
    "UNVERIFIED_ENTITY",
}


def production_company_memory_attestation_required(root: Path) -> bool:
    return _production_release_id(root) is not None


def _production_release_id(root: Path) -> str | None:
    project_root = root.resolve()
    release_dir = project_root.parent
    if not (
        project_root.name == "project"
        and release_dir.name.startswith("P9REL-")
        and release_dir.parent.name == "releases"
        and (release_dir / "production_release_manifest.json").is_file()
    ):
        return None
    return release_dir.name


def _validate_attestation_key(key_value: str | None) -> None:
    if key_value is None or len(key_value.encode("utf-8")) < 32:
        raise ValueError(
            "production company memory attestation key must be at least 32 bytes"
        )


@dataclass(frozen=True)
class CompanyMemoryDeltaApplyResult:
    processed_record_count: int
    written_count: int
    written_paths: list[Path]
    skipped_future_record_ids: list[str]
    skipped_invalid_record_ids: list[str]


class CompanyMemoryStore:
    def __init__(
        self,
        root: Path,
        *,
        create: bool = True,
        directory: Path | None = None,
    ) -> None:
        self.root = root
        self.dir = directory or root / "memory" / "company_memory"
        if directory is not None:
            try:
                self.dir.resolve().relative_to(root.resolve())
            except ValueError as exc:
                raise ValueError("company memory directory escapes the project") from exc
        if create:
            self.dir.mkdir(parents=True, exist_ok=True)

    def upsert_from_candidates(
        self,
        candidates: list[Candidate],
        *,
        prediction_path: Path,
        known_at: datetime,
        attestation_key: str | None = None,
    ) -> list[Path]:
        if production_company_memory_attestation_required(self.root):
            _validate_attestation_key(attestation_key)
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
            merged = merged.model_copy(update={"production_attestation": None})
            if attestation_key is None:
                write_json(path, merged.model_dump(mode="json"))
            else:
                attested = self._attested_candidate_memory(
                    merged,
                    path=path,
                    prediction_path=prediction_path,
                    prediction_hash=prediction_hash,
                    known_at=known_at,
                    key_value=attestation_key,
                )
                _write_json_atomic(path, attested.model_dump(mode="json"))
            written.append(path)
        return written

    def production_integrity_errors(
        self,
        *,
        attestation_key: str | None,
    ) -> list[str]:
        try:
            _validate_attestation_key(attestation_key)
        except ValueError as exc:
            return [f"production_company_memory_attestation_key_invalid:{exc}"]
        assert attestation_key is not None
        errors: list[str] = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                memory = CompanyMemory.model_validate(read_json(path))
            except (OSError, ValueError):
                errors.append(
                    f"production_company_memory_invalid:{path.name}"
                )
                continue
            source_types = {
                provenance.source_type for provenance in memory.provenance
            }
            if source_types == {"company_memory_delta_record"}:
                if memory.production_attestation is not None:
                    errors.append(
                        "production_company_memory_record_attestation_unexpected:"
                        f"{path.name}"
                    )
                continue
            error = self.candidate_attestation_error(
                path,
                memory=memory,
                key_value=attestation_key,
            )
            if error is not None:
                errors.append(error)
        return sorted(set(errors))

    def candidate_attestation_error(
        self,
        path: Path,
        *,
        memory: CompanyMemory,
        key_value: str,
    ) -> str | None:
        try:
            attestation = ProductionCompanyMemoryAttestation.model_validate(
                memory.production_attestation
            )
        except ValueError:
            return f"production_company_memory_attestation_invalid:{path.name}"
        try:
            memory_relative = path.resolve().relative_to(
                self.root.resolve()
            ).as_posix()
            prediction_path = (
                self.root.resolve() / attestation.prediction_artifact_path
            ).resolve()
        except ValueError:
            return f"production_company_memory_attestation_path_invalid:{path.name}"
        if not _canonical_production_prediction_path(
            self.root,
            prediction_path,
        ):
            return f"production_company_memory_attestation_path_invalid:{path.name}"
        if attestation.memory_artifact_path != memory_relative:
            return f"production_company_memory_attestation_memory_path_mismatch:{path.name}"
        if attestation.release_id != _production_release_id(self.root):
            return f"production_company_memory_attestation_release_mismatch:{path.name}"
        if not prediction_path.is_file():
            return f"production_company_memory_prediction_missing:{path.name}"
        memory_payload_sha256 = sha256_text(
            canonical_json(
                memory.model_dump(
                    mode="json",
                    exclude={"production_attestation"},
                )
            )
        )
        if attestation.memory_payload_sha256 != memory_payload_sha256:
            return f"production_company_memory_attestation_memory_hash_mismatch:{path.name}"
        if attestation.prediction_sha256 != file_sha256(prediction_path):
            return f"production_company_memory_attestation_prediction_hash_mismatch:{path.name}"
        if as_kst(attestation.known_at) != as_kst(memory.known_at):
            return f"production_company_memory_attestation_known_at_mismatch:{path.name}"
        if as_kst(attestation.issued_at) > now_kst() + timedelta(minutes=5):
            return f"production_company_memory_attestation_issued_in_future:{path.name}"
        if attestation.key_id != sha256_text(key_value)[:16]:
            return f"production_company_memory_attestation_key_mismatch:{path.name}"
        commitment = sha256_text(
            canonical_json(
                attestation.model_dump(
                    mode="json",
                    exclude={"commitment_sha256", "signature"},
                )
            )
        )
        signature = hmac.new(
            key_value.encode("utf-8"),
            commitment.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if (
            attestation.commitment_sha256 != commitment
            or not hmac.compare_digest(attestation.signature, signature)
        ):
            return f"production_company_memory_attestation_signature_invalid:{path.name}"
        return None

    def _attested_candidate_memory(
        self,
        memory: CompanyMemory,
        *,
        path: Path,
        prediction_path: Path,
        prediction_hash: str,
        known_at: datetime,
        key_value: str,
    ) -> CompanyMemory:
        _validate_attestation_key(key_value)
        try:
            memory_relative = path.resolve().relative_to(
                self.root.resolve()
            ).as_posix()
            prediction_relative = prediction_path.resolve().relative_to(
                self.root.resolve()
            ).as_posix()
        except ValueError as exc:
            raise ValueError(
                "production company memory source escapes project root"
            ) from exc
        issued_at = now_kst()
        release_id = _production_release_id(self.root)
        if release_id is None:
            raise ValueError(
                "production company memory attestation requires a release project"
            )
        if not _canonical_production_prediction_path(
            self.root,
            prediction_path,
        ):
            raise ValueError(
                "production company memory requires an immutable run prediction"
            )
        unsigned = {
            "schema_version": "nslab.production_company_memory_attestation.v1",
            "algorithm": "HMAC-SHA256",
            "issued_at": issued_at.isoformat(),
            "key_id": sha256_text(key_value)[:16],
            "release_id": release_id,
            "memory_artifact_path": memory_relative,
            "memory_payload_sha256": sha256_text(
                canonical_json(
                    memory.model_dump(
                        mode="json",
                        exclude={"production_attestation"},
                    )
                )
            ),
            "prediction_artifact_path": prediction_relative,
            "prediction_sha256": prediction_hash,
            "known_at": as_kst(known_at).isoformat(),
        }
        commitment = sha256_text(canonical_json(unsigned))
        attestation = ProductionCompanyMemoryAttestation(
            **unsigned,
            commitment_sha256=commitment,
            signature=hmac.new(
                key_value.encode("utf-8"),
                commitment.encode("ascii"),
                hashlib.sha256,
            ).hexdigest(),
        )
        return memory.model_copy(
            update={
                "production_attestation": attestation.model_dump(mode="json")
            }
        )

    def apply_record_deltas(
        self,
        *,
        as_of: datetime | None = None,
    ) -> CompanyMemoryDeltaApplyResult:
        all_records = BrainRecordStore(self.root).list_records()
        records = [
            record
            for record in all_records
            if record.record_type == "company_memory_delta"
        ]
        return self.apply_record_delta_records(
            records,
            as_of=as_of,
            identity_records=all_records,
        )

    def apply_record_delta_records(
        self,
        records: list[BrainRecordEnvelope],
        *,
        as_of: datetime | None = None,
        identity_records: list[BrainRecordEnvelope] | None = None,
    ) -> CompanyMemoryDeltaApplyResult:
        written: list[Path] = []
        skipped_future: list[str] = []
        skipped_invalid: list[str] = []
        cutoff = as_kst(as_of) if as_of is not None else None
        identity_index = _company_identity_index(
            identity_records if identity_records is not None else records,
            target_records=records,
        )
        for record in sorted(records, key=lambda item: item.record_id):
            if record.record_type != "company_memory_delta":
                continue
            memory = self._memory_from_delta_record(
                record,
                identity_index=identity_index,
            )
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
            payload = memory.model_dump(mode="json")
            try:
                observed_payload = read_json(path) if path.is_file() else None
            except (OSError, ValueError):
                observed_payload = None
            if observed_payload != payload:
                if production_company_memory_attestation_required(self.root):
                    raise ValueError(
                        "active production record-derived company memory "
                        f"is missing or differs from its sealed projection: {path.name}"
                    )
                _write_json_atomic(path, payload)
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
            available_from=known_at,
            known_at=known_at,
            provenance=[provenance],
        )

    def _memory_from_delta_record(
        self,
        record: BrainRecordEnvelope,
        *,
        identity_index: dict[tuple[str, str], list[BrainRecordEnvelope]],
    ) -> CompanyMemory | None:
        payload = record.payload
        ticker = _first_string(payload, "ticker", "ticker_symbol", "symbol")
        company_name = _first_string(payload, "company_name", "issuer_name", "name")
        if ticker and not company_name:
            company_name = _company_name_from_identity_records(
                record,
                ticker=ticker,
                identity_index=identity_index,
            )
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
            available_from=record.available_from,
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
            "available_from": min(existing.available_from, incoming.available_from),
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


def _company_identity_index(
    records: list[BrainRecordEnvelope],
    *,
    target_records: list[BrainRecordEnvelope],
) -> dict[tuple[str, str], list[BrainRecordEnvelope]]:
    """Index only identities needed by incomplete company-memory deltas."""

    target_keys = {
        (record.episode_id, ticker)
        for record in target_records
        for ticker in [_first_string(record.payload, "ticker", "ticker_symbol", "symbol")]
        if ticker is not None
        and _first_string(record.payload, "company_name", "issuer_name", "name") is None
    }
    index: dict[tuple[str, str], list[BrainRecordEnvelope]] = {}
    if not target_keys:
        return index
    for record in records:
        for ticker, company_name in _record_company_identities(record):
            key = (record.episode_id, ticker)
            if key not in target_keys or not company_name:
                continue
            index.setdefault(key, []).append(record)
    return index


def _company_name_from_identity_records(
    record: BrainRecordEnvelope,
    *,
    ticker: str,
    identity_index: dict[tuple[str, str], list[BrainRecordEnvelope]],
) -> str | None:
    """Resolve an omitted name from unambiguous, contemporaneous provenance."""

    source_ids = _record_source_ids(record)
    if not source_ids:
        return None
    try:
        target_known_at = _effective_delta_known_at(record)
    except ValueError:
        return None
    names: set[str] = set()
    for candidate in identity_index.get((record.episode_id, ticker), []):
        if candidate.record_id == record.record_id:
            continue
        if as_kst(candidate.available_from) > as_kst(record.available_from):
            continue
        candidate_known_at = _record_known_at(candidate)
        if candidate_known_at is None or candidate_known_at > target_known_at:
            continue
        if not (source_ids & _record_source_ids(candidate)):
            continue
        names.update(
            company_name
            for candidate_ticker, company_name in _record_company_identities(candidate)
            if candidate_ticker == ticker
        )
    return next(iter(names)) if len(names) == 1 else None


def _record_company_identities(record: BrainRecordEnvelope) -> set[tuple[str, str]]:
    containers = [record.payload]
    for key in ("payload", "D_outcome", "outcome", "issuer_day_outcome"):
        nested = record.payload.get(key)
        if isinstance(nested, dict):
            containers.append(nested)
    identities: set[tuple[str, str]] = set()
    for container in containers:
        ticker = _first_string(container, "ticker", "ticker_symbol", "symbol", "code")
        company_name = _first_string(
            container,
            "company_name",
            "issuer_name",
            "candidate_company_name",
            "name",
        )
        if ticker and company_name:
            identities.add((ticker, company_name))
    return identities


def _record_source_ids(record: BrainRecordEnvelope) -> set[str]:
    return {
        *record.provenance_source_ids,
        *_string_list(record.payload.get("provenance_source_ids")),
        *_string_list(record.payload.get("source_ids")),
    }


def _record_known_at(record: BrainRecordEnvelope) -> datetime | None:
    raw_known_at = record.payload.get("known_at")
    if isinstance(raw_known_at, str) and raw_known_at.strip():
        try:
            return max(as_kst(parse_datetime(raw_known_at)), as_kst(record.available_from))
        except ValueError:
            return None
    return as_kst(record.available_from)


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


def _canonical_production_prediction_path(root: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    parts = relative.parts
    return (
        len(parts) == 5
        and parts[:3] == ("runs", "checkpoints", "output_artifacts")
        and bool(parts[3])
        and parts[4] == "blind_prediction.json"
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        write_json(temporary_path, payload)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
