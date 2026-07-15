from pathlib import Path

runner = Path("parallel_source/temp/nslab_parallel_runner_20220826_20260715t171800.py")
text = runner.read_text(encoding="utf-8")
text = text.replace("20260715T171800KST", "20260715T180200KST")
text = text.replace("20260715t171800", "20260715t180200")
old_batch = 'row_batches(model_inputs, max_items=12, max_chars=52000)'
new_batch = 'row_batches(model_inputs, max_items=6, max_chars=30000)'
assert old_batch in text
text = text.replace(old_batch, new_batch, 1)
old_workers = 'ThreadPoolExecutor(max_workers=6, thread_name_prefix="nslab-semantic")'
new_workers = 'ThreadPoolExecutor(max_workers=12, thread_name_prefix="nslab-semantic")'
assert old_workers in text
text = text.replace(old_workers, new_workers, 1)
namespace = {"__name__": "__main__", "__file__": str(runner)}
exec(compile(text, str(runner), "exec"), namespace)
