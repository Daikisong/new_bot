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

- Preserve source `available_from`; replay snapshots store a separate effective
  next-session timestamp and are always `evaluation_only`.
- BUILD excludes every CALIBRATION/HOLDOUT record, outcome, claim, centroid,
  company-memory delta, and category-brain input.
- V0 and V1 use the same sealed news, truth, BUILD brain, memory snapshot, and
  pre-retrieval LLM checkpoint identity. Only the runtime retrieval variant may
  differ.
- A limited `--case-limit` run is `SMOKE`, not a formal split result.
- Missing sealed relevance labels, incomplete paired closure, future evidence,
  BLIND web access, online full scans, latency-budget failure, or citation
  failure means `HOLD`.
- Do not build semantic compiler v8, rebuild the full corpus brain, or activate
  production unless the preceding registered gate passes.
- The 2026-08-28 live OAuth probe for `NSLAB-20260102-be50ec83` is a preserved
  latency-gate failure, not a resumable performance run: 490 cutoff-safe news
  rows, 21 `gpt-5.6-sol/xhigh` open-world calls, 252 analyzed clusters, and
  5,798.275 seconds elapsed before V0 completed. Do not resume its remaining
  checkpoints unless the registered 90-second daily budget or the bounded call
  architecture has first changed. See
  `diagnostics/shadow_variant_comparison.json` for the commitment and HOLD
  decision.

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
