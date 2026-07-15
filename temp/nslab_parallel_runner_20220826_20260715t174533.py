from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

TRADE_DATE = "2022-08-26"
DATE8 = "20220826"
PREVIOUS_TRADE_DATE = "2022-08-25"
NEXT_TRADE_DATE = "2022-08-29"
STAMP = "20260715T174533KST"
STAMP_LOWER = "20260715t174533"
RUN_ID = f"nslab_run_{STAMP}_{DATE8}"
PIPELINE_COMMIT = "38299bfe2d296e4a8dbbaad04de2001fd63ba88a"
EXPECTED_PROMPT_SHA = "b5ba21ce1f6e3a91dacf19e33e16d5db9dface141e90a67e78c8588ba1553029"
EXPECTED_PROMPT_BYTES = 430485
MODEL_NAME = "openai/gpt-4.1-mini"

ROOT = Path.cwd()
MAIN_INPUTS = ROOT / "main_inputs"
PIPELINE_SOURCE = ROOT / "pipeline_source"
STOCK = ROOT / "stock_blind"
WORK = ROOT / f"work_{DATE8}_{STAMP_LOWER}"
INPUTS = WORK / "inputs"
PIPELINE = WORK / "pipeline"
BLIND_OUT = WORK / "blind"
POST_INPUTS = WORK / "post_inputs"
POST_OUT = WORK / "post_output"
FINAL_ARTIFACT = ROOT / f"final_artifact_{DATE8}_{STAMP_LOWER}"
TOKEN = os.environ["GITHUB_TOKEN"]


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    subprocess.run(cmd, check=True, env=env, cwd=str(cwd) if cwd else None)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fetch_exact(raw_url: str, api_url: str, dest: Path, label: str, warnings: list[dict[str, Any]], methods: dict[str, str]) -> bytes:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": f"NSLAB-{RUN_ID}",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    }
    try:
        req = urllib.request.Request(raw_url, headers=headers)
        with urllib.request.urlopen(req, timeout=240) as response:
            data = response.read()
        methods[label] = "GITHUB_RAW_EXACT_URL_AFTER_WEB_OPEN"
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            {
                "stage": "RAW_ACQUISITION",
                "label": label,
                "warning": f"{type(exc).__name__}: {exc}",
                "fallback": "GITHUB_API_RAW_AFTER_WEB_OPEN",
            }
        )
        api_headers = {
            "Authorization": f"Bearer {TOKEN}",
            "User-Agent": f"NSLAB-{RUN_ID}",
            "Accept": "application/vnd.github.raw+json",
        }
        req = urllib.request.Request(api_url, headers=api_headers)
        with urllib.request.urlopen(req, timeout=240) as response:
            data = response.read()
        methods[label] = "GITHUB_API_RAW_FALLBACK_AFTER_WEB_OPEN"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data


