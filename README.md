# news-scalping-lab

`news-scalping-lab` is a Python CLI system for importing long-running market-news research, compiling it into a versioned research brain, and producing reproducible pre-open analysis reports from a new news CSV.

The implementation is intentionally LLM-native. Production code does not contain domain keyword maps, stock whitelists, ticker lists, or fixed theme score tables. Research knowledge is data in `research/`, `memory/`, and `brain/`. `nslab audit hardcoding` scans source plus production prompts and repo guidance for those hardcoding patterns.

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
It shows the active brain version, memory sweep coverage, shard status, dominant sector hypotheses,
the full pre-open watchlist, excluded-but-watch candidates, path-grouped candidates,
evidence and objections, plus downloads for the context manifest, prediction JSON, Markdown report,
candidate verification, and final synthesis context.

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

`brain update --episode` performs a safe incremental merge when the current brain
already covers the prior accepted set exactly. If the current manifest is missing
or drift is detected, it falls back to `brain rebuild --mode full`; full rebuilds
remain reproducible from accepted source episodes. `brain audit` reports the
current build mode while preserving the last full rebuild timestamp across
incremental updates.
Brain shard summaries and daily memory-sweep shards both use
`limits.shard_episode_count` from `configs/default.yaml`, so context budget changes
are reflected in rebuilds and run manifests instead of being hidden in code.

`brain rebuild` also refreshes `memory/vector_index/manifest.json` and
`memory/vector_index/episodes.jsonl`. The local index is a deterministic
embedding-style projection of accepted episodes; it supports retrieval but is never
a candidate gate. `nslab audit coverage` fails if brain coverage is incomplete, the
vector index is stale, or the warehouse research episode projection is out of sync.

Strict and free-form semantic imports preserve the raw source under
`data/raw/research/`. Strict imports record source file, text, and canonical JSON
hashes in `ResearchEpisode.input_audit.strict_import`; semantic imports record
source-segment hashes and derived field source IDs in
`ResearchEpisode.input_audit.semantic_import`. `nslab audit provenance` verifies
those source files, hashes, segments, and source IDs.

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
When `--mode` is omitted, `nslab analyze` uses `default_mode` from
`configs/default.yaml`, whose initial value is `exhaustive`.
`brain` mode also loads global brain files and shard brain summaries, and records
memory sweep artifacts for every time-available accepted episode so those episodes
still influence synthesis indirectly.

Without `--web-search`, the BLIND phase runs in `NEWS_ONLY_STRICT` mode and does
not call web providers or price repositories. With `--web-search`, the BLIND phase
uses `CUTOFF_SAFE_WEB_BLIND`: every web result is timestamp-filtered by the temporal
guard, cutoff-after or unverified sources are excluded, and admitted web evidence is
recorded in the manifest and source ledger with `source_url`, `published_at`,
`time_verified`, and `retrieved_at`. D-day outcomes and D-day/current price fields
remain unavailable during BLIND. When a stock-web price source is configured, the
final synthesis context may include candidate snapshots only through D-1.
The context manifest records the LLM provider/model settings used for the run, and
`nslab audit provenance` cross-checks those settings against persisted LLM traces.
It also records `trade_date`, `cutoff_at`, and the execution `as_of` timestamp used
to filter time-available evidence; `nslab audit lookahead` fails schema-versioned
manifests that omit `as_of` or set it after the cutoff.
It separates total accepted research from cutoff-available and unavailable future
episodes, so walk-forward audits can verify that future memory was excluded.
`nslab context inspect` and `nslab audit provenance` verify those episode-scope
counts against the accepted research store and the run cutoff.
News CSV rows are additionally filtered to the default blind window from D-1 15:30:00
KST through D 08:59:59 KST. The manifest records `news_window_start_at` and
`news_window_end_at`, while row disposition artifacts record each row's `published_at`,
optional `collected_at`, window inclusion state, and exclusion reason.
Included news rows are clustered into `event_clusters.jsonl`, then reviewed by the
LLM as `news_novelty_review.json` using only current news and cutoff-safe web
sources. The review records novelty, first-public evidence time, contract stage,
customer/period/economic attribution fields, and dilution or financing risks; code
validates schema, source IDs, hashes, and cutoff timing.
After the exhaustive memory sweep, the LLM writes `semantic_retrieval_plan.json`
with the required positive/negative/near-miss/counterexample/leader/theme-failure
query categories. The engine executes those queries against accepted episodes,
filters unavailable future episodes, and persists `semantic_retrieval.jsonl`.
The open-world candidate expansion pass then writes `candidate_expansion.json`
with single-event, theme-formation, beneficiary-discovery, and continuation
routes. Continuation routes are explicitly limited to D-1 market data so the
next verification pass can investigate companies without using D-day prices.
When web search is enabled, candidate verification now covers both final
candidates and expansion subjects, checking listing/ticker identity, actual
business and supply-chain relation, prior market narratives, recent disclosure,
market cap, shares, D-1 turnover, limit-up status, and multi-day absorption.
Those checks are summarized per subject in `candidate_verification.json`, with
status counts and unresolved company-discovery items carried into final synthesis.
The red-team pass records the required attack checklist for every candidate and
keeps each objection marked as passed to synthesis rather than deleting candidates.
Successful text and structured-output traces must include prompt and completion
token estimates, tool calls, retry count, input hash, output hash, and prompt version.
Run IDs include the model settings snapshot, so changing providers or models creates
a separate manifest/checkpoint namespace instead of overwriting a prior run.
The same audit verifies listed brain and shard-brain context files against their
manifest hashes so replay context drift is detected. `context inspect` also checks
the news CSV hash, row counts, blind news-window inclusion counts, memory-sweep shard
artifacts, their hashes, cache-hit count, swept episode coverage, and required
pre-open report sections.
Use `nslab context inspect <run_id> --strict` in automation when context drift should
fail the command instead of only being reported in JSON.
Final synthesis inputs are also checkpointed as
`final_synthesis_context.json`, including the exact payload hash and input
summary that the synthesis prompt used. `context inspect` and provenance audit
recompute that summary from the stored payload and compare it with the manifest.

