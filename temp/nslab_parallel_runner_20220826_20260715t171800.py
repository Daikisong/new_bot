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

TRADE_DATE = "2022-08-26"
DATE8 = "20220826"
PREVIOUS_TRADE_DATE = "2022-08-25"
NEXT_TRADE_DATE = "2022-08-29"
STAMP = "20260715T171800KST"
RUN_ID = f"nslab_run_{STAMP}_{DATE8}"
PIPELINE_COMMIT = "38299bfe2d296e4a8dbbaad04de2001fd63ba88a"
EXPECTED_PROMPT_SHA = "b5ba21ce1f6e3a91dacf19e33e16d5db9dface141e90a67e78c8588ba1553029"
EXPECTED_PROMPT_BYTES = 430485

ROOT = Path.cwd()
CURRENT_MAIN = ROOT / "current_main"
PIPELINE_SOURCE = ROOT / "pipeline_source"
STOCK = ROOT / "stock_blind"
WORK = ROOT / "work_parallel_20220826_20260715t171800"
INPUTS = WORK / "inputs"
PIPELINE = WORK / "pipeline"
BLIND_OUT = WORK / "blind"
POST_INPUTS = WORK / "post_inputs"
POST_OUT = WORK / "post_output"
FINAL_ARTIFACT = ROOT / "final_artifact_20220826_20260715t171800"
TOKEN = os.environ["GITHUB_TOKEN"]


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    subprocess.run(cmd, check=True, env=env, cwd=str(cwd) if cwd else None)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_exact(
    raw_url: str,
    api_url: str,
    dest: Path,
    label: str,
    warnings: list[dict],
    methods: dict[str, str],
) -> bytes:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": f"NSLAB-{RUN_ID}",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    }
    try:
        req = urllib.request.Request(raw_url, headers=headers)
        with urllib.request.urlopen(req, timeout=180) as response:
            data = response.read()
        methods[label] = "GITHUB_RAW_EXACT_URL_AFTER_BROWSER_RAW_OPEN"
    except Exception as exc:
        warnings.append(
            {
                "stage": "RAW_ACQUISITION",
                "label": label,
                "warning": f"{type(exc).__name__}: {exc}",
                "fallback": "GITHUB_API_RAW_AFTER_BROWSER_RAW_OPEN",
            }
        )
        api_headers = {
            "Authorization": f"Bearer {TOKEN}",
            "User-Agent": f"NSLAB-{RUN_ID}",
            "Accept": "application/vnd.github.raw+json",
        }
        req = urllib.request.Request(api_url, headers=api_headers)
        with urllib.request.urlopen(req, timeout=180) as response:
            data = response.read()
        methods[label] = "GITHUB_API_RAW_AFTER_BROWSER_RAW_OPEN"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data


