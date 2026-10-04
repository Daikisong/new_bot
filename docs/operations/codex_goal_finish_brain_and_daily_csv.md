# Goal: 일회성 두뇌 컴파일 완료 및 장전 CSV 판단 흐름 검증

문서 갱신: 2026-10-05 08:05 KST
상태 스냅샷: 같은 시각의 프로세스·progress 파일 확인과 앞서 수행된 종료 후 read-only DB 감사를 반영했다. 실행을 다시 시작할 때는 상태를 처음부터 재확인한다.

## 최종 목표

이미 수집·repair·import·실임베딩·기초 분류한 연구자료를 다시 처음부터 처리하지 않고, 남은 고정 오프라인 합성을 이어서 검증된 Offline Semantic Brain V2 패키지로 만든다. 그 패키지를 실제 장전 CSV 흐름에 연결해, CSV와 cutoff-safe 두뇌 지식이 첫 GPT 판단 요청부터 함께 제공되고 섹터·종목 후보, 근거, 불확실성, 출처가 나오는지 확인한다.

이것은 GPT 기반 모델의 가중치를 새로 학습하는 일이 아니다. 기존 GPT 추론 모델이 사용할 수 있도록 연구에서 cutoff-safe 세계·카테고리 지식, 연결된 claims, 검색 인덱스, provenance를 한 번 만들어 고정하는 작업이다. 두뇌가 완성된 뒤 매일 원자료를 다시 해석하거나 record마다 LLM을 호출하지 않는다.

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

2026-10-05 08:05 KST 기준:

- build executor session은 exit code 1로 종료되었다. 이전 compiler Python PID 60848도 종료된 상태다.
- compile ID/worktree/source를 대상으로 새로 실행 중인 compiler process는 확인되지 않았다. 무관한 다른 프로젝트 프로세스에는 손대지 않는다.
- target DuckDB WAL 파일은 보이지 않는다.
- progress.json의 마지막 기록은 2026-10-05T07:41:37.437728+09:00이며, phase는 offline_reduce, persisted closure는 391/1,868, 남은 수는 1,477, current node 표시는 REDUCE-3823718c73c20aed453b다. 즉 DAG closure는 20.93%다. 이 비율은 연구 record coverage, LLM payload 노출률, 예상 완료 시간과 같지 않다.
- 종료 후 read-only DB 감사에서 reduce_nodes 391행과 같은 compile/source/plan identity가 확인되었다. 앞선 감사 시 mechanism_claims와 mechanism_claim_capsules는 아직 0행이었다. 패키지가 완성됐다는 뜻이 아니다.
- 직전 오류 문구는 semantic reduce claim cited an unavailable capsule이다. 앞선 시도에서 허용 ID가 정확히 일치하는 경우에만 특정 Arabic suffix를 정규화하는 좁은 수정이 들어갔고, compiler branch commit 8578372에서 Ruff, Mypy(139 files), pytest(1,934 passed)가 통과했다. 그 뒤 같은 오류가 다시 났다.
- 이번 오류의 실제 잘못된 citation 문자열, 해당 응답 trace/checkpoint, 허용 capsule 집합과의 차이는 아직 특정하지 못했다. 오류 메시지 자체는 citation 값을 노출하지 않는다. 이 값을 모른 채 정규화를 넓히거나 같은 빌드를 바로 재시작하지 않는다.
- 기존 root branch codex/quality-full-pr126의 release-binding 변경은 별도 commit 3fff4c4이며 당시 Ruff, Mypy(139 files), pytest(1,906 passed)가 통과했다. 이후 변경이 있으면 해당 worktree에서도 최종 gate를 다시 실행한다.
- 완성된 full-corpus V2 package, 실제 package로 실행한 장전 daily smoke, 같은 architecture의 정식 blind 평가, 최종 release binding은 아직 입증되지 않았다. Production 활성화는 HOLD다.

재개 직전 반드시 위 상태를 다시 확인한다. 다른 task가 build를 재개했거나 WAL이 생겼으면 새 writer를 시작하지 않는다. progress가 갱신됐으면 그 최신 증거로 본 절을 수정한다.

## 이미 끝난 단계: 다시 하지 말 것

