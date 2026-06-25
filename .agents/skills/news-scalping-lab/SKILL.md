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
nslab brain rebuild --mode full
nslab brain audit
```

Daily blind analysis:

```bash
nslab analyze --news path/to/news.csv --trade-date YYYY-MM-DD --cutoff YYYY-MM-DDT08:59:59+09:00 --mode exhaustive --web-search
```

Evaluation:

```bash
nslab evaluate --trade-date YYYY-MM-DD
```

Audits:

```bash
nslab audit hardcoding
nslab audit lookahead --trade-date YYYY-MM-DD
nslab audit provenance
nslab audit coverage
```

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
```

## Recovery

- If `brain audit` fails, run `nslab brain rebuild --mode full`.
- If exhaustive analysis reports coverage errors, check `research/accepted/` and `brain/current/coverage_manifest.json`.
- If hardcoding audit fails, move domain knowledge out of source code and into research or memory data.
- If lookahead audit fails, inspect the manifest `price_snapshot.allowed_through` and cutoff-after web exclusions.
