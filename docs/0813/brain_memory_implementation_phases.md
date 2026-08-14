# NSLAB 장기기억 두뇌 구현 단계

상태: Phase 0~9 bounded 구현·검증·외부 독립감사 APPROVE, Codex OAuth/CSV-only/local embedding bootstrap PASS, 실제 production activation은 readiness 5개 blocker로 차단

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

진행 상태: APPROVE (bounded implementation), 현재 corpus production 승격 차단

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

### 구현 결과

```text
ANN/FTS = 관련 cell 선택만 담당
선택 cell의 primary/secondary cutoff-safe 전체 member = 모집단 후보
요청한 independent unit type으로 분리 후 동일 unit 중복 제거
outcome 없음/충돌 = missing, negative로 변환하지 않음
newsless = ticker-day 전용 unit이므로 issuer/event 모집단에서 자동 제외
관측률 = observed_population_rate로만 표기
```

산출물:

```text
runs/populations/<run_id>/<cluster_id>/<population_id>/member_records.jsonl
runs/populations/<run_id>/<cluster_id>/<population_id>/independent_units.jsonl
runs/populations/<run_id>/<cluster_id>/<population_id>/population_cube.jsonl
runs/populations/<run_id>/<cluster_id>/<population_id>/population_manifest.json
```

`population_id`는 run/cluster, cutoff, selected cells, memory snapshot, corpus/source generation,
unit/routing 선택, 통계·cube·bootstrap version에 결속된다. `inspect-population`은 현재 snapshot
DB에서 전 구성원·unit·cube·summary를 다시 계산해 세 artifact와 정확히 비교한다.

내부 검증:

```text
3 reasoning issuer-day records -> 2 independent units 중복 제거
newsless record의 issuer-day 분모 유입 0
missing outcome의 high-return denominator 유입 0
incremental snapshot과 fresh full snapshot의 population/artifact hash 동일
artifact 변조 시 독립 inspection 실패
purpose별 lane/unit 분리 및 REASONING-only 분모
50,000 selected records hard budget
50,000 end-to-end: 약 343.5초, peak 1,080 MiB
50,000 초과는 표본 추출 없이 fail-closed, Phase 6 세분화 전 production blocker
strict unit 계약상 REASONING unsupported 64건은 통계 분모에서 제외하고 readiness 차단
250,000 cube-row 상한은 고카디널리티 end-to-end 미검증, Phase 6 운영 blocker
ruff PASS
mypy 98 modules PASS
pytest 1,397 PASS
외부 독립감사 APPROVE (bounded implementation)
```

## 9. Phase 6: diverse representatives와 adaptive drill-down

진행 상태: bounded implementation 완료, 외부 독립감사 APPROVE

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

`multi-hop beneficiary`와 `leader pair disagreement`는 Phase 6에서 근거 없는 문자열 hint로
받지 않는다. 두 신호는 current-event graph/pair artifact, cutoff, source provenance를 함께
검증할 수 있는 Phase 7 typed trigger evidence로 구현한다.

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

### 구현 결과

```text
unit 내부 member별 lane/role/path/quality 후보를 보존하고 최종 선택만 one-unit-one-record
unit-balanced 512 record pool -> 필수 minority strata -> MMR + bounded facility-location
entropy 기반 초기 8~16, distribution error가 남으면 최대 32
population stratum share 최대 오차 0.25 / token upper bound 24,000
trade-date concentration 8 / issuer-theme key concentration 4
adaptive depth 2 / cells 12 / records 32 / cumulative tokens 72,000
small ESS, polarity, regime, optional coverage, unexplained trigger만 Phase 6에서 확장
lane/regime/lane-regime facet HNSW + FTS union, online record-vector full scan 0
모든 iteration population/representative manifest hash 결속
explicit inspect에서 selection과 trace 전체 재계산
```

측정:

```text
50,000 synthetic units compute-only: 5.781초, peak 100.556 MiB
candidate pool 512, selected 16
real 1536D end-to-end는 Phase 5 production blocker 해소 전 미실측
compute-only 수치는 production latency PASS 근거가 아님
```

검증:

```text
ruff PASS
mypy 101 modules PASS
pytest 1,413 PASS
schema parity PASS
외부 독립감사 APPROVE (bounded Phase 6)
```

## 10. Phase 7: category brain·beneficiary graph·final synthesis 통합

상태: APPROVE (bounded Phase 7)

### 목적

category brain을 offline semantic query planner로 사용하고 purpose별 population statistics와
selected representative evidence를 bounded final synthesis에 통합한다.

구현 결과:

