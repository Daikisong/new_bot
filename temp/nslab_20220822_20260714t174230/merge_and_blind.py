from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", type=Path, required=True)
    parser.add_argument("--reviews-dir", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--news", type=Path, required=True)
    parser.add_argument("--access", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--blind-snapshot", type=Path, required=True)
    parser.add_argument("--example", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.path.insert(0, str(args.pipeline.resolve()))
    import blind  # type: ignore
    import common  # type: ignore

    news_rows = common.read_csv(args.news)
    expected_count = len(news_rows)
    reviews: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for shard_index in range(args.shard_count):
        review_path = args.reviews_dir / f"reviews_shard_{shard_index:02d}.jsonl"
        receipt_path = args.reviews_dir / f"receipt_shard_{shard_index:02d}.json"
        if not review_path.exists() or not receipt_path.exists():
            raise RuntimeError(f"missing semantic shard artifact {shard_index}")
        shard_reviews = common.read_jsonl(review_path)
        receipt = common.read_json(receipt_path)
        if receipt.get("status") != "FULL_ROW_SEMANTIC_REVIEW_COMPLETE":
            raise RuntimeError(f"invalid shard receipt {shard_index}: {receipt}")
        if receipt.get("reviewed_row_count") != len(shard_reviews):
            raise RuntimeError(f"shard receipt count mismatch {shard_index}")
        if common.sha256_file(review_path) != receipt.get("reviews_sha256"):
            raise RuntimeError(f"shard review hash mismatch {shard_index}")
        reviews.extend(shard_reviews)
        receipts.append(receipt)

    reviews.sort(key=lambda row: int(row.get("global_row_index") or -1))
    expected_indices = list(range(1, expected_count + 1))
    actual_indices = [int(row.get("global_row_index") or -1) for row in reviews]
    if actual_indices != expected_indices:
        raise RuntimeError("merged semantic review row-index coverage mismatch")
    expected_ids = [f"SRC-NEWS-{index:06d}" for index in expected_indices]
    actual_ids = [str(row.get("source_id")) for row in reviews]
    if actual_ids != expected_ids or len(set(actual_ids)) != expected_count:
        raise RuntimeError("merged semantic review source-id coverage mismatch")
    if any(row.get("full_title_body_reviewed") is not True for row in reviews):
        raise RuntimeError("merged semantic review contains unreviewed row")
    if any("MODEL_RESPONSE_PARSE_FAILURE" in (row.get("semantic_risk_flags") or []) for row in reviews):
        raise RuntimeError("semantic model parse-failure fallback is forbidden")

    # Exact-content duplicate audit is performed only after all rows were independently reviewed.
    first_by_content_hash: dict[str, str] = {}
    duplicate_count = 0
    for index, (source_row, review) in enumerate(zip(news_rows, reviews, strict=True), start=1):
        content_identity = {
            "date": source_row.get("date"),
            "time": source_row.get("time"),
            "title": source_row.get("title"),
            "body": source_row.get("body"),
        }
        content_hash = common.sha256_text(common.canonical_json(content_identity))
        if content_hash not in first_by_content_hash:
            first_by_content_hash[content_hash] = review["source_id"]
            continue
        duplicate_count += 1
        review["disposition"] = "DUPLICATE"
        review["material_queue_member"] = False
        review["duplicate_of_source_id"] = first_by_content_hash[content_hash]
        review["screening_recommendation"] = "AUDIT_ONLY"
        review["review_decision"] = "DUPLICATE_RETAINED_AFTER_FULL_SEMANTIC_REVIEW"
        review["rejection_reason"] = "EXACT_DATE_TIME_TITLE_BODY_DUPLICATE_OF_EARLIER_CSV_ROW"

    args.output.mkdir(parents=True, exist_ok=True)
    merged_path = args.output / "merged_semantic_reviews.jsonl"
    common.write_jsonl(merged_path, reviews)
    merge_receipt = {
        "schema_version": "nslab.semantic_review_merge_receipt.v1",
        "csv_row_count": expected_count,
        "merged_review_count": len(reviews),
        "shard_count": args.shard_count,
        "shard_receipt_count": len(receipts),
        "duplicate_after_full_review_count": duplicate_count,
        "full_title_body_reviewed_count": sum(
            1 for row in reviews if row.get("full_title_body_reviewed") is True
        ),
        "model_reviewer_count": sum(
            1 for row in reviews if row.get("semantic_reviewer") == common.MODEL_NAME
        ),
        "parse_failure_fallback_count": 0,
        "merged_reviews_sha256": common.sha256_file(merged_path),
        "status": "PHASE_1_SEMANTIC_DENOMINATOR_READY",
    }
    common.write_json(args.output / "semantic_review_merge_receipt.json", merge_receipt)

    def closed_review_population(
        passed_news_rows: list[dict[str, str]],
        passed_snapshot_rows: list[dict[str, str]],
        token: str,
        output: Path,
    ) -> list[dict[str, Any]]:
        if len(passed_news_rows) != expected_count:
            raise RuntimeError("blind runner news denominator changed after semantic merge")
        return common.read_jsonl(merged_path)

    blind.review_rows = closed_review_population
    sys.argv = [
        "blind.py",
        "--prompt", str(args.prompt),
        "--news", str(args.news),
        "--access", str(args.access),
        "--manifest", str(args.manifest),
        "--schema", str(args.schema),
        "--blind-snapshot", str(args.blind_snapshot),
        "--example", str(args.example),
        "--output", str(args.output),
        "--token", args.token,
        "--run-id", args.run_id,
    ]
    blind.main()

    artifacts = args.output / "artifacts"
    ledger = common.read_json(artifacts / "ledger_population_audit.json")
    required_equalities = {
        "csv_row_count": expected_count,
        "source_ledger_news_row_count": expected_count,
        "row_disposition_count": expected_count,
        "material_review_unreviewed_count": 0,
        "unscreened_material_observation_count": 0,
        "predeclared_final_candidate_list_count": 0,
    }
    for key, expected in required_equalities.items():
        if ledger.get(key) != expected:
            raise RuntimeError(f"blind denominator/anti-reward gate failed {key}: {ledger.get(key)!r}")
    final_receipt = {
        **merge_receipt,
        "blind_packet_status": "BLIND_PACKET_RENDERED_PENDING_CLEAN_RESEAL",
        "candidate_screening_count": ledger.get("candidate_screening_material_coverage_count"),
        "final_watchlist_count": ledger.get("final_watchlist_count"),
    }
    common.write_json(args.output / "merge_and_blind_receipt.json", final_receipt)
    print(json.dumps(final_receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
