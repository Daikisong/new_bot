# NSLAB 장기기억 두뇌 구현 단계

상태: Phase 0~4 구현·검증·독립감사 승인 완료, Phase 5 착수 전

기준 문서:

```text
docs/0813/brain_memory_usage_design_review.md
docs/0813/external_brain_design_feedback.md
```

목표:

```text
약 60만 record 전수의 분포와 반례를 활용하면서도
아침 뉴스 하루를 production latency와 token budget 안에서 분석하는
population-cell-representative-adaptive retrieval 두뇌를 구현한다.
```

## 1. 전체 phase map

```text
Phase 0  기준선·계약·corpus profile
Phase 1  현재 뉴스 전수 coverage와 semantic event clustering
Phase 2  전수 coverage path와 online reasoning path 분리
Phase 3  polarity·eligibility·quality·disposition 4축 분리
Phase 4  production retrieval index와 as-of memory cell
Phase 5  PopulationRetriever와 통계 cube
Phase 6  diverse representatives와 adaptive drill-down
Phase 7  category brain·beneficiary graph·final synthesis 통합
Phase 8  shadow replay·부하·편향 평가
Phase 9  1,127 bundle import와 production provider 승격
```

Phase 1~3은 현재 작은 record store에서도 구현·검증할 수 있다. Phase 4~8은 repaired corpus를
직접 production import하지 않고 별도 fixture/index root에서 50k, 200k, 600k 단계로 시험한다.
Phase 9 전까지 실제 production brain 승격은 하지 않는다.

## 2. 공통 불변 계약

모든 phase에 적용한다.

```text
종목·ticker·theme·region·beneficiary 하드코딩 금지
open-world first pass가 memory보다 먼저 실행
exact keyword는 보조 evidence이며 candidate gate가 아님
새 연구 import에 source code 변경이 필요하지 않음
brain_delta·unknown record silent drop 금지
현재 D-day price와 cutoff 이후 source의 BLIND 접근 금지
모든 집계·대표 사례·LLM claim에 provenance/context manifest 필수
training_eligible와 outcome polarity 분리
coverage 성공과 reasoning payload 크기 분리
```

## 3. Phase 0: 기준선·계약·corpus profile

### 목적

현재 동작을 수치화하고 새 구조의 strict contract를 먼저 고정한다.

### 구현

신규 strict schema 후보:

```text
src/news_scalping_lab/contracts/memory_context.py

RecordRoutingMetadata
NewsCoverageManifest
EventClusterManifest
MemoryCoverageManifest
MemoryCellManifest
PopulationManifest
RepresentativeSetManifest
AdaptiveRetrievalTrace
DailyMemoryContext
```

corpus profiler 후보:

```text
src/news_scalping_lab/tools/profile_brain_records.py
```

측정 항목:

```text
record/lane/polarity/type 수
training eligibility와 polarity 교차표
outcome field coverage
issuer-day/event-day/theme-day dedup ratio
payload size 평균/P95/P99/max
기간·market regime 분포
현재 linear retrieval latency
현재 sweep artifact 수·bytes·tokens
```

### 테스트

```text
fixture profile deterministic
같은 corpus의 manifest hash 동일
unknown type 보존
outcome missing을 negative로 세지 않음
record count와 profile count parity
```

### 종료 조건

```text
현재 968 imported records profile 생성
repaired corpus 표본 profile 생성
새 strict schema와 version key 확정
성능 baseline report 생성
```

## 4. Phase 1: 현재 뉴스 전수 coverage와 semantic clustering

### 목적

앞 12개만 보는 P0를 제거하고 하루 CSV의 모든 row를 사건 이해에 반영한다.

### 구현

수정 대상:

```text
src/news_scalping_lab/inference/analyzer.py
src/news_scalping_lab/config.py
src/news_scalping_lab/contracts/models.py
```

신규 후보:

```text
src/news_scalping_lab/inference/event_clustering.py
```

flow:

```text
전체 CSV strict parse
→ row ledger와 coverage count
→ exact duplicate collapse
→ semantic embedding batch
→ issuer/predicate/counterparty/numeric/time structural merge
→ cluster representative와 모든 member row 보존
→ MATERIAL_FULL_RETRIEVAL / MARKET_CONTEXT / AUDIT_ONLY / DUPLICATE
```

mock provider의 12개 제한은 fixture용으로만 유지하고, production path에서는 전체 cluster를
bounded batch로 처리한다.

### 핵심 gate

