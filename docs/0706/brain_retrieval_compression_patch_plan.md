# Brain Retrieval Compression Patch Plan

작성일: 2026-07-06

목적: 새 뉴스 CSV를 넣었을 때 오늘 뉴스 cluster가 두뇌 검색으로 넘어가기 전에 LLM 판단으로 누락되지 않도록, 현재 실제 코드 흐름을 점검하고 바로 구현 가능한 패치 계약을 정리한다.

## 결론

현재 구조는 다음 부분은 안전하다.

- `row_disposition.jsonl`은 전체 CSV row를 포함/제외 상태로 남긴다.
- `event_clusters.jsonl`은 LLM이 고르는 것이 아니라 코드가 deterministic하게 만든다.
- `news_novelty_review`는 LLM이 cluster를 빼먹어도 normalize 단계에서 fallback finding을 추가해 `cluster_count == reviewed_cluster_count`가 되게 한다.
- `exhaustive` 모드는 cutoff 시점에 사용 가능한 accepted episode와 brain record 전체를 sweep하고, `available_record_ids == swept_record_ids`가 아니면 실패한다.

하지만 현재 구조에는 중요한 구멍이 있다.

- `semantic_retrieval_plan`은 모든 event cluster별 query 생성을 hard contract로 강제하지 않는다.
- `candidate_expansion` fallback은 모든 cluster가 아니라 앞 3개 cluster만 `related_cluster_ids`에 연결한다.
- 따라서 LLM이 novelty/directness 단계에서 어떤 cluster를 낮게 보거나 query 계획에서 빠뜨리면, 그 cluster가 두뇌 검색 입력으로 충분히 연결되지 않을 수 있다.

고쳐야 할 핵심 원칙:

```text
오늘 뉴스 cluster는 두뇌 검색 전에 중요도 판단으로 버리면 안 된다.
모든 included event cluster는 최소 한 번 이상 semantic retrieval 입력이 되어야 한다.
모든 included event cluster는 candidate expansion 또는 retrieval coverage audit에 연결되어야 한다.
news_novelty_review는 filter/gate가 아니라 label/provenance metadata로만 사용한다.
```

## 현재 실제 흐름

파일: `src/news_scalping_lab/inference/analyzer.py`

현재 `DailyAnalyzer.analyze()` 핵심 순서:

```text
1. CSV load
2. open_world_first_analysis
3. initial retrieval search_semantic/search_records limit=20
4. ContextAssembler.assemble()
5. row_disposition.jsonl 작성
6. event_clusters.jsonl 작성
7. cutoff-safe web source 수집
8. news_novelty_review 작성
9. MemorySweeper.sweep()
10. exhaustive coverage 검사
11. semantic_retrieval_plan 작성
12. semantic_retrieval.jsonl 작성
13. candidate_expansion 작성
14. blind prediction
15. candidate web checks
16. red team
17. final synthesis
18. blind seal/output
```

### 1. row_disposition

구현 위치:

- `DailyAnalyzer._write_row_disposition_artifact()`
- `ContextManifest.row_disposition_coverage_ratio`

역할:

```text
전체 CSV row를 included/excluded로 기록한다.
기본 blind window는 D-1 15:30:00 KST ~ D 08:59:59 KST.
```

중요 판단:

```text
row_disposition은 뉴스 누락 방지의 1차 장치다.
이 단계는 LLM 판단이 아니다.
```

### 2. event_clusters

구현 위치:

- `DailyAnalyzer._write_event_cluster_artifact()`
- `_event_cluster_fingerprint()`

현재 방식:

```text
cluster_method = exact_normalized_title_body_v1
```

의미:

```text
뉴스를 의미적으로 줄이는 압축이 아니다.
같은 제목/본문 수준의 exact duplicate만 묶는다.
```

현재 artifact 필드:

```text
cluster_id
cluster_index
cluster_method
cluster_key_sha256
row_numbers
event_ids
source_ids
row_count
exact_duplicate_count
first_published_at
last_published_at_before_cutoff
representative_title_sha256
representative_body_sha256
novelty = unclear
requires_llm_novelty_review = true
```

중요 판단:

```text
event_clusters는 안전한 1차 압축이다.
하지만 query 생성을 위해 사람이 읽을 수 있는 title/body excerpt가 cluster artifact에 없다.
cluster별 semantic retrieval query를 만들려면 excerpt를 추가하는 편이 좋다.
```

### 3. news_novelty_review

구현 위치:

- `DailyAnalyzer._run_news_novelty_review()`
- `DailyAnalyzer._build_news_novelty_review_prompt()`
- `DailyAnalyzer._normalize_news_novelty_review()`
- `DailyAnalyzer._fallback_news_novelty_finding()`

현재 prompt는 다음을 요구한다.

```text
Preserve every cluster_id in the output
```

normalize 단계도 방어한다.

```python
for cluster_id, cluster_row in cluster_by_id.items():
    if cluster_id in seen_cluster_ids:
        continue
    normalized_findings.append(
        self._fallback_news_novelty_finding(...)
    )
```

중요 판단:

```text
news_novelty_review는 cluster 삭제 관문은 아니다.
LLM이 빼먹은 cluster도 fallback으로 UNCLEAR finding이 생성된다.
```

현재 부족한 점:

```text
news_novelty_review가 모든 cluster를 보존해도,
그 다음 semantic_retrieval_plan이 모든 cluster를 query로 쓰는지는 보장하지 않는다.
```

### 4. MemorySweeper

파일: `src/news_scalping_lab/context/sweep.py`

역할:

```text
cutoff 시점에 사용 가능한 accepted episode와 brain record 전체를 shard로 나눠 sweep artifact를 만든다.
```

중요 구현:

```text
available_records = records where record.available_from <= cutoff_at
swept_record_ids.extend(record_ids from every shard)
```

`exhaustive` 모드에서는 누락/중복/미래 record가 있으면 errors에 추가한다.

`DailyAnalyzer._fail_if_exhaustive_coverage_incomplete()`는 아래 조건을 검사한다.

```text
accepted_episode_count == swept_episode_count
swept_episode_ids == available accepted episode ids
available_record_count == swept_record_count
swept_record_ids == available_record_ids
```

중요 판단:

```text
전체 두뇌 record가 sweep되는 것은 맞다.
하지만 sweep은 "전체 기억 목록/요약/샤드 기여"이고,
오늘 각 cluster가 반드시 두뇌 검색 query로 연결되는 것을 보장하지는 않는다.
```

### 5. semantic_retrieval_plan

구현 위치:

