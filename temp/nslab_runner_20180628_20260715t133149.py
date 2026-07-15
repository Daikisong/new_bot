from __future__ import annotations

import re
import sys
from pathlib import Path

SOURCE_RUNNER = Path("parallel_source/temp/nslab_parallel_runner_20220826_20260715t171800.py")
text = SOURCE_RUNNER.read_text(encoding="utf-8")

replacements = [
    ("20260715T171800KST", "20260715T133149Z"),
    ("20260715t171800", "20260715t133149"),
    ("20220826", "20180628"),
    ("2022-08-26", "2018-06-28"),
    ("20220825", "20180627"),
    ("2022-08-25", "2018-06-27"),
    ("20220829", "20180629"),
    ("2022-08-29", "2018-06-29"),
    ("2022/08", "2018/06"),
]
for old, new in replacements:
    text = text.replace(old, new)

namespace = {
    "__name__": "nslab_runner_20180628_20260715t133149",
    "__file__": str(SOURCE_RUNNER),
}
exec(compile(text, str(SOURCE_RUNNER), "exec"), namespace)
original_prepare = namespace["prepare_inputs_and_pipeline"]


def prepare_current_run() -> dict:
    receipt = original_prepare()

    common_path = namespace["PIPELINE"] / "common.py"
    common = common_path.read_text(encoding="utf-8")
    common, model_count = re.subn(
        r"(?m)^MODEL_NAME\s*=\s*['\"]openai/gpt-4\.1-mini['\"]\s*$",
        'MODEL_NAME = "openai/gpt-4.1"',
        common,
        count=1,
    )
    if model_count != 1:
        raise AssertionError("MODEL_NAME patch anchor not found")
    substitutions = [
        ("attempts: int = 8,", "attempts: int = 5,"),
        ("urllib.request.urlopen(req, timeout=300)", "urllib.request.urlopen(req, timeout=240)"),
        ("min(90.0, 5.0 * (2 ** (attempt - 1)))", "min(60.0, 4.0 * (2 ** (attempt - 1)))"),
        ("time.sleep(min(45.0, 3.0 * attempt))", "time.sleep(min(30.0, 2.0 * attempt))"),
    ]
    for old, new in substitutions:
        if old not in common:
            raise AssertionError(f"common.py patch anchor not found: {old}")
        common = common.replace(old, new, 1)
    common_path.write_text(common, encoding="utf-8")

    blind_path = namespace["PIPELINE"] / "blind.py"
    blind = blind_path.read_text(encoding="utf-8")
    batch_old = "row_batches(model_inputs, max_items=12, max_chars=52000)"
    batch_new = "row_batches(model_inputs, max_items=24, max_chars=120000)"
    if batch_old not in blind:
        raise AssertionError("row batch patch anchor not found")
    blind = blind.replace(batch_old, batch_new, 1)
    if "max_tokens=15000," not in blind:
        raise AssertionError("blind max_tokens patch anchor not found")
    blind = blind.replace("max_tokens=15000,", "max_tokens=30000,", 1)
    blind_path.write_text(blind, encoding="utf-8")

    namespace["run"](
        [
            sys.executable,
            "-m",
            "py_compile",
            str(namespace["PIPELINE"] / "common.py"),
            str(namespace["PIPELINE"] / "blind.py"),
            str(namespace["PIPELINE"] / "reseal.py"),
            str(namespace["PIPELINE"] / "postmortem.py"),
        ]
    )
    return receipt


acquisition = prepare_current_run()
namespace["run_blind"](acquisition)
outcome_path = namespace["acquire_outcome_after_seal"](acquisition)
final_path = namespace["run_postmortem"](acquisition, outcome_path)
print(namespace["json"].dumps({"status": "ACCEPT_FULL", "final": str(final_path)}, sort_keys=True))
