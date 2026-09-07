# Codex Goal — NSLAB 아키텍처 바로잡기: One-Time Offline Brain + Thin Daily Inference

너는 `Daikisong/new_bot`의 수석 아키텍트다.

이번 작업은 “느린 경로를 조금 최적화”하는 작업이 아니다.
PR #125 이후 뒤집힌 **제품 경계 자체를 바로잡는 작업**이다.

사용자의 최종 제품 요구는 다음과 같다.

> 1,543개 Gold/repaired 연구 bundle과 823,279개 record를 한 번 offline에서 해석·통합해 영구 brain으로 만든다.
> 이후 매일 08시 뉴스 CSV 하나를 넣으면 이미 구축된 brain·memory·index를 사용해 주도섹터, 직접 촉매 종목, 수혜주, 대장 후보를 판단한다.
> 매일 historical raw corpus를 다시 LLM에 읽히거나, material cluster마다 과거 record를 수십 개씩 다시 map-reduce하지 않는다.

이 요구는 모든 구현·평가·운영 편의보다 우선한다.

## 2026-09-07 사용자 정정 및 재개 기준

이번 개정은 기존 문서의 정상 2회/최대 4회 daily 계약을 대체한다.
사용자가 원하는 흐름은 **저장된 두뇌 + 오늘 뉴스 -> GPT가 함께 해석·판단 -> 답변**이다.
두뇌 없이 수행하는 별도 1차 해석은 없다. 정상 호출은 1회이며, 구조화 응답
형식 오류일 때만 최대 1회 보정한다. Open-world는 새로운 사건과 종목을
배제하지 않는다는 뜻이지, 두뇌 없이 해석을 시작한다는 뜻이 아니다.

기존 offline compiler v5의 prompt, schema, model, checkpoint identity는 변경하지 않는다.
repair/import/기존 record embedding은 재실행하지 않는다. 최신 daily 구현은
`7798fb670f2a3e6aa9a7e28633080562537575b7`에 커밋되어 있다.

2026-09-07 19:04 KST 재개 전 실측 기준선:

```text
compile ID                 OFFLINE-COMPILE-add3461bd175147e255d
compiler                   nslab.offline_semantic_brain.compiler.v5
provider/model/reasoning    codex-oauth / gpt-5.6-sol / xhigh
planned logical calls      7,671
successful checkpoints     2,164 (28.2101% of logical calls, NOT elapsed time)
long-payload completed     90 / 90
leaf completed             2,074 / 7,423
reduce/review completed    0 / 158
remaining logical calls    5,507
previous stop reason       Codex usage limit on 2026-09-03
completed new package      none
production activated       false
```

동일 성공 checkpoint는 재사용한다. 재개 시 local geometry 약 16~18분은 현재
구현에서 재계산하지만 이미 성공한 LLM 의미 합성을 처음부터 다시 하는 것은 아니다.
`progress.json`의 record 100%는 local 단계 완료이지 전체 두뇌 완료가 아니다.
새 호출 성공을 확인하기 전에는 할당량이 풀렸다고 단정하지 않는다.

재개 명령은 다음과 같다. 중복 build 프로세스가 없는지 먼저 확인한다.
동일 build가 이미 실행 중이면 새 프로세스를 시작하지 말고 기존 진행을 확인한다.

```powershell
python -m news_scalping_lab.cli brain build-offline --source-project "C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project" --expected-manifest-sha256 "6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576"
```

Goal 자동 실행 상태와 로컬 build 프로세스 상태는 별도로 확인한다. 재개했다고
Goal을 완료로 표시하지 않는다. 빌드, 감사, 실제 일일 경로 평가, 활성화는 별개다.
이 문서의 저장소 사본은 `docs/operations/codex_goal_offline_brain_thin_daily_inference.md`다.

---

# 0. 현재 기준과 문제 인정

## 0.1 기준 commit 및 immutable 자산

```text
repository:
Daikisong/new_bot

starting main:
675074961920bece5ee6bd201efd3b5177015e48

baseline production staging brain:
brain-08fe3aaaa3

baseline production memory:
MEMIDX-1e64a1b6e6ba7b07b799

canonical records:
823279

training eligible:
524948

record corpus SHA-256:
2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75

existing real embedding:
pinned multilingual MiniLM, 384 dimensions
```

