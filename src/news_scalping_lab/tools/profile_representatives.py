"""Compute-only bounded profile for Phase 6 representative selection."""

from __future__ import annotations

import argparse
import json
import time
import tracemalloc
from datetime import date, datetime, timedelta

from news_scalping_lab.memory.diversity import (
    _mmr_select,
    _stratified_candidate_pool,
    _unit_candidates,
)
from news_scalping_lab.memory.index import RepresentativeSourceRecord
from news_scalping_lab.retrieval.embedding import DeterministicHashEmbeddingProvider
from news_scalping_lab.utils import KST, canonical_json, sha256_text


def profile(record_count: int) -> dict[str, object]:
    cutoff = datetime(2030, 1, 20, tzinfo=KST)
    tracemalloc.start()
    started = time.perf_counter()
    member_rows = []
    unit_rows = []
    for index in range(record_count):
        record_id = f"REC-{index:07d}"
        unit_id = f"ISSUER_DAY:2030-01-{(index % 10) + 1:02d}:{index:06d}"
        trade_date = date(2030, 1, 10) - timedelta(days=index % 10)
        high_return = 15.0 if index % 3 == 0 else -2.0 if index % 3 == 1 else 7.0
        member_rows.append(
            {
                "record_id": record_id,
                "independent_unit_id": unit_id,
                "trade_date": trade_date.isoformat(),
                "record_type": "supervised_issuer_day_case",
                "label_quality": "verified",
                "path_type": "DIRECT" if index % 2 else "INFERRED_NEW",
            }
        )
        unit_rows.append(
            {
                "independent_unit_id": unit_id,
                "trade_date": trade_date.isoformat(),
                "polarity": "POSITIVE" if high_return >= 5 else "NEGATIVE",
                "label_quality": "verified",
                "regime_clusters": ["RISK_ON" if index % 2 else "RISK_OFF"],
                "high_return_pct": high_return,
            }
        )
    candidates = _unit_candidates(member_rows, unit_rows, cutoff)
    pool = _stratified_candidate_pool(candidates, "POP-PROFILE")
    provider = DeterministicHashEmbeddingProvider()
    documents = [f"structural event evidence {index}" for index in range(len(pool))]
    vectors = provider.embed_texts(documents)
    source_by_id = {
        candidate.record_id: RepresentativeSourceRecord(
            record_id=candidate.record_id,
            embedding=tuple(vector),
            document=document,
            source_sha256=sha256_text(candidate.record_id),
            provenance_source_ids=(f"SRC-{candidate.record_id}",),
        )
        for candidate, vector, document in zip(pool, vectors, documents, strict=True)
    }
    query_vector = provider.embed_texts(["structural event evidence"])[0]
    population_strata: dict[str, set[str]] = {}
    for candidate in candidates:
        for stratum in candidate.strata:
            population_strata.setdefault(stratum, set()).add(
                candidate.independent_unit_id
            )
    selected = _mmr_select(
        pool,
        source_by_id,
        query_vector,
        target_selected_record_count=16,
        population_strata=population_strata,
    )
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "schema_version": "nslab.representative_profile.v1",
        "record_count": record_count,
        "candidate_pool_count": len(pool),
        "selected_record_count": len(selected),
        "elapsed_seconds": round(elapsed, 6),
        "peak_working_set_mib": round(peak / (1024 * 1024), 3),
        "selection_sha256": sha256_text(
            canonical_json([row.model_dump(mode="json") for row in selected])
        ),
        "scope": "compute_only_including_synthetic_member_and_unit_rows",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=50_000)
    args = parser.parse_args()
    if args.records < 1:
        raise ValueError("records must be positive")
    print(json.dumps(profile(args.records), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
