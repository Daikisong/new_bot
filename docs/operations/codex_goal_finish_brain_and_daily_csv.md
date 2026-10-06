# Goal: 일회성 두뇌 컴파일 완료 및 장전 CSV 판단 검증

문서 갱신: 2026-10-06 15:48 KST

현재 handoff 요약: 고정 compile ID `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 동일 writer가 02:37 KST부터 실행 중이다. Safe progress ledger의 최신 표본은 15:43:43 KST, `1,782/1,868` DAG node (95.40%), 86 node 잔여, phase `offline_reduce`, current node `REDUCE-fa8c20e31174bf0b48b2`다. `processed_record_count=823,279`는 입력 record 회계 값이지 DAG 완료 수가 아니다. 15:48:42 KST 확인에서 PowerShell parent PID `91528`, Python writer PID `76004`, Codex CLI 요청 process PID `76636`이 살아 있었다. PID는 handoff 참고값일 뿐 다음 실행에서 다시 식별해야 한다. 마지막 terminal read-only DB 대조는 재개 전 02:22 KST의 `1,406/1,868`이며, 현재 writer가 살아 있으므로 이후 DB/WAL을 읽거나 감사하지 않는다. 같은 시각 Python private memory 약 3.98 GiB, working set 137 MiB, 누적 CPU 256.75초, 사용 가능 RAM 18.43 GiB, C: 여유 230.75 GiB였다. 이는 단일 관측치라 메모리 누수 부재를 증명하지 않으므로 추세만 주기적으로 확인하고, 실제 지속 증가/자원 고갈이 없으면 느린 provider 대기만으로 중단하지 않는다. compile은 02:37 KST 재개 후 약 13시간 진행 중이나, 경과 시간을 완료율이나 ETA로 환산하지 않는다. Recovery fix `8b1a2f9`는 compiler worktree의 `codex/v5-gpt61-high-offline` branch에 push됐고 해당 revision의 quality gate도 통과했다. 다음 실행은 현재 writer/session을 다시 식별해 같은 작업을 관찰하며 중복 build를 시작하지 않는다.

## 목표

이미 import·repair·embedding·map까지 진행된 연구 원료를 처음부터 다시 처리하지 않는다. 기존 고정 빌드 `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 남은 작업을 이어서 닫고, 결과를 검증된 Offline Semantic Brain V2 패키지로 보존한다. 이어서 실제 장전 뉴스 CSV를 production `analyze-daily` 경로에 넣어, 이미 만든 두뇌와 당일 뉴스가 첫 GPT 판단 요청부터 함께 사용되는지 검증한다.

제품 목표는 사용자가 장전 CSV를 넣으면 cutoff-safe 두뇌 지식과 뉴스에 기반해 주도 섹터·종목 후보, 근거, 불확실성, 출처가 포함된 판단을 받는 것이다. 이 두뇌는 GPT 가중치를 새로 학습한 모델이 아니라, 기존 GPT 추론기가 매일 사용할 수 있도록 한 번 컴파일해 보존하는 지식 패키지와 검색 인덱스다. 완성 뒤 매일 연구 원문 전체를 재해석하거나 모든 record에 LLM을 호출하지 않는다.

## 진행 수치의 뜻

