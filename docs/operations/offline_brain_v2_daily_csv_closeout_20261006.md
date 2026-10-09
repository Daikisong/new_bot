# Offline Brain V2 and Daily CSV Closeout

Audit snapshot: 2026-10-08 KST
Result: one-time package compile and lineage audit pass; corrected XKRX window and v5 citation validation pass; all 29 supplied XKRX session dates are sealed through the current `analyze-daily` path (28 new runs plus one hash-verified existing smoke). This is functional/OOT runtime evidence, not predictive-quality approval. Predictive quality is unapproved; production remains on HOLD.

## Status

| Requirement | Status | Evidence |
|---|---|---|
| One-time offline compile | `COMPLETE` | Compile ID `OFFLINE-COMPILE-0dd9198ac9ef79215ab1` completed 2026-10-06 19:16:34 KST. Do not rerun import, repair, embedding, map, or compile. |
| Immutable package integrity and HNSW readiness | `PASS` | `BrainPackageDailyContextProvider.ensure_ready()` recalculated the package root and loaded the DuckDB read-only. Both semantic-capsule and mechanism-claim query plans passed the HNSW checks. |
| Plan/DAG/source lineage | `PASS` | All 1,868 planned task IDs and child lists exactly match DB nodes. The one-root graph is acyclic, has no missing, unreferenced, or multiply-parented nodes, and its coverage root matches the influence manifest. |
| Capsule and leaf coverage | `PASS` | All 4,969 leaf rows have valid count/root; their disjoint capsule-ID union exactly matches 52,644 JSONL and DB capsule payloads. The world coverage root is `15d9e8c7bf2f339a2a119952fe46f01fe2cf9f56c1e8a8750ec3a0d1f9d27c18`. |
| Assignments and centroids | `PASS` | 823,279 assignment export rows match DB rows exactly. Membership root matches `dd51591eeab66636cedfe0ac14af14d13d2a0a5c43ee00eaff30675f5ffbb401`. Assignment-to-centroid, capsule-to-centroid/category/member-count mismatches are all 0. |
| Mechanism claim citations | `PASS` | 40 claim payloads match between JSONL and DB. Their 111 support/contradiction edges match payload IDs and roles exactly: 105 supporting, 6 contradicting; no orphan or duplicate edges. |
| Payload exposure accounting | `PASS / PARTIAL EXPOSURE` | 181,979 unique records (22.104%) have a full payload exposure entry. Exposure root `0d402ea2aa50c9c4cf8a5048ec9739fe19cca2e2495d5184da6aad4a61a48072` matches both manifests; truncation count is 0. The other 641,300 records were not directly payload-exposed to the LLM. |
| Actual daily path | `PASS_HISTORICAL_OOT_29_SESSIONS` | 28 new dates passed the full v5 `analyze-daily` CLI; 2026-09-28 was reused after validating the existing full smoke hashes. All 29 session seals and artifact hashes were rechecked. This is functional/OOT runtime evidence, not a blind quality result. |
| Predictive quality gate | `NOT_RUN_GATE_MISSING` | No registered bounded gate with matching one-call architecture and HOLDOUT/paired score closure was found. Predictive quality is `UNAPPROVED`. |
| Production activation | `HOLD` | Package flags are `production_eligible=false`, `production_activated=false`. No pointer switch or production activation was performed. |

## Fixed Artifact Identity

| Item | Value |
|---|---|
| Package path | `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b\brain\packages\brain-v2-993b42487c557ca1` |
| Brain version | `brain-v2-993b42487c557ca1` |
| Package manifest SHA-256 | `3286247ce9271064455f702d1e44f8fdda4eb3659455f14e44517937da048378` |
| Package root | `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd` |
| Compile ID | `OFFLINE-COMPILE-0dd9198ac9ef79215ab1` |
| Source project | `C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project` |
| Source manifest SHA-256 | `6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576` |
| Source record corpus root | `2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75` |
| Memory snapshot | `MEMIDX-1e64a1b6e6ba7b07b799` |
| Build cutoff | `2026-08-21T18:52:07.302105+09:00` |
| Reduce plan SHA-256 | `6a3c78d89c55233afd66ef556eeb4cfba88c51047e140cccc268f95d5d51fc97` |
| Topology SHA-256 | `0663df89a0805c91f8526fc8a5126da004b06a20bbf0b7ac8ecb1978daa78ed5` |
| Embedding identity | `local_production:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2@e8f8c211226b894fcb81acc59f3b34ba3efd5f42:sha256:e0da458bb4f008d3c9fbf6dbff0fe0a482c025dfd7b759ef0294fb978d9eeca0:l2` |
| Package size | 19,791,364,549 bytes across 34 files; semantic DuckDB 18,426,376,192 bytes |

The source snapshot's actual manifest SHA matches the pinned source identity above. Its mutable `memory/retrieval_index/current.json` pointer still records `fc0d847d4eb0db688cf19570a3519d357a93558463797e26321a106d60040804`; the compiler manifest explicitly attests the actual pinned snapshot SHA and records the pointer mismatch. This report does not rewrite that pointer.

Reproducible package-root and real-HNSW readiness check (read-only; run from the compiler worktree):

```powershell
Set-Location 'C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b'
$env:PYTHONPATH = (Resolve-Path '.\src').Path
python -c "from pathlib import Path; from news_scalping_lab.config import load_settings; from news_scalping_lab.brain.offline_v2 import BrainPackageDailyContextProvider; package=Path(r'C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b\brain\packages\brain-v2-993b42487c557ca1'); settings=load_settings(Path.cwd(), resolve_production=False); provider=BrainPackageDailyContextProvider(settings, package_dir=package, embedding_provider=object()); provider.ensure_ready(); print({'passed': True, 'brain_version': provider._manifest.brain_version, 'package_root': provider._manifest.package_root, 'semantic_hnsw': provider._manifest.semantic_capsule_hnsw_index_ready, 'claim_hnsw': provider._manifest.mechanism_claim_hnsw_index_ready})"
```

