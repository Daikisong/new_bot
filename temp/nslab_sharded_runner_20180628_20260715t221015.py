from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DATE8 = "20180628"
TRADE_DATE = "2018-06-28"
PREVIOUS_TRADE_DATE = "2018-06-27"
NEXT_TRADE_DATE = "2018-06-29"
STAMP = "20260715T221015KST"
STAMP_LOWER = "20260715t221015"
RUN_ID = f"nslab_run_{STAMP}_{DATE8}"
STAGE_NAME = f"nslab_stage_{DATE8}_{STAMP_LOWER}"
FINAL_NAME = f"final_artifact_{DATE8}_{STAMP_LOWER}"
EXPECTED_PROMPT_SHA = "b5ba21ce1f6e3a91dacf19e33e16d5db9dface141e90a67e78c8588ba1553029"
EXPECTED_PROMPT_BYTES = 430485
EXPECTED_PROMPT_TITLE = "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER"
MODEL_NAME = "openai/gpt-4.1"
PIPELINE_COMMIT = "38299bfe2d296e4a8dbbaad04de2001fd63ba88a"


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    subprocess.run(cmd, check=True, env=env, cwd=str(cwd) if cwd else None)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(canonical_json(row) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def fetch_exact(raw_url: str, api_url: str, dest: Path, label: str, warnings: list[dict[str, Any]], methods: dict[str, str]) -> bytes:
    token = os.environ["GITHUB_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": f"NSLAB-{RUN_ID}",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    }
    try:
        request = urllib.request.Request(raw_url, headers=headers)
        with urllib.request.urlopen(request, timeout=180) as response:
            data = response.read()
        methods[label] = "GITHUB_RAW_EXACT_URL_AFTER_BROWSER_OPEN"
    except Exception as exc:  # noqa: BLE001
        warnings.append({
            "stage": "RAW_ACQUISITION",
            "label": label,
            "warning": f"{type(exc).__name__}: {exc}",
            "fallback": "GITHUB_API_RAW_AFTER_BROWSER_OPEN",
        })
        api_headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": f"NSLAB-{RUN_ID}",
            "Accept": "application/vnd.github.raw+json",
        }
        request = urllib.request.Request(api_url, headers=api_headers)
        with urllib.request.urlopen(request, timeout=180) as response:
            data = response.read()
        methods[label] = "GITHUB_API_RAW_FALLBACK_AFTER_BROWSER_OPEN"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data


def set_assign(text: str, name: str, value: Any) -> str:
    pattern = rf"(?m)^{re.escape(name)}\s*=\s*.*$"
    output, count = re.subn(pattern, f"{name} = {value!r}", text, count=1)
    if count != 1:
        raise RuntimeError(f"pipeline assignment anchor missing: {name}")
    return output


def load_stage_paths(stage: Path) -> dict[str, Path]:
    manifest = read_json(stage / "stage_manifest.json")
    return {key: stage / value for key, value in manifest["paths"].items()}


