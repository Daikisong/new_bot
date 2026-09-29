# Offline Brain + Thin Daily Architecture

## Product Boundary

Research repair, import, record embedding, semantic interpretation, contradiction review, and world-model synthesis are one-time offline work. A daily 08:00 CSV does not trigger any of them.

The daily product loads an immutable `BrainPackage` and current news before its single model request. That request interprets the news with the compiled knowledge and returns the final decision.

```text
CSV
  -> local parse / cutoff / clustering / CurrentEventCapsule
  -> load compiled world/category guidance
  -> local BrainPackage retrieval grounded in current news
  -> DailyBrainContext
  -> ONE CALL final_market_decision (interpretation + decision)
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

The call graph has exactly one logical call. `settings.llm.max_retries` may be 0 or 1; outer trace retry is fixed at 0. With the production Codex provider, this yields one normal call and at most one additional structured-output repair. There is no preliminary interpretation request.

## CurrentEventCapsule

Every cutoff-safe material cluster becomes one local capsule. The full artifact retains source row IDs, event/source IDs, representative title, predicate-bearing exact sentences, issuer/ticker/counterparty/numeric/modality literals, publication times, duplicate counts, and conflict flags.

Member bodies are not concatenated into prompts. If all full capsules exceed the byte budget, a deterministic local projection first reduces optional fields and finally uses an identity projection. Every material cluster ID remains present or the run fails; no silent truncation is allowed.

Every input CSV row also receives an explicit disposition in a separate ledger, including cutoff-window exclusions and audit-only rows.

## DailyBrainContext

Architecture v2 loads this context before any daily LLM call. Retrieval queries
come directly from event titles, predicate sentences, issuer/counterparty,
numbers, and modality. No model-generated interpretation controls initial recall.
The same request performs interpretation and final review, keeping the daily
witness set bounded. All stored world/category Markdown guidance is included with its
package-relative path and SHA-256. It is evidence, never a candidate allowlist.

The analyzer rejects missing, changed, future, or wrong-news initial context
before invoking the model. The run identity includes the package root, D-1
context, and model configuration. Daily prompt/checkpoint identities change;
offline compiler v5 and its existing checkpoints remain compatible.

The single structured response contains `analyzed_cluster_ids` and the final
prediction. Missing, duplicate, or added cluster IDs fail validation without
launching another analysis call. The decision envelope is saved and hashed in
the run manifest. This accounts for claimed coverage, not proven semantic quality.

The local brain reader may return only precompiled objects:

```text
SemanticMemoryCapsule
SynthesizedMechanismClaim
compiled world/category guidance
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

The current call-graph authority and historical evidence are recorded in:

```text
diagnostics/daily_llm_call_graph_before.json
diagnostics/daily_llm_call_graph_before.md
diagnostics/daily_llm_call_graph_single_call.json
```

The earlier `after` files preserve an intermediate two-call implementation
and are historical only; they are superseded by the one-call audit above.
They remain available for provenance:

```text
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
