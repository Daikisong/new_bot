# Goal: One-Time Brain, Daily Pre-Open CSV Decision

문서 갱신: 2026-10-05 KST. 최신 process 확인 07:22 KST, 최신 progress ledger 07:22:20 KST.

이 문서는 현재 저장소 상태를 이어받아 사용자가 원하는 운영 결과까지 완결하기 위한 실행 지시서다. 실행할 때마다 이 문서의 진행 숫자를 사실로 간주하지 말고 실제 프로세스, progress ledger, trace, receipt, manifest, DB를 먼저 확인한다. 이미 끝난 단계는 반복하지 않는다.

## 목표

이미 import하고 real embedding을 만든 연구 자료로 durable Offline Semantic Brain V2를 한 번 합성한다. 이후 사용자가 장전 CSV와 날짜/cutoff를 주면, 기존 brain과 cutoff-safe retrieval을 먼저 불러온 뒤 현재 뉴스와 함께 정상 GPT 결정 요청 한 번으로 주도 섹터와 근거가 충분한 종목 후보를 판단한다. 일일 경로에서 과거 원료 전체를 다시 해석하거나 record별 LLM 작업을 만들지 않는다.

완성물은 GPT 기반 모델의 가중치를 새로 학습한 파일이 아니다. cutoff-safe 세계/카테고리 합성 지식, 근거가 연결된 claims, 검색 인덱스, lineage와 package manifest를 기존 GPT 런타임에 연결하는 durable brain package다. Smoke나 구조적 coverage만으로 투자 판단 성능 또는 운영 활성화를 주장하지 않는다.

성공한 장전 실행은 날짜와 cutoff, 주도 섹터/테마, 근거가 있을 때만 순위화한 종목, 뉴스-회사 연결 이유, 과거 evidence citation, confidence/불확실성, 위험·무효화 조건, provenance/context manifest와 결과 파일 위치를 제공해야 한다. 근거가 부족하면 종목을 지어내지 않는다.

### 완료율과 완료 조건의 정의

- 이 goal은 고정된 offline DAG를 닫고, immutable brain package를 검증하고, 실제 daily 경로와 배포 예정 architecture의 평가를 마치는 유한 작업이다. 연구를 무한히 재해석하는 작업이 아니다.
- `823,279 records`는 입력 회계 모집단, `52,644 semantic units`는 의미 단위 수다. 어느 쪽도 brain synthesis 완료 건수나 직접 LLM 노출 건수가 아니다.
- 완료율로 쓸 수 있는 진행치는 고정 DAG의 `completed_model_node_count / total_model_node_count`뿐이며, 이름은 DAG closure로 한정한다. fresh provider 결과, 검증된 exact checkpoint 재사용, local carry/validation, 오류, 대기를 각각 분리한다.
- Offline synthesis는 모든 고정 DAG node가 source/plan/checkpoint identity 및 citation/child lineage 검증을 통과해 durable package에 반영되고 package integrity까지 통과해야 완료다. 노드 수만으로 의미 품질 완료를 선언하지 않는다.
- 일일 제품 흐름은 `기존 brain + cutoff-safe 관련 지식 + 오늘 CSV -> GPT 최종 판단 1회 -> 섹터/테마와 근거 있는 종목 후보 및 citation`이다. brain-free 1차 해석 호출은 없다. 구조 복구 호출은 최대 1회다.
- 이것은 GPT 가중치를 새로 학습한 모델이 아니다. 기존 GPT가 재사용하는 합성 지식·근거·검색 인덱스 package다. 완료 보고와 외부 리뷰에서 두 의미를 혼동하지 않는다.

### 사용자가 이전 진행에 불만을 느낀 이유와 실행 교훈

