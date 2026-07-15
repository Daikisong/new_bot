from __future__ import annotations

import os
import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/Daikisong/new_bot/24c4691bae68de0c6ccb36160faad76c6480afc4/temp/nslab_review_shard_20180628_20260715t222908.py"
request = urllib.request.Request(
    SOURCE_URL,
    headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "User-Agent": "NSLAB-MATRIX-20180628-FAST-SHARD",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    },
)
with urllib.request.urlopen(request, timeout=180) as response:
    source = response.read().decode("utf-8")
source = source.replace('common.MODEL_NAME = "openai/gpt-4.1-mini"', 'common.MODEL_NAME = "openai/gpt-4.1"', 1)
source = source.replace("attempts=8,", "attempts=4,", 1)
if 'common.MODEL_NAME = "openai/gpt-4.1"' not in source or "attempts=4," not in source:
    raise RuntimeError("fast shard patch anchors were not applied")
namespace = {"__name__": "__main__", "__file__": SOURCE_URL}
exec(compile(source, SOURCE_URL, "exec"), namespace)
