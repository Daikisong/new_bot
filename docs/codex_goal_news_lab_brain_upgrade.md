# Codex Goal — `new_bot`를 실제 LLM-native 뉴스 스캘핑 연구 두뇌로 완성

당신은 `Daikisong/new_bot` 저장소의 수석 아키텍트이자 구현 담당자다.

이번 작업은 설계 문서만 작성하는 작업이 아니다. 현재 저장소의 코드를 실제로 조사하고, 기존에 잘 작동하는 BLIND/POSTMORTEM·미래정보 누수 방지·provenance·hardcoding audit 구조는 보존하면서, 현재 빠져 있는 **연구 번들 수입 → record-level 장기 기억 → LLM 연구 두뇌 컴파일 → 일일 분석 컨텍스트 → 학습 데이터 export** 연결부를 실제 코드와 테스트로 완성하라.

질문을 되묻지 말고 합리적인 기본값으로 끝까지 진행한다. 계획만 제출하고 멈추지 않는다. 구현, 마이그레이션, smoke test, 전체 테스트, 문서 갱신까지 수행하고 실패를 수정한 뒤 최종 결과를 보고한다.

---

## 0. 프로젝트의 최종 목적

이 저장소의 목적은 뉴스 키워드를 코드에 하드코딩해 종목을 찾는 규칙 엔진이 아니다.

최종 구조는 다음이어야 한다.

```text
GPT Pro 날짜별 연구 bundle
→ immutable raw 보존
→ schema/version-aware import
→ brain_delta record를 손실 없이 정규화
→ record-level warehouse·retrieval·company/market memory
→ 여러 날짜의 성공·실패·반례를 LLM이 종합한 버전형 research brain
→ 새 뉴스 CSV의 open-world 분석
→ 추가 웹 조사
→ 과거 연구 두뇌 + 성공·실패·반례 활용
→ 주도섹터·단일뉴스 후보·수혜주 후보·연속성 후보 출력
```

연구자료를 코드의 `if/else`, 종목 whitelist, 지역→종목 dictionary, 뉴스 문구→고정 점수표로 번역하지 않는다.

새로운 정책, 새로운 지역, 새로운 산업, 처음 보는 회사가 등장해도 source code 수정 없이 LLM이 인과 메커니즘을 추론하고 과거 연구를 참고해야 한다.

---

## 1. 현재 저장소에서 반드시 재현·확인할 문제

구현 전에 현재 코드를 직접 조사하고 아래 현상을 재현하라. 사실과 다르면 실제 코드 기준으로 수정하되, 확인 없이 무시하지 마라.

### 1.1 현재 BrainCompiler가 실질적 두뇌가 아님

현재 compiler는 accepted episode에서 일반적인 claim 문장을 만들고, 여러 brain Markdown 파일에 거의 동일한 claim 목록을 반복해 쓰는 deterministic catalog에 가깝다.

다음을 검사한다.

```text
brain/current/00_world_model.md
brain/current/01_single_event_patterns.md
brain/current/02_theme_formation_patterns.md
brain/current/03_beneficiary_discovery.md
brain/current/04_leader_selection.md
brain/current/05_continuation_patterns.md
brain/current/06_failure_modes.md
brain/current/07_counterexamples.md
brain/current/08_market_memory.md
```

현재 파일들이 제목 외에는 실질적으로 동일하거나 category-specific synthesis가 없다면 이를 문제로 기록한다.

### 1.2 최신 연구 bundle과 현재 `ResearchEpisode` 모델이 호환되지 않음

최신 연구 산출물은 대략 다음 버전을 사용한다.

```text
nslab.research_bundle.v11
nslab.bundle_manifest.v11
nslab.research_episode.v11
```

최신 episode에는 다음과 같은 rich 구조가 포함된다.

```text
previous_trade_date
next_trade_date
window_start
bundle_status
research_daily_source
entity_quality_summary
fact_quality_summary
candidate_screening_summary
entity_resolution_summary
winner_census
issuer_day_cases
direct_event_cases
theme_formation_cases
blind_leader_pairs
negative_controls
brain_delta_summary
id_registry_summary
validation_summary
...
```

기존 `ResearchEpisode` v1에 억지로 집어넣거나 extra field를 버리지 마라.

### 1.3 `brain_delta.jsonl`이 검증만 되고 실제 기억에서 사라질 수 있음