- `DailyAnalyzer._run_semantic_retrieval_plan()`
- `DailyAnalyzer._build_semantic_retrieval_plan_prompt()`
- `DailyAnalyzer._normalize_semantic_retrieval_plan()`
- `DailyAnalyzer._write_semantic_retrieval_artifact()`

현재 prompt 입력:

```text
current_news
open_world_first_analysis
news_novelty_review
memory_sweep_artifacts
```

현재 required categories:

```text
positive_analogs
negative_controls
near_misses
counterexamples
leader_selection_pairs
theme_formation_failures
candidate_generation_errors
```

현재 normalize가 보장하는 것:

```text
각 required category가 최소 1개 query를 가진다.
```

현재 normalize가 보장하지 않는 것:

```text
각 event cluster가 최소 1개 query를 가진다.
각 cluster_id가 semantic_retrieval.jsonl에 추적된다.
각 cluster가 positive/negative/counterexample/error 검색을 모두 통과한다.
```

현재 `_write_semantic_retrieval_artifact()`는 query마다 다음을 수행한다.

```python
raw_episode_ids = self.retrieval.search_semantic(query.query, limit=5)
raw_record_ids = self._search_memory_records(
    query=query.query,
    limit=5,
    filters=_semantic_record_filters(query.category),
)
```

중요 판단:

```text
여기가 현재 핵심 구멍이다.
LLM이 query를 이상하게 만들거나 cluster 일부를 빼먹어도,
코드가 cluster별 coverage query를 자동 추가하지 않는다.
```

### 6. candidate_expansion

구현 위치:

- `DailyAnalyzer._run_candidate_expansion()`
- `DailyAnalyzer._normalize_candidate_expansion()`
- `DailyAnalyzer._fallback_candidate_expansion_finding()`

현재 required paths:

```text
SINGLE_EVENT
THEME_FORMATION
BENEFICIARY_DISCOVERY
CONTINUATION
```

현재 normalize가 보장하는 것:

```text
각 required path가 최소 1개 finding을 가진다.
unknown related_cluster_ids는 에러로 막는다.
```

현재 부족한 점:

```python
cluster_ids = [...][:3]
```

fallback candidate expansion은 앞 3개 cluster만 related_cluster_ids에 넣는다.

중요 판단:

```text
candidate_expansion도 모든 cluster coverage를 보장하지 않는다.
LLM이 일부 cluster를 빼먹고 fallback이 동작해도 앞 3개만 연결될 수 있다.
```

## 실제 위험 시나리오

예:

```text
뉴스 1000개
event cluster 930개
```

현재 안전한 부분:

```text
930개 cluster는 event_clusters.jsonl에 남는다.
news_novelty_review도 fallback 포함 930개 finding을 만든다.
memory sweep은 과거 record 전체를 sweep한다.
```

현재 위험한 부분:

```text
semantic retrieval query는 LLM이 만든 category별 query 몇 개일 수 있다.
그 query가 cluster 930개 중 일부 주제만 반영할 수 있다.
candidate expansion도 일부 cluster만 직접 연결할 수 있다.
```

따라서 다음 현상이 가능하다.

```text
작아 보이는 공시/특허/공급망 뉴스 cluster
-> novelty review에서 UNCLEAR 또는 낮은 중요도
-> semantic query에 직접 반영 안 됨
-> 과거 두뇌에 강한 유사 사례가 있어도 못 끌어옴
-> 후보 생성에서 누락
```

## 패치 목표

패치 목표는 LLM을 더 믿는 것이 아니라, LLM이 빼먹어도 코드가 보완하게 만드는 것이다.

필수 계약:

```text
event_cluster_count == semantic_cluster_coverage_source_count
semantic_cluster_coverage_missing_count == 0
candidate_expansion_cluster_coverage_missing_count == 0
```

## 구현 전 추가 논리검증

아래 이슈는 문서를 쓴 뒤 실제 로직을 다시 보면서 발견한 구현 위험이다. 이 부분을 반영하지 않고 바로 패치하면 다른 문제가 생긴다.

### 이슈 A: cluster coverage 검색 결과를 전부 final synthesis에 넣으면 context가 터진다

현재 final synthesis payload는 다음 함수를 통해 semantic retrieval 결과를 실제 prompt 입력에 넣는다.

```text
DailyAnalyzer._build_final_synthesis_payload()
DailyAnalyzer._read_semantic_retrieval_context()
```

현재 `_read_semantic_retrieval_context()`는:

```text
semantic_retrieval.jsonl rows 전체
manifest.semantic_retrieval_record_ids 전체 record 본문
```

를 읽어 `additional_semantic_retrieval`에 넣는다.

따라서 모든 cluster마다 broad query를 추가하고 그 결과 record id를 전부 `manifest.semantic_retrieval_record_ids`에 넣으면:

```text
cluster 1000개 * query당 5 records = 최대 5000 record ids
```

가 final synthesis payload로 들어갈 수 있다. 이건 누락은 막지만 context 폭발을 만든다.

수정 계약:

```text
cluster coverage query 실행 여부와
final synthesis에 승격할 retrieved record는 분리한다.
```

즉:

```text
semantic_cluster_coverage_rows
  = 모든 cluster가 retrieval 입력이 되었는지 감사하는 lane

semantic_retrieval_record_ids
  = final synthesis에 실제 본문을 실을, 제한된 promotion lane
```

cluster coverage query 결과는 artifact에 전부 남기되, final synthesis에는 bounded promotion만 넣는다.

권장 필드:

```text
semantic_cluster_coverage_query_count
semantic_cluster_coverage_source_count
semantic_cluster_coverage_covered_ids
semantic_cluster_coverage_missing_ids
semantic_cluster_coverage_raw_record_id_count
semantic_cluster_coverage_promoted_record_ids
semantic_cluster_coverage_promotion_limit
```

### 이슈 B: `cluster_coverage`를 required category에 섞으면 기존 semantic category 계약이 흔들린다

현재 required categories는 다음 7개다.

```text
positive_analogs
negative_controls
near_misses
counterexamples
leader_selection_pairs
theme_formation_failures
candidate_generation_errors
```

`_normalize_semantic_retrieval_plan()`은 이 category 순서로 sort한다. 여기에 `cluster_coverage`를 단순히 섞으면 기존 테스트와 summary 의미가 바뀐다.

수정 계약:

```text
cluster_coverage는 semantic required category가 아니다.
cluster_coverage는 별도 coverage lane이다.
```

따라서:

```text
SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES는 그대로 둔다.
cluster coverage는 별도 설정값 cluster_coverage_lanes로 둔다.
각 lane은 SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES 중 하나를 재사용한다.
```

### 이슈 C: cluster coverage missing은 "검색 결과 0건"이 아니다

검색 결과가 0건인 cluster가 있을 수 있다. 새롭거나 과거 사례가 없는 뉴스라면 정상이다.

따라서 hard fail 조건은:

```text
나쁜 것: cluster에 대한 coverage query row 자체가 없음
괜찮은 것: coverage query는 실행됐지만 retrieved record가 0건
```

로 구분해야 한다.

수정 계약:

```text
cluster_coverage_missing_ids
  = event_cluster_id 중 semantic_cluster_coverage.jsonl에 coverage query row가 없는 cluster

record_result_count == 0
  = 정상. record_retrieval_zero_is_valid=true와 같은 취급.
```

### 이슈 D: candidate expansion에 모든 cluster를 억지로 finding으로 넣으면 가짜 후보가 늘어난다

초안은 `_fallback_candidate_expansion_finding()`의 `[:3]`를 제거해서 모든 cluster를 `related_cluster_ids`에 넣자고 했다. 하지만 이걸 그대로 하면:

```text
후보 확장 finding 하나가 수백/수천 cluster를 물고 커짐
또는 audit-only cluster가 후보처럼 보일 수 있음
```

이라는 문제가 생긴다.

수정 계약:

```text
candidate expansion은 모든 cluster를 후보로 만들 필요가 없다.
대신 모든 cluster가 다음 둘 중 하나로 분류되어야 한다.

1. expansion finding에 실제 관련 cluster로 포함
2. audit_only_cluster_ids에 남아 "후보 확장으로 승격하지 않았지만 semantic retrieval coverage는 수행됨"으로 기록
```

권장 모델 변경:

```text
CandidateExpansionReview.covered_cluster_ids
CandidateExpansionReview.audit_only_cluster_ids
CandidateExpansionReview.uncovered_cluster_ids
```

`uncovered_cluster_ids`가 비어 있어야 한다.

### 이슈 E: StrictModel은 extra="forbid"라 schema 변경이 명시적이어야 한다

`SemanticRetrievalQuery`, `CandidateExpansionReview`, `ContextManifest`는 strict model 계열이다. 새 필드를 artifact에만 넣고 model에 추가하지 않으면 validation에서 막힌다.

수정 계약:

```text
구조화 JSON에 들어가는 새 필드는 반드시 contracts/models.py에 먼저 추가한다.
기존 artifact와 호환되도록 default_factory 또는 기본값을 둔다.
```

### 이슈 F: initial retrieval은 cluster coverage가 아니다

현재 `DailyAnalyzer.analyze()`는 event cluster 작성 전에 initial retrieval을 한다.

```text
raw_retrieved_ids = search_semantic(" ".join(web_queries), limit=20)
raw_retrieved_record_ids = search_records(..., limit=20)
```

이 retrieval은 open-world first pass 이후의 빠른 context retrieval이지, cluster coverage가 아니다.

수정 계약:

```text
initial retrieval은 그대로 보조 context로 둔다.
cluster coverage는 event_clusters + memory_sweep 이후 semantic_retrieval 단계에서 별도로 수행한다.
```

### 이슈 G: 단일 top-K cluster retrieval은 편향될 수 있으므로 lane별 상한이 필요하다

cluster가 1000개면 retrieval query도 크게 늘어난다. 다만 상한을 cluster당 단일 top 3으로 두면 semantic index가 가장 가깝다고 판단한 긍정 사례만 몰릴 수 있다. 검색 대상은 전체 brain으로 열어두되, 각 cluster에서 retrieval 목적을 lane으로 나누고 lane별 상한을 적용한다.

cluster coverage lane:

```text
positive_analogs
negative_controls
near_misses
counterexamples
leader_selection_pairs
theme_formation_failures
candidate_generation_errors
```

권장 config:

```text
limits.cluster_coverage_record_limit_per_lane: 3
limits.cluster_coverage_lanes:
  - positive_analogs
  - negative_controls
  - near_misses
  - counterexamples
  - leader_selection_pairs
  - theme_formation_failures
  - candidate_generation_errors
limits.cluster_coverage_promoted_record_limit: 360
limits.cluster_coverage_query_batch_size: 1
```

`cluster_coverage_record_limit_per_query`는 과거 config와 호환하기 위한 fallback으로만 남긴다. 정상 경로는 lane별 retrieval이다. promotion limit은 전체 10년치 brain 검색을 막는 값이 아니라, final synthesis context에 한 번에 올릴 record 본문 수를 제한하는 값이다. raw artifact에는 lane별 query, zero-result lane, excluded record까지 남겨 audit할 수 있어야 한다.

초기 구현은 batch size 1이 가장 안전하다. 나중에 성능 문제가 생기면 batch query를 별도 설계한다.

### 이슈 H: 현재 context inspect는 semantic category set을 정확히 7개로 기대한다

현재 `src/news_scalping_lab/cli.py`의 `_inspect_semantic_retrieval_plan_artifact()`는:

```text
set(query_categories) == set(expected_categories)
```

를 검사한다. 따라서 `cluster_coverage`를 일반 query category로 섞으면 strict inspect가 실패한다.

수정 계약:

```text
semantic plan inspect는 required category coverage와 coverage lane을 분리해서 검증한다.
```

검증 방식:

```text
required_category_queries = queries where coverage_query != true
coverage_queries = queries where coverage_query == true

set(required_category_queries.category) == required categories 7개
coverage_queries.category == cluster_coverage
coverage_queries.related_cluster_ids union == event_cluster_ids
```

`_inspect_semantic_retrieval_artifact()`도 현재 category counts를 전체 row 기준으로 비교한다. 따라서 summary를 다음처럼 분리한다.

```text
category_query_counts
  = 기존 required category 7개만 집계

coverage_lane_query_counts
  = cluster_coverage 등 coverage lane 집계
```

### 이슈 I: 현재 news_novelty_review inspect는 cluster id set 일치까지는 보지 않는다

`_normalize_news_novelty_review()`는 LLM이 빠뜨린 cluster를 fallback으로 보강한다. 하지만 `context inspect`는 현재 count 검증 중심이다.

추가 검증:

```text
event_clusters.jsonl cluster_id set
==
news_novelty_review.findings cluster_id set
```

또한 중복 finding도 막아야 한다.

```text
duplicate_news_novelty_cluster_ids == []
```

