"""Reduced-schema DuckDB query microbenchmark for Phase 4."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path
from statistics import median
from typing import Any

import duckdb

PROFILE_DIMENSIONS = 32
PROFILE_CELL_COUNT = 1024
PROFILE_QUERY_REPEATS = 7


def profile_memory_index(
    record_counts: list[int],
    *,
    query_repeats: int = PROFILE_QUERY_REPEATS,
) -> dict[str, Any]:
    if not record_counts or any(count < 1 for count in record_counts):
        raise ValueError("record counts must be positive")
    if query_repeats < 1:
        raise ValueError("query_repeats must be positive")
    results = [
        _profile_size(record_count, query_repeats=query_repeats)
        for record_count in record_counts
    ]
    return {
        "schema_version": "nslab.memory_index_query_microbenchmark.v1",
        "synthetic": True,
        "production_exit_gate": False,
        "limitations": [
            "32-dimensional synthetic vectors",
            "reduced SQL schema without production sidecar generation",
            "does not measure external embedding API or production peak RSS",
        ],
        "duckdb_version": duckdb.__version__,
        "embedding_dimensions": PROFILE_DIMENSIONS,
        "cell_count": PROFILE_CELL_COUNT,
        "query_repeats": query_repeats,
        "results": results,
        "passed": all(
            result["hnsw_plan_verified"]
            and result["fts_result_count"] > 0
            and result["future_member_count"] == 0
            for result in results
        ),
    }


def _profile_size(record_count: int, *, query_repeats: int) -> dict[str, Any]:
    profile_root = Path(tempfile.mkdtemp(prefix=f"nslab-memory-{record_count}-"))
    database_path = profile_root / "profile.duckdb"
    started = time.perf_counter()
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("INSTALL fts")
        connection.execute("LOAD fts")
        connection.execute("INSTALL vss")
        connection.execute("LOAD vss")
        connection.execute("SET hnsw_enable_experimental_persistence = true")
        vector_expression = (
            f"list_transform(range({PROFILE_DIMENSIONS}), "
            "x -> CAST(sin((i + 1) * (x + 1)) AS FLOAT))"
            f"::FLOAT[{PROFILE_DIMENSIONS}]"
        )
        connection.execute(
            f"""
            CREATE TABLE cells AS
            SELECT 'CELL-' || lpad(i::VARCHAR, 4, '0') AS cell_id,
                   {vector_expression} AS centroid,
                   CAST(ceil({record_count}::DOUBLE / {PROFILE_CELL_COUNT}) AS INTEGER)
                       AS primary_member_count,
                   CAST(ceil({record_count}::DOUBLE / {PROFILE_CELL_COUNT}) AS INTEGER)
                       AS independent_unit_count
            FROM range({PROFILE_CELL_COUNT}) source(i)
            """
        )
        connection.execute(
            f"""
            CREATE TABLE records AS
            SELECT 'REC-' || lpad(i::VARCHAR, 8, '0') AS record_id,
                   'CELL-' || lpad((i % {PROFILE_CELL_COUNT})::VARCHAR, 4, '0')
                       AS primary_cell_id,
                   'mechanism event contract cell ' || (i % {PROFILE_CELL_COUNT})::VARCHAR
                       AS document,
                   '2030-01-10T00:00:00+09:00'::VARCHAR AS available_from,
                   CASE WHEN i % 4 = 0 THEN 'AUDIT' ELSE 'REASONING' END
                       AS routing_disposition
            FROM range({record_count}) source(i)
            """
        )
        connection.execute("CREATE INDEX records_cell_idx ON records(primary_cell_id)")
        connection.execute("CREATE INDEX records_cutoff_idx ON records(available_from)")
        connection.execute(
            "CREATE TABLE reasoning_records AS SELECT * FROM records "
            "WHERE routing_disposition = 'REASONING'"
        )
        connection.execute(
            "PRAGMA create_fts_index('reasoning_records', 'record_id', 'document', "
            "stemmer='none', stopwords='none', ignore='', overwrite=1)"
        )
        connection.execute(
            "CREATE INDEX cells_hnsw_idx ON cells USING HNSW (centroid) "
            "WITH (metric = 'cosine')"
        )
        build_seconds = time.perf_counter() - started
        query_vector = [0.1 for _ in range(PROFILE_DIMENSIONS)]
        plan = "\n".join(
            str(row[1])
            for row in connection.execute(
                f"""
                EXPLAIN SELECT cell_id FROM cells
                ORDER BY array_cosine_distance(
                    centroid,
                    ?::FLOAT[{PROFILE_DIMENSIONS}]
                )
                LIMIT 12
                """,
                [query_vector],
            ).fetchall()
        )
        ann_times = _timed_queries(
            connection,
            f"""
            SELECT cell_id FROM cells
            ORDER BY array_cosine_distance(
                centroid,
                ?::FLOAT[{PROFILE_DIMENSIONS}]
            )
            LIMIT 12
            """,
            [query_vector],
            repeats=query_repeats,
        )
        fts_times = _timed_queries(
            connection,
            """
            SELECT record_id
            FROM (
                SELECT record_id,
                       fts_main_reasoning_records.match_bm25(record_id, 'contract') AS score
                FROM reasoning_records
            ) matched
            WHERE score IS NOT NULL
            ORDER BY score DESC
            LIMIT 100
            """,
            [],
            repeats=query_repeats,
        )
        cell_ids = [f"CELL-{index:04d}" for index in range(12)]
        member_times = _timed_queries(
            connection,
            """
            SELECT COUNT(*)
            FROM records
            WHERE primary_cell_id IN (SELECT UNNEST(?::VARCHAR[]))
              AND available_from <= '2030-01-10T08:59:59+09:00'
              AND routing_disposition = 'REASONING'
            """,
            [cell_ids],
            repeats=query_repeats,
        )
        fts_result_count = _fetch_count(
            connection,
                """
                SELECT COUNT(*) FROM (
                    SELECT fts_main_reasoning_records.match_bm25(record_id, 'contract') AS score
                    FROM reasoning_records
                ) matched WHERE score IS NOT NULL
                """,
        )
        future_member_count = _fetch_count(
            connection,
            "SELECT COUNT(*) FROM records WHERE available_from > '2030-01-10T08:59:59+09:00'",
        )
        return {
            "record_count": record_count,
            "database_bytes": database_path.stat().st_size,
            "build_seconds": round(build_seconds, 6),
            "hnsw_plan_verified": "HNSW_INDEX_SCAN" in plan,
            "ann_query_ms": _latency_summary(ann_times),
            "fts_query_ms": _latency_summary(fts_times),
            "cell_member_query_ms": _latency_summary(member_times),
            "fts_result_count": fts_result_count,
            "future_member_count": future_member_count,
        }
    finally:
        connection.close()
        shutil.rmtree(profile_root)


def _timed_queries(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[object],
    *,
    repeats: int,
) -> list[float]:
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        connection.execute(query, parameters).fetchall()
        durations.append((time.perf_counter() - started) * 1000.0)
    return durations


def _fetch_count(connection: duckdb.DuckDBPyConnection, query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None or not isinstance(row[0], int):
        raise ValueError("profile count query returned no integer")
    return row[0]


def _latency_summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "minimum": round(ordered[0], 6),
        "median": round(median(ordered), 6),
        "maximum": round(ordered[-1], 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--record-count",
        action="append",
        type=int,
        dest="record_counts",
    )
    parser.add_argument("--query-repeats", type=int, default=PROFILE_QUERY_REPEATS)
    args = parser.parse_args()
    counts = args.record_counts or [50_000, 200_000, 600_000]
    print(
        json.dumps(
            profile_memory_index(counts, query_repeats=args.query_repeats),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
