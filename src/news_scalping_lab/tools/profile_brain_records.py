"""Profile the current brain-record corpus without changing production state."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from typing import Any

from news_scalping_lab.context.sweep import MemorySweeper
from news_scalping_lab.contracts.memory_context import (
    BrainMemoryPhase0Baseline,
    BrainRecordCorpusProfile,
    IndependentUnitProfile,
    LinearRetrievalBenchmark,
    NumericDistribution,
    RepairedCorpusInventoryProfile,
    SweepBurdenEstimate,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import (
    record_evidence_polarity,
    record_memory_lanes,
    record_outcome_payload,
    record_response_class,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.store import (
    LocalRetrievalStore,
    inspect_vector_index,
)
from news_scalping_lab.utils import canonical_json, file_sha256, sha256_text, write_json

DEFAULT_BENCHMARK_QUERIES = ("시장 뉴스", "기업 공시", "정책 산업")
DEFAULT_SWEEP_SHARD_SIZE = 20
_OUTCOME_HIGH_FIELDS = (
    "outcome_high_return_pct",
    "high_return_pct",
    "intraday_high_return_pct",
    "D_high_return_pct",
)
_OUTCOME_CLOSE_FIELDS = (
    "outcome_close_return_pct",
    "close_return_pct",
    "D_close_return_pct",
)
_OUTCOME_UPPER_FIELDS = ("upper_limit_touched", "is_upper_limit", "upper_limit")
_TICKER_FIELDS = (
    "ticker",
    "candidate_ticker",
    "outcome_ticker",
    "issuer_ticker",
    "chosen_leader_ticker",
    "missed_ticker",
    "corrected_ticker",
)
_COMPANY_FIELDS = (
    "company_name",
    "candidate_company_name",
    "outcome_company_name",
    "issuer_company_name",
    "chosen_leader_company_name",
    "company_name_on_D",
)
_EVENT_FIELDS = (
    "event_id",
    "event_ids",
    "source_event_ids",
    "direct_event_case_id",
)
_THEME_FIELDS = (
    "theme_id",
    "theme_ids",
    "retrospective_theme_id",
    "theme_name",
)
_PAIR_FIELDS = ("blind_pair_id", "pair_id")
_REGIME_FIELDS = (
    "market_regime",
    "market_regime_label",
    "regime",
    "regime_label",
    "regime_cluster",
)


def profile_brain_records(
    root: Path,
    *,
    accepted_only: bool = True,
    benchmark_queries: Sequence[str] = (),
    benchmark_repeats: int = 1,
    sweep_shard_size: int = DEFAULT_SWEEP_SHARD_SIZE,
) -> BrainRecordCorpusProfile:
    """Build a deterministic corpus profile plus an optional latency sample."""

    if benchmark_repeats < 1:
        raise ValueError("benchmark_repeats must be positive")
    if sweep_shard_size < 1:
        raise ValueError("sweep_shard_size must be positive")

    records = BrainRecordStore(root).list_records(accepted_only=accepted_only)
    record_counts = Counter(record.record_type for record in records)
    polarity_counts: Counter[str] = Counter()
    lane_counts: Counter[str] = Counter()
    crosstab: dict[str, Counter[str]] = {
        "eligible": Counter(),
        "ineligible": Counter(),
    }
    outcome_coverage: Counter[str] = Counter()
    trade_year_counts: Counter[str] = Counter()
    available_year_counts: Counter[str] = Counter()
    regime_counts: Counter[str] = Counter()
    payload_sizes: list[int] = []
    unit_keys: dict[str, list[str]] = {
        "event-issuer-day": [],
        "issuer-day": [],
        "theme-day": [],
        "theme-day-ticker-day": [],
        "theme-day-pair": [],
        "ticker-day": [],
    }

    for record in records:
        polarity = record_evidence_polarity(record).value
        polarity_counts[polarity] += 1
        eligibility_key = "eligible" if record.training_eligible else "ineligible"
        crosstab[eligibility_key][polarity] += 1
        lane_counts.update(record_memory_lanes(record))
        trade_year_counts[str(record.trade_date.year)] += 1
        available_year_counts[str(record.available_from.year)] += 1
        payload_sizes.append(len(canonical_json(record.payload).encode("utf-8")))
        _update_outcome_coverage(outcome_coverage, record)
        regime_counts[_record_regime(record.payload)] += 1
        _append_independent_unit_keys(unit_keys, record)

    serialized_record_bytes = sum(
        len(record.model_dump_json().encode("utf-8")) + 1 for record in records
    )
    corpus_manifest_sha256 = _corpus_manifest_sha256(records)
    sweep_burden = MemorySweeper(
        root, shard_episode_count=sweep_shard_size
    ).estimate_payload_burden()
    return BrainRecordCorpusProfile(
        source_root=root.resolve().as_posix(),
        accepted_only=accepted_only,
        corpus_manifest_sha256=corpus_manifest_sha256,
        record_count=len(records),
        episode_count=len({record.episode_id for record in records}),
        training_eligible_record_count=sum(
            1 for record in records if record.training_eligible
        ),
        known_typed_record_count=sum(
            1 for record in records if record.typed_payload_status == "KNOWN_TYPED_PAYLOAD"
        ),
        unknown_typed_record_count=sum(
            1
            for record in records
            if record.typed_payload_status == "UNKNOWN_TYPED_PAYLOAD"
        ),
        record_counts_by_type=_sorted_counts(record_counts),
        record_counts_by_polarity=_sorted_counts(polarity_counts),
        record_counts_by_lane=_sorted_counts(lane_counts),
        eligibility_polarity_crosstab={
            key: _sorted_counts(value) for key, value in sorted(crosstab.items())
        },
        outcome_field_coverage=_sorted_counts(outcome_coverage),
        independent_unit_profiles={
            key: _independent_unit_profile(values)
            for key, values in sorted(unit_keys.items())
        },
        payload_bytes=_numeric_distribution(payload_sizes),
        trade_year_counts=_sorted_counts(trade_year_counts),
        available_year_counts=_sorted_counts(available_year_counts),
        regime_counts=_sorted_counts(regime_counts),
        linear_retrieval=_benchmark_linear_retrieval(
            root,
            records,
            queries=benchmark_queries,
            repeats=benchmark_repeats,
        ),
        sweep_burden=SweepBurdenEstimate(
            shard_size=sweep_shard_size,
            accepted_episode_count=sweep_burden.accepted_episode_count,
            estimated_episode_shard_count=sweep_burden.episode_shard_count,
            estimated_record_shard_count=sweep_burden.record_shard_count,
            estimated_total_shard_count=(
                sweep_burden.episode_shard_count + sweep_burden.record_shard_count
            ),
            serialized_episode_artifact_bytes=sweep_burden.episode_artifact_bytes,
            serialized_record_artifact_bytes=sweep_burden.record_artifact_bytes,
            serialized_total_artifact_bytes=(
                sweep_burden.episode_artifact_bytes + sweep_burden.record_artifact_bytes
            ),
            estimated_episode_artifact_tokens=sweep_burden.episode_artifact_tokens,
            estimated_record_artifact_tokens=sweep_burden.record_artifact_tokens,
            estimated_total_artifact_tokens=(
                sweep_burden.episode_artifact_tokens
                + sweep_burden.record_artifact_tokens
            ),
            serialized_record_bytes=serialized_record_bytes,
            estimator_version="memory_sweeper_serialized_dry_run.v1",
        ),
    )


def profile_repaired_inventory(manifest_path: Path) -> RepairedCorpusInventoryProfile:
    """Summarize repair outputs from their immutable sequential manifest."""

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"repair manifest line {line_number} must be an object")
        rows.append({str(key): value for key, value in payload.items()})

    status_counts = Counter(_string_value(row.get("final_status"), "UNKNOWN") for row in rows)
    engine_counts = Counter(_string_value(row.get("engine_digest"), "UNKNOWN") for row in rows)
    year_counts = Counter(_filename_year(row) for row in rows)
    source_bytes = [_non_negative_int(row.get("byte_size")) for row in rows]
    repaired_bytes = [
        value
        for row in rows
        if (value := _optional_non_negative_int(row.get("repaired_byte_size")))
        is not None
    ]
    declared_record_rows = [
        row for row in rows if _optional_non_negative_int(row.get("record_count")) is not None
    ]
    ready_rows = [row for row in rows if row.get("ready_for_import") is True]
    non_ready_rows = [row for row in rows if row.get("ready_for_import") is not True]
    ready_declared_rows = [
        row
        for row in ready_rows
        if _optional_non_negative_int(row.get("record_count")) is not None
    ]
    return RepairedCorpusInventoryProfile(
        manifest_path=manifest_path.resolve().as_posix(),
        manifest_sha256=file_sha256(manifest_path),
        entry_count=len(rows),
        ready_for_import_count=sum(1 for row in rows if row.get("ready_for_import") is True),
        declared_record_count=sum(
            _non_negative_int(row.get("record_count")) for row in declared_record_rows
        ),
        declared_training_eligible_record_count=sum(
            _non_negative_int(row.get("training_eligible_record_count")) for row in rows
        ),
        ready_declared_record_count=sum(
            _non_negative_int(row.get("record_count")) for row in ready_rows
        ),
        ready_declared_training_eligible_record_count=sum(
            _non_negative_int(row.get("training_eligible_record_count"))
            for row in ready_rows
        ),
        non_ready_declared_record_count=sum(
            _non_negative_int(row.get("record_count")) for row in non_ready_rows
        ),
        non_ready_declared_training_eligible_record_count=sum(
            _non_negative_int(row.get("training_eligible_record_count"))
            for row in non_ready_rows
        ),
        status_counts=_sorted_counts(status_counts),
        engine_digest_counts=_sorted_counts(engine_counts),
        filename_year_counts=_sorted_counts(year_counts),
        source_bytes=_numeric_distribution(source_bytes),
        repaired_bytes=_numeric_distribution(repaired_bytes),
        record_count_coverage_count=len(declared_record_rows),
        ready_record_count_coverage_count=len(ready_declared_rows),
    )


def build_phase0_baseline(
    root: Path,
    *,
    repair_manifest: Path | None = None,
    accepted_only: bool = True,
    benchmark_queries: Sequence[str] = (),
    benchmark_repeats: int = 1,
    sweep_shard_size: int = DEFAULT_SWEEP_SHARD_SIZE,
) -> BrainMemoryPhase0Baseline:
    corpus = profile_brain_records(
        root,
        accepted_only=accepted_only,
        benchmark_queries=benchmark_queries,
        benchmark_repeats=benchmark_repeats,
        sweep_shard_size=sweep_shard_size,
    )
    repaired = (
        profile_repaired_inventory(repair_manifest)
        if repair_manifest is not None and repair_manifest.exists()
        else None
    )
    return BrainMemoryPhase0Baseline(corpus=corpus, repaired_inventory=repaired)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile brain records and the sequential repair inventory."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--repair-manifest",
        type=Path,
        default=Path(
            "research/inbox/bundles/repaired/sequential_repair_manifest.v2.jsonl"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("diagnostics/brain_memory_phase0_baseline.json"),
    )
    parser.add_argument("--include-staged", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--benchmark-repeat", type=int, default=3)
    parser.add_argument("--benchmark-query", action="append", default=[])
    parser.add_argument("--sweep-shard-size", type=int, default=DEFAULT_SWEEP_SHARD_SIZE)
    args = parser.parse_args()

    root = args.root.resolve()
    repair_manifest = args.repair_manifest
    if not repair_manifest.is_absolute():
        repair_manifest = root / repair_manifest
    output = args.output
    if not output.is_absolute():
        output = root / output
    benchmark_queries = tuple(args.benchmark_query) or DEFAULT_BENCHMARK_QUERIES
    if not args.benchmark:
        benchmark_queries = ()

    report = build_phase0_baseline(
        root,
        repair_manifest=repair_manifest,
        accepted_only=not args.include_staged,
        benchmark_queries=benchmark_queries,
        benchmark_repeats=args.benchmark_repeat,
        sweep_shard_size=args.sweep_shard_size,
    )
    write_json(output, report.model_dump(mode="json"))
    markdown_path = output.with_suffix(".md")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    print(output)
    print(markdown_path)


def _corpus_manifest_sha256(records: Sequence[BrainRecordEnvelope]) -> str:
    entries = [
        record.model_dump(mode="json")
        for record in sorted(records, key=lambda item: (item.record_id, item.episode_id))
    ]
    return sha256_text(canonical_json(entries))


def _update_outcome_coverage(
    counts: Counter[str], record: BrainRecordEnvelope
) -> None:
    outcome = record_outcome_payload(record.payload)
    response_class = record_response_class(record.payload)
    has_high = _contains_any_field(record.payload, _OUTCOME_HIGH_FIELDS)
    has_close = _contains_any_field(record.payload, _OUTCOME_CLOSE_FIELDS)
    has_upper = _contains_any_field(record.payload, _OUTCOME_UPPER_FIELDS)
    usable_outcome = response_class is not None or has_high or has_close or has_upper
    if outcome:
        counts["declared_outcome_container"] += 1
    if usable_outcome:
        counts["any_outcome_payload"] += 1
    if response_class is not None:
        counts["response_class"] += 1
    if has_high:
        counts["high_return"] += 1
    if has_close:
        counts["close_return"] += 1
    if has_upper:
        counts["upper_limit"] += 1
    if outcome and not usable_outcome:
        counts["declared_but_unusable_outcome"] += 1
    if not usable_outcome:
        counts["missing_outcome"] += 1


def _append_independent_unit_keys(
    destination: dict[str, list[str]], record: BrainRecordEnvelope
) -> None:
    ticker = _first_string(record.payload, _TICKER_FIELDS)
    company = _first_string(record.payload, _COMPANY_FIELDS)
    issuer = ticker or company
    day = record.trade_date.isoformat()
    if issuer is not None:
        issuer_day = f"{day}|{issuer}"
        destination["issuer-day"].append(issuer_day)
        destination["ticker-day"].append(issuer_day)

    event_ids = _all_strings(record.payload, _EVENT_FIELDS)
    if issuer is not None:
        destination["event-issuer-day"].extend(
            f"{day}|{issuer}|{event_id}" for event_id in event_ids
        )
    theme_ids = _all_strings(record.payload, _THEME_FIELDS)
    destination["theme-day"].extend(f"{day}|{theme_id}" for theme_id in theme_ids)
    if issuer is not None:
        destination["theme-day-ticker-day"].extend(
            f"{day}|{theme_id}|{issuer}" for theme_id in theme_ids
        )
    pair_ids = _all_strings(record.payload, _PAIR_FIELDS)
    destination["theme-day-pair"].extend(f"{day}|{pair_id}" for pair_id in pair_ids)


def _record_regime(payload: Mapping[str, Any]) -> str:
    value = _first_string(payload, _REGIME_FIELDS)
    return value if value is not None else "UNKNOWN"


def _benchmark_linear_retrieval(
    root: Path,
    records: Sequence[BrainRecordEnvelope],
    *,
    queries: Sequence[str],
    repeats: int,
) -> LinearRetrievalBenchmark:
    empty_distribution = _numeric_distribution([])
    source_index_status = str(inspect_vector_index(root).get("status", "unknown"))
    if not queries:
        return LinearRetrievalBenchmark(
            benchmarked=False,
            algorithm="LocalRetrievalStore.search_records.v1",
            source_index_status=source_index_status,
            isolated_index_build_ms=0.0,
            includes_index_load_and_filter=False,
            query_count=0,
            repeat_count=0,
            scanned_record_count_per_query=len(records),
            latency_ms=empty_distribution,
            query_sha256s=[],
        )

    elapsed_ms: list[float] = []
    with TemporaryDirectory(prefix="nslab-profile-retrieval-") as temp_name:
        isolated_root = Path(temp_name)
        isolated_store = BrainRecordStore(isolated_root)
        grouped: dict[str, list[BrainRecordEnvelope]] = {}
        for record in records:
            grouped.setdefault(record.episode_id, []).append(record)
        for index, episode_records in enumerate(grouped.values(), start=1):
            (isolated_store.records_dir / f"profile-{index:06d}.jsonl").write_text(
                "".join(
                    record.model_dump_json() + "\n"
                    for record in sorted(
                        episode_records, key=lambda item: item.record_id
                    )
                ),
                encoding="utf-8",
            )
        retrieval = LocalRetrievalStore(isolated_root)
        build_started = perf_counter_ns()
        retrieval.rebuild_index()
        index_build_ms = (perf_counter_ns() - build_started) / 1_000_000
        for _repeat in range(repeats):
            for query in queries:
                started = perf_counter_ns()
                retrieval.search_records(query, limit=10)
                elapsed_ms.append((perf_counter_ns() - started) / 1_000_000)
    return LinearRetrievalBenchmark(
        benchmarked=True,
        algorithm="LocalRetrievalStore.search_records.v1",
        source_index_status=source_index_status,
        isolated_index_build_ms=round(index_build_ms, 6),
        includes_index_load_and_filter=True,
        query_count=len(queries),
        repeat_count=repeats,
        scanned_record_count_per_query=len(records),
        latency_ms=_numeric_distribution(elapsed_ms),
        query_sha256s=[sha256_text(query) for query in queries],
    )


def _independent_unit_profile(keys: Sequence[str]) -> IndependentUnitProfile:
    unique_count = len(set(keys))
    duplicate_count = len(keys) - unique_count
    ratio = duplicate_count / len(keys) if keys else 0.0
    return IndependentUnitProfile(
        keyed_record_count=len(keys),
        unique_unit_count=unique_count,
        duplicate_record_count=duplicate_count,
        dedup_ratio=round(ratio, 8),
    )


def _numeric_distribution(values: Sequence[int | float]) -> NumericDistribution:
    numeric = sorted(float(value) for value in values)
    if not numeric:
        return NumericDistribution(
            count=0,
            minimum=0.0,
            mean=0.0,
            p50=0.0,
            p95=0.0,
            p99=0.0,
            maximum=0.0,
        )
    return NumericDistribution(
        count=len(numeric),
        minimum=numeric[0],
        mean=round(statistics.fmean(numeric), 6),
        p50=_nearest_rank(numeric, 0.50),
        p95=_nearest_rank(numeric, 0.95),
        p99=_nearest_rank(numeric, 0.99),
        maximum=numeric[-1],
    )


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return round(float(values[index]), 6)


def _contains_any_field(payload: Mapping[str, Any], fields: Iterable[str]) -> bool:
    field_set = set(fields)
    return any(
        key in field_set and value is not None
        for mapping in _walk_mappings(payload)
        for key, value in mapping.items()
    )


def _walk_mappings(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    queue: list[Mapping[str, Any]] = [payload]
    while queue:
        current = queue.pop(0)
        yield current
        for value in current.values():
            if isinstance(value, Mapping):
                queue.append(value)
            elif isinstance(value, list):
                queue.extend(item for item in value if isinstance(item, Mapping))


def _first_string(payload: Mapping[str, Any], fields: Iterable[str]) -> str | None:
    field_names = tuple(fields)
    for mapping in _walk_mappings(payload):
        for field in field_names:
            value = mapping.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _all_strings(payload: Mapping[str, Any], fields: Iterable[str]) -> list[str]:
    found: list[str] = []
    field_names = tuple(fields)
    for mapping in _walk_mappings(payload):
        for field in field_names:
            value = mapping.get(field)
            if isinstance(value, str) and value.strip():
                found.append(value.strip())
            elif isinstance(value, list):
                found.extend(
                    item.strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                )
    return list(dict.fromkeys(found))


def _sorted_counts(counts: Mapping[str, int]) -> dict[str, int]:
    return {key: int(counts[key]) for key in sorted(counts)}


def _filename_year(row: Mapping[str, Any]) -> str:
    value = row.get("filename_date")
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return value[:4]
    return "UNKNOWN"


def _string_value(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _non_negative_int(value: object) -> int:
    parsed = _optional_non_negative_int(value)
    return parsed if parsed is not None else 0


def _render_markdown(report: BrainMemoryPhase0Baseline) -> str:
    corpus = report.corpus
    lines = [
        "# Brain Memory Phase 0 Baseline",
        "",
        f"- schema: `{report.schema_version}`",
        f"- corpus manifest: `{corpus.corpus_manifest_sha256}`",
        f"- accepted records: `{corpus.record_count}`",
        f"- accepted episodes: `{corpus.episode_count}`",
        f"- training eligible: `{corpus.training_eligible_record_count}`",
        f"- known typed: `{corpus.known_typed_record_count}`",
        f"- unknown typed preserved: `{corpus.unknown_typed_record_count}`",
        "",
        "## Polarity",
        "",
    ]
    lines.extend(
        f"- {key}: `{value}`"
        for key, value in corpus.record_counts_by_polarity.items()
    )
    lines.extend(["", "## Retrieval Baseline", ""])
    lines.extend(
        [
            f"- algorithm: `{corpus.linear_retrieval.algorithm}`",
            f"- benchmarked: `{corpus.linear_retrieval.benchmarked}`",
            f"- p50 ms: `{corpus.linear_retrieval.latency_ms.p50}`",
            f"- p95 ms: `{corpus.linear_retrieval.latency_ms.p95}`",
            f"- p99 ms: `{corpus.linear_retrieval.latency_ms.p99}`",
            f"- source index status: `{corpus.linear_retrieval.source_index_status}`",
            f"- estimated sweep shards: `{corpus.sweep_burden.estimated_total_shard_count}`",
            f"- estimated sweep tokens: `{corpus.sweep_burden.estimated_total_artifact_tokens}`",
        ]
    )
    repaired = report.repaired_inventory
    if repaired is not None:
        lines.extend(["", "## Repaired Inventory", ""])
        lines.extend(
            [
                f"- entries: `{repaired.entry_count}`",
                f"- ready for import: `{repaired.ready_for_import_count}`",
                f"- ready declared records: `{repaired.ready_declared_record_count}`",
                (
                    "- ready declared training eligible: "
                    f"`{repaired.ready_declared_training_eligible_record_count}`"
                ),
                f"- all declared records: `{repaired.declared_record_count}`",
            ]
        )
        lines.extend(
            f"- status {key}: `{value}`"
            for key, value in repaired.status_counts.items()
        )
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    main()