### 이슈 J: candidate expansion inspect는 required path 4개 set을 기대한다

현재 `_inspect_candidate_expansion_artifact()`는:

```text
set(observed_paths) == set(expected_paths)
```

를 검사한다. 따라서 audit-only cluster를 가짜 finding path로 추가하면 inspect가 깨진다.

수정 계약:

```text
audit-only cluster coverage는 CandidateExpansionFinding이 아니라
CandidateExpansionReview의 별도 필드로 둔다.
```

이렇게 해야 required path 4개 계약을 유지하면서 cluster 보존 audit를 추가할 수 있다.

### 이슈 K: final synthesis context는 semantic_retrieval artifact rows 전체 일치를 강제한다

현재 final synthesis context 검증은 다음 경로에 있다.

```text
src/news_scalping_lab/cli.py
  _inspect_final_synthesis_semantic_retrieval_context()

src/news_scalping_lab/audits/provenance.py
  _check_final_synthesis_semantic_retrieval_context()
```

둘 다 현재 구조상:

```text
context_payload.additional_semantic_retrieval.rows
==
semantic_retrieval.jsonl 전체 rows
```

를 요구한다.

따라서 cluster coverage row를 `semantic_retrieval.jsonl`에 1000개 추가하면, record 본문을 promotion limit으로 줄여도 `rows` 자체는 final synthesis context에 전부 들어간다.

수정 계약:

```text
cluster coverage audit artifact와 final synthesis semantic retrieval rows를 분리한다.
```

권장 구조:

```text
runs/checkpoints/semantic_retrieval/<RUN_ID>/semantic_retrieval.jsonl
  = 기존 required category 7개 semantic retrieval rows

runs/checkpoints/semantic_retrieval/<RUN_ID>/semantic_cluster_coverage.jsonl
  = 모든 event cluster별 coverage query/audit rows
```

manifest 필드:

```text
semantic_cluster_coverage_artifact
semantic_cluster_coverage_sha256
semantic_cluster_coverage_query_count
semantic_cluster_coverage_ids
semantic_cluster_coverage_missing_ids
semantic_cluster_coverage_promoted_record_ids
```

이렇게 하면 기존 `additional_semantic_retrieval.rows == semantic_retrieval.jsonl` 계약을 깨지 않고, cluster coverage audit를 별도 artifact로 닫을 수 있다.

final synthesis payload에는 다음만 넣는다.

```text
semantic_cluster_coverage_artifact
semantic_cluster_coverage_summary
semantic_cluster_coverage_ids
semantic_cluster_coverage_missing_ids
semantic_cluster_coverage_promoted_record_ids
```

필요하면 promoted records만 별도 `semantic_cluster_coverage_promoted_records`로 넣는다.
`additional_semantic_retrieval.records`는 기존 semantic retrieval record 계약만 유지한다.

### 이슈 L: provenance audit도 cli inspect와 같은 계약을 별도로 갖고 있다

`context inspect`만 고치면 부족하다. `src/news_scalping_lab/audits/provenance.py`에도 다음 검증이 따로 있다.

```text
_check_semantic_retrieval_plan_artifact()
_check_semantic_retrieval_artifact_summary()
_check_final_synthesis_semantic_retrieval_context()
_check_candidate_expansion_artifact()
_check_candidate_expansion_counts()
_check_news_novelty_review_artifact()
```

현재 provenance audit도 semantic retrieval category set, category counts, included record ids, final synthesis context embedded artifact 일치를 검사한다.

수정 계약:

```text
cli.py의 context inspect 패치와 provenance.py audit 패치는 항상 같은 의미로 같이 수정한다.
```

추가 provenance 검증:

```text
event cluster id set == news novelty finding cluster id set
semantic_cluster_coverage artifact hash/count/schema
semantic_cluster_coverage covered/missing ids
candidate expansion covered/audit_only/uncovered ids
final synthesis cluster coverage payload summary
```

### 이슈 M: reporting bundle/export도 manifest 필드 복사 대상이 늘어난다

`src/news_scalping_lab/reporting/bundle.py`는 analysis bundle을 만들 때 일부 manifest field를 명시적으로 복사한다.

현재 record 관련 필드는 `_copy_manifest_fields()`로 복사하지만, 새 cluster coverage 필드는 없다.

수정 계약:

```text
새 cluster coverage manifest 필드는 reporting bundle payload에도 복사한다.
```

추가 복사 대상:

```text
semantic_cluster_coverage_artifact
semantic_cluster_coverage_sha256
semantic_cluster_coverage_query_count
semantic_cluster_coverage_ids
semantic_cluster_coverage_missing_ids
semantic_cluster_coverage_promoted_record_ids
candidate_expansion_cluster_coverage_ids
candidate_expansion_audit_only_cluster_ids
candidate_expansion_uncovered_cluster_ids
```

`src/news_scalping_lab/reporting/render.py`의 preopen report는 필수 검증 경로는 아니지만, 운영 확인을 위해 summary만 표시하는 것이 좋다.

### 이슈 N: integration/unit tests 중 summary exact dict 비교가 많다

테스트에는 다음처럼 summary dict를 정확히 비교하는 케이스가 있다.

```text
tests/integration/test_analyze_e2e.py
tests/integration/test_minimum_cli_surface.py
tests/unit/test_guards_and_audits.py
tests/unit/test_reporting_bundle.py
```

따라서 summary에 새 key를 추가하면 기존 expected dict가 깨질 수 있다.

수정 계약:

```text
summary에 새 key를 추가하는 테스트는 expected dict를 함께 갱신한다.
또는 신규 coverage summary를 별도 manifest field로 분리해 기존 summary exact 비교의 파급을 줄인다.
```

권장:

```text
semantic_retrieval_summary에는 기존 의미를 최대한 유지한다.
cluster coverage는 semantic_cluster_coverage_summary 별도 field를 둔다.
candidate_expansion_summary에는 최소 counts만 추가하고, 상세 ids는 별도 field를 둔다.
```

그리고 final synthesis context에는 아래가 남아야 한다.

```text
event_cluster_ids
semantic_cluster_coverage_ids
candidate_expansion_related_cluster_ids
semantic_cluster_coverage_missing_ids
candidate_expansion_cluster_coverage_missing_ids
```

### 이슈 O: 모델 필드 변경은 tracked schema export까지 연결된다

`contracts/models.py`의 StrictModel 필드를 추가하면 런타임만 바뀌는 것이 아니다.
이 프로젝트는 JSON schema를 추적하고, scaffold/diagnostics 테스트가 schema stale 여부를 확인한다.

