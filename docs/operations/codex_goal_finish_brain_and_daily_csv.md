# Goal: 일회성 두뇌 컴파일 완료 및 장전 CSV 판단 흐름 검증

문서 갱신: 2026-10-05 16:37 KST
현재 handoff 기준: 동일 compile `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`은 citation fix commit `7bb1b40` 이후 2026-10-05 15:58:49 KST 재개되어 계속 실행 중이다. executor session `48993` / Python PID `20864`는 실행 시 다시 확인할 단서일 뿐이다. progress 원장은 `2026-10-05T16:36:50.012612+09:00`, `offline_reduce`, `881/1,868` closed, `987` remaining, current pointer `REDUCE-ea8fef5106c2916a5077`다. 이는 fixed-DAG closure만 뜻한다. writer와 WAL이 live인 동안 target DB/WAL을 열지 않는다. 재개 직전 terminal/read-only audit는 `reduce_nodes=812`, failed node row 0, capsules 52,644, assignment rows 823,279로 progress와 일치했다.

최신 실패 `REDUCE-e6c11b4aee1b4a8e62f1` / `TRACE-ee9c6faf94ed` / `LLMCKPT-45d46e52818e8fba`를 production builder로 재구성했다. Claim 3 citation 원문은 `CAP-cd066b07b85b5b40551a /”`; 다섯 child leaf의 정확한 allowed evidence set은 19개이며 base ID가 허용 집합에 있었다. 재생성 prompt는 checkpoint의 142,133자 및 SHA-256 `77dd0155b66f074f2dfeb90b6f54c4f6a2dab2aea47c8c241f7fce6aff572a33`와 일치했다. 접미부는 정확히 space + slash + U+201D였다. 새 normalizer는 해당 exact suffix만 allowed ID에 붙었을 때 citation field에서만 제거하고 원문·normalized ID·rule을 감사 row에 보존한다. 실제 checkpoint 응답을 새 코드에 read-only로 넣었을 때 네 claim이 검증되고 단 한 citation만 정규화되는 것을 확인했다.

Compiler 수정은 `7bb1b40`으로 원격 `codex/v5-gpt61-high-offline`에 push됐다. 정확한 suffix positive/negative 테스트 27개, Ruff PASS, Mypy 139 source files PASS, full pytest `1,960 passed / 1,559 warnings / 318.48s`다. 동일 source/import/embedding/map/plan/target/checkpoint identity로 재개했다. 재개 후 직전 관측에서 Python private memory는 약 3.67 GiB였고 동시성은 4였다. 현재 자원 상태는 실행 시 재관측한다. root worktree의 기존 untracked 진단/run 산출물은 보존한다. 상세 forensic 자료는 `diagnostics/offline_brain_recovery_20261004.md`에 있다. 이 handoff가 문서 안의 오래된 진행 스냅샷보다 우선한다.

이 goal은 이미 끝난 import·embedding·map·planner를 반복하지 않고 고정 DAG의 나머지를 완료한 뒤 실제 brain package와 장전 CSV 경로를 검증하는 유한한 작업이다. package 생성만으로 GPT 가중치 학습, backtest 통과 또는 production 승인을 주장하지 않는다.

## 최종 목표

이미 수집·repair·import·실임베딩·기초 분류한 연구자료를 다시 처음부터 처리하지 않고, 남은 고정 오프라인 합성을 이어서 검증된 Offline Semantic Brain V2 패키지로 만든다. 그 패키지를 실제 장전 CSV 흐름에 연결해, CSV와 cutoff-safe 두뇌 지식이 첫 GPT 판단 요청부터 함께 제공되고 섹터·종목 후보, 근거, 불확실성, 출처가 나오는지 확인한다.

이것은 GPT 기반 모델의 가중치를 새로 학습하는 일이 아니다. 기존 GPT 추론 모델이 사용할 수 있도록 연구에서 cutoff-safe 세계·카테고리 지식, 연결된 claims, 검색 인덱스, provenance를 한 번 만들어 고정하는 작업이다. 두뇌가 완성된 뒤 매일 원자료를 다시 해석하거나 record마다 LLM을 호출하지 않는다.

## 단위와 완료 의미

- 823,279 records는 고정 input manifest의 record accounting 수치다. 모든 record의 의미를 GPT가 직접 읽었다는 뜻은 아니다.
- 52,644 semantic units는 컴파일러가 묶은 semantic 입력 단위 수다. 연도 수, 요약 수, LLM 호출 수, 완전한 의미 소화량이 아니다.
- 1,868 DAG nodes는 현재 고정 offline compile plan의 유한한 model-task 수다. 이 goal의 컴파일 진행률 분모는 이것 하나뿐이다. `closed / 1,868`만 보고한다.
- 날짜 범위 2018-01-03~2026-06-19는 약 8년 반의 달력 범위다. 10년 전체 거래일·시장 구간을 채웠다고 추정하지 말고 날짜/연도/거래일 coverage audit로 확인한다.
- 여기서 말하는 두뇌는 GPT 가중치를 튜닝한 새 모델이 아니라, GPT가 매일 함께 읽을 수 있도록 한 번 구축·검증해 보존하는 brain package와 검색 인덱스다.
- 기술적 목표는 고정 compile/package와 intended daily CSV flow를 검증하는 것이다. 정식 quality gate가 통과하기 전에는 production 활성화를 뜻하지 않는다.

## 제품 계약

장전 CSV 처리의 의도된 흐름은 다음 한 가지다.

1. 지정된 거래일과 cutoff를 검증한다.
2. 이미 빌드된 두뇌와 실임베딩 검색 인덱스, cutoff-safe 현재 뉴스 관련 기억을 첫 판단 요청 전에 읽는다.
3. 현재 CSV와 위 지식을 함께 넣어 final market decision을 한 번 요청한다.
4. 구조화 출력이 잘못된 경우에만 repair 요청을 최대 한 번 허용한다.
5. 근거가 부족하면 이를 밝히고, 허위 확신이나 근거 없는 종목을 만들지 않는다.

매일 경로에는 별도의 brain-free 1차 뉴스 해석 호출이 없다. raw corpus 전체 재해석, record·cluster·lane별 LLM fan-out, 장전 general web search도 없다. 생산 BLIND 경로는 CSV_MEMORY_ONLY_STRICT이며 D-day 가격, cutoff 이후 정보, outcome 정보는 접근하지 않는다. 모든 결과에는 provenance와 context manifest가 있어야 한다.

## 사용자 및 외부 리뷰어에게 전달할 문제 정의

사용자의 불만은 단순히 작업이 느리다는 것만이 아니다. 오랜 실행 동안 record 수, semantic unit 수, LLM 호출, 고정 DAG 완료 수가 서로 다른 분모인데도 완료율처럼 혼용되어, 무슨 유한한 작업을 하는지, 언제 끝나는지, 끝나면 장전 CSV에서 무엇이 실제로 달라지는지 알기 어려웠다. 9일간 실행한 뒤에도 1.5%, 13%처럼 기준이 바뀐 수치가 제시되고, 완료된 단계를 다시 할 위험까지 생겼다.

따라서 이 goal은 다음을 강제한다.

