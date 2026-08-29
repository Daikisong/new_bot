# QUALITY_FULL Runtime Evidence Fan-out Fix

## 결론

안전한 3-case 평가의 첫 날짜는 material cluster가 478개였다. 기존 V1
구현은 cluster마다 1~6개의 evidence LLM 호출을 순차 실행하므로, 해당
날짜만 478~2,868회의 추가 호출이 필요했다. 이는 연구 품질을 위해 서로
다른 근거를 읽는 시간이 아니라 여러 cluster가 선택한 동일 record payload를
반복 전송하는 구조적 fan-out이었다.

실행은 `SHAREDCTX-9934ed4eef311fd9d5d6`에서 checkpoint 중단했다. 기존
map/reduce checkpoint를 복구했고 live shared novelty batch 7개가 완료됐으며
다음 호출은 중단됐다. V0/V1 prediction, `QPRED`, score는 생성되지 않았고
production artifact는 변경되지 않았다.

## 변경

- 모든 `(cluster_id, record_id, lane)` assignment를 보존한다.
- 여러 cluster가 공유하는 record payload는 bounded pack마다 한 번만 넣는다.
- prompt는 240,000 characters 이하로 pack하며 first-N과 silent truncation을
  허용하지 않는다.
- 첫 provider 호출 전에 `runtime_evidence_pack_plan.json`을 불변 저장한다.
- 완료 manifest는 plan, normalized output, tracing-provider checkpoint와 원출력
  SHA-256을 연결한다.
- QUALITY_FULL scoring은 pack 수와 인증 checkpoint 수가 다르면 실패한다.

## 회귀 관찰

2개 cluster가 같은 20개 record를 선택한 fixture에서는 40개 assignment를
유지하면서 payload occurrence를 40개에서 20개로 줄였고 호출은 1회였다.
큰 payload fixture는 3개 이상의 pack으로 나뉘었으며 prompt budget과 모든
assignment coverage를 유지했다. 동일 입력 재개에서는 live provider 재호출이
0회였고, plan을 다시 봉인해도 completed manifest와 다르면 검증이 거부됐다.

## 아직 측정하지 않은 것

실제 첫 날짜의 새 pack 수는 아직 없다. local retrieval이 끝난 뒤 pre-call
plan에 기록되는 pack 수가 정확한 runtime-evidence 호출 수다. 따라서 실제
3-case 소요시간과 V0/V1 predictive quality는 계속 `NOT_EVALUATED`이고
production activation은 `HOLD`다.

## 검증

`ruff`와 `mypy`(135 source files)를 통과했고, 전체 `pytest`는
1,845 tests를 342.28초에 모두 통과했다. 이 수치는 구현 회귀 gate이며 실제
3-case predictive score가 아니다.
