# Daily LLM Call Graph After PR-A (Historical, Superseded)

> This is a historical snapshot of an intermediate two-call implementation. It is not the current daily call graph.

The values in `daily_llm_call_graph_after.json` preserve what that intermediate
implementation did: two normal logical calls and up to four live invocations
when both calls needed a structured-output repair. Those values must not be
reported as the current product contract.

The authoritative current audit is
[`daily_llm_call_graph_single_call.json`](daily_llm_call_graph_single_call.json):
one normal brain-informed decision call, at most two live invocations including
one structured-output repair, and no preliminary interpretation call. The
compiled brain context is loaded before that decision call.

At the PR-A staging point, the offline V2 brain package had not yet been built
and the daily package reader remained fail-closed until its implementation
landed. Predictive quality was not evaluated and production was not activated.