| 수치 | 현재 값 | 정확한 의미 |
|---|---:|---|
| 원료 record | 823,279 | 고정 source manifest의 구조적 입력 회계 수. 각 record를 GPT가 직접 읽었다는 뜻은 아님 |
| semantic unit | 52,644 | 컴파일 입력을 묶은 단위 수. 연도, 요약, LLM 호출 수가 아님 |
| 고정 model-task DAG | 1,868 | 이번 compile plan에 이미 정해진 유한 작업 노드 수. reducer 1,858, category review 9, world root 1 |
| 마지막 terminal read-only DB 대조 | 1,406 / 1,868 (75.27%) | 재개 전 02:22 KST `reduce_nodes=1,406`; `semantic_capsules=52,644`; `semantic_unit_assignments=823,279`. 이후 writer가 살아 있어 DB를 다시 읽지 않음 |
| 최신 safe progress ledger | 1,782 / 1,868 (95.40%), 86 잔여 | `2026-10-06T15:43:43.113014+09:00`; phase `offline_reduce`; current node `REDUCE-fa8c20e31174bf0b48b2`. ledger의 record count 823,279는 input accounting이며 node 완료와 별개 |
| 최신 compile 상태 | 같은 고정 compile 실행 중 | 15:48:42 KST process 확인: PowerShell parent PID `91528`, Python PID `76004` (02:37:44 시작), Codex CLI 요청 PID `76636` (15:43:43 시작). PID는 다음 실행 때 재확인한다. 실행/session이 살아 있으면 관찰만 하고 중복 build 금지. writer/WAL live 중 DB/WAL read/hash/audit 금지 |

`823,279` 또는 `52,644`를 완료율 분모로 바꾸거나, 이를 보고 “10년치 의미를 모두 GPT가 읽었다”고 말하지 않는다. `52,644`는 연도 수나 52,644건의 개별 원문 요약이 아니라 이번 compiler의 semantic capsule/unit 수다. 이것만으로 연도별 연구 공백, 각 원문의 LLM 직접 노출 여부, 예측 유용성을 추론할 수 없다. 입력 record coverage, 날짜·연도·거래일 coverage, LLM payload exposure, claim citation coverage, DAG closure는 각각 별도 지표로 검증하고 보고한다. 현재 기록된 원료 날짜 범위 `2018-01-03`~`2026-06-19`는 약 8년 반의 달력 범위다. 10년 전체 또는 모든 거래일을 채웠다는 주장은 audit 증거 없이 하지 않는다.

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

상태 스냅샷은 `2026-10-06 15:48 KST`다. 같은 고정 compile은 01:28 KST reducer 출력 계약 오류 뒤, 이미 검증·push된 recovery fix를 포함해 02:37 KST 같은 identity로 재개되었다. 최신 safe ledger(`2026-10-06T15:43:43.113014+09:00`)는 `1,782/1,868` (95.40%) model-task node, 86 잔여, phase `offline_reduce`, current node `REDUCE-fa8c20e31174bf0b48b2`를 기록한다. 입력 record counter `823,279/823,279`는 record 회계이지 DAG closure가 아니다. 15:48:42 KST 확인에서 Python PID `76004`와 그 하위 Codex CLI 요청 PID `76636`이 살아 있었다. 재개 전 마지막 terminal read-only DB 대조(02:22 KST)는 `1,406/1,868`이며, writer가 살아 있는 동안 DB/WAL에는 접근하지 않는다. 자원 표본은 Python private memory 약 3.98 GiB, CPU 256.75초, 사용 가능 RAM 18.43 GiB, C: 여유 230.75 GiB다. 단일 표본으로 누수 여부를 단정하지 말고, 추세상 지속적인 비정상 증가나 고갈이 있을 때만 조치한다. Compiler worktree는 `codex/v5-gpt61-high-offline` branch, HEAD `8b1a2f9`, upstream `origin/codex/v5-gpt61-high-offline`이며 recovery code quality gate는 통과했다. 다음 단계는 이 writer/session의 완료를 관찰하는 것이지 새 compile을 시작하는 것이 아니다.

