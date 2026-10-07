# Goal: 검증된 두뇌 패키지로 장전 CSV 판단 완성

문서 갱신: 2026-10-08 KST

## 이 Goal의 목적

이미 끝난 1회성 연구 두뇌 컴파일을 다시 돌리지 않는다. 존재하는 Offline Semantic Brain V2 패키지의 계보와 검색 경로 감사를 마치고, 실제 cutoff-safe 장전 뉴스 CSV를 production `analyze-daily`에 넣어 두뇌와 CSV가 첫 GPT 판단 요청부터 함께 쓰이는지 검증한다. 같은 구조의 정식 blind 품질 gate가 확인되면 그 상태도 판정하되, 품질 승인과 사용자 승인 없이 production을 활성화하지 않는다.

최종 사용 흐름은 `장전 CSV + 이미 만들어진 두뇌/인덱스 -> GPT의 단일 판단 요청 -> 주도 섹터·종목 후보, 근거·인용·불확실성·출처`다. GPT 가중치를 fine-tune한 새 모델을 만드는 작업이 아니다. 원료 전체를 매일 재해석하거나 record·cluster·lane마다 GPT를 호출하지 않는다.

현재 요약: 기존 Offline Brain V2 패키지와 계보 감사는 PASS이며 재컴파일하지 않는다. XKRX 거래일 달력으로 장전 window를 고쳤고, 전달 폴더의 거래일 CSV 29개(39,898행)는 새 window 안에 있으며 cutoff 이후 게시 행은 0이다. 9월 28일 CSV로 격리 프로젝트에서 현재 코드의 전체 `analyze-daily` CLI를 실행했다. 고정 brain root를 로드하고 1,627행을 1,568개 capsule로 분석했으며, `gpt-5.6-sol/xhigh` 1회 판단 요청·repair 0회로 9개 후보와 5개 섹터를 만들었다. Web/import/rebuild/full-corpus scan은 모두 0이고, 후보·섹터 event IDs 및 후보 source-row 연결 검증이 통과했다. 전체 소요는 452.55초였다. 이는 10월 8일에 실행한 historical functional smoke이지 실시간 prediction이나 예측 성능 평가가 아니다. 정식 blind quality gate는 `NOT_RUN_GATE_MISSING`, 예측 품질은 `UNAPPROVED`, production은 `HOLD`다. 폴더에 `news_20261007.csv`가 없고 CSV에 `collected_at` 필드도 없다.

## 이미 완료된 작업

| 항목 | 확인된 결과 | 해석 |
|---|---|---|
| 고정 compile | `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`, 완료 시각 `2026-10-06 19:16:34 KST`, 약 16시간 39분 | 같은 작업의 import·repair·embedding·map과 유효 checkpoint를 재사용해 마친 1회성 합성이다. 다시 실행하지 않는다. |
| 산출 패키지 | `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b\brain\packages\brain-v2-993b42487c557ca1` | brain version `brain-v2-993b42487c557ca1` |
| 패키지 commitment | package root `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd`; manifest SHA-256 `3286247ce9271064455f702d1e44f8fdda4eb3659455f14e44517937da048378` | 이후 검증은 이 identity에 고정한다. |
| 입력 identity | source manifest SHA-256 `6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576`; record root `2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75`; memory snapshot `MEMIDX-1e64a1b6e6ba7b07b799` | source project는 `production/staging/P9IMPORT-3D770A7DD72457C97098/project`다. |
| 입력 규모 | 823,279 records, 52,644 semantic capsules, 1,868 DAG nodes | 서로 다른 단위다. 52,644는 연도 수도, 원문별 GPT 요약 수나 개별 GPT 호출 수도 아니다. |
| 의미 payload 노출 | 181,979 / 823,279 records, 22.104% 직접 노출; 641,300은 직접 미노출 | 모든 record는 compiler 모집단·할당 회계에 포함됐지만, 모든 원문을 GPT가 직접 읽은 것은 아니다. 이것만으로 두뇌가 쓸모없다거나 예측력이 입증됐다고 결론 내리지 않는다. |
| coverage / claims | assignment coverage 100%, unassigned 0, duplicate primary 0, rare outlier 8,297/8,297; mechanism claims 40, citation edges 111 (support 105, contradict 6) | 2026-10-06 전수 plan·leaf·assignment·claim/citation 대조 PASS. 세부 root와 결과는 closeout 문서에 기록한다. |
| 검색기 | 독립 `ensure_ready()` 재검증 통과, package root 재계산과 semantic capsule·mechanism claim 양쪽 DuckDB HNSW query plan 통과 | 검색기/패키지 감사 PASS. 실제 장전 CSV 사용성 smoke를 대신하지 않는다. |
| LLM 실행 기록 | 총 7,980 logical attempts = checkpoint hit 7,513 + fresh 성공 465 + 오류 2; output ledger 7,978행과 2건 차이는 size-contract 오류 trace로 설명되며 bounded repair 뒤 성공 | fresh 출력은 `gpt-6.1-sol/high`; 재사용 checkpoint에는 과거 `gpt-5.6-sol/xhigh` 등도 있다. 전체 결과를 6.1이 새로 작성했다고 표현하지 않는다. |
| build cutoff | `2026-08-21T18:52:07.302105+09:00` | 이 시각 뒤의 적격 CSV가 있어야 full compiled brain 경로를 실제 smoke할 수 있다. |
| 현재 activation | manifest의 `production_eligible=false`, `production_activated=false` | production HOLD 상태를 유지한다. |

