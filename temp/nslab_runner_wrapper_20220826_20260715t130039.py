from pathlib import Path

runner = Path("temp/nslab_runner_20220826_20260715t130039.py")
text = runner.read_text(encoding="utf-8")
old_sparse = 'run(["git", "-C", str(STOCK), "sparse-checkout", "set", *p_paths])'
new_sparse = 'run(["git", "-C", str(STOCK), "sparse-checkout", "set", "--no-cone", *p_paths])'
assert old_sparse in text
text = text.replace(old_sparse, new_sparse, 1)
old_receipt = '''        "news_file": news_name,
        "news_sha256": news_sha,
        "news_byte_size": len(news_raw),
        "csv_row_count": len(news_rows),'''
new_receipt = '''        "news_file": news_name,
        "news_sha256": news_sha,
        "news_byte_size": len(news_raw),
        "example_file": example_name,
        "example_sha256": example_sha,
        "example_byte_size": len(example_raw),
        "csv_row_count": len(news_rows),'''
assert old_receipt in text
text = text.replace(old_receipt, new_receipt, 1)
namespace = {"__name__": "__main__", "__file__": str(runner)}
exec(compile(text, str(runner), "exec"), namespace)