- 사용자는 “밤새 받은 CSV를 이미 연구자료로 만든 두뇌에 넣으면, GPT가 그 두뇌와 뉴스를 함께 보고 장전 추천을 한다”는 제품을 기대했다. 보고는 여러 날 동안 이 유한 제품을 어떤 고정 산출물로 만드는지, 몇 단위가 남았는지, 성공하면 daily CSV에서 무엇이 달라지는지를 설명하지 못했다.
- `823,279개 record 처리`, `52,644개 unit`, `12,487개` 같은 서로 다른 분모·단계의 숫자가 완료율처럼 섞여 전달됐다. 입력 accounting이 끝난 것을 brain 완성률로 오해하게 만들었고, 추정 ETA도 짧고 불안정한 구간에서 반복해 바뀌었다. 이로 인해 같은 작업을 반복하거나 끝없는 실험을 하는 것처럼 보였다.
- 앞으로는 매번 정확한 numerator/denominator, DAG closure와 fresh/reused/error/wait node 구분, 이번 단계가 CSV 실행에 제공하는 구체 기능, 남은 유한 산출물을 함께 적는다. 입력 회계·embedding·검색 색인을 의미 합성이나 추천 성능으로 부르지 않는다. 단기 속도만으로 완료 ETA를 만들어내지 않는다.
- 외부 리뷰도 “DB에 저장됐나”만 확인하지 않는다. 연구의 positive/negative/near-miss/희귀 메커니즘이 lineage와 함께 합성되고, 실제 daily one-call 입력에 cutoff-safe 지식과 근거로 들어가며, 같은 architecture 평가에서 누수 없이 동작하는지 확인한다. 모든 record가 LLM에 직접 전달됐다고 주장하지 말고, 직접 노출·간접 대표·claim 영향·daily retrievability를 분리해 증명한다.
- 작업의 다음 단계가 실제 daily 출력에 어떤 기능을 추가하는지 설명할 수 없거나 고정된 완료 조건이 없다면, 새 대규모 실행을 시작하지 말고 그 불일치를 먼저 보고한다.

## 이미 확인된 원료와 완료 작업

- 전체 원료 프로젝트: C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project
- Memory snapshot: MEMIDX-1e64a1b6e6ba7b07b799
- 실제 source manifest SHA-256: 6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576
- record root: 2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75
- 전수 accounting 모집단: 823,279 records. 현재 확인된 날짜 범위는 2018-01-03~2026-06-19다.
- 기존 assignment/semantic capsule 구조: 52,644개 semantic unit. 이는 연수, 거래일 수, LLM이 각각 읽은 연구자료 수 또는 1:1 요약 수가 아니다.
- 기존 real embedding, memory import/index, record assignment와 map stage는 완료된 자산이다. 이들을 다시 만들지 않는다.
- 823,279개와 날짜 범위만으로 10개년의 모든 거래일/연구자료가 빠짐없이 존재한다고 말할 수 없다. package 감사에서 연도별 실제 coverage와 gap을 보고한다.
- repaired corpus의 구조적 accounting 완료는 모든 payload가 LLM에 노출되었거나 각 record가 final brain에 의미 영향을 줬다는 증거가 아니다. 구조, 실제 LLM payload exposure, 근거가 있는 claims, daily runtime retrieval을 별도 수치로 보고한다.

## 이전 상태 스냅샷: 2026-10-05 06:21 KST (이후 오류로 중단됨)

아래는 당시 관측 기록이다. 이후 reducer 오류로 process가 종료됐으므로, 이 section의 PID/progress/current node는 현재 상태가 아니다. 최신 정보는 바로 다음 `현재 재개 상태`를 기준으로 하고, goal을 이어갈 때도 실제 process와 ledger를 다시 확인한다.

