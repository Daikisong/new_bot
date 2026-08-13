# NSLAB 장기기억 두뇌 설계 외부 피드백

상태: 수용 검토 완료

원 피드백 판정:

```text
ACCEPT_WITH_CHANGES
```

이 문서는 `docs/0813/brain_memory_usage_design_review.md`에 대해 받은 외부 피드백을
누락 없이 보존하고, 각 지적을 현재 코드에서 다시 확인한 결과를 기록한다. 구현 계획은
`docs/0813/brain_memory_implementation_phases.md`에서 관리한다.

## 1. 총괄 피드백

연구 원료는 방향이 맞고 두뇌 재료로 사용할 수준이다. 현재 병목은 연구를 다시 만드는
것이 아니라, repaired record 전체를 관련 모집단으로 집계하고 실제 일일 LLM 판단에
전달하는 계층이다.

외부 검토의 영역별 판정:

```text
Gold 연구 bundle·repair·record store       좋음
record-level lane 라우팅                   방향 맞음
open-world first 분석                      좋음
BLIND·available_from·provenance             좋음

현재 top-3/top-5 retrieval                 부족
현재 exhaustive record sweep              60만 개에서 비현실적
현재 final synthesis payload               60만 개에서 불가능
전체 관련 모집단 통계                      아직 없음
semantic memory cell                       아직 없음
adaptive drill-down                        아직 없음
production provider/embedding              아직 mock/catalog
```

현재 Phase A 기준 계획 corpus:

```text
READY_FOR_IMPORT bundle                    1,127개
READY_FOR_IMPORT brain record            606,737개
training_eligible record                 384,846개
```

이 수치는 repaired 결과이며 production import 완료 수치가 아니다. 예시로 이미 다뤘던
2024-11-11 연구는 458개 record 중 295개가 training eligible이다.

## 2. 10년치 연구를 모두 쓴다는 의미

외부 검토자는 기존 설계 문서의 A/B/C 구분에 동의했다.

```text
A. 모든 record 보존
B. 모든 cutoff-safe record 누락 감사
C. 모든 record 원문을 매일 prompt에 삽입
```

A와 B는 필수다. C는 불가능하고 필요하지 않다.

채택할 정의:

```text
모든 record 원문을 매일 LLM에 넣는다
≠ 10년치 두뇌를 모두 사용

모든 record가 저장·색인·시간검증·누락감사 대상이 됨
+ 현재 사건과 관련된 전체 모집단의 통계에 기여함
+ 성공·실패·반례를 대표하는 원문 사례를 LLM에 보여줌
+ category brain이 과거 전체에서 압축한 조건부 지식을 제공함
= 10년치 두뇌를 충분히 사용
```

장문맥에 전체 자료를 넣는 것은 relevant evidence가 문맥 중간에 있을 때 성능을 떨어뜨릴
수 있다. 계층적 압축과 선택된 근거 전달이 올바른 방향이라는 피드백이다.

참고:

```text
Lost in the Middle: https://arxiv.org/abs/2307.03172
```

## 3. 코드에서 확인된 치명적 문제

### 3.1 P0: production 분석도 현재 뉴스 앞 12개만 사용

현재 코드:

```python
blind_news_items = batch.items[: self.settings.limits.max_news_items_for_mock]
```

기본값:

```text
max_news_items_for_mock = 12
```

이 제한은 현재 provider 종류와 무관하게 적용된다. 따라서 real provider를 붙여도
open-world first analysis, novelty review, event clustering, retrieval query 생성의 입력이
앞 12개로 제한될 수 있다.

코드 확인:

```text
src/news_scalping_lab/inference/analyzer.py:230
src/news_scalping_lab/config.py:29
configs/default.yaml:27
```

필수 수정:

```text
mock provider
→ fixture/테스트용 제한 허용

real provider
→ 전체 CSV row coverage
→ semantic event clustering을 batch 처리
→ 모든 material cluster가 open-world analysis에 정확히 한 번 반영
```

단순히 제한을 2,000으로 올리는 방식은 채택하지 않는다. 전체 row coverage와 batch/event
cluster 계약이 필요하다.

### 3.2 P0: 현재 event clustering은 exact duplicate 수준

현재 cluster method:

```text
exact_normalized_title_body_v1
semantic_duplicate_cluster_count = 0
```

