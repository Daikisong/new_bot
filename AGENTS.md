# Repository Rules

- This repository is an LLM-native news scalping research system.
- Do not hardcode stocks, tickers, themes, regions, or beneficiary mappings in production code.
- Store research knowledge in `research/`, `memory/`, and `brain/`, not in source code.
- Exact keyword retrieval is only supporting evidence, never a judgment gate.
- Candidate generation always starts with an open-world pass.
- New research must be incorporated without source-code changes.
- Blind inference must not access D-day prices or information after the cutoff.
- Every output must include provenance and a context manifest.
- Completion requires `ruff`, `mypy`, and `pytest` to pass.

Record rules: brain_delta is a first-class source of truth and must never be
discarded. Production brain uses llm-full mode; catalog/full/incremental outputs
are not production brains. Production semantic indexes must use a real embedding
provider; deterministic vector indexes are local/test artifacts only. Episode
coverage is not record coverage. Training exports must originate from explicit
eligible records when `memory/records/` exists. Unknown bundle versions or record
types must be preserved or quarantined, never silently dropped.
Company memory derived from `company_memory_delta` records must respect both
`known_at` and `available_from`; never backfill future relationships into a
historical analysis context.

Production BLIND evidence policy is `CSV_MEMORY_ONLY_STRICT`. General web search
must remain disabled during BLIND inference, and all Phase 8 arms must use the
same zero-web evidence surface. Web is allowed only through the separate
post-close audit command and must not mutate predictions, ranks, or training data.
Production LLM calls use the official Codex CLI OAuth session without reading or
copying credential files. Production embedding uses a real provider with
`FAIL_CLOSED`; deterministic fallback is test/local-only.

Test commands:

```bash
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
```

## Temporary NSLAB transport

- [Current Vercel transport preview](https://trendbrief-git-nslab-transport-20220822-2026071-44d794-daiksong.vercel.app/nslab-20220822-9f7c31a2)
- [Current Vercel health endpoint](https://trendbrief-git-nslab-transport-20220822-2026071-44d794-daiksong.vercel.app/api/nslab-20220822-9f7c31a2?op=health)
- [Current Vercel prompt gzip](https://trendbrief-git-nslab-transport-20220822-2026071-44d794-daiksong.vercel.app/api/nslab-20220822-9f7c31a2?op=gzip&file=prompt)
- [Current Vercel CSV gzip](https://trendbrief-git-nslab-transport-20220822-2026071-44d794-daiksong.vercel.app/api/nslab-20220822-9f7c31a2?op=gzip&file=csv)
- [Current Vercel blind gzip](https://trendbrief-git-nslab-transport-20220822-2026071-44d794-daiksong.vercel.app/api/nslab-20220822-9f7c31a2?op=gzip&file=blind)
