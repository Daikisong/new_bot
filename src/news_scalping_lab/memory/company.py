"""File-backed company memory candidates."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from news_scalping_lab.contracts.models import Candidate, CompanyMemory, PathType, Provenance
from news_scalping_lab.utils import file_sha256, read_json, stable_id, write_json

GENERIC_COMPANY_NAMES = {
    "BENEFICIARY_DISCOVERY_REQUIRED",
    "D_MINUS_ONE_LEADER_REVIEW",
    "UNVERIFIED_ENTITY",
}


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
            path = self._path_for(memory)
            existing = self._read_existing(path)
            merged = _merge_company_memory(existing, memory) if existing else memory
            write_json(path, merged.model_dump(mode="json"))
            written.append(path)
        return written

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

    def _path_for(self, memory: CompanyMemory) -> Path:
        key = stable_id("CM", memory.ticker, memory.company_name, length=16)
        return self.dir / f"{key}.json"

    def _read_existing(self, path: Path) -> CompanyMemory | None:
        if not path.exists():
            return None
        return CompanyMemory.model_validate(read_json(path))


def _is_company_memory_candidate(candidate: Candidate) -> bool:
    if candidate.path_type != PathType.SINGLE_EVENT:
        return False
    if candidate.company_name in GENERIC_COMPANY_NAMES:
        return False
    return bool(candidate.company_name.strip())


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
            "provenance": [*existing.provenance, *incoming.provenance],
        }
    )


def _merged(first: list[str], second: list[str]) -> list[str]:
    values: list[str] = []
    for value in [*first, *second]:
        if value and value not in values:
            values.append(value)
    return values


def _relative_uri(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
