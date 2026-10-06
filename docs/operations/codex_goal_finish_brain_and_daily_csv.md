# Goal: 검증된 두뇌 패키지로 장전 CSV 판단 완성

문서 갱신: 2026-10-06 20:01 KST

## 이 Goal의 목적

이미 끝난 1회성 연구 두뇌 컴파일을 다시 돌리지 않는다. 존재하는 Offline Semantic Brain V2 패키지의 계보와 검색 경로 감사를 마치고, 실제 cutoff-safe 장전 뉴스 CSV를 production `analyze-daily`에 넣어 두뇌와 CSV가 첫 GPT 판단 요청부터 함께 쓰이는지 검증한다. 같은 구조의 정식 blind 품질 gate가 확인되면 그 상태도 판정하되, 품질 승인과 사용자 승인 없이 production을 활성화하지 않는다.

최종 사용 흐름은 `장전 CSV + 이미 만들어진 두뇌/인덱스 -> GPT의 단일 판단 요청 -> 주도 섹터·종목 후보, 근거·인용·불확실성·출처`다. GPT 가중치를 fine-tune한 새 모델을 만드는 작업이 아니다. 원료 전체를 매일 재해석하거나 record·cluster·lane마다 GPT를 호출하지 않는다.

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
