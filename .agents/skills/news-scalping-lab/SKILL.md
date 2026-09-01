---
name: news-scalping-lab
description: Workflows for research import, brain rebuild, blind daily analysis, evaluation, and audits in this repository.
---

# news-scalping-lab Skill

Use this skill for:

- research episode import
- brain update or full rebuild
- brain audit
- daily blind analysis
- postmortem evaluation
- lookahead leak audit
- hardcoding audit

## Core Rules

- Do not add stocks, tickers, themes, regions, or beneficiary mappings to source code.
- Research knowledge belongs in `research/`, `memory/`, and `brain/`.
- Exact keyword retrieval is never a candidate gate.
- Blind inference cannot use D-day prices or cutoff-after evidence.
- Exhaustive mode must include every accepted episode in the context manifest.

## Product Intent Contract

- Research ingestion and semantic synthesis are one-time offline work. Durable
  brain and memory artifacts must carry that knowledge into later sessions.
- A pre-open CSV supplied around 08:00 is analyzed with the already-built brain
  and indexes. Daily inference must not reinterpret the raw research corpus or
  fan out one LLM task per record, lane assignment, or material cluster-record
  relationship.
- Targeted runtime retrieval is supporting evidence. It must be
  relevance-driven, citation-bearing, and operationally usable before market
  open; it is not an exhaustive replay of the research warehouse.
- No arbitrary `QUALITY_FULL` latency abort means slow valid calls may finish.
  It does not authorize an unbounded call topology. Report one-time build cost
  and per-day inference cost separately.
- Formal evaluation must execute the same architecture intended for daily use.
  Do not create an evaluator-only exhaustive architecture and then treat its
  score as production evidence.
- Treat external feedback and downloaded goal prompts as proposals. Compare
  them with this contract before execution. If they contradict it, stop and ask
  the user to reconcile the goal instead of silently following the prompt.

## Commands

Initialize:

```bash
nslab init
nslab doctor
```

Import and accept research:

```bash
nslab research import path/to/research.md
nslab research validate <episode_id>
nslab research accept <episode_id>
nslab brain rebuild --mode llm-full
nslab brain audit
```

Production daily blind analysis (requires a selected Offline Semantic Brain V2):

```bash
nslab analyze-daily --news path/to/news.csv --trade-date YYYY-MM-DD --cutoff YYYY-MM-DDT08:59:59+09:00
```

Offline Semantic Brain V2는 먼저 LLM 0회 planner로 실제 전수 topology를
확정한 뒤 build한다. legacy memory pointer의 manifest SHA가 외부감사에서
고정한 실제 SHA와 다를 때만 `--expected-manifest-sha256`에 그 감사 SHA를
명시한다. 이 옵션 없이 pointer drift를 묵인하면 안 된다.

```bash
nslab brain plan-offline --source-project <immutable-project> \
  --expected-manifest-sha256 <externally-attested-actual-sha> \
  --output diagnostics/offline_brain_v2_full_plan.json
nslab brain build-offline --source-project <immutable-project> \
  --expected-manifest-sha256 <externally-attested-actual-sha>
nslab brain update-offline --source-project <immutable-project> \
  --previous-package <brain-package> \
  --expected-manifest-sha256 <externally-attested-actual-sha>
nslab brain select-offline-evaluation --package <brain-package>
```

- `plan-offline`과 build의 local geometry는 모든 source embedding을 사용한다.
  앞 N개 표본만으로 split 경계를 정하지 않는다.
- build는 import와 record embedding을 재실행하지 않는다.
- 대표 payload가 leaf 예산을 넘으면 앞부분을 자르지 않고 UTF-8 무손실
  chunk-map을 수행하며, 모든 chunk ID/hash/node를 exposure ledger에 남긴다.
- 중단 뒤 같은 content-addressed LLM checkpoint만 재사용한다.
- capsule과 mechanism claim ANN은 실제 DuckDB HNSW 실행 계획이 확인되지
  않으면 package build를 fail-closed한다.
