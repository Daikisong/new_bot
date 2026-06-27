# news-scalping-lab

`news-scalping-lab` is a Python CLI system for importing long-running market-news research, compiling it into a versioned research brain, and producing reproducible pre-open analysis reports from a new news CSV.

The implementation is intentionally LLM-native. Production code does not contain domain keyword maps, stock whitelists, ticker lists, or fixed theme score tables. Research knowledge is data in `research/`, `memory/`, and `brain/`. `nslab audit hardcoding` scans source plus production prompts and repo guidance for those hardcoding patterns.

## Quick Start

```bash
python -m pip install -e ".[dev]"
python -m news_scalping_lab.cli init
python -m news_scalping_lab.cli doctor
python -m news_scalping_lab.cli news inspect docs/csv/news_20260624.csv
python -m news_scalping_lab.cli brain rebuild --mode catalog --allow-catalog
python -m news_scalping_lab.cli brain audit
python -m news_scalping_lab.cli warehouse rebuild
python -m news_scalping_lab.cli warehouse verify
python -m news_scalping_lab.cli analyze --news docs/csv/news_20260624.csv --trade-date 2026-06-24 --cutoff 2026-06-24T08:59:59+09:00 --mode exhaustive --web-search
python -m news_scalping_lab.cli evaluate --trade-date 2026-06-24
python -m news_scalping_lab.cli brain update --episode 2026-06-24 --mode catalog --allow-catalog
```

This quick start uses the deterministic `--mode catalog --allow-catalog` path so
it works with the default mock provider while remaining clearly marked as
catalog-only. Production rebuilds omit `--mode` or pass `--mode llm-full`; mock
providers are rejected instead of being promoted as a production brain.

The same mock end-to-end flow is also available as one command:

```bash
python -m news_scalping_lab.cli demo
```

`doctor` prints a JSON readiness report for environment variables, API provider
readiness, DuckDB/warehouse state, stock-web configuration, brain HEAD, accepted
episode count, vector index state, and schema versions. Use `doctor --strict` in
automation when readiness findings should fail the command.

Outputs:

```text
predictions/YYYY-MM-DD.json
reports/YYYY-MM-DD_preopen.md
reports/YYYY-MM-DD_postmortem.json
runs/manifests/<run_id>.json
research/accepted/EP-*.json
brain/current/brain_manifest.json
brain/current/coverage_manifest.json
brain/current/record_coverage_manifest.json
memory/records/<episode_id>.jsonl
memory/record_manifests/<episode_id>.json
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
nslab brain update --episode <episode_id> --mode llm-full
nslab brain audit
```

`research import` reports the imported episode ID, trade date, mode, and source
path, and exits non-zero for missing or non-file input paths.
`research validate`, `research accept`, and `research reject` exit non-zero for
unknown episode IDs; accept/reject outputs use project-relative paths.

Batch flow:

```bash
nslab research import-batch data/inbox/research/
nslab brain rebuild --mode catalog --allow-catalog
nslab brain audit
```

`import-batch` accepts imported episodes by default so the documented rebuild flow
updates the brain immediately. Use `--no-accept` when you want to stage episodes
for manual validation first. The command reports imported/accepted IDs, counts,
source files, and skipped non-file paths, and exits non-zero for missing or
non-directory input paths.

Versioned research bundles:

```bash
nslab research inspect-bundle docs/20260622_nslab_episode_bundle.example.md
nslab research smoke-bundle --path path/to/real_bundle.md --require-valid
nslab research import-bundle path/to/bundle.md --validate --accept
nslab memory apply-company-deltas --as-of 2026-06-24T08:59:59+09:00
nslab memory stats
nslab memory audit --deep
```

`inspect-bundle` is version-aware and reports bundle, manifest, and episode schema
versions, raw/normalized brain record counts, training-eligible counts, type
distribution, provenance closure, and hash mismatches. `smoke-bundle` searches
`data/inbox/research/`, `tests/fixtures/research_bundles/`,
`NSLAB_REAL_BUNDLE_PATH`, then the explicit `--path`, writes
`diagnostics/bundle_smoke_report.*`,
and treats fixture-only success as synthetic smoke rather than production real
smoke. Explicit `.example.md` bundles are also kept out of production smoke so
documentation samples cannot satisfy real-bundle readiness. Production readiness
also checks that the selected real smoke bundle was imported into
`research/episodes/` and `memory/record_manifests/` with matching raw bundle hash
and record counts. It also rechecks the imported episode's
`validation_report.json` for import-loss, raw ID/type, training-eligible, typed
payload, and raw payload hash parity before considering the real bundle import
production-ready. `import-bundle` preserves the
original Markdown bundle, raw blocks, normalized episode index, record manifest, and
canonical `BrainRecordEnvelope` JSONL without forcing v10/v11 payloads into the
legacy `ResearchEpisode` model. Unsupported future bundle versions that still
expose a common episode envelope and `brain_delta.jsonl` are staged as
`forward_compatible_raw_only`: raw records are preserved with training disabled
until a versioned adapter exists. Opaque unsupported bundles are quarantined under
`data/quarantine/research_bundles/` without dropping the source bundle.