def fresh_prepare() -> dict[str, Any]:
    for path in (WORK, FINAL_ARTIFACT):
        if path.exists():
            shutil.rmtree(path)
    for path in (INPUTS, PIPELINE, BLIND_OUT, POST_INPUTS, POST_OUT, FINAL_ARTIFACT):
        path.mkdir(parents=True, exist_ok=True)

    required_main = [
        MAIN_INPUTS / "docs/research_prompt.md",
        MAIN_INPUTS / "docs/csv/news_20220826.csv",
        MAIN_INPUTS / "docs/example2.md",
    ]
    required_stock = [
        STOCK / "atlas/research_daily/manifest.json",
        STOCK / "atlas/research_daily/schema.json",
        STOCK / "atlas/research_daily/trading_calendar.csv",
        STOCK / "atlas/research_daily/access/2022/08/20220826.json",
        STOCK / "atlas/research_daily/snapshots/2022/08/20220825.csv",
    ]
    for path in required_main + required_stock:
        if not path.is_file():
            raise RuntimeError(f"required fresh checkout input missing: {path}")

    for filename in ("common.py", "blind.py", "reseal.py", "postmortem.py"):
        source = PIPELINE_SOURCE / "temp/nslab_20220819_pipeline" / filename
        if not source.is_file():
            raise RuntimeError(f"pipeline source missing: {source}")
        shutil.copy2(source, PIPELINE / filename)

    warnings: list[dict[str, Any]] = []
    methods: dict[str, str] = {}
    prompt_tmp = WORK / "prompt_tmp.md"
    news_tmp = WORK / "news_20220826_tmp.csv"
    example_tmp = WORK / "example2_tmp.md"
    blind_tmp = WORK / "blind_snapshot_20220825_tmp.csv"

    prompt_raw = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md?run={STAMP}",
        "https://api.github.com/repos/Daikisong/new_bot/contents/docs/research_prompt.md?ref=main",
        prompt_tmp,
        "prompt",
        warnings,
        methods,
    )
    news_raw = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_20220826.csv?run={STAMP}",
        "https://api.github.com/repos/Daikisong/new_bot/contents/docs/csv/news_20220826.csv?ref=main",
        news_tmp,
        "news",
        warnings,
        methods,
    )
    example_raw = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/example2.md?run={STAMP}",
        "https://api.github.com/repos/Daikisong/new_bot/contents/docs/example2.md?ref=main",
        example_tmp,
        "example",
        warnings,
        methods,
    )
    blind_raw = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2022/08/20220825.csv?run={STAMP}",
        "https://api.github.com/repos/Daikisong/stock-web/contents/atlas/research_daily/snapshots/2022/08/20220825.csv?ref=main",
        blind_tmp,
        "blind_snapshot",
        warnings,
        methods,
    )

    if prompt_raw != required_main[0].read_bytes():
        raise RuntimeError("fresh Raw prompt differs from current main checkout")
    if news_raw != required_main[1].read_bytes():
        raise RuntimeError("fresh Raw news CSV differs from current main checkout")
    if example_raw != required_main[2].read_bytes():
        raise RuntimeError("fresh Raw example differs from current main checkout")
    if blind_raw != required_stock[-1].read_bytes():
        raise RuntimeError("fresh Raw blind snapshot differs from BLIND-safe checkout")

    prompt_sha = sha256_bytes(prompt_raw)
    news_sha = sha256_bytes(news_raw)
    example_sha = sha256_bytes(example_raw)
    blind_sha = sha256_bytes(blind_raw)
    if prompt_sha != EXPECTED_PROMPT_SHA or len(prompt_raw) != EXPECTED_PROMPT_BYTES:
        raise RuntimeError("fresh main prompt hash/size mismatch")
    prompt_text = prompt_raw.decode("utf-8")
    if prompt_text.splitlines()[0] != "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER":
        raise RuntimeError("fresh main prompt title mismatch")
    if "nslab.gold_phase_machine.direct_csv_research.locked" not in prompt_text:
        raise RuntimeError("fresh main prompt lock revision missing")

    with news_tmp.open("r", encoding="utf-8-sig", newline="") as handle:
        news_rows = list(csv.DictReader(handle))
    if not news_rows or list(news_rows[0]) != ["page", "row", "date", "time", "title", "body"]:
        raise RuntimeError("news CSV schema mismatch")
    times: list[datetime] = []
    time_unverified: list[int] = []
    for index, row in enumerate(news_rows, 1):
        try:
            times.append(datetime.fromisoformat(f"{row['date']}T{row['time']}"))
        except Exception:  # noqa: BLE001
            time_unverified.append(index)

    access_path = STOCK / "atlas/research_daily/access/2022/08/20220826.json"
    access = json.loads(access_path.read_text(encoding="utf-8"))
    if access.get("trade_date") != TRADE_DATE or access.get("previous_trade_date") != PREVIOUS_TRADE_DATE or access.get("next_trade_date") != NEXT_TRADE_DATE:
        raise RuntimeError("stock-web access trade-date routing mismatch")
    if access.get("blind_snapshot_path") != "atlas/research_daily/snapshots/2022/08/20220825.csv":
        raise RuntimeError("blind snapshot path mismatch")
    if access.get("outcome_snapshot_path") != "atlas/research_daily/snapshots/2022/08/20220826.csv":
        raise RuntimeError("outcome snapshot path mismatch")
    with blind_tmp.open("r", encoding="utf-8-sig", newline="") as handle:
        blind_rows = list(csv.DictReader(handle))
    if blind_sha != access.get("blind_snapshot_sha256") or len(blind_raw) != access.get("blind_snapshot_bytes") or len(blind_rows) != access.get("blind_snapshot_row_count"):
        raise RuntimeError("blind snapshot provenance mismatch")
    if max(row.get("max_source_date", "") for row in blind_rows) > PREVIOUS_TRADE_DATE:
        raise RuntimeError("blind snapshot contains post-P source date")

    prompt_name = f"research_prompt_{STAMP}_{prompt_sha[:8]}.md"
    news_name = f"news_20220826_{STAMP}_{news_sha[:8]}.csv"
    example_name = f"example2_{STAMP}_{example_sha[:8]}.md"
    blind_name = f"blind_snapshot_20220825_{STAMP}_{blind_sha[:8]}.csv"
    prompt_tmp.rename(INPUTS / prompt_name)
    news_tmp.rename(INPUTS / news_name)
    example_tmp.rename(INPUTS / example_name)
    blind_tmp.rename(INPUTS / blind_name)

    cutoff = datetime.fromisoformat("2022-08-26T08:59:59")
    window_start = datetime.fromisoformat("2022-08-25T15:30:00")
    uncovered: list[str] = []
    if times and min(times) > window_start:
        uncovered.append(f"{window_start.isoformat()}..{(min(times)-timedelta(seconds=1)).isoformat()}")
    if times and max(times) < cutoff:
        uncovered.append(f"{(max(times)+timedelta(seconds=1)).isoformat()}..{cutoff.isoformat()}")

    receipt: dict[str, Any] = {
        "schema_version": "nslab.current_run_raw_acquisition.v1",
        "run_id": RUN_ID,
        "prompt_file": prompt_name,
        "prompt_sha256": prompt_sha,
        "prompt_byte_size": len(prompt_raw),
        "news_file": news_name,
        "news_sha256": news_sha,
        "news_byte_size": len(news_raw),
        "example_file": example_name,
        "example_sha256": example_sha,
        "example_byte_size": len(example_raw),
        "blind_snapshot_file": blind_name,
        "blind_snapshot_sha256": blind_sha,
        "blind_snapshot_byte_size": len(blind_raw),
        "blind_snapshot_row_count": len(blind_rows),
        "csv_row_count": len(news_rows),
        "parsed_row_count": len(news_rows),
        "columns": list(news_rows[0]),
        "min_published_at": min(times).isoformat() if times else None,
        "max_published_at": max(times).isoformat() if times else None,
        "time_unverified_rows": time_unverified,
        "control_char_count": sum(1 for ch in news_raw.decode("utf-8-sig") if ord(ch) < 32 and ch not in "\n\r\t"),
        "trade_date": TRADE_DATE,
        "previous_trade_date": PREVIOUS_TRADE_DATE,
        "next_trade_date": NEXT_TRADE_DATE,
        "official_trade_day": True,
        "input_coverage_warning": None if not uncovered else "CSV timestamp coverage does not exactly reach both configured boundaries.",
        "uncovered_time_ranges": uncovered,
        "outcome_snapshot_sha256_expected": access["outcome_snapshot_sha256"],
        "outcome_snapshot_byte_size_expected": access["outcome_snapshot_bytes"],
        "outcome_snapshot_row_count_expected": access["outcome_snapshot_row_count"],
        "raw_urls_opened": {
            "prompt": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md",
            "news": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_20220826.csv",
            "blind_snapshot": "https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2022/08/20220825.csv",
        },
        "stock_access_routing_method": "WEB_VIEW_PLUS_ACTIONS_CHECKOUT",
        "access_sha256_status": "WEB_VIEW_ONLY_UNHASHED",
        "acquisition_methods": methods,
        "acquisition_warnings": warnings,
        "preseal_outcome_download_count": 0,
        "preseal_outcome_header_read_count": 0,
        "preseal_outcome_sha256_count": 0,
        "preseal_outcome_row_count_count": 0,
        "preseal_outcome_parse_count": 0,
        "preseal_outcome_winner_census_count": 0,
    }
    (WORK / "acquisition_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (WORK / "acquisition_warnings.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in warnings) + ("\n" if warnings else ""),
        encoding="utf-8",
    )

    def set_assign(text: str, name: str, value: Any) -> str:
        output, count = re.subn(rf"(?m)^{re.escape(name)}\s*=\s*.*$", f"{name} = {value!r}", text, count=1)
        if count != 1:
            raise RuntimeError(f"pipeline constant not found: {name}")
        return output

    common_path = PIPELINE / "common.py"
    common = common_path.read_text(encoding="utf-8")
    values = {
        "TRADE_DATE": TRADE_DATE,
        "PREVIOUS_TRADE_DATE": PREVIOUS_TRADE_DATE,
        "NEXT_TRADE_DATE": NEXT_TRADE_DATE,
        "CUTOFF_AT": "2022-08-26T08:59:59+09:00",
        "WINDOW_START": "2022-08-25T15:30:00+09:00",
        "AVAILABLE_FROM": "2022-08-29T00:00:00+09:00",
        "INPUT_SHA256": news_sha,
        "INPUT_BYTE_SIZE": len(news_raw),
        "INPUT_ROW_COUNT": len(news_rows),
        "PROMPT_SHA256": prompt_sha,
        "PROMPT_BYTE_SIZE": len(prompt_raw),
        "BLIND_SNAPSHOT_SHA256": blind_sha,
        "BLIND_SNAPSHOT_ROWS": len(blind_rows),
        "OUTCOME_SNAPSHOT_SHA256": access["outcome_snapshot_sha256"],
        "OUTCOME_SNAPSHOT_BYTES": access["outcome_snapshot_bytes"],
        "OUTCOME_SNAPSHOT_ROWS": access["outcome_snapshot_row_count"],
        "MODEL_NAME": MODEL_NAME,
    }
    for key, value in values.items():
        common = set_assign(common, key, value)
    common = common.replace("20220819", DATE8).replace("2022-08-19", TRADE_DATE).replace("2022-08-18", PREVIOUS_TRADE_DATE).replace("2022-08-22", NEXT_TRADE_DATE)
    common_path.write_text(common, encoding="utf-8")

    for filename in ("blind.py", "reseal.py", "postmortem.py"):
        path = PIPELINE / filename
        text = path.read_text(encoding="utf-8")
        text = text.replace("20220819", DATE8).replace("2022-08-19", TRADE_DATE).replace("2022-08-18", PREVIOUS_TRADE_DATE).replace("2022-08-22", NEXT_TRADE_DATE)
        text = text.replace("ec55b86339923c35db8c7b31e01f1706213afa3ffdb535aac243f2fd56a454fb", news_sha)
        path.write_text(text, encoding="utf-8")

    blind_path = PIPELINE / "blind.py"
    blind_text = blind_path.read_text(encoding="utf-8")
    if "    fallback_count = 0\n" not in blind_text:
        raise RuntimeError("blind concurrency patch anchor missing: fallback_count")
    blind_text = blind_text.replace(
        "    fallback_count = 0\n",
        "    fallback_count = 0\n    import threading\n    fallback_lock = threading.Lock()\n",
        1,
    )
    if "            fallback_count += 1\n" not in blind_text:
        raise RuntimeError("blind concurrency patch anchor missing: increment")
    blind_text = blind_text.replace(
        "            fallback_count += 1\n",
        "            with fallback_lock:\n                fallback_count += 1\n",
        1,
    )
    old_loop = '''    for batch_index, batch in enumerate(row_batches(model_inputs, max_items=18, max_chars=78000), start=1):
        process(batch, f"FULL_ROW_SEMANTIC_REVIEW_{batch_index:03d}")
'''
    new_loop = '''    batches = list(row_batches(model_inputs, max_items=18, max_chars=78000))
    from concurrent.futures import ThreadPoolExecutor, as_completed
    max_workers = min(5, max(1, len(batches)))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="nslab-semantic") as executor:
        futures = [
            executor.submit(process, batch, f"FULL_ROW_SEMANTIC_REVIEW_{batch_index:03d}")
            for batch_index, batch in enumerate(batches, start=1)
        ]
        for future in as_completed(futures):
            future.result()
'''
    if old_loop not in blind_text:
        raise RuntimeError("blind concurrency patch anchor missing: batch loop")
    blind_text = blind_text.replace(old_loop, new_loop, 1)
    blind_path.write_text(blind_text, encoding="utf-8")

    run([sys.executable, "-m", "py_compile", *(str(PIPELINE / name) for name in ("common.py", "blind.py", "reseal.py", "postmortem.py"))])
    return receipt


