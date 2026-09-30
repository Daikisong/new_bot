# Offline V5 Resume and Audit Runbook

This runbook records the safe continuation boundary for the one-time production
brain build. It is intentionally separate from the daily inference path.

## Authoritative stopped snapshot

Snapshot date: 2026-09-29 20:09 KST

| Item | Value |
| --- | --- |
| Compile ID | `OFFLINE-COMPILE-add3461bd175147e255d` |
| Compiler | `nslab.offline_semantic_brain.compiler.v5` |
| Provider/model | `codex-oauth / gpt-5.6-sol / xhigh` |
| Source records | `823,279` |
| Semantic units | `52,644` |
| Successful calls | `7,902` |
| Successful call stages | `90` long-payload maps, `7,423` leaf maps, `383` reductions, `6` category reviews |
| Old planner projection | `8,018` (not a guaranteed bound) |
| Guaranteed full-build logical-call floor | `7,523` |
| Known mandatory nodes not yet successful | at least `5` |
| Package | not sealed |
| Production pointer | not activated |
| Stop reason | Codex OAuth usage limit |
| Earliest reported retry | `2026-10-04 03:31 KST` |

The previous `116`-remaining claim is withdrawn. It subtracted successful calls
from `8,018`, a planner projection based on semantic-unit hash buckets. Runtime
leaf buckets use model-derived capsule IDs, so the proxy can produce either more
or fewer buckets than runtime. The guaranteed `7,523` full-build floor is the
known map-call count plus one category review per non-empty category and one
world root call; it is already below the `7,902` successful checkpoints and
cannot estimate remaining work. At least five logical nodes remain unresolved:
the quota-failed reduce, three category reviews, and the world root. Additional
reduce calls depend on completed capsule and reduce outputs, so an exact
remaining call count is not currently established. The `progress.json` value
`record_progress_ratio=1.0` means only that local record geometry finished; it
does not mean semantic synthesis finished.

## Planner estimate semantics

The zero-LLM planner builds deterministic coverage-only leaf proxies and runs
the same child-count and canonical-JSON byte packer used by runtime reductions.
However, planned buckets hash semantic-unit IDs while runtime buckets hash
model-derived capsule IDs. The `estimated_reduce_review_call_count` and
`estimated_total_logical_llm_call_count` are therefore projections, not lower
bounds; they may be higher or lower than runtime. The plan reports a separate
guaranteed full-build floor from exact map-call counts, mandatory category
reviews, and the world root. If a proxy level cannot make progress because its
coverage alone exceeds the byte budget, the planner stops that simulation rather
than looping. Dynamic reduce progress and final call count must be read from
actual content-addressed build outputs.

## Memory-bounded planning

Full-population geometry and representative selection are local work, but their
DuckDB joins and NumPy clustering can allocate native memory independently of
Python garbage collection. Offline plan/build connections therefore use an
explicit `8GB` DuckDB memory limit and a compile-work-directory spill target.
Recursive clustering releases parent advanced-indexing arrays before descending.
This preserves the geometry and split predicates while preventing a large
stratum from retaining every intermediate dense matrix. A watchdog may stop an
evaluation-only plan above `12GB` private memory; an incomplete plan is never
treated as evidence and can be regenerated from the immutable source.

The corrected BUILD-only evaluation plan was regenerated read-only at
`C:\Users\eorb9\projects\nslab_semantic_upgrade_v7_eval_v2\project\diagnostics\offline_brain_v2_build_only_full_plan_20260929_updated.json`.
It binds to `MEMIDX-4409624afdffd1d01018`, contains 759,308 records and 49,385
semantic units, projects 7,158 logical calls (73 long-payload, 6,917 leaf, 168
proxy reduce/review), and made zero LLM calls. This estimate is not a lower
bound because its leaf buckets are proxies. The run peaked below
the watchdog threshold and returned memory to the host after the Python child
exited. The earlier 7,147-call plan remains preserved for comparison; the new
plan is authoritative for future evaluation build planning.

## Resume protocol

1. Confirm the OAuth retry window has passed and confirm that no process with
   this compile ID is already running.
2. Pin the original LLM identity in the shell. The CLI now fails closed if any
   of these values differ, rather than silently creating a new mock-provider
   compile identity.

```powershell
$env:NSLAB_LLM_PROVIDER = "codex-oauth"
$env:NSLAB_CODEX_MODEL = "gpt-5.6-sol"
$env:NSLAB_CODEX_REASONING_EFFORT = "xhigh"
```

3. Run the exact command below from the repository environment. Do not change
   the source project, expected manifest hash, compiler version, model,
   reasoning effort, prompt schemas, or checkpoint identity.

```powershell
python -m news_scalping_lab.cli brain build-offline `
  --source-project "C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project" `
  --expected-manifest-sha256 "6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576"
```

4. Verify that content-addressed successful checkpoints are reused. A resumed
   run must not reissue successful map or reduce nodes.
5. Treat any failed or identity-mismatched node as a stop condition. Do not
   bypass a provider limit or alter the prompt to make a failed node pass.
6. After the immutable package is written, run package closure, deep, and
   read-only parity audits before considering it eligible for evaluation.

## Post-build gates

The production V5 package and the evaluation C package are different artifacts.
The production V5 package uses the full 823,279-record source and must never be
used as the C arm. The C arm must be built separately from the pre-registered
evaluation-only snapshot (`MEMIDX-4409624afdffd1d01018`, BUILD population
759,308 records), and its package manifest must bind exactly to that snapshot.
The package must prove all of the following before the C arm is allowed:

- record, capsule, claim, assignment, and warehouse roots are internally
  consistent;
- every record is assigned exactly once and every reduce node has exact ordered
  child identity;
- the representative payload ledger has no truncation and matches its root;
- source records belong to the pre-registered BUILD split, with zero pairwise
  overlap against CALIBRATION and HOLDOUT;
- read-only audit leaves all package roots unchanged;
- `analyze-daily` loads the package before its single normal LLM call, with no
  daily import, rebuild, historical raw map, or web call.

Only after the separate evaluation C package passes those gates may the
physically separate A/B/C blind predictions be sealed and scored against
outcomes. Production activation remains a separate explicit step and is not
implied by either a successful production build or an evaluation build.

## Current evaluation-only plan

The evaluation-only geometry plan is
`OFFLINE-PLAN-f0fccb71afdb623e2609`, with an estimated `7,147` logical calls.
It made zero LLM calls and did not create a package or mutate a production
pointer. It must not be mistaken for the production V5 build or used as a
reason to restart a second full build.