def patch_parallel_blind(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = '''    for batch_index, batch in enumerate(row_batches(model_inputs, max_items=18, max_chars=78000), start=1):
        process(batch, f"FULL_ROW_SEMANTIC_REVIEW_{batch_index:03d}")
'''
    new = '''    review_batches = list(enumerate(row_batches(model_inputs, max_items=12, max_chars=52000), start=1))
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="nslab-semantic") as executor:
        futures = [
            executor.submit(process, batch, f"FULL_ROW_SEMANTIC_REVIEW_{batch_index:03d}")
            for batch_index, batch in review_batches
        ]
        for future in as_completed(futures):
            future.result()
'''
    if old not in text:
        raise RuntimeError("parallel blind patch anchor not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def prepare_inputs_and_pipeline() -> dict:
    for path in (WORK, FINAL_ARTIFACT):
        if path.exists():
            shutil.rmtree(path)
    for path in (INPUTS, PIPELINE, BLIND_OUT, POST_INPUTS, POST_OUT, FINAL_ARTIFACT):
        path.mkdir(parents=True, exist_ok=True)

    source_dir = PIPELINE_SOURCE / "temp/nslab_20220819_pipeline"
    for filename in ("common.py", "blind.py", "reseal.py", "postmortem.py"):
        shutil.copy2(source_dir / filename, PIPELINE / filename)

    warnings: list[dict] = [
        {
            "stage": "CHATGPT_PRIMARY_DOWNLOAD",
            "label": "prompt_and_news",
            "warning": "Browser Raw URLs were opened in the initiating web session; container download and one curl fallback failed before this isolated GitHub runner acquisition.",
            "fallback": "GITHUB_ACTIONS_RAW_EXACT_WITH_CURRENT_MAIN_BYTE_PARITY",
        }
    ]
    methods: dict[str, str] = {}
    prompt_tmp = WORK / "prompt_tmp.md"
    news_tmp = WORK / "news_20220826_tmp.csv"
    example_tmp = WORK / "example2_tmp.md"

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

    assert prompt_raw == (CURRENT_MAIN / "docs/research_prompt.md").read_bytes()
    assert news_raw == (CURRENT_MAIN / "docs/csv/news_20220826.csv").read_bytes()
    assert example_raw == (CURRENT_MAIN / "docs/example2.md").read_bytes()

    prompt_sha = sha256_bytes(prompt_raw)
    news_sha = sha256_bytes(news_raw)
    example_sha = sha256_bytes(example_raw)
    assert prompt_sha == EXPECTED_PROMPT_SHA
    assert len(prompt_raw) == EXPECTED_PROMPT_BYTES
    prompt_text = prompt_raw.decode("utf-8")
    assert prompt_text.splitlines()[0] == "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER"
    assert "nslab.gold_phase_machine.direct_csv_research.locked" in prompt_text

    with news_tmp.open("r", encoding="utf-8-sig", newline="") as handle:
        news_rows = list(csv.DictReader(handle))
    assert news_rows and list(news_rows[0]) == ["page", "row", "date", "time", "title", "body"]
    times: list[datetime] = []
    time_unverified: list[int] = []
    for index, row in enumerate(news_rows, 1):
        try:
            times.append(datetime.fromisoformat(f"{row['date']}T{row['time']}"))
        except Exception:
            time_unverified.append(index)

    access_path = STOCK / "atlas/research_daily/access/2022/08/20220826.json"
    blind_path = STOCK / "atlas/research_daily/snapshots/2022/08/20220825.csv"
    access = json.loads(access_path.read_text(encoding="utf-8"))
    blind_raw = blind_path.read_bytes()
    blind_sha = sha256_bytes(blind_raw)
    with blind_path.open("r", encoding="utf-8-sig", newline="") as handle:
        blind_rows = list(csv.DictReader(handle))
    assert access["trade_date"] == TRADE_DATE
    assert access["previous_trade_date"] == PREVIOUS_TRADE_DATE
    assert access["next_trade_date"] == NEXT_TRADE_DATE
    assert access["blind_snapshot_path"] == "atlas/research_daily/snapshots/2022/08/20220825.csv"
    assert access["outcome_snapshot_path"] == "atlas/research_daily/snapshots/2022/08/20220826.csv"
    assert blind_sha == access["blind_snapshot_sha256"]
    assert len(blind_raw) == access["blind_snapshot_bytes"]
    assert len(blind_rows) == access["blind_snapshot_row_count"]
    assert max(row.get("max_source_date", "") for row in blind_rows) <= PREVIOUS_TRADE_DATE
    assert not (STOCK / "atlas/research_daily/snapshots/2022/08/20220826.csv").exists()

    prompt_name = f"research_prompt_{STAMP}_{prompt_sha[:8]}.md"
    news_name = f"news_20220826_{STAMP}_{news_sha[:8]}.csv"
    example_name = f"example2_{STAMP}_{example_sha[:8]}.md"
    prompt_tmp.rename(INPUTS / prompt_name)
    news_tmp.rename(INPUTS / news_name)
    example_tmp.rename(INPUTS / example_name)

    cutoff = datetime.fromisoformat("2022-08-26T08:59:59")
    window_start = datetime.fromisoformat("2022-08-25T15:30:00")
    uncovered: list[str] = []
    if times and min(times) > window_start:
        uncovered.append(f"{window_start.isoformat()}..{(min(times)-timedelta(seconds=1)).isoformat()}")
    if times and max(times) < cutoff:
        uncovered.append(f"{(max(times)+timedelta(seconds=1)).isoformat()}..{cutoff.isoformat()}")

    receipt = {
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
        "input_coverage_warning": None if not uncovered else "CSV timestamp coverage does not exactly span the expected window.",
        "uncovered_time_ranges": uncovered,
        "blind_snapshot_sha256": blind_sha,
        "blind_snapshot_byte_size": len(blind_raw),
        "blind_snapshot_row_count": len(blind_rows),
        "outcome_snapshot_sha256_expected": access["outcome_snapshot_sha256"],
        "outcome_snapshot_byte_size_expected": access["outcome_snapshot_bytes"],
        "outcome_snapshot_row_count_expected": access["outcome_snapshot_row_count"],
        "raw_urls_opened": {
            "prompt": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md",
            "news": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_20220826.csv",
            "example": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/example2.md",
        },
        "acquisition_methods": methods,
        "acquisition_warnings": warnings,
        "access_sha256_status": "CHECKED_OUT_ROUTING_METADATA_ONLY",
        "preseal_outcome_download_count": 0,
        "preseal_outcome_header_read_count": 0,
        "preseal_outcome_sha256_count": 0,
        "preseal_outcome_row_count_count": 0,
        "preseal_outcome_parse_count": 0,
    }
    (WORK / "acquisition_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (WORK / "acquisition_warnings.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in warnings) + "\n",
        encoding="utf-8",
    )

    def set_assign(text: str, name: str, value) -> str:
        pattern = rf"(?m)^{re.escape(name)}\s*=\s*.*$"
        output, count = re.subn(pattern, f"{name} = {value!r}", text, count=1)
        assert count == 1, name
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
        "MODEL_NAME": "openai/gpt-4.1-mini",
    }
    for key, value in values.items():
        common = set_assign(common, key, value)
    common = (
        common.replace("20220819", DATE8)
        .replace("2022-08-19", TRADE_DATE)
        .replace("2022-08-18", PREVIOUS_TRADE_DATE)
        .replace("2022-08-22", NEXT_TRADE_DATE)
    )
    common_path.write_text(common, encoding="utf-8")

    for filename in ("blind.py", "reseal.py", "postmortem.py"):
        path = PIPELINE / filename
        text = path.read_text(encoding="utf-8")
        text = (
            text.replace("20220819", DATE8)
            .replace("2022-08-19", TRADE_DATE)
            .replace("2022-08-18", PREVIOUS_TRADE_DATE)
            .replace("2022-08-22", NEXT_TRADE_DATE)
            .replace("ec55b86339923c35db8c7b31e01f1706213afa3ffdb535aac243f2fd56a454fb", news_sha)
        )
        path.write_text(text, encoding="utf-8")

    patch_parallel_blind(PIPELINE / "blind.py")
    run([sys.executable, "-m", "py_compile", *(str(PIPELINE / name) for name in ("common.py", "blind.py", "reseal.py", "postmortem.py"))])
    return receipt


