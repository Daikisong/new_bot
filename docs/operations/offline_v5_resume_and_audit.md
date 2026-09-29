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
| Minimum logical-call floor | `8,018` |
| Minimum remaining calls | `116` |
| Package | not sealed |
| Production pointer | not activated |
| Stop reason | Codex OAuth usage limit |
| Earliest reported retry | `2026-10-04 03:31 KST` |

The `116` value is a lower bound, not an exact final count. Runtime reduction
prompts can split further at the byte limit, so the final total can be higher.
The `progress.json` value `record_progress_ratio=1.0` means only that local
record geometry finished; it does not mean semantic synthesis finished.

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