The remaining row-level reconciliation in this report was performed read-only against the named immutable plan, JSONL ledgers, and DuckDB tables. Exact compared roots and counts are recorded below so an external reviewer can independently repeat each join without trusting the summary row counts alone.

## Semantic and Model Accounting

| Measure | Result | Correct interpretation |
|---|---:|---|
| Source records | 823,279 | Population accounting, not 823,279 independent LLM readings |
| Semantic units/capsules | 52,644 | Compiled semantic groups, not years or individual news summaries |
| Model-task DAG | 1,868 | 1,858 category-reduce, 9 category-review, 1 world-root tasks |
| Direct full-payload exposure | 181,979 / 823,279 (22.104%) | Exposure ledger has 181,776 direct leaf payloads and 203 full chunk-map-then-leaf records; no silent truncation |
| Not directly payload-exposed | 641,300 | They remain in the record assignment/population accounting; do not describe them as individually read by GPT |
| Mechanism claims | 40 | Cited capsule edges are separately verified above |

The 7,980 logical compile attempts reconcile as 7,513 compatible checkpoint hits, 465 fresh successful `gpt-6.1-sol/high` outputs, and 2 failed size-contract attempts. The checkpoint output ledger contains 7,978 successful/hit entries; the two extra attempts are trace errors `TRACE-207ae44fc91e` and `TRACE-8bb42949d000`, both `semantic reduce output exceeds 12000-byte contract`, after which bounded size-repair outputs succeeded. The final synthesis reused compatible map results; map-stage accounting was 7,498 checkpoint hits plus 15 fresh outputs. Historical reused outputs include `gpt-5.6-sol/xhigh`; do not attribute the entire package to new 6.1 calls.

The compile manifest's `prompt_token_count=1,296,623,760` is not a verified token or cost count: this implementation's reported value is a UTF-8-byte upper bound. Do not use it as actual tokens or spend.

The source record dates span `2018-01-03` through `2026-06-19` across 1,542 distinct trade dates, about 8.5 calendar years. This does not prove a full 10 years or every exchange session. This package is compiled knowledge for an existing GPT inference model, not a newly fine-tuned GPT model.

## Daily CSV Search (2026-10-06 Snapshot)

The required daily smoke must use an original, unmodified pre-open CSV whose trade date/cutoff follows package build cutoff `2026-08-21T18:52:07.302105+09:00`. It must contain no D-day prices/outcomes, after-cutoff rows or metadata, synthetic rows, or rows removed to force acceptance.

Searches on 2026-10-06 covered repository `docs/csv`, `production/staging`, `data`, `C:\Users\eorb9\Downloads`, and `C:\Users\eorb9\Downloads\Downloads (2)`. The latest repository fixture named `news_*.csv` is `docs/csv/news_20260624.csv`, which predates the package cutoff. The newer Download CSVs found were blog keyword/backlink, disk inventory, and unrelated exports; no eligible news input was present. Staging CSVs found were research-episode data, not pre-open daily input.

The configured health endpoint was queried read-only and returned Vercel login HTML rather than JSON. The temporary Chrome tab likewise showed `Login – Vercel`; the Browser Use machine preflight returned exit code `1` with no output, so CDP was used to confirm the same login response. No account credential was entered or retrieved. The transport CSV was not downloaded.

At this 2026-10-06 search snapshot, `analyze-daily` was not run. Later, the user supplied a date range with 29 session files after the package build cutoff; see the 2026-10-08 full CLI smoke section below. Do not set the production pointer.

## Quality Gate and Release Boundary

- The repository-root `runs/semantic_brain_upgrade/quality_full` directory is absent. `QSEL-19b3c80ba392db8564c9` is mentioned in preparation reports, but its referenced root selection file is absent; `diagnostics/quality_full_sealed_d_minus_one_preparation.json` states actual prediction-to-score status `NOT_RUN`.
- The staging project contains only the blind and separate outcome files for `QSEL-16352cbccb703547c2ba`, a three-case selection. No paired prediction, HOLDOUT closure, score, or registered gate for the deployable one-call `analyze-daily` architecture was found. The generic metrics in `configs/evaluation.yaml` do not constitute a registered gate. The outcome file was not opened for prediction or scored.
- `QPRED-704f15cde6e4152b6931` and its 379-pack ancestry remain `HALTED_MISALIGNED_DIAGNOSTIC_ONLY`; the `QPRED-4ecc6155c077cb5b092c` ancestry remains invalidated. Never resume, score, compare, promote, or use either as formal cache input.
- Current classification is `NOT_RUN_GATE_MISSING`, predictive quality `UNAPPROVED`, production `HOLD`. Do not invent an unbounded/evaluator-only gate. Activation still requires a passing registered same-architecture blind gate, explicit user approval, package-bound release manifest, and verified rollback target/procedure.

## Next Action

No compile, historical-corpus import, embedding build, package-lineage re-audit, or replay of the same 29 dates is due. The current v5 `analyze-daily` path has passed one full CLI smoke and the 29-session historical OOT replay recorded below. Remaining evidence is `news_20261007.csv` if that date is in scope, reliable collection-time provenance for blind evaluation, and a registered bounded same-architecture quality gate with physically separated outcomes. The current architecture is `one_time_brain_thin_daily.v4` with prompt `thin_daily.final_market_decision.v5`. Keep predictive quality `UNAPPROVED` and production `HOLD` until the gate and separate release requirements pass.