LLM trace의 `prompt_token_count_reported=1,296,623,760`은 실제 토큰 수나 비용으로 인용하지 않는다. 해당 카운터는 UTF-8 byte 수를 보수적 token 상한처럼 기록한다. 필요하면 trace의 byte 필드라고 정확히 표시한다.

원료 날짜는 `2018-01-03`~`2026-06-19`, 1,542 distinct trade dates다. 이는 약 8년 반의 달력 범위이지 10년 전체나 모든 거래일을 채웠다는 뜻이 아니다. 연도별 record 수는 2018 104,470; 2019 97,670; 2020 85,768; 2021 82,808; 2022 100,118; 2023 102,292; 2024 88,504; 2025 97,678; 2026 63,971(6월 19일까지)이다. 거래소 공식 휴일 달력과 대조하기 전에는 관측 날짜 간격을 누락 거래일이라고 단정하지 않는다.

source manifest pointer의 SHA가 낡았지만, pinned memory snapshot의 실제 manifest SHA는 위 externally attested SHA와 일치한다. compile manifest는 pointer drift를 기록하고 실제 SHA override를 attested 처리했다. 이 차이를 숨기거나 manifest를 임의 수정하지 않는다.

## 최신 실행 상태: 2026-10-06 20:01 KST

| 단계 | 상태 | 현재 근거 |
|---|---|---|
| 고정 1회성 compile | `COMPLETE`; 다시 실행 금지 | `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`, package version/root/hash는 위 표. package 34 files, 19,791,364,549 bytes; DuckDB 18,426,376,192 bytes. |
| package·DAG 계보 감사 | `PASS` | plan SHA `6a3c78d89c55233afd66ef556eeb4cfba88c51047e140cccc268f95d5d51fc97`, topology SHA `0663df89a0805c91f8526fc8a5126da004b06a20bbf0b7ac8ecb1978daa78ed5`, DB와 1,868 task ID/child topology exact. 4,969 leaf closure와 world coverage root `15d9e8c7bf2f339a2a119952fe46f01fe2cf9f56c1e8a8750ec3a0d1f9d27c18`가 일치한다. |
| assignment/capsule/claim 계보 | `PASS` | leaf와 capsule 52,644개 exact; assignment ledger와 DB 823,279행 exact, membership root `dd51591eeab66636cedfe0ac14af14d13d2a0a5c43ee00eaff30675f5ffbb401`; capsule·centroid·member count mismatch 0. claim payload 40개와 citation edge 111개(지원 105, 반례 6) parity exact. |
| 직접 payload exposure | `PASS / PARTIAL EXPOSURE` | 181,979개 고유 record(22.104%)의 exposure root `0d402ea2aa50c9c4cf8a5048ec9739fe19cca2e2495d5184da6aad4a61a48072`가 manifest와 일치, truncation 0. 641,300개는 GPT에 직접 payload 미노출이다. |
| 실제 장전 CSV smoke | `BLOCKED_INPUT_REQUIRED` | 최신 로컬 검색에서 package cutoff 이후 적격 news CSV 없음. 지정 Vercel health/CSV transport는 Vercel 로그인 HTML을 반환했고 기존 Chrome에서도 로그인 페이지였다. 인증 정보는 입력하거나 다루지 않았다. |
| 정식 품질 gate | `NOT_RUN_GATE_MISSING`; predictive quality `UNAPPROVED` | root `QSEL-19b3...` artifact 없음, 준비 보고서도 actual run `NOT_RUN`. staging의 `QSEL-16352...`는 3-case blind/outcome artifact 쌍뿐이며 HOLDOUT/paired prediction/score 증거가 없다. |
| production | `HOLD` | package manifest `production_eligible=false`, `production_activated=false`; 품질 gate, 별도 사용자 승인, release binding 및 rollback proof가 없다. |