```text
immutable llm-full brain snapshot과 production memory snapshot exact 결속
CompiledBrainClaim offline DuckDB HNSW + vector ledger
material cluster별 query embedding 1회 + semantic category claim 최대 3개
catalyst/candidate-error/newsless purpose별 population/representative/adaptive 실행
candidate input과 company memory에서 exact 재생성되는 BeneficiaryGraph v2
cluster round-robin compact allocator와 48,000 bytes hard cap
FinalSynthesisContext v3 bounded projection과 selected-claim standalone proof
```

### category brain 입력

```text
memory cells
population distributions
representative successes/failures
unresolved contradictions
```

query plan은 original/expanded query, embedding model, query/claim vector hash, claim ID와 score를
기록한다. category claim은 query planner일 뿐 evidence가 아니다. claim corpus는 llm-full build에서
한 번 임베딩하고 daily online 경로는 query vector만 계산한다. final bundle은 전체 claim/vector
artifact가 아니라 실제 선택 claim proof만 포함한다. proof payload hash는 immutable category index
manifest의 Merkle root에 포함되며 local/standalone inspector가 inclusion proof를 독립 검증한다.
standalone은 embedded event rows와 selected claims에서 original/expanded query 문자열을 exact 재생성하며,
ANN score/top-K 재실행은 provider와 DB가 있는 local deep inspector가 담당한다.
category guidance는 verified claims와 representative selected IDs에서 별도 pure projection으로 재생성한다.

### beneficiary graph

```text
event → mechanism → benefit layer → business role → company
```

edge provenance와 cutoff-safe 재검증을 강제한다. candidate input artifact와 material event manifest,
candidate-matched company memory files에서 path를 exact 재생성한다. company memory는 `available_from`과
`known_at` 모두 cutoff 이하인 파일만 허용하고 aliases와 여러 delta의 business roles를 union한다.
standalone importer도 embedded candidate/event/company 원료를 동일 pure projector로 재계산해 graph
path, mechanism, role, source와 unresolved 후보의 exact parity를 검증한다.

`THEME_BENEFICIARY` graph path가 material cluster, source IDs와 실제 causal mechanism step 2개 이상을
가질 때만 typed adaptive trigger를 만든다. thesis/why-now와 synthetic prompt provenance는 causal
evidence로 세지 않는다. leader pair disagreement는 current-event pair evidence 계약이 없어 Phase 8
replay/eval 전까지 deferred로 둔다.

### purpose별 population

```text
catalyst_response -> event-issuer/issuer/theme/theme-ticker
candidate_error -> event-issuer/issuer/theme-ticker
newsless -> ticker-day
leader_selection -> Phase 8까지 deferred
```

DailyMemoryContext는 모든 material cluster의 `(cluster, purpose, unit)` built key와 uncovered purpose를
기록한다. inspector는 population/representative/adaptive Counter가 각 key별 정확히 1인지 재계산한다.
candidate-error와 newsless는 catalyst response 분모에 섞이지 않는다.
같은 pure chain validator를 standalone importer에도 적용해 artifact identity, selected record/unit IDs,
final trace references, typed trigger evidence와 built/uncovered 선언을 embedded 원료와 대조한다.
news/event/memory coverage와 llm-full brain의 run/cutoff/snapshot/corpus/source-generation/category-index도
같은 source-chain validator에서 exact 대조한다.
news covered=input/missing=0과 event input parity/unassigned=0/duplicate=0을 서명 전에 강제한다.

### final payload v3

```text
current event/coverage context
daily/graph immutable ref와 compact projection
purpose별 population summaries와 representative records
unresolved disagreements
category brain query plans와 selected guidance
current D-1/regime context
candidate verification와 red team
```

제외:

```text
전수 raw record shards
전수 contribution bodies
semantic retrieval의 raw episode/record bodies
semantic cluster의 promoted raw record bodies
현재 D-day price/outcome
```

`DailyMemoryContext v2`는 news/event/memory coverage, event cluster JSONL, purpose별 population,
representative, adaptive v4, immutable category brain/index manifest, selected claim proof,
beneficiary graph와 compact context의 path/hash/count를 결속한다. compact allocator는 모든 material
cluster의 coverage를 먼저 넣고 representative/graph path를 round-robin으로 추가한다. 11 material
cluster capacity 회귀에서 모든 cluster 대표를 유지한다.
standalone importer는 embedded population/representative/query/graph 원료에 같은 pure compact
projector를 적용해 compact counts, roles, disagreement와 excerpts의 exact parity를 검증한다.

