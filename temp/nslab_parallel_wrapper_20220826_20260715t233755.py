from __future__ import annotations

import importlib.util
from pathlib import Path

RUNNER_PATH = Path("runner_source/temp/nslab_parallel_runner_20220826_20260715t174533.py")
spec = importlib.util.spec_from_file_location("nslab_parallel_runner_20220826_fresh", RUNNER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load runner: {RUNNER_PATH}")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

stamp = "20260715T233755KST"
stamp_lower = "20260715t233755"
runner.STAMP = stamp
runner.STAMP_LOWER = stamp_lower
runner.RUN_ID = f"nslab_run_{stamp}_{runner.DATE8}"
runner.MODEL_NAME = "openai/gpt-4.1"
runner.WORK = runner.ROOT / f"work_{runner.DATE8}_{stamp_lower}"
runner.INPUTS = runner.WORK / "inputs"
runner.PIPELINE = runner.WORK / "pipeline"
runner.BLIND_OUT = runner.WORK / "blind"
runner.POST_INPUTS = runner.WORK / "post_inputs"
runner.POST_OUT = runner.WORK / "post_output"
runner.FINAL_ARTIFACT = runner.ROOT / f"final_artifact_{runner.DATE8}_{stamp_lower}"

_original_prepare = runner.fresh_prepare


def fresh_prepare() -> dict:
    receipt = _original_prepare()
    blind_path = runner.PIPELINE / "blind.py"
    text = blind_path.read_text(encoding="utf-8")
    old = 'max_workers = min(5, max(1, len(batches)))'
    new = 'max_workers = min(8, max(1, len(batches)))'
    if old not in text:
        raise RuntimeError("fresh worker-count patch anchor missing")
    blind_path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return receipt


runner.fresh_prepare = fresh_prepare
runner.main()
