# Goal: 일회성 두뇌 컴파일 완료 및 장전 CSV 판단 검증

문서 갱신: 2026-10-06 01:44 KST

## 목표

이미 import·repair·embedding·map까지 진행된 연구 원료를 처음부터 다시 처리하지 않는다. 기존 고정 빌드 `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 남은 작업을 이어서 닫고, 결과를 검증된 Offline Semantic Brain V2 패키지로 보존한다. 이어서 실제 장전 뉴스 CSV를 production `analyze-daily` 경로에 넣어, 이미 만든 두뇌와 당일 뉴스가 첫 GPT 판단 요청부터 함께 사용되는지 검증한다.

제품 목표는 사용자가 장전 CSV를 넣으면 cutoff-safe 두뇌 지식과 뉴스에 기반해 주도 섹터·종목 후보, 근거, 불확실성, 출처가 포함된 판단을 받는 것이다. 이 두뇌는 GPT 가중치를 새로 학습한 모델이 아니라, 기존 GPT 추론기가 매일 사용할 수 있도록 한 번 컴파일해 보존하는 지식 패키지와 검색 인덱스다. 완성 뒤 매일 연구 원문 전체를 재해석하거나 모든 record에 LLM을 호출하지 않는다.

## 진행 수치의 뜻

| 수치 | 현재 값 | 정확한 의미 |
|---|---:|---|
| 원료 record | 823,279 | 고정 source manifest의 구조적 입력 회계 수. 각 record를 GPT가 직접 읽었다는 뜻은 아님 |
| semantic unit | 52,644 | 컴파일 입력을 묶은 단위 수. 연도, 요약, LLM 호출 수가 아님 |
| 고정 model-task DAG | 1,868 | 이번 compile plan에 이미 정해진 유한 작업 노드 수. reducer 1,858, category review 9, world root 1 |
| 마지막 terminal read-only DB 대조 | 1,406 / 1,868 (75.27%) | `reduce_nodes=1,406`; `semantic_capsules=52,644`; `semantic_unit_assignments=823,279`. 고정 DAG 노드 저장 수이며 의미 노출률이나 예측 성능은 아님 |
| 현재 ledger | 1,406 / 1,868 (75.27%), 462 잔여 | `2026-10-06T01:27:05.245732+09:00`; phase `offline_reduce`. 오류 종료 후 ledger와 DB를 read-only 대조했으며 failed node는 미저장 |
| 최신 compile 상태 | terminal failure, 재개 대기 | 2026-10-06 01:44 KST process 재확인에서 해당 writer 없음, compile WAL 파일 없음. 최신 실패 원인은 아래 handoff 참조 |

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

상태 스냅샷은 `2026-10-06 01:44 KST`다. 같은 고정 compile `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 writer는 exit code 1로 종료했다. 01:27:05 KST progress ledger는 `1,406/1,868`을 기록했고, writer 종료 및 WAL 부재 확인 뒤 수행한 read-only DuckDB 대조에서 `reduce_nodes=1,406`, `semantic_capsules=52,644`, `semantic_unit_assignments=823,279`, centroids `52,644`가 확인됐다. 따라서 462 model-task node가 남았다. ledger pointer `REDUCE-688a8b76cd3abd5f2a44`는 저장된 노드이며 실패 노드가 아니다. 01:44 KST process 검색에서 해당 compile writer는 발견되지 않았고 target WAL 파일도 없었다. 다음 실행은 새 중복 빌드가 아니라 기존 작업의 오류 원인을 좁게 고친 뒤 같은 compile identity를 재개하는 것이다.

