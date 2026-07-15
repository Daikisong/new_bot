from __future__ import annotations

import os
import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/Daikisong/new_bot/4841b53f42245e24d87ccbbb11726ea60b1f234c/temp/nslab_matrix_aggregate_20180628_20260715t222908.py"
request = urllib.request.Request(
    SOURCE_URL,
    headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "User-Agent": "NSLAB-CURRENT-20180628-20260715T144120Z-AGGREGATE",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    },
)
with urllib.request.urlopen(request, timeout=180) as response:
    source = response.read().decode("utf-8")

replacements = [
    ("20260715T222908KST", "20260715T144120Z"),
    ("20260715t222908", "20260715t144120z"),
]
for old, new in replacements:
    if old not in source:
        raise RuntimeError(f"fresh aggregate patch anchor missing: {old}")
    source = source.replace(old, new)

namespace = {"__name__": "__main__", "__file__": SOURCE_URL}
exec(compile(source, SOURCE_URL, "exec"), namespace)