- Full-corpus synthesis는 compiler worktree `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b`에서 동일 compile `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`을 실행 중이다. Python PID `12492`(시작 `04:57:55 KST`)는 06:21:40 KST에 살아 있었고 executor session `38263`은 06:22 KST poll에 응답했다.
- 현재 단계는 `offline_reduce`다. 중단 전의 source, plan, map receipt, checkpoint directory, compile ID는 바꾸지 않았다.
- 고정 DAG는 총 1,868 nodes(reducer 1,858, category review 9, world root 1)다. ledger는 263/1,868(14.08% DAG closure), 남은 closure 1,605, current node `REDUCE-cc970084d4f760563db6`, 마지막 갱신 `06:21:36 KST`를 기록했다. 이는 고정 node closure 비율이지 corpus 의미 이해율, 품질 점수 또는 남은 시간 비율이 아니다.
- record_progress_ratio=1.0은 823,279 input records의 accounting 단계만 끝났다는 뜻이다. brain 합성 완료율이 아니다. DAG closure 수는 record 수나 실제 GPT 호출 수와도 같지 않다. 성공한 정확 일치 checkpoint, fresh LLM 결과, local carry/검증을 각각 trace와 ledger에서 계산한다.
- 이전 reducer 오류는 `semantic reduce claim cited an unavailable capsule`였다. checkpoint `LLMCKPT-25cbe98626525c18`의 `CAP-69a102da42987718cbbd` 뒤에 붙은 U+00AD+U+2014만, 그 prefix가 해당 node의 exact allowed set에 있을 때 정규화한다. 원문과 규칙을 audit field에 남긴다. 재개 trace `TRACE-64057b7edac3.json`은 이 node의 checkpoint hit를 기록했다. 해당 normalization row는 종료 후 DB read-only 검사로 확정한다.
- 이전 04:00 KST 시도의 Codex child chain PID 15684 -> 59560 -> 58984는 historical reference일 뿐이다. 새 실행 때 이 PID들을 살아 있다고 가정하거나 제어하지 않는다. 현재 process tree는 fresh command-line/parent inspection으로 식별하고, 어떤 프로세스 제어 전에도 대상이 이 프로젝트 소유인지 확인한다. 보호된 Bithumb process tree는 건드리지 않는다.
- 05:14~05:37 KST에 새로 완료된 trace 41개는 모두 status `ok`: fresh reducer 출력 40개와 `offline_category_review.continuation` 1개이며, 이 구간의 checkpoint hit와 trace 오류는 0개다. provider/model/effort는 모두 Codex OAuth `gpt-6.1-sol/high`. trace token estimate 합계는 prompt 5,363,619 / completion 81,646이며 청구량이 아니다. 가장 긴 완료 trace는 299.2초였다. DAG closure는 같은 구간 153에서 197 이상으로 늘어 node 수와 trace 수는 1:1이 아니다.
- 05:37~05:46 KST에 추가 완료된 11 trace는 모두 fresh `offline_semantic_reduce` status `ok`; checkpoint hit와 trace 오류는 0개다. prompt/completion 합산 추정치는 1,654,193 / 23,579 tokens, 가장 긴 trace는 229.3초였다. 청구량이 아니다.
- 05:46:00~05:59:04 KST에 기록된 trace는 22개이며 모두 `status=ok`, Codex OAuth `gpt-6.1-sol/high`다. 확인된 각 trace의 실제 input/output 및 node identity를 보존한다. trace 수와 DAG closure는 1:1이 아니므로 이 숫자를 진행률로 쓰지 않는다.
- 05:59:04~06:09:35 KST에는 새 reducer trace 17개가 모두 `status=ok`; checkpoint hit/trace error는 0개다. 모두 Codex OAuth `gpt-6.1-sol/high`이며 trace usage 추정 합계는 prompt 2,351,597 / completion 35,789 tokens다(청구량 아님).
- 06:09:35~06:21:40 KST에는 새 reducer trace 21개가 모두 `status=ok`; checkpoint hit/trace error는 0개다. 모두 Codex OAuth `gpt-6.1-sol/high`이며 trace usage 추정 합계는 prompt 2,868,691 / completion 47,597 tokens다(청구량 아님).
- Codex child PID `73956`은 06:21:40 KST에도 살아 있었다. ancestry는 compile PID `12492` -> `cmd.exe` PID `85176` -> `node.exe` PID `42708` -> `codex.exe` PID `73956`이고 요청 시작은 `05:20:59 KST`다. `last-message.txt`는 아직 없고, sample은 private memory 약 74 MiB, CPU 누적 32.6초였으며 그 시점 established TCP 연결은 관측되지 않았다. 같은 시간 동안 다른 reducer trace와 DAG closure가 진행됐으므로 이 child를 시간만으로 실패 처리하거나 종료하지 않았다. 재개 시 이 child의 output, trace, ledger 및 연결/자원 상태를 다시 확인한다.
- 별도 BUILD-only v6 plan-offline은 완료됐고 현재 프로세스는 종료됐다. 결과: C:\Users\eorb9\projects\nslab_semantic_upgrade_v7_eval_v2\project\diagnostics\offline_brain_v2_build_only_v6_plan_20261005.json; SHA-256 EA58A3DC9FCF269465BE405F4949973ECA3F0B8D44AD716212FB36E15DFCF52D; plan ID OFFLINE-PLAN-a2b58918e1a1020137fe. 입력 snapshot MEMIDX-4409624afdffd1d01018, record root은 full corpus root와 같고, record count 759,308, semantic unit 49,385다.
- 이 v6 계획은 provider Codex OAuth / gpt-6.1-sol / high, concurrency 4, planning LLM call 0, import/embedding reuse true, silent truncation 0, payload truncation 0이다. 대표 payload full-read는 170,333 records(계획 모집단의 약 22.43%)다. 예상 logical LLM call 7,257은 projection이며, actual call 수나 ETA가 아니다. 기존 memory current pointer의 SHA 105d64c6ea2a2b53f317fd0d543c94324c23767fd90d854f2ef1abe59747cdf0은 실제 snapshot manifest SHA와 다르다. plan은 실제 SHA f47cac17eb3f97bf856e358c078eca023e12d6cefce6a044878dcd87ab4e4f41을 override로 명시하고 attested=true로 기록했으며 pointer를 수정하지 않았다.
- BUILD-only package build는 full-corpus build와 서로 다른 target이어도 공용 checkpoint 디렉터리를 쓸 수 있다. 현 LLM checkpoint writer는 JSON을 직접 기록하므로 동일 cache를 쓰는 두 build를 겹치지 않는다. 현재 full-corpus build 종료 및 정합성 검증 뒤 evaluation build를 시작한다.
- Full-corpus compiler worktree의 최신 compiler gate는 제한된 citation suffix 수정 후 Ruff PASS, Mypy 139 files PASS, pytest 1,934 passed(1,325 warnings)다.
- Main repo의 V2 release binding과 package-pointer 검증 변경은 보존한다. generated release 및 V6 semantic JSON schemas를 exporter로 갱신했고 schema parity가 통과했다. Compiler V6가 추가한 `close_return_status_distribution`과 influence manifest commitment 필드를 main reader가 허용·회계하도록 backport했고, daily population statistics 반영 및 회귀 테스트를 추가했다. Signed release identity binding과 rollback helper 검증을 위해 임시 fixture에서 release A -> B -> rollback A를 검사하는 `test_rollback_reactivates_a_verified_previous_release`를 추가했다. 최신 코드 상태에서 Ruff PASS, Mypy 139 files PASS, 전체 pytest 1,906 passed/1,208 warnings/300.72 sec로 모두 통과했다. 이후 compiler integration이나 다른 코드 변경이 있으면 commit 전에 세 gate를 다시 실행한다. dirty diff와 기존 사용자 변경을 보존한다.
- 실제 full-corpus V2 package, 그 package를 사용하는 CSV smoke, 동일 architecture formal blind 평가, 실제 package root을 사용한 end-to-end signed-release inspection은 아직 완료 증거가 없다. 등록 품질 gate와 승인 전 production activation은 HOLD다.

