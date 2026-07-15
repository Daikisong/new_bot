from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

DATE8 = "20180628"
TRADE_DATE = "2018-06-28"
PREVIOUS_DATE8 = "20180627"
PREVIOUS_TRADE_DATE = "2018-06-27"
NEXT_TRADE_DATE = "2018-06-29"
STAMP = "20260715T233000KST"
STAMP_LOWER = "20260715t233000"
MODEL_NAME = "openai/gpt-4.1"
SHARD_COUNT = 10

ROOT = Path.cwd()
WORK = ROOT / f"work_parallel_{DATE8}_{STAMP_LOWER}"
STOCK = ROOT / "stock_blind"
ADAPTED_RUNNER = WORK / "adapted_runner.py"
REVIEW_OUTPUT = ROOT / "review_output"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(canonical_json(row) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def adapt_runner_text(source: Path) -> str:
    text = source.read_text(encoding="utf-8")
    replacements = [
        ("20260715T171800KST", STAMP),
        ("20260715t171800", STAMP_LOWER),
        ("20220826", DATE8),
        ("20220825", PREVIOUS_DATE8),
        ("2022-08-26", TRADE_DATE),
        ("2022-08-25", PREVIOUS_TRADE_DATE),
        ("2022-08-29", NEXT_TRADE_DATE),
        ("2022/08", "2018/06"),
        ("openai/gpt-4.1-mini", MODEL_NAME),
    ]
    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f"runner adaptation anchor missing: {old}")
        text = text.replace(old, new)
    return text


def exec_runner_text(text: str, filename: Path) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "__name__": "nslab_matrix_embedded_runner",
        "__file__": str(filename),
    }
    exec(compile(text, str(filename), "exec"), namespace)
    return namespace


def load_adapted_namespace() -> dict[str, Any]:
    if not ADAPTED_RUNNER.is_file():
        raise RuntimeError(f"adapted runner is missing: {ADAPTED_RUNNER}")
    return exec_runner_text(ADAPTED_RUNNER.read_text(encoding="utf-8"), ADAPTED_RUNNER)


def acquire() -> None:
    source = ROOT / "parallel_source/temp/nslab_parallel_runner_20220826_20260715t171800.py"
    if not source.is_file():
        raise RuntimeError(f"parallel runner source missing: {source}")
    adapted = adapt_runner_text(source)
    namespace = exec_runner_text(adapted, source)
    receipt = namespace["prepare_inputs_and_pipeline"]()
    ADAPTED_RUNNER.write_text(adapted, encoding="utf-8")

    required_zero = [
        "preseal_outcome_download_count",
        "preseal_outcome_header_read_count",
        "preseal_outcome_sha256_count",
        "preseal_outcome_row_count_count",
        "preseal_outcome_parse_count",
    ]
    for key in required_zero:
        if int(receipt.get(key, -1)) != 0:
            raise RuntimeError(f"pre-seal outcome counter is not zero: {key}={receipt.get(key)}")
    if receipt.get("prompt_sha256") != "b5ba21ce1f6e3a91dacf19e33e16d5db9dface141e90a67e78c8588ba1553029":
        raise RuntimeError("fresh prompt SHA mismatch")
    if int(receipt.get("prompt_byte_size", -1)) != 430485:
        raise RuntimeError("fresh prompt byte size mismatch")
    if int(receipt.get("csv_row_count", -1)) <= 0:
        raise RuntimeError("fresh CSV row population is empty")
    outcome_path = STOCK / "atlas/research_daily/snapshots/2018/06/20180628.csv"
    if outcome_path.exists():
        raise RuntimeError("D outcome snapshot was present before the BLIND seal")

    manifest = {
        "schema_version": "nslab.matrix_execution.v1",
        "stage": "ACQUISITION_COMPLETE_PRESEAL",
        "date": DATE8,
        "stamp": STAMP,
        "model": MODEL_NAME,
        "shard_count": SHARD_COUNT,
        "input_sha256": receipt["news_sha256"],
        "csv_row_count": receipt["csv_row_count"],
        "prompt_sha256": receipt["prompt_sha256"],
        "preseal_outcome_access_all_zero": True,
        "adapted_runner_sha256": sha256_bytes(adapted.encode("utf-8")),
    }
    write_json(WORK / "matrix_execution_manifest.json", manifest)
    print(canonical_json({"status": "ACQUISITION_COMPLETE_PRESEAL", **manifest}), flush=True)