연결 파일:

```text
src/news_scalping_lab/contracts/schemas.py
schemas/context_manifest.schema.json
schemas/semantic_retrieval_plan.schema.json
schemas/candidate_expansion_review.schema.json
tests/unit/test_project_scaffold.py
tests/unit/test_diagnostics.py
```

수정 계약:

```text
ContextManifest, SemanticRetrievalPlan, CandidateExpansionReview 모델을 바꾸면
python -m news_scalping_lab.contracts.schemas 또는 nslab init/schema export 경로로
tracked schemas를 함께 갱신한다.
```

### 이슈 P: final synthesis required_inputs와 input_summary 호환성을 같이 봐야 한다

final synthesis payload key를 추가하면 다음 계약이 같이 움직인다.

```text
src/news_scalping_lab/context/final_synthesis.py
  FINAL_SYNTHESIS_REQUIRED_INPUTS
  PRE_RECORD_ID_FINAL_SYNTHESIS_REQUIRED_INPUTS
  LEGACY_FINAL_SYNTHESIS_REQUIRED_INPUTS
  final_synthesis_required_inputs_compatible()
  final_synthesis_input_summary()

src/news_scalping_lab/cli.py
  _inspect_final_synthesis_context_artifact()

src/news_scalping_lab/audits/provenance.py
  _check_manifest_final_synthesis_context_artifact()
```

수정 계약:

```text
semantic_cluster_coverage를 required input으로 승격할지 결정한다.
승격한다면 FINAL_SYNTHESIS_REQUIRED_INPUTS와 호환성 tuple을 같이 갱신한다.
승격하지 않는다면 payload에는 넣되 required_inputs에는 넣지 않는 이유를 명확히 남긴다.
어느 쪽이든 final_synthesis_input_summary와 manifest.final_synthesis_context_summary가
동일하게 재계산되어야 한다.
```

권장:

```text
semantic_cluster_coverage는 final synthesis의 실제 판단 입력이므로 required_inputs에 포함한다.
다만 legacy/pre-record tuple 호환성은 유지해 기존 fixture import가 깨지지 않게 한다.
```

### 이슈 Q: UI/diagnostics는 핵심 판단 경로는 아니지만 artifact 연결을 깨면 운영 확인이 약해진다

UI view model은 현재 final_synthesis_context artifact path를 보여주고,
diagnostics는 schema 상태와 final_synthesis_context 증거를 점검한다.

연결 파일:

```text
src/news_scalping_lab/ui/view_model.py
src/news_scalping_lab/diagnostics.py
tests/unit/test_ui_view_model.py
tests/unit/test_diagnostics.py
```

수정 계약:

```text
새 cluster coverage artifact를 UI 필수 다운로드 대상으로 만들 필요는 없다.
하지만 diagnostics/schema stale 검증과 final_synthesis_context summary 검증은 통과해야 한다.
운영 가시성이 필요하면 UI에는 optional artifact path로만 노출한다.
```

## 패치 1: event cluster에 query용 excerpt 추가

파일:

```text
src/news_scalping_lab/inference/analyzer.py
```

함수:

```text
_write_event_cluster_artifact()
```

추가할 필드:

```text
representative_title_excerpt
representative_body_excerpt
```

권장 길이:

```text
title: 240 chars
body: 600 chars
```

이유:

```text
현재 cluster artifact에는 title/body hash만 있다.
cluster별 retrieval query를 안정적으로 만들려면 사람이 읽을 수 있는 짧은 excerpt가 필요하다.
```

주의:

```text
이 excerpt는 BLIND cutoff-safe input CSV에서 온 것이므로 lookahead가 아니다.
source_id/event_id/row_number와 함께 남기면 provenance도 닫힌다.
```

## 패치 2: semantic retrieval에 cluster coverage query 자동 추가

파일:

```text
src/news_scalping_lab/inference/analyzer.py
src/news_scalping_lab/contracts/models.py
src/news_scalping_lab/config.py
configs/default.yaml
```

추가 설정:

```yaml
limits:
  cluster_coverage_record_limit_per_lane: 3
  cluster_coverage_lanes:
    - positive_analogs
    - negative_controls
    - near_misses
    - counterexamples
    - leader_selection_pairs
    - theme_formation_failures
    - candidate_generation_errors
```

중요:

```text
cluster coverage lane은 semantic_retrieval_plan의 required category query와 별도로 실행한다.
required category 7개 계약은 유지하고, cluster coverage는 같은 category 이름을 coverage_query=true row에서 재사용한다.
cluster_coverage는 별도 coverage lane이다.
```

수정 함수:

```text
_semantic_record_filters()
_normalize_semantic_retrieval_plan()
_write_semantic_retrieval_artifact()
```

추가 함수:

```text
_write_semantic_cluster_coverage_artifact()
_read_semantic_cluster_coverage_context()
```

수정 모델:

```text
SemanticRetrievalQuery.related_cluster_ids: list[str] = []
SemanticRetrievalQuery.coverage_query: bool = false
ContextManifest.semantic_cluster_coverage_ids: list[str] = []
ContextManifest.semantic_cluster_coverage_missing_ids: list[str] = []
ContextManifest.semantic_cluster_coverage_promoted_record_ids: list[str] = []
ContextManifest.semantic_cluster_coverage_artifact: str | None = None
ContextManifest.semantic_cluster_coverage_sha256: str | None = None
ContextManifest.semantic_cluster_coverage_query_count: int = 0
ContextManifest.semantic_cluster_coverage_summary: dict[str, Any] = {}
```

schema 주의:

```text
contracts/models.py를 바꾸면 contracts/schemas.py 경로와 생성된 schema artifact가 영향을 받을 수 있다.
기존 bundle/import schema와 직접 연결되는지 확인하고, 필요한 경우 schema snapshot도 갱신한다.
```

계약:

```text
LLM이 어떤 semantic retrieval plan을 내든,
코드는 모든 event cluster마다 cluster_coverage query를 자동 추가한다.
```

구현 위치 주의:

```text
기존 required category query normalize/sort를 먼저 끝낸다.
그 다음 cluster_coverage query를 append한다.
```

이유:

```text
현재 sort는 SEMANTIC_RETRIEVAL_REQUIRED_CATEGORIES.index(item.category)를 사용한다.
cluster_coverage를 sort 전에 섞으면 ValueError가 날 수 있다.
```

생성 query 예:

```text
cluster coverage structural analogs
cluster_id=<EVCL...>
title=<representative_title_excerpt>
body=<representative_body_excerpt>
find positive analogs, negative controls, near misses, counterexamples, and candidate generation errors
```