Canonical record artifacts:

```text
data/raw/research/<bundle_sha>.md
research/episodes/<episode_id>/original_bundle.md
research/episodes/<episode_id>/bundle_envelope.json
research/episodes/<episode_id>/normalized_episode_index.json
research/episodes/<episode_id>/raw_blocks/
research/episodes/<episode_id>/validation_report.json
memory/records/<episode_id>.jsonl
memory/record_manifests/<episode_id>.json
memory/record_index/
```

Known `brain_delta` record types are typed and preserved. Unknown record types are
kept as `UNKNOWN_TYPED_PAYLOAD`, forced to `training_eligible=false`, and reported
by `nslab memory audit --deep` rather than silently dropped.
`company_memory_delta` records are applied as timestamped company memory entries;
both record `available_from` and payload `known_at` must be cutoff-available before
they can enter daily analysis context, so later business relations are not
backfilled into earlier runs.

`brain update --episode` defaults to `llm-full`, rebuilds through the production
compiler, and fails without a real provider. Offline smoke and legacy migration
flows must opt into the deterministic compatibility path with
`brain update --episode <id> --mode catalog --allow-catalog`; that path performs
a safe incremental merge when the current brain already covers the prior accepted
set exactly. If the current manifest is missing or drift is detected in
compatibility mode, it falls back to `brain rebuild --mode catalog
--allow-catalog`; catalog rebuilds remain reproducible from accepted source
episodes. `brain audit` reports the current build mode while preserving the last
full rebuild timestamp across incremental updates.
Brain shard summaries and daily memory-sweep shards both use
`limits.shard_episode_count` from `configs/default.yaml`, so context budget changes
are reflected in rebuilds and run manifests instead of being hidden in code.

`brain rebuild` also refreshes `memory/vector_index/manifest.json`,
`memory/vector_index/episodes.jsonl`, and `memory/vector_index/brain_records.jsonl`.
Catalog/local rebuilds use a deterministic embedding provider projection of
accepted episodes and normalized records. `llm-full` rebuilds use the configured
LLM embedding provider, so `doctor --production` can reject deterministic indexes
instead of promoting them as production semantic indexes. Retrieval supports
analysis, but it is never a candidate gate. `nslab audit coverage` fails if brain
coverage is incomplete, the vector index is stale, record coverage is incomplete,
or warehouse projections are out of sync.

Production brain compilation is guarded:

```bash
nslab brain rebuild
nslab brain rebuild --mode llm-full
nslab brain rebuild --mode catalog --allow-catalog
nslab memory rebuild-index --production
nslab doctor --production
```

`brain rebuild` defaults to `llm-full`. `llm-full` requires a real non-mock LLM
provider and normalized brain records. `memory rebuild-index --production` is
the standalone production semantic-index rebuild path; it rejects mock providers
and requires `OPENAI_API_KEY` for OpenAI-compatible embedding providers.
`catalog` preserves the deterministic compiler for tests, offline smoke, and
legacy migration, but `doctor --production` rejects catalog/full/incremental brain
manifests as production research brains. Production readiness also rejects mock
web research evidence; configure a live provider before treating web citations as
production evidence.
When production readiness fails, `doctor --production` includes
real bundle smoke/import status, `required_environment`, and
`remediation_commands`. It also emits `finding_counts_by_category`,
`findings_by_category`, and `blocker_summary` so long readiness reports can be
triaged by bundle, LLM, embedding, web-evidence, brain, record, warehouse, and
training gates. The normal production sequence is:
For `llm-full` brains it also reports `llm_full_brain`, which verifies the
compile manifest, compiled claims JSONL, configured provider/model match,
category counts, and the latest compile run cache/live-call accounting from
`diagnostics/brain_compile_report.json`.
It also reports `record_store`, a deep record-store audit summary that gates
production on brain_delta raw/normalized ID parity, training-eligible/type-count
parity, raw payload hash traceability, provenance closure, and record counts.
All-cache `llm-full` rebuilds are useful reproducibility evidence, but production
readiness requires at least one live LLM call in the latest compile run and all
compile evidence must match the current brain manifest version. The compile
source record count must also match current record coverage. The production
semantic index must be current, LLM-embedded with the configured provider and
embedding model, include brain-record vectors, and match the same record coverage
count. If `NSLAB_OPENAI_EMBEDDING_MODEL` is unset, production readiness verifies
against the OpenAI provider default `text-embedding-3-small`.

