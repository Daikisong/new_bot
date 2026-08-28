# Runtime Retrieval v4

## Mechanics Gate

- Gate A: `PASS`
- Snapshot: `MEMIDX-1e64a1b6e6ba7b07b799`
- Cases / clusters / traces: `12 / 12 / 12`
- Selected and LLM-exposed records: `1,376 / 1,376`
- Offline-unexposed recovered: `1,375`
- Future records / web calls / online full scans: `0 / 0 / 0`
- Integration order: `OPEN_WORLD_FIRST -> RUNTIME_RETRIEVAL_V4 -> SEMANTIC_QUERY_EXPANSION -> CANDIDATE_EXPANSION -> BLIND_INITIAL_PREDICTION -> FINAL_SYNTHESIS`
- Retrieval precedes candidate generation: `true`
- Memory used as a candidate gate: `false`

`rare_mechanism_recovered_count=1,375` is an audit-defined rare reasoning-payload proxy. It is not labeled rare-mechanism recall and must not be presented as such.

## Live OAuth Probe

The smallest sealed CALIBRATION case was `NSLAB-20260102-be50ec83` with 490 cutoff-safe news rows. The shared open-world stage used the real `codex-oauth / gpt-5.6-sol / xhigh` provider.

| Metric | Observed |
|---|---:|
| Completed open-world batches | 21 |
| Analyzed clusters | 252 |
| Covered current events | 257 |
| Live Codex calls | 21 |
| Elapsed | 5,798.275 s |
| Daily budget | 90.000 s |
| Budget multiple | 64.425x |
| Prompt token estimate | 1,763,142 |
| Completion token estimate | 82,533 |

The run was stopped after the daily latency gate had become mathematically impossible to recover. V0 did not complete and V1 was not started, so no paired performance claim is available. Continuing into per-cluster runtime evidence mapping would only increase the already-failed latency and call budget.

Formal shadow status is `HOLD_PARTIAL_LIVE_PROBE_EXCEEDED_DAILY_LATENCY_BUDGET`. Production remains `NOT_PRODUCTION_ACTIVATED`.