- 진행률의 유일한 분모는 아래 고정 plan의 1,868개 model-task DAG node다.
- 매 보고는 closed/1,868, 남은 node 수, 새 provider 성공, 정확히 일치하는 checkpoint 재사용, 로컬 carry/validation, 실패, 현재 실행 중을 분리한다.
- record accounting 완료와 두뇌 합성 완료를 혼동하지 않는다.
- 근거 없는 ETA를 만들지 않는다. 측정 가능한 최근 처리량이 생기면 가정과 범위를 함께 제시한다.
- 각 단계가 최종 장전 CSV 제품에서 무엇을 가능하게 하는지 설명하고, 끝없는 개선 작업을 추가하지 않는다.
- 완료됐다고 말하기 전에 패키지와 실제 일일 경로의 증거를 만든다.

## 고정 입력과 빌드 identity

- 원본 연구 프로젝트: C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project
- source manifest SHA-256: 6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576
- record root: 2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75
- 입력 accounting: 823,279 records
- 기존 memory snapshot: MEMIDX-1e64a1b6e6ba7b07b799
- 기존 semantic units: 52,644
- compile ID: OFFLINE-COMPILE-0dd9198ac9ef79215ab1
- fixed plan: 1,868 nodes (reducer 1,858, category review 9, world root 1)
- compiler worktree: C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b
- target DB: C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b\brain\.work\OFFLINE-COMPILE-0dd9198ac9ef79215ab1\semantic_capsule_index.duckdb
- checkpoint directory: C:\Users\eorb9\projects\news_bot\runs\checkpoints\llm
- 새 합성 호출의 지정 모델: Codex CLI OAuth, gpt-6.1-sol/high
- 호환 가능한 기존 checkpoint identity: gpt-5.6-sol/xhigh. 새 출력과 재사용 output의 실제 model/provider는 trace별로 구분한다. 모든 결과를 6.1 출력이라고 부르지 않는다.

823,279는 이 입력 manifest에 대한 record accounting 수치이지 각 record를 GPT가 직접 읽었다거나 최종 두뇌가 전부 의미적으로 소화했다는 증거가 아니다. 52,644는 semantic grouping unit 수이지 52,644개의 LLM 작성 요약이나 연도별 요약 수가 아니다. 기록된 날짜 범위는 2018-01-03부터 2026-06-19까지다. 이는 달력상 약 8년 반 범위이지 “10년치 전체 거래일 데이터가 빠짐없이 있다”는 증명이 아니다. 날짜·연도·거래일별 실제 coverage와 gap은 최종 audit에서 별도로 보고한다.

## 현재 검증된 상태

### 권위 있는 현재 상태

2026-10-05 15:58:49 KST에 동일 compile을 재개했다. 현재 session `48993` / Python PID `20864`가 live이며 WAL이 존재한다. 따라서 진행 중 DB에는 접근하지 않는다. 재개 직전 terminal/read-only DB 대조는 812 persisted node와 failed node row 0을 확인했다.

- 고정 compile identity: `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`; source manifest, record root, worktree, target DB, plan/topology, checkpoint 경로를 그대로 사용한다.
- 최신 ledger: `881/1,868` closed, `987` remaining (47.11% fixed-DAG progress), phase `offline_reduce`, current pointer `REDUCE-ea8fef5106c2916a5077`, updated `2026-10-05T16:36:50.012612+09:00`.
- source `processed_record_count=823279`와 `record_progress_ratio=1.0`은 input accounting일 뿐 semantic compile이나 brain 완성을 뜻하지 않는다. Daily brain decision, package, blind evaluation은 아직 완료되지 않았다.
- 현재 writer는 Python PID `20864`, executor session `48993`이다. 살아 있는 동안 새 writer를 시작하지 않는다. DAG 결과가 terminal일 때만 process/session, progress, exit code, WAL과 DB를 대조한다.
- 최신 citation incident는 allowed set·prompt·checkpoint까지 증명했고 commit `7bb1b40`으로 수정·push했다. 실행 중에는 새 runtime trace를 확인할 수 있지만 DB row나 WAL을 읽지 않는다. failed node의 persisted closure 여부와 checkpoint-hit trace는 terminal audit에서 확정한다.
- 직전 map trace 7,513개는 모두 `checkpoint_hit`, fresh map provider 호출 0건이었다. Source import와 embedding은 반복하지 않았다. 15:58 이후 이 compile invocation의 전체 trace를 terminal 후 분류해 map/reducer fresh, checkpoint hit, local carry를 node closure와 별개로 보고한다.

#### 이전 장애: ` ... a` 출력 suffix

- Failed node: `REDUCE-8becf902cac9db24b9ee`; purpose는 checkpoint와 동일하다. Checkpoint `LLMCKPT-8c7cc93bedf7c776`, status `ok`, model `gpt-6.1-sol/high` via Codex OAuth, input SHA-256 `24fe52fd814482b1b8a3de4cdf0061d5e2a802d6242e737267e7adc9ce1a3332`, output SHA-256 `bcaed98cc9056d7ad2cb4ac4d3bc5089004524e524570708efaa9903807fbae8`; checkpoint file SHA-256 `a4159dc8003e597fe508801ffcfb709e5c972048c9e4d6bb3917786ef501f5ef`.
- Three exact children are leaves `LEAF-BUCKET-b44f52d4219c942b7b20`, `LEAF-BUCKET-1b292e6eed64c24ce472`, `LEAF-BUCKET-ece1f9d19774454d1207` with 12, 11, and 12 capsules. Production `_capsule_leaf_nodes`, `_leaf_reduce_evidence_ids`, and `_reduce_prompt` rebuilt the allowed set and prompt. Prompt SHA-256 `f2294f470dbd7974068083dc549b0a7596a1bb0d39c2790b12f088a74f31b904`, chars 145,137, UTF-8 bytes 165,498 all match checkpoint input; canonical output SHA also matches checkpoint.
- The 12 allowed evidence IDs include `CAP-e3f1c1efc61d5c29ca67` from the second child leaf. Raw claim 3 supporting citation was exactly `CAP-e3f1c1efc61d5c29ca67 ... a`. The failed node had no persisted row after the failed attempt.
- Code now strips only exact suffix ` ... a` and only when the exact prefix belongs to that reducer's allowed set; raw value, normalized ID, and rule are retained in citation normalization audit. Rejected cases include unallowed base ID, ` ... b`, ` ... ab`, and extra appended IDs.
- Compiler commit `2c05062` was pushed to `origin/codex/v5-gpt61-high-offline`. Focused tests: 19 passed. Ruff PASS, Mypy PASS for 139 source files, full pytest PASS: 1,946 passed, 1,433 warnings, 295.81 seconds.
- 같은 compile은 493에서 재개되어 501 closure를 거쳐 602까지 진행했다. 이후 아래 U+2009 장애로 다시 terminal 됐으므로 602는 더 이상 현재 값이 아니다.

#### 해결 및 재개 확인: U+2009 citation 공백