## Supplemental CSV Search (2026-10-06 20:18 KST)

A follow-up filename scan also covered the user's Desktop, Documents, OneDrive, and project worktrees. The newest `news_YYYYMMDD.csv` name in these locations remained `news_20260624.csv`; copies were found under `OneDrive\바탕 화면\KiwoomTools\뉴스모음` and the `news_bot_quota_guardfix` worktree. These are older than the package cutoff `2026-08-21T18:52:07.302105+09:00` and are not valid point-in-time smoke inputs for this package. No later-dated pre-open CSV was found in the added locations. The daily smoke remains `BLOCKED_INPUT_REQUIRED` pending the user's actual eligible CSV.

## Daily Architecture Unit Tests (2026-10-06 20:22 KST)

The focused command `python -m pytest tests/unit/test_thin_daily.py tests/unit/test_offline_brain_v2.py::test_daily_reader_uses_only_precompiled_package -q --durations=10` passed all 17 tests. These deterministic mock/fixture tests cover the single `final_market_decision` call, brain context loaded before that request, bounded repair (at most two provider invocations), call count independent of large record/cluster counts, and reading a precompiled fixture package. They do not exercise the audited 19.8 GB package with a real daily CSV or live model/provider, and they are not a predictive-quality gate. Full `pytest`, Ruff, and mypy were not rerun.

## Historical v3 Smoke With User CSV (2026-10-08)

The user supplied `C:\Users\eorb9\Downloads\123-20261007T194150Z-1-001\123`: 32 CSVs, 42,909 rows, from `news_20260824.csv` through `news_20261006.csv`. `news_20260821.csv` and `news_20261007.csv` are absent. Source files were not modified. None of the CSVs has a `collected_at` column, so publication times can be checked against cutoff but collection-before-cutoff is not independently evidenced.

The isolated historical functional smoke used `news_20261006.csv`, trade date `2026-10-06`, cutoff `2026-10-06T08:59:59+09:00`, and default window start `2026-10-05T15:30:00+09:00`. All 911 rows were inside that window; latest publication was `08:59:53`. Input SHA-256: `0a4b3d15324fbdd65869b550eed286a2bb06c06ef6416e4de84ca3c063eb33ca`.

The selected package remained `brain-v2-993b42487c557ca1`, package root `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd`. The run formed 873 material event capsules from 911 rows and loaded 10 compiled guidance artifacts, 24 semantic memory capsules, and 24 exact witnesses (0 mechanism claims selected). It did not re-import, re-embed the historical corpus, or rebuild the one-time brain. It did compute local embeddings for current-event clustering and retrieval queries. Daily import, daily rebuild, web calls, online full-corpus scans, and future-record exposure were all 0.

The `one_time_brain_thin_daily.v3` path made one successful `final_market_decision` logical Codex OAuth call using `gpt-5.6-sol/xhigh`; structured repair retries were 0. End-to-end time was 402.06 seconds (6m42s), provider interval 321.03 seconds. The prompt was 939,725 characters, below the Codex CLI hard limit of 1,048,576; the recorded 1,258,259 is a conservative UTF-8 byte upper bound, not tokenizer usage. Prompt SHA-256: `9e59e1977e29c3dd952449b7bafc05683d98ccc737e9371b8771d1b781de2ddf`.

To stay below the provider request limit, the prompt uses compact capsule field aliases and validates the returned `analyzed_cluster_count`, while the full capsule, source identity, timestamps, and row disposition remain in separately hashed artifacts. That count is a model self-report checked against the artifact count; it is not independent proof of semantic attention. The LLM received the 873 deduplicated current-event capsules, not all 911 full article bodies. The isolated output is under `runs/daily_csv_smoke_20261008_compact/outputs/THINRUN-1e092dd16b3be82ec275/`; it was generated on October 8 for a historical October 6 cutoff and is not a formal prediction, scored result, or training input. Canonical predictions/reports and the production pointer were not written.

The folder-wide parser audit found 9,380 rows before the app's default window and 0 after-cutoff rows. Since the default start is the previous calendar day at 15:30, Monday/holiday runs can omit Friday-after-close and weekend news before Sunday 15:30. The Tuesday October 6 smoke does not validate weekend/holiday window semantics; a trading-session-aware start needs explicit definition and tests before those dates enter formal evaluation. The absent October 7 file also remains untested.

The first real prompt attempt was safely rejected before model generation because its 1,198,258 characters exceeded the Codex 1,048,576-character limit. Daily prompt architecture v3 compacted repeated capsule keys and did not require the model to echo hundreds of long cluster IDs. The successful 2026-10-06 run returned six candidates, but no market outcomes were opened and no quality score was computed. This is a historical functional smoke, not proof that the later 2026-09-28 holiday-window/citation path passed. A timestamp audit also found that the historical model response supplied `created_at` equal to cutoff; the sealing code now overwrites created/sealed timestamps with the actual run time. The original smoke artifact is preserved unchanged for forensics.

Verification at that historical point: `python -m ruff check .` passed; `python -m mypy src/news_scalping_lab` passed for 139 source files; full `python -m pytest` passed 1,907 tests (350.92 seconds). Current post-follow-up test totals are recorded below. Neither test set is a predictive-quality evaluation.

## XKRX Window and v5 Citation Follow-Up (2026-10-08)

### Input Audit