def run_blind(receipt: dict[str, Any]) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PIPELINE)
    run(
        [
            sys.executable,
            str(PIPELINE / "blind.py"),
            "--prompt",
            str(INPUTS / receipt["prompt_file"]),
            "--news",
            str(INPUTS / receipt["news_file"]),
            "--access",
            str(STOCK / "atlas/research_daily/access/2022/08/20220826.json"),
            "--manifest",
            str(STOCK / "atlas/research_daily/manifest.json"),
            "--schema",
            str(STOCK / "atlas/research_daily/schema.json"),
            "--blind-snapshot",
            str(INPUTS / receipt["blind_snapshot_file"]),
            "--example",
            str(INPUTS / receipt["example_file"]),
            "--output",
            str(BLIND_OUT),
            "--token",
            TOKEN,
            "--run-id",
            RUN_ID,
        ],
        env=env,
    )

    artifacts = BLIND_OUT / "artifacts"
    manifest = json.loads((artifacts / "blind_packet_manifest.json").read_text(encoding="utf-8"))
    seal = json.loads((artifacts / "blind_seal_receipt.json").read_text(encoding="utf-8"))
    if sha256_bytes(canonical_json(manifest).encode("utf-8")) != seal.get("blind_packet_manifest_sha256"):
        raise RuntimeError("blind packet manifest hash mismatch")
    if seal.get("blind_packet_manifest_verified") is not True or seal.get("preseal_outcome_access_all_zero") is not True or seal.get("seal_status") != "VERIFIED_CLEAN":
        raise RuntimeError("blind seal is not verified clean")
    for key in (
        "preseal_outcome_download_count",
        "preseal_outcome_header_read_count",
        "preseal_outcome_sha256_count",
        "preseal_outcome_row_count_count",
        "preseal_outcome_parse_count",
        "preseal_outcome_winner_census_count",
    ):
        if int(seal.get(key, -1)) != 0:
            raise RuntimeError(f"non-zero preseal outcome counter: {key}={seal.get(key)}")


