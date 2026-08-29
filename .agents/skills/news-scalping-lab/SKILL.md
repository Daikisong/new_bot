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

Daily blind analysis:

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
- Do not build semantic compiler v8, rebuild the full corpus brain, or activate
  production unless the preceding registered gate passes.
- Runtime V1 evidence must be assembled with the cross-cluster packed path. It
  preserves every selected `(cluster_id, record_id, lane)` assignment while
  emitting a repeated record payload only once per bounded pack. Before the
  first provider call it writes
  `runtime_evidence_pack_plan.json`; the completed manifest binds that plan,
  normalized outputs, and every tracing-provider checkpoint. Never restore the
  former per-cluster loop, first-N shortcuts, or silent truncation.
- Use the pack plan's exact call count to forecast the remaining provider time.
  Interrupted calls may resume only from content-addressed `ok` checkpoints;
  a partially written plan or missing checkpoint is not a completed quality
  result.
- The 2026-08-28/29 artifacts descended from
  `QPRED-4ecc6155c077cb5b092c` are permanently
  `INVALIDATED_DIAGNOSTIC_ONLY`. Their prediction-input preparation read a
  normalized index containing outcome-derived metadata, so they must never be
  resumed, scored, compared, promoted, or used as downstream cache input. The
  apparent long duration mixed shared OAuth work, cache hits, local retrieval,
  and interrupted attempts; it is not evidence that the full corpus was read by
  the LLM. Preserve the artifacts only for forensics. See
  `diagnostics/quality_full_invalidated_run_report.json`.

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