The supplied folder `C:\Users\eorb9\Downloads\123-20261007T194150Z-1-001\123` contains 32 CSVs and 42,909 rows, from `news_20260824.csv` through `news_20261006.csv`. It has no `news_20260821.csv` or `news_20261007.csv`. The header is `page,row,date,time,title,body`; there is no `collected_at`. Publication time is available for cutoff checks, but actual pre-cutoff collection cannot be independently established.

The XKRX calendar identifies `2026-09-24`, `2026-09-25`, and `2026-10-05` as non-sessions. Their 3,011 rows are not analyzed as daily predictions. The other 29 session files total 39,898 rows; using the corrected previous-session close and `08:59:59 KST` cutoff, 39,898 rows are in-window, zero are before the window, and zero are after cutoff.

For `news_20260928.csv` (SHA-256 `59365528d7a303dc539abd8b1489a6b4e8caea06d06704ca7e03b7b90a09d07c`), the previous XKRX session is September 23 because the exchange was closed September 24-25. The correct window begins `2026-09-23T15:30:00+09:00`; all 1,627 rows are inside it. The file rows by publication date are September 25: 276, September 26: 462, September 27: 556, September 28: 333. The former calendar-day start of September 27 at 15:30 would incorrectly omit 1,088 rows. For October 6, the correct previous session is October 2; its 911 rows include October 5 holiday 434 and October 6 477.

The code now uses `exchange-calendars` XKRX sessions for `default_news_window_start`, `next_trading_day`, and non-session rejection. Unit tests cover the September 28/October 6 start times and rejection of October 5. No ticker, theme, or beneficiary mapping was added to code.

### Citation Failure and Repair

The package-backed September 28 v3 call returned schema-valid JSON but failed post-validation because sector `triggering_events` contained prose instead of exact event IDs. Trace: `production/staging/P9IMPORT-3D770A7DD72457C97098/project/runs/traces/TRACE-f61c675c082d.json`. No prediction or canonical output was accepted.

A later v4 re-cluster run recreated 1,568 capsules from the 1,627-row CSV and hash-matched the already retrieved brain context. Its two real responses still contained unsupported event IDs; strict citation validation rejected both. Traces: `TRACE-6bfbf8fa582e.json` and `TRACE-688c7f3b8e0d.json`. The first failure was in candidate `event_ids`; the repair wording did not adequately distinguish current IDs from IDs or strings in brain context.

The correction raised the prompt to `thin_daily.final_market_decision.v5`, added schema descriptions for `Candidate.event_ids` and sector `triggering_events`, and made both main and repair prompts require exact IDs from current capsule `e` arrays. Candidate repair errors now include the allowed event IDs from capsules whose `r` rows overlap the candidate's source rows. Repair remains bounded to one; a second invalid answer fails closed.

The v5 live decision replay used the newly reclustered capsules and their exact prevalidated brain context, avoiding a repeated 19.8 GB package audit. The first GPT request contained current news and cutoff-safe brain context together. It used Codex OAuth `gpt-5.6-sol/xhigh`, prompt SHA-256 `269d94455d3f40a1db5a9786a12bde85acad17bb08290e26d1efa9690723493a`, 944,204 characters, and 280.137 seconds. It returned 10 candidates and 3 sectors, needed no repair, and passed citation validation. Output: `production/staging/P9IMPORT-3D770A7DD72457C97098/project/runs/daily_csv_smoke_calendar_v5_decision_replay_20261008/THINREPLAY-72d78b0aeb01e69033bc/`. Package root remained `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd`. The replay manifest explicitly says it reused prevalidated brain context; it is not a new end-to-end CLI/package initialization run. Canonical predictions/reports and production pointer were not written.

During the cached-context re-cluster run, Python private memory stabilized at about 3.05 GB, process working set peaked around 2.77 GB, and available system RAM was about 17.6 GB. About 131 local embedding worker threads were present; CPU time did not continue increasing while Codex was waiting. No leak or unbounded growth was observed, but this is one Windows measurement and not a production latency guarantee.

### Current Gates

After the final source/schema changes, `python -m ruff check .` passed, `python -m mypy src/news_scalping_lab` passed for 139 files, and full `python -m pytest` passed 1,913 tests in 309.63 seconds. The tracked schemas match the official exporter. The one-time brain package and lineage remain `COMPLETE/PASS`. Current daily path is `SMOKE_PASS_ONE_HISTORICAL_DATE`; predictive quality remains `NOT_RUN_GATE_MISSING` / `UNAPPROVED`; production remains `HOLD`.

## Full v5 Analyze-Daily CLI Smoke (2026-10-08)

Trade date `2026-09-28`, cutoff `2026-09-28T08:59:59+09:00`, XKRX prior-session window `2026-09-23T15:30:00+09:00`. The unmodified `news_20260928.csv` has 1,627 rows and SHA-256 `59365528d7a303dc539abd8b1489a6b4e8caea06d06704ca7e03b7b90a09d07c`.

The full CLI ran in isolated evaluation project `runs/daily_csv_smoke_v5_cli_eval_20261008/`; it reused the immutable 34-file package through NTFS hardlinks rather than copying its 19.8 GB payload. The project-local pointer has `production_activated=false`. Package identity remained brain `brain-v2-993b42487c557ca1`, root `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd`, manifest SHA `3286247ce9271064455f702d1e44f8fdda4eb3659455f14e44517937da048378`.

Run `THINRUN-46d70c9550d63b790a40` formed 1,568 capsules and loaded 10 compiled guidance artifacts, 24 semantic memory capsules, and 24 exact witnesses before the first decision call. The live provider was Codex OAuth `gpt-5.6-sol/xhigh`, prompt `thin_daily.final_market_decision.v5`, with 1 logical call and 0 structured repairs. It returned 9 candidates and 5 sectors. Daily import, brain rebuild, web calls, online full-corpus scans, and future-record exposure were 0. Wall time was `452.54972` seconds; the provider interval was `276.056837` seconds. This is a single historical Windows measurement, not an SLA.

