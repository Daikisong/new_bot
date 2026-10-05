# Goal: 일회성 두뇌 컴파일 완료 및 장전 CSV 판단 흐름 검증

문서 갱신: 2026-10-05 13:58 KST
현재 상태 기준시각: 기존 compile `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`은 tool session 74329 / Python PID 9572로 재개되어 실행 중이다. 최신 progress 원장은 `2026-10-05T13:56:41.233548+09:00`, `offline_reduce`, 684/1,868 closed, 1,184 remaining이다. 고정 DAG 진행률은 36.6%다. 마지막 progress pointer는 `CATEGORY-REVIEW-11375e6c015c8e7bd73e`이며 writer는 계속 관찰한다. 실패했던 `REDUCE-1438e4a54b2849782faa`는 새 trace `TRACE-5119051a50b5`에서 동일 checkpoint의 `checkpoint_hit`으로 재사용되어 수정 검증을 통과했고, DAG closure가 683에서 684로 증가했다. 새 map trace 표본은 `checkpoint_hit`이며 전체 map trace fresh/hit 집계는 terminal audit에서 확정한다. writer가 살아 있으므로 target DuckDB/WAL을 읽거나 hash하지 않는다.

직전 실패 원인은 `REDUCE-1438e4a54b2849782faa` / checkpoint `LLMCKPT-a38dbebd3e1bc6d3`에서 허용 citation `CAP-bd21b6945c38aebbc143` 뒤에 U+2009 THIN SPACE 한 글자가 붙은 것이었다. checkpoint input SHA-256은 `474eaf34b92977b7d5292e90d6751767ecbe69719f110c8b4dfe9be39a6bb827`, prompt SHA-256은 `1c9b5308d7554e642d65ba271e8bfb7096380bc3d77ab4a13d96557fb487cdb8`, output SHA-256은 `481e8886a0772aac73c27d25f1906bc63c3b2ff36e662b5b6beada4263107d29`다. exact production prompt 재구성과 checkpoint identity가 일치했고, 실패 시점에는 persisted reducer row가 없었다. 수정 commit `35fe4e5` push 후 재개에서 이 동일 checkpoint가 `checkpoint_hit`으로 검증되어 현재 closure는 684다. live writer가 있으므로 DB row 대조는 terminal 후로 미룬다.

U+2009에 대한 최소 수정은 compiler worktree `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b`, branch `codex/v5-gpt61-high-offline`에서 commit `35fe4e5` (`수정: 허용 capsule citation의 thin space 정규화`)로 push됐다. allowed capsule ID 바로 뒤에 정확히 U+2009가 붙은 경우에만 정규화하며 원문 citation, 정규화 ID, 적용 규칙을 audit에 남긴다. 허용되지 않은 ID와 U+200A 등 유사 공백은 거부한다. 재검증 게이트는 Ruff PASS, Mypy 139개 source file PASS, full pytest 1,949 passed / 1,460 warnings in 305.76s다. map usage ledger의 7,513개 요청이 가리키는 checkpoint 파일이 모두 존재하고 과거 fresh 15개도 status `ok`임을 사전 확인했다. 재개 시 exact map cache hit만 발생하는지 trace에서 확인하고 fresh provider map count를 별도로 보고한다.

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

2026-10-05 13:56:41 KST 기준 동일 compile writer는 tool session 74329 / Python PID 9572로 실행 중이다. Latest progress 원장은 684/1,868 closed, 1,184 remaining, phase `offline_reduce`다. Python private memory 표본은 변동 후 약 3.78 GiB, available RAM 약 20.46 GiB였고 CPU는 계속 증가했다. memory 상승/하락 단일 표본만으로 누수를 추정하지 않으며, resource trend와 progress를 같이 관찰한다. live writer 중 target DuckDB/WAL을 열지 않는다.

- 고정 compile identity: `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`; source manifest, record root, worktree, target DB, plan/topology, checkpoint 경로를 그대로 사용한다.
- 최신 ledger: 684/1,868 closed, 1,184 remaining (36.6% fixed-DAG progress), phase `offline_reduce`, last progress pointer `CATEGORY-REVIEW-11375e6c015c8e7bd73e`.
- source `processed_record_count=823279`와 `record_progress_ratio=1.0`은 input accounting일 뿐 semantic compile이나 brain 완성을 뜻하지 않는다. Daily brain decision, package, blind evaluation은 아직 완료되지 않았다.
- 이전 실행은 exit code 1로 종료됐고, 재개 session 74329 / Python PID 9572가 현재 유일하게 확인된 same-compile writer다. Current progress 684/1,868 at 13:56:41 KST; live DB/WAL inspection은 보류한다.
- 12:33 실패 trace의 provider 응답은 `status=ok`였지만 node validation은 실패했었다. 재개 trace `TRACE-5119051a50b5`는 같은 checkpoint에 `checkpoint_hit`을 기록했고 progress가 684로 증가했다. provider success와 DAG closure는 별도 집계한다.
- 현 compile에서 재시작 후 map prompt reconstruction이 수행되며, 사전 검증된 checkpoint와 일치한 최신 trace 표본은 전부 `checkpoint_hit`이다. terminal 시 이번 session의 전체 map fresh/hit 및 reduce fresh/hit/local reuse를 분리해 집계한다. 아래 09:54 이전 수치와 이전 suffix incidents는 복구 경위로만 보존한다.

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
- progress ledger의 앞선 411개 closure와 terminal 이후 확인한 reduce_nodes 411행, 검증된 exact checkpoint. full plan/receipt/DB/checkpoint lineage는 최종 terminal audit에서 다시 대조한다.