전수 대조 보고서는 [offline_brain_v2_daily_csv_closeout_20261006.md](offline_brain_v2_daily_csv_closeout_20261006.md)다. 동일 package audit을 반복하지 말고, 실제 미완료 작업은 적격 CSV를 확보해 daily smoke를 수행하는 것이다.

## 다음 실행에서 할 일

### 1. 작업 상태와 고정 산출물 재확인

- 현재 저장소의 `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`, 본 문서를 다시 읽고 product intent를 우선한다.
- 마지막 확인(2026-10-06 20:01 KST)에서 compile writer는 없고 package는 terminal output으로 존재한다. 다음 실행에서는 process가 다시 생겼는지만 확인하고 compile/build를 실행하지 않는다.
- package manifest SHA가 기록된 값과 일치하는지, 파일 metadata가 바뀌었는지만 빠르게 확인한다. 전체 package root와 18GB DB를 다시 읽는 `ensure_ready()`는 이번 Goal에서 이미 통과했으므로 파일 변경이 관측되지 않는 한 반복하지 않는다. identity가 다르면 선택·수정·재빌드하지 말고 정확한 차이를 보고한다.
- 기존 원료, source DB, package, checkpoint, WAL, logs, 그리고 사용자가 만든 출력은 보존한다. 기존 untracked `diagnostics/offline_reduce_dag_preflight_existing_capsules_20261004.json`, `runs/offline_v5_gpt61_high_20261004/`, `runs/resource_logs/`를 수정·이동·삭제·커밋하지 않는다.

### 2. 패키지 계보 및 무결성 감사: 완료

- 2026-10-06 20:01 KST 전수 read-only 대조에서 고정 plan, DB, leaf coverage, capsule ledger, assignments, claims/citation, influence manifest가 모두 일치했다. 1,868 plan task ID/child topology exact, 4,969 leaves와 52,644 capsules closure exact, 823,279 assignment 원장/DB 행별 exact, 40 claims/111 citation edges parity exact였고 orphan/mismatch는 0이다.
- package root 재계산, read-only package load, 실제 capsule 및 mechanism-claim DuckDB HNSW query plan도 통과했다. 재현 가능한 전체 수치와 hashes는 `offline_brain_v2_daily_csv_closeout_20261006.md`에 있다.
- 이 단계는 완료됐으므로 동일 package identity가 유지되는 한 audit을 반복하거나 코드를 고치지 않는다. 검증 후 새로 발견된 구체적 결함이 있을 때만 그 결함을 격리해 별도 범위를 정한다. 검증을 느슨하게 해 통과시키지 않는다.

### 3. 실제 장전 CSV로 daily smoke

- 2026-10-06 재검색 완료: 저장소 `docs/csv`의 가장 최신 `news_*.csv`는 `news_20260624.csv`로 build cutoff보다 오래됐다. Downloads 및 `Downloads (2)`에서 cutoff 이후 수정된 CSV를 훑었으나 발견된 파일은 블로그 keyword/backlink, 디스크 조사, 기타 자료이며 장전 뉴스 CSV가 아니다. staging CSV도 accepted research episode 자료였다. 동일 경로를 반복 검색하지 않는다. Vercel transport API 호출은 JSON 대신 `Login – Vercel` HTML을 반환했고 기존 Chrome에서도 같은 인증 화면을 확인했다.
- build cutoff `2026-08-21T18:52:07.302105+09:00`보다 뒤 trade date에 해당하는, 실제 pre-open 시점의 미변형 CSV인지 확인한다. D-day 가격·결과, cutoff 후 기사/메타데이터, 임의 삭제·trim·합성 행이 섞인 파일은 사용하지 않는다.
- 적격 CSV가 없으면 데이터를 만들지 말고 smoke를 `BLOCKED_INPUT_REQUIRED`로 기록한 뒤 실제 CSV를 사용자에게 요청한다. 그 상태를 smoke PASS나 제품 완성으로 표현하지 않는다.
- 적격 CSV가 있으면 audited package를 별도 evaluation/test project에서 선택해 지원되는 `analyze-daily` 경로로 실행한다. `brain/current`의 production pointer를 바꾸거나 production을 활성화하지 않는다. Codex CLI OAuth session을 사용하고 credential 파일을 열거나 복사하지 않는다. 실 embedding provider의 fail-closed 동작을 유지한다.
- 입력 trade date와 cutoff를 명시하고 production BLIND 정책 `CSV_MEMORY_ONLY_STRICT`를 지킨다. Web, D-day 가격·성과, outcome, cutoff 후 정보, 레거시 exhaustive `analyze`, point-in-time mode로 brain guidance를 생략하는 우회, mock LLM을 사용하지 않는다.
- 실행 manifest/context manifest가 위 package와 memory/index identity에 묶이는지, 처음이자 유일한 logical LLM call site `final_market_decision`에 뉴스와 brain guidance가 함께 있는지 확인한다. structured repair는 필요한 경우 최대 1회만 허용한다. 매 record/cluster/lane 호출은 금지다.
- 출력 후보·근거·불확실성·citations가 존재하고 citations가 실제 brain/CSV evidence에 해소되는지, cutoff/no-web 경계가 지켜지는지, 실제 provider/model, 호출 수, repair 여부, elapsed time 및 산출 경로를 기록한다. smoke는 작동 확인이지 predictive quality나 백테스트 성공 증명이 아니다.