- 최신 failed node: `REDUCE-1438e4a54b2849782faa`; trace `TRACE-5878fdd5cd0e`; checkpoint `LLMCKPT-a38dbebd3e1bc6d3`. Provider/model은 Codex OAuth `gpt-6.1-sol/high`, compiler v6, checkpoint status `ok`다.
- checkpoint input SHA-256 `474eaf34b92977b7d5292e90d6751767ecbe69719f110c8b4dfe9be39a6bb827`, exact prompt SHA-256 `1c9b5308d7554e642d65ba271e8bfb7096380bc3d77ab4a13d96557fb487cdb8` (148,337 chars; 159,871 UTF-8 bytes), canonical output SHA-256 `481e8886a0772aac73c27d25f1906bc63c3b2ff36e662b5b6beada4263107d29`다. Regenerated production prompt, checkpoint input/output hashes가 일치한다.
- Child leaves는 `LEAF-BUCKET-3bd1947233bb6403ece4`, `LEAF-BUCKET-e1563e5f8ef8cb3c1422`, `LEAF-BUCKET-1a9554d9002ac72edd87`이며 각각 capsule 14, 9, 14개다. Allowed evidence는 12개이고 `CAP-bd21b6945c38aebbc143`가 그 집합에 포함된다. 첫 claim의 raw citation은 이 allowed ID 뒤에 U+2009 하나만 붙은 값이다.
- 실패 당시 progress는 683/1,868이었고 failed node row가 없었다. 재개에서 `TRACE-5119051a50b5`의 status가 `checkpoint_hit`으로 기록되고 progress가 684로 증가해 수정 경로가 실행상 통과했다. 실제 reducer row 대조는 terminal audit 때 한다.
- U+2009 수정은 `src/news_scalping_lab/brain/offline_v2.py`, `src/news_scalping_lab/contracts/offline_brain.py`, `tests/unit/test_offline_brain_v2.py`에 한정되며 commit `35fe4e5`로 push됐다. 허용 prefix와 exact one-character suffix만 정규화하고 audit provenance를 보존한다. `...`, `... a`, U+200A, 근사 일치로 규칙을 넓히지 않는다.
- 수정 상태에서 `ruff check .` PASS, `mypy src/news_scalping_lab` PASS (139 source files), `pytest` PASS (1,949 passed, 1,460 warnings, 305.76 sec)다.
- 최초 재개 전에는 writer와 WAL이 없었으며, 7,513개 map usage row의 checkpoint 파일이 전부 있고 이전 fresh 15개 checkpoint도 status `ok`임을 확인했다. 동일 source, manifest, compile ID, sealed plan, DB, checkpoint directory, model identity로 재개했고 새 map trace 표본은 checkpoint hit이다. source import와 embedding은 다시 하지 않았다. 전체 map trace에서 fresh provider result가 발견되면 cache reuse로 간주하지 말고 별도 보고한다.

#### 두 번째 resume 장애: 정확히 두 개가 붙은 citation

- Failed node `REDUCE-f35170f769e30e97e97f` is `failure_modes`, level 0; trace `TRACE-a42a9b881ff6`, checkpoint `LLMCKPT-b09cc03fd85f6707`, Codex OAuth `gpt-6.1-sol/high`, compiler v6.
- Prompt SHA-256 `463b7ef02632bba07dc53f8919264d8fb2da31c6600275b9f88ea9380b3da449`; input SHA-256 `d762de2fa644d7b8d5a46444b08c473ad55105051667133a7e9d5f924494eab1`; output SHA-256 `218209ddf746b2ace7f9475bae861a63887e58ed5f0ca4421a90fc510fc17408`. Read-only reconstruction through production `_capsule_leaf_nodes`, `_leaf_reduce_evidence_ids`, `_leaf_coverage_root`, and `_reduce_prompt` exactly matched trace and checkpoint.
- Child leaves: `LEAF-BUCKET-5449c672da840993b9bd` (16 capsules), `LEAF-BUCKET-cfeb3a1f307c7feb4a8b` (14), `LEAF-BUCKET-fbf2a625fe8003805826` (12). The 12 allowed evidence IDs were:

```text
CAP-0b6eae5cd1528ab5940e  CAP-2212360c11e0ea9b2b4e
CAP-31656f834f0ed4add75e  CAP-4997675e19a4062f1e99
CAP-52003d02125a69790db1  CAP-780e809ff61b90e457f4
CAP-a4b360d763261d07301a  CAP-b000b78ab3dea157ffeb
CAP-bd063275ed97a110cfdb  CAP-d6e4217eb7fcfce035c6
CAP-e3833fcde16d04a25052  CAP-f67eeb004df915884ceb
```

- Claim 1 raw supporting citation was exactly `CAP-52003d02125a69790db1, CAP-bd063275ed97a110cfdb`; each ID is a member of the allowed set. Validation failed because the entire string was checked as one capsule ID.
- Compiler commit `7a693dc` splits only an exact single `", "` into two IDs when both independently belong to the current allowed set. Audit preserves the original value and records each normalized ID. Regression tests reject unallowed IDs, extra IDs, and spacing/delimiter variants. Full gates: Ruff PASS; Mypy PASS (139 source files); pytest PASS (1,955 passed, 1,514 warnings, 298.27 seconds).
- At 14:28 KST no writer or WAL remained. Read-only `reduce_nodes=725`, matching progress; failed node row count is 0. Resume the same compile ID/source/manifest/plan/target/checkpoint directory with `gpt-6.1-sol/high` and compatible `gpt-5.6-sol/xhigh`. Do not reimport or reembed. The previous resumed run's 7,513 map traces were all checkpoint hits; check the next run's trace evidence again.

### 복구 및 과거 진행 스냅샷

아래 09:54 이전 수치는 모두 과거 기록이다. 최신 진행 상태는 위 권위 있는 snapshot으로만 보고한다.

2026-10-05 09:54 KST 기준 당시 기록:

- Latest: session 14734 / Python PID 60928, offline_reduce, 475/1,868, remaining 1,393, current node REDUCE-10fb03cf3a896c1c7adc at 2026-10-05T09:53:46.616898+09:00. This is 25.4% of the fixed DAG.
- Since resume, 63 new checkpoint files have status ok with gpt-6.1-sol/high identity. Prompt-token estimates total 8,894,491 and completion-token estimates total 121,922. Progress moved from 411 to 475 (+64), comprising the 63 new outputs and one verified reducer checkpoint hit.
- Observed rate from 09:23:40 to 09:53:46 is 64 closures / 30m06s, about 2.13 nodes/min. A straight-line projection for 1,393 remaining is about 10h55m only if the aggregate rate and average node cost persist. This is low confidence, not a promised finish time.
- Resource sample: Python private memory about 3.8 GB, free RAM about 20 GB, C: free about 233 GB.
- The 09:44 bullets below are an earlier snapshot, retained as history.

- Current snapshot: session 14734 / Python PID 60928, offline_reduce, 453/1,868, remaining 1,415, current node REDUCE-418d22a10f0a08465a7f at 2026-10-05T09:44:18.745938+09:00. This is about 24.3% of the fixed DAG only.
- Since resume, 41 new GPT-6.1-sol/high checkpoint files have status ok; estimated totals are 5,885,006 prompt tokens and 79,175 completion tokens. Progress advanced 411 to 453 (+42): 41 fresh outputs and one exact reducer checkpoint hit are accounted for.
- Observed throughput over 09:23:40–09:44:18 was 42 closed nodes in 20m38s, about 2.04 nodes/min. Straight-line arithmetic for 1,415 remaining is about 11h35m if this aggregate rate and similar node costs continue. This is a low-confidence projection, not a completion promise; reducer prompt sizes and later review/root nodes vary.
- Latest resource sample: Python private memory about 3.8 GB, available RAM about 19.6 GB, C: free about 233 GB.
- The 09:39 bullets below preserve the earlier snapshot and are historical.

