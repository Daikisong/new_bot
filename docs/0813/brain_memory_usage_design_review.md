# NSLAB 장기기억 두뇌 활용 설계 검토 요청서

상태: 구현 전 설계 검토

작성일: 2026-08-13

외부 검토 반영 문서:

```text
docs/0813/external_brain_design_feedback.md
docs/0813/brain_memory_implementation_phases.md
```

외부 총괄 판정은 `ACCEPT_WITH_CHANGES`다. 특히 뉴스 12개 제한, exact-only event
clustering, 선형 전수 record 검색, exhaustive shard 본문의 final prompt 전달,
eligibility와 polarity 혼합을 선행 P0로 확인했다. 이 문서의 방향은 유지하되 상세 구현은
위 두 문서의 수정사항과 phase 순서를 따른다.

검토 대상:

```text
약 10년의 뉴스-가격 연구 record를 아침 뉴스 판단에 어떻게 충분히 활용할 것인가
```

이 문서는 연구 생성·repair 방식을 다시 논의하는 문서가 아니다. 이미 repair된 연구
record를 실제 일일 판단에서 사용하는 retrieval, aggregation, compression, final synthesis
구조를 검토하기 위한 문서다.

외부 검토자는 특히 아래를 확인해 주기 바란다.

```text
1. 60만 개 record를 빠뜨리지 않고 활용한다는 의미가 올바르게 정의됐는가
2. 대표 사례 수를 무작정 늘리지 않고도 전체 모집단의 분포를 보존할 수 있는가
3. 긍정 사례만 가까운 이웃으로 몰리는 편향을 충분히 막는가
4. 뉴스가 없거나 반응이 없었던 사례도 판단에 실질적으로 반영되는가
5. 압축 과정에서 LLM의 자의적 삭제·보상 해킹을 막을 수 있는가
6. BLIND cutoff와 provenance가 끝까지 유지되는가
7. 60만 개에서 수백만 개로 늘어나도 비용·지연이 감당 가능한가
```

## 1. 현재 연구 원료

현재 Phase A repair 기준 계획 모집단은 다음과 같다.

```text
READY_FOR_IMPORT bundle                1,127개
READY_FOR_IMPORT brain record        606,737개
training_eligible record             384,846개
```

이 수치는 production import 전 repaired 결과 기준이다. 현재 production brain에 이미
606,737개가 들어갔다는 뜻은 아니다.

주요 record 종류의 예시는 다음과 같다.

```text
supervised_direct_event_case          직접 기업 사건과 실제 반응
supervised_issuer_day_case            종목 하루 전체의 뉴스와 실제 반응
negative_control_case                 비슷해 보였지만 반응이 약했던 사례
newsless_or_unexplained_case          강하게 움직였지만 cutoff-safe 뉴스로 설명되지 않는 사례
candidate_generation_error_case       후보에서 놓친 사례
candidate_ranking_error_case          후보였지만 순위를 잘못 준 사례
ranking_error_case                    사후 확인된 순위 오류
theme_formation_case                  섹터·테마 형성 또는 실패 사례
beneficiary_discovery_case            후발·간접 수혜 발견 사례
counterexample                        상승 해석과 반대되는 사례
context_market_state_or_fact_case     시장 상태와 사실 문맥
```

`training_eligible`은 호재 여부가 아니다. 근거, provenance, label 계약이 닫혀 학습에
사용할 수 있다는 뜻이다. 실제 방향은 별도 `evidence_polarity`와 `memory_lanes`로
계산한다.

현재 memory lane은 다음 8개다.

```text
positive_analogs
negative_controls
near_misses
counterexamples
leader_selection_pairs
theme_formation_failures
candidate_generation_errors
newsless_or_unexplained
```

## 2. 사용자가 원하는 판단

아침 뉴스 하루치를 주면 두뇌가 아래 질문을 함께 답해야 한다.