다음은 그대로 보존한다.

```text
1,543 repaired bundle
823,279 BrainRecordEnvelope
record store
warehouse
record embeddings
memory cell memberships
population statistics
company memory
beneficiary provenance
baseline brain
external audit artifacts
BLIND/OUTCOME and available_from contracts
CSV_MEMORY_ONLY_STRICT
```

재repair, 재import, 전 record 재embedding은 금지한다.

## 0.2 현재 잘못된 경계

PR #125에서 다음이 daily path로 들어갔다.

```text
material cluster별 runtime retrieval
→ cluster당 16~128 historical raw records 선택
→ 16개 단위로 gpt-5.6-sol/xhigh evidence memo 호출
→ 모든 cluster에서 반복
```

또한 open-world pass는 event-cluster batch마다 LLM을 호출하고, 각 prompt에 `member_news` 전체를 넣는다.

이 구조는 daily inference 비용을 다음에 비례하게 만든다.

```text
material cluster count
× selected historical record count
÷ evidence batch size
```

이는 사용자가 원한 제품과 반대다.

---

# 1. 최상위 아키텍처 불변조건

## 1.1 Offline과 Online을 물리적으로 분리

```text
OFFLINE BRAIN BUILD
- research corpus가 바뀔 때만 실행
- historical record 의미 해석
- semantic clustering
- population/statistics
- success/failure boundary synthesis
- beneficiary/leader/continuation knowledge synthesis
- category/world brain 생성
- 많은 LLM 호출과 긴 실행시간 허용
- 결과를 immutable BrainPackage로 봉인

DAILY INFERENCE
- 오늘 뉴스 CSV마다 실행
- 이미 봉인된 BrainPackage·memory·index 사용
- historical raw corpus 재학습·재요약 금지
- historical record batch별 LLM 호출 금지
- corpus 크기와 무관한 bounded call graph
```

## 1.2 Daily path의 복잡도

Daily LLM 호출 수는 다음에 비례하면 안 된다.

```text
823,279 record 수
material event cluster 수
memory cell 수
retrieved record 수
```

정상 daily call graph:

```text
LOCAL: current news -> precompiled world/category knowledge + relevant memories
CALL 1: FINAL_MARKET_DECISION (brain-informed interpretation and answer together)
```

구조화 응답 형식 실패 시 최대 1회 bounded repair retry만 허용한다.
별도 해석, 후보별 판단, red-team, evidence map 호출을 추가하지 않는다.

```text
normal live calls = 1
hard maximum including schema repair = 2
```

이 상한은 속도 목표가 아니라 **offline/online 제품 경계 계약**이다.

사용자가 정하지 않은 90초 SLA는 품질 gate로 사용하지 않는다. Wall-clock은 측정·보고하되, daily LLM call graph가 corpus 규모에 따라 늘어나는 구현은 시간과 관계없이 실패다.

## 1.3 Daily historical evidence

Daily LLM은 historical raw records를 batch map-reduce하지 않는다.

허용:

```text
precompiled SemanticMemoryCapsule
precompiled SynthesizedMechanismClaim
precomputed population statistics
precomputed representative/counterexample capsule
company/beneficiary/leader graph edge
최종 검증용 bounded exact witness excerpt
```

최종 prompt에 넣을 historical raw witness는:

```text
전체 하루 기준 기본 12개
hard max 24개
별도 LLM 호출 없음
```

으로 제한한다.

Raw witness는 provenance와 exact excerpt 확인용이다. 의미 해석은 offline capsule에 이미 존재해야 한다.

---

# 2. 먼저 수행할 Call-Graph 감사

새 코드를 작성하기 전에 `DailyAnalyzer.analyze()`에서 도달 가능한 모든 LLM call site를 정적으로·동적으로 조사한다.

생성:

```text
diagnostics/daily_llm_call_graph_before.json
diagnostics/daily_llm_call_graph_before.md
```

각 call site:

```text
function
purpose
reachable from daily analyze
inside loop 여부
loop dimension
potential call count formula
input source
historical raw payload 포함 여부
```

현재 최소한 다음을 확인한다.

```text
_run_open_world_first_analysis:
cluster_batches loop에서 호출

build_runtime_evidence_memos:
runtime trace/cluster loop
+ selected historical records 16개 batch loop에서 호출
```