### 4. 정식 blind quality와 production 상태 판정

- 2026-10-06 registry/artifact 재검색 완료: root quality_full selection 경로와 QSEL-19b3c80ba392db8564c9 파일이 없다. 그 ID의 report는 준비 단계이며 actual prediction-to-score `NOT_RUN`으로 기록돼 있다. staging에 남은 QSEL-16352cbccb703547c2ba는 blind selection과 분리 outcome 파일 2개 및 3 case뿐이고, 같은 deployable one-call 경로의 HOLDOUT·paired prediction·score 또는 등록 gate는 찾지 못했다. 기존 `configs/evaluation.yaml`은 generic metric 목록이며 registered gate가 아니다. 이 증거가 바뀌기 전까지 gate 탐색/실행을 반복하지 않는다.
- gate가 있더라도 새 gate를 임의로 만들거나, 전체 원료에 비례하는 긴 LLM fan-out으로 바꾸지 않는다. 정해진 registered bounded blind protocol과 physically separated outcome 절차만 따른다.
- `QPRED-704f15cde6e4152b6931`와 379-pack ancestry는 `HALTED_MISALIGNED_DIAGNOSTIC_ONLY`; `QPRED-4ecc6155c077cb5b092c` ancestry는 invalidated다. 재개·채점·비교·승격·formal cache 입력으로 사용하지 않는다.
- 같은 architecture의 유효한 registered gate/sealed input이 없으면 `NOT_RUN_GATE_MISSING`, predictive quality `UNAPPROVED`, production `HOLD`로 기록한다. smoke 성공으로 품질 승인을 대신하지 않는다.
- production activation에는 품질 gate 통과, 별도의 명시적 사용자 승인, package-bound release manifest, 검증된 rollback 대상/절차가 모두 필요하다. 하나라도 없으면 pointer 변경·배포·활성화를 하지 않는다.

### 5. 외부 검토용 closeout과 전달

- `docs/operations/`에 compile/package 감사 결과와 daily smoke 상태를 담은 외부 검토용 closeout을 갱신한다. 민감한 credential은 포함하지 않고 재현 가능한 명령·artifact 경로·hash·검증 범위·제한·남은 blocker를 기록한다.
- compile 완료, 구조적 record coverage, LLM 직접 payload exposure, semantic unit 수, claim/citation coverage, daily smoke, predictive quality, production activation을 서로 다른 상태/수치로 보고한다.
- 이 Goal 문서는 `C:\Users\eorb9\Downloads\codex_goal_nslab_finish_brain_and_daily_csv.md`와 동기화하고, closeout은 별도 `C:\Users\eorb9\Downloads\offline_brain_v2_daily_csv_closeout_20261006.md` 사본으로 전달한다. 사용자가 남긴 변경과 untracked 산출물을 건드리지 않는다.
- 문서/코드 변경을 검토하고 `git diff --check`를 실행한다. 코드 변경이 없다면 이번에 새로 전체 테스트를 통과했다고 말하지 않는다. 관련 문서만 한국어 commit message로 commit/push하고 remote 결과를 확인한다. 범위 밖 파일은 포함하지 않는다.
- 마지막 응답에는 끝난 것, 미완료/blocked 항목, smoke와 quality gate 상태, production HOLD 여부, commit/push 결과를 간단히 분리해 보고한다. 입력 CSV가 없어 막혔다면 사용자가 제공할 정확한 파일 요건을 한 문장으로 요청한다.

## 절대 지킬 제품 계약

