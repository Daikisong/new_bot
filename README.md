.

<!-- NSLAB current-run temporary byte links -->

https://cdn.jsdelivr.net/gh/Daikisong/new_bot@b825e2d4f8e530987915112753c26fe65ca42def/docs/research_prompt.md

https://cdn.jsdelivr.net/gh/Daikisong/new_bot@b825e2d4f8e530987915112753c26fe65ca42def/docs/csv/news_20180628.csv

https://cdn.jsdelivr.net/gh/Daikisong/new_bot@b825e2d4f8e530987915112753c26fe65ca42def/docs/example2.md

https://cdn.jsdelivr.net/gh/Daikisong/stock-web@f49513d4b75cb1174aa52cff3d236b03ffb88b9d/atlas/research_daily/access/2018/06/20180628.json

https://cdn.jsdelivr.net/gh/Daikisong/stock-web@f49513d4b75cb1174aa52cff3d236b03ffb88b9d/atlas/research_daily/manifest.json

https://cdn.jsdelivr.net/gh/Daikisong/stock-web@f49513d4b75cb1174aa52cff3d236b03ffb88b9d/atlas/research_daily/schema.json

https://cdn.jsdelivr.net/gh/Daikisong/stock-web@f49513d4b75cb1174aa52cff3d236b03ffb88b9d/atlas/research_daily/trading_calendar.csv

https://cdn.jsdelivr.net/gh/Daikisong/stock-web@f49513d4b75cb1174aa52cff3d236b03ffb88b9d/atlas/research_daily/snapshots/2018/06/20180627.csv

## Quick Start

```bash
python -m pip install -e ".[dev]"
python -m news_scalping_lab.cli init
python -m news_scalping_lab.cli doctor
python -m news_scalping_lab.cli news inspect docs/csv/news_20260624.csv
python -m news_scalping_lab.cli analyze --news docs/csv/news_20260624.csv --trade-date 2026-06-24 --cutoff 2026-06-24T08:59:59+09:00 --mode exhaustive
python -m news_scalping_lab.cli evaluate --trade-date 2026-06-24
```

## Production Preparation

Production BLIND analysis uses `CSV_MEMORY_ONLY_STRICT`: current CSV, cutoff-safe
memory and brain artifacts, and D-1 stock-web data. General web search is disabled
and a Brave key is not a production requirement. Post-close web review is a
separate audit artifact and cannot mutate the sealed prediction.

Authenticate with the installed Codex CLI, then prepare the deployment checkout:

```bash
codex login
python -m news_scalping_lab.cli auth codex-status
python -m news_scalping_lab.cli production bootstrap-local --evidence-policy csv-memory-only-strict --llm-provider codex-oauth --embedding-provider auto --stock-web-path <STOCK_WEB_PATH>
python -m news_scalping_lab.cli production prepare-local --stock-web-path <STOCK_WEB_PATH>
python -m news_scalping_lab.cli doctor --production-preflight
```

`bootstrap-local` creates five distinct HMAC keys in the ignored local `.env`.
It never reads or copies Codex OAuth credentials. `prepare-local` uses the official
Codex CLI login, probes embedding capability, and prepares the pinned local
sentence-transformer when Codex exposes no embedding command. These commands do
not import 606,737 records, run the 40-day shadow gate, or activate a release.

See [production_activation_runbook.md](docs/0813/production_activation_runbook.md)
for the attested import, shadow evaluation, finalize, activate, and rollback flow.

## Production Memory Index

The deterministic JSONL vector index is for local tests only. Production uses the
pinned real embedding provider with `FAIL_CLOSED` policy and immutable DuckDB
FTS/HNSW memory-cell snapshots.

```bash
python -m news_scalping_lab.cli memory rebuild-index --production
python -m news_scalping_lab.cli memory inspect-index
python -m news_scalping_lab.cli memory search-cells "event mechanism" --cutoff-at 2026-06-24T08:59:59+09:00
```

## Quality Gates

```bash
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
python -m news_scalping_lab.cli full-check
python -m news_scalping_lab.cli demo
make full-check
```