현재 bundle parser가 `brain_delta.jsonl`의 hash와 구조를 검증하더라도, 최종 반환값이 구형 `ResearchEpisode` 하나뿐이면 rich record가 저장·warehouse·retrieval·training에 연결되지 않는다.

다음 record type은 절대 유실되면 안 된다.

```text
supervised_issuer_day_case
supervised_direct_event_case
supervised_theme_formation_case
beneficiary_discovery_case
blind_leader_preference_pair
candidate_generation_error_case
candidate_ranking_error_case
row_disposition_error_case
entity_resolution_error_case
memory_claim
mechanism_memory
counterexample
event_ticker_edge
company_memory_delta
research_question
```

`training_eligible=false`인 record도 삭제하지 않는다. 정식 학습에서는 제외하되 audit/hypothesis 기억으로 보존한다.

### 1.4 현재 training export와 warehouse가 rich record를 사용하지 않음

현재 training export가 `blind_analysis`, `blind_predictions`, generic postmortem만 다시 조합하고, explicit `brain_delta` record를 무시하는지 확인한다.

현재 warehouse가 다음만 저장하고 rich supervised record table을 만들지 않는지 확인한다.

```text
events
event_sources
event_ticker_edges
research_episodes
daily_outcomes
predictions
market_memory
mechanism_memory
company_memory
```

### 1.5 episode coverage와 research knowledge coverage를 혼동함

accepted episode 100% sweep는 episode 안의 rich record가 먼저 보존되었을 때만 의미가 있다.

앞으로는 다음을 별도로 측정해야 한다.

```text
accepted_episode_count
accepted_record_count
available_record_count_as_of
training_eligible_record_count_as_of
swept_record_count
unswept_record_ids
record_count_by_type
record_count_by_evidence_phase
```

---

## 2. 절대 보존할 기존 기능

다음은 갈아엎지 말고 회귀 없이 유지한다.

```text
BLIND와 OUTCOME/POSTMORTEM의 물리적·논리적 분리
cutoff 이후 정보 차단
D 가격 lookahead 차단
research_daily P/D snapshot 사용
bundle marker/hash/ID/source/provenance 검증
row disposition coverage
entity binding audit
corporate action quarantine
issuer-day 중복 방지
hardcoding audit
available_from 시간 규칙
immutable raw research 보존
CLI와 기존 테스트의 하위호환
```

`docs/research_prompt.md`와 `stock-web`의 연구 가격 접근층을 이번 작업에서 임의로 다시 설계하지 마라. 이번 목표는 `new_bot` 내부의 수입·기억·두뇌·분석·training 연결부다.

---

## 3. 버전형 Bundle Import 아키텍처

### 3.1 구형 단일 `ResearchEpisode` 강제 변환을 중단

다음 모델 계층을 구현한다.

```text
LegacyResearchEpisodeV1
ResearchBundleEnvelope
NormalizedEpisodeIndex
BrainRecordEnvelope
```

권장 의미:

```python
ResearchBundleEnvelope:
    bundle_schema_version
    manifest_schema_version
    episode_schema_version
    episode_id
    trade_date
    cutoff_at
    available_from
    bundle_status
    blind_valid
    raw_bundle_sha256
    raw_block_hashes
    raw_block_counts
    raw_block_paths
    normalized_episode_index
    record_manifest
    provenance_closure_status
```

`NormalizedEpisodeIndex`는 검색·정렬·감사용 최소 메타데이터만 가진다. 최신 `research_episode.json` 전체를 구형 모델로 손실 변환하지 않는다. 원본 JSON은 immutable하게 보존한다.

### 3.2 Version Adapter Registry

다음 protocol을 구현한다.

```python
class BundleVersionAdapter(Protocol):
    def supports(self, envelope_metadata) -> bool: ...
    def validate(self, parsed_bundle) -> ValidationResult: ...
    def normalize_episode_index(self, parsed_bundle) -> NormalizedEpisodeIndex: ...
    def normalize_brain_records(self, parsed_bundle) -> list[BrainRecordEnvelope]: ...
```

최소 adapter:

```text
LegacyV1Adapter
V10Adapter
V11Adapter
```

미지원 미래 버전은 조용히 필드를 버리지 말고:

```text
UNSUPPORTED_BUNDLE_VERSION
```

으로 quarantine한다.

단, 공통 envelope를 읽을 수 있고 record payload를 raw 보존할 수 있는 경우 `forward_compatible_raw_only` 상태를 지원한다.

