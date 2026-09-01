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

## Product Intent: One-Time Brain, Daily Decision

- Accepted repaired research is incorporated into durable `brain/`, `memory/`,
  and semantic indexes in a one-time offline build. The user must not pay the
  full raw-research interpretation cost again for every daily CSV.
- When the user supplies the pre-open CSV around 08:00, BLIND inference must use
  the already-built brain and indexes to produce the decision in an
  operationally usable run before market open. Exact latency targets are set by
  the user, not inferred by reviewers or agents.
- Daily inference may retrieve a small, relevance-driven, citation-bearing
  evidence set. It must not launch LLM work proportional to the raw corpus,
  every material cluster x record assignment, or an exhaustive lane ledger.
- `QUALITY_FULL` having no arbitrary wall-clock abort means an otherwise valid
  quality call is not killed merely for exceeding 90 seconds. It is not
  permission to design an unbounded daily call graph or to ignore operational
  usability.
- Formal evaluation must test the architecture intended for daily deployment.
  An evaluator-only exhaustive path cannot be used as proof that the daily
  product works.
- Before following an external review or goal prompt, compare it with this
  product intent. If it moves raw research interpretation into daily inference,
  makes one date require hundreds of LLM calls, or gates the one-time brain on a
  non-deployable evaluator, stop and reconcile the conflict with the user. Do
  not execute the conflicting goal as written.
- `QPRED-704f15cde6e4152b6931` and its 379-pack runtime-evidence ancestry are
  `HALTED_MISALIGNED_DIAGNOSTIC_ONLY`. Preserve them for forensics, but never
  resume, score, compare, promote, or use them as cache input for a formal run.

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
