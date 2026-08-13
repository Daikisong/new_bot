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
python -m news_scalping_lab.cli brain rebuild --mode catalog --allow-catalog
python -m news_scalping_lab.cli brain audit
python -m news_scalping_lab.cli warehouse rebuild
python -m news_scalping_lab.cli warehouse verify
python -m news_scalping_lab.cli analyze --news docs/csv/news_20260624.csv --trade-date 2026-06-24 --cutoff 2026-06-24T08:59:59+09:00 --mode exhaustive --web-search
python -m news_scalping_lab.cli evaluate --trade-date 2026-06-24
python -m news_scalping_lab.cli brain update --episode 2026-06-24 --mode catalog --allow-catalog
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