- 현재 권위 있는 상태는 session 14734 / Python PID 60928이다. 2026-10-05T09:38:54.290550+09:00 업데이트에서 phase offline_reduce, closure 439/1,868, remaining 1,429, current node REDUCE-118b768511a5e95ecd1e다. 23.5%는 DAG ledger 진행률이지 record coverage나 의미 이해도가 아니다.
- resume 이후 새 checkpoint 파일 27개가 생겼고 전부 status ok, gpt-6.1-sol/high identity다. prompt token estimate 합계 3,854,307, completion token estimate 합계 53,157이다. ledger는 411에서 439로 28개 증가했다. provider output status와 node validation closure를 동일시하지 말고 terminal 때 정확히 대조한다.
- 실패 reducer는 기존 checkpoint_hit trace TRACE-5208c051b465로 재사용됐다. map 단계도 resume 중 checkpoint_hit trace가 관측됐고 fresh/reuse 전체 집계는 terminal audit에서 확인한다.
- Python private memory 약 3.8GB, working set 약 3.1GB, free RAM 약 20GB, C: free 약 233GB다. representative/distribution 복원 중 일시적으로 약 5.8GB까지 올랐지만 reduce phase에서 약 3.8GB로 내려왔다.
- 아래의 09:19/08:59 상태 항목은 복구 이력이며 현재 process/progress 상태가 아니다.

- 이전 build session 41212는 child 정리 후 exit code 1로 terminal 처리됐다. 09:19 기준 compile ID/worktree/target DB를 쓰는 writer는 없고 `.duckdb.wal`도 없다. 새 실행을 시작하기 전에도 process tree를 재검사한다.
- `progress.json`의 마지막 값은 `2026-10-05T08:46:06.272609+09:00`이다. phase는 `offline_reduce`, ledger closure는 411/1,868, remaining은 1,457, last current node는 `REDUCE-d409b3dd3ed7424b0ade`다. 이는 약 22.0%의 DAG ledger 진행이지 record coverage, LLM 노출률, 두뇌 이해도, ETA가 아니다.
- terminal 이후 read-only DuckDB 대조에서 `reduce_nodes`는 411행으로 progress ledger와 일치했다. metadata의 compile ID, source manifest SHA, record root/count, plan SHA/topology SHA, semantic unit count가 이 문서의 고정 identity와 일치했다. embedding identity는 실제 sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 provider 기록이다. 전체 lineage/package audit는 build 종료 후 다시 한다.
- 두 번째 실패 node REDUCE-9a0ce8212e283ebc7228의 fixed plan child는 LEAF-BUCKET-60f20ae7ff8c050d27bc, LEAF-BUCKET-2c50a296d79a7d9bdb89, LEAF-BUCKET-b2f4c04ccc8ceab9b322다. 저장 capsule에서 실제 leaf builder를 재현했고 각 leaf가 각각 14, 13, 10 capsules로 정확히 일치했다. _leaf_reduce_evidence_ids의 union은 12개이며 CAP-57902ba14ee7543a0f12는 두 번째 leaf의 허용 목록에 실제 존재한다. 따라서 이 값은 잘못된 capsule ID가 아니라 정확히 " so keep going?"가 덧붙은 citation임을 입증했다.
- 이 node의 prompt 허용 ID 12개: CAP-060c8ea2994ad8599e6a, CAP-23747417453605988909, CAP-39161adf85c0a3af2fe4, CAP-56427025b62e8a7c1cb0, CAP-57902ba14ee7543a0f12, CAP-6bbfc348514c34b9c382, CAP-971da1fff580e86738e5, CAP-c8c56bda16acfa2b90b4, CAP-d26a5e46cce6861dc698, CAP-e1b59ba9e0c2d69bd5a0, CAP-e6bf3399bc316d5aba2e, CAP-f775e3b8e7ba16f893cf.
- 직전 오류 trace는 TRACE-c4e74376ecbc, checkpoint는 LLMCKPT-1ec9f0633e3c15f0이다. 목적은 REDUCE-0703331bc02f14ce3512, input SHA-256은 4dc293455d62a60a7bed27a064252a57ba3f3a4f1d63af7b0e21285d16acf543, output SHA-256은 75c9beb1f435019cd6d09a62ba38c6bd770c4d7e0e5586cb60a19fd9a895dd98이다. trace와 checkpoint hash가 일치하고 모델 identity는 gpt-6.1-sol/high다.
- 실패 citation은 CAP-4a7e8b51c6643343aeec 뒤에 U+2019 RIGHT SINGLE QUOTATION MARK가 붙은 값이었다. 컴파일러의 실제 leaf builder로 허용 evidence 집합 8개를 재구성했고, suffix를 뺀 정확한 ID가 집합에 있음을 확인했다. 잘못된 ID는 허용하지 않는다.
- 좁은 U+2019 suffix normalization, 별도 감사 rule, 실패 진단의 claim index/field/escaped citation, 허용되지 않은 ID 및 유사 구두점 거부 테스트를 추가했다. compiler branch commit fa81f2c가 push됐고 Ruff PASS, Mypy 139 files PASS, pytest 1,937 passed다.
- 두 번째 오류의 checkpoint LLMCKPT-776f66ab880dbbb4는 gpt-6.1-sol/high, provider status ok였으나 validator 오류 때문에 node closure가 아니었다. 이 checkpoint는 지우거나 재호출하지 않고 같은 compile에서 새 validator로 재검증한다.
- 좁은 exact phrase normalization을 source, contract audit enum, 회귀 테스트에 추가했다. 허용 prefix에서만 정확한 " so keep going?"를 제거하고 raw value와 rule을 citation_normalizations에 보존한다. 허용되지 않은 prefix, 비슷한 문구, 추가 citation이 붙은 값은 거부한다. compiler branch commit 95e9257가 push됐다. focused regression 14 passed, Ruff PASS, Mypy 139 files PASS, full pytest 1,941 passed.
- 기존 root branch codex/quality-full-pr126의 release-binding 변경은 별도 commit 3fff4c4이며 당시 Ruff, Mypy(139 files), pytest(1,906 passed)가 통과했다. 이후 변경이 있으면 해당 worktree에서도 최종 gate를 다시 실행한다.
- 완성된 full-corpus V2 package, 실제 package로 실행한 장전 daily smoke, 같은 architecture의 정식 blind 평가, 최종 release binding은 아직 입증되지 않았다. Production 활성화는 HOLD다.

goal 실행 직전 반드시 위 상태를 다시 확인한다. 다른 task가 build를 재개했거나 동일 writer가 살아 있으면 새 writer를 시작하지 않는다. progress가 갱신됐으면 그 최신 증거로 본 절을 수정한다. process가 살아 있다는 사실만으로 progress 중이라 판단하지 말고 progress timestamp, stdout/stderr, Codex child의 command line/CPU/output, resource trend를 확인한다. 현재 stale snapshot을 이유로 DuckDB를 열거나 같은 compile을 병렬로 시작하지 않는다.

## 이미 끝난 단계: 다시 하지 말 것

아래 입력 작업은 동일 identity에서 이미 완료된 것으로 취급한다. 현재 error를 해결하기 위해 처음부터 반복하지 않는다.

- repair 완료 research의 production import 및 record accounting
- 실임베딩 생성과 semantic index의 기존 기반 데이터
- record assignment 및 기존 map stage/receipt
- 과거 411·725·770·812·867 closure 수치는 historical snapshot이다. 812는 이번 resume 직전 terminal DB/read-only 대조값이며, 현재 writer가 시작된 뒤의 DB는 아직 읽지 않는다. 최신 진행은 아래 권위 있는 handoff의 `881/1,868` progress ledger를 사용한다. full plan/receipt/DB/checkpoint lineage는 terminal 이후 대조한다.

