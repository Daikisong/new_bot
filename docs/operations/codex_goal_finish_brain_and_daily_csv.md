# Goal: 일회성 두뇌 컴파일 완료 및 장전 CSV 판단 흐름 검증

문서 갱신: 2026-10-05 10:08 KST
현재 상태 기준시각: progress 원장 2026-10-05 10:02:10 KST. 기존 session 14734 / Python PID 60928은 `offline_reduce` 중 실패해 exit code 1로 종료했다. 직전 원장은 493/1,868, 남은 1,375, `current_model_node_id=REDUCE-c74a73797806b99d3155`였다. 종료 후 같은 DB writer가 없고 WAL도 없음을 확인했으며, read-only `reduce_nodes` count 493이 원장과 일치했다. 종료 오류는 `semantic reduce claim cited an unavailable capsule: claim_index=3 field=supporting_capsule_ids citation='CAP-e3f1c1efc61d5c29ca67 ... a' allowed_capsule_count=12`다. 오류 trace/checkpoint와 정확한 failed node를 아직 매핑하지 않았다. 26.4%는 DAG 원장 비율이지 record coverage나 의미 이해도 비율이 아니다. 원인을 밝히기 전 같은 compile을 무조건 재실행하거나 citation을 제거/완화하지 않는다.

다음 실행 요청은 이 문서의 **다음 실행 요청**에 적힌 문구를 사용한다. 지금은 기존 compile이 실패 종료했으므로 먼저 새 citation 오류를 trace/checkpoint와 실제 allowed capsule 집합에 연결하고, 검증된 최소 수정만 한 뒤 같은 compile을 재개한다. import·embedding·map·planner는 다시 하지 않는다. 이 goal은 1,868개 고정 node의 나머지를 닫은 뒤 실제 brain package와 장전 CSV 경로를 검증하는 유한한 작업이다. package 생성만으로 GPT 가중치 학습, backtest 통과 또는 production 승인을 주장하지 않는다.

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

2026-10-05 10:02:10 KST progress 원장, session 14734의 exit code 1, 종료 후 process/WAL 상태를 확인했다. 같은 compile writer가 없고 WAL이 없어 read-only 대조를 허용했다. `reduce_nodes`는 493행으로 progress 원장의 493과 일치했다.

- 마지막 실행: tool session 14734 / Python PID 60928 / parent PowerShell PID 24324, phase `offline_reduce`, terminal exit code 1.
- 고정 compile identity: `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`; worktree, source manifest, target DB, checkpoint 경로는 위 고정 identity를 유지한다.
- 마지막 ledger: 493/1,868 closed, 1,375 remaining (26.4% DAG progress); progress field의 current node는 `REDUCE-c74a73797806b99d3155`. 동시 reducer 실행 중 발생한 validator 오류이므로 이 field만으로 failed node를 확정하지 말고 trace/checkpoint에서 실제 node를 찾아야 한다.
- 미해결 오류: `semantic reduce claim cited an unavailable capsule: claim_index=3 field=supporting_capsule_ids citation='CAP-e3f1c1efc61d5c29ca67 ... a' allowed_capsule_count=12`. 이 citation이 허용 ID에 붙은 정규화 가능한 출력인지, 허용되지 않은 ID인지 현재 미판정이다. 기존 suffix fix를 넓히거나 claim/citation을 버리는 조치를 금지한다.
- 종료 직후 같은 compile writer 없음, target `.duckdb.wal` 없음, `reduce_nodes` 493행과 progress 493이 일치함을 확인했다. 재개 전 이 조건을 다시 확인하고 문제 출력이 persisted row인지 checkpoint인지 trace인지 분리해서 확인한다.
- `processed_record_count=823279`와 `record_progress_ratio=1.0`은 입력 record accounting만 뜻한다. offline synthesis node, payload exposure, brain package, daily decision이 완료됐다는 뜻이 아니다.
- 실행 재개 시 process/ledger/DB/WAL 상태를 다시 읽는다. 아래 과거 수치들은 복구 경위만을 위한 기록이며 현재 진행률로 인용하지 않는다.

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
- 각 arm은 production intended one-call, brain-loaded daily architecture와 같은 evidence surface를 사용한다. 제한된 case-limit은 smoke이지 formal split이 아니다.
- registered quality gates와 필요한 외부 승인을 확인한다. 등록되지 않은 기준이나 임의 latency·수익 임계치를 만들어 합격 처리하지 않는다.
- 현재 repository contract의 formal model/profile 및 명령은 실행 시점의 AGENTS.md, SKILL.md, quality-evaluation contract를 확인해 적용한다. 오프라인 합성 모델과 daily formal evaluation 모델을 혼동하거나 조용히 바꾸지 않는다.
- gate가 미등록 또는 미통과이면 결과를 정직하게 HOLD로 남긴다. 품질 승인 없이 production을 활성화하지 않는다.

### 7. Release 결속, 검증, 문서화 및 handoff

- 실제 audited V2 package root/pointer를 signed release manifest와 결속하고 rollback target/절차를 검증한다. release manifest가 runtime에서 선택한 것과 같은 package를 가리키는지 확인한다.
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

이 goal의 기술적 완료는 다음 증거가 모두 있을 때만 선언한다.

1. 동일 compile ID의 fixed DAG가 1,868/1,868이고 persisted source/plan/checkpoint/citation lineage가 통과한다.
2. immutable V2 package가 standalone/deep/HNSW/provenance/coverage audit를 통과한다.
3. 실제 daily CSV smoke가 intended one-call brain-loaded architecture, blind boundary, citations, manifests를 통과한다.
4. formal CALIBRATION/HOLDOUT이 같은 daily architecture로 완료되고, 품질 gate 결과가 등록된 기준에 따라 보고된다.
5. V2 package와 signed release binding/rollback trace가 일치한다.
6. 관련 code/doc gates, 필요한 commit/push, 외부 리뷰 가능한 완료 보고서가 확인된다.

