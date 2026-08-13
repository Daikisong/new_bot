# Phase 0 기준선·계약 구현 보고서

상태: 구현·독립 검토·전체 회귀 검증 완료

## 1. 구현 범위

Phase 0에서는 production 분석 동작을 바꾸지 않았다. 이후 Phase가 공통으로 사용할 strict
contract와 현재 corpus의 재현 가능한 기준선만 추가했다.

```text
src/news_scalping_lab/contracts/memory_context.py
src/news_scalping_lab/tools/profile_brain_records.py
schemas/*memory*.schema.json 등 9개 schema
diagnostics/brain_memory_phase0_baseline.json
diagnostics/brain_memory_phase0_baseline.md
```

새 계약은 다음을 분리한다.

```text
뉴스 row 전수 coverage
semantic event cluster
record 전수 coverage
memory cell membership
관련 관측 모집단 통계
대표 성공·실패·반례
adaptive drill-down trace
최종 daily memory context
```

`DailyMemoryContext`는 60만 record 본문을 담는 계약이 아니다. 전수 corpus는 hash·count·artifact
reference로 결속하고, reasoning에는 population과 대표 record reference만 전달하도록 경계를
고정했다.

## 2. 실제 imported store 기준선

2026-08-13 현재 production project root의 accepted record store를 읽은 결과다.

```text
accepted record             968
episode                       6
training eligible           649
known typed payload         914
unknown typed payload        54
corpus manifest SHA-256
764f4f70e28b60fd930624f873bd841e92b94510330b64ad26e64ca831b0ddd2
```

unknown typed 54개는 버리지 않고 type별 분포와 corpus hash에 그대로 포함했다.

현재 polarity 분포는 다음과 같다. 이 값은 Phase 3 전의 기존 router 동작을 기록한 기준선이며,
목표값이 아니다.

```text
POSITIVE        59
NEGATIVE       384
NEAR_MISS       53
UNEXPLAINED    214
CONTEXT        136
UNKNOWN        122
```

outcome field coverage:

```text
usable outcome value    749
declared container      924
declared but unusable   176
high return             748
close return            505
upper limit             161
response class          103
missing usable outcome  219
```

missing outcome은 자동으로 NEGATIVE로 계산하지 않는다. 해당 record의 명시 type·label에도 부정
근거가 없으면 UNKNOWN으로 유지한다.

## 3. 중복 단위 기준선

현재 968 record에서 ticker 또는 company identity를 찾을 수 있는 822건을 issuer-day로 묶으면
456개 독립 단위가 된다.

```text
issuer-day keyed records     822
unique issuer-day            456
duplicate records            366
dedup reduction ratio    44.53%
```

이는 향후 반응률 분모를 raw record 수로 세면 같은 종목·날짜가 여러 표를 갖게 된다는 직접적인
근거다. 용도별 `event-issuer-day`, `theme-day`, `theme-day-pair`, `ticker-day`도 별도 profile로
계산한다.

현재 imported store에는 정규화된 market regime label이 없어 968개 모두 `UNKNOWN`이다. 따라서
Phase 5에서 regime 통계를 만들기 전에 cutoff-safe normalizer가 필요하다.

## 4. 현재 검색과 sweep 비용

현재 Python full-scan + deterministic hash embedding 검색을 968 record에서 3 query, 5회 반복한
로컬 기준선이다. 시간값은 실행 머신 상태에 따라 바뀌며 corpus manifest hash에는 포함되지 않는다.

```text
P50   약 98.5 ms
P95   약 134.7 ms
P99   약 134.7 ms
scan  968 records/query
current source index invalid (v1 artifact, current contract v2)
```

현재 sweep 계약을 20 records/shard로 적용하면:

```text
968 records
→ 49 record shards
→ episode 1 + record 49 = 총 50 artifacts
→ 실제 MemorySweeper payload 직렬화 약 1.12 MB
→ 현재 sweep estimator 약 278k tokens
```

이 수치는 final prompt 실측 token이 아니라 기존 전수 record 본문 전달 구조의 부담을 비교하기
위한 결정론적 기준선이다.

## 5. repaired inventory 기준선

실제 repaired MD 전체를 다시 파싱하지 않고 순차 repair manifest를 읽어 집계했다. production
store에는 import하지 않았다.

```text
manifest entries                     1,397
REPAIRED_PASS / ready for import      1,127
ready declared records              606,737
ready declared training eligible    384,846
all status declared records         657,237
all status declared eligible        415,154
record count가 선언된 entries         1,231
```

상태 분포:

```text
REPAIRED_PASS                         1,127
PRESERVED_PARTIAL_NOT_CURRENT_GOLD      165
DEFERRED_NON_TRADING                     96
PRESERVED_SOURCE_PAYLOAD_ABSENT           8
PARTIAL_PRICE_SOURCE_MISSING               1
```

실제 import-ready 606,737 records를 현재 20 records/shard 구조에 그대로 넣으면 약 30,337개
record shard가 된다. 보류 상태까지 모두 포함한 보존 상한 657,237 records는 약 32,862개다.
따라서 Phase 2에서 전수 coverage manifest와 final reasoning payload를 분리해야 한다는 외부 피드백이
실제 corpus 수치로 확인됐다.

## 6. 결정성과 안전 경계

```text
corpus manifest hash 입력:
BrainRecordEnvelope 전체 canonical JSON

hash에서 제외:
실행 시간, wall-clock latency, 파일 절대경로
```

같은 corpus는 동일 manifest hash를 만든다. profiler는 record store와 repair manifest를 읽기만 하며
research import, warehouse rebuild, brain rebuild, production pointer 변경을 수행하지 않는다.

## 7. 검증

추가한 회귀 검증:

```text
strict model의 unknown field 거부
schema version 고정
daily context에 raw corpus 본문이 들어가지 않음
fixture profile 결정성
같은 corpus manifest hash 일치
unknown record type 보존
missing outcome 비부정 처리
record/type/polarity/eligibility count parity
repair manifest-only inventory 집계
```

최종 검증:

```text
python -m ruff check .                         PASS
python -m mypy src/news_scalping_lab           PASS
python -m pytest                               1,210 passed
```

독립 검토가 발견하고 반영한 사항:

```text
corpus hash를 envelope 전체에 결속
모든 cutoff/available time을 timezone-aware로 강제
SHA-256 형식과 coverage/population count 불변식 강제
ready repair와 보류 repair record 수 분리
usable outcome과 ticker/company-only outcome container 분리
실제 LocalRetrievalStore 호출과 MemorySweeper payload 직렬화로 기준선 교체
```

## 8. 다음 단계

Phase 1은 이 계약 위에서 다음 두 문제만 해결한다.

```text
real provider에서도 앞 12개 뉴스만 보던 제한 제거
exact duplicate 수준의 cluster를 semantic event cluster로 확장
```

모든 입력 row는 정확히 하나의 primary cluster 또는 duplicate parent를 가져야 하고, 모든 material
cluster가 open-world first 분석에 정확히 한 번 들어가야 Phase 1을 완료한다.