```text
이 사건과 비슷한 과거 사건은 실제로 얼마나 자주 강하게 반응했는가
좋아 보였지만 반응하지 않은 경우는 얼마나 많았는가
처음에는 중요하지 않다고 봤지만 실제로 움직인 경우가 있었는가
단일 종목 뉴스가 섹터 전체로 확장된 경우와 실패한 경우의 차이는 무엇인가
직접 뉴스가 없는 움직임을 억지 뉴스 원인으로 설명하고 있지 않은가
후발 수혜주를 놓친 과거 패턴이 현재에도 재현되는가
과거의 대장주 선택 오류와 현재 후보의 차이는 무엇인가
현재 시장 상태가 과거 성공 사례와 같은가, 다른가
```

원하는 결과는 단순한 종목·키워드 lookup이 아니다.

```text
현재 사건 이해
+ 과거 모집단 통계
+ 다양한 대표 성공/실패 사례
+ 반례와 설명되지 않는 사례
+ 현재와 과거의 구조적 차이
→ 후보 생성·검증·순위 판단
```

## 3. 현재 코드의 실제 동작

### 3.1 현재 뉴스 처리

현재 뉴스는 먼저 전체 row를 사건 cluster로 묶는다. 이 단계에서 과거 기억을 근거로
뉴스를 버리거나 후보를 차단하면 안 된다. open-world first pass가 과거 기억보다 먼저다.

관련 코드:

```text
src/news_scalping_lab/inference/analyzer.py
```

### 3.2 현재 semantic retrieval

현재 설정은 사건 cluster마다 memory lane별 최대 3개 record를 검색한다.

```text
8 lanes x 3 records = cluster당 최대 24개
```

전체 뉴스에 대한 semantic retrieval plan도 lane별 query마다 최대 5개 record를 검색한다.

```text
8 lanes x 5 records = query가 lane별 1개일 때 최대 40개
```

cluster별 결과는 round-robin으로 최대 360개까지 final context로 승격될 수 있다.

관련 설정:

```text
configs/default.yaml
cluster_coverage_record_limit_per_lane: 3
cluster_coverage_promoted_record_limit: 360
```

24개는 전체 두뇌 사용량이 아니라 사건별 대표 사례 후보 수다. 하지만 현재 구조에는
그 24개 뒤의 전체 관련 모집단 분포를 계산해 전달하는 계층이 없다. 따라서 대표 사례가
우연히 편향되면 10년치 기억의 실제 분포가 final LLM에 보이지 않을 수 있다.

### 3.3 현재 exhaustive sweep

`exhaustive` 모드는 cutoff 이전의 모든 accepted record를 읽고, 현재 기본값으로 record
20개씩 shard contribution을 만든다.

606,737개가 모두 import됐다고 가정하면 대략 다음 규모다.

```text
606,737 / 20 = 약 30,337 record shards
```

현재 final synthesis payload는 `record_level_shard_contributions`를 전부 읽어 넣는다.
작은 테스트 데이터에서는 안전하지만 60만 개 production 규모에서는 prompt 크기,
serialization, 디스크 I/O, LLM 입력 비용이 현실적으로 감당되지 않는다.

관련 코드:

```text
src/news_scalping_lab/context/sweep.py
src/news_scalping_lab/inference/analyzer.py
src/news_scalping_lab/context/final_synthesis.py
```

`session_pack_token_budget=60,000`은 존재하지만 현재 final synthesis의 전체
`record_level_shard_contributions`에 직접 적용되는 계약은 아니다.

## 4. 문제 정의

다음 세 문장은 서로 다른 요구다.

```text
A. 모든 record를 보존한다.
B. 모든 cutoff-safe record가 누락 감사 대상이 된다.
C. 모든 record 원문을 매일 LLM prompt에 넣는다.
```

A와 B는 필수다. C는 불가능하고 필요하지도 않다.

두뇌가 모든 기억을 활용한다는 것은 아래를 의미해야 한다.

```text
모든 record가 정확히 저장·색인·분류됨
모든 cutoff-safe record가 모집단 또는 집계에 기여함
현재 사건과 관련된 모집단을 넓게 찾음
관련 모집단 전체의 반응 분포와 불확실성을 계산함
그 분포를 대표하는 성공·실패·반례 record를 LLM에 보여줌
최종 판단이 어떤 모집단과 어떤 record에 근거했는지 추적 가능함
```

### 4.1 변경할 수 없는 프로젝트 원칙