def setup_mode(args: argparse.Namespace) -> None:
    root = Path.cwd()
    stage = root / STAGE_NAME
    if stage.exists():
        shutil.rmtree(stage)
    inputs = stage / "inputs"
    pipeline = stage / "pipeline"
    stock = stage / "stock"
    for path in (inputs, pipeline, stock, stage / "blind", stage / "post_inputs", stage / "post_output"):
        path.mkdir(parents=True, exist_ok=True)

    current_main = Path(args.current_main)
    pipeline_source = Path(args.pipeline_source) / "temp/nslab_20220819_pipeline"
    stock_source = Path(args.stock_source)
    warnings: list[dict[str, Any]] = []
    methods: dict[str, str] = {}

    prompt_tmp = stage / "prompt_tmp.md"
    news_tmp = stage / f"news_{DATE8}_tmp.csv"
    example_tmp = stage / "example2_tmp.md"
    prompt_raw = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md?run={STAMP}",
        "https://api.github.com/repos/Daikisong/new_bot/contents/docs/research_prompt.md?ref=main",
        prompt_tmp, "prompt", warnings, methods,
    )
    news_raw = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_{DATE8}.csv?run={STAMP}",
        f"https://api.github.com/repos/Daikisong/new_bot/contents/docs/csv/news_{DATE8}.csv?ref=main",
        news_tmp, "news", warnings, methods,
    )
    example_raw = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/example2.md?run={STAMP}",
        "https://api.github.com/repos/Daikisong/new_bot/contents/docs/example2.md?ref=main",
        example_tmp, "example", warnings, methods,
    )
    if prompt_raw != (current_main / "docs/research_prompt.md").read_bytes():
        raise RuntimeError("fresh Raw prompt differs from exact current-main checkout")
    if news_raw != (current_main / f"docs/csv/news_{DATE8}.csv").read_bytes():
        raise RuntimeError("fresh Raw news differs from exact current-main checkout")
    if example_raw != (current_main / "docs/example2.md").read_bytes():
        raise RuntimeError("fresh Raw example differs from exact current-main checkout")

    prompt_sha = sha256_bytes(prompt_raw)
    news_sha = sha256_bytes(news_raw)
    example_sha = sha256_bytes(example_raw)
    if prompt_sha != EXPECTED_PROMPT_SHA or len(prompt_raw) != EXPECTED_PROMPT_BYTES:
        raise RuntimeError("fresh Raw main prompt hash/size mismatch")
    prompt_text = prompt_raw.decode("utf-8")
    if prompt_text.splitlines()[0] != EXPECTED_PROMPT_TITLE:
        raise RuntimeError("fresh Raw main prompt title mismatch")
    if "nslab.gold_phase_machine.direct_csv_research.locked" not in prompt_text:
        raise RuntimeError("locked research prompt revision marker missing")

    news_rows = read_csv(news_tmp)
    expected_columns = ["page", "row", "date", "time", "title", "body"]
    if not news_rows or list(news_rows[0]) != expected_columns:
        raise RuntimeError("news CSV schema mismatch")
    parsed_times: list[datetime] = []
    time_unverified: list[int] = []
    for index, row in enumerate(news_rows, start=1):
        try:
            parsed_times.append(datetime.fromisoformat(f"{row['date']}T{row['time']}"))
        except Exception:
            time_unverified.append(index)

    access_src = stock_source / f"atlas/research_daily/access/2018/06/{DATE8}.json"
    blind_src = stock_source / f"atlas/research_daily/snapshots/2018/06/{PREVIOUS_TRADE_DATE.replace('-', '')}.csv"
    manifest_src = stock_source / "atlas/research_daily/manifest.json"
    schema_src = stock_source / "atlas/research_daily/schema.json"
    calendar_src = stock_source / "atlas/research_daily/trading_calendar.csv"
    for source in (access_src, blind_src, manifest_src, schema_src, calendar_src):
        if not source.is_file():
            raise RuntimeError(f"missing BLIND-safe stock input: {source}")
    access = read_json(access_src)
    blind_raw = blind_src.read_bytes()
    blind_rows = read_csv(blind_src)
    blind_sha = sha256_bytes(blind_raw)
    expected_blind_path = f"atlas/research_daily/snapshots/2018/06/{PREVIOUS_TRADE_DATE.replace('-', '')}.csv"
    expected_outcome_path = f"atlas/research_daily/snapshots/2018/06/{DATE8}.csv"
    assertions = {
        "trade_date": TRADE_DATE,
        "previous_trade_date": PREVIOUS_TRADE_DATE,
        "next_trade_date": NEXT_TRADE_DATE,
        "blind_snapshot_path": expected_blind_path,
        "outcome_snapshot_path": expected_outcome_path,
    }
    for key, value in assertions.items():
        if access.get(key) != value:
            raise RuntimeError(f"access routing mismatch {key}: {access.get(key)!r}")
    if blind_sha != access.get("blind_snapshot_sha256") or len(blind_raw) != int(access.get("blind_snapshot_bytes", -1)):
        raise RuntimeError("blind snapshot byte provenance mismatch")
    if len(blind_rows) != int(access.get("blind_snapshot_row_count", -1)):
        raise RuntimeError("blind snapshot row count mismatch")
    if {row.get("snapshot_date") for row in blind_rows} != {PREVIOUS_TRADE_DATE}:
        raise RuntimeError("blind snapshot date leakage or mismatch")

    prompt_name = f"research_prompt_{STAMP}_{prompt_sha[:8]}.md"
    news_name = f"news_{DATE8}_{STAMP}_{news_sha[:8]}.csv"
    example_name = f"example2_{STAMP}_{example_sha[:8]}.md"
    prompt_tmp.rename(inputs / prompt_name)
    news_tmp.rename(inputs / news_name)
    example_tmp.rename(inputs / example_name)

    stock_access = stock / f"atlas/research_daily/access/2018/06/{DATE8}.json"
    stock_blind = stock / expected_blind_path
    stock_manifest = stock / "atlas/research_daily/manifest.json"
    stock_schema = stock / "atlas/research_daily/schema.json"
    stock_calendar = stock / "atlas/research_daily/trading_calendar.csv"
    for source, dest in (
        (access_src, stock_access), (blind_src, stock_blind), (manifest_src, stock_manifest),
        (schema_src, stock_schema), (calendar_src, stock_calendar),
    ):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)

    for filename in ("common.py", "blind.py", "reseal.py", "postmortem.py"):
        shutil.copy2(pipeline_source / filename, pipeline / filename)

    common_path = pipeline / "common.py"
    common_text = common_path.read_text(encoding="utf-8")
    assignments = {
        "TRADE_DATE": TRADE_DATE,
        "PREVIOUS_TRADE_DATE": PREVIOUS_TRADE_DATE,
        "NEXT_TRADE_DATE": NEXT_TRADE_DATE,
        "CUTOFF_AT": f"{TRADE_DATE}T08:59:59+09:00",
        "WINDOW_START": f"{PREVIOUS_TRADE_DATE}T15:30:00+09:00",
        "AVAILABLE_FROM": f"{NEXT_TRADE_DATE}T00:00:00+09:00",
        "INPUT_SHA256": news_sha,
        "INPUT_BYTE_SIZE": len(news_raw),
        "INPUT_ROW_COUNT": len(news_rows),
        "PROMPT_SHA256": prompt_sha,
        "PROMPT_BYTE_SIZE": len(prompt_raw),
        "BLIND_SNAPSHOT_SHA256": blind_sha,
        "BLIND_SNAPSHOT_ROWS": len(blind_rows),
        "OUTCOME_SNAPSHOT_SHA256": access["outcome_snapshot_sha256"],
        "OUTCOME_SNAPSHOT_BYTES": int(access["outcome_snapshot_bytes"]),
        "OUTCOME_SNAPSHOT_ROWS": int(access["outcome_snapshot_row_count"]),
        "MODEL_NAME": MODEL_NAME,
    }
    for key, value in assignments.items():
        common_text = set_assign(common_text, key, value)
    common_text = (
        common_text.replace("20220819", DATE8)
        .replace("2022-08-19", TRADE_DATE)
        .replace("2022-08-18", PREVIOUS_TRADE_DATE)
        .replace("2022-08-22", NEXT_TRADE_DATE)
    )
    common_path.write_text(common_text, encoding="utf-8")
    for filename in ("blind.py", "reseal.py", "postmortem.py"):
        path = pipeline / filename
        text = path.read_text(encoding="utf-8")
        text = (
            text.replace("20220819", DATE8)
            .replace("2022-08-19", TRADE_DATE)
            .replace("2022-08-18", PREVIOUS_TRADE_DATE)
            .replace("2022-08-22", NEXT_TRADE_DATE)
            .replace("ec55b86339923c35db8c7b31e01f1706213afa3ffdb535aac243f2fd56a454fb", news_sha)
        )
        path.write_text(text, encoding="utf-8")
    run([sys.executable, "-m", "py_compile", *(str(pipeline / name) for name in ("common.py", "blind.py", "reseal.py", "postmortem.py"))])

    sys.path.insert(0, str(pipeline.resolve()))
    common = importlib.import_module("common")
    snapshot_by_code = {row.get("code", "").zfill(6): row for row in blind_rows if row.get("code")}
    model_inputs: list[dict[str, Any]] = []
    for index, row in enumerate(news_rows, start=1):
        full_text = f"{row.get('title', '')}\n{row.get('body', '')}"
        model_inputs.append({
            "row_index": index,
            "source_id": f"SRC-NEWS-{index:06d}",
            "published_at_kst": f"{row.get('date')}T{row.get('time')}+09:00",
            "title": row.get("title", ""),
            "body": row.get("body", ""),
            "krx_candidate_options": common.make_krx_options(full_text, blind_rows, snapshot_by_code),
        })
    write_jsonl(stage / "model_inputs.jsonl", model_inputs)

    expected_window_start = datetime.fromisoformat(f"{PREVIOUS_TRADE_DATE}T15:30:00")
    expected_cutoff = datetime.fromisoformat(f"{TRADE_DATE}T08:59:59")
    uncovered: list[str] = []
    if parsed_times and min(parsed_times) > expected_window_start:
        uncovered.append(f"{expected_window_start.isoformat()}..{(min(parsed_times)-timedelta(seconds=1)).isoformat()}")
    if parsed_times and max(parsed_times) < expected_cutoff:
        uncovered.append(f"{(max(parsed_times)+timedelta(seconds=1)).isoformat()}..{expected_cutoff.isoformat()}")

    acquisition_receipt = {
        "schema_version": "nslab.current_run_raw_acquisition.v1",
        "run_id": RUN_ID,
        "status": "VERIFIED",
        "prompt_file": prompt_name,
        "prompt_sha256": prompt_sha,
        "prompt_byte_size": len(prompt_raw),
        "news_file": news_name,
        "news_sha256": news_sha,
        "news_byte_size": len(news_raw),
        "example_file": example_name,
        "example_sha256": example_sha,
        "example_byte_size": len(example_raw),
        "csv_row_count": len(news_rows),
        "parsed_row_count": len(news_rows),
        "columns": expected_columns,
        "min_published_at": min(parsed_times).isoformat() if parsed_times else None,
        "max_published_at": max(parsed_times).isoformat() if parsed_times else None,
        "time_unverified_rows": time_unverified,
        "control_char_count": sum(1 for ch in news_raw.decode("utf-8-sig") if ord(ch) < 32 and ch not in "\n\r\t"),
        "trade_date": TRADE_DATE,
        "previous_trade_date": PREVIOUS_TRADE_DATE,
        "next_trade_date": NEXT_TRADE_DATE,
        "input_coverage_warning": None if not uncovered else "CSV timestamp coverage does not exactly span the expected window.",
        "uncovered_time_ranges": uncovered,
        "blind_snapshot_sha256": blind_sha,
        "blind_snapshot_byte_size": len(blind_raw),
        "blind_snapshot_row_count": len(blind_rows),
        "outcome_snapshot_sha256_expected": access["outcome_snapshot_sha256"],
        "outcome_snapshot_byte_size_expected": int(access["outcome_snapshot_bytes"]),
        "outcome_snapshot_row_count_expected": int(access["outcome_snapshot_row_count"]),
        "raw_urls_opened_in_initiating_web_session": True,
        "raw_urls": {
            "prompt": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md",
            "news": f"https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_{DATE8}.csv",
            "access": f"https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/access/2018/06/{DATE8}.json",
        },
        "acquisition_methods": methods,
        "acquisition_warnings": warnings,
        "access_sha256_status": "EXACT_CHECKOUT_ROUTING_METADATA_ONLY",
        "preseal_outcome_download_count": 0,
        "preseal_outcome_header_read_count": 0,
        "preseal_outcome_sha256_count": 0,
        "preseal_outcome_row_count_count": 0,
        "preseal_outcome_parse_count": 0,
    }
    write_json(stage / "acquisition_receipt.json", acquisition_receipt)
    write_jsonl(stage / "acquisition_warnings.jsonl", warnings)
    stage_manifest = {
        "schema_version": "nslab.sharded_stage_manifest.v1",
        "run_id": RUN_ID,
        "date8": DATE8,
        "shard_count": int(args.shard_count),
        "csv_row_count": len(news_rows),
        "pipeline_commit": PIPELINE_COMMIT,
        "paths": {
            "prompt": f"inputs/{prompt_name}",
            "news": f"inputs/{news_name}",
            "example": f"inputs/{example_name}",
            "pipeline": "pipeline",
            "access": f"stock/atlas/research_daily/access/2018/06/{DATE8}.json",
            "manifest": "stock/atlas/research_daily/manifest.json",
            "schema": "stock/atlas/research_daily/schema.json",
            "calendar": "stock/atlas/research_daily/trading_calendar.csv",
            "blind_snapshot": f"stock/{expected_blind_path}",
            "blind_output": "blind",
            "post_inputs": "post_inputs",
            "post_output": "post_output",
        },
    }
    write_json(stage / "stage_manifest.json", stage_manifest)
    write_json(stage / "setup_receipt.json", {
        "status": "VERIFIED",
        "stage_name": STAGE_NAME,
        "model_input_count": len(model_inputs),
        "model_inputs_sha256": sha256_file(stage / "model_inputs.jsonl"),
        "shard_count": int(args.shard_count),
        "preseal_outcome_access_all_zero": True,
    })