- 최신 실패 노드: `REDUCE-fb92a7fe3de830942ca1`, category `market_memory`, level 2, 고정 plan상 child 10개. Checkpoint `LLMCKPT-162b4798faed7835.json`; trace `TRACE-c5afa4477fb8.json` in `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b\runs\traces`. Provider/model은 Codex OAuth `gpt-6.1-sol/high`; 01:19:47~01:28:08 KST 호출 trace는 `retries=1`을 기록한다. Trace에 원시 invalid output은 없다. 오류만 검증 근거로 삼고 보이지 않는 응답을 분석했다고 주장하지 않는다.
- 정확한 validation failure는 `semantic reduce output exceeds 12000-byte contract`. Prompt SHA-256 `5556eddc7b5df086533efa8a2aed9b9302da17ee6bd3bc86860d50a179207e85`, UTF-8 97,742 bytes. 원본 checkpoint SHA-256 `30471E4095FFF3D6ADB35EA9B697400F097887149726ADE95996937D9D755AB6`; trace SHA-256 `0B9E304BDE54F20A71A7E105BD291FBBF15D5AA43DB78C1E0BD7B3BFC304E10A`. 두 파일은 원본 그대로 보존하고 수정·삭제하지 않는다.
- Size-error recovery는 code commit `8b1a2f9` (`origin/codex/v5-gpt61-high-offline`)에 구현되어 push됐다. Contract 한도를 contract module의 단일 `12,000` UTF-8-byte 상수로 두고, `_reduce_node`/`_reduce_world`가 **정확히 해당 validation error만** bounded size-repair purpose로 최대 한 번 재시도한다. Output을 10,000 bytes 이하로 compact하게 작성하되 strict 12,000-byte validator를 그대로 적용한다. Child identity/order와 citation allowlist 검증은 그대로며, raw rejected output이 없으므로 원본 full prompt/payload에서 완전한 replacement를 만든다.
- Resume 시 trace helper는 prompt SHA/size, response model, purpose, model config, metadata가 모두 맞는 기존 `status=error` checkpoint만 읽는다. 이전 original-purpose checkpoint가 정확한 size-error면 실패한 원본 호출을 반복하지 않고 `.size_repair.v1.<node_id>` purpose로 바로 넘어간다. 해당 recovery purpose의 matching error checkpoint가 이미 있으면 재호출 없이 fail closed한다. 다른 validation error는 기존 동작으로 전달된다. Base `_reduce_prompt`, schema, 1,868-node plan/topology, source identity는 변경하지 않았다.
- Compiler worktree quality gates: Ruff `PASS`; Mypy `PASS` (139 source files); full pytest `PASS` (`1,969 passed`, 1,631 warnings, 336.45 seconds). 회귀 테스트 5개가 category reducer, world reducer, exact prompt identity 재사용, 재개 시 recovery 중복 금지, 비-size 오류 no-retry를 확인했다. Code commit `8b1a2f9`가 remote branch와 일치함을 확인했다.
- 실행 경로 주의: PowerShell에서 compiler worktree cwd만으로 `python -m news_scalping_lab.cli`를 실행하면 현재 machine의 editable install이 root repo package를 선택할 수 있다. `--continue-after-map-plan`이 있는 pushed compiler CLI를 확실히 사용하도록 invocation 전에 `PYTHONPATH`를 compiler worktree의 `src`로 지정한다. Pytest는 `pyproject.toml`의 `pythonpath=src`를 사용하므로 위 전체 gate는 compiler worktree 코드로 실행됐다.
- 고정 입력/산출 identity는 그대로다: source manifest SHA-256 `6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576`, record root `2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75`, memory snapshot `MEMIDX-1e64a1b6e6ba7b07b799`, 1,868-node DAG, target DB 경로, checkpoint lineage, Codex OAuth `gpt-6.1-sol/high`, concurrency 4를 유지한다. Exact resume command는 다음 실행 지시에 있으며, compiler worktree `src`를 반드시 `PYTHONPATH`로 지정한다.
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
- For a deterministic validator failure, reconstruct the exact node prompt, allowed evidence IDs, trace, checkpoint, and persisted DB state. Do not claim an unavailable raw provider response was inspected. For the current size failure, the raw invalid response is absent from the trace/checkpoint, and its bounded recovery is already implemented and pushed as `8b1a2f9`; do not implement it again or replay the failed original-purpose call. Never weaken the 12,000-byte/schema/citation/coverage validation or silently drop evidence. Change code only for a separately evidenced defect, then run `python -m ruff check .`, `python -m mypy src/news_scalping_lab`, and `python -m pytest` in the compiler worktree before pushing and resuming the same build identity.

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

