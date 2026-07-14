from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

SOURCE = Path(os.environ.get("NSLAB_REVIEW_SOURCE", "source/temp/nslab_review_20220824_20260715t024535/review_news.py"))
spec = importlib.util.spec_from_file_location("nslab_review_source_current", SOURCE)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load review source: {SOURCE}")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.RUN_STAMP = "20260715T045906KST"
module.RUN_ID = "nslab_run_20260715T045906KST_20220824"
module.SHARD_COUNT = 16
module.main()
