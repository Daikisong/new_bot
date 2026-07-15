from __future__ import annotations

import os
import urllib.request

SOURCE_URL = "https://raw.githubusercontent.com/Daikisong/new_bot/24c4691bae68de0c6ccb36160faad76c6480afc4/temp/nslab_review_shard_20180628_20260715t222908.py"
request = urllib.request.Request(
    SOURCE_URL,
    headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "User-Agent": "NSLAB-MATRIX-20180628-RESILIENT-SHARD",
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    },
)
with urllib.request.urlopen(request, timeout=180) as response:
    source = response.read().decode("utf-8")

anchor = '''    common.MODEL_NAME = "openai/gpt-4.1-mini"
    blind.MODEL_NAME = common.MODEL_NAME
'''
replacement = '''    common.MODEL_NAME = "openai/gpt-4.1"
    blind.MODEL_NAME = common.MODEL_NAME
    _original_model_json = common.model_json
    _original_urlopen = common.urllib.request.urlopen

    def _short_urlopen(request, timeout=None, *args, **kwargs):
        return _original_urlopen(request, timeout=45, *args, **kwargs)

    common.urllib.request.urlopen = _short_urlopen

    def _resilient_model_json(token, *, system, user, label, log_path, max_tokens=6000, attempts=1):
        errors = []
        for model_name in ("openai/gpt-4.1", "openai/gpt-4o", "openai/gpt-4.1-mini"):
            common.MODEL_NAME = model_name
            blind.MODEL_NAME = model_name
            try:
                return _original_model_json(
                    token,
                    system=system,
                    user=user,
                    label=f"{label}-{model_name.rsplit('/', 1)[-1]}",
                    log_path=log_path,
                    max_tokens=min(int(max_tokens), 6000),
                    attempts=1,
                )
            except Exception as exc:
                errors.append(f"{model_name}:{type(exc).__name__}:{exc}")
        raise RuntimeError("ALL_GITHUB_MODELS_FAILED | " + " | ".join(errors))

    common.model_json = _resilient_model_json
'''
if anchor not in source:
    raise RuntimeError("model setup patch anchor missing")
source = source.replace(anchor, replacement, 1)
source = source.replace("max_tokens=12000,", "max_tokens=6000,", 1)
source = source.replace("attempts=8,", "attempts=1,", 1)
source = source.replace(
    "batches = list(common.row_batches(selected, max_items=3, max_chars=26000))",
    "batches = list(common.row_batches(selected, max_items=2, max_chars=18000))",
    1,
)
required = (
    "def _resilient_model_json",
    "max_tokens=6000,",
    "attempts=1,",
    "max_items=2, max_chars=18000",
)
if any(value not in source for value in required):
    raise RuntimeError("resilient shard patch anchors were not applied")
namespace = {"__name__": "__main__", "__file__": SOURCE_URL}
exec(compile(source, SOURCE_URL, "exec"), namespace)
