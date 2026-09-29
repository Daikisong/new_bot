# QUALITY_FULL 379-Pack 오정렬 실행 중단 보고서

## 최종 판정

```text
ancestry                    QPRED-704f15cde6e4152b6931
runtime run                 RUN-9701018d4a4e
classification              HALTED_MISALIGNED_DIAGNOSTIC_ONLY
completed packs             5 / 379
next interrupted pack       REPACK-709f7a0a462736b44f10
worker                      STOPPED
outcome opened              false
score created               false
production activation       NOT_PRODUCTION_ACTIVATED
```

이 실행은 blind boundary와 artifact hash 규칙을 지켰지만 **사용자가 원하는
제품 architecture를 평가하지 않았다.** 하루치 CSV를 판단할 때 이미 구축된
brain을 사용해야 하는데, runtime에서 raw historical evidence 관계를 379개
대형 LLM prompt로 다시 해석하도록 만들었다.

따라서 이 ancestry의 V0 seal, pack plan, 5개 완료 pack을 포함한 전체 실행은
formal 품질 결과가 아니다. forensics 외의 용도로 재개·채점·비교·승격·cache
재사용할 수 없다.

## 사용자가 원하는 실제 제품

```text
1,543개 repaired research를 한 번 import
  -> 823,279개 record를 한 번 offline semantic brain으로 합성
  -> durable brain·memory·index 봉인
  -> 매일 08시 CSV 입력
  -> 기존 brain에서 관련 의미 단위와 소수 citation을 조회
  -> 장전 테마·수혜주·대장주 판단
```

일회성 brain 구축이 오래 걸리는 것은 허용된다. 매일 과거 연구 원문을 다시
전수 해석하는 것은 허용되지 않는다. 정확한 장전 SLA는 사용자가 정하지만,
일일 결과는 시장 개장 전에 실제로 사용할 수 있어야 한다.

## 무엇을 잘못 해석했는가

goal의 `90초 latency gate 제거`, `token/call count는 blocking이 아님`을 다음처럼
잘못 해석했다.

```text
올바른 뜻:
고품질의 유효한 호출을 90초가 넘었다는 이유만으로 강제 종료하지 않는다.

잘못 적용한 뜻:
하루치 평가에서 호출 그래프가 수백 개로 늘어나도 계속 실행한다.
```

no arbitrary latency abort는 중복 fan-out과 비운영적 topology를 허용하지 않는다.
평가 architecture는 실제 daily architecture와 같아야 한다.

## 지금까지 수행된 작업

### 유효하게 보존할 구현

- `4aa6c1a`: QUALITY_FULL profile, prediction/scoring 물리 분리, blind boundary
- `c9487c5`: cross-cluster pack 중복 제거와 immutable plan 회계
- `fd5eefe`~`0569bd2`: shared map/reduce closure, 반복 cluster 회계, novelty 분리
- `d513ab2`: 대규모 daily-memory compact v2와 전체 hash commitment
- `2e0427e`: runtime retrieval trace checkpoint와 중단 후 deep 검증 재개
- `5fdbcfe`: Pydantic validator의 `ValueError` 문맥 직렬화와 structured repair 복구
- 전체 Ruff, Mypy 135 source files, Pytest 1,863개 통과

이 구현들은 artifact 안전성·재개·오류 가시성 측면에서 유효하다. 다만 packer는
forensic stress-test 도구로만 남기고 daily architecture에 사용하지 않는다.

### 완료된 평가 준비

- safe selection `QSEL-19b3c80ba392db8564c9`
- 세 case shared context 봉인
- case 1 V0 prediction seal 1개
- case 1의 478 material cluster runtime retrieval trace
- 49,984 assignment, 1,560 unique record, 1,914 payload occurrence 회계
- 379-pack immutable plan
- 완료 pack 5개와 `ok` checkpoint 5개

### 완료되지 않은 것