## 현재 재개 상태 (2026-10-05 07:22 KST)

- Full-corpus synthesis는 compiler worktree `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b`에서 기존 compile ID `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`로 재개되어 있다. 07:22 KST 확인 시 PowerShell PID `77968`(06:42:14 시작) 아래 Python PID `60848`(06:42:15 시작)이 동일 source, target DB, checkpoint directory로 `build-offline --continue-after-map-plan`을 실행 중이었다. 절대로 중복 시작하지 않는다.
- 고정 DAG는 총 1,868 nodes(reducer 1,858, category review 9, world root 1)다. 최신 progress ledger는 07:22:20 KST 기준 `356/1,868` closed, `1,512` remaining, 현재 node `REDUCE-58c3feaf2494aa9cadcc`를 기록한다(고정 DAG closure `19.06%`). 재개 직전 검증 closure 267에서 ledger상 89개 node closure가 늘었다. 이는 node closure 비율이지 corpus 의미 이해율, 품질 점수, fresh LLM 호출 수 또는 남은 시간 비율이 아니다. fresh/checkpoint/local carry 세부는 trace와 종료 후 ledger audit로 확인한다.
- Python private memory는 07:22 KST 표본에서 약 4.11 GB였다. 직전 관측 구간과 큰 변화가 없었다. 이를 전체 실행 내내 누수가 없다는 증명으로 확대하지 않고 계속 표본 확인한다.
- Record ratio `1.0`은 record accounting 완료만 뜻하며 synthesis 진행률이 아니다. 빌드가 live인 동안 target DuckDB/WAL을 별도 연결로 열거나 hash/count 검사하지 않고, 같은 대상에 writer를 추가하지 않는다. 재개 시점에 target WAL이 존재했다.
- 직전 실패는 `semantic reduce claim cited an unavailable capsule`였다. read-only 감사에서 trace `TRACE-718730099afa`와 checkpoint `LLMCKPT-4a6a1cbded76a0fb`의 input/output hash는 일치했다. 허용된 capsule ID 하나에 Arabic word suffix `عند`가 붙은 citation만 일치하지 않았다. compiler 수정은 해당 prefix가 node의 exact allowed set에 있을 때 정확한 suffix 형식만 정규화하고, 원문/규칙을 `SemanticReduceCitationNormalization` 감사 row에 남긴다. 임의 ID나 suffix는 여전히 거부한다.
- 위 제한 수정과 회귀 테스트는 compiler branch의 한국어 commit `8578372`로 push됐다. compiler branch gate는 Ruff PASS, Mypy 139 files PASS, pytest 1,934 passed(1,325 warnings). main branch의 V2 release binding/rollback 수정은 한국어 commit `3fff4c4`로 push됐고, 그 branch gate는 Ruff PASS, Mypy 139 files PASS, pytest 1,906 passed(1,208 warnings). 두 변경은 별도 worktree/branch에 있으며 private brain/data는 push하지 않았다.
- source manifest SHA-256 `6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576`, record root `2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75`, 823,279 records, 52,644 semantic units는 기존 검증 identity다. import, embedding, map과 이미 유효한 DAG node를 재생성하지 않고 이 identity 및 checkpoint를 재사용한다.

