from __future__ import annotations

import sys
from pathlib import Path

runner = Path("parallel_source/temp/nslab_parallel_runner_20220826_20260715t171800.py")
text = runner.read_text(encoding="utf-8")

replacements = [
    ("20260715T171800KST", "20260715T200715KST"),
    ("20260715t171800", "20260715t200715"),
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

old_batch = 'row_batches(model_inputs, max_items=12, max_chars=52000)'
new_batch = 'row_batches(model_inputs, max_items=6, max_chars=32000)'
assert old_batch in text
text = text.replace(old_batch, new_batch, 1)
old_workers = 'ThreadPoolExecutor(max_workers=6, thread_name_prefix="nslab-semantic")'
new_workers = 'ThreadPoolExecutor(max_workers=24, thread_name_prefix="nslab-semantic")'
assert old_workers in text
text = text.replace(old_workers, new_workers, 1)

namespace = {"__name__": "nslab_fresh_runner_20180628", "__file__": str(runner)}
exec(compile(text, str(runner), "exec"), namespace)
original_prepare = namespace["prepare_inputs_and_pipeline"]


def fresh_prepare() -> dict:
    receipt = original_prepare()
    common_path = namespace["PIPELINE"] / "common.py"
    common = common_path.read_text(encoding="utf-8")
    substitutions = [
        ('MODEL_NAME = "openai/gpt-4.1-mini"', 'MODEL_NAME = "openai/gpt-4.1"'),
        ('attempts: int = 8,', 'attempts: int = 4,'),
        ('urllib.request.urlopen(req, timeout=300)', 'urllib.request.urlopen(req, timeout=150)'),
        ('min(90.0, 5.0 * (2 ** (attempt - 1)))', 'min(45.0, 4.0 * (2 ** (attempt - 1)))'),
        ('time.sleep(min(45.0, 3.0 * attempt))', 'time.sleep(min(25.0, 2.0 * attempt))'),
    ]
    for old, new in substitutions:
        assert old in common, old
        common = common.replace(old, new, 1)
    common_path.write_text(common, encoding="utf-8")
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


acquisition = fresh_prepare()
namespace["run_blind"](acquisition)
outcome_path = namespace["acquire_outcome_after_seal"](acquisition)
final_path = namespace["run_postmortem"](acquisition, outcome_path)
print(namespace["json"].dumps({"status": "ACCEPT_FULL", "final": str(final_path)}, sort_keys=True))