def run_blind(receipt: dict) -> None:
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
            str(STOCK / "atlas/research_daily/snapshots/2022/08/20220825.csv"),
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

    manifest_path = BLIND_OUT / "artifacts/blind_packet_manifest.json"
    seal_path = BLIND_OUT / "artifacts/blind_seal_receipt.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(payload).hexdigest() == seal["blind_packet_manifest_sha256"]
    assert seal["blind_packet_manifest_verified"] is True
    assert seal["preseal_outcome_access_all_zero"] is True
    assert seal["seal_status"] == "VERIFIED_CLEAN"
    for key in (
        "preseal_outcome_download_count",
        "preseal_outcome_header_read_count",
        "preseal_outcome_sha256_count",
        "preseal_outcome_row_count_count",
        "preseal_outcome_parse_count",
    ):
        assert seal[key] == 0, (key, seal[key])
    assert not (STOCK / "atlas/research_daily/snapshots/2022/08/20220826.csv").exists()


def acquire_outcome_after_seal(receipt: dict) -> Path:
    warnings: list[dict] = []
    methods: dict[str, str] = {}
    temp = POST_INPUTS / "outcome_tmp.csv"
    data = fetch_exact(
        f"https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2022/08/20220826.csv?run={STAMP}",
        "https://api.github.com/repos/Daikisong/stock-web/contents/atlas/research_daily/snapshots/2022/08/20220826.csv?ref=main",
        temp,
        "outcome_post_seal",
        warnings,
        methods,
    )
    sha = sha256_bytes(data)
    assert sha == receipt["outcome_snapshot_sha256_expected"]
    assert len(data) == receipt["outcome_snapshot_byte_size_expected"]
    with temp.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == receipt["outcome_snapshot_row_count_expected"]
    name = f"outcome_snapshot_20220826_{STAMP}_{sha[:8]}.csv"
    final = POST_INPUTS / name
    temp.rename(final)
    (POST_INPUTS / "outcome_receipt.json").write_text(
        json.dumps(
            {
                "file": name,
                "sha256": sha,
                "byte_size": len(data),
                "row_count": len(rows),
                "acquisition_method": methods.get("outcome_post_seal"),
                "acquisition_warnings": warnings,
                "seal_verified_before_download": True,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return final


def run_postmortem(receipt: dict, outcome: Path) -> Path:
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
    reparse = POST_OUT / "final_reparse_receipt.json"
    assert final.is_file() and final.stat().st_size > 0
    assert reparse.is_file() and reparse.stat().st_size > 0
    reparse_receipt = json.loads(reparse.read_text(encoding="utf-8"))
    text = final.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    blocks = common.parse_markdown_blocks(text)
    brain = common.parse_block(blocks["brain_delta.jsonl"], "brain_delta.jsonl")
    manifest = common.parse_block(blocks["bundle_manifest.json"], "bundle_manifest.json")
    validation = common.parse_block(blocks["validation_report.json"], "validation_report.json")
    contract = common.parse_block(blocks["direct_ingest_contract.json"], "direct_ingest_contract.json")
    assert reparse_receipt["status"] == "ACCEPT_FULL"
    assert reparse_receipt["csv_row_count"] == receipt["csv_row_count"]
    assert reparse_receipt["outcome_ledger_count"] == receipt["outcome_snapshot_row_count_expected"]
    assert reparse_receipt["brain_delta_record_count"] > 0
    assert len(brain) == reparse_receipt["brain_delta_record_count"]
    assert reparse_receipt["brain_delta_payload_missing_count"] == 0
    assert reparse_receipt["brain_delta_manifest_payload_count_mismatch_count"] == 0
    assert reparse_receipt["brain_delta_declared_without_payload_count"] == 0
    assert manifest["files"]["brain_delta.jsonl"]["row_count"] == len(brain)
    assert validation["status"] == "ACCEPT_FULL"
    assert contract["direct_brain_ingest_ready"] is True
    assert contract["brain_eligible"] is True
    assert contract["automated_import_expected_to_pass"] is True

    independent = {
        "status": "ACCEPT_FULL",
        "csv_row_count": receipt["csv_row_count"],
        "outcome_ledger_count": receipt["outcome_snapshot_row_count_expected"],
        "brain_delta_record_count": len(brain),
        "brain_delta_payload_missing_count": 0,
        "brain_delta_manifest_payload_count_mismatch_count": 0,
        "brain_delta_declared_without_payload_count": 0,
        "final_sha256": sha256_bytes(final.read_bytes()),
        "final_byte_size": final.stat().st_size,
    }
    independent_path = POST_OUT / "independent_final_validation_receipt.json"
    independent_path.write_text(json.dumps(independent, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for source in (
        final,
        independent_path,
        reparse,
        WORK / "acquisition_receipt.json",
        WORK / "acquisition_warnings.jsonl",
        BLIND_OUT / "blind_completion_receipt.json",
        BLIND_OUT / "artifacts/blind_seal_receipt.json",
        POST_INPUTS / "outcome_receipt.json",
    ):
        if source.exists():
            shutil.copy2(source, FINAL_ARTIFACT / source.name)
    return FINAL_ARTIFACT / final.name


if __name__ == "__main__":
    acquisition = prepare_inputs_and_pipeline()
    run_blind(acquisition)
    outcome_path = acquire_outcome_after_seal(acquisition)
    final_path = run_postmortem(acquisition, outcome_path)
    print(json.dumps({"status": "ACCEPT_FULL", "final": str(final_path)}, sort_keys=True))
