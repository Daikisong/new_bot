from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

DATE8 = "20180628"
TRADE_DATE = "2018-06-28"
PREVIOUS_TRADE_DATE = "2018-06-27"
NEXT_TRADE_DATE = "2018-06-29"
STAMP = "20260715T222908KST"
STAMP_LOWER = "20260715t222908"
SHARD_COUNT = 32


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"non-object JSONL row in {path}")
            output.append(value)
    return output


def combine_shards() -> tuple[Path, dict[str, Any]]:
    input_dir = Path("precomputed_reviews")
    review_files = sorted(input_dir.glob("reviews_shard_*.jsonl"))
    receipt_files = sorted(input_dir.glob("acquisition_shard_*.json"))
    if len(review_files) != SHARD_COUNT or len(receipt_files) != SHARD_COUNT:
        raise RuntimeError(
            f"matrix artifact count mismatch reviews={len(review_files)} receipts={len(receipt_files)} expected={SHARD_COUNT}"
        )

    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in receipt_files]
    shard_ids = {int(row["shard"]) for row in receipts}
    if shard_ids != set(range(SHARD_COUNT)):
        raise RuntimeError(f"matrix receipt shard set mismatch: {sorted(shard_ids)}")
    for row in receipts:
        if row.get("schema_version") != "nslab.matrix_review_shard_receipt.v1":
            raise RuntimeError("matrix receipt schema mismatch")
        if int(row.get("preseal_outcome_access_count", -1)) != 0:
            raise RuntimeError("pre-seal outcome access was not zero")
        if int(row.get("selected_row_count", -1)) != int(row.get("review_row_count", -2)):
            raise RuntimeError("shard selected/review count mismatch")

    def singleton(field: str) -> Any:
        values = {json.dumps(row.get(field), sort_keys=True) for row in receipts}
        if len(values) != 1:
            raise RuntimeError(f"matrix receipt disagreement for {field}: {values}")
        return receipts[0].get(field)

    prompt_sha = str(singleton("prompt_sha256"))
    prompt_bytes = int(singleton("prompt_byte_size"))
    news_sha = str(singleton("news_sha256"))
    news_bytes = int(singleton("news_byte_size"))
    csv_row_count = int(singleton("csv_row_count"))
    blind_sha = str(singleton("blind_snapshot_sha256"))
    blind_bytes = int(singleton("blind_snapshot_byte_size"))
    blind_rows = int(singleton("blind_snapshot_row_count"))

    reviews: list[dict[str, Any]] = []
    for path in review_files:
        reviews.extend(read_jsonl(path))
    by_id: dict[str, dict[str, Any]] = {}
    for row in reviews:
        source_id = str(row.get("source_id") or "")
        if not re.fullmatch(r"SRC-NEWS-\d{6}", source_id):
            raise RuntimeError(f"invalid source id in matrix review: {source_id!r}")
        if source_id in by_id:
            raise RuntimeError(f"duplicate matrix review source id: {source_id}")
        by_id[source_id] = row
    expected_ids = [f"SRC-NEWS-{index:06d}" for index in range(1, csv_row_count + 1)]
    if set(by_id) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(by_id))[:20]
        extra = sorted(set(by_id) - set(expected_ids))[:20]
        raise RuntimeError(f"matrix review denominator mismatch missing={missing} extra={extra}")
    ordered = [by_id[source_id] for source_id in expected_ids]

    combined_dir = Path(f"matrix_precomputed_{DATE8}_{STAMP_LOWER}")
    combined_dir.mkdir(parents=True, exist_ok=False)
    combined_path = combined_dir / "combined_reviews.jsonl"
    combined_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in ordered) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema_version": "nslab.matrix_review_population_receipt.v1",
        "shard_count": SHARD_COUNT,
        "csv_row_count": csv_row_count,
        "review_row_count": len(ordered),
        "source_id_denominator_closed": True,
        "prompt_sha256": prompt_sha,
        "prompt_byte_size": prompt_bytes,
        "news_sha256": news_sha,
        "news_byte_size": news_bytes,
        "blind_snapshot_sha256": blind_sha,
        "blind_snapshot_byte_size": blind_bytes,
        "blind_snapshot_row_count": blind_rows,
        "preseal_outcome_access_count": 0,
        "combined_reviews_sha256": sha256_bytes(combined_path.read_bytes()),
        "shard_review_payload_sha256": {
            f"{int(row['shard']):02d}": row["review_payload_sha256"] for row in sorted(receipts, key=lambda item: int(item["shard"]))
        },
    }
    receipt_path = combined_dir / "matrix_review_population_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return combined_path.resolve(), receipt