report exporter는 local deep inspection을 통과한 exact Phase 7 artifact closure를
`NSLAB_PHASE7_TRANSPORT_HMAC_KEY`로 HMAC-SHA256 서명한다. standalone importer는 최소 32-byte 공유 운영
key로 run/date/cutoff와 closure signature를 검증하며 key가 없거나 다르면 fail-closed한다.
export/import/provenance는 process environment와 project `.env`를 동일 `Settings.env_value` 경로로 읽는다.
`research import-bundle`과 version-aware importer도 Phase 7 표식 v1 bundle을 저장하기 전에 동일 strict
parser/HMAC preflight를 수행한다.

Final v3는 full daily manifest나 graph를 prompt에 복제하지 않는다. immutable ref, snapshot/purpose
identity, 역할별 selected record IDs, compact context와 graph summary만 전달한다. legacy top-K는 기본
provenance로 재주입하지 않으며 final 후보 identity는 pre-final/verification/graph의 exact 교집합이어야
한다.

reporting bundle은 bounded reference closure를 source path, raw/embedded SHA, item count, line ending과
함께 운반한다. standalone importer는 역할별 exact schema allowlist와 canonical relative path를
강제하고 unknown/downgrade/path traversal을 거부한다. lookahead/provenance/reporting은 Phase7일 때만
real embedding index를 lazy 초기화하며 legacy audit은 provider 없이 동작한다.

### 종료 조건

```text
모든 final memory ID가 selected representative role set에 속함
final candidate identity가 candidate verification과 graph에 닫힘
newsless/candidate-error 전용 purpose coverage 또는 explicit uncovered ledger
future category claim과 category index orphan retry 회귀 통과
48 KB compact와 final prompt token hard gate
lookahead/provenance/standalone deep audit 통과
```

운영 제한:

```text
current corpus unsupported REASONING 64: production memory readiness 차단
50k population E2E와 high-cardinality cube: Phase 5 blocker 유지
real 1536D provider / 600k peak RSS·latency: Phase 8/9 미실측
leader pair typed trigger: cutoff-safe current pair artifact가 생길 때까지 deferred
```

외부 독립감사는 `APPROVE`이며 P0/P1 잔여 finding은 없다. 공개 version-aware import의 Phase 7 HMAC
preflight와 zero-write rejection까지 직접 재현했고, 독립 full pytest 1,431개 및 ruff/mypy/diff-check를
통과했다.

## 11. Phase 8: shadow replay·부하·편향 평가

상태: bounded evaluator와 source-closure verifier 및 외부 독립감사 완료, 실제 corpus production gate 차단

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

### 구현 결과

`ShadowReplayDataset v1`과 `ShadowEvaluationManifest v1`은 A~F feature/snapshot identity, sealed
build/calibration/holdout split, complete outcome universe, retrieval/system telemetry와 50k/200k/600k load
profile을 결속한다. split 계획은 calibration 전에 `memory seal-shadow-split` 실제 실행 시각과 HMAC으로
사전 등록하며 public API의 과거 시각 주입을 허용하지 않는다. 전체 dataset도 별도 HMAC과 canonical
content-addressed 경로로 봉인한다.

Arm telemetry는 `NSLAB_SHADOW_RUNNER_HMAC_KEY`로 실행 종료 5분 이내 source receipt를 발급하고,
qualitative/retrieval truth는 `NSLAB_SHADOW_TRUTH_HMAC_KEY`로 cutoff 이후 별도 receipt를 발급한다. Dataset
sealer는 두 source authority의 서명을 재검증하며 coherent telemetry/truth rewrite를 거부한다.
Measured load profile도 runner key로 workload·sample·raw metric·summary·snapshot 전체를 aggregate 서명하며,
마지막 sample 완료 5분 이내 발급과 dataset 생성 이전 완료를 강제한다.

`memory evaluate-shadow`는 full-universe Recall/Brier, failure·newsless·leader/theme 지표, retrieval diversity,
latency/token/memory/cost를 content-addressed artifact로 만들고 `memory inspect-shadow`가 source부터 exact
재계산한다. C~F는 canonical production memory snapshot v3 deep audit, B는 frozen top-3 index와 record-store
generation parity가 필수다. 같은 날짜의 A~F 실행 provider/model/prompt/config와 C~F snapshot은 exact
동일해야 한다. B~F는 같은 corpus/source generation/cutoff/llm-full brain을 사용하고 C~F retrieved record는
snapshot DuckDB projection과 일치해야 한다. B top-3 record도 같은 production snapshot DB에서 exact
projection을 가져야 하며, 모든 날짜·arm source hash와 historical context의 provider/model/class 설정을
독립 검증한다. Production closure는 factory-issued OpenAI provider와 validated stock-web price source를
요구하고 구조만 맞춘 fake provider는 거부한다.

