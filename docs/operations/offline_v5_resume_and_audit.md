# Offline V5 Resume and Audit Runbook

This runbook records the safe continuation boundary for the one-time production
brain build. It is intentionally separate from the daily inference path.

## Historical stopped snapshot (2026-09-29)

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

## Current resume status (2026-10-01)

The fixed OAuth reset timestamp was removed from the guarded launcher. It no
longer refuses to start based on a locally hard-coded quota date. The launcher
uses the configured Codex CLI/provider session and leaves account selection or
rotation to that toolchain; it does not inspect credential files or implement
local account switching. The provider response is authoritative. If the
provider ultimately returns a usage-limit error, preserve its failed checkpoint
and stop rather than retrying the same request indefinitely.

The launcher still validates the pinned compiler, source manifest, shared
checkpoint inventory, and build identity, and retains the 4-core affinity and
host-memory safeguards. Those checks protect reproducibility and machine
stability; they are not quota gates. A checkpoint sentinel is an expected
content-addressed checkpoint used to confirm the cache, not a quota lock.

The resumed build uses the pinned V5 identity and shared checkpoint directory.
At `2026-10-01T00:39:51Z`, PID `53340` was in `semantic_assignments` at
`346,661 / 823,279` records and 18,690 semantic units, with 4.96 GiB private
memory, 15.13 GiB host RAM available, and 372.70 GiB free on C:. The external
resource log is
`C:\Users\eorb9\projects\news_bot_trash\20260930_nslab_resource_guard\resource_logs\offline_v5_20261001T003407Z.jsonl`.
No model call had yet been observed in that sample. Live status must be read
from that log and the active launcher session; local record geometry alone
does not establish synthesis completion. Successful content-addressed
checkpoints remain reusable on a later resume.

A second launcher invocation currently exits because the active-build receipt
already exists. This is duplicate-process protection, not a quota check; do
not remove the receipt or start another compiler while PID `53340` is active.

## 2026-10-01T02:13Z latest resume interruption

The 00:39 resource sample above is historical. The resumed build started at
`2026-10-01T00:34:07Z` under root PID `53340`, using the pinned V5 identity and
the shared checkpoint directory. Twelve `offline_semantic_reduce` checkpoints
completed successfully through `2026-10-01T02:13:22Z`:

| UTC | Checkpoint | Result |
| --- | --- | --- |
| 01:12:02 | `LLMCKPT-1d6d8295e6996522` | Prior quota error replaced by `ok` |
| 01:20:26 | `LLMCKPT-16d89321be617337` | `ok` |
| 01:20:59 | `LLMCKPT-dcca5af150560830` | `ok` |
| 01:26:43 | `LLMCKPT-f9eb115cb3f1f4c3` | `ok` |
| 01:34:49 | `LLMCKPT-484083a463d97c02` | `ok` |
| 01:35:26 | `LLMCKPT-e969447ff262f16d` | `ok` |
| 01:47:46 | `LLMCKPT-0836fd9c229e39df` | `ok` |
| 01:49:26 | `LLMCKPT-9bfc76f498b1432d` | `ok` |
| 01:49:53 | `LLMCKPT-038f047f11f9ffe8` | `ok` |
| 02:03:03 | `LLMCKPT-1a1684fac2e659d0` | `ok` |
| 02:13:12 | `LLMCKPT-9fba79a209592f3c` | `ok` |
| 02:13:22 | `LLMCKPT-8623ef330658d3b2` | `ok` |

The run stopped because PID `49596` briefly had incomplete process identity
metadata during descendant-affinity refresh; the monitor treated that child
race as fatal and stopped the compiler. This was not a quota response, memory
threshold, or disk threshold. After the root exited, six exact descendants
were checked by PID, creation time, executable, command line, and parent
relationship; none belonged to the protected Bithumb tree. They were stopped
leaf-first. The compiler tree is absent, and shared checkpoints and compile
scratch were preserved.

The runner correction skips affinity only for a child that exits or changes
identity during verification, with a warning. Compiler-root identity and its
required 4-core affinity remain fail-closed; unverified processes remain
untouched. The active-build receipt at
`C:\Users\eorb9\projects\news_bot_trash\20260930_nslab_resource_guard\resource_logs\active_offline_v5_build.json`
still blocks another launch despite the verified-absent process tree. Receipt
removal was rejected by the execution tool (`rejected: blocked by policy`) and
was not bypassed or altered. Do not restart until an allowed receipt
reconciliation path is available. The V5 package remains incomplete and
production remains inactive.

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

