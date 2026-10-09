# Daily Brain Manual Use and Outcome Status

Date: 2026-10-09 KST

## Run a pre-open CSV

From the repository root:

```powershell
.\scripts\run_daily_brain.ps1 -NewsCsv "C:\path\to\news_YYYYMMDD.csv"
```

The standard filename supplies the trade date. Cutoff defaults to `08:59:59 KST`; pass `-TradeDate` for a nonstandard filename or `-Cutoff` for a different explicit cutoff. The default data project is `runs/daily_csv_oot_replay_20261008/project`; override it with `NSLAB_DAILY_PROJECT_ROOT` or `-ProjectRoot`.

The entry point verifies the selected BrainPackage pointer and package root, refuses to run against an activated production pointer, and refuses to overwrite an existing canonical prediction/report for that trade date. It invokes `python -m news_scalping_lab.cli analyze-daily --project-root ...`; the CLI reads the code repository's `.env` for existing provider configuration while storing the prediction, report, traces, and manifests in the separate data project. `-WhatIf` performs path/identity checks and prints the intended run without calling a model.

Outputs are `predictions/YYYY-MM-DD.json`, `reports/YYYY-MM-DD_preopen.md`, and `runs/manifests/<run_id>.json` under the selected data project. A normal run uses the current-news-grounded compiled brain before the first decision request, then makes one logical GPT decision request with at most one structured repair. It does not import/rebuild the research corpus, use web evidence, or place orders.

## Current operating classification

The selected package root is `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd`. Its manifest has `production_eligible=false` and its project pointer has `production_activated=false`. The manual path is therefore labeled research/paper use: it produces the brain-informed daily sector and ticker report, but this does not assert predictive quality approval, automatic trading readiness, or live-order permission. The missing post-close score does not block generating the daily report.

## Post-close price check

Status: `SKIPPED_INPUT_MISSING`. No actual prices were opened or scored for the 29 sealed predictions.

- The configured local `data/cache/stock-web` path is absent.
- The repository's `warehouse/daily_outcomes.parquet` contains one legacy `2026-06-24` mock-evaluation row, not outcomes for the replay dates.
- The configured upstream stock-web atlas manifest declares `max_date=2026-06-22`, before the 2026-08-24 through 2026-10-06 replay dates, and identifies its OHLC as raw/unadjusted. See the [manifest](https://raw.githubusercontent.com/Daikisong/stock-web/main/atlas/manifest.json) and [repository README](https://github.com/Daikisong/stock-web).
- The official KRX stock daily-trading API lists historical security data from 2010 onward. KRX usage requires an account, API key, and approval for the requested API; this project has no configured KRX key or current-date price atlas. See [KRX API usage](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) and the [stock daily-trading service](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC).

Do not treat the mock row or the old price atlas as a score for the 29 predictions. With valid post-close OHLCV through the replay dates, score predicted-candidate returns/precision separately from whole-market recall; whole-market recall requires the complete eligible ticker universe, not just tickers the model named.

## Verification

- The 29-date sealed replay remains unchanged; no prediction was rerun or overwritten.
- `scripts/run_daily_brain.ps1 -WhatIf` resolved the 2026-10-06 input, package root, and research-only status without a model call.
- `tests/unit/test_cli.py` passed, including the new separate-data-project CLI test.
- No package rebuild, outcome access, scoring, training export, or production activation was performed for this usability change.