Evidence files are `runs/manifests/THINRUN-46d70c9550d63b790a40.json`, `runs/traces/TRACE-33ac424bb135.json`, and `runs/thin_daily/THINRUN-46d70c9550d63b790a40/`. The prompt SHA-256 is `fdbaedb70d5c8c152aec96b261cc85ce001f31ae9ad82b2948b3d214ea72b6bd`; the sealed prediction SHA-256 is `3dd8949e47e85cfba1d675358ec93366ef271f9d93a6ba707f1215a4a178f756`. The trace status is `ok`. The saved prediction's created/sealed time is the actual run time, `2026-10-08T07:08:55.820616+09:00`, not the historical cutoff.

Post-run strict validation passed: all 31 candidate event citations map to capsules overlapping that candidate's cited source rows; all 38 sector event citations exist in the current-news capsules. The prediction, report, traces, and manifests are confined to the evaluation project. No D-day outcomes were read or scored and no training data was created. Because the CSV lacks `collected_at`, pre-cutoff publication timestamps do not prove it had actually been collected by that cutoff. This result proves daily-path functionality for one historical sample only, not predictive quality or production readiness.

## CSV Delivery and Formal Evaluator Follow-Up (2026-10-08)

The user re-shared `C:\Users\eorb9\Downloads\123-20261007T194150Z-1-001\123`. Recursive inspection found no additional nested/archive inputs: it is the same 32 CSVs / 42,909 rows, with dates August 24 through October 6. There are 29 XKRX sessions / 39,898 rows and three non-session files / 3,011 rows. October 7 is still absent. The filename provides `trade_date`; the repo's daily cutoff is `08:59:59 KST` on that date, so no separate cutoff timestamp is needed. All rows in the 29 session files fall between previous XKRX session close at 15:30 and that cutoff, with zero after-cutoff publication timestamps. There is no `collected_at` column, so historical pre-cutoff collection cannot be independently attested.

The requested August 21 file is not needed for post-build testing: the fixed V2 build cutoff is `2026-08-21T18:52:07.302105+09:00`, after that date's `08:59:59` cutoff. Using an August 21 morning CSV would test with a brain compiled later that evening. August 24 is the first eligible post-build session in this folder.

The formal code compares three arms through the same `ThinDailyAnalyzer` one-call architecture: A has no historical brain, B uses the legacy category-brain baseline, and C uses Offline V2. A full registered run requires sealed split inputs, cutoff-safe D-1 context, a BUILD-only V2 package, and a separate outcome artifact that remains unopened until all prediction seals and citation closure are complete. The supplied daily folder is not itself such a selection/outcome package. The repository has no `runs/semantic_brain_upgrade/quality_full` result directory or registered deployable-architecture gate; `configs/evaluation.yaml` remains a generic metric configuration, not an approval gate.

Running the 29 dates as individual live daily calls is not a quality score. Linear extrapolation from the single 452.54972-second full CLI smoke is 13,123.94 seconds (about 3h38m44s), unmeasured and not an SLA. No 29-call run was started: the existing full v5 CLI smoke already establishes one functional path sample, while predictive-quality evidence requires the separate sealed protocol above.

The scorer audit also found that `_citation_closure_passed` checked candidate event IDs and source-row IDs against the whole input independently, rather than requiring each candidate event citation to come from a capsule overlapping its cited rows. The evaluator now enforces that same row-to-event relationship as the production daily validator, with a regression test. Verification after the fix: targeted test module 11 passed, changed-file Ruff passed, mypy passed for 139 source files, and full `python -m pytest` passed `1914` tests in `314.79s`. This was a static verification fix only: it did not run an LLM, open outcomes, or score historical predictions.

Current state remains one historical daily CLI smoke `PASS`; formal predictive quality `NOT_RUN_GATE_MISSING` / `UNAPPROVED`; production `HOLD`. The one-time V2 brain was not rebuilt. October 7 input, independent collection-time provenance, a registered bounded same-architecture quality gate, and separate release approval are still absent.

## Full 29-Session OOT Daily Replay (2026-10-08)

### Scope and result

The supplied directory `C:\Users\eorb9\Downloads\123-20261007T194150Z-1-001\123` contains 32 CSV files / 42,909 rows. The 29 XKRX trading-session files contain 39,898 rows. Three non-session files (`news_20260924.csv`, `news_20260925.csv`, `news_20261005.csv`; 3,011 rows) were excluded. Session dates span 2026-08-24 through 2026-10-06; `news_20261007.csv` is absent. The repository's date-derived cutoff was `08:59:59 KST`; each accepted row's publication timestamp was inside the previous-XKRX-close-to-cutoff window. No source CSV was modified. The absence of `collected_at` means this replay cannot independently attest when the source files were collected.

All 29 session dates are sealed: 28 fresh CLI executions and one existing 2026-09-28 smoke reuse. The fresh runs used Codex OAuth `gpt-5.6-sol/xhigh`, one `final_market_decision` logical call per date, and at most one structured repair. There were 6 repairs across the 28 new dates (34 fresh provider invocations total). The fresh runs produced 249 candidates in aggregate. Per-run wall time averaged 430.17 seconds, median 397.32 seconds, minimum 300.20 seconds, maximum 794.42 seconds; the sum of recorded per-run durations was 12,044.75 seconds (3h 20m 45s). This is sequential historical replay work, not the latency of one CSV and not a production SLA.

