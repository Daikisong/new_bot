"""Profile selected-cell population aggregation without an embedding provider."""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import os
import tempfile
from ctypes import wintypes
from datetime import date, datetime
from pathlib import Path
from time import perf_counter

from news_scalping_lab.memory.index import PopulationCellMember, ProductionMemoryIndex
from news_scalping_lab.memory.population import PopulationRetriever, _compute_population
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import DeterministicHashEmbeddingProvider
from news_scalping_lab.utils import KST, canonical_json, sha256_text


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _peak_working_set_bytes() -> int | None:
    if os.name != "nt":
        return None
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    get_process_memory_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    get_process_memory_info.restype = wintypes.BOOL
    get_current_process = ctypes.windll.kernel32.GetCurrentProcess
    get_current_process.restype = wintypes.HANDLE
    success = get_process_memory_info(
        get_current_process(),
        ctypes.byref(counters),
        counters.cb,
    )
    return int(counters.PeakWorkingSetSize) if success else None


def profile_population(record_count: int) -> dict[str, object]:
    started = perf_counter()
    members = [
        PopulationCellMember(
            record_id=f"REC-{index:07d}",
            independent_unit_id=f"ISSUER_DAY:2030-01-10:{index:07d}",
            independent_unit_type="issuer-day",
            primary_cell_id="CELL-1",
            matched_cell_ids=("CELL-1",),
            trade_date=date(2030, 1, 10),
            record_type="supervised_issuer_day_case",
            training_eligible=True,
            routing_disposition="REASONING",
            evidence_polarity="POSITIVE" if index % 4 == 0 else "NEGATIVE",
            label_quality="verified",
            memory_lanes=(
                ("positive_analogs",)
                if index % 4 == 0
                else ("negative_controls",)
            ),
            path_type="UNKNOWN",
            regime_cluster="UNKNOWN",
            high_return_pct=10.0 if index % 4 == 0 else -1.0,
            close_return_pct=5.0 if index % 4 == 0 else -0.5,
            upper_limit_touched=False,
            outcome_observed=True,
            sample_weight=1.0,
            high_return_status="VALID",
            close_return_status="VALID",
            upper_limit_status="VALID",
            sample_weight_status="DEFAULT",
        )
        for index in range(record_count)
    ]
    generated_seconds = perf_counter() - started
    compute_started = perf_counter()
    result = _compute_population(
        members,
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
        query_regime_cluster=None,
        seed=1,
    )
    compute_seconds = perf_counter() - compute_started
    peak = _peak_working_set_bytes()
    return {
        "schema_version": "nslab.population_profile.v1",
        "profile_scope": "compute_only",
        "record_count": record_count,
        "independent_unit_count": len(result.units),
        "cube_row_count": len(result.cube_rows),
        "member_generation_seconds": round(generated_seconds, 6),
        "population_compute_seconds": round(compute_seconds, 6),
        "total_seconds": round(perf_counter() - started, 6),
        "peak_working_set_bytes": peak,
        "peak_working_set_mib": round(peak / 1024 / 1024, 3) if peak else None,
        "passed": len(result.units) == record_count,
    }


def profile_population_end_to_end(record_count: int) -> dict[str, object]:
    started = perf_counter()
    cutoff = datetime(2030, 1, 11, tzinfo=KST)
    with tempfile.TemporaryDirectory(prefix="nslab-population-profile-") as directory:
        root = Path(directory)
        store = BrainRecordStore(root)
        records_path = store.records_dir / "NSLAB-20300110-PROFILE.jsonl"
        with records_path.open("w", encoding="utf-8", newline="\n") as handle:
            for offset in range(record_count):
                payload = {
                    "record_type": "supervised_issuer_day_case",
                    "training_eligible": True,
                    "ticker": f"{offset:07d}",
                    "company_name": "population profile issuer",
                    "title": "supply agreement confirmed",
                    "response_class": "POSITIVE" if offset % 4 == 0 else "NEGATIVE",
                    "high_return_pct": 10.0 if offset % 4 == 0 else -1.0,
                    "close_return_pct": 5.0 if offset % 4 == 0 else -0.5,
                    "upper_limit_touched": False,
                    "label_quality": "verified",
                }
                digest = sha256_text(canonical_json(payload))
                record = BrainRecordEnvelope(
                    record_id=f"REC-{offset:07d}",
                    record_type="supervised_issuer_day_case",
                    episode_id="NSLAB-20300110-PROFILE",
                    trade_date=date(2030, 1, 10),
                    available_from=datetime(2030, 1, 10, 20, 0, tzinfo=KST),
                    training_target="issuer_day_response",
                    evidence_phase="POSTMORTEM",
                    training_eligible=True,
                    status="supported",
                    confidence_label="high",
                    provenance_source_ids=[f"SRC-{offset:07d}"],
                    raw_payload_sha256=digest,
                    normalized_payload_sha256=digest,
                    typed_payload_status="KNOWN_TYPED_PAYLOAD",
                    payload=payload,
                )
                handle.write(record.model_dump_json() + "\n")
        store.rebuild_indexes()
        gc.collect()
        index = ProductionMemoryIndex(
            root,
            embedding_provider=DeterministicHashEmbeddingProvider(),
            production=False,
        )
        snapshot = index.build(as_of=cutoff)
        cell_ids = [
            str(json.loads(line)["cell_id"])
            for line in (root / snapshot.cell_entries.artifact_path)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        result = PopulationRetriever(root, memory_index=index).build(
            run_id="RUN-PROFILE",
            cluster_id="EVT-PROFILE",
            cutoff_at=cutoff,
            selected_cell_ids=cell_ids,
            independent_unit_type="issuer-day",
        )
        inspection = PopulationRetriever(root, memory_index=index).inspect(
            result.manifest_path
        )
        peak = _peak_working_set_bytes()
        return {
            "schema_version": "nslab.population_profile.v1",
            "profile_scope": "record_store_index_population_build_and_inspect",
            "record_count": record_count,
            "independent_unit_count": result.manifest.independent_unit_count,
            "cube_row_count": result.manifest.cube_rows.item_count,
            "total_seconds": round(perf_counter() - started, 6),
            "peak_working_set_bytes": peak,
            "peak_working_set_mib": round(peak / 1024 / 1024, 3) if peak else None,
            "passed": bool(inspection["passed"]),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, required=True)
    parser.add_argument("--end-to-end", action="store_true")
    args = parser.parse_args()
    if args.records < 1:
        raise ValueError("--records must be positive")
    profile = (
        profile_population_end_to_end(args.records)
        if args.end_to_end
        else profile_population(args.records)
    )
    print(json.dumps(profile, sort_keys=True))


if __name__ == "__main__":
    main()
