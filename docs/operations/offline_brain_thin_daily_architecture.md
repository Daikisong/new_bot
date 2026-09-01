# Offline Brain + Thin Daily Architecture

## Product Boundary

Research repair, import, record embedding, semantic interpretation, contradiction review, and world-model synthesis are one-time offline work. A daily 08:00 CSV does not trigger any of them.

The daily product consumes an immutable `BrainPackage`, interprets current news once, performs bounded local retrieval, and makes the final market decision once.

```text
CSV
  -> local parse / cutoff / clustering / CurrentEventCapsule
  -> CALL 1 current_day_interpretation
  -> local BrainPackage retrieval
  -> DailyBrainContext
  -> CALL 2 final_market_decision
  -> sealed prediction / report / manifest
```

## PR-A Implementation

Production command:

```powershell
python -m news_scalping_lab.cli analyze-daily `
  --news <csv> `
  --trade-date YYYY-MM-DD `
  --cutoff YYYY-MM-DDTHH:MM:SS+09:00 `
  --d-minus-one-context <optional-cutoff-safe-json>
```

Implementation:

```text
src/news_scalping_lab/contracts/offline_brain.py
src/news_scalping_lab/inference/thin_daily.py
```

The call graph has exactly two logical calls. `settings.llm.max_retries` may be 0 or 1; outer trace retry is fixed at 0. With the production Codex provider, this yields two normal calls and no more than four live calls including one structured-output repair per logical call.

## CurrentEventCapsule

Every cutoff-safe material cluster becomes one local capsule. The full artifact retains source row IDs, event/source IDs, representative title, predicate-bearing exact sentences, issuer/ticker/counterparty/numeric/modality literals, publication times, duplicate counts, and conflict flags.

Member bodies are not concatenated into prompts. If all full capsules exceed the byte budget, a deterministic local projection first reduces optional fields and finally uses an identity projection. Every material cluster ID remains present or the run fails; no silent truncation is allowed.

Every input CSV row also receives an explicit disposition in a separate ledger, including cutoff-window exclusions and audit-only rows.

## DailyBrainContext

The local brain reader may return only precompiled objects:

```text
SemanticMemoryCapsule
SynthesizedMechanismClaim
population statistics
beneficiary / leader / continuation memory
current-vs-history differences
unresolved contradictions
at most 24 exact witnesses
```

The analyzer independently checks:

```text
future capsule / claim / witness count = 0
online full corpus scan count = 0
claim -> selected capsule closure
claim -> selected record closure
prediction -> event / row / capsule / claim / population / record closure
BLIND web count = 0
daily import count = 0
daily brain rebuild count = 0
historical raw daily map count = 0
```

## Legacy Boundary

`nslab analyze` and `DailyAnalyzer.analyze()` are now labeled `LEGACY_EXHAUSTIVE_DIAGNOSTIC_ONLY`. `build_runtime_evidence_memos()` and `build_runtime_evidence_memos_packed()` remain available only for forensic/offline diagnostics. They are unreachable from `analyze-daily`.

The before/after evidence is recorded in:

```text
diagnostics/daily_llm_call_graph_before.json
diagnostics/daily_llm_call_graph_before.md
diagnostics/daily_llm_call_graph_after.json
diagnostics/daily_llm_call_graph_after.md
```

## Activation State

PR-A establishes the product boundary and contracts. It does not claim that the brain is built or quality is proven.

```text
DAILY_PRODUCT_PATH_IMPLEMENTED = true
DAILY_CALL_GRAPH_BOUNDED = true
HISTORICAL_RAW_DAILY_REMAP_ZERO = true
OFFLINE_BRAIN_BUILT = false
BRAIN_MEMORY_ACTUALLY_USED = fixture-tested only
PREDICTIVE_QUALITY_EVALUATED = false
PRODUCTION_ACTIVATED = false
```

The default provider fails closed until PR-B/PR-C build and select the immutable V2 package and implement its bounded DuckDB/ANN reader. Falling back to the legacy heavy path is forbidden.