## 반복 금지와 빌드 규칙

1. 시작 즉시 full build와 v6 planner의 live process command line, PID, 최신 progress, output 파일을 확인한다. 해당 작업이 살아 있으면 관찰/이어받기만 하고 동일 작업을 추가 실행하지 않는다. 화면 세션이 사라져도 OS process가 살아 있을 수 있다.
2. 동일 source, plan, checkpoint, target identity를 유지한다. terminal 상태일 때만 exit code와 persisted ledger/DB/WAL/receipt를 확인한다. 실패면 오류 원인과 미저장 closure를 확인하고 같은 compile을 좁게 수정 후 재개한다.
3. 연구 import, embedding 생성, 전체 map-only compile, 이미 유효하게 저장된 DAG node는 재실행하지 않는다. 5.6 checkpoint를 재사용한 결과와 6.1 fresh 결과가 섞일 수 있으므로 node별 실제 model/provider/prompt/input/checkpoint provenance를 그대로 보존한다. 결과 전체를 6.1 생성이라고 단정하지 않는다.
4. Full build의 기존 source manifest SHA, fixed plan/topology, receipt, usage ledger, DB metadata와 persisted rows가 맞지 않으면 새 LLM 호출 전에 멈춘다. receipt/hash를 맞추려고 편집하지 않는다. 실행 중인 target DuckDB/WAL을 별도 writer로 열거나 hash 계산을 하지 않는다.
5. OAuth는 공식 Codex CLI 세션을 사용한다. credential 파일을 열거나 복사하지 않는다. quota guard를 임의로 추가하거나 실제 provider 오류 없이 quota 문제라고 추측하지 않는다.
6. bounded concurrency와 메모리/디스크/heartbeat를 관찰한다. 유효한 느린 요청만으로 build를 중단하지 않는다. 실제 고갈, 손상, 같은 node의 비정상 무진전, 무한 재시도 증거가 있을 때 증거를 저장하고 원인을 좁게 처리한다. 같은 checkpoint directory를 쓰는 별도 build는 동시에 실행하지 않는다.
7. 진행 보고는 고정 DAG의 closed / total, 남은 closure, fresh 성공, exact checkpoint reuse, 실패, 대기 수를 구분한다. record ratio를 brain 진행률로 부르거나 불완전한 표본의 속도로 ETA를 단정하지 않는다.