외부 제안도 다음 원칙을 위반하면 채택하지 않는다.

```text
종목·ticker·theme·region·beneficiary 관계를 production 코드에 하드코딩하지 않음
candidate generation은 항상 open-world pass에서 시작
exact keyword는 보조 근거이며 후보 생성·차단 gate가 아님
새 연구를 추가할 때 source code 변경이 필요하지 않아야 함
brain_delta와 unknown record를 조용히 버리지 않음
BLIND 단계는 cutoff 이후 정보와 현재 D-day 가격에 접근하지 않음
모든 통계·summary·대표 사례에 provenance와 context manifest를 남김
```

이 설계는 특정 LLM provider에 종속되지 않는다. Codex가 조종하는 개인 운영과 실제 API
provider를 붙인 production `llm-full` 운영 모두 같은 population/context artifact를 사용해야
한다.

## 5. 검토 요청 설계: 세 경로 분리

### 5.1 경로 A: 전수 보존·누락 감사

목적은 판단 prompt 생성이 아니라 기억 손실 방지다.

```text
모든 accepted record
→ available_from cutoff 검사
→ ID/type/hash/provenance/index coverage 검사
→ lane/polarity/cluster membership coverage 검사
→ 누락·중복·미색인 record 보고
```

산출 예:

```json
{
  "accepted_record_count": 606737,
  "available_as_of_count": 412880,
  "indexed_as_of_count": 412880,
  "unindexed_record_count": 0,
  "routing_disposition_unassigned_record_count": 0,
  "provenance_unclosed_count": 0
}
```

이 경로는 매일 전체 원문을 LLM에 넣지 않는다. 메타데이터·hash·index manifest로
coverage를 닫는다.

모든 record가 8개 판단 lane 중 하나에 들어가야 한다는 뜻은 아니다. 시장 문맥, research
question, audit-only record는 `CONTEXT`, `AUDIT`, `QUARANTINED` 같은 명시적 routing
disposition으로 닫을 수 있다. 허용되지 않는 것은 아무 역할도 없이 조용히 빠지는 record다.

### 5.2 경로 B: 모집단 검색·통계

현재 사건 cluster마다 단순 top 3을 바로 고르지 않는다.

```text
사건 cluster query
→ lane별 넓은 recall pool 수집
→ metadata/filter/semantic union
→ issuer-day 중복 및 동일 사건 파생 record 그룹화
→ 관련 모집단의 결과 분포 계산
→ 시장 상태·기간·직접성별 subgroup 비교
```

각 lane에서 최소한 아래를 계산한다.

```text
total_matched_record_count
unique_episode_count
unique_issuer_day_count
training_eligible_count
positive / negative / near_miss / unexplained count
upper_limit_touched count
high_return_pct 구간별 count
close_return_pct 양수/음수 count
median / quartile / tail distribution
record_type별 count
path_type별 count
최근 기간 vs 과거 기간 차이
현재 시장 상태와 유사한 subgroup count
effective_sample_size
missing_outcome_count
low_quality_or_quarantined_count
```

중요한 원칙:

```text
training_eligible를 positive로 세지 않는다.
record 여러 개가 같은 issuer-day를 설명할 때 하루를 과대표현하지 않는다.
newsless 사례를 뉴스 원인 성공률의 분모·분자에 임의로 섞지 않는다.
outcome이 없는 record는 0% 반응으로 간주하지 않는다.
상관관계를 인과관계라고 표현하지 않는다.
```

### 5.3 경로 C: 대표 사례·반례를 LLM에 전달

LLM에는 모집단 통계와 함께 다양한 대표 사례를 제공한다.

```text
관련 모집단 1,284건
positive 312 / negative 701 / near miss 190 / unknown 81
unique issuer-day 436

대표 positive 12건
대표 negative control 12건
대표 near miss 8건
대표 counterexample 8건
대표 newsless/unexplained 8건
다른 시장 상태의 반례 6건
```

대표 사례는 단순 similarity 상위순으로만 고르지 않는다. 다음 축의 다양성을 강제한다.

```text
outcome polarity
record type
episode/date
issuer-day
market regime
direct / theme / beneficiary path
novelty level
confidence / label quality
success와 failure boundary
```