query metadata에 추가할 필드:

```text
related_cluster_ids: [cluster_id]
coverage_query: true
```

`_semantic_record_filters("cluster_coverage")`는 `{}`를 반환한다.

이유:

```text
cluster_coverage는 positive만 보거나 negative만 보는 검색이 아니다.
해당 cluster에 대해 모든 record type 후보를 열어두는 broad retrieval이어야 한다.
```

단, broad retrieval 결과를 모두 final synthesis에 승격하지 않는다.

```text
semantic_cluster_coverage.jsonl에는 전부 기록
manifest.semantic_cluster_coverage_*에는 coverage audit 기록
manifest.semantic_retrieval_record_ids에는 promotion limit 안의 record만 추가
```

promotion limit은 config로 둔다.

```text
limits.cluster_coverage_record_limit_per_lane
limits.cluster_coverage_lanes
limits.cluster_coverage_promoted_record_limit
```

promotion 선택 규칙:

```text
1. cluster_index 오름차순, lane 설정 순서로 cluster coverage rows를 본다.
2. 각 cluster-lane에서 included_record_ids 첫 번째를 round-robin으로 1개씩 승격한다.
3. 아직 limit이 남으면 두 번째 record를 같은 방식으로 반복한다.
4. 중복 record id는 한 번만 승격한다.
5. 기존 required semantic category로 이미 승격된 record id는 중복 승격하지 않는다.
6. zero-result lane은 실패가 아니라 "해당 방향 사례 없음"으로 artifact에 남긴다.
```

이유:

```text
search_records는 score를 반환하지 않고 rank된 id list만 반환한다.
따라서 score 기반 재정렬보다 deterministic round-robin promotion이 재현성과 cluster 균형에 더 안전하다.
```

## 패치 3: 별도 semantic cluster coverage artifact 추가

파일:

```text
src/news_scalping_lab/inference/analyzer.py
```

함수:

```text
_write_semantic_cluster_coverage_artifact()
_read_semantic_cluster_coverage_context()
```

중요:

```text
기존 semantic_retrieval_plan.json / semantic_retrieval.jsonl 계약은 건드리지 않는다.
semantic_retrieval.jsonl은 기존 required category 7개 결과만 계속 담는다.
cluster coverage 결과는 semantic_cluster_coverage.jsonl에만 담는다.
```

이유:

```text
cli.py와 provenance.py는 현재 semantic_retrieval.jsonl row 전체, category_counts,
included/excluded ids, final_synthesis_context.additional_semantic_retrieval.rows
일치를 강하게 검증한다.

cluster coverage row를 semantic_retrieval.jsonl에 섞으면 기존 검증과
final synthesis context 크기 계약이 동시에 깨질 수 있다.
```

artifact 위치:

```text
runs/checkpoints/semantic_retrieval/<RUN_ID>/semantic_cluster_coverage.jsonl
```

각 row 필드:

```text
schema_version: nslab.semantic_cluster_coverage_result.v1
run_id
cluster_id
cluster_index
query_index
query
query_sha256
related_cluster_ids
coverage_query
retrieval_lane
category
source_cluster_indices
source_event_ids
source_ids
raw_episode_ids
included_episode_ids
excluded_episode_ids
raw_record_ids
included_record_ids
excluded_record_ids
record_retrieval_filters
result_count
record_result_count
excluded_count
excluded_record_count
cutoff_at
```

주의:

```text
source_ids는 event_clusters.jsonl에서 온 source_ids만 사용한다.
cutoff_after source를 cluster coverage row에 섞지 않는다.
retrieval_lane/category는 positive_analogs, negative_controls, near_misses,
counterexamples, leader_selection_pairs, theme_formation_failures,
candidate_generation_errors 중 하나여야 한다.
```

manifest 필드:

```text
semantic_cluster_coverage_artifact
semantic_cluster_coverage_sha256
semantic_cluster_coverage_query_count
semantic_cluster_coverage_ids
semantic_cluster_coverage_missing_ids
semantic_cluster_coverage_promoted_record_ids
semantic_cluster_coverage_summary
```

semantic_cluster_coverage_summary:

```text
cluster_coverage_source_count
cluster_coverage_query_count
cluster_coverage_lane_count
cluster_coverage_lanes
cluster_coverage_record_limit_per_lane
cluster_coverage_lane_query_counts
cluster_coverage_covered_count
cluster_coverage_missing_count
cluster_coverage_missing_ids
cluster_coverage_raw_record_id_count
cluster_coverage_promoted_record_count
cluster_coverage_promoted_record_ids
cluster_coverage_promotion_limit
```

hard fail 조건:

```text
if cluster_coverage_missing_ids:
    manifest.errors.append(...)
    raise ValueError or ExhaustiveCoverageError-compatible error
```

여기서 missing의 의미:

```text
event cluster에 대한 cluster_coverage row가 없음 = fail
cluster_coverage row는 있지만 record_result_count == 0 = pass
```

권장 에러명:

```text
ClusterCoverageError
```

단, 기존 `ExhaustiveCoverageError`와 별도 클래스로 두는 편이 디버깅이 쉽다.

final synthesis 연결:

```text
additional_semantic_retrieval에는 기존 semantic_retrieval.jsonl만 유지한다.
semantic_cluster_coverage는 별도 payload key로 넣는다.
semantic_cluster_coverage rows 전체를 넣을지 여부는 config limit로 제한하고,
기본은 summary + missing_ids + promoted_record_ids + promoted_records만 넣는다.
```

## 패치 4: candidate expansion cluster coverage 보강

파일:

```text
src/news_scalping_lab/inference/analyzer.py
```

함수:

```text
_fallback_candidate_expansion_finding()
_normalize_candidate_expansion()
```

현재 문제:

```python
cluster_ids = [...][:3]
```

수정 방향:

```text
모든 cluster_id를 억지로 candidate finding에 넣지 않는다.
candidate expansion은 실제 후보 확장과 audit-only cluster coverage를 분리한다.
```

수정 모델:

```text
CandidateExpansionReview.covered_cluster_ids: list[str] = []
CandidateExpansionReview.audit_only_cluster_ids: list[str] = []
CandidateExpansionReview.uncovered_cluster_ids: list[str] = []
```

normalize 규칙:

```text
all_cluster_ids = event_clusters.jsonl의 전체 cluster_id
finding_cluster_ids = 모든 findings.related_cluster_ids union
semantic_covered_ids = semantic cluster coverage로 확인된 cluster ids

covered_cluster_ids = finding_cluster_ids
audit_only_cluster_ids = semantic_covered_ids - finding_cluster_ids
uncovered_cluster_ids = all_cluster_ids - covered_cluster_ids - audit_only_cluster_ids
```

hard fail 조건:

```text
uncovered_cluster_ids가 비어 있지 않으면 fail
```

중요:

```text
audit_only_cluster_ids는 "후보가 아니다".
그 cluster가 semantic retrieval coverage를 통과했고,
candidate expansion에서 후보로 승격되지는 않았음을 명시하는 보존 기록이다.
```

manifest summary에 추가:

```text
candidate_expansion_cluster_coverage_source_count
candidate_expansion_cluster_coverage_covered_count
candidate_expansion_cluster_coverage_missing_count
candidate_expansion_cluster_coverage_missing_ids
```

hard fail 조건:

```text
candidate_expansion_cluster_coverage_missing_count > 0
```

## 패치 5: final synthesis context에 cluster coverage 필드 추가

파일:

```text
src/news_scalping_lab/inference/analyzer.py
src/news_scalping_lab/context/final_synthesis.py
tests/unit/test_guards_and_audits.py
tests/integration/test_analyze_e2e.py
tests/integration/test_minimum_cli_surface.py
```

`_build_final_synthesis_payload()` 또는 해당 payload 구성부에 추가:

```text
event_cluster_ids
semantic_cluster_coverage_ids
semantic_cluster_coverage_missing_ids
semantic_cluster_coverage_promoted_record_ids
candidate_expansion_cluster_coverage_ids
candidate_expansion_cluster_coverage_missing_ids
candidate_expansion_audit_only_cluster_ids
```

`final_synthesis_input_summary()`에 추가:

```text
event_cluster_id_count
semantic_cluster_coverage_id_count
semantic_cluster_coverage_missing_id_count
semantic_cluster_coverage_promoted_record_id_count
candidate_expansion_cluster_coverage_id_count
candidate_expansion_cluster_coverage_missing_id_count
candidate_expansion_audit_only_cluster_id_count
```

검증:

```text
event_cluster_id_count == semantic_cluster_coverage_id_count
event_cluster_id_count == candidate_expansion_cluster_coverage_id_count
missing counts == 0
```

주의:

```text
final synthesis에 cluster coverage query 결과 record 전체를 넣지 않는다.
넣는 것은 coverage ids/counts, artifact path, bounded promoted records뿐이다.
semantic_cluster_coverage를 required_inputs에 추가하면
FINAL_SYNTHESIS_REQUIRED_INPUTS와 legacy 호환 tuple을 같이 갱신한다.
```

## 패치 6: context inspect에 cluster coverage 검증 추가

파일:

```text
src/news_scalping_lab/cli.py
```

기존 관련 inspect:

```text
_inspect_event_cluster_artifact()
_inspect_news_novelty_review_artifact()
_inspect_semantic_retrieval_plan_artifact()
_inspect_semantic_retrieval_artifact()
_inspect_candidate_expansion_artifact()
final_synthesis context inspection
```

추가할 inspect:

```text
_inspect_semantic_cluster_coverage()
_inspect_candidate_expansion_cluster_coverage()
```

검사할 것:

```text
event_clusters.jsonl의 cluster_id set
news_novelty_review.findings의 cluster_id set
semantic_cluster_coverage.jsonl의 related_cluster_ids union
candidate_expansion.json의 findings.related_cluster_ids union
candidate_expansion.json의 audit_only_cluster_ids
manifest summary의 missing ids/counts
final_synthesis_context payload의 coverage ids/counts
```

실패 메시지:

```text
news novelty review cluster id set mismatch
news novelty review duplicate cluster ids
semantic retrieval missing event cluster coverage: <ids>
candidate expansion missing event cluster coverage: <ids>
final synthesis cluster coverage drift
```

semantic retrieval inspect 수정 시 주의:

```text
기존 required category 7개 검증은 유지한다.
단, coverage_query=true인 cluster_coverage query는 required category set 비교에서 제외한다.
```

candidate expansion inspect 수정 시 주의:

```text
required path 4개 검증은 유지한다.
audit_only_cluster_ids는 findings path coverage에 섞지 않는다.
```

## 패치 7: tests 추가

파일:

```text
tests/unit/test_analysis_modes.py
```

추가 테스트 1:

```text
test_semantic_retrieval_adds_cluster_coverage_queries_for_every_event_cluster
```

시나리오:

```text
CSV row 3개, 서로 다른 title/body
LLM semantic plan은 required category 1개만 반환하거나 fallback 사용
semantic_cluster_coverage.jsonl에 event_cluster_count * cluster_coverage_lane_count row가 있어야 함
각 cluster_id마다 모든 cluster_coverage_lanes query가 있어야 함
cluster_id union == event cluster ids
manifest.semantic_cluster_coverage_summary["cluster_coverage_missing_count"] == 0
```

추가 테스트 1-1:

```text
test_cluster_coverage_zero_retrieval_result_is_valid
```

시나리오:

```text
CSV row 2개
retrieval.search_records는 모든 cluster_coverage query에서 [] 반환
cluster_coverage_missing_count == 0
record_retrieval_zero_is_valid == true
분석은 실패하지 않아야 함
```

추가 테스트 1-2:

```text
test_cluster_coverage_records_are_bounded_before_final_synthesis
```

시나리오:

```text
cluster 50개
각 cluster query가 서로 다른 record id 5개 반환
semantic_cluster_coverage_raw_record_id_count는 크게 잡힘
semantic_cluster_coverage_promoted_record_ids는 configured limit 이하
final_synthesis_context의 retrieved semantic records도 limit 이하
```

추가 테스트 2:

```text
test_candidate_expansion_covers_every_event_cluster_even_when_llm_omits_clusters
```

시나리오:

```text
CSV row 4개
LLM candidate expansion이 cluster 1개만 related_cluster_ids로 반환
normalize 후 candidate_expansion_summary missing count == 0
cluster 1개는 covered_cluster_ids
나머지 3개는 audit_only_cluster_ids
uncovered_cluster_ids는 []
```

추가 테스트 3:

```text
test_final_synthesis_context_records_cluster_coverage_counts
```

검증:

```text
final_synthesis_context.payload.event_cluster_ids
final_synthesis_context.payload.semantic_cluster_coverage_ids
final_synthesis_context.payload.candidate_expansion_cluster_coverage_ids
모두 같은 set
missing lists empty
input_summary counts 일치
promoted record count는 configured limit 이하
```

