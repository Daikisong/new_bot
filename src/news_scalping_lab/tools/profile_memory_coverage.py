"""Profile streaming MemoryCoverageManifest creation at production corpus sizes."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from news_scalping_lab.context.memory_coverage import build_memory_coverage_manifest
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.utils import KST


@dataclass(frozen=True)
class _SyntheticRecord:
    record_id: str
    record_type: str
    episode_id: str
    trade_date: date
    available_from: datetime
    evidence_phase: str
    training_eligible: bool

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        del mode
        return {
            "schema_version": "nslab.brain_record_envelope.v1",
            "record_id": self.record_id,
            "record_type": self.record_type,
            "episode_id": self.episode_id,
            "trade_date": self.trade_date.isoformat(),
            "available_from": self.available_from.isoformat(),
            "training_target": "synthetic_profile",
            "evidence_phase": self.evidence_phase,
            "training_eligible": self.training_eligible,
            "eligibility_reason": "synthetic profile",
            "status": "accepted",
            "confidence_label": "high",
            "provenance_source_ids": [f"SRC-{self.record_id}"],
            "raw_payload_sha256": "a" * 64,
            "normalized_payload_sha256": "a" * 64,
            "typed_payload_status": "KNOWN_TYPED_PAYLOAD",
            "source_block": "brain_delta.jsonl",
            "source_line": 1,
            "payload": {"record_id": self.record_id},
        }


def _records(count: int, cutoff_at: datetime) -> Iterator[BrainRecordEnvelope]:
    for index in range(count):
        synthetic = _SyntheticRecord(
            record_id=f"REC-{index:09d}",
            record_type="supervised_direct_event_case",
            episode_id=f"EP-{index // 500:07d}",
            trade_date=cutoff_at.date() - timedelta(days=1),
            available_from=(
                cutoff_at - timedelta(minutes=1)
                if index % 10
                else cutoff_at + timedelta(minutes=1)
            ),
            evidence_phase="POSTMORTEM",
            training_eligible=index % 3 != 0,
        )
        yield cast(BrainRecordEnvelope, synthetic)


def profile(counts: list[int]) -> dict[str, Any]:
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    measurements: list[dict[str, Any]] = []
    for count in counts:
        with tempfile.TemporaryDirectory(prefix=f"nslab-coverage-{count}-") as name:
            root = Path(name)
            tracemalloc.start()
            started = time.perf_counter()
            result = build_memory_coverage_manifest(
                root,
                records=_records(count, cutoff_at),
                cutoff_at=cutoff_at,
                run_id=f"RUN-PROFILE-{count}",
            )
            elapsed = time.perf_counter() - started
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            measurements.append(
                {
                    "record_count": count,
                    "available_record_count": result.manifest.available_record_count,
                    "elapsed_seconds": round(elapsed, 3),
                    "records_per_second": round(count / max(elapsed, 0.001), 1),
                    "peak_memory_mib": round(peak / (1024 * 1024), 2),
                    "manifest_bytes": (
                        root / result.manifest_path
                    ).stat().st_size,
                    "cache_hit": result.cache_hit,
                }
            )
    return {
        "schema_version": "nslab.memory_coverage_profile.v1",
        "streaming": True,
        "measurements": measurements,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--counts",
        nargs="+",
        type=int,
        default=[50_000, 200_000, 600_000],
    )
    args = parser.parse_args()
    print(json.dumps(profile(args.counts), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