## Evaluation

```bash
nslab evaluate --trade-date 2026-07-15
```

Evaluation loads the sealed blind prediction, reads D-day outcome data only in the evaluation phase, labels outcomes, and writes postmortem learning with `available_from` set to the next trading day.
Daily OHLCV labels cover gap, high/close return, upper-limit touch/close/release,
one-price upper-limit, volume, amount, turnover, and prior-close market cap.
Intraday-only fields remain marked unavailable when only daily bars are present.
When the price source exposes a full D-day outcome universe, evaluation also fills UpperLimit Recall@5/10/20.
Without that universe it leaves recall empty with an explicit unavailable reason instead of faking recall from predicted candidates only.
Provenance audit verifies evaluation episodes against their sealed prediction and
postmortem report source hashes stored under immutable evaluation checkpoints,
and checks that evaluation learning is only available from the next trading day.

## Training Exports

```bash
nslab training export-sft
nslab training export-preference
nslab training export-evals
```

Each export writes a compatibility JSONL plus a manifest under `training_exports/<kind>/`.
The manifest also records `phase_outputs.BLIND` and `phase_outputs.POSTMORTEM`
JSONL files with independent hashes and row counts. Use the BLIND phase file for
blind-only SFT; failure-correction rows stay in the POSTMORTEM phase file.
Rows carry `training_category`, and manifests include `required_training_categories`,
`category_counts`, and `missing_training_categories` so blind reasoning, theme formation,
beneficiary discovery, leader comparison, preference, and failure-correction examples
stay separated and auditable.
`nslab audit provenance` recomputes export hashes, row counts, category counts, and
BLIND/POSTMORTEM phase consistency from the JSONL rows.

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
The pack manifest records the required Markdown file list, per-file hashes, per-file
token estimates, total token count, `budget_omitted_episode_ids`, and
`unavailable_episode_ids`. It also writes `omission_report.md` so budget omissions
and cutoff-after exclusions are readable without opening the JSON manifest.
`nslab audit lookahead` recomputes those
values and scans pack Markdown for cutoff-after episode references so copied GPT Web
context drift is detected.

## Analysis Bundle

```bash
nslab context export-analysis-bundle --run-id RUN-a5840d2def32
```

This writes a single Markdown bundle named `<YYYYMMDD>_nslab_episode_bundle.md`
under `reports/`. The bundle contains the report, sealed blind prediction,
research episode JSON, row disposition JSONL, brain delta JSONL, source ledger
JSONL, optional candidate web-check JSONL, phase state JSON, and bundle manifest
blocks with machine-verified hashes.

