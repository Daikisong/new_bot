# Current Daily LLM Call Graph

The authoritative production path is `ThinDailyAnalyzer.analyze()` through `nslab analyze-daily`.

```text
CSV parse and local clustering
-> load selected immutable BrainPackage
-> retrieve current-news-grounded capsules and claims locally
-> one final_market_decision call
```

Normal logical calls: 1. Maximum live invocations including one structured repair: 2. Historical raw-record remap, daily import, and daily brain rebuild: 0.

The earlier `daily_llm_call_graph_after.*` files describe the intermediate two-call implementation and are retained as historical evidence. The current machine-readable audit is `diagnostics/daily_llm_call_graph_single_call.json`.
