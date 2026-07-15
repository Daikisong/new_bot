from __future__ import annotations

import os
import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/Daikisong/new_bot/24c4691bae68de0c6ccb36160faad76c6480afc4/temp/nslab_review_shard_20180628_20260715t222908.py"
request = urllib.request.Request(
    SOURCE_URL,
    headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "User-Agent": "NSLAB-CURRENT-20180628-20260715T144120Z-SHARD",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    },
)
with urllib.request.urlopen(request, timeout=180) as response:
    source = response.read().decode("utf-8")

replacements = [
    ("20260715T222908KST", "20260715T144120Z"),
    ('common.MODEL_NAME = "openai/gpt-4.1-mini"', 'common.MODEL_NAME = "openai/gpt-4.1"'),
]
for old, new in replacements:
    if old not in source:
        raise RuntimeError(f"fresh shard patch anchor missing: {old}")
    source = source.replace(old, new, 1)

namespace = {"__name__": "__main__", "__file__": SOURCE_URL}
exec(compile(source, SOURCE_URL, "exec"), namespace)