추가 테스트 4:

```text
test_context_inspect_rejects_cluster_coverage_drift
```

시나리오:

```text
manifest/event_clusters에는 cluster 3개
semantic_cluster_coverage.jsonl에는 2개만 related_cluster_ids
context inspect --strict가 실패해야 함
```

## 구현 순서

1. config에 cluster coverage limit 추가.
2. `event_clusters.jsonl`에 representative title/body excerpt 추가.
3. `SemanticRetrievalQuery`에 `related_cluster_ids`와 `coverage_query` 기본 필드 추가.
4. `ContextManifest`에 semantic/candidate cluster coverage 필드 추가.
5. `_normalize_semantic_retrieval_plan()`에서 모든 event cluster별 `cluster_coverage` query 자동 추가. 단, required category 7개에는 섞지 않는다.
6. 기존 `_write_semantic_retrieval_artifact()`는 required category 7개 결과만 유지한다.
7. `_write_semantic_cluster_coverage_artifact()`에서 cluster coverage row를 실행/저장하고, missing query coverage만 실패 처리한다.
8. cluster coverage retrieved records는 raw audit과 promoted lane으로 분리하고 promotion limit을 적용한다.
9. `CandidateExpansionReview`에 covered/audit_only/uncovered cluster ids를 추가한다.
10. `_normalize_candidate_expansion()`에서 finding coverage와 audit-only coverage를 집계한다.
11. final synthesis payload/input summary에 cluster coverage counts와 promoted record ids를 추가한다.
12. final synthesis required_inputs 호환 tuple과 legacy compatibility를 갱신한다.
13. tracked JSON schemas를 갱신한다.
14. `context inspect` strict 검증 추가.
15. provenance audit 검증 추가.
16. reporting bundle field copy와 render summary를 갱신한다.
17. diagnostics/UI optional artifact 연결을 확인한다.
18. unit/integration tests 추가.
19. `ruff`, `mypy`, `pytest` 실행.

## 패치 후 기대되는 실행 계약

새 뉴스 CSV 분석 시:

```text
included_news_row_count >= event_cluster_count
event_cluster_count == news_novelty_review.reviewed_cluster_count
event_cluster_count == semantic_cluster_coverage_summary.cluster_coverage_covered_count
semantic_cluster_coverage_summary.cluster_coverage_missing_count == 0
semantic_cluster_coverage_summary.cluster_coverage_promoted_record_count <= configured limit
event_cluster_count == candidate_expansion_summary.cluster_coverage_covered_count
candidate_expansion_summary.cluster_coverage_missing_count == 0
final_synthesis_context_summary.semantic_cluster_coverage_missing_id_count == 0
final_synthesis_context_summary.candidate_expansion_cluster_coverage_missing_id_count == 0
```

## 이 패치가 해결하는 것

해결:

```text
두뇌 보기 전에 LLM이 중요도 판단으로 cluster를 사실상 누락하는 문제.
semantic retrieval query가 일부 뉴스 cluster만 반영하는 문제.
candidate expansion fallback이 앞 3개 cluster만 연결하는 문제.
final synthesis에서 cluster coverage가 검증되지 않는 문제.
```

해결하지 않는 것:

```text
embedding provider 품질 문제.
LLM이 검색된 record를 잘못 해석하는 문제.
stock-web 가격 source 누락 문제.
대량 cluster로 인한 token/context 크기 문제.
```

대량 cluster token 문제는 별도 최적화가 필요하다.

권장 후속:

```text
cluster coverage query는 전체 cluster마다 만들되,
final synthesis에는 모든 query 결과 원문을 넣지 않고
semantic_retrieval.jsonl artifact + lane-balanced promoted records + coverage summary를 넣는 방식으로 유지한다.
```

## 교차검증 체크리스트

코드 패치 후 반드시 확인:

```bash
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest tests/unit/test_analysis_modes.py
python -m pytest
```

파일럿 분석 후 확인:

```bash
python -m news_scalping_lab.cli context inspect <RUN_ID> --strict
python -m news_scalping_lab.cli audit provenance
python -m news_scalping_lab.cli audit coverage
python -m news_scalping_lab.cli audit lookahead --trade-date YYYY-MM-DD
```

수동 확인할 artifact:

```text
runs/checkpoints/event_clusters/<RUN_ID>/event_clusters.jsonl
runs/checkpoints/semantic_retrieval/<RUN_ID>/semantic_retrieval_plan.json
runs/checkpoints/semantic_retrieval/<RUN_ID>/semantic_retrieval.jsonl
runs/checkpoints/candidate_expansion/<RUN_ID>/candidate_expansion.json
runs/checkpoints/final_synthesis_context/<RUN_ID>/final_synthesis_context.json
runs/manifests/<RUN_ID>.json
```

합격 기준:

```text
모든 event cluster id가 semantic retrieval coverage에 등장한다.
모든 event cluster id가 candidate expansion coverage에 등장한다.
coverage missing ids가 비어 있다.
exhaustive record sweep은 기존처럼 100%다.
final synthesis payload와 manifest의 coverage count가 일치한다.
```

## 최종 판단

현재 로직은 "전체 기억 sweep"은 잘 닫혀 있지만, "오늘 뉴스 cluster 각각이 두뇌 검색으로 반드시 연결되는지"는 아직 hard contract가 아니다.

따라서 다음 코드 패치는 연구 프롬프트가 아니라 production analysis pipeline 쪽에서 해야 한다.

가장 중요한 변경은 이것이다.

```text
LLM이 semantic retrieval plan을 어떻게 쓰든,
코드가 모든 event cluster별 cluster_coverage query를 자동 추가한다.
```

단, cluster coverage query 결과를 전부 final synthesis에 싣지는 않는다.

최종 설계는 아래처럼 분리되어야 한다.

```text
1. 모든 cluster가 retrieval 입력으로 들어갔는지
   = hard coverage audit

2. 각 cluster에서 나온 검색 결과가 몇 개인지
   = semantic_retrieval.jsonl에 전부 기록

3. final synthesis에 실제 record 본문을 몇 개 실을지
   = promotion limit으로 제한

4. 후보 확장으로 승격하지 않은 cluster
   = audit_only_cluster_ids로 보존
```

이렇게 해야 사용자가 걱정한 "LLM이 쓸데없다고 판단해서 두뇌 매칭 전에 놓치는 문제"를 구조적으로 막으면서, 동시에 "cluster가 많아져 final synthesis context가 폭발하는 문제"도 피할 수 있다.