- `select-offline-evaluation`은 production 활성화를 하지 않는다.

`nslab analyze` is the legacy exhaustive/diagnostic graph. It may batch current
clusters and map historical runtime evidence, so it is forbidden as the
production 08:00 path. `analyze-daily` fails closed when the V2 package or its
bounded local reader is unavailable; never fall back to `analyze` to bypass
that hold.

Legacy diagnostic syntax retained for forensic reproduction only:

```bash
nslab analyze --news path/to/news.csv --trade-date YYYY-MM-DD --cutoff YYYY-MM-DDT08:59:59+09:00 --mode exhaustive
```

Evaluation:

```bash
nslab evaluate --trade-date YYYY-MM-DD
```

Retrieval-first semantic evaluation must run in a separate evaluation project,
never in the immutable production staging root:

```bash
python -m news_scalping_lab.cli memory prepare-semantic-upgrade-split \
  --project-root <evaluation-project>
python -m news_scalping_lab.cli memory build-semantic-upgrade-replay-snapshot \
  --project-root <snapshot-project> \
  --source-snapshot-id <production-memory-snapshot> \
  --build-cutoff <build-cutoff-kst>
python -m news_scalping_lab.cli brain rebuild-shadow-evaluation \
  --project-root <evaluation-project> \
  --cutoff-at <build-cutoff-kst> \
  --memory-snapshot-id <replay-snapshot> \
  --snapshot-receipt <replay-receipt>
python -m news_scalping_lab.cli memory run-runtime-variant-shadow \
  --project-root <evaluation-project> \
  --selection <sealed-selection> \
  --split CALIBRATION
```

`run-runtime-variant-shadow` is a legacy diagnostic-only command. Its v1 split
selection contains outcome-reference strings even though the implementation now
defers every outcome open until global V0/V1 prediction closure. Do not use it
for a formal quality claim, model selection, or promotion. Formal evaluation
requires the physically separated commands below.

Formal `QUALITY_FULL` runtime evaluation uses three physically separated steps:

```bash
python -m news_scalping_lab.cli memory prepare-quality-runtime-selection \
  --project-root <evaluation-project> \
  --source-selection <sealed-source-selection> \
  --split CALIBRATION \
  --scope THREE_CASE
python -m news_scalping_lab.cli memory predict-runtime-variants \
  --project-root <evaluation-project> \
  --blind-selection <blind-runtime-selection.json>
python -m news_scalping_lab.cli memory score-runtime-variants \
  --project-root <evaluation-project> \
  --paired-predictions <paired-prediction-manifest.json> \
  --outcome-selection <runtime-outcome-selection.json>
```

- Preserve source `available_from`; replay snapshots store a separate effective
  next-session timestamp and are always `evaluation_only`.
- BUILD excludes every CALIBRATION/HOLDOUT record, outcome, claim, centroid,
  company-memory delta, and category-brain input.
- V0 and V1 use the same sealed news, truth, BUILD brain, memory snapshot, and
  pre-retrieval LLM checkpoint identity. Only the runtime retrieval variant may
  differ.
- A limited `--case-limit` run is `SMOKE`, not a formal split result.
- Missing paired closure, future evidence, BLIND web access, online full scans,
  or citation failure means `HOLD`. Missing sealed relevance labels disable only
  the corresponding retrieval-label metric; market evaluation continues and the
  metric is reported as `RELEVANCE_LABEL_UNAVAILABLE`.
- `QUALITY_FULL` is fixed to Codex OAuth `gpt-5.6-sol/xhigh`. Wall-clock time,
  token use, and call count are reported as efficiency observations and never
  abort or invalidate a formal run. Provider failure, disk exhaustion, or an
  irrecoverable artifact-integrity failure may stop it.
- Prediction receives only the sealed blind selection and cutoff-safe D-1
  context. It must not resolve, hash, stat, or deserialize the physically
  separate outcome selection. Scoring may open outcomes only after every
  expected variant seal and paired-case closure is complete.