1. Use the currently configured Codex CLI session and confirm that no process
   with this compile ID is already running. The launcher must not infer quota
   availability from a hard-coded reset timestamp; the configured CLI/provider
   response is authoritative. If it returns a usage-limit error, preserve the
   failed checkpoint and stop the run.
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

## 2026-09-30 Production-source guarded planner projection

A fresh production-source `plan-offline` completed from a clean sparse worktree
at `origin/main` commit `b64b02b2c6e5976f5756234285ffb7aa4f029993`. The CLI was
verified to import from that worktree. The source was the immutable production
staging project and the externally attested actual manifest SHA was checked
before launch; the planner revalidated and recorded that same SHA:

```text
snapshot                    MEMIDX-1e64a1b6e6ba7b07b799
actual manifest SHA-256     6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576
record corpus root          2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75
plan ID                     OFFLINE-PLAN-149450301220655b94fe
plan artifact               diagnostics/offline_brain_v2_production_plan_20260930_guarded_projection.json
plan artifact SHA-256       5cfbd40e5f14fd4f37c455a35ff7182e132657aaadc4943209485b01ae26be7f
records / semantic units   823,279 / 52,644
provider / planning calls  mock / 0
import / embedding reuse   true / true
production activated       false
```

The plan projects 7,683 logical calls, with a separate guaranteed minimum of
7,522. The projected count is not a remaining-call count or ETA: it includes
171 proxy reduce/review calls, while actual reduce topology depends on
model-generated capsule IDs. The guaranteed floor consists of the exact map
count plus mandatory category reviews and the world root.

Full-population geometry selected 181,978 of 823,279 records (22.1040%) for
complete source-payload reads; the other 641,301 were not selected as direct raw
payload input by this plan. The selected payload total is 231,041,529
characters; 203 long representatives are chunked and planned truncation is
zero. These are plan fields, not completed GPT exposure or proof of semantic
influence. Package payload ledgers and claim provenance remain the evidence
gate.

This replan intentionally ran with BLAS/OpenMP/NumExpr thread-pool environment
limits set to two. Its wall time was 1,149.12 seconds. Resource samples were
approximately one minute apart: observed private memory reached at least 7.32
GiB at the representative/distribution phase transition; the lowest sampled
host available RAM was 13.21 GiB, and it was 21.06 GiB after normal process
exit. The process exited successfully, the compile work directory was removed,
and no compiler process remained. These are sampled observations, not an exact
private-memory high-water mark or proof of leak freedom. Progress advanced
through the full 823,279-record assignment; no long no-progress/high-CPU period
was observed.

Do not treat this thread-limited plan as an identity change or a fixed build
topology. The required pinned `7198b6b` build command and content-addressed
checkpoint directory remain unchanged; the eventual package and actual
checkpoint reuse decide what completes. The earlier tracked
`diagnostics/offline_brain_v2_full_plan.json` has the same plan ID and source
roots but a different representative-read root and projection (181,979 payload
reads / 7,671 estimated calls versus 181,978 / 7,683 here). A plan ID is not a
content hash, and neither projection proves the build's exact runtime topology.
Preserve the earlier artifact for comparison; do not subtract either plan from
the 7,902 successful checkpoints to estimate remaining work.

The source manifest pointer still carries its legacy SHA
`fc0d847d4eb0db688cf19570a3519d357a93558463797e26321a106d60040804`; the actual
manifest remained the externally attested `6c05...4576`, supplied explicitly
to the planner. Import, embeddings, source pointer, package output, and
production activation were not modified. The quota-reset statement above was
the assessment at the time of this historical planner audit; the fixed-date
launcher gate has since been removed as recorded in Current resume status.
Production remains HOLD until the build package and required evaluations pass.

## 2026-09-30 Production V5 CPU and memory guard

A read-only audit of the pinned build source and immutable project config found:

- `configs/default.yaml` sets `max_concurrency: 4`. The compiler's semaphore and batch worker counts use the effective value, but `NSLAB_MAX_CONCURRENCY` can override it; verify that the effective value is exactly 4 before resume. This bounds LLM concurrency, not CPU cores.
- Compiler commit `7198b6b` pins DuckDB `1.5.4`. Its connection setup sets `memory_limit=8GB` and a per-workdir spill directory, but does not set DuckDB `threads`. The local Python/DuckDB probe reported the default as 32 threads on this 32-logical-processor host.
- DuckDB documents `threads` as the parallel-query limit. Its `memory_limit` applies to the buffer manager and is not a hard process-RSS ceiling. See [DuckDB resource pragmas](https://duckdb.org/docs/lts/configuration/pragmas) and [DuckDB OOM guidance](https://duckdb.org/docs/lts/guides/performance/oom).

Do not patch the pinned compiler, change prompt/model/source identity, or invalidate successful checkpoints to add a thread setting mid-resume. For the next Windows build launch, apply processor affinity mask `0xF` (logical processors 0-3) to the exact, command-line-verified compiler PID before its source-assignment phase. This limits that PID to four logical processors without changing its compiler commit or checkpoint namespace; exact prompt/input hashes remain the cache-reuse authority. An isolated `timeout.exe` smoke test successfully set and read back this mask; no project process was changed. Verify the actual compiler mask and inspect its process descendants before model calls begin. Apply affinity to a descendant only after resolving its absolute executable and command line and confirming it is not under the protected Bithumb root.

Keep the 10-second resource samples scoped to the verified compiler and its verified build descendants. Use aggregate `BuildTreePrivateBytes` for the 8 GiB warning and same-phase growth signal; record root `RootPrivateBytes` and `RootWorkingSetBytes` separately for diagnosis. Also record aggregate working set, CPU time/affinity, host available RAM, and pagefile usage. A tree aggregate at or above 8 GiB is a warning, not a claimed leak or hard RSS cap. If available RAM remains below 6 GiB for 60 seconds, first preserve any completed checkpoint and stop only the identified compiler tree, then inspect the phase and workdir. Do not terminate unrelated Python, MCP, Posting, indexing, or trading processes; do not use `gc.collect()` as proof of release. The compiler PID is limited to four logical processors; verify descendant affinity separately. CPU load is observed rather than treated as a timeout for a valid LLM call.

### Guarded Windows resume launcher

`scripts/guarded_offline_v5_resume.ps1` encodes the pinned resume identity and
resource guard above. Its default mode is a no-build preflight: it verifies the
clean `7198b6b` compiler worktree, resolves the source pointer inside the
immutable project, hashes the actual manifest against the attested SHA, checks
the shared checkpoint sentinel/count, imports the CLI from the pinned worktree,
and confirms the effective provider/model/reasoning/concurrency. It does not
read credential files or make an OAuth/model call. `-StartBuild` is required to
launch anything; quota availability is not inferred from a fixed reset time.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\guarded_offline_v5_resume.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\guarded_offline_v5_resume.ps1 -StartBuild
```

At launch it rechecks available RAM, starts the exact V5 command with the
original source, actual manifest SHA, and shared checkpoint directory, then
verifies the process command line before applying affinity
mask `0xF`. It reapplies the mask to descendants only after resolving their
absolute executable/command line and excluding the protected Bithumb tree.
Every 10 seconds it writes a JSONL sample outside the repository under
`news_bot_trash\20260930_nslab_resource_guard\resource_logs`: compiler/tree
private bytes and working set, CPU time and affinity, host available RAM,
pagefile use, and C: free space. Eight GiB private bytes and six consecutive
increasing samples above that level raise warnings; those samples are evidence
to inspect, not by themselves proof of a leak. If available RAM stays below 6
GiB for 60 seconds, it stops only the re-verified compiler descendants and
root, preserving the shared checkpoints. No other process is managed.

Historical note: the launcher was initially added while the OAuth quota window
was believed unavailable. The early-start refusal described in the original
validation below was tied to the now-removed hard-coded reset timestamp; it is
not current launcher behavior. Do not substitute a mock identity for the
configured production provider.

On 2026-09-30, the launcher passed PowerShell parser validation and its
preflight without starting a build or making an OAuth/model call. It verified
the exact compiler/source/manifest/checkpoint identity above, effective
`codex-oauth/gpt-5.6-sol/xhigh`, concurrency `4`, and Python `3.14.2` from the
pinned worktree. The observed checkpoint inventory was 7,965 JSON files / 300,054,677
bytes. At that time, invoking `-StartBuild` before the configured reset was
verified to stop at the then-present quota guard before process creation. That
historical check did not advance semantic synthesis or change checkpoint state;
its start refusal was removed on 2026-10-01.

### Shared checkpoint integrity audit

At 2026-09-30 22:40 KST, a read-only streaming audit parsed all 7,965 shared
`LLMCKPT-*.json` files without emitting prompt or output bodies and found no
JSON, schema, ID, input-hash, output-hash, or content-address mismatches. For
each file it recomputed the V5-compatible checkpoint ID from operation,
purpose, input, model config, and metadata using the pinned compiler's
`stable_id`/`canonical_json` implementation; the result matched both the file
stem and embedded `checkpoint_id`.

The store contains exactly 7,902 successful V5 OAuth checkpoints
(`gpt-5.6-sol/xhigh`), one V5 OAuth error checkpoint
(`LLMCKPT-1d6d8295e6996522.json`, the quota failure), 11 successful V4
checkpoints, and 51 successful deterministic-mock checkpoints. Thus the
previously recorded 7,902 successful V5 count is confirmed; the other 62 files
are not counted as V5 successes. V5 cache identity includes the compiler
version, provider, model, reasoning effort, and exact request metadata, so the
V4 and mock files do not collide with V5 requests. The failed quota checkpoint
has `status=error` and is not a reusable success. This confirms existing cache
integrity, not the number of uncached future nodes or an ETA. No checkpoint was
modified, removed, copied, or reissued during that audit. The subsequent resume
state is recorded in Current resume status above.

## Progress and resource guard behavior

The guarded launcher reads the pinned compiler's `brain/.work/OFFLINE-COMPILE-*/progress.json` only when its file timestamp is no earlier than the verified compiler process start. It accepts the expected progress schema and sane field ranges; missing, partial, malformed, or ambiguous progress telemetry is logged as unavailable and does not stop the build. The file is not modified.

Progress is intentionally coarse. `semantic_assignments` updates the record count while assignments are built. Once that phase finishes, the compiler writes `representative_and_distribution_build` with all source records processed, then performs payload planning, leaf LLM maps, reductions, and package finalization without more progress-file updates. Therefore `records=823279/823279` means assignment geometry completed; it does not mean the LLM synthesis or package is complete. The broad phase label gives context, not a reliable remaining-time estimate.

The launcher sets and rechecks affinity mask `0xF` for the verified compiler and resolvable, identity-checked descendants, limiting those processes to logical CPUs 0-3. `max_concurrency=4` separately bounds compiler LLM concurrency. It does not impose a timeout on a valid provider request or declare high CPU use a failure. Aggregate `BuildTreePrivateBytes` at/above 8 GiB, or six consecutive increasing 10-second aggregate samples above that threshold within one reported phase, produce warnings only; they are not a leak diagnosis and do not trigger automatic termination. Root `RootPrivateBytes` and `RootWorkingSetBytes` remain separate diagnostic metrics. The JSONL log records phase and available record/unit counters next to process-tree and host-resource samples.

Automatic resource stop is narrower: if host available RAM stays below 6 GiB for 60 seconds, the launcher revalidates the exact pinned build root and descendants, force-stops only that compiler tree, and exits with code 2. The shared content-addressed checkpoints remain untouched. A forced stop does not run Python cleanup, so it may leave an incomplete `brain/.work/<compile_id>` scratch directory; it is not a completed package and must not be reused as one. Preserve it for inspection, verify the exact compiler tree is gone, and only remove that compile's scratch after confirming no build process still owns it. Do not remove or rewrite checkpoint JSON files.

An operator can request a controlled stop without guessing a PID:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\guarded_offline_v5_resume.ps1 -StopBuild
```

At launch, the runner writes an external `active_offline_v5_build.json` receipt binding a random run ID, pinned compiler root/commit, source/manifest/checkpoint identity, Python executable, exact command line, root PID and creation time, and parent PID. `-StopBuild` reads only this receipt; it never discovers a process by shared CLI arguments. It rechecks the pinned checkout commit and every recorded root identity before stopping the process tree. A missing, malformed, stale, or mismatched receipt fails closed without process control. Ambiguous descendants are left untouched; a process under the protected Bithumb project is never controlled. The receipt is removed only after the recorded root and all resolvable descendants are gone. If verification is incomplete, the receipt remains and blocks another build until the process tree is inspected. Shared checkpoints are preserved; forced stop may leave compiler scratch for inspection. Use the default no-build preflight to check readiness; neither preflight nor stop mode makes an OAuth call.

The pre-resume validation boundary above was historical and included the now-removed reset-time refusal. Current validation should cover PowerShell parsing, read-only preflight, process-identity checks, and `-StopBuild` failing closed when no valid launcher receipt exists. Do not launch a mock/alternate compiler to exercise the runtime guard.