The fixed package root was `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd`. Final independent checks found zero SHA mismatches across every fresh prediction, report, and run manifest; zero package-root or runtime-contract mismatches; zero blind web calls, daily imports, daily brain rebuilds, online full-corpus scans, and future records. `candidate_event_id_correction_count` summed to 3; those deterministic corrections only remove or derive current-event citations from candidate source rows and do not alter candidate ordering/company/source rows. State confirms `outcomes_opened=false`. The reused 2026-09-28 prediction and run manifest also match their prior hashes. No outcome, score, training export, canonical prediction, production pointer, or activation was written.

Replay root: `runs/daily_csv_oot_replay_20261008/project/`. The resumable state and full per-case counters are at `runs/daily_csv_oot_replay_20261008/project/runs/daily_csv_oot_replay/oot_batch_state.json`; sealed predictions, reports, and run manifests are in that project's `runs/daily_csv_oot_replay/artifacts/`. The immutable 34-file package was hardlinked, not duplicated. Maximum observed Python post-GC RSS was 2,018.3 MB; available RAM stayed above 17 GB in sampled checks, with no monotonic growth observed. The copied package pointer remained `production_activated=false`.

| Date | Status | Run ID | Candidates/sectors | Logical calls/repairs | Wall seconds | Input CSV SHA-256 | Prediction SHA-256 | Run-manifest SHA-256 |
|---|---|---|---:|---:|---:|---|---|---|
| 2026-08-24 | PASS | `THINRUN-b2a6c3805a601aeacebd` | 11/5 | 1/1 | 693.63 | `2d8b6fb87ba274904135c2fa621c733aab430a55e2bc285a7330b48a07a5947d` | `7e2b099ed730253ee6d686aa497366ce9e3c71358603d400a8f0ee9cb6b96918` | `8fa222eb114dcd56b3831d865cd2c7bd4fd16d0bdaf5de72721908ceee060807` |
| 2026-08-25 | PASS | `THINRUN-64bcc62e819daf8d0a7a` | 5/3 | 1/0 | 301.31 | `9a2c46d9f73fb7938e0f93e9c563d08c59392f8bd93d6fdec76f05cbf4f66c1f` | `da83fd756e1971ba7c019a4e277b0a8e4058ad622c77a6a171725b85b2c896af` | `a69a2ac3f601f12b4cf65bc76f4cb3f513c5b1392384b95f48c5b05f347f29a9` |
| 2026-08-26 | PASS | `THINRUN-3f764cb4efc8ae541afa` | 8/4 | 1/1 | 303.80 | `1ab8bbf6d3ce4185424ecf9eba19f373051abc8e61f60bd9763c9c526ad09011` | `55496ac3b7b92ea5a7253fe46e19bce9da0f37da6bebf23620a9c7784d7da0c9` | `8c0b985fc3884e13c7f97da005a9d64227ca683171f5ed1b24c1f167a98f9d79` |
| 2026-08-27 | PASS | `THINRUN-a480d07be295936d189a` | 10/4 | 1/0 | 360.59 | `93b0ff09e9bc9b7e0bda0731eca2c76ed5a587c1049ea8817a194df4853f011c` | `145268dd8fe7d17d828ea4e88757cab30332de4f9e0c6968bea4dee4063355fd` | `c2a53dfbf9f5574d03f1f6c7df1a6c991f0c7045ffd1e88f5fa2038ff7d7bf89` |
| 2026-08-28 | PASS | `THINRUN-f8001c19b9ea65dbfc85` | 12/5 | 1/0 | 460.88 | `7ae9f69bdfeccdca3f54aacfd0c65650c41f4b4f345df8a23262b3145217b9cf` | `5fe62968c23e1da0a1999fbb0a4f9503e172f8b5f73677be57a4990dd17264db` | `35592a3bcb33ce9b21d33e9fd596318fda0d1062d80e46b912ca39eed3d1d913` |
| 2026-08-31 | PASS | `THINRUN-b328b46b292c2730f3e7` | 8/5 | 1/0 | 388.74 | `56642a640f01d7f8656aca6a8a27f43e8da4f84e30a65210fb13deba4adbc725` | `08d63bcb8a24c3af520b3dc1802bd431c529116dd108e4bb2b7588d3ee7d24e0` | `bd8b30426784aecce1c412df9e7ea2b16a8c3eee3de6d8510022d4dc30873fae` |
| 2026-09-01 | PASS | `THINRUN-b50232539078a49aec65` | 10/4 | 1/0 | 359.65 | `93ae9254faf0fdc447fc655969a319569a10d8d2dd464c7e8d5e18086a1df07f` | `f56a538e3d63b7c087985fd02ee453148bfb4310be29b6c1e90639ae70e68126` | `67603472b5ca09045494479af57af403d82322ffce857dab34d43c043304719d` |
| 2026-09-02 | PASS | `THINRUN-ab131e019df9becdd4ae` | 7/4 | 1/0 | 354.11 | `1b1a9c08b6646b9cd20a7bf58372deed155bbde5048fbf550a9f924c3ce35de9` | `6ae151daf95b121644002b0d2b9cd2c9068cd20d19d949f34370b88b854ab001` | `f5c4a152b70be74feb3465eff1f22886e190010687ffbd046ef057f3e788c24b` |
| 2026-09-03 | PASS | `THINRUN-cfb2e77554ff848eee0e` | 10/5 | 1/0 | 346.34 | `be339a02e2b9878628a5b3b64226534a59527b785205c1f45b85268175e0670f` | `cf63718c7571fe44d2f35888577061738bad8a1fb7c46da0ae1b1e6dea03d8db` | `54228643abb295cc6c858c71948ac78d201bf833c90e5b3d07aab4e4f61edafe` |
| 2026-09-04 | PASS | `THINRUN-a24de56dab417737b47c` | 10/6 | 1/1 | 588.22 | `29de58c9be0de1c3aed9aa38945b06e745c553e45d9888664d1e0b579de71d08` | `30866df848978f93d180cd48c05573c53f97ac01701329b3ba28a351812438c7` | `1900b37a590a2699fcb22aee5b2c9e86741c5d645a00938c838efa96448f0b17` |
| 2026-09-07 | PASS | `THINRUN-5a612daeaf20d04f6636` | 10/5 | 1/0 | 399.70 | `c2f24f5ed507c26df25a1d6d1ff312c91110d44c1de3b6233e6d899a6b4fb63b` | `eef217152abf76196ac6950a3f41a9cec65140f6cd8ae007b7ee740926123f00` | `ce64294a085336c454b1ff28d8e647f504864bafd16a8536938c634a3647fb97` |
| 2026-09-08 | PASS | `THINRUN-ba12f8e4d2219cac74bf` | 11/4 | 1/0 | 345.17 | `958fb7b1d3aea546a760e306398f18ca339b9b16453d477b8cd5f7fc31bb53f0` | `5523ff1fef58f802f03f22092be444b54b3eefadb4a5cecf9bf53f8bae79a150` | `d0f0f015b634a5e52c97218e8f561c12ac386c0b785c97091082d6c2def99b56` |
| 2026-09-09 | PASS | `THINRUN-7889ea3a2394080a62bd` | 8/4 | 1/1 | 612.43 | `c0f13eaca9127f4e1d5f70cdbfeb428fa58abd4bf05978219346e0be8ddbc2c1` | `c124c1433851bfd9191dcd05c15b0b46051b1007b00fe73408da6c48969b1f5a` | `40d52ddf41e6c013601592788dec97df95a55fe4194825ae44d1fd2db380639a` |
| 2026-09-10 | PASS | `THINRUN-4ca7e8dd676e3bf70af4` | 10/6 | 1/0 | 518.44 | `9c12c5df1bac177fbb4a079a09ac93598271ed63d60a314fe9dfba9d41f6b59a` | `3fc16a790ac754551f956269050bc61dcd8f16c9e3156f66905c8d1c5914db19` | `a16b648504ccc9b69d7e788f0c3ae89de2dac53edd1e2ccbcb7e83b02febf21c` |
| 2026-09-11 | PASS | `THINRUN-8d2f9412f00a1d9dfc2d` | 8/5 | 1/0 | 413.69 | `e098a3aad24f9f24cbc6d68f69978b8fc8435bff5dc549cacf668ed7a7a0debf` | `f44acbc22b2193a6b5437b7ec6e436e5abd4b4150086e7ec4fdd11036c4786b7` | `ac6bfcc461a2b574e5e074fa99434244c296631f76d1c5f66d33b87ca77aa4c2` |
| 2026-09-14 | PASS | `THINRUN-af9990cc1cc9397a6199` | 8/6 | 1/0 | 414.72 | `5cbfcb8ac4ee47c0f792ea078106efe38ee0430e6faf129599ef85cb1940b60e` | `6be8f79e18ab1065b8f6ba4b8dac8cc88ea794edd7ef81224da3aef379c57f05` | `199717002ded351e8297821f1b77b709d1776a9a1ca867fbfd5df5301c0b43cf` |
| 2026-09-15 | PASS | `THINRUN-bfdc06495bd9d9a6e40b` | 9/4 | 1/0 | 300.20 | `63b2cb918df33541f41c0843763bad603c0a60bc912bf8637af56bfa9bfbb83d` | `34fba9874ff424ade89e79cb4caef14ea0e84cc7df91a8a52b6049d90cd6844c` | `1110f7612d70dab11fceec2d401d960645ee47f27c5e1e45f176733ffe01e864` |
| 2026-09-16 | PASS | `THINRUN-75f1670a1bf5107a3118` | 7/4 | 1/1 | 614.55 | `65a0e290caf44c9d879bdf67d1a1a6cb1feebb8e5eb116c660fbb89ada958493` | `085c9b7afaca78410a429ed72a0df8a046188450ba7274a907a7110b12eae5a1` | `89b66a36a97baf1a3ece702d92ce89062cebfe20dae7083832bda0cc396c7172` |
| 2026-09-17 | PASS | `THINRUN-09fea8398f4621d3eb8e` | 10/3 | 1/0 | 424.50 | `17312a16c7fa6c9cc7b642105a7d91b7770b5a117b59a6fd409f0470ba0c0f8e` | `37e21e23c5d35d27bca49ab8b7085d5befa55a7297c5ef13d401859bce690d67` | `aefc65c8495361ec132acce5bc6f7f47b6c58038efc4109e4402ca5068abb2cf` |
| 2026-09-18 | PASS | `THINRUN-9db9481ad5cd4b24f9da` | 8/3 | 1/0 | 340.53 | `34098c7196641c97d2aff2828bd1f07f542950587109700908d60244f12e9d07` | `b3c663c47bd2ebaf15fa89a4264fdc816de020968b954204db75d804d25e839a` | `25a5e12e76cf963470e1d5efbcfa667e2541d63bc36eb250fa331440c3bc56b2` |
| 2026-09-21 | PASS | `THINRUN-279f3bcf9c7ca8061514` | 10/5 | 1/0 | 311.90 | `11d82eaa8bc7137d844ef34f3cb83dc8368fe24134ffcc6ae6ceaa370737ee38` | `47c0f8d9073ceaa4852d393d2d04f34c4c910805baefd249a1d1e64412f08cf8` | `17ec710d53442ee1686a3eed4e1d4baa1a9d61b055a4d003136a81776a1f34dc` |
| 2026-09-22 | PASS | `THINRUN-605edc2709174b3719da` | 8/3 | 1/0 | 423.97 | `fc54ba170f462ba1f5153837e96533e0916346c367d59d4c94011493009f70cb` | `b15c30f58edec71914ad9a75bbc5ea71e50c9680ab9a3be802c2ddfa2cb4c1ab` | `aba131c3312f99011906fc0e7f99dc08aa540937932a30ef54003aa7fd95f103` |
| 2026-09-23 | PASS | `THINRUN-df945f6aa01c9fb6de90` | 8/6 | 1/0 | 359.90 | `add27795bb6abfe71c6151a7df933838cb1cfc2472ee6d09d768d4ab2dc39771` | `4d70db04ceead78bbb25493d8fb768f6d059df350941397ef4ece4815a7a9bca` | `e9324e3efab5e0946b53c9024831800f24676087e8bd461c9a155b94bb1ae60e` |
| 2026-09-28 | REUSED | `THINRUN-46d70c9550d63b790a40` | 9/5 | 1/0 | 452.55 existing | `59365528d7a303dc539abd8b1489a6b4e8caea06d06704ca7e03b7b90a09d07c` | `3dd8949e47e85cfba1d675358ec93366ef271f9d93a6ba707f1215a4a178f756` | `bf8e557ddd2d364f763b6f61cb991d145d91fc3187e43caf358a205dd568fd7a` |
| 2026-09-29 | PASS | `THINRUN-d28dc6682156569350cf` | 9/5 | 1/0 | 453.91 | `014d6b00e178b3a4e6518ff9dfe64e65f419c870dcf4ced8fee5834927d1023a` | `51ff074cd3680deb68457690cc6def746d2860d35bc8d59b6fdf424fc1570e01` | `ba27f307bd01f271e2e47a34bfa5897fa12803d2d9268c2824636ddc4463aa7f` |
| 2026-09-30 | PASS | `THINRUN-17c3ab432fa9d6fcb712` | 8/3 | 1/0 | 356.53 | `35df1a21d6a7c1a058a252d5d039a58b4b2ca3654383f33edbc718cc0f492acd` | `af40e73a2de2ee724b5aea2c64d21400a6cf91fa92850c3d6c081a380fc7ba92` | `fc8cafa177da5625f82cbbcba46f64f70d8ab2f076e53047d901160f439108f3` |
| 2026-10-01 | PASS | `THINRUN-2f768532cd43e6db7939` | 6/3 | 1/1 | 794.42 | `66ab799f015fbe83db986bf34241b8fc5f7ff5a3ea1111b147abd5f60fd1ba36` | `2178c0f09ae5b203a385d1a3731620322b220fd1ff7dd84eac5321d7469fd32b` | `d0091e0c46c485ad4cbc9a4cb14e06d67180bd4792e92810deac9940e5f030ce` |
| 2026-10-02 | PASS | `THINRUN-46b17bc0352a40c5ab48` | 10/4 | 1/0 | 407.98 | `d1b2c04ed74fb722aae08a058315fa0a3eb10905b0d44e678ea31be4b34f70f9` | `2414fdfd3564e0acf4517f44145af70c85a7d6a815c6dc4564ddf2456ec2ba4f` | `c14895522f373d23cc257b1ae2f9b44ef6ece663f1befe340c16e58d911065ec` |
| 2026-10-06 | PASS | `THINRUN-002dc8c79909b3653b6a` | 10/4 | 1/0 | 394.93 | `0a4b3d15324fbdd65869b550eed286a2bb06c06ef6416e4de84ca3c063eb33ca` | `b180a30ec4f12982b0290f1bf5f96bc78250e4c98bdec44c0516d3a07441b48c` | `6b1311944d564a1db8b283f06ea5526cdf6f4cf0bc87c0eefa30d0a8ea85490f` |