### 3.3 Latest v11 실제 계약

v11 adapter는 다음을 지켜야 한다.

```text
bundle_status == ACCEPT_FULL 또는 명시적 허용상태
blind_valid == true
validator_exit_code == 0
critical_error_count == 0
manifest·block hash 일치
ID·source reference closure 통과
brain_delta record count와 manifest count 일치
training_eligible count 일치
available_from 검증
```

`bundle_manifest.v1`로 하드코딩된 검사를 version-aware하게 바꾼다.

---

## 4. Canonical Brain Record 모델

### 4.1 엄격한 공통 envelope + record별 payload

다음 공통 모델을 구현한다.

```python
BrainRecordEnvelope:
    schema_version
    record_id
    record_type
    episode_id
    trade_date
    available_from
    training_target
    evidence_phase
    training_eligible
    eligibility_reason
    status
    confidence_label
    provenance_source_ids
    raw_payload_sha256
    normalized_payload_sha256
    payload
```

공통 envelope는 strict validation을 사용한다.

`payload`는 record type별 typed model을 우선 사용하되, 새로운 record type의 원문을 잃지 않도록 raw JSON도 함께 보존한다.

### 4.2 known record type typed models

최소 다음 typed payload를 만든다.

```text
SupervisedIssuerDayCase
SupervisedDirectEventCase
SupervisedThemeFormationCase
BeneficiaryDiscoveryCase
BlindLeaderPreferencePair
CandidateGenerationErrorCase
CandidateRankingErrorCase
RowDispositionErrorCase
EntityResolutionErrorCase
MemoryClaimRecord
MechanismMemoryRecord
CounterexampleRecord
EventTickerEdgeRecord
CompanyMemoryDeltaRecord
ResearchQuestionRecord
```

각 record의 중요 필드를 보존한다.

예:

```text
issuer_day_case_id
event_ids
observation_ids
fact_ids
inference_ids
safe_D1_features
outcome
response_class
sample_weight
label_quality
attribution_status
theme_id
peer universe
chosen/rejected candidate
blind preference
outcome winner
correction mode
```

### 4.3 unknown record type 정책

알 수 없는 record type을 버리지 않는다.

```text
known envelope valid + unknown payload
→ store as UNKNOWN_TYPED_PAYLOAD
→ training_eligible 강제 false
→ audit에서 표시
→ raw payload 보존
```

---

## 5. Immutable Record Store

다음 구조를 구현한다.

```text
data/raw/research/
research/episodes/<episode_id>/
  original_bundle.md
  bundle_envelope.json
  normalized_episode_index.json
  raw_blocks/
  validation_report.json
memory/records/<episode_id>.jsonl
memory/record_manifests/<episode_id>.json
memory/record_index/
```

원칙:

```text
원본 bundle 수정 금지
record_id 전역 유일
동일 episode 재수입은 content hash로 idempotent
같은 episode_id인데 hash가 다르면 conflict quarantine
training_eligible=false record도 보존
모든 normalized record에서 raw source line 또는 block offset 추적
```

---

## 6. Record-level Warehouse

기존 warehouse를 유지하면서 다음 파일을 추가한다.

```text
warehouse/brain_records.parquet
warehouse/issuer_day_cases.parquet
warehouse/direct_event_cases.parquet
warehouse/theme_formation_cases.parquet
warehouse/beneficiary_cases.parquet
warehouse/leader_pairs.parquet
warehouse/error_cases.parquet
warehouse/memory_claims.parquet
warehouse/research_questions.parquet
warehouse/record_provenance.parquet
warehouse/record_coverage.parquet
```

필수 조건:

```text
brain_records row count == normalized record count
record_id unique
episode_id join complete
training_eligible 정확히 반영
record_type별 specialized table count 합리적 일치
issuer_day_case_id + trade_date + ticker 중복 검사
event-level sample weight 합 검사
available_from as-of filter 지원
```

DuckDB에서 record-level 질의를 직접 수행할 수 있어야 한다.

---

## 7. Brain Compiler를 실제 LLM 연구 컴파일러로 교체

### 7.1 기존 deterministic compiler는 catalog mode로 보존

현재 compiler를 삭제하지 말고 다음 이름으로 분리한다.

```text
catalog
```

용도:

```text
unit test
offline smoke
LLM provider 장애 시 감사용 catalog
```