새 input identity가 실제로 달라졌다는 증거와 별도 승인이 없는 한 import, embedding, map, planner, compile ID를 다시 만들지 않는다. 실패 시 이미 저장된 closure와 checkpoint를 보존한다.

## 실행 단계

### 1. 시작 전 single-writer 및 identity 확인

- 사용자가 goal 실행을 요청하면 process tree와 command line에서 같은 compile ID, worktree, target DB, checkpoint directory를 쓰는 build writer가 있는지 확인한다.
- 같은 writer가 살아 있으면 새 build를 시작하지 말고 그 세션만 관찰한다. 터미널 화면이 끊겼다는 이유만으로 중복 실행하지 않는다.
- 실행기가 terminal이면 exit code, progress ledger, plan/receipt/source SHA, DB compile metadata, WAL 존재 여부를 맞춘다.
- live writer가 있거나 WAL이 있으면 해당 DuckDB의 read-only inspection, hashing, parity scan을 하지 않는다.
- 종료 후에도 plan/source/receipt/target/checkpoint identity가 바뀌었거나 ledger와 DB가 불일치하면 재실행하지 말고 mismatch를 먼저 진단한다.

### 2. 반복 citation 오류를 정확히 진단하고 좁게 고친다

2026-10-05의 `REDUCE-e6c11b4aee1b4a8e62f1` 실패는 해결 및 검증이 끝났다. Production prompt 재구성 결과 base capsule ID는 정확한 19개 allowed evidence set에 포함됐고, prompt/checkpoint SHA도 일치했다. citation의 정확한 접미부 `space + slash + U+201D`만 citation field에서 허용 ID에 한해 정규화한다. 원문·normalized ID·적용 규칙을 audit에 남기는 수정은 compiler commit `7bb1b40`으로 push됐다. Focused tests 27개, Ruff, Mypy 139 files, full pytest 1,960 passed가 통과했고 같은 compile은 이미 재개됐다. 이 실패를 다시 조사하거나 수정하지 않는다.

새 citation validation 실패가 생긴 경우에만 해당 node의 trace/checkpoint, child topology, exact allowed evidence set, prompt/input/output hash와 Unicode code point를 read-only로 재구성한다. 증거로 base ID가 allowed임이 확인된 exact formatting artifact만 최소 범위에서 다룬다. validator를 일반 완화하거나 claim을 버리지 말고, focused positive/negative tests와 전체 gate, compiler worktree의 Korean commit/push 후 동일 compile을 재개한다. writer/WAL이 live일 때 target DB를 읽지 않으며 건강하게 진행 중인 process를 임의 종료하지 않는다.

### 3. 동일 compile을 이어서 완료

오류 수정과 gate 통과 후에만, 동일 worktree/source/manifest/compile ID/target DB/checkpoint 경로로 재개한다. 새 output은 gpt-6.1-sol/high, 정확히 호환되는 기존 gpt-5.6-sol/xhigh checkpoint만 재사용한다. import, embedding, map을 다시 실행하지 않는다. 시작 직전 상태 확인을 포함해 다음 명령을 사용한다.

~~~powershell
$worktree = 'C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b'
Set-Location $worktree
$env:NSLAB_LLM_PROVIDER = 'codex-oauth'
$env:NSLAB_LLM_MODEL = 'gpt-6.1-sol'
$env:NSLAB_CODEX_MODEL = 'gpt-6.1-sol'
$env:NSLAB_CODEX_REASONING_EFFORT = 'high'
$env:NSLAB_MAX_CONCURRENCY = '4'
$env:PYTHONPATH = (Join-Path (Get-Location).Path 'src')

python -m news_scalping_lab.cli brain build-offline --source-project 'C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project' --expected-manifest-sha256 '6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576' --checkpoint-dir 'C:\Users\eorb9\projects\news_bot\runs\checkpoints\llm' --compatible-checkpoint-model 'gpt-5.6-sol/xhigh' --continue-after-map-plan
~~~

Use bounded configured concurrency 4; do not start a second process writing the same DB/checkpoint store. Monitor the exact compiler Python process, private memory/RSS, CPU, progress timestamp, child outputs, and disk. A high memory sample by itself is not proof of a leak. Do not clean unrelated caches or user files. Do not kill a healthy, progressing model request for exceeding an arbitrary time. If the parent has failed and a child is demonstrably stranded, resolve its full command line/ancestry, verify it belongs to this build, inspect whether it has produced an output or progress, and affect only that exact child.

For every user-facing update report the fixed node count first: closed / 1,868; remaining = 1,868 minus closed. Distinguish provider-fresh valid outputs, exact checkpoint reuse, local carry/validation, currently running, failed, and waiting. Report processed_record_count only as input accounting. Do not translate DAG percentage into corpus percentage, model understanding, or ETA.

### 4. 고정 DAG 및 package 완료 감사

- 1,868/1,868 node가 유효한 새 output 또는 정확히 일치하는 verified checkpoint/local closure로 닫혔는지 plan, receipt, trace, child lineage, persisted DB rows로 전수 대조한다.
- 빠진 node, 잘못된 citation, source identity 불일치, 미해결 error, stale/incompatible checkpoint가 있으면 package PASS를 선언하지 않는다.
- 동일 source identity로 immutable evaluation V2 package를 만들고 standalone/deep verifier, manifest/root, signature/provenance, schema parity, 중복·누락·unknown type, claim-to-source membership을 검사한다.
- real embedding provider와 실제 HNSW query plan을 확인한다. production index가 deterministic test vector로 대체되지 않았는지 확인한다.
- 종료 후 read-only parity/coverage 분석을 수행한다. record accounting, semantic unit/group, stage별 실제 LLM payload exposure, model별 trace, 실제 source citation이 있는 claim, runtime에서 인용 가능한 retrieved evidence를 각기 다른 표로 낸다. 구조 coverage가 100%라고 의미 노출도 100%라고 쓰지 않는다.
- 실제 날짜/연도/거래일별 coverage와 gap을 산출한다. 2018-01-03~2026-06-19 데이터만으로 완전한 10년을 주장하지 않는다.
- 패키지 생성이 production activation 또는 release promotion을 뜻한다고 말하지 않는다.

### 5. 실제 장전 daily architecture smoke

실제 V2 package와 허용된 historical/pre-open CSV/date/cutoff를 사용해 production intended path인 analyze-daily를 실행한다. 적격 CSV는 cutoff 후 정보가 prompt, memory retrieval, context manifest에 섞이지 않는지 먼저 확인한다.

필수 검증:

- 첫 LLM 요청 전에 compiled world/category knowledge와 cutoff-safe 현재 뉴스 관련 기억이 로드된다.
- 현재 CSV와 brain evidence가 같은 final_market_decision 요청에 함께 들어간다. 선행 interpretation 요청이 없다.
- 정상 경로는 logical LLM call 1회, 구조화 출력 repair는 최대 1회다. 측정한 실제 call 수를 증거와 함께 보고한다.
- exact keyword retrieval은 후보 gate가 아니며 open-world 후보 생성을 유지한다.
- CSV_MEMORY_ONLY_STRICT, no web, no D-day/outcome leakage, provenance 및 context manifest가 확인된다.
- citation이 최종 판단까지 연결되고 출력 파일/실제 latency/trace가 저장된다.
- 실행이 실패하면 legacy exhaustive analyze로 우회하지 않는다. Daily smoke와 backtest 성능 주장은 구분한다.

### 6. 같은 daily architecture의 정식 blind 평가