```text
input row count = disposition row count
모든 row가 정확히 하나의 primary cluster 또는 duplicate parent를 가짐
material cluster가 open-world pass에 정확히 한 번 포함
동일 row가 두 material cluster에 중복 집계되지 않음
semantic clustering이 ticker whitelist를 사용하지 않음
```

### 엣지 케이스

```text
동일 기사 제목만 다름
본문만 약간 다름
같은 회사의 다른 사건
한 기사에 두 회사·두 predicate
숫자 단위가 다른 동일 계약
속보/정정/종합 기사
1000개 이상 뉴스 row
semantic embedding 실패
모든 뉴스가 context/audit인 날
완전 신규 사건
```

### 종료 조건

```text
real-provider 분석에서 앞 12개 cap 없음
row coverage 100%
semantic duplicate fixture 통과
현재 open-world ordering 유지
기존 daily analysis E2E 통과
```

## 5. Phase 2: coverage path와 reasoning path 분리

### 목적

60만 record의 전수 감사는 유지하면서 전수 shard 본문을 final LLM prompt에서 제거한다.

### 구현

수정 대상:

```text
src/news_scalping_lab/context/sweep.py
src/news_scalping_lab/context/final_synthesis.py
src/news_scalping_lab/inference/analyzer.py
src/news_scalping_lab/audits/provenance.py
src/news_scalping_lab/cli.py
```

분리 후:

```text
Coverage path
→ accepted/available/indexed IDs·hash root
→ missing/duplicate/future/provenance counts
→ immutable MemoryCoverageManifest

Reasoning path
→ population context
→ representative context
→ category brain
```

제거/대체:

```text
final required input의 all_shard_contributions 본문 제거
final required input의 record_level_shard_contributions 본문 제거
→ memory_coverage_manifest ref/hash/count로 대체
```

기존 legacy context contract는 읽을 수 있도록 migration compatibility를 유지하되 새 production
prompt는 v2 계약만 사용한다.

### 핵심 gate

```text
available record coverage 100%
missing/duplicate/unexpected 0
final payload에 전수 record payload 없음
coverage manifest hash와 record store hash 결속
lookahead/provenance audit 유지
token budget hard gate
```

### crash/성능 테스트

```text
coverage manifest 생성 중 중단·재개
record 50k/200k/600k manifest 생성
final payload size가 corpus 크기에 선형 증가하지 않음
import 없는 날 coverage cache 재사용
```

### 종료 조건

```text
60만 record 예상 final payload가 설정 token budget 이내
전수 coverage audit는 동일하거나 더 강함
record shard body 제거 후 provenance audit 통과
```

## 6. Phase 3: 네 축 라우팅 분리

### 목적

가격 반응, 근거 품질, 학습 적격, reasoning 사용처를 독립시킨다.

### 모델

```text
evidence_polarity
training_eligible
label_quality
routing_disposition
```

수정 대상:

```text
src/news_scalping_lab/records/routing.py
src/news_scalping_lab/retrieval/store.py
src/news_scalping_lab/context/sweep.py
src/news_scalping_lab/brain/compiler.py
src/news_scalping_lab/training.py
```

변경 예:

```text
positive outcome + ineligible
현재: NEAR_MISS
변경: polarity=POSITIVE, eligible=false, disposition=AUDIT/QUARANTINED
```

fallback threshold는 다음 metadata와 함께 versioning한다.

```text
polarity_classifier_version
threshold_source
threshold_role = retrieval_calibration_only
```

### 테스트

```text
eligible positive
ineligible positive
eligible negative control
ineligible negative
near miss
newsless strong outcome
context/audit/unknown
missing numeric outcome
label과 numeric outcome 충돌
```

### 종료 조건

```text
eligibility 변경이 polarity를 변경하지 않음
positive reasoning support는 eligible+verified+REASONING만 가능
negative calibration과 newsless memory는 보존
training export와 runtime routing 교차표 통과
```

## 7. Phase 4: production retrieval index와 as-of memory cell

### 목적

온라인 full corpus scan을 제거하고 ANN이 통계 분모가 아닌 cell 선택기로 작동하게 한다.

### 구현

신규 후보:

```text
src/news_scalping_lab/memory/cells.py
src/news_scalping_lab/memory/index.py
src/news_scalping_lab/contracts/memory_context.py
```

index stack:

```text
metadata index
FTS/BM25 index
real embedding HNSW ANN index
provenance/beneficiary graph index
```

cell membership:

```text
primary_cell_id exactly 1
secondary_cell_ids 0..N
independent_unit_id
membership_score
membership_rule/version
```

as-of version:

```text
corpus_manifest_hash
max_available_from
embedding_model
clustering_version
normalizer_version
cell_schema_version
```

