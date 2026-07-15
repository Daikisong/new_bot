from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

DATE8 = "20180628"
TRADE_DATE = "2018-06-28"
PREVIOUS_TRADE_DATE = "2018-06-27"
NEXT_TRADE_DATE = "2018-06-29"
STAMP = "20260715T222908KST"
EXPECTED_PROMPT_SHA = "b5ba21ce1f6e3a91dacf19e33e16d5db9dface141e90a67e78c8588ba1553029"
EXPECTED_PROMPT_BYTES = 430485
TOKEN = os.environ["GITHUB_TOKEN"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_exact(raw_url: str, api_url: str, dest: Path, label: str) -> tuple[bytes, str, list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": f"NSLAB-MATRIX-{STAMP}-{label}",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    }
    try:
        request = urllib.request.Request(raw_url, headers=headers)
        with urllib.request.urlopen(request, timeout=180) as response:
            data = response.read()
        method = "GITHUB_RAW_EXACT_AFTER_BROWSER_OPEN"
    except Exception as exc:  # noqa: BLE001
        warnings.append({
            "label": label,
            "warning": f"{type(exc).__name__}: {exc}",
            "fallback": "GITHUB_CONTENTS_API_RAW_AFTER_BROWSER_OPEN",
        })
        api_headers = {
            "Authorization": f"Bearer {TOKEN}",
            "User-Agent": f"NSLAB-MATRIX-{STAMP}-{label}",
            "Accept": "application/vnd.github.raw+json",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        }
        request = urllib.request.Request(api_url, headers=api_headers)
        with urllib.request.urlopen(request, timeout=180) as response:
            data = response.read()
        method = "GITHUB_CONTENTS_API_RAW_AFTER_BROWSER_OPEN"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data, method, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0 <= args.shard < args.shards:
        raise ValueError("invalid shard index")

    args.output.mkdir(parents=True, exist_ok=True)
    work = Path(f"fresh_shard_{DATE8}_{STAMP}_{args.shard:02d}")
    work.mkdir(parents=True, exist_ok=False)

    prompt_raw, prompt_method, prompt_warnings = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md?run={STAMP}-shard-{args.shard}",
        "https://api.github.com/repos/Daikisong/new_bot/contents/docs/research_prompt.md?ref=main",
        work / "prompt_tmp.md",
        f"prompt-shard-{args.shard}",
    )
    news_raw, news_method, news_warnings = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_{DATE8}.csv?run={STAMP}-shard-{args.shard}",
        f"https://api.github.com/repos/Daikisong/new_bot/contents/docs/csv/news_{DATE8}.csv?ref=main",
        work / f"news_{DATE8}_tmp.csv",
        f"news-shard-{args.shard}",
    )
    blind_raw, blind_method, blind_warnings = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2018/06/20180627.csv?run={STAMP}-shard-{args.shard}",
        "https://api.github.com/repos/Daikisong/stock-web/contents/atlas/research_daily/snapshots/2018/06/20180627.csv?ref=main",
        work / "blind_snapshot_tmp.csv",
        f"blind-snapshot-shard-{args.shard}",
    )

    checkout_prompt = Path("current_main/docs/research_prompt.md").read_bytes()
    checkout_news = Path(f"current_main/docs/csv/news_{DATE8}.csv").read_bytes()
    checkout_blind = Path("stock_blind/atlas/research_daily/snapshots/2018/06/20180627.csv").read_bytes()
    if prompt_raw != checkout_prompt or news_raw != checkout_news or blind_raw != checkout_blind:
        raise RuntimeError("fresh Raw bytes do not match the exact current-main checkout")

    prompt_sha = sha256_bytes(prompt_raw)
    news_sha = sha256_bytes(news_raw)
    blind_sha = sha256_bytes(blind_raw)
    if prompt_sha != EXPECTED_PROMPT_SHA or len(prompt_raw) != EXPECTED_PROMPT_BYTES:
        raise RuntimeError("locked main prompt hash/size mismatch")
    prompt_text = prompt_raw.decode("utf-8")
    if prompt_text.splitlines()[0] != "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER":
        raise RuntimeError("locked main prompt title mismatch")
    if "nslab.gold_phase_machine.direct_csv_research.locked" not in prompt_text:
        raise RuntimeError("locked main prompt revision marker missing")

    news_path = work / f"news_{DATE8}_tmp.csv"
    blind_path = work / "blind_snapshot_tmp.csv"
    with news_path.open("r", encoding="utf-8-sig", newline="") as handle:
        news_rows = list(csv.DictReader(handle))
    if not news_rows or list(news_rows[0]) != ["page", "row", "date", "time", "title", "body"]:
        raise RuntimeError("news CSV schema mismatch")
    with blind_path.open("r", encoding="utf-8-sig", newline="") as handle:
        snapshot_rows = list(csv.DictReader(handle))

    access = json.loads(Path("stock_blind/atlas/research_daily/access/2018/06/20180628.json").read_text(encoding="utf-8"))
    expected_access = {
        "trade_date": TRADE_DATE,
        "previous_trade_date": PREVIOUS_TRADE_DATE,
        "next_trade_date": NEXT_TRADE_DATE,
        "blind_snapshot_path": "atlas/research_daily/snapshots/2018/06/20180627.csv",
        "outcome_snapshot_path": "atlas/research_daily/snapshots/2018/06/20180628.csv",
        "blind_snapshot_sha256": blind_sha,
        "blind_snapshot_row_count": len(snapshot_rows),
        "blind_snapshot_bytes": len(blind_raw),
    }
    for key, expected in expected_access.items():
        if access.get(key) != expected:
            raise RuntimeError(f"access metadata mismatch {key}: {access.get(key)!r} != {expected!r}")

    pipeline_dir = Path("pipeline_source/temp/nslab_20220819_pipeline").resolve()
    sys.path.insert(0, str(pipeline_dir))
    import common  # type: ignore
    import blind  # type: ignore

    common.MODEL_NAME = "openai/gpt-4.1-mini"
    blind.MODEL_NAME = common.MODEL_NAME
    snapshot_by_code = {row.get("code", "").zfill(6): row for row in snapshot_rows if row.get("code")}

    selected: list[dict[str, Any]] = []
    for index, row in enumerate(news_rows, start=1):
        if (index - 1) % args.shards != args.shard:
            continue
        full_text = f"{row.get('title', '')}\n{row.get('body', '')}"
        selected.append({
            "source_id": f"SRC-NEWS-{index:06d}",
            "published_at_kst": f"{row.get('date')}T{row.get('time')}+09:00",
            "title": row.get("title", ""),
            "body": row.get("body", ""),
            "krx_candidate_options": common.make_krx_options(full_text, snapshot_rows, snapshot_by_code),
        })

    reviews_by_id: dict[str, dict[str, Any]] = {}
    log_path = args.output / f"model_call_log_shard_{args.shard:02d}.jsonl"

    def process(batch: list[dict[str, Any]], label: str) -> None:
        try:
            parsed = common.model_json(
                TOKEN,
                system=blind.detailed_review_system(),
                user=blind.detailed_review_user(batch),
                label=label,
                log_path=log_path,
                max_tokens=12000,
                attempts=8,
            )
            records = parsed.get("records") if isinstance(parsed, dict) else parsed
            if not isinstance(records, list):
                raise ValueError("model response lacks records array")
            expected_ids = {item["source_id"] for item in batch}
            actual_ids = {str(item.get("source_id")) for item in records if isinstance(item, dict)}
            if expected_ids != actual_ids or len(records) != len(batch):
                raise ValueError(f"model record coverage mismatch expected={len(expected_ids)} actual={len(actual_ids)}")
            raw_by_id = {str(item["source_id"]): item for item in records}
            for input_row in batch:
                normalized = blind.normalize_review(raw_by_id[input_row["source_id"]], input_row, snapshot_by_code)
                if not normalized.get("full_title_body_reviewed"):
                    raise ValueError("full title/body review flag missing")
                quote = str(normalized.get("exact_quote") or "")
                if not quote or quote not in (input_row["title"] + "\n" + input_row["body"]):
                    raise ValueError("exact quote is not a non-empty source substring")
                if normalized.get("material_queue_member"):
                    if not normalized.get("review_decision"):
                        raise ValueError("material review decision missing")
                    binding = str(normalized.get("issuer_binding_status") or "")
                    if not binding.startswith("RESOLVED") and not normalized.get("rejection_reason"):
                        raise ValueError("material row lacks issuer binding or rejection reason")
                reviews_by_id[input_row["source_id"]] = normalized
        except Exception:
            if len(batch) == 1:
                raise
            midpoint = len(batch) // 2
            process(batch[:midpoint], label + "-A")
            process(batch[midpoint:], label + "-B")

    batches = list(common.row_batches(selected, max_items=3, max_chars=26000))
    for batch_index, batch in enumerate(batches, start=1):
        process(batch, f"MATRIX_FULL_ROW_REVIEW_S{args.shard:02d}_B{batch_index:03d}")

    if len(reviews_by_id) != len(selected):
        raise RuntimeError(f"shard review coverage mismatch {len(reviews_by_id)} != {len(selected)}")
    ordered = sorted(reviews_by_id.values(), key=lambda row: row["source_id"])
    output_path = args.output / f"reviews_shard_{args.shard:02d}.jsonl"
    output_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in ordered) + "\n",
        encoding="utf-8",
    )

    receipt = {
        "schema_version": "nslab.matrix_review_shard_receipt.v1",
        "shard": args.shard,
        "shards": args.shards,
        "selected_row_count": len(selected),
        "review_row_count": len(ordered),
        "first_source_id": ordered[0]["source_id"] if ordered else None,
        "last_source_id": ordered[-1]["source_id"] if ordered else None,
        "prompt_sha256": prompt_sha,
        "prompt_byte_size": len(prompt_raw),
        "news_sha256": news_sha,
        "news_byte_size": len(news_raw),
        "csv_row_count": len(news_rows),
        "blind_snapshot_sha256": blind_sha,
        "blind_snapshot_byte_size": len(blind_raw),
        "blind_snapshot_row_count": len(snapshot_rows),
        "prompt_acquisition_method": prompt_method,
        "news_acquisition_method": news_method,
        "blind_snapshot_acquisition_method": blind_method,
        "acquisition_warnings": prompt_warnings + news_warnings + blind_warnings,
        "preseal_outcome_access_count": 0,
        "review_payload_sha256": sha256_bytes(output_path.read_bytes()),
    }
    (args.output / f"acquisition_shard_{args.shard:02d}.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "SHARD_COMPLETE", "shard": args.shard, "rows": len(ordered)}, sort_keys=True))


if __name__ == "__main__":
    main()
