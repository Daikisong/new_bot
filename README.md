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
It shows the active brain version, memory sweep coverage, shard status, dominant sector hypotheses, candidates,
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

`import-batch` accepts imported episodes by default so the documented rebuild flow
updates the brain immediately. Use `--no-accept` when you want to stage episodes
for manual validation first.

`brain rebuild` also refreshes `memory/vector_index/manifest.json` and
`memory/vector_index/episodes.jsonl`. The local index is a deterministic
embedding-style projection of accepted episodes; it supports retrieval but is never
a candidate gate.

Free-form semantic imports preserve the raw source under `data/raw/research/`
and record a source-segment audit in `ResearchEpisode.input_audit.semantic_import`.
The audit stores source hashes, non-empty segment hashes, and the episode fields
that were derived from the preserved source provenance.
`nslab audit provenance` verifies those source files, hashes, segments, and source IDs.

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

Without `--web-search`, the BLIND phase runs in `NEWS_ONLY_STRICT` mode and does
not call web providers or price repositories. With `--web-search`, the BLIND phase
uses `CUTOFF_SAFE_WEB_BLIND`: every web result is timestamp-filtered by the temporal
guard, cutoff-after or unverified sources are excluded, and admitted web evidence is
recorded in the manifest and source ledger. Price repositories and D-day outcomes
remain unavailable during BLIND in both modes.
The context manifest records the LLM provider/model settings used for the run, and
`nslab audit provenance` cross-checks those settings against persisted LLM traces.
Run IDs include the model settings snapshot, so changing providers or models creates
a separate manifest/checkpoint namespace instead of overwriting a prior run.
The same audit verifies listed brain and shard-brain context files against their
manifest hashes so replay context drift is detected.

## Evaluation

```bash
nslab evaluate --trade-date 2026-07-15
```

Evaluation loads the sealed blind prediction, reads D-day outcome data only in the evaluation phase, labels outcomes, and writes postmortem learning with `available_from` set to the next trading day.
When the price source exposes a full D-day outcome universe, evaluation also fills UpperLimit Recall@5/10/20.
Without that universe it leaves recall empty with an explicit unavailable reason instead of faking recall from predicted candidates only.

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
If the token budget would omit currently available research episodes, the command also
writes a blocked manifest and exits non-zero so coverage loss is not missed silently.

## Analysis Bundle

```bash
nslab context export-analysis-bundle --run-id RUN-a5840d2def32
```

This writes a single Markdown bundle named `<YYYYMMDD>_nslab_episode_bundle.md`
under `reports/`. The bundle contains the report, sealed blind prediction,
research episode JSON, row disposition JSONL, brain delta JSONL, source ledger
JSONL, phase state JSON, and bundle manifest blocks with machine-verified hashes.

## Environment

Copy `.env.example` to `.env` and set values as needed.
CLI commands automatically load `.env` from the project root without overwriting already-set shell environment variables.

```text
NSLAB_LLM_PROVIDER=mock
NSLAB_LLM_REASONING_EFFORT=low
NSLAB_LLM_MAX_OUTPUT_TOKENS=4096
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

Model, reasoning, and output-token defaults live in `configs/models.yaml`.
Environment variables override them for local runs. The OpenAI adapter passes
the selected model, reasoning effort, and max output tokens into the SDK calls,
then records the same settings in context manifests and LLM traces.
Structured outputs are validated against the project Pydantic contracts.

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
warehouse/mechanism_memory.parquet
warehouse/company_memory.parquet
```