합성 20 calibration + 20 holdout A~F 회귀는 E/F Recall@20 1.0, B 0.0, E/F Brier 0.0,
B 0.75로 metric contract를 통과했다. Historical source closure는 canonical blind prediction/context, cutoff-safe
news, FULL_MARKET_COMPLETE postmortem의 truth hash/retrieval label 봉인, live full-market price universe, 전체 run
lookahead audit를 재검증한다. BlindPrediction candidate의 claimed theme/news-cause와 postmortem의 ticker별
theme/leader/newsless truth도 exact 교차검증한다.
합성/self-declared dataset은 `production_ready=false`다.

50k/200k/600k measured profile은 canonical raw sample receipt뿐 아니라 고유 run/time/content-addressed workload
ledger와 같은 record count의 production memory snapshot, real embedding provider/model/dimension,
corpus/source generation, deep inspection을
요구한다. 실제 corpus에는 아직 A~F run과 real 1536D profile이 없어 production gate는 닫혀 있다.

실제 corpus readiness는 paired historical date 1, production memory missing, catalog brain, mock provider,
pre-registration/runner/truth key missing으로 `ready=false`다. 32D reduced-schema 50k/200k/600k query microbenchmark는
통과했지만 real 1536D end-to-end profile이 아니므로 production load gate에 사용하지 않는다. 상세 근거는
`phase8_shadow_replay_evaluation_report.md`에 기록했다.

## 12. Phase 9: 실제 import와 production 승격

상태: bounded implementation 완료, 외부 독립감사 APPROVE, 실제 provider/shadow gate 미충족으로 activation 차단

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

### 구현 결과

실제 sequential repair manifest 1,397행과 source/repaired 약 32GB를 read-only deep hash로 다시 검증했다.
READY_FOR_IMPORT는 1,127 bundles, 606,737 records, eligible 384,846이며 finding은 0이다. inventory ID는
`P9INV-974A99B55B152FF02040`이고 source/repaired/quality-gate/entries root를 별도 artifact로 결속했다.

단건 importer의 bundle별 전수 record scan/index rebuild는 production batch 경로에서 제거했다. 격리 project
root에서 shared identity index를 유지하고 마지막에 SQLite-backed streaming index를 한 번만 만든다. 실패
workdir은 canonical stage에 publish되지 않으며 live store와 pointer는 변하지 않는다.

Release finalize는 batch import, llm-full brain, production memory, historical A~F shadow, doctor, provenance와
real provider를 모두 재검증한다. 성공 release만 content-addressed directory에 고정하고,
record artifact ledger, `configs/`·`prompts/`·`schemas/` file-hash ledger와 중단 복구 transaction을 함께
결속한다. episode 검증 metadata·brain·production memory/vector index·warehouse·선택 shadow evidence는
별도 release artifact ledger로 exact path/size/SHA를 봉인하고, record/release artifact root를 모두 release
ID에 포함한다. memory pointer는 canonical snapshot manifest 경로만 허용한다. 운영 `.env` secret은
release에 복사하지 않고 outer operator root에서 주입한다.
`production/current.json` 한 파일을 HMAC과 함께 원자 교체하고 activation history와 exact parity를 검사한다.
rollback도 이전 immutable release를 가리키는 동일 pointer 교체다.

정상 일일 lifecycle은 immutable release와 분리했다. record-derived company memory는 runtime exact no-op으로
검증하고, candidate company memory는 불변 run prediction·release ID·payload hash를 embedded HMAC
attestation으로 결속해 원자 기록한다. warehouse daily projection과 post-close evaluation episode는 명시적
가변 산출물이며, 봉인 doctor snapshot과 현재 readiness는 deep inspection에서 각각 검증한다. analyze/evaluate는
outer operator root에서 해석한 Settings를 active release까지 그대로 전달한다.

현재 로컬 deployment checkout에는 promotion key 5종, Codex OAuth, pinned real local embedding,
stock-web research_daily, CSV-only/disabled-web 정책이 준비되어 production preflight가 통과했다. 실제
import/activation은 실행하지 않았고 live store는 968 records이다. Phase 9 readiness는 inventory attestation,
current-store 정합 import, staged import, 40일 A~F shadow, active release의 5 blockers로 `ready=false`다.
상세 근거는 `phase9_production_import_and_promotion_report.md`에 기록했다.
최신 고정 tree의 외부 독립감사는 `APPROVE`이며 잔여 P0/P1 finding은 없다.
bootstrap 변경 tree의 gate는 `ruff check .`, `mypy` 119 modules, schema parity, 전체 pytest 1,526개 통과다.

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