기존 full-corpus compile의 재개 identity는 다음과 같다. 실행 중인 PID가 있으면 이 명령을 다시 실행하지 않는다.

    $worktree = 'C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b'
    Set-Location $worktree
    $env:NSLAB_LLM_PROVIDER = 'codex-oauth'
    $env:NSLAB_LLM_MODEL = 'gpt-6.1-sol'
    $env:NSLAB_CODEX_MODEL = 'gpt-6.1-sol'
    $env:NSLAB_CODEX_REASONING_EFFORT = 'high'
    $env:NSLAB_MAX_CONCURRENCY = '4'
    $env:PYTHONPATH = (Join-Path (Get-Location).Path 'src')

    python -m news_scalping_lab.cli brain build-offline --source-project 'C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project' --expected-manifest-sha256 '6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576' --checkpoint-dir 'C:\Users\eorb9\projects\news_bot\runs\checkpoints\llm' --compatible-checkpoint-model 'gpt-5.6-sol/xhigh' --continue-after-map-plan

## 완료 단계

### 1. 기존 full-corpus 합성 마무리

- compile ID `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`의 현재 process tree와 command line을 찾아 같은 작업인지 확인한다. 살아 있으면 동일 명령을 재실행하지 말고 해당 실행을 관찰한다. 종료된 경우에만 exit code 및 target DB/WAL/receipt를 검증한다.
- progress ledger의 DAG 카운터가 null이거나 낡았으면 진행률을 추측하지 않는다. 마지막으로 검증된 closure와 관측 시각을 분리해 보고하고, 현재 수치는 새 ledger/trace에서 입증될 때만 갱신한다.
- 같은 fixed plan의 모든 model-task node가 유효 결과 또는 검증된 exact checkpoint로 closure되었는지 확인한다. 누락 citation, 예상 외 source identity, 실패 node, child mismatch가 있으면 PASS 처리하지 않는다.
- plan/receipt/topology/source/target/WAL/usage trace와 checkpoint identity를 대조한다. 실제 모델별 fresh/reused 결과, prompt/completion token, 오류를 증거에서 집계한다.
- record assignments, semantic capsule/unit coverage, actual payload exposure, 생성 claims/citations를 별도 감사한다.

### 2. immutable V2 package와 의미 감사

- compiler가 완결된 뒤 evaluation-only V2 package를 생성하고 version/root, input source, compiler/model/prompt/schema identity, package receipt를 고정한다. select-offline-evaluation은 production activation이 아니다.
- package standalone/deep verification, real-provider embedding과 실제 HNSW query plan, citation/source membership, duplicate/missing/unknown type, read-only parity를 검사한다.
- 최소한 다음 네 숫자/목록을 독립적으로 보고한다: structural record accounting, stage별 실제 payload exposure, source citation이 유효한 claim, daily runtime에서 검색·citation 가능한 원료.
- 가능한 경우 기존 sample export 명령으로 외부 표본팩을 만들고 seed/명령/hash를 보고한다. 표본은 전수 검증을 대체하지 않는다.

### 3. 실제 daily CSV 경로 확인

- 구현된 analyze-daily 경로를 사용한다. legacy exhaustive analyze로 우회하지 않는다.
- current news를 cutoff-safe capsule로 만든 뒤 첫 LLM call 전에 compiled world/category guidance와 뉴스 기반 relevance/citation retrieval을 함께 로드한다. 정상 호출은 단 하나의 final_market_decision; structured repair는 최대 한 번이다.
- HNSW/reader는 bounded, read-only, citation-bearing이어야 한다. raw corpus 전체 scan, 과거 연구를 재해석하는 일일 LLM fan-out, record/cluster/lane 루프 안의 LLM 호출을 금지한다.
- CSV_MEMORY_ONLY_STRICT를 유지한다. 일반 웹, D-day 가격, cutoff 이후 정보, 미래 company relation, 결과(outcome) 정보가 BLIND 입력에 들어가면 HOLD다. 출력에는 provenance/context manifest를 남긴다.
- 실제 package를 사용한 날짜/cutoff 명시 smoke를 실행한다. 기존 post-cutoff smoke selection QSEL-732b1496e39c4e23de30을 쓸 수 있으면 활용하되, smoke를 backtest 점수로 부르지 않는다.
- 실제 elapsed time, model/provider, call/repair 수, retrieval citations, output/report 위치를 기록한다. 사용자가 정하지 않은 latency 목표를 만들지 않는다.