같은 사건을 언론사마다 다르게 쓴 기사는 서로 다른 cluster로 남을 수 있다. 그 상태에서
모든 cluster마다 8개 lane retrieval을 수행하면 같은 사건을 반복 검색한다.

코드 확인:

```text
src/news_scalping_lab/inference/analyzer.py:1003
src/news_scalping_lab/inference/analyzer.py:1053
```

필수 구조:

```text
전체 뉴스 row
→ exact duplicate 제거
→ semantic event clustering
→ issuer·predicate·counterparty·숫자·시점 기반 cluster merge
→ MATERIAL / MARKET_CONTEXT / AUDIT_ONLY / DUPLICATE 분류
→ MATERIAL cluster에 full memory retrieval
```

모든 row는 disposition과 provenance를 유지한다. 모든 row 또는 cluster가 8-lane full
retrieval을 받아야 한다는 뜻은 아니다.

### 3.3 P0: 현재 record 검색은 query마다 Python 선형 전수 검색

`LocalRetrievalStore.search_records()`는 JSONL index record 전체를 Python에서 필터링하고,
각 record의 term overlap과 cosine similarity를 계산한 뒤 전체 정렬한다.

코드 확인:

```text
src/news_scalping_lab/retrieval/store.py:171
src/news_scalping_lab/retrieval/store.py:238
src/news_scalping_lab/retrieval/store.py:245
```

100개 cluster를 가정하면:

```text
100 clusters x 8 lanes x 600,000 records
= 약 4억 8천만 score 비교
```

현재 deterministic hash embedding은 local/test artifact에 적합할 뿐 production semantic
index가 아니다.

권장 retrieval stack:

```text
1차 metadata filter
2차 HNSW ANN broad recall
3차 BM25/FTS union
4차 상위 수백 건 reranker
5차 memory cell·population lookup
```

참고:

```text
HNSW: https://arxiv.org/abs/1603.09320
ColBERTv2: https://arxiv.org/abs/2112.01488
```

### 3.4 P0: exhaustive coverage와 reasoning payload가 혼합됨

현재 `MemorySweeper`는 모든 available record를 기본 20개 단위로 나누고, 현재 뉴스 hash와
날짜를 포함한 cache key로 shard contribution을 만든다.

606,737 record 기준:

```text
606,737 / 20 = 약 30,337 record shards
```

현재 final synthesis required input과 payload에는 다음이 포함된다.

```text
all_shard_contributions
record_level_shard_contributions
```

코드 확인:

```text
src/news_scalping_lab/context/sweep.py:373
src/news_scalping_lab/inference/analyzer.py:3157
src/news_scalping_lab/inference/analyzer.py:3212
src/news_scalping_lab/context/final_synthesis.py:21
```

필수 분리:

```text
Coverage path
→ 모든 record ID·hash·index·available_from 전수 확인
→ coverage manifest만 final에 전달

Reasoning path
→ 현재 사건의 관련 population 통계
→ diverse representatives
→ category brain
→ 이 세 종류만 final reasoning payload에 전달
```

전수 sweep 성공은 manifest로 증명하고, 전수 shard 본문은 final prompt에서 제거해야 한다.

### 3.5 P0: `training_eligible`과 outcome polarity가 비대칭 혼합됨

현재 라우팅은 outcome이 positive여도 `training_eligible=false`이면 polarity를
`NEAR_MISS`로 바꾼다.

코드 확인:

```text
src/news_scalping_lab/records/routing.py:148
```

이는 근거 품질과 가격 반응 방향을 혼합한다. 아래 네 축을 독립시켜야 한다.

```text
evidence_polarity
  POSITIVE / NEGATIVE / NEAR_MISS / UNEXPLAINED / CONTEXT / UNKNOWN

training_eligible
  true / false

label_quality
  verified / quarantined / missing / ambiguous

routing_disposition
  REASONING / CONTEXT / AUDIT / QUARANTINED
```

예시:

```text
+29% 상승, provenance 불완전
→ polarity = POSITIVE
→ training_eligible = false
→ routing_disposition = AUDIT 또는 QUARANTINED
```

이를 `NEAR_MISS`로 바꾸면 안 된다.

현재 high return +5% 등의 fallback threshold는 retrieval 편의용 versioned classifier로만
사용할 수 있다. 시장 법칙이나 최종 매매 gate로 사용하면 안 된다.

### 3.6 P1: population layer와 memory cell이 아직 없음

현재 구현에는 다음 설계 계층이 없다.