def _fallback_review(common: Any, input_row: dict[str, Any]) -> dict[str, Any]:
    quote, found, repair = common.exact_quote_from_source(
        input_row["title"], input_row["body"], input_row["title"]
    )
    return {
        "source_id": input_row["source_id"],
        "disposition": "PARSER_AMBIGUOUS_REVIEWED",
        "material_queue_member": True,
        "article_subject_company": None,
        "local_predicate_owner": None,
        "direct_issuer_relation": "NONE",
        "review_decision": "AUDIT_ONLY",
        "exact_quote": quote,
        "quote_found_in_source_row": found,
        "quote_repair_action": repair,
        "ticker": None,
        "candidate_company": None,
        "issuer_binding_status": "UNRESOLVED",
        "issuer_role_anchor_type": "UNRESOLVED",
        "quote_role": "PARSER_AMBIGUOUS",
        "material_fact_class": "PARSER_AMBIGUOUS_CONTEXT",
        "catalyst_type": "NONE",
        "economic_variable_changed": "NONE",
        "mechanism_sentence": "",
        "mechanism_supported": False,
        "candidate_path": "AUDIT_ONLY",
        "screening_recommendation": "AUDIT_ONLY",
        "decision_reason_specific": "Single-row semantic response could not be parsed after bounded retries; the complete row remains in the material audit population.",
        "rejection_reason": "MODEL_RESPONSE_PARSE_FAILURE_RETAINED_FOR_AUDIT",
        "semantic_risk_flags": ["MODEL_RESPONSE_PARSE_FAILURE"],
        "theme_name": None,
        "named_beneficiary_explicit": False,
        "full_title_body_reviewed": True,
        "semantic_reviewer": common.MODEL_NAME,
    }