새 input identity가 실제로 달라졌다는 증거와 별도 승인이 없는 한 import, embedding, map, planner, compile ID를 다시 만들지 않는다. 실패 시 이미 저장된 closure와 checkpoint를 보존한다.

## 실행 단계

### 1. 시작 전 single-writer 및 identity 확인

- 사용자가 goal 실행을 요청하면 process tree와 command line에서 같은 compile ID, worktree, target DB, checkpoint directory를 쓰는 build writer가 있는지 확인한다.
- 같은 writer가 살아 있으면 새 build를 시작하지 말고 그 세션만 관찰한다. 터미널 화면이 끊겼다는 이유만으로 중복 실행하지 않는다.
- 실행기가 terminal이면 exit code, progress ledger, plan/receipt/source SHA, DB compile metadata, WAL 존재 여부를 맞춘다.
- live writer가 있거나 WAL이 있으면 해당 DuckDB의 read-only inspection, hashing, parity scan을 하지 않는다.
- 종료 후에도 plan/source/receipt/target/checkpoint identity가 바뀌었거나 ledger와 DB가 불일치하면 재실행하지 말고 mismatch를 먼저 진단한다.

### 2. 반복 citation 오류를 정확히 진단하고 좁게 고친다

이미 진단·수정한 실패 node REDUCE-0703331bc02f14ce3512의 trace/checkpoint를 기준으로 하고, 이후 실패가 생기면 같은 절차로 새 node를 진단한다:

- 해당 reducer의 purpose, input SHA, child lineage, trace와 checkpoint ID를 고정 plan 및 마지막 실패 시각과 연결한다.
- 응답이 checkpoint에 있으면 input SHA, model, prompt/schema/compiler identity가 정확히 맞는 경우만 읽는다. 어떤 output이 실패를 냈는지 확인할 수 없으면 코드/trace에서 실패 값을 보존하는 진단을 추가한 후 재현한다.
- plan과 child reducer의 evidence_capsule_ids로 이 node의 exact allowed capsule 집합을 재구성한다.
- 잘못된 citation 원문/code point, claim index, 허용 집합과의 차이를 기록한다.
- 해결한 오류 `REDUCE-9a0ce8212e283ebc7228` / `TRACE-6a20db7c6c15` / `LLMCKPT-776f66ab880dbbb4`의 exact allowed set membership을 위 상태 절처럼 재구성했다. prefix가 허용된다는 이유로 일반 문자열 stripping을 도입하지 않았다. commit 95e9257의 exact phrase rule 및 음성 테스트를 유지하고, 같은 checkpoint를 새 코드로 재검증한다.
- 입력에 실제로 허용된 capsule ID에 특정 출력 suffix만 붙은 것이 확인되는 경우에만 그 exact normalization을 허용한다. 임의 ID, 근사 일치, 비슷한 suffix, 다른 node로 확장하지 않는다. 정규화 전·후 값과 사유는 감사 trace에 남긴다.
- 원문과 정규화 값, 허용 set membership, 임의 ID 거부를 검증하는 focused regression test를 추가한다. 전체 경로에 validator를 약하게 만들지 않는다.
- 변경은 compiler worktree에서 하고 Ruff, Mypy, 관련 테스트와 pytest 전체를 통과시킨다. code/diff를 이해하고 필요한 Korean commit/push를 한다. 사용자 소유 변경과 untracked output은 건드리거나 되돌리지 않는다.
- 원인이 특정되지 않거나 허용 집합 검증이 불가능하면 이 지점에서 멈춰 추가 증거를 수집한다. 단순히 예외를 무시하거나 해당 claim을 버리고 진행하지 않는다.
- 현재처럼 오류 뒤에도 process가 살아 있으면, full command line/ancestry로 이 compile의 Codex child임을 재확인하고 직전 progress timestamp, CPU 변화, child output, 연결 상태를 다시 측정한다. output/progress가 재개되면 건드리지 않는다. 오류가 반환됐고 300초 executor join 경고 이후에도 같은 node/ledger에서 child가 새 output 없이 고착된 사실이 반복 확인될 때만 그 정확한 stranded child 하나를 종료한 뒤 parent/session이 terminal인지 확인한다. 관련 없는 process나 Python writer를 먼저 죽이지 않는다. terminal과 WAL 부재를 확인한 다음에만 read-only DB 검사를 한다.

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

