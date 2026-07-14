from __future__ import annotations
import importlib.util, os, sys
from pathlib import Path

SOURCE = Path(os.environ.get('NSLAB_REVIEW_SOURCE','source/temp/nslab_review_20220824_20260715t024535/review_news.py'))
spec=importlib.util.spec_from_file_location('nslab_current_review_source',SOURCE)
if spec is None or spec.loader is None: raise RuntimeError('source review module missing')
mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
# Use transports already proven successful in the same current-run population.
primary=os.environ.get('NSLAB_RESCUE_PRIMARY','openai/gpt-4o-mini')
mod.MODELS=[primary,'mistral-ai/mistral-medium-2505','meta/llama-3.3-70b-instruct','mistral-ai/mistral-small-2503','openai/gpt-4.1-nano','openai/gpt-4.1-mini','cohere/cohere-command-a','microsoft/phi-4-mini-instruct']
mod.FALLBACK_MODELS=['openai/gpt-4.1','microsoft/phi-4','deepseek/deepseek-v3-0324']
mod.main()