```text
memory/population.py
memory/cells.py
memory/diversity.py
memory/adaptive_retrieval.py
contracts/memory_context.py
```

Brain compiler는 category routing, catalog/llm-full 분리, real provider guard를 갖고 있지만
현재 raw record 50개 단위 LLM shard compile 방식이다.

606,737 record 기준 단순 추산:

```text
606,737 / 50 = 약 12,135 LLM record shards
```

코드 확인:

```text
src/news_scalping_lab/brain/compiler.py:103
src/news_scalping_lab/brain/compiler.py:1847
```

raw record 전체 rebuild는 비용이 매우 크므로 memory cell·population distribution 기반
incremental compiler가 필요하다.

참고:

```text
RAPTOR: https://arxiv.org/abs/2401.18059
GraphRAG: https://arxiv.org/abs/2404.16130
```

## 4. 기존 설계에서 그대로 채택할 내용

### 4.1 전수 coverage와 reasoning context 분리

```text
전수 record
→ 보존·색인·누락 감사

관련 population
→ 통계 계산

대표 evidence
→ LLM 비교 판단
```

모든 record가 population 집계 또는 명시적 routing disposition에 기여하면 전체 두뇌를
사용한 것이다.

### 4.2 결정론적 통계와 LLM 역할 분리

결정론적 코드가 담당할 영역:

```text
membership
deduplication
count / rate / percentile
missingness
representative selection constraints
provenance
coverage
```

LLM이 담당할 영역:

```text
현재와 과거의 구조적 공통점·차이
성공 조건과 실패 경계
충돌 사례의 의미
추가 검증 질문
최종 후보 논리와 반론
```

### 4.3 adaptive drill-down

다음 조건에서 부족한 lane 또는 subgroup만 추가 검색한다.

```text
positive와 negative 비중이 비슷함
effective sample size가 작음
시장 regime별 결과가 반대
newsless 비중이 높음
beneficiary 관계가 2단계 이상 추론
대장 후보 비교 근거가 상충
```

전체 retrieval을 무한 확장하지 않는다.

참고:

```text
Adaptive-RAG: https://arxiv.org/abs/2403.14403
```

## 5. 기존 설계에 반드시 반영할 수정

### 5.1 ANN top-K를 통계 모집단으로 사용하지 않음

ANN top-K는 recall 특성에 따라 positive/negative 비율을 왜곡할 수 있다.

채택 구조:

```text
ANN = 어떤 memory cell을 볼지 선택
cell의 cutoff-safe 전체 membership = 통계 분모
representative records = 설명용 표본
```

따라서 `positive 312 / total 1,284`는 그대로 예측 확률이라고 부르지 않는다.

```text
관련 관측 모집단 내 반응률
```

이라고 표현한다. 실제 확률로 쓰려면 walk-forward calibration이 필요하다.

### 5.2 memory cell은 primary와 secondary membership을 병행

```text
primary_cell_id
→ 통계 집계와 누락 감사용, 정확히 하나

secondary_cell_ids
→ retrieval query 확장용, 0개 이상
```

여러 cell을 합칠 때는 `independent_unit_id` 기준으로 다시 dedup한다.

### 5.3 독립 표본 단위는 목적별로 분리

```text
직접 사건 반응           event-issuer-day
종목 하루 총반응         issuer-day
테마 형성                theme-day
테마 구성 종목 반응      theme-day 안의 ticker-day
대장 선택                theme-day pair
뉴스 없는 강세           ticker-day
```

같은 issuer-day에 event가 4개면 가격 반응 투표권 합계는 1이어야 한다. event 분석에서는
각 event에 fractional weight를 적용한다.

유효 표본 수 권장식:

```text
effective_sample_size = (sum(w) ** 2) / sum(w ** 2)
```

confidence interval은 거래일 상관을 고려해 단순 binomial보다 거래일 단위 block bootstrap을
우선 검토한다.

### 5.4 일괄 time decay보다 기간별 병렬 분포 우선

초기에는 다음 분포를 함께 제공한다.

```text
최근 1년
최근 3년
과거 3~10년
유사 market regime
전체 기간
```

exponential time decay는 walk-forward 평가로 효과가 검증된 뒤 적용한다.

### 5.5 historical replay에는 as-of cell/index/brain snapshot 필수

최신 corpus로 만든 cell membership을 과거 replay에 사용하면 구조 자체에 미래 정보가
들어갈 수 있다.