```bash
set NSLAB_LLM_PROVIDER=openai
set OPENAI_API_KEY=...
set NSLAB_WEB_PROVIDER=brave
set BRAVE_SEARCH_API_KEY=...
set NSLAB_REAL_BUNDLE_PATH=path\to\real_bundle.md
python -m news_scalping_lab.cli research smoke-bundle --path %NSLAB_REAL_BUNDLE_PATH% --require-valid
python -m news_scalping_lab.cli brain rebuild --mode llm-full
python -m news_scalping_lab.cli memory rebuild-index --production
python -m news_scalping_lab.cli warehouse rebuild
python -m news_scalping_lab.cli warehouse verify
python -m news_scalping_lab.cli brain audit --deep
python -m news_scalping_lab.cli training export-sft
python -m news_scalping_lab.cli training export-preference
python -m news_scalping_lab.cli training export-evals
python -m news_scalping_lab.cli training audit
python -m news_scalping_lab.cli doctor --production
```

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

`exhaustive` mode is the default quality mode. It sweeps every accepted research
episode and every cutoff-available brain record, then fails if either coverage
count is incomplete. Retrieval misses are recorded but never used to block
open-world candidate generation.
When `--mode` is omitted, `nslab analyze` uses `default_mode` from
`configs/default.yaml`, whose initial value is `exhaustive`.
`brain` mode also loads global brain files and shard brain summaries, and records
memory sweep artifacts for every time-available accepted episode and record so
those memories still influence synthesis indirectly.

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
with the required positive analog, negative control, near-miss, counterexample,
leader-selection pair, theme-formation failure, and candidate-generation error
query categories. The engine executes those queries against accepted episodes and
brain records, filters unavailable future evidence, and persists
`semantic_retrieval.jsonl` with category-level episode IDs, record IDs, and the
structural record filters used for each query.
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
When retries occur, trace and checkpoint payloads retain the transient retry error
history before the successful response or final failure.
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
When canonical brain records exist, exports use explicit eligible records as the
source of truth. Preference rows come only from sealed
`blind_leader_preference_pair` records, not from cross-products of positive and
negative candidates. Ineligible records are excluded from JSONL outputs and listed
in the manifest with record IDs and reasons.
The manifest also records `phase_outputs.BLIND` and `phase_outputs.POSTMORTEM`
JSONL files with independent hashes and row counts. Use the BLIND phase file for
blind-only SFT; failure-correction rows stay in the POSTMORTEM phase file.
Rows carry `training_category`, and manifests include `required_training_categories`,
`category_counts`, and `missing_training_categories` so blind reasoning, theme formation,
beneficiary discovery, leader comparison, preference, and failure-correction examples
stay separated and auditable.
`nslab audit provenance` recomputes export hashes, row counts, category counts, and
BLIND/POSTMORTEM phase consistency from the JSONL rows.
Use `nslab training audit` to verify exported files, record weight checks, and
record-backed manifest consistency. The audit reads the JSONL outputs directly and
fails on ineligible exported rows, sealed-pair preference violations, hash/count
mismatches, or BLIND/POSTMORTEM phase mixing.

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
python -m news_scalping_lab.cli full-check
make full-check
```

## Warehouse

The warehouse is a derived DuckDB/Parquet projection, not the source of truth.
Canonical research stays under `research/`, `memory/`, and `brain/`.

```bash
nslab warehouse rebuild
nslab warehouse inspect
nslab warehouse verify
```

`warehouse inspect` preserves top-level row counts for each Parquet file and adds
a `status` object with required-file presence, sync flags, missing/unreadable files,
count mismatches, identity mismatches, and warehouse-specific findings.

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
warehouse/brain_records.parquet
warehouse/issuer_day_cases.parquet
warehouse/direct_event_cases.parquet
warehouse/theme_formation_cases.parquet
warehouse/beneficiary_cases.parquet
warehouse/leader_pairs.parquet
warehouse/error_cases.parquet
warehouse/memory_claims.parquet
warehouse/research_questions.parquet
warehouse/record_provenance.parquet
warehouse/record_coverage.parquet
```

Record-level warehouse verification checks row-count parity against
`memory/records/*.jsonl`, record ID uniqueness, type-specific table counts,
training eligibility flags, provenance links, and record coverage groups.

## Diagnostics

Operational commands write machine and Markdown reports under `diagnostics/`:

```text
diagnostics/bundle_import_report.json
diagnostics/bundle_import_report.md
diagnostics/bundle_smoke_report.json
diagnostics/bundle_smoke_report.md
diagnostics/brain_record_store_report.json
diagnostics/brain_record_store_report.md
diagnostics/record_coverage_report.json
diagnostics/record_coverage_report.md
diagnostics/brain_compile_report.json
diagnostics/brain_compile_report.md
diagnostics/training_export_report.json
diagnostics/training_export_report.md
diagnostics/migration_report.json
diagnostics/migration_report.md
diagnostics/production_readiness_report.json
diagnostics/production_readiness_report.md
```