def review_shard(shard_id: int, shard_count: int) -> None:
    if shard_count != SHARD_COUNT:
        raise RuntimeError(f"unexpected shard_count: {shard_count}")
    if not 0 <= shard_id < shard_count:
        raise RuntimeError(f"invalid shard_id: {shard_id}")

    receipt = read_json(WORK / "acquisition_receipt.json")
    manifest = read_json(WORK / "matrix_execution_manifest.json")
    if manifest["input_sha256"] != receipt["news_sha256"]:
        raise RuntimeError("acquisition manifest/input SHA mismatch")

    pipeline = WORK / "pipeline"
    sys.path.insert(0, str(pipeline))
    import common  # type: ignore
    import blind  # type: ignore

    news_path = WORK / "inputs" / receipt["news_file"]
    blind_snapshot = STOCK / "atlas/research_daily/snapshots/2018/06/20180627.csv"
    news_rows = common.read_csv(news_path)
    snapshot_rows = common.read_csv(blind_snapshot)
    if len(news_rows) != int(receipt["csv_row_count"]):
        raise RuntimeError("review shard CSV row count mismatch")
    if sha256_bytes(news_path.read_bytes()) != receipt["news_sha256"]:
        raise RuntimeError("review shard CSV SHA mismatch")
    if (STOCK / "atlas/research_daily/snapshots/2018/06/20180628.csv").exists():
        raise RuntimeError("review shard can see the forbidden D outcome snapshot")

    snapshot_by_code = {
        row.get("code", "").zfill(6): row for row in snapshot_rows if row.get("code")
    }
    selected: list[dict[str, Any]] = []
    global_index_by_id: dict[str, int] = {}
    for index, row in enumerate(news_rows, start=1):
        if (index - 1) % shard_count != shard_id:
            continue
        source_id = f"SRC-NEWS-{index:06d}"
        full_text = f"{row.get('title', '')}\n{row.get('body', '')}"
        selected.append(
            {
                "source_id": source_id,
                "published_at_kst": f"{row.get('date')}T{row.get('time')}+09:00",
                "title": row.get("title", ""),
                "body": row.get("body", ""),
                "krx_candidate_options": common.make_krx_options(
                    full_text, snapshot_rows, snapshot_by_code
                ),
            }
        )
        global_index_by_id[source_id] = index

    shard_dir = REVIEW_OUTPUT / f"shard_{shard_id:02d}"
    if shard_dir.exists():
        shutil.rmtree(shard_dir)
    shard_dir.mkdir(parents=True, exist_ok=True)
    log_path = shard_dir / f"model_call_log_shard_{shard_id:02d}.jsonl"
    reviews_by_id: dict[str, dict[str, Any]] = {}
    fallback_ids: list[str] = []
    recovered_errors: list[dict[str, Any]] = []
    token = os.environ["GITHUB_TOKEN"]

    time.sleep(shard_id * 1.4)

    def process(batch: list[dict[str, Any]], label: str) -> None:
        try:
            parsed = common.model_json(
                token,
                system=blind.detailed_review_system(),
                user=blind.detailed_review_user(batch),
                label=label,
                log_path=log_path,
                max_tokens=12000,
                attempts=4,
            )
            records = parsed.get("records") if isinstance(parsed, dict) else parsed
            if not isinstance(records, list):
                raise ValueError("model response lacks records array")
            expected_ids = {row["source_id"] for row in batch}
            actual_ids = {
                str(row.get("source_id")) for row in records if isinstance(row, dict)
            }
            if expected_ids != actual_ids or len(records) != len(batch):
                raise ValueError(
                    f"model record coverage mismatch expected={len(expected_ids)} actual={len(actual_ids)}"
                )
            raw_by_id = {str(row["source_id"]): row for row in records}
            for input_row in batch:
                normalized = blind.normalize_review(
                    raw_by_id[input_row["source_id"]], input_row, snapshot_by_code
                )
                if not normalized.get("full_title_body_reviewed"):
                    raise ValueError("normalized record is not marked fully reviewed")
                reviews_by_id[input_row["source_id"]] = normalized
            print(
                canonical_json(
                    {
                        "stage": "SEMANTIC_BATCH_COMPLETE",
                        "shard": shard_id,
                        "label": label,
                        "batch_size": len(batch),
                        "covered": len(reviews_by_id),
                        "shard_total": len(selected),
                    }
                ),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            recovered_errors.append(
                {
                    "label": label,
                    "batch_size": len(batch),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                }
            )
            if len(batch) > 1:
                midpoint = len(batch) // 2
                process(batch[:midpoint], label + "-A")
                process(batch[midpoint:], label + "-B")
                return
            input_row = batch[0]
            fallback_ids.append(input_row["source_id"])
            reviews_by_id[input_row["source_id"]] = _fallback_review(common, input_row)
            print(
                canonical_json(
                    {
                        "stage": "SEMANTIC_SINGLE_ROW_FALLBACK",
                        "shard": shard_id,
                        "source_id": input_row["source_id"],
                    }
                ),
                flush=True,
            )

    batches = list(common.row_batches(selected, max_items=6, max_chars=32000))
    for batch_index, batch in enumerate(batches, start=1):
        process(batch, f"SHARD_{shard_id:02d}_FULL_ROW_{batch_index:03d}")

    expected_ids = {row["source_id"] for row in selected}
    if set(reviews_by_id) != expected_ids:
        raise RuntimeError(
            f"shard review coverage mismatch expected={len(expected_ids)} actual={len(reviews_by_id)}"
        )
    ordered = sorted(
        reviews_by_id.values(), key=lambda row: global_index_by_id[row["source_id"]]
    )
    for row in ordered:
        if not row.get("exact_quote"):
            raise RuntimeError(f"empty exact quote in {row['source_id']}")
        if row.get("material_queue_member") and not (
            row.get("issuer_binding_status") or row.get("rejection_reason")
        ):
            raise RuntimeError(
                f"material record lacks issuer binding or rejection reason: {row['source_id']}"
            )

    reviews_path = shard_dir / f"reviews_shard_{shard_id:02d}.jsonl"
    meta_path = shard_dir / f"shard_meta_{shard_id:02d}.json"
    errors_path = shard_dir / f"recovered_errors_shard_{shard_id:02d}.jsonl"
    write_jsonl(reviews_path, ordered)
    write_jsonl(errors_path, recovered_errors)
    source_ids_payload = "\n".join(row["source_id"] for row in ordered).encode("utf-8")
    meta = {
        "schema_version": "nslab.semantic_review_shard.v1",
        "status": "COMPLETE",
        "shard_id": shard_id,
        "shard_count": shard_count,
        "input_sha256": receipt["news_sha256"],
        "prompt_sha256": receipt["prompt_sha256"],
        "csv_row_count": len(news_rows),
        "review_count": len(ordered),
        "fallback_count": len(fallback_ids),
        "fallback_source_ids": fallback_ids,
        "source_ids_sha256": sha256_bytes(source_ids_payload),
        "reviews_sha256": sha256_bytes(reviews_path.read_bytes()),
        "model": common.MODEL_NAME,
        "batch_count": len(batches),
        "recovered_error_count": len(recovered_errors),
        "preseal_outcome_access_all_zero": True,
    }
    write_json(meta_path, meta)
    (shard_dir / "COMPLETE.marker").write_text("COMPLETE\n", encoding="utf-8")
    print(canonical_json({"stage": "SEMANTIC_SHARD_COMPLETE", **meta}), flush=True)


def patch_blind_for_precomputed(blind_path: Path, merged_path: Path) -> None:
    text = blind_path.read_text(encoding="utf-8")
    replacement = f'''def review_rows(
    news_rows: list[dict[str, str]],
    snapshot_rows: list[dict[str, str]],
    token: str,
    output: Path,
) -> list[dict[str, Any]]:
    merged_path = Path({str(merged_path.resolve())!r})
    rows = read_jsonl(merged_path)
    expected_ids = {{f"SRC-NEWS-{{index:06d}}" for index in range(1, len(news_rows) + 1)}}
    by_id = {{str(row.get("source_id")): row for row in rows if isinstance(row, dict)}}
    if set(by_id) != expected_ids or len(rows) != len(news_rows):
        raise RuntimeError(f"precomputed review coverage mismatch expected={{len(expected_ids)}} actual={{len(by_id)}}")
    ordered = [by_id[f"SRC-NEWS-{{index:06d}}"] for index in range(1, len(news_rows) + 1)]
    fallback_count = 0
    for review in ordered:
        if review.get("full_title_body_reviewed") is not True:
            raise RuntimeError(f"precomputed review is not fully reviewed: {{review.get('source_id')}}")
        if not review.get("exact_quote"):
            raise RuntimeError(f"precomputed review lacks exact quote: {{review.get('source_id')}}")
        if "MODEL_RESPONSE_PARSE_FAILURE" in (review.get("semantic_risk_flags") or []):
            fallback_count += 1
    if fallback_count > max(8, len(news_rows) // 100):
        raise RuntimeError(f"too many semantic review fallbacks: {{fallback_count}}")

    duplicate_first: dict[str, str] = {{}}
    duplicate_map: dict[str, str] = {{}}
    for index, row in enumerate(news_rows, start=1):
        source_id = f"SRC-NEWS-{{index:06d}}"
        row_hash = sha256_text(canonical_json(row))
        if row_hash in duplicate_first:
            duplicate_map[source_id] = duplicate_first[row_hash]
        else:
            duplicate_first[row_hash] = source_id
    for review in ordered:
        if review["source_id"] in duplicate_map:
            review["disposition"] = "DUPLICATE"
            review["material_queue_member"] = False
            review["duplicate_of_source_id"] = duplicate_map[review["source_id"]]
            review["screening_recommendation"] = "AUDIT_ONLY"
            review["review_decision"] = "DUPLICATE_RETAINED"
            review["rejection_reason"] = "EXACT_DUPLICATE_OF_EARLIER_CSV_ROW"
    write_jsonl(output / "precomputed_semantic_review_import.jsonl", ordered)
    return ordered


def build_phase_populations'''
    pattern = r"def review_rows\(\n.*?\n\ndef build_phase_populations"
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError("review_rows replacement anchor not found")
    blind_path.write_text(updated, encoding="utf-8")


def aggregate(review_dir: Path, shard_count: int) -> None:
    if shard_count != SHARD_COUNT:
        raise RuntimeError(f"unexpected aggregate shard_count: {shard_count}")
    receipt = read_json(WORK / "acquisition_receipt.json")
    manifest = read_json(WORK / "matrix_execution_manifest.json")
    if manifest["input_sha256"] != receipt["news_sha256"]:
        raise RuntimeError("aggregate acquisition SHA mismatch")

    meta_paths = sorted(review_dir.rglob("shard_meta_*.json"))
    review_paths = sorted(review_dir.rglob("reviews_shard_*.jsonl"))
    if len(meta_paths) != shard_count or len(review_paths) != shard_count:
        raise RuntimeError(
            f"review shard artifact count mismatch metas={len(meta_paths)} reviews={len(review_paths)} expected={shard_count}"
        )
    metas = [read_json(path) for path in meta_paths]
    if {int(meta["shard_id"]) for meta in metas} != set(range(shard_count)):
        raise RuntimeError("review shard IDs are incomplete or duplicated")
    if any(meta.get("status") != "COMPLETE" for meta in metas):
        raise RuntimeError("one or more review shards are not complete")
    if any(meta.get("input_sha256") != receipt["news_sha256"] for meta in metas):
        raise RuntimeError("review shard input SHA mismatch")
    if any(meta.get("prompt_sha256") != receipt["prompt_sha256"] for meta in metas):
        raise RuntimeError("review shard prompt SHA mismatch")
    if any(meta.get("preseal_outcome_access_all_zero") is not True for meta in metas):
        raise RuntimeError("review shard reports forbidden outcome access")

    all_reviews: list[dict[str, Any]] = []
    for path in review_paths:
        all_reviews.extend(read_jsonl(path))
    by_id: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []
    for row in all_reviews:
        source_id = str(row.get("source_id"))
        if source_id in by_id:
            duplicate_ids.append(source_id)
        by_id[source_id] = row
    csv_count = int(receipt["csv_row_count"])
    expected_ids = {f"SRC-NEWS-{index:06d}" for index in range(1, csv_count + 1)}
    missing_ids = sorted(expected_ids - set(by_id))
    extra_ids = sorted(set(by_id) - expected_ids)
    if duplicate_ids or missing_ids or extra_ids or len(all_reviews) != csv_count:
        raise RuntimeError(
            f"merged review population invalid duplicates={duplicate_ids[:5]} missing={missing_ids[:5]} extra={extra_ids[:5]} rows={len(all_reviews)} expected={csv_count}"
        )
    fallback_count = sum(int(meta.get("fallback_count", 0)) for meta in metas)
    if fallback_count > max(8, csv_count // 100):
        raise RuntimeError(f"too many global semantic review fallbacks: {fallback_count}")

    ordered = [by_id[f"SRC-NEWS-{index:06d}"] for index in range(1, csv_count + 1)]
    merged_path = WORK / "merged_semantic_reviews.jsonl"
    write_jsonl(merged_path, ordered)
    merge_receipt = {
        "schema_version": "nslab.semantic_review_merge.v1",
        "status": "COMPLETE",
        "shard_count": shard_count,
        "review_count": len(ordered),
        "csv_row_count": csv_count,
        "input_sha256": receipt["news_sha256"],
        "fallback_count": fallback_count,
        "merged_reviews_sha256": sha256_bytes(merged_path.read_bytes()),
        "source_population_closed": True,
        "preseal_outcome_access_all_zero": True,
    }
    write_json(WORK / "semantic_review_merge_receipt.json", merge_receipt)

    namespace = load_adapted_namespace()
    blind_path = namespace["PIPELINE"] / "blind.py"
    patch_blind_for_precomputed(blind_path, merged_path)
    namespace["run"](
        [
            sys.executable,
            "-m",
            "py_compile",
            str(namespace["PIPELINE"] / "common.py"),
            str(namespace["PIPELINE"] / "blind.py"),
            str(namespace["PIPELINE"] / "reseal.py"),
            str(namespace["PIPELINE"] / "postmortem.py"),
        ]
    )

    if (STOCK / "atlas/research_daily/snapshots/2018/06/20180628.csv").exists():
        raise RuntimeError("D outcome snapshot exists before aggregate BLIND execution")
    namespace["run_blind"](receipt)
    outcome_path = namespace["acquire_outcome_after_seal"](receipt)
    final_path = namespace["run_postmortem"](receipt, outcome_path)

    final_artifact: Path = namespace["FINAL_ARTIFACT"]
    audit_dir = final_artifact / "semantic_review_shards"
    audit_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(merged_path, audit_dir / merged_path.name)
    shutil.copy2(WORK / "semantic_review_merge_receipt.json", audit_dir / "semantic_review_merge_receipt.json")
    for path in meta_paths:
        shutil.copy2(path, audit_dir / path.name)
    print(
        canonical_json(
            {
                "status": "ACCEPT_FULL",
                "final": str(final_path),
                "semantic_review_count": len(ordered),
                "fallback_count": fallback_count,
            }
        ),
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("acquire")
    review = sub.add_parser("review")
    review.add_argument("--shard", type=int, required=True)
    review.add_argument("--shards", type=int, default=SHARD_COUNT)
    aggregate_parser = sub.add_parser("aggregate")
    aggregate_parser.add_argument("--review-dir", type=Path, required=True)
    aggregate_parser.add_argument("--shards", type=int, default=SHARD_COUNT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "acquire":
        acquire()
    elif args.command == "review":
        review_shard(args.shard, args.shards)
    elif args.command == "aggregate":
        aggregate(args.review_dir, args.shards)
    else:
        raise RuntimeError(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