daily path에서 아래 패턴을 CI로 금지한다.

```text
for cluster ... await llm.*
for historical_record_batch ... await llm.*
for memory_cell ... await llm.*
for retrieval_lane ... await llm.*
```

---

# 3. PR #125에서 보존할 것과 제거할 것

## 3.1 보존

```text
HNSW/FTS local retrieval
13개 evidence lane 분류
cutoff-safe as-of filtering
offline exposure sidecar
record-level retrieval trace
future/web/full-scan guards
immutable artifact/resume logic
BUILD/CALIBRATION/HOLDOUT split infrastructure
```

## 3.2 Production daily path에서 제거

```text
build_runtime_evidence_memos()의 LLM 호출
cluster별 historical raw evidence mini-map
selected historical record 전부를 LLM에 노출해야 한다는 gate
RUNTIME_EVIDENCE_BATCH_SIZE 기반 daily fan-out
runtime_payload_exposed=모든 selected record 강제
```

해당 코드는 필요하면 다음으로 이동할 수 있다.

```text
offline compiler diagnostic
external audit sample
developer-only research inspection
```

하지만 `analyze`, production shadow, 실제 daily product에서는 호출되지 않아야 한다.

---

# 4. One-Time Offline Semantic Brain V2

기존 baseline brain은 삭제하지 않는다. 새 brain package를 별도 version으로 만든다.

## 4.1 SemanticMemoryCapsule

신규 strict model:

```text
SemanticMemoryCapsule
```

필드:

```text
capsule_id
category
semantic_unit_id
member_record_count
member_independent_unit_count
member_record_root
record_type_distribution
polarity_distribution
label_quality_distribution
time_distribution
regime_distribution

event_or_mechanism_summary
economic_transmission
market_narrative
applicable_conditions
failure_conditions
boundary_conditions
novelty/modality distinctions
leader_selection_implications
beneficiary_implications
continuation_implications

supporting_record_ids
contradicting_record_ids
near_miss_record_ids
counterexample_record_ids
newsless_or_unexplained_record_ids
error_record_ids

representative_exact_witnesses
available_from
provenance_root
embedding
```

Capsule은 특정 단어 점수표가 아니다. 조건부 메커니즘·적용조건·실패조건·반례를 압축한다.

## 4.2 모든 record의 기여

823,279개 모든 record는 정확히 하나의 primary semantic unit과 0개 이상의 secondary unit을 가진다.

모든 record는 최소한 다음에 기여한다.

```text
unit membership
population statistics
time/regime/polarity distribution
provenance root
```

모든 record raw payload를 LLM에 직접 넣을 필요는 없다.

대신 다음 semantic 차이는 별도 unit 또는 boundary/outlier로 반드시 보존한다.

```text
같은 structural signature 내부의 다른 mechanism
확정/예정/협의/신청 차이
경제가치 귀속 차이
정량 강도 차이
positive vs negative vs near-miss
성공처럼 보였지만 실패
약한 뉴스였지만 성공
newsless/unexplained
candidate generation/ranking error
theme breadth success/failure
leader inversion
시장 regime 반전
희귀 reasoning outlier
```

## 4.3 dynamic semantic coverage

기존의 다음 고정 제한을 semantic coverage 계약으로 사용하지 않는다.

```text
20,000-record shard당 representative 64
group당 representative 3
category records[:200]
```

기존 embedding과 generic structured axes를 사용한다.

```text
structural strata
→ embedding semantic subclusters
→ radius/diameter 검사
→ medoid + boundary + outlier
```

heterogeneous unit은 representative를 무작정 늘리기 전에 split한다.

필수:

```text
record primary assignment coverage = 100%
unassigned = 0
duplicate primary assignment = 0
rare/outlier unit coverage = 100%
unrepresented reasoning unit = 0
```

## 4.4 Offline LLM map/reduce

LLM 호출은 이 단계에서만 historical payload를 해석한다.

```text
semantic-unit leaf maps
→ mechanism subgroup reduce
→ category reduce
→ contradiction/boundary review
→ world model
```

모든 child node가 tree에 들어간다.

```text
first N shortcut 금지
silent truncation 금지
child omission 0
```

긴 실행과 다수 호출은 one-time offline 비용으로 허용한다.

content-addressed cache와 checkpoint/resume을 사용해 완료된 node를 다시 호출하지 않는다.