24개 제한은 이 경로의 초기 대표 사례 수로만 해석할 수 있다. 최종 개수는 고정 숫자보다
모집단 크기, 분포의 복잡성, 상충 정도, 현재 판단 불확실도에 따라 적응적으로 늘어나야 한다.

## 6. 권장 계층형 장기기억 구조

### 6.1 Layer 0: 원본 record store

```text
memory/records/
```

모든 record와 provenance의 최종 진실이다. 요약본으로 대체하지 않는다.

### 6.2 Layer 1: 검색 index

각 record에 다음을 색인한다.

```text
embedding
memory_lanes
evidence_polarity
record_type
trade_date / available_from
episode_id / issuer-day group
path_type
market-state features
outcome numeric fields
training_eligible / label_quality
provenance IDs
```

정확 keyword는 보조 feature일 뿐 candidate gate가 아니다.

### 6.3 Layer 2: semantic memory cells

record를 의미적으로 가까운 작은 memory cell에 묶는다. 종목명·테마명 하드코딩이 아니라
embedding과 구조적 feature로 만든다.

각 cell은 다음을 보존한다.

```text
cell_id
member_record_ids 전체
member_count
member hash root
lane별·polarity별 분포
issuer-day deduplicated 분포
기간·시장상태별 분포
대표 record IDs
반례 record IDs
cell summary
summary provenance
compiler/model/version
available_from_min/max
```

모든 record는 최소 하나의 primary cell에 속해야 한다. 여러 cell에 보조 membership을
가질 수 있지만 count 통계에서는 primary membership 또는 명시된 fractional weight로
중복을 방지한다.

### 6.4 Layer 3: category brain

`llm-full` compiler가 memory cell을 다시 category별로 종합한다.

```text
single_event
theme_formation
beneficiary_discovery
leader_selection
continuation
failure_modes
counterexamples
market_memory
world_model
```

category brain은 원본 record 대신 쓰는 요약본이 아니다. 빠른 사전지식과 retrieval query
계획을 만드는 계층이다. 모든 claim은 supporting/contradicting cell과 record ID로 내려갈 수
있어야 한다.

### 6.5 Layer 4: 일일 adaptive retrieval

```text
오늘 사건 cluster
→ category brain으로 query expansion
→ relevant memory cell broad recall
→ cell 내부 관련 record 확장
→ 모집단 통계
→ diverse representative selection
→ LLM 비교 판단
→ 불확실하면 drill-down 재검색
```

## 7. top-K를 어떻게 다룰 것인가

고정 `top 3`을 전체 근거로 쓰는 것은 반대한다. 그렇다고 상한 없이 전부 prompt에 넣는
것도 반대한다.

권장하는 두 단계는 다음과 같다.

```text
recall K: 통계와 대표 사례 후보를 만드는 넓은 pool
context K: LLM에 실제 전달하는 작고 다양한 대표 집합
```

초기 구현 가설이며 외부 검토가 필요한 값:

```text
lane별 recall pool:
  1차 200
  coverage/similarity margin이 불충분하면 500 → 1,000 → 2,000 확장

lane별 context representatives:
  기본 8~16
  polarity 충돌·분포 복잡성이 크면 최대 32

cluster 전체 promoted representatives:
  단순 360 hard cap 대신 token budget과 diversity coverage로 결정
```

숫자는 계약이 아니라 시작점이다. 실제 1,127일 corpus로 retrieval recall과 비용 실험 후
결정해야 한다.

### 검토 질문 A

```text
고정 K보다 similarity threshold + 최소 K + 최대 K 조합이 나은가
ANN top-K와 metadata subgroup sampling의 union이 충분한가
관련 모집단을 정의할 때 semantic radius를 어떻게 calibration할 것인가
long-tail beneficiary를 놓치지 않으려면 별도 query expansion이 필요한가
```

## 8. 모집단 weighting

같은 날·같은 종목·같은 사건에서 여러 record가 생성된다. 이를 독립 사례처럼 모두 세면
연구 형식이 풍부한 날짜가 과대표현된다.

권장 통계 단위:

```text
record-level count               audit용
episode-level count              날짜별 연구 coverage용
issuer-day-level count           가격 반응 확률용
event-issuer-day-level count     직접 사건 반응용
theme-day-level count            섹터 형성 반응용
```

기본 가격 반응률은 issuer-day 또는 event-issuer-day로 계산하고, 여러 record는 그 사례의
근거·관점으로 묶는 편이 타당하다.

### 검토 질문 B

```text
직접 사건 성공률의 기본 분모는 event-issuer-day가 맞는가
theme 사례는 theme-day와 ticker-day 중 어느 단위를 우선해야 하는가
같은 사건의 여러 기사·fact를 fractional weight로 합칠 것인가
오래된 사례에 time decay를 둘 것인가, 기간 subgroup만 보여줄 것인가
시장 regime weight는 사전 계산인가, 오늘 query 시 계산인가
```

## 9. LLM 압축 계약

LLM이 record를 읽고 중요해 보이는 것만 자유롭게 남기게 하면 누락과 보상 해킹이 생길 수
있다. 통계는 결정론적 코드가 계산하고, LLM은 설명과 비교만 담당해야 한다.

### 결정론적 계산 영역

```text
record membership
cutoff filtering
deduplication
count / rate / percentile
representative selection constraints
provenance closure
coverage and missing IDs
token budget allocation
```

### LLM 담당 영역

```text
현재 사건과 과거 memory cell의 구조적 공통점·차이 설명
성공 조건과 실패 경계 언어화
상충 사례가 현재 판단에 주는 의미
추가로 확인할 질문 생성
최종 후보의 근거와 반대 근거 종합
```

LLM summary에는 반드시 다음이 붙는다.

```text
input cell IDs
input record IDs 또는 population manifest hash
supporting record IDs
contradicting record IDs
member count
outcome distribution
compiler/model/prompt version
available_from ceiling
summary hash
```

### 검토 질문 C

```text
cell summary를 LLM이 만들되 deterministic statistics에 구속하는 방식이 충분한가
요약 2단계에서 information loss를 어떻게 정량화할 것인가
서로 다른 LLM 두 개의 독립 요약·비교가 필요한가
summary drift를 막기 위해 대표 사례와 통계를 매 rebuild마다 고정해야 하는가
```

## 10. 일일 판단 payload 제안

final LLM에는 전체 shard 원문 대신 아래 구조를 전달한다.

```json
{
  "current_event_cluster": {},
  "retrieval_population": {
    "matched_record_count": 1284,
    "unique_episode_count": 219,
    "unique_issuer_day_count": 436,
    "polarity_counts": {
      "positive": 312,
      "negative": 701,
      "near_miss": 190,
      "unexplained": 81
    },
    "outcome_distribution": {},
    "time_slices": [],
    "market_regime_slices": [],
    "coverage_manifest_sha256": "..."
  },
  "memory_cells": [],
  "representative_records": {
    "positive": [],
    "negative": [],
    "near_miss": [],
    "counterexamples": [],
    "newsless_or_unexplained": []
  },
  "unresolved_disagreements": [],
  "retrieval_audit": {}
}
```

LLM은 대표 사례의 개수만 보고 성공 확률을 추측하면 안 된다. 확률과 분포는
`retrieval_population`을 사용하고, 대표 사례는 조건과 메커니즘을 이해하는 데 사용한다.

## 11. adaptive drill-down

한 번의 retrieval로 바로 끝내지 않는다. 다음 조건에서는 해당 부분만 추가 검색한다.

```text
positive와 negative 비중이 비슷함
effective sample size가 작음
대표 사례가 특정 날짜·종목·시장상태에 몰림
현재 사건과 유사한 cell의 내부 분산이 큼
newsless 비중이 높아 뉴스 인과 설명이 약함
beneficiary 경로의 근거가 한 단계 이상 추론에 의존함
현재 후보 간 대장주 선택 근거가 상충함
```

추가 검색은 전체 prompt를 다시 만드는 것이 아니라 부족한 lane/subgroup만 확장한다.

## 12. BLIND와 시간 안전성

일일 retrieval의 모든 입력은 다음을 만족해야 한다.