아래 입력 작업은 동일 identity에서 이미 완료된 것으로 취급한다. 현재 error를 해결하기 위해 처음부터 반복하지 않는다.

- repair 완료 research의 production import 및 record accounting
- 실임베딩 생성과 semantic index의 기존 기반 데이터
- record assignment 및 기존 map stage/receipt
- 고정 plan의 앞선 391개 durable reducer closure와 검증된 정확 일치 checkpoint

새 input identity가 실제로 달라졌다는 증거와 별도 승인이 없는 한 import, embedding, map, planner, compile ID를 다시 만들지 않는다. 실패 시 이미 저장된 closure와 checkpoint를 보존한다.

## 실행 단계

### 1. 시작 전 single-writer 및 identity 확인

- 사용자가 goal 실행을 요청하면 process tree와 command line에서 같은 compile ID, worktree, target DB, checkpoint directory를 쓰는 build writer가 있는지 확인한다.
- 같은 writer가 살아 있으면 새 build를 시작하지 말고 그 세션만 관찰한다. 터미널 화면이 끊겼다는 이유만으로 중복 실행하지 않는다.
- 실행기가 terminal이면 exit code, progress ledger, plan/receipt/source SHA, DB compile metadata, WAL 존재 여부를 맞춘다.
- live writer가 있거나 WAL이 있으면 해당 DuckDB의 read-only inspection, hashing, parity scan을 하지 않는다.
- 종료 후에도 plan/source/receipt/target/checkpoint identity가 바뀌었거나 ledger와 DB가 불일치하면 재실행하지 말고 mismatch를 먼저 진단한다.

### 2. 반복 citation 오류를 정확히 진단하고 좁게 고친다

실패한 node REDUCE-3823718c73c20aed453b에 대해:

- 해당 reducer의 purpose, input SHA, child lineage, trace와 checkpoint ID를 고정 plan 및 마지막 실패 시각과 연결한다.
- 응답이 checkpoint에 있으면 input SHA, model, prompt/schema/compiler identity가 정확히 맞는 경우만 읽는다. 어떤 output이 실패를 냈는지 확인할 수 없으면 코드/trace에서 실패 값을 보존하는 진단을 추가한 후 재현한다.
- plan과 child reducer의 evidence_capsule_ids로 이 node의 exact allowed capsule 집합을 재구성한다.
- 잘못된 citation 원문/code point, claim index, 허용 집합과의 차이를 기록한다.
- 입력에 실제로 허용된 capsule ID에 특정 출력 suffix만 붙은 것이 확인되는 경우에만 그 exact normalization을 허용한다. 임의 ID, 근사 일치, 비슷한 suffix, 다른 node로 확장하지 않는다. 정규화 전·후 값과 사유는 감사 trace에 남긴다.
- 원문과 정규화 값, 허용 set membership, 임의 ID 거부를 검증하는 focused regression test를 추가한다. 전체 경로에 validator를 약하게 만들지 않는다.
- 변경은 compiler worktree에서 하고 Ruff, Mypy, 관련 테스트와 pytest 전체를 통과시킨다. code/diff를 이해하고 필요한 Korean commit/push를 한다. 사용자 소유 변경과 untracked output은 건드리거나 되돌리지 않는다.
- 원인이 특정되지 않거나 허용 집합 검증이 불가능하면 이 지점에서 멈춰 추가 증거를 수집한다. 단순히 예외를 무시하거나 해당 claim을 버리고 진행하지 않는다.

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

실행 요청이 오면 먼저 저장소 AGENTS.md·skill과 이 문서의 상태를 대조하고, 현재 process/session/ledger/trace/receipt/manifest/WAL를 다시 확인한다. live process가 있으면 중복 시작하지 않고 관찰한다. terminal 상태면 exact failed citation을 먼저 진단하고 최소 수정한 다음, 같은 compile identity를 재개한다. 검증된 import·embedding·map과 durable DAG closure를 재생성하지 않는다. package audit, 실제 daily smoke, 동일 architecture blind 평가, release binding, tests, Korean commit/push와 외부 리뷰용 보고까지 진행한다. 등록 품질 gate와 승인이 없으면 production activation은 HOLD한다.
