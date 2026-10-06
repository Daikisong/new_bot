# Offline Brain V2 and Daily CSV Closeout

Audit snapshot: 2026-10-06 20:01 KST
Result: one-time package compile and lineage audit pass; real daily CSV smoke is blocked on input; predictive quality is unapproved; production remains on HOLD.

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
| Actual `analyze-daily` smoke | `BLOCKED_INPUT_REQUIRED` | No eligible post-build-cutoff pre-open CSV was found in the searched local locations. The temporary Vercel transport returned a Vercel login page. No prediction was run. |
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

## Daily CSV Search and Blocker

The required daily smoke must use an original, unmodified pre-open CSV whose trade date/cutoff follows package build cutoff `2026-08-21T18:52:07.302105+09:00`. It must contain no D-day prices/outcomes, after-cutoff rows or metadata, synthetic rows, or rows removed to force acceptance.

Searches on 2026-10-06 covered repository `docs/csv`, `production/staging`, `data`, `C:\Users\eorb9\Downloads`, and `C:\Users\eorb9\Downloads\Downloads (2)`. The latest repository fixture named `news_*.csv` is `docs/csv/news_20260624.csv`, which predates the package cutoff. The newer Download CSVs found were blog keyword/backlink, disk inventory, and unrelated exports; no eligible news input was present. Staging CSVs found were research-episode data, not pre-open daily input.

The configured health endpoint was queried read-only and returned Vercel login HTML rather than JSON. The temporary Chrome tab likewise showed `Login – Vercel`; the Browser Use machine preflight returned exit code `1` with no output, so CDP was used to confirm the same login response. No account credential was entered or retrieved. The transport CSV was not downloaded.

Therefore `analyze-daily` was not run. The next required input is the user's actual pre-open CSV with trade date later than 2026-08-21, plus its intended trade date and cutoff if not encoded in the filename/rows. Once available, bind the audited package in a separate evaluation/test project and verify the one-call, brain-loaded, zero-web path. Do not set the production pointer.

## Quality Gate and Release Boundary

- The repository-root `runs/semantic_brain_upgrade/quality_full` directory is absent. `QSEL-19b3c80ba392db8564c9` is mentioned in preparation reports, but its referenced root selection file is absent; `diagnostics/quality_full_sealed_d_minus_one_preparation.json` states actual prediction-to-score status `NOT_RUN`.
- The staging project contains only the blind and separate outcome files for `QSEL-16352cbccb703547c2ba`, a three-case selection. No paired prediction, HOLDOUT closure, score, or registered gate for the deployable one-call `analyze-daily` architecture was found. The generic metrics in `configs/evaluation.yaml` do not constitute a registered gate. The outcome file was not opened for prediction or scored.
- `QPRED-704f15cde6e4152b6931` and its 379-pack ancestry remain `HALTED_MISALIGNED_DIAGNOSTIC_ONLY`; the `QPRED-4ecc6155c077cb5b092c` ancestry remains invalidated. Never resume, score, compare, promote, or use either as formal cache input.
- Current classification is `NOT_RUN_GATE_MISSING`, predictive quality `UNAPPROVED`, production `HOLD`. Do not invent an unbounded/evaluator-only gate. Activation still requires a passing registered same-architecture blind gate, explicit user approval, package-bound release manifest, and verified rollback target/procedure.

## Next Action

No compile, import, embedding build, or package-lineage re-audit is due. Continue when the user supplies an eligible pre-open CSV or makes the authenticated transport available. Until then, keep this goal active with daily smoke `BLOCKED_INPUT_REQUIRED`, predictive quality `UNAPPROVED`, and production `HOLD`.

## Supplemental CSV Search (2026-10-06 20:18 KST)

A follow-up filename scan also covered the user's Desktop, Documents, OneDrive, and project worktrees. The newest `news_YYYYMMDD.csv` name in these locations remained `news_20260624.csv`; copies were found under `OneDrive\바탕 화면\KiwoomTools\뉴스모음` and the `news_bot_quota_guardfix` worktree. These are older than the package cutoff `2026-08-21T18:52:07.302105+09:00` and are not valid point-in-time smoke inputs for this package. No later-dated pre-open CSV was found in the added locations. The daily smoke remains `BLOCKED_INPUT_REQUIRED` pending the user's actual eligible CSV.

## Daily Architecture Unit Tests (2026-10-06 20:22 KST)

The focused command `python -m pytest tests/unit/test_thin_daily.py tests/unit/test_offline_brain_v2.py::test_daily_reader_uses_only_precompiled_package -q --durations=10` passed all 17 tests. These deterministic mock/fixture tests cover the single `final_market_decision` call, brain context loaded before that request, bounded repair (at most two provider invocations), call count independent of large record/cluster counts, and reading a precompiled fixture package. They do not exercise the audited 19.8 GB package with a real daily CSV or live model/provider, and they are not a predictive-quality gate. Full `pytest`, Ruff, and mypy were not rerun.