### 중요 원칙

```text
ANN top-K = cell 후보
cell cutoff-safe 전체 member = population 후보
```

### 테스트

```text
ANN 결과가 한 polarity에 몰림
primary membership 누락/중복
secondary multi-membership
cell merge/split
embedding provider change
stale index
future member가 포함된 cell as-of filter
historical replay snapshot
incremental import update
```

### 종료 조건

```text
online query에서 Python full corpus scan 0회
production-code streaming build/audit 600건 통과
10,001+ bounded audit 재현 artifact는 Phase 8에서 생성
50k/200k/600k 축소 SQL query microbenchmark 통과
600k 1536D production-shape peak RSS/API profile은 Phase 8/9에서 측정
모든 reasoning-eligible record primary cell coverage
context/audit record 명시 disposition coverage
as-of replay가 future membership을 사용하지 않음
```

### 구현 결과

상세 결과는 `phase4_production_memory_index_report.md`에 기록했다.

```text
DuckDB metadata/FTS/HNSW/provenance graph snapshot 구현
primary membership 전수 1:1 및 bounded secondary membership 구현
full-envelope/cutoff/model/version content-addressed snapshot 구현
동일 embedding model 증분 vector 재사용 구현
online query source JSONL 전수 scan 0회 검증
600 production-code streaming build/audit 통과
streaming-audit branch tamper 회귀 통과
50k/200k/600k 축소 SQL query microbenchmark 통과(Phase 4 성능 종료 증거 아님)
600k 1536D production-shape builder 실측은 Phase 8/9 blocker로 유지
```

Phase 4 독립감사는 `APPROVE`이며 P0/P1 잔여 이슈는 없다. 600k 1536D 실측과 검색 품질
평가는 문서대로 Phase 8/9의 별도 종료 조건으로 유지한다.

## 8. Phase 5: PopulationRetriever와 통계 cube

### 목적

관련 memory cell의 cutoff-safe 전체 member를 사용해 deterministic 관측 모집단을 만든다.

### 구현

신규 후보:

```text
src/news_scalping_lab/memory/population.py
src/news_scalping_lab/memory/statistics.py
```

독립 단위:

```text
event-issuer-day
issuer-day
theme-day
theme-day ticker-day
theme-day pair
ticker-day
```

population cube:

```text
cell x lane x time_slice x regime_cluster x record_type x path_type x label_quality
```

계산:

```text
polarity counts
upper-limit count
high/close return distributions
missing outcome
effective sample size
block-bootstrap interval
member manifest hash
```

초기 time slice:

```text
최근 1년
최근 3년
과거 3~10년
유사 regime
전체 기간
```

### 핵심 gate

```text
ANN result count를 분모로 사용하지 않음
record count와 independent unit count 구분
newsless를 news catalyst success 분모에 자동 포함하지 않음
outcome missing을 negative로 처리하지 않음
관련 관측 모집단 내 반응률을 예측 확률이라 부르지 않음
```

### 종료 조건

```text
원본 record 재계산과 cube count/hash parity
issuer-day overcount 0
incremental update와 full rebuild 결과 동일
walk-forward 이전에는 probability 표현 금지
```

## 9. Phase 6: diverse representatives와 adaptive drill-down

### 목적

모집단 분포를 왜곡하지 않는 대표 사례를 token budget 안에서 제공한다.

### 구현

신규 후보:

```text
src/news_scalping_lab/memory/diversity.py
src/news_scalping_lab/memory/adaptive_retrieval.py
```

selection:

```text
stratified quotas
+ MMR
+ facility-location/submodular selection
```

strata:

```text
positive/negative/near-miss/unexplained
typical/boundary/counterexample
recent/old
same/different regime
direct/theme/beneficiary
high/low label quality
```

초기 context budget:

```text
lane별 8~16
복잡한 lane 최대 32
전체는 token budget과 diversity coverage로 결정
```

drill-down trigger:

```text
polarity conflict
small effective sample size
regime disagreement
high newsless share
multi-hop beneficiary
leader pair disagreement
low representative coverage
```

종료 조건:

```text
충족해야 할 minimum coverage
최대 depth
최대 cell/record/token budget
새 정보 gain 최소치
```

### 종료 조건

```text
대표 사례가 population distribution을 정해진 오차 내 보존
single date/ticker concentration 제한
minority polarity 보존
drill-down 무한 반복 불가
대표 record 전부 population member/provenance 결속
```

## 10. Phase 7: category brain·beneficiary graph·final synthesis 통합

### 목적

category brain을 query planner로 사용하고 population statistics와 raw evidence를 최종 판단에
통합한다.