Provider:

```text
codex-oauth
gpt-5.6-sol
xhigh
```

## 4.5 실제 LLM 지식 claim

기존 `DeterministicRecordClaim`은 evidence/index용으로 보존한다.

별도 생성:

```text
SynthesizedMechanismClaim
```

필드:

```text
claim_id
category
statement
mechanism
conditions
boundary_conditions
failure_modes
supporting_capsule_ids
contradicting_capsule_ids
supporting_record_ids
contradicting_record_ids
source_node_ids
available_from
confidence
status
```

두 claim 종류를 숫자와 문서에서 혼동하지 않는다.

## 4.6 BrainPackage

생성:

```text
brain/packages/<BRAIN_VERSION>/
```

필수:

```text
brain_package_manifest.json
semantic_capsules.jsonl
semantic_capsule_index.duckdb
synthesized_mechanism_claims.jsonl
category_brain/
world_model.md
population_cube/
beneficiary_graph/
leader_selection_memory/
continuation_memory/
company_memory_ref
record_provenance_roots
offline_compile_manifest.json
semantic_influence_manifest.json
```

BrainPackage manifest는 다음에 결속한다.

```text
record corpus root
memory snapshot root
warehouse root
embedding identity
compiler version
provider/model/reasoning
capsule root
mechanism claim root
category brain root
```

---

# 5. Thin Daily Inference

## 5.1 전체 뉴스 row coverage는 local

```text
CSV 전수 parse
exact duplicate 제거
semantic event clustering
issuer/predicate/counterparty/numeric/time conflict 분리
row disposition
```

모든 row는 coverage ledger에 남긴다.

이 단계는 local code + pinned embedding으로 수행한다. LLM call 0이다.

## 5.2 CurrentEventCapsule 생성

각 event cluster에서 LLM에 넣을 compact capsule을 local code로 만든다.

```text
cluster_id
source row IDs
대표 제목
predicate-bearing exact sentences
issuer/company literals
ticker literals
counterparty
numbers/units
modality
published time
duplicate count
conflict flags
```

금지:

```text
모든 member_news 본문 통째 삽입
동일 기사 반복 삽입
cluster별 LLM call
```

전체 `CurrentEventCapsule`을 한 prompt에 넣는다. Context 한도를 넘으면 local relevance/materiality allocator로 압축하되 모든 row/cluster disposition은 보존한다.

## 5.3 첫 LLM 호출 전 두뇌 장전

입력:

```text
CurrentEventCapsules
D-1 safe market/regime summary
selected immutable BrainPackage
```

local 처리:

```text
load compiled world_model and category_brain guidance
derive queries directly from current titles/predicate sentences/companies/numbers/modality
retrieve cutoff-safe semantic capsules and mechanism claims
bind the context to current-news hash, package root and build cutoff
```

여기서는 LLM을 호출하지 않는다. 별도 CurrentDayInterpretation 응답을 만들거나
그 가설에 의존해 검색하지 않는다. 과거 brain에 등장한 이름은 후보 허용목록이 아니다.

## 5.4 Local brain retrieval

오늘 뉴스에서 직접 구성한 query로 local index만 조회한다.

```text
SemanticMemoryCapsuleIndex
SynthesizedMechanismClaimIndex
CategoryBrainIndex
memory cells
population cube
company/beneficiary graph
leader/continuation memory
```

모든 검색·집계는 local code다. LLM call 0이다.

각 현재 사건별로 다음을 선택한다.

```text
positive capsule
negative capsule
near-miss capsule
counterexample capsule
theme success/failure capsule
beneficiary capsule
leader capsule
continuation capsule
error capsule
```

ANN은 capsule/cell 선택기다. 통계 분모는 precomputed cutoff-safe population이다.

## 5.5 DailyBrainContext

최종 LLM 입력용 compact artifact:

```text
current event capsules
compiled world/category guidance with source path and SHA-256
selected semantic capsules
selected synthesized mechanism claims
population statistics
current-vs-history structural differences
beneficiary/leader/continuation graph
unresolved contradictions
최대 24 exact raw witnesses
D-1 context
candidate verification
```

Historical raw record에 대한 새로운 daily LLM map/reduce는 없다.

## 5.6 유일한 CALL 1 — Brain-informed final market decision

