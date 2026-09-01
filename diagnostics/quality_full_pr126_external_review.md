# QUALITY_FULL PR126 External Review Brief

## Live Status Addendum

The earlier checkpoint described below has been superseded by a live formal run.
As of `2026-09-02T01:22:58.9005034+09:00`, the honest current label is:

```text
IMPLEMENTATION_MECHANICALLY_VERIFIED
ACTUAL_3_CASE_PREDICTION_RUNNING_NOT_SCORED
CASE_1_V0_SEALED
CASE_1_V1_RUNTIME_EVIDENCE_PACKS_4_OF_379
OUTCOME_NOT_OPENED
PRODUCTION_HOLD
```

See `diagnostics/quality_full_pr126_live_progress.{json,md}` for the exact
point-in-time artifact commitments, completed pack checkpoints, scope of the
1.0554% figure, and the remaining ordered gates. The large runtime artifacts
are intentionally local and are not made auditable merely by this Git commit;
they require a separate hash-verified export.

## Requested Verdict

Review the pushed `codex/quality-full-pr126` branch as an implementation and
safety checkpoint. Do not infer predictive quality from the mechanical tests.

The honest current label is:

```text
IMPLEMENTATION_MECHANICALLY_VERIFIED
ACTUAL_3_CASE_PREDICTION_RUNNING_NOT_SCORED
PRODUCTION_HOLD
```

## Non-Negotiable Facts

- The old `QPRED-4ecc6155c077cb5b092c` ancestry is permanently
  `INVALIDATED_DIAGNOSTIC_ONLY`. It must never be resumed, scored, compared, or
  promoted.
- That invalid run did not represent 20 hours of full-corpus LLM reading. Its
  elapsed history mixed 26 live shared OAuth calls, 18 shared cache hits, local
  MiniLM/DuckDB/FTS work, retries, and interrupted attempts. V1 made zero LLM
  calls before invalidation.
- The replacement three-case selection is
  `QSEL-19b3c80ba392db8564c9`, SHA-256
  `bfcc21e433ab36cf457254f7587f7fb71d2a12e4a332a12c0fead0b0554d3f9c`.
- Its physically separate outcome selection SHA-256 is
  `6c10f848bf6c77ebb3df0aa8fa5a9b951c419c3412db4192d9ad8ab96a114bdb`.
- Paired prediction manifest `QPRED-704f15cde6e4152b6931` now exists for that
  selection. It has one of six expected seals (`NSLAB-20260102-be50ec83/V0`),
  no paired case closure, `all_predictions_sealed=false`, and
  `outcome_opened=false`. No score report exists yet.
- One safe prediction attempt created incomplete shared context
  `SHAREDCTX-9934ed4eef311fd9d5d6`. Existing map/reduce checkpoints were
  restored and seven live novelty-batch calls completed; the next call was
  interrupted, and the attempt was
  stopped cleanly before V0/V1 prediction closure. That historical attempt
  produced no `QPRED`, no score, and no production mutation; the current safe
  run is the separate `QPRED-704f15cde6e4152b6931` ancestry.
- Production brain, memory, warehouse, record store, and activation pointers
  were not changed.

## Implementation Under Review

1. `QUALITY_FULL` is fixed to Codex OAuth `gpt-5.6-sol/xhigh`; wall time,
   tokens, and call count are observations, not abort gates.
2. Prediction and scoring have physically separate inputs. Prediction receives
   only a blind selection and sealed D-1 data. Outcome selection is opened only
   after the complete expected case x variant prediction closure is verified.
3. V0 and V1 reuse one content-addressed shared pre-retrieval context per case.
   Shared semantic identity includes transitive input, prompt, model, clustering,
   component, and provider-checkpoint commitments.
4. Every material event enters deterministic coverage and the complete shared
   map/reduce tree. Cache resume replays authenticated completed checkpoints.
5. Full D-1 universes are sealed but final prompts receive only an exact
   candidate-ticker projection with the full universe commitment.
6. Runtime retrieval reports searched, selected, payload-exposed, memo-used,
   final-cited, offline-unexposed, rare, lane, episode, year, and unused record
   observations separately.
7. Hash-referenced JSON, JSONL, CSV, outcome, final-synthesis, D-1, timing, and
   prediction artifacts are hashed and parsed from the same read buffer. Resume
   rejects scoring state that predates global prediction closure.
8. The legacy `run-runtime-variant-shadow` path now seals the complete selected
   V0/V1 cartesian population before outcome access and uses one verified outcome
   buffer per case. It remains diagnostic-only because its legacy selection
   format contains outcome-reference strings; it is not a formal evaluator.
9. V1 runtime evidence is now packed across all clusters. Every selected
   `(cluster_id, record_id, lane)` assignment remains present, but the same
   record payload is serialized only once per bounded pack. An immutable call
   plan is written before the first provider call; the completed manifest binds
   that plan, normalized outputs, and authenticated tracing checkpoints.