- 최신 실패 노드: `REDUCE-fb92a7fe3de830942ca1`; checkpoint `LLMCKPT-162b4798faed7835.json`; trace `TRACE-c5afa4477fb8.json` (`C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b\runs\traces`). Trace의 purpose는 해당 reducer이고 provider/model identity는 Codex OAuth `gpt-6.1-sol/high`다. 호출은 약 8분 21초 수행된 뒤 실패했으며 trace에 원시 invalid output은 저장되지 않았다. 원시 응답이 보존됐다고 가정하거나 이를 분석했다고 주장하지 않는다.
- 정확한 validation failure: `semantic reduce output exceeds 12000-byte contract`. `SemanticReduceDraft.validate_bounded_serialized_size()`의 12,000 UTF-8-byte 제한을 초과했다. Prompt SHA-256은 `5556eddc7b5df086533efa8a2aed9b9302da17ee6bd3bc86860d50a179207e85`; prompt는 97,742 bytes였다. Failure checkpoint/trace 파일은 수정·삭제하지 않고, 다음 실행에서 먼저 경로와 SHA-256을 기록해 보존한다.
- 필요한 code recovery는 compile topology나 schema/limit 완화가 아니다. Compiler worktree `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b`, branch `codex/v5-gpt61-high-offline`, starting HEAD `9ff03ff`를 실행 시 재확인한다. 해당 코드의 `offline_v2.py` `_reduce_node` 및 `_reduce_world`에서 **정확히 이 output-size validation error만** 최대 한 번 별도 content-addressed size-recovery request로 처리한다. 변경 전/후 child IDs와 허용 citation 집합을 유지하고, 원본 full prompt의 immutable child evidence에서 compact replacement를 생성하도록 한다. 제안 목표는 serialized output 10,000 UTF-8 bytes 이하이며, response가 다시 초과하거나 schema/citation/child identity 검증에 실패하면 fail closed한다. 12,000-byte contract를 올리거나 없애지 않고, 일반 validation을 느슨하게 하거나 rejected raw output에 접근 가능하다고 가정하지 않는다.
- Size-recovery 요청은 원래 prompt/checkpoint를 덮어쓰지 않는 고유 purpose를 사용하고 정확히 한 번만 시도한다. 기존 citation correction은 별도 오류에만 적용되므로 재사용하되 호출 예산/종료 조건은 유한하게 테스트한다. Base `_reduce_prompt`, schemas, plan/topology, checkpoint identity는 바꾸지 않는다. 회귀 테스트는 size 초과 시 1회 retry, 요청 purpose와 원본 payload, 최대 크기 내 정상 응답, 두 번째 초과 시 종료, 일반 schema 오류에는 size retry 없음, child/citation validation 유지 및 fail-closed를 확인한다.
- `9ff03ff`는 앞선 unavailable-citation 실패를 좁게 복구한 이미 push된 수정이며 이 수정은 별개의 새 실패다. 과거 fix를 다시 적용하지 않는다. 새 code change 뒤 compiler worktree에서 Ruff, Mypy, full pytest를 다시 통과시키고, 한국어 commit/push를 확인한 다음에만 같은 compile을 재개한다.
- 고정 입력/산출 identity는 그대로다: source manifest SHA-256 `6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576`, record root `2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75`, memory snapshot `MEMIDX-1e64a1b6e6ba7b07b799`, 1,868-node DAG, target DB 경로, checkpoint lineage, Codex OAuth `gpt-6.1-sol/high`, concurrency 4, repository-supported `--continue-after-map-plan`을 유지한다.
- Import, repair, real embedding, planning, map, compatible checkpoints는 재실행하지 않는다. Map usage ledger SHA-256 `36a92c903b7315e78ce4c36f676a6d98a69ba9ac31a06fc8e53af6b5b6282f3b`의 7,513 rows (7,498 compatible checkpoint hit, 15 fresh `gpt-6.1-sol/high`)는 완료 근거다. Fresh provider output, exact checkpoint hit, local carry/validation, 실패 호출은 trace와 usage ledger를 대조해 별도 집계한다.
- 기존 untracked 산출물 `diagnostics/offline_reduce_dag_preflight_existing_capsules_20261004.json`, `runs/offline_v5_gpt61_high_20261004/`, `runs/resource_logs/`와 모든 source/DB/WAL/checkpoint/run output은 보존한다. 정리·이동·삭제·무관한 commit을 하지 않는다.

이전 citation 복구 이력은 [offline_brain_recovery_20261004.md](../../diagnostics/offline_brain_recovery_20261004.md)에 있다. 새 작업자는 live writer가 없는지 매번 재확인하되, 이 문서의 PID나 ledger를 현재 상태로 가정하지 않는다. Writer/WAL이 살아 있으면 DB/WAL read, hash, parity, deep audit를 하지 않는다.

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
- For a deterministic validator failure, reconstruct the exact node prompt, allowed evidence IDs, trace, checkpoint, and persisted DB state. Do not claim an unavailable raw provider response was inspected. For the current size failure, the raw invalid response is absent from the trace/checkpoint. Implement only the bounded recovery specified in the current handoff, with positive and negative tests; never weaken the 12,000-byte/schema/citation/coverage validation or silently drop evidence. Run `python -m ruff check .`, `python -m mypy src/news_scalping_lab`, and `python -m pytest` in the compiler worktree before commit. Resume only after the fix is pushed and the original build identity is preserved.

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

아래 요청으로 이 문서의 목표를 실행한다. 문서 안의 시각과 progress는 handoff 증거이지 다음 실행 시점의 live 상태가 아니므로, process/ledger/WAL을 다시 확인한다.