The machine-readable state contains 29 entries and is resumable. Independent verification compared all 28 fresh run prediction/report/manifest files against their recorded SHA-256 and checked package root, one-call count, zero web/import/rebuild/full-corpus scan/future records, repair bound, and closed outcomes: hash mismatches 0, invariant failures 0, `outcomes_opened=false`. The existing 2026-09-28 prediction and manifest were separately rehashed against the reused entry and matched.

### Runtime fixes exercised by the replay

Before replaying dates, targeted regression work fixed three blockers: daily selected mechanism claims now close over selected capsule and record IDs; embedding vectors are omitted from the LLM-readable prompt while retained in the retrieval package/context; candidate event IDs are normalized against their cited source rows and the number of deterministic corrections is recorded in the run manifest. The last change never edits candidate rank, identity, or source rows. `candidate_event_id_correction_count` totaled 3 over the replay.

After these source/schema changes, verification passed: `python -m ruff check .`, `python -m mypy src/news_scalping_lab` (139 source files), and full `python -m pytest` (`1915 passed`, 1,253 warnings). The tests and runtime checks prove contract/function behavior only, not prediction accuracy. No outcome was opened or scored. The registered blind quality gate is still `NOT_RUN_GATE_MISSING`, predictive quality remains `UNAPPROVED`, and production remains `HOLD`; `news_20261007.csv` and collection-time provenance remain absent.

The 2026-10-09 manual daily entry point and post-close price-data availability are documented in [Daily Brain Manual Use and Outcome Status](daily_brain_manual_use_and_outcome_status_20261009.md). The entry point enables a supervised paper/research report run; it does not promote the package or claim a price-based accuracy result.
