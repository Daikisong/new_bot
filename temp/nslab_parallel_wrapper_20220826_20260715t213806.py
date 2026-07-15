from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

runner_path = Path("temp/nslab_parallel_runner_20220826_20260715t174533.py")
spec = importlib.util.spec_from_file_location("nslab_parallel_runner_20220826_current", runner_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load runner: {runner_path}")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

runner.STAMP = "20260715T213806KST"
runner.STAMP_LOWER = "20260715t213806"
runner.RUN_ID = "nslab_run_20260715T213806KST_20220826"
runner.MODEL_NAME = "openai/gpt-4o"
runner.WORK = runner.ROOT / "work_20220826_20260715t213806"
runner.INPUTS = runner.WORK / "inputs"
runner.PIPELINE = runner.WORK / "pipeline"
runner.BLIND_OUT = runner.WORK / "blind"
runner.POST_INPUTS = runner.WORK / "post_inputs"
runner.POST_OUT = runner.WORK / "post_output"
runner.FINAL_ARTIFACT = runner.ROOT / "final_artifact_20220826_20260715t213806"

_original_prepare = runner.fresh_prepare

def tuned_prepare():
    receipt = _original_prepare()

    blind_path = runner.PIPELINE / "blind.py"
    blind_text = blind_path.read_text(encoding="utf-8")
    old_batches = "batches = list(row_batches(model_inputs, max_items=18, max_chars=78000))"
    new_batches = "batches = list(row_batches(model_inputs, max_items=5, max_chars=30000))"
    old_workers = "max_workers = min(5, max(1, len(batches)))"
    new_workers = "max_workers = min(15, max(1, len(batches)))"
    if old_batches not in blind_text or old_workers not in blind_text:
        raise RuntimeError("semantic batch tuning anchors missing")
    blind_text = blind_text.replace(old_batches, new_batches, 1).replace(old_workers, new_workers, 1)
    blind_path.write_text(blind_text, encoding="utf-8")

    post_path = runner.PIPELINE / "postmortem.py"
    post_text = post_path.read_text(encoding="utf-8")
    replacements = {
        "for start in range(0, len(leaders), 8):": "for start in range(0, len(leaders), 5):",
        "batch = leaders[start:start + 8]": "batch = leaders[start:start + 5]",
        "label=f\"OUTCOME_REVERSE_AUDIT_{start//8+1:03d}\"": "label=f\"OUTCOME_REVERSE_AUDIT_{start//5+1:03d}\"",
    }
    for old, new in replacements.items():
        if old not in post_text:
            raise RuntimeError(f"postmortem batch tuning anchor missing: {old}")
        post_text = post_text.replace(old, new, 1)
    post_path.write_text(post_text, encoding="utf-8")

    runner.run([sys.executable, "-m", "py_compile", str(runner.PIPELINE / "common.py"), str(blind_path), str(runner.PIPELINE / "reseal.py"), str(post_path)])
    return receipt

runner.fresh_prepare = tuned_prepare
runner.main()