- 하루의 정상 처리에는 logical GPT decision call이 하나뿐이다: `final_market_decision`. news와 cutoff-safe compiled brain/category/world knowledge 및 관련 memory를 그 첫 요청에 함께 제공한다. brain-free 사전 해석 요청은 없다. 구조화 응답 복구만 최대 1회 허용한다.
- open-world 후보 탐색은 유지한다. 과거 ticker/theme/name은 candidate allowlist가 아니다. 정확 키워드 검색은 supporting evidence일 뿐 판단 gate가 아니다.
- daily 호출량은 원료 전체, 모든 record, 모든 cluster-record assignment 또는 lane ledger에 비례하면 안 된다. retrieval은 bounded·relevance-driven·citation-bearing이어야 한다.
- daily BLIND에서 web, D-day 가격/성과, outcome, cutoff 이후 자료를 사용하지 않는다. 모든 arm이 동일한 zero-web evidence surface를 사용해야 한다.
- 두뇌와 연구자료는 source code에 ticker/theme 매핑을 hardcode하지 않고 `research/`, `memory/`, `brain/` 산출물로 관리한다. unknown bundle/version/type은 버리기보다 보존하거나 quarantine한다.
- 재compile/re-import/re-embedding은 새로 승인된 연구 원료 또는 구체적으로 입증된 산출물 결함이 있을 때만 별도 scope로 수행한다. 이번 goal의 기본 작업은 기존 artifact 감사와 일일 경로 검증이다.

## 완료 판정

1. **1회성 컴파일:** 이미 완료. 새로 실행하지 않는다.
2. **패키지 감사:** plan·DAG·leaf·assignment·claim/citation 계보가 package/source roots와 일치해야 PASS다.
3. **일일 경로:** 실제 적격 CSV smoke PASS가 필요하다. 적격 CSV 미제공이면 `BLOCKED_INPUT_REQUIRED`; 전체 제품 검증 완료라고 하지 않는다.
4. **예측 품질:** 같은 one-call architecture의 정식 sealed blind gate가 없거나 미통과면 `UNAPPROVED`다.
5. **Production:** 별도 사용자 승인·release binding·rollback proof와 quality PASS 전에는 계속 `HOLD`이며 활성화하지 않는다.

이 Goal의 끝은 컴파일을 반복하는 것이 아니다. 기존 한 번의 빌드를 감사하고, 가능한 경우 실제 장전 CSV 연결을 검증하며, 실제 한계와 다음 입력을 외부 검토 가능한 문서로 남기는 것이다.

## 2026-10-08 제공 CSV 및 구 v3 daily smoke 기록

이 절은 10월 6일 CSV를 사용한 과거 v3 실행 기록이다. 휴장일 window와 event citation 결함을 확인한 뒤속 상태는 문서 끝의 `2026-10-08 KRX window 및 citation 후속` 절이 우선한다.

사용자가 제공한 `C:\Users\eorb9\Downloads\123-20261007T194150Z-1-001\123`에는 CSV 32개, 총 42,909행이 있다. 파일 범위는 `news_20260824.csv`부터 `news_20261006.csv`이며 `news_20260821.csv`와 `news_20261007.csv`는 없다. 원본 파일은 수정하지 않았다.

실제 smoke는 `news_20261006.csv`, 거래일 `2026-10-06`, cutoff `2026-10-06T08:59:59+09:00`으로 수행했다. 적용 시작 시각은 `2026-10-05T15:30:00+09:00`이고 911행 모두 cutoff 안에 있었다. 가장 늦은 게시시각은 `08:59:53`, 입력 SHA-256은 `0a4b3d15324fbdd65869b550eed286a2bb06c06ef6416e4de84ca3c063eb33ca`다. 단, CSV에 `collected_at` 필드가 없으므로 각 기사가 cutoff 전에 실제 수집됐다는 점까지 독립 증명되지는 않는다.

실제 실행은 고정 brain `brain-v2-993b42487c557ca1` 및 root `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd`를 사용했다. 911행은 873개 material event capsule로 묶였다. 모델은 10개 compiled guidance, 24개 semantic memory capsule과 24개 exact witness를 함께 받았고, mechanism claim은 0개 선택됐다. 이전 package 전체를 재compile/import/re-embed하지 않았다. 당일 raw research map, brain rebuild, web call, full corpus scan은 각각 0이다.

새 daily prompt architecture `one_time_brain_thin_daily.v3`로 Codex OAuth `gpt-5.6-sol/xhigh`에 `final_market_decision` 논리 호출 1회가 성공했고 structured repair는 0회였다. smoke 전체는 402.06초(6분 42초), provider 구간은 321.03초였다. prompt는 939,725자였고 Codex hard limit 1,048,576자 아래였다. 기록된 `1,258,259`는 UTF-8 byte 기반 conservative upper bound이며 실제 tokenizer가 센 token 수가 아니다.

입력 압축 시 원본 capsule, 전체 row/event/source ID, timestamp, row disposition은 별도 artifact로 보존한다. 모델 prompt의 capsule 키는 크기를 줄인 compact format이다. 모델 응답의 `analyzed_cluster_count=873`은 응답 스키마 검증과 artifact ledger의 행 수 일치 확인이지, 모델의 의미적 주의집중을 독립 증명하는 값은 아니다. LLM에 보낸 것은 911개 원문 본문 전체가 아니라 dedup된 873개 current-event capsule이다. Prompt SHA-256은 `9e59e1977e29c3dd952449b7bafc05683d98ccc737e9371b8771d1b781de2ddf`다.