다음 실행은 기존 active goal을 이어서 완료하는 것이다. 현재 compile writer는 tool session 74329 / Python PID 9572다. latest progress는 `2026-10-05T13:56:41.233548+09:00`, 684/1,868 closed, 1,184 remaining이며 phase는 `offline_reduce`다. U+2009 수정 commit `35fe4e5`와 전체 테스트 gate는 push 완료되었고 실패 node는 동일 checkpoint `checkpoint_hit`으로 통과했다. 이 writer가 terminal이 될 때까지 중복 build를 시작하지 않는다.

> 최신 goal 문서대로 기존 작업을 끝까지 진행해. 먼저 tool session 74329 / Python PID 9572가 동일 compile ID의 writer인지 실제 command line/ancestry로 확인하고, progress timestamp와 CPU/RAM을 읽어. 현재 마지막 progress는 684/1,868, 1,184 remaining, `2026-10-05T13:56:41.233548+09:00`이며 trace `TRACE-5119051a50b5`에서 실패 node checkpoint가 `checkpoint_hit`으로 재사용됐다. 이 writer가 살아 있으면 그것만 관찰하고 duplicate build를 띄우거나 DuckDB/WAL을 열지 마. terminal 뒤 plan/receipt/checkpoint/trace/persisted rows/WAL을 대조해 fixed 1,868 DAG를 닫아. 그 다음 immutable V2 package 검증, 실제 pre-open CSV의 production `analyze-daily` smoke, deployable daily architecture와 동일한 bounded blind 평가, release binding/rollback, 필요한 검증, goal/recovery 문서와 Downloads 동기화 및 외부 리뷰용 결과까지 마쳐. evaluator가 daily 코드 경로인지 확인하고 legacy exhaustive/379-pack을 formal 근거로 쓰지 마. 등록 gate가 없으면 새 기준이나 무제한 호출을 만들지 말고 `NOT_RUN_GATE_MISSING`, predictive quality 미승인, production HOLD로 기록해. DB, checkpoints, 대형 산출물, 기존 user/untracked 파일은 Git에 넣거나 변경하지 마.

실행 시 순서:

1. AGENTS.md, news-scalping-lab skill, 본 goal, 최신 progress 원장과 실제 process ancestry를 다시 읽는다. 저장된 session/PID를 재사용하지 말고 command line과 compile identity로 같은 writer인지 판정한다.
2. compiler fix `35fe4e5`와 전체 게이트가 원격 branch에 있는지 확인한다. 기존 `2c05062` U+2019/` ... a` 수정과 혼동하거나 반복하지 않는다.
3. 현재 session 74329 / PID 9572 writer를 먼저 확인하고, live라면 관찰만 계속한다. 이미 닫힌 failed node checkpoint는 `checkpoint_hit`이며 이를 다시 호출하지 않는다. `684/1,868`은 마지막 snapshot일 뿐 더 최신 progress가 있으면 그 값을 사용한다. terminal 후에만 exact plan/ledger/DB parity를 확인한다.
4. 새 오류가 재발할 때만 실제 failed node를 trace/checkpoint로 특정하고 child lineage의 exact allowed set을 재구성한다. U+2009 외 suffix로 정규화를 넓히거나 checkpoint를 삭제하지 않는다. writer가 살아 있는 동안 DuckDB/WAL을 열지 않는다. terminal 뒤에만 exit code, plan, ledger, source/manifest, checkpoint, DB rows, WAL을 대조한다.
4. immutable Offline Semantic Brain V2 package audit, 실제 장전 CSV smoke, 같은 daily architecture의 formal blind evaluation, release/rollback binding을 검증한다. 각 단계가 실패하면 legacy exhaustive 경로나 forensic-only artifact로 우회하지 않는다.
5. 필요한 테스트를 통과시키고, 문서와 Downloads 실행본을 동기화하며, 관련 문서/코드만 한국어 commit으로 push한다. 연구 원본·DB·checkpoint·대형 run output은 commit하지 않는다.

모든 상태 보고에서 고정 진행률 `closed/1,868`, `remaining`, provider-fresh 성공, 정확한 checkpoint 재사용, local carry/validation, failed, running을 나눠 보고한다. record accounting을 compile 완료율로 바꾸지 않는다. ETA는 충분한 실제 처리량 근거와 가정을 함께 제시할 때만 범위로 말한다. 기술 검증과 production activation을 구분하고, 등록 quality gate/요구 승인이 확인되지 않으면 production 상태는 계속 HOLD다.
