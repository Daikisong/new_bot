# Current Daily LLM Call Graph

The authoritative production path is `ThinDailyAnalyzer.analyze()` through `nslab analyze-daily`.
This contract was re-verified on 2026-09-30 at commit
`707093423d1906070ba2f8655e7ca8ff6ae4f180`.

```text
CSV parse and local clustering
-> load selected immutable BrainPackage
-> retrieve current-news-grounded capsules and claims locally
-> one final_market_decision call
```

Normal logical calls: 1. Maximum live invocations including one structured repair: 2.
Historical raw-record remap, daily import, daily brain rebuild, and blind web search: 0.
Historical exact witnesses are limited to 24.

The structured repair is inside `CodexOAuthProvider.generate_structured` and can run
only once after schema validation failure. It is a retry of the single decision
request, not a second interpretation or decision stage.

Current-news clustering and brain-query vectorization use the configured embedding
provider. These are separate from GPT decision-call counts; their call count, latency,
and cost must be reported separately. Brain retrieval reads precompiled package
indexes and does not invoke a generative LLM.

The earlier `daily_llm_call_graph_after.*` files describe the intermediate two-call implementation and are retained as historical evidence. The current machine-readable audit is `diagnostics/daily_llm_call_graph_single_call.json`.

Architecture evidence includes tests for brain-before-decision ordering, one normal
call, at most two live calls, independence from current input/record counts, and no
LLM call inside historical-record or memory loops. The focused 58-test suite,
Ruff, mypy (139 source files), and full pytest passed locally. GitHub quality-gate
`36652767437` passed all steps.

This proves the current code boundary, not a sealed V2 package or successful real-package
daily runtime. The package is unbuilt, predictive quality is unevaluated, and production
remains inactive. Missing BrainPackage still fails closed rather than falling back to
legacy `nslab analyze`.