결과 파일은 `runs/daily_csv_smoke_20261008_compact/outputs/THINRUN-1e092dd16b3be82ec275/` 아래에 있다. 6개 후보 및 sector report가 만들어졌지만 이 파일은 10월 8일에 실행한 과거 날짜 smoke다. 예측 성능을 채점하지 않았고 training이나 정식 blind 결과로 쓰지 않는다. Smoke 당시 모델이 `BlindPrediction.created_at`을 cutoff 시각으로 반환한 점을 발견해, 현재 코드는 sealing 시각을 실제 현재 시각으로 덮어쓰도록 고쳤다. 기존 smoke artifact는 수정하지 않고 forensic 자료로 보존한다.

당시 32개 파일 감사에서 구버전의 달력일 기준 window가 9,380행을 제외하는 문제가 드러났다. 월요일·연휴 직후에는 직전 달력일이 아니라 이전 실제 거래 세션의 15:30을 사용해야 한다. 해당 결함과 수정 후 전체 날짜 통계는 아래 후속 절에 기록한다.

실제 smoke 성공은 일일 연결 경로의 기능 확인일 뿐이다. 정식 same-architecture blind quality gate는 아직 `NOT_RUN_GATE_MISSING`, predictive quality는 `UNAPPROVED`, production은 계속 `HOLD`다. 기존 `2026-10-06` canonical prediction/report와 production pointer는 쓰지 않았다. 10월 7일 CSV가 폴더에 없으므로 그 날짜는 아직 검증되지 않았다.

코드 gate 결과: `ruff check .` 통과, `mypy src/news_scalping_lab` 통과, 전체 `pytest` 1,907개 통과. 이번 smoke에서 확인된 총 6분 42초는 제공된 CSV 한 건의 측정값이지 다른 날짜/장비/서비스의 보장치가 아니다. 수정의 핵심은 Codex 요청 1,048,576자 hard limit보다 낮은 1,000,000자 사전검사, 반복 키를 줄인 capsule payload, 응답의 긴 cluster ID 나열을 count 확인으로 대체한 점이다. 정식 평가는 이 v3 daily architecture 그대로 수행해야 한다.

## 2026-10-06 20:18 KST 추가 CSV 검색

바탕화면·문서·OneDrive·프로젝트 워크트리까지 파일명 기준으로 다시 검색했다. 추가 위치에서 확인된 최신 `news_YYYYMMDD.csv`도 `news_20260624.csv` 사본이며, Offline Brain V2 cutoff인 `2026-08-21T18:52:07.302105+09:00`보다 앞선 자료다. 이를 사용하면 cutoff 이후에 만들어진 brain 지식이 과거 거래일 판단에 들어갈 수 있으므로 smoke 입력으로 사용하지 않는다. 추가 검색에서도 적격 장전 CSV는 발견되지 않았다. 따라서 daily smoke는 계속 `BLOCKED_INPUT_REQUIRED`, predictive quality는 `UNAPPROVED`, production은 `HOLD`다.

## 2026-10-06 20:22 KST daily architecture 테스트

`python -m pytest tests/unit/test_thin_daily.py tests/unit/test_offline_brain_v2.py::test_daily_reader_uses_only_precompiled_package -q --durations=10` 실행 결과 17개 targeted unit test가 통과했다. mock/fixture 기준으로 brain을 첫 LLM 요청 전에 로드하고, `final_market_decision` 정상 호출은 하나이며 structured repair 포함 최대 두 번인 점, record/cluster 수에 비례해 LLM 호출이 늘지 않는 점, precompiled fixture package 조회를 검증했다. 실제 19.8GB 패키지와 장전 CSV 또는 live provider를 실행한 증거가 아니고 predictive-quality gate도 아니다. 이번에는 전체 pytest/Ruff/mypy를 다시 실행하지 않았다.

## 2026-10-08 KRX window 및 citation 후속

### 입력 범위 및 point-in-time 한계

사용자 폴더 `C:\Users\eorb9\Downloads\123-20261007T194150Z-1-001\123`에는 `news_*.csv` 32개, 42,909행이 있다. 파일명 범위는 `news_20260824.csv`~`news_20261006.csv`; 요청한 `news_20260821.csv`와 `news_20261007.csv`는 없다. CSV header는 `page,row,date,time,title,body`이고 `collected_at`은 없다. 따라서 본문 게시 시각은 cutoff-safe로 검사할 수 있지만, 원본이 해당 cutoff 전에 수집됐다는 점은 독립 증명할 수 없다.

