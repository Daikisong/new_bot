# Offline Semantic Brain V2 구현 보고서

## 사용자가 요구한 제품

823,279개 연구 record를 한 번 offline에서 의미적으로 합성하고, 매일 08시
CSV에는 이미 만들어진 brain을 사용한다. daily 정상 LLM 호출은 저장된 두뇌와
현재 뉴스를 함께 읽어 해석·검토·최종 판단하는 1회다. 별도 1차 해석은 없고,
구조화 응답 형식 오류에 한해 최대 1회 보정한다. daily에서 과거 raw record를
다시 map하는 호출은 0회다. 2026-09-07 사용자 정정으로 종전 2회 계약을 대체했다.

## 이번에 구현한 것

- 모든 record의 primary semantic unit 전수 assignment
- 표본 밖 record까지 사용하는 full-population embedding geometry
- medoid, boundary, outlier, 시점, record type, label, outcome 대표 선택
- 대표 payload 전체 읽기와 긴 payload UTF-8 무손실 chunk-map
- semantic capsule, mechanism claim, category/world recursive reduce
- content-addressed checkpoint 재개와 incremental package reuse
- capsule/claim DuckDB HNSW 및 실제 `HNSW_INDEX_SCAN` 검증
- daily package cutoff, future evidence, raw scan, activation fail-closed
- 구조적 기여 수와 LLM 직접 payload 노출 수의 분리 보고

## 전수 planner 결과

```text
record population contribution        823,279
semantic units                         52,644
representative full-payload reads      181,979 (22.1042%)
representative payload chars       231,021,740
representative truncation                    0
oversized semantic units                    38
chunked representative records             203
full payload chunks                        341
long-payload map calls                      90
leaf map calls                           7,423
reduce/review calls                        158
total logical calls                      7,671
max concurrency                              4
planner LLM calls                            0
production activated                     false
```

나머지 641,300개를 LLM이 직접 읽었다고 주장하지 않는다. 이 record들은
전수 embedding assignment, population 분포, provenance root에 기여한다.
대표가 된 181,979개는 앞 4,000자 절단 없이 전체 payload가 prompt 또는
chunk-map에 들어간다.

## 이전 계획의 시간 예측

실제 과거 Codex OAuth 5.6-sol/xhigh trace 142건의 평균은 54.39초,
중앙값은 47.71초, 90분위는 87.13초다. 동시성 4의 단순 예측은 약
25.4시간에서 46.4시간이며 평균 기준 약 29.0시간이다. rate limit과 schema
repair에 따라 더 길 수 있다. 완료 checkpoint는 재사용하므로 중단 시
처음부터 LLM 호출을 다시 하지 않는다. 이 수치는 초기 소규모 trace 기반의
과거 계획값이며, 현재 남은 시간을 보장하는 ETA가 아니다. 긴 실제 leaf 호출과
할당량 중단을 반영한 재개 이후 관측을 별도로 사용해야 한다.

## 2026-09-07 v5 재개

2026-09-03 Codex 할당량 한도로 중단된 동일 v5 build를 2026-09-07
19:05:51 KST에 재개했다. compile ID와 provider/model/reasoning이 기존과
동일한지 먼저 검증했으며, 중복 build 프로세스는 없었다.

```text
compile ID                    OFFLINE-COMPILE-add3461bd175147e255d
provider/model/reasoning       codex-oauth / gpt-5.6-sol / xhigh
successful nodes before resume 2,164 / 7,671 (28.2101%)
long-payload completed         90 / 90
leaf completed                2,074 / 7,423
reduce/review completed        0 / 158
remaining logical calls        5,507
resumed process ID             116688
```

비율은 합성 논리 호출 기준이지 소요시간 기준이 아니다. 기존 성공 checkpoint를
재사용하고, repair/import/기존 record embedding은 다시 실행하지 않는다.
local geometry는 현재 구현상 약 16~18분 재계산한다. 해당 local progress가
100%가 되어도 전체 LLM 합성이 끝났다는 뜻은 아니다.

19:29:47 KST 재관측에서 신규 leaf 4개가 성공해 총 2,168/7,671개(28.2623%)가
완료됐다. long-payload 90개, leaf 2,078개, reduce/review 0개이며 남은 논리
호출은 5,503개다. 이전 할당량 오류 checkpoint `LLMCKPT-978cd92f05059352`도
정상 `ok`로 저장됐고, 현재 v5 오류 checkpoint는 0개다. 프로세스는 계속 실행 중이다.

19:24:11 KST 중간 trace 관측에서 1,352개 모두 `checkpoint_hit`였으므로
기존 결과의 재사용도 실제 확인했다. 이는 최종 trace 개수로 주장하는 수치가 아니다.
할당량은 이번 신규 호출의 성공으로 확인했지만 남은 5,503개 전부를 끝낼 만큼의
잔여 할당량까지 보장하는 것은 아니다.

Goal 자동 실행 상태는 재개 전 `usageLimited`였고 19:29 KST 재확인에서는
`active`다. agent가 Goal 상태 변경 도구를 호출한 것은 아니다. 로컬 build 상태와
Goal 자동 실행 상태는 별도로 확인하며, 어느 쪽도 완료로 표시하지 않았다.

로그: `runs/offline_brain_v5_resume_20260907T190551.{stdout,stderr}.log`.
최신 goal은 `docs/operations/codex_goal_offline_brain_thin_daily_inference.md`이며
Downloads 원문도 같은 내용으로 수정하고 이전 원문은 별도 백업했다.
daily 수정은 offline compiler v5와 기존 성공 checkpoint 계약을 변경하지 않는다.

## 거부한 계획

`strict_v2`와 `candidate_v3`는 대표 document를 앞 4,000자로 자르면서
truncation 0이라고 보고할 수 있었기 때문에 build 입력으로 거부했다.
두 파일은 결함과 수정 과정을 외부에서 확인할 진단 증거로만 남긴다.

## 현재 판정

2026-09-07 재검증에서 Ruff, Mypy(138개 source file), 전체 pytest 1,887개가
통과했다. Downloads 원문과 저장소 goal 사본의 SHA-256도 동일하다. 이 테스트
통과는 실제 예측 품질 또는 full brain build 완료를 뜻하지 않는다.

```text
PR-B compiler and daily reader       IMPLEMENTED
823,279 zero-call plan               PASS
full one-time LLM build              RESUMED_LIVE_SUCCESS_CONFIRMED (not complete)
same-path CALIBRATION/HOLDOUT         NOT STARTED
production activation                HOLD
```

full build와 품질평가가 끝나기 전에는 production 완료로 보고하거나 pointer를
활성화하면 안 된다.
