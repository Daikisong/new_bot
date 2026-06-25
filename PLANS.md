# Implementation Plan

This repository is being built as `news-scalping-lab`: an LLM-native research-memory system for Korean market news analysis. Production code must stay generic. Research knowledge belongs in `research/`, `memory/`, and `brain/`, not in Python conditionals or source-level ticker/theme maps.

## Scope

1. Scaffold a Python package, CLI, configs, schemas, prompts, data directories, reports, and run manifests.
2. Implement canonical Pydantic contracts for research episodes, memory claims, event-ticker edges, brain manifests, context manifests, blind predictions, outcomes, and daily analysis.
3. Provide immutable research import with strict JSON and semantic mock conversion paths.
4. Provide deterministic mock LLM, web, embedding, and price providers so the system works without API keys.
5. Provide OpenAI and stock-web adapter seams that can be activated by config.
6. Implement brain rebuild, incremental update, and coverage audit with 100% accepted episode coverage checks.
7. Implement exhaustive context assembly that sweeps every accepted episode and never treats retrieval misses as candidate blockers.
8. Implement daily blind analysis outputs: `predictions/YYYY-MM-DD.json`, `reports/YYYY-MM-DD_preopen.md`, and `runs/manifests/<run_id>.json`.
9. Implement evaluation, hardcoding audit, lookahead audit, provenance audit, session pack export, and training export with real JSONL and manifest files.
10. Add unit, integration, and metamorphic tests, then run `ruff`, `mypy`, and `pytest`.

## Non-Negotiables

- No production source mapping from region, theme, policy, keyword, company, or ticker to candidate securities.
- No D-day price access in blind inference.
- No cutoff-after evidence in blind reports.
- Exhaustive mode must record `swept_episode_count == accepted_episode_count`.
- New research must change data/brain outputs, not source code.
- Every output must have provenance and a context manifest.