def transformed_runner_namespace() -> dict[str, Any]:
    runner_path = Path("parallel_source/temp/nslab_parallel_runner_20220826_20260715t171800.py")
    text = runner_path.read_text(encoding="utf-8")
    replacements = [
        ("20260715T171800KST", STAMP),
        ("20260715t171800", STAMP_LOWER),
        ("20220826", DATE8),
        ("2022-08-26", TRADE_DATE),
        ("20220825", "20180627"),
        ("2022-08-25", PREVIOUS_TRADE_DATE),
        ("20220829", "20180629"),
        ("2022-08-29", NEXT_TRADE_DATE),
        ("2022/08", "2018/06"),
    ]
    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f"runner transformation anchor missing: {old}")
        text = text.replace(old, new)
    namespace: dict[str, Any] = {
        "__name__": "nslab_matrix_aggregate_runner",
        "__file__": str(runner_path),
    }
    exec(compile(text, str(runner_path), "exec"), namespace)
    return namespace


def patch_review_loader(blind_path: Path, combined_path: Path, expected_count: int) -> None:
    text = blind_path.read_text(encoding="utf-8")
    loader = f'''def review_rows(
    news_rows: list[dict[str, str]],
    snapshot_rows: list[dict[str, str]],
    token: str,
    output: Path,
) -> list[dict[str, Any]]:
    precomputed_path = Path({str(combined_path)!r})
    rows = read_jsonl(precomputed_path)
    if len(rows) != {expected_count} or len(news_rows) != {expected_count}:
        raise RuntimeError(f"precomputed semantic review count mismatch reviews={{len(rows)}} news={{len(news_rows)}}")
    by_id: dict[str, dict[str, Any]] = {{}}
    for row in rows:
        source_id = str(row.get("source_id") or "")
        if source_id in by_id:
            raise RuntimeError(f"duplicate precomputed source id: {{source_id}}")
        by_id[source_id] = row
    expected_ids = [f"SRC-NEWS-{{index:06d}}" for index in range(1, len(news_rows) + 1)]
    if set(by_id) != set(expected_ids):
        raise RuntimeError("precomputed semantic review source-id denominator mismatch")

    duplicate_first: dict[str, str] = {{}}
    duplicate_map: dict[str, str] = {{}}
    ordered: list[dict[str, Any]] = []
    for index, source_id in enumerate(expected_ids, start=1):
        source = news_rows[index - 1]
        review = dict(by_id[source_id])
        if review.get("source_id") != source_id:
            raise RuntimeError("precomputed review ordering mismatch")
        if review.get("full_title_body_reviewed") is not True:
            raise RuntimeError(f"full-title/body review flag missing for {{source_id}}")
        quote = str(review.get("exact_quote") or "")
        source_text = str(source.get("title", "")) + "\\n" + str(source.get("body", ""))
        if not quote or quote not in source_text:
            raise RuntimeError(f"exact quote is not a source substring for {{source_id}}")
        if not review.get("review_decision"):
            raise RuntimeError(f"review decision missing for {{source_id}}")
        if review.get("material_queue_member"):
            binding = str(review.get("issuer_binding_status") or "")
            if not binding.startswith("RESOLVED") and not review.get("rejection_reason"):
                raise RuntimeError(f"material review lacks issuer binding or rejection reason for {{source_id}}")
        review["semantic_reviewer"] = MODEL_NAME
        row_hash = sha256_text(canonical_json(source))
        if row_hash in duplicate_first:
            duplicate_map[source_id] = duplicate_first[row_hash]
        else:
            duplicate_first[row_hash] = source_id
        ordered.append(review)

    for review in ordered:
        source_id = review["source_id"]
        if source_id in duplicate_map:
            review["disposition"] = "DUPLICATE"
            review["material_queue_member"] = False
            review["duplicate_of_source_id"] = duplicate_map[source_id]
            review["screening_recommendation"] = "AUDIT_ONLY"
            review["review_decision"] = "DUPLICATE_RETAINED"
            review["rejection_reason"] = "EXACT_DUPLICATE_OF_EARLIER_CSV_ROW"
    return ordered
'''
    pattern = r"def review_rows\(.*?\n\ndef build_phase_populations\("
    replacement = loader + "\n\ndef build_phase_populations("
    patched, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"review_rows replacement count was {count}")
    blind_path.write_text(patched, encoding="utf-8")