> 저장소 `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`, 이 goal, recovery report와 daily 제품 계약을 읽고 이 문서의 목표를 끝까지 이어서 수행해. 제품 목표는 새 GPT 가중치 학습이 아니라, 이미 모은 연구를 한 번 offline compile한 cutoff-safe brain package와 장전 CSV를 첫 GPT 판단 요청에 함께 넣어 섹터·종목 후보 및 근거를 내는 daily 흐름을 완성·검증하는 것이다. daily BLIND는 `CSV_MEMORY_ONLY_STRICT`, 한 번의 logical `final_market_decision`(+구조화 복구 최대 1회), no web/D-day/outcome/post-cutoff, raw corpus/record/cluster/lane 비례 LLM fan-out 금지다.
>
> 먼저 compile ID `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 live ancestry, safe ledger, WAL을 확인한다. 최신 기록은 `1,406/1,868` persisted node, 462 remaining이다. 기존 writer는 `REDUCE-fb92a7fe3de830942ca1`에서 `semantic reduce output exceeds 12000-byte contract`로 exit 1 종료했고, 당시 output-size validator의 원시 응답은 보존되지 않았다. source manifest SHA, record root, fixed 1,868-node plan, target DB, checkpoint lineage, Codex OAuth `gpt-6.1-sol/high`, concurrency 4와 `--continue-after-map-plan`을 바꾸지 않는다. 살아 있는 writer가 있으면 그것 하나만 관찰하고 duplicate build를 시작하지 않는다. writer/WAL live 동안 DB/WAL을 열거나 hash/audit하지 않는다.
>
> 미완료 DAG를 재개하기 전에 compiler worktree/branch/HEAD를 재확인하고 이 exact-size 오류에 대한 좁고 한 번뿐인 recovery를 구현해. 12,000-byte contract, schema, base prompts, topology, child/evidence coverage와 strict citation allowlist를 완화하지 않는다. 원본 failed checkpoint/trace를 hash해 보존한다. `_reduce_node` 및 `_reduce_world`에서 이 exact error일 때만 별도 content-addressed purpose로 원본 full prompt를 이용한 compact replacement를 최대 한 번 생성하고 10,000 UTF-8-byte 이하를 목표로 한다. 두 번째 초과, 다른 validation error, invalid child/citation이면 fail closed한다. Size/non-size 경로와 요청 목적, 최대 1회 호출, 정상 통과 및 fail-closed를 검증하는 회귀 테스트를 추가하고, 컴파일 worktree에서 Ruff/Mypy/full pytest 통과 후 한국어 commit/push를 확인한다. 그 후에만 기존 compile을 재개한다. Import/repair/embedding/planner/map/compatible checkpoints를 재실행하지 않는다. 진행률은 persisted closure, fresh provider result, exact cache hit, local carry, failure를 구분해 보고하고 근거 없는 ETA를 만들지 않는다.
>
> 같은 DAG가 terminal `1,868/1,868`이 되면 persisted rows와 source/plan/checkpoint/citation lineage를 대조한 뒤 supported immutable Offline Semantic Brain V2 package를 생성·standalone/deep 검증한다. Real embedding/HNSW, provenance, coverage, payload exposure, citations, 연도·거래일 공백을 각각 감사한다. `823,279` records, `52,644` semantic units/capsules, `1,868` DAG nodes는 서로 다른 수치다. record scan 완료나 DAG closure를 10년치 의미 노출/예측 성능/backtest/fine-tuning 성공이라고 부르지 않는다. 완료된 import·repair·embedding·map·고정 topology는 증거 있는 결함이 없는 한 반복하지 않는다.
>
> build cutoff `2026-08-21T18:52:07.302105+09:00` 이후의 cutoff-safe 실제 pre-open CSV를 다시 찾는다. 없으면 만들거나 trim하지 말고 `BLOCKED_INPUT_REQUIRED`로 사용자에게 요청한다. 있으면 audited package와 production `analyze-daily`로 daily smoke를 실행하고 첫 요청의 brain+CSV, cutoff/no-web 경계, citations, provenance/context manifest, 호출 수와 실제 latency를 확인한다. 이는 기능 smoke이지 predictive quality 증명이 아니다. registered bounded blind gate와 sealed inputs가 같은 deployable architecture에 없으면 `NOT_RUN_GATE_MISSING`, predictive quality `UNAPPROVED`, production `HOLD`다. `QPRED-704f15cde6e4152b6931` 379-pack 및 모든 invalidated ancestry는 재개·평가·비교·promotion·cache 입력 금지. 유효 quality gate, 사용자 승인, release binding과 rollback 증거 없이는 production을 활성화하지 않는다.
>
> 종료할 때 compile, package audit, 실제 CSV smoke, formal quality, production activation 상태를 분리해 보고해. goal/recovery 문서와 Downloads 사본을 동기화하고 관련 문서·코드만 처리한다. 연구 원료, DB/WAL, checkpoint, 큰 산출물과 기존 untracked/run output은 보존한다. 이 작업의 최종 산출은 기존 GPT가 사용하는 검증 brain package이지 새로 fine-tune한 GPT 모델이 아니다.