### 4. 배포 구조와 동일 architecture의 blind 평가

- Full-corpus package에는 holdout-era knowledge가 섞일 수 있으므로 이것을 formal evaluator의 BUILD brain으로 재사용하지 않는다.
- 기존 evaluation-only replay snapshot MEMIDX-4409624afdffd1d01018을 재사용한다. 이 snapshot은 759,308 BUILD records이고 2026-01-02 KST cutoff를 사용한다. 기존 sealed selection은 CALIBRATION 40 cases, HOLDOUT 40 cases이며, 해당 split과 input hash를 바꾸지 않는다.
- Model identity를 역할별로 구분한다. 현재 offline full-corpus/BUILD-only compiler identity는 Codex OAuth `gpt-6.1-sol/high`다. 코드상 formal `QUALITY_FULL` daily A/B/C runtime identity는 Codex OAuth `gpt-5.6-sol/xhigh`로 고정돼 있다(`contracts/quality_evaluation.py`). 둘을 같은 모델이라고 보고하지 않는다. daily runtime을 6.1/high로 바꾸려면 formal profile과 등록 평가 계약을 먼저 명시적으로 변경하고 그 설정으로 전체 blind 평가를 다시 해야 한다.
- 진행 중인 v6 planner의 결과를 먼저 확인한다. 오래된 v5 또는 deterministic-mock plan을 v6 build 근거로 재사용하지 않는다. 필요한 BUILD-only V2 compile은 이 snapshot만 입력으로 사용하고 import/embedding을 반복하지 않는다.
- CALIBRATION/HOLDOUT 모두 production에서 사용할 동일한 one-call, brain-loaded daily architecture로 blind prediction한다. 모든 arm과 prediction seal이 닫히기 전에 outcome 파일/필드를 읽지 않는다. prediction을 고친 후 outcome을 열지 않는다.
- smoke와 formal score를 구분해 기존 registered metrics를 보고한다. repository의 A/B/C scorer는 현재 HOLD_FOR_REGISTERED_QUALITY_GATES_AND_EXTERNAL_REVIEW를 반환하며 이 architecture용 numeric promotion threshold registry가 확인되지 않았다. 기준·수익률·정확도·latency threshold를 임의로 만들지 않는다. 기준이 등록되기 전 activation은 HOLD다.

### 5. Release binding, rollback, 검증 및 handoff

- 현재 production/release.py는 legacy brain manifest를 release artifact에 묶고 V2 brain_package_pointer와 V2 root를 signed release manifest에 고정하지 않는 gap이 확인됐다. 이 gap을 코드와 회귀 테스트로 fail-closed 처리한다. audited V2 package root/pointer, quality attestation, runtime이 실제로 같은 package를 읽는지 release manifest에서 추적 가능해야 한다.
- 등록 품질 gate 통과 및 필요한 사용자 승인 전에는 production activation 또는 shadow promotion을 하지 않는다. 미등록 기준이 남으면 명시적으로 HOLD하고, 무엇을 등록/승인해야 하는지 적는다.
- 실제 이전 버전 rollback target, rollback 명령, secrets-free provenance와 복귀 테스트를 준비한다. activation이 금지된 동안에는 이 절차를 검증만 하고 실행하지 않는다.
- 운영자가 승인한 경우 rollback CLI는 `python -m news_scalping_lab.cli production rollback --release-id <verified-previous-release-id>`다. promotion HMAC key는 기존 settings/environment 경로에서만 읽고 명령 인자·로그·Git에 포함하지 않는다. 이 goal 실행 중 실제 production pointer를 바꾸지 않는다.
- compiler worktree의 변경은 diff와 소유권을 확인한 뒤 통합한다. 사용자 수정 사항을 reset/revert하지 않는다. 전체 작업 후 아래 gate를 실행한다.

    python -m ruff check .
    python -m mypy src/news_scalping_lab
    python -m pytest

- relevant code/docs만 한국어 commit message로 commit/push한다. 대형/private brain, DB, 연구자료, run artifact를 Git에 올리지 않는다. 원격 branch/PR 상태는 인증된 사용 가능한 수단으로 확인하며 gh CLI 자체를 완료조건으로 만들지 않는다.

