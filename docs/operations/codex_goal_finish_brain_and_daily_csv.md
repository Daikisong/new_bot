# Goal: 검증된 두뇌 패키지로 장전 CSV 판단 완성

문서 갱신: 2026-10-06 19:39 KST

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
| coverage / claims | assignment coverage 100%, unassigned 0, duplicate primary 0, rare outlier 8,297/8,297; mechanism claims 40, citation edges 111 (support 105, contradict 6) | 정확한 task·leaf·claim 연결의 전수 계보 감사는 아래 남은 작업이다. |
| 검색기 | 독립 `ensure_ready()` 검증 통과, semantic capsule·mechanism claim 양쪽 query plan에서 DuckDB HNSW 사용 확인 | 패키지 root 전체 재계산과 read-only load를 통과했다. 남은 전수 lineage 검증을 대신하지 않는다. |
| LLM 실행 기록 | 총 7,980 logical attempts = checkpoint hit 7,513 + fresh 성공 465 + 오류 2; output ledger 7,978행과 2건 차이는 size-contract 오류 trace로 설명되며 bounded repair 뒤 성공 | fresh 출력은 `gpt-6.1-sol/high`; 재사용 checkpoint에는 과거 `gpt-5.6-sol/xhigh` 등도 있다. 전체 결과를 6.1이 새로 작성했다고 표현하지 않는다. |
| build cutoff | `2026-08-21T18:52:07.302105+09:00` | 이 시각 뒤의 적격 CSV가 있어야 full compiled brain 경로를 실제 smoke할 수 있다. |
| 현재 activation | manifest의 `production_eligible=false`, `production_activated=false` | production HOLD 상태를 유지한다. |

LLM trace의 `prompt_token_count_reported=1,296,623,760`은 실제 토큰 수나 비용으로 인용하지 않는다. 해당 카운터는 UTF-8 byte 수를 보수적 token 상한처럼 기록한다. 필요하면 trace의 byte 필드라고 정확히 표시한다.

원료 날짜는 `2018-01-03`~`2026-06-19`, 1,542 distinct trade dates다. 이는 약 8년 반의 달력 범위이지 10년 전체나 모든 거래일을 채웠다는 뜻이 아니다. 연도별 record 수는 2018 104,470; 2019 97,670; 2020 85,768; 2021 82,808; 2022 100,118; 2023 102,292; 2024 88,504; 2025 97,678; 2026 63,971(6월 19일까지)이다. 거래소 공식 휴일 달력과 대조하기 전에는 관측 날짜 간격을 누락 거래일이라고 단정하지 않는다.

source manifest pointer의 SHA가 낡았지만, pinned memory snapshot의 실제 manifest SHA는 위 externally attested SHA와 일치한다. compile manifest는 pointer drift를 기록하고 실제 SHA override를 attested 처리했다. 이 차이를 숨기거나 manifest를 임의 수정하지 않는다.

## 다음 실행에서 할 일

### 1. 작업 상태와 고정 산출물 재확인

- 현재 저장소의 `AGENTS.md`, `.agents/skills/news-scalping-lab/SKILL.md`, 본 문서를 다시 읽고 product intent를 우선한다.
- compile ID와 package path를 기준으로 writer/process가 남아 있는지 확인한다. compile은 이미 완료됐으므로 정상적인 후속 작업은 read-only audit와 daily smoke뿐이다.
- 현재 package manifest SHA와 package root가 위 identity와 일치하는지 확인한다. 불일치하면 선택·수정·재빌드하지 말고 정확한 차이를 보고한다.
- 기존 원료, source DB, package, checkpoint, WAL, logs, 그리고 사용자가 만든 출력은 보존한다. 기존 untracked `diagnostics/offline_reduce_dag_preflight_existing_capsules_20261004.json`, `runs/offline_v5_gpt61_high_20261004/`, `runs/resource_logs/`를 수정·이동·삭제·커밋하지 않는다.

### 2. 패키지 계보 및 무결성 감사 마무리

컴파일러 worktree `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b`에서 지원되는 read-only verifier와 ledger를 사용한다. package identity를 바꾸거나 synthesis를 재실행하지 않는다.

