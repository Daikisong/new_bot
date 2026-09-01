# Offline Semantic Brain V2 운영 및 외부감사 기준

## 목적

이 compiler의 목적은 823,279개 연구 record를 매일 다시 읽는 것이 아니다.
기존 import와 실임베딩을 한 번 재사용해 의미 단위, 성공과 실패 경계,
수혜주, 대장주, 지속성 지식을 immutable `BrainPackage`로 합성하는 것이다.
매일 08시 CSV는 이 package만 조회하고 정상 LLM 2회로 판단한다.

## 구현 경계

```text
OFFLINE
823,279 existing records and 384d real embeddings
  -> full-population semantic geometry
  -> exactly one primary semantic unit per record
  -> dynamic medoid, boundary, outlier and outcome representatives
  -> gpt-5.6-sol/xhigh leaf synthesis
  -> category reduction and contradiction review
  -> world reduction
  -> immutable BrainPackage with provenance roots and HNSW indexes

DAILY
CSV -> CurrentEventCapsule -> LLM call 1
    -> local capsule and claim HNSW retrieval
    -> at most 24 exact witnesses
    -> LLM call 2 -> sealed decision
```

`build-offline`은 repair, import, record embedding을 다시 실행하지 않는다.
`analyze-daily`은 import, rebuild, raw-record LLM map 권한이 없다.

## 전수 의미 보존 규칙

- 모든 record는 primary semantic unit 하나에 정확히 한 번 배정된다.
- split geometry는 앞 4,096개 같은 표본이 아니라 해당 stratum 전체
  embedding을 사용한다.
- 동일 cell과 polarity 안에서도 cosine 경계를 넘는 메커니즘은 나뉜다.
- medoid, boundary, 원 stratum outlier, 최초와 최신, record type, label,
  training eligibility, high/upper status 대표를 동적으로 보존한다.
- 모든 semantic unit은 leaf capsule과 recursive reduce tree에 들어간다.
- first-N shortcut, silent truncation, child omission은 허용하지 않는다.
- 각 claim은 supporting 또는 contradicting capsule provenance를 가져야 한다.

raw payload를 LLM이 직접 읽은 수와 embedding·population으로 기여한 수는
같은 지표로 보고하지 않는다. 외부감사에는 둘을 별도로 공개한다.

대표가 된 긴 document를 앞부분만 자르는 것도 금지한다. 한 semantic
unit의 leaf 입력이 예산을 넘으면 그 unit의 모든 대표 document를 UTF-8
경계에서 최대 72,000 byte chunk로 무손실 분할한다. 각 chunk를 별도
map하고 chunk ID, document/chunk SHA, map node ID를 exposure ledger에
기록한 뒤 모든 digest를 leaf synthesis에 전달한다. exact witness의 800자
excerpt는 citation 표시용이며 semantic ingestion payload를 대신하지 않는다.

## Daily index 규칙

package DB에는 capsule HNSW와 mechanism claim HNSW가 모두 있어야 한다.
compiler와 reader는 실제 `EXPLAIN` 결과에서 `HNSW_INDEX_SCAN`을 확인한다.
reader가 capsule 전체를 cosine 정렬 scan하면서
`online_full_corpus_scan_count=0`이라고 보고하는 것은 금지한다.

선택한 package의 `build_cutoff`가 daily cutoff보다 늦으면 즉시 실패한다.
평가용 과거 날짜는 그 날짜 이전 record만 포함한 별도 as-of package를
사용해야 한다.

## 중단과 재개

LLM leaf와 reduce node는 prompt, schema, provider, model, reasoning,
compiler version으로 content-addressed checkpoint를 만든다. 프로세스가
중단돼도 이미 `ok`로 봉인된 node는 동일 입력에서 다시 호출하지 않는다.
오류나 미완료 checkpoint를 성공 결과로 취급하지 않는다.

incremental update는 이전 package의 `semantic_unit_id + member_record_root`가
같은 capsule과 동일 content-addressed reduce node를 재사용한다. 새 연구로
영향받은 unit과 그 조상만 다시 합성한다.

### v4 장문 digest 실패와 v5 복구

2026-09-02 첫 full build는 823,279개 local assignment를 끝낸 뒤 v4 장문
digest 한 건이 서로 다른 record의 SHA를 잘못 복사해 fail-closed로 종료됐다.
LLM이 의미뿐 아니라 immutable source identity까지 다시 작성하게 한 계약이
원인이었다. 상세 증거는
`diagnostics/offline_brain_v2_v4_failed_build_report.{json,md}`에 고정했다.

v5 응답 schema는 LLM에 `chunk_id`와 의미 digest만 요구한다. record ID,
semantic unit ID, chunk 순번, document/chunk SHA는 원본 chunk ledger에서
compiler가 결정론적으로 재부착한다. v4 checkpoint 11개는 감사용으로
보존하지만 compiler/prompt version이 달라 v5가 재사용하지 않는다.

## Production source의 legacy pointer 불일치

현재 source snapshot은 다음 상태다.