```text
record.available_from <= current cutoff
company memory known_at <= current cutoff
source published_at <= current cutoff 또는 명시적 time-unverified 제외
D-day outcome은 현재 BLIND query나 prompt에 포함 금지
과거 record의 과거 D-day outcome은 record.available_from 이후의 역사적 기억으로 사용 가능
```

마지막 문장이 중요하다. 2024년 사건의 다음 날 공개된 outcome memory는 2026년 분석에는
사용할 수 있지만, 2024년 당일 08:59 분석을 재현할 때는 사용할 수 없다.

### 검토 질문 D

```text
historical outcome record가 현재 BLIND에는 허용된다는 계약이 명확한가
market regime를 계산할 때 현재 D-day 가격을 실수로 사용하는 경로가 없는가
retrieval cell summary의 available_from은 모든 member의 max로 두는 것이 안전한가
시간이 다른 member를 포함한 cell을 as-of query에서 효율적으로 필터링할 방법은 무엇인가
```

## 13. 권장 구현 변경

### 13.1 새 모듈 후보

```text
src/news_scalping_lab/memory/population.py
  관련 모집단, dedup key, outcome distribution 계산

src/news_scalping_lab/memory/cells.py
  semantic memory cell build/load/version

src/news_scalping_lab/memory/diversity.py
  polarity·기간·regime·episode 다양성 대표 추출

src/news_scalping_lab/memory/adaptive_retrieval.py
  recall 확장과 drill-down 상태 머신

src/news_scalping_lab/contracts/memory_context.py
  population/cell/representative/retrieval audit strict schema
```

### 13.2 기존 모듈 변경 후보

```text
retrieval/store.py
  memory cell index와 broad recall API 추가

context/sweep.py
  모든 record 원문 contribution 생성 경로와 coverage audit 경로 분리

inference/analyzer.py
  top 3 직접 final 승격 대신 adaptive retrieval orchestrator 호출

context/final_synthesis.py
  population statistics + representative context 계약으로 변경

brain/compiler.py
  memory cell/category synthesis와 record provenance 하강 경로 추가

audits/provenance.py
  population manifest, cell membership, representative lineage 검증
```

## 14. 단계별 구현 순서

### Stage 0. corpus profiling

현재 repaired 1,127일로 실제 분포를 먼저 측정한다.

```text
lane별 record 수
issuer-day 중복률
event-issuer-day 중복률
outcome numeric coverage
response class alias coverage
embedding distance distribution
날짜·시장상태 편중
record 평균/최대 크기
```

### Stage 1. exhaustive audit와 reasoning payload 분리

가장 먼저 해야 한다.

```text
현재: 모든 20-record shard contribution을 final payload에 포함
변경: 전수 coverage manifest만 final에 포함
      관련 population과 representative records만 reasoning payload에 포함
```

기존 provenance audit가 모든 record coverage를 계속 검증해야 한다.

### Stage 2. broad recall + population statistics

memory cell 없이 먼저 구현 가능하다.

```text
lane별 broad ANN retrieval
metadata union
issuer-day dedup
deterministic distribution
diverse representatives
```

현재 top 3과 새 방식의 결과를 shadow mode로 비교한다.

### Stage 3. semantic memory cells

corpus가 충분히 import된 뒤 offline build한다.

```text
cell build
membership audit
cell statistics
representative selection
LLM summary
category brain integration
```

### Stage 4. adaptive drill-down

불확실도 기반 추가 검색을 붙인다.

### Stage 5. production provider와 성능 검증

```text
real embedding
llm-full rebuild
production doctor
latency/cost benchmark
historical replay eval
```

## 15. 비교해야 할 대안

### 대안 A: top-K만 크게 늘리기

예: lane별 3개를 50개로 변경.

장점:

```text
구현이 단순함
```

문제:

```text
similarity 상위 편향은 그대로임
전체 모집단 분포를 모름
동일 날짜·동일 종목 중복 가능
50이라는 수의 근거가 없음
prompt 비용만 증가할 수 있음
```

판정: 단독 해결책으로 부적합.

### 대안 B: 모든 record shard를 LLM에 넣기

장점:

```text
표면적으로는 전수 사용
```

문제:

```text
60만 개에서 prompt 불가능
비용·지연 과다
LLM attention 희석
중간에서 자의적 누락될 가능성
```

판정: production 방식으로 부적합.

### 대안 C: 계층형 retrieval + 결정론적 통계 + 대표 사례

장점:

```text
전체 모집단 분포와 구체 사례를 함께 사용
긍정 편향 제어 가능
규모 확장 가능
provenance와 coverage를 닫을 수 있음
```

문제:

```text
cell build와 weighting 설계가 필요
평가 기준 없이 만들면 새로운 압축 편향이 생길 수 있음
```

판정: 현재 권장안.

## 16. 검증 데이터와 평가

단순 unit test만으로 충분하지 않다. 과거 날짜 replay eval이 필요하다.

### Retrieval 평가

```text
known relevant record recall@pool
positive/negative/newsless lane precision
issuer-day duplicate rate
year/regime diversity
long-tail beneficiary recall
counterexample inclusion rate
```

### Compression 평가

```text
population count parity
distribution parity
representative set coverage
minority polarity preservation
summary claim provenance closure
summary rebuild determinism
```

### 판단 평가

```text
candidate recall
final top-N hit rate
false-positive rate
newsless cause hallucination rate
theme over-expansion rate
leader selection error rate
calibration by confidence bucket
```

### 시스템 평가

```text
P50/P95/P99 latency
embedding query count
LLM input/output tokens
cache hit rate
peak memory
daily cost
full corpus rebuild duration
```

## 17. 필수 회귀·엣지 케이스

최소 다음을 테스트해야 한다.

```text
1. positive와 negative가 같은 embedding neighborhood에 몰린 경우
2. top similarity가 모두 같은 날짜·종목인 경우
3. 관련 positive는 많지만 negative control은 드문 경우
4. negative control은 많지만 현재와 시장상태가 다른 경우
5. 뉴스 없이 급등한 사례가 많은 사건
6. direct event와 theme beneficiary가 혼재한 사건
7. 최근 1년과 과거 9년의 반응이 반대인 사건
8. outcome numeric field가 없는 legacy record
9. training_eligible=true인 negative_control_case
10. training_eligible=false인 높은 수익 outcome record
11. 같은 issuer-day에서 record가 수십 개 파생된 경우
12. 동일 사건이 여러 memory cell에 속한 경우
13. cutoff 이후 available_from record가 검색 상위인 경우
14. cell summary의 일부 member만 cutoff-safe인 경우
15. embedding provider 변경 후 index가 stale인 경우
16. 대표 사례가 한 polarity만 차지하려는 경우
17. recall pool 확장에도 effective sample size가 작은 경우
18. long-tail beneficiary가 direct similarity로 잡히지 않는 경우
19. counterexample이 positive query와 매우 가까운 경우
20. 60만 개 전체 coverage 중 1개 record가 index에서 빠진 경우
21. population manifest hash가 final context와 불일치하는 경우
22. final LLM이 대표 사례 count를 모집단 비율로 오해하는 경우
23. LLM summary가 결정론적 통계와 모순되는 경우
24. no-news 사례에 임의 catalyst를 붙이는 경우
25. market regime 계산이 D-day 가격을 읽으려는 경우
26. query expansion이 특정 종목·테마 whitelist가 되는 경우
27. exact keyword miss지만 semantic mechanism은 같은 경우
28. 같은 뉴스가 여러 cluster에 중복된 경우
29. cluster 수가 매우 많은 뉴스 하루
30. 모든 lane retrieval이 0건인 완전 신규 사건
```

## 18. 완료 기준 제안

구현 완료는 단순히 prompt가 생성되는 것으로 판단하지 않는다.

```text
모든 cutoff-safe record가 coverage manifest에 정확히 1회 이상 설명됨
unindexed available record = 0
population statistics가 원본 record 재계산과 일치
issuer-day 중복 집계 오류 = 0
positive lane에 negative control 혼입 = 0
newsless 전용 population과 representative가 보존됨
각 representative가 population member와 provenance로 연결됨
모든 summary claim이 cell/record까지 추적 가능
final prompt가 설정 token budget 이내
60만 record에서도 목표 latency와 peak memory 충족
historical replay에서 기존 top-3 방식보다 recall/calibration 개선
lookahead/provenance/coverage/brain audit 통과
ruff/mypy/pytest 통과
```