10. A live safe run exposed a separate shared-map closure issue: the character
    budget packed 28 clusters despite a configured 12-cluster limit, and the
    provider omitted three cluster findings. The strict verifier stopped the
    run before sealing. Map and novelty batching now enforce both character and
    configured cluster limits, bind the numeric limits into cache identity, and
    treat non-`ok` checkpoints as live-retry candidates rather than successes.

## Safe Input Anchors

| Trade date | News rows | D-1 session | D-1 rows | D-1 bytes |
| --- | ---: | --- | ---: | ---: |
| 2026-01-02 | 490 | 2025-12-30 | 2,759 | 567,093 |
| 2026-03-24 | 1,652 | 2026-03-23 | 2,758 | 568,539 |
| 2026-03-16 | 2,506 | 2026-03-13 | 2,746 | 565,978 |

All three report zero D-day/future rows, zero duplicate tickers, zero prediction
price-repository access, and zero outcome access during preparation.

## Verification At This Checkpoint

```text
ruff                         PASS
mypy                         PASS (135 source files)
schema parity                PASS
full pytest                  PASS (1,863 tests)
independent boundary audit   PASS (no HIGH/MEDIUM)
safe-attempt OAuth calls     historical partial shared novelty only
real safe 3-case evaluation  RUNNING_NOT_SCORED
production mutation          0
```

The current full suite completed in 393.97 seconds. Warnings were
existing/runtime deprecation and audit-fixture warnings, not test failures.

Immediately after the earlier shared-closure fix, the gate was 1,847 passed in
395.15 seconds. The later live-progress checkpoint supersedes that count with
1,863 passed in 393.97 seconds; Ruff and Mypy (135 source files) also pass. See
`diagnostics/quality_full_shared_batch_closure_fix.{json,md}` for the historical
fix and `diagnostics/quality_full_pr126_live_progress.{json,md}` for the current
point-in-time state.

## Why The Safe Attempt Was Stopped

The first safe date contained 478 material clusters. The former implementation
made one to six sequential evidence calls per cluster, implying 478 to 2,868
additional calls for that date before final synthesis. This was duplicate
cross-cluster context fan-out, not required quality work and not evidence that
the full research corpus was being read. The attempt was checkpoint-stopped so
the repeated payloads could be packed without dropping assignments.

The replacement path writes `runtime_evidence_pack_plan.json` after local
retrieval and before any packed OAuth request. Its pack count is the exact
remaining runtime-evidence call count for that case and is the only supported
basis for a provider-time forecast.

## Local Retrieval Observation

No LLM was called in this benchmark. One material cluster required 33.328 seconds
and peaked at 7,034,241,024 RSS bytes. Two clusters required 61.971 seconds with
one worker and 49.702 seconds with two workers; two workers improved wall time by
1.247x while adding 1.808 GiB peak RSS. The OS cache was not flushed and worker 2
ran second, so this is not a throughput promise.

## Evidence Files

- `diagnostics/quality_full_pr126_live_progress.json`
- `diagnostics/quality_full_pr126_live_progress.md`
- `diagnostics/quality_full_invalidated_run_report.json`
- `diagnostics/quality_full_invalidated_run_report.md`
- `diagnostics/quality_full_sealed_d_minus_one_preparation.json`
- `diagnostics/quality_full_sealed_d_minus_one_preparation.md`
- `diagnostics/quality_full_representative_distribution_fix.json`
- `diagnostics/quality_full_representative_distribution_fix.md`
- `diagnostics/quality_full_local_retrieval_benchmark.json`
- `diagnostics/quality_full_local_retrieval_benchmark.md`

## Questions For The External Reviewer

1. Can any formal prediction process resolve, stat, hash, open, or deserialize an
   outcome artifact before every expected prediction seal and paired closure?
2. Does any hash-referenced artifact still have a verify-then-reopen or
   verified-payload-discard path that could change prediction or scoring input?
3. Can cache identity forgery reuse a semantically different shared context while
   preserving all checked provider checkpoints and transitive roots?
4. Do the full D-1 commitment and candidate-only prompt projection preserve the
   blind boundary without silently omitting a preliminary candidate?
5. Are market-universe, leader, Recall@K, precision, Brier/ECE, ineligible-row,
   citation, and offline-unexposed metrics defined without fabricating unavailable
   theme/newsless truth?
6. Does the running `QPRED-704f15cde6e4152b6931` ancestry preserve the formal
   blind boundary and checkpoint commitments through all six prediction seals?
7. Does the runtime-evidence pack graph prove complete assignment coverage,
   enforce prompt bounds without first-N/truncation, and reject plan/output/
   checkpoint commitment drift?

Do not approve V0/V1 quality, compiler v8 work, model selection, or production
activation from this checkpoint alone. Those require the real three-case score,
then CALIBRATION, compiler-v8/V2, HOLDOUT, and post-cutoff stages in order.