- 고정 reduce DAG plan의 1,868 task ID 집합과 DB의 실제 node ID 집합이 정확히 같은지 비교한다. 단순 row count나 `1,858 + 9 + 1` prefix count만으로 닫힘을 선언하지 않는다.
- parent/child closure와 각 category/world node의 입력 범위를 plan에 대조한다.
- `semantic_reduce_leaf_coverage.jsonl`을 capsule 및 52,644 semantic unit assignment와 양방향 대조한다. DB node payload의 `covered_capsule_ids`가 저장 공간 절약을 위해 비어 있을 수 있으므로 그것만으로 coverage 누락을 판정하지 않는다.
- record assignment, centroid, capsule, mechanism claim 및 claim-citation edge의 orphan·누락·중복을 확인하고 가능한 경우 claim payload의 citation ID/role과 edge ledger도 대조한다.
- source manifest SHA, record root, compile ID, topology SHA, package root, HNSW 인덱스 및 embedding identity를 한 감사 결과에 묶는다. 실제 embedding은 고정된 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` identity다.
- trace의 7,980 logical attempts, 7,513 checkpoint hit, 465 fresh 성공, 2 size-contract 오류와 bounded recovery, 7,978 output-ledger rows를 서로 대조해 설명한다. 오류 trace 외의 provider 원문을 읽었다고 주장하지 않는다.
- 개별 검증이 실패하면 원인을 격리해 보고한다. coverage/schema/citation 검증을 느슨하게 하거나 evidence를 버려 통과시키지 않는다. 증거가 있는 코드 결함이 발견될 때에만 별도 수정 범위를 제시하고 전체 `ruff`, `mypy`, `pytest`를 실행한다.

현재 read-only 예비 대조에서는 `reduce_nodes=1,868`, capsules=52,644, assignments=823,279, missing assignment-to-centroid=0, capsule-to-centroid=0, claims without edges=0, orphan claim/capsule edges=0을 확인했다. 다만 exact task ID closure, leaf coverage ledger, claim payload-citation parity는 아직 남은 검증으로 취급한다.

### 3. 실제 장전 CSV로 daily smoke

- 저장소 fixtures, `C:\Users\eorb9\Downloads`, `C:\Users\eorb9\Downloads\Downloads (2)`, 프로젝트 입력/staging에서 실제 원본 CSV 후보를 다시 검색한다. 이전 확인에서 저장소 `docs/csv`의 `news_*.csv`는 최대 `news_20260624.csv`여서 package cutoff보다 오래됐다. Downloads의 최근 수정 CSV들은 뉴스 입력이 아닌 자료가 다수였으므로 파일명이나 수정일만으로 후보를 고르지 않는다.
- build cutoff `2026-08-21T18:52:07.302105+09:00`보다 뒤 trade date에 해당하는, 실제 pre-open 시점의 미변형 CSV인지 확인한다. D-day 가격·결과, cutoff 후 기사/메타데이터, 임의 삭제·trim·합성 행이 섞인 파일은 사용하지 않는다.
- 적격 CSV가 없으면 데이터를 만들지 말고 smoke를 `BLOCKED_INPUT_REQUIRED`로 기록한 뒤 실제 CSV를 사용자에게 요청한다. 그 상태를 smoke PASS나 제품 완성으로 표현하지 않는다.
- 적격 CSV가 있으면 audited package를 별도 evaluation/test project에서 선택해 지원되는 `analyze-daily` 경로로 실행한다. `brain/current`의 production pointer를 바꾸거나 production을 활성화하지 않는다. Codex CLI OAuth session을 사용하고 credential 파일을 열거나 복사하지 않는다. 실 embedding provider의 fail-closed 동작을 유지한다.
- 입력 trade date와 cutoff를 명시하고 production BLIND 정책 `CSV_MEMORY_ONLY_STRICT`를 지킨다. Web, D-day 가격·성과, outcome, cutoff 후 정보, 레거시 exhaustive `analyze`, point-in-time mode로 brain guidance를 생략하는 우회, mock LLM을 사용하지 않는다.
- 실행 manifest/context manifest가 위 package와 memory/index identity에 묶이는지, 처음이자 유일한 logical LLM call site `final_market_decision`에 뉴스와 brain guidance가 함께 있는지 확인한다. structured repair는 필요한 경우 최대 1회만 허용한다. 매 record/cluster/lane 호출은 금지다.
- 출력 후보·근거·불확실성·citations가 존재하고 citations가 실제 brain/CSV evidence에 해소되는지, cutoff/no-web 경계가 지켜지는지, 실제 provider/model, 호출 수, repair 여부, elapsed time 및 산출 경로를 기록한다. smoke는 작동 확인이지 predictive quality나 백테스트 성공 증명이 아니다.

### 4. 정식 blind quality와 production 상태 판정

- 실행 시점의 registry와 sealed artifacts를 다시 확인해, 평가가 실제 deployable one-call `analyze-daily` architecture와 같은지 검증한다. 이전 기록만 믿지 않는다. 기존 `QSEL-19b3c80ba392db8564c9`는 report/anchor 외 selection artifact가 미확인이고, `QSEL-16352cbccb703547c2ba`는 calibration-only였으므로 HOLDOUT 및 일일 경로 통과의 증거로 간주하지 않는다.
- gate가 있더라도 새 gate를 임의로 만들거나, 전체 원료에 비례하는 긴 LLM fan-out으로 바꾸지 않는다. 정해진 registered bounded blind protocol과 physically separated outcome 절차만 따른다.
- `QPRED-704f15cde6e4152b6931`와 379-pack ancestry는 `HALTED_MISALIGNED_DIAGNOSTIC_ONLY`; `QPRED-4ecc6155c077cb5b092c` ancestry는 invalidated다. 재개·채점·비교·승격·formal cache 입력으로 사용하지 않는다.
- 같은 architecture의 유효한 registered gate/sealed input이 없으면 `NOT_RUN_GATE_MISSING`, predictive quality `UNAPPROVED`, production `HOLD`로 기록한다. smoke 성공으로 품질 승인을 대신하지 않는다.
- production activation에는 품질 gate 통과, 별도의 명시적 사용자 승인, package-bound release manifest, 검증된 rollback 대상/절차가 모두 필요하다. 하나라도 없으면 pointer 변경·배포·활성화를 하지 않는다.

### 5. 외부 검토용 closeout과 전달

- `docs/operations/`에 compile/package 감사 결과와 daily smoke 상태를 담은 외부 검토용 closeout을 갱신한다. 민감한 credential은 포함하지 않고 재현 가능한 명령·artifact 경로·hash·검증 범위·제한·남은 blocker를 기록한다.
- compile 완료, 구조적 record coverage, LLM 직접 payload exposure, semantic unit 수, claim/citation coverage, daily smoke, predictive quality, production activation을 서로 다른 상태/수치로 보고한다.
- 위 문서와 closeout의 저장소 사본을 `C:\Users\eorb9\Downloads\codex_goal_nslab_finish_brain_and_daily_csv.md`와 동기화한다. 사용자가 남긴 변경과 untracked 산출물을 건드리지 않는다.
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