## 이 Goal의 종료 판정

완료 보고는 package 생성, daily smoke, formal blind score, release binding, production activation을 각각 구분한다. 전부 자동으로 한 상태로 뭉뚱그리지 않는다.

- Full build/package의 무결성, 의미 감사, 실제 daily CSV smoke, 동일 architecture formal blind prediction/score, V2 release binding, code gates, 한국어 commit/push가 모두 증거로 확인되어야 기술 작업 완료다.
- 등록된 quality/promotion criteria가 없거나 통과하지 않으면 production activation은 HOLD로 남긴다. 이 경우 goal이 운영 배포까지 끝났다고 보고하지 않는다. 누락된 기준과 사용자 결정을 명확히 남긴다.
- 다음 장전 실행은 package가 승인/선택된 경우 CSV/date/cutoff를 넣어 brain을 재사용하며, import, embedding, map, corpus-wide synthesis를 반복하지 않는다. 연구자료가 새로 들어오면 별도의 검증된 incremental update만 수행한다.

## 최종 인계물

1. 고정 source와 build/package identity, verified package root 및 integrity reports
2. structural coverage, 실제 LLM exposure, citations/claims, runtime retrievability, 연도별 source coverage
3. 날짜/cutoff가 기록된 daily CSV smoke 결과, report/prediction/manifest 경로, 실제 latency 및 call 사용량
4. CALIBRATION/HOLDOUT A/B/C 결과와 leakage/quality gate 상태
5. V2 signed-release binding 상태, activation HOLD 또는 승인 증거, rollback 절차
6. 필요한 commit/push와 원격 상태
7. 다음 CSV를 실제로 처리할 정확한 명령과 입력 예시

## 함께 읽을 문서

- 저장소 AGENTS.md
- .agents/skills/news-scalping-lab/SKILL.md
- docs/operations/one_time_brain_daily_inference_intent.md
- docs/operations/offline_brain_thin_daily_architecture.md
- docs/operations/offline_semantic_brain_v2.md
- diagnostics/offline_brain_recovery_20261004.md

## 실행 요청

사용자가 이 goal 실행을 요청하면 계획만 다시 제출하지 않는다. live process와 최신 증거를 먼저 읽고, 위 순서대로 기존 합성의 완결부터 package, daily 경로, 동일 architecture 평가, release binding, 테스트, 한국어 commit/push까지 계속 진행한다. 둘 중 하나라도 live이면 중복 실행하지 않는다. 안전한 activation 기준이 없다면 그 선을 지키고 실제 달성 범위를 정확히 보고한다.

사용자가 다음에 그대로 보낼 수 있는 요청:

> C:\Users\eorb9\Downloads\codex_goal_nslab_finish_brain_and_daily_csv.md 최신본을 실행해. 저장소 AGENTS.md와 참조 문서를 먼저 읽고, 문서의 PID·progress는 관측 스냅샷으로 취급해 현재 process tree, session, ledger, trace를 다시 확인해. 같은 full-corpus compile이 살아 있으면 절대로 중복 실행하지 말고 그 handle을 이어서 관찰해. ledger DAG 카운터가 null이면 현재 퍼센트를 추측하지 말고 마지막 검증 closure와 미확인 상태를 구분해. terminal이면 source/plan/receipt/checkpoint identity와 저장된 closure를 읽기 전용으로 검증한 뒤 같은 compile을 재개해. 이미 완료된 import, embedding, map과 유효 node는 재생성하지 말고 checkpoint hit와 fresh provider output을 분리해 집계해. citation/lineage 오류는 입력과 허용 ID를 확인해 좁게 수정하고 원본 trace/checkpoint를 보존해. 전체 DAG/package가 검증되면 built package CSV smoke, BUILD-only 동일 architecture blind 평가, V2 release binding, schema parity와 전체 Ruff/Mypy/pytest, 한국어 commit/push까지 수행해. 각 단계가 daily CSV에 제공하는 기능과 고정 분모 기준의 남은 closure를 보고해. 직접 LLM 노출과 record coverage를 혼동하지 말고, 미등록 품질 기준을 만들거나 production을 임의 활성화하지 마.
