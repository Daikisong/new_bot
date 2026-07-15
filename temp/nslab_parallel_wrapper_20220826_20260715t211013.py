from __future__ import annotations

import importlib.util
from pathlib import Path

runner_path = Path("temp/nslab_parallel_runner_20220826_20260715t174533.py")
spec = importlib.util.spec_from_file_location("nslab_parallel_runner_20220826_current", runner_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load runner: {runner_path}")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

runner.STAMP = "20260715T211013KST"
runner.STAMP_LOWER = "20260715t211013"
runner.RUN_ID = "nslab_run_20260715T211013KST_20220826"
runner.MODEL_NAME = "openai/gpt-4o"
runner.WORK = runner.ROOT / "work_20220826_20260715t211013"
runner.INPUTS = runner.WORK / "inputs"
runner.PIPELINE = runner.WORK / "pipeline"
runner.BLIND_OUT = runner.WORK / "blind"
runner.POST_INPUTS = runner.WORK / "post_inputs"
runner.POST_OUT = runner.WORK / "post_output"
runner.FINAL_ARTIFACT = runner.ROOT / "final_artifact_20220826_20260715t211013"

runner.main()
