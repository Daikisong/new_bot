from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
import traceback
from pathlib import Path

STAMP = "20260716T001004Z"
DATE8 = "20180628"
TRADE_DATE = "2018-06-28"
PREVIOUS_DATE8 = "20180627"
PREVIOUS_TRADE_DATE = "2018-06-27"
NEXT_DATE8 = "20180629"
NEXT_TRADE_DATE = "2018-06-29"
SOURCE_WORKFLOW = Path("input_src/.github/workflows/nslab_gold_20180621_20260715T002346Z.yml")
STABLE_OUTPUT = Path("final_artifact_20180628_20260716t001004")


def adapt(text: str) -> str:
    placeholders = {
        "2018-06-20": "__PREVIOUS_ISO__",
        "2018-06-21": "__TRADE_ISO__",
        "2018-06-22": "__NEXT_ISO__",
        "20180620": "__PREVIOUS_DATE8__",
        "20180621": "__TRADE_DATE8__",
        "20180622": "__NEXT_DATE8__",
        "20260715T002346Z": "__RUN_STAMP__",
    }
    output = text
    for old, marker in placeholders.items():
        output = output.replace(old, marker)
    replacements = {
        "__PREVIOUS_ISO__": PREVIOUS_TRADE_DATE,
        "__TRADE_ISO__": TRADE_DATE,
        "__NEXT_ISO__": NEXT_TRADE_DATE,
        "__PREVIOUS_DATE8__": PREVIOUS_DATE8,
        "__TRADE_DATE8__": DATE8,
        "__NEXT_DATE8__": NEXT_DATE8,
        "__RUN_STAMP__": STAMP,
    }
    for marker, new in replacements.items():
        output = output.replace(marker, new)
    return output


def extract_run_blocks(yaml_text: str) -> list[str]:
    lines = yaml_text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)run:\s*\|\s*$", lines[i])
        if not match:
            i += 1
            continue
        base_indent = len(match.group(1))
        i += 1
        raw: list[str] = []
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                raw.append("")
                i += 1
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent <= base_indent:
                break
            raw.append(line)
            i += 1
        blocks.append(textwrap.dedent("\n".join(raw)).rstrip() + "\n")
    return blocks


def absorb_github_env(path: Path, env: dict[str, str]) -> None:
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("", encoding="utf-8")
    for line in lines:
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value


def copy_if_exists(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def main() -> None:
    if not SOURCE_WORKFLOW.is_file():
        raise RuntimeError(f"missing successful source workflow: {SOURCE_WORKFLOW}")
    source = SOURCE_WORKFLOW.read_text(encoding="utf-8")
    blocks = [adapt(block) for block in extract_run_blocks(source)]
    if len(blocks) != 4:
        raise RuntimeError(f"expected four executable source blocks, found {len(blocks)}")

    runner_temp = Path(os.environ["RUNNER_TEMP"])
    github_env = runner_temp / f"nslab_replay_env_{DATE8}_{STAMP}.txt"
    github_env.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "RUN_ID": f"NSLAB-WEB-{DATE8}-{STAMP}",
            "RUN_STAMP": STAMP,
            "TRADE_DATE": TRADE_DATE,
            "PREVIOUS_TRADE_DATE": PREVIOUS_TRADE_DATE,
            "NEXT_TRADE_DATE": NEXT_TRADE_DATE,
            "TRADE_COMPACT": DATE8,
            "PREVIOUS_COMPACT": PREVIOUS_DATE8,
            "NEXT_COMPACT": NEXT_DATE8,
            "EXPECTED_PROMPT_SHA": "b5ba21ce1f6e3a91dacf19e33e16d5db9dface141e90a67e78c8588ba1553029",
            "EXPECTED_PROMPT_BYTES": "430485",
            "PIPELINE_REF": "nslab-pack-pipeline-20180620-20260714T232000Z",
            "PIPELINE_ROOT": "temp/nslab_shared_20180620_20260714T232000Z/pipeline_source/temp/nslab_20220819_pipeline",
            "GITHUB_ENV": str(github_env),
            "PYTHONUNBUFFERED": "1",
        }
    )
    if not env.get("GITHUB_TOKEN"):
        raise RuntimeError("GITHUB_TOKEN missing")

    executed: list[dict[str, object]] = []
    try:
        for index, script in enumerate(blocks, start=1):
            subprocess.run(["bash", "-lc", script], check=True, env=env)
            absorb_github_env(github_env, env)
            executed.append({"block": index, "status": "completed"})

        final_path = Path(env.get("FINAL_PATH", ""))
        final_dir = Path(env.get("FINAL_DIR", ""))
        runroot = Path(env.get("RUNROOT", ""))
        blind_output = Path(env.get("BLIND_OUTPUT", ""))
        expected_name = f"{DATE8}_nslab_episode_bundle.md"
        if not final_path.is_file() or final_path.name != expected_name:
            raise RuntimeError(f"validated final bundle missing: {final_path}")

        if STABLE_OUTPUT.exists():
            shutil.rmtree(STABLE_OUTPUT)
        STABLE_OUTPUT.mkdir(parents=True)
        copy_if_exists(final_path, STABLE_OUTPUT / expected_name)
        for name in (
            "independent_final_validation_receipt.json",
            "current_importer_inspection.json",
        ):
            copy_if_exists(final_dir / name, STABLE_OUTPUT / name)
        copy_if_exists(runroot / "metadata.json", STABLE_OUTPUT / "metadata.json")
        copy_if_exists(runroot / "outcome_receipt.json", STABLE_OUTPUT / "outcome_receipt.json")
        copy_if_exists(
            blind_output / "seal_independent_verification.json",
            STABLE_OUTPUT / "seal_independent_verification.json",
        )
        receipt = {
            "status": "ACCEPT_FULL_REPLAY_COMPLETED",
            "source_workflow": str(SOURCE_WORKFLOW),
            "trade_date": TRADE_DATE,
            "final_bundle": expected_name,
            "executed_blocks": executed,
            "fresh_raw_urls_opened_in_initiating_web_session": True,
        }
        (STABLE_OUTPUT / "replay_receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    except Exception:
        failure = Path("failure_artifact_20180628_20260716t001004")
        failure.mkdir(parents=True, exist_ok=True)
        (failure / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        (failure / "executed_blocks.json").write_text(
            json.dumps(executed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for key in ("RUNROOT", "BLIND_OUTPUT", "FINAL_DIR"):
            value = env.get(key)
            if value:
                copy_if_exists(Path(value), failure / key.lower())
        raise


if __name__ == "__main__":
    main()