현재 뉴스와 이미 합성된 두뇌를 함께 읽는 한 번의 structured call에서 다음을
함께 수행한다. 해석 전용 호출은 없다.

```text
현재 사건의 의미 해석과 analyzed_cluster_ids 전수 회계
주도섹터 판단
직접 단일뉴스 후보
정책·산업 수혜주 후보
대장 후보
연속성 후보
반론/red-team
최종 순위
confidence
```

별도의 후보별·섹터별·red-team별 LLM fan-out은 금지한다.

각 최종 결과에:

```text
current source row IDs
semantic capsule IDs
mechanism claim IDs
supporting record IDs
contradicting record IDs
population manifest/root
uncertainty
```

를 남긴다.

Raw witness를 읽지 않은 record를 직접 인용하지 않는다. Capsule-level 근거는 capsule ID와 그 provenance root로 인용한다.

현재 daily v2 응답은 `BrainInformedDecision(analyzed_cluster_ids, prediction)`이다.
LLM 호출 전에 두뇌 누락, hash 변조, 잘못된 뉴스 결속, 미래 package를 거부한다.

---

# 6. Incremental Offline Update

새 Gold 연구 bundle이 추가될 때만 실행한다.

```text
new records import
→ affected semantic units 탐지
→ affected capsules/statistics 갱신
→ affected reduce ancestors만 재컴파일
→ 새 immutable BrainPackage
```

daily CSV 입력은 brain update trigger가 아니다.

다음 명령 경계를 분리한다.

```text
nslab brain build-offline
nslab brain update-offline
nslab analyze-daily
```

`analyze-daily`는 import·brain rebuild·historical evidence map을 호출할 권한이 없다.

---

# 7. 실제 Daily Product와 동일한 평가

평가용으로 별도의 heavy architecture를 만들지 않는다.

다음 명령/함수를 그대로 사용한다.

```text
analyze-daily
```

차이는 cutoff-safe snapshot과 outcome 봉인뿐이다.

## 7.1 비교 arm

```text
A:
현재 CSV + D-1, historical brain 없음

B:
baseline brain-08fe3aaaa3의 compact claims/context

C:
새 Offline Semantic Brain V2 BrainPackage
```

모든 arm의 daily call graph는 동일하다.

```text
정상 1 call
최대 2 calls including one schema repair
```

어떤 arm도 historical raw evidence mini-map을 실행하지 않는다.

## 7.2 평가 순서

기존 BUILD/CALIBRATION/HOLDOUT와 2026-06-23 이후 post-cutoff 자료를 사용할 수 있다.

각 split에서:

```text
모든 날짜 BLIND prediction 먼저 생성·봉인
→ 그 뒤 outcome 개방
→ market metrics 계산
```

## 7.3 지표

```text
sector Recall/precision
upper-limit Recall@5/10/20
high20 Recall@5/10/20
high10 Recall@5/10/20
candidate precision
leader selection accuracy
theme breadth accuracy
Brier/calibration
newsless hallucination
unsupported ticker generation
citation closure
```

속도·token·calls도 보고하지만 사용자가 정하지 않은 초 단위 gate로 품질평가를 중단하지 않는다.

반드시 확인:

```text
daily call count가 corpus size와 무관
daily historical raw LLM map calls = 0
daily import/rebuild calls = 0
daily selected capsule 수와 witness 수 bounded
```

---

# 8. 기존 PR #125 산출물 처리

## 8.1 유지

```text
retrieval v4 local candidate search
lane balancing
as-of evaluation snapshot
trace schema
future/web/full-scan guards
immutable resume
```

## 8.2 Deprecated

다음은 production daily path에서 deprecated로 표시한다.

```text
runtime_evidence_map_reduce.v1
build_runtime_evidence_memos()
cluster별 raw-record memo calls
```

테스트·감사 도구에서만 사용할 수 있으며 production import graph와 daily analyzer에서 도달 불가능해야 한다.

---

# 9. Architecture Tests

## 9.1 Daily LLM call graph

```text
test_daily_normal_call_count_is_one
test_daily_max_call_count_with_repairs_is_two
test_daily_loads_compiled_brain_before_first_llm_call
test_daily_retrieval_is_grounded_in_current_news_without_interpretation_call
test_daily_llm_call_count_is_independent_of_record_count
test_daily_llm_call_count_is_independent_of_material_cluster_count
test_no_daily_llm_call_inside_historical_record_loop
test_no_daily_llm_call_inside_memory_cell_loop
test_no_daily_runtime_evidence_memo_map
```

