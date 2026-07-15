from __future__ import annotations

import importlib.util
from pathlib import Path

runner_path = Path("temp/nslab_parallel_runner_20220826_20260715t174533.py")
spec = importlib.util.spec_from_file_location("nslab_parallel_runner_20220826", runner_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load runner: {runner_path}")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner.MODEL_NAME = "openai/gpt-4.1"
runner.main()