아래 요청으로 이 문서의 목표를 실행한다. 이 문서 갱신 스냅샷은 2026-10-06 15:48 KST이며 당시 같은 compile writer가 살아 있었다. `1,782/1,868`은 15:43:43 KST ledger의 값이지 이후 실행 시점의 live 값이 아니다. 매 실행은 process ancestry와 최신 ledger부터 다시 확인한다. writer가 살아 있으면 같은 작업을 관찰·기다리며 아래 build command를 실행하지 않는다.

> 저장소 `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`, 이 goal, recovery report와 daily 제품 계약을 읽고 이 문서의 목표를 끝까지 이어서 수행해. 제품 목표는 새 GPT 가중치 학습이 아니라, 이미 모은 연구를 한 번 offline compile한 cutoff-safe brain package와 장전 CSV를 첫 GPT 판단 요청에 함께 넣어 섹터·종목 후보 및 근거를 내는 daily 흐름을 완성·검증하는 것이다. `52,644` semantic capsule/unit을 연도 수, 개별 원문 요약 수, 또는 GPT의 직접 노출 수로 부르지 말고 서로 다른 coverage 및 lineage 지표로 감사해. daily BLIND는 `CSV_MEMORY_ONLY_STRICT`, 한 번의 logical `final_market_decision`(+구조화 복구 최대 1회), no web/D-day/outcome/post-cutoff, raw corpus/record/cluster/lane 비례 LLM fan-out 금지다.
>
> 먼저 compile ID `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 현재 process ancestry와 safe ledger를 확인한다. 이 문서 스냅샷에서는 02:37 KST부터 시작한 동일 writer가 살아 있었고 최신 ledger는 `1,782/1,868`, 86 remaining, `offline_reduce`였다. Python PID `76004`와 Codex CLI 요청 PID `76636`은 스냅샷 시점의 참고값일 뿐이며 live 상태의 증거로 재사용하지 않는다. matching writer가 살아 있으면 그 process/session 하나만 이어서 관찰하고 기다린다. duplicate build, 다른 compile, DB/WAL 열기·hash·audit은 금지다. writer가 종료했을 때만 exit/ancestry/ledger/WAL 상태를 확정하고 WAL activity가 settled된 후 DB를 read-only reconciliation한다. Code recovery `8b1a2f9`는 이미 pushed됐고 compiler worktree는 `origin/codex/v5-gpt61-high-offline`과 동기화되어 있으며 Ruff/Mypy/full pytest (`1,969 passed`)가 통과했다. `REDUCE-fb92a7fe3de830942ca1`의 matching size-error checkpoint는 이미 bounded `.size_repair.v1.<node_id>` 경로로 처리되므로 실패한 original prompt 호출을 반복하지 않는다. Size fix를 다시 구현하지 않는다. source manifest, record root, fixed plan/target/checkpoint identity, Codex OAuth `gpt-6.1-sol/high`, concurrency 4는 바꾸지 않는다.
>
> 같은 writer가 없고, process 종료와 WAL settle을 확인한 뒤 incomplete 상태이며 모든 source/plan/target/checkpoint identity가 일치할 때에만 아래 command로 동일 build를 재개한다. Worktree branch/HEAD/upstream을 검증하고 `PYTHONPATH`가 worktree의 `src`인지 확인한다. Machine editable install은 cwd만으로 다른 root repo package를 선택할 수 있다. Codex OAuth status와 `NSLAB_LLM_PROVIDER=codex-oauth`, `NSLAB_LLM_MODEL=NSLAB_CODEX_MODEL=gpt-6.1-sol`, `NSLAB_CODEX_REASONING_EFFORT=high`, `NSLAB_MAX_CONCURRENCY=4`를 검증한다. Source/manifest, record root, compile ID, 1,868-node plan/topology, target DB, checkpoint directory와 compatible `gpt-5.6-sol/xhigh` identity를 유지한다. Import, repair, embedding, planning, map 또는 이미 유효한 output을 다시 하지 않는다.
>
> ```powershell
> Set-Location 'C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b'
> $env:PYTHONPATH = (Resolve-Path '.\src').Path
> python -c "import news_scalping_lab.cli as cli; print(cli.__file__)"
> $env:NSLAB_LLM_PROVIDER = 'codex-oauth'
> $env:NSLAB_LLM_MODEL = 'gpt-6.1-sol'
> $env:NSLAB_CODEX_MODEL = 'gpt-6.1-sol'
> $env:NSLAB_CODEX_REASONING_EFFORT = 'high'
> $env:NSLAB_MAX_CONCURRENCY = '4'
> python -m news_scalping_lab.cli brain build-offline `
>   --source-project 'C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project' `
>   --checkpoint-dir 'C:\Users\eorb9\projects\news_bot\runs\checkpoints\llm' `
>   --compatible-checkpoint-model 'gpt-5.6-sol/xhigh' `
>   --expected-manifest-sha256 '6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576' `
>   --continue-after-map-plan
> ```
>
> Import, repair, embedding, planner, map-only/fresh map, and valid reducer outputs are already done. The continuation can reconstruct local geometry and reuse exact checkpoint hits, but must not invoke new import, embedding, planner, or map-only work. Report persisted DAG closure, provider-fresh outputs, checkpoint hits/local carry/failures separately and do not invent an ETA. If original source or plan identity differs, stop before any build.
>
> 같은 DAG가 terminal `1,868/1,868`이 되면 persisted rows와 source/plan/checkpoint/citation lineage를 대조한 뒤 supported immutable Offline Semantic Brain V2 package를 생성·standalone/deep 검증한다. Real embedding/HNSW, provenance, coverage, payload exposure, citations, 연도·거래일 공백을 각각 감사한다. `823,279` records, `52,644` semantic units/capsules, `1,868` DAG nodes는 서로 다른 수치다. record scan 완료나 DAG closure를 10년치 의미 노출/예측 성능/backtest/fine-tuning 성공이라고 부르지 않는다. 완료된 import·repair·embedding·map·고정 topology는 증거 있는 결함이 없는 한 반복하지 않는다.
>
> build cutoff `2026-08-21T18:52:07.302105+09:00` 이후의 cutoff-safe 실제 pre-open CSV를 다시 찾는다. 없으면 만들거나 trim하지 말고 `BLOCKED_INPUT_REQUIRED`로 사용자에게 요청한다. 있으면 audited package와 production `analyze-daily`로 daily smoke를 실행하고 첫 요청의 brain+CSV, cutoff/no-web 경계, citations, provenance/context manifest, 호출 수와 실제 latency를 확인한다. 이는 기능 smoke이지 predictive quality 증명이 아니다. registered bounded blind gate와 sealed inputs가 같은 deployable architecture에 없으면 `NOT_RUN_GATE_MISSING`, predictive quality `UNAPPROVED`, production `HOLD`다. `QPRED-704f15cde6e4152b6931` 379-pack 및 모든 invalidated ancestry는 재개·평가·비교·promotion·cache 입력 금지. 유효 quality gate, 사용자 승인, release binding과 rollback 증거 없이는 production을 활성화하지 않는다.
>
> 종료할 때 compile, package audit, 실제 CSV smoke, formal quality, production activation 상태를 분리해 보고해. goal/recovery 문서와 Downloads 사본을 동기화하고 관련 문서·코드만 처리한다. 연구 원료, DB/WAL, checkpoint, 큰 산출물과 기존 untracked/run output은 보존한다. 이 작업의 최종 산출은 기존 GPT가 사용하는 검증 brain package이지 새로 fine-tune한 GPT 모델이 아니다.