catalog 결과를 production research brain으로 표시하지 않는다.

### 7.2 production compiler mode

다음 mode를 구현한다.

```text
llm-full
```

`nslab brain rebuild`의 production 기본은 `llm-full`이다.

실 LLM provider가 설정되지 않았거나 mock provider라면:

```text
production brain rebuild 실패
```

해야 한다.

명시적:

```text
--mode catalog --allow-catalog
```

일 때만 deterministic catalog를 허용한다.

### 7.3 category-specific synthesis

아래 9개 파일은 서로 다른 record population과 prompt를 사용해야 한다.

```text
00_world_model.md
01_single_event_patterns.md
02_theme_formation_patterns.md
03_beneficiary_discovery.md
04_leader_selection.md
05_continuation_patterns.md
06_failure_modes.md
07_counterexamples.md
08_market_memory.md
```

routing은 산업명·지역명·종목명 하드코딩이 아니라 다음 구조적 필드로 한다.

```text
record_type
training_target
evidence_phase
path_type
response_class
attribution_status
error type
theme/leader relation
```

예:

```text
supervised_direct_event_case
→ single event evidence

supervised_theme_formation_case
→ theme formation evidence

blind_leader_preference_pair
→ leader selection evidence

candidate_generation_error_case
→ failure modes

counterexample
→ counterexamples
```

### 7.4 LLM map-reduce compilation

한 번에 모든 원본을 넣지 않는다.

```text
record shards
→ shard evidence summary
→ category evidence synthesis
→ contradiction/boundary review
→ final category brain
→ global world model
```

각 shard output은 cache하고 immutable하게 버전 관리한다.

cache key:

```text
record IDs + record hashes
brain compiler prompt hash
model/provider config
compiler version
```

### 7.5 Brain claim contract

최종 claim은 최소 다음을 가진다.

```python
CompiledBrainClaim:
    claim_id
    category
    statement
    mechanism
    scope
    conditions
    boundary_conditions
    failure_modes
    supporting_record_ids
    contradicting_record_ids
    supporting_episode_ids
    contradicting_episode_ids
    positive_case_count
    negative_case_count
    near_miss_count
    confidence_label
    status
    available_from
    provenance
```

한 episode만으로 `validated` 금지.

status lifecycle:

```text
tentative
supported
validated
disputed
retired
```

상태 승격은 configurable generic evidence policy와 LLM synthesis를 함께 사용한다. 도메인별 키워드 점수표는 금지한다.

### 7.6 Brain content diversity audit

다음 검사를 추가한다.

```text
9개 brain 파일 byte-identical 금지
제목만 다른 동일본문 금지
각 category의 source record type 분포 기록
각 claim에 최소 1개 supporting record ID
반례가 있는 claim은 contradiction 표시
```

파일 간 일정한 공통 핵심 원칙은 허용하지만, category-specific 내용과 evidence가 반드시 달라야 한다.

---

## 8. Record-level Research Coverage

### 8.1 episode coverage 외에 record coverage 추가

다음 manifest를 생성한다.

```text
brain/current/record_coverage_manifest.json
```

필드:

```text
accepted_episode_count
accepted_record_count
available_record_count
training_eligible_available_record_count
compiled_record_count
swept_record_count
unswept_record_ids
record_counts_by_type
record_counts_by_evidence_phase
record_counts_by_training_target
ineligible_record_count
audit_only_record_count
coverage_complete
```

### 8.2 exhaustive mode

일일 분석의 exhaustive mode에서는 `available_from <= as_of`인 모든 대상 record가 최소 한 번의 shard LLM context에 포함되어야 한다.

```text
swept_record_count == available_record_count
```

이 아니면 실행 실패다.

수천·수만 record를 final call 하나에 넣는 것이 아니라:

```text
all record shards map pass
+ current category brain
+ selected raw cases
+ final synthesis
```

구조로 구현한다.

episode를 한 번 봤다는 이유로 그 episode의 rich records를 모두 본 것으로 계산하지 않는다.

---

## 9. Retrieval을 record level로 확장

FTS와 embedding index의 기본 단위를 episode만이 아니라 record로 확장한다.

지원 필터:

```text
record_type
training_target
trade_date range
available_from
ticker
company_name
theme_id
path_type
response_class
training_eligible
evidence_phase
confidence_label
```