```text
live 현재 분석
→ 현재까지 전체 corpus의 최신 cell/index/brain

historical replay
→ replay cutoff까지 available했던 record로 만든 as-of cell/index/brain
```

최소 version key:

```text
corpus_manifest_hash
max_available_from
embedding_model
clustering_version
normalizer_version
```

## 6. 권장 최종 두뇌 구조

### 6.1 오프라인 import/build 경로

```text
Gold/repaired bundle
→ BrainRecordEnvelope
→ immutable record store
→ structural feature normalization
→ metadata/FTS index
→ HNSW semantic index
→ provenance graph
→ semantic memory cells
→ population statistics cube
→ diverse representative registry
→ category brain llm-full compile
```

### 6.2 structural feature는 offline normalizer가 파생

연구 prompt에 새 필드를 계속 추가하지 않는다. 현재 record payload에서 offline normalizer가
다음과 같은 feature를 파생한다.

```text
event family
modality: confirmed / announced / planned / negotiating
novelty
directness
counterparty presence
quantified value presence
contract/revenue ratio
buyback/market-cap ratio
shares cancelled ratio
dilution ratio
market cap/liquidity bucket
D-1 pre-absorption
path type
market regime vector
outcome polarity
```

없는 값은 `UNKNOWN`으로 보존하고 missingness 통계에 포함한다.

### 6.3 population cube

권장 차원:

```text
cell_id
x memory_lane
x time_slice
x regime_cluster
x record_type
x path_type
x label_quality
```

각 cube가 보존할 값:

```text
unique event-issuer-day
unique issuer-day
positive / negative / near-miss / unexplained
upper-limit touched
high return percentiles
close return percentiles
missing outcomes
effective sample size
member ID manifest hash
```

새 연구 import 시 전체 재계산이 아니라 영향받은 cell만 incremental update한다.

### 6.4 온라인 아침 뉴스 경로

```text
1. 전체 CSV 파싱
2. semantic event clustering
3. open-world first analysis
4. materiality routing
5. category brain으로 query expansion
6. relevant memory cell broad recall
7. cell 전체 member 기반 population 통계 조회
8. current regime subgroup 비교
9. diverse representative record 선택
10. LLM 비교 판단
11. 불확실 영역만 adaptive drill-down
12. candidate verification·red team
13. final synthesis
```

materiality disposition:

```text
MATERIAL_FULL_RETRIEVAL
MARKET_CONTEXT
AUDIT_ONLY
DUPLICATE
```

모든 뉴스 row는 disposition을 갖는다. full retrieval 대상만 제한한다.

## 7. 현재 사건을 판단하는 예시

예시 뉴스:

```text
A사, 시총의 6% 규모 자사주 소각 확정
```

잘못된 방식:

```text
자사주 소각 검색 → 과거 3개 출력 → LLM 판단
```

권장 방식:

```text
전체 관련 event-issuer-day 모집단
→ 확정/계획, 시총 대비 비율, 선반영, liquidity, regime subgroup
→ deterministic 반응 분포
→ 성공·실패·경계 대표 사례
→ 현재 사건과 과거 조건 차이 LLM 비교
```

예시 population context:

```text
자사주 소각 관련 event-issuer-day: 486건
확정 소각: 318건
취득/신탁/계획: 168건

확정 소각 중:
고가 +10% 이상: 74건
고가 +5~10%: 92건
약반응/음성: 152건
```

현재와 가까운 subgroup:

```text
시총 대비 3~10%
소형·중소형주
발표 전 20일 급등 미약
유통주식 감소 명확
주주환원 친화적 regime

event-issuer-day 58건
강한 반응 25건
약반응 33건
```

LLM은 통계를 다시 계산하지 않고 현재 사건이 어떤 조건과 실패 경계에 가까운지 비교한다.

최종 provenance:

```text
population_manifest_hash
selected_cell_ids
supporting_record_ids
contradicting_record_ids
current-vs-history differences
unresolved disagreements
confidence
```

## 8. beneficiary discovery는 graph retrieval 병행

semantic similarity만으로 후발 수혜주 관계를 모두 찾기 어렵다.

```text
현재 사건
→ 작동 메커니즘
→ 경제적 수혜층
→ 사업 역할
→ 상장사
```

graph edge:

```text
event → mechanism
mechanism → benefit layer
company → business role
company → customer/supply chain/region
theme → prior market-memory ticker
```