등록된 gate가 없거나 실패하면 위 결과를 production-ready/활성화로 표현하지 않는다. 다른 기술 작업이 끝났더라도 activation 상태와 사용자/외부 승인 필요 사항을 명시한다. 오래된 run을 반복 실행하거나 823,279 records를 다시 import/embedding/map하는 것은 완료 판정에 포함되지 않는다.

## 관련 기준 문서

- 저장소 AGENTS.md
- .agents/skills/news-scalping-lab/SKILL.md
- docs/operations/one_time_brain_daily_inference_intent.md
- docs/operations/offline_brain_thin_daily_architecture.md
- docs/operations/offline_semantic_brain_v2.md
- diagnostics/offline_brain_recovery_20261004.md

## 다음 실행 요청

문서를 실행시킬 때 사용자 요청은 아래 한 문장으로 충분하다. session/PID 숫자는 문서 작성 시점의 관측치이므로 실행 시 다시 확인한다.

> 최신 goal 문서 `C:\Users\eorb9\Downloads\codex_goal_nslab_finish_brain_and_daily_csv.md`대로 지금부터 끝까지 진행해. 시작 시 같은 compile ID의 process/session, progress, writer 및 WAL 상태를 다시 확인해. 마지막 session 14734는 exit code 1로 끝났고 `CAP-e3f1c1efc61d5c29ca67 ... a` citation 검증 오류가 있었으니, 먼저 해당 오류를 실제 failed node, trace/checkpoint, prompt의 exact allowed capsule 집합에 연결해 원인을 입증해. 허용된 ID의 정확한 출력 artifact임이 확인되기 전에는 suffix normalization을 넓히거나 claim/citation을 삭제하지 마. writer가 살아 있으면 새로 띄우지 말고 그 작업만 모니터링하고, terminal이면 writer 종료와 WAL 부재를 확인한 뒤에만 read-only audit해. 최소 수정과 회귀 테스트 후 같은 source/manifest/compile ID/target/checkpoint identity로 남은 DAG를 재개해. 연구 import, embedding, map, planner와 이미 완료된 단계는 반복하지 마. 고정 DAG 종료 후 Offline Semantic Brain V2 audit, 실제 package를 쓰는 pre-open `analyze-daily` smoke, 같은 일일 architecture의 blind evaluation, release binding/rollback, Ruff·Mypy·pytest, 한국어 commit/push 및 외부 리뷰용 보고서까지 진행해. 각 단계의 증거와 남은 일을 문서화하고, 등록 quality gate와 필요한 승인이 없으면 production 활성화는 하지 말고 HOLD로 보고해.

실행 시 순서:

1. AGENTS.md, news-scalping-lab skill, 본 goal, 실제 process ancestry, progress 원장, trace/receipt/manifest identity를 다시 읽는다. 마지막 관측 session 14734 / Python PID 60928의 exit 1 및 새 citation 오류를 기록과 대조한다. 화면 세션이 끊겼다는 이유만으로 중복 실행하지 않는다.
2. 같은 build writer가 실행 중이면 그 writer만 관찰한다. terminal이면 exit code, 고정 plan, ledger, checkpoint lineage, DB rows를 대조한다. 동일 DuckDB writer나 WAL이 남아 있는 동안 그 DB를 열지 않는다. 새 오류의 실제 failed node는 progress field로 추정하지 말고 trace와 checkpoint에서 특정한다.
3. 새 citation 오류에서 exact allowed capsule membership을 child lineage로 재구성한다. 원문·codepoint·claim field/index·input/prompt/schema/compiler identity와 output/checkpoint SHA를 보존한다. 이 exact base ID가 allowed set에 있고 suffix가 제거 가능한 출력 artifact임이 증명될 때만 좁은 normalization을 추가하고, raw/normalized 값과 규칙을 감사 데이터에 남긴다. 아니면 잘못된 citation으로 거부하고 유효한 checkpoint/요청만 재시도한다. 허용 집합 검증 없이 claim/citation을 버리거나 validator를 약화하지 않는다.
4. 미완료 또는 실패 node만 좁게 처리해 같은 source/manifest/compile ID/plan/target/checkpoint identity로 1,868-node DAG를 완료한다. 최소 수정에 대한 focused test, Ruff, Mypy, full pytest를 통과시킨다. 기존 input·embedding·map/planner를 재생성하지 않는다.
5. immutable Offline Semantic Brain V2 package audit, 실제 장전 CSV smoke, 같은 daily architecture의 formal blind evaluation, release/rollback binding을 차례로 검증한다. 각 단계가 실패하면 legacy exhaustive 경로나 forensic-only artifact로 우회하지 않는다.
6. 필요한 테스트를 통과시키고, 문서와 Downloads 실행본을 동기화하며, 관련 문서/코드만 한국어 commit으로 push한다. 연구 원본·DB·checkpoint·대형 run output은 commit하지 않는다.

모든 상태 보고에서 고정 진행률 `closed/1,868`, `remaining`, provider-fresh 성공, 정확한 checkpoint 재사용, local carry/validation, failed, running을 나눠 보고한다. record accounting을 compile 완료율로 바꾸지 않는다. ETA는 충분한 실제 처리량 근거와 가정을 함께 제시할 때만 범위로 말한다. 기술 검증과 production activation을 구분하고, 등록 quality gate/요구 승인이 확인되지 않으면 production 상태는 계속 HOLD다.