- 평가용 BUILD brain은 평가 cutoff보다 뒤의 CALIBRATION/HOLDOUT 뉴스, claims, outcome, centroid, company-memory delta 또는 category input을 포함하지 않는다.
- BUILD, CALIBRATION, HOLDOUT의 물리적 분리와 sealed selection/hash를 새로 검증한다. stale selection, forensic-only run, evaluator-only exhaustive path를 formal result로 재사용하지 않는다.
- 예측은 sealed blind selection 및 허용된 D-1 context만 읽고, 모든 arm/case의 prediction seal과 paired closure가 끝나기 전 outcome 파일/필드를 열지 않는다. scoring만 그 뒤 outcome을 연다.
- 명령 이름만 보고 evaluator가 맞다고 간주하지 않는다. 실행할 evaluator의 호출 경로를 코드에서 확인해 실제 `analyze-daily`/`ThinDailyAnalyzer` daily 경로, brain 선로딩, 동일 cutoff/evidence 정책, 1 logical LLM call + 최대 1 structured repair를 지키는지 입증한다. `DailyAnalyzer.analyze()`의 legacy exhaustive graph나 record/cluster/lane별 fan-out이면 formal 증거로 쓰지 않는다.
- 기존 `memory predict-runtime-variants` 경로가 위 조건을 만족하는지 반드시 검사한다. 만족하지 않으면 과거 결과를 formal로 재사용하지 않는다. 등록된 gate가 명시한 유한 case 수가 있고 필요한 sealed input이 있을 때만, 기존 blind-selection/seal/scoring 경계를 재사용하는 최소 evaluator를 보강한다. 호출 수는 gate에 정해진 case/arm 수로 한정하고, raw record나 relationship 수에 비례시키지 않는다.
- formal runner, 사전 등록된 gate/평가 범위 또는 물리적으로 분리된 blind input이 없으면 임의 기준·대규모 LLM run을 만들지 않는다. 빠진 항목과 이유를 기록하고 `NOT_RUN_GATE_MISSING` 또는 해당 상태로 닫으며, 품질 판정은 HOLD로 남긴다. case-limit smoke를 formal split이라고 부르지 않는다.
- 현재 repository contract의 formal model/profile 및 명령은 실행 시점의 AGENTS.md, SKILL.md, quality-evaluation contract를 확인해 적용한다. 오프라인 합성 모델과 daily formal evaluation 모델을 혼동하거나 조용히 바꾸지 않는다.
- 등록 gate가 통과해도 요구된 외부 승인이 확인되지 않으면 production을 활성화하지 않는다.

### 7. Release 결속, 검증, 문서화 및 handoff

- 실제 audited V2 package root/pointer를 저장소가 지원하는 release-binding manifest와 결속하고 rollback target/절차를 검증한다. 서명은 현행 release contract가 요구할 때만 사용하며, 새 서명 체계를 발명하지 않는다. binding이 runtime에서 선택한 것과 같은 package를 가리키는지 확인한다.
- production pointer 전환이나 실제 활성화는 별도 등록 quality gate, 요구된 사용자 승인, 검증된 rollback 없이 하지 않는다.
- 변경된 각 코드 worktree에서 repository 요구 gate를 실행한다.

~~~powershell
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
~~~

- 이 goal 문서와 필요 recovery/완료 보고서를 저장소 operations 문서 및 Downloads 실행본에 동기화한다. 최종 보고서에는 DAG close 현황, 실제 모델별 fresh/reused 작업·호출·토큰 증거, package roots, coverage/exposure/citation, daily smoke의 CSV/date/cutoff/latency/call 수, formal split/gate 상태, release/HOLD, 저장 파일 경로를 기록한다.
- 연구 원문, private DB, checkpoint, giant run artifact는 Git에 올리지 않는다. 관련 코드/문서만 Korean commit message로 commit/push하고 branch/remote 결과를 확인한다. 사용자 변경과 untracked output을 보존한다.
- 최종 daily handoff에는 실제로 검증된 analyze-daily 명령 예시와 입력 형식을 포함하되, 아직 검증하지 않은 latency나 정확도를 약속하지 않는다.

## 완료 판정

기술 작업은 다음 증거가 있을 때 닫는다. 단, 기술 완료와 predictive quality 승인/production 활성화는 별개의 상태로 보고한다.

1. 동일 compile ID의 fixed DAG가 1,868/1,868이고 persisted source/plan/checkpoint/citation lineage가 통과한다.
2. immutable V2 package가 standalone/deep/HNSW/provenance/coverage audit를 통과한다.
3. 실제 daily CSV smoke가 intended one-call brain-loaded architecture, blind boundary, citations, manifests를 통과한다.
4. 등록된 평가 gate가 있으면 같은 daily architecture로 CALIBRATION/HOLDOUT을 완료하고, 없으면 평가를 시작하지 않은 정확한 사유를 `NOT_RUN_GATE_MISSING`으로 기록한다. 어느 경우에도 미실행을 통과로 바꾸지 않는다.
5. V2 package와 repository-supported release-binding manifest/rollback trace가 일치한다.
6. 관련 code/doc gates, 필요한 commit/push, 외부 리뷰 가능한 완료 보고서가 확인된다.

등록된 gate가 없거나 실패하면 predictive quality는 미승인, production은 HOLD로 보고한다. 이를 이유로 새 평가 기준, 무제한 사례 수, 반복 합성 작업을 덧붙이지 않는다. 기술 작업이 끝났으면 미충족 항목과 승인 필요 사항을 적어 goal을 닫고, 새 연구 원료나 구체적 coverage 결함이 확인된 경우에만 별도 승인 후 후속 goal을 만든다. 오래된 run을 반복 실행하거나 823,279 records를 다시 import/embedding/map하는 것은 완료 판정에 포함되지 않는다.

## 관련 기준 문서

- 저장소 AGENTS.md
- .agents/skills/news-scalping-lab/SKILL.md
- docs/operations/one_time_brain_daily_inference_intent.md
- docs/operations/offline_brain_thin_daily_architecture.md
- docs/operations/offline_semantic_brain_v2.md
- diagnostics/offline_brain_recovery_20261004.md

## 다음 실행 요청

### 최신 handoff (2026-10-05 16:37 KST)

최신 progress ledger는 `2026-10-05T16:36:50.012612+09:00`, `offline_reduce`, `881/1,868` closed, `987` remaining, pointer `REDUCE-ea8fef5106c2916a5077`이다. 같은 compile writer는 마지막 확인 시 executor session `48993` / Python PID `20864`로 live였다. 수치는 fixed-DAG closure일 뿐이며 실행 시 process, ledger와 WAL을 새로 확인한다. writer/WAL live 동안 DB/WAL은 열지 않는다. Compiler fix `7bb1b40`은 push됐고 Ruff, Mypy 139 files, pytest 1,960 passed다. Python private memory는 `3,939,270,656` bytes(약 3.67 GiB), working set은 `3,178,217,472` bytes(약 2.96 GiB), 동시성 상한은 4다.

### 현재 전달할 실행 요청

