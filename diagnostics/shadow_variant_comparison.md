# Shadow Variant Comparison

## 판정

`HOLD`. 정식 V0/V1 성능 비교가 완료된 것이 아니다.

가장 작은 sealed CALIBRATION 사례인 `NSLAB-20260102-be50ec83`도 cutoff-safe 뉴스가 490줄이었다. 공통 open-world 단계에서 `gpt-5.6-sol / xhigh`를 21회 실제 호출해 252개 cluster와 257개 current event를 분석했지만, V0 한 건이 끝나기 전 이미 5,798.275초가 경과했다. 이는 90초 일일 예산의 64.425배다.

이 시점 이후의 계산으로 latency gate를 회복할 수 없으므로 체크포인트를 보존하고 실행을 중단했다. V1의 cluster별 retrieval/evidence map 호출까지 계속했다면 호출 수와 지연은 더 증가한다.

## 비교 가능 범위

| 항목 | 상태 |
|---|---|
| V0 completed observation | `NO` |
| V1 completed observation | `NO` |
| paired market metrics | `UNAVAILABLE` |
| paired bootstrap | `UNAVAILABLE` |
| known-relevant Recall@32/64/128 | `UNAVAILABLE_REQUIRES_SEALED_RELEVANCE_LABELS` |
| future leak / web-call comparison | `UNAVAILABLE_BEFORE_COMPLETED_OBSERVATION` |
| production mutation | `NO` |

12-case mechanics Gate A는 별도로 통과했지만 그것은 검색 배선 검증이지 예측력 또는 운영 latency 입증이 아니다. 따라서 compiler v8, V2, HOLDOUT, 전체 brain rebuild로 진행하지 않는다.
