# Daily LLM Call Graph After PR-A

새 production 진입점은 `nslab analyze-daily`와 `ThinDailyAnalyzer.analyze()`다.

## 고정 호출 그래프

```text
CALL 1: current_day_interpretation
CALL 2: final_market_decision
```

두 호출 모두 loop 밖에 있다. 설정상 structured schema repair는 호출당 최대 1회이므로 정상 논리 호출은 2회, 실제 agent invocation의 hard maximum은 4회다. outer tracing retry는 0으로 고정했다.

다음 단계는 모두 local code 또는 local index다.

```text
CSV parse / cutoff window
exact + semantic event clustering
row disposition ledger
CurrentEventCapsule 생성과 bounded projection
BrainPackage capsule/claim/population/graph 검색
citation·available_from closure 검증
artifact seal
```

## 차단된 경로

새 production graph에서는 아래 함수·작업이 도달 불가능하다.

```text
build_runtime_evidence_memos()
build_runtime_evidence_memos_packed()
historical record별 LLM map
memory cell별 LLM call
retrieval lane별 LLM call
daily import
daily brain rebuild
BLIND web
```

기존 `nslab analyze`는 `LEGACY_EXHAUSTIVE_DIAGNOSTIC_ONLY`로 표시했다. 해당 코드와 `runtime_evidence_map_reduce.v1`은 forensic/offline 감사에는 남지만 production 장전 경로로 사용할 수 없다.

## 현재 검증

회귀 테스트는 10/300개 입력 row와 10,000/823,279개 brain record population 선언에서 동일하게 논리 호출 2회를 확인했다. 모든 CSV row의 disposition, material cluster capsule 수, 본문 반복 미삽입, capsule·claim·record·population provenance closure도 검사한다.

## 아직 완료되지 않은 것

PR-A는 호출 경계와 타입을 구현한 단계다. Offline Semantic Brain V2 package와 bounded DuckDB/ANN reader가 아직 없으므로 기본 provider는 fail-closed한다. 따라서 현재 상태는 다음과 같다.

```text
DAILY_PRODUCT_PATH_IMPLEMENTED
DAILY_CALL_GRAPH_BOUNDED
HISTORICAL_RAW_DAILY_REMAP_ZERO
OFFLINE_BRAIN_BUILT = false
PREDICTIVE_QUALITY_EVALUATED = false
PRODUCTION_ACTIVATED = false
```