## Environment

Copy `.env.example` to `.env` and set values as needed.
CLI commands automatically load `.env` from the project root without overwriting already-set shell environment variables.

```text
NSLAB_LLM_PROVIDER=mock
NSLAB_LLM_REASONING_EFFORT=low
NSLAB_LLM_MAX_OUTPUT_TOKENS=4096
NSLAB_LLM_MAX_RETRIES=0
OPENAI_API_KEY=
NSLAB_OPENAI_MODEL=gpt-5-mini
NSLAB_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
NSLAB_PRICE_PROVIDER=mock
NSLAB_STOCK_WEB_PATH=
NSLAB_WEB_PROVIDER=mock
BRAVE_SEARCH_API_KEY=
```

Without API keys, the deterministic mock providers are used. To use OpenAI for structured semantic import and daily blind analysis:

```bash
python -m pip install -e ".[openai]"
set NSLAB_LLM_PROVIDER=openai
set OPENAI_API_KEY=...
nslab research import data/inbox/research/example.md --mode semantic
nslab analyze --news docs/csv/news_20260624.csv --trade-date 2026-06-24 --cutoff 2026-06-24T08:59:59+09:00 --mode exhaustive
```

Model, reasoning, output-token, and LLM retry defaults live in `configs/models.yaml`.
Environment variables override them for local runs. The OpenAI adapter passes
the selected model, reasoning effort, and max output tokens into the SDK calls.
The trace wrapper records the same settings and the actual retry count in context
manifests, checkpoints, and LLM traces.
Structured outputs are validated against the project Pydantic contracts.

For live cutoff-filtered web research, set Brave Search credentials and opt into
the live provider:

```bash
set NSLAB_WEB_PROVIDER=brave
set BRAVE_SEARCH_API_KEY=...
nslab doctor
```

The live provider calls Brave Search's news endpoint, records only hashed/excerpted
source artifacts, and still passes every result through `TemporalWebGuard`; any
result without a parseable publication timestamp is excluded from BLIND evidence.

## stock-web Price Source

Set `NSLAB_STOCK_WEB_PATH` to a local checkout or cache of `https://github.com/Songdaiki/stock-web`.

```bash
set NSLAB_PRICE_PROVIDER=stock-web
set NSLAB_STOCK_WEB_PATH=data/cache/stock-web
nslab doctor
```

To let the price factory prepare a GitHub-backed cache on demand, opt in explicitly:

```bash
set NSLAB_PRICE_PROVIDER=stock-web
set NSLAB_STOCK_WEB_CACHE=1
set NSLAB_STOCK_WEB_CACHE_PATH=data/cache/stock-web
set NSLAB_STOCK_WEB_REMOTE_URL=https://github.com/Songdaiki/stock-web.git
```

When `NSLAB_STOCK_WEB_PATH` or `NSLAB_STOCK_WEB_CACHE=1` is set, the config loader
also selects `stock-web` for compatibility. Setting `NSLAB_PRICE_PROVIDER=stock-web`
directly is stricter: if neither an explicit path nor an enabled cache is
available, the price factory fails instead of falling back to mock data.
`nslab doctor` reports the effective stock-web path it will inspect: the explicit
path when present, otherwise the enabled cache path.

The adapter reads `atlas/manifest.json` and `atlas/schema.json`, then uses the
configured shard roots such as:

```text
atlas/ohlcv_tradable_by_symbol_year/{prefix}/{code}/{year}.csv
```

It recognizes the standard stock-web short columns and schema-declared aliases for
the same fields:

```text
d,o,h,l,c,v,a,mc,s,m
```

Outcome labels use the previous tradable row for that ticker, not the previous calendar day.
During blind analysis, configured stock-web data is wrapped by `BlindPriceGuard` and
only D-1-or-earlier snapshots can be passed into final synthesis.

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
warehouse/events.parquet
warehouse/event_sources.parquet
warehouse/event_ticker_edges.parquet
warehouse/predictions.parquet
warehouse/daily_outcomes.parquet
warehouse/market_memory.parquet
warehouse/mechanism_memory.parquet
warehouse/company_memory.parquet
```