> 저장소 `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`, 이 goal 문서 전체와 `diagnostics/offline_brain_recovery_20261004.md`를 읽고 제품 의도와 금지된 forensic run을 먼저 확인해. 목표는 연구 원료를 다시 import하거나 매일 raw research를 재해석하는 것이 아니라, 기존 고정 compile과 이미 완료된 작업을 재사용해 검증 가능한 Offline Semantic Brain V2 및 실제 장전 CSV 제품 흐름을 완성하는 것이다.
>
> 먼저 현재 process command line/ancestry에서 `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 writer를 새로 찾아 progress ledger를 읽어. 문서에 기록된 session/PID는 오래됐을 수 있으므로 현재 사실로 믿지 마. 같은 writer가 살아 있으면 그 실행 하나만 계속 관찰하고 절대로 duplicate writer를 시작하지 마. live writer 또는 WAL이 있으면 DuckDB/WAL read, hash, parity scan을 하지 말고 progress, process/child status, invocation traces/checkpoints, memory/disk만 확인해. 건강하게 응답 중인 provider child를 임의 시간 기준으로 종료하지 마. 프로세스가 사라졌거나 UI session이 끊긴 것만으로 terminal이라 단정하지 말고 PID ancestry, exit status, ledger, WAL로 확인해. writer와 WAL이 모두 없을 때만 read-only DB 대조를 한다.
>
> 이후 immutable Offline Semantic Brain V2 package, 실제 production analyze-daily pre-open CSV flow, provenance/cutoff/citation, bounded blind gate, release binding/rollback을 차례로 검증한다. Daily path는 cutoff-safe brain과 CSV를 첫 final decision 요청에 함께 넣고 logical LLM call 1회, structured repair 최대 1회, CSV_MEMORY_ONLY_STRICT, no web/no D-day or outcome leakage를 지킨다. 등록된 formal gate/sealed input이 없으면 NOT_RUN_GATE_MISSING, predictive quality 미승인, production HOLD로 기록한다. Forensic-only 379-pack은 사용하지 말고 승인 없이 production을 활성화하지 않는다.
>
> 추가 실행 조건: 위 session/PID 숫자는 handoff snapshot일 뿐이므로 지금 ledger와 process를 다시 읽어 최신 값으로 보고해. 이 문서 갱신 시점에는 881/1,868, remaining 987, ledger time `2026-10-05T16:36:50.012612+09:00`다. 새 session에서 이 문서를 실행하더라도 먼저 같은 compile의 live writer를 찾아 관찰하고, 이미 살아 있으면 두 번째 writer를 만들지 마. 1,868/1,868 terminal closure 전에는 package build, daily smoke, formal evaluation을 먼저 시작하지 않는다.
>
> 이후 단계에서는 실제 repository-supported release binding과 rollback을 확인한다. 적격한 실제 pre-open CSV가 없으면 가짜 CSV로 성공을 꾸미지 말고 input 누락을 기록하거나 사용자에게 필요한 CSV를 요청한다. formal gate 또는 sealed blind input이 없으면 임의 평가를 만들지 말고 `NOT_RUN_GATE_MISSING`과 production HOLD를 유지한다. 완료 보고는 구조적 record coverage와 실제 LLM semantic exposure/claim citations를 별도 표로 구분하고, 10년치 완전성이나 backtest 성능을 증거 없이 주장하지 않는다.

### 과거 handoff 기준 (2026-10-05 15:34 KST, superseded)

기준은 2026-10-05 15:34 KST다. progress 원장의 최신 시각은 `2026-10-05T15:24:29.704335+09:00`이며, `offline_reduce`, 812/1,868 closed, 1,056 remaining, pointer `REDUCE-ba980a1a1418ba00f0ed`다. 최신 실행은 citation validation 오류로 exit code 1 terminal됐다. 현재 compile writer 0개, WAL 없음, read-only DB `reduce_nodes=812`, 실패 node row 0개, capsules 52,644, assignments 823,279다. `processed_record_count=823279`는 input accounting이며 합성 완료율이 아니다.

실패 citation은 `REDUCE-e6c11b4aee1b4a8e62f1` / `TRACE-ee9c6faf94ed` / `LLMCKPT-45d46e52818e8fba`의 `CAP-cd066b07b85b5b40551a /”`다. Base ID의 exact allowed-set membership은 아직 미확정이므로 먼저 read-only forensic diagnosis를 끝내고, 증거가 있을 때만 좁은 code fix를 한다. 완료 전까지 source/plan/DB/checkpoint identity는 고정한다.

### 과거 실행 요청 (2026-10-05 15:34 KST, superseded)

> 이 문서와 저장소 `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`를 기준으로 기존 `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`을 끝까지 이어서, 내가 원하는 장전 CSV 제품 흐름까지 증거로 검증해. 문서의 과거 PID/session은 현재 상태로 간주하지 말고 process command line/ancestry, progress, WAL을 새로 확인해. 현재 기록은 `offline_reduce` 812/1,868 closed, 1,056 remaining이며 마지막 run은 citation validation 오류로 exit code 1 terminal됐다. terminal 및 WAL 부재를 확인한 경우에만 read-only DB 대조를 하고, 최신 기록값은 `reduce_nodes=812`, 실패 node row 0, capsules 52,644, assignment rows 823,279다.
>
> 첫 작업은 `REDUCE-e6c11b4aee1b4a8e62f1` / `TRACE-ee9c6faf94ed` / `LLMCKPT-45d46e52818e8fba`의 read-only diagnosis다. Claim 3 citation 원문 `CAP-cd066b07b85b5b40551a /”`의 base ID가 exact allowed evidence set에 들어 있는지 production leaf/evidence/prompt builder로 재구성해 증명하고, trace/checkpoint/prompt SHA와 고정 plan을 대조해. 아직 membership은 미확정이다. 증거 전에는 수정하거나 resume하지 마. 허용 ID + 정확한 suffix 문제임이 증명된 경우에만 해당 citation field를 좁게 보정하고, positive/negative 회귀 테스트 후 compiler worktree에서 Ruff/Mypy/full pytest를 통과시키고 한국어 commit/push해. validator 일반 완화, ID 추측, 실패 claim 누락, checkpoint/DB 삭제는 금지야.
>
> fix가 검증된 뒤에만 동일 source manifest, record root, compile ID, sealed 1,868-node plan/topology, target DB, checkpoint directory로 `--continue-after-map-plan` 재개해. Codex OAuth `gpt-6.1-sol/high`, concurrency 4 및 정확히 호환되는 `gpt-5.6-sol/xhigh` checkpoint를 쓰고 source import, embedding, map provider work, planner를 반복하지 마. Progress 분모는 1,868 DAG node 하나만 사용하고, provider-fresh valid output / exact checkpoint hit / local carry·validation / failure / running을 나눠 `closed/1,868`, remaining으로 보고해. 823,279 records를 전부 GPT가 직접 읽었다거나 record count를 두뇌 완성률이라고 말하지 마.
>
> DAG가 1,868/1,868로 닫힌 뒤 immutable Offline Semantic Brain V2 package, real embedding/HNSW 및 provenance/coverage/source-claim lineage를 audit하고 실제 production `analyze-daily`로 허용된 pre-open CSV smoke를 해. daily 경로는 cutoff-safe brain/index와 현재 CSV를 첫 `final_market_decision` 요청에 함께 넣는 logical LLM call 한 번, 구조화 repair 최대 한 번이어야 해. `CSV_MEMORY_ONLY_STRICT`, no web, no D-day/outcome leakage, open-world 후보 생성, citation/provenance/context manifest를 지켜. 매일 raw corpus/record/cluster/lane별 LLM fan-out이나 legacy exhaustive 경로로 우회하지 마.
>
> 날짜/연도/거래일 coverage, semantic exposure, source citations는 구조적 record accounting과 별도 수치로 보고해. 현재 source 날짜 범위 2018-01-03~2026-06-19만으로 10년 전체라고 추정하지 마. 같은 deployable daily architecture의 사전 등록된 bounded blind gate와 physically separated sealed input이 있을 때만 formal evaluation을 진행해. gate가 없으면 `NOT_RUN_GATE_MISSING`, predictive quality 미승인, production HOLD다. invalidated/forensic-only 379-pack을 resume, score, compare, promote 또는 cache input으로 쓰지 마.
>
> 최종 package와 repository-supported release binding/rollback이 일치하는지 검증하되, 등록 quality gate와 필요한 승인 없이 production 활성화하지 마. 끝나면 goal/recovery/Downloads 문서와 외부 리뷰 보고서를 최신 증거로 갱신하고, 관련 문서·코드만 한국어 commit/push해. 기존 untracked 파일, research 원본, DB, checkpoint, 대형 run artifact는 변경·삭제·이동·commit하지 마. 기술 완료, predictive-quality 승인, production 활성화를 별개 판정으로 보고해.

### 구 실행 요청 (2026-10-05 15:06 snapshot, 실행 금지)

아래 한 문단은 당시 진행 중으로 잘못 남아 있던 15:06 스냅샷의 실행 지시문이다. 실행하거나 현재 상태로 인용하지 않는다. 새로 전달할 요청은 바로 위 `지금 전달할 실행 요청`만 사용한다.

> 이 goal 문서와 저장소 `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`를 기준으로 기존 `OFFLINE-COMPILE-0dd9198ac9ef79215ab1` 작업을 끝까지 이어서 검증해. 현재 executor session `34127` / Python PID `40148`가 같은 compile writer이므로 그 실행을 관찰하고 절대로 두 번째 writer를 시작하지 마. 현재 기준 progress는 770/1,868, 1,098 remaining이며 target DB/WAL은 열지 마. process/session이 끝나거나 관찰 연결이 끊기면 command line/ancestry와 progress/WAL로 실제 terminal 여부를 판정한 뒤에만 read-only DB 대조를 해. 아직 종료된 상태이고 WAL이 없을 때만 동일 source/manifest/plan/DB/checkpoint identity로 `--continue-after-map-plan` 재개해. 이미 끝난 source import, embedding, fresh map 작업, planner는 반복하지 마. 직전 7,513 map trace는 모두 checkpoint hit이고 fresh map 호출은 0이었으며, 이번 재개 후 생성된 44개 checkpoint는 현재까지 모두 `offline_semantic_reduce` 목적이다. 이번 실행의 전체 trace로 map/reducer fresh·checkpoint hit을 재검증해. `7a693dc`의 정확한 두-ID citation 수정과 `35fe4e5`의 U+2009 수정은 이미 push되고 전체 gate를 통과했다. 같은 오류가 재발하면 trace/checkpoint와 node의 exact allowed evidence set으로 원인을 재구성하고, validator를 넓히거나 checkpoint를 삭제하지 말고 최소 수정·회귀 테스트·전체 gate·한국어 commit/push 후 같은 compile을 재개해. fixed DAG 1,868개를 plan/receipt/trace/checkpoint/persisted row로 닫은 다음에만 immutable Offline Semantic Brain V2 package, 실제 pre-open CSV의 production `analyze-daily` 경로, 동일 deployable daily architecture의 bounded blind evaluation, release binding/rollback을 검증해. 매일 경로는 brain과 현재 CSV를 첫 판단 요청부터 함께 써야 하며 logical LLM call 한 번, 구조화 repair 최대 한 번, `CSV_MEMORY_ONLY_STRICT`, no web, no D-day/outcome leakage, citation/provenance/context manifest를 지켜. legacy exhaustive 경로와 forensic-only 379-pack을 formal 근거로 쓰지 마. 등록 gate/sealed input이 없으면 새 평가기준이나 대량 호출을 만들지 말고 `NOT_RUN_GATE_MISSING`, predictive quality 미승인, production HOLD로 기록해. 실제 승인과 rollback 증거 없이 production을 활성화하지 마. 끝나면 goal 및 recovery 문서와 Downloads본을 동기화하고, 외부 리뷰용 완료 보고서를 만들고, 관련 문서/코드만 한국어 commit으로 push해. 연구 원본, DB, checkpoint, 대형 산출물과 기존 user/untracked 파일은 변경하거나 commit하지 마.

### 실행 순서

1. 저장소 지침, 이 goal, recovery report와 최신 progress를 읽는다. 현재 process를 새로 탐색해 같은 compile writer 여부를 판단한다. 아래에 적힌 과거 PID/session 값만으로 실행 중 여부를 추정하지 않는다.
2. compiler worktree의 branch/HEAD/remote 상태와 이미 push된 citation-fix lineage `2c05062`, `35fe4e5`, `7a693dc`, `7bb1b40`을 확인한다. 실제 branch log에서 각 commit의 존재 여부를 판정하고 있으면 재적용하지 않는다. 사용자 변경과 untracked run 산출물은 보존한다.
3. writer가 있으면 관찰만 한다. terminal이고 writer/WAL이 없을 때 progress, exit code, plan/receipt/source manifest, compile metadata, target DB row 수를 대조한다. 서로 불일치하면 resume 전에 원인을 진단한다.
4. 문서에 적힌 progress는 handoff snapshot일 뿐이다. 실행 때 matching writer/WAL을 먼저 재발견한다. writer가 live면 관찰만 하며, terminal 및 WAL 부재일 때 최신 ledger·identity·DB를 대조한다. 동일 compile이 미완료일 때만 이 문서의 동일 compile 명령으로 재개한다. map receipt/plan은 로컬 재검증될 수 있지만 map provider 호출이 새로 발생하면 cache hit로 세지 말고 별도 보고한다. source import와 embedding은 반복하지 않는다.
5. 새 citation 오류가 날 때만 해당 node를 정확히 진단한다. provider 성공, checkpoint 존재, DAG closure는 별도로 집계한다. 수정은 해당 node의 허용 집합에 대한 exact membership을 보존하고, focused regression 및 Ruff/Mypy/전체 pytest 후 commit/push한다. healthy writer나 무관한 process를 종료하지 않는다.
6. 동일 daily architecture의 package audit, 실제 CSV smoke, bounded blind evaluation, release binding/rollback을 순서대로 수행한다. 평가 gate나 physically sealed input이 빠졌으면 그 단계는 `NOT_RUN_GATE_MISSING`으로 끝내고 production을 HOLD로 둔다. legacy exhaustive나 forensic artifact로 우회하지 않는다.
7. 결과·한계·필요 승인을 goal/recovery 문서 및 Downloads본에 기록한다. 외부 리뷰용으로 compile closure와 모델별 fresh/reused 호출, package roots, 실제 coverage/exposure/citations, daily CSV/date/cutoff/call 수/latency, evaluation gate, release 상태를 구분해 보고한다. 관련 소스/문서만 한국어 commit/push하고 원격 반영을 검증한다.

모든 진행 보고는 `closed/1,868`, `remaining`, provider-fresh valid output, 정확한 checkpoint hit, local carry/validation, failed, running을 분리한다. 현재 handoff snapshot은 881/1,868, remaining 987이다. 실행 시 최신 ledger로 갱신한다. 1,868은 고정된 DAG 작업 수이며 record·연도·의미 이해 비율이 아니다. 신뢰할 처리량 근거가 없으면 ETA를 만들지 않는다. 기술 완료, predictive-quality 승인, production 활성화는 각각 별도 상태로 보고한다.
