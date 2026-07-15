from __future__ import annotations

import importlib.util
from pathlib import Path

runner_path = Path("temp/nslab_parallel_runner_20220826_20260715t174533.py")
spec = importlib.util.spec_from_file_location("nslab_parallel_runner_20220826", runner_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load runner: {runner_path}")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

runner.STAMP = "20260715T191521KST"
runner.STAMP_LOWER = "20260715t191521"
runner.RUN_ID = f"nslab_run_{runner.STAMP}_{runner.DATE8}"
runner.MODEL_NAME = "openai/gpt-4.1"
runner.WORK = runner.ROOT / f"work_{runner.DATE8}_{runner.STAMP_LOWER}"
runner.INPUTS = runner.WORK / "inputs"
runner.PIPELINE = runner.WORK / "pipeline"
runner.BLIND_OUT = runner.WORK / "blind"
runner.POST_INPUTS = runner.WORK / "post_inputs"
runner.POST_OUT = runner.WORK / "post_output"
runner.FINAL_ARTIFACT = runner.ROOT / f"final_artifact_{runner.DATE8}_{runner.STAMP_LOWER}"
runner.main()
