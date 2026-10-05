# Goal: 일회성 두뇌 컴파일 완료 및 장전 CSV 판단 검증

문서 갱신: 2026-10-05 18:15 KST

## 목표

이미 import·repair·embedding·map까지 진행된 연구 원료를 처음부터 다시 처리하지 않는다. 기존 고정 빌드 `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 남은 작업을 이어서 닫고, 결과를 검증된 Offline Semantic Brain V2 패키지로 보존한다. 이어서 실제 장전 뉴스 CSV를 production `analyze-daily` 경로에 넣어, 이미 만든 두뇌와 당일 뉴스가 첫 GPT 판단 요청부터 함께 사용되는지 검증한다.

제품 목표는 사용자가 장전 CSV를 넣으면 cutoff-safe 두뇌 지식과 뉴스에 기반해 주도 섹터·종목 후보, 근거, 불확실성, 출처가 포함된 판단을 받는 것이다. 이 두뇌는 GPT 가중치를 새로 학습한 모델이 아니라, 기존 GPT 추론기가 매일 사용할 수 있도록 한 번 컴파일해 보존하는 지식 패키지와 검색 인덱스다. 완성 뒤 매일 연구 원문 전체를 재해석하거나 모든 record에 LLM을 호출하지 않는다.

## 진행 수치의 뜻

| 수치 | 현재 값 | 정확한 의미 |
|---|---:|---|
| 원료 record | 823,279 | 고정 source manifest의 구조적 입력 회계 수. 각 record를 GPT가 직접 읽었다는 뜻은 아님 |
| semantic unit | 52,644 | 컴파일 입력을 묶은 단위 수. 연도, 요약, LLM 호출 수가 아님 |
| 고정 model-task DAG | 1,868 | 이번 compile plan에 이미 정해진 유한 작업 노드 수. reducer 1,858, category review 9, world root 1 |
| 최신 완료 노드 | 1,005 / 1,868 | 2026-10-05 17:36:16 KST progress ledger 기준, fixed-DAG closure 53.80% |
| 최신 잔여 노드 | 863 | 1,868 - 1,005. 이 goal에서 합성 진행률로 사용할 분모는 1,868뿐 |

`823,279` 또는 `52,644`를 완료율 분모로 바꾸거나, 이를 보고 “10년치 의미를 모두 GPT가 읽었다”고 말하지 않는다. 입력 record coverage, 날짜·연도·거래일 coverage, LLM payload exposure, claim citation coverage, DAG closure는 각각 별도 지표로 검증하고 보고한다. 현재 기록된 원료 날짜 범위 `2018-01-03`~`2026-06-19`는 약 8년 반의 달력 범위다. 10년 전체 또는 모든 거래일을 채웠다는 주장은 audit 증거 없이 하지 않는다.

진행률은 항상 `closed / 1,868`, 남은 노드 수, provider-fresh 성공, 정확한 checkpoint 재사용, local carry/validation, failed, in-flight를 나눠 보고한다. record 처리 수는 DAG 완료 수가 아니다. 처리량 측정이 없거나 작업 비용이 크게 다르면 ETA를 만들지 않는다. 작업 시간을 완료율로 환산하거나 기준을 바꾸지 않는다.

## 고정 입력 및 산출 identity

- Source project: `C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project`
- Source manifest SHA-256: `6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576`
- Record root: `2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75`
- Memory snapshot: `MEMIDX-1e64a1b6e6ba7b07b799`
- Compile ID: `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`
- Fixed plan: 1,868 model-task nodes
- Compiler worktree: `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b`
- Target DB: `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b\brain\.work\OFFLINE-COMPILE-0dd9198ac9ef79215ab1\semantic_capsule_index.duckdb`
- Progress ledger: same compile work root's `progress.json`
- Checkpoints: `C:\Users\eorb9\projects\news_bot\runs\checkpoints\llm`
- Requested fresh synthesis identity: Codex CLI OAuth `gpt-6.1-sol/high`
- Reusable older checkpoint identity: only exact compatible `gpt-5.6-sol/xhigh` outputs already recorded in the checkpoint lineage. Report each trace's actual provider/model; do not describe reused 5.6 output as a new 6.1 response.

Build cutoff inherited from the current memory snapshot is `2026-08-21T18:52:07.302105+09:00`. The default daily context provider rejects a package that is later than the inference cutoff. Do not switch on point-in-time projection just to make an older CSV pass: that mode omits compiled guidance and is not the intended full-brain smoke.

## 현재 handoff 상태

Latest compile snapshot is `2026-10-05T17:36:16.975546+09:00`; the executor subsequently exited with code 1. At 18:15 KST, a new process scan found no matching writer and the target work root had no WAL:

- Python PID `20864`, created `2026-10-05 15:58:49 KST`
- The existing `news_scalping_lab.cli brain build-offline ... --continue-after-map-plan` invocation terminated. Executor session `48993` returned exit code 1.
- Latest safe ledger update: `2026-10-05T17:36:16.975546+09:00`
- Phase `offline_reduce`; `1,005/1,868` closed; `863` remaining; current node `REDUCE-f7c895812e8e1be3e57c`.
- Latest resource sample: private memory `3,942,232,064` bytes (about 3.67 GiB), working set `194,793,472` bytes (about 0.18 GiB), cumulative CPU `234.30` seconds.
- At the snapshot, Python PID `20864` was under wrapper PowerShell PID `74512`, with `NSLAB_MAX_CONCURRENCY=4`, Codex OAuth `gpt-6.1-sol/high`, and three direct Codex CLI child calls. These PIDs only identify that historical run.
- Terminal error: `semantic reduce claim cited an unavailable capsule`, node `REDUCE-7299554f42e527ca3ba4`, trace `TRACE-1b059c2cb747`, checkpoint `LLMCKPT-6f9f18fe5209a78e`. The original checkpoint is preserved. Do not rewrite or delete it.
- Narrow correction commit `9ff03ff` is pushed to `origin/codex/v5-gpt61-high-offline`; Ruff PASS, Mypy 139 files PASS, pytest `1,964 passed`. It permits one content-addressed correction request only after an exact unavailable-citation validation error, keeps non-citation draft fields and citation-list lengths unchanged, and still fails closed if the corrected response is invalid.
- At 18:15 KST no matching writer or `.wal` was present. Read-only reconciliation confirmed `reduce_nodes=1,005`, the failed node is not persisted, `semantic_capsules=52,644`, and `semantic_unit_assignments=823,279`. Re-discover process ancestry and WAL state before any resume; do not trust this snapshot as current later.

Existing source import, real embeddings, and map phase are not to be repeated. The prior map trace set contained 7,513 exact checkpoint hits and zero fresh map provider calls; verify the current run's traces at safe checkpoints/terminal and report any unexpected fresh work. Compiler citation fix `7bb1b40` is already pushed and passed focused regressions, Ruff, Mypy (139 source files), and pytest (`1,960 passed`, `1,559 warnings`). Do not reapply it. Earlier citation-recovery evidence and exact validator changes are preserved in [offline_brain_recovery_20261004.md](../../diagnostics/offline_brain_recovery_20261004.md).

The repository worktree already contains pre-existing untracked artifacts:

- `diagnostics/offline_reduce_dag_preflight_existing_capsules_20261004.json`
- `runs/offline_v5_gpt61_high_20261004/`
- `runs/resource_logs/`

Preserve these and all other user or run outputs. Never clean, move, delete, or commit research originals, production DBs/WALs, checkpoint files, or large generated run artifacts.

## Daily product contract

The intended production path is:

1. Validate the trade date, news CSV, and cutoff.
2. Load the already-built cutoff-safe world/category brain, semantic indexes, and relevant current-news-grounded memories before the first GPT decision request.
3. Send current news and that brain context together to the single logical `final_market_decision` call.
4. Allow at most one structured-output repair if validation fails.
5. Return open-world sector/security candidates with evidence, citations, uncertainty, provenance, and a context manifest. Do not invent an answer when evidence is insufficient.

There is no brain-free preliminary interpretation call. Do not use legacy exhaustive `nslab analyze`, web search, D-day prices/outcomes, post-cutoff information, or per-record/per-cluster/per-lane LLM fan-out in the production BLIND path. Its evidence policy is `CSV_MEMORY_ONLY_STRICT`. Targeted retrieval may return a small relevance-driven, citation-bearing evidence set; it must not make daily LLM cost proportional to the raw corpus.

## 실행 절차

### 1. 기존 compile만 이어서 관찰

- Read repository `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`, this goal, the recovery report, and the relevant V2/daily architecture docs.
- Search current processes by full command line and ancestry for compile ID `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`; verify the source/plan/target identity. A remembered PID, terminal session, or progress number is not proof of current process state.
- If the matching writer is alive, observe only that writer. Do not launch a second build. While writer or WAL is live, read only process/child status, progress ledger, invocation traces/checkpoint files when safe, and resource samples. Do not open the live DuckDB/WAL, hash it, or run parity/audit scans against it.
- Continue waiting while the healthy existing compile makes progress. Do not stop a healthy provider child merely because it is slow. Track Python private memory, child count, CPU, available RAM, and disk for actual runaway growth or exhaustion; do not convert routine resource observation into an arbitrary stop condition.
- If the writer exits or the console session is lost, establish terminal status from process ancestry, exit code, ledger, and WAL state. A disconnected session alone is not proof of exit. Do not inspect the DB until no matching writer remains and WAL activity is settled.
- Only after confirmed terminal state and settled WAL, reconcile progress, plan/receipt, source manifest, DB metadata/node rows, and checkpoint lineage read-only. If incomplete and consistent, resume the same compile identity and compatible checkpoints using the repository-supported command/profile. Do not re-import, re-embed, re-plan into a different topology, delete checkpoints, or change model/profile to make a resume convenient.
- If a new deterministic validator failure occurs, reconstruct that exact node's prompt, allowed evidence IDs, response, trace, and checkpoint. Make only a narrow evidence-based fix with positive and negative regression tests; never weaken validation generally or drop a claim. Run `python -m ruff check .`, `python -m mypy src/news_scalping_lab`, and `python -m pytest` in the code worktree before commit. Resume only after the fix is pushed and the original build identity is preserved.

### 2. Close and audit the package

After and only after the fixed DAG reaches terminal `1,868/1,868`:

- Confirm persisted node closure equals the plan and no failed/unpersisted node is hidden by the progress counter.
- Build/finalize the supported immutable Offline Semantic Brain V2 package without redoing source import, embedding, or fresh map work.
- Run the repository-supported standalone/deep verifier and package, source/claim provenance, citation, coverage, and real semantic-index audits. Verify the production semantic index uses a real embedding provider/HNSW, not a deterministic test vector index.
- Report input record accounting, dates/years/trading-day gaps, semantic payload exposure, claims and cited records, category/world outputs, HNSW/index roots, package roots, and model/provider/checkpoint counts as distinct measures. Counts and hashes are not a substitute for proving source-to-claim lineage.
- Do not call record accounting, unit count, model task closure, or package generation by itself “10-year semantic completion,” backtest success, or a fine-tuned model.

### 3. Exercise a real pre-open CSV

- Re-search the available inbox/staging/download locations for a real, unmodified pre-open news CSV whose trade date is later than the package build cutoff, and verify its rows do not include post-cutoff or outcome data. Prior preflight found no eligible CSV: the examined 2026-06-24 and 2026-04-14 files predate the build cutoff, and the examined 2023-06-08 file contains out-of-day rows. Recheck; do not assume that old search is current.
- If no eligible real file is available, request the user's actual CSV and mark smoke `BLOCKED_INPUT_REQUIRED`. Never fabricate/trim data to force a pass, and never report the full daily product verified without this smoke.
- Run the supported production `analyze-daily` command with the actual file, correct trade date/cutoff, and selected audited V2 package. Do not substitute a unit test, mock model, point-in-time mode without compiled guidance, or legacy `analyze` path.
- Verify the run manifest binds the exact brain/package and memory/index identities; one logical `final_market_decision` call was made (at most two live invocations only if one structured repair was needed); brain context was present in the first request; web/D-day/outcome/post-cutoff evidence was absent; citations resolve; output and context manifest are persisted; runtime is recorded rather than guessed.
- Treat this as a functional smoke, not evidence of predictive quality or a backtest.

### 4. Formal quality gate and release boundary

- At execution time, verify that a registered bounded blind quality gate exists for the same deployable one-call daily architecture, with physically separated/sealed blind news and outcomes and the current approved evaluation contract. Do not invent a gate, expand it into an unbounded corpus run, or switch to an evaluator-only architecture.
- Prior preflight found `QSEL-19b3c80ba392db8564c9` only in a report/anchor and did not find its selection artifact; `QSEL-16352cbccb703547c2ba` was calibration-only and did not prove HOLDOUT or daily analyzer closure. Recheck current files and registry before relying on either.
- `QPRED-704f15cde6e4152b6931` and its 379-pack ancestry are permanently `HALTED_MISALIGNED_DIAGNOSTIC_ONLY`. Never resume, score, compare, promote, or use them as cache input. Also preserve and exclude the invalidated `QPRED-4ecc6155c077cb5b092c` ancestry documented in the repository.
- If the valid registered gate or sealed inputs are absent, report `NOT_RUN_GATE_MISSING`, predictive quality `UNAPPROVED`, and production `HOLD`. A smoke pass does not override this.
- Verify the repository-supported release-binding manifest names the audited package and a tested rollback target/procedure. Do not switch the production pointer or activate production without the required quality gate, explicit approval, and rollback proof.

### 5. Evidence, documentation, and handoff

- Run the required quality commands for code changes: Ruff, Mypy, and full pytest. For a docs-only change, state which previously passing code revision/gates are being relied on; do not claim a new code test run.
- Update this goal and the recovery/closeout report with a timestamped, evidence-backed snapshot. Keep compiler closure, provider-fresh output, exact checkpoint hits, local carry, package audit, daily smoke, formal quality, and production activation in separate fields.
- Sync the repository copy and `C:\Users\eorb9\Downloads\codex_goal_nslab_finish_brain_and_daily_csv.md`. Commit and push only relevant source/docs changes with a Korean commit message when this task reaches a coherent checkpoint. Verify branch and remote result. Leave existing untracked/run outputs alone.
- Provide an external-reviewable closeout with exact paths/hashes, date coverage, semantic exposure/citation limits, actual model identities, CSV/date/cutoff/call count/latency if smoke ran, formal gate status, release/rollback state, and remaining blocker. State clearly that the system is a GPT-backed brain package, not a newly fine-tuned GPT model.

## 완료 판정과 상태 이름

- **Compile complete:** same fixed compile ID has `1,868/1,868` persisted nodes and its source/plan/checkpoint/citation lineage reconciles.
- **Brain package verified:** immutable package passes standalone/deep, real HNSW, provenance, citation, and coverage audits.
- **Daily flow demonstrated:** a real eligible pre-open CSV passes production `analyze-daily` with the one-call brain-loaded architecture, cutoff boundary, resolved citations, and complete manifest.
- **Predictive quality approved:** only a registered, sealed, bounded blind gate for that same architecture passes. Missing gate is `NOT_RUN_GATE_MISSING`, never PASS.
- **Production active:** only after quality approval, required user approval, release binding, and verified rollback. Otherwise remain HOLD.

Do not collapse these into one “done” label. If the compiler and package are complete but the CSV is missing, report the build as complete and daily product verification as `BLOCKED_INPUT_REQUIRED`; keep this goal open for that smoke. If the smoke works but the registered gate is absent, the technical flow is demonstrated while predictive quality remains unapproved and production stays HOLD. Do not extend the goal with more synthesis, new source imports, arbitrary model calls, or repeated rebuilds unless a concrete, evidenced defect or newly accepted research source requires a separately scoped change.

## 다음 실행 시 전달할 요청

아래 요청으로 이 문서의 목표를 실행한다. 현재 live writer를 재사용할 것이며, 문서의 handoff 수치는 실행 직전에 다시 확인한다.

> 저장소 `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`, `docs/operations/codex_goal_finish_brain_and_daily_csv.md`, `diagnostics/offline_brain_recovery_20261004.md`를 먼저 읽고 이 문서의 제품 계약과 완료 판정을 그대로 따라 진행해. 우선 현재 process command line/ancestry와 progress ledger를 다시 확인해. 같은 compile `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`은 마지막으로 `1,005/1,868` closed, `863` remaining (`2026-10-05T17:36:16.975546+09:00`)에서 exit code 1로 멈췄고, citation correction fix `9ff03ff`가 원격에 push돼 있다. 이 수치는 handoff일 뿐이니 현재 사실로 가정하지 마. 새 writer를 만들기 전에 matching process, WAL, ledger, branch/HEAD를 확인해. matching writer가 살아 있으면 그 하나만 관찰하고 절대로 duplicate를 시작하지 마. writer/WAL live 중 DuckDB/WAL read, hash, parity scan을 금지하고 progress/process/resource만 관찰해. process/session이 사라지면 exit code, ancestry, ledger, WAL로 terminal을 증명한 뒤에만 DB를 읽어. terminal + settled WAL + consistent DB를 확인한 경우에만 동일 source/manifest/plan/target/checkpoint identity, Codex OAuth `gpt-6.1-sol/high`, concurrency 4로 `--continue-after-map-plan` 재개해. 이미 완료한 import, embedding, planner, map work와 valid checkpoints는 반복하지 마.
>
> 기존 import, real embedding, map 및 완료된 checkpoint를 재실행하지 말고, fixed source manifest/record root/compile ID/1,868-node topology/target/checkpoint identity를 유지해. Fresh synthesis는 지정된 Codex OAuth `gpt-6.1-sol/high`를 사용하고 기존 정확히 호환되는 `gpt-5.6-sol/xhigh` checkpoint는 재사용하되, trace별 실제 model/provider를 구분해 보고해. fixed DAG의 progress만 `closed/1,868`로 표현하고 record/unit count나 그 비율을 뇌의 의미 이해율로 부르지 마. Compile이 닫힐 때까지 기다린 다음 persisted closure와 lineage를 대조하고, immutable V2 package, real HNSW/provenance/citation/coverage audit를 수행해.
>
> 이어서 현재 실제 pre-open CSV를 다시 찾는다. build cutoff `2026-08-21T18:52:07.302105+09:00`보다 이후 거래일이고 cutoff-safe인 real CSV가 없으면 임의 데이터를 만들지 말고 `BLOCKED_INPUT_REQUIRED`로 보고하고 사용자에게 파일을 요청해. 있으면 production `analyze-daily`로 실제 smoke를 실행하고 brain이 첫 `final_market_decision` 요청부터 함께 들어갔는지, 논리 호출 1회/repair 최대 1회, CSV_MEMORY_ONLY_STRICT, no web/no D-day or outcome leak, citation과 context manifest를 검증해. Legacy exhaustive graph 또는 evaluator 전용 경로로 우회하지 마.
>
> Formal blind gate와 sealed input은 실행 시점에 다시 확인해. 등록 gate/입력이 없으면 formal evaluation을 만들거나 무제한 확장하지 말고 `NOT_RUN_GATE_MISSING`, predictive quality `UNAPPROVED`, production `HOLD`로 기록해. `QPRED-704f15cde6e4152b6931`의 379-pack을 어떤 방식으로도 재개/평가/비교/cache로 사용하지 마. Package를 production pointer에 활성화하지 말고 repository-supported release binding과 rollback만 검증해. 변경된 코드에는 Ruff/Mypy/pytest를 실행하고, goal/recovery/외부 리뷰 문서와 Downloads본을 동기화한 다음 관련 문서/코드만 한국어 commit/push해. 연구 원본, DB/WAL, checkpoint, 대형 산출물 및 기존 untracked 파일을 변경·삭제·이동·commit하지 마. 마지막 보고에서 compile, package, real CSV smoke, formal quality, production activation을 각기 별도 상태로 판정하고 남은 항목을 명시해.