모든 edge는 연구 record 또는 cutoff-safe 출처 provenance를 가져야 한다. graph는 whitelist가
아니다.

```text
graph expansion
→ 신규 후보 제안
→ 현재 뉴스·공시·회사 자료 재검증
→ 최종 후보
```

## 9. 대표 사례 선택

단순 similarity 상위가 아니라 다음 strata를 보존한다.

```text
전형적 성공 medoid
전형적 실패 medoid
성공/실패 경계 사례
강한 뉴스인데 실패한 사례
약한 뉴스인데 성공한 사례
최근 사례
오래된 동일 메커니즘 사례
현재와 같은 regime
현재와 다른 regime
newsless/unexplained
직접주/간접주/시장기억주
고품질/낮은 품질
```

권장 조합:

```text
stratified quotas
+ MMR
+ facility-location/submodular selection
```

대표 record 기본 8~16, 최대 32는 초기 실험값이다. 다음에 따라 적응시킨다.

```text
population entropy
positive/negative 충돌
regime 분산
representative coverage
token budget
```

## 10. 성능·cache 피드백

온라인 full corpus scan은 0회가 목표다.

```text
오프라인
→ embedding/cell/cube 계산

온라인 pre-LLM
→ ANN cell recall
→ metadata filter
→ population cube lookup
→ representative selection

LLM
→ 현재 사건별 압축 context
```

초기 engineering target:

```text
600k record pre-LLM retrieval/aggregation P95: 3~5초 이내
전체 일일 분석 P95: real provider 포함 60~90초 이내
온라인 full corpus scan: 0회
일반 final prompt: 20k~50k tokens
복잡한 날 hard max: 60k~80k tokens
```

이는 보장값이 아니라 실제 corpus benchmark로 검증할 목표다.

cache 계층:

```text
corpus cell/cube cache
→ import가 없으면 재사용

query-to-cell cache
→ current event signature

representative selection cache
→ population hash + selection version

LLM comparison cache
→ current event + population manifest hash
```

corpus shard cache에 현재 뉴스 hash를 넣어 매일 전량 무효화하지 않는다.

## 11. BLIND와 market regime

market regime에는 D-1 이하 정보만 사용한다.

권장 continuous regime vector:

```text
KOSPI/KOSDAQ 1·5·20일 수익률
실현 변동성
상승/하락 breadth
상한가/급락 종목 수
거래대금 집중도
소형주 대 대형주 상대강도
섹터 집중도
외국인·기관 D-1 흐름
원달러·금리·유가·미국지수 이전 마감
```

단일 `RISK_ON / RISK_OFF` 하드코딩 label보다 vector와 유사 regime subgroup을 사용한다.

과거 outcome은 현재 분석에서 사용할 수 있다. historical replay에서는 replay cutoff 당시
available하지 않았던 record, cell, brain을 사용하면 안 된다.

## 12. category brain 역할

category brain은 최종 증거를 대신하는 압축본이 아니다.

역할:

```text
어떤 메커니즘을 조사할지
어떤 반례 lane을 반드시 볼지
어떤 subgroup이 중요한지
어떤 beneficiary graph path를 확장할지
```

최종 판단 입력:

```text
category brain
+ current population statistics
+ representative raw records
+ current D-1 context
+ current cutoff-safe evidence
```

## 13. production 상태에 대한 판정

외부 피드백 당시 진단은 대략 다음 상태였다.

```text
accepted episode: 2
accepted record: 968
build mode: catalog
catalog_only: true
```

production readiness blocker:

```text
catalog-only brain
deterministic mock embedding
mock LLM
mock price
mock web
```

따라서 현재 상태의 정확한 표현:

```text
10년치 원료를 보존·라우팅할 기반은 준비됨
10년치 원료를 빠르고 충분하게 판단에 쓰는 retrieval/aggregation layer는 설계 단계
```

## 14. 반드시 먼저 구현할 세 묶음

### 14.1 전수 감사와 online reasoning 물리적 분리

수정 대상:

```text
context/sweep.py
context/final_synthesis.py
inference/analyzer.py
```

필수 작업:

```text
production 뉴스 12개 cap 제거
semantic/material event clustering
exhaustive record shard 본문을 final payload에서 제거
coverage manifest만 final에 전달
record_level_shard_contributions required input 제거
```

### 14.2 PopulationRetriever

신규 후보:

```text
memory/population.py
contracts/memory_context.py
```

필수 기능:

```text
ANN + FTS + metadata broad recall
issuer-day/event-day/theme-day dedup
polarity와 eligibility 독립
전체 관련 population 통계
time/regime subgroup
missingness/effective sample size
population manifest/hash
```

ANN top-K를 통계 분모로 쓰지 않고 선택된 cell 전체 membership을 분모로 사용한다.

### 14.3 Memory cell + diverse representatives + adaptive drill-down

신규 후보:

```text
memory/cells.py
memory/diversity.py
memory/adaptive_retrieval.py
```

필수 기능:

```text
primary/secondary cell membership
incremental cell update
precomputed population cube
polarity/regime/time diversity representatives
uncertainty trigger
lane-specific drill-down
category brain integration
```

## 15. shadow 평가 피드백

같은 미사용 날짜를 다음 비교군으로 replay한다.

```text
A. memory 없음
B. 현재 top-3 retrieval
C. population statistics만
D. representatives만
E. cells + population + representatives
F. E + adaptive drill-down
```

판단 지표:

```text
후보 Recall@5/10/20
상한가·고가 +10/+20 recall
false-positive rate
leader selection error
theme over-expansion
newsless cause hallucination
confidence calibration / Brier score
```

retrieval 지표:

```text
known relevant record recall@pool
negative-control inclusion
counterexample inclusion
issuer-day duplicate rate
long-tail beneficiary recall
year/regime diversity
```

시스템 지표:

```text
P50/P95/P99 latency
LLM tokens
embedding query 수
cache hit rate
peak memory
하루 비용
```

부하 단계:

```text
50k records
200k records
600k records
```

## 16. 외부 피드백의 최종 결론

현재 코드 그대로라면 다음 위험이 실재한다.

```text
앞 12개 뉴스만 사용
exact cluster
선형 60만 record scan
fixed top-3/top-5
전체 약 3만 shard를 final payload에 전달
전체 모집단 통계 부재
```

하지만 기존 설계의 핵심 방향은 맞다.

```text
전수 보존·coverage
→ 관련 cell broad recall
→ 전체 모집단 통계
→ issuer-day 중복 제거
→ 성공·실패·반례 대표 추출
→ category brain과 LLM 비교
→ 불확실 부분만 drill-down
```

연구 원료 형식을 더 무겁게 만들 필요는 없다. 현재 repaired `brain_delta`에서 offline
normalizer가 사건 구조, 정량 강도, regime feature를 파생한다.

현재 필요한 것은 연구 prompt 필드 추가가 아니라 다음 계층이다.

```text
population
→ memory cell
→ representative selection
→ adaptive retrieval
```

## 17. 추적성 표

| 피드백 항목 | 코드 확인 | 수용 상태 | 구현 phase |
|---|---|---|---|
| 뉴스 12개 제한 | 확인 | 수용 | Phase 1 |
| exact-only clustering | 확인 | 수용 | Phase 1 |
| 선형 record 검색 | 확인 | 수용 | Phase 4 |
| exhaustive/final payload 혼합 | 확인 | 수용 | Phase 2 |
| eligibility/polarity 혼합 | 확인 | 수용 | Phase 3 |
| population layer 없음 | 확인 | 수용 | Phase 5 |
| memory cell 없음 | 확인 | 수용 | Phase 4 |
| ANN top-K 모집단 금지 | 설계 반영 | 수용 | Phase 5 |
| primary/secondary cell | 설계 반영 | 수용 | Phase 4 |
| 목적별 독립 표본 단위 | 설계 반영 | 수용 | Phase 5 |
| 일괄 time decay 보류 | 설계 반영 | 수용 | Phase 5/8 |
| as-of cell snapshot | 설계 반영 | 수용 | Phase 4/8 |
| beneficiary graph 병행 | 설계 반영 | 수용 | Phase 7 |
| diverse representative selection | 설계 반영 | 수용 | Phase 6 |
| adaptive drill-down | 설계 반영 | 수용 | Phase 6 |
| population cube incremental update | 설계 반영 | 수용 | Phase 5 |
| category brain 역할 제한 | 설계 반영 | 수용 | Phase 7 |
| shadow evaluation | 설계 반영 | 수용 | Phase 8 |
| production real provider | 기존 blocker 확인 | 수용 | Phase 9 |

피드백의 모든 주요 주장, 대안, 예시, 성능 목표, 평가 항목과 참고 문헌을 위에 보존했다.
