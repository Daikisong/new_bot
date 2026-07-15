from pathlib import Path

# Fresh execution trigger 2026-07-15T22:20:00+09:00.
runner = Path("temp/nslab_parallel_runner_20220826_20260715t174533.py")
text = runner.read_text(encoding="utf-8")
replacements = {
    'STAMP = "20260715T174533KST"': 'STAMP = "20260715T201109KST"',
    'STAMP_LOWER = "20260715t174533"': 'STAMP_LOWER = "20260715t201109"',
    'MODEL_NAME = "openai/gpt-4.1-mini"': 'MODEL_NAME = "openai/gpt-4.1"',
    'common = common_path.read_text(encoding="utf-8")': 'common = common_path.read_text(encoding="utf-8")\n    if "attempts: int = 8," not in common:\n        raise RuntimeError("model attempt patch anchor missing")\n    common = common.replace("attempts: int = 8,", "attempts: int = 3,", 1)',
    'batches = list(row_batches(model_inputs, max_items=18, max_chars=78000))': 'batches = list(row_batches(model_inputs, max_items=4, max_chars=22000))',
    'max_workers = min(5, max(1, len(batches)))': 'max_workers = min(24, max(1, len(batches)))',
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"runner patch anchor count for {old!r}: {count}")
    text = text.replace(old, new, 1)
namespace = {"__name__": "__main__", "__file__": str(runner)}
exec(compile(text, str(runner), "exec"), namespace)
