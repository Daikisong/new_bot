from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", type=Path, required=True)
    parser.add_argument("--news", type=Path, required=True)
    parser.add_argument("--blind-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.path.insert(0, str(args.pipeline.resolve()))
    import blind  # type: ignore
    import common  # type: ignore

    args.output.mkdir(parents=True, exist_ok=True)
    news_rows = common.read_csv(args.news)
    snapshot_rows = common.read_csv(args.blind_snapshot)
    total = len(news_rows)
    if args.shard_count <= 0 or not (0 <= args.shard_index < args.shard_count):
        raise RuntimeError("invalid shard coordinates")
    start = total * args.shard_index // args.shard_count
    end = total * (args.shard_index + 1) // args.shard_count
    assigned = news_rows[start:end]
    snapshot_by_code = {
        row.get("code", "").zfill(6): row
        for row in snapshot_rows
        if row.get("code")
    }

    model_inputs: list[dict[str, Any]] = []
    for global_index, row in enumerate(assigned, start=start + 1):
        source_id = f"SRC-NEWS-{global_index:06d}"
        full_text = f"{row.get('title', '')}\n{row.get('body', '')}"
        model_inputs.append(
            {
                "source_id": source_id,
                "global_row_index": global_index,
                "published_at_kst": f"{row.get('date')}T{row.get('time')}+09:00",
                "title": row.get("title", ""),
                "body": row.get("body", ""),
                "krx_candidate_options": common.make_krx_options(
                    full_text,
                    snapshot_rows,
                    snapshot_by_code,
                ),
            }
        )

    reviews_by_id: dict[str, dict[str, Any]] = {}
    log_path = args.output / f"model_call_log_shard_{args.shard_index:02d}.jsonl"

    def process(batch: list[dict[str, Any]], label: str) -> None:
        try:
            parsed = common.model_json(
                args.token,
                system=blind.detailed_review_system(),
                user=blind.detailed_review_user(batch),
                label=label,
                log_path=log_path,
                max_tokens=15000,
                attempts=10,
            )
            records = parsed.get("records") if isinstance(parsed, dict) else parsed
            if not isinstance(records, list):
                raise ValueError("model response lacks records array")
            expected_ids = {row["source_id"] for row in batch}
            actual_ids = {
                str(row.get("source_id"))
                for row in records
                if isinstance(row, dict)
            }
            if expected_ids != actual_ids or len(records) != len(batch):
                raise ValueError(
                    f"record coverage mismatch expected={len(expected_ids)} actual={len(actual_ids)}"
                )
            raw_by_id = {str(row["source_id"]): row for row in records}
            for input_row in batch:
                review = blind.normalize_review(
                    raw_by_id[input_row["source_id"]],
                    input_row,
                    snapshot_by_code,
                )
                review["global_row_index"] = input_row["global_row_index"]
                review["shard_index"] = args.shard_index
                reviews_by_id[input_row["source_id"]] = review
        except Exception as exc:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                process(batch[:midpoint], label + "-A")
                process(batch[midpoint:], label + "-B")
                return
            source_id = batch[0]["source_id"]
            raise RuntimeError(
                f"single-row semantic review failed for {source_id}: {type(exc).__name__}: {exc}"
            ) from exc

    batches = common.row_batches(model_inputs, max_items=8, max_chars=52000)
    for batch_index, batch in enumerate(batches, start=1):
        process(
            batch,
            f"FULL_ROW_SEMANTIC_REVIEW_S{args.shard_index:02d}_B{batch_index:03d}",
        )

    expected_ids = [f"SRC-NEWS-{index:06d}" for index in range(start + 1, end + 1)]
    if set(reviews_by_id) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(reviews_by_id))
        extra = sorted(set(reviews_by_id) - set(expected_ids))
        raise RuntimeError(f"shard coverage failure missing={missing[:10]} extra={extra[:10]}")
    ordered = [reviews_by_id[source_id] for source_id in expected_ids]
    if any(row.get("full_title_body_reviewed") is not True for row in ordered):
        raise RuntimeError("full-title/body review flag missing")
    if any(row.get("semantic_reviewer") != common.MODEL_NAME for row in ordered):
        raise RuntimeError("semantic reviewer provenance mismatch")

    reviews_path = args.output / f"reviews_shard_{args.shard_index:02d}.jsonl"
    common.write_jsonl(reviews_path, ordered)
    receipt = {
        "schema_version": "nslab.semantic_review_shard_receipt.v1",
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "global_start_row": start + 1,
        "global_end_row": end,
        "assigned_row_count": len(assigned),
        "reviewed_row_count": len(ordered),
        "first_source_id": expected_ids[0] if expected_ids else None,
        "last_source_id": expected_ids[-1] if expected_ids else None,
        "model": common.MODEL_NAME,
        "reviews_sha256": common.sha256_file(reviews_path),
        "status": "FULL_ROW_SEMANTIC_REVIEW_COMPLETE",
    }
    common.write_json(args.output / f"receipt_shard_{args.shard_index:02d}.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