XKRX calendar 기준으로 2026-09-24, 2026-09-25, 2026-10-05는 비거래일 파일이다(861, 579, 1,571행; 합계 3,011행). 이 날짜에는 `analyze-daily` 예측을 만들지 않는다. 나머지 29 거래일 CSV는 39,898행이며, production loader로 검사했을 때 모두 해당 거래일 cutoff `08:59:59 KST` 이내이자 이전 실제 거래 세션 `15:30 KST` 이후였다. 새 window 밖 0행, cutoff 이후 0행이다.

`news_20260928.csv`는 1,627행이다. XKRX상 직전 세션은 추석 휴장 전 9월 23일이므로 올바른 시작은 `2026-09-23T15:30:00+09:00`이다. 본문 날짜별 행은 9월 25일 276, 9월 26일 462, 9월 27일 556, 9월 28일 333이며 모두 포함된다. 구 calendar-day window `2026-09-27T15:30:00+09:00`라면 1,088행이 잘못 제외된다. `news_20261006.csv`는 911행이며 올바른 시작은 `2026-10-02T15:30:00+09:00`; 이 파일은 10월 5일 휴장일 434행과 10월 6일 477행만 포함하므로 구 window에서도 행 차이가 우연히 드러나지 않았다.

### Calendar implementation

`exchange-calendars`의 XKRX session calendar를 사용하도록 `default_news_window_start`, `next_trading_day`를 고쳤고 `is_krx_trading_day`를 추가했다. 휴장일 `analyze-daily`는 CSV/package를 읽기 전에 fail-closed한다. 테스트는 9월 28일 window `9월 23일 15:30`, 10월 6일 window `10월 2일 15:30`, 10월 5일 비거래일을 고정한다. 이 변경은 기사 분류나 후보를 소스코드에 hardcode하지 않는다.

### Citation failure and repair

- 실제 package-backed 9월 28일 첫 실행은 Codex OAuth `gpt-5.6-sol/xhigh`, architecture/prompt v3로 1회 호출됐지만 sector `triggering_events`에 event ID 대신 문장을 넣어 post-validation에서 거부됐다. Trace `production/staging/P9IMPORT-3D770A7DD72457C97098/project/runs/traces/TRACE-f61c675c082d.json`; 예측/manifest/canonical 결과는 승인되지 않았다.
- 새 v4 repair를 시험한 re-clustered run은 1,627행을 1,568개 capsule로 묶었다. 첫 응답은 `Candidate.event_ids`에 현재 뉴스에 없는 ID를 넣었고, repair 응답은 candidate와 sector 필드 모두에서 허용되지 않은 ID를 사용해 validator가 거부했다. Trace `TRACE-6bfbf8fa582e.json`, `TRACE-688c7f3b8e0d.json`; 확인된 invalid IDs는 `EVT-6cf0c10d552e`, `EVT-e21a88f37126f`다. 첫 validation 오류가 후보 ID였는데 repair 안내가 sector 필드 위주였던 점이 수정 계기다.
- prompt를 `thin_daily.final_market_decision.v5`로 올리고 Candidate/sector schema description과 main/repair prompt에 `current_event_capsules[].e`만 사용하도록 명시했다. 후보의 `source_row_ids`가 겹치는 capsule의 정확한 event ID를 validation 오류에도 포함한다. 1회 retry 뒤에도 validation은 fail-closed한다.
- v5 live decision replay는 방금 재생성한 동일 1,568개 capsule artifact와 이전 실제 package retrieval이 만든 동일 brain context를 hash 대조해 재사용했다. GPT의 첫 요청에는 현재 뉴스와 cutoff-safe brain context가 함께 있었다. `gpt-5.6-sol/xhigh`, prompt 944,204자, provider wall time 280.137초, 후보 10·섹터 3, structured repair 0, citations validation `PASS`. Output: `production/staging/P9IMPORT-3D770A7DD72457C97098/project/runs/daily_csv_smoke_calendar_v5_decision_replay_20261008/THINREPLAY-72d78b0aeb01e69033bc/`. News SHA-256 `59365528d7a303dc539abd8b1489a6b4e8caea06d06704ca7e03b7b90a09d07c`; package root remains `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd`. Canonical predictions/reports와 production pointer는 쓰지 않았다.
- 최초 v5 decision replay는 frozen context를 재사용했으므로 그 실행만으로는 full CLI 검증이 아니었다. 후속으로 아래 `Full v5 analyze-daily CLI Smoke`에서 package selection/load부터 prediction/report까지 전체 경로를 성공적으로 실행했다. 과거 10월 6일 v3 full smoke는 forensic 기능 증거로만 보존한다.

