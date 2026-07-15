from __future__ import annotations

import sys
from pathlib import Path

runner = Path("parallel_source/temp/nslab_parallel_runner_20220826_20260715t171800.py")
text = runner.read_text(encoding="utf-8")

replacements = [
    ("20260715T171800KST", "20260715T204725KST"),
    ("20260715t171800", "20260715t204725"),
    ("20220826", "20180628"),
    ("20220825", "20180627"),
    ("2022-08-26", "2018-06-28"),
    ("2022-08-25", "2018-06-27"),
    ("2022-08-29", "2018-06-29"),
    ("2022/08", "2018/06"),
    ("openai/gpt-4.1-mini", "openai/gpt-4.1"),
]
for old, new in replacements:
    if old not in text:
        raise RuntimeError(f"runner adaptation anchor missing: {old}")
    text = text.replace(old, new)

namespace = {
    "__name__": "nslab_gold_runner_20180628_20260715t204725",
    "__file__": str(runner),
}
exec(compile(text, str(runner), "exec"), namespace)
original_prepare = namespace["prepare_inputs_and_pipeline"]


def current_prepare() -> dict:
    receipt = original_prepare()
    blind_path = namespace["PIPELINE"] / "blind.py"
    blind = blind_path.read_text(encoding="utf-8")

    old_batch = "row_batches(model_inputs, max_items=12, max_chars=52000)"
    new_batch = "row_batches(model_inputs, max_items=10, max_chars=45000)"
    if old_batch not in blind:
        raise RuntimeError("semantic batch patch anchor not found")
    blind = blind.replace(old_batch, new_batch, 1)

    old_workers = 'ThreadPoolExecutor(max_workers=6, thread_name_prefix="nslab-semantic")'
    new_workers = 'ThreadPoolExecutor(max_workers=20, thread_name_prefix="nslab-semantic")'
    if old_workers not in blind:
        raise RuntimeError("semantic worker patch anchor not found")
    blind = blind.replace(old_workers, new_workers, 1)

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


acquisition = current_prepare()
namespace["run_blind"](acquisition)
outcome_path = namespace["acquire_outcome_after_seal"](acquisition)
final_path = namespace["run_postmortem"](acquisition, outcome_path)
print(
    namespace["json"].dumps(
        {"status": "ACCEPT_FULL", "final": str(final_path)},
        ensure_ascii=False,
        sort_keys=True,
    )
)