현재 뉴스 분석 시 retrieval은 다음 묶음을 함께 반환한다.

```text
positive analogs
negative controls
near misses
counterexamples
leader-selection pairs
theme formation failures
candidate generation errors
```

exact keyword hit가 없다는 이유로 후보를 버리지 않는다.

일일 분석은 항상:

```text
현재 뉴스 open-world first pass
→ memory retrieval/sweep
```

순서다.

---

## 10. Daily Analyzer 연결

기존 analyzer의 BLIND·시간 가드는 유지한다.

최종 synthesis context에 다음을 포함한다.

```text
current news open-world analysis
current category brain
record-level shard sweep outputs
retrieved positive records
retrieved negative/counterexample records
company memory as-of
market memory as-of
web research
red-team
safe D-1 context
```

context manifest에 다음을 기록한다.

```text
brain_version
compiler_mode
brain compiler model/provider
brain file hashes
accepted_record_count
available_record_count
swept_record_count
swept_record_ids
retrieved_record_ids
counterexample_record_ids
record shard artifacts
truncations
```

---

## 11. Training Export를 rich record 기반으로 교체

### 11.1 generic 재생성 금지

현재처럼 `blind_predictions` 전체를 다시 조합해 pair를 만들거나 generic lesson을 만드는 방식을 production training source로 사용하지 않는다.

다음 source of truth를 사용한다.

```text
supervised_issuer_day_case
supervised_direct_event_case
supervised_theme_formation_case
blind_leader_preference_pair
명시적으로 eligible인 error/correction records
```

### 11.2 issuer-day weight

반드시 검증한다.

```text
동일 trade_date+ticker issuer-day sample 총 weight == 1
해당 issuer-day의 event-level weights 합 == 1
중복 issuer-day 학습표본 0
```

### 11.3 preference export

모든 positive candidate × 모든 negative candidate cross product를 만들지 않는다.

오직 봉인된:

```text
blind_leader_preference_pair
```

record를 사용한다.

필드 분리:

```text
blind_preferred_ticker
blind_rejected_ticker
outcome_winner_ticker
blind_preference_correct
training_mode = positive_preference | correction
```

### 11.4 phase 분리

```text
BLIND-safe SFT
POSTMORTEM correction
preference
eval
audit-only
```

를 별도 파일로 내보낸다.

`training_eligible=false` record는 training export에서 제외하지만 skip manifest에 reason과 record ID를 남긴다.

### 11.5 export manifest

최소:

```text
source_episode_count
source_record_count
eligible_record_count
exported_record_count
skipped_record_count
counts_by_record_type
counts_by_training_target
duplicate_issuer_day_count
weight_validation_status
source hashes
```

---

## 12. Company/Market Memory

`company_memory_delta`를 시간 유효성 기반으로 적용한다.

```text
known_at / available_from 이후에만 사용
미래 사업관계 과거 소급 금지
상충 관계 보존
기존 목록에 없는 회사도 신규 memory 생성 가능
```

`event_ticker_edge`는 다음을 분리한다.

```text
DIRECT
FUNDAMENTAL
MARKET_MEMORY
CONTINUATION
INFERRED_NEW
```

outcome-only association은 cutoff provenance 없이는 production beneficiary memory로 승격하지 않는다.

---

## 13. Production Provider Guard

현재 config의 mock provider는 테스트에는 유지한다.

하지만 다음은 금지한다.

```text
mock LLM으로 production brain/current 승격
mock embedding으로 production semantic index 승격
mock web provider 결과를 실증 근거로 표시
```

`nslab doctor --production`을 구현해 다음이면 실패한다.

```text
LLM provider mock
brain compiler mode catalog
brain current manifest가 catalog_only
record coverage incomplete
latest brain audit 실패
```

---

## 14. Migration

### 14.1 기존 자료 보존

현재의 accepted demo episodes와 brain snapshots를 삭제하지 않는다.

```text
legacy/demo
catalog_only
```

상태로 표시한다.

현재 `brain/current`은 새 production brain이 deep audit를 통과하기 전까지 유지한다.

### 14.2 최신 bundle 수입

다음 위치를 순서대로 탐색한다.

```text
data/inbox/research/
tests/fixtures/research_bundles/
환경변수 NSLAB_REAL_BUNDLE_PATH
CLI 인자 경로
```

실제 v11 ACCEPT_FULL bundle이 제공되면 다음 parity를 검사한다.