## 19. 외부 검토자에게 답을 요청할 핵심 질문

다음 질문에는 가능하면 구체적인 대안과 실패 사례를 함께 답해 주기 바란다.

1. 전수 coverage와 LLM reasoning payload를 분리하는 것이 올바른가?
2. 관련 모집단은 ANN top-K, similarity threshold, memory cell 중 무엇으로 정의해야 하는가?
3. lane별 recall pool을 적응적으로 늘리는 종료 조건은 무엇이 적절한가?
4. 대표 사례 다양성 축에서 빠진 중요한 축이 있는가?
5. 가격 반응 통계의 기본 독립 단위는 issuer-day, event-issuer-day 중 무엇이어야 하는가?
6. theme formation의 독립 단위와 성공·실패 정의는 무엇이 적절한가?
7. 오래된 사례에 time decay를 주는 것이 맞는가, 기간별 분포를 병렬 제공하는 것이 맞는가?
8. market regime 유사도를 어떤 cutoff-safe feature로 계산해야 하는가?
9. LLM summary가 전체 분포를 왜곡하지 않았음을 어떻게 자동 검증할 수 있는가?
10. semantic memory cell의 membership은 단일 소속과 다중 소속 중 무엇이 안전한가?
11. multi-membership record의 통계 weight를 어떻게 계산해야 하는가?
12. 뉴스 없는 급등 사례는 어떤 판단 단계에서 가장 강하게 사용해야 하는가?
13. beneficiary discovery는 direct semantic similarity와 별도의 graph retrieval이 필요한가?
14. representative context 8~32개가 적절한지, 더 나은 token allocation 방식은 무엇인가?
15. 불확실도 기반 drill-down이 종료되지 않는 상황을 어떻게 막아야 하는가?
16. 60만 개 전체 record를 매일 메타데이터 scan하는 것과 precomputed cube 중 무엇이 현실적인가?
17. offline cell/category brain rebuild 주기는 import 시, 일간, 주간 중 무엇이 적절한가?
18. current final synthesis에서 `record_level_shard_contributions` 전량 전달을 제거할 때 놓칠 정보가 있는가?
19. shadow evaluation에서 반드시 비교해야 할 지표와 기준 날짜는 무엇인가?
20. 이 설계에 숨어 있는 lookahead, survivorship, selection bias는 무엇인가?

## 20. 현재 제안 결론

현재 24개는 너무 적거나 너무 많다고 단독으로 판정할 숫자가 아니다. 문제는 24개 뒤에
전체 관련 모집단 통계와 다양성 보장이 없다는 점이다.

권장 방향은 다음과 같다.

```text
모든 record 보존·coverage
→ 넓은 관련 모집단 recall
→ deterministic 통계·중복 제거
→ 다양성 있는 대표 성공/실패/반례 선택
→ category brain과 LLM 비교
→ 불확실 부분만 adaptive drill-down
```

따라서 다음 구현은 top 3을 단순히 큰 숫자로 바꾸는 작업이 아니라, exhaustive audit와
reasoning context를 분리하고 `population + representative evidence` 계약을 만드는 작업이어야
한다.

## 21. 외부 검토 답변 형식

검토자는 아래 형식으로 답하면 설계 반영 여부를 비교하기 쉽다.

```text
총괄 판정:
  ACCEPT / ACCEPT_WITH_CHANGES / REJECT

치명적 문제:
  - 문제
  - 왜 실제 판단을 망가뜨리는지
  - 권장 수정

통계·retrieval 문제:
  - 모집단 정의
  - weighting
  - recall/context K
  - diversity

LLM·압축 문제:
  - 누락 위험
  - 보상 해킹 위험
  - provenance 검증

BLIND·시간 문제:
  - lookahead 가능 경로
  - 필요한 gate

성능 문제:
  - 예상 latency/memory/cost
  - 권장 index/aggregation 구조

반드시 먼저 구현할 3개:
  1.
  2.
  3.

구현 전에 답해야 할 미결 질문:
  -
```
