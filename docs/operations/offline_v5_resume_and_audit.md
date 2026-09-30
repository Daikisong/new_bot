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

The authoritative BUILD-only evaluation plan was regenerated read-only at
`C:\Users\eorb9\projects\nslab_semantic_upgrade_v7_eval_v2\project\diagnostics\offline_brain_v2_build_only_full_plan_20260930_projection_audit.json`.
Its SHA-256 is `8362f4bac91a4734053e4fea2aa9cae18afb48556b5b7a17848f1d79c4ffa8db`.
It binds to `MEMIDX-4409624afdffd1d01018` and attested manifest SHA
`f47cac17eb3f97bf856e358c078eca023e12d6cefce6a044878dcd87ab4e4f41`, with
759,308 records and 49,385 semantic units. The 7,158-call figure (73
long-payload, 6,917 leaf, 168 proxy reduce/review) is a projection, not a lower
bound; the separate guaranteed full-build floor is 7,000 calls, including ten
mandatory reduce/review calls. Planning made zero LLM calls, the representative
payload truncation count is zero, and no package or production pointer was
created or changed.

The earlier file
`offline_brain_v2_build_only_full_plan_20260929_updated.json` is preserved but
superseded. Its stable source, geometry, representative-root, and call-count
fields match the regenerated plan, but it incorrectly marks estimated call
counts as lower bounds and omits the guaranteed minimum fields. Do not use its
bound flags or infer an exact remaining call count from either projection. The
original 7,147-call plan is also forensic comparison only.

The corrected plan took 915.59 seconds. Its Python process peaked at 9,693 MiB
private bytes (9.47 GiB) and 8,494 MiB working set (8.30 GiB); host available
RAM reached a low of 12.72 GiB. During the next cleanup interval private bytes
fell to 3,485 MiB (3.40 GiB), and after normal process exit host available RAM
was 22.58 GiB. The planner scratch files peaked at 291,585,012 bytes and were
removed by normal completion. This measures one evaluation-only planning run,
not the production V5 build or proof that all Python/native stages are leak-free.

During this run, C: free-space readings fluctuated without a measured planner
artifact explaining the full change. A single Windows `SearchIndexer` sample
(PID 15460) reported 123,586,425 bytes/sec written and 245,745,069 bytes/sec
read while the volume reported 2.89 GiB free; later samples returned to 30.21
GiB free and the process was idle. The Search service and settings were not
changed. Reading its ProgramData index directory returned `Access is denied`,
so the cause remains unknown. Defer another spill-heavy run if free space is
unstable; do not attribute these volume readings to Python memory use.

## Planner failure cleanup

A production-source `plan-offline` attempt on 2026-09-30 used the mock provider
and made zero LLM/OAuth calls. DuckDB failed to commit its planner WAL with a
disk-full error; no plan JSON was produced. The process peaked at about 5.18 GiB
private bytes and 4.40 GiB working set while host available RAM remained above
17 GiB, so the failure was disk pressure, not evidence of a Python memory leak.
The old success-only scratch cleanup left the failed plan's `.work` directory
behind. `plan()` now closes the DuckDB connection and removes that directory in
`finally`, including setup/assignment failures. A regression test injects an
assignment failure and asserts the scratch directory and output file are absent.
The run was not retried; a plan must be regenerated only after disk free space
is stable, and its result remains a projection rather than build completion.

The production `build()` path applies the same scratch rule to Python exceptions
or cancellation during DuckDB setup, semantic assignment, LLM map/reduce, and
influence-manifest generation: close the connection, remove only the compile's
`brain/.work/<compile_id>`, then re-raise. Content-addressed LLM checkpoints are
stored outside that directory and remain available for resume. A forced process
termination does not execute Python cleanup; after confirming the exact compiler
PID is gone, treat any remaining `.work` files as scratch, not completed state.

## Resume protocol

1. Confirm the OAuth retry window has passed and confirm that no process with
   this compile ID is already running.
2. Use an isolated clean worktree at tested compiler commit
   `7198b6b74bbd10f1cf2451ca399c0b63f14706a9` (merged to `main` as
   `afd8e8f951d3b0c097ae49054d85f0b5c90ce60f`). Do not use the existing
   `news_bot` checkout with tracked edits or `news_bot_next` with generated
   pytest artifacts. The exact revision and a completely clean worktree are
   checked below before Python imports or any provider call. Set and verify
   `PYTHONPATH` so the CLI comes from that worktree. The CLI also fails closed
   if the original LLM identity differs.

```powershell
$repo = "C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b"
$expectedCommit = "7198b6b74bbd10f1cf2451ca399c0b63f14706a9"
$actualCommit = (git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $expectedCommit) {
  throw "Unexpected compiler revision: $actualCommit"
}
$worktreeStatus = git -C $repo status --porcelain --untracked-files=all
if ($LASTEXITCODE -ne 0 -or -not [string]::IsNullOrWhiteSpace(($worktreeStatus -join "`n"))) {
  throw "Compiler worktree must be completely clean before build-offline."
}
Set-Location $repo
$env:PYTHONPATH = "$repo\src"
$expectedCli = Join-Path $repo "src\news_scalping_lab\cli.py"
$actualCli = python -c "import news_scalping_lab.cli as cli; print(cli.__file__)"
if ($actualCli.Trim() -ne $expectedCli) { throw "Unexpected CLI import: $actualCli" }
$env:NSLAB_LLM_PROVIDER = "codex-oauth"
$env:NSLAB_CODEX_MODEL = "gpt-5.6-sol"
$env:NSLAB_CODEX_REASONING_EFFORT = "xhigh"
```

3. Run the exact command below from that worktree. `--checkpoint-dir` must point
   to the original shared directory so successful content-addressed replies are
   found instead of reissued from the PR worktree's empty default cache. Do not
   change the source project, expected manifest hash, compiler version, model,
   reasoning effort, prompt schemas, or checkpoint identity.

```powershell
python -m news_scalping_lab.cli brain build-offline `
  --source-project "C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project" `
  --expected-manifest-sha256 "6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576" `
  --checkpoint-dir "C:\Users\eorb9\projects\news_bot\runs\checkpoints\llm"
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

The current evaluation-only geometry plan is
`OFFLINE-PLAN-f0fccb71afdb623e2609`, with 7,158 projected logical calls and a
separate 7,000-call guaranteed floor. It made zero LLM calls and did not create
a package or mutate a production pointer. It is only a plan for the separate
BUILD-only C package; it is not the production V5 build and is not an exact
remaining-call ETA or a reason to start a second full build.

Its payload exposure projection must also be reported precisely. The immutable
evaluation snapshot has 759,308 records and 49,385 semantic units. Full-population
embedding geometry is enabled; the planner selects 170,333 representative records
for complete source-payload reads, an exposure ratio of 22.4327%. The other
588,975 records (77.5673%) are not selected for direct raw-payload LLM input by
this plan, although their embeddings participate in full-population semantic
geometry and the compiler's population/assignment accounting. The selected
payloads total 212,838,762 characters; 143 long representatives are chunked, and
the projected representative truncation count is zero.

This plan used the mock provider and made zero model calls: the exposure fields
describe planned input selection, not completed GPT reading or semantic
influence. The 22.4327% is specific to the evaluation-only 759,308-record
snapshot; it must not be extrapolated to the separate 823,279-record production
build. It is also not directly comparable to the earlier 0.3265% audit of a
different compiler artifact and exposure definition. A built package must still
prove exact record assignment, leaf/capsule coverage, payload-ledger roots, and
the provenance of synthesized claims before any result is described as complete.
