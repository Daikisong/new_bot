from __future__ import annotations

import os
import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/Daikisong/new_bot/24c4691bae68de0c6ccb36160faad76c6480afc4/temp/nslab_review_shard_20180628_20260715t222908.py"
request = urllib.request.Request(
    SOURCE_URL,
    headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "User-Agent": "NSLAB-DIRECT-20180628-20260715T154606Z-03FA622356-SHARD",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    },
)
with urllib.request.urlopen(request, timeout=180) as response:
    source = response.read().decode("utf-8")

replacements = [
    ("20260715T222908KST", "20260715T154606Z"),
    ("batches = list(common.row_batches(selected, max_items=3, max_chars=26000))", "batches = list(common.row_batches(selected, max_items=6, max_chars=48000))"),
    ("max_tokens=12000,", "max_tokens=10000,"),
    ("attempts=8,", "attempts=4,"),
]
for old, new in replacements:
    if old not in source:
        raise RuntimeError(f"direct shard patch anchor missing: {old}")
    source = source.replace(old, new, 1)

namespace = {"__name__": "__main__", "__file__": SOURCE_URL}
exec(compile(source, SOURCE_URL, "exec"), namespace)
