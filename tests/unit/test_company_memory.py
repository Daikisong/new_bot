from __future__ import annotations

from datetime import datetime

from news_scalping_lab.contracts.models import Candidate, PathType
from news_scalping_lab.memory.company import CompanyMemoryStore
from news_scalping_lab.utils import KST, read_json, write_json


def _candidate(rank: int, company_name: str, path_type: PathType) -> Candidate:
    return Candidate(
        rank=rank,
        ticker="UNKNOWN",
        company_name=company_name,
        path_type=path_type,
        thesis=f"{company_name} can be investigated from the current event.",
        why_now="The candidate was generated before cutoff.",
        causal_chain=["current catalyst", path_type.value, "company verification"],
        counterarguments=["listing and relation must be checked"],
    )


def test_company_memory_persists_new_candidates_from_every_path_type(tmp_path) -> None:
    prediction_path = tmp_path / "predictions" / "2030-01-10.json"
    write_json(prediction_path, {"prediction_id": "PRED-company-memory"})
    known_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    candidates = [
        _candidate(1, "DirectCo", PathType.SINGLE_EVENT),
        _candidate(2, "BeneficiaryCo", PathType.THEME_BENEFICIARY),
        _candidate(3, "HybridCo", PathType.HYBRID),
        _candidate(4, "ContinuationCo", PathType.CONTINUATION),
        _candidate(5, "BENEFICIARY_DISCOVERY_REQUIRED", PathType.THEME_BENEFICIARY),
        _candidate(6, "D_MINUS_ONE_LEADER_REVIEW", PathType.CONTINUATION),
        _candidate(7, " ", PathType.SINGLE_EVENT),
    ]

    written = CompanyMemoryStore(tmp_path).upsert_from_candidates(
        candidates,
        prediction_path=prediction_path,
        known_at=known_at,
    )

    memories = [read_json(path) for path in sorted(written)]
    assert {memory["company_name"] for memory in memories} == {
        "DirectCo",
        "BeneficiaryCo",
        "HybridCo",
        "ContinuationCo",
    }
    assert all(memory["known_at"] == "2030-01-10T08:59:59+09:00" for memory in memories)
    assert all(
        memory["provenance"][0]["source_type"] == "blind_analysis_company_memory_candidate"
        for memory in memories
    )
    beneficiary_memory = next(
        memory for memory in memories if memory["company_name"] == "BeneficiaryCo"
    )
    assert "THEME_BENEFICIARY" in beneficiary_memory["supply_chain_roles"]