def main() -> None:
    combined_path, matrix_receipt = combine_shards()
    namespace = transformed_runner_namespace()
    acquisition = namespace["prepare_inputs_and_pipeline"]()

    parity_fields = {
        "prompt_sha256": matrix_receipt["prompt_sha256"],
        "prompt_byte_size": matrix_receipt["prompt_byte_size"],
        "news_sha256": matrix_receipt["news_sha256"],
        "news_byte_size": matrix_receipt["news_byte_size"],
        "csv_row_count": matrix_receipt["csv_row_count"],
        "blind_snapshot_sha256": matrix_receipt["blind_snapshot_sha256"],
        "blind_snapshot_byte_size": matrix_receipt["blind_snapshot_byte_size"],
        "blind_snapshot_row_count": matrix_receipt["blind_snapshot_row_count"],
    }
    for field, expected in parity_fields.items():
        if acquisition.get(field) != expected:
            raise RuntimeError(f"aggregate fresh acquisition disagrees with shard population for {field}")

    acquisition["matrix_review_shard_count"] = SHARD_COUNT
    acquisition["matrix_review_population_sha256"] = matrix_receipt["combined_reviews_sha256"]
    acquisition["matrix_review_population_count"] = matrix_receipt["review_row_count"]
    acquisition["matrix_review_source_id_denominator_closed"] = True
    acquisition["blind_snapshot_acquisition_method"] = "RAW_EXACT_VERIFIED_BY_ALL_MATRIX_SHARDS"
    acquisition_path = namespace["WORK"] / "acquisition_receipt.json"
    acquisition_path.write_text(json.dumps(acquisition, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    patch_review_loader(namespace["PIPELINE"] / "blind.py", combined_path, int(acquisition["csv_row_count"]))
    namespace["run"]([
        sys.executable,
        "-m",
        "py_compile",
        str(namespace["PIPELINE"] / "common.py"),
        str(namespace["PIPELINE"] / "blind.py"),
        str(namespace["PIPELINE"] / "reseal.py"),
        str(namespace["PIPELINE"] / "postmortem.py"),
    ])

    namespace["run_blind"](acquisition)
    outcome_path = namespace["acquire_outcome_after_seal"](acquisition)
    final_path = namespace["run_postmortem"](acquisition, outcome_path)

    matrix_receipt_path = combined_path.parent / "matrix_review_population_receipt.json"
    destination = namespace["FINAL_ARTIFACT"] / matrix_receipt_path.name
    destination.write_bytes(matrix_receipt_path.read_bytes())
    if not final_path.is_file() or final_path.stat().st_size <= 0:
        raise RuntimeError("validated final Markdown missing")
    print(json.dumps({
        "status": "ACCEPT_FULL",
        "final": str(final_path),
        "final_sha256": sha256_bytes(final_path.read_bytes()),
        "final_byte_size": final_path.stat().st_size,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