대규모 fixture:

```text
10k records vs 823,279 records
10 material clusters vs 300 material clusters
→ planned daily LLM call count 동일
```

## 9.2 News coverage

```text
test_all_news_rows_have_disposition
test_all_material_clusters_enter_current_event_capsules
test_member_news_bodies_are_not_repeated_in_prompt
test_open_world_interpretation_and_final_decision_share_one_call
```

## 9.3 Offline brain

```text
test_all_records_have_primary_semantic_unit
test_rare_mechanism_preserved
test_same_signature_different_mechanism_splits
test_all_semantic_units_enter_reduce_tree
test_no_first_n_category_shortcut
test_synthesized_claim_provenance_closure
test_incremental_update_matches_full_rebuild
```

## 9.4 Daily brain use

```text
test_daily_uses_selected_capsules
test_daily_uses_population_statistics
test_daily_uses_negative_and_counterexample_capsules
test_daily_final_output_has_capsule_and_record_provenance
test_daily_does_not_import_or_rebuild
test_daily_no_online_full_corpus_scan
test_daily_future_record_zero
test_daily_blind_web_zero
```

---

# 10. 실행 단계

한 거대한 PR로 뒤섞지 않는다.

```text
PR-A — Architecture boundary correction
- call graph audit
- daily runtime evidence LLM map 제거
- single-call brain-informed DailyAnalyzer path
- CurrentEventCapsule / DailyBrainContext contracts
- regression tests

PR-B — Offline Semantic Brain V2 compiler
- semantic units/capsules
- SynthesizedMechanismClaim
- complete recursive reduce
- BrainPackage

PR-C — One-time full 823,279 build
- existing records/embeddings 재사용
- 새 immutable brain package
- external audit

PR-D — Real daily-path evaluation
- A/B/C
- calibration/holdout/post-cutoff
- same single-call product path
```

PR-A가 통과하기 전에 offline full build를 시작하지 않는다.

---

# 11. 즉시 중단할 현재 작업

현재 실행 중인 작업이 다음 특성을 가지면 checkpoint를 보존하고 취소한다.

```text
하루당 hundreds of gpt-5.6-sol calls
historical raw records를 16개 batch로 daily map
material cluster마다 별도 historical LLM digest
daily 379-call 계획
evaluation-only heavy path가 실제 product path와 다름
```

해당 결과를 final brain 또는 quality evidence로 승격하지 않는다.

---

# 12. 완료 판정

다음을 따로 보고한다.

```text
OFFLINE_BRAIN_BUILT
DAILY_PRODUCT_PATH_IMPLEMENTED
DAILY_CALL_GRAPH_BOUNDED
HISTORICAL_RAW_DAILY_REMAP_ZERO
BRAIN_MEMORY_ACTUALLY_USED
PREDICTIVE_QUALITY_EVALUATED
PRODUCTION_ACTIVATED
```

서로 대신 사용하지 않는다.

이번 Goal의 핵심 성공조건:

```text
research corpus 해석 비용은 one-time offline으로 이동
daily inference는 이미 구축된 brain package를 조회
normal daily agent calls = 1
daily historical record LLM map calls = 0
daily import/rebuild = 0
최종 섹터·종목 판단은 capsule/claim/population/provenance를 실제 사용
```

---

# 13. 최종 보고

반드시 다음을 보고한다.

```text
1. 기존 daily LLM call graph
2. 제거한 per-cluster/per-record LLM call sites
3. 새 daily normal/max call count
4. CurrentEventCapsule count/bytes
5. DailyBrainContext count/bytes
6. historical raw witness count
7. offline semantic unit/capsule count
8. SynthesizedMechanismClaim count
9. one-time offline LLM calls/tokens/time
10. daily calls/tokens/time
11. record/import/embedding 재사용 여부
12. A/B/C quality metrics
13. future/web/full-scan findings
14. commit/PR/CI
15. production activation 상태
```

“매일 raw 원료를 다시 읽는 것이 더 깊은 분석”이라는 해석은 금지한다.

깊은 historical 해석은 offline brain build에서 끝내고, daily에는 그 해석 결과를 사용한다.