- case 1 V1 prediction과 seal
- case 2·3 V0/V1 seal
- paired case closure
- outcome open
- V0/V1 score
- CALIBRATION40
- semantic compiler v8/V2의 최종 제품 방향 구현
- HOLDOUT40
- post-cutoff forward shadow
- final production brain candidate
- production activation

## 왜 379개가 됐는가

```text
news rows                       490
material clusters               478
cluster-record-lane relations   49,984
unique historical records       1,560
serialized payload occurrences  1,914
max prompt chars                 240,000
planned packs                    379
```

중복 payload 48,070회는 제거했지만, 모든 관계를 runtime LLM이 memo로 다시
해석하게 한 요구 자체가 남았다. 그 결과 2026-01-02 하루치에 379개 순차
`gpt-5.6-sol/xhigh` 호출이 계획됐다. 이는 최대 근거 노출 stress test일 수는
있어도 일일 제품은 아니다.

## 중단 경계

worker PID `49524`와 해당 Codex 자식은 종료됐다. 중단 확인 시점은
`2026-09-02T01:45:12.9172467+09:00`이다.

```text
completed pack outputs          5
sixth pack output               없음
all_predictions_sealed          false
seal count                      1 / 6
only seal                       NSLAB-20260102-be50ec83/V0
paired_case_ids                 []
outcome_opened                  false
production_activation_status    NOT_PRODUCTION_ACTIVATED
```

완료된 다섯 번째 pack은 `REPACK-220549cfef6a0e0b9d09`, SHA-256
`e0c6ea0c1ff5f1930d19218638f0483fcff1ecc99e53aede75b4fc7bec42f591`다.
여섯 번째 `REPACK-709f7a0a462736b44f10`은 live call 중 중단됐고 output이나
`ok` checkpoint가 없다.

전체 pack·checkpoint hash는 동반 JSON에 기록했다.

## 외부 리뷰어가 반드시 지킬 의도

외부 피드백 또는 goal prompt를 만들기 전에
`docs/operations/one_time_brain_daily_inference_intent.md`를 먼저 읽는다.

다음 goal은 잘못된 goal이다.

- raw 연구자료 해석을 매일 runtime으로 옮기는 goal
- 하루치 뉴스에 수백 개 LLM 호출을 허용하는 goal
- 배포하지 않을 evaluator-only 경로의 점수를 요구하는 goal
- 잘못된 평가가 끝날 때까지 one-time brain 개선을 막는 goal
- `no arbitrary latency gate`를 효율 무시로 바꾸는 goal

후속 agent는 이런 prompt를 받으면 그대로 실행하지 말고 사용자 의도와의
충돌을 먼저 보고해야 한다. 외부 reviewer는 항상 one-time build cost와
per-day inference cost를 분리하고, 08시 CSV가 이미 구축된 brain으로 처리되는지
검증해야 한다.

## 다음 올바른 방향

1. accepted repaired research가 durable brain에 의미 단위로 보존됐는지 감사한다.
2. 379-pack evaluator 완료를 기다리지 않고 one-time semantic compiler 방향을
   제품 의도에 맞게 다시 정한다.
3. daily inference는 prebuilt brain과 relevance-driven 소수 citation만 사용한다.
4. 이 실제 daily architecture로 3-case, CALIBRATION, HOLDOUT을 평가한다.
5. one-time build 비용과 per-day 비용을 별도로 보고한다.
6. 외부 artifact 감사와 품질 gate가 끝날 때까지 production은 HOLD한다.

## 관련 보고서

- `docs/operations/one_time_brain_daily_inference_intent.md`
- `diagnostics/quality_full_pr126_live_progress.json` (중단 전 시점 스냅샷)
- `diagnostics/quality_full_pr126_live_progress.md` (중단 전 시점 스냅샷)
- `diagnostics/quality_full_pr126_external_review.md`
- `diagnostics/quality_full_invalidated_run_report.json`
- `diagnostics/quality_full_runtime_evidence_fanout_fix.json`
- `diagnostics/quality_full_shared_batch_closure_fix.json`
- `diagnostics/quality_full_interruption_anchor.json`