예시 기준:

```text
brain_delta total: 327
training eligible: 325
supervised issuer-day: 150
supervised direct-event: 171
supervised theme: 3
blind leader pair: 3
```

이 숫자는 fixture가 해당 2026-06-22 bundle일 때만 적용한다. 다른 bundle에 하드코딩하지 않는다.

실제 bundle이 없으면 숫자를 조작하거나 가짜 real smoke를 만들지 않는다. 대신 최소 synthetic v11 fixture를 만들고 real smoke가 pending임을 명확히 보고한다.

### 14.3 새 brain 승격

다음 조건을 모두 통과해야 `brain/HEAD`와 `brain/current`을 교체한다.

```text
bundle import 성공
record count/hash parity
record coverage 100%
llm-full compiler
category diversity audit
provenance closure
lookahead audit
hardcoding audit
all tests pass
```

---

## 15. CLI

기존 명령을 깨뜨리지 않으면서 다음을 추가·정비한다.

```bash
nslab research import-bundle <path> --validate --accept
nslab research inspect-bundle <path>
nslab research migrate-legacy

nslab memory inspect --episode <episode_id>
nslab memory inspect-record <record_id>
nslab memory stats
nslab memory audit --deep

nslab warehouse rebuild
nslab warehouse verify

nslab brain rebuild --mode llm-full
nslab brain rebuild --mode catalog --allow-catalog
nslab brain update --episode <episode_id> --mode llm-full
nslab brain audit --deep
nslab brain diff <version_a> <version_b>

nslab analyze ... --mode exhaustive
nslab context inspect <run_id>

nslab training export-sft
nslab training export-preference
nslab training export-evals
nslab training audit

nslab doctor --production
```

---

## 16. Audits

다음 deep audit를 구현한다.

### 16.1 Import loss audit

```text
raw brain_delta count
normalized record count
record ID set equality
training eligible count equality
record type count equality
raw payload hash equality
```

하나라도 불일치하면 accept 금지.

### 16.2 Record provenance audit

```text
모든 eligible record의 provenance source 존재
모든 fact/inference/event 참조 존재
available_from 유효
outcome label quality 유효
```

### 16.3 Brain evidence audit

```text
모든 compiled claim에 support record 존재
존재하지 않는 record ID 참조 0
contradiction 누락 0
한 episode만으로 validated claim 0
```

### 16.4 Context coverage audit

```text
exhaustive available record count == swept record count
unswept record 0
future available_from record 노출 0
```

### 16.5 Training audit

```text
ineligible record export 0
issuer-day duplicate 0
event weight sum mismatch 0
sealed pair 외 preference 생성 0
BLIND/POSTMORTEM phase 혼합 0
```

### 16.6 Brain diversity audit

```text
9개 category file 동일본문 0
category source population mismatch 0
empty category인데 complete 선언 0
```

---

## 17. Tests

기존 테스트를 모두 유지하고 다음 테스트를 추가한다.

```text
test_v11_bundle_imports_without_legacy_schema_failure
test_v11_brain_delta_is_not_discarded
test_bundle_record_count_and_hash_parity
test_training_eligible_count_parity
test_unknown_bundle_version_quarantined_without_data_loss
test_unknown_record_type_raw_payload_preserved
test_legacy_v1_episode_still_supported

test_record_store_idempotent
test_episode_hash_conflict_quarantined
test_record_level_available_from_filter
test_record_provenance_closure

test_warehouse_brain_record_counts
test_issuer_day_unique_and_weight_one
test_event_weights_sum_to_one

test_llm_full_requires_real_provider
test_catalog_brain_marked_catalog_only
test_brain_category_files_are_not_identical
test_compiled_claims_reference_existing_records
test_single_episode_cannot_validate_rule
test_full_rebuild_from_raw_is_reproducible_with_cache

test_exhaustive_record_coverage_100_percent
test_retrieval_miss_does_not_block_open_world_candidates
test_negative_and_counterexample_retrieval

test_training_export_uses_explicit_brain_records
test_preference_export_uses_sealed_pairs_only
test_ineligible_records_not_exported
test_blind_postmortem_exports_separated

test_company_memory_respects_known_at
test_no_future_record_in_historical_context
test_hardcoding_audit_still_passes
test_lookahead_audit_still_passes
```

실제 v11 bundle 경로가 제공되면 integration test에서 실제 count parity도 수행한다.

---