재실행 자원 관측에서 Python private memory는 약 3.05GB로 안정됐고, Codex 응답 대기 중 CPU time 증가 없이 시스템 가용 RAM은 약 17.6GB였다. Torch/local embedding worker pool은 131개 thread로 관측됐다. 무한 증가나 memory leak 증거는 없지만, 이는 단일 Windows 측정이며 운영 latency 보장은 아니다.

### Verification and remaining status

최종 코드 상태에서 `python -m ruff check .` 통과, `python -m mypy src/news_scalping_lab` 통과(139 files), `python -m pytest` 1,911 passed (304.50s). Tracked JSON schemas는 공식 exporter로 다시 생성해 contract parity를 확인했다.

현재 package compile/lineage는 `COMPLETE/PASS`; 일일 경로는 `SMOKE_PASS_ONE_HISTORICAL_DATE`(전체 v5 CLI 경로 통과, 아래 기록); formal predictive quality는 `NOT_RUN_GATE_MISSING` / `UNAPPROVED`; production은 `HOLD`다. `news_20261007.csv`와 `collected_at` 증거가 없고 같은 architecture의 registered blind gate도 없다. One-time compile은 재실행하지 않는다.

## 2026-10-08 Full v5 `analyze-daily` CLI Smoke

`news_20260928.csv`는 package build cutoff 뒤의 거래일이며 추석 연휴 직후 달력 경계도 검증하는 입력이다. trade date `2026-09-28`, cutoff `2026-09-28T08:59:59+09:00`, 올바른 이전 세션 window 시작 `2026-09-23T15:30:00+09:00`을 적용했다. 입력 SHA-256은 `59365528d7a303dc539abd8b1489a6b4e8caea06d06704ca7e03b7b90a09d07c`이고 1,627행 모두 window 안에 있었다.

이전 19.8 GB brain package는 변경하지 않고, 검증 프로젝트 `runs/daily_csv_smoke_v5_cli_eval_20261008/`에서 34개 파일의 hardlink로 재사용했다. 프로젝트 전용 package pointer는 `production_activated=false`다. 선택 package version/root/manifest SHA는 각각 `brain-v2-993b42487c557ca1`, `b3dc694131b41c1553817ca7b2e00747391e79f95170ad856ae7054c120165dd`, `3286247ce9271064455f702d1e44f8fdda4eb3659455f14e44517937da048378`로 고정됐다. package 재컴파일, source/import/repair, historical re-embedding은 하지 않았다.

산출물 run ID는 `THINRUN-46d70c9550d63b790a40`이다. 1,568개 current-event capsule, 10개 compiled brain guidance, 24개 semantic capsule, 24개 exact witness를 첫 요청 전에 불러왔다. provider는 Codex OAuth `gpt-5.6-sol/xhigh`, prompt `thin_daily.final_market_decision.v5`; 단일 logical call 1회, structured repair 0회, 최대 허용 호출 2회였다. blind web 0, daily import 0, daily brain rebuild 0, online full-corpus scan 0, future record 0. 결과는 9 candidates와 5 sectors다. CLI 정상 종료 뒤 저장 결과를 현재의 stricter row-level validator로 재검증해 후보 event citation 31개가 각 candidate source rows와 일치하고, sector event citation 38개가 모두 현재 CSV capsule ID임을 확인했다.

전체 `analyze-daily` wall time은 `452.54972`초(약 7분 33초), 실제 provider 구간은 `276.056837`초, prompt 길이는 944,310자였다. 이 한 번의 Windows historical replay에서 측정한 값으로 운영 SLA를 보장하지 않는다. 결과는 격리 프로젝트 내부 prediction/report/context manifest에만 저장됐다. 본 프로젝트의 canonical prediction이나 production pointer는 바꾸지 않았고, D-day 결과·outcome을 열거나 scoring/training을 하지 않았다. 원본 CSV에는 `collected_at`이 없으므로 기사 게시시각이 cutoff 전이라는 것은 확인했지만 당시 수집된 원본이라는 점까지 독립 증명하지는 못한다.

이 변경에서 후보 `event_ids`가 현재 전체 CSV 어디엔가 존재하는지만 보던 validator를 보강해, 각 후보의 `source_row_ids`와 겹치는 capsule에 실제 포함된 event ID만 허용한다. 섹터 검증을 후보 loop 밖으로 분리해 후보가 0개여도 검사를 생략하지 않게 했다. 회귀 테스트를 추가했고 현재 전체 gate는 Ruff PASS, mypy 139 files PASS, pytest `1913 passed`다.

일일 경로의 한 날짜 기능 smoke는 PASS지만 정식 same-architecture blind quality gate는 여전히 `NOT_RUN_GATE_MISSING` / `UNAPPROVED`다. `news_20261007.csv`, 수집시각 provenance, 등록된 bounded blind gate, 별도 사용자 release 승인이 남아 있으므로 production은 `HOLD`다.