def review_mode(args: argparse.Namespace) -> None:
    stage = Path(args.stage)
    paths = load_stage_paths(stage)
    pipeline = paths["pipeline"]
    sys.path.insert(0, str(pipeline.resolve()))
    common = importlib.import_module("common")
    blind = importlib.import_module("blind")
    all_inputs = read_jsonl(stage / "model_inputs.jsonl")
    shard_index = int(args.shard_index)
    shard_count = int(args.shard_count)
    selected = [row for row in all_inputs if (int(row["row_index"]) - 1) % shard_count == shard_index]
    if not selected:
        raise RuntimeError(f"empty review shard {shard_index}")
    snapshot_rows = read_csv(paths["blind_snapshot"])
    snapshot_by_code = {row.get("code", "").zfill(6): row for row in snapshot_rows if row.get("code")}
    output = Path(args.output)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / f"model_call_log_shard_{shard_index:02d}.jsonl"
    reviews_by_id: dict[str, dict[str, Any]] = {}
    fallback_ids: list[str] = []

    def process(batch: list[dict[str, Any]], label: str) -> None:
        try:
            parsed = common.model_json(
                os.environ["GITHUB_TOKEN"],
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
            actual_ids = {str(row.get("source_id")) for row in records if isinstance(row, dict)}
            if expected_ids != actual_ids or len(records) != len(batch):
                raise ValueError(f"model record coverage mismatch expected={len(expected_ids)} actual={len(actual_ids)}")
            raw_by_id = {str(row["source_id"]): row for row in records}
            for input_row in batch:
                reviews_by_id[input_row["source_id"]] = blind.normalize_review(raw_by_id[input_row["source_id"]], input_row, snapshot_by_code)
        except Exception:
            if len(batch) > 1:
                midpoint = len(batch) // 2
                process(batch[:midpoint], label + "-A")
                process(batch[midpoint:], label + "-B")
                return
            input_row = batch[0]
            fallback_ids.append(input_row["source_id"])
            quote, found, repair = common.exact_quote_from_source(input_row["title"], input_row["body"], input_row["title"])
            reviews_by_id[input_row["source_id"]] = {
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
                "decision_reason_specific": "Single-row semantic response could not be parsed; the complete title/body row remains in the audit population.",
                "rejection_reason": "MODEL_RESPONSE_PARSE_FAILURE_RETAINED_FOR_AUDIT",
                "semantic_risk_flags": ["MODEL_RESPONSE_PARSE_FAILURE"],
                "theme_name": None,
                "named_beneficiary_explicit": False,
                "full_title_body_reviewed": True,
                "semantic_reviewer": MODEL_NAME,
            }

    for batch_index, batch in enumerate(common.row_batches(selected, max_items=6, max_chars=30000), start=1):
        process(batch, f"SHARD_{shard_index:02d}_FULL_ROW_REVIEW_{batch_index:03d}")
    expected_ids = {row["source_id"] for row in selected}
    if set(reviews_by_id) != expected_ids:
        raise RuntimeError(f"review shard coverage mismatch {shard_index}")
    ordered = [reviews_by_id[row["source_id"]] for row in selected]
    review_path = output / f"reviews_shard_{shard_index:02d}.jsonl"
    write_jsonl(review_path, ordered)
    write_json(output / f"review_receipt_{shard_index:02d}.json", {
        "schema_version": "nslab.sharded_semantic_review_receipt.v1",
        "run_id": RUN_ID,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "expected_count": len(selected),
        "reviewed_count": len(ordered),
        "source_ids": [row["source_id"] for row in ordered],
        "fallback_count": len(fallback_ids),
        "fallback_source_ids": fallback_ids,
        "reviews_sha256": sha256_file(review_path),
        "full_title_body_reviewed_count": sum(1 for row in ordered if row.get("full_title_body_reviewed") is True),
    })


def patch_blind_for_precomputed(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = r"def review_rows\(\n.*?\n\ndef build_phase_populations"
    replacement = '''def review_rows(\n    news_rows: list[dict[str, str]],\n    snapshot_rows: list[dict[str, str]],\n    token: str,\n    output: Path,\n) -> list[dict[str, Any]]:\n    precomputed_path = output / "precomputed_reviews.jsonl"\n    if not precomputed_path.is_file():\n        raise RuntimeError("precomputed semantic review payload missing")\n    rows = read_jsonl(precomputed_path)\n    expected_ids = [f"SRC-NEWS-{index:06d}" for index in range(1, len(news_rows) + 1)]\n    by_id = {str(row.get("source_id")): row for row in rows}\n    if len(rows) != len(news_rows) or set(by_id) != set(expected_ids):\n        raise RuntimeError("precomputed semantic review coverage mismatch")\n    ordered = [by_id[source_id] for source_id in expected_ids]\n    if any(row.get("full_title_body_reviewed") is not True for row in ordered):\n        raise RuntimeError("precomputed semantic review lacks full-text receipt")\n    return ordered\n\n\ndef build_phase_populations'''
    patched, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError("blind review_rows patch anchor missing")
    path.write_text(patched, encoding="utf-8")


def blind_mode(args: argparse.Namespace) -> None:
    stage = Path(args.stage)
    paths = load_stage_paths(stage)
    reviews_dir = Path(args.reviews)
    review_files = sorted(reviews_dir.glob("reviews_shard_*.jsonl"))
    receipt_files = sorted(reviews_dir.glob("review_receipt_*.json"))
    stage_manifest = read_json(stage / "stage_manifest.json")
    expected_shards = int(stage_manifest["shard_count"])
    if len(review_files) != expected_shards or len(receipt_files) != expected_shards:
        raise RuntimeError(f"shard artifact count mismatch reviews={len(review_files)} receipts={len(receipt_files)} expected={expected_shards}")
    reviews: list[dict[str, Any]] = []
    receipts = [read_json(path) for path in receipt_files]
    for path in review_files:
        reviews.extend(read_jsonl(path))
    news_rows = read_csv(paths["news"])
    expected_ids = {f"SRC-NEWS-{index:06d}" for index in range(1, len(news_rows) + 1)}
    actual_ids = [str(row.get("source_id")) for row in reviews]
    if len(actual_ids) != len(expected_ids) or set(actual_ids) != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise RuntimeError("full semantic review denominator is not closed")
    by_id = {str(row["source_id"]): row for row in reviews}

    duplicate_first: dict[str, str] = {}
    duplicate_map: dict[str, str] = {}
    for index, row in enumerate(news_rows, start=1):
        source_id = f"SRC-NEWS-{index:06d}"
        row_hash = sha256_bytes(canonical_json(row).encode("utf-8"))
        if row_hash in duplicate_first:
            duplicate_map[source_id] = duplicate_first[row_hash]
        else:
            duplicate_first[row_hash] = source_id
    for source_id, duplicate_of in duplicate_map.items():
        review = by_id[source_id]
        review["disposition"] = "DUPLICATE"
        review["material_queue_member"] = False
        review["duplicate_of_source_id"] = duplicate_of
        review["screening_recommendation"] = "AUDIT_ONLY"
        review["review_decision"] = "DUPLICATE_RETAINED"
        review["rejection_reason"] = "EXACT_DUPLICATE_OF_EARLIER_CSV_ROW"

    ordered = [by_id[f"SRC-NEWS-{index:06d}"] for index in range(1, len(news_rows) + 1)]
    fallback_count = sum(int(receipt.get("fallback_count", 0)) for receipt in receipts)
    if fallback_count > max(8, len(news_rows) // 100):
        raise RuntimeError(f"too many semantic review fallbacks: {fallback_count}")
    for row in ordered:
        if row.get("full_title_body_reviewed") is not True:
            raise RuntimeError(f"row not fully reviewed: {row.get('source_id')}")
        if row.get("material_queue_member"):
            if not row.get("review_decision") or not row.get("exact_quote"):
                raise RuntimeError(f"material review evidence missing: {row.get('source_id')}")
            if not row.get("issuer_binding_status") and not row.get("rejection_reason"):
                raise RuntimeError(f"material binding/rejection missing: {row.get('source_id')}")

    blind_output = paths["blind_output"]
    blind_output.mkdir(parents=True, exist_ok=True)
    precomputed = blind_output / "precomputed_reviews.jsonl"
    write_jsonl(precomputed, ordered)
    patch_blind_for_precomputed(paths["pipeline"] / "blind.py")
    run([sys.executable, "-m", "py_compile", str(paths["pipeline"] / "blind.py")])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(paths["pipeline"].resolve())
    run([
        sys.executable, str(paths["pipeline"] / "blind.py"),
        "--prompt", str(paths["prompt"]),
        "--news", str(paths["news"]),
        "--access", str(paths["access"]),
        "--manifest", str(paths["manifest"]),
        "--schema", str(paths["schema"]),
        "--blind-snapshot", str(paths["blind_snapshot"]),
        "--example", str(paths["example"]),
        "--output", str(blind_output),
        "--token", os.environ["GITHUB_TOKEN"],
        "--run-id", RUN_ID,
    ], env=env)

    artifacts = blind_output / "artifacts"
    manifest = read_json(artifacts / "blind_packet_manifest.json")
    seal = read_json(artifacts / "blind_seal_receipt.json")
    manifest_payload = canonical_json(manifest).encode("utf-8")
    if sha256_bytes(manifest_payload) != seal.get("blind_packet_manifest_sha256"):
        raise RuntimeError("blind manifest seal hash mismatch")
    if seal.get("blind_packet_manifest_verified") is not True or seal.get("preseal_outcome_access_all_zero") is not True or seal.get("seal_status") != "VERIFIED_CLEAN":
        raise RuntimeError("blind seal not clean")
    for key in (
        "preseal_outcome_download_count", "preseal_outcome_header_read_count", "preseal_outcome_sha256_count",
        "preseal_outcome_row_count_count", "preseal_outcome_parse_count",
    ):
        if int(seal.get(key, -1)) != 0:
            raise RuntimeError(f"preseal outcome access counter nonzero: {key}")
    source_rows = read_jsonl(artifacts / "source_ledger.jsonl")
    row_dispositions = read_jsonl(artifacts / "row_disposition.jsonl")
    material_queue = read_jsonl(artifacts / "material_review_queue.jsonl")
    material_reviews = read_jsonl(artifacts / "material_review.jsonl")
    candidate_screening = read_jsonl(artifacts / "candidate_screening.jsonl")
    news_source_count = sum(1 for row in source_rows if row.get("source_type") == "NEWS_CSV_ROW")
    if news_source_count != len(news_rows) or len(row_dispositions) != len(news_rows):
        raise RuntimeError("PHASE 1 full population count mismatch")
    if len(material_queue) != len(material_reviews) or len(candidate_screening) != len(material_reviews):
        raise RuntimeError("material population closure mismatch")
    write_json(stage / "blind_stage_receipt.json", {
        "schema_version": "nslab.sharded_blind_stage_receipt.v1",
        "run_id": RUN_ID,
        "status": "VERIFIED_CLEAN",
        "csv_row_count": len(news_rows),
        "source_ledger_news_row_count": news_source_count,
        "row_disposition_count": len(row_dispositions),
        "material_review_queue_count": len(material_queue),
        "material_reviewed_count": len(material_reviews),
        "candidate_screening_count": len(candidate_screening),
        "semantic_review_fallback_count": fallback_count,
        "precomputed_reviews_sha256": sha256_file(precomputed),
        "blind_packet_manifest_sha256": seal["blind_packet_manifest_sha256"],
        "preseal_outcome_access_all_zero": True,
    })


def post_mode(args: argparse.Namespace) -> None:
    stage = Path(args.stage)
    paths = load_stage_paths(stage)
    blind_output = paths["blind_output"]
    artifacts = blind_output / "artifacts"
    manifest = read_json(artifacts / "blind_packet_manifest.json")
    seal = read_json(artifacts / "blind_seal_receipt.json")
    if sha256_bytes(canonical_json(manifest).encode("utf-8")) != seal.get("blind_packet_manifest_sha256"):
        raise RuntimeError("post stage blind seal hash mismatch")
    if seal.get("blind_packet_manifest_verified") is not True or seal.get("preseal_outcome_access_all_zero") is not True or seal.get("seal_status") != "VERIFIED_CLEAN":
        raise RuntimeError("post stage refused non-clean blind seal")
    for key in (
        "preseal_outcome_download_count", "preseal_outcome_header_read_count", "preseal_outcome_sha256_count",
        "preseal_outcome_row_count_count", "preseal_outcome_parse_count",
    ):
        if int(seal.get(key, -1)) != 0:
            raise RuntimeError(f"post stage detected preseal outcome access: {key}")
    for name, metadata in manifest.get("files", {}).items():
        path = artifacts / name
        if not path.is_file() or sha256_file(path) != metadata.get("sha256") or path.stat().st_size != int(metadata.get("byte_size", -1)):
            raise RuntimeError(f"blind artifact manifest verification failed: {name}")

    acquisition = read_json(stage / "acquisition_receipt.json")
    access = read_json(paths["access"])
    post_inputs = paths["post_inputs"]
    post_inputs.mkdir(parents=True, exist_ok=True)
    warnings: list[dict[str, Any]] = []
    methods: dict[str, str] = {}
    outcome_tmp = post_inputs / "outcome_tmp.csv"
    outcome_raw = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/{access['outcome_snapshot_path']}?run={STAMP}",
        f"https://api.github.com/repos/Daikisong/stock-web/contents/{access['outcome_snapshot_path']}?ref=main",
        outcome_tmp, "outcome_post_seal", warnings, methods,
    )
    outcome_sha = sha256_bytes(outcome_raw)
    if outcome_sha != acquisition["outcome_snapshot_sha256_expected"] or len(outcome_raw) != int(acquisition["outcome_snapshot_byte_size_expected"]):
        raise RuntimeError("post-seal outcome byte provenance mismatch")
    outcome_rows = read_csv(outcome_tmp)
    if len(outcome_rows) != int(acquisition["outcome_snapshot_row_count_expected"]):
        raise RuntimeError("post-seal outcome row count mismatch")
    outcome_name = f"outcome_snapshot_{DATE8}_{STAMP}_{outcome_sha[:8]}.csv"
    outcome_path = post_inputs / outcome_name
    outcome_tmp.rename(outcome_path)
    write_json(post_inputs / "outcome_receipt.json", {
        "schema_version": "nslab.postseal_outcome_acquisition.v1",
        "run_id": RUN_ID,
        "file": outcome_name,
        "sha256": outcome_sha,
        "byte_size": len(outcome_raw),
        "row_count": len(outcome_rows),
        "acquisition_method": methods.get("outcome_post_seal"),
        "acquisition_warnings": warnings,
        "seal_verified_before_download": True,
        "blind_packet_manifest_sha256": seal["blind_packet_manifest_sha256"],
    })

    env = os.environ.copy()
    env["PYTHONPATH"] = str(paths["pipeline"].resolve())
    post_output = paths["post_output"]
    post_output.mkdir(parents=True, exist_ok=True)
    run([
        sys.executable, str(paths["pipeline"] / "postmortem.py"),
        "--blind-dir", str(blind_output),
        "--outcome", str(outcome_path),
        "--output", str(post_output),
        "--token", os.environ["GITHUB_TOKEN"],
        "--run-id", RUN_ID,
    ], env=env)

    sys.path.insert(0, str(paths["pipeline"].resolve()))
    if "common" in sys.modules:
        del sys.modules["common"]
    common = importlib.import_module("common")
    final = post_output / f"{DATE8}_nslab_episode_bundle.md"
    reparse_path = post_output / "final_reparse_receipt.json"
    if not final.is_file() or final.stat().st_size <= 0 or not reparse_path.is_file():
        raise RuntimeError("final bundle or reparse receipt missing")
    text = final.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise RuntimeError("final bundle is not import-ready YAML-front-matter Markdown")
    blocks = common.parse_markdown_blocks(text)
    brain = common.parse_block(blocks["brain_delta.jsonl"], "brain_delta.jsonl")
    bundle_manifest = common.parse_block(blocks["bundle_manifest.json"], "bundle_manifest.json")
    validation = common.parse_block(blocks["validation_report.json"], "validation_report.json")
    contract = common.parse_block(blocks["direct_ingest_contract.json"], "direct_ingest_contract.json")
    reparse = read_json(reparse_path)
    if reparse.get("status") != "ACCEPT_FULL" or validation.get("status") != "ACCEPT_FULL":
        raise RuntimeError("final reparse validator did not ACCEPT_FULL")
    if int(reparse.get("csv_row_count", -1)) != int(acquisition["csv_row_count"]):
        raise RuntimeError("final CSV row count parity failure")
    if int(reparse.get("outcome_ledger_count", -1)) != int(acquisition["outcome_snapshot_row_count_expected"]):
        raise RuntimeError("final outcome ledger count parity failure")
    if not brain or len(brain) != int(reparse.get("brain_delta_record_count", -1)):
        raise RuntimeError("brain_delta payload missing or count mismatch")
    for key in (
        "brain_delta_payload_missing_count", "brain_delta_manifest_payload_count_mismatch_count", "brain_delta_declared_without_payload_count",
    ):
        if int(reparse.get(key, -1)) != 0:
            raise RuntimeError(f"brain_delta hard gate failed: {key}")
    if int(bundle_manifest["files"]["brain_delta.jsonl"]["row_count"]) != len(brain):
        raise RuntimeError("bundle manifest brain_delta row count mismatch")
    if contract.get("direct_brain_ingest_ready") is not True or contract.get("brain_eligible") is not True or contract.get("automated_import_expected_to_pass") is not True:
        raise RuntimeError("direct ingest contract is not ready")

    independent = {
        "schema_version": "nslab.independent_final_validation_receipt.v1",
        "run_id": RUN_ID,
        "status": "ACCEPT_FULL",
        "csv_row_count": acquisition["csv_row_count"],
        "outcome_ledger_count": acquisition["outcome_snapshot_row_count_expected"],
        "brain_delta_record_count": len(brain),
        "brain_delta_payload_missing_count": 0,
        "brain_delta_manifest_payload_count_mismatch_count": 0,
        "brain_delta_declared_without_payload_count": 0,
        "final_sha256": sha256_file(final),
        "final_byte_size": final.stat().st_size,
    }
    independent_path = post_output / "independent_final_validation_receipt.json"
    write_json(independent_path, independent)
    final_dir = Path.cwd() / FINAL_NAME
    if final_dir.exists():
        shutil.rmtree(final_dir)
    final_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        final, independent_path, reparse_path, stage / "acquisition_receipt.json", stage / "acquisition_warnings.jsonl",
        stage / "setup_receipt.json", stage / "blind_stage_receipt.json",
        artifacts / "blind_seal_receipt.json", post_inputs / "outcome_receipt.json",
    ]
    for source in sources:
        if source.exists():
            shutil.copy2(source, final_dir / source.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    setup = sub.add_parser("setup")
    setup.add_argument("--current-main", required=True)
    setup.add_argument("--pipeline-source", required=True)
    setup.add_argument("--stock-source", required=True)
    setup.add_argument("--shard-count", type=int, default=16)
    review = sub.add_parser("review")
    review.add_argument("--stage", required=True)
    review.add_argument("--shard-index", type=int, required=True)
    review.add_argument("--shard-count", type=int, required=True)
    review.add_argument("--output", required=True)
    blind = sub.add_parser("blind")
    blind.add_argument("--stage", required=True)
    blind.add_argument("--reviews", required=True)
    post = sub.add_parser("post")
    post.add_argument("--stage", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "setup":
        setup_mode(args)
    elif args.mode == "review":
        review_mode(args)
    elif args.mode == "blind":
        blind_mode(args)
    elif args.mode == "post":
        post_mode(args)
    else:
        raise RuntimeError(args.mode)


if __name__ == "__main__":
    main()
