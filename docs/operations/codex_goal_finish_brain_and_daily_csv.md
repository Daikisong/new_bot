# Goal: 일회성 두뇌 컴파일 완료 및 장전 CSV 판단 검증

문서 갱신: 2026-10-05 23:11 KST

## 목표

이미 import·repair·embedding·map까지 진행된 연구 원료를 처음부터 다시 처리하지 않는다. 기존 고정 빌드 `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 남은 작업을 이어서 닫고, 결과를 검증된 Offline Semantic Brain V2 패키지로 보존한다. 이어서 실제 장전 뉴스 CSV를 production `analyze-daily` 경로에 넣어, 이미 만든 두뇌와 당일 뉴스가 첫 GPT 판단 요청부터 함께 사용되는지 검증한다.

제품 목표는 사용자가 장전 CSV를 넣으면 cutoff-safe 두뇌 지식과 뉴스에 기반해 주도 섹터·종목 후보, 근거, 불확실성, 출처가 포함된 판단을 받는 것이다. 이 두뇌는 GPT 가중치를 새로 학습한 모델이 아니라, 기존 GPT 추론기가 매일 사용할 수 있도록 한 번 컴파일해 보존하는 지식 패키지와 검색 인덱스다. 완성 뒤 매일 연구 원문 전체를 재해석하거나 모든 record에 LLM을 호출하지 않는다.

## 진행 수치의 뜻

| 수치 | 현재 값 | 정확한 의미 |
|---|---:|---|
| 원료 record | 823,279 | 고정 source manifest의 구조적 입력 회계 수. 각 record를 GPT가 직접 읽었다는 뜻은 아님 |
| semantic unit | 52,644 | 컴파일 입력을 묶은 단위 수. 연도, 요약, LLM 호출 수가 아님 |
| 고정 model-task DAG | 1,868 | 이번 compile plan에 이미 정해진 유한 작업 노드 수. reducer 1,858, category review 9, world root 1 |
| 마지막 terminal DB 확인 노드 | 1,005 / 1,868 | 2026-10-05 18:15 KST read-only reconciliation 기준, 당시 fixed-DAG closure 53.80% |
| 마지막 terminal DB 기준 잔여 노드 | 863 | 1,868 - 1,005. 현재 live writer가 재개되어 이 값은 최신 진행값이 아님 |
| 현재 writer ledger | 1,276 / 1,868 (68.31%), 592 잔여 | `2026-10-05T23:10:38.764553+09:00`; phase `offline_reduce`. Live ledger 수치이며 terminal DB에 저장된 closure로 아직 대조되지 않음 |

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

상태 스냅샷은 `2026-10-05 23:11 KST`다. 이전 writer는 reducer citation validation 오류로 exit code 1 종료했다. 이력상 좁은 citation 복구 commit `7bb1b40`에 이어 `9ff03ff`가 추가되었고, compiler HEAD `9ff03ff`에서 동일 빌드가 18:19 KST부터 재개되어 마지막 23:10 KST process scan에서도 실행 중이었다. 다음 실행에서도 process ancestry와 ledger를 다시 확인하고, 살아 있는 writer가 있으면 그 하나만 이어서 관찰한다.

- 18:15 KST의 마지막 terminal read-only DB reconciliation: `reduce_nodes=1,005`, failed node는 미저장, `semantic_capsules=52,644`, `semantic_unit_assignments=823,279`. 이는 재개 전 기준값이다.
- 이전 terminal failure: `semantic reduce claim cited an unavailable capsule`, node `REDUCE-7299554f42e527ca3ba4`, trace `TRACE-1b059c2cb747`, checkpoint `LLMCKPT-6f9f18fe5209a78e`. 원본 checkpoint는 보존한다. 재작성·삭제하지 않는다.
- Fix `9ff03ff`는 `origin/codex/v5-gpt61-high-offline`에 push되었다. 허용된 정확한 citation ID 집합에 없는 citation으로 reducer validation이 실패한 경우에만 content-addressed correction request를 한 번 추가한다. 비-citation draft 내용, claim 수/순서, citation list 길이는 그대로여야 하며, correction도 틀리면 fail closed한다.
- 동일 source/manifest/compile/target/checkpoint identity와 Codex OAuth `gpt-6.1-sol/high`, `NSLAB_MAX_CONCURRENCY=4`로 `--continue-after-map-plan`이 18:19 KST 재시작되었다. 18:42 KST process scan에서는 wrapper PowerShell PID `33952` 아래 단일 Python writer PID `13716`이 같은 command로 살아 있었고 executor session `60017`도 active였다. PID/session은 임시 식별자이므로 다음 호출에서 재탐색한다.
- 23:10:38.764553 KST latest safe ledger: phase `offline_reduce`, `completed_model_node_count=1,276`, `total_model_node_count=1,868`, `current_model_node_id=REDUCE-a6c629542b7b795926da`, `processed_record_count=823,279`. Ledger 기준 1,276/1,868 (68.31%), 592 remaining. 이는 live progress 표시일 뿐 terminal DB에 저장된 closure 증거가 아니며, 종료 후 persisted node rows와 대조해야 한다. Record scan 100%는 compile DAG closure나 semantic understanding 100%가 아니다.
- 이전 오류 노드의 보정 결과가 실제 checkpoint로 생성되었다: `LLMCKPT-a0a69c78021fc16e`, purpose `offline_semantic_reduce.REDUCE-7299554f42e527ca3ba4.citation_repair.v1.REDUCE-7299554f42e527ca3ba4`, status `ok`, provider `CodexOAuthProvider`, model `gpt-6.1-sol`, reasoning `high`, output SHA-256 `278c5b898a992db789b12f7b3d634073d2932057dd5381f614aef63cb4674506`. Writer는 이후 reducer 노드까지 진행했다. 이는 보정 경로가 실행되어 작업이 전진했다는 증거지만, persisted DB row closure는 writer 종료 후 따로 대조한다. 원본 invalid checkpoint는 그대로 보존되어 있다.
- 재개 이후 안정된 checkpoint JSON 35개를 확인했다(18:43 KST 관측, 모두 15초 이상 변경되지 않은 파일). 모두 `status=ok`, `CodexOAuthProvider`, `gpt-6.1-sol/high`였고 이 중 1개가 위의 단일 citation correction이었다. 이 checkpoint 파일 수를 fresh provider call 총계로 단정하지 않는다. 최종 fresh/cache/local carry 집계는 trace와 usage ledger 대조 후 보고한다.
- 현재 writer가 살아 있어 target DuckDB/WAL은 열지 않았고, WAL 상태·DB closure·현재 DAG progress를 추론하지 않는다. Writer가 종료된 뒤 exit code, process ancestry, ledger, WAL settle을 확인하고서만 read-only DB reconciliation을 수행한다.
- 23:10 KST process/resource scan confirmed the same Python writer PID `13716` under wrapper PID `33952`. Resource sample: private memory about `3.87 GiB`, working set about `131 MiB`, cumulative CPU about `262.25` seconds; free RAM about `14.62 GiB`, C: free about `234.63 GiB`. This is an observation, not an ETA or completion estimate. No DB/WAL was read. Compiler commit `9ff03ff` already passed Ruff, Mypy (139 source files), and pytest (`1,964 passed`, 1,586 warnings); do not rerun unchanged gates.
- 앞선 import, real embedding, planner 및 map 결과는 다시 만들지 않는다. 고정 map usage ledger `brain/.work/OFFLINE-COMPILE-0dd9198ac9ef79215ab1/map_stage_checkpoint_usage.jsonl`의 SHA-256 `36a92c903b7315e78ce4c36f676a6d98a69ba9ac31a06fc8e53af6b5b6282f3b`를 재검증했다. 7,513 rows 중 compatible checkpoint hit 7,498건, fresh output 15건이며 fresh 15건은 모두 `gpt-6.1-sol/high`; model-config 합계는 `gpt-5.6-sol/xhigh` 7,462건, `gpt-6.1-sol/high` 51건이다. map 단계는 이미 완료됐으며 다시 실행하지 않는다.
- `diagnostics/offline_reduce_dag_preflight_existing_capsules_20261004.json`, `runs/offline_v5_gpt61_high_20261004/`, `runs/resource_logs/`는 기존 untracked 산출물이다. 이 파일과 모든 사용자/실행 산출물은 보존하며 정리·이동·삭제·commit하지 않는다.
- Citation fixes are distinct and both are already applied: `7bb1b40` narrowly normalizes one provable citation suffix; `9ff03ff` permits at most one content-addressed correction request for an unavailable-capsule citation while preserving claim content and strict allowlist validation. Do not reapply either fix. Evidence is in [offline_brain_recovery_20261004.md](../../diagnostics/offline_brain_recovery_20261004.md).

현재 live writer가 있는 동안 DuckDB/WAL read, hash, parity, deep audit는 금지한다. Build plan/progress를 다시 확인할 때에도 이 안전 규칙이 우선이다.

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

Do not collapse these into one “done” label. The bounded engineering work ends after the fixed compile reaches a verified terminal state, the package audit is complete, and the real-CSV smoke is either demonstrated or precisely marked `BLOCKED_INPUT_REQUIRED`. If the CSV is unavailable, stop active work and wait for that user input; do not keep a process running. If the smoke passes but the registered gate is absent, report the technical flow as demonstrated, quality as unapproved, and production as HOLD; this is a final status for this run, not a reason to invent an unbounded evaluation or repeat compilation. Production activation remains a separate approval/release action. No more synthesis, imports, arbitrary model calls, or rebuilds are in scope absent a newly accepted source or an evidenced defect with a separate scope.

## 다음 실행 시 전달할 요청

아래 요청으로 이 문서의 목표를 실행한다. 2026-10-05 23:10:38 KST ledger에서는 compile writer가 `1,276/1,868`로 실행 중이었다. 실행 시점에 process와 ledger를 다시 탐색한다. 살아 있으면 기존 writer 하나만 관찰하고, 종료가 입증된 경우에만 terminal reconciliation 후 같은 고정 빌드를 재개한다.

> 저장소 `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`, 이 goal, 회복 보고서 및 daily 제품 계약을 먼저 읽고 끝까지 수행해. 목표는 이미 모은 연구를 고정된 일회성 offline compile으로 검증 가능한 brain package에 보존하고, 장전 CSV와 brain을 첫 GPT 판단 요청에 함께 넣는 실제 daily 흐름을 확인하는 것이다. 새 GPT 가중치를 학습하거나 매일 연구 원문을 다시 읽히는 작업이 아니다. 823,279 record, 52,644 semantic unit, 1,868 DAG node는 서로 다른 단위다. 52,644는 연도나 요약 수가 아니며 `1,276/1,868`은 compile task 진행률이지 연구 의미 노출률이나 예측 성능이 아니다. 알려진 원료 날짜 범위 `2018-01-03`~`2026-06-19`는 달력상 약 8년 반이므로 10년 전체를 채웠다고 주장하지 말고, 완료 audit에서 연도·거래일 공백, payload exposure와 citation coverage를 각각 산출해.
>
> 실행 시점에 compile ID `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 process ancestry와 safe progress ledger를 다시 확인해. source manifest SHA `6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576`, record root `2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75`, 1,868-node topology, target DB와 checkpoint directory를 유지한다. 살아 있는 writer가 있으면 그것 하나만 관찰하고 duplicate build를 시작하지 않는다. writer/WAL live 중 DuckDB/WAL open/hash/parity/deep audit는 금지한다. 종료 후 exit code, ancestry, ledger, WAL settle을 확인한 뒤에만 read-only DB reconciliation을 한다. DAG가 미완료이고 persisted state가 fixed plan과 일치할 때만 동일 build를 Codex OAuth `gpt-6.1-sol/high`, concurrency 4, repository-supported `--continue-after-map-plan`으로 재개한다. Import, repair, real embedding, planning, map, 기존 유효 checkpoint는 반복하지 않는다. Fresh provider output, exact checkpoint hit, local carry, failure를 trace/usage lineage로 나눠 집계한다. throughput 근거 없이 ETA를 제시하지 않는다.
>
> 동일 fixed DAG가 terminal `1,868/1,868`에 도달하면 progress만 믿지 말고 persisted node closure와 source/plan/checkpoint/citation lineage를 대조한다. Supported immutable Offline Semantic Brain V2 package를 만들고 standalone/deep verifier, real embedding/HNSW, provenance, citation, coverage와 실제 날짜·거래일 공백을 audit한다. 모델/provider별 새 출력과 checkpoint 재사용을 구분한다. 이를 10년 전체 의미 이해, backtest 성공, fine-tuned model이라고 부르지 않는다.
>
> build cutoff `2026-08-21T18:52:07.302105+09:00` 이후 거래일이고 cutoff-safe한 실제 pre-open CSV가 있는지 다시 찾는다. 없으면 데이터를 만들거나 자르지 말고 `BLOCKED_INPUT_REQUIRED`로 멈추어 사용자에게 CSV를 요청한다. 있으면 audited package로 production `analyze-daily` smoke를 수행하고 brain과 CSV가 첫 `final_market_decision` 요청에 함께 들어갔는지, logical call 1회와 structured repair 최대 1회, `CSV_MEMORY_ONLY_STRICT`, no web/D-day/outcome/post-cutoff data, resolved citations와 context manifest를 검증한다. 이 smoke는 기능 확인이지 predictive quality/backtest 증명이 아니다. Raw corpus·record·cluster·lane 비례 LLM fan-out이나 legacy exhaustive `analyze`로 우회하지 않는다.
>
> 실행 시점의 registered bounded blind gate와 physically sealed input을 확인한다. 같은 daily architecture에 gate/입력이 없으면 평가 범위를 무제한으로 새로 만들지 말고 `NOT_RUN_GATE_MISSING`, predictive quality `UNAPPROVED`, production `HOLD`로 보고한다. `QPRED-704f15cde6e4152b6931`의 379-pack 및 invalidated ancestry를 재개·평가·비교·promotion·cache에 절대 쓰지 않는다. 명시 승인, 유효 gate, audited release binding, 검증된 rollback 없이는 production pointer를 활성화하지 않는다. 새 코드 변경이 있을 때만 Ruff/Mypy/pytest를 수행한다. goal과 필요한 recovery report, Downloads 사본을 동기화하고 관련 tracked 문서/코드만 한국어 commit/push한다. 연구 원본, DB/WAL, checkpoint, 큰 산출물과 기존 untracked/run output은 변경·삭제·이동·commit하지 않는다.
>
> 종료 보고에서는 compile, package audit, real-CSV daily smoke, predictive quality gate, production activation을 각각 별도 상태로 판정하고 미충족 조건을 명시한다. Once-only compile/package가 정상 완료되면 새로 수락된 연구 자료나 증거가 있는 구체적 결함이 없는 한 재import·재embed·재compile하지 않는다. 이후 일일 운영은 선택된 검증 package와 새 CSV로 inference만 한다.