def acquire_outcome_after_seal(receipt: dict[str, Any]) -> Path:
    seal = json.loads((BLIND_OUT / "artifacts/blind_seal_receipt.json").read_text(encoding="utf-8"))
    if seal.get("seal_status") != "VERIFIED_CLEAN" or seal.get("preseal_outcome_access_all_zero") is not True:
        raise RuntimeError("outcome acquisition attempted without verified clean seal")
    warnings: list[dict[str, Any]] = []
    methods: dict[str, str] = {}
    temp = POST_INPUTS / "outcome_snapshot_20220826_tmp.csv"
    data = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2022/08/20220826.csv?run={STAMP}",
        "https://api.github.com/repos/Daikisong/stock-web/contents/atlas/research_daily/snapshots/2022/08/20220826.csv?ref=main",
        temp,
        "outcome_snapshot_post_seal",
        warnings,
        methods,
    )
    sha = sha256_bytes(data)
    if sha != receipt["outcome_snapshot_sha256_expected"] or len(data) != receipt["outcome_snapshot_byte_size_expected"]:
        raise RuntimeError("outcome snapshot hash/size mismatch")
    with temp.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        header = list(rows[0]) if rows else []
    if len(rows) != receipt["outcome_snapshot_row_count_expected"]:
        raise RuntimeError("outcome snapshot row count mismatch")
    final_name = f"outcome_snapshot_20220826_{STAMP}_{sha[:8]}.csv"
    final = POST_INPUTS / final_name
    temp.rename(final)
    outcome_receipt = {
        "schema_version": "nslab.outcome_acquisition_receipt.v1",
        "file": final_name,
        "sha256": sha,
        "byte_size": len(data),
        "row_count": len(rows),
        "header": header,
        "acquisition_method": methods.get("outcome_snapshot_post_seal"),
        "acquisition_warnings": warnings,
        "seal_verified_before_download": True,
    }
    (POST_INPUTS / "outcome_receipt.json").write_text(json.dumps(outcome_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return final


def run_postmortem(receipt: dict[str, Any], outcome: Path) -> Path:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PIPELINE)
    run(
        [
            sys.executable,
            str(PIPELINE / "postmortem.py"),
            "--blind-dir",
            str(BLIND_OUT),
            "--outcome",
            str(outcome),
            "--output",
            str(POST_OUT),
            "--token",
            TOKEN,
            "--run-id",
            RUN_ID,
        ],
        env=env,
    )

    sys.path.insert(0, str(PIPELINE))
    import common  # type: ignore

    final = POST_OUT / "20220826_nslab_episode_bundle.md"
    reparse_path = POST_OUT / "final_reparse_receipt.json"
    if not final.is_file() or final.stat().st_size <= 0 or not reparse_path.is_file():
        raise RuntimeError("final bundle or reparse receipt missing")
    text = final.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise RuntimeError("final bundle does not start with YAML front matter")
    blocks = common.parse_markdown_blocks(text)
    required = [
        "source_ledger.jsonl",
        "row_disposition.jsonl",
        "material_review_queue.jsonl",
        "material_review.jsonl",
        "candidate_screening.jsonl",
        "outcome_ledger.jsonl",
        "outcome_leader_census.jsonl",
        "outcome_to_news_audit.jsonl",
        "brain_delta.jsonl",
        "ledger_population_audit.json",
        "bundle_manifest.json",
        "validation_report.json",
        "direct_ingest_contract.json",
        "blind_report.md",
        "postmortem_report.md",
    ]
    missing = [name for name in required if name not in blocks]
    if missing:
        raise RuntimeError(f"required final blocks missing: {missing}")

    parsed = {name: common.parse_block(blocks[name], name) for name in required}
    reparse = json.loads(reparse_path.read_text(encoding="utf-8"))
    ledger_audit = parsed["ledger_population_audit.json"]
    source_ledger = parsed["source_ledger.jsonl"]
    row_disposition = parsed["row_disposition.jsonl"]
    material_queue = parsed["material_review_queue.jsonl"]
    material_review = parsed["material_review.jsonl"]
    candidate_screening = parsed["candidate_screening.jsonl"]
    outcome_ledger = parsed["outcome_ledger.jsonl"]
    leaders = parsed["outcome_leader_census.jsonl"]
    outcome_audit = parsed["outcome_to_news_audit.jsonl"]
    brain = parsed["brain_delta.jsonl"]
    manifest = parsed["bundle_manifest.json"]
    validation = parsed["validation_report.json"]
    contract = parsed["direct_ingest_contract.json"]
    blind_report = parsed["blind_report.md"]
    postmortem_report = parsed["postmortem_report.md"]

    if reparse.get("status") != "ACCEPT_FULL" or validation.get("status") != "ACCEPT_FULL":
        raise RuntimeError("final validator did not return ACCEPT_FULL")
    if ledger_audit.get("csv_row_count") != receipt["csv_row_count"]:
        raise RuntimeError("ledger audit csv_row_count mismatch")
    if ledger_audit.get("source_ledger_news_row_count") != receipt["csv_row_count"]:
        raise RuntimeError("source ledger news denominator mismatch")
    if len(row_disposition) != receipt["csv_row_count"]:
        raise RuntimeError("row disposition denominator mismatch")
    if len(material_queue) != len(material_review):
        raise RuntimeError("material review queue not fully closed")
    if ledger_audit.get("material_review_unreviewed_count") != 0:
        raise RuntimeError("material review contains unreviewed rows")
    if len(candidate_screening) < len(material_review):
        raise RuntimeError("candidate screening does not cover material population")
    if len(outcome_ledger) != receipt["outcome_snapshot_row_count_expected"]:
        raise RuntimeError("outcome ledger full-market denominator mismatch")
    if len(outcome_audit) != len(leaders):
        raise RuntimeError("outcome reverse audit is not 1:1 with leader census")
    if not brain:
        raise RuntimeError("brain_delta payload is empty")
    for key in (
        "brain_delta_payload_missing_count",
        "brain_delta_manifest_payload_count_mismatch_count",
        "brain_delta_declared_without_payload_count",
    ):
        if int(reparse.get(key, -1)) != 0:
            raise RuntimeError(f"brain delta final reparse counter failed: {key}")
    if manifest.get("files", {}).get("brain_delta.jsonl", {}).get("row_count") != len(brain):
        raise RuntimeError("brain delta manifest row count mismatch")
    if contract.get("direct_brain_ingest_ready") is not True or contract.get("brain_eligible") is not True or contract.get("automated_import_expected_to_pass") is not True:
        raise RuntimeError("direct ingest contract not ready")
    if contract.get("fatal_blockers") not in ([], None):
        raise RuntimeError("direct ingest contract has fatal blockers")
    for section in range(1, 20):
        if f"## {section}." not in blind_report:
            raise RuntimeError(f"blind report section missing: {section}")
    for section in range(20, 37):
        if f"## {section}." not in postmortem_report:
            raise RuntimeError(f"postmortem report section missing: {section}")

    independent = {
        "status": "ACCEPT_FULL",
        "run_id": RUN_ID,
        "csv_row_count": receipt["csv_row_count"],
        "source_ledger_total_row_count": len(source_ledger),
        "source_ledger_news_row_count": ledger_audit.get("source_ledger_news_row_count"),
        "row_disposition_count": len(row_disposition),
        "material_review_queue_count": len(material_queue),
        "material_reviewed_count": len(material_review),
        "candidate_screening_count": len(candidate_screening),
        "outcome_ledger_count": len(outcome_ledger),
        "outcome_leader_census_count": len(leaders),
        "outcome_to_news_audit_count": len(outcome_audit),
        "brain_delta_record_count": len(brain),
        "brain_delta_payload_missing_count": 0,
        "brain_delta_manifest_payload_count_mismatch_count": 0,
        "brain_delta_declared_without_payload_count": 0,
        "final_markdown_sha256": sha256_bytes(final.read_bytes()),
        "final_markdown_byte_size": final.stat().st_size,
    }
    independent_path = POST_OUT / "independent_final_validation_receipt.json"
    independent_path.write_text(json.dumps(independent, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for source in (
        final,
        independent_path,
        reparse_path,
        WORK / "acquisition_receipt.json",
        WORK / "acquisition_warnings.jsonl",
        BLIND_OUT / "blind_completion_receipt.json",
        BLIND_OUT / "artifacts/blind_seal_receipt.json",
        POST_INPUTS / "outcome_receipt.json",
    ):
        if source.exists():
            shutil.copy2(source, FINAL_ARTIFACT / source.name)
    return FINAL_ARTIFACT / final.name


def main() -> None:
    receipt = fresh_prepare()
    run_blind(receipt)
    outcome = acquire_outcome_after_seal(receipt)
    final = run_postmortem(receipt, outcome)
    print(json.dumps({"status": "ACCEPT_FULL", "final": str(final)}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