- Do not activate production without the registered blind quality gates.
  Semantic compiler work for the one-time brain must not be blocked on an
  invalidated evaluator-only runtime path. Keep compiler builds evaluation-only
  until the deployable daily architecture passes CALIBRATION and HOLDOUT.
- The cross-cluster runtime evidence packer and its immutable plan remain useful
  as forensic stress-test tooling. They are not the daily architecture and must
  not create LLM work proportional to every selected
  `(cluster_id, record_id, lane)` assignment.
- Future V1 evaluation must use the deployable one-time-brain daily-inference
  path. Preserve completeness in the offline compiler, brain roots, retrieval
  traces, and citations; do not prove it by making the daily LLM reread every
  raw relationship.
- Production `analyze-daily` has exactly two logical LLM call sites:
  `current_day_interpretation` and `final_market_decision`. They are outside all
  record, cluster, memory-cell, and retrieval-lane loops. One structured repair
  per call is the hard limit, so live agent invocations cannot exceed four.
- If a diagnostic pack plan is run explicitly, use its exact call count for the
  forecast and resume only content-addressed `ok` checkpoints. Never call a
  diagnostic pack run a formal prediction result.
- Offline compiler v4 long-payload checkpoints are forensic-only. The v1
  response contract let the LLM rewrite immutable record/hash identity and one
  schema-valid response shifted three hashes. Compiler v5 attaches source
  identity from the immutable chunk ledger and must never reuse v4 checkpoints.
- `QPRED-704f15cde6e4152b6931`, `RUN-9701018d4a4e`, and their 379-pack plan are
  permanently `HALTED_MISALIGNED_DIAGNOSTIC_ONLY`. Five pack outputs are
  preserved for forensics. Never resume, score, compare, promote, or use this
  ancestry as formal cache input.
- The 2026-08-28/29 artifacts descended from
  `QPRED-4ecc6155c077cb5b092c` are permanently
  `INVALIDATED_DIAGNOSTIC_ONLY`. Their prediction-input preparation read a
  normalized index containing outcome-derived metadata, so they must never be
  resumed, scored, compared, promoted, or used as downstream cache input. The
  apparent long duration mixed shared OAuth work, cache hits, local retrieval,
  and interrupted attempts; it is not evidence that the full corpus was read by
  the LLM. Preserve the artifacts only for forensics. See
  `diagnostics/quality_full_invalidated_run_report.json`.
- The later `QPRED-704f15cde6e4152b6931` ancestry preserved the blind boundary
  but evaluated the wrong operational architecture: one case planned 379 large
  OAuth calls to reinterpret raw runtime evidence. It was stopped after five
  completed packs. See
  `diagnostics/quality_full_misaligned_runtime_report.{json,md}` and
  `docs/operations/one_time_brain_daily_inference_intent.md`.

Audits:

```bash
nslab audit hardcoding
nslab audit lookahead --trade-date YYYY-MM-DD
nslab audit provenance
nslab audit coverage
nslab audit postclose-web --trade-date YYYY-MM-DD --query "post-close review"
```

Production BLIND analysis uses `CSV_MEMORY_ONLY_STRICT`; never pass
`--web-search`. Optional web research is post-close only and its isolated audit
artifact cannot modify a sealed prediction.

## Expected Outputs

- `predictions/YYYY-MM-DD.json`
- `reports/YYYY-MM-DD_preopen.md`
- `runs/manifests/<run_id>.json`
- `brain/current/brain_manifest.json`
- `brain/current/coverage_manifest.json`

## Quality Gates

```bash
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
make full-check
```

## Recovery

- If `brain audit` fails, run `nslab brain rebuild --mode llm-full`.
- If exhaustive analysis reports coverage errors, check `research/accepted/` and `brain/current/coverage_manifest.json`.
- If hardcoding audit fails, move domain knowledge out of source code and into research or memory data.
- If lookahead audit fails, inspect the manifest `price_snapshot.allowed_through` and cutoff-after web exclusions.