```text
snapshot                  MEMIDX-1e64a1b6e6ba7b07b799
pointer manifest SHA      fc0d847d...040804
actual manifest SHA       6c05dcf4...fd4576
database SHA              f2933b80...3b73
record corpus root        2d25581c...1f6d75
```

2026-08-27 외부감사 target lock과 두 독립 로컬 사본은 actual manifest SHA
`6c05dcf4...fd4576`을 동일하게 고정했다. 따라서 resolver는 기본적으로
불일치를 거부하고, 명령에 actual SHA를 명시했을 때만 읽는다. package와
compile manifest에는 pointer SHA, actual SHA, match 여부, explicit attestation
여부를 모두 기록한다. 원본 pointer나 source artifact는 수정하지 않는다.

## 2026-09-02 전수 계획 비교

세 계획은 모두 실제 823,279개 embedding을 LLM 0회로 계산했다.

```text
strict v2 diagnostic
semantic units                    144,408
representative records             331,544
logical calls                       12,102
문제                                대표 document 앞 4,000자만 사용

candidate v3 diagnostic
semantic units                     52,644
representative records             181,979
logical calls                        5,660
문제                                대표 document 앞 4,000자만 사용

authoritative v5
semantic units                     52,644
representative records             181,979
representative payload chars  231,021,740
representative exposure ratio       22.1042%
oversized semantic units                  38
chunked representative records           203
full payload chunks                      341
long-payload map calls                    90
leaf calls                              7,423
reduce/review calls                       158
total logical calls                     7,671
max concurrency                              4
truncated representative payloads            0
wall clock                          1,060.07초
production activated                    false
```

authoritative 파일은 `diagnostics/offline_brain_v2_full_plan.json`이며 SHA-256은
`1de209106188cf8b402d7ebfef2abcd7e6b53f262990889f9a840ed7161847f9`다.
strict와 candidate는 과분할·payload 절단 결함을 숨기지 않기 위한 진단
증거로만 보존하며 build 입력으로 사용하지 않는다.

v5 plan ID는 `OFFLINE-PLAN-149450301220655b94fe`다. v4와 semantic unit,
대표 원문, chunk, LLM topology 수가 동일하므로 source identity 결속 수정이
연구 원료의 의미 범위를 줄이지 않았음을 확인했다.

181,979개 대표 record는 짧은 payload를 leaf가 전부 읽거나, oversized
payload를 UTF-8 무손실 chunk-map한 뒤 leaf가 모든 digest를 읽는다. 나머지
641,300개는 LLM 직접 payload 노출로 계산하지 않으며, 전수 embedding
assignment, population 분포, provenance root로 기여한다고 별도 보고한다.

## 일회성 시간 예측

기존 실제 Codex OAuth `gpt-5.6-sol/xhigh` trace 142건의 wall time은 다음과
같다.

```text
median call       47.71초
mean call         54.39초
p90 call          87.13초
```

7,671 logical calls와 동시성 4가 이상적으로 유지될 때 단순 예측은 중앙
25.4시간, 평균 29.0시간, p90 46.4시간이다. rate limit, schema repair,
OAuth 경쟁에 따라 더 길어질 수 있다. 완료된 content-addressed checkpoint는
재실행하지 않으므로 중단 뒤 처음부터 다시 시작하지 않는다. 다만 시작 시
전수 local geometry 약 16분은 현재 구현에서 재계산한다.

## 외부 리뷰 질문

외부 reviewer는 다음을 분리해 판정해야 한다.

1. 823,279개 primary assignment가 정말 중복과 누락 없이 닫혔는가.
2. 표본 밖 이질 record가 별도 unit 또는 outlier 대표로 보존되는가.
3. 대표 직접 노출 수와 전체 population 기여 수를 과장 없이 구분했는가.
4. 모든 leaf가 category와 world reduce root까지 닫히는가.
5. 중단 후 완료 checkpoint를 재사용하는가.
6. daily retrieval이 raw record나 capsule 전체 scan을 하지 않는가.
7. daily 정상 LLM 호출이 정확히 2회이고 corpus 크기와 무관한가.
8. CALIBRATION과 HOLDOUT이 동일 `analyze-daily` 경로를 쓰는가.
9. quality gate 전 production pointer가 활성화되지 않았는가.

## 현재 상태

```text
DAILY_PRODUCT_PATH_IMPLEMENTED       true
DAILY_CALL_GRAPH_BOUNDED             true
HISTORICAL_RAW_DAILY_REMAP_ZERO      true
OFFLINE_V2_COMPILER_FIXTURE_TESTED   true
FULL_823279_PLAN_STRICT_COMPLETED     true
FULL_823279_PLAN_V5_COMPLETED         true
FULL_823279_BUILD_V4_FAILED_CLOSED    true
FULL_823279_BUILD_V5_RETRY_PENDING    true
FULL_823279_BUILD_COMPLETED           false
PREDICTIVE_QUALITY_EVALUATED          false
PRODUCTION_ACTIVATED                  false
```