## 18. Diagnostics

다음을 생성한다.

```text
diagnostics/bundle_import_report.json
diagnostics/bundle_import_report.md
diagnostics/brain_record_store_report.json
diagnostics/brain_record_store_report.md
diagnostics/record_coverage_report.json
diagnostics/record_coverage_report.md
diagnostics/brain_compile_report.json
diagnostics/brain_compile_report.md
diagnostics/training_export_report.json
diagnostics/training_export_report.md
diagnostics/migration_report.json
diagnostics/migration_report.md
```

보고 내용:

```text
bundle version
episode ID
raw/normalized record counts
eligible/ineligible counts
counts by record type
dropped record count
quarantined record count
warehouse counts
brain compiler mode/provider/model
category claim counts
record coverage
unswept IDs
training export counts
all audit results
```

`dropped record count`는 반드시 0이어야 한다. 미지원 record는 quarantine 또는 raw-only 보존으로 계산한다.

---

## 19. README·AGENTS.md

README에 실제 흐름을 명확히 작성한다.

```text
research bundle
→ import/validate
→ record store
→ warehouse
→ brain llm-full compile
→ brain audit
→ daily analyze
→ training export
```

`AGENTS.md`에는 기존 원칙을 유지하며 다음을 추가한다.

```text
- brain_delta is a first-class source of truth and must never be discarded.
- production brain must be compiled in llm-full mode.
- episode coverage is not record coverage.
- training exports must originate from explicit eligible records.
- unknown versions or record types must be preserved or quarantined, never silently dropped.
```

---

## 20. 실제 실행 순서

구현만 하고 멈추지 말고 다음을 실제 수행한다.

```text
1. 현재 repo 구조·코드·tests 조사
2. 문제 재현 및 짧은 implementation plan 작성
3. versioned bundle adapter 구현
4. canonical BrainRecordEnvelope 구현
5. immutable record store 구현
6. warehouse record tables 구현
7. training exporter 전환
8. record-level retrieval/context coverage 구현
9. catalog/llm-full compiler 분리
10. category-specific LLM compiler 구현
11. production provider guard 구현
12. migration 구현
13. audits/tests 구현
14. README/AGENTS 갱신
15. synthetic v11 fixture import
16. 실제 v11 bundle이 있으면 real smoke import
17. warehouse rebuild
18. llm-full brain rebuild
19. deep brain audit
20. training exports 및 audit
21. pytest -q
22. ruff check .
23. mypy src
24. 실패 수정
25. 최종 결과 보고
```

실제 LLM API key가 없어 llm-full 실행을 못 하면:

```text
구현과 mock/cached integration test는 완료
production brain 승격은 하지 않음
catalog_only 상태 유지
필요 환경변수와 exact command 보고
```

한다. mock 결과를 production brain으로 위장하지 않는다.

---

## 21. 완료 기준

다음이 모두 충족되어야 완료다.

```text
- latest v11 bundle schema를 손실 없이 import
- brain_delta raw record가 모두 보존
- normalized record count와 raw count 일치
- training eligible count 일치
- rich record가 warehouse/retrieval/training에 연결
- current 9개 brain 파일이 category-specific
- production brain compiler가 실제 LLM synthesis 사용
- catalog mode가 production으로 오인되지 않음
- exhaustive mode record coverage 100%
- retrieval miss가 후보 생성 gate가 아님
- explicit sealed pair만 preference 학습
- issuer-day 중복과 weight 검증 통과
- available_from와 lookahead audit 통과
- hardcoding audit 통과
- legacy data 보존
- unsupported data silent drop 0
- pytest, ruff, mypy 통과
```

---

## 22. 최종 응답 방식

최종 응답에는 다음을 구체적으로 보고한다.

```text
1. 발견한 실제 기존 문제
2. 변경한 아키텍처
3. 추가·수정한 파일
4. bundle version compatibility
5. 실제 또는 synthetic v11 import 결과
6. raw/normalized/eligible record count
7. dropped/quarantined record count
8. warehouse table count
9. brain compiler mode와 provider
10. 9개 category brain의 차별화 결과
11. exhaustive record coverage 결과
12. training export 결과
13. migration 결과
14. pytest/ruff/mypy 결과
15. production 실행 명령
16. API key 등 남은 외부 의존성
```

계획만 말하지 말고 실제 구현·마이그레이션·검증 결과를 제출한다.
