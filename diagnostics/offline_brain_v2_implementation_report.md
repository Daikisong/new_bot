# Offline Semantic Brain V2 구현 보고서

## 사용자가 요구한 제품

823,279개 연구 record를 한 번 offline에서 의미적으로 합성하고, 매일 08시
CSV에는 이미 만들어진 brain을 사용한다. daily 정상 LLM 호출은 현재 사건
해석 1회와 최종 시장 판단 1회, 총 2회다. daily에서 과거 raw record를 다시
map하는 호출은 0회다.

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

## 예상 시간

실제 과거 Codex OAuth 5.6-sol/xhigh trace 142건의 평균은 54.39초,
중앙값은 47.71초, 90분위는 87.13초다. 동시성 4의 단순 예측은 약
25.4시간에서 46.4시간이며 평균 기준 약 29.0시간이다. rate limit과 schema
repair에 따라 더 길 수 있다. 완료 checkpoint는 재사용하므로 중단 시
처음부터 LLM 호출을 다시 하지 않는다.

## 거부한 계획

`strict_v2`와 `candidate_v3`는 대표 document를 앞 4,000자로 자르면서
truncation 0이라고 보고할 수 있었기 때문에 build 입력으로 거부했다.
두 파일은 결함과 수정 과정을 외부에서 확인할 진단 증거로만 남긴다.

## 현재 판정

```text
PR-B compiler and daily reader       IMPLEMENTED
823,279 zero-call plan               PASS
full one-time LLM build              NOT STARTED
same-path CALIBRATION/HOLDOUT         NOT STARTED
production activation                HOLD
```

full build와 품질평가가 끝나기 전에는 production 완료로 보고하거나 pointer를
활성화하면 안 된다.
