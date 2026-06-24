# news-scalping-lab

`news-scalping-lab` is a Python CLI system for importing long-running market-news research, compiling it into a versioned research brain, and producing reproducible pre-open analysis reports from a new news CSV.

The implementation is intentionally LLM-native. Production code does not contain domain keyword maps, stock whitelists, ticker lists, or fixed theme score tables. Research knowledge is data in `research/`, `memory/`, and `brain/`.

## Quick Start

```bash
python -m pip install -e ".[dev]"
python -m news_scalping_lab.cli init
python -m news_scalping_lab.cli doctor
python -m news_scalping_lab.cli news inspect docs/csv/news_20260624.csv
python -m news_scalping_lab.cli brain rebuild --mode full
python -m news_scalping_lab.cli warehouse rebuild
python -m news_scalping_lab.cli analyze --news docs/csv/news_20260624.csv --trade-date 2026-06-24 --cutoff 2026-06-24T08:59:59+09:00 --mode exhaustive
```

Outputs:

```text
predictions/YYYY-MM-DD.json
reports/YYYY-MM-DD_preopen.md
runs/manifests/<run_id>.json
```

## Local UI

Install the optional UI extra and launch the Streamlit dashboard:

```bash
python -m pip install -e ".[ui]"
python -m news_scalping_lab.cli ui --port 8501
```

The UI accepts a news CSV, trade date, cutoff timestamp, analysis mode, and web-search option.
It shows the active brain version, memory sweep coverage, dominant sector hypotheses, candidates,
evidence and objections, plus downloads for the context manifest, prediction JSON, and Markdown report.

## Research Flow

```bash
nslab research import path/to/research_episode.json
nslab research validate <episode_id>
nslab research accept <episode_id>
nslab brain update --episode <episode_id>
nslab brain audit
```

Batch flow:

```bash
nslab research import-batch data/inbox/research/
nslab brain rebuild --mode full
nslab brain audit
```

## Daily Blind Analysis

```bash
nslab analyze \
  --news data/inbox/news/0700_news_20260715.csv \
  --trade-date 2026-07-15 \
  --cutoff 2026-07-15T08:59:59+09:00 \
  --mode exhaustive \
  --web-search
```

`exhaustive` mode is the default quality mode. It sweeps every accepted research episode and fails if coverage is incomplete. Retrieval misses are recorded but never used to block open-world candidate generation.

## Evaluation

```bash
nslab evaluate --trade-date 2026-07-15
```

Evaluation loads the sealed blind prediction, reads D-day outcome data only in the evaluation phase, labels outcomes, and writes postmortem learning with `available_from` set after the trade date.

## Session Pack

```bash
nslab context export-session-pack \
  --news docs/csv/news_20260624.csv \
  --trade-date 2026-06-24 \
  --cutoff 2026-06-24T08:59:59+09:00 \
  --mode brain
```

This creates a GPT Web session pack under `session_packs/YYYY-MM-DD/`.
If the selected brain or shard-brain context contains cutoff-after episode IDs,
the export writes a blocked manifest and exits non-zero instead of producing an unsafe pack.

## Environment

Copy `.env.example` to `.env` and set values as needed.

```text
NSLAB_LLM_PROVIDER=mock
OPENAI_API_KEY=
NSLAB_OPENAI_MODEL=gpt-5-mini
NSLAB_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
NSLAB_STOCK_WEB_PATH=
```

Without API keys, the deterministic mock providers are used. To use OpenAI for structured semantic import and daily blind analysis:

```bash
python -m pip install -e ".[openai]"
set NSLAB_LLM_PROVIDER=openai
set OPENAI_API_KEY=...
nslab research import data/inbox/research/example.md --mode semantic
nslab analyze --news docs/csv/news_20260624.csv --trade-date 2026-06-24 --cutoff 2026-06-24T08:59:59+09:00 --mode exhaustive
```

The OpenAI adapter uses Structured Outputs through the Python SDK and validates responses against the project Pydantic contracts.

## stock-web Price Source

Set `NSLAB_STOCK_WEB_PATH` to a local checkout or cache of `https://github.com/Songdaiki/stock-web`.

```bash
set NSLAB_STOCK_WEB_PATH=data/cache/stock-web
nslab doctor
```

To let the price factory prepare a GitHub-backed cache on demand, opt in explicitly:

```bash
set NSLAB_STOCK_WEB_CACHE=1
set NSLAB_STOCK_WEB_CACHE_PATH=data/cache/stock-web
set NSLAB_STOCK_WEB_REMOTE_URL=https://github.com/Songdaiki/stock-web.git
```

The adapter reads `atlas/manifest.json` and `atlas/schema.json`, then uses:

```text
atlas/ohlcv_tradable_by_symbol_year/{prefix}/{code}/{year}.csv
```

It expects the stock-web shard columns:

```text
d,o,h,l,c,v,a,mc,s,m
```

Outcome labels use the previous tradable row for that ticker, not the previous calendar day.

## Quality Gates

```bash
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
```

## Warehouse

The warehouse is a derived DuckDB/Parquet projection, not the source of truth.
Canonical research stays under `research/`, `memory/`, and `brain/`.

```bash
nslab warehouse rebuild
nslab warehouse inspect
```

Generated tables include:

```text
warehouse/research_episodes.parquet
warehouse/event_ticker_edges.parquet
warehouse/predictions.parquet
warehouse/daily_outcomes.parquet
warehouse/market_memory.parquet
```