### category brain 입력

```text
memory cells
population distributions
representative successes/failures
unresolved contradictions
```

### beneficiary graph

```text
event → mechanism → benefit layer → business role → company
```

edge provenance와 cutoff-safe 재검증을 강제한다. graph result는 후보 제안이지 whitelist가
아니다.

### final payload v2

```text
current event clusters
coverage manifest ref
population manifests
memory cells
representative records
unresolved disagreements
category brain guidance
current D-1/regime context
candidate verification
red team
```

제외:

```text
전수 raw record shards
전수 contribution bodies
현재 D-day price/outcome
```

### 종료 조건

```text
모든 final claim이 population/cell/record까지 추적 가능
newsless 원인 환각 test 통과
beneficiary graph 신규 후보 open-world 검증 통과
final prompt token hard gate
lookahead/provenance/coverage audit 통과
```

## 11. Phase 8: shadow replay·부하·편향 평가

### 비교군

```text
A memory 없음
B 기존 top-3
C population only
D representatives only
E cells + population + representatives
F E + adaptive drill-down
```

### dataset split

```text
build 기간
calibration 기간
완전 holdout replay 기간
```

historical replay는 반드시 as-of corpus/index/cell/brain snapshot을 사용한다.

### 평가

```text
candidate Recall@5/10/20
high +10/+20 recall
false positive
leader error
theme over-expansion
newsless hallucination
Brier/calibration
retrieval recall/diversity/dedup
latency/tokens/memory/cost
```

### 부하

```text
50k
200k
600k
```

목표:

```text
pre-LLM P95 3~5초
daily analysis P95 60~90초
online full scan 0
normal final 20k~50k tokens
hard max 60k~80k tokens
```

### 종료 조건

```text
E 또는 F가 B보다 recall/calibration 개선
newsless hallucination 악화 없음
latency/token budget 충족
selection bias와 survivorship audit 통과
```

## 12. Phase 9: 실제 import와 production 승격

### 전제

```text
Phase 1~8 종료
READY_FOR_IMPORT manifest 검증
real LLM/embedding/web/price/stock-web provider 준비
```

### 순서

```text
1,127 repaired bundle import/accept
→ warehouse rebuild/verify
→ production HNSW/FTS/metadata index build
→ memory cell/population cube build
→ llm-full category brain build
→ deep audit
→ doctor --production
→ holdout daily shadow
→ production enable
```

### rollback

```text
brain/index/cell/cube 모두 versioned snapshot
현재 production pointer는 모든 audit 통과 후 atomic switch
실패 시 이전 snapshot 유지
```

### 종료 조건

```text
import loss 0
record coverage 100%
production provider real
catalog_only false
llm-full build
production index real embedding
lookahead/provenance/coverage/brain audit 0 findings
doctor --production 통과
```

## 13. phase별 작업 원칙

각 phase는 다음 순서로 진행한다.

```text
1. 해당 phase 코드와 호출부 line-by-line 재확인
2. strict schema와 failure state 먼저 추가
3. unit test
4. integration test
5. adversarial/edge tests
6. 이전 phase regression
7. 실제 corpus 표본 shadow run
8. ruff/mypy/pytest
9. phase report와 남은 blocker 기록
10. 다음 phase 시작 승인
```

한 phase가 끝나기 전에 다음 phase의 production path를 활성화하지 않는다. 기능 flag로 기존
경로와 shadow 비교가 가능해야 한다.

## 14. 구현 시작 우선순위

바로 시작할 순서:

```text
Phase 0
→ Phase 1
→ Phase 2
→ Phase 3
```

이 네 단계는 현재 확인된 P0를 닫는다.

그 다음:

```text
Phase 4
→ Phase 5
→ Phase 6
→ Phase 7
→ Phase 8
→ Phase 9
```

top-3 숫자만 먼저 늘리거나 1,127 bundle을 production에 먼저 import하지 않는다.

## 15. 완료의 의미

최종 완료는 다음 상태다.

```text
모든 뉴스 row가 현재 사건 이해에 반영됨
모든 cutoff-safe record가 coverage에 포함됨
온라인 full corpus scan 없음
ANN이 통계 분모가 아님
population 통계가 독립 표본 단위로 계산됨
positive/negative/newsless/counterexample이 균형 있게 사용됨
대표 evidence가 전체 분포를 설명함
LLM은 통계 계산이 아닌 비교·추론을 담당함
모든 판단이 population/cell/record provenance로 내려감
historical replay에 future cell leakage 없음
production provider와 성능 기준 통과
```
